"""Stand-ins: supply, labeler, heartbeat.  uv run pytest"""

from simulator.bottle import Bottle, BottleFactory
from simulator.conveyor import Conveyor
from simulator.standins import Heartbeat, LabelerStandIn, Supply

TICK = 0.1


def run_supply(supply: Supply, c1: Conveyor, seconds: float, motor_on: bool = True) -> int:
    """Run supply + C1 like the plant does; returns the number of bottles put on C1."""
    put = 0
    for n in range(round(seconds / TICK)):
        put += len(supply.step(TICK, n * TICK))
        c1.step(motor_on, TICK)
    return put


# --- Supply ------------------------------------------------------------------------
def test_supply_rate_30_per_min():
    c1 = Conveyor("C1")
    assert run_supply(Supply(c1, BottleFactory(), per_min=30), c1, 10.0) == 5  # one per 2 s


def test_supply_paused():
    c1 = Conveyor("C1")
    supply = Supply(c1, BottleFactory(), per_min=30)
    supply.paused = True
    supply.add(3)
    assert run_supply(supply, c1, 10.0) == 0


def test_supply_add_n_without_rate():
    c1 = Conveyor("C1")
    supply = Supply(c1, BottleFactory(), per_min=0)
    supply.add(3)
    assert run_supply(supply, c1, 10.0) == 3
    assert supply.pending == 0
    assert [b.id for b in c1.slots if b] == [3, 2, 1]  # slots go entry -> exit: bottle 1 is in front


def test_supply_waits_when_entry_taken():
    c1 = Conveyor("C1")
    supply = Supply(c1, BottleFactory(), per_min=30)
    assert run_supply(supply, c1, 10.0, motor_on=False) == 1  # C1 stands: entry stays taken
    assert c1.count == 1
    assert run_supply(supply, c1, 0.6) == 1  # C1 moves -> the waiting bottle comes at once


# --- Labeler -----------------------------------------------------------------------
def fill_c2(c2: Conveyor) -> None:
    for i in range(len(c2.slots)):
        c2.slots[i] = Bottle(i + 1)


def run_labeler(labeler: LabelerStandIn, c2: Conveyor, seconds: float) -> None:
    for n in range(round(seconds / TICK)):
        c2.step(labeler.motor_on, TICK)
        labeler.step(TICK, n * TICK)


def test_labeler_takes_one_every_2_s():
    c2 = Conveyor("C2")
    fill_c2(c2)
    labeler = LabelerStandIn(c2)
    run_labeler(labeler, c2, 5.0)  # at 0.1 s, 2.1 s, 4.1 s
    assert labeler.taken == 3


def test_labeler_stopped_c2_fills_up():
    c2 = Conveyor("C2")
    labeler = LabelerStandIn(c2)
    labeler.running = False
    for i in range(8):
        c2.put_at_entry(Bottle(i + 1))
        run_labeler(labeler, c2, 1.0)
    assert labeler.taken == 0
    assert c2.count == 8 and c2.occupied(1)  # the queue reaches the S7 slot


# --- Heartbeat ---------------------------------------------------------------------
def test_heartbeat_toggles_every_second():
    hb = Heartbeat()
    values = [hb.step(TICK) for _ in range(30)]  # 3 s
    changes = sum(1 for a, b in zip(values, values[1:]) if a != b)
    assert values[8] is False and values[9] is True  # first toggle after 1 s
    assert changes == 3  # at 1 s, 2 s and 3 s


def test_heartbeat_frozen_when_disabled():
    hb = Heartbeat()
    hb.enabled = False
    assert {hb.step(TICK) for _ in range(50)} == {False}
