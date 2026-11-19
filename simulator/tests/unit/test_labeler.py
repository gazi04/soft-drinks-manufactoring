"""Labeler physics: stage 2 melt tank and label web, stage 1 pocket.  uv run pytest

No PLC here: the tests write the PLC outputs by hand, in ticks of 0.1 s as the plant will.
`heat_controller` and `wrap_one_label` play the PLC's rules in the real loop's order
(read sensors, set outputs, step), so the one-tick latency is included.
"""

import pytest

from simulator import address_map_labeler as lm
from simulator import config
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.machines.labeler import Labeler, LabelerOutputs, LabelerSensors, LabelWeb, MeltTank

TICK = 0.1

# PLC numbers (plcs/labeler/pous/programs/main.ld, docs/labeler/state-diagram.md). config.py must
# not import PLC values; if a PLC default changes, change it here too.
PLC_GLUE_SETPOINT = 1700  # 0.1 degC
PLC_GLUE_HYST = 50
PLC_GLUE_BAND = 100
PLC_GLUE_MAX = 2000
PLC_HEAT_TIMEOUT_S = 120
PLC_LABEL_TIMEOUT_S = 5

WRAP = LabelerOutputs(clamp_valve=True, spin_motor=True, label_feed=True, glue_roller=True)
OFF = LabelerOutputs()


def warnings(events) -> list[str]:
    return [e.text for e in events if e.is_warning]


# --- Melt tank --------------------------------------------------------------------
def heat_controller(tank: MeltTank, seconds: float, now: float = 0.0) -> tuple[list[int], list[str]]:
    """The PLC's Heat_Demand rule (set below setpoint - hyst, reset at setpoint) for `seconds`.

    Returns the GlueTemp readings and the warnings.
    """
    demand, temps, warned = False, [], []
    for _ in range(round(seconds / TICK)):
        temp = tank.glue_temp  # 1. read sensors
        if temp < PLC_GLUE_SETPOINT - PLC_GLUE_HYST:  # 2. set outputs
            demand = True
        elif temp >= PLC_GLUE_SETPOINT:
            demand = False
        now += TICK
        warned += warnings(tank.step(demand and temp < PLC_GLUE_MAX, TICK, now))  # 3. physics step
        temps.append(tank.glue_temp)
    return temps, warned


def time_to_ready(tank: MeltTank, limit_s: float = 600.0) -> float:
    """Heater on until GlueTemp is inside the Glue_Ready window (lower edge)."""
    t = 0.0
    while tank.glue_temp < PLC_GLUE_SETPOINT - PLC_GLUE_BAND:
        tank.step(True, TICK, t)
        t += TICK
        assert t < limit_s, "glue never reached the window"
    return t


def test_heat_up_well_below_heat_timeout():
    t = time_to_ready(MeltTank(temp_c=config.ROOM_C))
    assert 40 <= t <= 50  # 43.6 s computed for 160 degC
    assert t < 0.5 * PLC_HEAT_TIMEOUT_S


def test_hysteresis_holds_inside_the_glue_ready_window():
    tank = MeltTank(temp_c=config.ROOM_C)
    temps, warned = heat_controller(tank, 300)
    settled = temps[round(120 / TICK) :]  # after heat-up
    assert min(settled) >= PLC_GLUE_SETPOINT - PLC_GLUE_HYST - 5  # one tick late at most
    assert max(settled) <= PLC_GLUE_SETPOINT + 5  # overshoot about 0.2 degC per tick
    assert all(abs(t - PLC_GLUE_SETPOINT) <= PLC_GLUE_BAND for t in settled)  # Glue_Ready never drops
    assert not warned


def test_heater_off_cools_toward_room():
    tank = MeltTank(temp_c=170.0)
    for i in range(round(20.3 / TICK)):
        tank.step(False, TICK, i * TICK)
    assert tank.temp_c == pytest.approx(165.0, abs=0.1)  # room loss: 5 degC in about 20 s
    for i in range(round(3600 / TICK)):
        tank.step(False, TICK, i * TICK)
    assert tank.temp_c == pytest.approx(config.ROOM_C, abs=0.5)


def test_exact_solution_late_tick_equals_short_ticks():
    a, b = MeltTank(temp_c=50.0), MeltTank(temp_c=50.0)
    a.step(True, 3.0, 3.0)
    for i in range(30):
        b.step(True, TICK, i * TICK)
    assert a.temp_c == pytest.approx(b.temp_c, rel=1e-9)


def test_stuck_heater_reaches_glue_max_and_smokes_once():
    tank = MeltTank(temp_c=170.0)
    t, warned = 0.0, []
    while tank.glue_temp < PLC_GLUE_MAX:
        t += TICK
        warned += warnings(tank.step(True, TICK, t))
        assert t < 60, "a stuck heater must reach GlueMax (over-temperature scenario)"
    assert 15 <= t <= 22  # 18.4 s computed
    for _ in range(50):
        t += TICK
        warned += warnings(tank.step(True, TICK, t))
    assert len(warned) == 1 and "smokes" in warned[0]


def test_cold_glue_blocks_mix_by_mass_and_leave_the_window():
    tank = MeltTank(temp_c=170.0, glue_kg=1.0)
    assert tank.add_glue(1.0) == 1.0
    assert tank.temp_c == pytest.approx((170.0 + config.ROOM_C) / 2)
    assert tank.glue_temp < PLC_GLUE_SETPOINT - PLC_GLUE_BAND  # Glue_Ready drops: rule 4, WARM_UP again
    assert time_to_ready(tank) < PLC_HEAT_TIMEOUT_S


def test_add_glue_caps_at_tank_capacity():
    tank = MeltTank(glue_kg=4.5)
    assert tank.add_glue(2.0) == pytest.approx(0.5)
    assert tank.glue_kg == pytest.approx(config.GLUE_TANK_KG)
    with pytest.raises(ValueError):
        tank.add_glue(-1)


def test_glue_low_and_sticky():
    tank = MeltTank(temp_c=170.0, glue_kg=config.GLUE_LOW_KG)
    assert not tank.glue_low and tank.sticky
    tank.take_glue(1.0)
    assert tank.glue_low
    assert tank.glue_temp == 1700
    assert not MeltTank(temp_c=140.0).sticky  # too cold
    assert not MeltTank(temp_c=170.0, glue_kg=0.0).sticky  # empty


# --- Label web --------------------------------------------------------------------
class Station:
    """Web + warm tank + one bottle that spins while the test says so."""

    def __init__(self, labels: int = config.ROLL_LABELS, temp_c: float = 170.0) -> None:
        self.web = LabelWeb(labels)
        self.tank = MeltTank(temp_c=temp_c)
        self.bottle: Bottle | None = Bottle(id=1)
        self.now = 0.0
        self.warnings: list[str] = []

    def tick(self, out: LabelerOutputs, spinning: bool = True) -> None:
        self.now += TICK
        self.warnings += warnings(self.web.step(out, self.bottle, spinning, self.tank, TICK, self.now))


def wrap_one_label(st: Station, limit_s: float = 10.0) -> tuple[float, int]:
    """WRAP like the PLC: feed until the Label_Mark rising edge, then PRESS cuts (one tick of Cutter).

    Returns (seconds until the edge was seen, ticks the mark stayed ON).
    """
    t, before = 0.0, st.web.label_mark
    while True:
        mark = st.web.label_mark  # 1. read sensors
        if mark and not before:  # 2. edge -> PRESS: feed off, knife on
            break
        before = mark
        st.tick(WRAP)  # 3. physics step
        t += TICK
        assert t < limit_s, "no Label_Mark"
    on_ticks = 0
    st.tick(LabelerOutputs(clamp_valve=True, spin_motor=True, cutter=True))
    while st.web.label_mark:
        on_ticks += 1
        st.tick(LabelerOutputs(clamp_valve=True, spin_motor=True))
    return t, on_ticks + 1


def test_one_label_gives_one_mark_well_below_label_timeout():
    st = Station()
    t, on_ticks = wrap_one_label(st)
    assert t == pytest.approx(config.LABEL_LENGTH_MM / config.LABEL_FEED_MM_S, abs=TICK + 1e-9)
    assert t < PLC_LABEL_TIMEOUT_S
    assert 1 <= on_ticks <= 4  # at least one tick, gone long before the next WRAP
    assert st.web.labels_left == config.ROLL_LABELS - 1 and st.web.marks == 1
    # the web runs on until the tick the edge is seen: 210 mm is 10.5 ticks of 20 mm -> 220 mm
    assert config.LABEL_LENGTH_MM <= st.bottle.label_mm <= config.LABEL_LENGTH_MM + config.LABEL_FEED_MM_S * TICK
    assert st.web.lost_mm == 0 and not st.warnings


def test_many_labels_no_drift():
    st = Station()
    times = []
    for i in range(20):
        st.bottle = Bottle(id=i + 1)
        t, _ = wrap_one_label(st)
        times.append(t)
        assert st.bottle.label_mm == pytest.approx(config.LABEL_LENGTH_MM, abs=config.LABEL_FEED_MM_S * TICK + 1e-6)
        for _ in range(20):  # PRESS, UNCLAMP, DISCHARGE, gate: the web stands
            st.tick(OFF, spinning=False)
    assert st.web.marks == 20 and st.web.labels_left == config.ROLL_LABELS - 20
    assert max(times) - min(times) <= TICK + 1e-9  # the overrun after one mark does not shift the next
    assert not st.warnings


def test_glue_taken_per_label():
    st = Station()
    before = st.tank.glue_kg
    wrap_one_label(st)
    used_g = (before - st.tank.glue_kg) * 1000
    assert used_g == pytest.approx(config.GLUE_PER_LABEL_G, rel=0.15)  # plus the overrun of one tick


def test_empty_roll_feeds_only_its_tail_and_gives_no_mark():
    st = Station(labels=1)
    wrap_one_label(st)
    assert st.web.roll_empty and st.web.roll_low
    label_before = st.bottle.label_mm
    for _ in range(round(PLC_LABEL_TIMEOUT_S / TICK)):
        st.tick(WRAP)
        assert not st.web.label_mark
    # after the last mark only the rest of the tail moves, then the web stands
    assert st.bottle.label_mm - label_before == pytest.approx(config.ROLL_TAIL_MM - (label_before - config.LABEL_LENGTH_MM))
    assert st.web.web_left_mm == 0
    st.tick(WRAP)
    assert st.bottle.label_mm == pytest.approx(config.LABEL_LENGTH_MM + config.ROLL_TAIL_MM)
    st.web.load_roll()
    assert not st.web.roll_empty and not st.web.roll_low
    wrap_one_label(st)  # works again after the roll change


def wrap_with_run_on(st: Station, run_on_ticks: int, limit_s: float = 10.0) -> None:
    """WRAP as in the real loop: the feed stops `run_on_ticks` ticks after the Label_Mark edge
    (the mark is written at the end of a tick, the PLC sees it in its next scan, the simulator reads the
    stopped output one tick later: 1-2 ticks = 20-40 mm of web)."""
    t, before = 0.0, st.web.label_mark
    while not (st.web.label_mark and not before):
        before = st.web.label_mark
        st.tick(WRAP)
        t += TICK
        assert t < limit_s, "no Label_Mark"
    for _ in range(run_on_ticks):
        st.tick(WRAP)
    st.tick(LabelerOutputs(clamp_valve=True, spin_motor=True, cutter=True))


@pytest.mark.parametrize("run_on_ticks", [1, 2])
def test_last_label_of_a_roll_is_whole_despite_the_run_on(run_on_ticks):
    """Found live (labeler stage 7, roll_empty): the web stopped at the last mark, the run-on of the
    label before was missing and the last label left with 169 of 210 mm. The tail finishes it."""
    st = Station(labels=2)
    lengths = []
    for i in range(3):  # two labels, then the roll is empty
        st.bottle = Bottle(id=i + 1)
        try:
            wrap_with_run_on(st, run_on_ticks, limit_s=PLC_LABEL_TIMEOUT_S)
        except AssertionError:  # the third wrap: no mark any more (the PLC's LabelTimeout would follow)
            break
        lengths.append(st.bottle.label_mm)
    assert len(lengths) == 2 and st.web.roll_empty
    for length in lengths:
        assert length >= config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM  # whole, no "broken label"
    assert lengths[1] == pytest.approx(config.LABEL_LENGTH_MM, abs=config.LABEL_FEED_MM_S * TICK + 1e-6)
    assert not st.warnings


def test_roll_low_threshold():
    web = LabelWeb(config.ROLL_LOW_LABELS + 1)
    assert not web.roll_low
    web.labels_left -= 1
    assert web.roll_low and not web.roll_empty
    with pytest.raises(ValueError):
        web.load_roll(-1)


@pytest.mark.parametrize(
    ("temp_c", "spinning", "roller", "has_bottle", "reason"),
    [
        (140.0, True, True, True, "glue not sticky"),
        (170.0, False, True, True, "not clamped and spinning"),
        (170.0, True, False, True, "glue roller off"),
        (170.0, True, True, False, "no bottle"),
    ],
)
def test_label_lost_without_spin_glue_or_bottle(temp_c, spinning, roller, has_bottle, reason):
    st = Station(temp_c=temp_c)
    if not has_bottle:
        st.bottle = None
    out = LabelerOutputs(clamp_valve=True, spin_motor=spinning, label_feed=True, glue_roller=roller)
    for _ in range(10):
        st.tick(out, spinning=spinning)
    if has_bottle:
        assert st.bottle.label_mm == 0  # Label_Present stays FALSE
    assert st.web.lost_mm == pytest.approx(10 * config.LABEL_FEED_MM_S * TICK)
    assert len(st.warnings) == 1 and reason in st.warnings[0]


def test_glue_roller_runs_dry_warning():
    st = Station()
    st.tank.glue_kg = 0.0
    st.tick(WRAP)
    assert any("runs dry" in w for w in st.warnings)
    assert st.bottle.label_mm == 0


def test_knife_missed_warning_and_cut_resets_it():
    st = Station()
    st.web.since_cut_mm = 0.0
    for _ in range(round(1.5 * config.LABEL_LENGTH_MM / config.LABEL_FEED_MM_S / TICK) + 2):
        st.tick(WRAP)
    assert any("knife did not cut" in w for w in st.warnings)
    st.tick(LabelerOutputs(cutter=True))
    assert st.web.since_cut_mm == 0


def test_outputs_in_coil_order():
    fields = list(LabelerOutputs.__dataclass_fields__)
    assert len(fields) == lm.OUTPUTS_COUNT
    assert fields[lm.GLUE_HEATER] == "glue_heater" and fields[lm.DISCHARGE_MOTOR] == "discharge_motor"


# --- Stage 1: pocket (gate, bell, turntable, pusher) ---------------------------------
PLC_GATE_PULSE_S = 0.5
PLC_CLAMP_TIMEOUT_S = 2
PLC_OUT_TIMEOUT_S = 3


class Pocket:
    """A Labeler between a C2 with a queue of bottles and an empty C3; both belts run."""

    def __init__(self, queue: int = 0) -> None:
        self.c2, self.c3 = Conveyor("C2"), Conveyor("C3")
        for i in range(queue):  # a queue standing at the exit
            self.c2.slots[-1 - i] = Bottle(id=i + 1)
        self.lab = Labeler(self.c2, self.c3)
        self.now = 0.0
        self.events: list = []

    def tick(self, out: LabelerOutputs = OFF) -> None:
        self.now += TICK
        self.c2.step(True, TICK)
        self.events += self.lab.step(out, TICK, self.now)
        self.c3.step(True, TICK)

    def run(self, seconds: float, out: LabelerOutputs = OFF) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(out)

    @property
    def warnings(self) -> list[str]:
        return [e.text for e in self.events if e.is_warning]


def test_gate_pulse_admits_exactly_one_bottle_of_a_queue():
    p = Pocket(queue=4)
    assert p.lab.sensors().bottle_at_gate
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    p.run(2.0)
    assert p.lab.pocket is not None and p.lab.pocket.id == 1
    assert p.c2.count == 3 and not p.lab.lost and not p.warnings


def test_bottle_reaches_the_pocket_within_clamp_timeout():
    p = Pocket(queue=1)
    t = 0.0
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    t += PLC_GATE_PULSE_S
    while not p.lab.sensors().bottle_in_pocket:
        p.tick()
        t += TICK
        assert t < 5
    # gate 0.3 s + travel 0.5 s, measured from the start of the pulse
    assert t == pytest.approx(config.GATE_PASS_S + config.GATE_TRAVEL_S, abs=TICK + 1e-9)
    assert t - config.GATE_PASS_S < PLC_CLAMP_TIMEOUT_S


def test_second_bottle_onto_a_full_pocket_is_lost():
    p = Pocket(queue=3)
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    p.run(1.5)
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))  # no interlock in the physics
    p.run(1.5)
    assert len(p.lab.lost) == 1 and p.lab.pocket.id == 1
    assert any("two bottles" in w for w in p.warnings)


def test_bell_moves_within_clamp_timeout():
    p = Pocket()
    t = 0.0
    while not p.lab.sensors().bell_down:
        p.tick(LabelerOutputs(clamp_valve=True))
        t += TICK
    assert t == pytest.approx(config.BELL_MOVE_S, abs=1e-9) and t < PLC_CLAMP_TIMEOUT_S
    p.tick()  # the bell starts up: Bell_Down drops at once
    assert not p.lab.sensors().bell_down


def test_n_turns_give_n_separate_pulses():
    p = Pocket()
    readings = []  # Spin_Pulse at the end of every tick, as the PLC sees it
    for _ in range(round(5 * config.SPIN_TURN_S / TICK)):
        p.tick(LabelerOutputs(spin_motor=True))
        readings.append(p.lab.sensors().spin_pulse)
    rising = [i for i in range(1, len(readings)) if readings[i] and not readings[i - 1]]
    assert len(rising) + readings[0] == 5  # 5 turns = 5 pulses
    on_ticks = sum(readings) / 5
    assert 1 <= on_ticks < config.SPIN_TURN_S / TICK  # each pulse >= one tick and OFF again before the next


def test_spin_without_clamp_warns():
    p = Pocket(queue=1)
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    p.run(1.0)
    p.tick(LabelerOutputs(spin_motor=True))
    assert any("unclamped" in w for w in p.warnings)


def _bottle_in_pocket(label_mm: float = 0.0) -> Pocket:
    p = Pocket()
    p.lab.pocket = Bottle(id=7, label_mm=label_mm)
    return p


def test_discharge_onto_c3_within_out_timeout():
    p = _bottle_in_pocket(label_mm=config.LABEL_LENGTH_MM)
    assert p.lab.sensors().label_present
    t = 0.0
    while p.lab.sensors().bottle_in_pocket:
        p.tick(LabelerOutputs(discharge_motor=True))
        t += TICK
        assert t < PLC_OUT_TIMEOUT_S
    assert t == pytest.approx(config.DISCHARGE_S, abs=1e-9)
    assert p.c3.count == 1 and p.lab.discharged == 1 and p.lab.labelled == 1 and not p.warnings


def test_discharge_waits_while_c3_entry_is_taken():
    p = _bottle_in_pocket(label_mm=config.LABEL_LENGTH_MM)
    p.c3.slots = [Bottle(id=100 + i) for i in range(len(p.c3.slots))]  # C3 full up to its entry
    p.run(2.0, LabelerOutputs(discharge_motor=True))
    assert p.lab.pocket is not None and p.lab.sensors().c3_full
    p.c3.take_at_exit()
    p.run(2.0, LabelerOutputs(discharge_motor=True))  # the queue moves up, the entry frees
    assert p.lab.pocket is None


def test_pusher_against_the_bell_moves_nothing():
    p = _bottle_in_pocket(label_mm=config.LABEL_LENGTH_MM)
    p.lab.bell = 1.0
    p.run(1.0, LabelerOutputs(clamp_valve=True, discharge_motor=True))
    assert p.lab.pocket is not None and any("against the bell" in w for w in p.warnings)


@pytest.mark.parametrize(("label_mm", "text"), [(0.0, "without a label"), (100.0, "broken label")])
def test_audit_of_unlabelled_and_broken_labels(label_mm, text):
    p = _bottle_in_pocket(label_mm=label_mm)
    p.run(1.0, LabelerOutputs(discharge_motor=True))
    assert p.lab.discharged == 1 and p.lab.labelled == 0
    assert any(text in w for w in p.warnings)


@pytest.mark.parametrize(("label_mm", "code"), [
    (None, lm.POCKET_EMPTY),
    (0.0, lm.POCKET_BOTTLE),
    (1.0, lm.POCKET_LABELLING),
    (config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM - 1, lm.POCKET_LABELLING),
    (config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM, lm.POCKET_LABELLED),  # the audit's "whole label"
    (config.LABEL_LENGTH_MM, lm.POCKET_LABELLED),
])
def test_pocket_code_for_the_hmi(label_mm, code):
    p = Pocket() if label_mm is None else _bottle_in_pocket(label_mm=label_mm)
    assert p.lab.pocket_code() == code


def test_sensor_fields_in_io_list_order():
    fields = list(LabelerSensors.__dataclass_fields__)
    # the 8 sensors of %MX0 in coil order, then %MX4, then the analog value
    assert fields[:8] == ["bottle_at_gate", "bottle_in_pocket", "bell_down", "label_present",
                          "c3_full", "roll_empty", "label_mark", "spin_pulse"]
    assert fields[8:] == ["roll_low", "glue_low", "glue_temp"]
    assert lm.SPIN_PULSE - lm.BOTTLE_AT_GATE == 7 and lm.GLUE_LOW - lm.ROLL_LOW == 1
