"""ui/app.py with the real labeler: the third tab driven by Textual's test pilot (no PLC).  uv run pytest

The PLC ports have no server: the loop keeps running, the labeler PLC "does not answer" (outputs OFF),
which is enough to check that every labeler control only puts a command on the queue and that the loop
applies it. Widgets in a hidden tab can be read but not clicked, so each test opens the tab with key 3.
"""

from textual.widgets import Input, Select, Static, Switch, TabbedContent

from simulator import config
from simulator.bottle import Bottle
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc
from simulator.ui.app import SimulatorApp

from test_panel import SIZE, run, texts
from test_plcs_link import free_port


def make_app(tmp_path) -> SimulatorApp:
    return SimulatorApp(Plant(with_labeler=True), FillerPlc(port=free_port()), tmp_path / "panel.log",
                        labeler_plc=LabelerPlc(port=free_port()))


async def open_labeler_tab(app, pilot) -> None:
    await pilot.press("3")
    await pilot.pause(0.1)
    assert app.query_one("#tabs", TabbedContent).active == "tab-labeler"


def test_key_3_opens_the_labeler_tab_and_a_switch_injects_a_fault(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_labeler_tab(app, pilot)
            await pilot.click("#sw-web-torn")
            await pilot.pause(0.3)  # a few ticks + one redraw
            assert app.plant.labeler.web.torn is True
            assert "command: web_torn = True" in texts(app)
            world = str(app.query_one("#labeler-world", Static).render())
            assert "Pocket" in world and "WEB TORN" in world and "not answering" in world
            await pilot.press("1")
            await pilot.pause(0.1)
            assert app.query_one("#tabs", TabbedContent).active == "tab-filler"
            await pilot.press("q")

    run(test)


def test_value_rows_and_the_clear_gate_button(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_labeler_tab(app, pilot)
            lab = app.plant.labeler
            app.query_one("#in-roll", Input).value = "5"
            await pilot.click("#bt-roll")
            app.query_one("#in-glue-level", Input).value = "2"
            await pilot.click("#bt-glue-level")
            await pilot.pause(0.3)
            assert lab.web.labels_left == 5 and lab.tank.glue_kg == 2.0
            app.query_one("#in-add-glue", Input).value = "1.5"
            await pilot.click("#bt-add-glue")
            lab.entering = Bottle(99)  # a bottle stuck between gate and pocket
            await pilot.click("#bt-clear-gate")
            await pilot.pause(0.3)
            assert lab.tank.glue_kg == 3.5
            assert lab.entering is None and [b.id for b in lab.removed] == [99]
            assert "command: clear_gate = True" in texts(app)
            await pilot.press("q")

    run(test)


def test_labeler_switches_follow_the_plant_without_sending_commands(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.plant.labeler.bell_stuck = True  # e.g. the bell_stuck scenario
            app.plant.packer.running = False
            app.plant.labeler_heartbeat.enabled = False
            await pilot.pause(0.4)  # one redraw; the tab is hidden, the sync still works
            assert app.query_one("#sw-bell", Switch).value is True
            assert app.query_one("#sw-packer", Switch).value is False
            assert app.query_one("#sw-labeler-hb", Switch).value is False
            assert not any(t.startswith(("command: bell_stuck", "command: packer_running", "command: labeler_heartbeat"))
                           for t in texts(app))
            await pilot.press("q")

    run(test)


def test_a_labeler_scenario_starts_from_the_shared_row(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_labeler_tab(app, pilot)  # the row sits under the tabs: reachable from every tab
            app.query_one("#in-scenario", Select).value = "roll_empty"
            await pilot.click("#bt-scenario")
            await pilot.pause(0.3)
            assert app.slot.runner is not None and app.slot.runner.scenario.name == "roll_empty"
            assert "roll_empty: step 1/" in str(app.query_one("#scenario-status", Static).render())  # waits: PLC not running
            assert app.plant.labeler.web.labels_left == config.ROLL_LABELS  # step 1 waits for the PLC first
            await pilot.press("q")

    run(test)
