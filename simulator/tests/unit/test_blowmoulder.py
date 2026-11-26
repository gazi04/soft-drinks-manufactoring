"""Blow moulder physics: stage 1 oven station (hopper + chute, loader, oven pocket, lamps).  uv run pytest

No PLC here: the tests write the PLC outputs by hand, in ticks of 0.1 s as the plant will.
`plc_lamp_power` is the PLC's P controller (state-diagram.md rule 5) in the PLC's integer math, and
`OvenStation` plays the oven station's step table (FEED -> LOAD -> HEAT -> HOT) in the real loop's
order (read sensors, set outputs, step). Its outputs reach the physics `delay` ticks late: 1 tick is the
real loop (the PLC scans several times between the simulator's write and its next read), 2 ticks the
worst case (a scan just missed).
"""

from collections import deque

import pytest

from simulator import config
from simulator.machines.blowmoulder import Blowmoulder, BlowmoulderOutputs, Hopper, OvenThermal, Preform

TICK = 0.1

# PLC numbers (plcs/blowmoulder/.../main.ld, docs/blowmoulder/state-diagram.md). config.py must not
# import PLC values; if a PLC constant changes, change it here too.
PLC_MOVE_TIMEOUT_S = 3
PLC_HEAT_TIMEOUT_S = 15
PLC_SOAK_S = 1.0
PLC_OVEN_SETPOINT = 1050  # 0.1 degC
PLC_OVEN_BAND = 50
PLC_OVEN_MAX = 1300
PLC_ERR_MAX = 1000
PLC_KP = 10
PLC_BIAS = 0
MOULD_CYCLE_S = 5.4  # state-diagram.md: the mould station's cycle per bottle (simulator target)

FEED = BlowmoulderOutputs(hopper_feed=True)
LOAD = BlowmoulderOutputs(loader=True)
OFF = BlowmoulderOutputs()


def warnings(events) -> list[str]:
    return [e.text for e in events if e.is_warning]


def lamps(pct: int) -> BlowmoulderOutputs:
    return BlowmoulderOutputs(lamp_power=pct)


def plc_lamp_power(oven_temp: int, kp: int = PLC_KP, bias: int = PLC_BIAS,
                   setpoint: int = PLC_OVEN_SETPOINT) -> int:
    """Rule 5 as the PLC computes it: LIMIT the error, MUL, DIV (INT, truncates toward 0), ADD, LIMIT;
    0 at OvenMax. Lamps_On (Auto + preform) is the caller's job."""
    if oven_temp >= PLC_OVEN_MAX:
        return 0
    error = max(-PLC_ERR_MAX, min(setpoint - oven_temp, PLC_ERR_MAX))
    p_term = int(kp * error / 10)
    return max(0, min(bias + p_term, 100))


def oven_ready(oven_temp: int) -> bool:
    return PLC_OVEN_SETPOINT - PLC_OVEN_BAND <= oven_temp <= PLC_OVEN_SETPOINT + PLC_OVEN_BAND


class OvenStation:
    """The PLC's oven station (FEED 10 -> LOAD 15 -> HEAT 20 -> HOT 25) with the lamp P controller.
    No mould: a test takes the hot preform out with take_preform() to end the cycle."""

    def __init__(self, bm: Blowmoulder | None = None, kp: int = PLC_KP, bias: int = PLC_BIAS, delay: int = 1) -> None:
        self.bm = bm or Blowmoulder()
        self.kp, self.bias = kp, bias
        self.step_no = 10
        self.soak_s = 0.0
        self.now = 0.0
        self.events: list = []
        self.temps: list[float] = []  # OvenTemp in degC after every tick
        self._pipe: deque[BlowmoulderOutputs] = deque([OFF] * (delay - 1))
        self.times: dict[int, float] = {10: 0.0}  # step -> time it was first entered (starts in FEED)

    def _plc_scan(self) -> BlowmoulderOutputs:
        bm = self.bm
        ready = oven_ready(bm.oven_temp)
        if self.step_no == 10 and bm.preform_at_loader and not bm.preform_in_oven:
            self._goto(15)
        elif self.step_no == 15 and bm.preform_in_oven:
            self._goto(20)
        elif self.step_no == 20 and self.soak_s >= PLC_SOAK_S - 1e-9:
            self._goto(25)
        elif self.step_no == 25 and not bm.preform_in_oven:
            self._goto(10)
        self.soak_s = self.soak_s + TICK if self.step_no == 20 and ready else 0.0
        lamp = plc_lamp_power(bm.oven_temp, self.kp, self.bias) if bm.preform_in_oven else 0
        return BlowmoulderOutputs(hopper_feed=self.step_no == 10 and not bm.preform_at_loader,
                                  loader=self.step_no == 15 and not bm.preform_in_oven, lamp_power=lamp)

    def _goto(self, step_no: int) -> None:
        self.step_no = step_no
        self.times.setdefault(step_no, self.now)

    def tick(self) -> None:
        self._pipe.append(self._plc_scan())
        out = self._pipe.popleft()
        self.now += TICK
        self.events += self.bm.step(out, TICK, self.now)
        self.temps.append(self.bm.oven_temp / 10)

    def run(self, seconds: float) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick()

    def run_until(self, cond, limit_s: float) -> float:
        start = self.now
        while not cond():
            assert self.now - start < limit_s, f"not reached within {limit_s} s"
            self.tick()
        return self.now - start


def run(bm: Blowmoulder, out: BlowmoulderOutputs, seconds: float, now: float = 0.0) -> list:
    events = []
    for i in range(round(seconds / TICK)):
        events += bm.step(out, TICK, now + (i + 1) * TICK)
    return events


def with_preform_in_pocket(temp_c: float = config.ROOM_C) -> Blowmoulder:
    bm = Blowmoulder()
    bm.pocket = Preform(1, temp_c, temp_c)
    return bm


# --- Hopper and chute ----------------------------------------------------------------
def test_chute_brings_a_preform_after_chute_time():
    bm = Blowmoulder()
    run(bm, FEED, config.CHUTE_S - TICK)
    assert not bm.preform_at_loader
    events = run(bm, FEED, TICK)
    assert bm.preform_at_loader and bm.at_loader.id == 1
    assert bm.hopper.count == config.HOPPER_PREFORMS - 1
    assert "Preform 1 at the loader" in events[0].text


def test_chute_waits_while_the_place_is_taken():
    bm = Blowmoulder()
    run(bm, FEED, 3.0)  # a preform at the loader; the next one must not come
    assert bm.at_loader.id == 1 and bm.hopper.count == config.HOPPER_PREFORMS - 1


def test_chute_keeps_its_progress_when_feed_drops():
    hopper = Hopper()
    assert hopper.step(True, True, 0.4) is None
    assert hopper.step(False, True, 5.0) is None
    assert hopper.step(True, True, 0.2).id == 1


def test_chute_needs_the_loader_home():
    """The pusher face covers the place while the loader is out."""
    bm = Blowmoulder()
    run(bm, BlowmoulderOutputs(hopper_feed=True, loader=True), 2.0)
    assert not bm.preform_at_loader
    run(bm, FEED, config.LOADER_RETURN_S + config.CHUTE_S)
    assert bm.preform_at_loader


def test_preform_ids_count_up():
    hopper = Hopper()
    assert [hopper.step(True, True, config.CHUTE_S).id for _ in range(3)] == [1, 2, 3]


def test_preform_low_and_hopper_empty():
    hopper = Hopper(config.PREFORM_LOW + 1)
    assert not hopper.preform_low
    hopper.step(True, True, config.CHUTE_S)
    assert hopper.preform_low and not hopper.hopper_empty
    hopper.count = 1
    assert hopper.step(True, True, config.CHUTE_S) is not None
    assert hopper.hopper_empty
    assert hopper.step(True, True, 10.0) is None


# --- Loader --------------------------------------------------------------------------
def test_loader_pushes_the_preform_into_the_pocket_well_inside_move_timeout():
    bm = Blowmoulder()
    run(bm, FEED, config.CHUTE_S)
    t = 0.0
    events = []
    while not bm.preform_in_oven:
        events += bm.step(LOAD, TICK, t)
        t += TICK
        assert t < PLC_MOVE_TIMEOUT_S
    assert t == pytest.approx(config.LOADER_OUT_S)
    assert not bm.preform_at_loader and bm.pocket.id == 1 and bm.loaded == 1
    assert "Preform 1 into the oven pocket" in events[-1].text
    run(bm, OFF, config.LOADER_RETURN_S)
    assert bm.loader_home


def test_loader_stroke_without_a_preform_does_nothing():
    bm = Blowmoulder()
    assert run(bm, LOAD, 1.0) == []
    assert bm.loader_out and not bm.preform_in_oven


def test_loader_into_an_occupied_pocket_is_refused_with_one_warning():
    bm = with_preform_in_pocket()
    run(bm, FEED, config.CHUTE_S)
    events = run(bm, LOAD, 2.0)
    assert warnings(events) == ["loader pushed a preform against the preform in the oven pocket"]
    assert bm.at_loader.id == 1 and bm.pocket.id == 1 and bm.loaded == 0


# --- Heating -------------------------------------------------------------------------
def test_cold_preform_in_drops_the_reading():
    bm = Blowmoulder()
    bm.thermal.wall_c = 90.0
    assert bm.oven_temp == 900
    run(bm, FEED, config.CHUTE_S)
    run(bm, LOAD, config.LOADER_OUT_S)
    assert bm.preform_in_oven and bm.oven_temp == pytest.approx(200, abs=1)


def test_full_lamps_reach_oven_max_in_about_four_seconds():
    bm = with_preform_in_pocket()
    t = 0.0
    while bm.oven_temp < PLC_OVEN_MAX:
        bm.step(lamps(100), TICK, t)
        t += TICK
    assert 3.5 <= t <= 4.5  # 3.9 s: a stuck lamp is caught by Alm_OvenOverTemp quickly
    assert bm.pocket.peak_c >= PLC_OVEN_MAX / 10


@pytest.mark.parametrize("delay", [1, 2])
def test_p_controller_heats_a_cold_preform_in_time(delay):
    st = OvenStation(delay=delay)
    st.run_until(lambda: st.step_no == 25, limit_s=PLC_HEAT_TIMEOUT_S)
    heat_s = st.times[25] - st.times[20]
    assert heat_s < 4.5  # ready ~2.8 s + soak 1 s, far below HeatTimeout 15 s
    oven_cycle = st.times[25] - st.times[10]  # chute + load + heat + soak
    assert oven_cycle < MOULD_CYCLE_S - 0.3  # ready before the mould is back (its GRIP 0.3 s is after the hand-over)
    assert warnings(st.events) == []


def settle(kp: int, bias: int = 0, delay: int = 1, seconds: float = 30.0) -> OvenStation:
    st = OvenStation(with_preform_in_pocket(), kp=kp, bias=bias, delay=delay)
    st.step_no = 20
    st.run(seconds)
    return st


def test_p_controller_settles_with_the_offset_rule_5_teaches():
    st = settle(PLC_KP)
    last = st.temps[-50:]
    assert max(last) - min(last) < 0.5
    offset = PLC_OVEN_SETPOINT / 10 - last[-1]
    assert 2.0 <= offset <= 4.0  # ~3 degC: P alone needs an error to give the ~30 % the preform needs
    assert oven_ready(round(last[-1] * 10))


def test_bias_removes_the_offset():
    st = settle(PLC_KP, bias=30)
    assert abs(PLC_OVEN_SETPOINT / 10 - st.temps[-1]) < 1.0


def test_low_kp_without_bias_never_gets_ready():
    """Kp 5: the 28 % the preform needs at 100 degC would need an error of 5.7 degC -> it settles below the window
    (HEAT timeout on the real PLC). Bias 30 gives the power without an error."""
    st = settle(5)
    assert max(st.temps) < (PLC_OVEN_SETPOINT - PLC_OVEN_BAND) / 10
    st = settle(5, bias=30)
    assert oven_ready(round(st.temps[-1] * 10))


@pytest.mark.parametrize("delay", [1, 2])
@pytest.mark.parametrize("kp", [1, 5, 10, 15, 20, 25, 30])
def test_lamp_loop_stays_below_oven_max(kp, delay):
    st = settle(kp, delay=delay)
    assert max(st.temps) < PLC_OVEN_MAX / 10
    last = st.temps[-50:]
    if kp <= 15:
        assert max(last) - min(last) < 0.5  # settled
    else:
        # P gain x dead time: a limit cycle, but inside the ready window (teaching point, roadmap Q5)
        assert all(oven_ready(round(t * 10)) for t in last)


# --- Empty pocket ----------------------------------------------------------------------
def test_take_preform_keeps_the_reading_and_the_wall_cools():
    bm = with_preform_in_pocket(105.0)
    preform = bm.take_preform()
    assert preform.id == 1 and not bm.preform_in_oven
    assert bm.oven_temp == 1050
    run(bm, OFF, 30.0)  # one wall time constant
    expected = config.ROOM_C + (105.0 - config.ROOM_C) / 2.718281828
    assert bm.oven_temp / 10 == pytest.approx(expected, abs=0.1)
    assert bm.take_preform() is None


def test_lamps_on_an_empty_pocket_heat_the_wall_slowly():
    bm = Blowmoulder()
    run(bm, lamps(100), 4.0)
    assert config.ROOM_C < bm.oven_temp / 10 < PLC_OVEN_MAX / 10  # a preform would pass OvenMax in 3.9 s


# --- Late tick and lamp range -------------------------------------------------------------
def test_one_late_tick_equals_three_short_ones():
    a, b = with_preform_in_pocket(), with_preform_in_pocket()
    a.thermal.wall_c = b.thermal.wall_c = 50.0
    out = BlowmoulderOutputs(hopper_feed=True, lamp_power=40)
    a.step(out, 0.3, 0.3)
    for i in range(3):
        b.step(out, TICK, (i + 1) * TICK)
    assert a.pocket.temp_c == pytest.approx(b.pocket.temp_c, abs=1e-9)
    assert a.hopper._chute_s == pytest.approx(b.hopper._chute_s)
    assert OvenThermal.balance_c(100) == pytest.approx(303.0)


@pytest.mark.parametrize("power, used", [(150, 100), (-5, 0)])
def test_lamp_power_outside_range_is_clamped_with_one_warning(power, used):
    bm = with_preform_in_pocket(50.0)
    ref = with_preform_in_pocket(50.0)
    events = run(bm, lamps(power), 1.0)
    run(ref, lamps(used), 1.0)
    assert warnings(events) == [f"LampPower {power} % outside 0-100 %"]
    assert bm.pocket.temp_c == pytest.approx(ref.pocket.temp_c)
