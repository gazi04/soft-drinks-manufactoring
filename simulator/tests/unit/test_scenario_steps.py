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
