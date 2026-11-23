"""Packer simulator stage 0: the address map matches docs/packer/io-list.md, the PLC program
(plcs/packer/pous/programs/main.ld) and the one-writer rule.

Pure Python, no PLC needed:  uv run pytest
"""

import re
from pathlib import Path

from simulator import address_map as filler
from simulator import address_map_labeler as labeler
from simulator import address_map_packer as pm
from simulator import address_map_syrup as syrup

ROOT = Path(__file__).parents[3]
IO_LIST = ROOT / "docs" / "packer" / "io-list.md"
MAIN_LD = ROOT / "plcs" / "packer" / "pous" / "programs" / "main.ld"

# io-list / PLC variable name -> constant of the module. The guards below compare every entry
# with the io-list tables and with the located variables of the PLC program.
NAMED = {
    "ConveyorMotor3": pm.CONVEYOR_MOTOR3, "GateMotor": pm.GATE_MOTOR, "GroupPusher": pm.GROUP_PUSHER,
    "WrapConveyor": pm.WRAP_CONVEYOR, "FilmFeed": pm.FILM_FEED, "Knife": pm.KNIFE, "WrapBar": pm.WRAP_BAR,
    "OvenHeater": pm.OVEN_HEATER, "OvenBlower": pm.OVEN_BLOWER, "OvenConveyor": pm.OVEN_CONVEYOR,
    "Bottle_At_Gate": pm.BOTTLE_AT_GATE, "Bottle_Counted": pm.BOTTLE_COUNTED, "Pusher_Home": pm.PUSHER_HOME,
    "Pusher_Out": pm.PUSHER_OUT, "Pack_At_Film": pm.PACK_AT_FILM, "Film_In_Slot": pm.FILM_IN_SLOT,
    "Film_Pulse": pm.FILM_PULSE, "Wrap_Home": pm.WRAP_HOME,
    "Film_Empty": pm.FILM_EMPTY, "Film_Low": pm.FILM_LOW, "Oven_Entry": pm.OVEN_ENTRY, "Oven_Exit": pm.OVEN_EXIT,
    "Table_Full": pm.TABLE_FULL, "Airflow_OK": pm.AIRFLOW_OK,
    "E_Stop_OK": pm.E_STOP_OK, "Heartbeat": pm.HEARTBEAT,
    "OvenTemp": pm.OVEN_TEMP,
    "State": pm.STATE, "Step": pm.STEP, "WrapStep": pm.WRAP_STEP, "PackCount": pm.PACK_COUNT,
    "GroupCount": pm.GROUP_COUNT, "PacksInOven": pm.PACKS_IN_OVEN, "FeedPulses": pm.FEED_PULSES,
    "FeedCount": pm.FEED_COUNT,
    "Alm_FilmFault": pm.ALM_FILM_FAULT, "Alm_PackJam": pm.ALM_PACK_JAM,
    "Alm_OutfeedBlocked": pm.ALM_OUTFEED_BLOCKED, "Alm_CommLost": pm.ALM_COMM_LOST,
    "Alm_FilmEmpty": pm.ALM_FILM_EMPTY, "Alm_OvenOverTemp": pm.ALM_OVEN_OVER_TEMP,
    "Alm_OvenHeatTimeout": pm.ALM_OVEN_HEAT_TIMEOUT, "Alm_OvenJam": pm.ALM_OVEN_JAM,
    "Alm_AirflowLost": pm.ALM_AIRFLOW_LOST, "Alm_WrapTimeout": pm.ALM_WRAP_TIMEOUT,
    "Outfeed_Full": pm.OUTFEED_FULL, "Group_Complete": pm.GROUP_COMPLETE, "Jog_Block": pm.JOG_BLOCK,
    "Heat_Demand": pm.HEAT_DEMAND, "Oven_Ready": pm.OVEN_READY, "Starved": pm.STARVED,
    "Wrap_Clear": pm.WRAP_CLEAR, "Oven_On": pm.OVEN_ON,
    "Cmd": pm.CMD, "ModeReq": pm.MODE_REQ,
    "OvenSetpoint": pm.OVEN_SETPOINT, "OvenHyst": pm.OVEN_HYST, "OvenBand": pm.OVEN_BAND,
    "FilmLength": pm.FILM_LENGTH,
    "Man_C3": pm.MAN_C3, "Man_Gate": pm.MAN_GATE, "Man_GroupPush": pm.MAN_GROUP_PUSH,
    "Man_WrapConveyor": pm.MAN_WRAP_CONVEYOR, "Man_FilmFeed": pm.MAN_FILM_FEED, "Man_Knife": pm.MAN_KNIFE,
    "Man_WrapBar": pm.MAN_WRAP_BAR, "Man_OvenConveyor": pm.MAN_OVEN_CONVEYOR,
}  # fmt: skip

PLACEHOLDER_ROWS = {"–"}  # io-list rows for spare / unused addresses; no name, no constant
CONVERTERS = {"QX": pm.qx, "MX": pm.mx, "MW": pm.mw}


def doc_addresses() -> dict[str, tuple[str, int]]:
    """Rows of io-list.md whose Modbus cell is exactly 'coil N' or 'HR N': name -> (kind, N).

    Placeholder rows are skipped (%MX1.0 unused, the spare ranges).
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
    assert pm.qx(0, 0) == 0 and pm.mx(0, 0) == 8192 and pm.mw(0) == 1024  # the same OpenPLC v4 mapping


def test_numbers_match_io_list():
    assert (pm.CONVEYOR_MOTOR3, pm.OVEN_HEATER, pm.OVEN_BLOWER, pm.OVEN_CONVEYOR) == (0, 7, 8, 9)
    assert (pm.BOTTLE_AT_GATE, pm.WRAP_HOME, pm.E_STOP_OK, pm.HEARTBEAT) == (8192, 8199, 8201, 8202)
    assert (pm.FILM_EMPTY, pm.AIRFLOW_OK) == (8224, 8229)
    assert pm.OVEN_TEMP == 1036
    assert (pm.STATE, pm.STEP, pm.WRAP_STEP, pm.PACK_COUNT) == (1074, 1075, 1076, 1077)
    assert (pm.GROUP_COUNT, pm.FEED_COUNT) == (1064, 1067)
    assert (pm.ALM_FILM_FAULT, pm.ALM_OVEN_JAM) == (8232, 8239)
    assert (pm.ALM_AIRFLOW_LOST, pm.ALM_WRAP_TIMEOUT) == (8248, 8249)
    assert (pm.OUTFEED_FULL, pm.GROUP_COMPLETE, pm.OVEN_ON) == (8216, 8217, 8223)
    assert (pm.CMD, pm.MODE_REQ, pm.OVEN_SETPOINT, pm.FILM_LENGTH) == (1024, 1025, 1054, 1057)
    assert (pm.MAN_C3, pm.MAN_OVEN_CONVEYOR) == (8208, 8215)


def test_same_alarm_bit_in_every_plc():
    assert pm.ALM_COMM_LOST == filler.ALM_COMM_LOST == syrup.ALM_COMM_LOST == labeler.ALM_COMM_LOST == 8235


def test_same_bits_as_the_other_machines_for_the_same_job():
    # report 6.2: the standard layout, so simulator and HMI code can be shared
    assert pm.OUTFEED_FULL == filler.OUTFEED_FULL == labeler.OUTFEED_FULL
    assert pm.JOG_BLOCK == filler.JOG_BLOCK and pm.UNUSED_MX1_0 == filler.PRODUCT_AVAILABLE
    assert (pm.E_STOP_OK, pm.HEARTBEAT) == (filler.E_STOP_OK, filler.HEARTBEAT)
    assert pm.ALM_OUTFEED_BLOCKED == labeler.ALM_OUTFEED_BLOCKED
    assert pm.OVEN_TEMP == labeler.GLUE_TEMP  # the one analog sensor sits in %MW12 in both
    assert (pm.SETPOINTS_START, pm.STATUS_START) == (labeler.SETPOINTS_START, labeler.STATUS_START)


def test_sensor_blocks_are_contiguous():
    assert pm.SENSOR_BLOCK[0] == pm.SENSOR_BLOCK_START == 8192
    assert list(pm.SENSOR_BLOCK) == list(range(8192, 8203))  # no gap, no overlap
    assert max(pm.SENSOR_BLOCK) < pm.MAN_C3  # never reaches the manual buttons
    assert pm.UNUSED_MX1_0 in pm.SENSOR_BLOCK and pm.UNUSED_MX1_0 == 8200

    assert pm.EXTRA_SENSOR_BLOCK[0] == pm.EXTRA_SENSOR_BLOCK_START == 8224
    assert list(pm.EXTRA_SENSOR_BLOCK) == list(range(8224, 8230))
    # between the conditioned bits and the alarms, touching neither
    assert pm.CONDITIONED_START + pm.CONDITIONED_COUNT <= min(pm.EXTRA_SENSOR_BLOCK)
    assert max(pm.EXTRA_SENSOR_BLOCK) < pm.ALARMS_START
    assert (pm.ANALOG_START, pm.ANALOG_COUNT) == (1036, 1)


def test_block_sizes():
    assert (pm.OUTPUTS_START, pm.OUTPUTS_COUNT) == (0, 10)
    assert (pm.STATUS_START, pm.STATUS_COUNT) == (1074, 4)
    assert (pm.COMPUTED_START, pm.COMPUTED_COUNT) == (1064, 4)
    assert (pm.ALARMS_START, pm.ALARMS_COUNT) == (8232, 8)  # all of %MX5
    assert (pm.EXTRA_ALARMS_START, pm.EXTRA_ALARMS_COUNT) == (8248, 2)  # %MX7.0-7.1
    assert (pm.CONDITIONED_START, pm.CONDITIONED_COUNT) == (8216, 8)
    assert (pm.SETPOINTS_START, pm.SETPOINTS_COUNT) == (1054, 4)


def test_simulator_never_writes_plc_or_hmi_addresses():
    plc_coils = set(range(pm.OUTPUTS_START, pm.OUTPUTS_START + pm.OUTPUTS_COUNT))
    plc_coils |= set(range(pm.ALARMS_START, pm.ALARMS_START + pm.ALARMS_COUNT))
    plc_coils |= set(range(pm.EXTRA_ALARMS_START, pm.EXTRA_ALARMS_START + pm.EXTRA_ALARMS_COUNT))
    plc_coils |= set(range(pm.CONDITIONED_START, pm.CONDITIONED_START + pm.CONDITIONED_COUNT))
    hmi_coils = {pm.MAN_C3, pm.MAN_GATE, pm.MAN_GROUP_PUSH, pm.MAN_WRAP_CONVEYOR, pm.MAN_FILM_FEED,
                 pm.MAN_KNIFE, pm.MAN_WRAP_BAR, pm.MAN_OVEN_CONVEYOR}
    plc_registers = set(range(pm.STATUS_START, pm.STATUS_START + pm.STATUS_COUNT))
    plc_registers |= set(range(pm.COMPUTED_START, pm.COMPUTED_START + pm.COMPUTED_COUNT))
    hmi_registers = {pm.CMD, pm.MODE_REQ} | set(range(pm.SETPOINTS_START, pm.SETPOINTS_START + pm.SETPOINTS_COUNT))

    assert pm.SIM_WRITE_COILS == set(pm.SENSOR_BLOCK) | set(pm.EXTRA_SENSOR_BLOCK)
    assert not pm.SIM_WRITE_COILS & (plc_coils | hmi_coils)
    assert pm.SIM_WRITE_REGISTERS == {1036}  # OvenTemp; animation words come with the HMI plan (part C)
    assert not pm.SIM_WRITE_REGISTERS & (plc_registers | hmi_registers)


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
    missing = set(doc_addresses()) - set(NAMED)
    assert not missing, f"io-list.md rows without a constant in address_map_packer.py: {sorted(missing)}"


def test_matches_plc_program():
    # the uploaded program is the final truth: a variable moved in the Editor but not in the
    # io-list (or the other way round) shows up here
    plc = plc_addresses()
    assert len(plc) == len(NAMED) == 67
    for name, address in NAMED.items():
        assert name in plc, f"{name} is not a located variable in main.ld"
        assert plc[name] == address, f"{name}: code {address}, main.ld {plc[name]}"
    extra = set(plc) - set(NAMED)
    assert not extra, f"located variables in main.ld without a constant: {sorted(extra)}"
