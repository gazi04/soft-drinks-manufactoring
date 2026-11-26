"""ui/app.py with the real blow moulder: the fifth tab driven by Textual's test pilot (no PLC).  uv run pytest

The PLC ports have no server: the loop keeps running, the blow moulder PLC "does not answer" (outputs OFF, the
exhaust vents), which is enough to check that every blow moulder control only puts a command on the queue and that
the loop applies it. Widgets in a hidden tab can be read but not clicked, so each test opens the tab with key 5.
"""

from textual.widgets import Input, Select, Static, Switch, TabbedContent

from simulator import config
from simulator.machines.blowmoulder import BlowmoulderOutputs, Preform
from simulator.plant import Plant
from simulator.plcs import BlowmoulderPlc, FillerPlc
from simulator.ui import view
from simulator.ui.app import SimulatorApp, scenario_options

from test_panel import SIZE, run, texts
from test_plcs_link import free_port

WORLD_WIDTH = 86  # characters of the world column at 140x45 (controls 50)


def make_app(tmp_path, with_blowmoulder: bool = True) -> SimulatorApp:
    return SimulatorApp(Plant(with_blowmoulder=with_blowmoulder), FillerPlc(port=free_port()), tmp_path / "panel.log",
                        blowmoulder_plc=BlowmoulderPlc(port=free_port()) if with_blowmoulder else None)


async def open_tab(app, pilot) -> None:
    await pilot.press("5")
    await pilot.pause(0.1)
    assert app.query_one("#tabs", TabbedContent).active == "tab-blowmoulder"


def test_key_5_opens_the_tab_and_a_switch_injects_a_fault(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_tab(app, pilot)
            await pilot.click("#sw-bm-arm")
            await pilot.pause(0.3)  # a few ticks + one redraw
            assert app.plant.blowmoulder.arm_stuck is True
            assert "command: arm_stuck = True" in texts(app)
            world = str(app.query_one("#blowmoulder-world", Static).render())
            assert "Hopper" in world and "ARM STUCK" in world and "not answering" in world
            await pilot.press("q")

    run(test)


def test_value_rows_and_the_drop_button(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            await open_tab(app, pilot)
            b = app.plant.blowmoulder
            app.query_one("#in-preforms", Input).value = "3"
            await pilot.click("#bt-preforms")
            app.query_one("#in-high-blow", Input).value = "20"
            await pilot.click("#bt-high-blow")
            b.held = Preform(7)
            await pilot.click("#bt-drop-preform")
            await pilot.pause(0.3)
            assert b.hopper.count == 3 and b.high_blow_bar == 20.0
            assert b.held is None and [p.id for p in b.dropped] == [7]
            assert "command: drop_preform = True" in texts(app)
            world = str(app.query_one("#blowmoulder-world", Static).render())
            assert "REGULATOR 20 BAR" in world and "dropped 1" in world
            await pilot.press("q")

    run(test)


def test_switches_follow_the_plant_without_sending_commands(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            app.plant.blowmoulder.exhaust_blocked = True  # e.g. the exhaust_blocked scenario
            app.plant.blowmoulder_heartbeat.enabled = False
            app.plant.filler.door_blocked = True  # c1_backed_up
            await pilot.pause(0.4)  # one redraw; the tab is hidden, the sync still works
            assert app.query_one("#sw-bm-exhaust", Switch).value is True
            assert app.query_one("#sw-bm-hb", Switch).value is False
            assert app.query_one("#sw-door", Switch).value is True  # the filler tab's switch
            assert not any(t.startswith(("command: exhaust_blocked", "command: blowmoulder_heartbeat",
                                         "command: filler_door_blocked")) for t in texts(app))
            await pilot.press("q")

    run(test)


def test_a_blowmoulder_scenario_starts_from_the_shared_row(tmp_path):
    async def test():
        app = make_app(tmp_path)
        async with app.run_test(size=SIZE) as pilot:
            def offered() -> set[str]:
                return {value for _, value in app.query_one("#in-scenario", Select)._options if isinstance(value, str)}

            await open_tab(app, pilot)
            assert "air_low" in offered() and "film_torn" not in offered() and len(offered()) == 13
            app.query_one("#in-scenario", Select).value = "hopper_empty"
            await pilot.click("#bt-scenario")
            await pilot.pause(0.3)
            assert app.slot.runner is not None and app.slot.runner.scenario.name == "hopper_empty"
            assert "hopper_empty (blowmoulder): step 1/" in str(app.query_one("#scenario-status", Static).render())
            assert app.plant.blowmoulder.hopper.count == config.HOPPER_PREFORMS  # step 1 waits for the PLC first
            await pilot.press("q")

    run(test)


def test_without_the_blow_moulder_key_5_only_notifies(tmp_path):
    async def test():
        app = make_app(tmp_path, with_blowmoulder=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("5")
            await pilot.pause(0.1)
            assert app.query_one("#tabs", TabbedContent).active == "tab-filler" and not app.query("#blowmoulder-world")
            assert app.query("#sw-paused") and app.query("#in-rate")  # the supply is back on the filler tab
            await pilot.press("q")

    run(test)


def test_scenario_options_need_the_blow_moulder():
    assert not scenario_options("blowmoulder", True, True, True)
    offered = {name for _, name in scenario_options("blowmoulder", True, True, True, with_blowmoulder=True)}
    assert {"hopper_empty", "c1_backed_up", "regulator_failed"} <= offered and "film_torn" not in offered


# --- The world text -------------------------------------------------------------------------------
def test_world_text_shows_the_analog_values_on_both_sides():
    plant = Plant(with_blowmoulder=True)
    b = plant.blowmoulder
    b.pocket = Preform(5, 102.1, 102.4)
    b.mould = 1.0
    b.cavity = Preform(4, stretched=True, max_bar=39.0)
    b.pressure_bar = 35.0
    plant.blowmoulder_outputs = BlowmoulderOutputs(mould_close=True, stretch_rod=True, high_blow_valve=True,
                                                   lamp_power=29)
    text = view.blowmoulder_world_text(plant).plain
    assert "preform 5 102.1 °C" in text and "OvenTemp 1021  peak 102.4 °C" in text
    assert " 29 %" in text and "LampPower holds 102.1 °C" in text  # 20 + 2.83 x 29
    assert "35.0 bar" in text and "raw 24192" in text and "regulator 39 bar" in text
    assert "preform 4, stretched, max 39.0 bar" in text and "not answering" not in text
    assert max(len(line) for line in text.splitlines()) <= WORLD_WIDTH
    b.pressure_wire_break = True
    text = view.blowmoulder_world_text(plant).plain
    assert "raw -2000" in text and "WIRE BREAK" in text


def test_world_text_without_the_plc_shows_the_dead_outputs():
    plant = Plant(with_blowmoulder=True)  # no tick yet: the PLC has not answered
    text = view.blowmoulder_world_text(plant)
    assert "not answering" in text.plain and "exhaust vents" in text.plain
    exhaust = text.plain.index("ExhaustValve ●")  # BLOWMOULDER_DEAD: the normally-open exhaust is shown open
    assert exhaust > 0


def test_world_text_without_the_blow_moulder():
    assert "--no-blowmoulder" in view.blowmoulder_world_text(Plant()).plain
