"""scenarios.py: steps, conditions and the built-in scenarios (no PLC).  uv run pytest

Named test_scenario_steps.py, not test_scenarios.py: tests/test_scenarios.py is the
script that runs the scenarios against the real PLC.
"""

import pytest

from simulator.commands import Command
from simulator.plant import Plant
from simulator.plcs import PlcStatus
from simulator.scenarios import (
    SCENARIOS, Condition, Scenario, ScenarioRunner, Step, alarm, fill_level_at_least, in_step, plc_running,
)


def status(step=0, state=0, comm=False) -> PlcStatus:
    return PlcStatus(state, step, 0, False, False, False, comm, 500, 2000, 1500)


def run(runner: ScenarioRunner, plant: Plant, seconds: float, st: PlcStatus | None = None) -> list[Command]:
    """Tick the runner for `seconds` of simulation time (0.1 s ticks)."""
    fired = []
    for _ in range(round(seconds / 0.1)):
        plant.now += 0.1
        commands, _events = runner.due(plant, st)
        fired += commands
    return fired


def test_timeline_step_fires_after_its_seconds_counted_from_the_previous_step():
    sc = Scenario("t", "", (Step(1.0, Command("heartbeat", False)), Step(2.0, Command("heartbeat", True))))
    plant, runner = Plant(), ScenarioRunner(sc)
    runner.due(plant, None)  # first tick starts the timeline
    assert run(runner, plant, 0.9) == []
    assert run(runner, plant, 0.1) == [Command("heartbeat", False)]
    assert run(runner, plant, 1.9) == []  # 2 s after the FIRST step, not after the start
    assert run(runner, plant, 0.1) == [Command("heartbeat", True)]
    assert runner.done


def test_condition_step_waits_and_later_steps_wait_for_it():
    sc = Scenario("c", "", (Step(plc_running()), Step(0, Command("index_jam", True))))
    plant, runner = Plant(), ScenarioRunner(sc)
    assert run(runner, plant, 5.0, status(step=0)) == []  # PLC in IDLE: the timeline does not start
    assert run(runner, plant, 0.1, status(step=10)) == [Command("index_jam", True)]  # both fire in one tick
    assert runner.done


def test_each_fired_step_is_logged():
    sc = Scenario("log", "", (Step(0, Command("e_stop", True), "press Start"), Step(in_step(20))))
    plant, runner = Plant(), ScenarioRunner(sc)
    _, events = runner.due(plant, status(step=20))
    assert [e.text for e in events] == [
        "scenario log 1/2 after 0 s: e_stop = True (press Start)",
        "scenario log 2/2 when step 20: wait done",
    ]


def test_conditions():
    plant = Plant()
    assert not plc_running()(plant, None)  # no status read yet
    assert alarm("Alm_CommLost")(plant, status(comm=True))
    assert not alarm("Alm_CommLost")(plant, status())
    assert not fill_level_at_least(200)(plant, None)  # no bottle at S4
    assert isinstance(in_step(20), Condition) and in_step(20).name == "step 20"


def test_a_bad_command_in_a_scenario_fails_when_it_is_defined():
    with pytest.raises(ValueError):
        Scenario("bad", "", (Step(0, Command("index_jamm", True)),))


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_builtin_scenarios_wait_for_the_plc_first(name):
    steps = SCENARIOS[name].steps
    first_condition = next(s for s in steps if not isinstance(s.wait, (int, float)))
    assert first_condition.wait.name == "PLC started"
    # only "set up" commands may come before it (gaps: supply rate 0 before bottles queue up)
    before = steps[: steps.index(first_condition)]
    assert all(s.command is not None and s.command.name == "supply_rate" for s in before)


def test_scenario_slot_is_empty_until_started_then_delegates():
    from simulator.scenarios import ScenarioSlot

    plant, slot = Plant(), ScenarioSlot()
    assert slot.due(plant, status(step=10)) == ([], [])
    slot.start(Scenario("s", "", (Step(0, Command("index_jam", True)),)))
    commands, events = slot.due(plant, status(step=10))
    assert commands == [Command("index_jam", True)] and len(events) == 1
    assert slot.runner.done


# === Syrup room scenarios (stage 6) ===================================================
from simulator.plcs import SyrupStatus  # noqa: E402
from simulator.scenarios import no_product, product_available  # noqa: E402

SYRUP_SCENARIOS = [n for n, s in SCENARIOS.items() if s.target == "syrup"]


def syrup_status(step=0, state=0, **alarms) -> SyrupStatus:
    flags = dict(alm_overflow=False, alm_over_pressure=False, alm_cool_timeout=False, alm_comm_lost=False,
                 alm_low_syrup=False, alm_dose_timeout=False) | alarms
    return SyrupStatus(state, step, 0, **flags)


def test_the_syrup_scenarios_exist():
    assert set(SYRUP_SCENARIOS) == {
        "low_syrup", "cooling_failure", "over_pressure", "pump_failure", "level_sensor_stuck",
        "buffer_full", "buffer_starved",
    }


def test_a_syrup_scenario_sees_the_syrup_status_not_the_filler_status():
    sc = Scenario("s", "", (Step(in_step(40), Command("chiller_failed", True)),), target="syrup")
    plant = Plant(with_syrup=True)
    runner = ScenarioRunner(sc)
    assert runner.due(plant, status(step=40), None)[0] == []  # the filler is in a step 40: not the syrup PLC
    assert runner.due(plant, None, syrup_status(step=40))[0] == [Command("chiller_failed", True)]


def test_a_filler_scenario_still_sees_the_filler_status():
    sc = Scenario("f", "", (Step(in_step(20), Command("index_jam", True)),))
    runner = ScenarioRunner(sc)
    assert runner.due(Plant(), status(step=20), syrup_status(step=0))[0] == [Command("index_jam", True)]


def test_scenario_slot_passes_the_syrup_status_on():
    from simulator.scenarios import ScenarioSlot

    slot = ScenarioSlot()
    slot.start(Scenario("s", "", (Step(in_step(10), Command("chiller_failed", True)),), target="syrup"))
    assert slot.due(Plant(with_syrup=True), None, syrup_status(step=10))[0] == [Command("chiller_failed", True)]


def test_syrup_commands_need_a_syrup_scenario_and_the_target_is_checked():
    with pytest.raises(ValueError):
        Scenario("bad", "", (Step(0, Command("chiller_failed", True)),))  # filler target
    with pytest.raises(ValueError):
        Scenario("bad", "", (), target="labeler")
    Scenario("ok", "", (Step(0, Command("product_available", False)),), target="syrup")  # shared command


@pytest.mark.parametrize("name", SYRUP_SCENARIOS)
def test_every_syrup_scenario_waits_for_the_syrup_plc_and_removes_its_fault(name):
    steps = SCENARIOS[name].steps
    assert steps[0].wait.name == "PLC started"
    names = [(s.command.name, s.command.value) for s in steps if s.command]
    for fault in ("chiller_failed", "co2_valve_stuck", "water_pump_failed", "mix_level_stuck"):
        if (fault, True) in names:
            assert (fault, False) in names  # the scenario ends with the cause removed


def test_product_conditions_look_at_the_wire_to_the_filler():
    plant = Plant(with_syrup=True)
    from simulator.plant import SyrupSignals

    plant.syrup_signals = SyrupSignals(product_available=True)  # what the syrup PLC said last tick
    assert product_available()(plant, None) and not no_product()(plant, None)
    plant.product_available = False  # pipe cut
    assert no_product()(plant, None)


def test_low_syrup_numbers_fit_the_plc_defaults():
    # batch 1 needs 90 L (WaterBatch 500 x 10 / 55); 600 - 450 = 150 leaves 60 < 90 for batch 2
    from simulator import config

    assert config.SYRUP_START_L - 450 - 90 < 90 <= config.SYRUP_START_L - 450


def test_buffer_full_numbers_block_without_an_overflow_alarm():
    """The buffer level set at MIX must stay <= BufferMax (else Alm_Overflow -> FAULT, found live
    2026-11-13), still leave no room for the batch ~80 s later even with the filler drawing, and the
    later draw must make room again."""
    plc_buffer_max, plc_dose_target, filler_draw_l = 1950, 590, 3.0 * 80  # 3 L/s is above the measured 2.2
    steps = SCENARIOS["buffer_full"].steps
    level = next(s.command.value for s in steps if s.command and s.command.name == "fill_buffer_to")
    drawn = next(s.command.value for s in steps if s.command and s.command.name == "draw_buffer")
    assert level <= plc_buffer_max  # no Alm_Overflow
    assert level - filler_draw_l + plc_dose_target > plc_buffer_max  # Blocked even with the filler running
    assert level - drawn + plc_dose_target <= plc_buffer_max  # TRANSFER after the draw (no filler: worst case)
