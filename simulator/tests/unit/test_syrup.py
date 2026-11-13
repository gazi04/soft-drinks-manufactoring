"""Syrup room physics, stage 1: tank volumes.  uv run pytest

No PLC here: the tests write the PLC outputs by hand. `Rig` steps the room in ticks of
0.1 s, the way the plant will do it. `Controller` plays the PLC's dosing rules in the
real loop's order (read sensors, set outputs, step), so the one-step latency is included.
"""

import random

import pytest

from simulator import config
from simulator.machines.syrup import SyrupOutputs, SyrupRoom, SyrupSensors

TICK = 0.1

# PLC numbers (plcs/syrup/pous/programs/main.ld, docs/syrup/state-diagram.md). config.py must not
# import PLC values; if a PLC default changes, change it here too.
PLC_WATER_BATCH = 500
PLC_DOSE_TARGET = 590  # WaterBatch + WaterBatch * 10 / SyrupRatio (55) = 500 + 90
PLC_MIX_EMPTY = 5
PLC_DOSE_TIMEOUT_S = 60

ON_WATER = SyrupOutputs(water_pump=True)
ON_SYRUP = SyrupOutputs(syrup_pump=True)
ON_TRANSFER = SyrupOutputs(transfer_pump=True)
ON_AGITATOR = SyrupOutputs(agitator=True)
OFF = SyrupOutputs()


class Rig:
    def __init__(self, **levels: float) -> None:
        self.room = SyrupRoom(**levels)
        self.now = 0.0
        self.warnings: list[str] = []

    def tick(self, out: SyrupOutputs = OFF, dt: float = TICK) -> SyrupSensors:
        self.now += dt
        self.warnings += [e.text for e in self.room.step(out, dt, self.now) if e.is_warning]
        return self.room.sensors()

    def run(self, seconds: float, out: SyrupOutputs = OFF) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick(out)


def dose(rig: Rig, pump: str, target: int, limit_s: float = 120.0) -> float:
    """Run `pump` like the PLC: on while the sensor is below target. Returns the seconds it took."""
    t = 0.0
    while t < limit_s:
        level = rig.room.sensors().mix_level  # 1. read sensors
        out = SyrupOutputs(**{pump: level < target})  # 2. set outputs
        if not getattr(out, pump):
            return t
        rig.tick(out)  # 3. physics step
        t += TICK
    raise AssertionError(f"{pump} did not reach {target} L within {limit_s} s")


# --- Flows ------------------------------------------------------------------------
def test_water_pump_adds_to_mixing_tank():
    rig = Rig(mix_l=0)
    rig.run(10, ON_WATER)
    assert rig.room.mix_l == pytest.approx(10 * config.WATER_FLOW_L_S)
    rig.run(5, OFF)
    assert rig.room.mix_l == pytest.approx(10 * config.WATER_FLOW_L_S)  # pump off: nothing moves


def test_syrup_pump_moves_syrup_into_mixing_tank():
    rig = Rig(syrup_l=600, mix_l=0)
    rig.run(10, ON_SYRUP)
    moved = 10 * config.SYRUP_FLOW_L_S
    assert rig.room.syrup_l == pytest.approx(600 - moved)
    assert rig.room.mix_l == pytest.approx(moved)
    assert rig.room.water_in_l == 0  # syrup is not mains water


def test_transfer_pump_moves_mixing_tank_into_buffer():
    rig = Rig(mix_l=300, buffer_l=500)
    rig.run(4, ON_TRANSFER)
    moved = 4 * config.TRANSFER_FLOW_L_S
    assert rig.room.mix_l == pytest.approx(300 - moved)
    assert rig.room.buffer_l == pytest.approx(500 + moved)


def test_valves_and_agitator_change_no_level():
    rig = Rig(syrup_l=600, mix_l=300, buffer_l=500)
    rig.run(10, SyrupOutputs(agitator=True, cooling_valve=True, co2_valve=True))
    assert (rig.room.syrup_l, rig.room.mix_l, rig.room.buffer_l) == (600, 300, 500)
    assert rig.warnings == []


def test_dt_zero_changes_nothing_and_a_late_tick_equals_three_steps():
    rig = Rig(mix_l=100)
    rig.tick(ON_WATER, dt=0.0)
    assert rig.room.mix_l == 100
    late, steps = Rig(mix_l=100), Rig(mix_l=100)
    late.tick(ON_WATER, dt=0.3)
    steps.run(0.3, ON_WATER)
    assert late.room.mix_l == pytest.approx(steps.room.mix_l)


# --- Empty and full tanks ---------------------------------------------------------
def test_syrup_pump_stops_at_an_empty_tank_and_warns_once():
    rig = Rig(syrup_l=0.75, mix_l=0)  # 0.5 L per tick: tick 2 is a PARTIAL step (0.25 L left of 0.5 wanted)
    rig.run(0.2, ON_SYRUP)
    assert rig.room.syrup_l == 0 and rig.room.mix_l == pytest.approx(0.75)
    assert rig.warnings == []  # the partial last step is not "running dry"
    rig.run(0.5, ON_SYRUP)  # now the pump really runs dry
    assert rig.room.mix_l == pytest.approx(0.75)  # no liquid from nothing
    assert len(rig.warnings) == 1 and "syrup pump runs dry" in rig.warnings[0]
    rig.run(0.1, OFF)  # period over
    rig.room.deliver_syrup(0.5)
    rig.run(0.3, ON_SYRUP)  # empties again and runs dry again: a second period, a second warning
    assert len(rig.warnings) == 2


def test_transfer_pump_never_makes_the_mixing_level_negative():
    rig = Rig(mix_l=30, buffer_l=0)
    levels = [rig.tick(ON_TRANSFER).mix_level for _ in range(50)]
    assert min(levels) == 0 and rig.room.mix_l >= 0
    assert rig.room.buffer_l == pytest.approx(30)
    assert len(rig.warnings) == 1 and "transfer pump runs dry" in rig.warnings[0]


def test_mixing_tank_overflow_clamps_counts_spill_and_warns_once():
    rig = Rig(mix_l=990)
    rig.run(2, ON_WATER)  # 40 L wanted, 10 L fit
    assert rig.room.mix_l == pytest.approx(config.MIX_TANK_L)
    assert rig.room.spilled_l == pytest.approx(30)
    assert len(rig.warnings) == 1 and "mixing tank overflows" in rig.warnings[0]
    rig.run(1, OFF)
    rig.run(1, ON_WATER)  # a new period after the pump was off
    assert len(rig.warnings) == 2


def test_buffer_overflow_clamps_counts_spill_and_warns_once():
    rig = Rig(mix_l=200, buffer_l=1990)
    rig.run(2, ON_TRANSFER)  # 50 L wanted, 10 L fit
    assert rig.room.buffer_l == pytest.approx(config.BUFFER_TANK_L)
    assert rig.room.spilled_l == pytest.approx(40)
    assert len(rig.warnings) == 1 and "buffer tank overflows" in rig.warnings[0]


def test_agitator_warning_below_minimum_level_only():
    rig = Rig(mix_l=config.AGITATOR_MIN_L - 1)
    rig.run(1, ON_AGITATOR)
    assert len(rig.warnings) == 1 and "propeller uncovered" in rig.warnings[0]
    rig.run(1, ON_AGITATOR)
    assert len(rig.warnings) == 1  # one per period
    rig.run(1, OFF)
    rig.room.mix_l = config.AGITATOR_MIN_L
    rig.run(1, ON_AGITATOR)
    assert len(rig.warnings) == 1  # covered: fine


# --- Sensors ----------------------------------------------------------------------
def test_levels_are_rounded_to_int():
    s = Rig(syrup_l=99.6, mix_l=0.4, buffer_l=1234.5).tick()
    assert (s.syrup_level, s.mix_level, s.buffer_level) == (100, 0, 1234)  # round half to even
    assert all(isinstance(v, int) for v in (s.syrup_level, s.mix_level, s.buffer_level))


def test_high_high_switch_uses_the_true_level():
    room = SyrupRoom(mix_l=config.MIX_HIGH_HIGH_L - 0.1)
    assert room.sensors().mix_high_high is False
    assert room.sensors().mix_level == round(config.MIX_HIGH_HIGH_L - 0.1)  # analog reads 980 already
    room.mix_l = config.MIX_HIGH_HIGH_L
    assert room.sensors().mix_high_high is True


# --- Interfaces -------------------------------------------------------------------
def test_deliver_syrup_is_limited_by_the_tank():
    room = SyrupRoom(syrup_l=900)
    assert room.deliver_syrup(50) == 50
    assert room.deliver_syrup(500) == pytest.approx(50)  # only 50 L of room left
    assert room.syrup_l == config.SYRUP_TANK_L
    assert room.delivered_l == pytest.approx(100)


def test_draw_buffer_is_limited_by_the_content():
    room = SyrupRoom(buffer_l=30)
    assert room.draw_buffer(10) == 10
    assert room.draw_buffer(100) == pytest.approx(20)
    assert room.buffer_l == 0 and room.drawn_l == pytest.approx(30)


def test_negative_amounts_raise():
    room = SyrupRoom()
    with pytest.raises(ValueError):
        room.deliver_syrup(-1)
    with pytest.raises(ValueError):
        room.draw_buffer(-1)


# --- Fit with the PLC -------------------------------------------------------------
def test_doses_fit_the_plc_dose_timeout_with_margin():
    water_s = PLC_WATER_BATCH / config.WATER_FLOW_L_S
    syrup_s = (PLC_DOSE_TARGET - PLC_WATER_BATCH) / config.SYRUP_FLOW_L_S
    for seconds in (water_s, syrup_s):
        assert seconds * 2 <= PLC_DOSE_TIMEOUT_S


def test_pump_overshoot_stays_within_one_tick_of_flow():
    # Water: from an empty tank to 500 L. The PLC stops the pump the tick AFTER the sensor reaches the
    # target, and the sensor is rounded, so the true level lands in [-0.5, + flow x tick) of the target.
    rig = Rig(mix_l=0)
    seconds = dose(rig, "water_pump", PLC_WATER_BATCH)
    error = rig.room.mix_l - PLC_WATER_BATCH
    assert -0.5 <= error < config.WATER_FLOW_L_S * TICK + 1e-9
    assert seconds == pytest.approx(PLC_WATER_BATCH / config.WATER_FLOW_L_S, abs=0.2)
    # Syrup: from 500 L to 590 L
    seconds = dose(rig, "syrup_pump", PLC_DOSE_TARGET)
    error = rig.room.mix_l - PLC_DOSE_TARGET
    assert -0.5 <= error < config.SYRUP_FLOW_L_S * TICK + 1e-9


def test_one_batch_with_a_plc_like_controller():
    rig = Rig(syrup_l=600, mix_l=0, buffer_l=500)
    dose(rig, "water_pump", PLC_WATER_BATCH)
    water = rig.room.mix_l
    dose(rig, "syrup_pump", PLC_DOSE_TARGET)
    batch = rig.room.mix_l
    assert batch == pytest.approx(PLC_DOSE_TARGET, abs=2.5)
    # transfer until the PLC sees MixEmpty: the sensor must read <= 5 L
    t = 0.0
    while rig.room.sensors().mix_level > PLC_MIX_EMPTY and t < 120:
        rig.tick(ON_TRANSFER)
        t += TICK
    assert t == pytest.approx((batch - PLC_MIX_EMPTY) / config.TRANSFER_FLOW_L_S, abs=0.3)
    assert 0 <= rig.room.mix_l <= PLC_MIX_EMPTY + config.TRANSFER_FLOW_L_S * TICK  # the residue stays
    assert rig.room.buffer_l == pytest.approx(500 + batch - rig.room.mix_l)
    assert rig.room.syrup_l == pytest.approx(600 - (batch - water))  # the syrup came out of its tank
    assert rig.room.spilled_l == 0 and rig.warnings == []


# --- Conservation -----------------------------------------------------------------
def test_no_liquid_is_created_or_lost():
    """Random outputs, deliveries and draws for 5 000 ticks: the ledger always balances."""
    rng = random.Random(7)
    rig = Rig(syrup_l=300, mix_l=100, buffer_l=1500)
    names = ("water_pump", "syrup_pump", "agitator", "cooling_valve", "co2_valve", "transfer_pump")
    for i in range(5000):
        if i % 50 == 0:  # change the outputs now and then, so pumps run long enough to fill/empty tanks
            out = SyrupOutputs(**{n: rng.random() < 0.4 for n in names})
        if i % 400 == 0:
            rig.room.deliver_syrup(rng.uniform(0, 600))
        if i % 90 == 0:
            rig.room.draw_buffer(rng.uniform(0, 500))
        rig.tick(out)
        room = rig.room
        assert 0 <= room.syrup_l <= config.SYRUP_TANK_L + 1e-9
        assert 0 <= room.mix_l <= config.MIX_TANK_L + 1e-9
        assert 0 <= room.buffer_l <= config.BUFFER_TANK_L + 1e-9
        inside = room.syrup_l + room.mix_l + room.buffer_l + room.spilled_l
        assert inside == pytest.approx(room.start_l + room.water_in_l + room.delivered_l - room.drawn_l, abs=1e-6)
    assert rig.room.spilled_l > 0 and rig.room.water_in_l > 0  # the run really exercised the limits


# === Stage 2: temperature and CO2 pressure =============================================
# PLC numbers (main.ld): CoolSetpoint 40 (4.0 degC), TempHyst 5, PressSetpoint 350, PressHyst 20,
# PressMax 600, CarbTime 10 s, CoolTimeout 60 s.
PLC_COOL_SETPOINT, PLC_TEMP_HYST = 40, 5
PLC_PRESS_SETPOINT, PLC_PRESS_HYST, PLC_PRESS_MAX = 350, 20, 600
PLC_CARB_TIME_S, PLC_COOL_TIMEOUT_S = 10.0, 60.0


class PlcRules:
    """The PLC's hysteresis latches and Carb_Timer, ported to Python to drive the physics in tests."""

    def __init__(self) -> None:
        self.temp_ok = False
        self.co2_demand = False
        self.carb_s = 0.0

    def update(self, s: SyrupSensors, dt: float, carbonating: bool = False) -> None:
        if s.product_temp <= PLC_COOL_SETPOINT:
            self.temp_ok = True
        elif s.product_temp > PLC_COOL_SETPOINT + PLC_TEMP_HYST:
            self.temp_ok = False
        if s.co2_pressure < PLC_PRESS_SETPOINT - PLC_PRESS_HYST:
            self.co2_demand = True
        elif s.co2_pressure >= PLC_PRESS_SETPOINT:
            self.co2_demand = False
        # Carb_Timer: runs while the valve is NOT wanted, restarts whenever it is
        self.carb_s = self.carb_s + dt if carbonating and not self.co2_demand else 0.0

    def outputs(self, s: SyrupSensors) -> SyrupOutputs:
        return SyrupOutputs(
            agitator=True,
            cooling_valve=not self.temp_ok,
            co2_valve=self.co2_demand and self.temp_ok and s.co2_pressure < PLC_PRESS_MAX,
        )


def cool(rig: Rig, rules: PlcRules, limit_s: float = 120.0) -> float:
    """COOL step: valve on until Temp_OK. Returns the seconds it took."""
    t = 0.0
    rules.update(rig.room.sensors(), 0.0)
    while not rules.temp_ok and t < limit_s:
        rig.tick(rules.outputs(rig.room.sensors()))
        t += TICK
        rules.update(rig.room.sensors(), TICK)
    return t


def carbonate(rig: Rig, rules: PlcRules, limit_s: float = 120.0) -> tuple[float, int]:
    """CARBONATE step until Carb_Timer is done. Returns (seconds, number of CO2 valve openings)."""
    t, openings, was_open = 0.0, 0, False
    while rules.carb_s < PLC_CARB_TIME_S and t < limit_s:
        out = rules.outputs(rig.room.sensors())
        openings += out.co2_valve and not was_open
        was_open = out.co2_valve
        rig.tick(out)
        t += TICK
        rules.update(rig.room.sensors(), TICK, carbonating=True)
    return t, openings


# --- Temperature ------------------------------------------------------------------
def test_cooling_valve_pulls_toward_the_coolant_but_never_below():
    rig = Rig(mix_l=300, temp_c=15.0)
    temps = []
    for _ in range(1200):  # 120 s
        rig.tick(SyrupOutputs(cooling_valve=True))
        temps.append(rig.room.temp_c)
    assert temps == sorted(temps, reverse=True)  # only falls
    assert rig.room.coolant_c < min(temps) < 1.0  # settles just above the coolant (room heat leaks in)


def test_without_cooling_the_product_warms_toward_the_room():
    rig = Rig(mix_l=300, temp_c=4.0)
    rig.run(60, OFF)
    assert 4.0 < rig.room.temp_c < config.ROOM_C


def test_a_late_tick_gives_the_same_temperature_as_three_short_ones():
    late, steps = Rig(mix_l=300, temp_c=15.0), Rig(mix_l=300, temp_c=15.0)
    late.tick(SyrupOutputs(cooling_valve=True), dt=0.3)
    for _ in range(3):
        steps.tick(SyrupOutputs(cooling_valve=True))
    assert late.room.temp_c == pytest.approx(steps.room.temp_c, abs=1e-9)  # exact solution of the lag


def test_water_and_syrup_mix_by_volume():
    room = SyrupRoom(mix_l=500, temp_c=config.WATER_TEMP_C)
    room._into_mix(90, config.SYRUP_TEMP_C, from_mains=False)
    assert room.temp_c == pytest.approx((500 * 15 + 90 * 20) / 590)  # 15.76 degC
    residue = SyrupRoom(mix_l=5, temp_c=4.0)  # the product left after a transfer
    residue._into_mix(500, config.WATER_TEMP_C, from_mains=True)
    assert residue.temp_c == pytest.approx((5 * 4 + 500 * 15) / 505)


def test_dosing_through_the_pumps_gives_about_the_same_mix_temperature():
    rig = Rig(syrup_l=600, mix_l=0)
    dose(rig, "water_pump", PLC_WATER_BATCH)
    dose(rig, "syrup_pump", PLC_DOSE_TARGET)
    assert rig.room.temp_c == pytest.approx(15.76, abs=0.3)  # room heat warms it a little while dosing


def test_an_empty_tank_has_no_temperature_to_change():
    rig = Rig(mix_l=0, temp_c=15.0)
    rig.run(60, SyrupOutputs(cooling_valve=True))
    assert rig.room.temp_c == 15.0


def test_sensor_resolution_is_a_tenth_of_a_degree():
    room = SyrupRoom(mix_l=300, temp_c=4.04)
    assert room.sensors().product_temp == 40  # reads 4.0 degC: the PLC's Temp_OK sets here


# --- Pressure ---------------------------------------------------------------------
def test_co2_valve_raises_the_pressure():
    rig = Rig(mix_l=300)
    rig.run(1, SyrupOutputs(co2_valve=True))
    assert rig.room.pressure_bar == pytest.approx(config.CO2_FILL_BAR_S, abs=0.02)
    assert rig.room.sensors().co2_pressure == pytest.approx(50, abs=2)


def test_unsaturated_liquid_takes_up_co2_and_the_pressure_falls():
    stirred, still = Rig(mix_l=300, pressure_bar=3.5), Rig(mix_l=300, pressure_bar=3.5)
    stirred.run(5, ON_AGITATOR)
    still.run(5, OFF)
    fall_stirred = 3.5 - stirred.room.pressure_bar
    fall_still = 3.5 - still.room.pressure_bar
    assert fall_stirred > 0.3  # about 0.45 bar
    assert fall_stirred > 5 * fall_still  # without the agitator it is about 10x slower


def test_saturated_liquid_holds_the_pressure():
    rig = Rig(mix_l=300, pressure_bar=3.5, dissolved_bar=3.5)
    rig.run(30, ON_AGITATOR)
    assert rig.room.pressure_bar == pytest.approx(3.5, abs=1e-9)


def test_co2_is_conserved_while_dissolving():
    """P + G*D stays constant with the valve closed and no vent: no CO2 appears or disappears."""
    rig = Rig(mix_l=300, pressure_bar=3.5, dissolved_bar=0.5)
    total = rig.room.pressure_bar + config.HEADSPACE_GAIN * rig.room.dissolved_bar
    rig.run(60, ON_AGITATOR)
    assert rig.room.pressure_bar + config.HEADSPACE_GAIN * rig.room.dissolved_bar == pytest.approx(total, abs=1e-9)
    assert rig.room.pressure_bar == pytest.approx(rig.room.dissolved_bar, abs=0.01)  # in balance after 60 s


def test_fresh_water_dilutes_the_dissolved_co2():
    room = SyrupRoom(mix_l=300, dissolved_bar=3.0)
    room._into_mix(300, config.WATER_TEMP_C, from_mains=True)
    assert room.dissolved_bar == pytest.approx(1.5)


def test_a_late_tick_gives_nearly_the_same_pressure_as_three_short_ones():
    out = SyrupOutputs(co2_valve=True, agitator=True)
    late, steps = Rig(mix_l=300, pressure_bar=3.0), Rig(mix_l=300, pressure_bar=3.0)
    late.tick(out, dt=0.3)
    for _ in range(3):
        steps.tick(out)
    assert late.room.pressure_bar == pytest.approx(steps.room.pressure_bar, abs=1e-3)


def test_relief_valve_limits_the_pressure_and_warns_once_per_period():
    rig = Rig(mix_l=300, pressure_bar=7.9)
    rig.run(5, SyrupOutputs(co2_valve=True))
    assert rig.room.pressure_bar == config.PRESS_RELIEF_BAR
    assert len([w for w in rig.warnings if "relief" in w]) == 1
    rig.run(2, OFF)  # pressure drops a little below the limit: period over
    rig.run(5, SyrupOutputs(co2_valve=True))
    assert len([w for w in rig.warnings if "relief" in w]) == 2


def test_transfer_vents_the_headspace_and_an_empty_tank_holds_no_co2():
    rig = Rig(mix_l=590, buffer_l=500, pressure_bar=3.5)
    rig.run(23.4, ON_TRANSFER)
    assert rig.room.pressure_bar < 0.1  # the next batch must not start under pressure
    low = Rig(mix_l=3, dissolved_bar=2.0)
    low.tick(OFF)
    assert low.room.dissolved_bar == 0.0


def test_pressure_sensor_resolution_is_a_hundredth_of_a_bar():
    assert SyrupRoom(mix_l=300, pressure_bar=3.4949).sensors().co2_pressure == 349


def test_a_stuck_open_co2_valve_reaches_the_plc_overpressure_limit():
    rig = Rig(mix_l=590, temp_c=4.0)
    t = 0.0
    while rig.room.sensors().co2_pressure < PLC_PRESS_MAX and t < 60:
        rig.tick(SyrupOutputs(co2_valve=True, agitator=True))  # forced open, whatever the PLC wants
        t += TICK
    assert t < 40  # Alm_OverPressure can happen long before the relief valve


# --- Fit with the PLC: its rules driving the physics ---------------------------------
def test_cooling_reaches_temp_ok_well_inside_the_cool_timeout():
    rig = Rig(syrup_l=600, mix_l=0)
    dose(rig, "water_pump", PLC_WATER_BATCH)
    dose(rig, "syrup_pump", PLC_DOSE_TARGET)
    rig.run(20, ON_AGITATOR)  # MIX step: the room warms the product a little meanwhile
    seconds = cool(rig, PlcRules())
    print(f"COOL took {seconds:.1f} s")
    assert seconds * 1.8 <= PLC_COOL_TIMEOUT_S


def test_the_thermostat_does_not_chatter():
    rig = Rig(mix_l=590, temp_c=4.0)
    rules = PlcRules()
    openings, was_open = 0, False
    for _ in range(1200):  # 120 s with the agitator on and the cooling held, as in CARBONATE
        s = rig.room.sensors()
        rules.update(s, TICK)
        out = SyrupOutputs(agitator=True, cooling_valve=not rules.temp_ok)
        openings += out.cooling_valve and not was_open
        was_open = out.cooling_valve
        rig.tick(out)
    assert 2 <= openings <= 8  # the 0.5 degC band is real: a few cycles per minute, not a flutter


def test_carbonation_cycles_the_valve_then_holds_so_carb_timer_can_finish():
    rig = Rig(mix_l=590, temp_c=4.0)
    rules = PlcRules()
    rules.temp_ok = True
    seconds, openings = carbonate(rig, rules)
    print(f"CARBONATE took {seconds:.1f} s with {openings} valve openings")
    assert PLC_CARB_TIME_S < seconds <= 60
    assert openings >= 2  # the pressure fell after the first fill: not saturated yet


def test_a_whole_batch_leaves_the_tank_unpressurised_and_in_the_buffer():
    rig = Rig(syrup_l=600, mix_l=0, buffer_l=500)
    rules = PlcRules()
    dose(rig, "water_pump", PLC_WATER_BATCH)
    dose(rig, "syrup_pump", PLC_DOSE_TARGET)
    rig.run(20, ON_AGITATOR)
    cool(rig, rules)
    carbonate(rig, rules)
    batch = rig.room.mix_l
    while rig.room.sensors().mix_level > PLC_MIX_EMPTY:
        rig.tick(ON_TRANSFER)
    assert rig.room.pressure_bar < 0.1
    assert rig.room.buffer_l == pytest.approx(500 + batch - rig.room.mix_l)
    assert rig.warnings == []
