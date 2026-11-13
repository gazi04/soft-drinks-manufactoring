"""Plant with the syrup room: the Product_Available wire and the buffer drawn by the filler.

No PLC here. `SyrupTestController` plays the syrup PLC's steps 5-60 (a short port of the rules,
like the filler's TestController in test_plant.py), so the two machines can run together.
"""

import pytest
from test_plant import TestController, discharged, warnings
from test_syrup import PLC_DOSE_TARGET, PLC_MIX_EMPTY, PLC_WATER_BATCH, PlcRules

from simulator import config
from simulator.events import EventLog
from simulator.machines.filler import FILL, FillerOutputs
from simulator.machines.syrup import SyrupOutputs, SyrupSensors
from simulator.plant import Plant, SyrupSignals

TICK = 0.1
FILL_VALVE = FillerOutputs(conveyor1=True, fill_valve=True)
WAIT_DEMAND, DOSE_WATER, DOSE_SYRUP, MIX, COOL, CARBONATE, WAIT_BUFFER, TRANSFER = 5, 10, 20, 30, 40, 50, 55, 60
PLC_BUFFER_LOW, PLC_BUFFER_MIN, PLC_AVAIL_BAND, PLC_BUFFER_MAX = 800, 100, 20, 1950
PLC_SYRUP_TARGET, PLC_MIX_TIME_S = PLC_DOSE_TARGET - PLC_WATER_BATCH, 20.0


class SyrupTestController:
    """The syrup PLC's sequence without alarms, Stop or modes: steps 5-60 and Product_Available."""

    __test__ = False  # not a pytest test class

    def __init__(self) -> None:
        self.step = WAIT_DEMAND
        self.rules = PlcRules()
        self.product_available = False
        self.mix_s = 0.0
        self.batches = 0
        self.steps_seen: list[int] = [WAIT_DEMAND]

    def signals(self, s: SyrupSensors, dt: float) -> SyrupSignals:
        # Product_Available: hysteresis on the buffer level only
        if s.buffer_level >= PLC_BUFFER_MIN + PLC_AVAIL_BAND:
            self.product_available = True
        elif s.buffer_level < PLC_BUFFER_MIN:
            self.product_available = False
        self.rules.update(s, dt, carbonating=self.step == CARBONATE)

        room = s.buffer_level + s.mix_level <= PLC_BUFFER_MAX
        before = self.step
        if self.step == WAIT_DEMAND and s.buffer_level < PLC_BUFFER_LOW and s.syrup_level >= PLC_SYRUP_TARGET:
            self.step = DOSE_WATER
        elif self.step == DOSE_WATER and s.mix_level >= PLC_WATER_BATCH:
            self.step = DOSE_SYRUP
        elif self.step == DOSE_SYRUP and s.mix_level >= PLC_DOSE_TARGET:
            self.step, self.mix_s = MIX, 0.0
        elif self.step == MIX:
            self.mix_s += dt
            if self.mix_s >= PLC_MIX_TIME_S - 1e-9:
                self.step = COOL
        elif self.step == COOL and self.rules.temp_ok:
            self.step = CARBONATE
        elif self.step == CARBONATE and self.rules.carb_s >= 10.0 - 1e-9:
            self.step = TRANSFER if room else WAIT_BUFFER
        elif self.step == WAIT_BUFFER and room:
            self.step = TRANSFER
        elif self.step == TRANSFER and s.mix_level <= PLC_MIX_EMPTY:
            self.step = WAIT_DEMAND
            self.batches += 1
        if self.step != before:
            self.steps_seen.append(self.step)

        held = self.rules.outputs(s)  # agitator on, cooling while not Temp_OK, CO2 while wanted
        out = SyrupOutputs(
            water_pump=self.step == DOSE_WATER,
            syrup_pump=self.step == DOSE_SYRUP,
            agitator=self.step in (MIX, COOL, CARBONATE, WAIT_BUFFER),
            cooling_valve=self.step in (COOL, CARBONATE, WAIT_BUFFER) and held.cooling_valve,
            co2_valve=self.step in (CARBONATE, WAIT_BUFFER) and held.co2_valve,
            transfer_pump=self.step == TRANSFER,
        )
        return SyrupSignals(out, self.product_available)


def with_bottle_at_fill(plant: Plant) -> None:
    from simulator.bottle import Bottle
    from simulator.machines.filler import FILL

    plant.filler.pockets[FILL] = Bottle(1)


YES = SyrupSignals(product_available=True)
NO = SyrupSignals(product_available=False)


# --- Optional syrup room, stand-in unchanged ------------------------------------------
def test_without_a_syrup_room_the_stand_in_stays():
    plant = Plant()
    assert plant.syrup is None and plant.syrup_writes() is None
    assert plant.writes().product_available  # held TRUE
    w, _ = plant.step(FillerOutputs(), TICK, NO)  # a syrup argument is ignored
    assert w.product_available
    plant.product_available = False
    assert not plant.writes().product_available


# --- The Product_Available wire ---------------------------------------------------------
def test_no_signal_before_the_first_step():
    assert not Plant(with_syrup=True).writes().product_available


def test_the_filler_gets_the_syrup_plc_signal():
    plant = Plant(with_syrup=True)
    w, _ = plant.step(FillerOutputs(), TICK, YES)
    assert w.product_available and plant.writes().product_available
    w, _ = plant.step(FillerOutputs(), TICK, NO)
    assert not w.product_available


def test_a_dead_syrup_plc_drives_no_wire():
    plant = Plant(with_syrup=True)
    plant.step(FillerOutputs(), TICK, YES)
    w, _ = plant.step(FillerOutputs(), TICK, None)  # no answer from the PLC
    assert not w.product_available


def test_the_plant_remembers_what_the_syrup_plc_said_last():
    plant = Plant(with_syrup=True)
    assert plant.syrup_signals is None  # nothing read yet
    signals = SyrupSignals(SyrupOutputs(agitator=True), True)
    plant.step(FillerOutputs(), TICK, signals)
    assert plant.syrup_signals is signals  # the panel shows these output lamps
    plant.step(FillerOutputs(), TICK, None)
    assert plant.syrup_signals is None  # PLC not answering
    filler_only = Plant()
    filler_only.step(FillerOutputs(), TICK, signals)
    assert filler_only.syrup_signals is None  # no syrup room: ignored


def test_product_flows_with_pipe_and_buffer_not_with_the_signal():
    """The signal tells the filler PLC what to do; the liquid only cares about the pipe and the tank."""
    for signal, pipe, buffer_l, flows in (
        (YES, True, 500.0, True),
        (NO, True, 500.0, True),  # signal FALSE: a real filler PLC would not open the valve, but if it does, product comes
        (YES, False, 500.0, False),  # pipe cut
        (YES, True, 0.0, False),  # buffer empty, signal wrongly TRUE: nothing comes out
    ):
        plant = Plant(with_syrup=True)
        plant.product_available = pipe
        plant.syrup.buffer_l = buffer_l
        with_bottle_at_fill(plant)
        for _ in range(20):
            plant.step(FILL_VALVE, TICK, signal)
        assert (plant.filler.dispensed_ml > 0) is flows, (signal, pipe, buffer_l)
        assert (plant.syrup.drawn_l > 0) is flows


def test_a_bottle_is_not_filled_from_an_empty_buffer():
    """BufferMin 0 on the HMI keeps Product_Available TRUE while the buffer runs dry: the bottle stays short."""
    plant = Plant(with_syrup=True)
    plant.syrup.buffer_l = 3.0  # 3 L = 100 ml of fill at scale 30
    with_bottle_at_fill(plant)
    for _ in range(50):  # 5 s of open valve would be 500 ml
        plant.step(FILL_VALVE, TICK, YES)
    assert plant.syrup.buffer_l == pytest.approx(0, abs=1e-9)
    assert plant.filler.pockets[FILL].level_ml == pytest.approx(100, abs=10)  # at most one sub-step more


# --- The filler draws from the buffer --------------------------------------------------
def test_a_bottle_takes_its_share_from_the_buffer():
    plant = Plant(with_syrup=True)
    start = plant.syrup.buffer_l
    with_bottle_at_fill(plant)
    for _ in range(50):  # 5 s of fill = 500 ml
        plant.step(FILL_VALVE, TICK, YES)
    assert plant.filler.dispensed_ml == pytest.approx(500)
    assert plant.syrup.buffer_l == pytest.approx(start - 0.5 * config.BUFFER_DRAW_SCALE)  # 25 L


def test_the_ledgers_agree_and_the_scale_is_a_plant_attribute():
    plant = Plant(with_syrup=True)
    plant.buffer_draw_scale = 10.0
    for _ in range(30):  # no bottle: pure spill, still drawn
        plant.step(FILL_VALVE, TICK, YES)
    assert plant.syrup.drawn_l == pytest.approx(plant.filler.dispensed_ml / 1000 * 10.0)
    assert plant.syrup.drawn_l == pytest.approx(0.3 * 10.0)


def test_an_empty_buffer_warns_once_and_never_goes_negative():
    plant = Plant(with_syrup=True)
    plant.syrup.buffer_l = 10.0
    texts = []
    for _ in range(100):  # 10 s of flow = 1 l * 50 = 50 L wanted, 10 L there
        _, events = plant.step(FILL_VALVE, TICK, YES)
        texts += [e.text for e in events if e.is_warning and "ran empty" in e.text]
    assert plant.syrup.buffer_l == 0
    assert len(texts) == 1
    assert plant.syrup.drawn_l == pytest.approx(10.0)  # no more product than there was


def test_a_late_tick_draws_like_short_ticks():
    late, short = Plant(with_syrup=True), Plant(with_syrup=True)
    for plant in (late, short):
        with_bottle_at_fill(plant)
    late.step(FILL_VALVE, 0.35, YES)
    for _ in range(35):
        short.step(FILL_VALVE, 0.01, YES)  # 35 tiny ticks are one sub-step each
    assert late.syrup.buffer_l == pytest.approx(short.syrup.buffer_l)
    assert late.filler.dispensed_ml == pytest.approx(short.filler.dispensed_ml)


# --- The syrup room runs inside the plant -------------------------------------------------
def test_the_syrup_plc_outputs_move_the_syrup_room():
    plant = Plant(with_syrup=True)
    for _ in range(50):
        plant.step(FillerOutputs(), TICK, SyrupSignals(SyrupOutputs(water_pump=True)))
    assert plant.syrup.mix_l == pytest.approx(5 * config.WATER_FLOW_L_S)
    assert plant.syrup_writes().sensors.mix_level == 100
    for _ in range(20):
        plant.step(FillerOutputs(), TICK, None)  # PLC gone: all pumps off
    assert plant.syrup.mix_l == pytest.approx(5 * config.WATER_FLOW_L_S)


def test_heartbeats_are_separate_and_the_e_stop_is_shared():
    plant = Plant(with_syrup=True)
    plant.syrup_heartbeat.enabled = False
    plant.step(FillerOutputs(), 1.0, YES)
    assert plant.writes().heartbeat and not plant.syrup_writes().heartbeat
    plant.e_stop_ok = False
    assert not plant.writes().e_stop_ok and not plant.syrup_writes().e_stop_ok


# --- The whole line, both machines, no PLC -------------------------------------------------
def test_the_whole_line_runs_ten_minutes_with_both_machines():
    plant, filler_ctrl, syrup_ctrl = Plant(with_syrup=True), TestController(), SyrupTestController()
    log = EventLog(max_events=100_000)
    w, sw = plant.writes(), plant.syrup_writes()
    buffer_min, buffer_max, available_lost = plant.syrup.buffer_l, plant.syrup.buffer_l, False
    for tick in range(6000):  # 600 s
        signals = syrup_ctrl.signals(sw.sensors, TICK)
        w, events = plant.step(filler_ctrl.outputs(w, TICK), TICK, signals)
        sw = plant.syrup_writes()
        log.extend(events)
        buffer_min, buffer_max = min(buffer_min, plant.syrup.buffer_l), max(buffer_max, plant.syrup.buffer_l)
        available_lost |= not w.product_available
    print(f"batches {syrup_ctrl.batches}, bottles {plant.labeler.taken}, "
          f"buffer {buffer_min:.0f}..{buffer_max:.0f} L, steps {syrup_ctrl.steps_seen[:9]}")
    assert syrup_ctrl.batches >= 2
    assert syrup_ctrl.steps_seen[1] == DOSE_WATER  # the first batch started at once (buffer 500 < 800)
    assert plant.labeler.taken >= 20
    assert set(discharged(log)) == {"left filler (500 ml)"}
    assert warnings(log) == []
    assert not available_lost  # the draw scale leaves the buffer well above the PLC's BufferMin
    assert buffer_min >= PLC_BUFFER_MIN + PLC_AVAIL_BAND and buffer_max <= PLC_BUFFER_MAX
    room = plant.syrup
    assert room.drawn_l == pytest.approx(plant.filler.dispensed_ml / 1000 * plant.buffer_draw_scale)
    inside = room.syrup_l + room.mix_l + room.buffer_l + room.spilled_l
    assert inside == pytest.approx(room.start_l + room.water_in_l + room.delivered_l - room.drawn_l, abs=1e-6)
