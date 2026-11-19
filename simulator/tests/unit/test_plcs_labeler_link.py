"""LabelerPlc against a local Modbus server: the shared base behaviour and its own requests.

No PLC, no Docker: pymodbus' own ModbusTcpServer on a free localhost port plays the runtime
(helpers from test_plcs_link.py / test_plcs_syrup_link.py).
"""

import asyncio
import logging

import pytest
from pymodbus.client import AsyncModbusTcpClient
from test_plcs_link import free_port, start_server, until
from test_plcs_syrup_link import start_big_server

from simulator import address_map_labeler as lm
from simulator.machines.labeler import LabelerOutputs, LabelerSensors
from simulator.plant import LabelerWrites
from simulator.plcs import FillerPlc, LabelerPlc, PlcCommError, PlcReplyError, SyrupPlc

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def labeler_writes(glue_temp: int = 1700, anim: tuple[int, int] = (0, 0), **on: bool) -> LabelerWrites:
    values = dict(bottle_at_gate=False, bottle_in_pocket=False, bell_down=False, label_present=False,
                  c3_full=False, roll_empty=False, label_mark=False, spin_pulse=False, roll_low=False,
                  glue_low=False)
    values.update(on)
    return LabelerWrites(LabelerSensors(glue_temp=glue_temp, **values), e_stop_ok=True, heartbeat=True, anim=anim)


async def connected(port: int) -> tuple[LabelerPlc, AsyncModbusTcpClient]:
    """The client under test plus a second plain client that plays the PLC / HMI / a second writer."""
    plc = LabelerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
    assert await plc.ensure_connected()
    other = AsyncModbusTcpClient("127.0.0.1", port=port)
    await other.connect()
    return plc, other


# --- The shared base class ----------------------------------------------------------
def test_request_without_connection_is_plc_comm_error():
    plc = LabelerPlc(port=free_port())
    with pytest.raises(PlcCommError):
        asyncio.run(plc.read_outputs())
    assert not plc.link_ok and not plc.connected


def test_an_error_reply_keeps_the_link():
    async def scenario() -> None:
        port = free_port()
        server = await start_server(port)  # only 16 registers: HR 1074 is outside
        plc = LabelerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
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
        plc = LabelerPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
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
    assert (FillerPlc().port, SyrupPlc().port, LabelerPlc().port) == (502, 5022, 5023)
    assert LabelerPlc.write_coils == lm.SIM_WRITE_COILS and LabelerPlc.write_registers == lm.SIM_WRITE_REGISTERS
    assert LabelerPlc.write_coils > FillerPlc.write_coils  # the same first block + the %MX4 sensors
    assert LabelerPlc.write_registers == {lm.GLUE_TEMP, lm.ANIM_C3, lm.ANIM_STATION}  # + HMI animation (L0)


# --- LabelerPlc requests ---------------------------------------------------------------
def test_write_puts_every_value_on_its_address():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, reader = await connected(port)
        await plc.write(labeler_writes(glue_temp=1650, anim=(0b1000_0001, lm.POCKET_LABELLING), bell_down=True,
                                       spin_pulse=True, glue_low=True))
        coils = (await reader.read_coils(lm.SENSOR_BLOCK_START, count=11)).bits[:11]
        assert [a for a, on in zip(lm.SENSOR_BLOCK, coils) if on] == [lm.BELL_DOWN, lm.SPIN_PULSE, lm.E_STOP_OK,
                                                                      lm.HEARTBEAT]
        assert (await reader.read_coils(lm.ROLL_LOW, count=2)).bits[:2] == [False, True]
        assert (await reader.read_holding_registers(lm.GLUE_TEMP, count=1)).registers == [1650]
        assert (await reader.read_holding_registers(lm.ANIM_START, count=2)).registers == [0b1000_0001, 2]
        # the PLC's status words right after the analog sensor stay untouched
        assert (await reader.read_holding_registers(lm.STATUS_START, count=4)).registers == [0, 0, 0, 0]
        # the HMI's and the PLC's addresses in between are untouched
        assert not any((await reader.read_coils(lm.MAN_C2, count=16)).bits[:16])  # %MX2, %MX3
        assert (await reader.read_holding_registers(lm.CMD, count=2)).registers == [0, 0]
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_read_outputs_and_status_decode():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        await other.write_coils(0, [True, False, True, True, False, False, False, True, True])  # plays the PLC
        assert await plc.read_outputs() == LabelerOutputs(conveyor_motor2=True, clamp_valve=True, spin_motor=True,
                                                          glue_heater=True, discharge_motor=True)
        await other.write_registers(lm.STATUS_START, [3, 35, 0, 17])
        await other.write_coils(lm.ALARMS_START, [False, False, True, False, False, False, True])
        await other.write_register(lm.MODE_REQ, 1)
        s = await plc.read_status()
        assert (s.state, s.step, s.good_count, s.mode) == (3, 35, 17, 1)
        assert [n for n, on in s.alarms.items() if on] == ["Alm_OutfeedBlocked", "Alm_GlueHeatTimeout"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_verify_writes_detects_a_second_writer_on_each_block():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        w = labeler_writes(roll_low=True)
        await plc.write(w)
        assert await plc.verify_writes(w) == []  # nobody else writes
        await other.write_coil(lm.C3_FULL, True)  # e.g. a test script left running
        await other.write_coil(lm.ROLL_LOW, False)
        await other.write_register(lm.GLUE_TEMP, 2100)
        assert await plc.verify_writes(w) == ["c3_full", "roll_low", "glue_temp"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
