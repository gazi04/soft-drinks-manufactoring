"""Commands: the only way to change the simulated world while it runs.

    control panel (stage 8) ─┐
    scenario (scenarios.py) ─┼─► CommandQueue.put() ─► tick start: apply(plant)
    test script             ─┘

A command is a name and a value, e.g. Command("index_jam", True). put() checks
it at once (a typo fails where it was made, not somewhere inside the tick);
apply() runs at the start of a tick (report section 7.3, step 1), so the world
only ever changes in one place, between two physics steps.

Every command only sets an attribute that the plant already has (the stand-ins
were built with plain switches for this, see standins.py). A command never
decides anything for the PLC: "E-stop pressed" only changes the E_Stop_OK wire,
switching the outputs off is the PLC's job.

Injected faults are logged as normal SIM events, not warnings: the fault is
intended. What matters is the PLC's reaction, which the watcher logs next to it.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from simulator.events import Event
from simulator.plant import Plant


@dataclass(frozen=True)
class Command:
    name: str
    value: bool | int | float


@dataclass(frozen=True)
class _Spec:
    kind: type  # bool, int or float
    setter: Callable[[Plant, bool | int | float], None]
    help: str


def _set_supply_rate(plant: Plant, v) -> None:
    plant.supply.per_min = v


def _add_bottles(plant: Plant, v) -> None:
    plant.supply.add(v)


def _set_supply_paused(plant: Plant, v) -> None:
    plant.supply.paused = v


def _set_fill_flow(plant: Plant, v) -> None:
    plant.filler.fill_flow_ml_s = v


def _set_product_available(plant: Plant, v) -> None:
    plant.product_available = v


def _set_e_stop(plant: Plant, v) -> None:
    plant.e_stop_ok = not v  # pressed = the safety chain is open = E_Stop_OK FALSE


def _set_index_jam(plant: Plant, v) -> None:
    plant.filler.index_jammed = v


def _set_labeler_running(plant: Plant, v) -> None:
    plant.labeler.running = v


def _set_heartbeat(plant: Plant, v) -> None:
    plant.heartbeat.enabled = v


COMMANDS: dict[str, _Spec] = {
    "supply_rate": _Spec(float, _set_supply_rate, "bottles per minute put on C1 (0 = none)"),
    "add_bottles": _Spec(int, _add_bottles, "put n extra bottles on C1"),
    "supply_paused": _Spec(bool, _set_supply_paused, "TRUE = the supply puts no bottles on C1"),
    "fill_flow": _Spec(float, _set_fill_flow, "fill flow in ml/s (100 = normal, < 50 = fill timeout)"),
    "product_available": _Spec(bool, _set_product_available, "Product_Available wire from the syrup room"),
    "e_stop": _Spec(bool, _set_e_stop, "TRUE = E-stop pressed (E_Stop_OK FALSE)"),
    "index_jam": _Spec(bool, _set_index_jam, "TRUE = index belt blocked (motor ON, belt stands)"),
    "labeler_running": _Spec(bool, _set_labeler_running, "FALSE = labeler takes no bottles, C2 fills up"),
    "heartbeat": _Spec(bool, _set_heartbeat, "FALSE = heartbeat frozen (PLC: Alm_CommLost after 3 s)"),
}


def check(command: Command) -> None:
    """Raise ValueError if the name is unknown or the value has the wrong type."""
    spec = COMMANDS.get(command.name)
    if spec is None:
        raise ValueError(f"unknown command {command.name!r} (known: {', '.join(COMMANDS)})")
    v = command.value
    if spec.kind is bool:
        ok = isinstance(v, bool)
    elif spec.kind is int:
        ok = isinstance(v, int) and not isinstance(v, bool)  # True is an int in Python
    else:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    if not ok:
        raise ValueError(f"{command.name} needs a {spec.kind.__name__}, got {v!r}")
    if spec.kind is not bool and v < 0:
        raise ValueError(f"{command.name} must not be negative, got {v!r}")


class CommandQueue:
    """Commands waiting for the start of the next tick, applied in the order they came."""

    def __init__(self) -> None:
        self._queue: deque[Command] = deque()

    def put(self, name: str, value: bool | int | float) -> None:
        self.put_command(Command(name, value))

    def put_command(self, command: Command) -> None:
        check(command)
        self._queue.append(command)

    def apply(self, plant: Plant) -> list[Event]:
        """Apply every waiting command to the plant; one SIM event per command."""
        events: list[Event] = []
        while self._queue:
            command = self._queue.popleft()
            COMMANDS[command.name].setter(plant, command.value)
            events.append(Event.sim(plant.now, f"command: {command.name} = {command.value}"))
        return events

    def __len__(self) -> int:
        return len(self._queue)
