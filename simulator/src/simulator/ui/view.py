"""Text for the control panel, built from the plant and the PLC status.

Pure functions, no Textual: they take the simulator's objects and return
rich Text, so they can be tested without a terminal (tests/unit/test_view.py)
and the screen code in app.py stays small.

    world_text(plant)            the physical truth: bottles, pockets, sensor lamps
    syrup_world_text(plant)      the syrup room's truth: tanks, temperature, pressure, outputs, faults
    labeler_world_text(plant)    the labeler's truth: gate, pocket, label roll, glue tank, C3, outputs, faults
    packer_world_text(plant)     the packer's truth: gate, table, pusher, wrap conveyor, film, bar, oven, outfeed
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
from simulator.machines.labeler import LabelerOutputs
from simulator.machines.packer import Pack, PackerOutputs
from simulator.machines.syrup import SyrupOutputs
from simulator.plant import Plant
from simulator.plcs import (
    LABELER_STATE_NAMES,
    LABELER_STEP_NAMES,
    MODE_NAMES,
    PACKER_STATE_NAMES,
    PACKER_STEP_NAMES,
    PACKER_WRAP_STEP_NAMES,
    STATE_NAMES,
    STEP_NAMES,
    SYRUP_STATE_NAMES,
    SYRUP_STEP_NAMES,
    FillerPlc,
    LabelerPlc,
    PackerPlc,
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
    if plant.labeler_standin is not None:
        labeler = "running" if plant.labeler_standin.running else "STOPPED"
        t.append(f"Labeler  {labeler}, taken {plant.labeler_standin.taken}   lost {len(f.lost)}\n\n")
    else:  # the real labeler: its own tab 3
        t.append(f"Labeler  real machine (tab 3), discharged {plant.labeler.discharged}   lost {len(f.lost)}\n\n")

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


LABELER_LAMP_COL = 46  # "C2       " + a conveyor (35 characters) + 2 spaces


def labeler_world_text(plant: Plant) -> Text:
    """The labeler's physical truth: gate, pocket, label roll, glue tank, C3, PLC outputs, injected faults.

    Lamps on the left of a pair are SENSORS as written to the labeler PLC (like the other tabs), the
    others are the labeler PLC's OUTPUTS from the last tick; red markers for every injected fault.
    """
    lab = plant.labeler
    if lab is None:
        return Text("no real labeler (started with --no-labeler / --filler-only)", style="dim")
    t = Text()
    w = plant.labeler_writes()
    s = w.sensors
    out = plant.labeler_outputs if plant.labeler_outputs is not None else LabelerOutputs()  # no answer: OFF

    def left(text: str) -> None:  # left part of a line; the lamps start in the same column on every line
        t.append(text.ljust(LABELER_LAMP_COL))

    def lamps(*pairs: tuple[str, bool]) -> None:
        for i, (name, on) in enumerate(pairs):
            t.append("  " if i else "")
            t.append_text(lamp(name, on))

    left(f"C2       {conveyor_text(plant.c2)}")
    lamps(("Bottle_At_Gate", s.bottle_at_gate), ("ConveyorMotor2", out.conveyor_motor2))
    t.append("\n")
    left(f"Gate     entering {lab.entering.id if lab.entering else 'none'}")
    lamps(("GateMotor", out.gate_motor))
    _fault(t, lab.gate_jammed, "GATE JAMMED")
    t.append("\n\n")

    pocket = lab.pocket
    share = f"label {pocket.label_mm / config.LABEL_LENGTH_MM:.0%}" if pocket else ""
    left(f"Pocket   {pocket.id if pocket else 'empty'}  {share}")
    lamps(("Bottle_In_Pocket", s.bottle_in_pocket), ("Label_Present", s.label_present))
    t.append("\n")
    left(f"         bell {'down' if s.bell_down else 'up'}  turns {lab.turns:.0f}")
    lamps(("Bell_Down", s.bell_down), ("Spin_Pulse", s.spin_pulse))
    _fault(t, lab.bell_stuck, "BELL STUCK")
    t.append("\n")
    left("")
    lamps(("ClampValve", out.clamp_valve), ("SpinMotor", out.spin_motor))
    t.append("\n\n")

    web = lab.web
    left(f"Roll     {level_bar(web.labels_left, config.ROLL_LABELS)} {web.labels_left:>5} / {config.ROLL_LABELS} labels")
    lamps(("Roll_Low", s.roll_low), ("Roll_Empty", s.roll_empty))
    _fault(t, web.torn, "WEB TORN")
    t.append("\n")
    left(f"         lost {web.lost_mm:.0f} mm of web")
    lamps(("Label_Mark", s.label_mark), ("LabelFeed", out.label_feed), ("Cutter", out.cutter))
    t.append("\n")
    tank = lab.tank
    left(f"Glue     {s.glue_temp / 10:>5.1f} °C {level_bar(tank.glue_kg, config.GLUE_TANK_KG)} "
         f"{tank.glue_kg:.1f} / {config.GLUE_TANK_KG:g} kg")
    lamps(("Glue_Low", s.glue_low), ("GlueHeater", out.glue_heater))
    t.append("\n")
    left("")
    lamps(("GlueRoller", out.glue_roller))
    _fault(t, tank.heater_failed, "HEATER FAILED")
    _fault(t, tank.heater_stuck_on, "STUCK ON" if tank.heater_failed else "HEATER STUCK ON")  # both: fits 86
    t.append("\n\n")

    left(f"C3       {conveyor_text(plant.c3)}")
    lamps(("C3_Full", s.c3_full), ("DischargeMotor", out.discharge_motor))
    t.append("\n")
    t.append("Packer   ")
    if plant.packer is not None:  # the real packer: its PLC drives C3
        t.append(f"packer PLC, {plant.packer.groups_pushed} groups pushed\n\n")
    else:
        if plant.packer_standin.running:
            t.append("running")
        else:  # packer_running = False: C3 fills up
            t.append("STOPPED", style="bold red")
        t.append(f", taken {plant.packer_standin.taken}\n\n")

    t.append("Wires    ")
    lamps(("E_Stop_OK", w.e_stop_ok), ("Heartbeat", w.heartbeat))
    _fault(t, not plant.labeler_heartbeat.enabled, "FROZEN")
    t.append(f"\nCount    discharged {lab.discharged}  labelled {lab.labelled}  lost {len(lab.lost)}  "
             f"removed by hand {len(lab.removed)}")
    if plant.labeler_outputs is None:
        t.append("\nlabeler PLC not answering: outputs taken as OFF, C2 stands", style="bold red")
    return t


PACKER_LAMP_COL = 40  # the wrap / oven tracks are 34 characters wide plus the 9 of the name column
TRACK_CELL_MM = 50.0  # one character of a track


def track(packs: list[Pack], length_mm: float, marks: dict[float, str]) -> str:
    """A conveyor in mm as a row of 50 mm cells: '█' in every cell a pack touches, mark characters (an eye,
    the slot) where nothing covers them, '·' elsewhere. Positions are the packs' fronts (a pack is 200 mm
    long): a pack between two cells shows 5 cells, so an eye under it is never drawn free."""
    cells = ["·"] * round(length_mm / TRACK_CELL_MM)
    for at, char in marks.items():
        cells[min(len(cells) - 1, int(at // TRACK_CELL_MM))] = char
    for pack in packs:
        tail = pack.front_mm - config.PACK_LENGTH_MM
        for i in range(len(cells)):
            if tail < (i + 1) * TRACK_CELL_MM - 1e-9 and pack.front_mm > i * TRACK_CELL_MM + 1e-9:
                cells[i] = "█"
    return "[" + "".join(cells) + "]"


def packer_world_text(plant: Plant) -> Text:
    """The packer's physical truth: gate, collation table, pusher, wrap conveyor, film, wrap bar, oven,
    outfeed table and palletizer, the PLC outputs and every injected fault.

    Lamps on the left of a pair are SENSORS as written to the packer PLC (Bottle_Counted and Film_Pulse as
    the PulseWire passed them), the others are the packer PLC's OUTPUTS from the last tick.
    """
    pk = plant.packer
    if pk is None:
        return Text("no real packer (started with --no-packer / --no-labeler / --filler-only)", style="dim")
    t = Text()
    w = plant.packer_writes()
    s = w.sensors
    out = plant.packer_outputs if plant.packer_outputs is not None else PackerOutputs()  # no answer: OFF
    wrap, film, oven = pk.wrapper, pk.wrapper.film, pk.oven

    def left(text: str) -> None:  # left part of a line; the lamps start in the same column on every line
        t.append(text.ljust(PACKER_LAMP_COL))

    def lamps(*pairs: tuple[str, bool]) -> None:
        for i, (name, on) in enumerate(pairs):
            t.append("  " if i else "")
            t.append_text(lamp(name, on))

    slots = "".join("●" if b else "·" for b in plant.c3.slots)  # compact: the full row is on the labeler tab
    exit_bottle = plant.c3.exit_bottle
    left(f"C3       [{slots}]  at the gate {exit_bottle.id if exit_bottle else 'none'}")
    lamps(("Bottle_At_Gate", s.bottle_at_gate), ("ConveyorMotor3", out.conveyor_motor3))
    t.append("\n")
    left(f"Gate     entering {pk.entering.id if pk.entering else 'none'}")
    lamps(("Bottle_Counted", s.bottle_counted), ("GateMotor", out.gate_motor))
    t.append("\n")
    ids = " ".join(str(b.id) for b in pk.table)
    left(f"Table    {len(pk.table)}/{config.PACK_BOTTLES}  {ids}"[:PACKER_LAMP_COL - 1])
    lamps(("Pusher_Home", s.pusher_home), ("Pusher_Out", s.pusher_out))
    t.append("\n")
    left(f"Pusher   {level_bar(pk.pusher, 1.0)} {pk.pusher:4.0%}")
    lamps(("GroupPusher", out.group_pusher))
    _fault(t, pk.pusher_stuck, "PUSHER STUCK")
    t.append("\n\n")

    marks = {config.FILM_STOP_MM: "¦", config.FILM_SLOT_MM: "|"}
    left(f"Wrap     {track(wrap.packs, config.OVEN_ENTRY_MM, marks)}")
    lamps(("Pack_At_Film", s.pack_at_film), ("WrapConveyor", out.wrap_conveyor))
    t.append("\n")
    fronts = ", ".join(f"{p.id}: {p.front_mm:.0f}" for p in wrap.packs) or "no pack"
    left(f"         fronts mm {fronts}"[:PACKER_LAMP_COL - 1])
    lamps(("Wrap_Home", s.wrap_home), ("WrapBar", out.wrap_bar))
    _fault(t, wrap.bar_stuck, "BAR STUCK")
    t.append("\n")
    left(f"Film     {level_bar(film.left_mm, config.FILM_ROLL_MM)} {film.left_mm / 1000:6.1f} / "
         f"{config.FILM_ROLL_MM / 1000:g} m")
    lamps(("Film_Low", s.film_low), ("Film_Empty", s.film_empty))
    _fault(t, film.torn, "FILM TORN")
    t.append("\n")
    sheet = f"sheet {film.sheet_mm:.0f} mm {'cut' if film.cut else 'on the roll'}" if film.sheet_mm > 0 else "slot empty"
    left(f"         {sheet}")
    lamps(("Film_In_Slot", s.film_in_slot), ("Film_Pulse", s.film_pulse))
    t.append("\n")
    left(f"         marks {film.pulses}")
    lamps(("FilmFeed", out.film_feed), ("Knife", out.knife))
    t.append("\n\n")

    left(f"Oven     {track(oven.packs, config.OVEN_LENGTH_MM, {})}")
    lamps(("Oven_Entry", s.oven_entry), ("Oven_Exit", s.oven_exit), ("OvenConveyor", out.oven_conveyor))
    t.append("\n")
    left(f"         {s.oven_temp / 10:5.1f} °C  {len(oven.packs)} pack(s) inside")
    lamps(("OvenHeater", out.oven_heater), ("OvenBlower", out.oven_blower), ("Airflow_OK", s.airflow_ok))
    t.append("\n")
    faults = [text for on, text in ((oven.heater_failed, "HEATER FAILED"),
                                    (oven.heater_stuck_on and not oven.heater_failed, "HEATER STUCK ON"),
                                    (oven.heater_stuck_on and oven.heater_failed, "STUCK ON"),
                                    (oven.blower_failed, "BLOWER FAILED"), (oven.jammed, "JAMMED")) if on]
    if faults:
        left("")
        t.append("  ".join(faults), style="bold red")
        t.append("\n")
    table = pk.outfeed
    left(f"Outfeed  {len(table.packs)}/{config.TABLE_PACKS} packs")
    lamps(("Table_Full", s.table_full))
    t.append("\n")
    pallet = plant.palletizer
    t.append("Pallet   ")
    if pallet.running:
        t.append("running")
    else:  # palletizer_running = False: the outfeed table fills up
        t.append("STOPPED", style="bold red")
    t.append(f", taken {pallet.taken}\n")

    t.append("Wires    ")
    lamps(("E_Stop_OK", w.e_stop_ok), ("Heartbeat", w.heartbeat))
    _fault(t, not plant.packer_heartbeat.enabled, "FROZEN")
    t.append(f"\nCount    pushed {pk.groups_pushed}  wrapped {wrap.wrapped}  out {oven.left}  "
             f"palletized {pallet.taken}  taken out by hand {len(oven.removed)}")
    if plant.packer_outputs is None:
        t.append("\npacker PLC not answering: outputs taken as OFF, C3 stands", style="bold red")
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


def labeler_header_text(stats: LoopStats | None, plc: LabelerPlc) -> Text:
    """The labeler PLC's line (link, mode, step, state, GoodCount, alarms), like the syrup room's."""
    t = Text()
    link = "up" if plc.link_ok else "not connected"
    t.append(f"labeler PLC {plc.host}:{plc.port} {link}", style="green" if plc.link_ok else "bold red")
    status = stats.labeler_status if stats else None
    if status is None:
        return t
    mode = MODE_NAMES.get(status.mode, status.mode)
    t.append(f"  │  mode {mode}", style="" if status.mode == MODE_AUTO else "bold yellow")
    t.append(f"  │  step {LABELER_STEP_NAMES.get(status.step, status.step)}"
             f"  │  state {LABELER_STATE_NAMES.get(status.state, status.state)}"
             f"  │  GoodCount {status.good_count}  │  ")
    active = [name for name, on in status.alarms.items() if on]
    t.append(f"alarms: {', '.join(active)}" if active else "alarms: none", style="bold red" if active else "")
    if status.mode != MODE_AUTO:
        t.append(f"\nlabeler mode {mode}: Start is ignored. Auto + start:  uv run python tests/hmi.py --labeler auto reset start",
                 style="yellow")
    elif status.step == IDLE:
        t.append("\nlabeler PLC in IDLE: start it with  uv run python tests/hmi.py --labeler auto reset start",
                 style="yellow")
    return t


def packer_header_text(stats: LoopStats | None, plc: PackerPlc) -> Text:
    """The packer PLC's line (link, mode, both steps, state, PackCount, alarms), like the labeler's."""
    t = Text()
    link = "up" if plc.link_ok else "not connected"
    t.append(f"packer PLC {plc.host}:{plc.port} {link}", style="green" if plc.link_ok else "bold red")
    status = stats.packer_status if stats else None
    if status is None:
        return t
    mode = MODE_NAMES.get(status.mode, status.mode)
    t.append(f"  │  mode {mode}", style="" if status.mode == MODE_AUTO else "bold yellow")
    t.append(f"  │  step {PACKER_STEP_NAMES.get(status.step, status.step)}"
             f" / wrap {PACKER_WRAP_STEP_NAMES.get(status.wrap_step, status.wrap_step)}"
             f"  │  state {PACKER_STATE_NAMES.get(status.state, status.state)}"
             f"  │  PackCount {status.pack_count}  │  ")
    active = [name for name, on in status.alarms.items() if on]
    t.append(f"alarms: {', '.join(active)}" if active else "alarms: none", style="bold red" if active else "")
    if status.mode != MODE_AUTO:
        t.append(f"\npacker mode {mode}: Start is ignored. Auto + start:  uv run python tests/hmi.py --packer auto reset start",
                 style="yellow")
    elif status.step == IDLE and status.wrap_step == IDLE:
        t.append("\npacker PLC in IDLE: start it with  uv run python tests/hmi.py --packer auto reset start",
                 style="yellow")
    return t


def header_text(
    stats: LoopStats | None, plc: FillerPlc, syrup_plc: SyrupPlc | None = None, labeler_plc: LabelerPlc | None = None,
    packer_plc: PackerPlc | None = None,
) -> Text:
    """The filler PLC's line, and under it the syrup room's, the labeler's and the packer's when there are ones."""
    t = filler_header_text(stats, plc)
    if syrup_plc is not None:
        t.append("\n")
        t.append_text(syrup_header_text(stats, syrup_plc))
    if labeler_plc is not None:
        t.append("\n")
        t.append_text(labeler_header_text(stats, labeler_plc))
    if packer_plc is not None:
        t.append("\n")
        t.append_text(packer_header_text(stats, packer_plc))
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
    name = f"{runner.scenario.name} ({runner.scenario.target})"  # the machine: readable from every tab
    return f"{name}: done" if runner.done else f"{name}: step {runner.index + 1}/{n}"


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
