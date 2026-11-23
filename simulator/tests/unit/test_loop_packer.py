"""loop.run with the packer PLC (the fourth link) against local fake PLCs (pymodbus servers).  uv run pytest

The fake servers run no PLC program: outputs stay OFF unless a test sets them with a second client.
These tests check the loop mechanics with the fourth link (who is written, what C3 does, what happens
when the packer PLC is down); the real loop is tests/test_closed_loop_packer.py.
"""

import asyncio
import logging

import pytest
from test_loop import start_fake_plc, status
from test_loop_syrup import client, coil, plc_texts, register, sim_texts
from test_plcs_link import free_port

from simulator import address_map as am
from simulator import address_map_labeler as lm
from simulator import address_map_packer as pm
from simulator import config, loop, plant_lock
from simulator.bottle import Bottle
from simulator.events import EventLog
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc, PackerPlc, PackerStatus, SyrupPlc

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def packer_status(state=0, step=0, wrap_step=0, packs=0, mode=1, **alarms) -> PackerStatus:
    flags = dict(alm_film_fault=False, alm_pack_jam=False, alm_outfeed_blocked=False, alm_comm_lost=False,
                 alm_film_empty=False, alm_oven_over_temp=False, alm_oven_heat_timeout=False, alm_oven_jam=False,
                 alm_airflow_lost=False, alm_wrap_timeout=False)
    flags.update(alarms)
    return PackerStatus(state, step, wrap_step, packs, 0, 0, 17, 0, mode=mode, **flags)


def packer_plant() -> Plant:
    return Plant(with_labeler=True, with_packer=True)


def test_four_plcs_are_written_and_each_link_is_reported():
    async def scenario() -> None:
        ports = fp, sp, lp, pp = free_port(), free_port(), free_port(), free_port()
        servers = [await start_fake_plc(p) for p in ports]
        plant, log = Plant(with_syrup=True, with_labeler=True, with_packer=True), EventLog()
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=sp),
                               labeler_plc=LabelerPlc(port=lp), packer_plc=PackerPlc(port=pp))
        f, pk = await client(fp), await client(pp)
        assert await coil(f, am.E_STOP_OK) and await coil(pk, pm.E_STOP_OK)  # one E-stop chain for all
        assert await register(pk, pm.OVEN_TEMP) == round(config.ROOM_C * 10)  # the cold oven
        assert await coil(pk, pm.PUSHER_HOME) and await coil(pk, pm.WRAP_HOME)  # the machine at rest
        up = [t for t in sim_texts(log) if "link up" in t]
        assert len(up) == 4 and any(t.startswith("packer PLC link up") for t in up)
        assert 9 <= stats.ticks <= 12 and log.warning_count == 0
        f.close(), pk.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_packer_plc_drives_c3_and_sees_the_bottle_at_the_gate():
    async def scenario() -> None:
        fp, lp, pp = free_port(), free_port(), free_port()
        servers = [await start_fake_plc(p) for p in (fp, lp, pp)]
        pk = await client(pp)
        await pk.write_coil(pm.CONVEYOR_MOTOR3, True)  # plays the packer PLC: C3 runs, the gate stays shut
        plant, log = packer_plant(), EventLog()
        plant.c3.put_at_entry(Bottle(id=99))
        await loop.run(FillerPlc(port=fp), plant, log, seconds=4.5, labeler_plc=LabelerPlc(port=lp),
                       packer_plc=PackerPlc(port=pp))
        assert plant.c3.exit_bottle is not None and plant.c3.exit_bottle.id == 99  # 7 slot steps of 0.5 s
        assert await coil(pk, pm.BOTTLE_AT_GATE)  # the sensor came back to the packer PLC
        assert plant.packer.table == [] and plant.packer.entering is None  # gate shut: nothing taken
        pk.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_a_dead_packer_plc_stops_c3_but_not_the_labeler():
    async def scenario() -> None:
        fp, lp = free_port(), free_port()
        servers = [await start_fake_plc(p) for p in (fp, lp)]
        lab = await client(lp)
        await lab.write_coil(lm.CONVEYOR_MOTOR2, True)  # the labeler PLC runs C2
        plant, log = packer_plant(), EventLog()
        plant.c3.put_at_entry(Bottle(id=1))
        plant.c2.put_at_entry(Bottle(id=2))
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.5, labeler_plc=LabelerPlc(port=lp),
                               packer_plc=PackerPlc(port=free_port()))
        assert stats.ticks >= 13  # the dead link never blocks the tick
        assert plant.c3.slots[0] is not None  # C3 stands: its motor belongs to the silent PLC
        assert plant.c2.slots[0] is None  # C2 moved: the labeler is not affected
        assert plant.packer.oven.temp_c < config.ROOM_C + 0.1  # no heater either
        dead = [t for t in sim_texts(log) if "packer PLC" in t and "not reachable" in t]
        assert len(dead) == 1 and "--no-packer" in dead[0]  # said once, with the way out
        lab.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_packer_status_and_both_steps_are_watched_and_summarised():
    async def scenario() -> None:
        fp, lp, pp = free_port(), free_port(), free_port()
        servers = [await start_fake_plc(p) for p in (fp, lp, pp)]
        pk = await client(pp)
        await pk.write_registers(pm.STATUS_START, [1, 10, 20, 4])  # Running, COUNT, FEED, PackCount 4

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 5:
                asyncio.get_running_loop().create_task(pk.write_registers(pm.STATUS_START, [1, 15, 22, 5]))

        stats = await loop.run(FillerPlc(port=fp), packer_plant(), log := EventLog(), seconds=1.2, on_tick=on_tick,
                               labeler_plc=LabelerPlc(port=lp), packer_plc=PackerPlc(port=pp))
        texts = plc_texts(log)
        assert "packer: status: mode Stop, step COUNT, wrap step FEED, state Running, PackCount 4" in texts
        assert "packer: step COUNT -> WAIT_WRAP" in texts and "packer: wrap step FEED -> CUT" in texts
        assert stats.packer_status.pack_count == 5 and "PackCount +1" in stats.summary()
        pk.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_the_packer_port_is_locked_and_link_and_plant_must_match():
    async def scenario() -> None:
        fp, lp, pp = free_port(), free_port(), free_port()
        await loop.run(FillerPlc(port=fp), packer_plant(), EventLog(), seconds=0.2, labeler_plc=LabelerPlc(port=lp),
                       packer_plc=PackerPlc(port=pp))
        assert plant_lock.is_held("127.0.0.1", pp)
        with pytest.raises(ValueError, match="needs a plant with the packer"):
            await loop.run(FillerPlc(port=free_port()), Plant(with_labeler=True), EventLog(), seconds=0.2,
                           labeler_plc=LabelerPlc(port=free_port()), packer_plc=PackerPlc(port=free_port()))
        with pytest.raises(ValueError, match="nobody else drives C3"):
            await loop.run(FillerPlc(port=free_port()), packer_plant(), EventLog(), seconds=0.2,
                           labeler_plc=LabelerPlc(port=free_port()))

    asyncio.run(scenario())


# --- CLI, panel header ---------------------------------------------------------------------
def test_cli_drives_four_plcs_unless_told_otherwise():
    from simulator import _packer_plc, _plant, parse_args

    default = parse_args([])
    assert (default.packer_port, default.packer_host, default.with_packer) == (5024, default.host, True)
    plant = _plant(default)
    assert plant.packer is not None and plant.palletizer is not None and _packer_plc(default).port == 5024
    three = parse_args(["--no-packer"])
    assert _plant(three).packer is None and _plant(three).packer_standin is not None and _packer_plc(three) is None
    assert _plant(three).labeler is not None
    for options in (["--no-labeler"], ["--filler-only"]):  # no labeler, no packer: it takes C3's bottles
        args = parse_args(options)
        assert not args.with_packer and _plant(args).packer is None and _packer_plc(args) is None
    assert parse_args(["--packer-host", "plc-packer", "--packer-port", "6002"]).packer_port == 6002


def test_cli_refuses_the_packer_stand_in_scenario_with_the_real_packer(monkeypatch):
    import simulator
    from simulator.scenarios import SCENARIOS, uses_command

    assert uses_command(SCENARIOS["packer_stopped"], "packer_running")
    monkeypatch.setattr("sys.argv", ["simulator", "--headless", "--scenario", "packer_stopped"])
    with pytest.raises(SystemExit, match="--no-packer"):
        simulator.main()


def test_summary_counts_packs_with_the_real_packer():
    from simulator import _summary

    plant = packer_plant()
    plant.packer.groups_pushed, plant.palletizer.taken = 3, 2
    assert "packs 3 pushed / 2 palletized" in _summary(loop.LoopStats(), plant, 0)


def test_header_shows_the_packer_plc_line_and_its_hint():
    from simulator.ui import view

    plc, syrup, labeler, packer = FillerPlc(port=1), SyrupPlc(port=2), LabelerPlc(port=3), PackerPlc(port=4)
    assert "packer" not in view.header_text(None, plc, syrup, labeler).plain  # without a packer: unchanged
    lines = view.header_text(None, plc, syrup, labeler, packer).plain.splitlines()
    assert lines[3].startswith("packer PLC 127.0.0.1:4 not connected")
    stats = loop.LoopStats(ticks=5, dt_min=0.1, dt_max=0.1, status=status(step=0, state=0),
                           packer_status=packer_status(3, 15, 24, 7, alm_outfeed_blocked=True))
    text = view.packer_header_text(stats, packer).plain
    assert "step WAIT_WRAP / wrap READY" in text and "state Blocked" in text and "PackCount 7" in text
    assert "alarms: Alm_OutfeedBlocked" in text and "hmi.py" not in text  # running: no hint
    idle = loop.LoopStats(packer_status=packer_status(mode=0))
    assert "hmi.py --packer auto reset start" in view.packer_header_text(idle, packer).plain

