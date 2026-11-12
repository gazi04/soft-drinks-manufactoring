"""Syrup simulator stage 0: the address map matches docs/syrup/io-list.md and the one-writer rule.

Pure Python, no PLC needed:  uv run pytest
"""

import re
from pathlib import Path

from simulator import address_map as filler
from simulator import address_map_syrup as sm

IO_LIST = Path(__file__).parents[3] / "docs" / "syrup" / "io-list.md"

# io-list name -> constant of the module. The doc guard below compares every entry with the table.
NAMED = {
    "WaterPump": sm.WATER_PUMP, "SyrupPump": sm.SYRUP_PUMP, "Agitator": sm.AGITATOR,
    "CoolingValve": sm.COOLING_VALVE, "CO2Valve": sm.CO2_VALVE, "TransferPump": sm.TRANSFER_PUMP,
    "MixHighHigh": sm.MIX_HIGH_HIGH, "E_Stop_OK": sm.E_STOP_OK, "Heartbeat": sm.HEARTBEAT,
    "SyrupLevel": sm.SYRUP_LEVEL, "MixLevel": sm.MIX_LEVEL, "ProductTemp": sm.PRODUCT_TEMP,
    "CO2Pressure": sm.CO2_PRESSURE, "BufferLevel": sm.BUFFER_LEVEL,
    "Product_Available": sm.PRODUCT_AVAILABLE_OUT,
    "State": sm.STATE, "Step": sm.STEP, "BatchCount": sm.BATCH_COUNT,
    "Alm_Overflow": sm.ALM_OVERFLOW, "Alm_OverPressure": sm.ALM_OVER_PRESSURE,
    "Alm_CoolTimeout": sm.ALM_COOL_TIMEOUT, "Alm_CommLost": sm.ALM_COMM_LOST,
    "Alm_LowSyrup": sm.ALM_LOW_SYRUP, "Alm_DoseTimeout": sm.ALM_DOSE_TIMEOUT,
    "Temp_OK": sm.TEMP_OK, "CO2_Demand": sm.CO2_DEMAND, "Jog_Block": sm.JOG_BLOCK,
    "Batch_Dosed": sm.BATCH_DOSED, "SyrupTarget": sm.SYRUP_TARGET, "DoseTarget": sm.DOSE_TARGET,
    "Cmd": sm.CMD, "ModeReq": sm.MODE_REQ,
    "WaterBatch": sm.WATER_BATCH, "SyrupRatio": sm.SYRUP_RATIO, "MixTime": sm.MIX_TIME,
    "CoolSetpoint": sm.COOL_SETPOINT, "TempHyst": sm.TEMP_HYST, "PressSetpoint": sm.PRESS_SETPOINT,
    "PressHyst": sm.PRESS_HYST, "BufferLow": sm.BUFFER_LOW, "BufferMin": sm.BUFFER_MIN,
    "CarbTime": sm.CARB_TIME,
    "Man_WaterPump": sm.MAN_WATER_PUMP, "Man_SyrupPump": sm.MAN_SYRUP_PUMP,
    "Man_Agitator": sm.MAN_AGITATOR, "Man_Cooling": sm.MAN_COOLING, "Man_CO2": sm.MAN_CO2,
    "Man_Transfer": sm.MAN_TRANSFER,
}  # fmt: skip


PLACEHOLDER_ROWS = {"–"}  # io-list rows for spare addresses; they have no name and no constant


def doc_addresses() -> dict[str, tuple[str, int]]:
    """Rows of io-list.md whose Modbus cell is exactly 'coil N' or 'HR N': name -> (kind, N)."""
    found = {}
    for line in IO_LIST.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        match = re.fullmatch(r"(coil|HR) (\d+)", cells[3])
        if match:
            assert cells[0] not in found, f"{cells[0]} appears twice in io-list.md"
            found[cells[0]] = (match.group(1), int(match.group(2)))
    return found


def test_mapping_anchors():
    assert sm.qx(0, 0) == 0 and sm.mx(0, 0) == 8192 and sm.mw(0) == 1024  # the same OpenPLC v4 mapping


def test_numbers_match_io_list():
    assert (sm.WATER_PUMP, sm.TRANSFER_PUMP) == (0, 5)
    assert (sm.MIX_HIGH_HIGH, sm.E_STOP_OK, sm.HEARTBEAT) == (8192, 8201, 8202)
    assert (sm.SYRUP_LEVEL, sm.MIX_LEVEL, sm.PRODUCT_TEMP, sm.CO2_PRESSURE, sm.BUFFER_LEVEL) == tuple(
        range(1036, 1041)
    )
    assert sm.PRODUCT_AVAILABLE_OUT == 8240
    assert (sm.STATE, sm.STEP, sm.BATCH_COUNT) == (1074, 1075, 1077)
    assert (sm.ALM_OVERFLOW, sm.ALM_DOSE_TIMEOUT) == (8232, 8237)
    assert (sm.TEMP_OK, sm.BATCH_DOSED, sm.SYRUP_TARGET, sm.DOSE_TARGET) == (8216, 8219, 1064, 1065)
    assert (sm.CMD, sm.MODE_REQ, sm.WATER_BATCH, sm.CARB_TIME) == (1024, 1025, 1054, 1063)
    assert (sm.MAN_WATER_PUMP, sm.MAN_TRANSFER) == (8208, 8213)


def test_same_alarm_bit_as_filler():
    assert sm.ALM_COMM_LOST == filler.ALM_COMM_LOST == 8235  # "Alm_CommLost = %MX5.3 in every PLC"


def test_handshake_pair():
    # stage 3 copies the first to the second every tick
    assert (sm.PRODUCT_AVAILABLE_OUT, filler.PRODUCT_AVAILABLE) == (8240, 8200)


def test_sensor_blocks_are_contiguous():
    assert sm.SENSOR_BLOCK[0] == sm.SENSOR_BLOCK_START == 8192
    assert len(sm.SENSOR_BLOCK) == 11
    assert list(sm.SENSOR_BLOCK) == list(range(8192, 8203))  # no gap, no overlap
    assert max(sm.SENSOR_BLOCK) < sm.MAN_WATER_PUMP  # never reaches the manual buttons
    assert sm.UNUSED_MX1_0 in sm.SENSOR_BLOCK and sm.UNUSED_MX1_0 == 8200
    assert (sm.ANALOG_START, sm.ANALOG_COUNT) == (1036, 5)


def test_block_sizes():
    assert (sm.OUTPUTS_START, sm.OUTPUTS_COUNT) == (0, 6)
    assert (sm.STATUS_START, sm.STATUS_COUNT) == (1074, 4)
    assert (sm.ALARMS_START, sm.ALARMS_COUNT) == (8232, 6)  # 6 alarms, the filler has 4
    assert (sm.SETPOINTS_START, sm.SETPOINTS_COUNT) == (1054, 10)


def test_simulator_never_writes_plc_or_hmi_addresses():
    plc_coils = set(range(sm.OUTPUTS_START, sm.OUTPUTS_START + sm.OUTPUTS_COUNT))
    plc_coils |= set(range(sm.ALARMS_START, sm.ALARMS_START + sm.ALARMS_COUNT))
    plc_coils |= {sm.PRODUCT_AVAILABLE_OUT, sm.TEMP_OK, sm.CO2_DEMAND, sm.JOG_BLOCK, sm.BATCH_DOSED}
    hmi_coils = {sm.MAN_WATER_PUMP, sm.MAN_SYRUP_PUMP, sm.MAN_AGITATOR, sm.MAN_COOLING, sm.MAN_CO2, sm.MAN_TRANSFER}
    plc_registers = set(range(sm.STATUS_START, sm.STATUS_START + sm.STATUS_COUNT))
    plc_registers |= {sm.SYRUP_TARGET, sm.DOSE_TARGET}
    hmi_registers = {sm.CMD, sm.MODE_REQ} | set(range(sm.SETPOINTS_START, sm.SETPOINTS_START + sm.SETPOINTS_COUNT))

    assert sm.SIM_WRITE_COILS == set(sm.SENSOR_BLOCK)
    assert not sm.SIM_WRITE_COILS & (plc_coils | hmi_coils)
    assert sm.SIM_WRITE_REGISTERS == set(range(1036, 1041))
    assert not sm.SIM_WRITE_REGISTERS & (plc_registers | hmi_registers)


def test_no_two_names_share_an_address():
    # registers are HR 1024..8191; everything else is a coil (%QX 0..1023 or %MX from 8192)
    registers = [a for a in NAMED.values() if 1024 <= a < 8192]
    coils = [a for a in NAMED.values() if not 1024 <= a < 8192]
    assert len(registers) == len(set(registers)) and len(coils) == len(set(coils))


def test_matches_io_list_document():
    doc = doc_addresses()
    assert doc, "no table rows parsed from io-list.md"
    for name, address in NAMED.items():
        assert name in doc, f"{name} not found in io-list.md"
        kind, number = doc[name]
        assert number == address, f"{name}: code {address}, io-list.md {kind} {number}"
        assert kind == ("HR" if 1024 <= address < 8192 else "coil"), f"{name}: wrong kind {kind}"


def test_every_io_list_row_has_a_constant():
    # the other direction: a new row in io-list.md must get a constant and an entry in NAMED
    missing = set(doc_addresses()) - set(NAMED) - PLACEHOLDER_ROWS
    assert not missing, f"io-list.md rows without a constant in address_map_syrup.py: {sorted(missing)}"
