"""plcs.py blow moulder mapping functions (names <-> Modbus values). No PLC needed.  uv run pytest"""

from dataclasses import fields

import pytest

from simulator import address_map_blowmoulder as bm
from simulator.machines.blowmoulder import BlowmoulderOutputs, BlowmoulderSensors
from simulator.plant import BlowmoulderWrites
from simulator.plcs import (
    BLOWMOULDER_ALARM_NAMES,
    BLOWMOULDER_ALARM_SPAN,
    BLOWMOULDER_READBACK_COUNT,
    BLOWMOULDER_REGS_COUNT,
    BLOWMOULDER_REGS_START,
    decode_blowmoulder_outputs,
    decode_blowmoulder_status,
    encode_blowmoulder_analog,
    encode_blowmoulder_extra_block,
    encode_blowmoulder_sensor_block,
    foreign_blowmoulder_writes,
    to_register,
)

ANALOG = ("oven_temp", "blow_pressure_raw")
BOOL_SENSORS = [f.name for f in fields(BlowmoulderSensors) if f.name not in ANALOG]
FIRST = [("preform_at_loader", bm.PREFORM_AT_LOADER), ("preform_in_oven", bm.PREFORM_IN_OVEN),
         ("arm_at_oven", bm.ARM_AT_OVEN), ("arm_at_mould", bm.ARM_AT_MOULD), ("mould_closed", bm.MOULD_CLOSED),
         ("mould_open", bm.MOULD_OPEN), ("rod_up", bm.ROD_UP), ("rod_down", bm.ROD_DOWN),
         ("e_stop_ok", bm.E_STOP_OK), ("heartbeat", bm.HEARTBEAT)]
EXTRA = [("bottle_in_mould", bm.BOTTLE_IN_MOULD), ("bottle_ejected", bm.BOTTLE_EJECTED),
         ("hopper_empty", bm.HOPPER_EMPTY), ("preform_low", bm.PREFORM_LOW), ("c1_full", bm.C1_FULL)]


def writes(*, e_stop_ok=False, heartbeat=False, oven_temp=0, blow_pressure_raw=0, **on: bool) -> BlowmoulderWrites:
    values = {name: False for name in BOOL_SENSORS}
    values.update(on)
    sensors = BlowmoulderSensors(oven_temp=oven_temp, blow_pressure_raw=blow_pressure_raw, **values)
    return BlowmoulderWrites(sensors, e_stop_ok, heartbeat)


def test_decode_outputs_in_coil_order_with_lamp_power_from_hr0():
    bits = [False] * 16  # pymodbus pads 10 coils to 16 bits
    bits[bm.GRIPPER] = bits[bm.EXHAUST_VALVE] = True
    bits[10] = True  # padding: must be ignored
    assert decode_blowmoulder_outputs(bits, 42) == BlowmoulderOutputs(gripper=True, exhaust_valve=True, lamp_power=42)
    assert [f.name for f in fields(BlowmoulderOutputs)][bm.EJECTOR] == "ejector"
    assert decode_blowmoulder_outputs(bits, 0xFFFF).lamp_power == -1  # signed INT: a PLC bug shows as negative


@pytest.mark.parametrize("field, address", FIRST)
def test_each_first_block_field_lands_on_its_coil(field, address):
    block = encode_blowmoulder_sensor_block(writes(**{field: True}))
    assert len(block) == len(bm.SENSOR_BLOCK) == 11
    assert [bm.SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]
    assert not any(encode_blowmoulder_extra_block(writes(**{field: True})))


@pytest.mark.parametrize("field, address", EXTRA)
def test_each_extra_field_lands_on_its_coil(field, address):
    block = encode_blowmoulder_extra_block(writes(**{field: True}))
    assert len(block) == len(bm.EXTRA_SENSOR_BLOCK) == 5
    assert [bm.EXTRA_SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]
    assert not any(encode_blowmoulder_sensor_block(writes(**{field: True})))


def test_unused_mx1_0_is_always_false():
    everything = writes(e_stop_ok=True, heartbeat=True, **{name: True for name in BOOL_SENSORS})
    block = encode_blowmoulder_sensor_block(everything)
    assert block[bm.UNUSED_MX1_0 - bm.SENSOR_BLOCK_START] is False
    assert sum(block) == 10 and all(encode_blowmoulder_extra_block(everything))


def test_every_sensor_field_is_written_exactly_once():
    named = [name for name, _ in FIRST + EXTRA] + list(ANALOG)
    assert sorted(named) == sorted([f.name for f in fields(BlowmoulderSensors)] + ["e_stop_ok", "heartbeat"])


def test_analog_words_are_signed():
    assert encode_blowmoulder_analog(writes(oven_temp=1050, blow_pressure_raw=24192)) == [1050, 24192]
    assert encode_blowmoulder_analog(writes(blow_pressure_raw=-2000)) == [0, 63536]  # wire break, two's complement
    assert encode_blowmoulder_analog(writes(blow_pressure_raw=40000)) == [0, 32767]  # clamped to INT


def test_decode_status_with_negative_words_and_alarm_order():
    regs = [0] * BLOWMOULDER_REGS_COUNT
    regs[:3] = [100, to_register(-28), to_register(-150)]  # Lamp_Calc, BlowPressure, OvenError
    at = bm.STATUS_START - BLOWMOULDER_REGS_START
    regs[at: at + 4] = [9, 900, 900, 17]
    span = [False] * BLOWMOULDER_ALARM_SPAN
    span[bm.ALM_HANDLING_JAM - bm.ALARMS_START] = True
    span[bm.ALM_PRESSURE_SENSOR - bm.ALARMS_START] = True
    span[bm.ALARMS_COUNT] = True  # %MX6.0 between the two alarm bytes: not an alarm
    s = decode_blowmoulder_status(regs, span, 2)
    assert (s.state, s.step, s.mould_step, s.bottle_count, s.mode) == (9, 900, 900, 17, 2)
    assert (s.lamp_calc, s.blow_pressure, s.oven_error) == (100, -28, -150)
    assert [n for n, on in s.alarms.items() if on] == ["Alm_HandlingJam", "Alm_PressureSensor"]
    assert len(BLOWMOULDER_ALARM_NAMES) == bm.ALARMS_COUNT + bm.EXTRA_ALARMS_COUNT == 11


def test_spans_cover_exactly_the_intended_addresses():
    assert BLOWMOULDER_REGS_START == bm.LAMP_CALC and BLOWMOULDER_REGS_START + BLOWMOULDER_REGS_COUNT - 1 == bm.BOTTLE_COUNT
    assert bm.ALARMS_START + BLOWMOULDER_ALARM_SPAN - 1 == bm.ALM_PRESSURE_SENSOR
    assert bm.SENSOR_BLOCK_START + BLOWMOULDER_READBACK_COUNT - 1 == bm.C1_FULL


def test_foreign_writes_names_every_signal_that_differs():
    w = writes(e_stop_ok=True, heartbeat=True, arm_at_oven=True, c1_full=True, oven_temp=1000, blow_pressure_raw=-500)
    span = [False] * BLOWMOULDER_READBACK_COUNT
    for address, value in zip(bm.SENSOR_BLOCK, encode_blowmoulder_sensor_block(w)):
        span[address - bm.SENSOR_BLOCK_START] = value
    for address, value in zip(bm.EXTRA_SENSOR_BLOCK, encode_blowmoulder_extra_block(w)):
        span[address - bm.SENSOR_BLOCK_START] = value
    analog = encode_blowmoulder_analog(w)
    span[bm.MAN_LOADER - bm.SENSOR_BLOCK_START] = True  # the HMI between the blocks: not judged
    assert foreign_blowmoulder_writes(w, span, analog) == []
    span[bm.ROD_DOWN - bm.SENSOR_BLOCK_START] = True
    span[bm.C1_FULL - bm.SENSOR_BLOCK_START] = False
    assert foreign_blowmoulder_writes(w, span, [analog[0], 0]) == ["rod_down", "c1_full", "blow_pressure_raw"]
