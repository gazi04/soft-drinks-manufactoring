"""Plant: supply -> C1 -> filler -> C2 -> labeler, driven by a test controller.  uv run pytest

The TestController below is TEST INPUT, not part of the simulator. To test the
physics without Docker, something has to set the PLC outputs, so it copies the
filler PLC's rules in a few lines (WORK / WAIT_OUTFEED / INDEX, door pulse,
2 s / 3 s Outfeed_Full filter; docs/filler/state-diagram.md). From stage 5 on,
the real OpenPLC program sets the outputs and this class is not used.

Like the real loop, the controller sees the sensors of the previous tick, so the
belt overruns one tick after the S8 edge, as it will with the real PLC.
"""

import random

import pytest

from simulator.bottle import Bottle
from simulator.events import EventLog, Source
from simulator.machines.filler import FillerOutputs
from simulator.plant import Plant, SimWrites

TICK = 0.1
EPS = 1e-9
WORK, WAIT_OUTFEED, INDEX = 10, 15, 20


class TestController:
    __test__ = False  # not a pytest test class

    def __init__(self) -> None:
        self.step = WORK  # as after Auto + Start
        self.rinse_t = self.cap_t = self.index_t = 0.0
        self.full_on_t = self.full_off_t = 0.0
        self.outfeed_full = False
        self.door = False  # door pulse armed for this INDEX (S2 at step start)
        self.prev_s8 = True
        self.indexes = 0

    def _enter_index(self, w: SimWrites) -> None:
        self.step, self.index_t, self.door = INDEX, 0.0, w.s2
        self.rinse_t = self.cap_t = 0.0

    def outputs(self, w: SimWrites, dt: float) -> FillerOutputs:
        # Outfeed_Full: S7 ON 2 s -> TRUE, S7 OFF 3 s -> FALSE
        self.full_on_t = self.full_on_t + dt if w.s7 else 0.0
        self.full_off_t = 0.0 if w.s7 else self.full_off_t + dt
        if self.full_on_t >= 2.0 - EPS:
            self.outfeed_full = True
        if self.full_off_t >= 3.0 - EPS:
            self.outfeed_full = False

        # Transitions
        if self.step == INDEX and w.s8 and not self.prev_s8:
            self.step = WORK
            self.indexes += 1
        elif self.step == WAIT_OUTFEED and not self.outfeed_full:
            self._enter_index(w)
        elif self.step == WORK:
            rinse_done = not w.s3 or self.rinse_t >= 2.0 - EPS
            fill_done = not w.s4 or w.fill_level >= 500
            cap_done = not w.s5 or self.cap_t >= 1.5 - EPS
            present = w.s2 or w.s3 or w.s4 or w.s5
            if rinse_done and fill_done and cap_done and present:
                if w.s5 and self.outfeed_full:
                    self.step = WAIT_OUTFEED
                else:
                    self._enter_index(w)
        self.prev_s8 = w.s8

        # Outputs from the step
        out = FillerOutputs(conveyor1=True)
        if self.step == WORK:
            out.rinse_valve = w.s3 and self.rinse_t < 2.0 - EPS
            out.fill_valve = w.s4 and w.fill_level < 500 and w.product_available
            out.cap_motor = w.s5 and self.cap_t < 1.5 - EPS
            self.rinse_t += dt if out.rinse_valve else 0.0
            self.cap_t += dt if out.cap_motor else 0.0
        elif self.step == INDEX:
            out.index_motor = True
            out.door_motor = self.door and self.index_t < 0.5 - EPS  # TP 0.5 s
            self.index_t += dt
        return out


def run(plant: Plant, ctrl: TestController, log: EventLog, seconds: float, until=None) -> None:
    w = plant.writes()
    for _ in range(round(seconds / TICK)):
        w, events = plant.step(ctrl.outputs(w, TICK), TICK)
        log.extend(events)
        if until is not None and until():
            return


def discharged(log: EventLog) -> list[str]:
    return [e.text for e in log.recent(10_000) if e.text.startswith("left filler (")]


def warnings(log: EventLog) -> list[str]:
    return [e.format() for e in log.filter(warnings_only=True)]


# --- Wiring values and sensors -----------------------------------------------------
def test_writes_carry_wiring_values():
    plant = Plant()
    w = plant.writes()
    assert w.product_available and w.e_stop_ok and not w.heartbeat
    plant.product_available = False
    plant.e_stop_ok = False
    w, _ = plant.step(FillerOutputs(), 1.0)
    assert not w.product_available and not w.e_stop_ok
    assert w.heartbeat  # toggled after 1 s


def test_s7_watches_c2_slot_1():
    plant = Plant()
    plant.c2.slots[0] = Bottle(1)
    assert not plant.writes().s7
    plant.c2.slots[1] = Bottle(2)
    assert plant.writes().s7


# --- Closed loop with the test controller ------------------------------------------
def test_twenty_bottles_through_the_line():
    plant, ctrl, log = Plant(), TestController(), EventLog(max_events=10_000)
    plant.supply.per_min = 0
    plant.supply.add(20)
    run(plant, ctrl, log, 600.0, until=lambda: plant.labeler.taken == 20)
    assert plant.labeler.taken == 20
    assert discharged(log) == ["left filler (500 ml)"] * 20
    assert warnings(log) == []
    assert plant.filler.lost == []


def test_supply_faster_than_filler_c1_fills_up():
    plant, ctrl, log = Plant(), TestController(), EventLog(max_events=10_000)
    run(plant, ctrl, log, 120.0)  # 30 bottles/min, the filler makes ~9/min
    assert warnings(log) == []
    assert plant.c1.count == 8  # C1 full, the supply waits
    assert len(discharged(log)) >= 10


def test_labeler_stopped_blocks_without_crash_and_resumes():
    plant, ctrl, log = Plant(), TestController(), EventLog(max_events=10_000)
    plant.labeler.running = False
    run(plant, ctrl, log, 300.0)
    assert plant.c2.count == 7  # queue stands at the S7 slot ...
    assert not plant.c2.occupied(0)  # ... and slot 0 stays free: the reserve was not needed
    assert ctrl.step == WAIT_OUTFEED  # Blocked
    assert warnings(log) == []  # no crash on C2: S7 at slot 1 leaves room

    out_before = len(discharged(log))
    plant.labeler.running = True
    run(plant, ctrl, log, 60.0)
    assert len(discharged(log)) > out_before  # flows again by itself
    assert warnings(log) == []


def test_s7_slot_choice_short_work_cycle(monkeypatch):
    """Why S7 watches slot 1, not slot 0 (config.S7_SLOT).

    C2 is queued up to slot 1, bottle 1 is at the cap station and bottle 2
    (already full) at the fill station, nothing behind. Bottle 1 lands on slot 0
    and stays. In the next WORK only the cap runs (1.5 s), shorter than the 2 s
    Outfeed_Full filter. With S7 on slot 0 the controller indexes and bottle 2
    crashes onto bottle 1; with S7 on slot 1 C2 is already "full" and it waits.
    """

    def scenario() -> tuple[TestController, EventLog]:
        plant, ctrl, log = Plant(), TestController(), EventLog()
        plant.labeler.running = False
        plant.supply.per_min = 0
        for i in range(1, 8):
            plant.c2.slots[i] = Bottle(100 + i)
        plant.filler.pockets[3] = Bottle(1, level_ml=500, rinsed=True)  # cap station
        plant.filler.pockets[2] = Bottle(2, level_ml=500, rinsed=True)  # fill station
        run(plant, ctrl, log, 30.0)
        return ctrl, log

    ctrl, log = scenario()  # S7_SLOT = 1
    assert warnings(log) == [] and ctrl.step == WAIT_OUTFEED

    monkeypatch.setattr("simulator.config.S7_SLOT", 0)
    ctrl, log = scenario()
    assert any("crash" in w for w in warnings(log))


# --- Late ticks: the real loop never ticks exactly every 0.1 s ----------------------
def run_jittered(plant: Plant, ctrl: TestController, log: EventLog, seconds: float, next_dt) -> None:
    """Like run(), but every tick has its own dt (the real loop measures dt)."""
    w = plant.writes()
    t = 0.0
    while t < seconds:
        dt = next_dt()
        w, events = plant.step(ctrl.outputs(w, dt), dt)
        log.extend(events)
        t += dt


def bottle_warnings(log: EventLog) -> list[str]:
    return [e.format() for e in log.filter(source=Source.BOTTLE, warnings_only=True)]


def test_uneven_ticks_no_false_warnings():
    rnd = random.Random(1)
    plant, ctrl, log = Plant(), TestController(), EventLog(max_events=100_000)
    run_jittered(plant, ctrl, log, 600.0, lambda: rnd.uniform(0.08, 0.2))
    assert warnings(log) == []
    assert plant.labeler.taken >= 80


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_stalls_up_to_0_4_s_only_flagged_as_slow_ticks(seed):
    # 5 % of the ticks are 0.4 s late (far worse than the measured 102-110 ms loop)
    rnd = random.Random(seed)
    plant, ctrl, log = Plant(), TestController(), EventLog(max_events=100_000)
    run_jittered(plant, ctrl, log, 600.0, lambda: 0.4 if rnd.random() < 0.05 else 0.1)
    assert bottle_warnings(log) == []  # no bottle harmed by the late ticks ...
    assert any("slow tick" in w for w in warnings(log))  # ... and each one is reported


def test_s8_pulse_inside_one_late_tick_is_not_lost():
    plant = Plant()
    plant.filler._phase = 1.4  # belt just before the next pitch boundary
    w, _ = plant.step(FillerOutputs(index_motor=True), 0.4)  # boundary + 0.3 s past it
    assert plant.filler._phase > plant.filler.cam_window * plant.filler.pitch_s  # cam already OFF ...
    assert w.s8  # ... but the PLC still gets the edge in this tick
    w, _ = plant.step(FillerOutputs(), 0.1)
    assert not w.s8  # the next tick shows the real cam again


# --- Setpoints from the PLC --------------------------------------------------------
def test_apply_setpoints():
    plant = Plant()
    assert plant.apply_setpoints(330, 2000, 1500) == []  # normal values: no warning
    assert plant.filler.fill_setpoint_ml == 330  # underfill limit follows the HMI
    plant.apply_setpoints(0, 0, 0)  # PLC's first scan not done yet: ignored
    assert plant.filler.fill_setpoint_ml == 330
    events = plant.apply_setpoints(330, 1000, 1500)
    assert [e.text.split(" is")[0] for e in events] == ["RinseTime 1000 ms"]
    assert plant.apply_setpoints(330, 1000, 1500) == []  # same value again: no repeat
