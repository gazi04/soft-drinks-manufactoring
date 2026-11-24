"""The FUXA project (docs/hmi/fuxa-project.json) matches the PLCs: tags, screens, alarms, trends.

    uv run python tests/test_hmi_tags.py --offline   # H1 only: the export file, no PLC, no FUXA
    uv run python tests/test_hmi_tags.py             # H1-H12: FUXA and all four PLCs must be running

Checks:
    H1 export (per device, filler + syrup room + labeler + packer): every signal of its address map has exactly one tag,
       with FUXA address = Modbus number + 1, the right area and type (catches an edit in
       the editor that moved a tag); the degC / bar tags of the syrup room have FUXA's
       expression scale (read this / f, write Math.round(this * f)), no other tag has a scale
    H2 read path: for every tag, the value FUXA reads (GET /api/getTagValue) equals the
       value read directly from the PLC over Modbus (a scaled tag: FUXA value x factor)
    H3 write path: FillSetpoint written THROUGH FUXA (POST /api/setTagValue) arrives in
       the PLC; the old value is written back afterwards
    H4 FUXA reports the PLC device as connected (connection status tag = 5)
    H2 + H4 also run for the syrup room PLC (port 5022), and H3 there writes PressHyst 0.29 bar
       through FUXA: the PLC must get 29 (rounded, not cut off to 28), then the old value
    H2 + H4 also run for the labeler PLC (port 5023, HMI L1), and H3 there writes GlueHyst 3.5 degC
       through FUXA: the PLC must get 35, then the old value
    H2 + H4 also run for the packer PLC (port 5024, HMI P1), and H3 there writes OvenHyst 4.5 degC
       through FUXA: the PLC must get 45, then the old value
    H5 header (export, stages 2, S2, L2 and P2), on every generated view:
       row 1 = all four machines: a chip per machine on its State that opens its screen, with one
       show-text per state, step and mode value; an E-stop segment per PLC (I17: red at 0, "E-stop OK" /
       "E-STOP" texts, then a "no link" cover on that PLC's connection status, so a lost PLC never shows
       red); a link cover per PLC over its WHOLE chip with the machine's name drawn on top (P2); Line/Alarms/Trends open the
       view of the same machine. Row 2 = the machine of the view (fv.CONTEXT): Start/Stop/Reset
       write its Cmd 1/2/3, Auto/Manual/Stop its ModeReq 1/2/0, a chip per alarm bit; on shared
       views the picker opens the same view for each other machine. Every button has at most one
       event per kind (several writes per click dropped FUXA's PLC link). Layout: header boxes
       inside 1280 x 96, none overlapping in a row, except a link cover, which must lie inside its own chip
    H6 Filler screen (export, stage 3): a hold-to-run button per Man_* (1 on press, 0 on
       release and on mouseout, visible only in Manual); setpoint inputs with the right
       tag and min < max; a lamp per sensor/output; pocket + state words per Anim_* tag;
       fill level and setpoint bars
    H7 Line overview (export, stage 4; every Line view): 8 slot shapes per conveyor, each bound to its own bit
       (bitmask 1, 2, 4 ... 128) of Anim_C1 / Anim_C2 (filler PLC) / Anim_C3 (labeler PLC); C1 and index rollers
       turn with ConveyorMotor1 / IndexMotor, C2 rollers with the LABELER's ConveyorMotor2, C3 rollers with the
       PACKER's ConveyorMotor3 (P4); the filler block opens the Filler view; L4: the labeler block on the labeler State opens
       the Labeler view, its step / glue texts let clicks through; the row C1 < filler < C2 < labeler < C3 runs left
       to right without overlap; P4: the packer block under the end of C3 on the packer State opens the Packer view,
       its steps / oven / packs-in-oven texts let clicks through, a chute from C3's end down to it, the
       palletizer stand-in left of it, the pack counter on PackCount;
       S4: the syrup room block on the syrup State (one colour per state) opens the Syrup room
       view, its state / step texts and buffer level (syrup BufferLevel) let clicks through; the
       product pipe and "no product" on the syrup PLC's Product_Available (the source), the
       "filler input" lamp on the filler's; syrup block above the pipe above the filler block
    H8 Alarms (export, stages 5, S5 and L5): one FUXA alarm per PLC alarm bit of EVERY machine, plus the
       labeler's warnings on the sensor bits Roll_Low / Glue_Low (priority low)
       (names <machine>_<bit>, unique) with exactly one priority enabled (the one of
       fv.ALARM_TEXTS), group = the machine's title, active at value 1, checked (checkdelay > 0),
       ackmode ackactive; each Alarms view has one unfiltered active-alarms table (the whole
       line) with Ack and Machine (group) columns and a Reset button (Cmd = 3 of the machine of
       that Alarms view); every alarm chip opens its machine's Alarms view; the side menu opens FUXA's alarm panel (history)
    H9 Trends (export, stages 6, S6 and L6), per machine: FUXA's DAQ (history) is on for exactly its
       trend tags (every change); every line of its charts is bound to an existing, logged tag of
       its device, stepAfter; its Trends view has one realtime chart widget per chart of that
       machine (loading the history when it opens); the side menu opens Trends
    H11 Syrup room screen (export, S3): a hold-to-run button per syrup Man_* (visible only in
       the syrup room's Manual mode), the 10 recipe inputs with the screen's limits (decimal step
       for degC / bar), a lamp per output and signal, 3 tank bars on the level tags with the tank
       sizes, the values, a step text per step, every pipe on its output; the note under the buffer
       tank names the simulator's buffer draw factor with the numbers of simulator.config; the
       agitator impeller in the mixing tank turns with the Agitator output
    H12 Labeler screen (export, HMI L3): a hold-to-run button per labeler Man_* (only in its Manual mode),
       the 4 setpoint inputs with their limits (decimal step for degC) AND the limits' rules: lowest ready
       glue >= the simulator's stick limit, highest window edge < GlueMax, heater band < window, max turns
       < label timeout; a lamp per sensor / output / conditioned bit, the glue temperature and setpoint bars
       (0..220 degC) with the GlueMax tick, pocket body / label / word on every Anim_Station code, C3 slots
       on the bits of Anim_C3, turntable turning with SpinMotor, web on LabelFeed, the step texts, and
       Reset counter = ONE write of labeler Cmd 4
    H13 Packer screen (export, P3): a hold-to-run button per packer Man_* (visible only in its Manual mode),
       the 4 setpoint inputs with limits that pass five named rules (shrink: ready oven >= SHRINK_C; OvenMax;
       heater band < ready window; warm-up < HeatTimeout from the simulator's oven model; film = the PLC's
       LIMIT and fed within FeedTimeout); one lamp per output / conditioned / sensor bit; wrap and oven cells
       on their Anim bits, collation places on GroupCount, outfeed places on Anim_Table, the C3 end on the
       labeler's Anim_C3; the film sheet per Anim_Film code; turning parts on their motors; Reset counter =
       one write of packer Cmd 4
    H10 DAQ (live, stage 6): FUXA's history (GET /api/daq) has values of every trend tag from
       the last 10 min (also when idle: one value a minute at least), and no stored fill level
       is above the fill setpoint + 20 ml. Syrup room: every syrup trend tag has points too, and
       the stored setpoints are in degC / bar (CoolSetpoint <= 10, PressSetpoint <= 5), not raw INTs
       Labeler (L6): every labeler trend tag has points, stored in degC (GlueSetpoint <= 200,
       GlueTemp <= 250), not raw INTs

Run H2 with the simulator idle (no bottles moving), so the values do not change between
the two reads; each tag still gets 3 tries. H3 writes only FillSetpoint (FUXA owns it);
Cmd and Man_* are never written here, they would start or move the machine.
"""

import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from simulator import address_map as am
from simulator import address_map_labeler as lm
from simulator import address_map_packer as pm
from simulator import address_map_syrup as sm
from simulator import config

import fuxa_tags as ft
from fuxa_shapes import esc as fs_esc
import fuxa_views as fv
from simulator.plcs import STATE_NAMES, SYRUP_STEP_NAMES

CONNECTED = 5  # FUXA 1.3.5 runtime/devices/device.js ConnectionStatusEnum.ON

# Not signals: helper numbers, bulk-transfer anchors and the unused filler bit.
NOT_SIGNALS = {"MX_COIL_OFFSET", "MW_REGISTER_OFFSET", "UNUSED_MX0_7",
               "OUTPUTS_START", "OUTPUTS_COUNT", "SENSOR_BLOCK_START", "STATUS_START", "STATUS_COUNT",
               "ALARMS_START", "ALARMS_COUNT", "ANIM_START", "ANIM_COUNT", "ANIM_POCKETS"}
COIL_SIGNALS = {"CONVEYOR1", "DOOR_MOTOR", "RINSE_VALVE", "FILL_VALVE", "CAP_MOTOR", "INDEX_MOTOR",
                "S2", "S3", "S4", "S5", "S6", "S7", "S8", "PRODUCT_AVAILABLE", "E_STOP_OK", "HEARTBEAT",
                "ALM_INDEX_JAM", "ALM_FILL_TIMEOUT", "ALM_OUTFEED_BLOCKED", "ALM_COMM_LOST",
                "OUTFEED_FULL", "ENTRY_LOAD", "JOG_BLOCK",
                "MAN_C1", "MAN_DOOR", "MAN_RINSE", "MAN_FILL", "MAN_CAP", "MAN_INDEX"}


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def expected_addresses() -> set[tuple[str, int]]:
    """Every signal address in address_map, as (FUXA area, Modbus number)."""
    names = [n for n in dir(am) if n.isupper() and isinstance(getattr(am, n), int)
             and not n.startswith("POCKET_") and n not in NOT_SIGNALS]
    unknown = [n for n in names if n not in COIL_SIGNALS and not (1024 <= getattr(am, n) < 2048)]
    if unknown:
        raise SystemExit(f"address_map has new constants this test does not know: {unknown}")
    wanted = {(ft.COIL if n in COIL_SIGNALS else ft.HOLDING, getattr(am, n)) for n in names}
    wanted |= {(ft.HOLDING, a) for a in range(am.ANIM_START, am.ANIM_START + am.ANIM_COUNT)}
    return wanted


SYRUP_NOT_SIGNALS = {"OUTPUTS_START", "OUTPUTS_COUNT", "SENSOR_BLOCK_START", "ANALOG_START", "ANALOG_COUNT",
                     "STATUS_START", "STATUS_COUNT", "ALARMS_START", "ALARMS_COUNT", "SETPOINTS_START",
                     "SETPOINTS_COUNT", "UNUSED_MX1_0"}


def expected_syrup_addresses() -> set[tuple[str, int]]:
    """Every signal address in address_map_syrup. The area follows from the number: holding registers
    are 1024-2047 (%MW), everything else is a coil (%QX from 0, %MX from 8192)."""
    names = [n for n in dir(sm) if n.isupper() and isinstance(getattr(sm, n), int) and n not in SYRUP_NOT_SIGNALS]
    return {(ft.HOLDING if 1024 <= getattr(sm, n) < 2048 else ft.COIL, getattr(sm, n)) for n in names}


LABELER_NOT_SIGNALS = {"OUTPUTS_START", "OUTPUTS_COUNT", "SENSOR_BLOCK_START", "EXTRA_SENSOR_BLOCK_START",
                       "ANALOG_START", "ANALOG_COUNT", "STATUS_START", "STATUS_COUNT", "ALARMS_START",
                       "ALARMS_COUNT", "CONDITIONED_START", "CONDITIONED_COUNT", "SETPOINTS_START",
                       "SETPOINTS_COUNT", "ANIM_START", "ANIM_COUNT", "UNUSED_MX1_0"}


def expected_labeler_addresses() -> set[tuple[str, int]]:
    """Every signal address in address_map_labeler (pocket codes are values, not addresses)."""
    names = [n for n in dir(lm) if n.isupper() and isinstance(getattr(lm, n), int)
             and not n.startswith("POCKET_") and n not in LABELER_NOT_SIGNALS]
    return {(ft.HOLDING if 1024 <= getattr(lm, n) < 2048 else ft.COIL, getattr(lm, n)) for n in names}


PACKER_NOT_SIGNALS = {"OUTPUTS_START", "OUTPUTS_COUNT", "SENSOR_BLOCK_START", "EXTRA_SENSOR_BLOCK_START",
                      "ANALOG_START", "ANALOG_COUNT", "STATUS_START", "STATUS_COUNT", "COMPUTED_START",
                      "COMPUTED_COUNT", "ALARMS_START", "ALARMS_COUNT", "EXTRA_ALARMS_START", "EXTRA_ALARMS_COUNT",
                      "CONDITIONED_START", "CONDITIONED_COUNT", "SETPOINTS_START", "SETPOINTS_COUNT", "ANIM_START",
                      "ANIM_COUNT", "ANIM_CELL_MM", "WRAP_CELLS", "OVEN_CELLS", "UNUSED_MX1_0"}


def expected_packer_addresses() -> set[tuple[str, int]]:
    """Every signal address in address_map_packer (sheet codes are values, cell sizes are mm, not addresses)."""
    names = [n for n in dir(pm) if n.isupper() and isinstance(getattr(pm, n), int)
             and not n.startswith("SHEET_") and n not in PACKER_NOT_SIGNALS]
    return {(ft.HOLDING if 1024 <= getattr(pm, n) < 2048 else ft.COIL, getattr(pm, n)) for n in names}


def h1_export() -> list[bool]:
    return [h1_machine(ft.FILLER, expected_addresses(), fv.TAGS_ID),
            h1_machine(ft.SYRUP, expected_syrup_addresses(), fv.SYRUP_TAGS_ID),
            h1_machine(ft.LABELER, expected_labeler_addresses(), fv.LABELER_TAGS_ID),
            h1_machine(ft.PACKER, expected_packer_addresses(), fv.PACKER_TAGS_ID)]


def h1_machine(m: ft.Machine, expected: set[tuple[str, int]], page_id: str) -> bool:
    name_ = f"H1 export {m.key}"
    project = json.loads(ft.EXPORT.read_text())
    dev = project["devices"].get(m.device_id)
    if dev is None:
        return check(name_, False, f"no device {m.device_id} in {ft.EXPORT.name}")
    tags = list(dev["tags"].values())
    problems = []
    names = [t["name"] for t in tags]
    if len(set(names)) != len(names):
        problems.append("duplicate tag names")
    have = {}
    for t in tags:
        key = (t["memaddress"], t["address"] - 1)
        if key in have:
            problems.append(f"{t['name']} and {have[key]} on the same address")
        have[key] = t["name"]
    for name, area, number, kind, _ in m.tags:
        t = dev["tags"].get(m.tag_id(name))
        if t is None:
            problems.append(f"{name} missing")
        elif (t["memaddress"], t["address"], t["type"]) != (area, ft.fuxa_address(number), kind):
            problems.append(f"{name} is {t['memaddress']}/{t['address']}/{t['type']}, "
                            f"expected {area}/{ft.fuxa_address(number)}/{kind}")
        elif m.factor(name) != 1 and (t.get("scale"), t.get("format")) != tuple(ft.scale(m.factor(name)).values()):
            problems.append(f"{name}: scale {t.get('scale')} / format {t.get('format')}, expected / {m.factor(name)}")
        elif m.factor(name) == 1 and (t.get("scale") or t.get("format")):
            problems.append(f"{name} is scaled but should show the PLC value")
    missing = expected - set(have)
    if missing:
        problems.append(f"address map signals without a tag: {sorted(missing)}")
    status = project["devices"]["0"].get("tags", {}).get(m.status_tag_id)
    if not status or status.get("memaddress") != m.device_id:
        problems.append("connection status tag missing")
    # test page: one Value shape per tag, present in the SVG and bound to that tag
    page = next((v for v in project["hmi"]["views"] if v["id"] == page_id), None)
    if page is None:
        problems.append(f"view {page_id} missing")
    else:
        for name, *_ in m.tags:
            item = page["items"].get(fv.value_id(name, m))
            if item is None or item["property"].get("variableId") != m.tag_id(name):
                problems.append(f"tags page: {name} not bound")
            elif f'id="{fv.value_id(name, m)}"' not in page["svgcontent"]:
                problems.append(f"tags page: {name} not drawn")
    return check(name_, not problems,
                 f"{len(tags)} tags, polling {dev['polling']} ms, {dev['property']['address']}"
                 + (f"; {problems[:4]}" if problems else ""))


def prop(items: dict, item_id: str) -> dict:
    return items.get(item_id, {}).get("property", {})


def goes_to(items: dict, item_id: str) -> str | None:
    """The view a click on the item opens (None: no onpage event)."""
    ev = [e for e in prop(items, item_id).get("events", []) if e.get("action") == "onpage"]
    return ev[0]["actparam"] if ev else None


def shows_at(items: dict, item_id: str, tag: str, number: int) -> bool:
    """A text shown exactly while `tag` == number."""
    shows = [a for a in prop(items, item_id).get("actions", []) if a["type"] == "show"]
    return bool(shows) and shows[0]["variableId"] == tag and shows[0]["range"] == {"min": number, "max": number}


BOX_ATTR = re.compile(r'(?<![\w-])(x|y|width|height)="([-\d.]+)"')  # not rx= / stroke-width=


def header_boxes(svg: str, key: str) -> dict[str, tuple[float, float, float, float]]:
    """id -> (x, y, w, h) of every header rectangle of one view (chips; a button's rect is <id>_r)."""
    boxes = {}
    for tag in re.findall(r"<rect [^>]*>", svg):
        ident = re.search(r'id="([^"]+)"', tag)
        if ident and (ident[1].startswith(f"hdr_{key}_") or ident[1].startswith(f"HXB_hdr_{key}_")):
            a = {k: float(v) for k, v in BOX_ATTR.findall(tag)}
            boxes[ident[1]] = (a["x"], a["y"], a["width"], a["height"])
    return boxes


def layout_problems(svg: str, key: str) -> list[str]:
    """Header boxes inside 1280 x 96 and no two of the same row overlapping, except a PLC link cover,
    which must lie inside its own box (its machine chip, or its E-stop segment)."""
    boxes, problems = header_boxes(svg, key), []
    pairs = [(f"hdr_{key}_{m}_chip", f"hdr_{key}_{m}_link") for m in (ui.m.key for ui in fv.UIS)]
    pairs += [(f"hdr_{key}_estop_{m}", f"hdr_{key}_estop_{m}_link") for m in (ui.m.key for ui in fv.UIS)]
    allowed = {frozenset(pair) for pair in pairs}
    for box, cover in pairs:
        if box not in boxes or cover not in boxes:
            problems.append(f"{box} or {cover} missing")
            continue
        (bx, by, bw, bh), (cx, cy, cw, ch) = boxes[box], boxes[cover]
        if not (bx <= cx and cx + cw <= bx + bw and by <= cy and cy + ch <= by + bh):
            problems.append(f"{cover} not inside {box}")
    for ident, (x, y, w, h) in boxes.items():
        if x < 0 or y < 0 or x + w > fv.W or y + h > fv.HEADER_H:
            problems.append(f"{ident} outside the header")
    names = sorted(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (ax, ay, aw, ah), (bx, by, bw, bh) = boxes[a], boxes[b]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah and frozenset((a, b)) not in allowed:
                problems.append(f"{a} overlaps {b}")
    return problems


def h5_header() -> bool:
    """Every generated view: row 1 shows both machines, row 2 the machine of that view (fv.CONTEXT)."""
    project = json.loads(ft.EXPORT.read_text())
    views = {v["id"]: v for v in project["hmi"]["views"]}
    problems = []
    for view_id in fv.GENERATED:
        v = views.get(view_id)
        if v is None:
            problems.append(f"view {view_id} missing")
            continue
        items, svg, name = v["items"], v["svgcontent"], v["name"]
        key = fv.HEADER_KEY[view_id]
        ui = fv.CONTEXT[view_id]
        p = f"HXB_hdr_{key}"

        def writes(button_id: str) -> tuple | None:
            ev = prop(items, button_id).get("events", [])
            return (ev[0]["actoptions"].get("variableId"), int(ev[0]["actparam"])) if ev else None

        # Row 2: commands and mode go to the machine of this view
        for label, number in fv.CMD_BUTTONS:
            if writes(f"{p}_cmd_{label.lower()}") != (ui.m.tag_id("Cmd"), number):
                problems.append(f"{name}: {label} does not write {ui.m.key} Cmd={number}")
        for label, number in fv.MODE_BUTTONS:
            if writes(f"{p}_mode_{label.lower()}") != (ui.m.tag_id("ModeReq"), number):
                problems.append(f"{name}: mode {label} does not write {ui.m.key} ModeReq={number}")
        for bit in ui.alarms:
            if prop(items, f"hdr_{key}_{bit}").get("variableId") != ui.m.tag_id(bit):
                problems.append(f"{name}: no chip for {ui.m.key} {bit}")
        # Row 2: picker on the shared views (opens the same kind of view for the other machine)
        kind = fv.KIND.get(view_id)
        if kind is None:
            if f'id="hdr_{key}_machine"' not in svg:
                problems.append(f"{name}: no machine label in row 2")
        for other in fv.UIS if kind else ():
            target = goes_to(items, f"{p}_pick_{other.m.key}")
            if other is ui and (f"{p}_pick_{other.m.key}" not in items or target is not None):
                problems.append(f"{name}: picker {other.m.key} should be the current machine")
            elif other is not ui and (target not in views or fv.KIND.get(target) != kind
                                      or fv.CONTEXT[target] is not other):
                problems.append(f"{name}: picker {other.m.key} opens {target}")
        # Row 1: every machine with its PLC link cover, the E-stop of every PLC, navigation
        for chip in fv.UIS:
            k = f"hdr_{key}_{chip.m.key}"
            if prop(items, f"{k}_chip").get("variableId") != chip.m.tag_id("State") \
                    or goes_to(items, f"{k}_chip") != chip.detail:
                problems.append(f"{name}: {chip.m.key} chip not on State / not opening {chip.detail}")
            for prefix, names, tag in (("state", STATE_NAMES, "State"), ("step", chip.steps, "Step"),
                                       ("mode", fv.MODE_WORDS, "ModeReq")):
                for number in names:
                    if not shows_at(items, f"{k}_{prefix}_{number}", chip.m.tag_id(tag), number):
                        problems.append(f"{name}: {chip.m.key} has no {prefix} text for {number}")
            if prop(items, f"{k}_link").get("variableId") != chip.m.status_tag_id \
                    or not shows_at(items, f"{k}_link_lost", chip.m.status_tag_id, 0):
                problems.append(f"{name}: no link cover for {chip.m.key}")
            elif not (svg.find(f'id="{k}_mode_0"') < svg.find(f'id="{k}_link"') < svg.find(f'id="{k}_link_lost"')):
                problems.append(f"{name}: {chip.m.key} link cover not drawn over the state / mode words")
            elif 'pointer-events="none"' not in re.search(rf'<rect [^>]*id="{k}_link"[^>]*>', svg).group(0):
                problems.append(f"{name}: {chip.m.key} link cover catches the chip's click")
            elif header_boxes(svg, key).get(f"{k}_link") != header_boxes(svg, key).get(f"{k}_chip"):
                problems.append(f"{name}: {chip.m.key} link cover does not cover the whole chip (stale words show, P2)")
            elif not svg.find(f'id="{k}_link_lost"') < svg.find(f'id="{k}_title"'):
                problems.append(f"{name}: {chip.m.key} name not drawn on top of the link cover")
        for bit in ui.alarms:  # every alarm chip has a label of at most two lines
            if bit not in fv.ALARM_LABELS or not 1 <= len(fv.alarm_label_lines(fv.ALARM_LABELS[bit])) <= 2:
                problems.append(f"{name}: alarm chip {bit} without a short label")
        lefts = []
        for chip in fv.UIS:  # E-stop, a segment per PLC (I17): red at 0, OK / E-STOP texts, link cover on top
            e, k, status = chip.m.tag_id("E_Stop_OK"), f"hdr_{key}_estop_{chip.m.key}", chip.m.status_tag_id
            seg = prop(items, k)
            if seg.get("variableId") != e or {(r["min"], r["color"]) for r in seg.get("ranges", [])} != {(0, fv.RED), (1, fv.NEUTRAL)} \
                    or not shows_at(items, f"{k}_ok", e, 1) or not shows_at(items, f"{k}_on", e, 0):
                problems.append(f"{name}: E-stop segment not complete for {chip.m.key}")
            cover = prop(items, f"{k}_link")
            covered = {(r["min"], r["max"], r["color"]) for r in cover.get("ranges", [])}
            if cover.get("variableId") != status or covered != {(0, fv.LINK_OK - 1, fv.NO_LINK), (fv.LINK_OK, fv.LINK_OK, "none")}:
                problems.append(f"{name}: {chip.m.key} E-stop segment has no link cover on its own PLC's status")
            if not any(a["type"] == "show" and a["variableId"] == status and a["range"] == {"min": 0, "max": fv.LINK_OK - 1}
                       for a in prop(items, f"{k}_nolink").get("actions", [])):
                problems.append(f"{name}: {chip.m.key} has no 'no link' text")
            order = [svg.find(f'id="{k}{s}"') for s in ("", "_ok", "_on", "_link", "_name", "_nolink")]
            if -1 in order or order != sorted(order):
                problems.append(f"{name}: {chip.m.key} E-stop drawing order wrong (segment, texts, cover, name, no link)")
            boxes = header_boxes(svg, key)
            if boxes.get(f"{k}_link") != boxes.get(k):  # the whole segment: a partial cover left half of it red
                problems.append(f"{name}: {chip.m.key} link cover does not cover the whole E-stop segment")
            lefts.append(header_boxes(svg, key).get(k, (0,))[0])
        if lefts != sorted(lefts):
            problems.append(f"{name}: E-stop segments not in machine order {lefts}")
        for nav_kind, label in fv.NAV_BUTTONS:
            target = goes_to(items, f"{p}_nav_{nav_kind}")
            if target not in views or fv.KIND.get(target) != nav_kind or fv.CONTEXT[target] is not ui:
                problems.append(f"{name}: nav {label} opens {target}")
        # FUXA sends all events of one click at once; several writes per click dropped the
        # PLC link (stage 3). Every header button must do exactly one thing.
        many = []
        for i in items.values():
            kinds = [e["type"] for e in i.get("property", {}).get("events", [])]
            if len(kinds) != len(set(kinds)):
                many.append(i["name"])
        if many:
            problems.append(f"{name}: shapes with two events of the same kind {many[:3]}")
        missing = [i for i in items if f'id="{i}"' not in svg]
        if missing:
            problems.append(f"{name}: items without SVG element {missing[:3]}")
        ids = re.findall(r'\sid="([^"]+)"', svg)  # one id = one FUXA binding: a duplicate drives two shapes (L4)
        twice = sorted({i for i in ids if ids.count(i) > 1})
        if twice:
            problems.append(f"{name}: element ids used twice {twice[:4]}")
        problems += [f"{name}: {x}" for x in layout_problems(svg, key)]
    return check("H5 header", not problems, f"{len(fv.GENERATED)} views, start {project['hmi']['layout'].get('start')}"
                 + (f"; {problems[:4]}" if problems else ""))


LAMP_TAGS = ("ConveyerMotor1", "S2", "DoorMotor", "Entry_Load", "S3", "RinseValve", "S4", "FillValve",
             "S5", "CapMotor", "IndexMotor", "S8", "S6", "S7", "Outfeed_Full", "Jog_Block")


def h6_filler() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.MAIN_ID), None)
    if view is None:
        return check("H6 Filler screen", False, "view missing")
    items, svg, problems = view["items"], view["svgcontent"], []
    mode = ft.tag_id("ModeReq")
    for label, tag in fv.MANUAL_BUTTONS:
        item = items.get(f"HXB_man_{label.lower()}", {}).get("property", {})
        ev = {(e["type"], e["actparam"], e["actoptions"]["variableId"]) for e in item.get("events", [])}
        want = {(k, n, ft.tag_id(tag)) for k, n in (("mousedown", "1"), ("mouseup", "0"), ("mouseout", "0"))}
        shows = [a for a in item.get("actions", []) if a["type"] == "show" and a["variableId"] == mode]
        if ev != want or not shows or shows[0]["range"] != {"min": fv.MANUAL, "max": fv.MANUAL}:
            problems.append(f"manual {label} wrong")
    for _label, tag, _unit, lo, hi in fv.SETPOINTS:
        prop = items.get(f"HXI_in_{tag}", {}).get("property", {})
        opts = prop.get("options", {})
        if prop.get("variableId") != ft.tag_id(tag) or not opts.get("min", 0) < opts.get("max", 0) or (opts["min"], opts["max"]) != (lo, hi):
            problems.append(f"setpoint {tag} wrong")
    for tag in LAMP_TAGS:
        if items.get(f"lamp_{tag}", {}).get("property", {}).get("variableId") != ft.tag_id(tag):
            problems.append(f"no lamp {tag}")
    for name, anim, _lamps in fv.POCKETS:
        key = name.lower()
        if items.get(f"pocket_{key}", {}).get("property", {}).get("variableId") != ft.tag_id(anim):
            problems.append(f"pocket {name} not bound")
        words = [i for i in items if i.startswith(f"pocket_{key}_word_")]
        if len(words) != len(fv.POCKET_WORDS):
            problems.append(f"pocket {name}: {len(words)} state words")
    shows = [a for a in items.get("manual_blocked", {}).get("property", {}).get("actions", []) if a["type"] == "show"]
    if not shows or shows[0]["variableId"] != ft.tag_id("Jog_Block") or shows[0]["range"] != {"min": 1, "max": 1}:
        problems.append("no 'manual blocked' text on Jog_Block")
    for bar, tag in (("GXP_fill_level", "FillLevel"), ("GXP_fill_setpoint", "FillSetpoint")):
        if items.get(bar, {}).get("property", {}).get("variableId") != ft.tag_id(tag):
            problems.append(f"bar {tag} missing")
    missing = [i for i in items if f'id="{i}"' not in svg]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return check("H6 Filler screen", not problems, f"{len(items)} items" + (f"; {problems[:4]}" if problems else ""))


SYRUP_LAMPS = ("WaterPump", "SyrupPump", "TransferPump", "Product_Available", "MixHighHigh", "Agitator",
               "CoolingValve", "CO2Valve", "Temp_OK", "CO2_Demand", "Batch_Dosed")
SYRUP_TANKS = (("GXP_tank_syrup", "SyrupLevel", config.SYRUP_TANK_L), ("GXP_tank_mix", "MixLevel", config.MIX_TANK_L),
               ("GXP_tank_buffer", "BufferLevel", config.BUFFER_TANK_L))


def h11_syrup() -> bool:
    """The syrup room screen (S3): same rules as the Filler screen, on the syrup PLC's tags."""
    project = json.loads(ft.EXPORT.read_text())
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.SYRUP_MAIN_ID), None)
    if view is None:
        return check("H11 Syrup screen", False, "view missing")
    items, svg, problems = view["items"], view["svgcontent"], []
    S, mode = ft.SYRUP.tag_id, ft.SYRUP.tag_id("ModeReq")
    # the agitator impeller turns with the syrup Agitator output (clockwise at 1, stop at 0) and nothing else
    acts = prop(items, "agitator_impeller").get("actions", [])
    if {(a["type"], a["range"]["min"], a["variableId"]) for a in acts} != {("clockwise", 1, S("Agitator")),
                                                                           ("stop", 0, S("Agitator"))}:
        problems.append("agitator impeller does not turn with the syrup Agitator output")
    # the simulator's buffer draw factor is on the screen, with the numbers of the current config
    for k, line in enumerate(fv.buffer_scale_note()):
        if not re.search(rf'<text id="note_buffer_scale{k}"[^>]*>{re.escape(fs_esc(line))}</text>', svg):
            problems.append(f"buffer scale note line {k} missing or outdated")
    if f"{config.BUFFER_DRAW_SCALE:g}" not in fv.buffer_scale_note()[1]:
        problems.append("buffer scale note does not name the factor")
    for label, tag in fv.SYRUP_MANUAL_BUTTONS:  # hold to run, on the SYRUP tag, only in its Manual mode
        item = prop(items, f"HXB_syrup_{tag}")
        ev = {(e["type"], e["actparam"], e["actoptions"]["variableId"]) for e in item.get("events", [])}
        want = {(k, n, S(tag)) for k, n in (("mousedown", "1"), ("mouseup", "0"), ("mouseout", "0"))}
        shows = [a for a in item.get("actions", []) if a["type"] == "show" and a["variableId"] == mode]
        if ev != want or not shows or shows[0]["range"] != {"min": fv.MANUAL, "max": fv.MANUAL}:
            problems.append(f"manual {label} wrong")
    for _label, tag, _unit, lo, hi in fv.SYRUP_SETPOINTS:  # recipe inputs with the screen's limits
        p = prop(items, f"HXI_in_{tag}")
        opts = p.get("options", {})
        if p.get("variableId") != S(tag) or not lo < hi or (opts.get("min"), opts.get("max")) != (lo, hi):
            problems.append(f"setpoint {tag} wrong")
        decimal = ft.SYRUP.factor(tag) != 1  # degC / bar: the browser must accept decimals
        if (f'id="I-HXI_in_{tag}" type="number" step="any"' in svg) != decimal:
            problems.append(f"setpoint {tag}: step attribute")
    for tag in SYRUP_LAMPS:
        if prop(items, f"lamp_{tag}").get("variableId") != S(tag):
            problems.append(f"no lamp {tag}")
    for bar, tag, capacity in SYRUP_TANKS:
        p = prop(items, bar)
        if p.get("variableId") != S(tag) or (p.get("ranges") or [{}])[0].get("max") != capacity:
            problems.append(f"tank bar {tag} wrong")
    for tag in ("ProductTemp", "CO2Pressure", "BatchCount", "SyrupTarget", "DoseTarget"):
        if prop(items, f"val_{tag}").get("variableId") != S(tag):
            problems.append(f"no value {tag}")
    for pipe_id, tag, *_ in fv.SYRUP_PIPES:
        if prop(items, pipe_id).get("variableId") != S(tag):
            problems.append(f"pipe {pipe_id} not on {tag}")
    for number in SYRUP_STEP_NAMES:
        if not shows_at(items, f"syrup_step_{number}", S("Step"), number):
            problems.append(f"no step text {number}")
    if not shows_at(items, "syrup_manual_blocked", S("Jog_Block"), 1):
        problems.append("no 'manual blocked' text on Jog_Block")
    missing = [i for i in items if f'id="{i}"' not in svg]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return check("H11 Syrup screen", not problems, f"{len(items)} items" + (f"; {problems[:4]}" if problems else ""))


LABELER_LAMPS = ("ConveyorMotor2", "Bottle_At_Gate", "GateMotor", "Bottle_Entering", "Roll_Low", "Roll_Empty",
                 "Label_Mark", "Cutter", "GlueRoller", "Bottle_In_Pocket", "ClampValve", "Bell_Down", "Label_Present",
                 "SpinMotor", "Spin_Pulse", "DischargeMotor", "C3_Full", "Outfeed_Full", "GlueHeater", "Heat_Demand",
                 "Glue_Ready", "Glue_Low", "Starved", "Jog_Block")
LABEL_TIMEOUT_S = 5  # PLC constant LabelTimeout (docs/labeler/state-diagram.md)


def h12_labeler() -> bool:
    """The labeler screen (HMI L3): hold buttons, setpoints with safe limits, lamps, pocket, glue bar, counter reset."""
    project = json.loads(ft.EXPORT.read_text())
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.LABELER_MAIN_ID), None)
    if view is None:
        return check("H12 Labeler screen", False, "view missing")
    items, svg, problems = view["items"], view["svgcontent"], []
    L, mode = ft.LABELER.tag_id, ft.LABELER.tag_id("ModeReq")
    for label, tag in fv.LABELER_MANUAL_BUTTONS:  # hold to run, on the LABELER tag, only in its Manual mode
        item = prop(items, f"HXB_labeler_{tag}")
        ev = {(e["type"], e["actparam"], e["actoptions"]["variableId"]) for e in item.get("events", [])}
        want = {(k, n, L(tag)) for k, n in (("mousedown", "1"), ("mouseup", "0"), ("mouseout", "0"))}
        shows = [a for a in item.get("actions", []) if a["type"] == "show" and a["variableId"] == mode]
        if ev != want or not shows or shows[0]["range"] != {"min": fv.MANUAL, "max": fv.MANUAL}:
            problems.append(f"manual {label} wrong")
    if {t for _l, t in fv.LABELER_MANUAL_BUTTONS} != {n for n, *_ in ft.LABELER_TAGS if n.startswith("Man_")}:
        problems.append("not one hold button per Man_* tag")
    limits = {}
    for _label, tag, _unit, lo, hi in fv.LABELER_SETPOINTS:
        p = prop(items, f"HXI_in_labeler_{tag}")
        opts = p.get("options", {})
        if p.get("variableId") != L(tag) or not lo < hi or (opts.get("min"), opts.get("max")) != (lo, hi):
            problems.append(f"setpoint {tag} wrong")
        if (f'id="I-HXI_in_labeler_{tag}" type="number" step="any"' in svg) != (ft.LABELER.factor(tag) != 1):
            problems.append(f"setpoint {tag}: step attribute")
        limits[tag] = (lo, hi)
    # the limits keep every legal combination safe (fv.LABELER_SETPOINTS comment)
    sp, hyst, band, turns = (limits.get(t, (0, 0)) for t in ("GlueSetpoint", "GlueHyst", "GlueBand", "PressTurns"))
    if sp[0] - band[1] < config.GLUE_STICK_MIN_C:
        problems.append(f"lowest ready glue {sp[0] - band[1]} degC < stick limit {config.GLUE_STICK_MIN_C}")
    if sp[1] + band[1] >= fv.GLUE_MAX_C:
        problems.append(f"highest window edge {sp[1] + band[1]} degC reaches GlueMax {fv.GLUE_MAX_C}")
    if hyst[1] >= band[0]:
        problems.append(f"heater band {hyst[1]} not below the window {band[0]}: Glue_Ready would drop while heating")
    if turns[1] * config.SPIN_TURN_S >= LABEL_TIMEOUT_S:
        problems.append(f"{turns[1]} turns reach the {LABEL_TIMEOUT_S} s label timeout")
    for tag in LABELER_LAMPS:
        if prop(items, f"lamp_{tag}").get("variableId") != L(tag):
            problems.append(f"no lamp {tag}")
    for bar, tag in (("GXP_glue_temp", "GlueTemp"), ("GXP_glue_setpoint", "GlueSetpoint")):
        p = prop(items, bar)
        if p.get("variableId") != L(tag) or (p.get("ranges") or [{}])[0].get("max") != fv.GLUE_SCALE_C:
            problems.append(f"bar {tag} wrong")
    if 'id="glue_max_tick"' not in svg:
        problems.append("no GlueMax tick")
    for value_id, tag in (("val_GlueTemp", "GlueTemp"), ("val_labeler_GoodCount", "GoodCount"),
                          ("val_labeler_GlueTemp", "GlueTemp"), ("val_labeler_GlueSetpoint", "GlueSetpoint")):
        if prop(items, value_id).get("variableId") != L(tag):
            problems.append(f"no value {value_id}")
    ev = prop(items, "HXB_labeler_reset_counter").get("events", [])  # one write: Cmd = 4 of the LABELER
    if [(e["type"], e["actoptions"].get("variableId"), e["actparam"]) for e in ev] != [("click", L("Cmd"), "4")]:
        problems.append(f"Reset counter does not write labeler Cmd = 4 (once): {ev}")
    for element_id in ("pocket_body", "pocket_label"):
        p = prop(items, element_id)
        if p.get("variableId") != L("Anim_Station") or {r["min"] for r in p.get("ranges", [])} != set(fv.LABELER_POCKET_WORDS):
            problems.append(f"{element_id} not on every Anim_Station code")
    for code in fv.LABELER_POCKET_WORDS:
        if not shows_at(items, f"pocket_word_{code}", L("Anim_Station"), code):
            problems.append(f"no pocket word {code}")
    for i in range(8):
        p = prop(items, f"lab_c3_slot{i}")
        if p.get("variableId") != L("Anim_C3") or p.get("bitmask") != 1 << i:
            problems.append(f"C3 slot {i} not on bit {i} of Anim_C3")
        p = prop(items, f"lab_c3_label{i}")  # every bottle on C3 is labelled: white band with the same bit
        if p.get("variableId") != L("Anim_C3") or p.get("bitmask") != 1 << i:
            problems.append(f"C3 label {i} not on bit {i} of Anim_C3")
        p = prop(items, f"lab_c3_cap{i}")  # every bottle after the filler is capped: cap with the same bit
        if p.get("variableId") != L("Anim_C3") or p.get("bitmask") != 1 << i:
            problems.append(f"C3 cap {i} not on bit {i} of Anim_C3")
    p = prop(items, "pocket_cap")  # the pocket bottle came capped: cap on every code except empty
    capped = {r["min"] for r in p.get("ranges", []) if r.get("color") == fv.CAP_COLOUR}
    if p.get("variableId") != L("Anim_Station") or capped != set(fv.LABELER_POCKET_WORDS) - {lm.POCKET_EMPTY}:
        problems.append(f"pocket cap not on every non-empty Anim_Station code: {sorted(capped)}")
    acts = prop(items, "label_roll").get("actions", [])
    if {(a["type"], a["range"]["min"], a["variableId"]) for a in acts} != {("clockwise", 1, L("LabelFeed")),
                                                                           ("stop", 0, L("LabelFeed"))}:
        problems.append("label roll does not turn with LabelFeed")
    acts = prop(items, "turntable").get("actions", [])
    if {(a["type"], a["range"]["min"], a["variableId"]) for a in acts} != {("clockwise", 1, L("SpinMotor")),
                                                                           ("stop", 0, L("SpinMotor"))}:
        problems.append("turntable does not turn with SpinMotor")
    for pipe_id in ("web_top", "web_down", "web_in"):
        if prop(items, pipe_id).get("variableId") != L("LabelFeed"):
            problems.append(f"{pipe_id} not on LabelFeed")
    if prop(items, "bell").get("variableId") != L("Bell_Down") or prop(items, "gate").get("variableId") != L("GateMotor"):
        problems.append("bell / gate not on Bell_Down / GateMotor")
    for number in fv.LABELER_STEP_NAMES:
        if not shows_at(items, f"labeler_step_{number}", L("Step"), number):
            problems.append(f"no step text {number}")
    if not shows_at(items, "labeler_manual_blocked", L("Jog_Block"), 1):
        problems.append("no 'manual blocked' text on Jog_Block")
    missing = [i for i in items if f'id="{i}"' not in svg]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return check("H12 Labeler screen", not problems, f"{len(items)} items" + (f"; {problems[:4]}" if problems else ""))


PACKER_FEED_TIMEOUT_S = 10  # PLC constant FeedTimeout (docs/packer/state-diagram.md)
PACKER_HEAT_TIMEOUT_S = 120  # PLC constant HeatTimeout
PACKER_FILM_MIN, PACKER_FILM_MAX = 600, 1200  # PLC LIMIT on FilmLength (FilmMin, FilmMax)


def oven_warm_up_s(target_c: float) -> float:
    """Seconds the simulator's oven needs from room temperature to target_c with the heater on all the time
    (Oven._heat: exponential toward the balance of heater and room loss)."""
    rate_heat, rate_room = 1 / config.OVEN_HEAT_TAU_S, 1 / config.OVEN_ROOM_TAU_S
    balance = (rate_heat * config.OVEN_HEATER_C + rate_room * config.ROOM_C) / (rate_heat + rate_room)
    if target_c >= balance:
        return float("inf")
    return -math.log((balance - target_c) / (balance - config.ROOM_C)) / (rate_heat + rate_room)


def h13_packer() -> bool:
    """The packer screen (HMI P3): hold buttons, setpoints with safe limits, a lamp per signal, cells on their
    bits, the film sheet on every Anim_Film code, oven bar, counter reset."""
    project = json.loads(ft.EXPORT.read_text())
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.PACKER_MAIN_ID), None)
    if view is None:
        return check("H13 Packer screen", False, "view missing")
    items, svg, problems = view["items"], view["svgcontent"], []
    P, mode = ft.PACKER.tag_id, ft.PACKER.tag_id("ModeReq")
    for label, tag in fv.PACKER_MANUAL_BUTTONS:  # hold to run, on the PACKER tag, only in its Manual mode
        item = prop(items, f"HXB_packer_{tag}")
        ev = {(e["type"], e["actparam"], e["actoptions"]["variableId"]) for e in item.get("events", [])}
        want = {(k, n, P(tag)) for k, n in (("mousedown", "1"), ("mouseup", "0"), ("mouseout", "0"))}
        shows = [a for a in item.get("actions", []) if a["type"] == "show" and a["variableId"] == mode]
        if ev != want or not shows or shows[0]["range"] != {"min": fv.MANUAL, "max": fv.MANUAL}:
            problems.append(f"manual {label} wrong")
    if {t for _l, t in fv.PACKER_MANUAL_BUTTONS} != {n for n, *_ in ft.PACKER_TAGS if n.startswith("Man_")}:
        problems.append("not one hold button per Man_* tag")
    limits = {}
    for _label, tag, _unit, lo, hi in fv.PACKER_SETPOINTS:
        p = prop(items, f"HXI_in_packer_{tag}")
        opts = p.get("options", {})
        if p.get("variableId") != P(tag) or not lo < hi or (opts.get("min"), opts.get("max")) != (lo, hi):
            problems.append(f"setpoint {tag} wrong")
        if (f'id="I-HXI_in_packer_{tag}" type="number" step="any"' in svg) != (ft.PACKER.factor(tag) != 1):
            problems.append(f"setpoint {tag}: step attribute")
        limits[tag] = (lo, hi)
    # the limits keep every legal combination safe (fv.PACKER_SETPOINTS comment), each rule by name
    sp, hyst, band, film = (limits.get(t, (0, 0)) for t in ("OvenSetpoint", "OvenHyst", "OvenBand", "FilmLength"))
    if sp[0] - band[1] < config.SHRINK_C:
        problems.append(f"shrink rule: lowest ready oven {sp[0] - band[1]} degC < SHRINK_C {config.SHRINK_C}")
    if sp[1] >= fv.OVEN_MAX_C:
        problems.append(f"OvenMax rule: setpoint up to {sp[1]} degC reaches OvenMax {fv.OVEN_MAX_C}")
    if hyst[1] >= band[0]:
        problems.append(f"band rule: heater band {hyst[1]} not below the window {band[0]}: Oven_Ready would drop")
    if oven_warm_up_s(sp[1] - band[0]) >= PACKER_HEAT_TIMEOUT_S:
        problems.append(f"warm-up rule: {sp[1] - band[0]} degC takes {oven_warm_up_s(sp[1] - band[0]):.0f} s "
                        f">= HeatTimeout {PACKER_HEAT_TIMEOUT_S} s")
    if film[1] / config.FILM_FEED_MM_S >= PACKER_FEED_TIMEOUT_S or film != (PACKER_FILM_MIN, PACKER_FILM_MAX):
        problems.append(f"film rule: {film} mm (PLC LIMIT {PACKER_FILM_MIN}-{PACKER_FILM_MAX}, feed < {PACKER_FEED_TIMEOUT_S} s)")
    lamps = [tag for _x, column in fv.PACKER_LAMP_COLUMNS for tag, _label in column] + ["Starved", "Jog_Block"]
    signals = [n for n, *_ , d in ft.PACKER_TAGS if d.split(":")[0] in ("output", "conditioned", "sensor")
               and n != "OvenTemp"]
    if sorted(lamps) != sorted(signals):
        problems.append(f"lamps != outputs + conditioned + sensors: {sorted(set(signals) ^ set(lamps))}")
    for tag in lamps:
        if prop(items, f"lamp_{tag}").get("variableId") != P(tag):
            problems.append(f"no lamp {tag}")
    for bar, tag in (("GXP_oven_temp", "OvenTemp"), ("GXP_oven_setpoint", "OvenSetpoint")):
        p = prop(items, bar)
        if p.get("variableId") != P(tag) or (p.get("ranges") or [{}])[0].get("max") != fv.OVEN_SCALE_C:
            problems.append(f"bar {tag} wrong")
    if 'id="oven_max_tick"' not in svg:
        problems.append("no OvenMax tick")
    for value_id, tag in (("val_OvenTemp", "OvenTemp"), ("val_packer_PackCount", "PackCount"),
                          ("val_packer_GroupCount", "GroupCount"), ("val_packer_FeedCount", "FeedCount"),
                          ("val_packer_FeedPulses", "FeedPulses"), ("val_packer_PacksInOven", "PacksInOven")):
        if prop(items, value_id).get("variableId") != P(tag):
            problems.append(f"no value {value_id}")
    ev = prop(items, "HXB_packer_reset_counter").get("events", [])  # one write: Cmd = 4 of the PACKER
    if [(e["type"], e["actoptions"].get("variableId"), e["actparam"]) for e in ev] != [("click", P("Cmd"), "4")]:
        problems.append(f"Reset counter does not write packer Cmd = 4 (once): {ev}")
    for prefix, tag, cells in (("pk_wrap", "Anim_Wrap", pm.WRAP_CELLS), ("pk_oven", "Anim_Oven", pm.OVEN_CELLS)):
        for i in range(cells):
            p = prop(items, f"{prefix}{i}")
            if p.get("variableId") != P(tag) or p.get("bitmask") != 1 << i:
                problems.append(f"{prefix} cell {i} not on bit {i} of {tag}")
    for i in range(config.PACK_BOTTLES):  # place i shows a bottle from GroupCount i + 1 on
        lit = {(r["min"], r["max"]) for r in prop(items, f"pk_table{i}").get("ranges", []) if r["color"] == fv.PRODUCT}
        if prop(items, f"pk_table{i}").get("variableId") != P("GroupCount") or lit != {(i + 1, config.PACK_BOTTLES)}:
            problems.append(f"collation place {i} not lit from GroupCount {i + 1}")
    for i in range(config.TABLE_PACKS):
        lit = {(r["min"], r["max"]) for r in prop(items, f"pk_out{i}").get("ranges", []) if r["color"] == fv.PRODUCT}
        if prop(items, f"pk_out{i}").get("variableId") != P("Anim_Table") or lit != {(i + 1, config.TABLE_PACKS)}:
            problems.append(f"outfeed place {i} not lit from Anim_Table {i + 1}")
    for bit in (5, 6, 7):  # the end of C3 from the LABELER's slot word, capped and labelled
        for part in ("slot", "label", "cap"):
            p = prop(items, f"pk_c3_{part}{bit}")
            if p.get("variableId") != ft.LABELER.tag_id("Anim_C3") or p.get("bitmask") != 1 << bit:
                problems.append(f"C3 {part} {bit} not on bit {bit} of the labeler's Anim_C3")
    sheet = {}  # Anim_Film code -> (up, under): which sheet shape is drawn
    for element_id in ("pk_sheet_up", "pk_sheet_under"):
        p = prop(items, element_id)
        if p.get("variableId") != P("Anim_Film"):
            problems.append(f"{element_id} not on Anim_Film")
        for r in p.get("ranges", []):
            for code in range(r["min"], r["max"] + 1):
                sheet.setdefault(code, {})[element_id] = r["color"] != "none"
    want = {pm.SHEET_NONE: (False, False), pm.SHEET_FEEDING: (True, False), pm.SHEET_IN_SLOT: (True, False),
            pm.SHEET_UNDER_PACK: (False, True)}
    got = {c: (v.get("pk_sheet_up"), v.get("pk_sheet_under")) for c, v in sheet.items()}
    if got != want:
        problems.append(f"film sheet per code {got}, expected {want}")
    for code in fv.FILM_WORDS:
        if not shows_at(items, f"pk_film_word_{code}", P("Anim_Film"), code):
            problems.append(f"no film word {code}")
    turning = {"pk_film_roll": "FilmFeed", "pk_wrap_bar": "WrapBar", "pk_blower": "OvenBlower",
               **{f"pk_c3_roller{k}": "ConveyorMotor3" for k in range(2)},
               **{f"pk_wrap_roller{k}": "WrapConveyor" for k in range(3)},
               **{f"pk_oven_roller{k}": "OvenConveyor" for k in range(3)}}
    for element_id, tag in turning.items():
        acts = prop(items, element_id).get("actions", [])
        if {(a["type"], a["range"]["min"], a["variableId"]) for a in acts} != {("clockwise", 1, P(tag)), ("stop", 0, P(tag))}:
            problems.append(f"{element_id} does not turn with {tag}")
    for element_id, tag in (("pk_gate", "GateMotor"), ("pk_pusher", "GroupPusher"), ("pk_knife", "Knife"),
                            ("pk_heater", "OvenHeater")):
        if prop(items, element_id).get("variableId") != P(tag):
            problems.append(f"{element_id} not on {tag}")
    for prefix, tag, names in (("packer_step", "Step", fv.PACKER_STEP_NAMES), ("packer_wrap_step", "WrapStep",
                                                                                 fv.PACKER_WRAP_STEP_NAMES)):
        for number in names:
            if not shows_at(items, f"{prefix}_{number}", P(tag), number):
                problems.append(f"no {tag} text {number}")
    if not shows_at(items, "packer_manual_blocked", P("Jog_Block"), 1):
        problems.append("no 'manual blocked' text on Jog_Block")
    missing = [i for i in items if f'id="{i}"' not in svg]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return check("H13 Packer screen", not problems, f"{len(items)} items" + (f"; {problems[:4]}" if problems else ""))


def h7_line() -> bool:
    """Every Line view (filler, syrup room, labeler in header row 2) has the same body."""
    project = json.loads(ft.EXPORT.read_text())
    problems, counts = [], []
    for view_id in fv.SHARED["line"].values():
        view = next((v for v in project["hmi"]["views"] if v["id"] == view_id), None)
        if view is None:
            problems.append(f"view {view_id} missing")
            continue
        counts.append(len(view["items"]))
        problems += [f"{view['name']}: {x}" for x in h7_body(view)]
    if project["hmi"]["layout"].get("start") != fv.LINE_ID:
        problems.append("start page is not Line")
    return check("H7 Line overview", not problems, f"items {counts}" + (f"; {problems[:4]}" if problems else ""))


def h7_body(view: dict) -> list[str]:
    items, problems = view["items"], []
    # slot words live in the PLC the simulator writes them to: C1 / C2 in the filler, C3 in the labeler (L0)
    for key, anim, m in (("c1", "Anim_C1", ft.FILLER), ("c2", "Anim_C2", ft.FILLER), ("c3", "Anim_C3", ft.LABELER)):
        for i in range(8):
            prop = items.get(f"{key}_slot{i}", {}).get("property", {})
            if prop.get("variableId") != m.tag_id(anim) or prop.get("bitmask") != 1 << i:
                problems.append(f"{key} slot {i} not on bit {i} of {m.key} {anim}")
    def turns_with(item_id: str) -> str | None:
        acts = items.get(item_id, {}).get("property", {}).get("actions", [])
        kinds = {(a["type"], a["range"]["min"], a["variableId"]) for a in acts}
        tags = {a["variableId"] for a in acts}
        if len(tags) == 1 and kinds == {("clockwise", 1, *tags), ("stop", 0, *tags)}:
            return tags.pop()
        return None if not acts else "wrong"
    for k in range(3):
        if turns_with(f"c1_roller{k}") != ft.tag_id("ConveyerMotor1"):
            problems.append(f"C1 roller {k}")
        if turns_with(f"c2_roller{k}") != ft.LABELER.tag_id("ConveyorMotor2"):  # the labeler drives C2 (L4)
            problems.append(f"C2 roller {k} not on the labeler's ConveyorMotor2")
        if turns_with(f"c3_roller{k}") != ft.PACKER.tag_id("ConveyorMotor3"):  # the packer drives C3 (P4)
            problems.append(f"C3 roller {k} not on the packer's ConveyorMotor3")
    for k in range(2):
        if turns_with(f"filler_roller{k}") != ft.tag_id("IndexMotor"):
            problems.append(f"index roller {k}")
    ev = items.get("filler_block", {}).get("property", {}).get("events", [])
    if not ev or ev[0]["action"] != "onpage" or ev[0]["actparam"] != fv.MAIN_ID:
        problems.append("filler block does not open the Filler view")
    problems += h7_syrup(view)
    problems += h7_labeler(view)
    problems += h7_packer(view)
    missing = [i for i in items if f'id="{i}"' not in view["svgcontent"]]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return problems


def h7_labeler(view: dict) -> list[str]:
    """L4: the labeler block (State colour, click -> its screen, step and glue drawn click-through on top),
    C3 lamps on the labeler's tags, and the row left to right without overlap:
    C1 < filler < C2 < labeler < C3, packer stand-in below the end of C3."""
    items, svg, problems = view["items"], view["svgcontent"], []
    L = ft.LABELER.tag_id
    block = prop(items, "labeler_block")
    if block.get("variableId") != L("State") or {(r["min"], r["max"]) for r in block.get("ranges", [])} \
            != {(v, v) for v in fv.STATE_COLOURS}:
        problems.append("labeler block not coloured by the labeler State")
    if goes_to(items, "labeler_block") != fv.LABELER_MAIN_ID:
        problems.append("labeler block does not open the Labeler view")
    on_top = []
    for prefix, tag, names in (("labeler_state", "State", STATE_NAMES), ("labeler_step_line", "Step", fv.LABELER_STEP_NAMES)):
        bad = [n for n in names if not shows_at(items, f"{prefix}_{n}", L(tag), n)]
        if bad:
            problems.append(f"labeler {tag} texts {bad[:3]}")
        on_top += [f"{prefix}_{n}" for n in names]
    if prop(items, "line_glue").get("variableId") != L("GlueTemp"):
        problems.append("glue value not on the labeler GlueTemp")
    on_top.append("line_glue")
    for element_id in on_top:
        element = re.search(rf'<\w+ [^>]*id="{element_id}"[^>]*>', svg)
        if element is None or 'pointer-events="none"' not in element.group(0):
            problems.append(f"{element_id} catches the labeler block's click")
            break
    if prop(items, "line_labeler_goodcount").get("variableId") != L("GoodCount"):
        problems.append("labelled-bottle counter not on the labeler GoodCount")
    # two lamps with the same tag name from two PLCs: each its own id and binding (found in L4: one id drove both)
    for lamp_id, tag in (("lamp_Outfeed_Full", ft.tag_id("Outfeed_Full")), ("lamp_S7", ft.tag_id("S7")),
                         ("lamp_labeler_Outfeed_Full", L("Outfeed_Full")), ("lamp_labeler_C3_Full", L("C3_Full"))):
        if prop(items, lamp_id).get("variableId") != tag:
            problems.append(f"{lamp_id} not on {tag}")
    for key, m in (("c2", ft.FILLER), ("c3", ft.LABELER)):  # bottles after the filler wear their cap
        for i in range(8):
            p = prop(items, f"{key}_cap{i}")
            if p.get("variableId") != m.tag_id(f"Anim_{key.upper()}") or p.get("bitmask") != 1 << i:
                problems.append(f"{key}_cap{i} not on bit {i} of Anim_{key.upper()}")
    for i in range(8):  # C3 bottles wear their label (Anim_C3 of the labeler PLC, same bit as the slot)
        p = prop(items, f"c3_label{i}")
        if p.get("variableId") != L("Anim_C3") or p.get("bitmask") != 1 << i:
            problems.append(f"c3_label{i} not on bit {i} of the labeler's Anim_C3")
    ids = re.findall(r'\sid="([^"]+)"', svg)
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        problems.append(f"element ids used twice (one FUXA binding for two shapes): {twice[:4]}")

    def x_span(element_id: str) -> tuple[float, float, float] | None:
        element = re.search(rf'<rect [^>]*id="{element_id}"[^>]*>', svg)
        if element is None:
            return None
        a = dict(BOX_ATTR.findall(element.group(0)))
        return float(a["x"]), float(a["x"]) + float(a["width"]), float(a["y"])
    row = [x_span(i) for i in ("c1_slot0", "c1_slot7", "filler_block", "c2_slot0", "c2_slot7", "labeler_block",
                               "c3_slot0", "c3_slot7")]
    if None in row:
        return problems + ["a part of the row is missing"]
    edges = [v for left, right, _ in row for v in (left, right)]
    if edges != sorted(edges) or row[-1][1] > fv.W:  # strictly left to right, inside the view
        problems.append(f"row not left to right without overlap: {[(round(a), round(b)) for a, b, _ in row]}")
    return problems


def h7_packer(view: dict) -> list[str]:
    """P4: the packer block under the end of C3 (State colour, click -> its screen, both steps, oven
    temperature and packs in the oven drawn click-through on top), the chute from C3's end down to it, the
    palletizer stand-in left of it, the pack counter."""
    items, svg, problems = view["items"], view["svgcontent"], []
    P = ft.PACKER.tag_id
    block = prop(items, "packer_block")
    if block.get("variableId") != P("State") or {(r["min"], r["max"]) for r in block.get("ranges", [])} \
            != {(v, v) for v in fv.STATE_COLOURS}:
        problems.append("packer block not coloured by the packer State")
    if goes_to(items, "packer_block") != fv.PACKER_MAIN_ID:
        problems.append("packer block does not open the Packer view")
    on_top = []
    for prefix, tag, names in (("packer_state", "State", STATE_NAMES), ("packer_step_line", "Step", fv.PACKER_STEP_NAMES),
                               ("packer_wrap_line", "WrapStep", fv.PACKER_WRAP_STEP_NAMES)):
        bad = [n for n in names if not shows_at(items, f"{prefix}_{n}", P(tag), n)]
        if bad:
            problems.append(f"packer {tag} texts {bad[:3]}")
        on_top += [f"{prefix}_{n}" for n in names]
    for value_id, tag in (("line_oven", "OvenTemp"), ("line_packs_in_oven", "PacksInOven")):
        if prop(items, value_id).get("variableId") != P(tag):
            problems.append(f"{value_id} not on the packer {tag}")
        on_top.append(value_id)
    for element_id in on_top:
        element = re.search(rf'<\w+ [^>]*id="{element_id}"[^>]*>', svg)
        if element is None or 'pointer-events="none"' not in element.group(0):
            problems.append(f"{element_id} catches the packer block's click")
            break
    if prop(items, "line_packer_packcount").get("variableId") != P("PackCount"):
        problems.append("pack counter not on the packer PackCount")

    def box(element_id: str) -> tuple[float, float, float, float] | None:
        element = re.search(rf'<rect [^>]*id="{element_id}"[^>]*>', svg)
        if element is None:
            return None
        a = {k: float(v) for k, v in BOX_ATTR.findall(element.group(0))}
        return a["x"], a["y"], a["x"] + a["width"], a["y"] + a["height"]
    blk, chute, c3_end = box("packer_block"), box("c3_chute"), box("c3_slot7")
    if None in (blk, chute, c3_end):
        return problems + ["packer block, chute or C3's last slot missing"]
    if not (blk[0] <= c3_end[0] and blk[1] > fv.BELT_Y + 56 and blk[2] <= fv.W and blk[3] <= fv.H):
        problems.append(f"packer block {blk} not under the end of C3 {c3_end} (below its lamps, inside the view)")
    if not (c3_end[0] <= chute[0] <= c3_end[2] + 10 and chute[1] <= fv.BELT_Y + 12 and abs(chute[3] - blk[1]) < 1):
        problems.append(f"chute {chute} does not run from C3's end down to the packer block")
    texts = [(float(x), float(y)) for x, y in
             re.findall(r'<text [^>]*x="([\d.]+)"[^>]*y="([\d.]+)"[^>]*>Palletizer</text>', svg)]
    if not texts or not (texts[0][0] < blk[0] and blk[1] <= texts[0][1] <= blk[3]):
        problems.append("palletizer stand-in not left of the packer block")
    if re.search(r'>Packer</text>\s*<text[^>]*>simulated<', svg):
        problems.append("the old grey packer stand-in is still drawn")
    return problems


def h7_syrup(view: dict) -> list[str]:
    """S4: the syrup room block (State colour, click -> its screen, state / step / buffer drawn
    click-through on top), the product pipe from the syrup PLC's output, the filler's input lamp."""
    items, svg, problems = view["items"], view["svgcontent"], []
    S = ft.SYRUP.tag_id
    block = prop(items, "syrup_block")
    colours = {(r["min"], r["max"]) for r in block.get("ranges", [])}
    if block.get("variableId") != S("State") or colours != {(v, v) for v in fv.STATE_COLOURS}:
        problems.append("syrup block not coloured by the syrup State")
    if goes_to(items, "syrup_block") != fv.SYRUP_MAIN_ID:
        problems.append("syrup block does not open the Syrup room view")
    on_top = []
    for prefix, tag, names in (("syrup_state", "State", STATE_NAMES), ("syrup_step_line", "Step", SYRUP_STEP_NAMES)):
        bad = [n for n in names if not shows_at(items, f"{prefix}_{n}", S(tag), n)]
        if bad:
            problems.append(f"syrup {tag} texts {bad[:3]}")
        on_top += [f"{prefix}_{n}" for n in names]
    if prop(items, "line_buffer").get("variableId") != S("BufferLevel"):
        problems.append("buffer value not on the syrup BufferLevel")
    on_top.append("line_buffer")
    for element_id in on_top:  # drawn over the block: must let the click through to it
        element = re.search(rf'<\w+ [^>]*id="{element_id}"[^>]*>', svg)
        if element is None or 'pointer-events="none"' not in element.group(0):
            problems.append(f"{element_id} catches the block's click")
            break
    for item_id in ("product_pipe", "no_product"):
        p = prop(items, item_id)
        tags = {p.get("variableId")} | {a["variableId"] for a in p.get("actions", [])}
        if tags - {None} != {S("Product_Available")}:
            problems.append(f"{item_id} not on the syrup PLC's Product_Available")
    if prop(items, "lamp_Product_Available").get("variableId") != ft.tag_id("Product_Available"):
        problems.append("filler input lamp not on the filler's Product_Available")
    # geometry: header < syrup block < pipe < filler block
    def box(element_id: str) -> tuple[float, float] | None:
        element = re.search(rf'<rect [^>]*id="{element_id}"[^>]*>', svg)
        if element is None:
            return None
        attrs = dict(BOX_ATTR.findall(element.group(0)))
        return float(attrs["y"]), float(attrs["y"]) + float(attrs["height"])
    syrup, pipe, filler = box("syrup_block"), box("product_pipe"), box("filler_block")
    if None in (syrup, pipe, filler) or not (fv.HEADER_H <= syrup[0] and syrup[1] <= pipe[0] and pipe[1] <= filler[0]):
        problems.append(f"layout syrup {syrup} pipe {pipe} filler {filler}")
    return problems


def h8_alarms() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    problems = []
    alarms = project.get("alarms", [])
    defs = {a["name"]: a for a in alarms}
    if len(defs) != len(alarms):
        problems.append("two alarm definitions with the same name")
    for ui in fv.UIS:  # every machine: one definition per alarm bit, group = the machine (column Machine)
        texts = fv.ALARM_TEXTS.get(ui.m.key, {})
        warnings = fv.SENSOR_WARNINGS.get(ui.m.key, ())  # FUXA-only warnings on sensor bits (labeler L5, packer P5)
        if set(texts) != set(ui.alarms) | set(warnings):
            problems.append(f"{ui.title}: alarm texts {sorted(texts)} != alarm bits + warnings")
        for bit in warnings:
            if texts.get(bit, ("",))[0] != "low" or ui.m.tag_id(bit) not in {ui.m.tag_id(n) for n, *_ in ui.m.tags}:
                problems.append(f"{ui.title} {bit}: a sensor warning must be priority low, on an existing tag")
        for bit in [*ui.alarms, *warnings]:
            a = defs.get(fv.alarm_name(bit, ui.m))
            if a is None or a["property"].get("variableId") != ui.m.tag_id(bit):
                problems.append(f"no {ui.title} alarm for {bit}")
                continue
            on = [p for p in ("highhigh", "high", "low", "info") if a[p]["enabled"]]
            lvl = a[on[0]] if len(on) == 1 else {}
            if (len(on) != 1 or (lvl["min"], lvl["max"]) != (1, 1) or lvl["checkdelay"] <= 0
                    or lvl["ackmode"] != "ackactive"):
                problems.append(f"{ui.title} {bit}: levels {on}")
            elif on[0] != texts.get(bit, ("",))[0] or lvl["group"] != ui.title or not lvl["text"]:
                problems.append(f"{ui.title} {bit}: priority {on[0]} / group {lvl['group']!r}")
    for view_id in fv.SHARED["alarms"].values():  # one Alarms view per machine (header row 2)
        view = next((v for v in project["hmi"]["views"] if v["id"] == view_id), None)
        if view is None:
            problems.append(f"view {view_id} missing")
            continue
        tables = [i for i in view["items"].values() if i["type"] == "svg-ext-own_ctrl-table"]
        active = [i for i in tables if i["property"]["type"] == "alarms"]
        options = active[0]["property"]["options"] if len(active) == 1 else {}
        if not {"ack", "group"} <= {c["id"] for c in options.get("alarmsColumns", [])}:
            problems.append(f"{view['name']}: no active alarms table with Ack and Machine columns")
        elif any(options.get("alarmFilter", {}).values()):  # one list for the whole line (report 8.1)
            problems.append(f"{view['name']}: alarm table filtered {options['alarmFilter']}")
        cmd = fv.CONTEXT[view_id].m.tag_id("Cmd")
        ev = view["items"].get("HXB_alarms_reset", {}).get("property", {}).get("events", [])
        if not ev or (ev[0]["actoptions"].get("variableId"), ev[0]["actparam"]) != (cmd, "3"):
            problems.append(f"{view['name']}: Reset button does not write {cmd} = 3")
    for v in project["hmi"]["views"]:
        if v["id"] not in fv.GENERATED:
            continue
        key, ui = fv.HEADER_KEY[v["id"]], fv.CONTEXT[v["id"]]
        for bit in ui.alarms:  # the chips of the machine in row 2 open that machine's Alarms view
            ev = v["items"].get(f"hdr_{key}_{bit}", {}).get("property", {}).get("events", [])
            if not ev or ev[0]["actparam"] != fv.shared_view("alarms", ui):
                problems.append(f"{v['name']}: chip {bit} does not open its Alarms view")
    menu = project["hmi"]["layout"].get("navigation", {}).get("items", [])
    if fv.ALARM_PANEL not in {m.get("view") for m in menu}:
        problems.append("side menu has no alarm panel entry")
    return check("H8 Alarms", not problems, f"{len(defs)} alarm definitions, side menu {len(menu)} items"
                 + (f"; {problems[:4]}" if problems else ""))


def h9_trends() -> bool:
    """Per machine: DAQ on exactly its trend tags, its charts bound to its device, its Trends view."""
    project = json.loads(ft.EXPORT.read_text())
    problems, logged_total = [], 0
    charts = {c["id"]: c for c in project.get("charts", [])}
    for ui in fv.UIS:
        m = ui.m
        tags = project["devices"].get(m.device_id, {}).get("tags", {})
        logged = {t["name"] for t in tags.values() if t["daq"]["enabled"]}
        logged_total += len(logged)
        if logged != set(m.trend_tags):
            problems.append(f"{m.key}: DAQ on for {sorted(logged)}, expected {sorted(m.trend_tags)}")
        if not all(tags[m.tag_id(n)]["daq"]["changed"] for n in m.trend_tags if m.tag_id(n) in tags):
            problems.append(f"{m.key}: a trend tag does not store every change")
        mine = fv.MACHINE_CHARTS[m.key]
        for chart_id, _title, _window, lines in mine:
            chart = charts.get(chart_id)
            if chart is None:
                problems.append(f"chart {chart_id} missing")
                continue
            if [ln["id"] for ln in chart["lines"]] != [m.tag_id(tag) for tag, *_ in lines]:
                problems.append(f"{chart_id}: lines {[ln['id'] for ln in chart['lines']]}")
            for ln in chart["lines"]:
                if ln["id"] not in tags or ln["device"] != m.name or ln["lineInterpolation"] != 1:
                    problems.append(f"{chart_id}: line {ln['id']} not bound / not stepAfter")
                elif not tags[ln["id"]]["daq"]["enabled"]:  # the chart loads its history from the DAQ
                    problems.append(f"{chart_id}: line {ln['id']} has no DAQ history")
        view_id = fv.shared_view("trends", ui)
        view = next((v for v in project["hmi"]["views"] if v["id"] == view_id), None)
        if view is None:
            problems.append(f"view {view_id} missing")
            continue
        widgets = [i for i in view["items"].values() if i["type"] == "svg-ext-html_chart"]
        shown = sorted(i["property"]["id"] for i in widgets)
        if shown != sorted(c[0] for c in mine):
            problems.append(f"{view['name']}: chart widgets show {shown}")
        for i in widgets:
            prop = i["property"]
            if prop["type"] != "realtime1" or not prop["options"].get("loadOldValues"):
                problems.append(f"{i['id']}: not realtime with history")
            if f'id="D-{i["id"]}"' not in view["svgcontent"]:
                problems.append(f"{i['id']}: no chart div")
        missing = [i for i in view["items"] if f'id="{i}"' not in view["svgcontent"]]
        if missing:
            problems.append(f"items without SVG element {missing[:3]}")
    menu = project["hmi"]["layout"].get("navigation", {}).get("items", [])
    if fv.TRENDS_ID not in {m.get("view") for m in menu}:
        problems.append("side menu has no Trends entry")
    return check("H9 Trends", not problems, f"DAQ on {logged_total} tags, {len(charts)} charts"
                 + (f"; {problems[:4]}" if problems else ""))


def fuxa_history(tag: str, minutes: int, m: ft.Machine = ft.FILLER) -> list[tuple[int, float]]:
    """(ms, value) pairs FUXA's DAQ stored for a tag in the last minutes."""
    now = int(time.time() * 1000)
    query = urllib.parse.quote(json.dumps({"sids": [m.tag_id(tag)], "from": now - minutes * 60_000, "to": now}))
    with urllib.request.urlopen(f"{ft.FUXA_URL}/api/daq?query={query}", timeout=10) as r:
        return [(d["dt"], float(d["value"])) for d in json.load(r)[0]]


def h10_daq() -> bool:
    problems, counts = [], {}
    for tag in ft.TREND_TAGS:
        counts[tag] = len(fuxa_history(tag, 10))
        if not counts[tag]:
            problems.append(f"no {tag} history in the last 10 min")
    fill, setpoint = fuxa_history("FillLevel", 10), fuxa_history("FillSetpoint", 10)
    top = max(v for _, v in setpoint) if setpoint else None
    over = [v for _, v in fill if top is not None and v > top + 20]
    if over:
        problems.append(f"{len(over)} fill values above setpoint {top:.0f} + 20 ml (max {max(over):.0f})")
    return check("H10 DAQ", not problems, f"points in 10 min: {counts}" + (f"; {problems[:4]}" if problems else ""))


def h10_daq_syrup() -> bool:
    """The syrup room's history: every trend tag has points from the last 10 min (once a minute
    even when idle), stored in degC / bar as shown (FUXA scales before it stores). Checked on the
    two setpoints, whose range is known: raw INTs would be 20-100 (0.1 degC) and 200-500 (0.01 bar)."""
    problems, counts = [], {}
    for tag in ft.SYRUP.trend_tags:
        counts[tag] = len(fuxa_history(tag, 10, ft.SYRUP))
        if not counts[tag]:
            problems.append(f"no {tag} history in the last 10 min")
    for tag, top in (("CoolSetpoint", 10), ("PressSetpoint", 5)):
        raw = [v for _, v in fuxa_history(tag, 10, ft.SYRUP) if v > top]
        if raw:
            problems.append(f"{tag} stored as {max(raw):g} (> {top}: not scaled)")
    return check("H10 DAQ syrup", not problems, f"points in 10 min: {counts}" + (f"; {problems[:4]}" if problems else ""))


def h10_daq_labeler() -> bool:
    """The labeler's history (HMI L6): every trend tag has points from the last 10 min, stored in
    degC as shown. Checked on GlueSetpoint (165-180 degC; the raw INT would be 1650-1800) and on
    GlueTemp, which never passes GlueMax 200 degC by much (raw: up to 2000)."""
    problems, counts = [], {}
    for tag in ft.LABELER.trend_tags:
        counts[tag] = len(fuxa_history(tag, 10, ft.LABELER))
        if not counts[tag]:
            problems.append(f"no {tag} history in the last 10 min")
    for tag, top in (("GlueSetpoint", 200), ("GlueTemp", 250)):
        raw = [v for _, v in fuxa_history(tag, 10, ft.LABELER) if v > top]
        if raw:
            problems.append(f"{tag} stored as {max(raw):g} (> {top}: not scaled)")
    return check("H10 DAQ labeler", not problems, f"points in 10 min: {counts}" + (f"; {problems[:4]}" if problems else ""))


def h10_daq_packer() -> bool:
    """The packer's history (HMI P6): every trend tag has points from the last 10 min, stored in
    degC as shown. Checked on OvenSetpoint (185-210 degC; the raw INT would be 1850-2100) and on
    OvenTemp, which stays below the heater's balance of about 253 degC (raw: up to 2530)."""
    problems, counts = [], {}
    for tag in ft.PACKER.trend_tags:
        counts[tag] = len(fuxa_history(tag, 10, ft.PACKER))
        if not counts[tag]:
            problems.append(f"no {tag} history in the last 10 min")
    for tag, top in (("OvenSetpoint", 230), ("OvenTemp", 300)):
        raw = [v for _, v in fuxa_history(tag, 10, ft.PACKER) if v > top]
        if raw:
            problems.append(f"{tag} stored as {max(raw):g} (> {top}: not scaled)")
    return check("H10 DAQ packer", not problems, f"points in 10 min: {counts}" + (f"; {problems[:4]}" if problems else ""))


def fuxa_values(ids: list[str]) -> dict[str, object]:
    query = urllib.parse.quote(json.dumps(ids))
    with urllib.request.urlopen(f"{ft.FUXA_URL}/api/getTagValue?ids={query}", timeout=3) as r:
        return {v["id"]: v.get("value") for v in json.load(r)}


def fuxa_set(tag: str, value: int) -> None:
    body = json.dumps({"tags": [{"id": tag, "value": value}]}).encode()
    request = urllib.request.Request(f"{ft.FUXA_URL}/api/setTagValue", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    # About 1-2 % of FUXA's Modbus writes time out (TransactionTimedOutError, answered with
    # HTTP 400; issue I16, measured with and without DAQ). Up to 3 tries, so H3 tests the write
    # path and not that flake, and the old setpoint is always written back.
    for attempt in range(3):
        try:
            urllib.request.urlopen(request, timeout=5).close()
            return
        except urllib.error.HTTPError:
            if attempt == 2:
                raise
            time.sleep(1)


def plc_value(plc: ModbusTcpClient, area: str, number: int) -> int:
    if area == ft.COIL:
        return int(plc.read_coils(number, count=1).bits[0])
    return plc.read_holding_registers(number, count=1).registers[0]


def as_int(value) -> int | None:
    """FUXA returns Bool tags as true/false (or 0/1) and numbers possibly as text."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        if value.lower() in ("true", "false"):
            return int(value.lower() == "true")
        return int(float(value))
    return int(value)


def as_plc(value, factor: int) -> int | None:
    """FUXA's value back in PLC units: a scaled tag (degC, bar) times its factor, rounded."""
    if factor == 1 or value is None or value == "":
        return as_int(value)
    return round(float(value) * factor)


def to_signed(word: int) -> int:
    return word - 0x10000 if word >= 0x8000 else word


def h2_read(plc: ModbusTcpClient, m: ft.Machine = ft.FILLER) -> bool:
    ids = [m.tag_id(name) for name, *_ in m.tags]
    bad = {}
    for name, area, number, _kind, _ in m.tags:
        for _ in range(3):
            fuxa = as_plc(fuxa_values([m.tag_id(name)])[m.tag_id(name)], m.factor(name))
            plc_v = plc_value(plc, area, number)
            plc_v = to_signed(plc_v) if area == ft.HOLDING else plc_v
            if fuxa == plc_v:
                bad.pop(name, None)
                break
            bad[name] = f"FUXA {fuxa} / PLC {plc_v}"
            time.sleep(0.3)
    return check(f"H2 read path {m.key}", not bad, f"{len(ids) - len(bad)}/{len(ids)} tags equal"
                 + (f"; {dict(list(bad.items())[:5])}" if bad else ""))


def h3_write(plc: ModbusTcpClient) -> bool:
    old = plc_value(plc, ft.HOLDING, am.FILL_SETPOINT)
    probe = 480 if old != 480 else 470
    fuxa_set(ft.tag_id("FillSetpoint"), probe)
    time.sleep(0.5)
    seen = plc_value(plc, ft.HOLDING, am.FILL_SETPOINT)
    fuxa_set(ft.tag_id("FillSetpoint"), old)
    time.sleep(0.5)
    back = plc_value(plc, ft.HOLDING, am.FILL_SETPOINT)
    return check("H3 write path", seen == probe and back == old,
                 f"FillSetpoint via FUXA {probe} -> PLC {seen}, restored {back} (was {old})")


def h4_connected(m: ft.Machine = ft.FILLER) -> bool:
    # right after an import FUXA reconnects the device: the status is 0 for a few seconds
    for _ in range(20):
        value = as_int(fuxa_values([m.status_tag_id])[m.status_tag_id])
        if value == CONNECTED:
            break
        time.sleep(0.5)
    return check(f"H4 connection {m.key}", value == CONNECTED, f"status tag {value} (5 = connected)")


def h3_syrup(plc: ModbusTcpClient) -> bool:
    """A scaled setpoint written through FUXA arrives rounded: PressHyst 0.29 bar -> 29 (FUXA's
    Modbus divisor would write 28, fuxa_tags.scale). The old value is written back."""
    old = plc_value(plc, ft.HOLDING, sm.PRESS_HYST)
    fuxa_set(ft.SYRUP.tag_id("PressHyst"), 0.29)
    time.sleep(0.5)
    seen = plc_value(plc, ft.HOLDING, sm.PRESS_HYST)
    fuxa_set(ft.SYRUP.tag_id("PressHyst"), old / 100)
    time.sleep(0.5)
    back = plc_value(plc, ft.HOLDING, sm.PRESS_HYST)
    return check("H3 write path syrup", seen == 29 and back == old,
                 f"PressHyst via FUXA 0.29 bar -> PLC {seen} (29 expected), restored {back} (was {old})")


def syrup_live() -> list[bool]:
    """H4 + H2 + H3 + H10 for the syrup room PLC (port 5022). H3 writes one setpoint FUXA owns and restores it."""
    plc = ModbusTcpClient(config.PLC_HOST, port=config.SYRUP_PORT, timeout=2)
    try:
        if not plc.connect():
            return [check("H2 read path syrup", False, f"no connection to {config.PLC_HOST}:{config.SYRUP_PORT}")]
        plc.read_holding_registers(sm.STATE, count=1)
        return [h4_connected(ft.SYRUP), h2_read(plc, ft.SYRUP), h3_syrup(plc), h10_daq_syrup()]
    except (ModbusException, OSError):
        return [check("H2 read path syrup", False, "the syrup PLC does not answer on Modbus (upload the program)")]
    finally:
        plc.close()


def h3_labeler(plc: ModbusTcpClient) -> bool:
    """A scaled labeler setpoint written through FUXA: GlueHyst 3.5 degC -> PLC 35; the old value is written back."""
    old = plc_value(plc, ft.HOLDING, lm.GLUE_HYST)
    probe = 3.5 if old != 35 else 4.5
    fuxa_set(ft.LABELER.tag_id("GlueHyst"), probe)
    time.sleep(0.5)
    seen = plc_value(plc, ft.HOLDING, lm.GLUE_HYST)
    fuxa_set(ft.LABELER.tag_id("GlueHyst"), old / 10)
    time.sleep(0.5)
    back = plc_value(plc, ft.HOLDING, lm.GLUE_HYST)
    return check("H3 write path labeler", seen == round(probe * 10) and back == old,
                 f"GlueHyst via FUXA {probe} degC -> PLC {seen} ({round(probe * 10)} expected), restored {back} (was {old})")


def labeler_live() -> list[bool]:
    """H4 + H2 + H3 + H10 for the labeler PLC (port 5023, HMI L1 / L6). H3 writes one setpoint FUXA owns and restores it."""
    plc = ModbusTcpClient(config.PLC_HOST, port=config.LABELER_PORT, timeout=2)
    try:
        if not plc.connect():
            return [check("H2 read path labeler", False, f"no connection to {config.PLC_HOST}:{config.LABELER_PORT}")]
        plc.read_holding_registers(lm.STATE, count=1)
        return [h4_connected(ft.LABELER), h2_read(plc, ft.LABELER), h3_labeler(plc), h10_daq_labeler()]
    except (ModbusException, OSError):
        return [check("H2 read path labeler", False, "the labeler PLC does not answer on Modbus (upload the program)")]
    finally:
        plc.close()


def h3_packer(plc: ModbusTcpClient) -> bool:
    """A scaled packer setpoint written through FUXA: OvenHyst 4.5 degC -> PLC 45; the old value is written back."""
    old = plc_value(plc, ft.HOLDING, pm.OVEN_HYST)
    probe = 4.5 if old != 45 else 3.5
    fuxa_set(ft.PACKER.tag_id("OvenHyst"), probe)
    time.sleep(0.5)
    seen = plc_value(plc, ft.HOLDING, pm.OVEN_HYST)
    fuxa_set(ft.PACKER.tag_id("OvenHyst"), old / 10)
    time.sleep(0.5)
    back = plc_value(plc, ft.HOLDING, pm.OVEN_HYST)
    return check("H3 write path packer", seen == round(probe * 10) and back == old,
                 f"OvenHyst via FUXA {probe} degC -> PLC {seen} ({round(probe * 10)} expected), restored {back} (was {old})")


def packer_live() -> list[bool]:
    """H4 + H2 + H3 + H10 for the packer PLC (port 5024, HMI P1 / P6). H3 writes one setpoint FUXA owns and restores it."""
    plc = ModbusTcpClient(config.PLC_HOST, port=config.PACKER_PORT, timeout=2)
    try:
        if not plc.connect():
            return [check("H2 read path packer", False, f"no connection to {config.PLC_HOST}:{config.PACKER_PORT}")]
        plc.read_holding_registers(pm.STATE, count=1)
        return [h4_connected(ft.PACKER), h2_read(plc, ft.PACKER), h3_packer(plc), h10_daq_packer()]
    except (ModbusException, OSError):
        return [check("H2 read path packer", False, "the packer PLC does not answer on Modbus (upload the program)")]
    finally:
        plc.close()


def filler_live() -> list[bool]:
    """H4, H2, H3, H10 for the filler PLC (port 502). A filler that does not answer is one FAIL, so
    the syrup room's checks still run."""
    plc = ModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT, timeout=2)
    try:
        if not plc.connect():
            return [check("H2 read path filler", False, f"no connection to {config.PLC_HOST}:{config.PLC_PORT}")]
        plc.read_holding_registers(am.STATE, count=1)  # the runtime accepts TCP even when the program is stopped
        return [h4_connected(), h2_read(plc), h3_write(plc), h10_daq()]
    except (ModbusException, OSError):
        return [check("H2 read path filler", False, "the filler PLC does not answer on Modbus (upload the program)")]
    finally:
        plc.close()


def main() -> None:
    results = [*h1_export(), h5_header(), h6_filler(), h7_line(), h8_alarms(), h9_trends(), h11_syrup(),
               h12_labeler(), h13_packer()]
    if "--offline" not in sys.argv:
        results += filler_live() + syrup_live() + labeler_live() + packer_live()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
