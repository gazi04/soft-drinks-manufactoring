"""Filler physics: index belt, door, stations, discharge.  uv run pytest

No PLC here: the tests write the PLC outputs by hand. `Rig` steps C1, the
filler and C2 in ticks of 0.1 s, the way the plant will do it in stage 4.
"""

import pytest

from simulator.bottle import Bottle, BottleFactory
from simulator.conveyor import Conveyor
from simulator.events import EventLog
from simulator.machines.filler import CAP, ENTRY, FILL, RINSE, Filler, FillerOutputs, FillerSensors

TICK = 0.1
OFF = FillerOutputs()


class Rig:
    """C1 + filler + C2, stepped together. `n_bottles` are queued at C1's exit."""

    def __init__(self, n_bottles: int = 0) -> None:
        self.factory = BottleFactory()
        self.c1 = Conveyor("C1")
        self.c2 = Conveyor("C2")
        self.filler = Filler(self.c1, self.c2)
        self.log = EventLog()
        self.now = 0.0
        for i in range(n_bottles):
            self.c1.slots[-1 - i] = self.factory.new()  # bottle 1 at the exit

    def tick(self, out: FillerOutputs = OFF, product: bool = True, c2_motor: bool = False) -> FillerSensors:
        self.now += TICK
        self.c1.step(True, TICK)  # C1 runs all the time, like on the real line
        self.log.extend(self.filler.step(out, product, TICK, self.now))
        self.c2.step(c2_motor, TICK)
        return self.filler.sensors()

    def run(self, seconds: float, out: FillerOutputs = OFF, **kw) -> list[FillerSensors]:
        return [self.tick(out, **kw) for _ in range(round(seconds / TICK))]

    def texts(self, bottle_id: int) -> list[str]:
        return [e.text for e in self.log.filter(bottle_id=bottle_id)]

    def warnings(self) -> list[str]:
        return [e.text for e in self.log.filter(warnings_only=True)]


def rising_edges(values: list[bool], before: bool) -> int:
    count = 0
    for value in values:
        if value and not before:
            count += 1
        before = value
    return count


BELT = FillerOutputs(index_motor=True)


# --- 3a: index belt, S8 cam, station sensors -----------------------------------------
def test_belt_at_rest_s8_on():
    rig = Rig()
    assert rig.filler.sensors().s8  # the belt starts standing at a station


def test_one_pitch_gives_one_shift_and_one_s8_edge():
    rig = Rig()
    rig.filler.pockets[ENTRY] = Bottle(1)
    s8 = [s.s8 for s in rig.run(1.5, BELT)]
    assert rig.filler.shifts == 1
    assert rising_edges(s8, before=True) == 1
    assert s8[-1]  # the edge comes at the end of the pitch
    assert rig.filler.pockets[RINSE].id == 1


def test_overrun_after_edge_keeps_s8_and_stations_on():
    rig = Rig()
    rig.filler.pockets[ENTRY:CAP] = [Bottle(1), Bottle(2), Bottle(3)]
    rig.run(1.5, BELT)
    s = rig.run(0.15, BELT)[-1]  # PLC reaction delay: the belt runs a little longer
    assert rig.filler.shifts == 1
    assert s.s8 and s.s3 and s.s4 and s.s5


def test_station_sensors_off_while_moving():
    rig = Rig()
    rig.filler.pockets[RINSE:] = [Bottle(1), Bottle(2), Bottle(3)]
    assert rig.filler.sensors().s3  # standing: seen
    s = rig.run(0.7, BELT)[-1]  # mid-move
    assert not (s.s3 or s.s4 or s.s5 or s.s8)


def test_motor_off_belt_stands():
    rig = Rig()
    rig.filler.pockets[ENTRY] = Bottle(1)
    rig.run(5.0)
    assert rig.filler.shifts == 0
    assert rig.filler.pockets[ENTRY].id == 1


def test_long_dt_gives_several_shifts():
    rig = Rig()
    rig.filler.step(BELT, True, 3.0, 3.0)  # e.g. after a laptop stall
    assert rig.filler.shifts == 2


# --- 3b: door ----------------------------------------------------------------------
def test_door_pulse_admits_exactly_one_bottle():
    rig = Rig(n_bottles=8)
    assert rig.filler.sensors().s2
    rig.run(0.5, FillerOutputs(door_motor=True))  # the PLC's 0.5 s TP
    rig.run(2.0)  # door closed again
    assert rig.filler.pockets[ENTRY].id == 1
    assert rig.texts(1) == ["entered filler"]
    assert rig.warnings() == []
    assert rig.filler.sensors().s2  # bottle 2 waits at the door


def test_door_held_open_lets_a_second_bottle_in():
    rig = Rig(n_bottles=8)
    rig.run(2.0, FillerOutputs(door_motor=True))
    assert rig.filler.pockets[ENTRY].id == 1
    assert rig.filler.lost  # bottle 2 (and maybe 3) fell off
    assert any("two bottles in one pocket" in w for w in rig.warnings())


def test_door_without_bottle_does_nothing():
    rig = Rig()
    rig.run(1.0, FillerOutputs(door_motor=True))
    assert rig.filler.pockets == [None, None, None, None]
    assert rig.log.recent(10) == []


# --- 3c: stations ------------------------------------------------------------------
FILL_ON = FillerOutputs(fill_valve=True)


def test_fill_5_s_gives_500_ml():
    rig = Rig()
    rig.filler.pockets[FILL] = Bottle(1)
    rig.run(2.0, FILL_ON)
    assert rig.filler.sensors().fill_level == 200
    rig.run(3.0, FILL_ON)
    assert rig.filler.pockets[FILL].level_ml == pytest.approx(500)
    assert rig.filler.sensors().fill_level == 500
    rig.tick()  # valve closes -> the flow period ends
    assert rig.texts(1) == ["filled to 500 ml"]


def test_no_product_no_fill_no_spill():
    rig = Rig()
    rig.filler.pockets[FILL] = Bottle(1)
    rig.run(3.0, FILL_ON, product=False)
    assert rig.filler.pockets[FILL].level_ml == 0
    assert rig.warnings() == []


def test_valve_without_bottle_spills_once():
    rig = Rig()
    rig.run(2.0, FILL_ON)
    assert rig.warnings() == ["spill: product flows with no bottle at the fill station"]
    assert rig.filler.sensors().fill_level == 0


def test_half_filled_bottle_keeps_level_and_is_topped_up():
    rig = Rig()
    rig.filler.pockets[FILL] = Bottle(1)
    rig.run(2.0, FILL_ON)  # E-stop at 200 ml: valve closes
    rig.run(3.0)
    rig.run(3.0, FILL_ON)  # restart: WORK fills up to 500
    rig.tick()
    assert rig.texts(1) == ["filled to 200 ml", "filled to 500 ml"]
    rig.run(1.5, BELT)  # the level travels with the bottle
    assert rig.filler.pockets[CAP].level_ml == pytest.approx(500)
    assert rig.filler.sensors().fill_level == 0  # nothing at S4 now


def test_rinse_needs_minimum_time():
    rig = Rig()
    rig.filler.pockets[RINSE] = Bottle(1)
    rig.run(0.5, FillerOutputs(rinse_valve=True))
    assert not rig.filler.pockets[RINSE].rinsed
    rig.run(1.5, FillerOutputs(rinse_valve=True))  # 2 s in total, like the PLC's RinseTime
    assert rig.filler.pockets[RINSE].rinsed
    assert rig.texts(1) == ["rinsed"]


def test_cap_needs_minimum_time():
    rig = Rig()
    rig.filler.pockets[CAP] = Bottle(1)
    rig.run(0.5, FillerOutputs(cap_motor=True))
    assert not rig.filler.pockets[CAP].capped
    rig.run(1.0, FillerOutputs(cap_motor=True))
    assert rig.filler.pockets[CAP].capped


# --- 3d: discharge and audit, full sequence ----------------------------------------
def plc_index(rig: Rig, c2_motor: bool = False) -> list[FillerSensors]:
    """Mini fake PLC, step INDEX: door pulse 0.5 s if S2, belt until the S8 rising edge,
    then one more tick (the PLC's reaction delay = belt overrun)."""
    door = rig.filler.sensors().s2
    before = rig.filler.sensors().s8
    seen = []
    for n in range(50):  # 5 s = the PLC's index jam timeout
        s = rig.tick(FillerOutputs(index_motor=True, door_motor=door and n < 5), c2_motor=c2_motor)
        seen.append(s)
        if s.s8 and not before:
            seen.append(rig.tick(BELT, c2_motor=c2_motor))
            return seen
        before = s.s8
    raise AssertionError("index jam: no S8 edge within 5 s")


def plc_work(rig: Rig, c2_motor: bool = False) -> None:
    """Mini fake PLC, step WORK: rinse 2 s, fill to 500 ml, cap 1.5 s, in parallel."""
    for n in range(100):
        t = n * TICK
        s = rig.filler.sensors()
        out = FillerOutputs(
            rinse_valve=s.s3 and t < 2.0 - 1e-9,
            fill_valve=s.s4 and s.fill_level < 500,
            cap_motor=s.s5 and t < 1.5 - 1e-9,
        )
        if not (out.rinse_valve or out.fill_valve or out.cap_motor):
            rig.tick(c2_motor=c2_motor)  # the valve-closed tick ends the flow period
            return
        rig.tick(out, c2_motor=c2_motor)
    raise AssertionError("WORK did not finish")


def test_one_bottle_through_the_machine():
    rig = Rig(n_bottles=1)
    for _ in range(3):  # door + shift to rinse, then fill, then cap
        plc_index(rig)
        plc_work(rig)
    seen = plc_index(rig)  # last index: out onto C2
    seen += rig.run(1.0)  # the S6 pulse outlasts the INDEX step
    assert rig.texts(1) == ["entered filler", "rinsed", "filled to 500 ml", "capped", "left filler (500 ml)"]
    assert rig.warnings() == []
    assert sum(s.s6 for s in seen) * TICK == pytest.approx(0.3)  # S6 pulse 0.3 s = 3 ticks
    bottle = rig.c2.slots[0]
    assert bottle.id == 1 and bottle.rinsed and bottle.capped


def test_eight_bottles_all_stations_busy_no_warnings():
    rig = Rig(n_bottles=8)  # C2 has 8 slots, so all 8 fit without a labeler
    for _ in range(8 + 3):  # 3 more cycles to empty the machine
        plc_index(rig, c2_motor=True)
        plc_work(rig, c2_motor=True)
    assert rig.warnings() == []
    assert rig.c2.count == 8
    for bottle in rig.c2.slots:
        assert bottle.rinsed and bottle.capped and round(bottle.level_ml) == 500


def test_audit_bad_bottle():
    rig = Rig()
    rig.filler.pockets[CAP] = Bottle(1, level_ml=320)
    seen = rig.run(1.5, BELT)
    assert seen[-1].s6
    assert rig.warnings() == [
        "left filler not rinsed",
        "left filler uncapped",
        "left filler underfilled: 320 ml < 500 ml",
    ]


def test_audit_uses_the_rounded_level():
    rig = Rig()
    rig.filler.pockets[CAP] = Bottle(1, level_ml=499.9999, rinsed=True, capped=True)
    rig.run(1.5, BELT)
    assert rig.warnings() == []


def test_crash_when_c2_entry_occupied():
    rig = Rig()
    rig.c2.slots[0] = Bottle(99)
    rig.filler.pockets[CAP] = Bottle(1, level_ml=500, rinsed=True, capped=True)
    rig.run(1.5, BELT)
    assert rig.warnings() == ["crash: C2 entry occupied, fell off"]
    assert [b.id for b in rig.filler.lost] == [1]
