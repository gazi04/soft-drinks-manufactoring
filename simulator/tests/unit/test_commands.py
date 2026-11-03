"""commands.py: the command queue and every command's effect on the plant.  uv run pytest"""

import pytest

from simulator.commands import COMMANDS, Command, CommandQueue
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


def test_every_command_is_tested_above():
    assert set(COMMANDS) == {
        "supply_rate", "add_bottles", "supply_paused", "fill_flow", "product_available",
        "e_stop", "index_jam", "labeler_running", "heartbeat",
    }


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
