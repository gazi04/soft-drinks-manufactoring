"""Blow moulder simulator stage 0: the address map matches docs/blowmoulder/io-list.md, the PLC
program (plcs/blowmoulder/pous/programs/main.ld) and the one-writer rule.

Pure Python, no PLC needed:  uv run pytest
"""

import re
from pathlib import Path

from simulator import address_map as filler
from simulator import address_map_blowmoulder as bm
from simulator import address_map_labeler as labeler
from simulator import address_map_packer as packer
from simulator import address_map_syrup as syrup

ROOT = Path(__file__).parents[3]
IO_LIST = ROOT / "docs" / "blowmoulder" / "io-list.md"
MAIN_LD = ROOT / "plcs" / "blowmoulder" / "pous" / "programs" / "main.ld"

# io-list / PLC variable name -> constant of the module. The guards below compare every entry
# with the io-list tables and with the located variables of the PLC program.
NAMED = {
    "HopperFeed": bm.HOPPER_FEED, "Loader": bm.LOADER, "ArmToMould": bm.ARM_TO_MOULD, "Gripper": bm.GRIPPER,
    "MouldClose": bm.MOULD_CLOSE, "StretchRod": bm.STRETCH_ROD, "PreBlowValve": bm.PRE_BLOW_VALVE,
    "HighBlowValve": bm.HIGH_BLOW_VALVE, "ExhaustValve": bm.EXHAUST_VALVE, "Ejector": bm.EJECTOR,
    "LampPower": bm.LAMP_POWER,
    "Preform_At_Loader": bm.PREFORM_AT_LOADER, "Preform_In_Oven": bm.PREFORM_IN_OVEN,
    "Arm_At_Oven": bm.ARM_AT_OVEN, "Arm_At_Mould": bm.ARM_AT_MOULD, "Mould_Closed": bm.MOULD_CLOSED,
    "Mould_Open": bm.MOULD_OPEN, "Rod_Up": bm.ROD_UP, "Rod_Down": bm.ROD_DOWN,
    "Bottle_In_Mould": bm.BOTTLE_IN_MOULD, "Bottle_Ejected": bm.BOTTLE_EJECTED, "Hopper_Empty": bm.HOPPER_EMPTY,
    "Preform_Low": bm.PREFORM_LOW, "C1_Full": bm.C1_FULL,
    "E_Stop_OK": bm.E_STOP_OK, "Heartbeat": bm.HEARTBEAT,
    "OvenTemp": bm.OVEN_TEMP, "BlowPressure_Raw": bm.BLOW_PRESSURE_RAW,
    "State": bm.STATE, "Step": bm.STEP, "MouldStep": bm.MOULD_STEP, "BottleCount": bm.BOTTLE_COUNT,
    "Lamp_Calc": bm.LAMP_CALC, "BlowPressure": bm.BLOW_PRESSURE, "OvenError": bm.OVEN_ERROR,
    "Alm_NoPreforms": bm.ALM_NO_PREFORMS, "Alm_HandlingJam": bm.ALM_HANDLING_JAM,
    "Alm_OutfeedBlocked": bm.ALM_OUTFEED_BLOCKED, "Alm_CommLost": bm.ALM_COMM_LOST,
    "Alm_MouldFault": bm.ALM_MOULD_FAULT, "Alm_OvenOverTemp": bm.ALM_OVEN_OVER_TEMP,
    "Alm_OvenHeatTimeout": bm.ALM_OVEN_HEAT_TIMEOUT, "Alm_BlowFault": bm.ALM_BLOW_FAULT,
    "Alm_ExhaustFault": bm.ALM_EXHAUST_FAULT, "Alm_OverPressure": bm.ALM_OVER_PRESSURE,
    "Alm_PressureSensor": bm.ALM_PRESSURE_SENSOR,
    "Outfeed_Full": bm.OUTFEED_FULL, "Oven_Ready": bm.OVEN_READY, "Jog_Block": bm.JOG_BLOCK,
    "Preform_Hot": bm.PREFORM_HOT, "Pressure_Reached": bm.PRESSURE_REACHED, "Pressure_Safe": bm.PRESSURE_SAFE,
    "Starved": bm.STARVED, "Lamps_On": bm.LAMPS_ON,
    "Cmd": bm.CMD, "ModeReq": bm.MODE_REQ,
    "OvenSetpoint": bm.OVEN_SETPOINT, "OvenBand": bm.OVEN_BAND, "Kp": bm.KP, "Bias": bm.BIAS,
    "BlowSetpoint": bm.BLOW_SETPOINT,
    "Man_HopperFeed": bm.MAN_HOPPER_FEED, "Man_Loader": bm.MAN_LOADER, "Man_Arm": bm.MAN_ARM,
    "Man_Gripper": bm.MAN_GRIPPER, "Man_MouldClose": bm.MAN_MOULD_CLOSE, "Man_Rod": bm.MAN_ROD,
    "Man_Ejector": bm.MAN_EJECTOR,
}  # fmt: skip

# Holding registers (%MW and %QW); every other name is a coil. Needed because LampPower is
# HR 0 and HopperFeed coil 0: the number alone does not tell the table any more.
REGISTERS = {
    "LampPower", "OvenTemp", "BlowPressure_Raw", "State", "Step", "MouldStep", "BottleCount",
    "Lamp_Calc", "BlowPressure", "OvenError", "Cmd", "ModeReq",
    "OvenSetpoint", "OvenBand", "Kp", "Bias", "BlowSetpoint",
}  # fmt: skip

PLACEHOLDER_ROWS = {"–"}  # io-list rows for spare / unused addresses; no name, no constant
CONVERTERS = {"QX": bm.qx, "MX": bm.mx, "MW": bm.mw, "QW": bm.qw}


def doc_addresses() -> dict[str, tuple[str, int]]:
    """Rows of io-list.md whose Modbus cell starts with 'coil N' or 'HR N': name -> (kind, N).

    'starts with': LampPower's cell is 'HR 0 (verified 2026-11-25)'. Placeholder rows are
    skipped (%MX1.0 unused, the spare ranges).
    """
    found = {}
    for line in IO_LIST.read_text(encoding="utf-8").splitlines():
        cells = [c.strip().replace("**", "") for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in PLACEHOLDER_ROWS:
            continue
        match = re.match(r"(coil|HR) (\d+)(?: \(|$)", cells[3])
        if match:
            assert cells[0] not in found, f"{cells[0]} appears twice in io-list.md"
            found[cells[0]] = (match.group(1), int(match.group(2)))
    return found


def plc_addresses() -> dict[str, tuple[str, int]]:
    """Located variables of the PLC program ('Name : TYPE AT %MX0.0 ...') -> (kind, Modbus number)."""
    found = {}
    pattern = re.compile(r"^\s*(\w+) : \w+ AT %(QX|MX|MW|QW)(\d+)(?:\.(\d+))?")
    for line in MAIN_LD.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            name, area, first, bit = match.groups()
            assert name not in found, f"{name} declared twice in main.ld"
            convert = CONVERTERS[area]
            if area in ("MW", "QW"):
                found[name] = ("HR", convert(int(first)))
            else:
                found[name] = ("coil", convert(int(first), int(bit)))
    return found


def kind_of(name: str) -> str:
    return "HR" if name in REGISTERS else "coil"


def test_mapping_anchors():
    assert bm.qx(0, 0) == 0 and bm.mx(0, 0) == 8192 and bm.mw(0) == 1024  # the same OpenPLC v4 mapping
    assert bm.qw(0) == 0  # %QW0 = HR 0, verified on the PLC (ladder stage 0, 2026-11-25)
    assert bm.qw(1) < bm.mw(0)  # the %QW words sit below the %MW offset


def test_numbers_match_io_list():
    assert (bm.HOPPER_FEED, bm.HIGH_BLOW_VALVE, bm.EXHAUST_VALVE, bm.EJECTOR) == (0, 7, 8, 9)
    assert bm.LAMP_POWER == 0
    assert (bm.PREFORM_AT_LOADER, bm.ROD_DOWN, bm.E_STOP_OK, bm.HEARTBEAT) == (8192, 8199, 8201, 8202)
    assert (bm.BOTTLE_IN_MOULD, bm.C1_FULL) == (8224, 8228)
    assert (bm.OVEN_TEMP, bm.BLOW_PRESSURE_RAW) == (1036, 1037)
    assert (bm.STATE, bm.STEP, bm.MOULD_STEP, bm.BOTTLE_COUNT) == (1074, 1075, 1076, 1077)
    assert (bm.LAMP_CALC, bm.BLOW_PRESSURE, bm.OVEN_ERROR) == (1064, 1065, 1066)
    assert (bm.ALM_NO_PREFORMS, bm.ALM_BLOW_FAULT) == (8232, 8239)
    assert (bm.ALM_EXHAUST_FAULT, bm.ALM_PRESSURE_SENSOR) == (8248, 8250)
    assert (bm.OUTFEED_FULL, bm.OVEN_READY, bm.LAMPS_ON) == (8216, 8217, 8223)
    assert (bm.CMD, bm.MODE_REQ, bm.OVEN_SETPOINT, bm.BLOW_SETPOINT) == (1024, 1025, 1054, 1058)
    assert (bm.MAN_HOPPER_FEED, bm.MAN_EJECTOR) == (8208, 8214)


def test_scaling_constants_match_the_plc():
    # rule 6: BlowPressure = Raw * 400 / 27648; 24192 counts = 35.0 bar
    assert (bm.RAW_FULL, bm.PRESS_FULL) == (27648, 400)
    assert 24192 * bm.PRESS_FULL // bm.RAW_FULL == 350


def test_same_alarm_bit_in_every_plc():
    assert bm.ALM_COMM_LOST == filler.ALM_COMM_LOST == syrup.ALM_COMM_LOST == labeler.ALM_COMM_LOST
    assert bm.ALM_COMM_LOST == packer.ALM_COMM_LOST == 8235


def test_same_bits_as_the_other_machines_for_the_same_job():
    # report 6.2: the standard layout, so simulator and HMI code can be shared
    assert bm.OUTFEED_FULL == filler.OUTFEED_FULL == packer.OUTFEED_FULL
    assert bm.JOG_BLOCK == filler.JOG_BLOCK and bm.UNUSED_MX1_0 == filler.PRODUCT_AVAILABLE
    assert (bm.E_STOP_OK, bm.HEARTBEAT) == (filler.E_STOP_OK, filler.HEARTBEAT)
    assert bm.ALM_OUTFEED_BLOCKED == packer.ALM_OUTFEED_BLOCKED
    assert bm.OVEN_TEMP == packer.OVEN_TEMP  # the first analog sensor sits in %MW12 everywhere
    assert (bm.SETPOINTS_START, bm.STATUS_START) == (packer.SETPOINTS_START, packer.STATUS_START)
    assert (bm.STEP, bm.MOULD_STEP) == (packer.STEP, packer.WRAP_STEP)  # two sequencers as the packer
    assert bm.EXTRA_ALARMS_START == packer.EXTRA_ALARMS_START


def test_sensor_blocks_are_contiguous():
    assert bm.SENSOR_BLOCK[0] == bm.SENSOR_BLOCK_START == 8192
    assert list(bm.SENSOR_BLOCK) == list(range(8192, 8203))  # no gap, no overlap
    assert max(bm.SENSOR_BLOCK) < bm.MAN_HOPPER_FEED  # never reaches the manual buttons
    assert bm.UNUSED_MX1_0 in bm.SENSOR_BLOCK and bm.UNUSED_MX1_0 == 8200

    assert bm.EXTRA_SENSOR_BLOCK[0] == bm.EXTRA_SENSOR_BLOCK_START == 8224
    assert list(bm.EXTRA_SENSOR_BLOCK) == list(range(8224, 8229))
    # between the conditioned bits and the alarms, touching neither
    assert bm.CONDITIONED_START + bm.CONDITIONED_COUNT <= min(bm.EXTRA_SENSOR_BLOCK)
    assert max(bm.EXTRA_SENSOR_BLOCK) < bm.ALARMS_START
    assert (bm.ANALOG_START, bm.ANALOG_COUNT) == (1036, 2)


def test_block_sizes():
    assert (bm.OUTPUTS_START, bm.OUTPUTS_COUNT) == (0, 10)
    assert (bm.STATUS_START, bm.STATUS_COUNT) == (1074, 4)
    assert (bm.COMPUTED_START, bm.COMPUTED_COUNT) == (1064, 3)
    assert (bm.ALARMS_START, bm.ALARMS_COUNT) == (8232, 8)  # all of %MX5
    assert (bm.EXTRA_ALARMS_START, bm.EXTRA_ALARMS_COUNT) == (8248, 3)  # %MX7.0-7.2
    assert (bm.CONDITIONED_START, bm.CONDITIONED_COUNT) == (8216, 8)
    assert (bm.SETPOINTS_START, bm.SETPOINTS_COUNT) == (1054, 5)


def test_simulator_never_writes_plc_or_hmi_addresses():
    plc_coils = set(range(bm.OUTPUTS_START, bm.OUTPUTS_START + bm.OUTPUTS_COUNT))
    plc_coils |= set(range(bm.ALARMS_START, bm.ALARMS_START + bm.ALARMS_COUNT))
    plc_coils |= set(range(bm.EXTRA_ALARMS_START, bm.EXTRA_ALARMS_START + bm.EXTRA_ALARMS_COUNT))
    plc_coils |= set(range(bm.CONDITIONED_START, bm.CONDITIONED_START + bm.CONDITIONED_COUNT))
    hmi_coils = {bm.MAN_HOPPER_FEED, bm.MAN_LOADER, bm.MAN_ARM, bm.MAN_GRIPPER, bm.MAN_MOULD_CLOSE,
                 bm.MAN_ROD, bm.MAN_EJECTOR}
    plc_registers = {bm.LAMP_POWER} | set(range(bm.STATUS_START, bm.STATUS_START + bm.STATUS_COUNT))
    plc_registers |= set(range(bm.COMPUTED_START, bm.COMPUTED_START + bm.COMPUTED_COUNT))
    hmi_registers = {bm.CMD, bm.MODE_REQ} | set(range(bm.SETPOINTS_START, bm.SETPOINTS_START + bm.SETPOINTS_COUNT))

    assert bm.SIM_WRITE_COILS == set(bm.SENSOR_BLOCK) | set(bm.EXTRA_SENSOR_BLOCK)
    assert not bm.SIM_WRITE_COILS & (plc_coils | hmi_coils)
    assert bm.SIM_WRITE_REGISTERS == {1036, 1037}  # OvenTemp + BlowPressure_Raw (animation words: part C)
    assert not bm.SIM_WRITE_REGISTERS & (plc_registers | hmi_registers)
    assert 0 not in bm.SIM_WRITE_REGISTERS  # never LampPower (HR 0)


def test_no_two_names_share_an_address():
    registers = [a for n, a in NAMED.items() if kind_of(n) == "HR"]
    coils = [a for n, a in NAMED.items() if kind_of(n) == "coil"]
    assert len(registers) == len(set(registers)) and len(coils) == len(set(coils))
    assert all(a < 8192 for a in registers) and all(a < 1024 or a >= 8192 for a in coils)


def test_matches_io_list_document():
    doc = doc_addresses()
    assert doc, "no table rows parsed from io-list.md"
    for name, address in NAMED.items():
        assert name in doc, f"{name} not found in io-list.md"
        kind, number = doc[name]
        assert number == address, f"{name}: code {address}, io-list.md {kind} {number}"
        assert kind == kind_of(name), f"{name}: wrong kind {kind}"


def test_every_io_list_row_has_a_constant():
    # the other direction: a new row in io-list.md must get a constant and an entry in NAMED
    missing = set(doc_addresses()) - set(NAMED)
    assert not missing, f"io-list.md rows without a constant in address_map_blowmoulder.py: {sorted(missing)}"


def test_matches_plc_program():
    # the uploaded program is the final truth: a variable moved in the Editor but not in the
    # io-list (or the other way round) shows up here
    plc = plc_addresses()
    assert len(plc) == len(NAMED) == 68
    for name, address in NAMED.items():
        assert name in plc, f"{name} is not a located variable in main.ld"
        assert plc[name] == (kind_of(name), address), f"{name}: code {kind_of(name)} {address}, main.ld {plc[name]}"
    extra = set(plc) - set(NAMED)
    assert not extra, f"located variables in main.ld without a constant: {sorted(extra)}"
