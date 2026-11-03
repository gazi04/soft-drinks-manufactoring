"""Scenarios: fixed command sequences for repeatable headless runs.

    uv run simulator --headless --scenario index_jam
    uv run simulator --list-scenarios

A scenario is a list of steps. Each step WAITS, then puts one command on the
queue (or none: a pure wait). It waits either

    - a number of seconds after the previous step fired (a timeline), or
    - until a condition is true ("the PLC is in INDEX", "the bottle at S4 has
      200 ml"), for faults that must hit a certain moment of the cycle.

Steps fire strictly one after the other. Every scenario starts with "until the
PLC runs": the simulator can be started before or after Start is pressed, and a
timeline must not run while the PLC still waits in IDLE.

A scenario can never press Reset or Start: those are HMI commands and the
simulator never writes them (one-writer rule). A fault scenario therefore ends
by removing the cause (jam cleared, heartbeat on) and prints a note saying what
the operator has to press.
"""

from collections.abc import Callable
from dataclasses import dataclass

from simulator import config
from simulator.commands import Command, check
from simulator.events import Event
from simulator.plant import Plant
from simulator.plcs import PlcStatus

# PLC step and state numbers (plcs.STEP_NAMES / STATE_NAMES)
IDLE, INDEX, FAULT = 0, 20, 900
BLOCKED = 3


@dataclass(frozen=True)
class Condition:
    """A named test of the world and the last PLC status; the name goes into the log."""

    name: str
    test: Callable[[Plant, PlcStatus | None], bool]

    def __call__(self, plant: Plant, status: PlcStatus | None) -> bool:
        return self.test(plant, status)


# --- Conditions ------------------------------------------------------------------
def plc_running() -> Condition:
    return Condition("PLC started", lambda p, s: s is not None and s.step != IDLE)


def in_step(step: int) -> Condition:
    return Condition(f"step {step}", lambda p, s: s is not None and s.step == step)


def in_state(state: int) -> Condition:
    return Condition(f"state {state}", lambda p, s: s is not None and s.state == state)


def alarm(name: str) -> Condition:
    return Condition(f"{name} ON", lambda p, s: s is not None and s.alarms[name])


def fill_level_at_least(ml: int) -> Condition:
    return Condition(f"bottle at S4 has {ml} ml", lambda p, s: p.filler.sensors().fill_level >= ml)


# --- Scenario --------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    wait: float | Condition  # seconds after the previous step, or a condition
    command: Command | None = None  # None = only wait
    note: str = ""  # shown when the step fires, e.g. what the operator has to press

    def describe(self) -> str:
        when = f"after {self.wait:g} s" if isinstance(self.wait, (int, float)) else f"when {self.wait.name}"
        what = f"{self.command.name} = {self.command.value}" if self.command else "wait done"
        return f"{when}: {what}" + (f" ({self.note})" if self.note else "")


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    steps: tuple[Step, ...]

    def __post_init__(self) -> None:
        for step in self.steps:  # a typo in a scenario fails when it is defined, not mid-run
            if step.command is not None:
                check(step.command)


class ScenarioRunner:
    """Walks through a scenario; due() is called once per tick by the loop."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.index = 0  # next step to fire
        self._since: float | None = None  # simulation time the previous step fired

    @property
    def done(self) -> bool:
        return self.index >= len(self.scenario.steps)

    def due(self, plant: Plant, status: PlcStatus | None) -> tuple[list[Command], list[Event]]:
        """Commands of every step that is due now (several if they follow at once)."""
        if self._since is None:
            self._since = plant.now  # the timeline starts with the first tick
        commands: list[Command] = []
        events: list[Event] = []
        while not self.done:
            step = self.scenario.steps[self.index]
            if isinstance(step.wait, (int, float)):
                ready = plant.now - self._since >= step.wait - 1e-9
            else:
                ready = step.wait(plant, status)
            if not ready:
                break
            self.index += 1
            self._since = plant.now
            if step.command is not None:
                commands.append(step.command)
            n = len(self.scenario.steps)
            events.append(Event.sim(plant.now, f"scenario {self.scenario.name} {self.index}/{n} {step.describe()}"))
        return commands, events


class ScenarioSlot:
    """A place for one scenario that can be started while the loop runs (control panel).

    The loop only calls due(), like on a ScenarioRunner; an empty slot gives nothing.
    Starting a new scenario replaces the old one, which stops where it was.
    """

    def __init__(self) -> None:
        self.runner: ScenarioRunner | None = None

    def start(self, scenario: Scenario) -> None:
        self.runner = ScenarioRunner(scenario)

    def due(self, plant: Plant, status: PlcStatus | None) -> tuple[list[Command], list[Event]]:
        if self.runner is None:
            return [], []
        return self.runner.due(plant, status)


# --- Built-in scenarios ------------------------------------------------------------
def _c(name: str, value) -> Command:
    return Command(name, value)


RESET_START = "cause removed: press Reset + Start on the HMI (tests/hmi.py reset start)"

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("gaps", "bottles in groups of 3, 2, 3 with gaps: empty pockets skipped, Starved in between", (
            Step(0, _c("supply_rate", 0)),
            Step(plc_running()),
            Step(0, _c("add_bottles", 3)),
            Step(60, _c("add_bottles", 2)),  # 3 bottles need ~45 s to leave: the filler runs empty
            Step(60, _c("add_bottles", 3)),
        )),
        Scenario("no_product", "syrup room stops delivering while a bottle fills: Starved, no alarm", (
            Step(plc_running()),
            Step(fill_level_at_least(200), _c("product_available", False)),
            Step(15, _c("product_available", True)),
        )),
        Scenario("labeler_stopped", "labeler stops: C2 fills, Blocked, alarm after 2 min, continues by itself", (
            Step(plc_running()),
            Step(10, _c("labeler_running", False)),
            Step(alarm("Alm_OutfeedBlocked"), _c("labeler_running", True)),
        )),
        Scenario("index_jam", "index belt blocked in INDEX: Alm_IndexJam after 5 s, FAULT", (
            Step(plc_running()),
            Step(in_step(INDEX), _c("index_jam", True)),
            Step(in_step(FAULT)),
            Step(2, _c("index_jam", False), RESET_START),
        )),
        Scenario("e_stop", "E-stop while a bottle is half filled: all off, release + Start tops it up", (
            Step(plc_running()),
            Step(fill_level_at_least(200), _c("e_stop", True)),
            Step(3, _c("e_stop", False), "E-stop released: press Start on the HMI (tests/hmi.py start)"),
        )),
        Scenario("heartbeat_off", "heartbeat frozen: Alm_CommLost after 3 s, outputs off", (
            Step(plc_running()),
            Step(fill_level_at_least(200), _c("heartbeat", False)),  # same moment every run
            Step(alarm("Alm_CommLost")),
            Step(2, _c("heartbeat", True), RESET_START),
        )),
        Scenario("slow_fill", "fill flow 40 ml/s: 500 ml need 12.5 s > 10 s, Alm_FillTimeout, FAULT", (
            Step(plc_running()),
            Step(0, _c("fill_flow", 40.0)),
            Step(in_step(FAULT)),
            Step(0, _c("fill_flow", config.FILL_FLOW_ML_S), RESET_START),
        )),
    )
}
