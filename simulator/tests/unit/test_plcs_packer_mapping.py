"""plcs.py packer mapping functions (names <-> Modbus values). No PLC needed.  uv run pytest"""

import asyncio
from dataclasses import fields, replace

import pytest

from simulator import address_map_packer as pm
from simulator.machines.packer import PackerOutputs, PackerSensors
from simulator.plant import PackerWrites
from simulator.plcs import (
    PACKER_ALARM_NAMES,
    PACKER_ALARM_SPAN,
    PACKER_COIL_FIELDS,
    PACKER_READBACK_COUNT,
    PACKER_REGS_COUNT,
    PACKER_REGS_START,
    PackerPlc,
    decode_packer_outputs,
    decode_packer_status,
    encode_packer_animation,
    encode_packer_extra_block,
    encode_packer_sensor_block,
    foreign_packer_writes,
    to_register,
)

BOOL_SENSORS = [f.name for f in fields(PackerSensors) if f.name != "oven_temp"]
FIRST = [("bottle_at_gate", pm.BOTTLE_AT_GATE), ("bottle_counted", pm.BOTTLE_COUNTED), ("pusher_home", pm.PUSHER_HOME),
         ("pusher_out", pm.PUSHER_OUT), ("pack_at_film", pm.PACK_AT_FILM), ("film_in_slot", pm.FILM_IN_SLOT),
         ("film_pulse", pm.FILM_PULSE), ("wrap_home", pm.WRAP_HOME), ("e_stop_ok", pm.E_STOP_OK),
         ("heartbeat", pm.HEARTBEAT)]
EXTRA = [("film_empty", pm.FILM_EMPTY), ("film_low", pm.FILM_LOW), ("oven_entry", pm.OVEN_ENTRY),
         ("oven_exit", pm.OVEN_EXIT), ("table_full", pm.TABLE_FULL), ("airflow_ok", pm.AIRFLOW_OK)]


def writes(*, e_stop_ok=False, heartbeat=False, oven_temp=0, **on: bool) -> PackerWrites:
    values = {name: False for name in BOOL_SENSORS}
    values.update(on)
    return PackerWrites(PackerSensors(oven_temp=oven_temp, **values), e_stop_ok, heartbeat)


def test_decode_outputs_in_coil_order():
    bits = [False] * 16  # pymodbus pads 10 coils to 16 bits
    bits[pm.KNIFE] = bits[pm.OVEN_CONVEYOR] = True
    bits[10] = True  # padding: must be ignored
    assert decode_packer_outputs(bits) == PackerOutputs(knife=True, oven_conveyor=True)
    assert [f.name for f in fields(PackerOutputs)][pm.OVEN_BLOWER] == "oven_blower"


@pytest.mark.parametrize("field, address", FIRST)
def test_each_first_block_field_lands_on_its_coil(field, address):
    block = encode_packer_sensor_block(writes(**{field: True}))
    assert len(block) == len(pm.SENSOR_BLOCK) == 11
    assert [pm.SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]
    assert not any(encode_packer_extra_block(writes(**{field: True})))


@pytest.mark.parametrize("field, address", EXTRA)
def test_each_extra_field_lands_on_its_coil(field, address):
    block = encode_packer_extra_block(writes(**{field: True}))
    assert len(block) == len(pm.EXTRA_SENSOR_BLOCK) == 6
    assert [pm.EXTRA_SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]
    assert not any(encode_packer_sensor_block(writes(**{field: True})))  # never in the first block


def test_every_sensor_has_a_coil_and_unused_mx1_0_is_always_false():
    assert {f for f, _ in FIRST + EXTRA} == set(BOOL_SENSORS) | {"e_stop_ok", "heartbeat"}
    everything = writes(e_stop_ok=True, heartbeat=True, **{name: True for name in BOOL_SENSORS})
    block = encode_packer_sensor_block(everything)
    assert sum(block) == 10 and block[pm.UNUSED_MX1_0 - pm.SENSOR_BLOCK_START] is False
    assert pm.UNUSED_MX1_0 not in PACKER_COIL_FIELDS


def test_oven_temp_register():
    assert to_register(writes(oven_temp=1900).sensors.oven_temp) == 1900
    assert to_register(-50) == 65486


def test_animation_words_encoded_in_order():
    w = replace(writes(), anim=(0b111_0000_0000, 0b100_0000_0011, 6, pm.SHEET_IN_SLOT))
    assert encode_packer_animation(w) == [0x700, 0x403, 6, 2]
    with pytest.raises(AssertionError):  # a word an INT cannot hold never reaches the PLC
        encode_packer_animation(replace(w, anim=(1 << 15, 0, 0, 0)))


def test_decode_status_reads_every_word_and_alarm_of_the_spans():
    regs = list(range(100, 100 + PACKER_REGS_COUNT))  # every word distinct: an off-by-one shows
    regs[pm.STATUS_START - PACKER_REGS_START: pm.STATUS_START - PACKER_REGS_START + 4] = [3, 15, 24, 7]
    regs[:4] = [6, 2, 17, 9]  # GroupCount, PacksInOven, FeedPulses, FeedCount
    span = [False] * 24  # padded
    span[1] = True  # %MX5.1 Alm_PackJam
    span[7] = True  # %MX5.7 Alm_OvenJam
    span[9] = True  # %MX6.1: unused, must be ignored
    span[pm.ALM_WRAP_TIMEOUT - pm.ALARMS_START] = True  # %MX7.1
    s = decode_packer_status(regs, span, mode=2)
    assert (s.state, s.step, s.wrap_step, s.pack_count) == (3, 15, 24, 7)
    assert (s.group_count, s.packs_in_oven, s.feed_pulses, s.feed_count, s.mode) == (6, 2, 17, 9, 2)
    assert [n for n, on in s.alarms.items() if on] == ["Alm_PackJam", "Alm_OvenJam", "Alm_WrapTimeout"]
    assert len(PACKER_ALARM_NAMES) == pm.ALARMS_COUNT + pm.EXTRA_ALARMS_COUNT == 10
    assert PACKER_ALARM_NAMES[3] == "Alm_CommLost"  # the same bit as in every PLC


def test_span_sizes():
    assert (PACKER_REGS_START, PACKER_REGS_COUNT) == (pm.GROUP_COUNT, 14)  # %MW40..%MW53
    assert PACKER_ALARM_SPAN == 18  # %MX5.0..%MX7.1
    assert pm.SENSOR_BLOCK_START + PACKER_READBACK_COUNT - 1 == pm.AIRFLOW_OK  # %MX0.0..%MX4.5


def test_foreign_writes_names_the_wrong_signals():
    w = writes(e_stop_ok=True, heartbeat=True, pusher_home=True, airflow_ok=True, oven_temp=1900)
    span = [False] * PACKER_READBACK_COUNT
    for address, value in zip(pm.SENSOR_BLOCK, encode_packer_sensor_block(w)):
        span[address - pm.SENSOR_BLOCK_START] = value
    for address, value in zip(pm.EXTRA_SENSOR_BLOCK, encode_packer_extra_block(w)):
        span[address - pm.SENSOR_BLOCK_START] = value
    span[pm.MAN_GATE - pm.SENSOR_BLOCK_START] = True  # an HMI button between the blocks: not judged
    assert foreign_packer_writes(w, span, 1900) == []
    bad = list(span)
    bad[pm.PUSHER_HOME - pm.SENSOR_BLOCK_START] = False  # forced in the debugger
    bad[pm.UNUSED_MX1_0 - pm.SENSOR_BLOCK_START] = True  # set by somebody else
    bad[pm.TABLE_FULL - pm.SENSOR_BLOCK_START] = True
    assert foreign_packer_writes(w, bad, 2000) == ["pusher_home", f"coil {pm.UNUSED_MX1_0}", "table_full", "oven_temp"]


def test_client_refuses_addresses_that_are_not_the_simulators():
    plc = PackerPlc()  # works outside asyncio; the guard fires before the link check
    refused = [
        ("_write_register", (pm.CMD, 4)),
        ("_write_register", (pm.MODE_REQ, 1)),
        ("_write_register", (pm.OVEN_SETPOINT, 1900)),  # a setpoint (HMI)
        ("_write_register", (pm.FILM_LENGTH, 850)),
        ("_write_register", (pm.OVEN_TEMP + 1, 0)),  # %MW13, spare
        ("_write_register", (pm.WRAP_STEP, 0)),  # the PLC's status
        ("_write_register", (pm.PACKS_IN_OVEN, 0)),  # computed by the PLC
        ("_write_coils", (pm.MAN_KNIFE, [True])),  # a manual button (HMI)
        ("_write_coils", (pm.OVEN_READY, [True])),  # the PLC's conditioned bits
        ("_write_coils", (pm.ALM_COMM_LOST, [False])),  # the PLC's alarms
        ("_write_coils", (pm.ALM_AIRFLOW_LOST, [False])),
        ("_write_coils", (pm.OVEN_HEATER, [True])),  # a PLC output
        ("_write_coils", (pm.SENSOR_BLOCK_START, [False] * 12)),  # one coil too long reaches %MX1.3
        ("_write_coils", (pm.EXTRA_SENSOR_BLOCK_START, [False] * 7)),  # reaches the spare %MX4.6
        ("_write_coils", (pm.SENSOR_BLOCK_START, [False] * PACKER_READBACK_COUNT)),  # the read span, never written
    ]
    for method, args in refused:
        with pytest.raises(ValueError):
            asyncio.run(getattr(plc, method)(*args))
