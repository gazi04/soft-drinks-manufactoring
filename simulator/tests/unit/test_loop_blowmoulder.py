"""loop.run with the blow moulder PLC (the fifth link) against local fake PLCs (pymodbus servers).  uv run pytest

The fake servers run no PLC program: outputs stay OFF unless a test sets them with a second client. These tests
check the loop mechanics with the fifth link (who is written, what the blow moulder does, what happens when its
PLC is down), the CLI options, the panel header and the filler tab with the blow moulder; the real loop is
tests/test_closed_loop_blowmoulder.py.
"""

import asyncio
import logging

import pytest
from test_loop import start_fake_plc, status
from test_loop_syrup import client, coil, plc_texts, register, sim_texts
from test_panel import SIZE, run
from test_plcs_link import free_port
from textual.widgets import Static

from simulator import address_map as am
from simulator import address_map_blowmoulder as bm
from simulator import config, loop, plant_lock
from simulator.commands import COMMANDS
from simulator.events import EventLog
from simulator.plant import Plant
from simulator.plcs import BlowmoulderPlc, BlowmoulderStatus, FillerPlc, LabelerPlc, PackerPlc, SyrupPlc
from simulator.scenarios import SCENARIOS, SUPPLY_COMMANDS, uses_supply
from simulator.ui.app import SimulatorApp, scenario_options

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def bm_status(state=0, step=0, mould_step=0, bottles=0, mode=1, **alarms) -> BlowmoulderStatus:
    names = ("alm_no_preforms", "alm_handling_jam", "alm_outfeed_blocked", "alm_comm_lost", "alm_mould_fault",
             "alm_oven_over_temp", "alm_oven_heat_timeout", "alm_blow_fault", "alm_exhaust_fault",
             "alm_over_pressure", "alm_pressure_sensor")
    flags = {name: False for name in names} | alarms
    return BlowmoulderStatus(state, step, mould_step, bottles, 0, 0, 0, mode=mode, **flags)


def bm_plant() -> Plant:
    return Plant(with_blowmoulder=True)


def test_five_plcs_are_written_and_each_link_is_reported():
    async def scenario() -> None:
        ports = fp, sp, lp, pp, bp = [free_port() for _ in range(5)]
        servers = [await start_fake_plc(p) for p in ports]
        plant = Plant(with_syrup=True, with_labeler=True, with_packer=True, with_blowmoulder=True)
        log = EventLog()
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=sp),
                               labeler_plc=LabelerPlc(port=lp), packer_plc=PackerPlc(port=pp),
                               blowmoulder_plc=BlowmoulderPlc(port=bp))
        f, b = await client(fp), await client(bp)
        assert await coil(f, am.E_STOP_OK) and await coil(b, bm.E_STOP_OK)  # one E-stop chain for all
        assert await register(b, bm.OVEN_TEMP) == round(config.ROOM_C * 10)  # the cold, empty pocket
        assert await coil(b, bm.ARM_AT_OVEN) and await coil(b, bm.MOULD_OPEN) and await coil(b, bm.ROD_UP)
        up = [t for t in sim_texts(log) if "link up" in t]
        assert len(up) == 5 and any(t.startswith("blow moulder PLC link up") for t in up)
        assert 9 <= stats.ticks <= 12 and log.warning_count == 0
        f.close(), b.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_blow_moulder_plc_feeds_a_preform_and_sees_it_at_the_loader():
    async def scenario() -> None:
        fp, bp = free_port(), free_port()
        servers = [await start_fake_plc(p) for p in (fp, bp)]
        b = await client(bp)
        await b.write_coil(bm.HOPPER_FEED, True)  # plays the blow moulder PLC: the chute runs
        plant, log = bm_plant(), EventLog()
        await loop.run(FillerPlc(port=fp), plant, log, seconds=1.2, blowmoulder_plc=BlowmoulderPlc(port=bp))
        assert plant.blowmoulder.preform_at_loader  # CHUTE_S 0.6 s
        assert await coil(b, bm.PREFORM_AT_LOADER)  # the sensor came back to the blow moulder PLC
        b.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_a_dead_blow_moulder_plc_moves_nothing_but_not_the_filler():
    async def scenario() -> None:
        fp = free_port()
        server = await start_fake_plc(fp)
        f = await client(fp)
        await f.write_coil(am.CONVEYOR1, True)  # the filler PLC runs C1
        plant, log = bm_plant(), EventLog()
        plant.c1.slots[0] = plant.factory.new()
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.5, blowmoulder_plc=BlowmoulderPlc(port=free_port()))
        assert stats.ticks >= 13  # the dead link never blocks the tick
        assert plant.c1.slots[0] is None  # C1 moved: the filler is not affected
        assert plant.blowmoulder_outputs is None and not plant.blowmoulder.preform_at_loader
        dead = [t for t in sim_texts(log) if "blow moulder PLC" in t and "not reachable" in t]
        assert len(dead) == 1 and "--no-blowmoulder" in dead[0]  # said once, with the way out
        f.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_the_blow_moulder_status_and_both_steps_are_watched_and_summarised():
    async def scenario() -> None:
        fp, bp = free_port(), free_port()
        servers = [await start_fake_plc(p) for p in (fp, bp)]
        b = await client(bp)
        await b.write_registers(bm.STATUS_START, [1, 25, 20, 4])  # Running, HOT, GRIP, BottleCount 4

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 5:
                asyncio.get_running_loop().create_task(b.write_registers(bm.STATUS_START, [1, 10, 22, 5]))

        stats = await loop.run(FillerPlc(port=fp), bm_plant(), log := EventLog(), seconds=1.2, on_tick=on_tick,
                               blowmoulder_plc=BlowmoulderPlc(port=bp))
        texts = plc_texts(log)
        assert "blow moulder: status: mode Stop, step HOT, mould step GRIP, state Running, BottleCount 4" in texts
        assert "blow moulder: step HOT -> FEED" in texts and "blow moulder: mould step GRIP -> SWING" in texts
        assert stats.blowmoulder_status.bottle_count == 5 and "BottleCount +1" in stats.summary()
        b.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_packer_watcher_text_is_unchanged():
    from simulator.plcs import PACKER_STATE_NAMES, PACKER_STEP_NAMES, PACKER_WRAP_STEP_NAMES
    from test_loop_packer import packer_status

    w = loop.PlcWatcher(PACKER_STEP_NAMES, PACKER_STATE_NAMES, "packer: ", "PackCount", lambda s: s.pack_count,
                        wrap_step_names=PACKER_WRAP_STEP_NAMES)
    w.update(packer_status(1, 10, 20, 4), 0.0)
    assert [e.text for e in w.update(packer_status(1, 10, 22, 4), 0.1)] == ["packer: wrap step FEED -> CUT"]


def test_the_blow_moulder_port_is_locked_and_link_and_plant_must_match():
    async def scenario() -> None:
        fp, bp = free_port(), free_port()
        await loop.run(FillerPlc(port=fp), bm_plant(), EventLog(), seconds=0.2, blowmoulder_plc=BlowmoulderPlc(port=bp))
        assert plant_lock.is_held("127.0.0.1", bp)
        with pytest.raises(ValueError, match="needs a plant with the blow moulder"):
            await loop.run(FillerPlc(port=free_port()), Plant(), EventLog(), seconds=0.2,
                           blowmoulder_plc=BlowmoulderPlc(port=free_port()))
        with pytest.raises(ValueError, match="nobody else makes the bottles"):
            await loop.run(FillerPlc(port=free_port()), bm_plant(), EventLog(), seconds=0.2)

    asyncio.run(scenario())


# --- CLI ----------------------------------------------------------------------------------------
def test_cli_drives_five_plcs_unless_told_otherwise():
    from simulator import _blowmoulder_plc, _plant, parse_args

    default = parse_args([])
    assert (default.blowmoulder_port, default.blowmoulder_host, default.with_blowmoulder) == (5025, default.host, True)
    plant = _plant(default)
    assert plant.blowmoulder is not None and plant.supply is None and _blowmoulder_plc(default).port == 5025
    assert plant.packer is not None and plant.labeler is not None and plant.syrup is not None  # all five
    four = parse_args(["--no-blowmoulder", "--rate", "12", "--add", "3"])
    plant = _plant(four)
    assert plant.blowmoulder is None and _blowmoulder_plc(four) is None and plant.packer is not None
    assert plant.supply.per_min == 12 and plant.supply.pending == 3
    assert _plant(parse_args(["--no-blowmoulder"])).supply.per_min == config.SUPPLY_PER_MIN  # the old default
    alone = parse_args(["--filler-only"])
    assert not alone.with_blowmoulder and _plant(alone).supply is not None  # the filler alone: supply
    assert parse_args(["--blowmoulder-host", "plc-blowmolder", "--blowmoulder-port", "6003"]).blowmoulder_port == 6003


@pytest.mark.parametrize("options", [["--add", "5"], ["--rate", "10"]])
def test_cli_refuses_supply_options_with_the_blow_moulder(monkeypatch, options):
    import simulator

    monkeypatch.setattr("sys.argv", ["simulator", "--headless", *options])
    with pytest.raises(SystemExit, match="--no-blowmoulder"):
        simulator.main()


def test_cli_refuses_a_supply_scenario_with_the_blow_moulder(monkeypatch):
    import simulator

    supply_scenarios = [name for name, s in SCENARIOS.items() if uses_supply(s)]
    assert supply_scenarios  # the filler's starved scenario sets the supply
    monkeypatch.setattr("sys.argv", ["simulator", "--headless", "--scenario", supply_scenarios[0]])
    with pytest.raises(SystemExit, match="uses the bottle supply: add --no-blowmoulder"):
        simulator.main()
    assert SUPPLY_COMMANDS == {name for name, spec in COMMANDS.items() if spec.supply}


def test_summary_counts_blown_bottles():
    from simulator import _summary

    plant = Plant(with_labeler=True, with_packer=True, with_blowmoulder=True)
    plant.blowmoulder.ejected = 7
    assert _summary(loop.LoopStats(), plant, 0).startswith("blown 7, ")
    assert not _summary(loop.LoopStats(), Plant(), 0).startswith("blown")


# --- Panel ----------------------------------------------------------------------------------------
def test_header_shows_the_blow_moulder_plc_line_and_its_hint():
    from simulator.ui import view

    plc, b = FillerPlc(port=1), BlowmoulderPlc(port=5)
    assert "blow moulder" not in view.header_text(None, plc).plain  # without it: unchanged
    lines = view.header_text(None, plc, blowmoulder_plc=b).plain.splitlines()
    assert lines[1].startswith("blow moulder PLC 127.0.0.1:5 not connected")
    stats = loop.LoopStats(ticks=5, dt_min=0.1, dt_max=0.1, status=status(step=0, state=0),
                           blowmoulder_status=bm_status(3, 25, 80, 9, alm_outfeed_blocked=True))
    text = view.blowmoulder_header_text(stats, b).plain
    assert "step HOT / mould EJECT" in text and "state Blocked" in text and "BottleCount 9" in text
    assert "alarms: Alm_OutfeedBlocked" in text and "hmi.py" not in text  # running: no hint
    idle = loop.LoopStats(blowmoulder_status=bm_status(mode=0))
    assert "hmi.py --blowmoulder auto reset start" in view.blowmoulder_header_text(idle, b).plain


def test_scenario_list_hides_supply_scenarios_with_the_blow_moulder():
    with_supply = {name for _, name in scenario_options("filler", True, True, True)}
    without = {name for _, name in scenario_options("filler", True, True, True, with_blowmoulder=True)}
    hidden = with_supply - without
    assert hidden and all(uses_supply(SCENARIOS[name]) for name in hidden)


def test_filler_tab_with_the_blow_moulder_has_no_supply_controls(tmp_path):
    async def test():
        app = SimulatorApp(bm_plant(), FillerPlc(port=free_port()), tmp_path / "panel.log",
                           blowmoulder_plc=BlowmoulderPlc(port=free_port()))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            assert not app.query("#in-rate") and not app.query("#in-add") and not app.query("#sw-paused")
            world = str(app.query_one("#world", Static).render())
            assert "Blow mld hopper 1000" in world and "blow moulder PLC not answering" in world
            header = str(app.query_one("#header", Static).render())
            assert "blow moulder PLC" in header
            await pilot.press("q")

    run(test)
