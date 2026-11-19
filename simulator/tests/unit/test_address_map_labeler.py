"""Labeler simulator stage 0: the address map matches docs/labeler/io-list.md, the PLC program
(plcs/labeler/pous/programs/main.ld) and the one-writer rule.

Pure Python, no PLC needed:  uv run pytest
"""

import re
from pathlib import Path

from simulator import address_map as filler
from simulator import address_map_labeler as lm
from simulator import address_map_syrup as syrup

ROOT = Path(__file__).parents[3]
IO_LIST = ROOT / "docs" / "labeler" / "io-list.md"
MAIN_LD = ROOT / "plcs" / "labeler" / "pous" / "programs" / "main.ld"

# io-list / PLC variable name -> constant of the module. The guards below compare every entry
# with the io-list tables and with the located variables of the PLC program.
NAMED = {
    "ConveyorMotor2": lm.CONVEYOR_MOTOR2, "GateMotor": lm.GATE_MOTOR, "ClampValve": lm.CLAMP_VALVE,
    "SpinMotor": lm.SPIN_MOTOR, "LabelFeed": lm.LABEL_FEED, "Cutter": lm.CUTTER,
    "GlueRoller": lm.GLUE_ROLLER, "GlueHeater": lm.GLUE_HEATER, "DischargeMotor": lm.DISCHARGE_MOTOR,
    "Bottle_At_Gate": lm.BOTTLE_AT_GATE, "Bottle_In_Pocket": lm.BOTTLE_IN_POCKET,
    "Bell_Down": lm.BELL_DOWN, "Label_Present": lm.LABEL_PRESENT, "C3_Full": lm.C3_FULL,
    "Roll_Empty": lm.ROLL_EMPTY, "Label_Mark": lm.LABEL_MARK, "Spin_Pulse": lm.SPIN_PULSE,
    "Roll_Low": lm.ROLL_LOW, "Glue_Low": lm.GLUE_LOW,
    "E_Stop_OK": lm.E_STOP_OK, "Heartbeat": lm.HEARTBEAT,
    "GlueTemp": lm.GLUE_TEMP,
    "State": lm.STATE, "Step": lm.STEP, "GoodCount": lm.GOOD_COUNT,
    "Alm_LabelTimeout": lm.ALM_LABEL_TIMEOUT, "Alm_PocketJam": lm.ALM_POCKET_JAM,
    "Alm_OutfeedBlocked": lm.ALM_OUTFEED_BLOCKED, "Alm_CommLost": lm.ALM_COMM_LOST,
    "Alm_LabelEmpty": lm.ALM_LABEL_EMPTY, "Alm_GlueOverTemp": lm.ALM_GLUE_OVER_TEMP,
    "Alm_GlueHeatTimeout": lm.ALM_GLUE_HEAT_TIMEOUT,
    "Outfeed_Full": lm.OUTFEED_FULL, "Bottle_Entering": lm.BOTTLE_ENTERING, "Jog_Block": lm.JOG_BLOCK,
    "Heat_Demand": lm.HEAT_DEMAND, "Glue_Ready": lm.GLUE_READY, "Starved": lm.STARVED,
    "Cmd": lm.CMD, "ModeReq": lm.MODE_REQ,
    "GlueSetpoint": lm.GLUE_SETPOINT, "GlueHyst": lm.GLUE_HYST, "GlueBand": lm.GLUE_BAND,
    "PressTurns": lm.PRESS_TURNS,
    "Man_C2": lm.MAN_C2, "Man_Gate": lm.MAN_GATE, "Man_Clamp": lm.MAN_CLAMP, "Man_Spin": lm.MAN_SPIN,
    "Man_Feed": lm.MAN_FEED, "Man_Cut": lm.MAN_CUT, "Man_Discharge": lm.MAN_DISCHARGE,
}  # fmt: skip
# io-list rows the PLC program does not declare: written by the simulator for FUXA only (HMI L0)
ANIMATION = {"Anim_C3": lm.ANIM_C3, "Anim_Station": lm.ANIM_STATION}

PLACEHOLDER_ROWS = {"–"}  # io-list rows for spare / unused addresses; no name, no constant
CONVERTERS = {"QX": lm.qx, "MX": lm.mx, "MW": lm.mw}


def doc_addresses() -> dict[str, tuple[str, int]]:
    """Rows of io-list.md whose Modbus cell is exactly 'coil N' or 'HR N': name -> (kind, N).

    Placeholder rows are skipped: the labeler has two of them with a single coil
    (%MX1.0 unused, %MX5.7 spare), which would otherwise look like a duplicate name.
    """
    found = {}
    for line in IO_LIST.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in PLACEHOLDER_ROWS:
            continue
        match = re.fullmatch(r"(coil|HR) (\d+)", cells[3])
        if match:
            assert cells[0] not in found, f"{cells[0]} appears twice in io-list.md"
            found[cells[0]] = (match.group(1), int(match.group(2)))
    return found


def plc_addresses() -> dict[str, int]:
    """Located variables of the PLC program ('Name : TYPE AT %MX0.0 ...') -> Modbus number."""
    found = {}
    pattern = re.compile(r"^\s*(\w+) : \w+ AT %(QX|MX|MW)(\d+)(?:\.(\d+))?")
    for line in MAIN_LD.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            name, area, first, bit = match.groups()
            assert name not in found, f"{name} declared twice in main.ld"
            convert = CONVERTERS[area]
            found[name] = convert(int(first)) if area == "MW" else convert(int(first), int(bit))
    return found


def test_mapping_anchors():
    assert lm.qx(0, 0) == 0 and lm.mx(0, 0) == 8192 and lm.mw(0) == 1024  # the same OpenPLC v4 mapping


def test_numbers_match_io_list():
    assert (lm.CONVEYOR_MOTOR2, lm.GLUE_HEATER, lm.DISCHARGE_MOTOR) == (0, 7, 8)
    assert (lm.BOTTLE_AT_GATE, lm.SPIN_PULSE, lm.E_STOP_OK, lm.HEARTBEAT) == (8192, 8199, 8201, 8202)
    assert (lm.ROLL_LOW, lm.GLUE_LOW) == (8224, 8225)
    assert lm.GLUE_TEMP == 1036
    assert (lm.STATE, lm.STEP, lm.GOOD_COUNT) == (1074, 1075, 1077)
    assert (lm.ALM_LABEL_TIMEOUT, lm.ALM_GLUE_HEAT_TIMEOUT) == (8232, 8238)
    assert (lm.OUTFEED_FULL, lm.BOTTLE_ENTERING, lm.STARVED) == (8216, 8217, 8221)
    assert (lm.CMD, lm.MODE_REQ, lm.GLUE_SETPOINT, lm.PRESS_TURNS) == (1024, 1025, 1054, 1057)
    assert (lm.MAN_C2, lm.MAN_DISCHARGE) == (8208, 8214)


def test_same_alarm_bit_in_every_plc():
    assert lm.ALM_COMM_LOST == filler.ALM_COMM_LOST == syrup.ALM_COMM_LOST == 8235


def test_same_bits_as_the_filler_for_the_same_job():
    # report 6.2: the standard layout, so simulator and HMI code can be shared
    assert lm.OUTFEED_FULL == filler.OUTFEED_FULL and lm.BOTTLE_ENTERING == filler.ENTRY_LOAD
    assert lm.JOG_BLOCK == filler.JOG_BLOCK and lm.UNUSED_MX1_0 == filler.PRODUCT_AVAILABLE
    assert (lm.E_STOP_OK, lm.HEARTBEAT) == (filler.E_STOP_OK, filler.HEARTBEAT)


def test_sensor_blocks_are_contiguous():
    assert lm.SENSOR_BLOCK[0] == lm.SENSOR_BLOCK_START == 8192
    assert list(lm.SENSOR_BLOCK) == list(range(8192, 8203))  # no gap, no overlap
    assert max(lm.SENSOR_BLOCK) < lm.MAN_C2  # never reaches the manual buttons
    assert lm.UNUSED_MX1_0 in lm.SENSOR_BLOCK and lm.UNUSED_MX1_0 == 8200

    assert lm.EXTRA_SENSOR_BLOCK[0] == lm.EXTRA_SENSOR_BLOCK_START == 8224
    assert list(lm.EXTRA_SENSOR_BLOCK) == [8224, 8225]
    # between the conditioned bits and the alarms, touching neither
    assert lm.CONDITIONED_START + lm.CONDITIONED_COUNT <= min(lm.EXTRA_SENSOR_BLOCK)
    assert max(lm.EXTRA_SENSOR_BLOCK) < lm.ALARMS_START
    assert (lm.ANALOG_START, lm.ANALOG_COUNT) == (1036, 1)


def test_block_sizes():
    assert (lm.OUTPUTS_START, lm.OUTPUTS_COUNT) == (0, 9)
    assert (lm.STATUS_START, lm.STATUS_COUNT) == (1074, 4)
    assert (lm.ALARMS_START, lm.ALARMS_COUNT) == (8232, 7)  # filler 4, syrup room 6
    assert (lm.CONDITIONED_START, lm.CONDITIONED_COUNT) == (8216, 6)
    assert (lm.SETPOINTS_START, lm.SETPOINTS_COUNT) == (1054, 4)


def test_simulator_never_writes_plc_or_hmi_addresses():
    plc_coils = set(range(lm.OUTPUTS_START, lm.OUTPUTS_START + lm.OUTPUTS_COUNT))
    plc_coils |= set(range(lm.ALARMS_START, lm.ALARMS_START + lm.ALARMS_COUNT))
    plc_coils |= set(range(lm.CONDITIONED_START, lm.CONDITIONED_START + lm.CONDITIONED_COUNT))
    hmi_coils = {lm.MAN_C2, lm.MAN_GATE, lm.MAN_CLAMP, lm.MAN_SPIN, lm.MAN_FEED, lm.MAN_CUT, lm.MAN_DISCHARGE}
    plc_registers = set(range(lm.STATUS_START, lm.STATUS_START + lm.STATUS_COUNT))
    hmi_registers = {lm.CMD, lm.MODE_REQ} | set(range(lm.SETPOINTS_START, lm.SETPOINTS_START + lm.SETPOINTS_COUNT))

    assert lm.SIM_WRITE_COILS == set(lm.SENSOR_BLOCK) | set(lm.EXTRA_SENSOR_BLOCK)
    assert not lm.SIM_WRITE_COILS & (plc_coils | hmi_coils)
    assert lm.SIM_WRITE_REGISTERS == {1036, 1094, 1095}  # GlueTemp + the 2 animation words
    assert not lm.SIM_WRITE_REGISTERS & (plc_registers | hmi_registers)


def test_animation_words():
    # %MW70-71 as the filler's Anim_C1 / Anim_C2 (its %MW70-75 live in another PLC)
    assert (lm.ANIM_C3, lm.ANIM_STATION) == (1094, 1095)
    assert (lm.ANIM_START, lm.ANIM_COUNT) == (1094, 2)
    assert (lm.POCKET_EMPTY, lm.POCKET_BOTTLE, lm.POCKET_LABELLING, lm.POCKET_LABELLED) == (0, 1, 2, 3)


def test_no_two_names_share_an_address():
    # registers are HR 1024..8191; everything else is a coil (%QX 0..1023 or %MX from 8192)
    every = {**NAMED, **ANIMATION}
    registers = [a for a in every.values() if 1024 <= a < 8192]
    coils = [a for a in every.values() if not 1024 <= a < 8192]
    assert len(registers) == len(set(registers)) and len(coils) == len(set(coils))


def test_matches_io_list_document():
    doc = doc_addresses()
    assert doc, "no table rows parsed from io-list.md"
    for name, address in {**NAMED, **ANIMATION}.items():
        assert name in doc, f"{name} not found in io-list.md"
        kind, number = doc[name]
        assert number == address, f"{name}: code {address}, io-list.md {kind} {number}"
        assert kind == ("HR" if 1024 <= address < 8192 else "coil"), f"{name}: wrong kind {kind}"


def test_every_io_list_row_has_a_constant():
    # the other direction: a new row in io-list.md must get a constant and an entry in NAMED
    missing = set(doc_addresses()) - set(NAMED) - set(ANIMATION)
    assert not missing, f"io-list.md rows without a constant in address_map_labeler.py: {sorted(missing)}"


def test_matches_plc_program():
    # the uploaded program is the final truth: a variable moved in the Editor but not in the
    # io-list (or the other way round) shows up here
    plc = plc_addresses()
    assert len(plc) == len(NAMED) == 51
    for name, address in NAMED.items():
        assert name in plc, f"{name} is not a located variable in main.ld"
        assert plc[name] == address, f"{name}: code {address}, main.ld {plc[name]}"
    extra = set(plc) - set(NAMED)
    assert not extra, f"located variables in main.ld without a constant: {sorted(extra)}"
    assert not set(plc) & set(ANIMATION), "the animation words belong to the simulator, not the PLC"
