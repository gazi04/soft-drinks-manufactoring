"""Plant with the real packer: ... -> labeler -> C3 -> packer -> palletizer stand-in.  uv run pytest

No PLC here. `PackerTestController` plays the packer PLC's two sequencers and its oven rules (a short
port of docs/packer/state-diagram.md without alarms, Stop or modes: it starts as after Auto + Start),
like the labeler's LabelerTestController, so the whole line can run without Docker. From the
closed-loop stage on, the real OpenPLC program sets the outputs and this class is not used.

Like the real loop, every controller sees the sensors of the previous tick.
"""

import pytest
from test_plant import TestController
from test_plant_labeler import WAIT_OUTFEED, LabelerTestController

from simulator import address_map_packer as pm
from simulator import config
from simulator.commands import CommandQueue
from simulator.events import EventLog
from simulator.machines.packer import PackerOutputs, PackerSensors
from simulator.plant import PackerWrites, Plant, PulseWire

TICK = 0.1
EPS = 1e-9
# Collation steps (Step) and wrap steps (WrapStep), docs/packer/state-diagram.md
WARM_UP, COUNT, WAIT_WRAP, PUSH, RETRACT = 5, 10, 15, 20, 25
WAIT_PACK, FEED, CUT, READY, RUN_ON, WRAP, RUN_OUT = 10, 20, 22, 24, 30, 32, 34
# PLC defaults and constants (plcs/packer/pous/programs/main.ld)
OVEN_SETPOINT, OVEN_HYST, OVEN_BAND, OVEN_MAX = 1900, 50, 150, 2300
PACK_SIZE, FEED_PULSES = 6, 17  # FilmLength 850 / FilmPulseMM 50
CLEAR_TIME_S, CUT_TIME_S = 3.0, 0.3
# Settle time (found by this stage, in main.ld since 2026-11-23): the 6th bottle is counted at the gate
# but needs PACKER_GATE_S 0.4 s to land on the table; without a wait the PLC pushed 0.1-0.2 s after the count.
# Settle_Timer := TON(IN := Step_WaitWrap, PT := SettleTime 0.5 s) -> Group_Settled in the WAIT_WRAP -> PUSH transition.
SETTLE_S = 0.5


class PackerTestController:
    """The packer PLC's two sequencers and oven rules without alarms, Stop or modes."""

    __test__ = False  # not a pytest test class

    def __init__(self, settle_s: float = SETTLE_S) -> None:
        self.settle_s = settle_s  # 0 = the PLC before Settle_Timer
        self.settle_t = 0.0  # Settle_Timer
        self.step, self.wrap_step = WARM_UP, WAIT_PACK  # as after Auto + Start (Wrap_Home at the start)
        self.heat_demand = False
        self.outfeed_full = False
        self.full_on_t = self.full_off_t = 0.0
        self.group = 0  # Group_Counter.CV
        self.feed = 0  # Feed_Counter.CV
        self.clear_t = 0.0  # Clear_Timer
        self.cut_t = 0.0  # Cut_Timer
        self.prev_counted = self.prev_pulse = False
        self.prev_home = True
        self.steps_seen: list[int] = [WARM_UP]
        self.wrap_steps_seen: list[int] = [WAIT_PACK]
        self.sheets = 0  # CUT steps entered (one sheet each)

    def outputs(self, s: PackerSensors, dt: float) -> PackerOutputs:
        # Values and conditioning: oven window + hysteresis (rule 6), Outfeed_Full filter, edges
        oven_ready = abs(s.oven_temp - OVEN_SETPOINT) <= OVEN_BAND
        if s.oven_temp < OVEN_SETPOINT - OVEN_HYST:
            self.heat_demand = True
        elif s.oven_temp >= OVEN_SETPOINT:
            self.heat_demand = False
        self.full_on_t = self.full_on_t + dt if s.table_full else 0.0
        self.full_off_t = 0.0 if s.table_full else self.full_off_t + dt
        if self.full_on_t >= 2.0 - EPS:
            self.outfeed_full = True
        if self.full_off_t >= 3.0 - EPS:
            self.outfeed_full = False
        home_edge = s.wrap_home and not self.prev_home
        self.prev_home = s.wrap_home
        # Counters (CTU: rising edges; RESET held in RETRACT / READY) and timers
        if s.bottle_counted and not self.prev_counted:
            self.group += 1
        if s.film_pulse and not self.prev_pulse:
            self.feed += 1
        self.prev_counted, self.prev_pulse = s.bottle_counted, s.film_pulse
        if self.step == RETRACT:
            self.group = 0
        if self.wrap_step == READY:
            self.feed = 0
        group_complete = self.group >= PACK_SIZE
        self.settle_t = self.settle_t + dt if self.step == WAIT_WRAP else 0.0
        group_settled = self.settle_t >= self.settle_s - EPS
        feed_done = self.feed >= FEED_PULSES
        sheet_waiting = s.film_in_slot and self.feed == 0
        clearing = self.wrap_step == WAIT_PACK and s.pusher_home and not s.pack_at_film
        self.clear_t = self.clear_t + dt if clearing else 0.0
        wrap_clear = self.clear_t >= CLEAR_TIME_S - EPS
        self.cut_t = self.cut_t + dt if self.wrap_step == CUT else 0.0

        # Collation transitions (rule 2: the push waits for WAIT_PACK and a clear belt)
        before = self.step
        if self.step == WARM_UP and oven_ready:
            self.step = COUNT
        elif self.step == COUNT and group_complete:
            self.step = WAIT_WRAP
        elif self.step == WAIT_WRAP and self.wrap_step == WAIT_PACK and wrap_clear and group_settled:
            self.step = PUSH
        elif self.step == PUSH and s.pusher_out:
            self.step = RETRACT
        elif self.step == RETRACT and s.pusher_home:
            self.step = COUNT
        if self.step != before:
            self.steps_seen.append(self.step)
        # Wrap transitions (rules 4-5)
        before = self.wrap_step
        if self.wrap_step == WAIT_PACK and s.pack_at_film and sheet_waiting:
            self.wrap_step = READY
        elif self.wrap_step == WAIT_PACK and s.pack_at_film and not s.film_empty:
            self.wrap_step = FEED
        elif self.wrap_step == FEED and feed_done:
            self.wrap_step = CUT
            self.sheets += 1
        elif self.wrap_step == CUT and self.cut_t >= CUT_TIME_S - EPS:
            self.wrap_step = READY
        elif (self.wrap_step == READY and s.film_in_slot and oven_ready and not self.outfeed_full
              and s.wrap_home):
            self.wrap_step = RUN_ON
        elif self.wrap_step == RUN_ON and not s.pack_at_film:
            self.wrap_step = WRAP
        elif self.wrap_step == WRAP and home_edge:
            self.wrap_step = RUN_OUT
        elif self.wrap_step == RUN_OUT and s.oven_entry:
            self.wrap_step = WAIT_PACK
        if self.wrap_step != before:
            self.wrap_steps_seen.append(self.wrap_step)

        # Outputs with the interlocks of rule 12 (wrap bar > film feed > knife by rung order)
        ws = self.wrap_step
        bar = ws == WRAP
        feed = ws == FEED and not feed_done and s.wrap_home and not bar
        knife = ws == CUT and not feed
        return PackerOutputs(
            conveyor_motor3=True,
            gate_motor=self.step == COUNT and not group_complete and s.pusher_home,
            group_pusher=self.step == PUSH,
            wrap_conveyor=((ws == WAIT_PACK and not s.pack_at_film) or ws in (RUN_ON, WRAP, RUN_OUT))
            and not feed and not knife,
            film_feed=feed,
            knife=knife,
            wrap_bar=bar and not feed and not knife,
            oven_heater=self.heat_demand and s.airflow_ok and s.oven_temp < OVEN_MAX,
            oven_blower=True,  # Oven_On: Auto
            oven_conveyor=True,  # Oven_On OR Packs_Inside
        )


class Line:
    """Plant with the labeler and the packer, driven by the three test controllers."""

    def __init__(self) -> None:
        self.plant = Plant(with_labeler=True, with_packer=True)
        self.filler = TestController()
        self.labeler = LabelerTestController()
        self.packer = PackerTestController()
        self.log = EventLog()
        self.w = self.plant.writes()
        self.lw = self.plant.labeler_writes()
        self.pw: PackerWrites = self.plant.packer_writes()

    def tick(self, dt: float = TICK, packer_answers: bool = True) -> None:
        out = self.filler.outputs(self.w, dt)
        lab_out = self.labeler.outputs(self.lw.sensors, dt)
        pack_out = self.packer.outputs(self.pw.sensors, dt) if packer_answers else None
        self.w, events = self.plant.step(out, dt, labeler=lab_out, packer=pack_out)
        self.lw = self.plant.labeler_writes()
        self.pw = self.plant.packer_writes()
        self.log.extend(events)

    def run(self, seconds: float, until=None, **kw) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(**kw)
            if until is not None and until():
                return

    def warnings(self) -> list[str]:
        return [e.format() for e in self.log.filter(warnings_only=True)]


def bottles(n: int) -> Line:
    """A line with exactly n bottles in the supply."""
    line = Line()
    line.plant.supply.add(n)
    line.plant.supply.per_min = 0
    return line


# --- Plant set-up and writes -----------------------------------------------------------------
def test_plants_without_the_packer_keep_the_stand_in():
    for plant in (Plant(), Plant(with_labeler=True)):
        assert plant.packer is None and plant.palletizer is None and plant.packer_writes() is None
    assert Plant(with_labeler=True).packer_standin is not None
    with pytest.raises(ValueError):
        Plant(with_packer=True)  # the packer needs the labeler's C3


def test_writes_carry_wiring_and_the_packer_heartbeat():
    line = Line()
    assert line.plant.packer_standin is None
    s = line.pw.sensors
    assert line.pw.e_stop_ok and not line.pw.heartbeat
    assert s.oven_temp == round(config.ROOM_C * 10) and s.pusher_home and s.wrap_home  # cold, at rest
    assert not s.film_empty and not s.film_low and not s.table_full and not s.airflow_ok
    line.plant.e_stop_ok = False  # one E-stop chain for every PLC
    line.run(config.HEARTBEAT_S)
    assert not line.pw.e_stop_ok and line.pw.heartbeat
    assert not line.lw.e_stop_ok and not line.w.e_stop_ok


def test_packer_running_command_is_ignored_with_the_real_packer():
    plant = Plant(with_labeler=True, with_packer=True)
    q = CommandQueue()
    q.put("packer_running", False)
    events = q.apply(plant)
    assert [e.text for e in events] == [
        "command packer_running = False ignored: the real packer runs (its PLC decides when C3 stops)"]


# --- The line ----------------------------------------------------------------------------------
def test_line_packs_three_groups():
    line = bottles(18)
    line.run(400, until=lambda: line.plant.palletizer.taken == 3)
    plant = line.plant
    assert plant.palletizer.taken == 3 and plant.packer.groups_pushed == 3
    assert plant.packer.wrapper.wrapped == 3 and plant.packer.oven.left == 3
    assert plant.packer.wrapper.film.pulses == 3 * FEED_PULSES == line.packer.sheets * FEED_PULSES
    assert plant.labeler.labelled == 18 and not plant.labeler.lost and not plant.filler.lost
    assert not line.warnings(), line.warnings()
    assert line.packer.steps_seen[:6] == [WARM_UP, COUNT, WAIT_WRAP, PUSH, RETRACT, COUNT]
    assert {FEED, CUT, READY, RUN_ON, WRAP, RUN_OUT} <= set(line.packer.wrap_steps_seen)


def test_every_pack_is_wrapped_shrunk_and_in_bottle_order():
    line = bottles(12)
    table = line.plant.packer.outfeed
    line.plant.palletizer.running = False  # keep the packs on the table to look at them
    line.run(400, until=lambda: len(table.packs) == 2)
    packs = table.packs
    assert [p.id for p in packs] == [1, 2]
    assert [b.id for p in packs for b in p.bottles] == list(range(1, 13))
    for p in packs:
        assert p.wrapped and p.shrunk and config.SHEET_NEEDED_MM <= p.sheet_mm <= 900
        assert all(b.label_mm >= config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM for b in p.bottles)
    assert not line.warnings(), line.warnings()


def test_palletizer_stopped_backs_the_whole_line_up_then_it_continues():
    """Report 5.5: the table fills, READY waits (Blocked), the gate stays shut, C3 fills, the labeler
    is Blocked, C2 fills and the filler is Blocked. Nothing is lost and nothing warns."""
    line = Line()
    plant = line.plant
    plant.palletizer.running = False
    line.run(900, until=lambda: line.filler.outfeed_full)
    assert line.filler.outfeed_full and line.w.s7  # C2 full: the filler is Blocked
    assert plant.packer.outfeed.table_full and line.packer.outfeed_full
    assert line.packer.wrap_step == READY and plant.packer.wrapper.pack_at_film  # Blocked (State 3)
    assert line.packer.step == WAIT_WRAP and len(plant.packer.table) == PACK_SIZE  # gate shut
    assert line.lw.sensors.c3_full and line.labeler.step == WAIT_OUTFEED  # the labeler is Blocked
    assert len(plant.packer.outfeed.packs) <= config.TABLE_PACKS
    pushed = plant.packer.groups_pushed
    plant.palletizer.running = True
    line.run(120)
    assert plant.packer.groups_pushed > pushed and not line.filler.outfeed_full
    assert not plant.labeler.lost and not plant.filler.lost
    assert not line.warnings(), line.warnings()


def test_packer_plc_not_answering_stops_c3_and_the_oven():
    line = bottles(4)
    line.run(90, packer_answers=False)
    plant = line.plant
    assert plant.packer_outputs is None
    assert plant.c3.count > 0 and plant.packer.table == [] and plant.packer.entering is None
    assert plant.packer.oven.temp_c < config.ROOM_C + 0.1 and not line.pw.sensors.airflow_ok


# --- Late ticks: the counted pulses ----------------------------------------------------------
# The labeler's own late-tick weakness (found here, not a packer matter): ticks of 0.2-0.29 s can give a
# label 170-180 mm long (broken), and the packer then reports that bottle in its pack.
LABELER_LATE_TICK = ("left the labeler with a broken label", "left the oven with bottles without a whole label")


@pytest.mark.parametrize("pattern", [(0.15,), (0.1, 0.1, 0.25)], ids=["0.15 s", "every 3rd 0.25 s"])
def test_late_ticks_lose_no_counted_pulse(pattern):
    """No bottle and no 50 mm of film is missed: every sheet gets exactly its 17 pulse marks, and no
    7th bottle or short group warns (the check on the other warnings). Both patterns stay below the 0.3 s slow-tick warning. With a plain OR instead of
    the PulseWire the isolated 0.25 s ticks merge Film_Pulse edges (64 marks instead of 34)."""
    line = bottles(12)
    t, i = 0.0, 0
    while line.plant.palletizer.taken < 2:
        dt = pattern[i % len(pattern)]
        line.tick(dt=dt)
        t, i = t + dt, i + 1
        assert t < 500, "two packs not made with late ticks"
    assert plant_film_exact(line)
    other = [w for w in line.warnings() if not any(x in w for x in LABELER_LATE_TICK)]
    assert not other, other


def plant_film_exact(line: Line) -> bool:
    """Every sheet got exactly its 17 pulse marks: the controller missed no Film_Pulse edge (a missed
    edge feeds 50 mm more film, one more mark)."""
    return line.plant.packer.wrapper.film.pulses == line.packer.sheets * FEED_PULSES


def test_pulse_wire_writes_what_the_or_writes_at_normal_ticks():
    wire = PulseWire()
    levels = [False, True, True, False, False, True, True, False]  # one sub-step per tick
    written = []
    for level in levels:
        wire.sample(level)
        written.append(wire.tick())
    assert written == levels


def test_pulse_wire_separates_two_pulses_in_consecutive_ticks():
    """A late tick: the tail of pulse 1 and pulse 2 both fall into tick 2. The OR would write
    ON, ON (one edge lost); the wire writes ON, OFF, ON."""
    wire = PulseWire()
    ticks = [[False, True], [True, False, True], [False, False], [False, False]]
    written = []
    for subs in ticks:
        for level in subs:
            wire.sample(level)
        written.append(wire.tick())
    assert written == [True, False, True, False]
    assert wire.pending == 0


def test_pulse_wire_never_starts_an_on_without_an_edge():
    wire = PulseWire()
    wire.sample(True)
    assert wire.tick()  # edge 1 passed
    wire.sample(False)
    wire.sample(True)  # edge 2 inside the next tick, while the written value is ON
    assert not wire.tick()  # OFF first
    wire.sample(True)  # still ON in the physics
    assert wire.tick()  # edge 2 passed now
    wire.sample(True)
    assert wire.tick()  # continues, no new edge
    wire.sample(False)
    assert not wire.tick()
    wire.sample(False)
    assert not wire.tick() and wire.pending == 0


def test_without_settle_time_the_plc_pushes_before_the_sixth_bottle_lands():
    """Why SETTLE_S: without it (the PLC before Settle_Timer), PUSH starts 0.1-0.2 s after the 6th count edge,
    while that bottle still needs 0.4 s to reach the table: it lands in front of a moving pusher."""
    line = bottles(6)
    line.packer = PackerTestController(settle_s=0.0)
    line.run(200, until=lambda: line.plant.packer.groups_pushed == 1)
    assert line.plant.packer.groups_pushed == 1
    assert any("bottle onto the collation table with the pusher out" in w for w in line.warnings())


# --- HMI animation words (HMI P0) ------------------------------------------------------------
def test_animation_words_follow_the_packs_without_a_gap():
    line = bottles(12)
    line.plant.palletizer.running = False  # the packs stay on the table
    table = line.plant.packer.outfeed
    assert line.pw.anim == (0, 0, 0, 0)
    wrap_bits = oven_bits = 0
    sheets, gaps = set(), []
    for _ in range(round(400 / TICK)):
        line.tick()
        packer = line.plant.packer
        assert line.pw.anim == packer.anim_words()  # what the PLC gets = what the model says
        wrap, oven, packs, sheet = line.pw.anim
        wrap_bits, oven_bits = wrap_bits | wrap, oven_bits | oven
        sheets.add(sheet)
        # a pack past the push line is always drawn somewhere (no gap at the hand-over to the oven)
        moving = [p for p in packer.wrapper.packs if p.front_mm > 0] + packer.oven.packs
        if moving and not wrap and not oven:
            gaps.append(line.plant.now)
        assert packs == len(table.packs)
        if len(table.packs) == 2:
            break
    assert len(table.packs) == 2 and not gaps, gaps
    assert wrap_bits == (1 << pm.WRAP_CELLS) - 1 and oven_bits == (1 << pm.OVEN_CELLS) - 1  # every cell seen
    assert sheets == {pm.SHEET_NONE, pm.SHEET_FEEDING, pm.SHEET_IN_SLOT, pm.SHEET_UNDER_PACK}
    assert line.pw.anim[:2] == (0, 0) and line.pw.anim[2] == 2  # both packs on the table, nothing moving
