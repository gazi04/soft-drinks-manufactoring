"""commands.py: the command queue and every command's effect on the plant.  uv run pytest"""

import pytest

from simulator import config
from simulator.commands import COMMANDS, SYRUP_COMMANDS, Command, CommandQueue
from simulator.events import Source
from simulator.plant import Plant


def applied(name, value) -> Plant:
    plant = Plant()
    q = CommandQueue()
    q.put(name, value)
    q.apply(plant)
    return plant


def test_every_command_sets_its_attribute():
    assert applied("supply_rate", 12).supply.per_min == 12
    assert applied("add_bottles", 5).supply.pending == 5
    assert applied("supply_paused", True).supply.paused is True
    assert applied("fill_flow", 40.0).filler.fill_flow_ml_s == 40.0
    assert applied("product_available", False).product_available is False
    assert applied("e_stop", True).e_stop_ok is False  # pressed = chain open
    assert applied("e_stop", False).e_stop_ok is True
    assert applied("index_jam", True).filler.index_jammed is True
    assert applied("labeler_running", False).labeler.running is False
    assert applied("heartbeat", False).heartbeat.enabled is False


SYRUP_COMMANDS_TESTED = {
    "deliver_syrup", "drain_syrup", "chiller_failed", "water_pump_failed", "syrup_pump_failed",
    "co2_valve_stuck", "vent_tank", "mix_level_stuck", "drain_mix_tank", "draw_buffer", "fill_buffer",
    "buffer_draw_scale", "syrup_heartbeat", "fill_buffer_to",
}


def applied_syrup(name, value, **start) -> Plant:
    plant = Plant(with_syrup=True)
    for key, v in start.items():
        setattr(plant.syrup, key, v)
    q = CommandQueue()
    q.put(name, value)
    q.apply(plant)
    return plant


def test_every_syrup_command_sets_its_attribute():
    assert applied_syrup("deliver_syrup", 100.0, syrup_l=500.0).syrup.syrup_l == 600
    assert applied_syrup("drain_syrup", 450.0).syrup.syrup_l == config.SYRUP_START_L - 450
    assert applied_syrup("chiller_failed", True).syrup.coolant_c == config.COOLANT_FAILED_C
    assert applied_syrup("chiller_failed", False, coolant_c=20.0).syrup.coolant_c == config.COOLANT_C
    assert applied_syrup("water_pump_failed", True).syrup.water_pump_failed is True
    assert applied_syrup("syrup_pump_failed", True).syrup.syrup_pump_failed is True
    assert applied_syrup("co2_valve_stuck", True).syrup.co2_valve_stuck is True
    assert applied_syrup("vent_tank", True, pressure_bar=7.0).syrup.pressure_bar == 0
    assert applied_syrup("vent_tank", False, pressure_bar=7.0).syrup.pressure_bar == 7.0  # not an action
    assert applied_syrup("mix_level_stuck", True, mix_l=123.4).syrup.mix_level_frozen == 123
    assert applied_syrup("mix_level_stuck", False, mix_level_frozen=5).syrup.mix_level_frozen is None
    assert applied_syrup("drain_mix_tank", True, mix_l=980.0).syrup.mix_l == 0
    assert applied_syrup("drain_mix_tank", False, mix_l=980.0).syrup.mix_l == 980
    assert applied_syrup("draw_buffer", 400.0).syrup.buffer_l == config.BUFFER_START_L - 400
    assert applied_syrup("fill_buffer", 1000.0).syrup.buffer_l == config.BUFFER_START_L + 1000
    assert applied_syrup("buffer_draw_scale", 100.0).buffer_draw_scale == 100.0
    assert applied_syrup("fill_buffer_to", 1900.0).syrup.buffer_l == 1900  # a level, not an amount
    assert applied_syrup("fill_buffer_to", 1900.0, buffer_l=1920.0).syrup.buffer_l == 1920  # never drains
    plant = applied_syrup("syrup_heartbeat", False)
    assert plant.syrup_heartbeat.enabled is False and plant.heartbeat.enabled is True  # only the syrup PLC's


def test_syrup_commands_are_marked_and_the_others_are_not():
    assert SYRUP_COMMANDS == SYRUP_COMMANDS_TESTED


def test_a_syrup_command_without_a_syrup_room_is_ignored_with_a_warning():
    plant, q = Plant(), CommandQueue()
    q.put("drain_syrup", 10.0)
    events = q.apply(plant)
    assert len(events) == 1 and events[0].is_warning and "ignored" in events[0].text


def test_every_command_is_tested_above():
    assert set(COMMANDS) == {
        "supply_rate", "add_bottles", "supply_paused", "fill_flow", "product_available",
        "e_stop", "index_jam", "labeler_running", "heartbeat",
    } | SYRUP_COMMANDS_TESTED


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("index_jamm", True),  # typo
        ("index_jam", 1),  # bool command needs a bool
        ("fill_flow", True),  # True is an int in Python, but not a flow
        ("add_bottles", 2.5),  # whole bottles only
        ("add_bottles", -1),
        ("supply_rate", -3.0),
    ],
)
def test_put_rejects_bad_commands(name, value):
    q = CommandQueue()
    with pytest.raises(ValueError):
        q.put(name, value)
    assert len(q) == 0  # nothing half-queued


def test_number_commands_accept_int_and_float():
    q = CommandQueue()
    q.put("fill_flow", 40)
    q.put("supply_rate", 0.5)
    assert len(q) == 2


def test_apply_empties_queue_in_order_and_logs_each_command():
    plant = Plant()
    q = CommandQueue()
    q.put("product_available", False)
    q.put_command(Command("product_available", True))  # the later command wins
    events = q.apply(plant)
    assert len(q) == 0
    assert plant.product_available is True
    assert [e.text for e in events] == ["command: product_available = False", "command: product_available = True"]
    assert all(e.source == Source.SIM and not e.is_warning for e in events)
    assert q.apply(plant) == []  # nothing left
