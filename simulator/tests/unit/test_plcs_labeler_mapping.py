"""plcs.py labeler mapping functions (names <-> Modbus values). No PLC needed.  uv run pytest"""

import asyncio
from dataclasses import fields, replace

import pytest

from simulator import address_map_labeler as lm
from simulator.machines.labeler import LabelerOutputs, LabelerSensors
from simulator.plant import LabelerWrites
from simulator.plcs import (
    LABELER_ALARM_NAMES,
    LABELER_COIL_FIELDS,
    LabelerPlc,
    decode_labeler_outputs,
    decode_labeler_status,
    encode_labeler_animation,
    encode_labeler_extra_block,
    encode_labeler_sensor_block,
    foreign_labeler_writes,
    to_register,
)

BOOL_SENSORS = [f.name for f in fields(LabelerSensors) if f.name != "glue_temp"]


def writes(*, e_stop_ok=False, heartbeat=False, glue_temp=0, **on: bool) -> LabelerWrites:
    values = {name: False for name in BOOL_SENSORS}
    values.update(on)
    return LabelerWrites(LabelerSensors(glue_temp=glue_temp, **values), e_stop_ok, heartbeat)


def test_decode_outputs_in_coil_order():
    bits = [False] * 16  # pymodbus pads 9 coils to 16 bits
    bits[lm.GLUE_HEATER] = bits[lm.DISCHARGE_MOTOR] = True
    bits[9] = True  # padding: must be ignored
    assert decode_labeler_outputs(bits) == LabelerOutputs(glue_heater=True, discharge_motor=True)


@pytest.mark.parametrize(
    "field, address",
    [("bottle_at_gate", lm.BOTTLE_AT_GATE), ("bottle_in_pocket", lm.BOTTLE_IN_POCKET), ("bell_down", lm.BELL_DOWN),
     ("label_present", lm.LABEL_PRESENT), ("c3_full", lm.C3_FULL), ("roll_empty", lm.ROLL_EMPTY),
     ("label_mark", lm.LABEL_MARK), ("spin_pulse", lm.SPIN_PULSE), ("e_stop_ok", lm.E_STOP_OK),
     ("heartbeat", lm.HEARTBEAT)],
)
def test_each_field_lands_on_its_coil(field, address):
    block = encode_labeler_sensor_block(writes(**{field: True}))
    assert len(block) == len(lm.SENSOR_BLOCK) == 11
    assert [lm.SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]
    assert encode_labeler_extra_block(writes(**{field: True})) == [False, False]


def test_unused_mx1_0_is_always_false():
    everything = writes(e_stop_ok=True, heartbeat=True, **{name: True for name in BOOL_SENSORS})
    block = encode_labeler_sensor_block(everything)
    assert sum(block) == 10 and block[lm.UNUSED_MX1_0 - lm.SENSOR_BLOCK_START] is False
    assert lm.UNUSED_MX1_0 not in LABELER_COIL_FIELDS


def test_extra_block_order():
    assert encode_labeler_extra_block(writes(roll_low=True)) == [True, False]  # %MX4.0
    assert encode_labeler_extra_block(writes(glue_low=True)) == [False, True]  # %MX4.1
    assert sum(encode_labeler_sensor_block(writes(roll_low=True, glue_low=True))) == 0  # never in the first block


def test_glue_temp_register():
    assert to_register(writes(glue_temp=1700).sensors.glue_temp) == 1700
    assert to_register(-50) == 65486  # a frozen tank below 0 degC still travels correctly


def test_animation_words_encoded_in_order():
    w = replace(writes(), anim=(0b0000_0110, lm.POCKET_LABELLED))
    assert encode_labeler_animation(w) == [6, 3]
    with pytest.raises(AssertionError):  # a word an INT cannot hold never reaches the PLC
        encode_labeler_animation(replace(w, anim=(1 << 15, 0)))


def test_decode_status_alarms_in_address_order():
    alarms = [False] * 8  # padded to 8
    alarms[1] = alarms[5] = True  # Alm_PocketJam, Alm_GlueOverTemp
    s = decode_labeler_status([9, 900, 0, 42], alarms, mode=1)
    assert (s.state, s.step, s.good_count, s.mode) == (9, 900, 42, 1)
    assert [name for name, on in s.alarms.items() if on] == ["Alm_PocketJam", "Alm_GlueOverTemp"]
    assert len(LABELER_ALARM_NAMES) == lm.ALARMS_COUNT == 7
    assert LABELER_ALARM_NAMES[3] == "Alm_CommLost"  # the same bit as in every PLC


def test_foreign_writes_names_the_wrong_signals():
    w = writes(e_stop_ok=True, heartbeat=True, bell_down=True, roll_low=True, glue_temp=1700)
    coils, extra = encode_labeler_sensor_block(w), encode_labeler_extra_block(w)
    assert foreign_labeler_writes(w, coils, extra, 1700) == []
    bad_coils = list(coils)
    bad_coils[lm.BELL_DOWN - lm.SENSOR_BLOCK_START] = False  # forced FALSE in the debugger
    bad_coils[lm.UNUSED_MX1_0 - lm.SENSOR_BLOCK_START] = True  # set by somebody else
    assert foreign_labeler_writes(w, bad_coils, [False, True], 1500) == [
        "bell_down", f"coil {lm.UNUSED_MX1_0}", "roll_low", "glue_low", "glue_temp"]


def test_client_refuses_addresses_that_are_not_the_simulators():
    plc = LabelerPlc()  # works outside asyncio; the guard fires before the link check
    refused = [
        ("_write_register", (lm.CMD, 4)),
        ("_write_register", (lm.MODE_REQ, 1)),
        ("_write_register", (lm.GLUE_SETPOINT, 1700)),  # a setpoint (HMI)
        ("_write_register", (lm.GLUE_TEMP + 1, 0)),  # %MW13, spare
        ("_write_register", (lm.STEP, 0)),  # the PLC's status
        ("_write_coils", (lm.MAN_SPIN, [True])),  # a manual button (HMI)
        ("_write_coils", (lm.OUTFEED_FULL, [True])),  # the PLC's conditioned bits
        ("_write_coils", (lm.GLUE_READY, [True])),
        ("_write_coils", (lm.ALM_COMM_LOST, [False])),  # the PLC's alarm
        ("_write_coils", (lm.GLUE_HEATER, [True])),  # a PLC output
        ("_write_coils", (lm.SENSOR_BLOCK_START, [False] * 12)),  # one coil too long reaches %MX1.3
        ("_write_coils", (lm.EXTRA_SENSOR_BLOCK_START, [False] * 3)),  # reaches the spare %MX4.2
    ]
    for method, args in refused:
        with pytest.raises(ValueError):
            asyncio.run(getattr(plc, method)(*args))
