"""The filler's FUXA views: header (on every view), Filler main view, Tags test page.

Built from fuxa_shapes.py (the FUXA shape formats) and fuxa_tags.py (the tag
table). Run through `uv run python tests/fuxa_tags.py`, which writes the export
that is then imported into FUXA.

Header (report 8.1/8.2, roadmap stage 2), 1280 x 96 at the top of every view:
  row 1: machine name | State block (colour + name) | Step | Mode | mode hint | E-stop | PLC link
  row 2: Start Stop Reset | Auto Manual Stop (active mode highlighted) | 4 alarm chips | navigation
Colours (ISA-101): grey is normal, colour only for states and problems. State colours
follow report 8.2. The header only WRITES Cmd (1 Start, 2 Stop, 3 Reset; the PLC sets
it back to 0) and ModeReq (0 Stop, 1 Auto, 2 Manual).
"""

from simulator import address_map as am
from simulator import config
from simulator.plcs import ALARM_NAMES, MODE_NAMES, STATE_NAMES, STEP_NAMES

import fuxa_shapes as fs
import fuxa_tags as ft

W, H = 1280, 720
HEADER_H = 96
LINE_ID, LINE_NAME = "v_filler_line", "Line"
MAIN_ID, MAIN_NAME = "v_filler_main", "Filler"
ALARMS_ID, ALARMS_NAME = "v_filler_alarms", "Alarms"
TRENDS_ID, TRENDS_NAME = "v_filler_trends", "Trends"
TAGS_ID, TAGS_NAME = "v_filler_tags", "Tags"
GENERATED = (LINE_ID, MAIN_ID, ALARMS_ID, TRENDS_ID, TAGS_ID)
NAV = ((LINE_ID, LINE_NAME), (MAIN_ID, MAIN_NAME), (ALARMS_ID, ALARMS_NAME), (TRENDS_ID, TRENDS_NAME),
       (TAGS_ID, TAGS_NAME))
HEADER_KEY = {LINE_ID: "line", MAIN_ID: "main", ALARMS_ID: "alarms", TRENDS_ID: "trends",
              TAGS_ID: "tags"}  # header id prefix per view
ALARM_PANEL = "[alarms]"  # side-menu target that opens FUXA's own alarm panel (active + history)

# colours
HEADER_BG = "#d4d4d4"
NEUTRAL = "#c4c4c4"  # chip / button at rest
TEXT = "#1d1d1d"
MUTED = "#555555"
RED, GREEN, YELLOW, ORANGE, GREY = "#d64545", "#3fa34d", "#e6c229", "#e8812f", "#9e9e9e"
ACTIVE = "#1f4e9a"  # selected mode / current view
STATE_COLOURS = {0: GREY, 1: GREEN, 2: YELLOW, 3: ORANGE, 9: RED}  # report 8.2
ALARM_LABELS = {"Alm_IndexJam": "Index jam", "Alm_FillTimeout": "Fill timeout",
                "Alm_OutfeedBlocked": "Outfeed blocked", "Alm_CommLost": "Comm lost"}
LINK_OK = 5  # FUXA connection status: 0 off, 3 warning, 5 connected

CMD_BUTTONS = (("Start", 1), ("Stop", 2), ("Reset", 3))
MANUAL = 2  # ModeReq value of Manual
# hold-to-run buttons: (label, Man_* tag)
MANUAL_BUTTONS = (("C1", "Man_C1"), ("Door", "Man_Door"), ("Rinse", "Man_Rinse"),
                  ("Fill", "Man_Fill"), ("Cap", "Man_Cap"), ("Index", "Man_Index"))
MODE_BUTTONS = (("Auto", 1), ("Manual", 2), ("Stop", 0))

T = ft.tag_id  # tag name -> FUXA tag id


class Builder:
    """Collects SVG parts and items of one view."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.items: dict = {}

    def add(self, shape: fs.Shape | str) -> None:
        if isinstance(shape, str):
            self.parts.append(shape)
        else:
            self.parts.append(shape[0])
            self.items |= shape[1]


def header(b: Builder, key: str, current: str) -> None:
    """Draw the header into builder b. key makes the ids unique per view."""
    p = f"hdr_{key}"
    b.add(fs.rect(0, 0, W, HEADER_H, HEADER_BG, rx=0))
    b.add(fs.text(16, 36, "Filler", size=20, bold=True))

    # State block: colour + name (report 8.2)
    b.add(fs.rect_ranges(f"{p}_state", T("State"), 90, 8, 150, 40,
                         [fs.colour_range(v, v, c) for v, c in STATE_COLOURS.items()], default=GREY))
    b.add(fs.enum_texts(f"{p}_state_name", T("State"), STATE_NAMES, 165, 34, size=17, bold=True, anchor="middle"))

    # Step name, with the raw number for steps the table does not know
    b.add(fs.text(256, 22, "Step", size=11, color=MUTED))
    b.add(fs.enum_texts(f"{p}_step_name", T("Step"), STEP_NAMES, 256, 42, size=16, bold=True))
    b.add(fs.value(f"{p}_step_no", T("Step"), 396, 42, size=12, anchor="start", color=MUTED, bold=False))

    # Mode name + hint when Start would be ignored (issue I10)
    b.add(fs.text(436, 22, "Mode", size=11, color=MUTED))
    b.add(fs.enum_texts(f"{p}_mode_name", T("ModeReq"), MODE_NAMES, 436, 42, size=16, bold=True))
    b.add(fs.rect_ranges(f"{p}_hint_bg", T("ModeReq"), 524, 10, 330, 36,
                         [fs.colour_range(0, 0, "#f2d14b"), fs.colour_range(1, 1, HEADER_BG),
                          fs.colour_range(2, 2, NEUTRAL)], default=HEADER_BG))
    b.add(fs.conditional_text(f"{p}_hint_stop", T("ModeReq"), 0, 0, 689, 33,
                              "Stop mode: Start is ignored – press Auto", size=13, bold=True,
                              color="#7a0000", anchor="middle"))
    b.add(fs.conditional_text(f"{p}_hint_manual", T("ModeReq"), 2, 2, 689, 33,
                              "Manual: sequencer paused", size=13, bold=True, anchor="middle"))

    # E-stop chip
    b.add(fs.rect_ranges(f"{p}_estop", T("E_Stop_OK"), 868, 10, 180, 36,
                         [fs.colour_range(0, 0, RED), fs.colour_range(1, 1, NEUTRAL)], default=NEUTRAL))
    b.add(fs.conditional_text(f"{p}_estop_on", T("E_Stop_OK"), 0, 0, 958, 33, "E-STOP PRESSED",
                              size=14, bold=True, color="#ffffff", anchor="middle"))
    b.add(fs.conditional_text(f"{p}_estop_ok", T("E_Stop_OK"), 1, 1, 958, 33, "E-stop OK",
                              size=14, anchor="middle"))

    # PLC link chip (FUXA's own connection status of the device)
    status = ft.STATUS_TAG_ID
    b.add(fs.rect_ranges(f"{p}_link", status, 1060, 10, 204, 36,
                         [fs.colour_range(0, 0, RED), fs.colour_range(1, LINK_OK - 1, YELLOW),
                          fs.colour_range(LINK_OK, LINK_OK, NEUTRAL)], default=NEUTRAL))
    b.add(fs.conditional_text(f"{p}_link_lost", status, 0, 0, 1162, 33, "PLC link LOST",
                              size=14, bold=True, color="#ffffff", anchor="middle"))
    b.add(fs.conditional_text(f"{p}_link_slow", status, 1, LINK_OK - 1, 1162, 33, "PLC link slow",
                              size=14, bold=True, anchor="middle"))
    b.add(fs.conditional_text(f"{p}_link_ok", status, LINK_OK, LINK_OK, 1162, 33, "PLC link OK",
                              size=14, anchor="middle"))

    # Row 2: commands (Cmd) and mode selector (ModeReq)
    for i, (label, number) in enumerate(CMD_BUTTONS):
        b.add(fs.button(f"{p}_cmd_{label.lower()}", label, 16 + i * 84, 54, 80, 32,
                        [fs.set_value(T("Cmd"), number)], background=NEUTRAL))
    # One write per click: FUXA 1.3.5 sends all events of a click at the same time, and 7 writes
    # at once (ModeReq + releasing the six Man_*) timed out and dropped the PLC link (stage 3).
    for i, (label, number) in enumerate(MODE_BUTTONS):
        others = [fs.colour_range(v, v, NEUTRAL, TEXT) for v in MODE_NAMES if v != number]
        b.add(fs.button(f"{p}_mode_{label.lower()}", label, 280 + i * 84, 54, 80, 32,
                        [fs.set_value(T("ModeReq"), number)], background=NEUTRAL,
                        tag_id=T("ModeReq"), ranges=[fs.colour_range(number, number, ACTIVE, "#ffffff"), *others]))

    # Alarm chips (the alarm list itself is stage 5)
    for i, name in enumerate(ALARM_NAMES):
        x = 540 + i * 100
        b.add(fs.rect_ranges(f"{p}_{name}", T(name), x, 54, 96, 32,
                             [fs.colour_range(0, 0, NEUTRAL), fs.colour_range(1, 1, RED)], default=NEUTRAL,
                             events=[fs.goto(ALARMS_ID)]))  # click a chip -> alarm list
        b.add(fs.text(x + 48, 75, ALARM_LABELS[name], size=12, anchor="middle", click_through=True))

    # Navigation
    for i, (view_id, name) in enumerate(NAV):
        here = view_id == current
        b.add(fs.button(f"{p}_nav_{name.lower()}", name, 948 + i * 63, 54, 60, 32, [fs.goto(view_id)],
                        background=ACTIVE if here else NEUTRAL, color="#ffffff" if here else TEXT))


# --- Filler detail screen (stage 3) ----------------------------------------------
BG = "#e6e6e6"  # page background
LAMP_OFF, LAMP_ON = "#7a7a7a", "#ffffff"  # ISA-101: no colour for normal equipment states
POCKET_COLOURS = {  # state code -> (body fill, outline); the simulator writes the codes
    am.POCKET_EMPTY: (BG, "#9e9e9e"),
    am.POCKET_BOTTLE: ("#ffffff", "#555555"),
    am.POCKET_RINSED: ("#cfe3f2", "#555555"),
    am.POCKET_FILLED: ("#7b4a22", "#555555"),
    am.POCKET_CAPPED: ("#7b4a22", "#555555"),
}
POCKET_WORDS = {am.POCKET_EMPTY: "empty", am.POCKET_BOTTLE: "bottle", am.POCKET_RINSED: "rinsed",
                am.POCKET_FILLED: "filled", am.POCKET_CAPPED: "capped"}
POCKETS = (("Entry", "Anim_Entry", (("Entry_Load", "loaded"),)),
           ("Rinse", "Anim_Rinse", (("S3", "S3 bottle"), ("RinseValve", "valve"))),
           ("Fill", "Anim_Fill", (("S4", "S4 bottle"), ("FillValve", "valve"))),
           ("Cap", "Anim_Cap", (("S5", "S5 bottle"), ("CapMotor", "cap head"))))
FILL_SCALE_ML = 600  # fill bars 0..600 ml (the setpoint limit below keeps the setpoint inside)
# setpoint inputs (roadmap issue I9: the PLC does not limit them, FUXA refuses values outside):
# fill <= 600 ml = 6 s at the simulator's flow, well below the 10 s fill timeout;
# rinse / cap not below the time a bottle physically needs (simulator config), at most 5 s.
SETPOINTS = (
    ("Fill setpoint", "FillSetpoint", "ml", 100, FILL_SCALE_ML),
    ("Rinse time", "RinseTime", "ms", round(config.RINSE_MIN_S * 1000), 5000),
    ("Cap time", "CapTime", "ms", round(config.CAP_MIN_S * 1000), 5000),
)
LAMP_RANGES = [fs.colour_range(0, 0, LAMP_OFF, TEXT), fs.colour_range(1, 1, LAMP_ON, TEXT)]


def lamp(b: Builder, tag: str, x: float, y: float, label: str) -> None:
    b.add(fs.circle_ranges(f"lamp_{tag}", T(tag), x, y, 8, LAMP_RANGES, default=LAMP_OFF))
    b.add(fs.text(x + 14, y + 4, label, size=12))


def machine_row(b: Builder) -> None:
    b.add(fs.text(16, 128, "Machine", size=15, bold=True))
    b.add(f'<circle cx="110" cy="123" r="6" fill="{LAMP_ON}" stroke="{TEXT}" stroke-width="1.5"/>')  # legend
    b.add(fs.text(122, 127, "lamp ON   (grey = OFF)", size=12, color=MUTED))
    # C1 infeed and entry door
    b.add(fs.text(20, 150, "C1 infeed", size=13, bold=True))
    b.add(fs.rect(20, 252, 150, 8, "#555555", rx=2))
    lamp(b, "ConveyerMotor1", 28, 300, "C1 motor")
    lamp(b, "S2", 28, 330, "S2 waiting")
    lamp(b, "DoorMotor", 28, 360, "door open")
    # the four pockets on the index belt
    b.add(fs.rect(200, 252, 590, 8, "#555555", rx=2))
    for i, (name, anim, lamps) in enumerate(POCKETS):
        cx = 260 + i * 145
        b.add(fs.text(cx, 150, name, size=14, bold=True, anchor="middle"))
        ranges = [fs.colour_range(code, code, fill, line) for code, (fill, line) in POCKET_COLOURS.items()]
        b.add(fs.rect_ranges(f"pocket_{name.lower()}", T(anim), cx - 22, 170, 44, 80, ranges, default=BG))
        b.add(fs.rect_ranges(f"cap_{name.lower()}", T(anim), cx - 12, 160, 24, 9,
                             [fs.colour_range(am.POCKET_CAPPED, am.POCKET_CAPPED, "#2b2b2b"),
                              fs.colour_range(am.POCKET_EMPTY, am.POCKET_FILLED, BG)], default=BG))
        b.add(fs.enum_texts(f"pocket_{name.lower()}_word", T(anim), POCKET_WORDS, cx, 278, size=13,
                            anchor="middle", color=MUTED))
        for j, (tag, label) in enumerate(lamps):
            lamp(b, tag, cx - 40, 300 + j * 30, label)
    lamp(b, "IndexMotor", 220, 375, "index belt")
    lamp(b, "S8", 365, 375, "S8 index cam")
    lamp(b, "Jog_Block", 510, 375, "manual blocked (jog watchdog)")
    # discharge and C2 outfeed
    b.add(fs.text(830, 150, "C2 outfeed", size=13, bold=True))
    b.add(fs.rect(810, 252, 150, 8, "#555555", rx=2))
    lamp(b, "S6", 818, 300, "S6 out")
    lamp(b, "S7", 818, 330, "S7 C2 full")
    lamp(b, "Outfeed_Full", 818, 360, "outfeed full")


def fill_panel(b: Builder) -> None:
    b.add(fs.text(1000, 128, "Fill station", size=15, bold=True))
    b.add(fs.progress("fill_level", T("FillLevel"), 1010, 150, 44, 220, 0, FILL_SCALE_ML, "#7b4a22"))
    b.add(fs.progress("fill_setpoint", T("FillSetpoint"), 1060, 150, 12, 220, 0, FILL_SCALE_ML, "#4a4a4a"))
    b.add(fs.text(1004, 160, str(FILL_SCALE_ML), size=11, color=MUTED, anchor="end"))
    b.add(fs.text(1004, 372, "0", size=11, color=MUTED, anchor="end"))
    b.add(fs.text(1032, 390, "level", size=11, color=MUTED, anchor="middle"))
    b.add(fs.text(1070, 390, "set", size=11, color=MUTED, anchor="middle"))
    for k, (label, tag, unit) in enumerate((("Fill level", "FillLevel", "ml"), ("Setpoint", "FillSetpoint", "ml"),
                                             ("Good bottles", "GoodCount", ""))):
        y = 190 + k * 40
        b.add(fs.text(1090, y, label, size=13))
        b.add(fs.value(f"val_{tag}", T(tag), 1225, y, size=15))
        b.add(fs.text(1230, y, unit, size=12, color=MUTED))


def setpoint_box(b: Builder) -> None:
    b.add(fs.rect(16, 420, 560, 280, "#dcdcdc", rx=6))
    b.add(fs.text(32, 450, "Setpoints", size=15, bold=True))
    for k, (label, tag, unit, lo, hi) in enumerate(SETPOINTS):
        y = 495 + k * 55
        b.add(fs.text(32, y, label, size=14))
        b.add(fs.number_input(f"in_{tag}", T(tag), 200, y - 22, 120, 32, lo, hi))
        b.add(fs.text(330, y, unit, size=13))
        b.add(fs.text(380, y, f"{lo}–{hi} {unit}", size=12, color=MUTED))
    b.add(fs.text(32, 640, "Type a value and press Enter. Values outside the range are refused.",
                  size=12, color=MUTED))


def manual_box(b: Builder) -> None:
    b.add(fs.rect(596, 420, 668, 280, "#dcdcdc", rx=6))
    b.add(fs.text(612, 450, "Manual (hold to run)", size=15, bold=True))
    # jog watchdog (PLC, issue I3): a button held > 10 s, e.g. a page closed while pressing it
    b.add(fs.conditional_text("manual_blocked", T("Jog_Block"), 1, 1, 612, 610,
                              "Manual blocked: a button was held > 10 s (or a page was closed). Release all buttons.",
                              size=13, bold=True, color="#a00000"))
    b.add(fs.conditional_text("manual_hint", T("ModeReq"), 0, MANUAL - 1, 612, 500,
                              "The manual buttons appear in Manual mode – press Manual in the header.",
                              size=13, color=MUTED))
    for i, (label, tag) in enumerate(MANUAL_BUTTONS):
        x, y = 612 + (i % 3) * 150, 475 + (i // 3) * 64
        b.add(fs.hold_button(f"man_{label.lower()}", label, x, y, 136, 48, T(tag), T("ModeReq"), MANUAL, MANUAL))
    b.add(fs.text(612, 630, "Interlocks (checked by the PLC): Fill only with a bottle at S4;", size=12, color=MUTED))
    b.add(fs.text(612, 650, "Index only with all valves closed and the cap head stopped.", size=12, color=MUTED))
    b.add(fs.text(612, 676, "A button acts only while it is held (released when the pointer leaves it).",
                  size=12, color=MUTED))


def filler_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[MAIN_ID], MAIN_ID)
    machine_row(b)
    fill_panel(b)
    setpoint_box(b)
    manual_box(b)
    return fs.view(MAIN_ID, MAIN_NAME, W, H, b.parts, b.items)


# --- Line overview (stage 4) -------------------------------------------------------
# Report 8.3: FUXA polls every 200 ms, too slow for smooth motion, so a conveyor is 8 slots and
# the simulator writes one bit per slot (Anim_C1/C2, bit 0 = entry ... bit 7 = exit). Each slot is
# a bottle shape bound to ONE bit (FUXA bitmask), so bottles step along and visibly pile up.
# The rollers turn while the conveyor's motor output is ON.
SLOT_PITCH, SLOT_W, SLOT_H = 38, 26, 46
BELT_Y = 360  # top of the belt band
STANDIN = "#bdbdbd"
PRODUCT = "#7b4a22"


def no_click(svg: str) -> str:
    """Let clicks pass through a drawing on top of a clickable block."""
    return svg.replace("<rect ", '<rect pointer-events="none" ', 1)


def conveyor(b: Builder, key: str, x: float, label: str, anim: str, motor: str | None, bottle: str) -> None:
    """8 slots + belt + 3 rollers. motor None = driven by a machine that is not built (static rollers).
    One call per conveyor, so a later machine's conveyor is one more call (issue I11)."""
    b.add(fs.text(x, BELT_Y - 90, label, size=13, bold=True))
    b.add(fs.rect(x - 4, BELT_Y, 8 * SLOT_PITCH + 4, 10, "#555555", rx=2))
    for i in range(8):
        b.add(fs.rect_ranges(f"{key}_slot{i}", T(anim), x + i * SLOT_PITCH, BELT_Y - SLOT_H - 2, SLOT_W, SLOT_H,
                             [fs.colour_range(0, 0, BG), fs.colour_range(1, 1, bottle, "#333333")],
                             default=BG, bitmask=1 << i))
    for k in range(3):
        b.add(fs.roller(f"{key}_roller{k}", x + 14 + k * 140, BELT_Y + 24, 11, T(motor) if motor else None))


def standin_block(b: Builder, x: float, y: float, w: float, h: float, lines: tuple[str, ...]) -> None:
    """A machine that does not exist yet: grey, no tag (the simulator stands in for it)."""
    b.add(fs.rect(x, y, w, h, STANDIN, rx=6))
    for k, text in enumerate(lines):
        b.add(fs.text(x + w / 2, y + 24 + k * 18, text, size=13 if k == 0 else 11, bold=k == 0,
                      anchor="middle", color=TEXT if k == 0 else MUTED))


def machine_block(b: Builder, key: str, x: float, y: float, w: float, h: float, name: str, target: str) -> None:
    """A real machine: coloured from its State (report 8.2), click opens its detail screen."""
    b.add(fs.rect_ranges(f"{key}_block", T("State"), x, y, w, h,
                         [fs.colour_range(v, v, c) for v, c in STATE_COLOURS.items()], default=GREY,
                         events=[fs.goto(target)]))
    b.add(fs.text(x + w / 2, y + 26, name, size=17, bold=True, anchor="middle", click_through=True))
    b.add(fs.enum_texts(f"{key}_state", T("State"), STATE_NAMES, x + w / 2, y + 46, size=13, anchor="middle",
                        click_through=True))
    b.add(fs.text(x + w / 2, y + h - 10, "click for details", size=11, anchor="middle", color=TEXT,
                  click_through=True))


def line_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[LINE_ID], LINE_ID)
    b.add(fs.text(16, 128, "Line overview", size=15, bold=True))
    # blow moulder (stand-in) -> C1
    standin_block(b, 16, 300, 110, 100, ("Blow moulder", "simulated", "(bottle supply)"))
    conveyor(b, "c1", 140, "C1 infeed", "Anim_C1", "ConveyerMotor1", "#ffffff")
    lamp(b, "S2", 420, BELT_Y + 56, "S2")
    # filler: coloured block, mini pockets, index belt rollers
    fx, fy, fw, fh = 464, 270, 236, 170
    machine_block(b, "filler", fx, fy, fw, fh, "Filler", MAIN_ID)
    for k, (_name, anim, _lamps) in enumerate(POCKETS):
        ranges = [fs.colour_range(code, code, fill, line) for code, (fill, line) in POCKET_COLOURS.items()]
        svg, items = fs.rect_ranges(f"filler_pocket{k}", T(anim), fx + 30 + k * 48, fy + 62, 30, 50, ranges, default=BG)
        b.add((no_click(svg), items))
    for k in range(2):
        svg, items = fs.roller(f"filler_roller{k}", fx + 60 + k * 116, fy + 128, 9, T("IndexMotor"))
        b.add((svg.replace("<path ", '<path pointer-events="none" ', 1), items))
    # syrup room (stand-in) and the product pipe into the filler
    standin_block(b, fx + 38, 128, 160, 60, ("Syrup room", "simulated"))
    b.add(fs.rect_ranges("product_pipe", T("Product_Available"), fx + fw / 2 - 5, 188, 10, fy - 188,
                         [fs.colour_range(0, 0, STANDIN), fs.colour_range(1, 1, PRODUCT)], default=STANDIN))
    b.add(fs.conditional_text("no_product", T("Product_Available"), 0, 0, fx + fw / 2 + 12, 236,
                              "no product", size=12, bold=True, color="#a00000"))
    # C2 (driven by the labeler stand-in) -> labeler (stand-in)
    conveyor(b, "c2", 724, "C2 outfeed (driven by the labeler)", "Anim_C2", None, PRODUCT)
    lamp(b, "S7", 730, BELT_Y + 56, "S7 C2 full")
    lamp(b, "Outfeed_Full", 860, BELT_Y + 56, "outfeed full")
    standin_block(b, 1040, 300, 120, 100, ("Labeler", "simulated", "(takes bottles)"))
    # counters and legend
    b.add(fs.text(16, 500, "Good bottles", size=14))
    b.add(fs.value("line_goodcount", T("GoodCount"), 180, 500, size=16))
    b.add(fs.rect(16, 540, 14, 24, "#ffffff", rx=2, stroke="#333333"))
    b.add(fs.text(38, 557, "bottle on a belt (one per slot; the simulator writes the slot bits)", size=12, color=MUTED))
    b.add(fs.roller("legend_roller", 23, 590, 8, None))
    b.add(fs.text(38, 594, "a turning roller = conveyor motor ON", size=12, color=MUTED))
    b.add(fs.rect(16, 612, 14, 14, STANDIN, rx=2))
    b.add(fs.text(38, 624, "grey block = machine not built yet, simulated", size=12, color=MUTED))
    return fs.view(LINE_ID, LINE_NAME, W, H, b.parts, b.items)


# --- Alarms (stage 5) ---------------------------------------------------------------
# One FUXA alarm per PLC alarm bit (report 9.3): bit -> (FUXA priority, text). The names are
# fixed ("filler_<bit>"): FUXA keys its alarm history by name, a rename would orphan it.
ALARMS = {
    "Alm_IndexJam": ("highhigh", "Index belt jam: no S8 pulse within 5 s in INDEX (FAULT)"),
    "Alm_FillTimeout": ("highhigh", "Fill timeout: setpoint not reached within 10 s (FAULT)"),
    "Alm_CommLost": ("high", "Plant heartbeat lost for 3 s (simulator link)"),
    "Alm_OutfeedBlocked": ("low", "Outfeed blocked longer than 2 min (warning, no FAULT)"),
}
ALARM_GROUP = "Filler"


def alarm_name(bit: str) -> str:
    return f"filler_{bit}"


def alarm_definitions() -> list[dict]:
    return [fs.alarm(alarm_name(bit), T(bit), priority, text, ALARM_GROUP) for bit, (priority, text) in ALARMS.items()]


def alarms_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[ALARMS_ID], ALARMS_ID)
    b.add(fs.text(16, 128, "Active alarms", size=15, bold=True))
    b.add(fs.alarm_table("alarms_active", 16, 140, 1248, 230, "alarms",
                         [("ontime", "Time", 170), ("type", "Priority", 110), ("text", "Alarm", 560),
                          ("status", "Status", 150), ("ack", "Ack", 80)]))
    b.add(fs.button("alarms_reset", "Reset", 16, 392, 140, 44, [fs.set_value(T("Cmd"), 3)], background=NEUTRAL))
    for k, line in enumerate((
        "Ack (the round button in a row) = \"I have seen this alarm\". FUXA keeps every alarm in this list until it",
        "is acknowledged, also one that came and went, so nothing is missed. Ack does not change the PLC.",
        "Reset = the PLC clears its alarm bits, but only those whose cause is gone. After a FAULT (index jam, fill",
        "timeout) the machine also needs Start. Colours: red = FAULT, orange = high, yellow = warning.",
    )):
        b.add(fs.text(176, 406 + k * 20, line, size=13, color=TEXT if k in (0, 2) else MUTED))
    b.add(fs.text(16, 520, "Alarm history", size=15, bold=True))
    b.add(fs.text(16, 548, "Menu \u2630 (top left) \u2192 Alarm history \u2192 \u22ee \u2192 History Alarms \u2192 pick the dates "
                  "\u2192 Apply. It lists every alarm with its on, off and ack time.", size=13))
    b.add(fs.text(16, 572, "(FUXA's own alarm panel; FUXA 1.3.5 cannot show the history in a table on a view.)",
                  size=12, color=MUTED))
    return fs.view(ALARMS_ID, ALARMS_NAME, W, H, b.parts, b.items)


def apply_alarms(project: dict) -> list[str]:
    """Replace the filler alarm definitions, keep alarms made in the editor."""
    ours = {alarm_name(bit) for bit in ALARMS}
    kept = [a for a in project.get("alarms", []) if a["name"] not in ours]
    project["alarms"] = kept + alarm_definitions()
    return [f"{len(ALARMS)} alarms"]


def side_menu() -> list[dict]:
    """FUXA's side menu (top-left button): the views plus FUXA's alarm panel with the history."""
    icons = {LINE_ID: "linear_scale", MAIN_ID: "local_drink", ALARMS_ID: "notifications",
             TRENDS_ID: "show_chart", TAGS_ID: "list"}
    items = [{"id": f"nav_{key}", "text": name, "view": view_id, "icon": icons[view_id]}
             for view_id, name in NAV for key in [HEADER_KEY[view_id]]]
    return items + [{"id": "nav_alarm_panel", "text": "Alarm history", "view": ALARM_PANEL, "icon": "history"}]


# --- Trends (stage 6) ---------------------------------------------------------------
# FUXA logs the TREND_TAGS (fuxa_tags.py, DAQ: every change + once a minute) into its SQLite
# store; a realtime chart shows the last `window` minutes and refills them from that store when
# the view opens. Chart ids are fixed: the chart shapes find their chart by id.
DARK_BLUE = "#1f4e9a"
CHARTS = (  # (id, title = FUXA chart name, window min, [(tag, label, colour)])
    ("ch_filler_fill", "Fill level (ml)", 5, [("FillLevel", "Fill level ml", DARK_BLUE),
                                              ("FillSetpoint", "Setpoint ml", "#8a8a8a")]),
    ("ch_filler_count", "Good bottles", 30, [("GoodCount", "Good bottles", TEXT)]),
    ("ch_filler_state", "Machine state", 30, [("State", "State", TEXT)]),
)


def charts() -> list[dict]:
    return [{"id": chart_id, "name": title,
             "lines": [fs.chart_line(T(tag), ft.DEVICE_NAME, label, colour) for tag, label, colour in lines]}
            for chart_id, title, _window, lines in CHARTS]


def trends_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[TRENDS_ID], TRENDS_ID)
    # fill chart across the top, count (left) and state (right) below it
    places = {"ch_filler_fill": (16, 106, 1248, 300), "ch_filler_count": (16, 414, 616, 250),
              "ch_filler_state": (648, 414, 616, 250)}
    for chart_id, _title, window, _lines in CHARTS:
        b.add(fs.chart(chart_id.replace("ch_filler_", "trend_"), chart_id, *places[chart_id], fs.chart_options(window)))
    fill_win, count_win = CHARTS[0][2], CHARTS[1][2]
    key = " \u00b7 ".join(f"{number} {name}" for number, name in STATE_NAMES.items())
    b.add(fs.text(648, 684, f"State: {key}", size=13))
    # texts at the bottom start at x = 64: FUXA's round menu button covers the bottom-left corner
    b.add(fs.text(64, 684, "Normal: each bottle fills up to the setpoint, back to 0 at the index.", size=13))
    b.add(fs.text(64, 706, f"Last {fill_win} min (fill) and {count_win} min (count, state). History: FUXA's DAQ "
                  "store (volume fuxa-daq), every change + once a minute, kept 1 year.", size=12, color=MUTED))
    return fs.view(TRENDS_ID, TRENDS_NAME, W, H, b.parts, b.items)


def apply_charts(project: dict) -> list[str]:
    """Replace the filler charts, keep charts made in the editor."""
    ours = {chart_id for chart_id, *_ in CHARTS}
    kept = [c for c in project.get("charts", []) if c["id"] not in ours]
    project["charts"] = kept + charts()
    return [f"{len(CHARTS)} charts"]


# --- Test page "Tags": every tag as a plain live value (stage 1) --------------------
# column -> description prefixes shown in it (the prefix of each TAGS description is its group)
COLUMNS = [
    ("Status + alarms", ("status", "alarm")),
    ("Outputs (PLC)", ("output", "conditioned")),
    ("Sensors (simulator)", ("sensor",)),
    ("Wiring (simulator)", ("wiring",)),
    ("Commands (HMI)", ("command", "setpoint")),
    ("Manual (HMI)", ("manual",)),
    ("Animation (simulator)", ("animation",)),
]
COL_W, ROW_H, TOP = 180, 26, HEADER_H + 74


def value_id(name: str) -> str:
    return f"VAL_filler_{name}"


def tags_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[TAGS_ID], TAGS_ID)
    b.add(fs.text(20, HEADER_H + 38, "Filler PLC – all tags (test page, read only)", size=20, bold=True))
    for col, (title, prefixes) in enumerate(COLUMNS):
        x = 20 + col * COL_W
        b.add(fs.text(x, TOP, title, bold=True))
        row = 0
        for name, _area, _number, _kind, description in ft.TAGS:
            if description.split(":")[0] not in prefixes:
                continue
            y = TOP + 30 + row * ROW_H
            b.add(fs.text(x, y, name, size=13))
            b.add(fs.value(value_id(name), T(name), x + COL_W - 20, y))
            row += 1
    return fs.view(TAGS_ID, TAGS_NAME, W, H, b.parts, b.items)


def apply_views(project: dict) -> list[str]:
    """Replace the generated views, drop an empty MainView, open the viewer on Line."""
    changes, kept = [], []
    for v in project["hmi"]["views"]:
        if v["id"] in GENERATED:
            changes.append(f"view {v['name']}")
        elif v["name"] == "MainView" and not v.get("items"):
            changes.append("empty MainView removed")
        else:
            kept.append(v)
    project["hmi"]["views"] = kept + [line_view(), filler_view(), alarms_view(), trends_view(), tags_view()]
    project["hmi"]["layout"].setdefault("navigation", {})["items"] = side_menu()
    project["hmi"]["layout"]["start"] = LINE_ID  # the overview is the main screen (report 8.1)
    return changes
