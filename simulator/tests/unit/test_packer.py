"""Packer physics: stage 1 collation (gate, count sensor, collation table, group pusher) and
stage 3 oven + outfeed table + palletizer stand-in.  uv run pytest

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
from simulator.machines.packer import Oven, OutfeedTable, Pack, Packer, PackerOutputs
from simulator.standins import PalletizerStandIn

TICK = 0.1

# PLC numbers (plcs/packer/pous/programs/main.ld, docs/packer/state-diagram.md). config.py must
# not import PLC values; if a PLC constant changes, change it here too.
PLC_PACK_SIZE = 6
PLC_PUSH_TIMEOUT_S = 3
PLC_OVEN_SETPOINT = 1900  # 0.1 degC
PLC_OVEN_HYST = 50
PLC_OVEN_BAND = 150
PLC_OVEN_MAX = 2300
PLC_HEAT_TIMEOUT_S = 120
PLC_AIRFLOW_TIMEOUT_S = 3
PLC_OVEN_JAM_S = 20

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


# --- Oven: blower and air temperature (stage 3) ------------------------------------------
def make_pack(pack_id: int) -> Pack:
    factory = BottleFactory()
    return Pack(pack_id, [factory.new() for _ in range(6)])


def oven_controller(oven: Oven, seconds: float, now: float = 0.0) -> tuple[list[int], list[str]]:
    """The PLC's oven rules 6-7 in loop order for `seconds`, blower ON: Heat_Demand set below
    setpoint - hyst, reset at setpoint; heater := Heat_Demand AND Airflow_OK AND OvenTemp < OvenMax.

    Returns the OvenTemp readings and the warnings.
    """
    demand, temps, warned = False, [], []
    for _ in range(round(seconds / TICK)):
        temp, air = oven.oven_temp, oven.airflow_ok  # 1. read sensors
        if temp < PLC_OVEN_SETPOINT - PLC_OVEN_HYST:  # 2. set outputs
            demand = True
        elif temp >= PLC_OVEN_SETPOINT:
            demand = False
        now += TICK
        warned += warnings(oven.step(demand and air and temp < PLC_OVEN_MAX, True, False, TICK, now))  # 3. step
        temps.append(oven.oven_temp)
    return temps, warned


def test_airflow_follows_the_blower_within_airflow_timeout():
    oven = Oven(OutfeedTable())
    ticks = 0
    while not oven.airflow_ok:
        oven.step(False, True, False, TICK, ticks * TICK)
        ticks += 1
    assert ticks * TICK == pytest.approx(config.BLOWER_SPINUP_S) and config.BLOWER_SPINUP_S < PLC_AIRFLOW_TIMEOUT_S
    oven.step(False, False, False, TICK, ticks * TICK)
    assert not oven.airflow_ok  # blower OFF: no air at once


def test_heat_up_reaches_oven_ready_well_below_heat_timeout():
    oven = Oven(OutfeedTable())
    t = 0.0
    while oven.oven_temp < PLC_OVEN_SETPOINT - PLC_OVEN_BAND:
        oven.step(oven.airflow_ok, True, False, TICK, t)  # the heater waits for the air (interlock)
        t += TICK
        assert t < PLC_HEAT_TIMEOUT_S, "oven never reached Oven_Ready"
    assert 50 < t < 60  # 54.6 s by calculation + the blower's 1 s: half of HeatTimeout


def test_hysteresis_stays_inside_the_ready_window():
    oven = Oven(OutfeedTable(), temp_c=PLC_OVEN_SETPOINT / 10)
    temps, warned = oven_controller(oven, 300.0)
    after_spinup = temps[round(config.BLOWER_SPINUP_S / TICK) + 30:]
    assert min(after_spinup) >= PLC_OVEN_SETPOINT - PLC_OVEN_BAND and max(after_spinup) <= PLC_OVEN_SETPOINT + PLC_OVEN_BAND
    assert min(after_spinup) >= 1840 and max(after_spinup) <= 1910  # the 5 degC band plus one tick
    assert not warned


def test_heater_left_on_reaches_oven_max():
    oven = Oven(OutfeedTable(), temp_c=PLC_OVEN_SETPOINT / 10)
    t = 0.0
    while oven.oven_temp < PLC_OVEN_MAX:
        oven.step(True, True, False, TICK, t)  # contactor welded: heater ON whatever the PLC says
        t += TICK
        assert t < 120, "a stuck heater must reach OvenMax (over-temperature scenario)"
    assert 45 < t < 60


def test_heater_without_air_does_not_heat_the_air():
    oven = Oven(OutfeedTable(), temp_c=PLC_OVEN_SETPOINT / 10)
    for i in range(100):
        oven.step(True, False, False, TICK, i * TICK)  # heater ON, blower OFF
    assert oven.temp_c < PLC_OVEN_SETPOINT / 10


def test_late_tick_equals_short_ticks():
    a, b = Oven(OutfeedTable()), Oven(OutfeedTable())
    for oven in (a, b):
        oven.step(False, True, False, config.BLOWER_SPINUP_S, 0.0)  # air flows
    a.step(True, True, False, 1.0, 2.0)
    for i in range(10):
        b.step(True, True, False, TICK, 1.0 + (i + 1) * TICK)
    assert a.temp_c == pytest.approx(b.temp_c)


# --- Oven: conveyor, eyes, shrink, outfeed table ---------------------------------------
class OvenRun:
    """A hot oven (or a cold one) with the conveyor ON; records both eyes per tick."""

    def __init__(self, hot: bool = True) -> None:
        self.table = OutfeedTable()
        self.oven = Oven(self.table, temp_c=190.0 if hot else config.ROOM_C)
        self.hot = hot
        self.now = 0.0
        self.entry: list[bool] = []
        self.exit: list[bool] = []
        self.events: list = []

    def put(self, pack: Pack) -> bool:
        """The wrap conveyor hands a pack over; the eyes are sampled at once (the plant writes them
        at the end of the tick in which the pack arrived)."""
        accepted = self.oven.put_at_entry(pack)
        self.entry.append(self.oven.oven_entry)
        self.exit.append(self.oven.oven_exit)
        return accepted

    def tick(self, conveyor: bool = True) -> None:
        self.now += TICK
        heater = self.hot and self.oven.airflow_ok and self.oven.oven_temp < PLC_OVEN_SETPOINT
        self.events += self.oven.step(heater, self.hot, conveyor, TICK, self.now)
        self.entry.append(self.oven.oven_entry)
        self.exit.append(self.oven.oven_exit)

    def run(self, seconds: float, conveyor: bool = True) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(conveyor)


def rising_edges(levels: list[bool]) -> list[int]:
    """Tick indexes where the level goes from OFF to ON."""
    return [i for i in range(len(levels)) if levels[i] and (i == 0 or not levels[i - 1])]


def test_one_pack_through_a_hot_oven():
    run = OvenRun()
    assert run.put(make_pack(1))
    run.run(15.0)
    assert sum(run.entry) == round(config.PACK_LENGTH_MM / config.OVEN_SPEED_MM_S / TICK)  # ON about 2 s
    exit_edges = rising_edges(run.exit)
    assert len(rising_edges(run.entry)) == 1 and len(exit_edges) == 1
    assert exit_edges[0] * TICK == pytest.approx(10.0)  # entry -> exit edge 10 s (sample 0 = the put)
    assert 10.0 <= PLC_OVEN_JAM_S / 2  # OvenJamTime is about twice the transit
    assert len(run.table.packs) == 1 and run.oven.packs == [] and run.oven.left == 1
    pack = run.table.packs[0]
    assert pack.shrunk and pack.hot_s >= config.SHRINK_S
    assert not warnings(run.events)


def test_cold_oven_pack_leaves_not_shrunk():
    run = OvenRun(hot=False)
    run.oven.put_at_entry(make_pack(1))
    run.run(15.0)
    assert len(run.table.packs) == 1 and not run.table.packs[0].shrunk
    assert warnings(run.events) == ["Pack 1 (bottles 1-6) left the oven not shrunk (0.0 s hot)"]


def test_conveyor_off_holds_the_packs_and_the_eye():
    run = OvenRun()
    run.oven.put_at_entry(make_pack(1))
    run.run(10.5)  # the pack covers the exit eye
    assert run.oven.oven_exit
    front = run.oven.packs[0].front_mm
    run.run(5.0, conveyor=False)  # FAULT: the oven conveyor stops
    assert run.oven.packs[0].front_mm == front and run.oven.oven_exit and run.table.packs == []
    run.run(3.0)
    assert len(run.table.packs) == 1


def test_entry_gap_gives_two_separate_edges_per_eye():
    run = OvenRun()
    assert run.oven.put_at_entry(make_pack(1))
    assert not run.oven.put_at_entry(make_pack(2))  # the entry is not clear
    waited = 0
    pack2 = make_pack(2)
    while not run.oven.put_at_entry(pack2):
        run.tick()
        waited += 1
    assert waited * TICK == pytest.approx((config.PACK_LENGTH_MM + config.OVEN_GAP_MM) / config.OVEN_SPEED_MM_S)
    run.run(20.0)
    assert len(rising_edges(run.entry)) == 2 and len(rising_edges(run.exit)) == 2
    off_between = [i for i, level in enumerate(run.exit) if not level]
    assert len(run.table.packs) == 2 and off_between  # the exit eye was OFF between the two packs


def test_table_full_sensor_leaves_room_for_the_packs_past_ready():
    table = OutfeedTable()
    for i in range(3):
        assert not table.table_full
        table.packs.append(make_pack(i + 1))
    assert table.table_full and table.has_room
    assert config.TABLE_PACKS - config.TABLE_FULL_PACKS >= 3  # 1 on the wrap conveyor + 2 in the oven (Q3)


def test_full_table_holds_the_pack_at_the_exit():
    run = OvenRun()
    run.table.packs = [make_pack(i) for i in range(10, 10 + config.TABLE_PACKS)]
    run.oven.put_at_entry(make_pack(1))
    run.run(20.0)
    assert len(run.oven.packs) == 1 and run.oven.oven_exit  # waits at the exit, the eye stays ON
    assert warnings(run.events) == ["Pack 1 (bottles 1-6) waits at the oven exit: outfeed table full"]
    run.table.take()  # the palletizer takes one
    run.tick()
    assert run.oven.packs == [] and not run.oven.oven_exit and len(run.table.packs) == config.TABLE_PACKS


# --- Palletizer stand-in ---------------------------------------------------------------
def test_palletizer_takes_one_pack_per_take_time():
    table = OutfeedTable()
    table.packs = [make_pack(i) for i in range(1, 4)]
    pal = PalletizerStandIn(table)
    taken_at = []
    for i in range(round(25.0 / TICK)):
        if pal.step(TICK, (i + 1) * TICK):
            taken_at.append(round((i + 1) * TICK, 1))
    assert taken_at == [0.1, 10.1, 20.1] and pal.taken == 3 and table.packs == []
    assert pal.step(TICK, 30.0) == []  # empty table: nothing to take


def test_stopped_palletizer_takes_nothing():
    table = OutfeedTable()
    table.packs = [make_pack(1)]
    pal = PalletizerStandIn(table)
    pal.running = False
    for i in range(100):
        pal.step(TICK, i * TICK)
    assert len(table.packs) == 1 and pal.taken == 0


# --- The packer drives its oven from the PLC outputs -------------------------------------
def test_packer_step_drives_the_oven_from_the_outputs():
    packer = Packer(Conveyor("C3"))
    packer.oven.put_at_entry(make_pack(1))
    out = PackerOutputs(oven_heater=True, oven_blower=True, oven_conveyor=True)
    for i in range(round(2.0 / TICK)):
        packer.step(out, TICK, (i + 1) * TICK)
    assert packer.oven.airflow_ok and packer.oven.temp_c > config.ROOM_C  # blower + heater
    assert packer.oven.packs[0].front_mm == pytest.approx(2.0 * config.OVEN_SPEED_MM_S)  # conveyor
    assert packer.oven.outfeed is packer.outfeed
