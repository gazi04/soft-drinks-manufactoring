"""ui/app.py with the syrup room: the second tab driven by Textual's test pilot (no PLC).  uv run pytest

Both PLC ports have no server: the loop keeps running, the syrup PLC "does not answer" (outputs OFF),
which is enough to check that every syrup control only puts a command on the queue and that the loop
applies it. Widgets in a hidden tab can be read but not clicked, so each test opens the tab with key 2.
"""

from textual.widgets import Input, Select, Static, Switch, TabbedContent

from simulator import config
from simulator.plant import Plant
from simulator.plcs import FillerPlc, SyrupPlc
from simulator.ui.app import SimulatorApp

from test_panel import SIZE, run, texts
from test_plcs_link import free_port


def make_app(tmp_path) -> SimulatorApp:
    return SimulatorApp(Plant(with_syrup=True), FillerPlc(port=free_port()), tmp_path / "panel.log",
                        syrup_plc=SyrupPlc(port=free_port()))


async def open_syrup_tab(app, pilot) -> None:
    await pilot.press("2")
    await pilot.pause(0.1)
    assert app.query_one("#tabs", TabbedContent).active == "tab-syrup"


def test_key_2_opens_the_syrup_tab_and_a_switch_injects_a_fault(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_syrup_tab(app, pilot)
            await pilot.click("#sw-chiller")
            await pilot.pause(0.3)  # a few ticks
            assert app.plant.syrup.coolant_c == config.COOLANT_FAILED_C
            assert "command: chiller_failed = True" in texts(app)
            world = str(app.query_one("#syrup-world", Static).render())
            assert "Mixing tank" in world and "CHILLER FAILED" in world and "not answering" in world
            await pilot.press("1")
            await pilot.pause(0.1)
            assert app.query_one("#tabs", TabbedContent).active == "tab-filler"
            await pilot.press("q")

    run(test)


def test_value_rows_and_action_buttons(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_syrup_tab(app, pilot)
            room = app.plant.syrup
            app.query_one("#in-deliver", Input).value = "100"
            await pilot.click("#bt-deliver")
            room.pressure_bar = 3.0
            await pilot.click("#bt-vent")
            await pilot.pause(0.3)
            assert room.syrup_l == config.SYRUP_START_L + 100
            assert room.pressure_bar == 0
            assert "command: vent_tank = True" in texts(app)
            await pilot.press("q")

    run(test)


def test_syrup_switches_follow_the_plant_without_sending_commands(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.plant.syrup.co2_valve_stuck = True  # e.g. the over_pressure scenario
            app.plant.syrup_heartbeat.enabled = False
            await pilot.pause(0.4)  # one redraw; the tab is hidden, the sync still works
            assert app.query_one("#sw-co2", Switch).value is True
            assert app.query_one("#sw-syrup-hb", Switch).value is False
            assert not any(t.startswith(("command: co2_valve_stuck", "command: syrup_heartbeat")) for t in texts(app))
            await pilot.press("q")

    run(test)


def test_a_syrup_scenario_starts_from_the_shared_row(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_syrup_tab(app, pilot)  # the row sits under the tabs: reachable from every tab
            app.query_one("#in-scenario", Select).value = "buffer_full"
            await pilot.click("#bt-scenario")
            await pilot.pause(0.3)
            assert app.slot.runner is not None and app.slot.runner.scenario.name == "buffer_full"
            assert "buffer_full (syrup): step 1/" in str(app.query_one("#scenario-status", Static).render())  # waits: PLC not running
            await pilot.press("q")

    run(test)
