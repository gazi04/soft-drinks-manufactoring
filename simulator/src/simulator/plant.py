"""Plant: the whole simulated world around the PLCs, in one step().

    Supply ─► C1 ─► Filler ─► C2 ─► LabelerStandIn
                 ▲   │ product used
    Product_Available│ ▼                          (only with_syrup=True)
    (wire)  ◄── SyrupRoom: syrup tank, mixing tank, buffer tank
                 + wiring: Product_Available, E_Stop_OK, Heartbeats

    PLC outputs (FillerOutputs, SyrupSignals) ──► plant.step(out, dt, syrup) ──► SimWrites + events

The plant is still pure physics: it gets the PLC outputs, moves the world by dt
seconds and returns every value the simulator writes back to the PLC. It
decides nothing. It knows the signals by NAME only; which Modbus address each
name has is the job of plcs.py (stage 5), so this file never imports
address_map or pymodbus and can be unit-tested without a PLC.
"""

import math
from dataclasses import dataclass, field

from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.events import Event
from simulator.machines.filler import Filler, FillerOutputs
from simulator.machines.syrup import SyrupOutputs, SyrupRoom, SyrupSensors
from simulator.standins import Heartbeat, LabelerStandIn, Supply


@dataclass(frozen=True)
class SimWrites:
    """Every value the simulator writes to the filler PLC, by name."""

    s2: bool
    s3: bool
    s4: bool
    s5: bool
    s6: bool
    s7: bool
    s8: bool
    product_available: bool
    e_stop_ok: bool
    heartbeat: bool
    fill_level: int
    anim: tuple[int, ...] = (0,) * 6  # HMI animation %MW70..75: C1 slots, C2 slots, 4 pocket codes


@dataclass(frozen=True)
class SyrupSignals:
    """Everything the plant reads from the syrup room PLC: its outputs and the %MX6.0 wire."""

    outputs: SyrupOutputs = field(default_factory=SyrupOutputs)
    product_available: bool = False  # Product_Available, wired to the filler's %MX1.0


@dataclass(frozen=True)
class SyrupWrites:
    """Every value the simulator writes to the syrup room PLC: sensors and wiring."""

    sensors: SyrupSensors
    e_stop_ok: bool
    heartbeat: bool


class Plant:
    def __init__(self, *, with_syrup: bool = False) -> None:
        self.now = 0.0  # simulation time in seconds
        self.factory = BottleFactory()
        self.c1 = Conveyor("C1")
        self.c2 = Conveyor("C2")
        self.filler = Filler(self.c1, self.c2)
        self.supply = Supply(self.c1, self.factory)
        self.labeler = LabelerStandIn(self.c2)
        self.heartbeat = Heartbeat()
        # The syrup room is optional: without it Product_Available is a stand-in held at TRUE
        self.syrup: SyrupRoom | None = SyrupRoom() if with_syrup else None
        self.syrup_heartbeat = Heartbeat()  # its own, so a comm failure can hit one PLC only
        self.buffer_draw_scale = config.BUFFER_DRAW_SCALE  # litres from the buffer per litre filled
        # What the syrup PLC said in the last tick (outputs + Product_Available); None = no answer yet / PLC
        # not answering. The wire to the filler is derived from it, and the panel shows the pump lamps.
        self.syrup_signals: SyrupSignals | None = None
        self._dispensed_seen = 0.0  # filler.dispensed_ml already taken from the buffer
        self._buffer_dry = False  # the empty-buffer warning was already given for this period
        # Wiring values. product_available is the pipe itself: TRUE = connected, FALSE = cut (command).
        # The physics does not act on e_stop_ok: switching the outputs off is the PLC's job.
        self.product_available = True
        self.e_stop_ok = True  # one E-stop chain for both PLCs
        self._setpoints_seen: dict[str, int] = {}  # last RinseTime / CapTime checked
        # Pulse sensors seen ON during the last tick (see step())
        self._s6_seen = False
        self._s8_seen = False

    def step(
        self, out: FillerOutputs, dt: float, syrup: SyrupSignals | None = None
    ) -> tuple[SimWrites, list[Event]]:
        """Move the world by dt seconds with the given PLC outputs.

        `syrup` is what the syrup room PLC says; None = that PLC does not answer: its outputs are OFF
        and it drives no Product_Available wire. It is ignored when the plant has no syrup room.

        A late tick (dt longer than SUBSTEP_S) is split into equal smaller steps.
        One big step would let two things that happen one after the other happen at
        once, e.g. C1 brings the next bottle to the door AND the door timer already
        counts the whole dt for it. The total time stays dt, so the simulator's
        clock keeps pace with the PLC's timers.

        What sub-steps cannot fix: during a late tick the PLC outputs are old (the
        PLC may have stopped the belt in between, we only see it next tick). So a
        tick longer than SLOW_TICK_S is reported as a SIM warning: an audit warning
        right after it may be caused by the simulator, not by the PLC program.

        Pulse sensors (S6, S8): the PLC uses their rising edges, and the simulator
        only writes once per tick. A pulse that starts and ends inside one late tick
        would be lost (missed S8 edge = belt runs on, bottles skip a station; missed
        S6 = GoodCount too low). So S6/S8 are written ON if they were ON at ANY
        sub-step of the tick (report 7.3: every pulse lasts at least one tick).
        Presence sensors (S3-S5) are not stretched: a stale ON would let the PLC
        open a valve over a bottle that has already moved on.
        """
        events: list[Event] = []
        signals = syrup if self.syrup is not None else None
        self.syrup_signals = signals
        syrup_out = signals.outputs if signals is not None else SyrupOutputs()
        if dt > config.SLOW_TICK_S:
            events.append(Event.sim(self.now + dt, f"slow tick: {dt:.2f} s (outputs were old for that long)", warning=True))
        # The heartbeats are stepped once per TICK, not per sub-step: the PLC only sees the value at the
        # end of the tick, and a late tick must not hide a toggle (see Heartbeat)
        self.heartbeat.step(dt)
        self.syrup_heartbeat.step(dt)
        n = max(1, math.ceil(dt / config.SUBSTEP_S - 1e-9))
        self._s6_seen = self._s8_seen = False
        for _ in range(n):
            events += self._advance(out, syrup_out, dt / n)
            s = self.filler.sensors()
            self._s6_seen |= s.s6
            self._s8_seen |= s.s8
        return self.writes(), events

    def _advance(self, out: FillerOutputs, syrup_out: SyrupOutputs, dt: float) -> list[Event]:
        """One physics step of at most SUBSTEP_S."""
        self.now += dt
        events: list[Event] = []
        # Bottles flow from left to right: supply -> C1 -> filler -> C2 -> labeler
        events += self.supply.step(dt, self.now)
        self.c1.step(out.conveyor1, dt)
        if self.syrup is not None:
            events += self.syrup.step(syrup_out, dt, self.now)
        events += self.filler.step(out, self.product_flows, dt, self.now)
        events += self._draw_from_buffer()
        self.c2.step(self.labeler.motor_on, dt)
        events += self.labeler.step(dt, self.now)
        return events

    @property
    def filler_product_available(self) -> bool:
        """The SIGNAL to the filler PLC (%MX1.0): the pipe is connected AND the syrup PLC says there is product."""
        if self.syrup is None:
            return self.product_available  # stand-in
        signals = self.syrup_signals  # None = the syrup PLC does not answer: a dead PLC drives no wire
        return self.product_available and signals is not None and signals.product_available

    @property
    def product_flows(self) -> bool:
        """The PHYSICS at the fill valve: product comes out when the pipe is connected and the buffer holds some.

        Not the PLC signal: Product_Available only tells the filler PLC whether to open the valve. If the
        signal is wrong (e.g. BufferMin set to 0 on the HMI) and the buffer runs dry, the open valve gives
        nothing, the bottle stays underfilled and the filler PLC raises its fill timeout.
        """
        if self.syrup is None:
            return self.product_available  # stand-in: the pipe alone
        return self.product_available and self.syrup.buffer_l > 1e-9

    def _draw_from_buffer(self) -> list[Event]:
        """The product the filler used since the last call is taken out of the buffer tank."""
        if self.syrup is None:
            return []
        used_ml = self.filler.dispensed_ml - self._dispensed_seen
        self._dispensed_seen = self.filler.dispensed_ml
        if used_ml <= 0:
            self._buffer_dry = False
            return []
        wanted = used_ml / 1000 * self.buffer_draw_scale
        got = self.syrup.draw_buffer(wanted)
        short = got < wanted - 1e-9
        warn = short and not self._buffer_dry
        self._buffer_dry = short
        if warn:  # the last litres ran out during this sub-step; from the next one on the valve gives nothing
            return [Event.sim(self.now, "buffer tank ran empty while the fill valve was open: no more product",
                              warning=True)]
        return []

    def apply_setpoints(self, fill_ml: int, rinse_ms: int, cap_ms: int) -> list[Event]:
        """Take over the setpoints read from the PLC (the HMI may change them at any time).

        - FillSetpoint becomes the underfill limit of the audit.
        - RinseTime / CapTime shorter than what a bottle physically needs: one SIM warning
          per change, so a too-short setpoint is explained once, instead of only showing up
          as "not rinsed" / "uncapped" on every bottle.
        Values <= 0 are ignored (the PLC's first scan has not written its defaults yet).
        """
        events: list[Event] = []
        if fill_ml > 0:
            self.filler.fill_setpoint_ml = fill_ml
        checks = (
            ("RinseTime", rinse_ms, self.filler.rinse_min_s, "rinsed"),
            ("CapTime", cap_ms, self.filler.cap_min_s, "capped"),
        )
        for name, ms, min_s, done in checks:
            if ms <= 0 or self._setpoints_seen.get(name) == ms:
                continue
            self._setpoints_seen[name] = ms
            if ms / 1000 < min_s - 1e-9:
                text = f"{name} {ms} ms is shorter than the {min_s:g} s a bottle needs: bottles will not be {done}"
                events.append(Event.sim(self.now, text, warning=True))
        return events

    def writes(self) -> SimWrites:
        """Current sensor and wiring values, without moving anything (e.g. the first write)."""
        s = self.filler.sensors()
        return SimWrites(
            s2=s.s2,
            s3=s.s3,
            s4=s.s4,
            s5=s.s5,
            s6=s.s6 or self._s6_seen,
            s7=self.c2.occupied(config.S7_SLOT),
            s8=s.s8 or self._s8_seen,
            product_available=self.filler_product_available,
            e_stop_ok=self.e_stop_ok,
            heartbeat=self.heartbeat.value,
            fill_level=s.fill_level,
            anim=(self.c1.slot_bits(), self.c2.slot_bits(), *self.filler.pocket_codes()),
        )

    def syrup_writes(self) -> SyrupWrites | None:
        """Current sensor and wiring values for the syrup room PLC (None without a syrup room)."""
        if self.syrup is None:
            return None
        return SyrupWrites(self.syrup.sensors(), self.e_stop_ok, self.syrup_heartbeat.value)
