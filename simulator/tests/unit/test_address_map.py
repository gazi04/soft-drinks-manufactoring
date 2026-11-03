"""Stage 0: the address map matches docs/filler/io-list.md and the one-writer rule.

Pure Python, no PLC needed:  uv run pytest
"""

from simulator import address_map as am


def test_mapping_anchors():
    # verified with the smoke test on 2026-10-28
    assert am.qx(0, 0) == 0
    assert am.mx(0, 0) == 8192
    assert am.mw(0) == 1024
    assert am.mw(12) == 1036


def test_numbers_match_io_list():
    assert (am.CONVEYOR1, am.INDEX_MOTOR) == (0, 5)
    assert (am.S2, am.S8) == (8192, 8198)
    assert (am.PRODUCT_AVAILABLE, am.E_STOP_OK, am.HEARTBEAT) == (8200, 8201, 8202)
    assert am.FILL_LEVEL == 1036
    assert (am.STATE, am.STEP, am.GOOD_COUNT) == (1074, 1075, 1077)
    assert (am.ALM_INDEX_JAM, am.ALM_COMM_LOST) == (8232, 8235)
    assert (am.CMD, am.MODE_REQ, am.FILL_SETPOINT) == (1024, 1025, 1054)
    assert (am.MAN_C1, am.MAN_INDEX) == (8208, 8213)


def test_animation_words():
    assert (am.ANIM_C1, am.ANIM_C2, am.ANIM_POCKETS) == (1094, 1095, 1096)  # %MW70, 71, 72
    anim = set(range(am.ANIM_START, am.ANIM_START + am.ANIM_COUNT))
    assert anim == set(range(1094, 1100)) and anim <= am.SIM_WRITE_REGISTERS


def test_sensor_block_is_one_contiguous_write():
    block = am.SENSOR_BLOCK
    assert block[0] == am.SENSOR_BLOCK_START == 8192
    assert len(block) == 11
    assert list(block) == list(range(8192, 8192 + 11))  # 8192..8202, no gap, no overlap


def test_bulk_write_stays_inside_simulator_addresses():
    assert set(am.SENSOR_BLOCK) <= am.SIM_WRITE_COILS
    assert max(am.SENSOR_BLOCK) < am.MAN_C1  # never reaches the manual buttons


def test_simulator_never_writes_hmi_or_plc_addresses():
    hmi_coils = {am.MAN_C1, am.MAN_DOOR, am.MAN_RINSE, am.MAN_FILL, am.MAN_CAP, am.MAN_INDEX}
    plc_coils = set(range(am.OUTPUTS_START, am.OUTPUTS_START + am.OUTPUTS_COUNT))
    plc_coils |= set(range(am.ALARMS_START, am.ALARMS_START + am.ALARMS_COUNT))
    hmi_registers = {am.CMD, am.MODE_REQ, am.FILL_SETPOINT, am.RINSE_TIME, am.CAP_TIME}
    plc_registers = set(range(am.STATUS_START, am.STATUS_START + am.STATUS_COUNT))

    assert not am.SIM_WRITE_COILS & (hmi_coils | plc_coils)
    assert not am.SIM_WRITE_REGISTERS & (hmi_registers | plc_registers)
