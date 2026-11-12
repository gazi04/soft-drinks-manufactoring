"""Syrup room: syrup tank, mixing tank (= carbonator) and buffer tank.

    mains water ─► WaterPump ──┐
    syrup tank ──► SyrupPump ──┴─► MIXING TANK ──► TransferPump ──► BUFFER TANK ──► filler
                                   (Agitator, cooling, CO2: later stages)

Stage 1 (this file): VOLUMES only. The three tanks are float litres; the PLC outputs
move liquid between them. Temperature and pressure come in stage 2; SyrupSensors
already has their fields (constants for now) so its shape does not change later.

The room only knows the PLC outputs (SyrupOutputs) and reports what its sensors see
(SyrupSensors). It does not decide anything: if the PLC runs a pump into a full tank
the liquid spills and the audit logs a warning. The decisions stay in the PLC.

Known model limits:
- Water comes from the mains and is never short.
- The PLC stops TRANSFER at MixLevel <= 5 L, so up to 5 L of finished product stay in the
  mixing tank and the next water dose is that much shorter. Physical, not a bug.
- Order inside one step: syrup tank -> mixing tank (dosing), then mixing tank -> buffer.
  Both pumps can only run together in Manual mode.
"""

from dataclasses import dataclass

from simulator import config
from simulator.events import Event

# Tolerance for "the tank is empty", as in conveyor.py / filler.py (sums of 0.1 are not exact)
_EPSILON = 1e-9


@dataclass
class SyrupOutputs:
    """PLC outputs %QX0.0-0.5 (coils 0-5), in coil order."""

    water_pump: bool = False
    syrup_pump: bool = False
    agitator: bool = False
    cooling_valve: bool = False
    co2_valve: bool = False
    transfer_pump: bool = False


@dataclass(frozen=True)
class SyrupSensors:
    """What the sensors report, as INT in the PLC's units."""

    syrup_level: int  # L
    mix_level: int  # L
    buffer_level: int  # L
    product_temp: int  # 0.1 degC (constant until the temperature physics)
    co2_pressure: int  # 0.01 bar (constant until the pressure physics)
    mix_high_high: bool  # level switch on the mixing tank, from the TRUE level


class SyrupRoom:
    def __init__(
        self,
        syrup_l: float = config.SYRUP_START_L,
        mix_l: float = 0.0,
        buffer_l: float = config.BUFFER_START_L,
    ) -> None:
        # True state, litres. Public: tests and (stage 6) fault commands set them directly.
        self.syrup_l = syrup_l
        self.mix_l = mix_l
        self.buffer_l = buffer_l
        # Ledger: every litre that enters or leaves the room, so volume conservation can be checked
        self.start_l = syrup_l + mix_l + buffer_l
        self.water_in_l = 0.0  # from the mains
        self.delivered_l = 0.0  # syrup deliveries
        self.drawn_l = 0.0  # taken from the buffer (the filler)
        self.spilled_l = 0.0  # lost over the brim
        self._now = 0.0
        self._events: list[Event] = []
        self._active: set[str] = set()  # warnings already given for the current period
        self._mix_spilled = self._buffer_spilled = False

    # --- Physics --------------------------------------------------------------------
    def step(self, out: SyrupOutputs, dt: float, now: float) -> list[Event]:
        """Move the liquid for dt seconds with the given PLC outputs."""
        self._now = now
        self._events = []
        self._mix_spilled = self._buffer_spilled = False  # set below when a tank runs over this step

        # Dosing: water from the mains, syrup from its tank
        if out.water_pump:
            self._into_mix(config.WATER_FLOW_L_S * dt, from_mains=True)
        dry = out.syrup_pump and self.syrup_l <= _EPSILON
        self._warn("syrup_dry", dry, "syrup pump runs dry: syrup tank is empty")
        if out.syrup_pump and not dry:
            moved = min(config.SYRUP_FLOW_L_S * dt, self.syrup_l)
            self.syrup_l -= moved
            self._into_mix(moved, from_mains=False)

        # Transfer: mixing tank -> buffer tank
        dry = out.transfer_pump and self.mix_l <= _EPSILON
        self._warn("transfer_dry", dry, "transfer pump runs dry: mixing tank is empty")
        if out.transfer_pump and not dry:
            moved = min(config.TRANSFER_FLOW_L_S * dt, self.mix_l)
            self.mix_l -= moved
            self.buffer_l += moved
            over = self.buffer_l - config.BUFFER_TANK_L
            if over > 0:
                self.buffer_l -= over
                self.spilled_l += over
                self._buffer_spilled = over > _EPSILON

        # Evaluated every step (not only when a pump runs), so each warning re-arms when its cause ends
        self._warn("mix_overflow", self._mix_spilled, "mixing tank overflows: liquid spilled")
        self._warn("buffer_overflow", self._buffer_spilled, "buffer tank overflows: product spilled")
        self._warn(
            "agitator_dry",
            out.agitator and self.mix_l < config.AGITATOR_MIN_L,
            f"agitator runs with the propeller uncovered ({self.mix_l:.0f} L < {config.AGITATOR_MIN_L:.0f} L)",
        )
        return self._events

    def _into_mix(self, litres: float, from_mains: bool) -> None:
        """Liquid into the mixing tank; what does not fit is spilled."""
        if from_mains:
            self.water_in_l += litres
        self.mix_l += litres
        over = self.mix_l - config.MIX_TANK_L
        if over > 0:
            self.mix_l -= over
            self.spilled_l += over
            self._mix_spilled = self._mix_spilled or over > _EPSILON

    def _warn(self, key: str, condition: bool, text: str) -> None:
        """One warning per period: logged when the condition starts, re-armed when it ends."""
        if not condition:
            self._active.discard(key)
        elif key not in self._active:
            self._active.add(key)
            self._events.append(Event.sim(self._now, text, warning=True))

    # --- Interfaces to the world outside --------------------------------------------
    def deliver_syrup(self, litres: float) -> float:
        """A syrup delivery. Returns the litres that fitted into the tank."""
        if litres < 0:
            raise ValueError(f"litres must be >= 0, got {litres}")
        taken = min(litres, config.SYRUP_TANK_L - self.syrup_l)
        self.syrup_l += taken
        self.delivered_l += taken
        return taken

    def draw_buffer(self, litres: float) -> float:
        """The filler takes product. Returns the litres that were there."""
        if litres < 0:
            raise ValueError(f"litres must be >= 0, got {litres}")
        taken = min(litres, self.buffer_l)
        self.buffer_l -= taken
        self.drawn_l += taken
        return taken

    # --- Sensors --------------------------------------------------------------------
    def sensors(self) -> SyrupSensors:
        """The only place where the true state becomes reported values (a stuck sensor goes here)."""
        return SyrupSensors(
            syrup_level=round(self.syrup_l),
            mix_level=round(self.mix_l),
            buffer_level=round(self.buffer_l),
            product_temp=config.ROOM_TEMP_X10,
            co2_pressure=0,
            mix_high_high=self.mix_l >= config.MIX_HIGH_HIGH_L,
        )
