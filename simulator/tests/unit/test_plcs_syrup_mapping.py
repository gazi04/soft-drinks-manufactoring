"""plcs.py syrup room mapping functions (names <-> Modbus values). No PLC needed.  uv run pytest"""

import asyncio

import pytest

from simulator import address_map_syrup as sm
from simulator.machines.syrup import SyrupOutputs, SyrupSensors
from simulator.plant import SyrupWrites
from simulator.plcs import (
    SYRUP_ALARM_NAMES,
    SyrupPlc,
    decode_syrup_outputs,
    decode_syrup_status,
    encode_analog_block,
    encode_syrup_sensor_block,
    foreign_syrup_writes,
    from_register,
    to_register,
)


def writes(*, mix_high_high=False, e_stop_ok=False, heartbeat=False, **analog) -> SyrupWrites:
    values = dict(syrup_level=0, mix_level=0, buffer_level=0, product_temp=0, co2_pressure=0)
    values.update(analog)
    return SyrupWrites(SyrupSensors(mix_high_high=mix_high_high, **values), e_stop_ok, heartbeat)


def test_decode_outputs_in_coil_order():
    out = decode_syrup_outputs([True, False, True, False, False, True, False, False])  # padded to 8
    assert out == SyrupOutputs(water_pump=True, agitator=True, transfer_pump=True)


@pytest.mark.parametrize(
    "field, address",
    [("mix_high_high", sm.MIX_HIGH_HIGH), ("e_stop_ok", sm.E_STOP_OK), ("heartbeat", sm.HEARTBEAT)],
)
def test_each_field_lands_on_its_coil(field, address):
    block = encode_syrup_sensor_block(writes(**{field: True}))
    assert len(block) == len(sm.SENSOR_BLOCK) == 11
    assert [sm.SENSOR_BLOCK_START + i for i, v in enumerate(block) if v] == [address]


def test_spare_and_unused_coils_are_always_false():
    block = encode_syrup_sensor_block(writes(mix_high_high=True, e_stop_ok=True, heartbeat=True, mix_level=999))
    assert sum(block) == 3  # only the three real signals
    for spare in (*range(sm.MIX_HIGH_HIGH + 1, sm.MIX_HIGH_HIGH + 8), sm.UNUSED_MX1_0):
        assert block[spare - sm.SENSOR_BLOCK_START] is False


def test_analog_block_is_in_register_order():
    w = writes(syrup_level=600, mix_level=500, product_temp=158, co2_pressure=350, buffer_level=800)
    assert encode_analog_block(w) == [600, 500, 158, 350, 800]  # %MW12 .. %MW16
    assert (sm.SYRUP_LEVEL, sm.MIX_LEVEL, sm.PRODUCT_TEMP, sm.CO2_PRESSURE, sm.BUFFER_LEVEL) == tuple(range(1036, 1041))


def test_negative_values_travel_as_twos_complement():
    assert to_register(-5) == 65531
    assert from_register(65531) == -5
    assert encode_analog_block(writes(product_temp=-5))[2] == 65531  # a register is unsigned, the PLC INT is signed
    assert from_register(to_register(-32768)) == -32768 and from_register(to_register(32767)) == 32767


def test_values_outside_the_int_range_are_clamped_not_wrapped():
    assert to_register(70000) == 32767
    assert to_register(-70000) == 0x8000  # -32768
    assert to_register(0) == 0


def test_decode_status_alarms_in_address_order():
    alarms = [False, True, False, True, False, True, False, False]  # padded to 8
    s = decode_syrup_status([1, 30, 0, 7], alarms, mode=2)
    assert (s.state, s.step, s.batch_count, s.mode) == (1, 30, 7, 2)
    assert s.alm_over_pressure and s.alm_comm_lost and s.alm_dose_timeout  # indexes 1, 3, 5
    assert not (s.alm_overflow or s.alm_cool_timeout or s.alm_low_syrup)
    assert [name for name, on in s.alarms.items() if on] == ["Alm_OverPressure", "Alm_CommLost", "Alm_DoseTimeout"]
    assert len(SYRUP_ALARM_NAMES) == sm.ALARMS_COUNT == 6
    assert SYRUP_ALARM_NAMES[3] == "Alm_CommLost"  # the same bit as in every PLC


def test_foreign_writes_names_the_wrong_signals():
    w = writes(mix_high_high=True, heartbeat=True, mix_level=320, buffer_level=500)
    coils, regs = encode_syrup_sensor_block(w), encode_analog_block(w)
    assert foreign_syrup_writes(w, coils, regs) == []
    bad_coils = list(coils)
    bad_coils[sm.MIX_HIGH_HIGH - sm.SENSOR_BLOCK_START] = False  # e.g. forced FALSE in the debugger
    bad_coils[4] = True  # a spare bit set by somebody else (%MX0.4 = coil 8196)
    bad_regs = list(regs)
    bad_regs[4] = 0  # buffer_level
    assert foreign_syrup_writes(w, bad_coils, bad_regs) == ["mix_high_high", f"coil {sm.MIX_HIGH_HIGH + 4}", "buffer_level"]


def test_client_refuses_addresses_that_are_not_the_simulators():
    plc = SyrupPlc()  # works outside asyncio; the guard fires before the link check
    refused = [
        ("_write_register", (sm.CMD, 1)),
        ("_write_register", (sm.MODE_REQ, 1)),
        ("_write_register", (sm.WATER_BATCH, 500)),  # a setpoint (HMI)
        ("_write_coils", (sm.MAN_WATER_PUMP, [True])),  # a manual button (HMI)
        ("_write_coils", (sm.PRODUCT_AVAILABLE_OUT, [True])),  # the PLC's output
        ("_write_coils", (sm.ALM_COMM_LOST, [False])),  # the PLC's alarm
        ("_write_coils", (sm.SENSOR_BLOCK_START, [False] * 12)),  # one coil too long reaches %MX1.3
        ("_write_registers", (sm.ANALOG_START, [0] * 6)),  # one register too long reaches %MW17 (spare)
        ("_write_registers", (sm.ANALOG_START - 1, [0] * 5)),  # starts one register early (%MW11)
    ]
    for method, args in refused:
        with pytest.raises(ValueError):
            asyncio.run(getattr(plc, method)(*args))
