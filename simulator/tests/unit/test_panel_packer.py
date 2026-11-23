"""ui/app.py with the real packer: the fourth tab driven by Textual's test pilot (no PLC).  uv run pytest

The PLC ports have no server: the loop keeps running, the packer PLC "does not answer" (outputs OFF), which is
enough to check that every packer control only puts a command on the queue and that the loop applies it.
Widgets in a hidden tab can be read but not clicked, so each test opens the tab with key 4.
"""

from textual.widgets import Input, Select, Static, Switch, TabbedContent

from simulator import config
from simulator.bottle import Bottle
from simulator.machines.packer import Pack
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc, PackerPlc
from simulator.ui.app import SimulatorApp, scenario_options

from test_panel import SIZE, run, texts
from test_plcs_link import free_port


def make_app(tmp_path, with_packer: bool = True) -> SimulatorApp:
    return SimulatorApp(Plant(with_labeler=True, with_packer=with_packer), FillerPlc(port=free_port()),
                        tmp_path / "panel.log", labeler_plc=LabelerPlc(port=free_port()),
                        packer_plc=PackerPlc(port=free_port()) if with_packer else None)


async def open_packer_tab(app, pilot) -> None:
    await pilot.press("4")
    await pilot.pause(0.1)
    assert app.query_one("#tabs", TabbedContent).active == "tab-packer"


def test_key_4_opens_the_packer_tab_and_a_switch_injects_a_fault(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_packer_tab(app, pilot)
            await pilot.click("#sw-pk-bar")
            await pilot.pause(0.3)  # a few ticks + one redraw
            assert app.plant.packer.wrapper.bar_stuck is True
            assert "command: wrap_bar_stuck = True" in texts(app)
            world = str(app.query_one("#packer-world", Static).render())
            assert "Wrap" in world and "BAR STUCK" in world and "not answering" in world
            await pilot.press("q")

    run(test)


def test_value_rows_and_the_hand_action_buttons(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_packer_tab(app, pilot)
            pk = app.plant.packer
            app.query_one("#in-film", Input).value = "1.3"
            await pilot.click("#bt-film")
            app.query_one("#in-sheet", Input).value = "800"
            await pilot.click("#bt-sheet")
            await pilot.pause(0.3)
            assert pk.wrapper.film.left_mm == 1300.0 and pk.wrapper.film.sheet_mm == 800.0 and pk.wrapper.film.cut
            pk.oven.packs.append(Pack(4, [Bottle(1)]))  # a pack caught in the oven
            await pilot.click("#bt-drop-sheet")
            await pilot.click("#bt-clear-oven")
            await pilot.pause(0.3)
            assert pk.wrapper.film.sheet_mm == 0 and pk.oven.packs == [] and [p.id for p in pk.oven.removed] == [4]
            assert "command: clear_oven = True" in texts(app)
            await pilot.press("q")

    run(test)


def test_packer_switches_follow_the_plant_without_sending_commands(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.plant.packer.oven.blower_failed = True  # e.g. the blower_failed scenario
            app.plant.palletizer.running = False
            app.plant.packer_heartbeat.enabled = False
            await pilot.pause(0.4)  # one redraw; the tab is hidden, the sync still works
            assert app.query_one("#sw-pk-blower", Switch).value is True
            assert app.query_one("#sw-pk-palletizer", Switch).value is False
            assert app.query_one("#sw-pk-hb", Switch).value is False
            assert not any(t.startswith(("command: blower_failed", "command: palletizer_running",
                                         "command: packer_heartbeat")) for t in texts(app))
            assert not app.query("#sw-packer")  # the stand-in's switch is gone with the real packer
            await pilot.press("q")

    run(test)


def test_a_packer_scenario_starts_from_the_shared_row(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            def offered() -> set[str]:
                return {value for _, value in app.query_one("#in-scenario", Select)._options if isinstance(value, str)}

            await open_packer_tab(app, pilot)
            assert "film_torn" in offered() and "web_torn" not in offered()
            app.query_one("#in-scenario", Select).value = "film_empty"
            await pilot.click("#bt-scenario")
            await pilot.pause(0.3)
            assert app.slot.runner is not None and app.slot.runner.scenario.name == "film_empty"
            assert "film_empty (packer): step 1/" in str(app.query_one("#scenario-status", Static).render())
            assert app.plant.packer.wrapper.film.left_mm == config.FILM_ROLL_MM  # step 1 waits for the PLC first
            await pilot.press("q")

    run(test)


def test_without_the_packer_key_4_only_notifies(tmp_path):
    async def test():
        app = make_app(tmp_path, with_packer=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4")
            await pilot.pause(0.1)
            assert app.query_one("#tabs", TabbedContent).active == "tab-filler" and not app.query("#packer-world")
            assert app.query("#sw-packer")  # the stand-in's switch on the labeler tab
            await pilot.press("q")

    run(test)


def test_scenario_options_follow_the_stand_ins():
    with_packer = {name for _, name in scenario_options("labeler", True, True, True)}
    standin = {name for _, name in scenario_options("labeler", True, True, False)}
    assert "packer_stopped" in standin and "packer_stopped" not in with_packer  # it stops the stand-in
    assert not scenario_options("packer", True, True, False)  # packer scenarios need the real packer
    assert {name for _, name in scenario_options("packer", True, True, True)} >= {"film_torn", "palletizer_stopped"}
