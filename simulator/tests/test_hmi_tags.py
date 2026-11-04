"""FUXA stage 1: the FUXA tags of the filler PLC show the right PLC addresses.

    uv run python tests/test_hmi_tags.py --offline   # H1 only: the export file, no PLC, no FUXA
    uv run python tests/test_hmi_tags.py             # H1-H10: FUXA and the PLC must be running

Checks:
    H1 export: every address_map signal has exactly one tag in docs/hmi/fuxa-project.json,
       with FUXA address = Modbus number + 1, the right area and type (catches an edit in
       the editor that moved a tag)
    H2 read path: for every tag, the value FUXA reads (GET /api/getTagValue) equals the
       value read directly from the PLC over Modbus
    H3 write path: FillSetpoint written THROUGH FUXA (POST /api/setTagValue) arrives in
       the PLC; the old value is written back afterwards
    H4 FUXA reports the PLC device as connected (connection status tag = 5)
    H5 header (export, stage 2): on every generated view; Start/Stop/Reset write Cmd 1/2/3,
       Auto/Manual/Stop write ModeReq 1/2/0; one show-text per state, step and mode value;
       a chip per alarm bit; the navigation buttons open existing views; every button has
       at most one event per kind (several writes per click dropped FUXA's PLC link)
    H6 Filler screen (export, stage 3): a hold-to-run button per Man_* (1 on press, 0 on
       release and on mouseout, visible only in Manual); setpoint inputs with the right
       tag and min < max; a lamp per sensor/output; pocket + state words per Anim_* tag;
       fill level and setpoint bars
    H7 Line overview (export, stage 4): 8 slot shapes per conveyor, each bound to its own bit
       (bitmask 1, 2, 4 ... 128) of Anim_C1 / Anim_C2; C1 and index rollers turn with
       ConveyorMotor1 / IndexMotor, C2 rollers static; the filler block opens the Filler view;
       product pipe on Product_Available
    H8 Alarms (export, stage 5): one FUXA alarm per PLC alarm bit with exactly one priority
       enabled, active at value 1, checked (checkdelay > 0), ackmode ackactive; the Alarms view
       has an active-alarms table with an Ack column and a Reset button (Cmd = 3); every alarm
       chip opens the Alarms view; the side menu opens FUXA's alarm panel (history)
    H9 Trends (export, stage 6): FUXA's DAQ (history) is on for exactly the trend tags (every
       change); every chart line is bound to an existing tag of the device, stepAfter; the
       Trends view has one realtime chart widget per chart (loading the history when it
       opens), and the header and side menu open it
    H10 DAQ (live, stage 6): FUXA's history (GET /api/daq) has values of every trend tag from
       the last 10 min (also when idle: one value a minute at least), and no stored fill level
       is above the fill setpoint + 20 ml

Run H2 with the simulator idle (no bottles moving), so the values do not change between
the two reads; each tag still gets 3 tries. H3 writes only FillSetpoint (FUXA owns it);
Cmd and Man_* are never written here, they would start or move the machine.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from simulator import address_map as am
from simulator import config

import fuxa_tags as ft
import fuxa_views as fv
from simulator.plcs import ALARM_NAMES, MODE_NAMES, STATE_NAMES, STEP_NAMES

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


def h1_export() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    dev = project["devices"].get(ft.DEVICE_ID)
    if dev is None:
        return check("H1 export", False, f"no device {ft.DEVICE_ID} in {ft.EXPORT.name}")
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
    for name, area, number, kind, _ in ft.TAGS:
        t = dev["tags"].get(ft.tag_id(name))
        if t is None:
            problems.append(f"{name} missing")
        elif (t["memaddress"], t["address"], t["type"]) != (area, ft.fuxa_address(number), kind):
            problems.append(f"{name} is {t['memaddress']}/{t['address']}/{t['type']}, "
                            f"expected {area}/{ft.fuxa_address(number)}/{kind}")
    missing = expected_addresses() - set(have)
    if missing:
        problems.append(f"address_map signals without a tag: {sorted(missing)}")
    status = project["devices"]["0"].get("tags", {}).get(ft.STATUS_TAG_ID)
    if not status or status.get("memaddress") != ft.DEVICE_ID:
        problems.append("connection status tag missing")
    # test page: one Value shape per tag, present in the SVG and bound to that tag
    page = next((v for v in project["hmi"]["views"] if v["id"] == fv.TAGS_ID), None)
    if page is None:
        problems.append("view Tags missing")
    else:
        for name, *_ in ft.TAGS:
            item = page["items"].get(fv.value_id(name))
            if item is None or item["property"].get("variableId") != ft.tag_id(name):
                problems.append(f"Tags page: {name} not bound")
            elif f'id="{fv.value_id(name)}"' not in page["svgcontent"]:
                problems.append(f"Tags page: {name} not drawn")
    return check("H1 export", not problems,
                 f"{len(tags)} tags, polling {dev['polling']} ms, {dev['property']['address']}"
                 + (f"; {problems[:4]}" if problems else ""))


def h5_header() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    views = {v["id"]: v for v in project["hmi"]["views"]}
    problems = []
    for view_id in fv.GENERATED:
        v = views.get(view_id)
        if v is None:
            problems.append(f"view {view_id} missing")
            continue
        items = v["items"]
        key = fv.HEADER_KEY[view_id]
        p = f"HXB_hdr_{key}"

        def writes(button_id: str) -> tuple | None:
            ev = items.get(button_id, {}).get("property", {}).get("events", [])
            return (ev[0]["actoptions"].get("variableId"), int(ev[0]["actparam"])) if ev else None

        for label, number in fv.CMD_BUTTONS:
            if writes(f"{p}_cmd_{label.lower()}") != (ft.tag_id("Cmd"), number):
                problems.append(f"{v['name']}: {label} does not write Cmd={number}")
        for label, number in fv.MODE_BUTTONS:
            if writes(f"{p}_mode_{label.lower()}") != (ft.tag_id("ModeReq"), number):
                problems.append(f"{v['name']}: mode {label} does not write ModeReq={number}")
        # FUXA sends all events of one click at once; several writes per click dropped the
        # PLC link (stage 3). Every header button must do exactly one thing.
        many = []
        for i in items.values():
            kinds = [e["type"] for e in i.get("property", {}).get("events", [])]
            if len(kinds) != len(set(kinds)):
                many.append(i["name"])
        if many:
            problems.append(f"{v['name']}: shapes with two events of the same kind {many[:3]}")
        for prefix, names in (("state_name", STATE_NAMES), ("step_name", STEP_NAMES), ("mode_name", MODE_NAMES)):
            for number in names:
                item = items.get(f"hdr_{key}_{prefix}_{number}")
                shows = [a for a in (item or {}).get("property", {}).get("actions", []) if a["type"] == "show"]
                if not shows or shows[0]["range"] != {"min": number, "max": number}:
                    problems.append(f"{v['name']}: no {prefix} text for {number}")
        for name in ALARM_NAMES:
            if items.get(f"hdr_{key}_{name}", {}).get("property", {}).get("variableId") != ft.tag_id(name):
                problems.append(f"{v['name']}: no chip for {name}")
        for target, name in fv.NAV:
            ev = items.get(f"{p}_nav_{name.lower()}", {}).get("property", {}).get("events", [])
            if not ev or ev[0]["actparam"] not in views:
                problems.append(f"{v['name']}: nav {name} broken")
        missing = [i for i in items if f'id="{i}"' not in v["svgcontent"]]
        if missing:
            problems.append(f"{v['name']}: items without SVG element {missing[:3]}")
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


def h7_line() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.LINE_ID), None)
    if view is None:
        return check("H7 Line overview", False, "view missing")
    items, problems = view["items"], []
    for key, anim in (("c1", "Anim_C1"), ("c2", "Anim_C2")):
        for i in range(8):
            prop = items.get(f"{key}_slot{i}", {}).get("property", {})
            if prop.get("variableId") != ft.tag_id(anim) or prop.get("bitmask") != 1 << i:
                problems.append(f"{key} slot {i} not on bit {i} of {anim}")
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
        if turns_with(f"c2_roller{k}") is not None:
            problems.append(f"C2 roller {k} should be static")
    for k in range(2):
        if turns_with(f"filler_roller{k}") != ft.tag_id("IndexMotor"):
            problems.append(f"index roller {k}")
    ev = items.get("filler_block", {}).get("property", {}).get("events", [])
    if not ev or ev[0]["action"] != "onpage" or ev[0]["actparam"] != fv.MAIN_ID:
        problems.append("filler block does not open the Filler view")
    if items.get("product_pipe", {}).get("property", {}).get("variableId") != ft.tag_id("Product_Available"):
        problems.append("product pipe")
    if project["hmi"]["layout"].get("start") != fv.LINE_ID:
        problems.append("start page is not Line")
    missing = [i for i in items if f'id="{i}"' not in view["svgcontent"]]
    if missing:
        problems.append(f"items without SVG element {missing[:3]}")
    return check("H7 Line overview", not problems, f"{len(items)} items" + (f"; {problems[:4]}" if problems else ""))


def h8_alarms() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    problems = []
    defs = {a["name"]: a for a in project.get("alarms", [])}
    for bit in ALARM_NAMES:
        a = defs.get(fv.alarm_name(bit))
        if a is None or a["property"].get("variableId") != ft.tag_id(bit):
            problems.append(f"no alarm for {bit}")
            continue
        on = [p for p in ("highhigh", "high", "low", "info") if a[p]["enabled"]]
        lvl = a[on[0]] if len(on) == 1 else {}
        if len(on) != 1 or (lvl["min"], lvl["max"]) != (1, 1) or lvl["checkdelay"] <= 0 or lvl["ackmode"] != "ackactive":
            problems.append(f"{bit}: levels {on}")
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.ALARMS_ID), None)
    if view is None:
        problems.append("Alarms view missing")
    else:
        tables = [i for i in view["items"].values() if i["type"] == "svg-ext-own_ctrl-table"]
        active = [i for i in tables if i["property"]["type"] == "alarms"]
        if len(active) != 1 or "ack" not in {c["id"] for c in active[0]["property"]["options"]["alarmsColumns"]}:
            problems.append("no active alarms table with Ack column")
        ev = view["items"].get("HXB_alarms_reset", {}).get("property", {}).get("events", [])
        if not ev or (ev[0]["actoptions"].get("variableId"), ev[0]["actparam"]) != (ft.tag_id("Cmd"), "3"):
            problems.append("Reset button does not write Cmd = 3")
    for v in project["hmi"]["views"]:
        if v["id"] not in fv.GENERATED:
            continue
        key = fv.HEADER_KEY[v["id"]]
        for bit in ALARM_NAMES:
            ev = v["items"].get(f"hdr_{key}_{bit}", {}).get("property", {}).get("events", [])
            if not ev or ev[0]["actparam"] != fv.ALARMS_ID:
                problems.append(f"{v['name']}: chip {bit} does not open Alarms")
    menu = project["hmi"]["layout"].get("navigation", {}).get("items", [])
    if fv.ALARM_PANEL not in {m.get("view") for m in menu}:
        problems.append("side menu has no alarm panel entry")
    return check("H8 Alarms", not problems, f"{len(defs)} alarm definitions, side menu {len(menu)} items"
                 + (f"; {problems[:4]}" if problems else ""))


def h9_trends() -> bool:
    project = json.loads(ft.EXPORT.read_text())
    problems = []
    tags = project["devices"][ft.DEVICE_ID]["tags"]
    logged = {t["name"] for t in tags.values() if t["daq"]["enabled"]}
    if logged != set(ft.TREND_TAGS):
        problems.append(f"DAQ on for {sorted(logged)}, expected {sorted(ft.TREND_TAGS)}")
    if not all(tags[ft.tag_id(n)]["daq"]["changed"] for n in ft.TREND_TAGS if ft.tag_id(n) in tags):
        problems.append("a trend tag does not store every change")
    charts = {c["id"]: c for c in project.get("charts", [])}
    for chart_id, _title, _window, lines in fv.CHARTS:
        chart = charts.get(chart_id)
        if chart is None:
            problems.append(f"chart {chart_id} missing")
            continue
        if [ln["id"] for ln in chart["lines"]] != [ft.tag_id(tag) for tag, *_ in lines]:
            problems.append(f"{chart_id}: lines {[ln['id'] for ln in chart['lines']]}")
        for ln in chart["lines"]:
            if ln["id"] not in tags or ln["device"] != ft.DEVICE_NAME or ln["lineInterpolation"] != 1:
                problems.append(f"{chart_id}: line {ln['id']} not bound / not stepAfter")
    view = next((v for v in project["hmi"]["views"] if v["id"] == fv.TRENDS_ID), None)
    if view is None:
        problems.append("Trends view missing")
    else:
        widgets = [i for i in view["items"].values() if i["type"] == "svg-ext-html_chart"]
        shown = sorted(i["property"]["id"] for i in widgets)
        if shown != sorted(c[0] for c in fv.CHARTS):
            problems.append(f"chart widgets show {shown}")
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
    return check("H9 Trends", not problems, f"DAQ on {len(logged)} tags, {len(charts)} charts"
                 + (f"; {problems[:4]}" if problems else ""))


def fuxa_history(tag: str, minutes: int) -> list[tuple[int, float]]:
    """(ms, value) pairs FUXA's DAQ stored for a tag in the last minutes."""
    now = int(time.time() * 1000)
    query = urllib.parse.quote(json.dumps({"sids": [ft.tag_id(tag)], "from": now - minutes * 60_000, "to": now}))
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


def to_signed(word: int) -> int:
    return word - 0x10000 if word >= 0x8000 else word


def h2_read(plc: ModbusTcpClient) -> bool:
    ids = [ft.tag_id(name) for name, *_ in ft.TAGS]
    bad = {}
    for name, area, number, _kind, _ in ft.TAGS:
        for _ in range(3):
            fuxa = as_int(fuxa_values([ft.tag_id(name)])[ft.tag_id(name)])
            plc_v = plc_value(plc, area, number)
            plc_v = to_signed(plc_v) if area == ft.HOLDING else plc_v
            if fuxa == plc_v:
                bad.pop(name, None)
                break
            bad[name] = f"FUXA {fuxa} / PLC {plc_v}"
            time.sleep(0.3)
    return check("H2 read path", not bad, f"{len(ids) - len(bad)}/{len(ids)} tags equal"
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


def h4_connected() -> bool:
    # right after an import FUXA reconnects the device: the status is 0 for a few seconds
    for _ in range(20):
        value = as_int(fuxa_values([ft.STATUS_TAG_ID])[ft.STATUS_TAG_ID])
        if value == CONNECTED:
            break
        time.sleep(0.5)
    return check("H4 connection", value == CONNECTED, f"status tag {value} (5 = connected)")


def main() -> None:
    results = [h1_export(), h5_header(), h6_filler(), h7_line(), h8_alarms(), h9_trends()]
    if "--offline" not in sys.argv:
        plc = ModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT, timeout=2)
        if not plc.connect():
            raise SystemExit(f"no connection to the PLC at {config.PLC_HOST}:{config.PLC_PORT} (is the program started?)")
        try:
            plc.read_holding_registers(am.STATE, count=1)  # the runtime accepts TCP even when the program is stopped
        except (ModbusException, OSError):
            plc.close()
            raise SystemExit("the PLC does not answer on Modbus: start the program in the OpenPLC Editor (port 8443)")
        try:
            results += [h4_connected(), h2_read(plc), h3_write(plc), h10_daq()]
        finally:
            plc.close()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
