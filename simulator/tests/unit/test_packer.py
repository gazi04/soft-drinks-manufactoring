"""Packer physics: stage 1 collation (gate, count sensor, collation table, group pusher).  uv run pytest

No PLC here: the tests write the PLC outputs by hand, in ticks of 0.1 s as the plant will, with C3
stepped before the packer (the plant's order). `Collation` plays the PLC's COUNT rule in the real
loop's order (read sensors, set outputs, step), so the one-tick latency is included.
"""

from dataclasses import fields

import pytest

from simulator import address_map_packer as pm
from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.machines.packer import Packer, PackerOutputs

TICK = 0.1

# PLC numbers (plcs/packer/pous/programs/main.ld, docs/packer/state-diagram.md). config.py must
# not import PLC values; if a PLC constant changes, change it here too.
PLC_PACK_SIZE = 6
PLC_PUSH_TIMEOUT_S = 3

GATE = PackerOutputs(gate_motor=True)
PUSH = PackerOutputs(group_pusher=True)
OFF = PackerOutputs()


def warnings(events) -> list[str]:
    return [e.text for e in events if e.is_warning]


def queued_c3(n: int, factory: BottleFactory) -> Conveyor:
    """C3 with a queue of n bottles (at most one per slot) standing at its exit: the labeler filled it
    while the gate was shut."""
    c3 = Conveyor("C3")
    for _ in range(n):
        assert c3.put_at_entry(factory.new())
        for _ in range(round(config.SLOT_STEP_S / TICK) * len(c3.slots)):
            c3.step(True, TICK)
    assert c3.count == n
    return c3


class Line:
    """C3 + packer stepped together, with a clock. More bottles than C3 has slots wait upstream
    (`backlog`) and are put onto C3's entry as soon as it is free, as the labeler would."""

    def __init__(self, n_bottles: int) -> None:
        self.factory = BottleFactory()
        queued = min(n_bottles, config.CONVEYOR_SLOTS)
        self.c3 = queued_c3(queued, self.factory)
        self.backlog = n_bottles - queued
        self.packer = Packer(self.c3)
        self.now = 0.0
        self.events: list = []

    def tick(self, out: PackerOutputs, dt: float = TICK) -> None:
        self.now += dt
        if self.backlog and not self.c3.occupied(0):
            self.c3.put_at_entry(self.factory.new())
            self.backlog -= 1
        self.c3.step(True, dt)  # C3 runs continuously (ConveyorMotor3)
        self.events += self.packer.step(out, dt, self.now)

    def run(self, out: PackerOutputs, seconds: float) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(out)


class Collation:
    """The PLC's COUNT step: Group_Counter (CTU on the rising edge of Bottle_Counted, PV 6),
    gate := NOT Group_Complete AND Pusher_Home. `latency` extra ticks before an output change
    reaches the plant (a slow scan or a late tick)."""

    def __init__(self, line: Line, latency: int = 0) -> None:
        self.line = line
        self.count = 0
        self._last = False
        self._pending = [OFF] * latency

    def tick(self) -> None:
        packer = self.line.packer
        counted = packer.bottle_counted  # 1. read sensors
        if counted and not self._last:
            self.count += 1
        self._last = counted
        gate = self.count < PLC_PACK_SIZE and packer.pusher_home  # 2. set outputs
        self._pending.append(PackerOutputs(gate_motor=gate))
        self.line.tick(self._pending.pop(0))  # 3. physics step

    def run(self, seconds: float) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick()


def push_and_return(line: Line) -> None:
    """PUSH until Pusher_Out, then RETRACT until Pusher_Home (the PLC's steps 20 and 25)."""
    for _ in range(round(PLC_PUSH_TIMEOUT_S / TICK)):
        if line.packer.pusher_out:
            break
        line.tick(PUSH)
    assert line.packer.pusher_out, "pusher not out within PushTimeout"
    for _ in range(round(PLC_PUSH_TIMEOUT_S / TICK)):
        if line.packer.pusher_home:
            break
        line.tick(OFF)
    assert line.packer.pusher_home, "pusher not home within PushTimeout"


# --- Outputs ------------------------------------------------------------------------
def test_outputs_in_coil_order():
    names = [f.name for f in fields(PackerOutputs)]
    coils = [pm.CONVEYOR_MOTOR3, pm.GATE_MOTOR, pm.GROUP_PUSHER, pm.WRAP_CONVEYOR, pm.FILM_FEED, pm.KNIFE,
             pm.WRAP_BAR, pm.OVEN_HEATER, pm.OVEN_BLOWER, pm.OVEN_CONVEYOR]
    assert len(names) == pm.OUTPUTS_COUNT
    assert coils == list(range(pm.OUTPUTS_START, pm.OUTPUTS_START + pm.OUTPUTS_COUNT))
    assert names[0] == "conveyor_motor3" and names[-1] == "oven_conveyor"


# --- Gate and count sensor ----------------------------------------------------------
def test_one_count_pulse_per_bottle_with_gaps_the_ctu_sees():
    line = Line(8)
    pulse_ticks, levels = [], []
    for _ in range(60):  # 6 s, gate held open
        line.tick(GATE)
        levels.append(line.packer.bottle_counted)
    # split the samples into ON and OFF runs
    runs, current, length = [], levels[0], 0
    for level in levels:
        if level == current:
            length += 1
        else:
            runs.append((current, length))
            current, length = level, 1
    runs.append((current, length))
    on_runs = [n for level, n in runs if level]
    off_between = [n for level, n in runs[1:-1] if not level]
    assert len(on_runs) == 8  # every bottle of the queue: one pulse
    assert all(n == round(config.BOTTLE_COUNTED_S / TICK) for n in on_runs)  # 2 ticks ON
    assert min(off_between) >= 3  # >= 3 ticks OFF: the CTU sees a new rising edge
    assert len(line.packer.table) == 8 and line.c3.count == 0


def test_bottle_reaches_the_table_after_the_gate_time():
    line = Line(1)
    line.tick(GATE)
    assert line.packer.entering is not None and line.packer.table == []
    left_at = line.now
    while not line.packer.table:
        line.tick(OFF)  # the gate shuts behind the bottle: it is past the gate and lands anyway
    assert line.now - left_at == pytest.approx(config.PACKER_GATE_S)


@pytest.mark.parametrize("latency", [0, 1])
def test_plc_count_rule_admits_exactly_six_the_seventh_waits(latency):
    line = Line(10)
    plc = Collation(line, latency)
    plc.run(10.0)
    assert plc.count == PLC_PACK_SIZE
    assert len(line.packer.table) == 6 and line.packer.entering is None
    assert line.packer.bottle_at_gate  # the 7th stands at C3's exit
    assert [b.id for b in line.packer.table] == [1, 2, 3, 4, 5, 6]
    assert not warnings(line.events)


def test_gate_shut_takes_nothing():
    line = Line(3)
    line.run(OFF, 3.0)
    assert line.packer.bottle_at_gate and not line.packer.bottle_counted
    assert line.packer.table == [] and line.c3.count == 3


def test_long_step_takes_one_bottle_and_shows_its_pulse():
    line = Line(3)
    line.tick(GATE, dt=0.35)  # a late tick
    assert line.c3.count == 2 and line.packer.entering is not None
    assert line.packer.bottle_counted  # the pulse began in this step: visible at its end


# --- Pusher -------------------------------------------------------------------------
def collated(n: int = 6) -> Line:
    """A line with n bottles on the collation table and C3 empty."""
    line = Line(n)
    line.run(GATE, n * config.SLOT_STEP_S + 1.0)
    assert len(line.packer.table) == n and line.c3.count == 0
    line.events.clear()
    return line


def test_push_moves_the_group_onto_the_wrap_conveyor_within_push_timeout():
    line = collated()
    group = list(line.packer.table)
    line.tick(PUSH)
    assert not line.packer.pusher_home and not line.packer.pusher_out  # Pusher_Home drops at once
    started = line.now - TICK
    while not line.packer.pusher_out:
        line.tick(PUSH)
    assert line.now - started == pytest.approx(config.PUSH_OUT_S)
    assert config.PUSH_OUT_S < PLC_PUSH_TIMEOUT_S and config.PUSH_RETURN_S < PLC_PUSH_TIMEOUT_S
    assert line.packer.table == [] and len(line.packer.wrap_packs) == 1
    pack = line.packer.wrap_packs[0]
    assert pack.id == 1 and pack.bottles == group  # the same 6 bottles, in order
    returned = line.now
    while not line.packer.pusher_home:
        line.tick(OFF)
    assert line.now - returned == pytest.approx(config.PUSH_RETURN_S)
    assert not warnings(line.events)


def test_push_stopped_halfway_leaves_the_group_on_the_table():
    line = collated()
    line.run(PUSH, 0.5)  # an E-stop in the middle of PUSH
    line.run(OFF, 1.0)
    assert line.packer.pusher_home and len(line.packer.table) == 6 and line.packer.wrap_packs == []
    push_and_return(line)  # the restart pushes the same group again
    assert len(line.packer.wrap_packs) == 1 and line.packer.table == []


def test_pusher_held_out_pushes_once():
    line = collated()
    line.run(PUSH, 3.0)
    assert line.packer.groups_pushed == 1 and len(line.packer.wrap_packs) == 1


def test_two_full_cycles_make_two_packs():
    line = Line(12)
    for _ in range(2):
        plc = Collation(line)
        plc.run(8.0)  # COUNT
        assert len(line.packer.table) == 6
        push_and_return(line)  # PUSH, RETRACT (the counter is reset in RETRACT: a new Collation)
    packs = line.packer.wrap_packs
    assert [p.id for p in packs] == [1, 2] and line.packer.groups_pushed == 2
    assert [b.id for p in packs for b in p.bottles] == list(range(1, 13))
    assert packs[1].name == "Pack 2 (bottles 7-12)"
    assert not warnings(line.events)


# --- Audit --------------------------------------------------------------------------
def test_seventh_bottle_warns_once():
    line = Line(8)
    line.run(GATE, 8 * config.SLOT_STEP_S + 1.0)  # gate never shut: the PLC forgot to count
    assert len(line.packer.table) == 8
    assert warnings(line.events) == ["7 bottles on the collation table (room for 6)"]


def test_bottle_onto_the_table_with_the_pusher_out_warns():
    line = Line(1)
    line.tick(PackerOutputs(gate_motor=True, group_pusher=True))  # Manual: gate and pusher together
    line.run(PUSH, 0.4)  # the bottle lands while the pusher is halfway out
    line.run(OFF, 1.0)
    assert len(line.packer.table) == 1 and line.packer.wrap_packs == []
    assert warnings(line.events) == ["bottle onto the collation table with the pusher out"]


def test_incomplete_group_pushed_warns():
    line = collated(5)
    push_and_return(line)
    assert len(line.packer.wrap_packs) == 1
    assert warnings(line.events) == ["Pack 1 (bottles 1-5) pushed with 5 bottles, not 6"]


def test_empty_table_push_makes_no_pack():
    line = Line(0)
    push_and_return(line)
    assert line.packer.wrap_packs == [] and not warnings(line.events)
