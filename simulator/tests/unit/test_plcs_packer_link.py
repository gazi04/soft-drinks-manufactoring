"""PackerPlc against a local Modbus server: the shared base behaviour and its own requests.

No PLC, no Docker: pymodbus' own ModbusTcpServer on a free localhost port plays the runtime
(helpers from test_plcs_link.py / test_plcs_syrup_link.py).
"""

import asyncio
import logging

import pytest
from pymodbus.client import AsyncModbusTcpClient
from test_plcs_link import free_port, start_server, until
from test_plcs_syrup_link import start_big_server

from simulator import address_map_packer as pm
from simulator.machines.packer import PackerOutputs, PackerSensors
from simulator.plant import PackerWrites
from simulator.plcs import FillerPlc, LabelerPlc, PackerPlc, PlcCommError, PlcReplyError, SyrupPlc

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def packer_writes(oven_temp: int = 1900, anim: tuple[int, int, int, int] = (0, 0, 0, 0), **on: bool) -> PackerWrites:
    values = dict(bottle_at_gate=False, bottle_counted=False, pusher_home=False, pusher_out=False,
                  pack_at_film=False, film_in_slot=False, film_pulse=False, wrap_home=False, film_empty=False,
                  film_low=False, oven_entry=False, oven_exit=False, table_full=False, airflow_ok=False)
    values.update(on)
    return PackerWrites(PackerSensors(oven_temp=oven_temp, **values), e_stop_ok=True, heartbeat=True, anim=anim)


async def connected(port: int) -> tuple[PackerPlc, AsyncModbusTcpClient]:
    """The client under test plus a second plain client that plays the PLC / HMI / a second writer."""
    plc = PackerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
    assert await plc.ensure_connected()
    other = AsyncModbusTcpClient("127.0.0.1", port=port)
    await other.connect()
    return plc, other


# --- The shared base class ----------------------------------------------------------
def test_request_without_connection_is_plc_comm_error():
    plc = PackerPlc(port=free_port())
    with pytest.raises(PlcCommError):
        asyncio.run(plc.read_outputs())
    assert not plc.link_ok and not plc.connected


def test_an_error_reply_keeps_the_link():
    async def scenario() -> None:
        port = free_port()
        server = await start_server(port)  # only 16 registers: HR 1064 is outside
        plc = PackerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
        assert await plc.ensure_connected()
        with pytest.raises(PlcReplyError):
            await plc.read_status()
        assert plc.link_ok and plc.connected
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_drop_and_reconnect():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc = PackerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
        assert await plc.ensure_connected()
        await plc.read_outputs()
        await server.shutdown()  # "runtime restart"
        await asyncio.sleep(0.3)

        async def read_fails() -> bool:
            try:
                await plc.read_outputs()
                return False
            except PlcCommError:
                return True

        assert await until(read_fails)
        assert not plc.link_ok
        server = await start_big_server(port)

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


def test_default_port_and_ownership():
    assert (FillerPlc().port, SyrupPlc().port, LabelerPlc().port, PackerPlc().port) == (502, 5022, 5023, 5024)
    assert PackerPlc.write_coils == pm.SIM_WRITE_COILS and PackerPlc.write_registers == pm.SIM_WRITE_REGISTERS
    assert PackerPlc.write_registers == {pm.OVEN_TEMP, pm.ANIM_WRAP, pm.ANIM_OVEN, pm.ANIM_TABLE, pm.ANIM_FILM}  # + P0
    assert len(PackerPlc.write_coils) == 11 + 6  # both sensor blocks, nothing between them


# --- PackerPlc requests ---------------------------------------------------------------
def test_write_puts_every_value_on_its_address_and_nothing_else():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, reader = await connected(port)
        await plc.write(packer_writes(oven_temp=1875, anim=(0b110_0000_0000, 0b11, 4, pm.SHEET_UNDER_PACK),
                                      pusher_home=True, film_pulse=True, wrap_home=True, film_low=True,
                                      airflow_ok=True))
        coils = (await reader.read_coils(pm.SENSOR_BLOCK_START, count=11)).bits[:11]
        assert [a for a, on in zip(pm.SENSOR_BLOCK, coils) if on] == [pm.PUSHER_HOME, pm.FILM_PULSE, pm.WRAP_HOME,
                                                                      pm.E_STOP_OK, pm.HEARTBEAT]
        extra = (await reader.read_coils(pm.EXTRA_SENSOR_BLOCK_START, count=8)).bits[:8]
        assert extra == [False, True, False, False, False, True, False, False]  # %MX4.6-4.7 spare: untouched
        assert (await reader.read_holding_registers(pm.OVEN_TEMP, count=2)).registers == [1875, 0]
        assert (await reader.read_holding_registers(pm.ANIM_START, count=5)).registers == [0b110_0000_0000, 0b11, 4,
                                                                                           3, 0]  # %MW74 untouched
        # the HMI's and the PLC's addresses in between are untouched
        assert not any((await reader.read_coils(pm.MAN_C3, count=16)).bits[:16])  # %MX2, %MX3
        assert (await reader.read_holding_registers(pm.CMD, count=2)).registers == [0, 0]
        assert not any((await reader.read_holding_registers(pm.COMPUTED_START, count=14)).registers)
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_read_outputs_and_status_decode():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        await other.write_coils(0, [True, False, False, True, False, False, True, False, True, True])  # the PLC
        assert await plc.read_outputs() == PackerOutputs(conveyor_motor3=True, wrap_conveyor=True, wrap_bar=True,
                                                         oven_blower=True, oven_conveyor=True)
        await other.write_registers(pm.COMPUTED_START, [4, 1, 17, 5])
        await other.write_registers(pm.STATUS_START, [1, 10, 32, 12])
        await other.write_coils(pm.ALARMS_START, [False, False, False, False, True])  # Alm_FilmEmpty
        await other.write_coil(pm.ALM_AIRFLOW_LOST, True)
        await other.write_register(pm.MODE_REQ, 1)
        s = await plc.read_status()
        assert (s.state, s.step, s.wrap_step, s.pack_count, s.mode) == (1, 10, 32, 12, 1)
        assert (s.group_count, s.packs_in_oven, s.feed_pulses, s.feed_count) == (4, 1, 17, 5)
        assert [n for n, on in s.alarms.items() if on] == ["Alm_FilmEmpty", "Alm_AirflowLost"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_verify_writes_detects_a_second_writer_on_each_block():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        w = packer_writes(airflow_ok=True)
        await plc.write(w)
        await other.write_coil(pm.MAN_GATE, True)  # the HMI between the blocks: not a foreign write
        assert await plc.verify_writes(w) == []
        await other.write_coil(pm.PACK_AT_FILM, True)  # e.g. a test script left running
        await other.write_coil(pm.AIRFLOW_OK, False)
        await other.write_register(pm.OVEN_TEMP, 2400)
        assert await plc.verify_writes(w) == ["pack_at_film", "airflow_ok", "oven_temp"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
