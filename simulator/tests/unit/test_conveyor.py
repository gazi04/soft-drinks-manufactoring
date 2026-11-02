"""Conveyor: belt motion, accumulation, sensors.  uv run pytest

The conveyors here use the defaults from config.py: 8 slots, 0.5 s per slot step.
"""

from simulator.bottle import Bottle, BottleFactory
from simulator.conveyor import Conveyor

TICK = 0.1  # the simulator's tick


def run(conveyor: Conveyor, seconds: float, motor_on: bool = True) -> None:
    """Run the belt for some seconds in ticks of 0.1 s, like the tick loop does."""
    for _ in range(round(seconds / TICK)):
        conveyor.step(motor_on, TICK)


def positions(conveyor: Conveyor) -> list[int]:
    return [i for i, bottle in enumerate(conveyor.slots) if bottle is not None]


def test_motor_off_nothing_moves():
    c1 = Conveyor("C1")
    c1.put_at_entry(BottleFactory().new())
    run(c1, 10, motor_on=False)
    assert positions(c1) == [0]


def test_one_slot_per_step_and_stops_at_exit():
    c1 = Conveyor("C1")
    c1.put_at_entry(BottleFactory().new())
    run(c1, 0.5)
    assert positions(c1) == [1]
    run(c1, 3.0)  # 6 more steps -> exit (slot 7)
    assert positions(c1) == [7]
    run(c1, 5.0)  # nobody takes it: it stays at the exit
    assert positions(c1) == [7]


def test_float_rounding_does_not_delay_a_step():
    # 0.1 added eight times is 0.7999999999999999, not 0.8 (floating point)
    c1 = Conveyor("C1", step_s=0.8)
    c1.put_at_entry(BottleFactory().new())
    for _ in range(8):
        c1.step(True, 0.1)
    assert positions(c1) == [1]


def test_long_dt_gives_several_steps():
    c1 = Conveyor("C1")
    c1.put_at_entry(BottleFactory().new())
    c1.step(True, 1.0)  # e.g. after a laptop stall
    assert positions(c1) == [2]


def test_stopped_belt_keeps_its_position():
    c1 = Conveyor("C1")
    c1.put_at_entry(BottleFactory().new())
    run(c1, 0.3)
    run(c1, 5.0, motor_on=False)
    run(c1, 0.2)  # 0.3 + 0.2 = one full step
    assert positions(c1) == [1]


def test_bottles_accumulate_at_the_exit():
    c1 = Conveyor("C1")
    factory = BottleFactory()
    for _ in range(4):
        c1.put_at_entry(factory.new())
        run(c1, 0.5)  # move it off the entry so the next one fits
    run(c1, 10)
    assert positions(c1) == [4, 5, 6, 7]
    assert [b.id for b in c1.slots[4:]] == [4, 3, 2, 1]  # first bottle in front


def test_packed_queue_moves_as_one():
    c1 = Conveyor("C1")
    factory = BottleFactory()
    for _ in range(4):
        c1.put_at_entry(factory.new())
        run(c1, 0.5)
    run(c1, 10)  # packed in slots 4..7
    assert c1.take_at_exit().id == 1
    run(c1, 0.5)  # one step: every bottle moves, no gap opens
    assert positions(c1) == [5, 6, 7]


def test_put_on_occupied_entry_is_refused():
    c2 = Conveyor("C2")
    factory = BottleFactory()
    first = factory.new()
    assert c2.put_at_entry(first)
    assert not c2.put_at_entry(factory.new())
    assert c2.slots[0] is first


def test_full_sensor_flickers_while_flowing_and_stays_on_when_full():
    c2 = Conveyor("C2")
    factory = BottleFactory()
    sensor_slot = 1
    history = []  # sensor value per tick
    taking = True
    for tick in range(600):  # 60 s
        if tick == 300:
            taking = False  # the consumer (labeler) stops after 30 s
        if tick % 20 == 0:  # a new bottle every 2 s
            c2.put_at_entry(factory.new())
        if taking:
            c2.take_at_exit()
        c2.step(True, TICK)
        history.append(c2.occupied(sensor_slot))

    flowing = history[:300]
    rising_edges = sum(1 for a, b in zip(flowing, flowing[1:]) if b and not a)
    longest_on = max_run(flowing, True)
    assert rising_edges >= 10  # flickers: one pulse per bottle
    assert longest_on < 20  # never ON for 2 s without a break -> PLC filter stays FALSE

    assert all(history[-30:])  # labeler stopped: queue back to the sensor, ON for the last 3 s


def max_run(values: list[bool], wanted: bool) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value == wanted else 0
        longest = max(longest, current)
    return longest


def test_slot_bits_exit_bottle_and_count():
    c1 = Conveyor("C1")
    factory = BottleFactory()
    c1.slots[7] = factory.new()
    c1.slots[0] = factory.new()
    assert c1.slot_bits() == 0b1000_0001
    assert c1.count == 2
    assert c1.occupied(-1) and c1.occupied(0) and not c1.occupied(3)
    assert c1.exit_bottle.id == 1  # peek: still there
    assert c1.count == 2
    assert c1.take_at_exit().id == 1
    assert c1.exit_bottle is None
    assert c1.take_at_exit() is None


def test_queue_restarts_from_rest_after_take():
    # A queue stands at the exit while the belt runs on under it. When the front bottle
    # is taken, the next one needs a FULL slot step to reach the exit (not the rest of
    # an old step): this is what lets a 0.5 s door pulse admit only one bottle.
    c1 = Conveyor("C1")
    for i in range(3):
        c1.slots[-1 - i] = Bottle(i + 1)
    run(c1, 0.4)  # belt runs, queue stands; phase would be 0.4
    c1.take_at_exit()
    run(c1, 0.4)
    assert c1.exit_bottle is None  # 0.4 s is not enough
    run(c1, 0.1)
    assert c1.exit_bottle.id == 2
