"""Text for the control panel, built from the plant and the PLC status.

Pure functions, no Textual: they take the simulator's objects and return
rich Text, so they can be tested without a terminal (tests/unit/test_view.py)
and the screen code in app.py stays small.

    world_text(plant)            the physical truth: bottles, pockets, sensor lamps
    syrup_world_text(plant)      the syrup room's truth: tanks, temperature, pressure, outputs, faults
    header_text(stats, plc)      what the PLC reports: link, step, state, alarms
    scenario_text(slot)          which scenario runs and how far it is
    event_style(event)           colour of one log line
    LOG_FILTERS / matches()      the event log filters (all, bottle, PLC, warnings)
"""

from rich.text import Text

from simulator import config
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Event, Source
from simulator.loop import LoopStats
from simulator.machines.filler import CAP, ENTRY, FILL, RINSE
from simulator.machines.syrup import SyrupOutputs
from simulator.plant import Plant
from simulator.plcs import (
    MODE_NAMES,
    STATE_NAMES,
    STEP_NAMES,
    SYRUP_STATE_NAMES,
    SYRUP_STEP_NAMES,
    FillerPlc,
    SyrupPlc,
)
from simulator.scenarios import ScenarioSlot

ON, OFF = "●", "○"
IDLE = 0
MODE_AUTO = 1


def lamp(name: str, on: bool) -> Text:
    """'S2 ●' in green when ON, 'S2 ○' dim when OFF."""
    return Text(f"{name} {ON if on else OFF}", style="bold green" if on else "dim")


def bottle_label(bottle: Bottle | None, show_ml: bool = False) -> str:
    """Bottle in a pocket: '7', '7 R', '6 230ml R', '5 500ml RC', or '.' for an empty pocket."""
    if bottle is None:
        return "."
    flags = ("R" if bottle.rinsed else "") + ("C" if bottle.capped else "")
    ml = f" {round(bottle.level_ml)}ml" if show_ml or bottle.level_ml > 0 else ""
    return f"{bottle.id}{ml}{' ' + flags if flags else ''}"


def conveyor_text(conveyor: Conveyor) -> str:
    """Slots from the entry (left) to the exit (right), as the bottles flow."""
    return "[ " + " ".join(f"{b.id if b else '.':>3}" for b in conveyor.slots) + " ]"


def world_text(plant: Plant) -> Text:
    s = plant.writes()  # exactly what is written to the PLC (S6/S8 kept ON for one write)
    f = plant.filler
    supply = plant.supply
    t = Text()

    rate = "paused" if supply.paused else f"{supply.per_min:g}/min"
    t.append(f"Supply   {rate}, {supply.pending} waiting\n")

    t.append(f"C1       {conveyor_text(plant.c1)} ─► ")
    t.append_text(lamp("S2", s.s2))
    t.append("\n\n")

    jam = Text("  JAMMED", style="bold red") if f.index_jammed else Text("")
    t.append("Belt     ")
    t.append_text(lamp("S8", s.s8))
    t.append(f"      belt moved {f.shifts} pitches")
    t.append_text(jam)
    t.append("\n")
    names = {ENTRY: "ENTRY", RINSE: "RINSE", FILL: "FILL", CAP: "CAP"}
    t.append("         " + "".join(f"{names[p]:<12}" for p in names) + "\n")
    t.append("         " + "".join(f"{bottle_label(f.pockets[p], show_ml=p == FILL):<12}" for p in names) + "\n")
    t.append("         " + " " * 12)
    for name, on in (("S3", s.s3), ("S4", s.s4), ("S5", s.s5)):
        t.append_text(lamp(name, on))
        t.append(" " * 8)
    t.append_text(lamp("S6", s.s6))
    t.append(f"\n         fill level {s.fill_level} ml\n\n")

    t.append(f"C2       {conveyor_text(plant.c2)}   ")
    t.append_text(lamp("S7", s.s7))
    t.append("\n")
    labeler = "running" if plant.labeler.running else "STOPPED"
    t.append(f"Labeler  {labeler}, taken {plant.labeler.taken}   lost {len(f.lost)}\n\n")

    t.append("Wires    ")
    t.append_text(lamp("Product", s.product_available))
    t.append("   ")
    t.append_text(lamp("E_Stop_OK", s.e_stop_ok))
    t.append("   ")
    t.append_text(lamp("Heartbeat", s.heartbeat))
    if not plant.heartbeat.enabled:
        t.append("  FROZEN", style="bold red")
    return t


def level_bar(litres: float, capacity: float, width: int = 10) -> str:
    """'▕██████░░░░▏': the tank level as a bar of `width` cells (rounded, clamped to empty..full)."""
    full = max(0, min(width, round(litres / capacity * width))) if capacity > 0 else 0
    return "▕" + "█" * full + "░" * (width - full) + "▏"


def _fault(t: Text, active: bool, text: str) -> None:
    """A red marker after a lamp when a fault is injected (nothing when it is not)."""
    if active:
        t.append(f"  {text}", style="bold red")


LAMP_COL = 43  # column where the lamps start in the syrup world text


def syrup_world_text(plant: Plant) -> Text:
    """The syrup room's physical truth: tanks, temperature, pressure, PLC outputs, injected faults.

    Numbers are the SENSOR values as written to the PLC (like world_text), so a stuck sensor shows
    what the PLC sees; the true level is added next to it. Lamps = the syrup PLC's outputs last tick.
    """
    room = plant.syrup
    t = Text()
    if room is None:
        return Text("no syrup room (started with --filler-only)", style="dim")
    w = plant.syrup_writes()
    s = w.sensors
    signals = plant.syrup_signals
    out = signals.outputs if signals is not None else SyrupOutputs()  # no answer: outputs taken as OFF

    def left(text: str) -> None:  # left part of a line; the lamps start in the same column on every line
        t.append(text.ljust(LAMP_COL))

    def tank(name: str, level: int, capacity: float) -> None:
        left(f"{name:<12}{level:>5} L {level_bar(level, capacity)} {capacity:>5.0f} L")

    tank("Syrup tank", s.syrup_level, config.SYRUP_TANK_L)
    t.append_text(lamp("SyrupPump", out.syrup_pump))
    _fault(t, room.syrup_pump_failed, "FAILED")
    t.append("\n")
    left("Mains water")
    t.append_text(lamp("WaterPump", out.water_pump))
    _fault(t, room.water_pump_failed, "FAILED")
    t.append("\n")

    tank("Mixing tank", s.mix_level, config.MIX_TANK_L)
    t.append_text(lamp("MixHighHigh", s.mix_high_high))
    _fault(t, room.mix_level_frozen is not None, f"SENSOR STUCK (true {room.mix_l:.0f} L)")
    t.append("\n")
    left(f"   product {s.product_temp / 10:>5.1f} °C   chiller {room.coolant_c:.0f} °C")
    t.append_text(lamp("Agitator", out.agitator))
    t.append("  ")
    t.append_text(lamp("CoolingValve", out.cooling_valve))
    _fault(t, room.coolant_c != config.COOLANT_C, "CHILLER FAILED")
    t.append("\n")
    left(f"   CO2     {s.co2_pressure / 100:>5.2f} bar  dissolved {room.dissolved_bar:.2f} bar")
    t.append_text(lamp("CO2Valve", out.co2_valve))
    _fault(t, room.co2_valve_stuck, "STUCK OPEN")
    t.append("\n")

    tank("Buffer tank", s.buffer_level, config.BUFFER_TANK_L)
    t.append_text(lamp("TransferPump", out.transfer_pump))
    t.append("\n\n")

    t.append("Wires    Product_Available: ")
    t.append_text(lamp("syrup PLC", signals is not None and signals.product_available))
    t.append("  ")
    t.append_text(lamp("to filler", plant.filler_product_available))
    t.append("\n         ")
    t.append_text(lamp("E_Stop_OK", w.e_stop_ok))
    t.append("   ")
    t.append_text(lamp("Heartbeat", w.heartbeat))
    _fault(t, not plant.syrup_heartbeat.enabled, "FROZEN")
    t.append(f"\nLedger   water {room.water_in_l:.0f} L  delivered {room.delivered_l:.0f} L  "
             f"drawn {room.drawn_l:.0f} L  spilled {room.spilled_l:.0f} L  hand {room.manual_l:+.0f} L  "
             f"draw scale x{plant.buffer_draw_scale:g}")
    if signals is None:
        t.append("\nsyrup PLC not answering: outputs taken as OFF, no Product_Available", style="bold red")
    return t


def syrup_header_text(stats: LoopStats | None, plc: SyrupPlc) -> Text:
    """The syrup room PLC's line under the filler's (link, mode, step, state, batches, alarms)."""
    t = Text()
    link = "up" if plc.link_ok else "not connected"
    t.append(f"syrup PLC {plc.host}:{plc.port} {link}", style="green" if plc.link_ok else "bold red")
    status = stats.syrup_status if stats else None
    if status is None:
        return t
    mode = MODE_NAMES.get(status.mode, status.mode)
    t.append(f"  │  mode {mode}", style="" if status.mode == MODE_AUTO else "bold yellow")
    t.append(f"  │  step {SYRUP_STEP_NAMES.get(status.step, status.step)}"
             f"  │  state {SYRUP_STATE_NAMES.get(status.state, status.state)}"
             f"  │  BatchCount {status.batch_count}  │  ")
    active = [name for name, on in status.alarms.items() if on]
    t.append(f"alarms: {', '.join(active)}" if active else "alarms: none", style="bold red" if active else "")
    if status.mode != MODE_AUTO:
        t.append(f"\nsyrup mode {mode}: Start is ignored. Auto + start:  uv run python tests/hmi.py --syrup auto reset start",
                 style="yellow")
    elif status.step == IDLE:
        t.append("\nsyrup PLC in IDLE: start it with  uv run python tests/hmi.py --syrup auto reset start",
                 style="yellow")
    return t


def header_text(stats: LoopStats | None, plc: FillerPlc, syrup_plc: SyrupPlc | None = None) -> Text:
    """The filler PLC's line, and under it the syrup room PLC's when there is one."""
    t = filler_header_text(stats, plc)
    if syrup_plc is not None:
        t.append("\n")
        t.append_text(syrup_header_text(stats, syrup_plc))
    return t


def filler_header_text(stats: LoopStats | None, plc: FillerPlc) -> Text:
    t = Text()
    link = "up" if plc.link_ok else "not connected"
    t.append(f"PLC {plc.host}:{plc.port} {link}", style="green" if plc.link_ok else "bold red")
    status = stats.status if stats else None
    if status is None:
        return t
    mode = MODE_NAMES.get(status.mode, status.mode)
    t.append(f"  │  mode {mode}", style="" if status.mode == MODE_AUTO else "bold yellow")
    t.append(f"  │  step {STEP_NAMES.get(status.step, status.step)}"
             f"  │  state {STATE_NAMES.get(status.state, status.state)}"
             f"  │  GoodCount {status.good_count}  │  ")
    active = [name for name, on in status.alarms.items() if on]
    t.append(f"alarms: {', '.join(active)}" if active else "alarms: none", style="bold red" if active else "")
    if stats.ticks > 1:
        t.append(f"  │  dt {stats.dt_min * 1000:.0f}-{stats.dt_max * 1000:.0f} ms")
    if status.mode != MODE_AUTO:
        t.append(f"\nmode {mode}: Start is ignored. Auto + start:  uv run python tests/hmi.py auto reset start",
                 style="yellow")
    elif status.step == IDLE:
        t.append("\nPLC in IDLE: start it with  uv run python tests/hmi.py auto reset start", style="yellow")
    return t


def scenario_text(slot: ScenarioSlot) -> str:
    runner = slot.runner
    if runner is None:
        return "no scenario"
    n = len(runner.scenario.steps)
    return f"{runner.scenario.name}: done" if runner.done else f"{runner.scenario.name}: step {runner.index + 1}/{n}"


def event_style(event: Event) -> str:
    if event.is_warning:
        return "bold red"
    return {Source.PLC: "cyan", Source.SIM: "yellow"}.get(event.source, "")


# Event log filters, in the order the `l` key cycles through them
LOG_FILTERS = ("all", "bottle", "plc", "warnings")


def matches(event: Event, name: str) -> bool:
    if name == "bottle":
        return event.source == Source.BOTTLE
    if name == "plc":
        return event.source == Source.PLC
    if name == "warnings":
        return event.is_warning
    return True
