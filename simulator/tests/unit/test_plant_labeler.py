"""Plant with the real labeler: supply -> C1 -> filler -> C2 -> labeler -> C3 -> packer.  uv run pytest

No PLC here. `LabelerTestController` plays the labeler PLC's steps 5-40 (a short port of
docs/labeler/state-diagram.md without alarms, Stop or modes), like the filler's TestController
in test_plant.py, so the line can run without Docker. From the closed-loop stage on, the real
OpenPLC program sets the outputs and this class is not used.

Like the real loop, both controllers see the sensors of the previous tick.
"""

from test_plant import TestController

from simulator import config
from simulator.events import EventLog
from simulator.machines.labeler import LabelerOutputs, LabelerSensors
from simulator.plant import LabelerWrites, Plant

TICK = 0.1
EPS = 1e-9
WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS, UNCLAMP, WAIT_OUTFEED, DISCHARGE = 5, 10, 20, 22, 24, 26, 35, 40
# PLC defaults (plcs/labeler/pous/programs/main.ld)
GLUE_SETPOINT, GLUE_HYST, GLUE_BAND, GLUE_MAX, PRESS_TURNS = 1700, 50, 100, 2000, 2
GATE_PULSE_S, CUT_PULSE_S = 0.5, 0.2


class Tp:
    """IEC TP: a pulse of fixed length that starts at a rising edge of IN."""

    def __init__(self, pt: float) -> None:
        self.pt, self.left, self.prev_in = pt, 0.0, False

    def __call__(self, inp: bool, dt: float) -> bool:
        if inp and not self.prev_in and self.left <= EPS:
            self.left = self.pt
        self.prev_in = inp
        q = self.left > EPS
        self.left = max(0.0, self.left - dt)
        return q


class LabelerTestController:
    """The labeler PLC's sequence without alarms, Stop or modes (starts as after Auto + Start)."""

    __test__ = False  # not a pytest test class

    def __init__(self) -> None:
        self.step = WARM_UP
        self.heat_demand = False
        self.entering = False  # Bottle_Entering
        self.outfeed_full = False
        self.full_on_t = self.full_off_t = 0.0
        self.turns = 0  # Spin_Counter
        self.prev_mark = self.prev_spin = False
        self.gate_tp, self.cut_tp = Tp(GATE_PULSE_S), Tp(CUT_PULSE_S)
        self.steps_seen: list[int] = [WARM_UP]

    def outputs(self, s: LabelerSensors, dt: float) -> LabelerOutputs:
        # Conditioning: glue window + hysteresis, Outfeed_Full filter (2 s on / 3 s off), edges
        glue_ready = abs(s.glue_temp - GLUE_SETPOINT) <= GLUE_BAND
        if s.glue_temp < GLUE_SETPOINT - GLUE_HYST:
            self.heat_demand = True
        elif s.glue_temp >= GLUE_SETPOINT:
            self.heat_demand = False
        self.full_on_t = self.full_on_t + dt if s.c3_full else 0.0
        self.full_off_t = 0.0 if s.c3_full else self.full_off_t + dt
        if self.full_on_t >= 2.0 - EPS:
            self.outfeed_full = True
        if self.full_off_t >= 3.0 - EPS:
            self.outfeed_full = False
        mark_edge = s.label_mark and not self.prev_mark
        spin_edge = s.spin_pulse and not self.prev_spin
        self.prev_mark, self.prev_spin = s.label_mark, s.spin_pulse
        if s.bottle_in_pocket:
            self.entering = False

        # Transitions (state-diagram.md step table)
        before = self.step
        if self.step == WARM_UP and glue_ready:
            self.step = WAIT_BOTTLE
        elif self.step == WAIT_BOTTLE:
            if s.bottle_in_pocket and not s.label_present and glue_ready:
                self.step = CLAMP
            elif s.bottle_in_pocket and s.label_present:
                self.step = WAIT_OUTFEED if self.outfeed_full else DISCHARGE
            elif not glue_ready and not s.bottle_in_pocket:
                self.step = WARM_UP
        elif self.step == CLAMP and s.bell_down:
            self.step = WRAP
        elif self.step == WRAP and mark_edge:
            self.step, self.turns = PRESS, 0
        elif self.step == PRESS:
            self.turns += spin_edge
            if self.turns >= PRESS_TURNS:
                self.step = UNCLAMP
        elif self.step == UNCLAMP and not s.bell_down:
            self.step = WAIT_OUTFEED if self.outfeed_full else DISCHARGE
        elif self.step == WAIT_OUTFEED and not self.outfeed_full:
            self.step = DISCHARGE
        elif self.step == DISCHARGE and not s.bottle_in_pocket:
            self.step = WAIT_BOTTLE
        if self.step != before:
            self.steps_seen.append(self.step)

        # Outputs with the interlocks of rule 9
        gate_in = (self.step == WAIT_BOTTLE and s.bottle_at_gate and not s.bottle_in_pocket
                   and not self.entering and not s.roll_empty and glue_ready)
        gate = self.gate_tp(gate_in, dt) and not s.bottle_in_pocket
        if gate:
            self.entering = True
        feed = self.step == WRAP and s.bell_down and glue_ready
        return LabelerOutputs(
            conveyor_motor2=True,
            gate_motor=gate,
            clamp_valve=self.step in (CLAMP, WRAP, PRESS) and s.bottle_in_pocket,
            spin_motor=self.step in (WRAP, PRESS) and s.bell_down,
            label_feed=feed,
            cutter=self.cut_tp(self.step == PRESS, dt) and s.bell_down,
            glue_roller=feed,
            glue_heater=self.heat_demand and s.glue_temp < GLUE_MAX,
            discharge_motor=self.step == DISCHARGE and not s.bell_down and not self.outfeed_full,
        )


class Line:
    """Plant with the labeler, driven by both test controllers."""

    def __init__(self) -> None:
        self.plant = Plant(with_labeler=True)
        self.filler = TestController()
        self.labeler = LabelerTestController()
        self.log = EventLog()
        self.w = self.plant.writes()
        self.lw: LabelerWrites = self.plant.labeler_writes()

    def tick(self, dt: float = TICK, labeler_answers: bool = True) -> None:
        out = self.filler.outputs(self.w, dt)
        lab_out = self.labeler.outputs(self.lw.sensors, dt) if labeler_answers else None
        self.w, events = self.plant.step(out, dt, labeler=lab_out)
        self.lw = self.plant.labeler_writes()
        self.log.extend(events)

    def run(self, seconds: float, until=None, **kw) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(**kw)
            if until is not None and until():
                return

    def warnings(self) -> list[str]:
        return [e.format() for e in self.log.filter(warnings_only=True)]


def test_default_plant_keeps_the_stand_in():
    plant = Plant()
    assert plant.labeler is None and plant.c3 is None and plant.labeler_standin is not None
    assert plant.labeler_writes() is None


def test_writes_carry_wiring_and_the_labeler_heartbeat():
    line = Line()
    assert line.lw.e_stop_ok and not line.lw.heartbeat
    assert line.lw.sensors.glue_temp == round(config.ROOM_C * 10)  # cold start
    line.plant.e_stop_ok = False  # one E-stop chain for every PLC
    line.run(config.HEARTBEAT_S)
    assert not line.lw.e_stop_ok and line.lw.heartbeat


def test_line_labels_every_bottle_and_the_packer_takes_them():
    line = Line()
    line.plant.supply.add(15)
    line.plant.supply.per_min = 0
    line.run(400, until=lambda: line.plant.packer.taken == 15)
    lab = line.plant.labeler
    assert line.plant.packer.taken == 15
    assert lab.discharged == lab.labelled == 15
    assert not lab.lost and not line.plant.filler.lost
    assert not line.warnings(), line.warnings()
    assert {WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS, UNCLAMP, DISCHARGE} <= set(line.labeler.steps_seen)
    assert lab.web.marks == 15  # one label per bottle, none wasted


def test_animation_words_follow_the_pocket_and_c3():
    # HMI L0: %MW70 = C3 slot bits, %MW71 = pocket code; one bottle goes 1 -> 2 -> 3 -> 0
    line = Line()
    assert line.lw.anim == (0, 0)
    line.plant.supply.add(1)
    line.plant.supply.per_min = 0
    codes, c3_bits = [], set()
    for _ in range(round(200 / TICK)):
        line.tick()
        bits, code = line.lw.anim  # %MW70 C3 slots, %MW71 pocket code
        if not codes or codes[-1] != code:
            codes.append(code)
        c3_bits.add(bits)
        assert bits == line.plant.c3.slot_bits()
        if line.plant.packer.taken == 1:
            break
    assert codes == [0, 1, 2, 3, 0]
    # the bottle stepped along C3. Slots 0 and 7 are not seen at a tick end: the pusher's tick also moves
    # the belt (sub-steps), and the packer stand-in takes the bottle as soon as it reaches slot 7
    assert {1 << i for i in range(1, config.CONVEYOR_SLOTS - 1)} <= c3_bits
    assert line.lw.anim == (0, 0)  # taken by the packer: nothing left


def test_labeler_keeps_up_with_the_filler():
    # 30 bottles/min supply: the filler (about 6.3 s per bottle) is slower than the labeler (about 6.0 s),
    # so C2 never fills up: S7 only blinks for 0.5 s while a bottle passes slot 1, never the 2 s the
    # filler's Outfeed_Full filter needs
    line = Line()
    line.run(60)  # glue warm, line running
    longest = run = 0
    for _ in range(round(120 / TICK)):
        line.tick()
        run = run + 1 if line.w.s7 else 0
        longest = max(longest, run)
        assert not line.filler.outfeed_full
    assert longest * TICK < 2.0
    assert line.plant.labeler.labelled >= 18 and not line.warnings()


def test_packer_stopped_fills_c3_then_c2_and_continues():
    line = Line()
    line.run(60)
    line.plant.packer.running = False
    line.run(300)
    plant = line.plant
    assert plant.labeler.sensors().c3_full and line.labeler.outfeed_full
    assert line.labeler.step == WAIT_OUTFEED  # Blocked: a labelled bottle waits in the pocket
    assert plant.labeler.pocket is not None and plant.labeler.pocket.label_mm > 0
    assert line.w.s7  # C2 filled up behind the labeler: the filler sees it full
    assert line.filler.outfeed_full
    labelled = plant.labeler.labelled
    plant.packer.running = True
    line.run(60)
    assert plant.labeler.labelled > labelled and not plant.labeler.lost
    assert not line.warnings(), line.warnings()


def test_labeler_plc_not_answering_stops_c2():
    line = Line()
    line.plant.supply.add(10)
    line.run(60, labeler_answers=False)
    assert line.plant.labeler_outputs is None
    assert line.plant.labeler.discharged == 0 and line.plant.labeler.web.marks == 0
    assert line.plant.c2.count > 0  # bottles stand on C2: its motor belongs to the silent PLC
    assert line.plant.labeler.tank.temp_c < config.ROOM_C + 0.1  # no heater either


def test_late_ticks_keep_the_pulse_edges():
    # every tick 0.35 s: a 0.3 s Label_Mark / Spin_Pulse could start and end inside one tick
    line = Line()
    line.plant.supply.add(6)
    line.plant.supply.per_min = 0
    t = 0.0
    while line.plant.packer.taken < 6:
        line.tick(dt=0.35)
        t += 0.35
        assert t < 600, "bottles not labelled with late ticks"
    assert line.plant.labeler.labelled == 6
    assert all("slow tick" in w for w in line.warnings())  # no other warning
