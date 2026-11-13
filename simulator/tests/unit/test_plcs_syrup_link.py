"""PlcLink behavior for BOTH PLC classes, and SyrupPlc requests, against a local Modbus server.

No PLC, no Docker: pymodbus' own ModbusTcpServer on a free localhost port plays the runtime.
The base behavior (no connection, error reply keeps the link, drop and reconnect) is proven
once for FillerPlc and once for SyrupPlc, so the shared base class is really shared.
"""

import asyncio
import logging

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer
from test_plcs_link import free_port, start_server, until

from simulator import address_map_syrup as sm
from simulator.machines.syrup import SyrupOutputs, SyrupSensors
from simulator.plant import SyrupSignals, SyrupWrites
from simulator.plcs import FillerPlc, PlcCommError, PlcReplyError, SyrupPlc

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

# (class, its first harmless read): the filler reads its outputs, the syrup room its signals
CASES = [pytest.param(FillerPlc, "read_outputs", id="filler"), pytest.param(SyrupPlc, "read_signals", id="syrup")]


async def start_big_server(port: int) -> ModbusTcpServer:
    """Memory big enough for the real addresses (coils up to 8240, registers up to 1100)."""
    device = ModbusDeviceContext(
        co=ModbusSequentialDataBlock(1, [False] * 8300),
        hr=ModbusSequentialDataBlock(1, [0] * 1100),
    )
    server = ModbusTcpServer(ModbusServerContext(devices=device, single=True), address=("127.0.0.1", port))
    await server.serve_forever(background=True)
    return server


def syrup_writes(**analog) -> SyrupWrites:
    values = dict(syrup_level=600, mix_level=0, buffer_level=500, product_temp=200, co2_pressure=0)
    values.update(analog)
    return SyrupWrites(SyrupSensors(mix_high_high=False, **values), e_stop_ok=True, heartbeat=True)


# --- The shared base class ----------------------------------------------------------
@pytest.mark.parametrize("cls, read", CASES)
def test_request_without_connection_is_plc_comm_error(cls, read):
    plc = cls(port=free_port())  # no running loop needed to create it
    with pytest.raises(PlcCommError):
        asyncio.run(getattr(plc, read)())
    assert not plc.link_ok and not plc.connected


@pytest.mark.parametrize("cls, read", CASES)
def test_an_error_reply_keeps_the_link(cls, read):
    async def scenario() -> None:
        port = free_port()
        server = await start_server(port)  # only 16 registers: HR 1074 is outside
        plc = cls(port=port, reconnect_s=0.1, timeout_s=0.5)
        assert await plc.ensure_connected()
        assert not plc.link_ok  # connected is not yet "answered"
        with pytest.raises(PlcReplyError):
            await plc.read_status()
        assert plc.link_ok and plc.connected
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("cls, read", CASES)
def test_drop_and_reconnect(cls, read):
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = cls(port=port, reconnect_s=0.1, timeout_s=0.5)
        assert await plc.ensure_connected()
        await getattr(plc, read)()
        assert plc.link_ok

        await server.shutdown()  # "runtime restart"
        await asyncio.sleep(0.3)
        assert not plc.connected

        async def read_fails() -> bool:
            try:
                await getattr(plc, read)()
                return False
            except PlcCommError:  # never pymodbus' ConnectionException
                return True

        assert await until(read_fails)
        assert not plc.link_ok

        server = await start_big_server(port)

        async def read_ok() -> bool:
            if not await plc.ensure_connected():
                return False
            try:
                await getattr(plc, read)()
                return True
            except PlcCommError:
                return False

        assert await until(read_ok)
        assert plc.link_ok
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_each_class_keeps_its_own_default_port_and_ownership():
    assert (FillerPlc().port, SyrupPlc().port) == (502, 5022)
    assert FillerPlc.write_coils == SyrupPlc.write_coils  # same sensor block 8192-8202 on purpose
    assert FillerPlc.write_registers != SyrupPlc.write_registers  # the filler also owns the animation words
    assert not SyrupPlc.write_registers & set(range(1094, 1100))  # the animation words %MW70-75 are not the syrup room's
    assert SyrupPlc.write_coils == sm.SIM_WRITE_COILS and SyrupPlc.write_registers == sm.SIM_WRITE_REGISTERS


# --- SyrupPlc requests ---------------------------------------------------------------
def test_write_puts_every_value_on_its_address():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = SyrupPlc(port=port)
        assert await plc.ensure_connected()
        await plc.write(syrup_writes(mix_level=250, product_temp=-5))

        reader = AsyncModbusTcpClient("127.0.0.1", port=port)
        await reader.connect()
        coils = (await reader.read_coils(sm.SENSOR_BLOCK_START, count=11)).bits[:11]
        assert [a for a, on in zip(sm.SENSOR_BLOCK, coils) if on] == [sm.E_STOP_OK, sm.HEARTBEAT]
        regs = (await reader.read_holding_registers(sm.ANALOG_START, count=5)).registers
        assert regs == [600, 250, 65531, 0, 500]  # -5 as two's complement
        assert (await reader.read_holding_registers(sm.CMD, count=2)).registers == [0, 0]  # HMI words untouched
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_read_signals_decodes_outputs_and_product_available():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = SyrupPlc(port=port)
        assert await plc.ensure_connected()
        other = AsyncModbusTcpClient("127.0.0.1", port=port)  # plays the PLC: sets its outputs
        await other.connect()
        await other.write_coils(0, [False, True, False, False, True, False])  # SyrupPump, CO2Valve
        assert await plc.read_signals() == SyrupSignals(SyrupOutputs(syrup_pump=True, co2_valve=True), False)
        await other.write_coil(sm.PRODUCT_AVAILABLE_OUT, True)
        signals = await plc.read_signals()
        assert signals.product_available and signals.outputs.syrup_pump
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_read_status_decodes_state_step_alarms_and_mode():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = SyrupPlc(port=port)
        assert await plc.ensure_connected()
        other = AsyncModbusTcpClient("127.0.0.1", port=port)
        await other.connect()
        await other.write_registers(sm.STATUS_START, [9, 900, 0, 3])
        await other.write_coils(sm.ALARMS_START, [False, False, True, False, False, False])  # Alm_CoolTimeout
        await other.write_register(sm.MODE_REQ, 1)
        s = await plc.read_status()
        assert (s.state, s.step, s.batch_count, s.mode) == (9, 900, 3, 1)
        assert [n for n, on in s.alarms.items() if on] == ["Alm_CoolTimeout"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_verify_writes_detects_a_second_writer():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = SyrupPlc(port=port)
        assert await plc.ensure_connected()
        w = syrup_writes(mix_level=250)
        await plc.write(w)
        assert await plc.verify_writes(w) == []  # nobody else writes

        other = AsyncModbusTcpClient("127.0.0.1", port=port)  # e.g. a test script left running
        await other.connect()
        await other.write_coil(sm.E_STOP_OK, False)
        await other.write_register(sm.MIX_LEVEL, 999)
        assert await plc.verify_writes(w) == ["e_stop_ok", "mix_level"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
