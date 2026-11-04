"""Builders for FUXA 1.3.5 view shapes (pure functions, no FUXA, no PLC).

A FUXA view is an SVG drawing plus a dict of "items". An item tells FUXA what to
do with the SVG element that has the same id (bind it to a tag, colour it, react
to a click). Every builder here returns both parts: (svg_markup, {id: item}).
The formats were read from FUXA's demo project and its client code
(client/dist/main*.js); they are what the editor writes when a shape is dragged
onto a view and configured in its property dialog.

Used mechanisms:
- Value (svg-ext-value): FUXA writes the tag value into the <text>.
- Shapes with ranges (svg-ext-shapes-rect): fill/stroke colour from the value,
  ranges = [{min, max, color, stroke}].
- Show/hide actions (svg-ext-shapes-text): a text is shown while the tag is in
  its range and hidden otherwise. FUXA's Value shape cannot turn a number into a
  word, so a step name is one text per value, only the matching one visible.
  SVG.js show() clears the inline display, so every text starts hidden.
- HTML button (svg-ext-html_button): click events write a value to a tag
  (onSetValue) or open a view (onpage); ranges colour the button from a tag.

Ids are fixed strings so a second generator run gives the same file (screens and
tests refer to them).
"""

INT16_MIN, INT16_MAX = -32768, 32767
FONT = 'font-family="sans-serif"'

Shape = tuple[str, dict]


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x: float, y: float, content: str, size: int = 14, bold: bool = False, color: str = "#1d1d1d",
         anchor: str = "start", element_id: str | None = None, hidden: bool = False,
         click_through: bool = False) -> str:
    """Static text (no item). With element_id it can be the target of an item.
    click_through: clicks go to the shape below (e.g. a clickable machine block)."""
    attrs = [f'x="{x}"', f'y="{y}"', FONT, f'font-size="{size}"', f'text-anchor="{anchor}"', f'fill="{color}"',
             'xml:space="preserve"']
    if bold:
        attrs.append('font-weight="bold"')
    if element_id:
        attrs.insert(0, f'id="{element_id}"')
    if hidden:
        attrs.append('style="display:none"')
    if click_through:
        attrs.append('pointer-events="none"')
    return f"<text {' '.join(attrs)}>{esc(content)}</text>"


def rect(x: float, y: float, w: float, h: float, fill: str, element_id: str | None = None, rx: int = 4,
         stroke: str = "none") -> str:
    ident = f'id="{element_id}" ' if element_id else ""
    return f'<rect {ident}x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}"/>'


def value(element_id: str, tag_id: str, x: float, y: float, size: int = 13, anchor: str = "end",
          color: str = "#000000", bold: bool = True) -> Shape:
    """Live value of a tag (FUXA Value shape)."""
    weight = ' font-weight="bold"' if bold else ""
    svg = (f'<g id="{element_id}" type="svg-ext-value" text-anchor="{anchor}" {FONT} font-size="{size}" '
           f'fill="none" stroke-width="0"><text id="{element_id}_t" x="{x}" y="{y}" text-anchor="{anchor}" {FONT} '
           f'font-size="{size}"{weight} fill="{color}" xml:space="preserve">##.##</text></g>')
    item = {"id": element_id, "type": "svg-ext-value", "name": element_id, "label": "Value",
            "property": {"variableId": tag_id, "events": [], "actions": [], "ranges": []}}
    return svg, {element_id: item}


def colour_range(lo: int, hi: int, color: str, stroke: str | None = None) -> dict:
    return {"min": lo, "max": hi, "color": color, "stroke": stroke or color}


def rect_ranges(element_id: str, tag_id: str, x: float, y: float, w: float, h: float, ranges: list[dict],
                default: str = "#bdbdbd", bitmask: int | None = None, events: list[dict] | None = None,
                stroke: str = "none") -> Shape:
    """Rectangle whose colour follows a tag (FUXA Shapes with ranges).

    bitmask: compare only one bit of the tag. FUXA then uses (value & bitmask) ? 1 : 0 against the
    ranges (No.checkBitmask), so ranges 0 and 1 colour one conveyor slot from Anim_C1.
    events: e.g. [goto(view)] to open a screen when the rectangle is clicked."""
    prop = {"variableId": tag_id, "ranges": ranges, "events": events or [], "actions": []}
    if bitmask is not None:
        prop["bitmask"] = bitmask
    item = {"id": element_id, "type": "svg-ext-shapes-rect", "name": element_id, "label": "Shapes", "property": prop}
    svg = rect(x, y, w, h, default, element_id, stroke=stroke)
    if events:
        svg = svg.replace("<rect ", '<rect style="cursor:pointer" ', 1)
    return svg, {element_id: item}


def roller(element_id: str, cx: float, cy: float, r: float, motor_tag: str | None) -> Shape:
    """Belt roller: a circle with a cross (a plain circle shows no rotation). With motor_tag it turns
    while the tag is 1 (FUXA action clockwise, svg.js rotate loop) and stops at 0; without, static."""
    d = (f"M{cx - r},{cy} a{r},{r} 0 1,0 {2 * r},0 a{r},{r} 0 1,0 {-2 * r},0 "
         f"M{cx - r},{cy} L{cx + r},{cy} M{cx},{cy - r} L{cx},{cy + r}")
    svg = f'<path id="{element_id}" d="{d}" fill="#9e9e9e" stroke="#3a3a3a" stroke-width="1.5"/>'
    actions = []
    if motor_tag:
        actions = [{"variableId": motor_tag, "type": "clockwise", "range": {"min": 1, "max": 1}, "options": {}},
                   {"variableId": motor_tag, "type": "stop", "range": {"min": 0, "max": 0}, "options": {}}]
    item = {"id": element_id, "type": "svg-ext-shapes-path", "name": element_id, "label": "Shapes",
            "property": {"events": [], "actions": actions}}
    return svg, {element_id: item}


def show_hide_actions(tag_id: str, lo: int, hi: int) -> list[dict]:
    """Show while lo <= value <= hi, hide for every other INT16 value (two hide ranges)."""
    actions = [{"variableId": tag_id, "type": "show", "range": {"min": lo, "max": hi}, "options": {}}]
    if lo > INT16_MIN:
        actions.append({"variableId": tag_id, "type": "hide", "range": {"min": INT16_MIN, "max": lo - 1}, "options": {}})
    if hi < INT16_MAX:
        actions.append({"variableId": tag_id, "type": "hide", "range": {"min": hi + 1, "max": INT16_MAX}, "options": {}})
    return actions


def conditional_text(element_id: str, tag_id: str, lo: int, hi: int, x: float, y: float, content: str,
                     **style) -> Shape:
    """A text that is visible only while the tag is in [lo, hi]."""
    item = {"id": element_id, "type": "svg-ext-shapes-text", "name": element_id, "label": "Shapes",
            "property": {"actions": show_hide_actions(tag_id, lo, hi), "events": []}}
    return text(x, y, content, element_id=element_id, hidden=True, **style), {element_id: item}


def enum_texts(prefix: str, tag_id: str, names: dict[int, str], x: float, y: float, **style) -> Shape:
    """One text per value; only the one matching the tag is visible (e.g. step names)."""
    svg, items = [], {}
    for number, name in names.items():
        s, i = conditional_text(f"{prefix}_{number}", tag_id, number, number, x, y, name, **style)
        svg.append(s)
        items |= i
    return "".join(svg), items


def set_value(tag_id: str, number: int) -> dict:
    """Click event: write a number to a tag."""
    return {"type": "click", "action": "onSetValue", "actparam": str(number), "actoptions": {"variableId": tag_id}}


def goto(view_id: str) -> dict:
    """Click event: open another view."""
    return {"type": "click", "action": "onpage", "actparam": view_id, "actoptions": {}}


def button(element_id: str, label: str, x: float, y: float, w: float, h: float, events: list[dict],
           background: str = "#c8c8c8", color: str = "#1d1d1d", tag_id: str | None = None,
           ranges: list[dict] | None = None, actions: list[dict] | None = None) -> Shape:
    """HTML button. With tag_id + ranges its background follows the tag (range color = background,
    range stroke = text colour).

    FUXA finds the inner <BUTTON> by the id prefix "B-HXB_" (label, colours), so the group id
    must start with "HXB_"; the prefix is added here.
    """
    element_id = element_id if element_id.startswith("HXB_") else f"HXB_{element_id}"
    style = (f"width: 100%; height: 100%; vector-effect: non-scaling-stroke; background-color: {background}; "
             f"color: {color}; font-size: 14px; font-weight: bold;")
    svg = (f'<g id="{element_id}" type="svg-ext-html_button" fill="{background}" font-size="14" {FONT} '
           f'text-anchor="middle" stroke="{color}">'
           f'<rect id="{element_id}_r" stroke-width="0" x="{x}" y="{y}" width="{w}" height="{h}" fill="{background}" '
           f'stroke="{color}"/>'
           f'<foreignObject id="H-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}">'
           f'<BUTTON id="B-{element_id}" class="md-btn md-btn-raised" style="{style}">{esc(label)}</BUTTON>'
           f'</foreignObject></g>')
    prop = {"text": label, "events": events, "actions": actions or []}
    if tag_id:
        prop |= {"variableId": tag_id, "ranges": ranges or []}
    item = {"id": element_id, "type": "svg-ext-html_button", "name": label, "label": "HtmlButton", "property": prop}
    return svg, {element_id: item}


def press(tag_id: str, number: int, kind: str) -> dict:
    """Mouse event (mousedown / mouseup / mouseout): write a number to a tag."""
    return {"type": kind, "action": "onSetValue", "actparam": str(number), "actoptions": {"variableId": tag_id}}


def hold_button(element_id: str, label: str, x: float, y: float, w: float, h: float, tag_id: str,
                show_tag: str, show_lo: int, show_hi: int) -> Shape:
    """Hold-to-run button: tag = 1 while the mouse button is held on it, 0 on release or when the
    pointer leaves it (mouseout). Visible only while show_tag is in [show_lo, show_hi]
    (e.g. only in Manual mode); hidden HTML buttons cannot be clicked."""
    events = [press(tag_id, 1, "mousedown"), press(tag_id, 0, "mouseup"), press(tag_id, 0, "mouseout")]
    return button(element_id, label, x, y, w, h, events, actions=show_hide_actions(show_tag, show_lo, show_hi))


def circle_ranges(element_id: str, tag_id: str, cx: float, cy: float, r: float, ranges: list[dict],
                  default: str = "#7a7a7a", stroke: str = "#1d1d1d") -> Shape:
    """Lamp: a circle whose fill follows a tag (FUXA Shapes with ranges)."""
    item = {"id": element_id, "type": "svg-ext-shapes-circle", "name": element_id, "label": "Shapes",
            "property": {"variableId": tag_id, "ranges": ranges, "events": [], "actions": []}}
    svg = f'<circle id="{element_id}" cx="{cx}" cy="{cy}" r="{r}" fill="{default}" stroke="{stroke}" stroke-width="1.5"/>'
    return svg, {element_id: item}


def progress(element_id: str, tag_id: str, x: float, y: float, w: float, h: float, lo: int, hi: int,
             color: str, background: str = "#c8c8c8") -> Shape:
    """Vertical bar (FUXA gauge_progress): bar height = (value - lo) / (hi - lo) of h.

    FUXA needs exactly three children with the prefixes A-GXP_ (background), B-GXP_ (bar) and
    H-GXP_ (label holder); the group id gets "GXP_". The scale lo..hi is fixed (no tag)."""
    element_id = element_id if element_id.startswith("GXP_") else f"GXP_{element_id}"
    svg = (f'<g id="{element_id}" type="svg-ext-gauge_progress" font-size="14" {FONT} stroke="none">'
           f'<rect id="A-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}" fill="{background}" stroke="none"/>'
           f'<rect id="B-{element_id}" x="{x}" y="{y + h}" width="{w}" height="0" fill="{color}" stroke="none"/>'
           f'<foreignObject id="H-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}" font-size="14"/></g>')
    item = {"id": element_id, "type": "svg-ext-gauge_progress", "name": element_id, "label": "HtmlProgress",
            "property": {"variableId": tag_id, "events": [], "actions": [],
                         "ranges": [{"type": "minmax", "min": lo, "max": hi, "style": [True, True], "color": color}]}}
    return svg, {element_id: item}


def number_input(element_id: str, tag_id: str, x: float, y: float, w: float, h: float, lo: int, hi: int) -> Shape:
    """Number field (FUXA html_input). FUXA writes the tag on Enter, only if lo <= value <= hi
    (otherwise it shows a message); while the field is not focused it shows the tag value.

    FUXA finds the <INPUT> by the prefix "I-HXI_"; the group id gets "HXI_"."""
    element_id = element_id if element_id.startswith("HXI_") else f"HXI_{element_id}"
    style = ("width: calc(100% - 7px); height: calc(100% - 7px); text-align: right; border: unset; "
             "background-color: rgb(255, 255, 255); color: rgb(0, 0, 0); font-size: 15px; vector-effect: non-scaling-stroke;")
    svg = (f'<g id="{element_id}" type="svg-ext-html_input" stroke="#1d1d1d" text-anchor="right" {FONT} '
           f'font-size="14" fill="#ffffff">'
           f'<rect id="{element_id}_r" x="{x}" y="{y}" width="{w}" height="{h}" fill="#ffffff" stroke="#7a7a7a" '
           f'stroke-width="1"/>'
           f'<foreignObject id="H-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}">'
           f'<INPUT id="I-{element_id}" type="number" style="{style}"/></foreignObject></g>')
    item = {"id": element_id, "type": "svg-ext-html_input", "name": element_id, "label": "HtmlInput",
            "property": {"variableId": tag_id, "events": [], "actions": [], "ranges": [],
                         "options": {"type": "number", "numeric": True, "min": lo, "max": hi, "updated": True}}}
    return svg, {element_id: item}


# --- Alarms (stage 5) -----------------------------------------------------------------
PRIORITIES = ("highhigh", "high", "low", "info")  # FUXA alarm levels, most severe first
PRIORITY_COLOURS = {"highhigh": ("#d64545", "#ffffff"), "high": ("#e8812f", "#1d1d1d"),
                    "low": ("#e6c229", "#1d1d1d"), "info": ("#c4c4c4", "#1d1d1d")}


def alarm(name: str, tag_id: str, priority: str, text: str, group: str) -> dict:
    """FUXA alarm definition (project["alarms"]): active while the tag is 1.

    FUXA checks a level only if enabled, checkdelay > 0 and min..max is valid; the value is
    compared as min <= value <= max (a Bool true counts as 1). ackmode "ackactive": the
    operator can acknowledge while the alarm is active, and an alarm that came and went stays
    in the list until it is acknowledged (nothing is missed)."""
    def level(p: str) -> dict:
        bk, fg = PRIORITY_COLOURS[p]
        on = p == priority
        return {"enabled": on, "checkdelay": 1, "min": 1 if on else None, "max": 1 if on else None,
                "timedelay": 0, "text": text if on else "", "group": group if on else "",
                "ackmode": "ackactive", "bkcolor": bk, "color": fg}
    return {"name": name, "property": {"variableId": tag_id}, **{p: level(p) for p in PRIORITIES},
            "actions": {"enabled": False, "values": []}}


def alarm_table(element_id: str, x: float, y: float, w: float, h: float, kind: str,
                columns: list[tuple[str, str, int]]) -> Shape:
    """FUXA table widget showing the active alarms (kind "alarms", with an Ack button per row
    when the "ack" column is present) or the alarm history (kind "alarmsHistory").

    Markup as the editor builds it (own_ctrl extension): the group id gets "OXC_", FUXA puts the
    table into the div "D-OXC_…". columns: (field, header text, width px); field from ontime, text,
    type, group, status, ack (active). FUXA 1.3.5 never loads data into a table of kind
    "alarmsHistory", so the history is FUXA's own alarm panel (side menu, stage 5)."""
    element_id = element_id if element_id.startswith("OXC_") else f"OXC_{element_id}"
    svg = (f'<g id="{element_id}" type="svg-ext-own_ctrl-table" fill="#ffffff" stroke="#000000" stroke-width="1" '
           f'font-size="14" {FONT} text-anchor="right">'
           f'<rect id="{element_id}_r" x="{x}" y="{y}" width="{w}" height="{h}" stroke-width="0" fill="#ffffff"/>'
           f'<foreignObject id="H-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}">'
           f'<div id="D-{element_id}" style="width:100%;height:100%;"></div></foreignObject></g>')
    options = {
        "paginator": {"show": False}, "filter": {"show": False}, "daterange": {"show": kind == "alarmsHistory"},
        "realtime": False, "lastRange": "last1h", "gridColor": "#E0E0E0",
        "header": {"show": True, "height": 30, "fontSize": 13, "background": "#d4d4d4", "color": "#1d1d1d"},
        "row": {"height": 30, "fontSize": 13, "background": "#f4f4f4", "color": "#1d1d1d"},
        "selection": {"background": "#3059AF", "color": "#FFFFFF", "fontBold": True},
        "columns": [], "rows": [],
        "alarmsColumns": [{"id": field, "type": "label", "label": label, "width": width, "align": "left"}
                          for field, label, width in columns],
        "alarmFilter": {"filterA": [], "filterB": [], "filterC": []},
        "reportsColumns": [], "reportFilter": {"filterA": []},
    }
    item = {"id": element_id, "type": "svg-ext-own_ctrl-table", "name": element_id, "label": "HtmlTable",
            "property": {"id": None, "type": kind, "options": options, "events": []}}
    return svg, {element_id: item}


# --- Trends (stage 6) -------------------------------------------------------------------
STEP_AFTER = 1  # FUXA lineInterpolation: 0 linear, 1 stepAfter, 2 stepBefore, 3 spline, 4 none


def chart_line(tag_id: str, device_name: str, label: str, color: str, yaxis: int = 1) -> dict:
    """One line of a FUXA chart (project["charts"][i]["lines"]), bound to a tag by id.

    stepAfter: the DAQ stores a value only when it changes, so a straight line between two
    samples would draw a ramp that never happened; stepAfter holds each value until the next."""
    return {"id": tag_id, "name": label, "device": device_name, "label": label, "color": color,
            "yaxis": yaxis, "lineInterpolation": STEP_AFTER, "lineWidth": 2, "spanGaps": True}


def chart_options(window_min: int, **overrides) -> dict:
    """Options of a realtime chart: FUXA's defaults (chart-uplot DefaultOptions) in ISA-101 greys.

    realtime = window in minutes; loadOldValues = when the view opens, fill the window from the
    DAQ history (so the curves are still there after a page change or a reload)."""
    return {"title": "", "fontFamily": "sans-serif", "legendFontSize": 12, "titleHeight": 16,
            "axisLabelFontSize": 12, "labelsDivWidth": 0, "colorBackground": "#f4f4f4",
            "legendBackground": "rgba(255,255,255,0)", "axisLineColor": "#555555", "axisLabelColor": "#1d1d1d",
            "legendMode": "always", "series": [], "width": 360, "height": 200, "decimalsPrecision": 0,
            "realtime": window_min, "loadOldValues": True, "mouseWheelScroll": False, "mouseWheelZoom": False,
            "staticChart": False, "dateFormat": "DD_MM_YYYY", "timeFormat": "hh_mm_ss"} | overrides


def chart(element_id: str, chart_id: str, x: float, y: float, w: float, h: float, options: dict) -> Shape:
    """FUXA chart widget (realtime) showing the chart chart_id of project["charts"].

    Markup as the editor builds it (extHtmlChart): the group id gets "HXC_", FUXA puts the chart
    into the div "D-HXC_…". The tags it listens to come from the chart's lines, its title from
    the chart's name."""
    element_id = element_id if element_id.startswith("HXC_") else f"HXC_{element_id}"
    svg = (f'<g id="{element_id}" type="svg-ext-html_chart" style="pointer-events:none" fill="#ffffff" '
           f'stroke="#000000" stroke-width="1" font-size="14" {FONT} text-anchor="right">'
           f'<rect id="{element_id}_r" x="{x}" y="{y}" width="{w}" height="{h}" stroke-width="0" fill="#f4f4f4"/>'
           f'<foreignObject id="H-{element_id}" x="{x}" y="{y}" width="{w}" height="{h}">'
           f'<div id="D-{element_id}" style="width:100%;height:100%;"></div></foreignObject></g>')
    item = {"id": element_id, "type": "svg-ext-html_chart", "name": element_id, "label": "HtmlChart",
            "property": {"id": chart_id, "type": "realtime1", "options": options, "events": []}}
    return svg, {element_id: item}


def svg_document(width: int, height: int, parts: list[str]) -> str:
    return (f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:svg="http://www.w3.org/2000/svg" xmlns:html="http://www.w3.org/1999/xhtml">\n <g>\n'
            f'  <title>Layer 1</title>\n  ' + "\n  ".join(parts) + "\n </g>\n</svg>")


def view(view_id: str, name: str, width: int, height: int, parts: list[str], items: dict,
         background: str = "#e6e6e6ff") -> dict:
    return {
        "id": view_id,
        "name": name,
        "type": "svg",
        "profile": {"width": width, "height": height, "bkcolor": background, "margin": 10,
                    "align": "topCenter", "gridType": "fixed", "viewRenderDelay": 0},
        "svgcontent": svg_document(width, height, parts),
        "items": items,
        "variables": {},
    }
