"""plcs.FillerPlc link behaviour against a local in-process Modbus server.  uv run pytest

No PLC, no Docker: pymodbus' own ModbusTcpServer on a free localhost port plays
the runtime. It only holds coils 0-15 and registers 0-15, so a read of the
status registers (HR 1074) gets an error REPLY from it.

Covers the three link problems found in stage 5:
    P1 FillerPlc can be created outside asyncio;
    P2 a request while the link is down gives PlcCommError (never pymodbus'
       ConnectionException);
    P3 link_ok means "the last request was answered"; an error reply keeps the link.
"""

import asyncio
import logging
import socket
import time

import pytest
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from simulator.plcs import FillerPlc, PlcCommError, PlcReplyError

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(port: int) -> ModbusTcpServer:
    # pymodbus data blocks count from 1 internally: start 1 = Modbus address 0
    device = ModbusDeviceContext(
        co=ModbusSequentialDataBlock(1, [False] * 16),
        hr=ModbusSequentialDataBlock(1, [0] * 16),
    )
    server = ModbusTcpServer(ModbusServerContext(devices=device, single=True), address=("127.0.0.1", port))
    await server.serve_forever(background=True)
    return server


async def until(condition, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await condition():
            return True
        await asyncio.sleep(0.05)
    return False


def test_request_without_connection_is_plc_comm_error():
    plc = FillerPlc(port=free_port())  # P1: no running loop needed here
    with pytest.raises(PlcCommError):
        asyncio.run(plc.read_outputs())  # never connected: no network involved
    assert not plc.link_ok and not plc.connected


def test_drop_error_reply_and_reconnect():
    async def scenario() -> None:
        port = free_port()
        server = await start_server(port)
        plc = FillerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)

        # Normal read: link_ok only after an answer, not after the TCP connect
        assert await plc.ensure_connected()
        assert not plc.link_ok
        await plc.read_outputs()
        assert plc.link_ok

        # Error reply (address outside the server's data): the link stays up
        with pytest.raises(PlcReplyError):
            await plc.read_status()
        assert plc.link_ok and plc.connected

        # Server gone ("runtime restart"): PlcCommError, never ConnectionException
        await server.shutdown()
        await asyncio.sleep(0.3)  # let the client process the closed socket (transport -> None) first:
        assert not plc.connected  # now pymodbus would raise "not connected" while CREATING a request
        errors = []

        async def read_fails() -> bool:
            try:
                await plc.read_outputs()
                return False
            except PlcCommError as error:  # ConnectionException would escape and fail the test
                errors.append(error)
                return True

        assert await until(read_fails)
        assert not plc.link_ok

        # Server back on the same port: the client reconnects by itself
        server = await start_server(port)

        async def read_ok() -> bool:
            if not await plc.ensure_connected():
                return False
            try:
                await plc.read_outputs()
                return True
            except PlcCommError:
                return False

        assert await until(read_ok)
        assert plc.link_ok
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_verify_writes_detects_a_second_writer():
    async def scenario() -> None:
        from pymodbus.client import AsyncModbusTcpClient

        from simulator import address_map as am
        from simulator.plant import Plant

        port = free_port()
        device = ModbusDeviceContext(  # big enough for the real sensor block and FillLevel
            co=ModbusSequentialDataBlock(1, [False] * 8300),
            hr=ModbusSequentialDataBlock(1, [0] * 1100),
        )
        server = ModbusTcpServer(ModbusServerContext(devices=device, single=True), address=("127.0.0.1", port))
        await server.serve_forever(background=True)

        plc = FillerPlc(port=port)
        assert await plc.ensure_connected()
        w = Plant().writes()  # healthy defaults: S8, Product_Available, E_Stop_OK ON
        await plc.write(w)
        assert await plc.verify_writes(w) == []  # nobody else writes

        other = AsyncModbusTcpClient("127.0.0.1", port=port)  # e.g. a test script left running
        await other.connect()
        await other.write_coil(am.S8, False)
        assert await plc.verify_writes(w) == ["s8"]

        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
