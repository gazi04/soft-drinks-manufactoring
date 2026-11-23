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

A scenario has a `target`: "filler" (default), "syrup" or "labeler". Its conditions look at the status of
that PLC (every status type has .step / .state / .alarms). A syrup scenario needs the syrup room in the
plant (not --filler-only); the operator presses Reset / Start with `tests/hmi.py --syrup ...`. A labeler
scenario needs the real labeler (not --no-labeler); Reset / Start with `tests/hmi.py --labeler ...`. A packer
scenario needs the real packer (not --no-packer); Reset / Start with `tests/hmi.py --packer ...`.
Faults that leave a mark the PLC cannot clear itself (tank full of water, 7 bar in the headspace)
end with the maintenance command (drain_mix_tank, vent_tank) before the note.
"""

from collections.abc import Callable
from dataclasses import dataclass

from simulator import config
from simulator.commands import LABELER_COMMANDS, PACKER_COMMANDS, SYRUP_COMMANDS, Command, check
from simulator.events import Event
from simulator.plant import Plant
from simulator.plcs import LabelerStatus, PackerStatus, PlcStatus, SyrupStatus

# PLC step and state numbers (plcs.STEP_NAMES / STATE_NAMES)
IDLE, INDEX, FAULT = 0, 20, 900
BLOCKED = 3
# Syrup room steps (plcs.SYRUP_STEP_NAMES)
S_WAIT_DEMAND, S_DOSE_WATER, S_MIX, S_COOL, S_CARBONATE, S_TRANSFER = 5, 10, 30, 40, 50, 60
# Labeler steps (plcs.LABELER_STEP_NAMES)
L_WAIT_BOTTLE, L_PRESS, L_DISCHARGE = 10, 24, 40
# Packer steps (plcs.PACKER_STEP_NAMES, PACKER_WRAP_STEP_NAMES)
P_COUNT, P_CUT = 10, 22
TARGETS = ("filler", "syrup", "labeler", "packer")


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


def in_wrap_step(step: int) -> Condition:
    """The packer's second sequencer (WrapStep)."""
    return Condition(f"wrap step {step}", lambda p, s: s is not None and s.wrap_step == step)


def pack_in_oven() -> Condition:
    return Condition("a pack in the oven", lambda p, s: bool(p.packer.oven.packs))


def no_product() -> Condition:
    return Condition("filler has no Product_Available", lambda p, s: not p.filler_product_available)


def product_available() -> Condition:
    return Condition("filler has Product_Available", lambda p, s: p.filler_product_available)


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
    target: str = "filler"  # which PLC's status the conditions see: "filler", "syrup", "labeler" or "packer"

    def __post_init__(self) -> None:
        if self.target not in TARGETS:
            raise ValueError(f"scenario {self.name}: target must be one of {TARGETS}, got {self.target!r}")
        for step in self.steps:  # a typo in a scenario fails when it is defined, not mid-run
            if step.command is not None:
                check(step.command)
                if self.target != "syrup" and step.command.name in SYRUP_COMMANDS:
                    raise ValueError(f"scenario {self.name}: {step.command.name} needs target='syrup'")
                if self.target != "labeler" and step.command.name in LABELER_COMMANDS:
                    raise ValueError(f"scenario {self.name}: {step.command.name} needs target='labeler'")
                if self.target != "packer" and step.command.name in PACKER_COMMANDS:
                    raise ValueError(f"scenario {self.name}: {step.command.name} needs target='packer'")


def uses_command(scenario: Scenario, name: str) -> bool:
    """True if one of the scenario's steps sends the command `name` (e.g. labeler_running needs the stand-in)."""
    return any(step.command is not None and step.command.name == name for step in scenario.steps)


class ScenarioRunner:
    """Walks through a scenario; due() is called once per tick by the loop."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.index = 0  # next step to fire
        self._since: float | None = None  # simulation time the previous step fired

    @property
    def done(self) -> bool:
        return self.index >= len(self.scenario.steps)

    def due(
        self,
        plant: Plant,
        status: PlcStatus | None,
        syrup_status: SyrupStatus | None = None,
        labeler_status: LabelerStatus | None = None,
        packer_status: PackerStatus | None = None,
    ) -> tuple[list[Command], list[Event]]:
        """Commands of every step that is due now (several if they follow at once).

        Conditions see `syrup_status` in a syrup scenario, `labeler_status` in a labeler scenario,
        `packer_status` in a packer scenario, `status` (the filler) otherwise."""
        if self.scenario.target == "syrup":
            status = syrup_status
        elif self.scenario.target == "labeler":
            status = labeler_status
        elif self.scenario.target == "packer":
            status = packer_status
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

    def due(
        self,
        plant: Plant,
        status: PlcStatus | None,
        syrup_status: SyrupStatus | None = None,
        labeler_status: LabelerStatus | None = None,
        packer_status: PackerStatus | None = None,
    ) -> tuple[list[Command], list[Event]]:
        if self.runner is None:
            return [], []
        return self.runner.due(plant, status, syrup_status, labeler_status, packer_status)


# --- Built-in scenarios ------------------------------------------------------------
def _c(name: str, value) -> Command:
    return Command(name, value)


RESET_START = "cause removed: press Reset + Start on the HMI (tests/hmi.py reset start)"
RESET_START_LABELER = "cause removed: press Reset + Start on the labeler (tests/hmi.py --labeler reset start)"
RESET_START_PACKER = "cause removed: press Reset + Start on the packer (tests/hmi.py --packer reset start)"

_ALL: tuple[Scenario, ...] = (
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
    # --- Syrup room (target="syrup"): press Reset + Start with `tests/hmi.py --syrup ...` ----------
    Scenario("low_syrup", "syrup tank too low for the next batch: Starved + Alm_LowSyrup, continues after the refill", (
        Step(plc_running()),
        Step(0, _c("drain_syrup", 450.0)),  # 150 L left: batch 1 takes 90, batch 2 finds 60 < 90
        Step(in_step(S_TRANSFER)),
        Step(in_step(S_WAIT_DEMAND)),
        Step(0, _c("draw_buffer", 400.0)),  # buffer below BufferLow: a new batch is needed
        Step(alarm("Alm_LowSyrup")),
        Step(5, _c("deliver_syrup", 500.0), "syrup delivered: the PLC continues by itself, no Reset needed"),
    ), target="syrup"),
    Scenario("cooling_failure", "chiller dead in COOL: Alm_CoolTimeout after 60 s, FAULT", (
        Step(plc_running()),
        Step(in_step(S_COOL), _c("chiller_failed", True)),
        Step(alarm("Alm_CoolTimeout")),
        Step(2, _c("chiller_failed", False), RESET_START + "; the batch resumes at MIX"),
    ), target="syrup"),
    Scenario("over_pressure", "CO2 valve stuck open in CARBONATE: Alm_OverPressure, FAULT", (
        Step(plc_running()),
        Step(in_step(S_CARBONATE), _c("co2_valve_stuck", True)),
        Step(alarm("Alm_OverPressure")),
        Step(2, _c("co2_valve_stuck", False)),
        Step(0, _c("vent_tank", True), RESET_START + "; valve repaired and tank vented"),
    ), target="syrup"),
    Scenario("pump_failure", "water pump dead in DOSE_WATER: Alm_DoseTimeout after 60 s, FAULT", (
        Step(plc_running()),
        Step(in_step(S_DOSE_WATER), _c("water_pump_failed", True)),
        Step(alarm("Alm_DoseTimeout")),
        Step(2, _c("water_pump_failed", False), RESET_START + "; the dose continues from the level"),
    ), target="syrup"),
    Scenario("level_sensor_stuck", "mixing level sensor stuck low: MixHighHigh -> Alm_Overflow, pumps off, FAULT", (
        Step(plc_running()),
        Step(in_step(S_DOSE_WATER), _c("mix_level_stuck", True)),
        Step(alarm("Alm_Overflow")),
        Step(2, _c("mix_level_stuck", False)),
        Step(0, _c("drain_mix_tank", True), RESET_START + "; sensor repaired, tank drained to waste"),
    ), target="syrup"),
    Scenario("buffer_full", "buffer has no room for the batch: WAIT_BUFFER (Blocked), continues when product is used", (
        Step(plc_running()),
        # Top the buffer up TO 1900 L (a level, not an amount: the level at MIX depends on the filler).
        # - not above BufferMax 1950: BufferLevel > BufferMax is Alm_Overflow -> FAULT (live 2026-11-13
        #   with "+1500 L": 2000 L, FAULT instead of Blocked);
        # - Blocked for sure: the PLC checks Buffer_Room ~80 s later (end of CARBONATE); even a filler
        #   drawing 3 L/s leaves >= 1660 L, and 1660 + 590 > 1950. ("+1000 L" only blocked when MIX
        #   began above ~535 L.)
        # - draw_buffer 700 then leaves <= 1200 L: 1200 + 590 <= 1950, TRANSFER runs.
        Step(in_step(S_MIX), _c("fill_buffer_to", 1900.0)),
        Step(in_state(BLOCKED)),
        Step(3, _c("draw_buffer", 700.0)),
    ), target="syrup"),
    Scenario("buffer_starved", "filler draws faster than the syrup room delivers: buffer empty, filler Starved, recovers", (
        Step(plc_running()),
        Step(10, _c("buffer_draw_scale", 100.0)),
        Step(no_product()),
        Step(0, _c("buffer_draw_scale", config.BUFFER_DRAW_SCALE)),
        Step(product_available()),
    ), target="syrup"),
    # --- Labeler (target="labeler"): press Reset + Start with `tests/hmi.py --labeler ...` ----------
    Scenario("web_torn", "label web torn before a bottle: WRAP sees no Label_Mark, Alm_LabelTimeout after 5 s, FAULT", (
        Step(plc_running()),
        Step(in_step(L_WAIT_BOTTLE), _c("web_torn", True)),  # between two bottles: no half label
        Step(alarm("Alm_LabelTimeout")),
        Step(2, _c("web_torn", False), RESET_START_LABELER + "; web threaded again, the bottle is labelled"),
    ), target="labeler"),
    Scenario("bell_stuck", "bell stuck down in PRESS: UNCLAMP sees Bell_Down, Alm_PocketJam after 2 s, FAULT", (
        Step(plc_running()),
        Step(in_step(L_PRESS), _c("bell_stuck", True)),
        Step(alarm("Alm_PocketJam")),
        Step(2, _c("bell_stuck", False), RESET_START_LABELER + "; bell freed, the labelled bottle is discharged"),
    ), target="labeler"),
    Scenario("heater_failed", "glue heater burnt out: WARM_UP never ready, Alm_GlueHeatTimeout after 120 s, FAULT", (
        Step(plc_running(), _c("heater_failed", True)),
        Step(alarm("Alm_GlueHeatTimeout")),
        Step(2, _c("heater_failed", False), RESET_START_LABELER + "; heater replaced, warm-up again"),
    ), target="labeler"),
    Scenario("heater_stuck_on", "glue heater stuck on: GlueTemp reaches 200 degC, Alm_GlueOverTemp, FAULT", (
        Step(plc_running()),
        Step(in_step(L_WAIT_BOTTLE), _c("heater_stuck_on", True)),
        Step(alarm("Alm_GlueOverTemp")),
        Step(2, _c("heater_stuck_on", False),
             RESET_START_LABELER + " once GlueTemp < 200 degC; WARM_UP until the glue is back in the window"),
    ), target="labeler"),
    Scenario("roll_empty", "label roll runs empty: Starved + Alm_LabelEmpty, no FAULT, continues after the roll change", (
        Step(plc_running(), _c("load_roll", 2)),  # a roll with 2 labels left
        Step(alarm("Alm_LabelEmpty")),
        Step(5, _c("load_roll", config.ROLL_LABELS), "new roll loaded: the labeler continues by itself, no Reset"),
    ), target="labeler"),
    Scenario("glue_refill", "glue low, then cold glue blocks: Glue_Low warning, WARM_UP after the bottle, no FAULT", (
        Step(plc_running(), _c("glue_level", 0.9)),  # below GLUE_LOW_KG: Glue_Low
        Step(in_step(L_DISCHARGE), _c("add_glue", 1.0)),  # the label is on: the cold glue hits no wrap
    ), target="labeler"),
    Scenario("gate_jam", "bottle stuck in the gate: Bottle_Entering 2 s, Alm_PocketJam, FAULT", (
        Step(plc_running()),
        Step(in_step(L_WAIT_BOTTLE), _c("gate_jam", True)),
        Step(alarm("Alm_PocketJam")),
        Step(2, _c("clear_gate", True)),  # the operator takes the stuck bottle away
        Step(0, _c("gate_jam", False), RESET_START_LABELER + "; gate cleared, the next bottle comes"),
    ), target="labeler"),
    Scenario("packer_stopped", "packer stops: C3 fills, WAIT_OUTFEED (Blocked), warning after 2 min, continues by itself", (
        Step(plc_running()),
        Step(5, _c("packer_running", False)),
        Step(alarm("Alm_OutfeedBlocked"), _c("packer_running", True)),
    ), target="labeler"),
    # --- Packer (target="packer"): press Reset + Start with `tests/hmi.py --packer ...` -----------
    Scenario("film_torn", "film torn before the first sheet: FEED counts no pulse, Alm_FilmFault after 10 s, FAULT", (
        Step(plc_running(), _c("film_torn", True)),
        Step(alarm("Alm_FilmFault")),
        Step(2, _c("film_torn", False), RESET_START_PACKER + "; film threaded again, the sheet is fed"),
    ), target="packer"),
    # READY passes within one PLC scan when the oven is ready, so the sheet is dropped while the PLC
    # cuts (CUT lasts 0.3 s): at the end of CUT the slot is empty and READY waits for it
    Scenario("sheet_dropped", "sheet falls out of the slot at the cut: READY without a sheet, Alm_FilmFault after 1 s",
             (
        Step(plc_running()),
        Step(in_wrap_step(P_CUT), _c("drop_sheet", True)),
        Step(alarm("Alm_FilmFault")),
        Step(2, _c("put_sheet", 850.0), RESET_START_PACKER + "; the sheet put back by hand is used, no new feed"),
    ), target="packer"),
    Scenario("film_empty", "film roll runs empty: the next pack waits, Starved + Alm_FilmEmpty, no FAULT, roll change",
             (
        Step(plc_running(), _c("load_film", 1.3)),  # one sheet starts (1.3 m > the 1.25 m tail), then Film_Empty
        Step(alarm("Alm_FilmEmpty")),
        Step(5, _c("load_film", config.FILM_ROLL_MM / 1000), "new roll loaded: the packer continues by itself"),
    ), target="packer"),
    Scenario("pusher_stuck", "group pusher stuck: PUSH without Pusher_Out, Alm_PackJam after 3 s, FAULT", (
        Step(plc_running(), _c("pusher_stuck", True)),
        Step(alarm("Alm_PackJam")),
        Step(2, _c("pusher_stuck", False), RESET_START_PACKER + "; the same group is pushed"),
    ), target="packer"),
    Scenario("wrap_bar_stuck", "wrap bar does not turn: Alm_WrapTimeout after 5 s, FAULT; that pack is not wrapped", (
        Step(plc_running(), _c("wrap_bar_stuck", True)),
        Step(alarm("Alm_WrapTimeout")),
        Step(2, _c("wrap_bar_stuck", False), RESET_START_PACKER + "; bar freed, the next pack is wrapped"),
    ), target="packer"),
    Scenario("oven_heater_failed", "oven heater burnt out: WARM_UP never ready, Alm_OvenHeatTimeout after 120 s, FAULT",
             (
        Step(plc_running(), _c("oven_heater_failed", True)),
        Step(alarm("Alm_OvenHeatTimeout")),
        Step(2, _c("oven_heater_failed", False), RESET_START_PACKER + "; heater replaced, warm-up again"),
    ), target="packer"),
    Scenario("oven_heater_stuck_on", "oven heater stuck on: 230 degC, Alm_OvenOverTemp, FAULT, blower runs on 30 s", (
        Step(plc_running()),
        Step(in_step(P_COUNT), _c("oven_heater_stuck_on", True)),
        Step(alarm("Alm_OvenOverTemp")),
        Step(2, _c("oven_heater_stuck_on", False),
             RESET_START_PACKER + " once OvenTemp < 230 degC; WARM_UP until the oven is back in its window"),
    ), target="packer"),
    Scenario("blower_failed", "blower runs without air: heater off, Alm_AirflowLost after 3 s, FAULT", (
        Step(plc_running()),
        Step(in_step(P_COUNT), _c("blower_failed", True)),
        Step(alarm("Alm_AirflowLost")),
        Step(2, _c("blower_failed", False), RESET_START_PACKER + "; blower repaired"),
    ), target="packer"),
    Scenario("pack_stuck_in_oven", "pack caught in the oven: no Oven_Exit, Alm_OvenJam after 20 s, FAULT, oven cleared",
             (
        Step(plc_running()),
        Step(pack_in_oven(), _c("oven_jam", True)),
        Step(alarm("Alm_OvenJam")),
        Step(2, _c("clear_oven", True)),  # the operator takes the pack out
        Step(0, _c("oven_jam", False), RESET_START_PACKER + "; Reset also zeroes PacksInOven"),
    ), target="packer"),
    Scenario("palletizer_stopped", "palletizer stops: table full, READY waits (Blocked), warning after 2 min, continues",
             (
        Step(plc_running(), _c("palletizer_running", False)),
        Step(alarm("Alm_OutfeedBlocked"), _c("palletizer_running", True), "the packer continues by itself"),
    ), target="packer"),
)
SCENARIOS: dict[str, Scenario] = {s.name: s for s in _ALL}
# a second scenario with the same name would silently replace the first (found: the packer's roll_empty
# hid the labeler's): names must be unique across all machines
assert len(SCENARIOS) == len(_ALL), sorted(n for n in SCENARIOS if [s.name for s in _ALL].count(n) > 1)
