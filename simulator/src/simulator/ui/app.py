"""The control panel: the engineer's test bench (report section 7.5).

    ┌ header: filler PLC line, syrup PLC line (link, step, state, alarms, counter, dt) ─┐
    │ [1 Filler] [2 Syrup room] [3 Labeler]   one tab per machine, same layout in each: │
    │   world: the physical truth                 │ controls: values, faults, switches  │
    ├ scenario: [select] Run  status   (select: the active tab's scenarios) ─────────────┤
    ├ event log (filter with l) ───────────────────────────────────────────────────────┤
    └ footer: q quit, l log filter, 1 / 2 / 3 tabs ──────────────────────────────────────┘

How it runs:
- The tick loop (loop.run) is a Textual worker. Textual and the loop share one
  asyncio event loop, so a control can call queue.put() directly; the loop
  applies the command at the start of its next tick.
- The screen is redrawn by a timer 4 times per second, not on every tick:
  drawing 10 times per second would steal time from the loop (late ticks).
- The panel only CHANGES THE WORLD (supply, faults, stand-in signals, tank
  actions). It never writes Cmd, mode or setpoints: Start / Stop / Reset belong
  to the HMI (tests/hmi.py until FUXA exists).
- The syrup room tab exists only when the plant has a syrup room (not --filler-only), the labeler
  tab only with the real labeler (not --no-labeler / --filler-only).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from simulator import config, loop
from simulator.commands import CommandQueue
from simulator.events import Event, EventLog
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc, SyrupPlc
from simulator.scenarios import SCENARIOS, ScenarioSlot, uses_command
from simulator.ui import view

# switch id -> (command, the plant value the switch shows, label)
Switches = dict[str, tuple[str, Callable[[Plant], bool], str]]
SWITCHES: Switches = {
    "sw-paused": ("supply_paused", lambda p: p.supply.paused, "Pause supply"),
    "sw-product": ("product_available", lambda p: p.product_available, "Product available"),
    "sw-estop": ("e_stop", lambda p: not p.e_stop_ok, "E-stop pressed"),
    "sw-jam": ("index_jam", lambda p: p.filler.index_jammed, "Index belt jam"),
    "sw-labeler": ("labeler_running", lambda p: p.labeler_standin is not None and p.labeler_standin.running, "Labeler running"),
    "sw-heartbeat": ("heartbeat", lambda p: p.heartbeat.enabled, "Heartbeat"),
}
# Syrup room tab: only used when plant.syrup exists (the lambdas read plant.syrup)
SYRUP_SWITCHES: Switches = {
    "sw-chiller": ("chiller_failed", lambda p: p.syrup.coolant_c != config.COOLANT_C, "Chiller failed"),
    "sw-water-pump": ("water_pump_failed", lambda p: p.syrup.water_pump_failed, "Water pump failed"),
    "sw-syrup-pump": ("syrup_pump_failed", lambda p: p.syrup.syrup_pump_failed, "Syrup pump failed"),
    "sw-co2": ("co2_valve_stuck", lambda p: p.syrup.co2_valve_stuck, "CO2 valve stuck"),
    "sw-level": ("mix_level_stuck", lambda p: p.syrup.mix_level_frozen is not None, "Level sensor stuck"),
    "sw-syrup-hb": ("syrup_heartbeat", lambda p: p.syrup_heartbeat.enabled, "Heartbeat"),
}
# Labeler tab: only used when plant.labeler exists (the lambdas read plant.labeler / plant.packer)
LABELER_SWITCHES: Switches = {
    "sw-web-torn": ("web_torn", lambda p: p.labeler.web.torn, "Label web torn"),
    "sw-bell": ("bell_stuck", lambda p: p.labeler.bell_stuck, "Bell stuck"),
    "sw-heater-failed": ("heater_failed", lambda p: p.labeler.tank.heater_failed, "Heater failed"),
    "sw-heater-on": ("heater_stuck_on", lambda p: p.labeler.tank.heater_stuck_on, "Heater stuck on"),
    "sw-gate-jam": ("gate_jam", lambda p: p.labeler.gate_jammed, "Gate jam"),
    "sw-packer": ("packer_running", lambda p: p.packer.running, "Packer running"),
    "sw-labeler-hb": ("labeler_heartbeat", lambda p: p.labeler_heartbeat.enabled, "Heartbeat"),
}

# button id -> (input id, command, type of the value)
VALUE_BUTTONS: dict[str, tuple[str, str, type]] = {
    "bt-rate": ("in-rate", "supply_rate", float),
    "bt-add": ("in-add", "add_bottles", int),
    "bt-flow": ("in-flow", "fill_flow", float),
    # syrup room
    "bt-deliver": ("in-deliver", "deliver_syrup", float),
    "bt-drain-syrup": ("in-drain-syrup", "drain_syrup", float),
    "bt-draw": ("in-draw", "draw_buffer", float),
    "bt-fill": ("in-fill", "fill_buffer", float),
    "bt-scale": ("in-scale", "buffer_draw_scale", float),
    # labeler
    "bt-roll": ("in-roll", "load_roll", int),
    "bt-glue-level": ("in-glue-level", "glue_level", float),
    "bt-add-glue": ("in-add-glue", "add_glue", float),
}

# button id -> command sent with True: one-off hand actions, not states (nothing to show on a switch)
ACTION_BUTTONS: dict[str, str] = {
    "bt-vent": "vent_tank",
    "bt-drain-mix": "drain_mix_tank",
    "bt-clear-gate": "clear_gate",
}

REFRESH_S = 0.25


# tab id -> the scenario target its select shows
TAB_TARGETS = {"tab-filler": "filler", "tab-syrup": "syrup", "tab-labeler": "labeler"}


def scenario_options(target: str, with_syrup: bool, with_labeler: bool = False) -> list[tuple[str, str]]:
    """(label, name) for the scenario select of one machine's tab: only scenarios with that target, and only
    what the plant can run: syrup scenarios need the syrup room, labeler scenarios the real labeler, and a
    scenario that stops the labeler stand-in (labeler_stopped) needs the stand-in."""
    def offered(s) -> bool:
        if s.target != target:
            return False
        if s.target == "syrup":
            return with_syrup
        if s.target == "labeler":
            return with_labeler
        return not (with_labeler and uses_command(s, "labeler_running"))

    return [(s.name, s.name) for s in SCENARIOS.values() if offered(s)]


class SimulatorApp(App):
    TITLE = "Soft drink line simulator"
    CSS = """
    #header { height: auto; padding: 0 1; background: $boost; }
    #tabs { height: auto; }
    .main { height: 22; }
    .world { width: 1fr; border: round $primary; padding: 0 1; }
    .controls { width: 50; border: round $primary; padding: 0 1; }
    TabPane { padding: 0; }
    .row { height: 1; }
    .switch-row { margin-bottom: 1; }  /* one-line switches stacked without a gap read as one block */
    .row Label { width: 20; }
    .row Input { width: 10; }
    .row Button { min-width: 8; margin-left: 1; }
    Switch { height: 1; border: none; padding: 0; width: 6; }
    #scenario-row { height: 1; padding: 0 1; }
    #scenario-row Label { width: 10; }
    #in-scenario { width: 30; }
    #scenario-status { width: 1fr; margin-left: 2; color: $text-muted; }
    #log-title { height: 1; padding: 0 1; background: $boost; }
    #log { height: 1fr; }
    """
    BINDINGS = [
        ("q", "quit_panel", "Quit"),
        ("ctrl+q", "quit_panel", "Quit"),
        ("l", "next_filter", "Log filter"),
        ("1", "tab('tab-filler')", "Filler"),
        ("2", "tab('tab-syrup')", "Syrup room"),
        ("3", "tab('tab-labeler')", "Labeler"),
    ]

    def __init__(
        self, plant: Plant, plc: FillerPlc, log_path: Path | None, scenario: str | None = None,
        syrup_plc: SyrupPlc | None = None, labeler_plc: LabelerPlc | None = None,
    ) -> None:
        super().__init__()
        self.plant = plant
        self.plc = plc
        self.syrup_plc = syrup_plc  # the second PLC (None = filler only)
        self.labeler_plc = labeler_plc  # the third PLC (None = labeler stand-in, no labeler tab)
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

    @property
    def has_syrup(self) -> bool:
        return self.plant.syrup is not None

    @property
    def has_labeler(self) -> bool:
        return self.plant.labeler is not None

    def _switches(self) -> Switches:
        """The switches that exist on this panel (syrup ones only with a syrup room, labeler ones only with the labeler)."""
        switches = dict(SWITCHES)
        if self.has_syrup:
            switches |= SYRUP_SWITCHES
        if self.has_labeler:
            switches |= LABELER_SWITCHES
        return switches

    # --- Layout ----------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with TabbedContent(id="tabs"):
            with TabPane("1 Filler", id="tab-filler"):
                with Horizontal(classes="main"):
                    yield Static(id="world", classes="world")
                    with VerticalScroll(classes="controls"):
                        yield self._value_row("Supply /min", "in-rate", f"{self.plant.supply.per_min:g}", "number",
                                              "bt-rate", "Set")
                        yield self._value_row("Add bottles", "in-add", "5", "integer", "bt-add", "Add")
                        yield self._value_row("Fill flow ml/s", "in-flow", f"{config.FILL_FLOW_ML_S:g}", "number",
                                              "bt-flow", "Set")
                        yield from self._switch_rows(SWITCHES)
            if self.has_syrup:
                with TabPane("2 Syrup room", id="tab-syrup"):
                    with Horizontal(classes="main"):
                        yield Static(id="syrup-world", classes="world")
                        with VerticalScroll(classes="controls"):
                            yield self._value_row("Deliver syrup L", "in-deliver", "500", "number", "bt-deliver", "Add")
                            yield self._value_row("Drain syrup L", "in-drain-syrup", "100", "number",
                                                  "bt-drain-syrup", "Drain")
                            yield self._value_row("Draw buffer L", "in-draw", "400", "number", "bt-draw", "Take")
                            yield self._value_row("Fill buffer L", "in-fill", "500", "number", "bt-fill", "Fill")
                            yield self._value_row("Draw scale L/L", "in-scale", f"{config.BUFFER_DRAW_SCALE:g}",
                                                  "number", "bt-scale", "Set")
                            yield Horizontal(
                                Button("Vent tank", id="bt-vent", compact=True),
                                Button("Drain mix tank", id="bt-drain-mix", compact=True),
                                classes="row switch-row",
                            )
                            yield from self._switch_rows(SYRUP_SWITCHES)
            if self.has_labeler:
                with TabPane("3 Labeler", id="tab-labeler"):
                    with Horizontal(classes="main"):
                        yield Static(id="labeler-world", classes="world")
                        with VerticalScroll(classes="controls"):
                            yield self._value_row("Load roll labels", "in-roll", f"{config.ROLL_LABELS}", "integer",
                                                  "bt-roll", "Load")
                            yield self._value_row("Glue level kg", "in-glue-level", f"{config.GLUE_START_KG:g}",
                                                  "number", "bt-glue-level", "Set")
                            yield self._value_row("Add glue kg", "in-add-glue", "1", "number", "bt-add-glue", "Add")
                            yield Horizontal(
                                Button("Clear gate", id="bt-clear-gate", compact=True),
                                classes="row switch-row",
                            )
                            yield from self._switch_rows(LABELER_SWITCHES)
        # One scenario row under the tabs; its select shows the active tab's scenarios (filler at start)
        yield Horizontal(
            Label("Scenario"),
            Select(scenario_options("filler", self.has_syrup, self.has_labeler), id="in-scenario", compact=True,
                   prompt="choose"),
            Button("Run", id="bt-scenario", compact=True),
            Static(id="scenario-status"),
            id="scenario-row",
        )
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

    def _switch_rows(self, switches: Switches):
        for switch_id, (_, value, label) in switches.items():
            yield Horizontal(Label(label), Switch(value=value(self.plant), id=switch_id), classes="row switch-row")

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
            syrup_plc=self.syrup_plc, labeler_plc=self.labeler_plc,
        )

    def _on_tick(self, stats: loop.LoopStats) -> None:
        self.stats = stats  # only remember; drawing is the timer's job

    # --- Redraw ----------------------------------------------------------------------
    def refresh_view(self) -> None:
        self.query_one("#header", Static).update(view.header_text(self.stats, self.plc, self.syrup_plc, self.labeler_plc))
        self.query_one("#world", Static).update(view.world_text(self.plant))
        if self.has_syrup:
            self.query_one("#syrup-world", Static).update(view.syrup_world_text(self.plant))
        if self.has_labeler:
            self.query_one("#labeler-world", Static).update(view.labeler_world_text(self.plant))
        self.query_one("#scenario-status", Static).update(view.scenario_text(self.slot))
        # A queued command is applied at the next tick: until then the plant still has the
        # old value, and syncing now would flip the switch back for a moment.
        if len(self.queue) == 0:
            self._sync_switches()

    def _sync_switches(self) -> None:
        """Show the plant's values (a scenario may have changed them) without sending commands."""
        for switch_id, (_, value, _) in self._switches().items():
            switch = self.query_one(f"#{switch_id}", Switch)  # also works on the hidden tab
            wanted = value(self.plant)
            if switch.value != wanted:
                with switch.prevent(Switch.Changed):  # a sync is not an operator action
                    switch.value = wanted

    # --- Controls: every control only puts a command on the queue --------------------
    def on_switch_changed(self, message: Switch.Changed) -> None:
        command, _, _ = self._switches()[message.switch.id]
        self._put(command, message.value)

    def on_button_pressed(self, message: Button.Pressed) -> None:
        button = message.button.id
        if button == "bt-scenario":
            self._start_scenario()
        elif button in ACTION_BUTTONS:
            self._put(ACTION_BUTTONS[button], True)
        else:
            self._put_value(*VALUE_BUTTONS[button])

    def _start_scenario(self) -> None:
        name = self.query_one("#in-scenario", Select).value
        if name not in SCENARIOS:
            self.notify("choose a scenario first", severity="warning")
            return
        old = self.slot.runner
        self.slot.start(SCENARIOS[name])  # one slot: a running scenario is replaced and stops where it was
        if old is not None and not old.done:
            self.notify(f"scenario {name} started, {old.scenario.name} ({old.scenario.target}) stopped")
        else:
            self.notify(f"scenario {name} started")

    def _put_value(self, input_id: str, command: str, kind: type) -> None:
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

    # --- Tabs ------------------------------------------------------------------------
    def action_tab(self, tab_id: str) -> None:
        """Keys 1 / 2 / 3: show a machine's tab (syrup tab only with a syrup room, labeler tab only with the labeler)."""
        if tab_id == "tab-syrup" and not self.has_syrup:
            self.notify("no syrup room tab (started with --filler-only)", severity="warning")
            return
        if tab_id == "tab-labeler" and not self.has_labeler:
            self.notify("no labeler tab (started with --no-labeler / --filler-only)", severity="warning")
            return
        self.query_one("#tabs", TabbedContent).active = tab_id

    def on_tabbed_content_tab_activated(self, message: TabbedContent.TabActivated) -> None:
        """Keys 1 / 2 / 3 and mouse clicks: the scenario select shows only the new tab's machine."""
        target = TAB_TARGETS.get(message.pane.id)
        if target is not None:
            self.query_one("#in-scenario", Select).set_options(
                scenario_options(target, self.has_syrup, self.has_labeler))

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
        if self.syrup_plc is not None:
            self.syrup_plc.close()
        if self.labeler_plc is not None:
            self.labeler_plc.close()
        self.events.close()
        self.exit(self.stats)
