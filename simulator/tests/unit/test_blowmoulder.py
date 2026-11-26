"""Blow moulder physics: stage 1 oven station (hopper + chute, loader, oven pocket, lamps) and stage 2
mould station (arm, gripper, mould, stretch rod, blow air, ejector, dead plate onto C1).  uv run pytest

No PLC here: the tests write the PLC outputs by hand, in ticks of 0.1 s as the plant will.
`plc_lamp_power` is the PLC's P controller (state-diagram.md rule 5) in the PLC's integer math, and
`OvenStation` plays the oven station's step table (FEED -> LOAD -> HEAT -> HOT) in the real loop's
order (read sensors, set outputs, step); `MouldStation` does the same for the mould station's step table
(10 WAIT_PREFORM -> 20 GRIP -> ... -> 80 EJECT -> 10). Their outputs reach the physics `delay` ticks late: 1 tick is the
real loop (the PLC scans several times between the simulator's write and its next read), 2 ticks the
worst case (a scan just missed).
"""

from collections import deque
from dataclasses import fields

import pytest

from simulator import address_map_blowmoulder as bmap
from simulator import config
from simulator.machines.blowmoulder import (Blowmoulder, BlowmoulderOutputs, BlowmoulderSensors, Hopper, OvenThermal,
                                            Preform)

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
PLC_GRIP_S = 0.3
PLC_HOLD_S = 1.0
PLC_BLOW_SETPOINT = 350  # 0.1 bar
PLC_BLOW_SETPOINT_MAX = 380  # HMI limit
PLC_MAX_PRESSURE = 400
PLC_EXHAUST_LIMIT = 5
PLC_BLOW_TIMEOUT_S = 2
PLC_EXHAUST_TIMEOUT_S = 3
PLC_RAW_FULL, PLC_PRESS_FULL = 27648, 400
PLC_RAW_MAX = 32511

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


# =====================================================================================
# Stage 2: mould station
# =====================================================================================
def plc_pressure(raw: int) -> int:
    """Rule 6 as the PLC computes it: DINT_TO_INT(INT_TO_DINT(raw) * 400 / 27648), 0.1 bar, truncated toward 0."""
    return int(raw * PLC_PRESS_FULL / PLC_RAW_FULL)


def until(bm: Blowmoulder, out: BlowmoulderOutputs, cond, limit_s: float = PLC_MOVE_TIMEOUT_S) -> float:
    """Step with `out` until cond() holds; the time it took (asserted below limit_s)."""
    t = 0.0
    while not cond():
        bm.step(out, TICK, t)
        t += TICK
        assert t < limit_s + 1e-9, f"not reached within {limit_s} s"
    return t


def at_mould_with_preform(temp_c: float = 104.0) -> Blowmoulder:
    """The arm at the mould, the gripper closed with a preform taken from the oven at temp_c."""
    bm = with_preform_in_pocket(temp_c)
    until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed)
    until(bm, BlowmoulderOutputs(gripper=True, arm_to_mould=True), lambda: bm.arm_at_mould)
    return bm


def closed_with_preform(temp_c: float = 104.0) -> Blowmoulder:
    """The preform in the closed mould, arm home, exhaust open (CLOSE done)."""
    bm = at_mould_with_preform(temp_c)
    run(bm, BlowmoulderOutputs(arm_to_mould=True, exhaust_valve=True), PLC_GRIP_S)
    until(bm, BlowmoulderOutputs(exhaust_valve=True), lambda: bm.arm_at_oven)
    until(bm, BlowmoulderOutputs(mould_close=True, exhaust_valve=True), lambda: bm.mould_closed)
    return bm


STRETCH = BlowmoulderOutputs(mould_close=True, stretch_rod=True, pre_blow_valve=True)
BLOW = BlowmoulderOutputs(mould_close=True, stretch_rod=True, high_blow_valve=True)
EXHAUST = BlowmoulderOutputs(mould_close=True, stretch_rod=True, exhaust_valve=True)


def blown(temp_c: float = 104.0) -> Blowmoulder:
    """A bottle blown at 35 bar and held, still in the closed mould under pressure."""
    bm = closed_with_preform(temp_c)
    until(bm, STRETCH, lambda: bm.rod_down)
    until(bm, BLOW, lambda: plc_pressure(bm.blow_pressure_raw) >= PLC_BLOW_SETPOINT)
    run(bm, BLOW, PLC_HOLD_S)
    return bm


def opened(bm: Blowmoulder) -> Blowmoulder:
    """EXHAUST -> ROD_UP -> OPEN, as the PLC does it."""
    until(bm, EXHAUST, lambda: plc_pressure(bm.blow_pressure_raw) < PLC_EXHAUST_LIMIT)
    until(bm, BlowmoulderOutputs(mould_close=True, exhaust_valve=True), lambda: bm.rod_up)
    until(bm, BlowmoulderOutputs(exhaust_valve=True), lambda: bm.mould_open)
    return bm


# --- Moving parts --------------------------------------------------------------------------
@pytest.mark.parametrize("attr, out, sensor, back_sensor", [
    ("arm", BlowmoulderOutputs(arm_to_mould=True), "arm_at_mould", "arm_at_oven"),
    ("mould", BlowmoulderOutputs(mould_close=True), "mould_closed", "mould_open"),
    ("rod", BlowmoulderOutputs(mould_close=True, stretch_rod=True), "rod_down", "rod_up"),
    ("ejector", BlowmoulderOutputs(ejector=True), "ejector_out", "ejector_home"),
])
def test_every_move_ends_well_inside_move_timeout(attr, out, sensor, back_sensor):
    bm = Blowmoulder()
    if attr == "rod":
        until(bm, BlowmoulderOutputs(mould_close=True), lambda: bm.mould_closed)
    assert getattr(bm, back_sensor)
    assert until(bm, out, lambda: getattr(bm, sensor)) <= 0.5 + 1e-9
    back = BlowmoulderOutputs(mould_close=attr == "rod")
    assert until(bm, back, lambda: getattr(bm, back_sensor)) <= 0.5 + 1e-9


def test_gripper_closes_inside_grip_time():
    bm = Blowmoulder()
    assert until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed) < PLC_GRIP_S


# --- Hand-over and release -----------------------------------------------------------------
def test_hand_over_takes_the_preform_at_the_arms_first_move():
    bm = with_preform_in_pocket(104.0)
    until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed)
    assert bm.preform_in_oven  # the closed gripper alone does not take it (it cools a little meanwhile: lamps 0)
    events = bm.step(BlowmoulderOutputs(gripper=True, arm_to_mould=True), TICK, 1.0)
    assert not bm.preform_in_oven and bm.held.id == 1
    assert bm.oven_temp == round(bm.held.transfer_c * 10)  # the pyrometer now sees the wall, no jump
    assert f"Preform 1 out of the oven at {bm.held.transfer_c:.1f} degC" in events[0].text


def test_arm_leaving_with_the_gripper_open_leaves_the_preform():
    bm = with_preform_in_pocket(104.0)
    events = run(bm, BlowmoulderOutputs(arm_to_mould=True), 1.0)
    assert bm.preform_in_oven and bm.held is None and warnings(events) == []


def test_release_at_the_open_mould_puts_the_preform_in():
    bm = at_mould_with_preform()
    events = run(bm, BlowmoulderOutputs(arm_to_mould=True), PLC_GRIP_S)
    assert bm.bottle_in_mould and bm.held is None and warnings(events) == []
    assert not bm.gripper_closed


def test_release_mid_swing_drops_the_preform():
    bm = with_preform_in_pocket()
    until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed)
    run(bm, BlowmoulderOutputs(gripper=True, arm_to_mould=True), 0.2)
    events = run(bm, BlowmoulderOutputs(arm_to_mould=True), 0.3)
    assert warnings(events) == ["Preform 1 dropped: the gripper opened and the arm is not at the mould"]
    assert not bm.bottle_in_mould and [p.id for p in bm.dropped] == [1]


def test_release_into_an_occupied_mould_drops_the_preform():
    bm = at_mould_with_preform()
    bm.cavity = Preform(9)
    events = run(bm, BlowmoulderOutputs(arm_to_mould=True), PLC_GRIP_S)
    assert "the mould is not empty" in warnings(events)[0] and bm.cavity.id == 9


# --- Blow pressure ---------------------------------------------------------------------------
def test_pre_blow_reaches_about_8_bar_while_the_rod_goes_down():
    bm = closed_with_preform()
    assert until(bm, STRETCH, lambda: bm.rod_down) == pytest.approx(config.ROD_DOWN_S)
    assert 7.5 <= bm.pressure_bar <= config.PRE_BLOW_BAR
    assert bm.cavity.stretched


@pytest.mark.parametrize("setpoint", [PLC_BLOW_SETPOINT, PLC_BLOW_SETPOINT_MAX])
def test_high_blow_reaches_the_setpoint_well_inside_blow_timeout(setpoint):
    bm = closed_with_preform()
    until(bm, STRETCH, lambda: bm.rod_down)
    t = until(bm, BLOW, lambda: plc_pressure(bm.blow_pressure_raw) >= setpoint, limit_s=PLC_BLOW_TIMEOUT_S)
    assert t <= 0.7  # 0.31 s to 35 bar, 0.52 s to 38 bar


def test_hold_stays_below_max_pressure_and_exhaust_is_fast():
    bm = blown()
    run(bm, BLOW, 5.0)  # far longer than HOLD
    assert plc_pressure(bm.blow_pressure_raw) < PLC_MAX_PRESSURE
    assert bm.pressure_bar == pytest.approx(config.HIGH_BLOW_BAR, abs=0.05)
    t = until(bm, EXHAUST, lambda: plc_pressure(bm.blow_pressure_raw) < PLC_EXHAUST_LIMIT,
              limit_s=PLC_EXHAUST_TIMEOUT_S)
    assert t <= 1.0  # 0.65 s


def test_transducer_counts_fit_the_plc_scaling():
    bm = Blowmoulder()
    bm.pressure_bar = 35.0
    assert bm.blow_pressure_raw == 24192 and plc_pressure(24192) == PLC_BLOW_SETPOINT
    bm.pressure_bar = 39.0
    assert bm.blow_pressure_raw <= PLC_RAW_MAX
    bm.pressure_bar = 60.0  # far over range: the register clips at the INT limit
    assert bm.blow_pressure_raw == 32767


def test_high_blow_with_the_exhaust_open_never_reaches_the_setpoint():
    bm = closed_with_preform()
    until(bm, STRETCH, lambda: bm.rod_down)
    events = run(bm, BlowmoulderOutputs(mould_close=True, stretch_rod=True, high_blow_valve=True,
                                        exhaust_valve=True), PLC_BLOW_TIMEOUT_S)
    assert warnings(events) == ["high blow with the exhaust open"]
    assert bm.pressure_bar == pytest.approx(19.5, abs=0.5)  # -> Alm_BlowFault on the PLC


def test_blowing_into_an_open_mould_builds_no_pressure():
    bm = Blowmoulder()
    events = run(bm, BlowmoulderOutputs(high_blow_valve=True), 1.0)
    assert warnings(events) == ["blow air into a mould that is not closed"] and bm.pressure_bar == 0.0


def test_opening_under_pressure_is_audited_and_vents():
    bm = blown()
    events = run(bm, BlowmoulderOutputs(stretch_rod=True), TICK)
    assert "mould opened under 39.0 bar" in warnings(events)[0]
    assert bm.pressure_bar == 0.0


def test_one_late_pressure_step_equals_three_short_ones():
    a, b = closed_with_preform(), closed_with_preform()
    a.step(BLOW, 0.3, 0.3)
    for i in range(3):
        b.step(BLOW, TICK, (i + 1) * TICK)
    assert a.pressure_bar == pytest.approx(b.pressure_bar, abs=1e-9)


# --- Rod and arm audits ------------------------------------------------------------------------
def test_rod_down_in_an_open_mould_is_audited():
    events = run(Blowmoulder(), BlowmoulderOutputs(stretch_rod=True), 1.0)
    assert warnings(events) == ["stretch rod down in a mould that is not closed"]


def test_arm_into_a_closed_mould_is_audited():
    bm = Blowmoulder()
    until(bm, BlowmoulderOutputs(mould_close=True), lambda: bm.mould_closed)
    events = run(bm, BlowmoulderOutputs(mould_close=True, arm_to_mould=True), 1.0)
    assert warnings(events) == ["arm inside a mould that is not open"]


def test_mould_closing_on_the_arm_is_audited():
    bm = Blowmoulder()
    until(bm, BlowmoulderOutputs(arm_to_mould=True), lambda: bm.arm_at_mould)
    events = run(bm, BlowmoulderOutputs(arm_to_mould=True, mould_close=True), TICK)
    assert warnings(events) == ["arm inside a mould that is not open"]


# --- Ejector, dead plate, C1 ---------------------------------------------------------------------
def test_ejected_bottle_lands_on_c1_with_one_pulse():
    bm = opened(blown())
    events = []
    t = until(bm, BlowmoulderOutputs(ejector=True, exhaust_valve=True), lambda: not bm.bottle_in_mould)
    assert t == pytest.approx(config.EJECTOR_OUT_S)
    assert bm.c1.slots[0].id == 1 and bm.dead_plate is None and bm.ejected == 1
    pulse = 0
    while bm.bottle_ejected:
        events += bm.step(BlowmoulderOutputs(exhaust_valve=True), TICK, 5.0)
        pulse += 1
    assert pulse == round(config.BOTTLE_EJECTED_S / TICK)
    assert warnings(events) == []


def test_bottle_waits_on_the_dead_plate_while_c1s_entry_is_taken():
    bm = opened(blown())
    bm.c1.slots[0] = Preform(99)  # anything on C1's entry slot
    until(bm, BlowmoulderOutputs(ejector=True), lambda: not bm.bottle_in_mould)
    assert bm.dead_plate is not None and not bm.bottle_ejected
    bm.c1.slots[0] = None
    bm.step(OFF, TICK, 9.0)
    assert bm.c1.slots[0].id == 1 and bm.bottle_ejected


def test_ejector_cannot_finish_against_a_full_dead_plate():
    bm = opened(blown())
    bm.dead_plate = Preform(99)
    bm.c1.slots[0] = Preform(98)
    events = run(bm, BlowmoulderOutputs(ejector=True), PLC_MOVE_TIMEOUT_S)
    assert warnings(events) == ["ejector stopped: a bottle still stands on the dead plate"]
    assert bm.bottle_in_mould and bm.ejector == pytest.approx(config.EJECTOR_BLOCKED)  # -> Alm_HandlingJam


def test_ejector_into_a_closed_mould_moves_nothing():
    bm = blown()
    events = run(bm, BlowmoulderOutputs(mould_close=True, ejector=True), 1.0)
    assert "ejector out with the mould not open" in warnings(events) and bm.bottle_in_mould


@pytest.mark.parametrize("make, problem", [
    (lambda: opened(closed_with_preform()), "left the blow moulder as an unblown preform (Preform 1)"),
    (lambda: opened(blown(80.0)), "blown cold: 77.8 degC at the hand-over (needs 95)"),  # 80 degC, cooled in GRIP
    (lambda: opened(blown(125.0)), "overheated: 125.0 degC in the oven (crystallised above 120)"),
])
def test_bad_bottles_are_audited_at_c1(make, problem):
    bm = make()
    events = run(bm, BlowmoulderOutputs(ejector=True), 0.5)
    assert warnings(events) == [problem]
    assert bm.c1.slots[0].id == 1  # no reject: it goes onto C1 anyway (Q4)


def test_half_blown_bottle_is_audited():
    bm = closed_with_preform()
    until(bm, STRETCH, lambda: bm.rod_down)
    run(bm, EXHAUST, 0.1)
    opened(bm)
    events = run(bm, BlowmoulderOutputs(ejector=True), 0.5)
    assert len(warnings(events)) == 1 and warnings(events)[0].startswith("half-blown")


# --- Sensors ---------------------------------------------------------------------------------------
def test_sensor_fields_follow_the_address_map():
    """Every sensor field has its address constant (same name upper case) the simulator writes."""
    for f in fields(BlowmoulderSensors):
        address = getattr(bmap, f.name.upper())
        assert address in bmap.SIM_WRITE_COILS | bmap.SIM_WRITE_REGISTERS, f.name
    assert len(fields(BlowmoulderSensors)) == 15


def test_sensors_at_rest():
    s = Blowmoulder().sensors()
    assert (s.arm_at_oven, s.mould_open, s.rod_up, s.arm_at_mould, s.mould_closed, s.rod_down) == (
        True, True, True, False, False, False)
    assert not s.bottle_in_mould and not s.c1_full and s.blow_pressure_raw == 0 and s.oven_temp == 200


# --- The whole machine with a Python copy of both step tables ----------------------------------------
MOULD_TIMEOUTS = {22: 3, 24: 3, 26: 3, 30: 3, 40: 3, 50: PLC_BLOW_TIMEOUT_S, 60: PLC_EXHAUST_TIMEOUT_S, 62: 3,
                  70: 3, 80: 3}


class MouldStation:
    """The PLC's mould station (state-diagram.md step table, rules 1, 3, 4 without the modes), reading
    the same sensors as the real PLC, with its pressure scaling."""

    def __init__(self, oven: OvenStation) -> None:
        self.oven = oven
        self.step_no = 10
        self.step_s = 0.0
        self.longest: dict[int, float] = {}  # step -> longest time spent in it

    def scan(self) -> BlowmoulderOutputs:
        bm, s = self.oven.bm, self.step_no
        pressure = plc_pressure(bm.blow_pressure_raw)
        go = {
            10: self.oven.step_no == 25 and bm.mould_open and not bm.bottle_in_mould and bm.arm_at_oven and bm.rod_up,
            20: self.step_s >= PLC_GRIP_S - 1e-9,
            22: bm.arm_at_mould,
            24: self.step_s >= PLC_GRIP_S - 1e-9 and bm.bottle_in_mould,
            26: bm.arm_at_oven,
            30: bm.mould_closed,
            40: bm.rod_down,
            50: pressure >= PLC_BLOW_SETPOINT,
            52: self.step_s >= PLC_HOLD_S - 1e-9,
            60: pressure < PLC_EXHAUST_LIMIT,
            62: bm.rod_up,
            70: bm.mould_open,
            80: not bm.bottle_in_mould,
        }[s]
        if go:
            nxt = {10: 20, 20: 22, 22: 24, 24: 26, 26: 30, 30: 40, 40: 50, 50: 52, 52: 60, 60: 62, 62: 70, 70: 80,
                   80: 10}[s]
            self.longest[s] = max(self.longest.get(s, 0.0), self.step_s)
            self.step_no, self.step_s = nxt, 0.0
        s = self.step_no
        return BlowmoulderOutputs(
            arm_to_mould=s in (22, 24),
            gripper=s in (20, 22),
            mould_close=30 <= s <= 62,
            stretch_rod=s in (40, 50, 52, 60),
            pre_blow_valve=s == 40,
            high_blow_valve=s in (50, 52),
            exhaust_valve=s not in (40, 50, 52),
            ejector=s == 80 and not bm.c1_full,
        )


class Machine(OvenStation):
    """Both stations; C1 drained at its exit (the filler would take the bottles)."""

    def __init__(self) -> None:
        super().__init__()
        self.mould = MouldStation(self)
        self.taken = []

    def _plc_scan(self) -> BlowmoulderOutputs:
        oven = super()._plc_scan()
        mould = self.mould.scan()
        self.mould.step_s += TICK
        return BlowmoulderOutputs(hopper_feed=oven.hopper_feed, loader=oven.loader, lamp_power=oven.lamp_power,
                                  **{f.name: getattr(mould, f.name) for f in fields(mould)
                                     if f.name not in ("hopper_feed", "loader", "lamp_power")})

    def tick(self) -> None:
        super().tick()
        self.bm.c1.step(True, TICK)
        bottle = self.bm.c1.take_at_exit()
        if bottle is not None:
            self.taken.append(bottle)


def test_both_stations_make_bottles_without_a_warning():
    m = Machine()
    ejected_at = []
    while m.bm.ejected < 4:
        before = m.bm.ejected
        m.tick()
        if m.bm.ejected > before:
            ejected_at.append(m.now)
        assert m.now < 40.0
    assert warnings(m.events) == []
    ids = sorted([b.id for b in m.bm.c1.slots if b] + [b.id for b in m.taken])
    assert ids == [1, 2, 3, 4]
    for step_no, longest in m.mould.longest.items():
        if step_no in MOULD_TIMEOUTS:
            assert longest < MOULD_TIMEOUTS[step_no], (step_no, longest)
    periods = [b - a for a, b in zip(ejected_at, ejected_at[1:])]
    assert all(MOULD_CYCLE_S - 0.5 <= p < 7.0 for p in periods), periods
