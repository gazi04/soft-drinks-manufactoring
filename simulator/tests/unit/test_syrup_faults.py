"""Syrup room physics, stage 6: injected faults and hand actions.  uv run pytest

The PLC is never told about a fault; these tests check only what the world does.
"""

import pytest

from simulator import config
from simulator.machines.syrup import SyrupOutputs, SyrupRoom
from test_syrup import OFF, ON_AGITATOR, ON_SYRUP, ON_WATER, Rig

CO2_ON = SyrupOutputs(co2_valve=True, agitator=True)
COOL_ON = SyrupOutputs(cooling_valve=True, agitator=True)


# --- Pumps ----------------------------------------------------------------------
def test_failed_water_pump_moves_nothing_and_gives_no_audit_warning():
    rig = Rig(mix_l=0)
    rig.room.water_pump_failed = True
    rig.run(10, ON_WATER)
    assert rig.room.mix_l == 0 and rig.room.water_in_l == 0 and rig.warnings == []
    rig.room.water_pump_failed = False  # repaired
    rig.run(1, ON_WATER)
    assert rig.room.mix_l == pytest.approx(config.WATER_FLOW_L_S)


def test_failed_syrup_pump_moves_nothing_even_with_an_empty_tank():
    rig = Rig(syrup_l=600, mix_l=0)
    rig.room.syrup_pump_failed = True
    rig.run(10, ON_SYRUP)
    assert rig.room.syrup_l == 600 and rig.room.mix_l == 0 and rig.warnings == []
    rig2 = Rig(syrup_l=0, mix_l=0)
    rig2.room.syrup_pump_failed = True
    rig2.run(2, ON_SYRUP)
    assert rig2.warnings == []  # a dead pump does not "run dry"


# --- Chiller --------------------------------------------------------------------
def test_failed_chiller_cannot_cool_but_a_working_one_can():
    good, bad = Rig(mix_l=590, temp_c=15.8), Rig(mix_l=590, temp_c=15.8)
    bad.room.coolant_c = config.COOLANT_FAILED_C
    good.run(60, COOL_ON)
    bad.run(60, COOL_ON)
    assert good.room.temp_c < 4.0
    assert bad.room.temp_c > 15.8  # warms toward the room, never below the start


# --- CO2 valve ------------------------------------------------------------------
def test_stuck_co2_valve_keeps_filling_when_the_plc_closes_it():
    rig = Rig(mix_l=590, temp_c=4.0)
    rig.room.co2_valve_stuck = True
    rig.run(30, ON_AGITATOR)  # PLC valve output OFF
    assert rig.room.pressure_bar > 6.0  # over the PLC's PressMax
    rig.run(60, ON_AGITATOR)
    assert rig.room.pressure_bar == pytest.approx(config.PRESS_RELIEF_BAR)  # the relief valve holds it
    assert any("relief" in w for w in rig.warnings)


def test_pressure_stays_down_without_the_fault():
    rig = Rig(mix_l=590, temp_c=4.0)
    rig.run(30, ON_AGITATOR)
    assert rig.room.pressure_bar == 0


def test_vent_headspace_keeps_the_dissolved_co2():
    rig = Rig(mix_l=590, temp_c=4.0)
    rig.room.co2_valve_stuck = True
    rig.run(40, ON_AGITATOR)
    dissolved = rig.room.dissolved_bar
    rig.room.co2_valve_stuck = False
    rig.room.vent_headspace()
    assert rig.room.pressure_bar == 0 and rig.room.dissolved_bar == dissolved > 0
    rig.run(30, ON_AGITATOR)  # the liquid gives some back, but far below the limit
    assert rig.room.pressure_bar < 6.0 * 0.5


# --- Level sensor ---------------------------------------------------------------
def test_frozen_level_sensor_reports_the_old_value_but_the_switch_sees_the_truth():
    rig = Rig(mix_l=100)
    rig.room.freeze_mix_level(True)
    rig.run(60, ON_WATER)  # 100 + 1200 L: over the brim
    sensors = rig.room.sensors()
    assert sensors.mix_level == 100
    assert sensors.mix_high_high is True
    rig.room.freeze_mix_level(False)
    assert rig.room.sensors().mix_level == round(rig.room.mix_l)


def test_water_reaches_the_high_high_switch_before_the_dose_timeout():
    rig = Rig(mix_l=5)
    rig.room.freeze_mix_level(True)
    t = 0.0
    while not rig.room.sensors().mix_high_high:
        rig.tick(ON_WATER)
        t += 0.1
        assert t < 60, "DoseTimeout would fire before Alm_Overflow"
    assert 45 < t < 55


# --- Hand actions + ledger ------------------------------------------------------
def test_hand_actions_return_the_litres_moved_and_clip():
    room = Rig(syrup_l=100, mix_l=300, buffer_l=1900).room
    assert room.fill_buffer(500) == pytest.approx(config.BUFFER_TANK_L - 1900)
    assert room.drain_syrup(250) == 100 and room.syrup_l == 0
    assert room.drain_mix_tank() == 300 and room.mix_l == 0
    for bad in (room.fill_buffer, room.drain_syrup):
        with pytest.raises(ValueError):
            bad(-1)


def test_ledger_conserves_litres_with_hand_actions():
    rig = Rig(syrup_l=600, mix_l=0, buffer_l=500)
    room = rig.room
    rig.run(20, ON_WATER)
    room.fill_buffer(300)
    room.drain_syrup(200)
    room.draw_buffer(100)
    room.deliver_syrup(50)
    room.drain_mix_tank()
    inside = room.syrup_l + room.mix_l + room.buffer_l + room.spilled_l
    assert inside == pytest.approx(
        room.start_l + room.water_in_l + room.delivered_l - room.drawn_l + room.manual_l, abs=1e-6)
    assert room.manual_l == pytest.approx(300 - 200 - 400)


def test_fill_buffer_to_tops_up_to_a_level_and_books_it_as_a_hand_action():
    room = SyrupRoom(buffer_l=700.0)
    assert room.fill_buffer_to(1900) == pytest.approx(1200)
    assert room.buffer_l == 1900 and room.manual_l == pytest.approx(1200)
    assert room.fill_buffer_to(1000) == 0 and room.buffer_l == 1900  # already above: nothing
    assert room.fill_buffer_to(5000) == pytest.approx(100)  # capped at the tank
    with pytest.raises(ValueError):
        room.fill_buffer_to(-1)
