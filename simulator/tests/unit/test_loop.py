"""loop.py: watcher and the tick loop against a local fake PLC (pymodbus server).  uv run pytest

The local server holds every address the loop uses, all 0 / False at the start.
It does not run a PLC program, so the outputs stay OFF: these tests check the
loop mechanics (rate, link handling, read-back), not the bottle flow. The bottle
flow with the real PLC is tests/test_closed_loop.py.
"""

import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from simulator import address_map as am
from simulator import loop
from simulator.commands import CommandQueue
from simulator.events import EventLog, Source
from simulator.plant import Plant
from simulator.plcs import FillerPlc, PlcStatus

from test_plcs_link import free_port

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def status(step=0, state=0, good=0, jam=False, comm=False, mode=1) -> PlcStatus:
    return PlcStatus(state, step, good, jam, False, False, comm, 500, 2000, 1500, mode)


def test_watcher_reports_changes_only():
    w = loop.PlcWatcher()
    first = w.update(status(comm=True), 0.0)
    assert [e.text for e in first] == ["status: mode Auto, step IDLE, state Stopped, GoodCount 0", "Alm_CommLost active"]
    assert w.update(status(comm=True), 0.1) == []  # nothing changed
    texts = [e.text for e in w.update(status(step=10, state=1), 0.2)]
    assert texts == ["step IDLE -> WORK", "state Stopped -> Running", "Alm_CommLost cleared"]
    assert all(e.source == Source.PLC for e in w.update(status(step=20, state=1, jam=True), 0.3))


async def start_fake_plc(port: int) -> ModbusTcpServer:
    device = ModbusDeviceContext(
        co=ModbusSequentialDataBlock(1, [False] * 8300),
        hr=ModbusSequentialDataBlock(1, [0] * 1100),
    )
    server = ModbusTcpServer(ModbusServerContext(devices=device, single=True), address=("127.0.0.1", port))
    await server.serve_forever(background=True)
    return server


def sim_texts(log: EventLog) -> list[str]:
    return [e.text for e in log.filter(source=Source.SIM)]


def test_loop_rate_link_and_read_back():
    async def scenario() -> None:
        port = free_port()
        server = await start_fake_plc(port)
        plc, plant, log = FillerPlc(port=port), Plant(), EventLog()
        stats = await loop.run(plc, plant, log, seconds=1.0)
        assert 9 <= stats.ticks <= 12  # fixed rate 0.1 s
        assert stats.dt_max < 0.2
        assert sim_texts(log)[0].startswith("PLC link up")
        assert log.warning_count == 0

        # A second writer changes S8 while the loop runs -> one warning naming it
        other = AsyncModbusTcpClient("127.0.0.1", port=port)
        await other.connect()

        async def meddle() -> None:
            for _ in range(20):
                await other.write_coil(am.S8, False)  # the plant writes S8 = TRUE (belt at rest)
                await asyncio.sleep(0.05)

        task = asyncio.create_task(meddle())
        await loop.run(plc, plant, log, seconds=1.0)
        await task
        foreign = [t for t in sim_texts(log) if "changed by someone else" in t]
        assert len(foreign) == 1 and "s8" in foreign[0]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_loop_without_plc_keeps_running_with_outputs_off():
    async def scenario() -> None:
        plc, plant, log = FillerPlc(port=free_port()), Plant(), EventLog()
        plant.supply.add(3)
        stats = await loop.run(plc, plant, log, seconds=0.5)
        assert stats.ticks >= 4  # the loop never blocks on the dead link
        texts = sim_texts(log)
        assert len([t for t in texts if "not reachable" in t]) == 1  # logged once, not every tick
        assert plant.c1.count == 1  # C1 motor OFF: the first bottle stays at the entry

    asyncio.run(scenario())


def test_queued_command_is_applied_at_the_next_tick_and_reaches_the_plc():
    async def scenario() -> None:
        port = free_port()
        server = await start_fake_plc(port)
        plc, plant, log = FillerPlc(port=port), Plant(), EventLog()
        commands = CommandQueue()

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 3:
                commands.put("e_stop", True)  # like a panel button pressed between two ticks

        stats = await loop.run(plc, plant, log, seconds=0.6, on_tick=on_tick, commands=commands)
        assert plant.e_stop_ok is False
        assert "command: e_stop = True" in sim_texts(log)
        assert stats.status is not None and stats.status.step == 0  # last status kept for callers

        reader = AsyncModbusTcpClient("127.0.0.1", port=port)
        await reader.connect()
        e_stop_ok = (await reader.read_coils(am.E_STOP_OK, count=1)).bits[0]
        assert e_stop_ok is False  # the wire value was written to the PLC
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_watcher_reports_mode_changes():
    w = loop.PlcWatcher()
    w.update(status(mode=0), 0.0)
    assert [e.text for e in w.update(status(mode=1), 0.1)] == ["mode Stop -> Auto"]


def test_animation_words_reach_the_plc():
    async def scenario() -> None:
        port = free_port()
        server = await start_fake_plc(port)
        plc, plant, log = FillerPlc(port=port), Plant(), EventLog()
        plant.supply.add(1)  # one bottle on C1's entry (C1 motor OFF: it stays there)
        await loop.run(plc, plant, log, seconds=0.3)
        reader = AsyncModbusTcpClient("127.0.0.1", port=port)
        await reader.connect()
        words = (await reader.read_holding_registers(am.ANIM_START, count=am.ANIM_COUNT)).registers
        assert words == list(plant.writes().anim) == [1, 0, 0, 0, 0, 0]
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
