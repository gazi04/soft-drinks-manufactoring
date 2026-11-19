"""loop.run with the labeler PLC (the third link) against local fake PLCs (pymodbus servers).  uv run pytest

The fake servers run no PLC program: outputs stay OFF unless a test sets them with a second client.
These tests check the loop mechanics with the third link (who is written, what C2 does, what happens
when the labeler PLC is down); the real loop is tests/test_closed_loop_labeler.py.
"""

import asyncio
import logging

import pytest
from test_loop import start_fake_plc, status
from test_loop_syrup import client, coil, plc_texts, register, sim_texts
from test_plcs_link import free_port

from simulator import address_map as am
from simulator import address_map_labeler as lm
from simulator import config, loop, plant_lock
from simulator.bottle import Bottle
from simulator.events import EventLog
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc, LabelerStatus, SyrupPlc

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def labeler_status(state=0, step=0, good=0, mode=1, **alarms) -> LabelerStatus:
    flags = dict(alm_label_timeout=False, alm_pocket_jam=False, alm_outfeed_blocked=False, alm_comm_lost=False,
                 alm_label_empty=False, alm_glue_over_temp=False, alm_glue_heat_timeout=False)
    flags.update(alarms)
    return LabelerStatus(state, step, good, mode=mode, **flags)


def test_three_plcs_are_written_and_each_link_is_reported():
    async def scenario() -> None:
        ports = fp, sp, lp = free_port(), free_port(), free_port()
        servers = [await start_fake_plc(p) for p in ports]
        plant, log = Plant(with_syrup=True, with_labeler=True), EventLog()
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=sp),
                               labeler_plc=LabelerPlc(port=lp))
        f, lab = await client(fp), await client(lp)
        assert await coil(f, am.E_STOP_OK) and await coil(lab, lm.E_STOP_OK)  # one E-stop chain for all
        assert await register(lab, lm.GLUE_TEMP) == round(config.ROOM_C * 10)  # the cold melt tank
        up = [t for t in sim_texts(log) if "link up" in t]
        assert len(up) == 3 and any(t.startswith("labeler PLC link up") for t in up)
        assert 9 <= stats.ticks <= 12 and log.warning_count == 0
        f.close(), lab.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_labeler_plc_drives_c2_and_sees_the_bottle_at_the_gate():
    async def scenario() -> None:
        fp, lp = free_port(), free_port()
        fs, ls = await start_fake_plc(fp), await start_fake_plc(lp)
        lab = await client(lp)
        await lab.write_coil(lm.CONVEYOR_MOTOR2, True)  # plays the labeler PLC: C2 runs
        plant, log = Plant(with_labeler=True), EventLog()
        plant.c2.put_at_entry(Bottle(id=99))
        await loop.run(FillerPlc(port=fp), plant, log, seconds=4.5, labeler_plc=LabelerPlc(port=lp))
        assert plant.c2.exit_bottle is not None and plant.c2.exit_bottle.id == 99  # 7 slot steps of 0.5 s
        assert await coil(lab, lm.BOTTLE_AT_GATE)  # the sensor came back to the labeler PLC
        lab.close()
        await fs.shutdown(), await ls.shutdown()

    asyncio.run(scenario())


def test_a_dead_labeler_plc_stops_c2_but_not_the_filler():
    async def scenario() -> None:
        fp = free_port()
        fs = await start_fake_plc(fp)
        f = await client(fp)
        await f.write_coil(am.CONVEYOR1, True)  # the filler PLC runs C1
        plant, log = Plant(with_labeler=True), EventLog()
        plant.c2.put_at_entry(Bottle(id=1))
        plant.supply.add(1)
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.5, labeler_plc=LabelerPlc(port=free_port()))
        assert stats.ticks >= 13  # the dead link never blocks the tick
        assert plant.c2.slots[0] is not None  # C2 stands: its motor belongs to the silent PLC
        assert plant.c1.count == 1 and plant.c1.slots[0] is None  # C1 moved: the filler is not affected
        dead = [t for t in sim_texts(log) if "labeler PLC" in t and "not reachable" in t]
        assert len(dead) == 1 and "--no-labeler" in dead[0]  # said once, with the way out
        assert any(t.startswith("PLC link up") for t in sim_texts(log))
        f.close()
        await fs.shutdown()

    asyncio.run(scenario())


def test_the_labeler_status_is_watched_and_summarised():
    async def scenario() -> None:
        fp, lp = free_port(), free_port()
        fs, ls = await start_fake_plc(fp), await start_fake_plc(lp)
        lab = await client(lp)
        await lab.write_registers(lm.STATUS_START, [1, 22, 0, 9])  # Running, WRAP, GoodCount 9

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 5:
                asyncio.get_running_loop().create_task(lab.write_registers(lm.STATUS_START, [1, 24, 0, 10]))

        stats = await loop.run(FillerPlc(port=fp), Plant(with_labeler=True), log := EventLog(), seconds=1.2,
                               on_tick=on_tick, labeler_plc=LabelerPlc(port=lp))
        texts = plc_texts(log)
        assert "labeler: status: mode Stop, step WRAP, state Running, GoodCount 9" in texts
        assert "labeler: step WRAP -> PRESS" in texts
        assert stats.labeler_status.good_count == 10 and "labeler GoodCount +1" in stats.summary()
        lab.close()
        await fs.shutdown(), await ls.shutdown()

    asyncio.run(scenario())


def test_the_labeler_port_is_locked_and_link_and_plant_must_match():
    async def scenario() -> None:
        fp, lp = free_port(), free_port()
        await loop.run(FillerPlc(port=fp), Plant(with_labeler=True), EventLog(), seconds=0.2, labeler_plc=LabelerPlc(port=lp))
        assert plant_lock.is_held("127.0.0.1", lp)
        with pytest.raises(ValueError, match="needs a plant with the labeler"):
            await loop.run(FillerPlc(port=free_port()), Plant(), EventLog(), seconds=0.2,
                           labeler_plc=LabelerPlc(port=free_port()))
        with pytest.raises(ValueError, match="nobody else drives C2"):
            await loop.run(FillerPlc(port=free_port()), Plant(with_labeler=True), EventLog(), seconds=0.2)

    asyncio.run(scenario())


# --- CLI, panel header ---------------------------------------------------------------------
def test_cli_drives_three_plcs_unless_told_otherwise():
    from simulator import _labeler_plc, _plant, parse_args

    default = parse_args([])
    assert (default.labeler_port, default.labeler_host, default.with_labeler) == (5023, default.host, True)
    plant = _plant(default)
    assert plant.syrup is not None and plant.labeler is not None and _labeler_plc(default).port == 5023
    two = parse_args(["--no-labeler"])
    assert _plant(two).labeler is None and _plant(two).labeler_standin is not None and _labeler_plc(two) is None
    assert _plant(two).syrup is not None
    alone = parse_args(["--filler-only"])
    assert _plant(alone).syrup is None and _plant(alone).labeler is None and _labeler_plc(alone) is None
    assert parse_args(["--labeler-host", "plc-labeler", "--labeler-port", "6001"]).labeler_port == 6001


def test_cli_refuses_a_stand_in_scenario_with_the_real_labeler(monkeypatch):
    import simulator
    from simulator.scenarios import SCENARIOS, uses_command

    assert uses_command(SCENARIOS["labeler_stopped"], "labeler_running")
    assert not uses_command(SCENARIOS["index_jam"], "labeler_running")
    monkeypatch.setattr("sys.argv", ["simulator", "--headless", "--scenario", "labeler_stopped"])
    with pytest.raises(SystemExit, match="--no-labeler"):
        simulator.main()


def test_summary_counts_labelled_and_packed_bottles():
    from simulator import _summary

    plant = Plant(with_labeler=True)
    plant.labeler.discharged, plant.labeler.labelled, plant.packer.taken = 5, 5, 4
    text = _summary(loop.LoopStats(), plant, 0)
    assert "labelled 5 of 5 discharged, packed 4" in text
    assert "bottles out 0" in _summary(loop.LoopStats(), Plant(), 0)  # the stand-in summary is unchanged


def test_header_shows_the_labeler_plc_line_and_its_hint():
    from simulator.ui import view

    plc, syrup, labeler = FillerPlc(port=1), SyrupPlc(port=2), LabelerPlc(port=3)
    assert "labeler" not in view.header_text(None, plc, syrup).plain  # without a labeler: unchanged
    lines = view.header_text(None, plc, syrup, labeler).plain.splitlines()
    assert lines[2].startswith("labeler PLC 127.0.0.1:3 not connected")
    stats = loop.LoopStats(ticks=5, dt_min=0.1, dt_max=0.1, status=status(step=0, state=0),
                           labeler_status=labeler_status(1, 24, 12, alm_label_empty=True))
    text = view.header_text(stats, plc, None, labeler).plain
    assert "step PRESS" in text and "GoodCount 12" in text and "alarms: Alm_LabelEmpty" in text
    stats.labeler_status = labeler_status()
    assert "hmi.py --labeler auto reset start" in view.header_text(stats, plc, None, labeler).plain
    stats.labeler_status = labeler_status(mode=0)
    assert "labeler mode Stop: Start is ignored" in view.header_text(stats, plc, None, labeler).plain


def test_panel_runs_the_third_link_and_closes_it(tmp_path):
    from simulator.ui.app import SimulatorApp

    async def test() -> None:
        labeler = LabelerPlc(port=free_port())
        app = SimulatorApp(Plant(with_labeler=True), FillerPlc(port=free_port()), tmp_path / "panel.log",
                           labeler_plc=labeler)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.5)
            assert "labeler PLC" in app.query_one("#header").render().plain
            assert "Labeler  real machine" in app.query_one("#world").render().plain
            await pilot.press("q")
        assert not labeler.connected

    asyncio.run(test())


# --- Stage 6: labeler scenarios in the loop ------------------------------------------------
def test_a_labeler_scenario_reacts_to_the_labeler_status_and_changes_the_labeler():
    from simulator.scenarios import SCENARIOS, ScenarioRunner

    async def scenario() -> None:
        fp, lp = free_port(), free_port()
        fs, ls = await start_fake_plc(fp), await start_fake_plc(lp)
        lab = await client(lp)
        await lab.write_registers(lm.STATUS_START, [1, 10, 0, 0])  # Running, WAIT_BOTTLE
        plant, log = Plant(with_labeler=True), EventLog()
        runner = ScenarioRunner(SCENARIOS["web_torn"])
        await loop.run(FillerPlc(port=fp), plant, log, seconds=0.6, scenario=runner, labeler_plc=LabelerPlc(port=lp))
        assert plant.labeler.web.torn is True  # WAIT_BOTTLE (step 10) of the LABELER, not of the filler
        assert runner.index == 2  # waits for Alm_LabelTimeout now
        assert any("command: web_torn = True" in t for t in sim_texts(log))
        lab.close()
        await fs.shutdown(), await ls.shutdown()

    asyncio.run(scenario())


def test_cli_refuses_a_labeler_scenario_without_the_real_labeler(monkeypatch):
    import simulator

    for flag in ("--no-labeler", "--filler-only"):
        monkeypatch.setattr("sys.argv", ["simulator", "--headless", flag, "--scenario", "web_torn"])
        with pytest.raises(SystemExit, match="needs the real labeler"):
            simulator.main()


def test_panel_offers_only_the_scenarios_the_plant_can_run():
    from simulator.ui.app import scenario_options

    full = [name for _, name in scenario_options(True, True)]
    assert "web_torn" in full and "low_syrup" in full and "labeler_stopped" not in full  # no stand-in to stop
    stand_in = [name for _, name in scenario_options(True, False)]
    assert "labeler_stopped" in stand_in and "web_torn" not in stand_in
    assert "index_jam" in full and "index_jam" in stand_in
