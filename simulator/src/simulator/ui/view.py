"""Text for the control panel, built from the plant and the PLC status.

Pure functions, no Textual: they take the simulator's objects and return
rich Text, so they can be tested without a terminal (tests/unit/test_view.py)
and the screen code in app.py stays small.

    world_text(plant)            the physical truth: bottles, pockets, sensor lamps
    header_text(stats, plc)      what the PLC reports: link, step, state, alarms
    scenario_text(slot)          which scenario runs and how far it is
    event_style(event)           colour of one log line
    LOG_FILTERS / matches()      the event log filters (all, bottle, PLC, warnings)
"""

from rich.text import Text

from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Event, Source
from simulator.loop import LoopStats
from simulator.machines.filler import CAP, ENTRY, FILL, RINSE
from simulator.plant import Plant
from simulator.plcs import MODE_NAMES, STATE_NAMES, STEP_NAMES, FillerPlc
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


def header_text(stats: LoopStats | None, plc: FillerPlc) -> Text:
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
