"""BlowmoulderPlc against a local Modbus server: the shared base behaviour and its own requests.

No PLC, no Docker: pymodbus' own ModbusTcpServer on a free localhost port plays the runtime
(helpers from test_plcs_link.py / test_plcs_syrup_link.py).
"""

import asyncio
import logging

import pytest
from pymodbus.client import AsyncModbusTcpClient
from test_plcs_link import free_port, start_server
from test_plcs_syrup_link import start_big_server

from simulator import address_map_blowmoulder as bm
from simulator.machines.blowmoulder import BlowmoulderOutputs, BlowmoulderSensors
from simulator.plant import BlowmoulderWrites
from simulator.plcs import BlowmoulderPlc, PackerPlc, PlcCommError, PlcReplyError, to_register

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def bm_writes(oven_temp: int = 1050, blow_pressure_raw: int = 0, **on: bool) -> BlowmoulderWrites:
    values = dict(preform_at_loader=False, preform_in_oven=False, arm_at_oven=False, arm_at_mould=False,
                  mould_closed=False, mould_open=False, rod_up=False, rod_down=False, bottle_in_mould=False,
                  bottle_ejected=False, hopper_empty=False, preform_low=False, c1_full=False)
    values.update(on)
    sensors = BlowmoulderSensors(oven_temp=oven_temp, blow_pressure_raw=blow_pressure_raw, **values)
    return BlowmoulderWrites(sensors, e_stop_ok=True, heartbeat=True)


async def connected(port: int) -> tuple[BlowmoulderPlc, AsyncModbusTcpClient]:
    """The client under test plus a second plain client that plays the PLC / HMI / a second writer."""
    plc = BlowmoulderPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
    assert await plc.ensure_connected()
    other = AsyncModbusTcpClient("127.0.0.1", port=port)
    await other.connect()
    return plc, other


def test_request_without_connection_is_plc_comm_error():
    plc = BlowmoulderPlc(port=free_port())
    with pytest.raises(PlcCommError):
        asyncio.run(plc.read_outputs())
    assert not plc.link_ok and not plc.connected


def test_an_error_reply_keeps_the_link():
    async def scenario() -> None:
        port = free_port()
        server = await start_server(port)  # only 16 registers: HR 1064 is outside
        plc = BlowmoulderPlc(port=port, reconnect_s=0.1, timeout_s=0.5)
        assert await plc.ensure_connected()
        with pytest.raises(PlcReplyError):
            await plc.read_status()
        assert plc.link_ok and plc.connected
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_default_port_and_ownership():
    assert (PackerPlc().port, BlowmoulderPlc().port) == (5024, 5025)
    assert BlowmoulderPlc.write_coils == bm.SIM_WRITE_COILS and BlowmoulderPlc.write_registers == bm.SIM_WRITE_REGISTERS
    assert BlowmoulderPlc.write_registers == {bm.OVEN_TEMP, bm.BLOW_PRESSURE_RAW}  # animation words: HMI part C
    assert len(BlowmoulderPlc.write_coils) == 11 + 5  # both sensor blocks, nothing between them


def test_lamp_power_hr0_can_never_be_written():
    plc = BlowmoulderPlc(port=free_port())
    with pytest.raises(ValueError, match="may not write register 0"):
        asyncio.run(plc._write_register(bm.LAMP_POWER, 50))  # the guard fires before the link check


def test_write_puts_every_value_on_its_address_and_nothing_else():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, reader = await connected(port)
        await plc.write(bm_writes(oven_temp=1021, blow_pressure_raw=-2000, arm_at_oven=True, mould_open=True,
                                  rod_up=True, hopper_empty=True, c1_full=True))
        coils = (await reader.read_coils(bm.SENSOR_BLOCK_START, count=11)).bits[:11]
        assert [a for a, on in zip(bm.SENSOR_BLOCK, coils) if on] == [bm.ARM_AT_OVEN, bm.MOULD_OPEN, bm.ROD_UP,
                                                                      bm.E_STOP_OK, bm.HEARTBEAT]
        extra = (await reader.read_coils(bm.EXTRA_SENSOR_BLOCK_START, count=8)).bits[:8]
        assert extra == [False, False, True, False, True, False, False, False]  # %MX4.5-4.7 spare: untouched
        assert (await reader.read_holding_registers(bm.OVEN_TEMP, count=3)).registers == [1021, 63536, 0]
        # the PLC's analog output, the HMI's and the PLC's addresses in between are untouched
        assert (await reader.read_holding_registers(bm.LAMP_POWER, count=1)).registers == [0]
        assert not any((await reader.read_coils(bm.MAN_HOPPER_FEED, count=16)).bits[:16])  # %MX2, %MX3
        assert (await reader.read_holding_registers(bm.CMD, count=2)).registers == [0, 0]
        assert not any((await reader.read_holding_registers(bm.COMPUTED_START, count=14)).registers)
        reader.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_read_outputs_includes_lamp_power_and_status_decodes_signed():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        await other.write_coils(0, [True, True, False, False, True, False, False, False, True, False])  # the PLC
        await other.write_register(bm.LAMP_POWER, 37)
        assert await plc.read_outputs() == BlowmoulderOutputs(hopper_feed=True, loader=True, mould_close=True,
                                                              exhaust_valve=True, lamp_power=37)
        await other.write_registers(bm.COMPUTED_START, [100, to_register(-7), to_register(-55)])
        await other.write_registers(bm.STATUS_START, [3, 25, 80, 42])
        await other.write_coil(bm.ALM_OUTFEED_BLOCKED, True)
        await other.write_coil(bm.ALM_EXHAUST_FAULT, True)
        await other.write_register(bm.MODE_REQ, 1)
        s = await plc.read_status()
        assert (s.state, s.step, s.mould_step, s.bottle_count, s.mode) == (3, 25, 80, 42, 1)
        assert (s.lamp_calc, s.blow_pressure, s.oven_error) == (100, -7, -55)
        assert [n for n, on in s.alarms.items() if on] == ["Alm_OutfeedBlocked", "Alm_ExhaustFault"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())


def test_verify_writes_detects_a_second_writer_on_each_block():
    async def scenario() -> None:
        port = free_port()
        server = await start_big_server(port)
        plc, other = await connected(port)
        w = bm_writes(blow_pressure_raw=-500, arm_at_oven=True, preform_low=True)
        await plc.write(w)
        await other.write_coil(bm.MAN_GRIPPER, True)  # the HMI between the blocks: not a foreign write
        assert await plc.verify_writes(w) == []
        await other.write_coil(bm.MOULD_CLOSED, True)  # e.g. a test script left running
        await other.write_coil(bm.PREFORM_LOW, False)
        await other.write_register(bm.BLOW_PRESSURE_RAW, 24192)
        assert await plc.verify_writes(w) == ["mould_closed", "preform_low", "blow_pressure_raw"]
        other.close()
        plc.close()
        await server.shutdown()

    asyncio.run(scenario())
