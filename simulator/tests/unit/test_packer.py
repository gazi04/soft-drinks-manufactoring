"""Packer physics: stage 1 collation (gate, count sensor, collation table, group pusher),
stage 2 wrap (wrap conveyor, film, knife, wrap bar) and stage 3 oven + outfeed table + palletizer
stand-in.  uv run pytest

No PLC here: the tests write the PLC outputs by hand, in ticks of 0.1 s as the plant will, with C3
stepped before the packer (the plant's order). `Collation` plays the PLC's COUNT rule in the real
loop's order (read sensors, set outputs, step), so the one-tick latency is included; `WrapPlc`
does the same for the wrap sequencer's step table.
"""

import itertools
from dataclasses import fields

import pytest

from simulator import address_map_packer as pm
from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.machines.packer import Film, Oven, OutfeedTable, Pack, Packer, PackerOutputs, Wrapper
from simulator.standins import PalletizerStandIn

TICK = 0.1

# PLC numbers (plcs/packer/pous/programs/main.ld, docs/packer/state-diagram.md). config.py must
# not import PLC values; if a PLC constant changes, change it here too.
PLC_PACK_SIZE = 6
PLC_PUSH_TIMEOUT_S = 3
PLC_CLEAR_TIME_S = 3
PLC_FILM_PULSE_MM = 50
PLC_FILM_MIN, PLC_FILM_MAX = 600, 1200
PLC_FEED_TIMEOUT_S = 10
PLC_CUT_TIME_S = 0.3
PLC_SHEET_CHECK_S = 1
PLC_WRAP_TIMEOUT_S = 5
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
FEED = PackerOutputs(film_feed=True)
KNIFE = PackerOutputs(knife=True)
CONVEYOR = PackerOutputs(wrap_conveyor=True)


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
        line.run(CONVEYOR, config.PACK_LENGTH_MM / config.WRAP_SPEED_MM_S)  # the pack clears the push line
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


# --- Wrap (stage 2) ------------------------------------------------------------------------


def feed_pulses(film_length: int) -> int:
    """The PLC's FeedPulses: LIMIT(FilmMin, FilmLength, FilmMax) / FilmPulseMM (INT division)."""
    return min(max(film_length, PLC_FILM_MIN), PLC_FILM_MAX) // PLC_FILM_PULSE_MM


def pulse_runs(levels: list[bool]) -> tuple[list[int], list[int]]:
    """Lengths in ticks of the ON runs, and of the OFF runs between two ON runs."""
    runs = [(level, len(list(group))) for level, group in itertools.groupby(levels)]
    while runs and not runs[0][0]:
        runs.pop(0)
    while runs and not runs[-1][0]:
        runs.pop()
    return [n for level, n in runs if level], [n for level, n in runs if not level]


class WrapRig:
    """Wrapper + a hot oven with its conveyor ON, stepped in the packer's order (wrap, then oven)."""

    def __init__(self, roll_mm: float = config.FILM_ROLL_MM, oven_conveyor: bool = True) -> None:
        self.oven = Oven(OutfeedTable(), temp_c=190.0)
        self.wrapper = Wrapper(self.oven, Film(roll_mm))
        self.film = self.wrapper.film
        self.oven_conveyor = oven_conveyor
        self.now = 0.0
        self.events: list = []
        self._next_id = 1

    def place(self) -> Pack:
        """The pusher sets a new pack down at the push line."""
        pack = make_pack(self._next_id)
        self._next_id += 1
        self.events += self.wrapper.put_at_entry(pack, self.now)
        return pack

    def tick(self, out: PackerOutputs) -> None:
        self.now += TICK
        self.events += self.wrapper.step(out, TICK, self.now)
        self.events += self.oven.step(False, True, self.oven_conveyor, TICK, self.now)

    def run(self, out: PackerOutputs, seconds: float) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(out)


class WrapPlc:
    """The PLC's wrap sequencer (state-diagram.md, wrap step table, rules 4, 5, 12) in loop order:
    read the sensors, count Film_Pulse edges, one transition, outputs with the interlocks, physics
    step. Oven ready and outfeed free are assumed. `latency` extra ticks before an output change
    reaches the plant. Records every step's start time and the Film_Pulse level per tick."""

    def __init__(self, rig: WrapRig, film_length: int = 850, latency: int = 0) -> None:
        self.rig = rig
        self.pulses_needed = feed_pulses(film_length)
        self.step = 10
        self.count = 0  # Feed_Counter.CV (kept through a stop, reset in READY)
        self.started: dict[int, float] = {10: 0.0}  # last start time of every step
        self.durations: dict[int, list[float]] = {}
        self.pulse_levels: list[bool] = []
        self.sheet_fault = False
        self._pulse_was = False
        self._home_was = True
        self._step_s = 0.0
        self._no_sheet_s = 0.0
        self._pending = [OFF] * latency

    def stop(self) -> None:
        """Stop / E-stop: the step goes to IDLE, every output off (the counter keeps its count)."""
        self._goto(0)

    def start(self) -> None:
        """Start (rule 2): WAIT_PACK, or WRAP when the bar was stopped in the middle of a turn."""
        self._goto(10 if self.rig.wrapper.wrap_home else 32)

    def _goto(self, step: int) -> None:
        self.durations.setdefault(self.step, []).append(self._step_s)
        self.step, self._step_s = step, 0.0
        self.started[step] = self.rig.now

    def tick(self) -> None:
        w, film = self.rig.wrapper, self.rig.film
        # 1. Read the sensors
        at_film, in_slot, pulse, home = w.pack_at_film, film.film_in_slot, film.film_pulse, w.wrap_home
        entry = self.rig.oven.oven_entry
        self.pulse_levels.append(pulse)
        # 2. Counter (CTU on the rising edge), timers
        if pulse and not self._pulse_was:
            self.count += 1
        self._pulse_was = pulse
        done = self.count >= self.pulses_needed
        home_edge = home and not self._home_was
        self._home_was = home
        # 3. One transition
        s = self.step
        if s == 10 and at_film and in_slot and self.count == 0:
            self._goto(24)
        elif s == 10 and at_film and not (in_slot and self.count == 0) and not film.film_empty:
            self._goto(20)
        elif s == 20 and done:
            self._goto(22)
        elif s == 22 and self._step_s >= PLC_CUT_TIME_S - 1e-9:
            self._goto(24)
        elif s == 24 and in_slot and home:
            self._goto(30)
        elif s == 30 and not at_film:
            self._goto(32)
        elif s == 32 and home_edge:
            self._goto(34)
        elif s == 34 and entry:
            self._goto(10)
        if self.step == 24:
            self.count = 0  # Feed_Counter reset in READY
            self._no_sheet_s = 0.0 if in_slot else self._no_sheet_s + TICK
            self.sheet_fault |= self._no_sheet_s >= PLC_SHEET_CHECK_S
        # 4. Outputs with the interlocks (wrap bar > film feed > knife lock-out, rule 12)
        s = self.step
        bar = s == 32
        feed = s == 20 and not done and home and not bar
        knife = s == 22 and not feed
        conveyor = ((s == 10 and not at_film) or s in (30, 32, 34)) and not feed and not knife
        self._pending.append(PackerOutputs(wrap_conveyor=conveyor, film_feed=feed, knife=knife, wrap_bar=bar))
        # 5. Physics
        self.rig.tick(self._pending.pop(0))
        self._step_s += TICK

    def run_until(self, condition, seconds: float = 30.0) -> None:
        for _ in range(round(seconds / TICK)):
            if condition():
                return
            self.tick()
        assert condition(), f"not reached within {seconds} s (step {self.step})"


def wrap_one(film_length: int = 850, latency: int = 0) -> tuple[WrapRig, WrapPlc, Pack]:
    """One pack from the push line through the whole wrap sequence into the oven."""
    rig = WrapRig()
    pack = rig.place()
    plc = WrapPlc(rig, film_length, latency)
    plc.run_until(lambda: pack in rig.oven.packs and plc.step == 10)
    return rig, plc, pack


def test_film_pulse_every_level_lasts_a_tick():
    film = Film()
    levels = []
    for i in range(round(10.0 / TICK)):
        film.step(True, False, TICK, (i + 1) * TICK)
        levels.append(film.film_pulse)
    on, off = pulse_runs(levels)
    assert len(on) == film.pulses == int(10.0 * config.FILM_FEED_MM_S / config.FILM_PULSE_MM)  # 30 edges
    assert min(on) >= 1 and min(off) >= 1  # the CTU sees both edges of every pulse
    assert all(a + b >= 3 for a, b in zip(on, off))  # rising edges >= 0.3 s apart
    assert config.FILM_FEED_MM_S * 0.3 < config.FILM_PULSE_MM  # a late 0.3 s tick never passes two marks


@pytest.mark.parametrize("film_length", [850, 1200, 600, 870])
@pytest.mark.parametrize("latency", [0, 1])
def test_feed_counts_the_sheet_within_feed_timeout(film_length, latency):
    rig = WrapRig()
    rig.place()
    plc = WrapPlc(rig, film_length, latency)
    plc.run_until(lambda: plc.step == 22)
    n = feed_pulses(film_length)
    assert plc.count == n and rig.film.pulses == n
    assert rig.now - plc.started[20] < PLC_FEED_TIMEOUT_S
    run_on = (1 + latency) * config.FILM_FEED_MM_S * TICK  # the PLC stops the feed 1-2 ticks after the edge
    assert n * PLC_FILM_PULSE_MM <= rig.film.sheet_mm <= n * PLC_FILM_PULSE_MM + run_on + 1e-6


def test_film_in_slot_means_a_pulse_was_counted_at_every_encoder_phase():
    """The PLC's Sheet_Waiting (Film_In_Slot AND FeedCount = 0) means "a CUT sheet": an uncut sheet
    the slot sensor sees must always have passed at least one pulse mark, whatever the roller's phase."""
    for phase in range(0, int(config.FILM_PULSE_MM)):
        film = Film()
        film.roller_mm = float(phase)
        for i in range(round(1.0 / TICK)):
            film.step(True, False, TICK, (i + 1) * TICK)
            if film.film_in_slot:
                assert film.pulses >= 1, f"phase {phase} mm: sheet seen without a pulse"
        assert film.film_in_slot


def test_knife_cuts_within_cut_time():
    film = Film()
    film.step(True, False, 1.0, 1.0)  # 150 mm out of the slot
    film.step(False, True, TICK, 1.1)
    assert not film.cut  # one tick is not a cut
    film.step(False, False, TICK, 1.2)
    for i in range(round(PLC_CUT_TIME_S / TICK)):
        film.step(False, True, TICK, 1.3 + i * TICK)
        if film.cut:
            break
    assert film.cut and (i + 1) * TICK == pytest.approx(config.KNIFE_CUT_S)
    assert config.KNIFE_CUT_S < PLC_CUT_TIME_S
    assert film.film_in_slot  # the cut sheet stays in the slot


@pytest.mark.parametrize("latency", [0, 1])
def test_pack_reaches_the_film_stop_within_clear_time_and_stays_on_the_eye(latency):
    rig = WrapRig()
    pack = rig.place()
    plc = WrapPlc(rig, latency=latency)
    plc.run_until(lambda: plc.step == 20)
    assert rig.now - plc.started[10] < PLC_CLEAR_TIME_S - 0.5  # margin for the push and the PLC
    assert rig.wrapper.pack_at_film and rig.wrapper.slot_covered is None  # overshoot stays before the slot
    assert config.FILM_STOP_MM <= pack.front_mm <= config.FILM_STOP_MM + 2 * config.WRAP_SPEED_MM_S * TICK + 1e-6


@pytest.mark.parametrize("latency", [0, 1])
def test_one_pack_wrapped_and_into_the_oven_within_every_timeout(latency):
    rig, plc, pack = wrap_one(latency=latency)
    assert pack.wrapped and rig.wrapper.wrapped == 1
    assert config.SHEET_NEEDED_MM <= pack.sheet_mm <= 900
    for step, limit in ((30, PLC_PUSH_TIMEOUT_S), (32, PLC_WRAP_TIMEOUT_S), (34, PLC_PUSH_TIMEOUT_S)):
        assert max(plc.durations[step]) < limit - 1.0, f"step {step}: {plc.durations[step]}"
    assert plc.durations[22] == [pytest.approx(PLC_CUT_TIME_S)]
    assert rig.oven.oven_entry and rig.wrapper.packs == [] and rig.wrapper.wrap_home
    assert not plc.sheet_fault and not warnings(rig.events)


@pytest.mark.parametrize("latency", [0, 1])
def test_wrap_bar_stops_inside_its_home_window_turn_after_turn(latency):
    """WRAP: WrapBar ON until the rising edge of Wrap_Home; the bar stops 1-2 ticks later, inside
    the window, and the next turn starts from there (no drift)."""
    w = Wrapper(Oven(OutfeedTable()))
    now = 0.0
    for turn in range(10):
        pending = [OFF] * latency
        turning, home_was, started = True, w.wrap_home, now
        while turning or any(o.wrap_bar for o in pending):
            home = w.wrap_home
            if home and not home_was:
                turning = False  # the rising edge ends WRAP
            home_was = home
            pending.append(PackerOutputs(wrap_bar=turning))
            now += TICK
            w.step(pending.pop(0), TICK, now)
        assert now - started < PLC_WRAP_TIMEOUT_S - 2.0
        assert w.wrap_home, f"turn {turn}: bar stopped outside home at {w.bar:.3f} turn"


def test_two_cycles_wrap_two_packs_without_warnings():
    rig = WrapRig()
    plc = WrapPlc(rig)
    packs = []
    for _ in range(2):
        packs.append(rig.place())
        plc.run_until(lambda: packs[-1] in rig.oven.packs and plc.step == 10)
        for _ in range(round(PLC_CLEAR_TIME_S / TICK)):  # Wrap_Clear before the next push
            plc.tick()
    assert all(p.wrapped for p in packs) and rig.film.pulses == 2 * 17
    assert not warnings(rig.events)


# --- Wrap: audits --------------------------------------------------------------------------
def test_pack_over_an_uncut_sheet_tears_it():
    rig = WrapRig()
    pack = rig.place()
    rig.run(CONVEYOR, 2.0)  # front at 400: at the film stop
    rig.run(FEED, 900 / config.FILM_FEED_MM_S)
    rig.run(CONVEYOR, 1.0)  # no knife: drives over the uncut sheet
    assert pack.sheet_mm > 0 and not rig.film.film_in_slot
    assert any("uncut sheet: film torn" in w for w in warnings(rig.events))


def test_short_sheet_warns_at_the_oven_entry():
    rig, plc, pack = wrap_one(film_length=600)
    assert pack.wrapped and pack.sheet_mm < config.SHEET_NEEDED_MM
    assert [w for w in warnings(rig.events) if "short sheet" in w] == [
        f"{pack.name} entered the oven with a short sheet ({pack.sheet_mm:.0f} mm, needs 800)"]


def test_pack_without_film_warns_at_the_oven_entry():
    rig = WrapRig()
    pack = rig.place()
    rig.run(CONVEYOR, 6.0)
    assert pack in rig.oven.packs
    assert warnings(rig.events) == [f"{pack.name} entered the oven without film"]


def test_bar_turn_without_sheet_wraps_nothing():
    rig = WrapRig()
    pack = rig.place()
    rig.run(PackerOutputs(wrap_conveyor=True, wrap_bar=True), 6.0)
    assert not pack.wrapped and rig.wrapper.wrapped == 0 and pack in rig.oven.packs
    assert warnings(rig.events) == [f"{pack.name} entered the oven without film"]


def test_feed_with_the_bar_out_warns():
    rig = WrapRig()
    rig.run(PackerOutputs(wrap_bar=True), 0.5)
    assert not rig.wrapper.wrap_home
    rig.run(FEED, 0.3)
    assert warnings(rig.events) == ["film fed into the wrap bar's path (bar not home)"]


def test_second_sheet_under_a_cut_sheet_warns():
    rig = WrapRig()
    rig.run(FEED, 1.0)
    rig.run(KNIFE, 0.3)
    rig.run(FEED, 0.2)
    assert warnings(rig.events) == ["film fed under a cut sheet: two sheets in the slot"]


# --- Wrap: film roll -------------------------------------------------------------------------
def test_film_low_and_empty_thresholds():
    film = Film(config.FILM_LOW_MM + 10)
    assert not film.film_low and not film.film_empty
    film.load_roll(config.FILM_LOW_MM)
    assert film.film_low and not film.film_empty
    film.load_roll(config.FILM_TAIL_MM)
    assert film.film_low and film.film_empty


def test_sheet_started_before_film_empty_finishes_on_the_tail():
    """The largest sheet (1200 mm) started 1 mm before Film_Empty gets all its pulses and its run-on:
    the same sheet as from a full roll."""
    sheets = []
    for roll_mm in (config.FILM_TAIL_MM + 1, config.FILM_ROLL_MM):
        rig = WrapRig(roll_mm=roll_mm)
        rig.place()
        plc = WrapPlc(rig, film_length=1200, latency=1)
        plc.run_until(lambda: plc.step == 22)
        assert rig.film.pulses == 24
        sheets.append(rig.film.sheet_mm)
    assert rig.film.film_empty is False and sheets[0] == pytest.approx(sheets[1])
    assert Film(config.FILM_TAIL_MM + 1 - sheets[0]).film_empty  # the first roll is now on its tail


def test_empty_roll_starts_no_sheet_and_bare_core_feeds_nothing():
    rig = WrapRig(roll_mm=config.FILM_TAIL_MM)
    rig.place()
    plc = WrapPlc(rig)
    for _ in range(round(5.0 / TICK)):
        plc.tick()
    assert plc.step == 10 and rig.wrapper.pack_at_film  # waits in WAIT_PACK (Starved, Alm_FilmEmpty)
    core = Film(0.0)
    for i in range(10):
        core.step(True, False, TICK, (i + 1) * TICK)
    assert core.pulses == 0 and core.sheet_mm == 0 and not core.film_pulse


def test_torn_film_rollers_turn_nothing_moves():
    film = Film()
    film.torn = True
    for i in range(30):
        film.step(True, False, TICK, (i + 1) * TICK)
    assert film.pulses == 0 and film.sheet_mm == 0 and film.left_mm == config.FILM_ROLL_MM


# --- Wrap: stops and restarts (state-diagram.md rule 11) --------------------------------------
def test_stop_in_feed_restart_feeds_only_the_rest():
    rig = WrapRig()
    rig.place()
    plc = WrapPlc(rig)
    plc.run_until(lambda: plc.step == 20 and plc.count == 8)
    plc.stop()
    for _ in range(10):
        plc.tick()  # IDLE: the counter keeps 8
    assert rig.film.film_in_slot and plc.count == 8
    plc.start()
    plc.run_until(lambda: plc.step == 22)
    assert rig.film.pulses == 17 and plc.count == 17
    assert 850 <= rig.film.sheet_mm <= 850 + config.FILM_FEED_MM_S * TICK + 1e-6


def test_stop_in_ready_restart_uses_the_cut_sheet():
    rig = WrapRig()
    rig.place()
    plc = WrapPlc(rig)
    plc.run_until(lambda: plc.step == 24)
    plc.stop()
    for _ in range(5):
        plc.tick()
    plc.start()
    plc.tick()
    assert plc.step == 24  # Sheet_Waiting: READY, no second sheet
    plc.run_until(lambda: plc.step == 10 and not rig.wrapper.packs)
    assert rig.film.pulses == 17 and rig.oven.packs[0].wrapped and not warnings(rig.events)


def test_e_stop_in_wrap_restart_finishes_the_turn_and_wraps_once():
    rig = WrapRig()
    pack = rig.place()
    plc = WrapPlc(rig)
    plc.run_until(lambda: plc.step == 32)
    for _ in range(3):
        plc.tick()  # the bar leaves home
    plc.stop()
    plc.tick()
    assert not rig.wrapper.wrap_home
    plc.start()
    assert plc.step == 32
    plc.run_until(lambda: pack in rig.oven.packs and plc.step == 10)
    assert pack.wrapped and rig.wrapper.wrapped == 1 and rig.wrapper.wrap_home
    assert not warnings(rig.events)


def test_e_stop_in_run_on_past_the_slot_feeds_under_the_pack_next_pack_takes_the_sheet():
    """PLC limit (rule 11): the pack has taken its sheet but still covers Pack_At_Film; the restart's
    WAIT_PACK sees no sheet and feeds one under it. The physics reports it; the next pack takes it."""
    rig = WrapRig()
    first = rig.place()
    plc = WrapPlc(rig)
    plc.run_until(lambda: plc.step == 30 and first.front_mm >= config.FILM_SLOT_MM + 20)
    plc.stop()
    plc.tick()
    assert rig.wrapper.pack_at_film and first.sheet_mm > 0
    plc.start()
    plc.run_until(lambda: first in rig.oven.packs and plc.step == 10)
    assert first.wrapped
    assert any("film fed under Pack 1" in w for w in warnings(rig.events))
    assert rig.film.film_in_slot and rig.film.cut  # the extra sheet waits in the slot
    for _ in range(round(PLC_CLEAR_TIME_S / TICK)):
        plc.tick()
    second = rig.place()
    plc.run_until(lambda: plc.step != 10, seconds=5.0)
    assert plc.step == 24  # Sheet_Waiting: no new feed
    plc.run_until(lambda: second in rig.oven.packs and plc.step == 10)
    assert second.wrapped and rig.film.pulses == 2 * 17


# --- Wrap: hand-over to the oven and the Packer wiring -----------------------------------------
def test_oven_entry_not_clear_holds_the_pack_at_the_entry():
    rig = WrapRig(oven_conveyor=False)
    first = rig.place()
    rig.run(CONVEYOR, 1.0)
    second = rig.place()
    rig.run(CONVEYOR, 6.0)
    assert first in rig.oven.packs and second in rig.wrapper.packs
    assert second.front_mm == config.OVEN_ENTRY_MM
    assert f"{second.name} waits at the oven entry: the last pack is still there" in warnings(rig.events)


def test_packer_step_drives_the_wrap_from_the_outputs():
    line = collated()
    push_and_return(line)
    pack = line.packer.wrap_packs[0]
    line.run(CONVEYOR, 2.0)
    assert line.packer.wrapper.pack_at_film and pack.front_mm == pytest.approx(config.FILM_STOP_MM)
    line.run(FEED, 1.0)
    assert line.packer.wrapper.film.pulses == 3 and line.packer.wrapper.film.film_in_slot
    line.run(CONVEYOR, 4.0)
    assert pack in line.packer.oven.packs and line.packer.oven.oven_entry


# --- Injected faults and hand actions (stage 7) ------------------------------------------------
def test_stuck_pusher_stays_where_it_is():
    line = collated()
    line.packer.pusher_stuck = True
    line.run(PUSH, 3.0)
    assert line.packer.pusher_home and not line.packer.pusher_out and line.packer.wrap_packs == []
    line.packer.pusher_stuck = False
    push_and_return(line)  # repaired: the same group is pushed
    assert len(line.packer.wrap_packs) == 1 and len(line.packer.wrap_packs[0].bottles) == 6


def test_stuck_wrap_bar_does_not_turn_and_wraps_nothing():
    rig = WrapRig()
    pack = rig.place()
    rig.wrapper.bar_stuck = True
    pack.sheet_mm = 850.0
    pack.front_mm = 700.0  # in reach of the bar
    rig.run(PackerOutputs(wrap_bar=True), 3.0)
    assert rig.wrapper.bar == 0.0 and rig.wrapper.wrap_home and not pack.wrapped


def test_dropped_sheet_and_the_sheet_put_back():
    film = Film()
    film.step(True, False, 1.0, 1.0)
    film.drop_sheet()
    assert film.sheet_mm == 0 and not film.film_in_slot and not film.cut
    film.put_sheet(850.0)
    assert film.film_in_slot and film.cut  # a cut sheet: the PLC's Sheet_Waiting
    with pytest.raises(ValueError):
        film.put_sheet(-1.0)


def warm_oven(**faults) -> Oven:
    oven = Oven(OutfeedTable(), temp_c=190.0)
    for name, value in faults.items():
        setattr(oven, name, value)
    oven.step(False, True, False, config.BLOWER_SPINUP_S, 0.0)  # blower spun up (if it can)
    return oven


def test_failed_heater_heats_nothing_even_when_stuck_on():
    oven = warm_oven(heater_failed=True, heater_stuck_on=True)  # a dead element wins over a welded contactor
    oven.step(True, True, False, 10.0, 1.0)
    assert oven.temp_c < 190.0


def test_stuck_on_heater_heats_while_air_flows_whatever_the_plc_says():
    oven = warm_oven(heater_stuck_on=True)
    oven.step(False, True, False, 10.0, 1.0)  # PLC heater OFF, blower ON
    assert oven.temp_c > 190.0
    hot = oven.temp_c
    oven.step(False, False, False, 10.0, 11.0)  # blower OFF (after its run-on): no air, no heat
    assert oven.temp_c < hot


def test_failed_blower_moves_no_air_so_no_heat():
    oven = warm_oven(blower_failed=True)
    assert not oven.airflow_ok
    oven.step(True, True, False, 10.0, 1.0)
    assert not oven.airflow_ok and oven.temp_c < 190.0


def test_jammed_oven_holds_its_packs_and_clear_takes_them_out():
    run = OvenRun()
    pack = make_pack(1)
    run.put(pack)
    run.oven.jammed = True
    run.run(5.0)
    assert pack.front_mm == 0.0 and run.oven.oven_entry  # the conveyor runs, the pack stands
    events = run.oven.clear()
    assert run.oven.packs == [] and run.oven.removed == [pack] and not run.oven.oven_entry
    assert [e.text for e in events] == [f"{pack.name} taken out of the oven by hand"]


# --- HMI animation words (HMI P0) ------------------------------------------------------------
def test_anim_cells_of_a_pack_on_the_wrap_conveyor_and_in_the_oven():
    packer = Packer(Conveyor("C3"))
    assert packer.anim_words() == (0, 0, 0, pm.SHEET_NONE)
    pack = make_pack(1)
    packer.wrapper.packs.append(pack)
    pack.front_mm = 0.0  # just set down at the push line: its body is still behind the line
    assert packer.anim_words()[0] == 0
    pack.front_mm = 200.0  # covers 0-200 mm exactly: cells 0 and 1, not 2
    assert packer.anim_words()[0] == 0b11
    pack.front_mm = 450.0  # 250-450 mm: cells 2, 3, 4
    assert packer.anim_words()[0] == 0b1_1100
    pack.front_mm = config.OVEN_ENTRY_MM  # at the entry, waiting: the last two cells
    assert packer.anim_words()[:2] == (0b110_0000_0000, 0)
    # handed over: its front is at the oven's entry eye, its body still on the wrap conveyor's end
    packer.wrapper.packs.remove(pack)
    pack.front_mm = 0.0
    packer.oven.packs.append(pack)
    assert packer.anim_words()[:2] == (0b110_0000_0000, 0)
    pack.front_mm = 150.0  # half inside: wrap cell 10 and oven cells 0-1
    assert packer.anim_words()[:2] == (0b100_0000_0000, 0b11)
    pack.front_mm = 1100.0  # 900-1100 in oven mm: cell 9 and the exit cell 10
    assert packer.anim_words()[:2] == (0, 0b110_0000_0000)
    pack.front_mm = 1200.0  # waiting over the exit for room on the table: only the exit cell
    assert packer.anim_words()[:2] == (0, 0b100_0000_0000)


def test_anim_table_count_and_sheet_codes():
    packer = Packer(Conveyor("C3"))
    packer.outfeed.packs += [make_pack(1), make_pack(2)]
    assert packer.anim_words()[2] == 2
    film = packer.wrapper.film
    packer.wrapper.step(FEED, TICK, 0.0)  # the rollers move film
    assert packer.anim_words()[3] == pm.SHEET_FEEDING
    packer.wrapper.step(OFF, TICK, 0.1)  # film stands, sheet out of the slot
    assert film.sheet_mm > 0 and packer.anim_words()[3] == pm.SHEET_IN_SLOT
    pack = make_pack(3)
    pack.sheet_mm, _ = film.take_sheet()  # a pack drove over the slot and took it
    packer.wrapper.packs.append(pack)
    assert packer.anim_words()[3] == pm.SHEET_UNDER_PACK
    pack.wrapped = True  # the wrap bar laid it over the top
    assert packer.anim_words()[3] == pm.SHEET_NONE
