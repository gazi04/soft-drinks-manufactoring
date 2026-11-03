"""plcs.py mapping functions (names <-> Modbus values). No PLC needed.  uv run pytest"""

import asyncio

import pytest

from simulator import address_map as am
from simulator.plant import SimWrites
from simulator.plcs import (
    FillerPlc,
    decode_outputs,
    decode_status,
    encode_animation,
    encode_fill_level,
    encode_sensor_block,
    foreign_writes,
)


def writes(**on) -> SimWrites:
    """SimWrites with everything False / 0, except the given fields."""
    fields = dict(
        s2=False, s3=False, s4=False, s5=False, s6=False, s7=False, s8=False,
        product_available=False, e_stop_ok=False, heartbeat=False, fill_level=0,
    )
    fields.update(on)
    return SimWrites(**fields)


def test_decode_outputs_in_coil_order():
    out = decode_outputs([True, False, True, False, False, True, False, False])  # padded to 8
    assert out.conveyor1 and out.rinse_valve and out.index_motor
    assert not (out.door_motor or out.fill_valve or out.cap_motor)


@pytest.mark.parametrize(
    "field, address",
    [
        ("s2", am.S2), ("s3", am.S3), ("s4", am.S4), ("s5", am.S5), ("s6", am.S6), ("s7", am.S7),
        ("s8", am.S8), ("product_available", am.PRODUCT_AVAILABLE), ("e_stop_ok", am.E_STOP_OK),
        ("heartbeat", am.HEARTBEAT),
    ],
)
def test_each_field_lands_on_its_coil(field, address):
    block = encode_sensor_block(writes(**{field: True}))
    assert len(block) == len(am.SENSOR_BLOCK) == 11
    assert [am.SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]


def test_unused_coil_always_false():
    all_on = writes(**{f: True for f in SimWrites.__dataclass_fields__ if f not in ("fill_level", "anim")})
    block = encode_sensor_block(all_on)
    assert block[am.UNUSED_MX0_7 - am.SENSOR_BLOCK_START] is False
    assert sum(block) == 10


def test_fill_level_clamped_to_int():
    assert encode_fill_level(500) == 500
    assert encode_fill_level(499.6) == 500
    assert encode_fill_level(-3) == 0
    assert encode_fill_level(70000) == 32767


def test_decode_status():
    s = decode_status([1, 20, 0, 42], [False, True, False, True, False, False, False, False], [500, 2000, 1500], 2)
    assert (s.state, s.step, s.good_count, s.mode) == (1, 20, 42, 2)
    assert (s.fill_setpoint, s.rinse_time_ms, s.cap_time_ms) == (500, 2000, 1500)
    assert s.alm_fill_timeout and s.alm_comm_lost
    assert not (s.alm_index_jam or s.alm_outfeed_blocked)


def test_encode_animation():
    assert encode_animation(writes(anim=(0b1000_0001, 0b1, 0, 2, 3, 4))) == [129, 1, 0, 2, 3, 4]
    with pytest.raises(AssertionError):
        encode_animation(writes(anim=(1, 2, 3)))  # always 6 words


def test_client_refuses_hmi_addresses():
    plc = FillerPlc()  # works outside asyncio: the pymodbus client is only made when connecting
    # the guard fires before the link check, so no connection is needed
    with pytest.raises(ValueError):
        asyncio.run(plc._write_register(am.CMD, 1))
    with pytest.raises(ValueError):
        asyncio.run(plc._write_coils(am.MAN_C1, [True]))
    with pytest.raises(ValueError):  # animation block one word too long would reach %MW76 (not ours)
        asyncio.run(plc._write_registers(am.ANIM_START, [0] * 7))
    with pytest.raises(ValueError):  # bulk write one coil too long would reach %MX1.3
        asyncio.run(plc._write_coils(am.SENSOR_BLOCK_START, [False] * 12))


def test_foreign_writes_names_the_wrong_signals():
    w = writes(s4=True, heartbeat=True, fill_level=320)
    good = encode_sensor_block(w)
    assert foreign_writes(w, good, 320) == []
    forced = list(good)
    forced[am.S4 - am.SENSOR_BLOCK_START] = False  # e.g. S4 forced FALSE in the debugger
    assert foreign_writes(w, forced, 0) == ["s4", "fill_level"]
