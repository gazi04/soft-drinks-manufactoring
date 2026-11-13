"""loop.run with two PLCs against two local fake PLCs (pymodbus servers).  uv run pytest

The fake servers run no PLC program: outputs stay OFF unless a test sets them with a second
client. These tests check the loop mechanics with two links (who is written, what is copied,
what happens when one link is down); the real two-PLC loop is tests/test_closed_loop_syrup.py.
"""

import asyncio
import logging

import pytest
from pymodbus.client import AsyncModbusTcpClient
from test_loop import start_fake_plc, status
from test_plcs_link import free_port

from simulator import address_map as am
from simulator import address_map_syrup as sm
from simulator import config, loop, plant_lock
from simulator.events import EventLog, Source
from simulator.plant import Plant
from simulator.plcs import FillerPlc, SyrupPlc, SyrupStatus

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def sim_texts(log: EventLog) -> list[str]:
    return [e.text for e in log.filter(source=Source.SIM)]


def plc_texts(log: EventLog) -> list[str]:
    return [e.text for e in log.filter(source=Source.PLC)]


async def both(seconds: float = 1.0, **kw):
    """Run the loop for `seconds` against two fake servers; returns (stats, log, plant, ports, servers)."""
    fp, sp = free_port(), free_port()
    fs, ss = await start_fake_plc(fp), await start_fake_plc(sp)
    plant, log = Plant(with_syrup=True), EventLog()
    stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=seconds, syrup_plc=SyrupPlc(port=sp), **kw)
    return stats, log, plant, (fp, sp), (fs, ss)


async def client(port: int) -> AsyncModbusTcpClient:
    c = AsyncModbusTcpClient("127.0.0.1", port=port)
    await c.connect()
    return c


async def coil(c: AsyncModbusTcpClient, address: int) -> bool:
    return bool((await c.read_coils(address, count=1)).bits[0])


async def register(c: AsyncModbusTcpClient, address: int) -> int:
    return (await c.read_holding_registers(address, count=1)).registers[0]


def test_both_plcs_are_written_and_each_link_is_reported():
    async def scenario() -> None:
        stats, log, plant, (fp, sp), servers = await both(1.0)
        f, s = await client(fp), await client(sp)
        assert await coil(f, am.E_STOP_OK) and await coil(s, sm.E_STOP_OK)  # one E-stop chain for both
        assert [await register(s, a) for a in range(sm.ANALOG_START, sm.ANALOG_START + 5)] == [600, 0, 200, 0, 500]
        up = [t for t in sim_texts(log) if "link up" in t]
        assert len(up) == 2 and any(t.startswith("PLC link up") for t in up) and any(t.startswith("syrup PLC link up") for t in up)
        assert 9 <= stats.ticks <= 12 and log.warning_count == 0
        f.close(), s.close()
        for server in servers:
            await server.shutdown()

    asyncio.run(scenario())


def test_product_available_is_copied_from_the_syrup_plc_to_the_filler_plc():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        fs, ss = await start_fake_plc(fp), await start_fake_plc(sp)
        plant, log = Plant(with_syrup=True), EventLog()
        f, s = await client(fp), await client(sp)
        seen: list[bool] = []

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 6:
                asyncio.get_running_loop().create_task(s.write_coil(sm.PRODUCT_AVAILABLE_OUT, True))
            if stats.ticks == 18:
                asyncio.get_running_loop().create_task(s.write_coil(sm.PRODUCT_AVAILABLE_OUT, False))
            seen.append(plant.filler_product_available)

        await loop.run(FillerPlc(port=fp), plant, log, seconds=3.0, on_tick=on_tick, syrup_plc=SyrupPlc(port=sp))
        first_true, last_true = seen.index(True), len(seen) - 1 - seen[::-1].index(True)
        # `seen[i]` is tick i + 1; the signal is set by the task started at tick 6 / 18
        assert 6 <= first_true <= 9  # the PLC's signal reaches the wire within a few ticks
        assert 17 <= last_true <= 21 and not seen[-1]  # and drops again
        assert not await coil(f, am.PRODUCT_AVAILABLE)  # what the filler PLC holds at the end
        f.close(), s.close()
        await fs.shutdown(), await ss.shutdown()

    asyncio.run(scenario())


def test_the_syrup_plc_outputs_move_the_syrup_room_and_come_back_as_sensors():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        fs, ss = await start_fake_plc(fp), await start_fake_plc(sp)
        s = await client(sp)
        await s.write_coil(sm.WATER_PUMP, True)  # the "PLC" starts the water pump
        plant, log = Plant(with_syrup=True), EventLog()
        await loop.run(FillerPlc(port=fp), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=sp))
        level = await register(s, sm.MIX_LEVEL)
        assert level == pytest.approx(plant.syrup.mix_l, abs=1)
        assert 15 <= level <= 25  # about 1 s x 20 L/s
        s.close()
        await fs.shutdown(), await ss.shutdown()

    asyncio.run(scenario())


def test_a_dead_syrup_plc_does_not_stop_the_filler_and_gives_it_no_product():
    async def scenario() -> None:
        fp = free_port()
        fs = await start_fake_plc(fp)
        f = await client(fp)
        plant, log = Plant(with_syrup=True), EventLog()
        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=free_port()))
        assert stats.ticks >= 9  # the dead link never blocks the tick
        assert await coil(f, am.E_STOP_OK) and not await coil(f, am.PRODUCT_AVAILABLE)
        dead = [t for t in sim_texts(log) if "syrup PLC" in t and "not reachable" in t]
        assert len(dead) == 1 and "--filler-only" in dead[0]  # said once, with the way out
        assert any(t.startswith("PLC link up") for t in sim_texts(log))
        f.close()
        await fs.shutdown()

    asyncio.run(scenario())


def test_a_dead_filler_plc_does_not_stop_the_syrup_room():
    async def scenario() -> None:
        sp = free_port()
        ss = await start_fake_plc(sp)
        s = await client(sp)
        await s.write_coil(sm.WATER_PUMP, True)
        plant, log = Plant(with_syrup=True), EventLog()
        stats = await loop.run(FillerPlc(port=free_port()), plant, log, seconds=1.0, syrup_plc=SyrupPlc(port=sp))
        assert stats.ticks >= 9
        assert await register(s, sm.MIX_LEVEL) > 10  # the syrup room still runs
        assert len([t for t in sim_texts(log) if t.startswith("PLC") and "not reachable" in t]) == 1
        s.close()
        await ss.shutdown()

    asyncio.run(scenario())


def test_a_syrup_plc_that_appears_later_is_picked_up_without_repeating_the_filler_problems():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        fs = await start_fake_plc(fp)
        plant, log = Plant(with_syrup=True), EventLog()
        servers = []

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 5:
                async def start() -> None:
                    servers.append(await start_fake_plc(sp))
                asyncio.get_running_loop().create_task(start())

        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=4.0, on_tick=on_tick,
                               syrup_plc=SyrupPlc(port=sp, reconnect_s=0.2))
        texts = sim_texts(log)
        assert any(t.startswith("syrup PLC link up") for t in texts)
        assert len([t for t in texts if t.startswith("PLC link up")]) == 1  # the filler link was not touched
        assert stats.syrup_status is not None
        await fs.shutdown(), await servers[0].shutdown()

    asyncio.run(scenario())


def test_a_second_writer_on_the_syrup_block_is_named():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        fs, ss = await start_fake_plc(fp), await start_fake_plc(sp)
        other = await client(sp)
        plant, log = Plant(with_syrup=True), EventLog()

        async def meddle() -> None:
            for _ in range(20):
                await other.write_register(sm.MIX_LEVEL, 777)  # the plant writes 0 here
                await asyncio.sleep(0.05)

        task = asyncio.create_task(meddle())
        await loop.run(FillerPlc(port=fp), plant, log, seconds=1.5, syrup_plc=SyrupPlc(port=sp))
        await task
        foreign = [t for t in sim_texts(log) if "changed by someone else" in t]
        assert len(foreign) == 1 and "mix_level" in foreign[0] and "syrup" in foreign[0]
        other.close()
        await fs.shutdown(), await ss.shutdown()

    asyncio.run(scenario())


def test_the_syrup_status_is_watched_and_summarised():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        fs, ss = await start_fake_plc(fp), await start_fake_plc(sp)
        s = await client(sp)
        await s.write_registers(sm.STATUS_START, [1, 10, 0, 4])  # State Running, DOSE_WATER, BatchCount 4
        plant, log = Plant(with_syrup=True), EventLog()

        def on_tick(stats: loop.LoopStats) -> None:
            if stats.ticks == 5:
                asyncio.get_running_loop().create_task(s.write_registers(sm.STATUS_START, [1, 20, 0, 5]))

        stats = await loop.run(FillerPlc(port=fp), plant, log, seconds=1.2, on_tick=on_tick, syrup_plc=SyrupPlc(port=sp))
        texts = plc_texts(log)
        assert "syrup: status: mode Stop, step DOSE_WATER, state Running, BatchCount 4" in texts
        assert "syrup: step DOSE_WATER -> DOSE_SYRUP" in texts and "syrup: batch complete (BatchCount 5)" in texts
        assert isinstance(stats.syrup_status, SyrupStatus) and stats.syrup_status.batch_count == 5
        assert "BatchCount +1" in stats.summary()
        s.close()
        await fs.shutdown(), await ss.shutdown()

    asyncio.run(scenario())


def test_the_watcher_texts_are_configurable_and_the_filler_ones_stay():
    filler = loop.PlcWatcher()
    assert [e.text for e in filler.update(status(), 0.0)][0] == "status: mode Auto, step IDLE, state Stopped, GoodCount 0"
    syrup = loop.PlcWatcher({5: "WAIT_DEMAND"}, {1: "Running"}, "syrup: ", "BatchCount", lambda s: s.batch_count, True)
    first = SyrupStatus(1, 5, 2, False, False, False, False, False, False, 1)
    assert syrup.update(first, 0.0)[0].text == "syrup: status: mode Auto, step WAIT_DEMAND, state Running, BatchCount 2"
    assert syrup.update(first, 0.1) == []  # nothing changed


def test_both_ports_are_locked_and_a_syrup_link_needs_a_syrup_room():
    async def scenario() -> None:
        fp, sp = free_port(), free_port()
        await loop.run(FillerPlc(port=fp), Plant(with_syrup=True), EventLog(), seconds=0.2, syrup_plc=SyrupPlc(port=sp))
        assert plant_lock.is_held("127.0.0.1", fp) and plant_lock.is_held("127.0.0.1", sp)
        with pytest.raises(ValueError, match="syrup room"):
            await loop.run(FillerPlc(port=free_port()), Plant(), EventLog(), seconds=0.2, syrup_plc=SyrupPlc(port=free_port()))

    asyncio.run(scenario())


# --- CLI, panel header ---------------------------------------------------------------------
def test_cli_drives_both_plcs_unless_filler_only():
    from simulator import _plant, _syrup_plc, parse_args

    both_args = parse_args([])
    assert (both_args.syrup_port, both_args.syrup_host, both_args.filler_only) == (5022, both_args.host, False)
    assert _plant(both_args).syrup is not None and _syrup_plc(both_args).port == 5022
    only = parse_args(["--filler-only", "--syrup-port", "6000"])
    assert _plant(only).syrup is None and _syrup_plc(only) is None  # the old behavior, stand-in included
    assert parse_args(["--syrup-host", "plc-syrup", "--syrup-port", "6000"]).syrup_host == "plc-syrup"


def test_header_shows_the_syrup_plc_line_and_its_hint():
    from simulator.ui import view

    plc, syrup = FillerPlc(port=1), SyrupPlc(port=2)
    assert "syrup" not in view.header_text(None, plc).plain  # filler-only header unchanged
    text = view.header_text(None, plc, syrup).plain
    assert text.splitlines()[1].startswith("syrup PLC 127.0.0.1:2 not connected")
    stats = loop.LoopStats(ticks=5, dt_min=0.1, dt_max=0.1, status=status(step=0, state=0),
                           syrup_status=SyrupStatus(1, 30, 7, False, False, True, False, False, False, 1))
    lines = view.header_text(stats, plc, syrup).plain
    assert "step MIX" in lines and "BatchCount 7" in lines and "alarms: Alm_CoolTimeout" in lines
    stats.syrup_status = SyrupStatus(0, 0, 0, False, False, False, False, False, False, 1)
    assert "hmi.py --syrup auto reset start" in view.header_text(stats, plc, syrup).plain  # syrup in IDLE
    stats.syrup_status = SyrupStatus(0, 0, 0, False, False, False, False, False, False, 0)
    assert "syrup mode Stop: Start is ignored" in view.header_text(stats, plc, syrup).plain


def test_panel_runs_the_second_link_and_closes_it(tmp_path):
    from simulator.ui.app import SimulatorApp

    async def test() -> None:
        syrup = SyrupPlc(port=free_port())
        app = SimulatorApp(Plant(with_syrup=True), FillerPlc(port=free_port()), tmp_path / "panel.log", syrup_plc=syrup)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause(0.5)
            assert "syrup PLC" in app.query_one("#header").render().plain
            assert any("syrup PLC" in e.text and "not reachable" in e.text for e in app.events.recent(len(app.events)))
            await pilot.press("q")

    asyncio.run(test())
