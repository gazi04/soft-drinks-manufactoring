"""Blow moulder stage 6: faults in the physics, the commands that set them, the 13 scenarios.  uv run pytest

No PLC: the faults are checked on the machine with outputs written by hand (or the stage 3 test controller),
the scenarios only for their shape; tests/test_blowmoulder_scenarios.py runs them on the real PLC.
"""

import pytest
from test_blowmoulder import (BLOW, EXHAUST, LOAD, STRETCH, Preform, blown, closed_with_preform, plc_pressure, run, until,
                              with_preform_in_pocket)

from simulator import config
from simulator.commands import BLOWMOULDER_COMMANDS, COMMANDS, CommandQueue
from simulator.machines.blowmoulder import Blowmoulder, BlowmoulderOutputs
from simulator.plant import Plant
from simulator.plcs import BlowmoulderStatus
from simulator.scenarios import SCENARIOS, ScenarioRunner, uses_command

TICK = 0.1
BLOWMOULDER_COMMANDS_TESTED = {
    "load_preforms", "preform_stuck", "lamps_failed", "lamps_stuck_on", "arm_stuck", "drop_preform", "mould_stuck",
    "high_blow_bar", "exhaust_blocked", "pressure_wire_break", "ejector_stuck", "blowmoulder_heartbeat",
}
SCENARIO_NAMES = ["hopper_empty", "preform_stuck", "lamps_failed", "lamps_stuck_on", "arm_stuck", "preform_dropped",
                  "mould_stuck", "air_low", "exhaust_blocked", "regulator_failed", "sensor_wire_break",
                  "ejector_stuck", "c1_backed_up"]


def applied(name, value, plant: Plant | None = None) -> Plant:
    plant = plant or Plant(with_blowmoulder=True)
    q = CommandQueue()
    q.put(name, value)
    q.apply(plant)
    return plant


# --- Faults in the physics ---------------------------------------------------------------------
def test_stuck_preform_never_reaches_the_pocket():
    bm = Blowmoulder()
    run(bm, BlowmoulderOutputs(hopper_feed=True), config.CHUTE_S)
    bm.preform_stuck = True
    run(bm, LOAD, 3.0)
    assert bm.preform_at_loader and not bm.preform_in_oven and bm.loader_out  # -> Alm_HandlingJam
    bm.preform_stuck = False
    run(bm, LOAD, TICK)
    assert bm.preform_in_oven


def test_failed_lamps_give_no_heat_and_win_over_stuck_on():
    bm = with_preform_in_pocket()
    bm.lamps_failed = bm.lamps_stuck_on = True
    run(bm, BlowmoulderOutputs(lamp_power=100), 15.0)
    assert bm.oven_temp == round(config.ROOM_C * 10)  # -> Alm_OvenHeatTimeout


def test_stuck_lamps_heat_whatever_the_plc_writes():
    bm = with_preform_in_pocket()
    bm.lamps_stuck_on = True
    run(bm, BlowmoulderOutputs(lamp_power=0), 4.5)
    assert bm.oven_temp >= 1300  # OvenMax: the PLC's LampPower 0 changes nothing


@pytest.mark.parametrize("attr, out, sensor", [
    ("arm_stuck", BlowmoulderOutputs(arm_to_mould=True), "arm_at_oven"),
    ("mould_stuck", BlowmoulderOutputs(mould_close=True), "mould_open"),
    ("ejector_stuck", BlowmoulderOutputs(ejector=True), "ejector_home"),
])
def test_a_stuck_part_stays_where_it_is(attr, out, sensor):
    bm = Blowmoulder()
    setattr(bm, attr, True)
    run(bm, out, 3.0)
    assert getattr(bm, sensor)


def test_stuck_arm_leaves_the_preform_in_the_pocket():
    bm = with_preform_in_pocket(103.0)
    bm.arm_stuck = True
    until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed)
    run(bm, BlowmoulderOutputs(gripper=True, arm_to_mould=True), 3.0)
    assert bm.preform_in_oven and bm.held is None


def test_dropped_preform_leaves_the_gripper_and_never_reaches_the_mould():
    bm = with_preform_in_pocket()
    until(bm, BlowmoulderOutputs(gripper=True), lambda: bm.gripper_closed)
    run(bm, BlowmoulderOutputs(gripper=True, arm_to_mould=True), 0.2)
    bm.drop_preform()
    run(bm, BlowmoulderOutputs(arm_to_mould=True), 1.0)
    assert [p.id for p in bm.dropped] == [1] and not bm.bottle_in_mould  # -> Alm_HandlingJam in RELEASE
    bm.drop_preform()  # nothing held: nothing happens
    assert len(bm.dropped) == 1


@pytest.mark.parametrize("bar, reached", [(20.0, False), (45.0, True)])
def test_the_regulator_sets_the_high_blow_pressure(bar, reached):
    bm = closed_with_preform()
    bm.high_blow_bar = bar
    until(bm, STRETCH, lambda: bm.rod_down)
    run(bm, BLOW, 2.0)
    assert (plc_pressure(bm.blow_pressure_raw) >= 350) == reached  # 20 bar: Alm_BlowFault
    if reached:
        assert plc_pressure(bm.blow_pressure_raw) >= 400  # 45 bar passes MaxPressure 40: Alm_OverPressure


def test_blocked_exhaust_keeps_the_pressure():
    bm = blown()
    bm.exhaust_blocked = True
    run(bm, EXHAUST, 3.0)
    assert bm.pressure_bar > 38.0  # -> Alm_ExhaustFault, the mould stays closed
    bm.exhaust_blocked = False
    run(bm, EXHAUST, 1.0)
    assert plc_pressure(bm.blow_pressure_raw) < 5


def test_wire_break_reads_below_the_live_zero():
    bm = blown()
    bm.pressure_wire_break = True
    assert bm.blow_pressure_raw == config.WIRE_BREAK_RAW == -2000 and bm.pressure_bar > 30  # the air is still there
    bm.pressure_wire_break = False
    assert bm.blow_pressure_raw > 20000


def test_hopper_load_sets_the_count():
    bm = Blowmoulder(hopper_preforms=0)
    assert bm.hopper_empty
    bm.hopper.load(5)
    assert bm.hopper.count == 5 and not bm.hopper_empty and bm.preform_low


def test_blocked_filler_door_lets_nothing_pass():
    plant = Plant()
    plant.filler.door_blocked = True
    plant.c1.slots[-1] = plant.factory.new()
    from simulator.machines.filler import FillerOutputs
    for _ in range(10):
        plant.step(FillerOutputs(conveyor1=True, door_motor=True), TICK)
    assert plant.c1.exit_bottle is not None  # still waiting at the door


# --- Commands -------------------------------------------------------------------------------------
def test_blowmoulder_commands_are_marked_and_complete():
    assert BLOWMOULDER_COMMANDS == BLOWMOULDER_COMMANDS_TESTED
    assert not COMMANDS["filler_door_blocked"].blowmoulder  # a filler fault: also with the supply


@pytest.mark.parametrize("name, value, read", [
    ("load_preforms", 3, lambda p: p.blowmoulder.hopper.count == 3),
    ("preform_stuck", True, lambda p: p.blowmoulder.preform_stuck),
    ("lamps_failed", True, lambda p: p.blowmoulder.lamps_failed),
    ("lamps_stuck_on", True, lambda p: p.blowmoulder.lamps_stuck_on),
    ("arm_stuck", True, lambda p: p.blowmoulder.arm_stuck),
    ("mould_stuck", True, lambda p: p.blowmoulder.mould_stuck),
    ("high_blow_bar", 20.0, lambda p: p.blowmoulder.high_blow_bar == 20.0),
    ("exhaust_blocked", True, lambda p: p.blowmoulder.exhaust_blocked),
    ("pressure_wire_break", True, lambda p: p.blowmoulder.pressure_wire_break),
    ("ejector_stuck", True, lambda p: p.blowmoulder.ejector_stuck),
    ("blowmoulder_heartbeat", False, lambda p: not p.blowmoulder_heartbeat.enabled),
    ("filler_door_blocked", True, lambda p: p.filler.door_blocked),
])
def test_each_command_sets_its_fault(name, value, read):
    assert read(applied(name, value))


def test_drop_preform_command_is_an_action():
    plant = Plant(with_blowmoulder=True)
    plant.blowmoulder.held = Preform(4)
    applied("drop_preform", False, plant)
    assert plant.blowmoulder.held is not None  # False does nothing
    applied("drop_preform", True, plant)
    assert plant.blowmoulder.held is None and plant.blowmoulder.dropped[0].id == 4


def test_heartbeat_command_freezes_only_the_blow_moulders():
    plant = applied("blowmoulder_heartbeat", False)
    assert plant.heartbeat.enabled and plant.packer_heartbeat.enabled and not plant.blowmoulder_heartbeat.enabled


def test_blowmoulder_commands_are_ignored_without_the_blow_moulder():
    plant, q = Plant(), CommandQueue()  # the bottle supply
    q.put("arm_stuck", True)
    events = q.apply(plant)
    assert len(events) == 1 and events[0].is_warning and "--no-blowmoulder" in events[0].text


# --- Scenarios ------------------------------------------------------------------------------------
def test_thirteen_blowmoulder_scenarios():
    names = [s.name for s in SCENARIOS.values() if s.target == "blowmoulder"]
    assert names == SCENARIO_NAMES
    assert "c1_stopped" not in SCENARIOS  # a stopped C1 is issue I18, not a backup: c1_backed_up instead
    assert uses_command(SCENARIOS["c1_backed_up"], "filler_door_blocked")


def test_a_blowmoulder_command_in_another_target_is_refused():
    from simulator.scenarios import Scenario, Step, plc_running
    from simulator.commands import Command

    with pytest.raises(ValueError, match="needs target='blowmoulder'"):
        Scenario("x", "x", (Step(plc_running(), Command("arm_stuck", True)),))


def bm_status(step=0, mould_step=0, **alarms) -> BlowmoulderStatus:
    names = ("alm_no_preforms", "alm_handling_jam", "alm_outfeed_blocked", "alm_comm_lost", "alm_mould_fault",
             "alm_oven_over_temp", "alm_oven_heat_timeout", "alm_blow_fault", "alm_exhaust_fault",
             "alm_over_pressure", "alm_pressure_sensor")
    return BlowmoulderStatus(1, step, mould_step, 0, 0, 0, 0, **({n: False for n in names} | alarms))


def test_the_runner_sees_the_blow_moulder_status():
    runner = ScenarioRunner(SCENARIOS["preform_dropped"])
    plant = Plant(with_blowmoulder=True)
    filler_busy = None  # the filler's status must not count
    assert runner.due(plant, filler_busy, blowmoulder_status=bm_status(10, 10))[0] == []  # started, not yet SWING
    commands, _ = runner.due(plant, filler_busy, blowmoulder_status=bm_status(25, 22))
    assert [c.name for c in commands] == ["drop_preform"]
    commands, _ = runner.due(plant, filler_busy, blowmoulder_status=bm_status(10, 900, alm_handling_jam=True))
    assert commands == [] and not runner.done  # the last step waits 2 s


def test_cli_refuses_a_blowmoulder_scenario_without_the_blow_moulder(monkeypatch):
    import simulator

    monkeypatch.setattr("sys.argv", ["simulator", "--headless", "--no-blowmoulder", "--scenario", "arm_stuck"])
    with pytest.raises(SystemExit, match="needs the real blow moulder"):
        simulator.main()


def test_c1_backs_up_to_c1_full_with_the_door_blocked():
    """The plant side of c1_backed_up, with the stage 3 test controllers: C1 runs, nothing passes the door."""
    from test_plant_blowmoulder import Line

    line = Line()
    line.plant.filler.door_blocked = True
    line.run(120.0, until=lambda: line.bm.outfeed_full)
    assert line.bm.outfeed_full and line.bw.sensors.c1_full and not line.warnings()
    line.plant.filler.door_blocked = False
    ejected = line.plant.blowmoulder.ejected
    line.run(60.0)
    assert line.plant.blowmoulder.ejected > ejected and not line.warnings()
