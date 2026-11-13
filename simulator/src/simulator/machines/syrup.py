"""Syrup room: syrup tank, mixing tank (= carbonator) and buffer tank.

    mains water ─► WaterPump ──┐
    syrup tank ──► SyrupPump ──┴─► MIXING TANK ──► TransferPump ──► BUFFER TANK ──► filler
                                   Agitator, CoolingValve (jacket), CO2Valve

Stage 1: VOLUMES. The three tanks are float litres; the PLC outputs move liquid between them.
Stage 2: TEMPERATURE and CO2 PRESSURE of the mixing tank.

    temperature   first-order lag toward the chiller (cooling valve open) and toward the room
                  (always, slowly); water and syrup mix in by volume
    pressure      CO2 valve fills the headspace; the liquid takes CO2 up (dissolved_bar) and the
                  headspace loses what the liquid gains; the transfer pump vents the headspace

The room only knows the PLC outputs (SyrupOutputs) and reports what its sensors see
(SyrupSensors). It does not decide anything: if the PLC runs a pump into a full tank
the liquid spills and the audit logs a warning. The decisions stay in the PLC.

Order inside step():
    1. inflows (water, syrup), with their temperature     4. gas: fill, dissolve, vent, relief
    2. transfer out                                        5. audit warnings
    3. thermal relaxation (cooling valve, room heat)

step() is valid for dt <= 0.5 s: the temperature is solved exactly, but the gas part is a
plain Euler step. The plant splits late ticks into steps of at most 0.1 s anyway.

Known model limits:
- Water comes from the mains and is never short.
- The PLC stops TRANSFER at MixLevel <= 5 L, so up to 5 L of finished product stay in the
  mixing tank and the next water dose is that much shorter. Physical, not a bug.
- Both pumps can only run together in Manual mode.
- Headspace expansion while the tank empties is ignored: the pressure only changes by the
  vent rule (transfer pump running = tank emptied into the unpressurised buffer).
- CO2 solubility does not depend on the temperature, so "warm product gives its CO2 back"
  is not modelled. The cooling speed does not depend on the batch volume.
"""

import math
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
    product_temp: int  # 0.1 degC
    co2_pressure: int  # 0.01 bar
    mix_high_high: bool  # level switch on the mixing tank, from the TRUE level


class SyrupRoom:
    def __init__(
        self,
        syrup_l: float = config.SYRUP_START_L,
        mix_l: float = 0.0,
        buffer_l: float = config.BUFFER_START_L,
        temp_c: float = config.ROOM_C,
        pressure_bar: float = 0.0,
        dissolved_bar: float = 0.0,
    ) -> None:
        # True state. Public: tests and (stage 6) fault commands set them directly.
        self.syrup_l = syrup_l  # litres
        self.mix_l = mix_l
        self.buffer_l = buffer_l
        self.temp_c = temp_c  # product in the mixing tank
        self.pressure_bar = pressure_bar  # headspace of the mixing tank
        self.dissolved_bar = dissolved_bar  # CO2 in the liquid, as the pressure it would balance with
        self.coolant_c = config.COOLANT_C  # chiller temperature; "chiller failed" raises it
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
        """Move the liquid, heat and gas for dt seconds with the given PLC outputs."""
        self._now = now
        self._events = []
        self._mix_spilled = self._buffer_spilled = False  # set below when a tank runs over this step

        # 1. Dosing: water from the mains, syrup from its tank
        if out.water_pump:
            self._into_mix(config.WATER_FLOW_L_S * dt, config.WATER_TEMP_C, from_mains=True)
        dry = out.syrup_pump and self.syrup_l <= _EPSILON
        self._warn("syrup_dry", dry, "syrup pump runs dry: syrup tank is empty")
        if out.syrup_pump and not dry:
            moved = min(config.SYRUP_FLOW_L_S * dt, self.syrup_l)
            self.syrup_l -= moved
            self._into_mix(moved, config.SYRUP_TEMP_C, from_mains=False)

        # 2. Transfer: mixing tank -> buffer tank (temperature and dissolved CO2 stay in the liquid)
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

        # 3.+4. Heat and gas of the mixing tank
        self._relax_temperature(out.cooling_valve, dt)
        self._gas(out, dt)

        # 5. Evaluated every step (not only when a pump runs), so each warning re-arms when its cause ends
        self._warn("mix_overflow", self._mix_spilled, "mixing tank overflows: liquid spilled")
        self._warn("buffer_overflow", self._buffer_spilled, "buffer tank overflows: product spilled")
        self._warn(
            "agitator_dry",
            out.agitator and self.mix_l < config.AGITATOR_MIN_L,
            f"agitator runs with the propeller uncovered ({self.mix_l:.0f} L < {config.AGITATOR_MIN_L:.0f} L)",
        )
        self._warn(
            "relief",
            self.pressure_bar >= config.PRESS_RELIEF_BAR,
            f"pressure relief valve lifts at {config.PRESS_RELIEF_BAR:g} bar",
        )
        return self._events

    def _into_mix(self, litres: float, temp_c: float, from_mains: bool) -> None:
        """Liquid into the mixing tank. Temperature and dissolved CO2 mix by volume; the rest spills."""
        if from_mains:
            self.water_in_l += litres
        total = self.mix_l + litres
        if total > _EPSILON:  # volume-weighted average: the residue keeps its share
            self.temp_c = (self.temp_c * self.mix_l + temp_c * litres) / total
            self.dissolved_bar = self.dissolved_bar * self.mix_l / total  # fresh liquid holds no CO2
        self.mix_l = total
        over = self.mix_l - config.MIX_TANK_L
        if over > 0:
            self.mix_l -= over
            self.spilled_l += over
            self._mix_spilled = self._mix_spilled or over > _EPSILON

    def _relax_temperature(self, cooling_valve: bool, dt: float) -> None:
        """dT/dt = -valve (T - coolant) / tau_cool - (T - room) / tau_room, solved exactly.

        The inputs are constant during one step, so the solution is an exponential toward the
        balance temperature. It cannot overshoot, and a late tick gives the same result as
        several short ones.
        """
        if self.mix_l <= _EPSILON:
            return  # no product, no temperature
        rate_cool = (1.0 / config.COOL_TAU_S) if cooling_valve else 0.0
        rate_room = 1.0 / config.ROOM_TAU_S
        rate = rate_cool + rate_room
        balance = (rate_cool * self.coolant_c + rate_room * config.ROOM_C) / rate
        self.temp_c = balance + (self.temp_c - balance) * math.exp(-rate * dt)

    def _gas(self, out: SyrupOutputs, dt: float) -> None:
        """CO2 valve, dissolving, venting and the relief valve (Euler, dt <= 0.5 s)."""
        empty = self.mix_l <= config.MIX_EMPTY_L
        if empty:
            self.dissolved_bar = 0.0  # a few litres of film hold no CO2 worth tracking
        else:
            k = config.DISSOLVE_K_STIRRED if out.agitator else config.DISSOLVE_K_STILL
            taken = k * (self.pressure_bar - self.dissolved_bar) * dt  # < 0: CO2 leaves the liquid
            self.dissolved_bar += taken
            self.pressure_bar -= config.HEADSPACE_GAIN * taken
        if out.co2_valve:
            self.pressure_bar += config.CO2_FILL_BAR_S * dt
        if out.transfer_pump or empty:  # emptied against the unpressurised buffer / nothing in the tank
            self.pressure_bar -= self.pressure_bar * min(1.0, dt / config.VENT_TAU_S)
        self.pressure_bar = max(0.0, min(self.pressure_bar, config.PRESS_RELIEF_BAR))

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
            product_temp=round(self.temp_c * 10),
            co2_pressure=round(self.pressure_bar * 100),
            mix_high_high=self.mix_l >= config.MIX_HIGH_HIGH_L,  # its own switch: always the TRUE level
        )
