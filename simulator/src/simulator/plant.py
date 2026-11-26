"""Plant: the whole simulated world around the PLCs, in one step().

    Supply ─► C1 ─► Filler ─► C2 ─► LabelerStandIn
    (or Blowmoulder ─► C1, only with_blowmoulder=True)
                 ▲   │ product used    └─► Labeler ─► C3 ─► PackerStandIn   (only with_labeler=True)
    Product_Available│ ▼                                └─► Packer ─► PalletizerStandIn
    (wire)  ◄── SyrupRoom: syrup tank,              (only with_packer=True, needs with_labeler)
                 mixing tank, buffer tank (only with_syrup=True)
                 + wiring: Product_Available, E_Stop_OK, Heartbeats

    PLC outputs (FillerOutputs, SyrupSignals, LabelerOutputs, PackerOutputs, BlowmoulderOutputs)
        ──► plant.step(out, dt, syrup, labeler, packer, blowmoulder) ──► SimWrites + events
        (SyrupWrites / LabelerWrites / PackerWrites / BlowmoulderWrites from syrup_writes() / labeler_writes() /
        packer_writes() / blowmoulder_writes())

The plant is still pure physics: it gets the PLC outputs, moves the world by dt
seconds and returns every value the simulator writes back to the PLC. It
decides nothing. It knows the signals by NAME only; which Modbus address each
name has is the job of plcs.py (stage 5), so this file never imports
address_map or pymodbus and can be unit-tested without a PLC.
"""

import math
from dataclasses import dataclass, field, replace

from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.events import Event
from simulator.machines.blowmoulder import Blowmoulder, BlowmoulderOutputs, BlowmoulderSensors
from simulator.machines.filler import Filler, FillerOutputs
from simulator.machines.labeler import Labeler, LabelerOutputs, LabelerSensors
from simulator.machines.packer import Packer, PackerOutputs, PackerSensors
from simulator.machines.syrup import SyrupOutputs, SyrupRoom, SyrupSensors
from simulator.standins import Heartbeat, LabelerStandIn, PackerStandIn, PalletizerStandIn, Supply


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


@dataclass(frozen=True)
class LabelerWrites:
    """Every value the simulator writes to the labeler PLC: sensors, wiring and HMI animation."""

    sensors: LabelerSensors
    e_stop_ok: bool
    heartbeat: bool
    anim: tuple[int, int] = (0, 0)  # HMI animation %MW70..71: C3 slots, pocket code


@dataclass(frozen=True)
class PackerWrites:
    """Every value the simulator writes to the packer PLC: sensors, wiring and HMI animation."""

    sensors: PackerSensors
    e_stop_ok: bool
    heartbeat: bool
    anim: tuple[int, int, int, int] = (0, 0, 0, 0)  # HMI animation %MW70..73: wrap cells, oven cells, table, sheet


@dataclass(frozen=True)
class BlowmoulderWrites:
    """Every value the simulator writes to the blow moulder PLC: sensors and wiring (animation words: HMI part C)."""

    sensors: BlowmoulderSensors
    e_stop_ok: bool
    heartbeat: bool


# What the blow moulder's valves and cylinders do when its PLC does not answer: every output OFF, but the exhaust
# vents. It is a normally-open valve (docs/blowmoulder/state-diagram.md rule 3): lose the PLC, lose the air, and the
# bottle vents by itself. The mould then opens on its valve's spring side (a known model limit: the real PLC would
# hold a closed mould, rule 4).
BLOWMOULDER_DEAD = BlowmoulderOutputs(exhaust_valve=True)


class PulseWire:
    """What the PLC sees of a COUNTED pulse sensor (the packer's Bottle_Counted and Film_Pulse).

    The PLC only sees one value per tick and counts rising edges. Writing a pulse ON if it was ON at
    any sub-step (the OR used for the filler's and the labeler's sparse pulses) merges two pulses
    that fall into consecutive ticks: no OFF tick between them, one edge lost for good. Film_Pulse
    comes every 0.33 s, so one late tick of 0.2-0.3 s (below the slow-tick warning) could do that.

    This wire never loses an edge: it counts the rising edges of the sub-steps and passes each one on.
    It writes ON only to pass a new edge or to keep an ON going; when a new edge comes while the last
    written value was ON, it writes OFF in this tick and the ON in the next (the edge comes one tick
    late). At normal 0.1 s ticks it writes exactly what the OR writes. Limit: one bit per tick carries
    at most one edge per two ticks; ticks that stay slower than half the pulse period lose pace.
    """

    def __init__(self) -> None:
        self.value = False  # the last value written
        self.pending = 0  # rising edges not passed on yet
        self._level = False  # the physics level at the last sub-step
        self._seen = False  # the physics was ON at some sub-step of this tick

    def sample(self, level: bool) -> None:
        """The physics level at the end of one sub-step."""
        if level and not self._level:
            self.pending += 1
        self._level = level
        self._seen |= level

    def tick(self) -> bool:
        """End of the tick: the value to write."""
        if self.pending and not self.value:
            self.pending -= 1
            self.value = True  # pass one edge
        elif self.pending:
            self.value = False  # a new edge behind an ON: make the OFF first
        else:
            self.value = self.value and self._seen  # only keep an ON going, never start one without an edge
        self._seen = False
        return self.value


class Plant:
    def __init__(self, *, with_syrup: bool = False, with_labeler: bool = False, with_packer: bool = False,
                 with_blowmoulder: bool = False) -> None:
        if with_packer and not with_labeler:
            raise ValueError("with_packer needs with_labeler: the packer takes its bottles from the labeler's C3")
        self.now = 0.0  # simulation time in seconds
        self.factory = BottleFactory()
        self.c1 = Conveyor("C1")
        self.c2 = Conveyor("C2")
        self.filler = Filler(self.c1, self.c2)
        # The blow moulder is optional: without it Supply puts the bottles onto C1 (the old behaviour). With it,
        # the blow moulder PLC makes them from preforms; the bottle ids come from the same factory.
        self.supply: Supply | None = None
        self.blowmoulder: Blowmoulder | None = None
        if with_blowmoulder:
            self.blowmoulder = Blowmoulder(self.c1, self.factory)
        else:
            self.supply = Supply(self.c1, self.factory)
        self.blowmoulder_heartbeat = Heartbeat()
        # What the blow moulder PLC said in the last tick; None = no answer (BLOWMOULDER_DEAD)
        self.blowmoulder_outputs: BlowmoulderOutputs | None = None
        # The labeler is optional: without it a stand-in drives C2 and takes its bottles (the old behaviour).
        # With it, the labeler PLC drives C2, the labeler pushes onto C3 and a packer stand-in empties C3 –
        # or, with_packer, the packer PLC drives C3 and the real packer takes its bottles (a palletizer
        # stand-in empties its outfeed table).
        self.labeler: Labeler | None = None
        self.c3: Conveyor | None = None
        self.packer: Packer | None = None
        self.packer_standin: PackerStandIn | None = None
        self.palletizer: PalletizerStandIn | None = None
        self.labeler_standin: LabelerStandIn | None = None
        if with_labeler:
            self.c3 = Conveyor("C3")
            self.labeler = Labeler(self.c2, self.c3)
            if with_packer:
                self.packer = Packer(self.c3, check_labels=True)
                self.palletizer = PalletizerStandIn(self.packer.outfeed)
            else:
                self.packer_standin = PackerStandIn(self.c3)
        else:
            self.labeler_standin = LabelerStandIn(self.c2)
        self.labeler_heartbeat = Heartbeat()  # its own, as the syrup room's
        # What the labeler PLC said in the last tick; None = no answer: all its outputs OFF, C2 stands
        self.labeler_outputs: LabelerOutputs | None = None
        self.packer_heartbeat = Heartbeat()
        # What the packer PLC said in the last tick; None = no answer: all its outputs OFF, C3 stands
        self.packer_outputs: PackerOutputs | None = None
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
        self._mark_seen = False  # Label_Mark and Spin_Pulse: the labeler's pulse sensors
        self._spin_seen = False
        self._counted_wire = PulseWire()  # Bottle_Counted and Film_Pulse: the packer's counted pulses
        self._film_wire = PulseWire()
        self._ejected_seen = False  # Bottle_Ejected: the blow moulder's pulse sensor

    def step(
        self,
        out: FillerOutputs,
        dt: float,
        syrup: SyrupSignals | None = None,
        labeler: LabelerOutputs | None = None,
        packer: PackerOutputs | None = None,
        blowmoulder: BlowmoulderOutputs | None = None,
    ) -> tuple[SimWrites, list[Event]]:
        """Move the world by dt seconds with the given PLC outputs.

        `syrup` is what the syrup room PLC says; None = that PLC does not answer: its outputs are OFF
        and it drives no Product_Available wire. It is ignored when the plant has no syrup room.
        `labeler` are the labeler PLC's outputs; None = that PLC does not answer: all OFF, so C2 stands.
        It is ignored when the plant has no labeler (the stand-in drives C2).
        `packer` are the packer PLC's outputs; None = that PLC does not answer: all OFF, so C3 stands
        and the oven is off. It is ignored when the plant has no real packer.
        `blowmoulder` are the blow moulder PLC's outputs; None = that PLC does not answer: all OFF, the exhaust
        vents (BLOWMOULDER_DEAD). It is ignored when the plant has no blow moulder (Supply fills C1).

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
        open a valve over a bottle that has already moved on. The labeler's pulse
        sensors (Label_Mark, Spin_Pulse) and the blow moulder's Bottle_Ejected (one per bottle, >= 5 s apart)
        are kept the same way. The packer's counted pulses
        (Bottle_Counted, Film_Pulse) come faster and go through a PulseWire, which never merges two.
        """
        events: list[Event] = []
        signals = syrup if self.syrup is not None else None
        self.syrup_signals = signals
        syrup_out = signals.outputs if signals is not None else SyrupOutputs()
        self.labeler_outputs = labeler if self.labeler is not None else None
        labeler_out = self.labeler_outputs or LabelerOutputs()
        self.packer_outputs = packer if self.packer is not None else None
        packer_out = self.packer_outputs or PackerOutputs()
        self.blowmoulder_outputs = blowmoulder if self.blowmoulder is not None else None
        blowmoulder_out = self.blowmoulder_outputs or BLOWMOULDER_DEAD
        if dt > config.SLOW_TICK_S:
            events.append(Event.sim(self.now + dt, f"slow tick: {dt:.2f} s (outputs were old for that long)", warning=True))
        # The heartbeats are stepped once per TICK, not per sub-step: the PLC only sees the value at the
        # end of the tick, and a late tick must not hide a toggle (see Heartbeat)
        self.heartbeat.step(dt)
        self.syrup_heartbeat.step(dt)
        self.labeler_heartbeat.step(dt)
        self.packer_heartbeat.step(dt)
        self.blowmoulder_heartbeat.step(dt)
        n = max(1, math.ceil(dt / config.SUBSTEP_S - 1e-9))
        self._s6_seen = self._s8_seen = self._mark_seen = self._spin_seen = self._ejected_seen = False
        for _ in range(n):
            events += self._advance(out, syrup_out, labeler_out, packer_out, blowmoulder_out, dt / n)
            s = self.filler.sensors()
            self._s6_seen |= s.s6
            self._s8_seen |= s.s8
            if self.labeler is not None:
                ls = self.labeler.sensors()
                self._mark_seen |= ls.label_mark
                self._spin_seen |= ls.spin_pulse
            if self.blowmoulder is not None:
                self._ejected_seen |= self.blowmoulder.bottle_ejected
            if self.packer is not None:
                self._counted_wire.sample(self.packer.bottle_counted)
                self._film_wire.sample(self.packer.wrapper.film.film_pulse)
        if self.packer is not None:
            self._counted_wire.tick()
            self._film_wire.tick()
        return self.writes(), events

    def _advance(
        self,
        out: FillerOutputs,
        syrup_out: SyrupOutputs,
        labeler_out: LabelerOutputs,
        packer_out: PackerOutputs,
        blowmoulder_out: BlowmoulderOutputs,
        dt: float,
    ) -> list[Event]:
        """One physics step of at most SUBSTEP_S."""
        self.now += dt
        events: list[Event] = []
        # Bottles flow from left to right: supply or blow moulder -> C1 -> filler -> C2 -> labeler (-> C3 -> packer
        # -> palletizer)
        if self.blowmoulder is not None:
            events += self.blowmoulder.step(blowmoulder_out, dt, self.now)
        else:
            events += self.supply.step(dt, self.now)
        self.c1.step(out.conveyor1, dt)
        if self.syrup is not None:
            events += self.syrup.step(syrup_out, dt, self.now)
        events += self.filler.step(out, self.product_flows, dt, self.now)
        events += self._draw_from_buffer()
        if self.labeler is None:
            self.c2.step(self.labeler_standin.motor_on, dt)
            events += self.labeler_standin.step(dt, self.now)
            return events
        self.c2.step(labeler_out.conveyor_motor2, dt)
        events += self.labeler.step(labeler_out, dt, self.now)
        if self.packer is None:
            self.c3.step(self.packer_standin.motor_on, dt)
            events += self.packer_standin.step(dt, self.now)
            return events
        self.c3.step(packer_out.conveyor_motor3, dt)
        events += self.packer.step(packer_out, dt, self.now)
        events += self.palletizer.step(dt, self.now)
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

    def labeler_writes(self) -> LabelerWrites | None:
        """Current sensor and wiring values for the labeler PLC (None without a labeler).

        Label_Mark and Spin_Pulse are ON if they were ON at any sub-step of the last tick (see step()).
        """
        if self.labeler is None:
            return None
        s = self.labeler.sensors()
        s = replace(s, label_mark=s.label_mark or self._mark_seen, spin_pulse=s.spin_pulse or self._spin_seen)
        return LabelerWrites(s, self.e_stop_ok, self.labeler_heartbeat.value,
                             anim=(self.c3.slot_bits(), self.labeler.pocket_code()))

    def packer_writes(self) -> PackerWrites | None:
        """Current sensor and wiring values for the packer PLC (None without the real packer).

        Bottle_Counted and Film_Pulse are the values of their PulseWires (see step()).
        """
        if self.packer is None:
            return None
        s = replace(self.packer.sensors(), bottle_counted=self._counted_wire.value, film_pulse=self._film_wire.value)
        return PackerWrites(s, self.e_stop_ok, self.packer_heartbeat.value, anim=self.packer.anim_words())

    def blowmoulder_writes(self) -> BlowmoulderWrites | None:
        """Current sensor and wiring values for the blow moulder PLC (None without the blow moulder).

        Bottle_Ejected is ON if it was ON at any sub-step of the last tick (see step()).
        """
        if self.blowmoulder is None:
            return None
        s = self.blowmoulder.sensors()
        s = replace(s, bottle_ejected=s.bottle_ejected or self._ejected_seen)
        return BlowmoulderWrites(s, self.e_stop_ok, self.blowmoulder_heartbeat.value)
