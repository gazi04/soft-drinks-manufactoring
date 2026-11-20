"""The FUXA views: header (on every view), Line, Filler, Alarms, Trends, and a Tags test page per PLC
(filler, syrup room, labeler). Functions that can draw any machine take `m: ft.Machine` (default: the filler).

Built from fuxa_shapes.py (the FUXA shape formats) and fuxa_tags.py (the tag
table). Run through `uv run python tests/fuxa_tags.py`, which writes the export
that is then imported into FUXA.

Header for three machines (report 8.1/8.2, roadmap stages 2, S2 and L2), 1280 x 96 at the top of every view:
  row 1 (the same on every view): Filler chip | Syrup room chip | Labeler chip | E-stop per PLC | Line Alarms Trends
         A machine chip is coloured by that machine's State and shows state, step and mode
         ("Stop – press Auto" after a PLC restart, issue I10); a click opens its screen. Its PLC
         link is a cover over the right part of the chip: invisible while FUXA is connected,
         yellow "PLC slow" / red "PLC LOST" otherwise (it hides the stale state and mode).
  row 2 (the machine of this view): machine box | Start Stop Reset | Auto Manual Stop | its alarm chips
         On Line, Alarms and Trends the machine box is a picker: these views exist once per
         machine (v_filler_line / v_syrup_line / v_labeler_line ...), same body, and the picker
         opens the same view for another machine.
Colours (ISA-101): grey is normal, colour only for states and problems. State colours
follow report 8.2. The header only WRITES Cmd (1 Start, 2 Stop, 3 Reset; the PLC sets
it back to 0) and ModeReq (0 Stop, 1 Auto, 2 Manual) of the machine in row 2.
"""

from dataclasses import dataclass

from simulator import address_map as am
from simulator import address_map_labeler as lm
from simulator import config
from simulator.plcs import (ALARM_NAMES, LABELER_ALARM_NAMES, LABELER_STEP_NAMES, MODE_NAMES, STATE_NAMES,
                            STEP_NAMES, SYRUP_ALARM_NAMES, SYRUP_STEP_NAMES)

import fuxa_shapes as fs
import fuxa_tags as ft

W, H = 1280, 720
HEADER_H = 96
LINE_ID, LINE_NAME = "v_filler_line", "Line"
MAIN_ID, MAIN_NAME = "v_filler_main", "Filler"
ALARMS_ID, ALARMS_NAME = "v_filler_alarms", "Alarms"
TRENDS_ID, TRENDS_NAME = "v_filler_trends", "Trends"
TAGS_ID, TAGS_NAME = "v_filler_tags", "Tags"
SYRUP_TAGS_ID, SYRUP_TAGS_NAME = "v_syrup_tags", "Syrup tags"  # side menu only (test page)
SYRUP_MAIN_ID, SYRUP_MAIN_NAME = "v_syrup_main", "Syrup room"  # the syrup room's screen (S3)
# Line, Alarms and Trends once more with the syrup room in header row 2 (the row-2 picker opens them)
SYRUP_LINE_ID, SYRUP_LINE_NAME = "v_syrup_line", "Line (syrup room)"
SYRUP_ALARMS_ID, SYRUP_ALARMS_NAME = "v_syrup_alarms", "Alarms (syrup room)"
SYRUP_TRENDS_ID, SYRUP_TRENDS_NAME = "v_syrup_trends", "Trends (syrup room)"
LABELER_TAGS_ID, LABELER_TAGS_NAME = "v_labeler_tags", "Labeler tags"  # side menu only (test page, HMI L1)
# Line, Alarms and Trends a third time with the labeler in header row 2 (HMI L2)
LABELER_LINE_ID, LABELER_LINE_NAME = "v_labeler_line", "Line (labeler)"
LABELER_ALARMS_ID, LABELER_ALARMS_NAME = "v_labeler_alarms", "Alarms (labeler)"
LABELER_TRENDS_ID, LABELER_TRENDS_NAME = "v_labeler_trends", "Trends (labeler)"
LABELER_MAIN_ID, LABELER_MAIN_NAME = "v_labeler_main", "Labeler"  # the labeler's screen (HMI L3)
GENERATED = (LINE_ID, MAIN_ID, ALARMS_ID, TRENDS_ID, TAGS_ID, SYRUP_TAGS_ID,
             SYRUP_LINE_ID, SYRUP_ALARMS_ID, SYRUP_TRENDS_ID, SYRUP_MAIN_ID, LABELER_TAGS_ID,
             LABELER_LINE_ID, LABELER_ALARMS_ID, LABELER_TRENDS_ID, LABELER_MAIN_ID)
NAV = ((LINE_ID, LINE_NAME), (MAIN_ID, MAIN_NAME), (SYRUP_MAIN_ID, SYRUP_MAIN_NAME),
       (LABELER_MAIN_ID, LABELER_MAIN_NAME), (ALARMS_ID, ALARMS_NAME),
       (TRENDS_ID, TRENDS_NAME), (TAGS_ID, TAGS_NAME))  # side menu
HEADER_KEY = {LINE_ID: "line", MAIN_ID: "main", ALARMS_ID: "alarms", TRENDS_ID: "trends",
              TAGS_ID: "tags", SYRUP_TAGS_ID: "syrup_tags", SYRUP_LINE_ID: "syrup_line",
              SYRUP_ALARMS_ID: "syrup_alarms", SYRUP_TRENDS_ID: "syrup_trends",
              SYRUP_MAIN_ID: "syrup_main", LABELER_TAGS_ID: "labeler_tags", LABELER_LINE_ID: "labeler_line",
              LABELER_ALARMS_ID: "labeler_alarms", LABELER_TRENDS_ID: "labeler_trends",
              LABELER_MAIN_ID: "labeler_main"}  # header id prefix per view
ALARM_PANEL = "[alarms]"  # side-menu target that opens FUXA's own alarm panel (active + history)


@dataclass(frozen=True)
class MachineUi:
    """What the header needs to draw one machine (its tags are in m, issue I11)."""
    m: ft.Machine
    title: str
    plc_label: str  # text of its PLC link chip
    steps: dict[int, str]
    alarms: tuple[str, ...]  # alarm bits in address order
    detail: str  # view the machine chip opens
    short: str = ""  # label of its row-2 picker button (76 px: "Syrup room" does not fit at 14 px bold)

    @property
    def pick_label(self) -> str:
        return self.short or self.title


FILLER_UI = MachineUi(ft.FILLER, "Filler", "Filler PLC", STEP_NAMES, ALARM_NAMES, MAIN_ID)
SYRUP_UI = MachineUi(ft.SYRUP, "Syrup room", "Syrup PLC", SYRUP_STEP_NAMES, SYRUP_ALARM_NAMES, SYRUP_MAIN_ID,
                     short="Syrup")
LABELER_UI = MachineUi(ft.LABELER, "Labeler", "Labeler PLC", LABELER_STEP_NAMES, LABELER_ALARM_NAMES,
                       LABELER_MAIN_ID)
UIS = (FILLER_UI, SYRUP_UI, LABELER_UI)

# The views shown once per machine: kind -> machine key -> view id (header nav and row-2 picker)
SHARED = {"line": {"filler": LINE_ID, "syrup": SYRUP_LINE_ID, "labeler": LABELER_LINE_ID},
          "alarms": {"filler": ALARMS_ID, "syrup": SYRUP_ALARMS_ID, "labeler": LABELER_ALARMS_ID},
          "trends": {"filler": TRENDS_ID, "syrup": SYRUP_TRENDS_ID, "labeler": LABELER_TRENDS_ID}}
KIND = {view_id: kind for kind, views in SHARED.items() for view_id in views.values()}
# The machine in header row 2 of every view
CONTEXT = {MAIN_ID: FILLER_UI, TAGS_ID: FILLER_UI, SYRUP_TAGS_ID: SYRUP_UI, SYRUP_MAIN_ID: SYRUP_UI,
           LABELER_TAGS_ID: LABELER_UI, LABELER_MAIN_ID: LABELER_UI}
CONTEXT |= {view_id: ui for views in SHARED.values() for ui in UIS for key, view_id in views.items()
            if key == ui.m.key}
VIEW_NAMES = {LINE_ID: LINE_NAME, SYRUP_LINE_ID: SYRUP_LINE_NAME, ALARMS_ID: ALARMS_NAME,
              SYRUP_ALARMS_ID: SYRUP_ALARMS_NAME, TRENDS_ID: TRENDS_NAME, SYRUP_TRENDS_ID: SYRUP_TRENDS_NAME,
              LABELER_LINE_ID: LABELER_LINE_NAME, LABELER_ALARMS_ID: LABELER_ALARMS_NAME,
              LABELER_TRENDS_ID: LABELER_TRENDS_NAME}


def shared_view(kind: str, ui: MachineUi) -> str:
    return SHARED[kind][ui.m.key]

# colours
HEADER_BG = "#d4d4d4"
NEUTRAL = "#c4c4c4"  # chip / button at rest
TEXT = "#1d1d1d"
MUTED = "#555555"
RED, GREEN, YELLOW, ORANGE, GREY = "#d64545", "#3fa34d", "#e6c229", "#e8812f", "#9e9e9e"
ACTIVE = "#1f4e9a"  # selected mode / current view
PRODUCT = "#7b4a22"  # finished product (Line pipe, filled bottles, syrup room)
STATE_COLOURS = {0: GREY, 1: GREEN, 2: YELLOW, 3: ORANGE, 9: RED}  # report 8.2
ALARM_LABELS = {"Alm_IndexJam": "Index jam", "Alm_FillTimeout": "Fill timeout",
                "Alm_OutfeedBlocked": "Outfeed blocked", "Alm_CommLost": "Comm lost",
                "Alm_Overflow": "Overflow", "Alm_OverPressure": "Over-pressure", "Alm_CoolTimeout": "Cool timeout",
                "Alm_LowSyrup": "Low syrup", "Alm_DoseTimeout": "Dose timeout",
                "Alm_LabelTimeout": "Label timeout", "Alm_PocketJam": "Pocket jam", "Alm_LabelEmpty": "Roll empty",
                "Alm_GlueOverTemp": "Glue too hot", "Alm_GlueHeatTimeout": "Heat timeout"}
LINK_OK = 5  # FUXA connection status: 0 off, 3 warning, 5 connected
NO_LINK = "#dedede"  # E-stop segment of a PLC FUXA is not connected to: unknown, neither OK nor pressed

CMD_BUTTONS = (("Start", 1), ("Stop", 2), ("Reset", 3))
MANUAL = 2  # ModeReq value of Manual
# hold-to-run buttons: (label, Man_* tag)
MANUAL_BUTTONS = (("C1", "Man_C1"), ("Door", "Man_Door"), ("Rinse", "Man_Rinse"),
                  ("Fill", "Man_Fill"), ("Cap", "Man_Cap"), ("Index", "Man_Index"))
MODE_BUTTONS = (("Auto", 1), ("Manual", 2), ("Stop", 0))
# Mode as shown in a machine chip: Stop mode carries the hint (after a PLC restart Start is ignored, I10)
MODE_WORDS = {0: "Stop – press Auto", 1: "Auto", 2: "Manual (paused)"}
NAV_BUTTONS = (("line", "Line"), ("alarms", "Alarms"), ("trends", "Trends"))

# Header geometry (x from the left edge, see the module docstring)
# Three machines (HMI L2): the PLC link is a cover inside each machine chip (LINK_W, right part), so
# no separate link chips; row 2 shifts right for a three-button picker, the alarm chips get narrower
# (the labeler has 7). 16 + 3 x 250 + 2 x 8 = 782 | E-stop 790-996 (a segment per PLC, I17) | nav 1004-1264.
ROW1_Y, ROW1_H = 10, 36  # chips and buttons of row 1 (the machine chips are 4 px taller)
ROW2_Y, ROW2_H = 54, 32
CHIP_X, CHIP_W, CHIP_GAP = 16, 250, 8  # machine chips
LINK_W = 112  # the link cover over the right part of a machine chip (state and mode words)
ESTOP_X, ESTOP_SEG_W, ESTOP_GAP = 790, 66, 4  # one E-stop segment per PLC (I17): 790-996
NAV_X, NAV_W, NAV_GAP = 1004, 84, 4
PICK_X, PICK_W, PICK_GAP = 16, 76, 4  # row 2: machine box / picker, 16-252
CMD_X, MODE_X, BTN_W, BTN_GAP = 262, 478, 66, 4  # 262-468, 478-684
ALARM_X, ALARM_W, ALARM_GAP = 694, 78, 4  # up to 7 chips: 694-1264

T = ft.tag_id  # tag name -> FUXA tag id of the FILLER. A function that can draw another machine takes
#                `m: ft.Machine` and shadows it locally with `T = m.tag_id` (one device per PLC, issue I11).


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
    """Draw the header of view `current` into builder b. key makes the ids unique per view.
    Row 1 shows every machine; row 2 belongs to CONTEXT[current]."""
    ui = CONTEXT[current]
    p = f"hdr_{key}"
    b.add(fs.rect(0, 0, W, HEADER_H, HEADER_BG, rx=0))
    for i, chip in enumerate(UIS):
        machine_chip(b, p, chip, CHIP_X + i * (CHIP_W + CHIP_GAP), here=chip.detail == current)
    estop_chip(b, p)
    # Navigation: the shared views of this view's machine; the machine screens open from the chips
    for i, (kind, label) in enumerate(NAV_BUTTONS):
        here = KIND.get(current) == kind
        b.add(fs.button(f"{p}_nav_{kind}", label, NAV_X + i * (NAV_W + NAV_GAP), ROW1_Y, NAV_W, ROW1_H,
                        [fs.goto(shared_view(kind, ui))],
                        background=ACTIVE if here else NEUTRAL, color="#ffffff" if here else TEXT))
    row2(b, p, ui, current)


def machine_chip(b: Builder, p: str, ui: MachineUi, x: float, here: bool) -> None:
    """Row 1: one machine at a glance, coloured by its State (report 8.2); click = its screen.
    here: this view is that machine's screen (a dark blue bar under the chip)."""
    T = ui.m.tag_id  # this machine's tags (shadows the filler helper)
    k = f"{p}_{ui.m.key}"
    right = x + CHIP_W - 10
    b.add(fs.rect_ranges(f"{k}_chip", T("State"), x, ROW1_Y - 2, CHIP_W, ROW1_H + 4,
                         [fs.colour_range(v, v, c) for v, c in STATE_COLOURS.items()], default=GREY,
                         events=[fs.goto(ui.detail)]))
    b.add(fs.text(x + 10, 25, ui.title, size=15, bold=True, click_through=True))
    b.add(fs.enum_texts(f"{k}_state", T("State"), STATE_NAMES, right, 25, size=14, bold=True, anchor="end",
                        click_through=True))
    b.add(fs.enum_texts(f"{k}_step", T("Step"), ui.steps, x + 10, 42, size=12, click_through=True))
    b.add(fs.enum_texts(f"{k}_mode", T("ModeReq"), MODE_WORDS, right, 42, size=12, bold=True, anchor="end",
                        click_through=True))
    link_cover(b, k, ui, x + CHIP_W - LINK_W)  # after the words: it hides them when the link is lost
    if here:
        b.add(fs.rect(x, ROW1_Y + ROW1_H + 3, CHIP_W, 4, ACTIVE, rx=1))


def estop_chip(b: Builder, p: str) -> None:
    """One E-stop chain reaches every PLC (report 9.2); the chip shows it per PLC, one segment each
    (decision I17, 2026-11-19). Per segment, drawing order (later = on top):
      1. the segment, red while that PLC reads E_Stop_OK = 0, grey otherwise
      2. "E-stop OK" while it reads 1, "E-STOP" (white) while it reads 0 (bottom line)
      3. a link cover over the WHOLE segment, on that PLC's FUXA connection status: transparent while
         connected, light grey otherwise (a cover over the bottom line only left the top half red:
         FUXA colours the segment from null, too; found in the browser check)
      4. the PLC's short name (top line), always readable, and "no link" while the cover shows.
    Why the cover: FUXA gives a lost device's tags the value null, and its ranges read null as 0, so
    without it a lost PLC showed "E-STOP PRESSED" (I17). The cover hides only its own PLC's segment;
    the other PLCs still show the E-stop. A real E-stop turns every segment red; a single red segment
    is a wiring fault (only that PLC sees the chain open)."""
    for i, ui in enumerate(UIS):
        e, k = ui.m.tag_id("E_Stop_OK"), f"{p}_estop_{ui.m.key}"
        x = ESTOP_X + i * (ESTOP_SEG_W + ESTOP_GAP)
        mid = x + ESTOP_SEG_W / 2
        b.add(fs.rect_ranges(k, e, x, ROW1_Y, ESTOP_SEG_W, ROW1_H,
                             [fs.colour_range(0, 0, RED), fs.colour_range(1, 1, NEUTRAL)], default=NEUTRAL))
        b.add(fs.conditional_text(f"{k}_ok", e, 1, 1, mid, 40, "E-stop OK", size=11, anchor="middle"))
        b.add(fs.conditional_text(f"{k}_on", e, 0, 0, mid, 40, "E-STOP", size=12, bold=True, color="#ffffff",
                                  anchor="middle"))
        status = ui.m.status_tag_id
        b.add(fs.rect_ranges(f"{k}_link", status, x, ROW1_Y, ESTOP_SEG_W, ROW1_H,
                             [fs.colour_range(0, LINK_OK - 1, NO_LINK, NO_LINK),  # border too: no red edge shows
                              fs.colour_range(LINK_OK, LINK_OK, "none")], default=NO_LINK))
        b.add(fs.text(mid, 24, ui.pick_label, size=10, anchor="middle", element_id=f"{k}_name"))
        b.add(fs.conditional_text(f"{k}_nolink", status, 0, LINK_OK - 1, mid, 40, "no link", size=11,
                                  color=MUTED, anchor="middle"))


def link_cover(b: Builder, k: str, ui: MachineUi, x: float) -> None:
    """FUXA's own connection status of one PLC device, as a cover over the right part of its machine
    chip (HMI L2: three chips leave no room for link chips). Connected: transparent, the chip shows
    state and mode as usual (ISA-101: nothing to see when all is well). Slow: yellow "PLC slow".
    Lost: red "PLC LOST" over the state and mode words, which are stale then. A click goes through
    to the chip (no_click), so the machine's screen still opens."""
    status, mid = ui.m.status_tag_id, x + LINK_W / 2
    svg, items = fs.rect_ranges(f"{k}_link", status, x, ROW1_Y - 2, LINK_W, ROW1_H + 4,
                                [fs.colour_range(0, 0, RED), fs.colour_range(1, LINK_OK - 1, YELLOW),
                                 fs.colour_range(LINK_OK, LINK_OK, "none")], default="none")
    b.add((no_click(svg), items))
    b.add(fs.conditional_text(f"{k}_link_lost", status, 0, 0, mid, 33, "PLC LOST", size=13, bold=True,
                              color="#ffffff", anchor="middle", click_through=True))
    b.add(fs.conditional_text(f"{k}_link_slow", status, 1, LINK_OK - 1, mid, 33, "PLC slow", size=13, bold=True,
                              anchor="middle", click_through=True))


def row2(b: Builder, p: str, ui: MachineUi, current: str) -> None:
    """Row 2: the machine of this view – which one, its commands, its mode, its alarm chips."""
    T = ui.m.tag_id  # this machine's tags
    kind = KIND.get(current)
    if kind is None:  # a machine's own screen: a label, nothing to choose
        width = len(UIS) * PICK_W + (len(UIS) - 1) * PICK_GAP
        b.add(fs.rect(PICK_X, ROW2_Y, width, ROW2_H, ACTIVE, f"{p}_machine"))
        b.add(fs.text(PICK_X + width / 2, 75, ui.title, size=14, bold=True, color="#ffffff", anchor="middle"))
    else:  # a shared view: the picker opens the same view for the other machine
        for i, other in enumerate(UIS):
            here = other is ui
            b.add(fs.button(f"{p}_pick_{other.m.key}", other.pick_label, PICK_X + i * (PICK_W + PICK_GAP), ROW2_Y,
                            PICK_W, ROW2_H, [] if here else [fs.goto(shared_view(kind, other))],
                            background=ACTIVE if here else NEUTRAL, color="#ffffff" if here else TEXT))

    # Commands (Cmd) and mode selector (ModeReq) of this machine
    for i, (label, number) in enumerate(CMD_BUTTONS):
        b.add(fs.button(f"{p}_cmd_{label.lower()}", label, CMD_X + i * (BTN_W + BTN_GAP), ROW2_Y, BTN_W, ROW2_H,
                        [fs.set_value(T("Cmd"), number)], background=NEUTRAL))
    # One write per click: FUXA 1.3.5 sends all events of a click at the same time, and 7 writes
    # at once (ModeReq + releasing the six Man_*) timed out and dropped the PLC link (stage 3).
    for i, (label, number) in enumerate(MODE_BUTTONS):
        others = [fs.colour_range(v, v, NEUTRAL, TEXT) for v in MODE_NAMES if v != number]
        b.add(fs.button(f"{p}_mode_{label.lower()}", label, MODE_X + i * (BTN_W + BTN_GAP), ROW2_Y, BTN_W, ROW2_H,
                        [fs.set_value(T("ModeReq"), number)], background=NEUTRAL,
                        tag_id=T("ModeReq"), ranges=[fs.colour_range(number, number, ACTIVE, "#ffffff"), *others]))

    # Alarm chips of this machine; a click opens its alarm list
    for i, name in enumerate(ui.alarms):
        x = ALARM_X + i * (ALARM_W + ALARM_GAP)
        b.add(fs.rect_ranges(f"{p}_{name}", T(name), x, ROW2_Y, ALARM_W, ROW2_H,
                             [fs.colour_range(0, 0, NEUTRAL), fs.colour_range(1, 1, RED)], default=NEUTRAL,
                             events=[fs.goto(shared_view("alarms", ui))]))
        b.add(fs.text(x + ALARM_W / 2, 74, ALARM_LABELS[name], size=10, anchor="middle", click_through=True))


# --- Filler detail screen (stage 3) ----------------------------------------------
BG = "#e6e6e6"  # page background
LAMP_OFF, LAMP_ON = "#7a7a7a", "#ffffff"  # ISA-101: no colour for normal equipment states
CAP_COLOUR = "#2b2b2b"  # a screwed-on cap (filler pocket CAPPED and every bottle after the filler)
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


def lamp(b: Builder, tag: str, x: float, y: float, label: str, m: ft.Machine = ft.FILLER,
         element_id: str | None = None) -> None:
    """A lamp on one bit. element_id: needed when a view has two lamps with the same tag name from two
    PLCs (Line: the filler's and the labeler's Outfeed_Full); one id = one FUXA binding (found in L4)."""
    b.add(fs.circle_ranges(element_id or f"lamp_{tag}", m.tag_id(tag), x, y, 8, LAMP_RANGES, default=LAMP_OFF))
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
                             [fs.colour_range(am.POCKET_CAPPED, am.POCKET_CAPPED, CAP_COLOUR),
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


# --- Syrup room screen (S3) ------------------------------------------------------------
# Report 8.1: tanks with live levels, pipes with flow animation, pumps and valves, temperature and
# CO2 pressure, recipe. Same layout as the Filler screen: process on top, setpoints bottom left,
# manual buttons bottom right. All syrup tags: ft.SYRUP (degC / bar already scaled by FUXA, fuxa_tags.scale).
S = ft.SYRUP.tag_id  # syrup room tag name -> FUXA tag id
PIPE_OFF, WATER, GAS = "#b0b0b0", "#5f86b0", "#555555"
SYRUP_DARK = "#5a3417"
AGITATOR_Y = 336  # impeller centre, near the bottom of the mixing tank bar (190..370)
TANK_TOP, TANK_H = 190, 180  # level bars y 190..370
MIX_MAX, BUFFER_MAX = 950, 1950  # PLC limits (docs/syrup/state-diagram.md, "Fixed constants")
# Recipe setpoints: (label, tag, unit, min, max). Ranges so a legal recipe never trips an alarm
# (PLC MixMax 950 L, DoseTimeout / CoolTimeout 60 s, PressMax 6.00 bar; simulator water 20 L/s,
# syrup 5 L/s, chiller 0 degC): the largest batch is 700 + 700 x 10 / 30 = 933 L, its syrup
# 233 L takes 47 s; cooling 16 -> 2 degC takes about 41 s; BufferLow 1000 + 933 <= BufferMax 1950.
# BufferMin > 0: with 0 the Product_Available signal never drops (simulator review 2026-11-13).
SYRUP_SETPOINTS = (
    ("Water per batch", "WaterBatch", "L", 100, 700),
    ("Ratio water:syrup", "SyrupRatio", "x10", 30, 100),
    ("Mix time", "MixTime", "s", 5, 120),
    ("Cool setpoint", "CoolSetpoint", "°C", 2, 10),  # whole numbers as int: FUXA stores JSON from JavaScript
    ("Cooling band", "TempHyst", "°C", 0.2, 2),
    ("CO2 setpoint", "PressSetpoint", "bar", 2, 5),
    ("CO2 band", "PressHyst", "bar", 0.05, 0.5),
    ("New batch below", "BufferLow", "L", 200, 1000),
    ("Product min", "BufferMin", "L", 50, 300),
    ("Carbonation time", "CarbTime", "s", 5, 60),
)
SYRUP_MANUAL_BUTTONS = (("Water pump", "Man_WaterPump"), ("Syrup pump", "Man_SyrupPump"),
                        ("Agitator", "Man_Agitator"), ("Cooling", "Man_Cooling"),
                        ("CO2 valve", "Man_CO2"), ("Transfer", "Man_Transfer"))
# (pipe id, output tag that makes it flow, colour when flowing, x, y, w, h)
SYRUP_PIPES = (
    ("pipe_water", "WaterPump", WATER, 100, 136, 248, 6), ("pipe_water_down", "WaterPump", WATER, 342, 136, 6, 54),
    ("pipe_co2", "CO2Valve", GAS, 412, 148, 60, 6), ("pipe_co2_down", "CO2Valve", GAS, 412, 148, 6, 42),
    ("pipe_jacket", "CoolingValve", WATER, 434, 200, 6, 160),
    ("pipe_syrup", "SyrupPump", SYRUP_DARK, 100, 346, 230, 6),
    ("pipe_transfer", "TransferPump", PRODUCT, 446, 346, 254, 6),
    ("pipe_product", "Product_Available", PRODUCT, 780, 346, 110, 6),
)


def buffer_scale_note() -> tuple[str, str]:
    """The simulator takes BUFFER_DRAW_SCALE times more product from the buffer than the filler puts
    into a bottle (time compression: a real 590 L batch would last hours; docs/simulator/design-notes.md).
    The PLCs never see the factor, so the screen must say it, or 15 L per 0.5 L bottle looks like a
    unit error. Built from the simulator's config, so the text follows a changed factor."""
    scale, bottle_ml = config.BUFFER_DRAW_SCALE, config.DEFAULT_FILL_SETPOINT_ML
    return (f"Simulated: a {bottle_ml} ml bottle draws {bottle_ml / 1000 * scale:g} L from the buffer",
            f"(x{scale:g} time compression: a batch lasts minutes, not hours)")


def decimals(tag: str, m: ft.Machine = ft.SYRUP) -> int:
    return len(str(m.factor(tag))) - 1


def pipe(b: Builder, element_id: str, tag: str, colour: str, x: float, y: float, w: float, h: float,
         m: ft.Machine = ft.SYRUP) -> None:
    """A pipe that is grey while its pump / valve is OFF and shows the medium's colour while it flows
    (the labeler uses it for its label web: grey while the feed stands, white while it runs)."""
    b.add(fs.rect_ranges(element_id, m.tag_id(tag), x, y, w, h,
                         [fs.colour_range(0, 0, PIPE_OFF), fs.colour_range(1, 1, colour)], default=PIPE_OFF))


def tank(b: Builder, key: str, tag: str, name: str, x: float, w: float, capacity: float, colour: str,
         limit: int | None = None) -> None:
    """A tank as a level bar 0..capacity with its name, scale, live level and the PLC's high limit."""
    capacity = int(capacity)  # 1000.0 -> 1000: FUXA (JavaScript) would rewrite it, the export must round-trip
    b.add(fs.text(x + w / 2, TANK_TOP - 10, name, size=14, bold=True, anchor="middle"))
    b.add(fs.progress(f"tank_{key}", S(tag), x, TANK_TOP, w, TANK_H, 0, capacity, colour))
    b.add(fs.text(x - 4, TANK_TOP + 10, f"{capacity:.0f}", size=11, color=MUTED, anchor="end"))
    b.add(fs.text(x - 4, TANK_TOP + TANK_H, "0", size=11, color=MUTED, anchor="end"))
    b.add(fs.value(f"val_{tag}", S(tag), x + w / 2 + 12, TANK_TOP + TANK_H + 22, size=15))
    b.add(fs.text(x + w / 2 + 16, TANK_TOP + TANK_H + 22, "L", size=13))
    if limit is not None:  # a tick at the PLC's high limit (overflow alarm, pump interlock)
        y = TANK_TOP + TANK_H * (1 - limit / capacity)
        b.add(fs.rect(x - 6, y - 1, w + 12, 2, RED, rx=0))
        b.add(fs.text(x + w + 8, y + 4, f"max {limit}", size=11, color="#a00000"))


def syrup_process(b: Builder) -> None:
    b.add(fs.text(16, 128, "Process", size=15, bold=True))
    b.add(f'<circle cx="96" cy="123" r="6" fill="{LAMP_ON}" stroke="{TEXT}" stroke-width="1.5"/>')  # legend
    b.add(fs.text(108, 127, "ON", size=12, color=MUTED))
    b.add(fs.text(16, 143, "Mains water", size=12, color=MUTED))
    b.add(fs.text(478, 154, "CO2 supply", size=12, color=MUTED))
    for element_id, tag, colour, x, y, w, h in SYRUP_PIPES:
        pipe(b, element_id, tag, colour, x, y, w, h)
    tank(b, "syrup", "SyrupLevel", "Syrup tank", 40, 60, config.SYRUP_TANK_L, SYRUP_DARK)
    tank(b, "mix", "MixLevel", "Mixing tank / carbonator", 330, 100, config.MIX_TANK_L, PRODUCT, MIX_MAX)
    # agitator: a static shaft from the tank top and an impeller that turns while the Agitator output is ON
    # (same FUXA action as the conveyor rollers on the Line view)
    b.add(fs.rect(379, TANK_TOP, 2, AGITATOR_Y - TANK_TOP, "#3a3a3a", rx=0))
    b.add(fs.impeller("agitator_impeller", 380, AGITATOR_Y, 16, S("Agitator")))
    tank(b, "buffer", "BufferLevel", "Buffer tank", 700, 80, config.BUFFER_TANK_L, PRODUCT, BUFFER_MAX)
    b.add(fs.text(895, 354, "to the filler", size=12, color=MUTED))
    for k, line in enumerate(buffer_scale_note()):  # under the buffer tank, above the setpoint boxes
        b.add(fs.text(700, 404 + k * 13, line, size=11, color=MUTED, element_id=f"note_buffer_scale{k}"))
    m = ft.SYRUP
    lamp(b, "WaterPump", 200, 125, "water pump", m)
    lamp(b, "SyrupPump", 150, 333, "syrup pump", m)
    lamp(b, "TransferPump", 520, 333, "transfer pump", m)
    lamp(b, "Product_Available", 790, 333, "product available", m)
    for k, (tag, label) in enumerate((("MixHighHigh", "high-high switch"), ("Agitator", "agitator"),
                                      ("CoolingValve", "cooling valve"), ("CO2Valve", "CO2 valve"))):
        lamp(b, tag, 462, 214 + k * 30, label, m)


def syrup_values(b: Builder) -> None:
    """The batch at a glance: step, counter, temperature and pressure with their hysteresis bits."""
    m, x, right = ft.SYRUP, 990, 1225
    b.add(fs.text(x, 128, "Batch", size=15, bold=True))
    b.add(fs.text(x, 158, "Step", size=13))
    b.add(fs.enum_texts("syrup_step", S("Step"), SYRUP_STEP_NAMES, right + 30, 158, size=15, bold=True, anchor="end"))
    rows = (("Batches done", "BatchCount", "", 188), ("Temperature", "ProductTemp", "°C", 218),
            ("CO2 pressure", "CO2Pressure", "bar", 278), ("Syrup per batch", "SyrupTarget", "L", 338),
            ("Level after dosing", "DoseTarget", "L", 364))
    for label, tag, unit, y in rows:
        b.add(fs.text(x, y, label, size=13))
        b.add(fs.value(f"val_{tag}", S(tag), right, y, size=15))
        b.add(fs.text(right + 5, y, unit, size=12, color=MUTED))
    lamp(b, "Temp_OK", x + 8, 240, "cold enough (Temp_OK)", m)
    lamp(b, "CO2_Demand", x + 8, 300, "CO2 wanted (CO2_Demand)", m)
    lamp(b, "Batch_Dosed", x + 8, 390, "water + syrup dosed", m)


def syrup_setpoints(b: Builder) -> None:
    b.add(fs.rect(16, 420, 760, 280, "#dcdcdc", rx=6))
    b.add(fs.text(32, 450, "Recipe setpoints", size=15, bold=True))
    for k, (label, tag, unit, lo, hi) in enumerate(SYRUP_SETPOINTS):
        x, y = 32 + (k // 5) * 380, 485 + (k % 5) * 42
        d = decimals(tag)
        b.add(fs.text(x, y, label, size=13))
        b.add(fs.number_input(f"in_{tag}", S(tag), x + 140, y - 21, 84, 30, lo, hi, step="any" if d else None))
        b.add(fs.text(x + 232, y, unit, size=13))
        b.add(fs.text(x + 270, y, f"{lo:.{d}f}–{hi:.{d}f}", size=11, color=MUTED))
    b.add(fs.text(32, 684, "Type a value and press Enter; values outside the range are refused. "
                  "Ratio 55 = 5.5 : 1 water : syrup.", size=12, color=MUTED))


def syrup_manual(b: Builder) -> None:
    b.add(fs.rect(792, 420, 472, 280, "#dcdcdc", rx=6))
    b.add(fs.text(808, 450, "Manual (hold to run)", size=15, bold=True))
    b.add(fs.conditional_text("syrup_manual_hint", S("ModeReq"), 0, MANUAL - 1, 808, 500,
                              "The buttons appear in Manual mode (header).", size=13, color=MUTED))
    for i, (label, tag) in enumerate(SYRUP_MANUAL_BUTTONS):
        x, y = 808 + (i % 3) * 150, 470 + (i // 3) * 60
        b.add(fs.hold_button(f"syrup_{tag}", label, x, y, 136, 48, S(tag), S("ModeReq"), MANUAL, MANUAL))
    b.add(fs.conditional_text("syrup_manual_blocked", S("Jog_Block"), 1, 1, 808, 598,
                              "Manual blocked: a button was held > 10 s. Release all.", size=13, bold=True,
                              color="#a00000"))
    for k, line in enumerate(("Interlocks (the PLC checks them, also in Manual):",
                              "pumps stop at 950 L or the high-high switch; agitator from 100 L;",
                              "CO2 only into cold product and below 6 bar;",
                              "transfer only with product in the tank and room in the buffer.",
                              "A button acts only while it is held.")):
        b.add(fs.text(808, 620 + k * 17, line, size=12, color=TEXT if k == 0 else MUTED))


def syrup_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[SYRUP_MAIN_ID], SYRUP_MAIN_ID)
    syrup_process(b)
    syrup_values(b)
    syrup_setpoints(b)
    syrup_manual(b)
    return fs.view(SYRUP_MAIN_ID, SYRUP_MAIN_NAME, W, H, b.parts, b.items)


# --- Labeler screen (HMI L3) ------------------------------------------------------------
L = ft.LABELER.tag_id  # labeler tag name -> FUXA tag id
GLUE_COLOUR = "#c07a2a"  # molten hot-melt
GLUE_SCALE_C = 220  # temperature bar 0..220 degC
GLUE_MAX_C = 200  # PLC constant GlueMax 2000 (0.1 degC): over-temperature FAULT (docs/labeler/state-diagram.md)
GLUE_TOP, GLUE_H = 150, 200  # temperature bar y 150..350
# Pocket state codes (simulator, %MW71) -> bottle body and label band colours, and a word
POCKET_BODY = {lm.POCKET_EMPTY: (BG, "#9e9e9e"), lm.POCKET_BOTTLE: (PRODUCT, "#555555"),
               lm.POCKET_LABELLING: (PRODUCT, "#555555"), lm.POCKET_LABELLED: (PRODUCT, "#555555")}
POCKET_LABEL = {lm.POCKET_EMPTY: "none", lm.POCKET_BOTTLE: "none", lm.POCKET_LABELLING: "#d9d2bd",
                lm.POCKET_LABELLED: "#ffffff"}
LABELER_POCKET_WORDS = {lm.POCKET_EMPTY: "empty", lm.POCKET_BOTTLE: "bottle", lm.POCKET_LABELLING: "labelling",
                        lm.POCKET_LABELLED: "labelled"}
# Setpoints: (label, tag, unit, min, max), in the units FUXA shows (degC, scaled from the PLC's 0.1 degC).
# Chosen so a legal value never trips an alarm or loses a label (checked 2026-11-19 against the PLC
# constants and simulator.config):
#   setpoint - window >= 165 - 15 = 150 degC = GLUE_STICK_MIN_C (below it the label falls off);
#   setpoint + window <= 180 + 15 = 195 degC < GlueMax 200 degC (over-temperature FAULT);
#   heater band <= 5 < 6 <= window: Glue_Ready stays on while the heater cycles;
#   slowest warm-up 20 -> 180 - 6 = 174 degC takes about 51 s < HeatTimeout 120 s;
#   4 turns = 4 s at SPIN_TURN_S 1.0 s < LabelTimeout 5 s.
# The PLC defaults (170 / 5 / 10 degC, 2 turns) lie inside every range.
LABELER_SETPOINTS = (
    ("Glue setpoint", "GlueSetpoint", "°C", 165, 180),
    ("Heater band", "GlueHyst", "°C", 2, 5),
    ("Ready window ±", "GlueBand", "°C", 6, 15),
    ("Turns after cut", "PressTurns", "turns", 1, 4),
)
LABELER_MANUAL_BUTTONS = (("C2", "Man_C2"), ("Gate", "Man_Gate"), ("Bell", "Man_Clamp"), ("Turntable", "Man_Spin"),
                          ("Feed + glue", "Man_Feed"), ("Knife", "Man_Cut"), ("Pusher", "Man_Discharge"))
RESET_COUNTER = 4  # Cmd value: GoodCount -> 0 (labeler only; Reset = 3 keeps the count)
C3_X, C3_PITCH = 530, 22  # first C3 slots on the screen (all 8; the Line view shows C3 too, L4)


def slot_label(element_id: str, tag_id: str, x: float, y: float, w: float, h: float, bit: int) -> fs.Shape:
    """White label band over a conveyor slot's bottle, shown with the same slot bit (bitmask): every
    bottle on C3 left the labeler labelled (no inspection, no reject), so a bottle there always wears
    its label. Drawn after the slot, so it lies on top; transparent while the slot is empty."""
    return fs.rect_ranges(element_id, tag_id, x, y + h * 0.3, w, h * 0.35,
                          [fs.colour_range(0, 0, "none", "none"), fs.colour_range(1, 1, POCKET_LABEL[lm.POCKET_LABELLED], MUTED)],
                          default="none", bitmask=1 << bit)


def slot_cap(element_id: str, tag_id: str, x: float, y: float, w: float, bit: int) -> fs.Shape:
    """Cap on top of a conveyor slot's bottle (y = top of the bottle), shown with the same slot bit:
    every bottle on C2 and C3 left the filler capped (it only discharges a CAPPED pocket), so a bottle
    there always wears its cap. Transparent while the slot is empty."""
    cw, ch = w * 0.6, 6
    return fs.rect_ranges(element_id, tag_id, x + (w - cw) / 2, y - ch, cw, ch,
                          [fs.colour_range(0, 0, "none"), fs.colour_range(1, 1, CAP_COLOUR)],
                          default="none", bitmask=1 << bit)


def labeler_process(b: Builder) -> None:
    """C2 end and gate -> pocket on the turntable (bell above, disc below) -> pusher -> C3; label roll and
    web from the top left; melt tank as a temperature bar on the right."""
    m = ft.LABELER
    b.add(fs.text(16, 128, "Process", size=15, bold=True))
    b.add(f'<circle cx="96" cy="123" r="6" fill="{LAMP_ON}" stroke="{TEXT}" stroke-width="1.5"/>')  # legend
    b.add(fs.text(108, 127, "ON", size=12, color=MUTED))
    # C2 end and the gate
    b.add(fs.text(40, 318, "C2 from the filler", size=12, color=MUTED))
    b.add(fs.rect(40, 330, 210, 8, "#555555", rx=2))
    b.add(fs.rect_ranges("gate", L("GateMotor"), 252, 268, 8, 70,
                         [fs.colour_range(0, 0, "#555555"), fs.colour_range(1, 1, LAMP_ON, TEXT)], default="#555555"))
    lamp(b, "ConveyorMotor2", 48, 356, "C2 motor", m)
    lamp(b, "Bottle_At_Gate", 196, 356, "bottle at gate", m)
    lamp(b, "GateMotor", 48, 380, "gate open (pulse)", m)
    lamp(b, "Bottle_Entering", 196, 380, "bottle on its way", m)
    # label roll (turns while LabelFeed pulls the web, like the conveyor rollers) and web (white while the feed runs)
    b.add(fs.label_roll("label_roll", 190, 180, 34, L("LabelFeed")))
    b.add(fs.text(190, 236, "label roll", size=12, color=MUTED, anchor="middle"))
    pipe(b, "web_top", "LabelFeed", LAMP_ON, 190, 144, 116, 5, m)
    pipe(b, "web_down", "LabelFeed", LAMP_ON, 301, 144, 5, 146, m)
    pipe(b, "web_in", "LabelFeed", LAMP_ON, 301, 285, 29, 5, m)
    for k, (tag, label) in enumerate((("Roll_Low", "roll low"), ("Roll_Empty", "roll empty"))):  # left of the roll
        lamp(b, tag, 40, 150 + k * 24, label, m)
    for k, (tag, label) in enumerate((("Label_Mark", "label mark"), ("Cutter", "knife"), ("GlueRoller", "glue roller"))):
        lamp(b, tag, 318, 166 + k * 24, label, m)  # beside the web, where they act
    # pocket: bell above, bottle (body + label band), turntable below
    b.add(fs.rect_ranges("bell", L("Bell_Down"), 322, 226, 60, 14,
                         [fs.colour_range(0, 0, "#bdbdbd", TEXT), fs.colour_range(1, 1, "#555555", TEXT)],
                         default="#bdbdbd"))
    b.add(fs.rect_ranges("pocket_body", L("Anim_Station"), 330, 248, 44, 84,
                         [fs.colour_range(c, c, fill, line) for c, (fill, line) in POCKET_BODY.items()], default=BG))
    b.add(fs.rect_ranges("pocket_label", L("Anim_Station"), 330, 274, 44, 30,
                         [fs.colour_range(c, c, colour, "#555555" if colour != "none" else "none")
                          for c, colour in POCKET_LABEL.items()], default="none"))
    # cap on the pocket bottle (every bottle came capped from the filler), under the bell that clamps it
    b.add(fs.rect_ranges("pocket_cap", L("Anim_Station"), 340, 240, 24, 8,
                         [fs.colour_range(c, c, "none" if c == lm.POCKET_EMPTY else CAP_COLOUR)
                          for c in LABELER_POCKET_WORDS], default="none"))
    b.add(fs.impeller("turntable", 352, 352, 16, L("SpinMotor")))
    b.add(fs.enum_texts("pocket_word", L("Anim_Station"), LABELER_POCKET_WORDS, 352, 392, size=13, bold=True,
                        anchor="middle"))
    for k, (tag, label) in enumerate((("Bottle_In_Pocket", "bottle in pocket"), ("ClampValve", "bell valve"),
                                      ("Bell_Down", "bell down"), ("Label_Present", "label on bottle"),
                                      ("SpinMotor", "turntable"), ("Spin_Pulse", "turn pulse"))):
        lamp(b, tag, 400, 250 + k * 24, label, m)
    # pusher -> C3 (all 8 slot bits of Anim_C3; the Line view draws C3 too)
    b.add(fs.text(C3_X, 260, "C3 to the packer", size=12, color=MUTED))
    for i in range(8):
        b.add(fs.rect_ranges(f"lab_c3_slot{i}", L("Anim_C3"), C3_X + i * C3_PITCH, 290, 16, 40,
                             [fs.colour_range(0, 0, BG), fs.colour_range(1, 1, PRODUCT, "#333333")],
                             default=BG, bitmask=1 << i))
        b.add(slot_label(f"lab_c3_label{i}", L("Anim_C3"), C3_X + i * C3_PITCH, 290, 16, 40, i))
        b.add(slot_cap(f"lab_c3_cap{i}", L("Anim_C3"), C3_X + i * C3_PITCH, 290, 16, i))
    b.add(fs.rect(C3_X - 4, 332, 8 * C3_PITCH + 4, 6, "#555555", rx=2))
    lamp(b, "DischargeMotor", C3_X, 356, "pusher", m)
    lamp(b, "C3_Full", C3_X, 380, "C3 full", m)
    lamp(b, "Outfeed_Full", C3_X + 84, 380, "outfeed full", m)


def labeler_tank(b: Builder) -> None:
    """Melt tank: temperature bar 0..220 degC with a red tick at GlueMax, the setpoint as a thin bar."""
    m, x = ft.LABELER, 770
    b.add(fs.text(x + 40, 128, "Melt tank", size=14, bold=True, anchor="middle"))
    b.add(fs.progress("glue_temp", L("GlueTemp"), x, GLUE_TOP, 50, GLUE_H, 0, GLUE_SCALE_C, GLUE_COLOUR))
    b.add(fs.progress("glue_setpoint", L("GlueSetpoint"), x + 56, GLUE_TOP, 12, GLUE_H, 0, GLUE_SCALE_C, "#8a8a8a"))
    b.add(fs.text(x - 4, GLUE_TOP + 10, f"{GLUE_SCALE_C}", size=11, color=MUTED, anchor="end"))
    b.add(fs.text(x - 4, GLUE_TOP + GLUE_H, "0", size=11, color=MUTED, anchor="end"))
    y = GLUE_TOP + GLUE_H * (1 - GLUE_MAX_C / GLUE_SCALE_C)
    b.add(fs.rect(x - 6, y - 1, 80, 2, RED, rx=0, element_id="glue_max_tick"))
    b.add(fs.text(x + 78, y + 4, f"max {GLUE_MAX_C} °C", size=11, color="#a00000"))
    b.add(fs.value("val_GlueTemp", L("GlueTemp"), x + 40, GLUE_TOP + GLUE_H + 22, size=15))
    b.add(fs.text(x + 44, GLUE_TOP + GLUE_H + 22, "°C", size=13))
    b.add(fs.text(x + 74, GLUE_TOP + GLUE_H + 22, "grey bar = setpoint", size=11, color=MUTED))
    for k, (tag, label) in enumerate((("GlueHeater", "heater"), ("Heat_Demand", "heat wanted"),
                                      ("Glue_Ready", "glue ready"), ("Glue_Low", "glue low"))):
        lamp(b, tag, x + 92, 214 + k * 24, label, m)


def labeler_values(b: Builder) -> None:
    """Step, the labelled-bottle counter with its own reset, the glue temperature."""
    m, x, right = ft.LABELER, 990, 1225
    b.add(fs.text(x, 128, "Labeling", size=15, bold=True))
    b.add(fs.text(x, 158, "Step", size=13))
    b.add(fs.enum_texts("labeler_step", L("Step"), LABELER_STEP_NAMES, right + 30, 158, size=15, bold=True,
                        anchor="end"))
    b.add(fs.text(x, 188, "Labelled bottles", size=13))
    b.add(fs.value("val_labeler_GoodCount", L("GoodCount"), right, 188, size=15))
    # one write per click (I13): Cmd = 4, the PLC zeroes GoodCount and clears Cmd
    b.add(fs.button("labeler_reset_counter", "Reset counter", x, 198, 150, 32,
                    [fs.set_value(L("Cmd"), RESET_COUNTER)], background=NEUTRAL))
    for label, tag, y in (("Glue temperature", "GlueTemp", 262), ("Glue setpoint", "GlueSetpoint", 288)):
        b.add(fs.text(x, y, label, size=13))
        b.add(fs.value(f"val_labeler_{tag}", L(tag), right, y, size=15))
        b.add(fs.text(right + 5, y, "°C", size=12, color=MUTED))
    lamp(b, "Starved", x + 8, 318, "starved (no bottle 2 s / roll empty)", m)
    lamp(b, "Jog_Block", x + 8, 342, "manual blocked (jog watchdog)", m)
    for k, line in enumerate(("Reset counter zeroes the count (Cmd 4).", "Reset in the header clears alarms",
                              "and keeps the count.")):
        b.add(fs.text(x, 372 + k * 15, line, size=11, color=MUTED))


def labeler_setpoints(b: Builder) -> None:
    b.add(fs.rect(16, 420, 760, 280, "#dcdcdc", rx=6))
    b.add(fs.text(32, 450, "Setpoints", size=15, bold=True))
    m = ft.LABELER
    for k, (label, tag, unit, lo, hi) in enumerate(LABELER_SETPOINTS):
        x, y = 32, 485 + k * 42
        d = decimals(tag, m)
        b.add(fs.text(x, y, label, size=13))
        b.add(fs.number_input(f"in_labeler_{tag}", L(tag), x + 140, y - 21, 84, 30, lo, hi, step="any" if d else None))
        b.add(fs.text(x + 232, y, unit, size=13))
        b.add(fs.text(x + 280, y, f"{lo:.{d}f}–{hi:.{d}f}", size=11, color=MUTED))
    for k, line in enumerate((
        "Type a value and press Enter; values outside the range are refused.",
        "The heater switches on below setpoint − band and off at the setpoint (hysteresis).",
        "Glue is ready inside setpoint ± window; the window is always wider than the band, so the",
        "glue stays ready while the heater cycles. Turns after cut press the label tail on.",
    )):
        b.add(fs.text(32, 638 + k * 17, line, size=12, color=MUTED))


def labeler_manual(b: Builder) -> None:
    b.add(fs.rect(792, 420, 472, 280, "#dcdcdc", rx=6))
    b.add(fs.text(808, 450, "Manual (hold to run)", size=15, bold=True))
    b.add(fs.conditional_text("labeler_manual_hint", L("ModeReq"), 0, MANUAL - 1, 808, 500,
                              "The buttons appear in Manual mode (header).", size=13, color=MUTED))
    for i, (label, tag) in enumerate(LABELER_MANUAL_BUTTONS):
        x, y = 808 + (i % 4) * 112, 470 + (i // 4) * 58
        b.add(fs.hold_button(f"labeler_{tag}", label, x, y, 108, 48, L(tag), L("ModeReq"), MANUAL, MANUAL))
    b.add(fs.conditional_text("labeler_manual_blocked", L("Jog_Block"), 1, 1, 808, 598,
                              "Manual blocked: a button was held > 10 s. Release all.", size=13, bold=True,
                              color="#a00000"))
    for k, line in enumerate(("Interlocks (the PLC checks them, also in Manual):",
                              "gate: pocket empty · bell: a bottle in the pocket",
                              "turntable, knife: bell down · feed + glue: bell down, glue ready",
                              "pusher: bell up, C3 not full · heater: automatic in every mode",
                              "A button acts only while it is held.")):
        b.add(fs.text(808, 620 + k * 17, line, size=12, color=TEXT if k == 0 else MUTED))


def labeler_view() -> dict:
    b = Builder()
    header(b, HEADER_KEY[LABELER_MAIN_ID], LABELER_MAIN_ID)
    labeler_process(b)
    labeler_tank(b)
    labeler_values(b)
    labeler_setpoints(b)
    labeler_manual(b)
    return fs.view(LABELER_MAIN_ID, LABELER_MAIN_NAME, W, H, b.parts, b.items)


# --- Line overview (stage 4; syrup room block S4) ---------------------------------
# Report 8.3: FUXA polls every 200 ms, too slow for smooth motion, so a conveyor is 8 slots and
# the simulator writes one bit per slot (Anim_C1/C2, bit 0 = entry ... bit 7 = exit). Each slot is
# a bottle shape bound to ONE bit (FUXA bitmask), so bottles step along and visibly pile up.
# The rollers turn while the conveyor's motor output is ON.
SLOT_PITCH, SLOT_W, SLOT_H = 30, 22, 46  # 8 slots = 240 px: C1, C2 and C3 fit in one row (HMI L4)
BELT_Y = 360  # top of the belt band
STANDIN = "#bdbdbd"
# Syrup room block (S4): above the filler block (y 270), same x and width, so the product pipe
# runs straight down between the two centres (y 226..270)
SYRUP_BLOCK_Y, SYRUP_BLOCK_H = 108, 118
# One row (HMI L4): x of every part of the Line view, left to right
C1_X, FILLER_X, BLOCK_W, C2_X = 108, 364, 200, 580
LABELER_X, LABELER_W, C3_LINE_X, PACKER_X = 836, 160, 1012, 1140


def no_click(svg: str) -> str:
    """Let clicks pass through a drawing on top of a clickable block."""
    return svg.replace("<rect ", '<rect pointer-events="none" ', 1)


def click_through(svg: str) -> str:
    """The same for a group shape (e.g. fs.value); SVG children inherit pointer-events."""
    return svg.replace("<g ", '<g pointer-events="none" ', 1)


def conveyor(b: Builder, key: str, x: float, label: str, anim: str, motor: str | None, bottle: str,
             anim_m: ft.Machine = ft.FILLER, motor_m: ft.Machine = ft.FILLER, capped: bool = False,
             labelled: bool = False) -> None:
    """8 slots + belt + 3 rollers. motor None = driven by a machine that is not built (static rollers).
    anim_m / motor_m: the PLC whose memory holds the slot word (the simulator writes it there) and the
    PLC that drives the motor; for C2 they differ (slots in the filler PLC, motor from the labeler).
    capped: every bottle wears a cap (C2 and C3, after the filler).
    labelled: every bottle wears a white label band (C3, after the labeler).
    One call per conveyor, so a later machine's conveyor is one more call (issue I11)."""
    b.add(fs.text(x, BELT_Y - 90, label, size=13, bold=True))
    b.add(fs.rect(x - 4, BELT_Y, 8 * SLOT_PITCH + 4, 10, "#555555", rx=2))
    for i in range(8):
        b.add(fs.rect_ranges(f"{key}_slot{i}", anim_m.tag_id(anim), x + i * SLOT_PITCH, BELT_Y - SLOT_H - 2,
                             SLOT_W, SLOT_H, [fs.colour_range(0, 0, BG), fs.colour_range(1, 1, bottle, "#333333")],
                             default=BG, bitmask=1 << i))
        if capped:
            b.add(slot_cap(f"{key}_cap{i}", anim_m.tag_id(anim), x + i * SLOT_PITCH, BELT_Y - SLOT_H - 2, SLOT_W, i))
        if labelled:
            b.add(slot_label(f"{key}_label{i}", anim_m.tag_id(anim), x + i * SLOT_PITCH, BELT_Y - SLOT_H - 2,
                             SLOT_W, SLOT_H, i))
    for k in range(3):
        b.add(fs.roller(f"{key}_roller{k}", x + 14 + k * 105, BELT_Y + 24, 11,
                        motor_m.tag_id(motor) if motor else None))


def standin_block(b: Builder, x: float, y: float, w: float, h: float, lines: tuple[str, ...]) -> None:
    """A machine that does not exist yet: grey, no tag (the simulator stands in for it)."""
    b.add(fs.rect(x, y, w, h, STANDIN, rx=6))
    for k, text in enumerate(lines):
        b.add(fs.text(x + w / 2, y + 24 + k * 18, text, size=13 if k == 0 else 11, bold=k == 0,
                      anchor="middle", color=TEXT if k == 0 else MUTED))


def machine_block(b: Builder, key: str, x: float, y: float, w: float, h: float, name: str, target: str,
                  m: ft.Machine = ft.FILLER) -> None:
    """A real machine: coloured from its State (report 8.2), click opens its detail screen."""
    T = m.tag_id  # this machine's tags
    b.add(fs.rect_ranges(f"{key}_block", T("State"), x, y, w, h,
                         [fs.colour_range(v, v, c) for v, c in STATE_COLOURS.items()], default=GREY,
                         events=[fs.goto(target)]))
    b.add(fs.text(x + w / 2, y + 26, name, size=17, bold=True, anchor="middle", click_through=True))
    b.add(fs.enum_texts(f"{key}_state", T("State"), STATE_NAMES, x + w / 2, y + 46, size=13, anchor="middle",
                        click_through=True))
    b.add(fs.text(x + w / 2, y + h - 10, "click for details", size=11, anchor="middle", color=TEXT,
                  click_through=True))


def line_view(view_id: str = LINE_ID) -> dict:
    """The line overview; view_id picks the machine of header row 2 (same body for every machine).
    Real machines (filler, syrup room since S4, labeler since L4) are blocks coloured by their State; a
    click opens their screen. Machines not built yet (blow moulder, packer) are grey stand-ins.
    One row, left to right (decision 2026-11-19): blow moulder 16-96 | C1 108-348 | filler 364-564 with the
    syrup room above it | C2 580-820 | labeler 836-996 | C3 1012-1252 | packer stand-in under the end of C3."""
    b = Builder()
    header(b, HEADER_KEY[view_id], view_id)
    b.add(fs.text(16, 128, "Line overview", size=15, bold=True))
    # blow moulder (stand-in) -> C1
    standin_block(b, 16, 300, 80, 100, ("Blow", "moulder", "simulated"))
    conveyor(b, "c1", C1_X, "C1 infeed", "Anim_C1", "ConveyerMotor1", "#ffffff")
    lamp(b, "S2", C1_X + 8 * SLOT_PITCH - 40, BELT_Y + 56, "S2")
    # filler: coloured block, mini pockets, index belt rollers
    fx, fy, fw, fh = FILLER_X, 270, BLOCK_W, 170
    machine_block(b, "filler", fx, fy, fw, fh, "Filler", MAIN_ID)
    for k, (_name, anim, _lamps) in enumerate(POCKETS):
        ranges = [fs.colour_range(code, code, fill, line) for code, (fill, line) in POCKET_COLOURS.items()]
        svg, items = fs.rect_ranges(f"filler_pocket{k}", T(anim), fx + 16 + k * 46, fy + 62, 30, 50, ranges, default=BG)
        b.add((no_click(svg), items))
    for k in range(2):
        svg, items = fs.roller(f"filler_roller{k}", fx + 50 + k * 100, fy + 128, 9, T("IndexMotor"))
        b.add((svg.replace("<path ", '<path pointer-events="none" ', 1), items))
    # syrup room (S4): the real syrup PLC as a block above the filler, coloured by its State, with
    # its step and buffer level (the buffer is why Product_Available comes and goes)
    sy, sh = SYRUP_BLOCK_Y, SYRUP_BLOCK_H
    machine_block(b, "syrup", fx, sy, fw, sh, "Syrup room", SYRUP_MAIN_ID, ft.SYRUP)
    b.add(fs.enum_texts("syrup_step_line", S("Step"), SYRUP_STEP_NAMES, fx + fw / 2, sy + 66, size=13,
                        bold=True, anchor="middle", click_through=True))
    b.add(fs.text(fx + fw / 2 - 8, sy + 90, "Buffer", size=12, anchor="end", click_through=True))
    svg, items = fs.value("line_buffer", S("BufferLevel"), fx + fw / 2 + 40, sy + 90, size=13)
    b.add((click_through(svg), items))
    b.add(fs.text(fx + fw / 2 + 44, sy + 90, "L", size=12, click_through=True))
    # product pipe from the SOURCE: the syrup PLC's output Product_Available (%MX6.0)
    top = sy + sh
    b.add(fs.rect_ranges("product_pipe", S("Product_Available"), fx + fw / 2 - 5, top, 10, fy - top,
                         [fs.colour_range(0, 0, STANDIN), fs.colour_range(1, 1, PRODUCT)], default=STANDIN))
    b.add(fs.conditional_text("no_product", S("Product_Available"), 0, 0, fx + fw / 2 + 12, top + 20,
                              "no product", size=12, bold=True, color="#a00000"))
    # the other end of the wire: the filler's input (the simulator copies the signal, emulating
    # the hardwiring). Pipe brown + lamp off = the signal does not reach the filler.
    lamp(b, "Product_Available", fx + 20, top + 24, "filler input")
    # C2: its slots are written into the filler PLC, its motor is the labeler's ConveyorMotor2 (L4)
    conveyor(b, "c2", C2_X, "C2 (driven by the labeler)", "Anim_C2", "ConveyorMotor2", PRODUCT,
             motor_m=ft.LABELER, capped=True)
    lamp(b, "S7", C2_X + 6, BELT_Y + 56, "S7 C2 full")
    lamp(b, "Outfeed_Full", C2_X + 120, BELT_Y + 56, "outfeed full")
    # labeler (L4): the real labeler PLC as a block, coloured by its State, with its step and glue
    # temperature (the reason it waits in WARM_UP); click opens the Labeler screen
    lx = LABELER_X
    machine_block(b, "labeler", lx, fy, LABELER_W, fh, "Labeler", LABELER_MAIN_ID, ft.LABELER)
    b.add(fs.enum_texts("labeler_step_line", L("Step"), LABELER_STEP_NAMES, lx + LABELER_W / 2, fy + 70, size=13,
                        bold=True, anchor="middle", click_through=True))
    b.add(fs.text(lx + LABELER_W / 2 - 8, fy + 96, "Glue", size=12, anchor="end", click_through=True))
    svg, items = fs.value("line_glue", L("GlueTemp"), lx + LABELER_W / 2 + 44, fy + 96, size=13)
    b.add((click_through(svg), items))
    b.add(fs.text(lx + LABELER_W / 2 + 48, fy + 96, "°C", size=12, click_through=True))
    # C3 (driven by the packer stand-in, no PLC output: static rollers) -> packer (stand-in)
    conveyor(b, "c3", C3_LINE_X, "C3 (driven by the packer)", "Anim_C3", None, PRODUCT, anim_m=ft.LABELER,
             capped=True, labelled=True)
    lamp(b, "C3_Full", C3_LINE_X + 6, BELT_Y + 56, "C3 full", ft.LABELER, "lamp_labeler_C3_Full")
    lamp(b, "Outfeed_Full", C3_LINE_X + 96, BELT_Y + 56, "outfeed full", ft.LABELER, "lamp_labeler_Outfeed_Full")
    standin_block(b, PACKER_X, 450, 1264 - PACKER_X, 84, ("Packer", "simulated", "(takes bottles)"))
    # counters and legend
    b.add(fs.text(16, 500, "Good bottles", size=14))
    b.add(fs.value("line_goodcount", T("GoodCount"), 180, 500, size=16))
    b.add(fs.text(220, 500, "Labelled", size=14))
    b.add(fs.value("line_labeler_goodcount", L("GoodCount"), 330, 500, size=16))
    b.add(fs.rect(16, 540, 14, 24, "#ffffff", rx=2, stroke="#333333"))
    b.add(fs.text(38, 557, "bottle on a belt (one per slot; the simulator writes the slot bits)", size=12, color=MUTED))
    b.add(fs.roller("legend_roller", 23, 590, 8, None))
    b.add(fs.text(38, 594, "a turning roller = conveyor motor ON", size=12, color=MUTED))
    b.add(fs.rect(16, 612, 14, 14, STANDIN, rx=2))
    b.add(fs.text(38, 624, "grey block = machine not built yet, simulated", size=12, color=MUTED))
    return fs.view(view_id, VIEW_NAMES[view_id], W, H, b.parts, b.items)


# --- Alarms (stage 5; syrup room S5) --------------------------------------------------
# One FUXA alarm per PLC alarm bit (report 9.3): bit -> (FUXA priority, text). Priorities:
# highhigh (red) = the bit puts the machine into FAULT, high (orange) = comm lost, low (yellow) =
# warning. The names are fixed ("<machine key>_<bit>"): FUXA keys its alarm history by name, a
# rename would orphan it; the prefix also keeps the two Alm_CommLost apart.
ALARMS = {
    "Alm_IndexJam": ("highhigh", "Index belt jam: no S8 pulse within 5 s in INDEX (FAULT)"),
    "Alm_FillTimeout": ("highhigh", "Fill timeout: setpoint not reached within 10 s (FAULT)"),
    "Alm_CommLost": ("high", "Plant heartbeat lost for 3 s (simulator link)"),
    "Alm_OutfeedBlocked": ("low", "Outfeed blocked longer than 2 min (warning, no FAULT)"),
}
# Causes: docs/syrup/ladder-roadmap.md stage 7 (MixMax 950 L, BufferMax 1950 L, PressMax 6.00 bar,
# CoolTimeout / DoseTimeout 60 s). LowSyrup is not latched: it is the Starved state, not a fault.
SYRUP_ALARMS = {
    "Alm_Overflow": ("highhigh", "Overflow: mixing tank ≥ 950 L or high-high switch, or buffer ≥ 1950 L (FAULT)"),
    "Alm_OverPressure": ("highhigh", "Over-pressure: CO2 ≥ 6.00 bar in the mixing tank (FAULT)"),
    "Alm_CoolTimeout": ("highhigh", "Cooling timeout: cool setpoint not reached within 60 s in COOL (FAULT)"),
    "Alm_DoseTimeout": ("highhigh", "Dosing timeout: water or syrup level not reached within 60 s "
                                    "(pump failure, FAULT)"),
    "Alm_CommLost": ("high", "Plant heartbeat lost for 3 s (simulator link)"),
    "Alm_LowSyrup": ("low", "Low syrup: not enough for the next batch (warning, Starved; clears after refill)"),
}
# Causes: docs/labeler/io-list.md + state-diagram.md (LabelTimeout 5 s, ClampTimeout 2 s, OutTimeout 3 s,
# HeatTimeout 120 s, GlueMax 200.0 degC). LabelEmpty is not latched (Starved, clears after the roll change).
LABELER_ALARMS = {
    "Alm_LabelTimeout": ("highhigh", "Label timeout: no label mark within 5 s in WRAP (web torn) or turns not "
                                     "reached within 5 s in PRESS (FAULT)"),
    "Alm_PocketJam": ("highhigh", "Pocket jam: bottle not in the pocket, bell not moved within 2 s, or not "
                                  "discharged within 3 s (FAULT)"),
    "Alm_GlueOverTemp": ("highhigh", "Glue over-temperature: 200 °C reached, heater off (FAULT; Reset below 200 °C)"),
    "Alm_GlueHeatTimeout": ("highhigh", "Glue heat-up timeout: not ready within 120 s of WARM_UP (heater failed, FAULT)"),
    "Alm_CommLost": ("high", "Plant heartbeat lost for 3 s (simulator link)"),
    "Alm_OutfeedBlocked": ("low", "Outfeed blocked longer than 2 min: C3 full (warning, no FAULT)"),
    "Alm_LabelEmpty": ("low", "Label roll empty: no new bottle (warning, Starved; clears after the roll change)"),
    # FUXA-only warnings straight on sensor bits (decision 2026-11-15: no PLC alarm bit, nothing latched)
    "Roll_Low": ("low", "Label roll low: prepare the next roll (warning)"),
    "Glue_Low": ("low", "Glue low in the melt tank: add glue blocks (warning)"),
}
# Labeler warnings on sensor bits: FUXA alarms, but no header chip (row 2 is full with the 7 PLC bits)
SENSOR_WARNINGS = {"labeler": ("Roll_Low", "Glue_Low")}
ALARM_TEXTS = {FILLER_UI.m.key: ALARMS, SYRUP_UI.m.key: SYRUP_ALARMS,  # machine key -> its alarms
               LABELER_UI.m.key: LABELER_ALARMS}


def alarm_name(bit: str, m: ft.Machine = ft.FILLER) -> str:
    return f"{m.key}_{bit}"


def alarm_definitions() -> list[dict]:
    """All machines; group = the machine's title (the Machine column of the alarm list). Dict order,
    not address order: it keeps the filler definitions unchanged in the export."""
    return [fs.alarm(alarm_name(bit, ui.m), ui.m.tag_id(bit), priority, text, ui.title)
            for ui in UIS for bit, (priority, text) in ALARM_TEXTS[ui.m.key].items()]


def alarms_view(view_id: str = ALARMS_ID) -> dict:
    """FUXA's active alarm list of the WHOLE line (report 8.1: from all PLCs; unfiltered, the column
    Machine is the alarm's group); Reset goes to the machine of header row 2 (view_id)."""
    ui = CONTEXT[view_id]
    b = Builder()
    header(b, HEADER_KEY[view_id], view_id)
    b.add(fs.text(16, 128, "Active alarms", size=15, bold=True))
    b.add(fs.alarm_table("alarms_active", 16, 140, 1248, 230, "alarms",
                         [("ontime", "Time", 170), ("group", "Machine", 120), ("type", "Priority", 100),
                          ("text", "Alarm", 520), ("status", "Status", 140), ("ack", "Ack", 80)]))
    b.add(fs.button("alarms_reset", "Reset", 16, 392, 140, 44, [fs.set_value(ui.m.tag_id("Cmd"), 3)],
                    background=NEUTRAL))
    for k, line in enumerate((
        "Ack (the round button in a row) = \"I have seen this alarm\". FUXA keeps every alarm in this list until it",
        "is acknowledged, also one that came and went, so nothing is missed. Ack does not change the PLC.",
        f"Reset = the {ui.title.lower()} PLC (picked in the header) clears its alarm bits, but only those whose",
        "cause is gone. After a FAULT the machine also needs Start. Colours: red = FAULT, orange = high, yellow = warning.",
        f"The list shows the alarms of every machine (column Machine); Reset acts on the {ui.title.lower()} only "
        "(other machine: picker in the header).",
    )):
        b.add(fs.text(176, 406 + k * 20, line, size=13, color=TEXT if k in (0, 2) else MUTED))
    b.add(fs.text(16, 520, "Alarm history", size=15, bold=True))
    b.add(fs.text(16, 548, "Menu \u2630 (top left) \u2192 Alarm history \u2192 \u22ee \u2192 History Alarms \u2192 pick the dates "
                  "\u2192 Apply. It lists every alarm with its on, off and ack time.", size=13))
    b.add(fs.text(16, 572, "(FUXA's own alarm panel; FUXA 1.3.5 cannot show the history in a table on a view.)",
                  size=12, color=MUTED))
    return fs.view(view_id, VIEW_NAMES[view_id], W, H, b.parts, b.items)


def apply_alarms(project: dict) -> list[str]:
    """Replace the generated alarm definitions (all machines), keep alarms made in the editor."""
    ours = alarm_definitions()
    names = {a["name"] for a in ours}
    kept = [a for a in project.get("alarms", []) if a["name"] not in names]
    project["alarms"] = kept + ours
    return [f"{len(ours)} alarms"]


def side_menu() -> list[dict]:
    """FUXA's side menu (top-left button): the views plus FUXA's alarm panel with the history."""
    icons = {LINE_ID: "linear_scale", MAIN_ID: "local_drink", SYRUP_MAIN_ID: "opacity", LABELER_MAIN_ID: "label",
             ALARMS_ID: "notifications",
             TRENDS_ID: "show_chart", TAGS_ID: "list"}
    items = [{"id": f"nav_{key}", "text": name, "view": view_id, "icon": icons[view_id]}
             for view_id, name in NAV for key in [HEADER_KEY[view_id]]]
    return items + [{"id": "nav_syrup_tags", "text": SYRUP_TAGS_NAME, "view": SYRUP_TAGS_ID, "icon": "list"},
                    {"id": "nav_labeler_tags", "text": LABELER_TAGS_NAME, "view": LABELER_TAGS_ID, "icon": "list"},
                    {"id": "nav_alarm_panel", "text": "Alarm history", "view": ALARM_PANEL, "icon": "history"}]


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


# Syrup room (S6): a batch is ~151 s, so 15 min show about six batches and a whole buffer cycle
SYRUP_CHARTS = (
    ("ch_syrup_levels", "Tank levels (L)", 15, [("MixLevel", "Mixing tank L", PRODUCT),
                                               ("BufferLevel", "Buffer tank L", DARK_BLUE)]),
    ("ch_syrup_temp", "Product temperature (°C)", 15, [("ProductTemp", "Temperature °C", DARK_BLUE),
                                                       ("CoolSetpoint", "Setpoint °C", "#8a8a8a")]),
    ("ch_syrup_press", "CO2 pressure (bar)", 15, [("CO2Pressure", "Pressure bar", DARK_BLUE),
                                                  ("PressSetpoint", "Setpoint bar", "#8a8a8a")]),
    ("ch_syrup_state", "Syrup room state", 30, [("State", "State", TEXT)]),
)
# Labeler (L6): a warm-up takes about a minute, so 15 min show it and the heater cycling after it
LABELER_CHARTS = (
    ("ch_labeler_glue", "Glue temperature (°C)", 15, [("GlueTemp", "Glue °C", DARK_BLUE),
                                                      ("GlueSetpoint", "Setpoint °C", "#8a8a8a")]),
    ("ch_labeler_count", "Labelled bottles", 30, [("GoodCount", "Labelled bottles", TEXT)]),
    ("ch_labeler_state", "Labeler state", 30, [("State", "State", TEXT)]),
)
MACHINE_CHARTS = {FILLER_UI.m.key: CHARTS, SYRUP_UI.m.key: SYRUP_CHARTS,  # machine key -> its charts
                  LABELER_UI.m.key: LABELER_CHARTS}
SYRUP_CHART_PLACES = {"ch_syrup_levels": (16, 106, 616, 282), "ch_syrup_temp": (648, 106, 616, 282),
                      "ch_syrup_press": (16, 396, 616, 250), "ch_syrup_state": (648, 396, 616, 250)}
SYRUP_CHART_DECIMALS = {"ch_syrup_temp": 1, "ch_syrup_press": 2}  # legend shows 4.5 degC / 3.25 bar


def charts() -> list[dict]:
    """Every machine's charts; a line names its tag by id and its device by name."""
    return [{"id": chart_id, "name": title,
             "lines": [fs.chart_line(ui.m.tag_id(tag), ui.m.name, label, colour) for tag, label, colour in lines]}
            for ui in UIS for chart_id, title, _window, lines in MACHINE_CHARTS[ui.m.key]]


def trends_view(view_id: str = TRENDS_ID) -> dict:
    """The trend charts of the machine of header row 2 (view_id)."""
    b = Builder()
    header(b, HEADER_KEY[view_id], view_id)
    if CONTEXT[view_id] is SYRUP_UI:
        syrup_trends(b)
        return fs.view(view_id, VIEW_NAMES[view_id], W, H, b.parts, b.items)
    if CONTEXT[view_id] is LABELER_UI:
        labeler_trends(b)
        return fs.view(view_id, VIEW_NAMES[view_id], W, H, b.parts, b.items)
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
    return fs.view(view_id, VIEW_NAMES[view_id], W, H, b.parts, b.items)


def syrup_trends(b: Builder) -> None:
    """S6: levels and temperature on top, pressure and state below (2 x 2)."""
    for chart_id, _title, window, _lines in SYRUP_CHARTS:
        options = fs.chart_options(window, **({"decimalsPrecision": SYRUP_CHART_DECIMALS[chart_id]}
                                              if chart_id in SYRUP_CHART_DECIMALS else {}))
        b.add(fs.chart(chart_id.replace("ch_", "trend_"), chart_id, *SYRUP_CHART_PLACES[chart_id], options))
    key = " \u00b7 ".join(f"{number} {name}" for number, name in STATE_NAMES.items())
    b.add(fs.text(648, 668, f"State: {key}", size=13))
    # x = 64: FUXA's round menu button covers the bottom-left corner; lines end before x 640
    b.add(fs.text(64, 668, "Normal batch: the mixing tank fills (water, then syrup), cools to", size=13))
    b.add(fs.text(64, 686, "its setpoint, gets CO2 up to its setpoint, then goes to the buffer.", size=13))
    b.add(fs.text(64, 706, f"Last {SYRUP_CHARTS[0][2]} min, state {SYRUP_CHARTS[3][2]} min. FUXA stores °C / bar "
                  "as shown (scaled first).", size=12, color=MUTED))


LABELER_CHART_PLACES = {"ch_labeler_glue": (16, 106, 1248, 300), "ch_labeler_count": (16, 414, 616, 250),
                        "ch_labeler_state": (648, 414, 616, 250)}  # the filler's layout


def labeler_trends(b: Builder) -> None:
    """L6: glue temperature across the top, labelled bottles (left) and state (right) below it."""
    for chart_id, _title, window, _lines in LABELER_CHARTS:
        options = fs.chart_options(window, **({"decimalsPrecision": 1} if chart_id == "ch_labeler_glue" else {}))
        b.add(fs.chart(chart_id.replace("ch_", "trend_"), chart_id, *LABELER_CHART_PLACES[chart_id], options))
    key = " · ".join(f"{number} {name}" for number, name in STATE_NAMES.items())
    b.add(fs.text(648, 684, f"State: {key}", size=13))
    # x = 64: FUXA's round menu button covers the bottom-left corner; lines end before x 640
    b.add(fs.text(64, 668, "Normal: the glue warms up to its setpoint in under a minute,", size=13))
    b.add(fs.text(64, 686, "then the heater keeps it in a narrow band (saw tooth).", size=13))
    b.add(fs.text(64, 706, f"Last {LABELER_CHARTS[0][2]} min (glue), {LABELER_CHARTS[1][2]} min (count, state). "
                  "FUXA stores °C as shown (scaled first).", size=12, color=MUTED))


def apply_charts(project: dict) -> list[str]:
    """Replace the charts of every machine, keep charts made in the editor."""
    ours = charts()
    ids = {c["id"] for c in ours}
    kept = [c for c in project.get("charts", []) if c["id"] not in ids]
    project["charts"] = kept + ours
    return [f"{len(ours)} charts"]


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


def value_id(name: str, m: ft.Machine = ft.FILLER) -> str:
    return f"VAL_{m.key}_{name}"


def tags_view(m: ft.Machine = ft.FILLER, view_id: str = TAGS_ID, view_name: str = TAGS_NAME,
              header_key: str = HEADER_KEY[TAGS_ID]) -> dict:
    """Every tag of machine m as a plain live value, one column per group (description prefix)."""
    b = Builder()
    header(b, header_key, view_id)  # row 2 = CONTEXT[view_id], the machine of this page
    b.add(fs.text(20, HEADER_H + 38, f"{m.name} – all tags (test page, read only)", size=20, bold=True))
    for col, (title, prefixes) in enumerate(COLUMNS):
        x = 20 + col * COL_W
        b.add(fs.text(x, TOP, title, bold=True))
        row = 0
        for name, _area, _number, _kind, description in m.tags:
            if description.split(":")[0] not in prefixes:
                continue
            y = TOP + 30 + row * ROW_H
            b.add(fs.text(x, y, name, size=13))
            b.add(fs.value(value_id(name, m), m.tag_id(name), x + COL_W - 20, y))
            row += 1
    return fs.view(view_id, view_name, W, H, b.parts, b.items)


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
    project["hmi"]["views"] = kept + [line_view(), filler_view(), alarms_view(), trends_view(), tags_view(),
                                      tags_view(ft.SYRUP, SYRUP_TAGS_ID, SYRUP_TAGS_NAME, HEADER_KEY[SYRUP_TAGS_ID]),
                                      line_view(SYRUP_LINE_ID), alarms_view(SYRUP_ALARMS_ID),
                                      trends_view(SYRUP_TRENDS_ID), syrup_view(),
                                      tags_view(ft.LABELER, LABELER_TAGS_ID, LABELER_TAGS_NAME,
                                                HEADER_KEY[LABELER_TAGS_ID]),
                                      line_view(LABELER_LINE_ID), alarms_view(LABELER_ALARMS_ID),
                                      trends_view(LABELER_TRENDS_ID), labeler_view()]
    project["hmi"]["layout"].setdefault("navigation", {})["items"] = side_menu()
    project["hmi"]["layout"]["start"] = LINE_ID  # the overview is the main screen (report 8.1)
    return changes
