"""ui/app.py: the control panel driven by Textual's test pilot (no PLC).  uv run pytest

The PLC port has no server: the loop keeps running with all outputs OFF (as in
test_loop.py), which is enough to check that every control only puts a command
on the queue and that the loop applies it.
"""

import asyncio

from textual.widgets import Input, Select, Switch

from simulator.plant import Plant
from simulator.plcs import FillerPlc
from simulator.ui.app import SimulatorApp

from test_plcs_link import free_port

SIZE = (140, 45)  # a normal terminal; the default 80x24 hides the lower controls


def make_app(tmp_path, scenario=None) -> SimulatorApp:
    return SimulatorApp(Plant(), FillerPlc(port=free_port()), tmp_path / "panel.log", scenario)


def texts(app: SimulatorApp) -> list[str]:
    return [e.text for e in app.events.recent(len(app.events))]


def run(test) -> None:
    asyncio.run(test())


def test_switch_puts_a_command_that_the_loop_applies(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.click("#sw-estop")
            await pilot.pause(0.3)  # a few ticks
            assert app.plant.e_stop_ok is False
            assert "command: e_stop = True" in texts(app)
            await pilot.press("q")

    run(test)


def test_value_buttons_and_invalid_input(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.query_one("#in-flow", Input).value = "40"
            await pilot.click("#bt-flow")
            await pilot.pause(0.3)
            assert app.plant.filler.fill_flow_ml_s == 40.0

            app.query_one("#in-add", Input).value = "-5"  # put() refuses negatives
            await pilot.click("#bt-add")
            await pilot.pause(0.1)
            assert len(app.queue) == 0 and app.plant.supply.pending == 0
            await pilot.press("q")

    run(test)


def test_switches_follow_the_plant_without_sending_commands(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.plant.labeler.running = False  # e.g. a scenario changed it
            await pilot.pause(0.4)  # one redraw
            assert app.query_one("#sw-labeler", Switch).value is False
            assert not any(t.startswith("command: labeler_running") for t in texts(app))
            await pilot.press("q")

    run(test)


def test_scenario_started_from_the_panel(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.query_one("#in-scenario", Select).value = "gaps"
            await pilot.click("#bt-scenario")
            await pilot.pause(0.3)
            assert app.slot.runner is not None and app.slot.runner.scenario.name == "gaps"
            assert app.plant.supply.per_min == 0  # step 1 (supply_rate 0) needs no PLC
            await pilot.press("q")

    run(test)


def test_log_filter_and_clean_quit(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("l")
            assert app.log_filter == "bottle"
            await pilot.press("q")
            await pilot.pause()
        assert app.return_value is not None and app.return_value.ticks > 0  # stats of the stopped loop
        assert app.events._file is None  # log file closed
        assert (tmp_path / "panel.log").read_text().count("not reachable") == 1

    run(test)
