"""The control panel: the engineer's test bench (report section 7.5).

    ┌ header: PLC link, step, state, alarms, GoodCount, dt ─────────────────┐
    │ world: C1, belt pockets, C2, sensor lamps  │ controls: supply, faults,│
    │ (the physical truth)                       │ stand-in signals, scenario│
    ├ event log (filter with l) ─────────────────────────────────────────────┤
    └ footer: q quit, l log filter ──────────────────────────────────────────┘

How it runs:
- The tick loop (loop.run) is a Textual worker. Textual and the loop share one
  asyncio event loop, so a control can call queue.put() directly; the loop
  applies the command at the start of its next tick.
- The screen is redrawn by a timer 4 times per second, not on every tick:
  drawing 10 times per second would steal time from the loop (late ticks).
- The panel only CHANGES THE WORLD (supply, faults, stand-in signals). It never
  writes Cmd, mode or setpoints: Start / Stop / Reset belong to the HMI
  (tests/hmi.py until FUXA exists).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Input, Label, RichLog, Select, Static, Switch

from simulator import config, loop
from simulator.commands import CommandQueue
from simulator.events import Event, EventLog
from simulator.plant import Plant
from simulator.plcs import FillerPlc
from simulator.scenarios import SCENARIOS, ScenarioSlot
from simulator.ui import view

# switch id -> (command, the plant value the switch shows, label)
SWITCHES: dict[str, tuple[str, Callable[[Plant], bool], str]] = {
    "sw-paused": ("supply_paused", lambda p: p.supply.paused, "Pause supply"),
    "sw-product": ("product_available", lambda p: p.product_available, "Product available"),
    "sw-estop": ("e_stop", lambda p: not p.e_stop_ok, "E-stop pressed"),
    "sw-jam": ("index_jam", lambda p: p.filler.index_jammed, "Index belt jam"),
    "sw-labeler": ("labeler_running", lambda p: p.labeler.running, "Labeler running"),
    "sw-heartbeat": ("heartbeat", lambda p: p.heartbeat.enabled, "Heartbeat"),
}

# button id -> (input id, command, type of the value)
VALUE_BUTTONS: dict[str, tuple[str, str, type]] = {
    "bt-rate": ("in-rate", "supply_rate", float),
    "bt-add": ("in-add", "add_bottles", int),
    "bt-flow": ("in-flow", "fill_flow", float),
}

REFRESH_S = 0.25


class SimulatorApp(App):
    TITLE = "Soft drink line simulator"
    CSS = """
    #header { height: auto; padding: 0 1; background: $boost; }
    #main { height: 24; }
    #world { width: 1fr; border: round $primary; padding: 0 1; }
    #controls { width: 50; border: round $primary; padding: 0 1; }
    .row { height: 1; }
    .switch-row { margin-bottom: 1; }  /* one-line switches stacked without a gap read as one block */
    .row Label { width: 20; }
    .row Input { width: 10; }
    .row Button { min-width: 8; margin-left: 1; }
    Switch { height: 1; border: none; padding: 0; width: 6; }
    #in-scenario { width: 18; }
    #scenario-status { height: 1; color: $text-muted; }
    #log-title { height: 1; padding: 0 1; background: $boost; }
    #log { height: 1fr; }
    """
    BINDINGS = [
        ("q", "quit_panel", "Quit"),
        ("ctrl+q", "quit_panel", "Quit"),
        ("l", "next_filter", "Log filter"),
    ]

    def __init__(self, plant: Plant, plc: FillerPlc, log_path: Path | None, scenario: str | None = None) -> None:
        super().__init__()
        self.plant = plant
        self.plc = plc
        self.queue = CommandQueue()
        self.slot = ScenarioSlot()
        if scenario:
            self.slot.start(SCENARIOS[scenario])
        # not "self.log": Textual's App already uses that name for its own logger
        self.events = EventLog(file_path=log_path, echo=self._show_event)
        self.stats: loop.LoopStats | None = None
        self.log_filter = "all"
        self._stop = asyncio.Event()
        self._worker = None
        self._log_widget: RichLog | None = None

    # --- Layout ----------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="main"):
            yield Static(id="world")
            with VerticalScroll(id="controls"):
                yield self._value_row("Supply /min", "in-rate", f"{self.plant.supply.per_min:g}", "number", "bt-rate", "Set")
                yield self._value_row("Add bottles", "in-add", "5", "integer", "bt-add", "Add")
                yield self._value_row("Fill flow ml/s", "in-flow", f"{config.FILL_FLOW_ML_S:g}", "number", "bt-flow", "Set")
                for switch_id, (_, value, label) in SWITCHES.items():
                    yield Horizontal(Label(label), Switch(value=value(self.plant), id=switch_id), classes="row switch-row")
                options = [(name, name) for name in SCENARIOS]
                yield Horizontal(
                    Label("Scenario"),
                    Select(options, id="in-scenario", compact=True, prompt="choose"),
                    Button("Run", id="bt-scenario", compact=True),
                    classes="row",
                )
                yield Static(id="scenario-status")
        yield Static(id="log-title")
        yield RichLog(id="log", max_lines=1000, markup=False)
        yield Footer()

    @staticmethod
    def _value_row(label: str, input_id: str, value: str, kind: str, button_id: str, text: str) -> Horizontal:
        return Horizontal(
            Label(label),
            Input(value=value, type=kind, id=input_id, compact=True),
            Button(text, id=button_id, compact=True),
            classes="row",
        )

    # --- Start: the loop as a worker, the redraw timer -------------------------------
    def on_mount(self) -> None:
        self._log_widget = self.query_one("#log", RichLog)
        self._update_log_title()
        self._worker = self.run_worker(self._run_loop(), exclusive=True, name="tick loop")
        self.set_interval(REFRESH_S, self.refresh_view)
        self.refresh_view()

    async def _run_loop(self) -> None:
        self.stats = await loop.run(
            self.plc, self.plant, self.events,
            stop=self._stop, on_tick=self._on_tick, commands=self.queue, scenario=self.slot,
        )

    def _on_tick(self, stats: loop.LoopStats) -> None:
        self.stats = stats  # only remember; drawing is the timer's job

    # --- Redraw ----------------------------------------------------------------------
    def refresh_view(self) -> None:
        self.query_one("#header", Static).update(view.header_text(self.stats, self.plc))
        self.query_one("#world", Static).update(view.world_text(self.plant))
        self.query_one("#scenario-status", Static).update(view.scenario_text(self.slot))
        # A queued command is applied at the next tick: until then the plant still has the
        # old value, and syncing now would flip the switch back for a moment.
        if len(self.queue) == 0:
            self._sync_switches()

    def _sync_switches(self) -> None:
        """Show the plant's values (a scenario may have changed them) without sending commands."""
        for switch_id, (_, value, _) in SWITCHES.items():
            switch = self.query_one(f"#{switch_id}", Switch)
            wanted = value(self.plant)
            if switch.value != wanted:
                with switch.prevent(Switch.Changed):  # a sync is not an operator action
                    switch.value = wanted

    # --- Controls: every control only puts a command on the queue --------------------
    def on_switch_changed(self, message: Switch.Changed) -> None:
        command, _, _ = SWITCHES[message.switch.id]
        self._put(command, message.value)

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id == "bt-scenario":
            name = self.query_one("#in-scenario", Select).value
            if name in SCENARIOS:
                self.slot.start(SCENARIOS[name])
                self.notify(f"scenario {name} started")
            else:
                self.notify("choose a scenario first", severity="warning")
            return
        input_id, command, kind = VALUE_BUTTONS[message.button.id]
        text = self.query_one(f"#{input_id}", Input).value
        try:
            value = kind(text)
        except ValueError:
            self.notify(f"{command}: '{text}' is not a {kind.__name__}", severity="error")
            return
        self._put(command, value)

    def _put(self, command: str, value) -> None:
        try:
            self.queue.put(command, value)
        except ValueError as error:  # checked by commands.check(): nothing is queued
            self.notify(str(error), severity="error")

    # --- Event log -------------------------------------------------------------------
    def _show_event(self, event: Event) -> None:
        """EventLog echo: called for every new event (same thread as the loop)."""
        if self._log_widget is not None and view.matches(event, self.log_filter):
            self._log_widget.write(Text(event.format(), style=view.event_style(event)))

    def action_next_filter(self) -> None:
        i = view.LOG_FILTERS.index(self.log_filter)
        self.log_filter = view.LOG_FILTERS[(i + 1) % len(view.LOG_FILTERS)]
        self._log_widget.clear()
        for event in self.events.recent(len(self.events)):
            if view.matches(event, self.log_filter):
                self._log_widget.write(Text(event.format(), style=view.event_style(event)))
        self._update_log_title()

    def _update_log_title(self) -> None:
        self.query_one("#log-title", Static).update(
            f"Event log – filter: {self.log_filter}  (l = next filter; the log file keeps everything)"
        )

    # --- Quit ------------------------------------------------------------------------
    async def action_quit_panel(self) -> None:
        """Stop the loop cleanly (last tick finished, link and log file closed), then exit."""
        self._stop.set()
        if self._worker is not None:
            await self._worker.wait()
        self.plc.close()
        self.events.close()
        self.exit(self.stats)
