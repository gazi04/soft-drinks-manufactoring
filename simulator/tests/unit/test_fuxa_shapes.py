"""fuxa_shapes.py: the FUXA shape builders (pure, no FUXA).  uv run pytest"""

import pytest

import fuxa_shapes as fs
from simulator.plcs import MODE_NAMES, STEP_NAMES


def covered(actions: list[dict], kind: str) -> set[int]:
    out = set()
    for a in actions:
        if a["type"] == kind:
            out |= set(range(a["range"]["min"], a["range"]["max"] + 1))
    return out


@pytest.mark.parametrize("lo,hi", [(0, 0), (10, 10), (1, 4), (fs.INT16_MIN, 0), (5, fs.INT16_MAX)])
def test_show_and_hide_ranges_cover_int16_without_overlap(lo, hi):
    actions = fs.show_hide_actions("t", lo, hi)
    show, hide = covered(actions, "show"), covered(actions, "hide")
    assert show == set(range(lo, hi + 1))
    assert not show & hide
    assert show | hide == set(range(fs.INT16_MIN, fs.INT16_MAX + 1))


def test_enum_texts_one_hidden_text_per_value_exactly_one_visible():
    for names in (STEP_NAMES, MODE_NAMES):
        svg, items = fs.enum_texts("p", "t_step", names, 0, 0)
        assert len(items) == len(names) and svg.count('style="display:none"') == len(names)
        for value in (*names, 7, -1):  # known values and two unknown ones
            visible = [i for i in items.values() if value in covered(i["property"]["actions"], "show")]
            assert len(visible) == (1 if value in names else 0)


def test_button_gets_fuxa_id_prefix_and_writes_through_its_event():
    svg, items = fs.button("cmd_start", "Start", 0, 0, 90, 32, [fs.set_value("t_filler_Cmd", 1)])
    (item_id, item), = items.items()
    assert item_id == "HXB_cmd_start" and 'id="B-HXB_cmd_start"' in svg  # FUXA looks for "B-HXB_"
    assert item["property"]["events"] == [
        {"type": "click", "action": "onSetValue", "actparam": "1", "actoptions": {"variableId": "t_filler_Cmd"}}]
    assert "variableId" not in item["property"]  # no colour binding unless asked


def test_text_is_escaped():
    assert "&lt;b&gt;" in fs.text(0, 0, "<b>")


def test_hold_button_writes_1_on_press_0_on_release_and_when_the_pointer_leaves():
    svg, items = fs.hold_button("man_c1", "C1", 0, 0, 80, 32, "t_man", "t_mode", 2, 2)
    item = items["HXB_man_c1"]
    events = {(e["type"], e["actparam"], e["actoptions"]["variableId"]) for e in item["property"]["events"]}
    assert events == {("mousedown", "1", "t_man"), ("mouseup", "0", "t_man"), ("mouseout", "0", "t_man")}
    assert covered(item["property"]["actions"], "show") == {2}  # visible only in Manual


def test_progress_has_the_three_children_fuxa_needs():
    svg, items = fs.progress("fill", "t_level", 0, 0, 40, 200, 0, 600, "#7b4a22")
    assert list(items) == ["GXP_fill"]
    for prefix in ("A-GXP_fill", "B-GXP_fill", "H-GXP_fill"):
        assert f'id="{prefix}"' in svg
    assert svg.count("<rect") == 2 and svg.count("<foreignObject") == 1
    assert items["GXP_fill"]["property"]["ranges"][0] == {
        "type": "minmax", "min": 0, "max": 600, "style": [True, True], "color": "#7b4a22"}


def test_number_input_limits_and_prefix():
    svg, items = fs.number_input("sp", "t_sp", 0, 0, 90, 30, 100, 600)
    options = items["HXI_sp"]["property"]["options"]
    assert options == {"type": "number", "numeric": True, "min": 100, "max": 600, "updated": True}
    assert 'id="I-HXI_sp"' in svg  # FUXA finds the field by "I-HXI_"


def test_slot_shape_uses_one_bit():
    _, items = fs.rect_ranges("c1_slot3", "t_anim", 0, 0, 26, 46, [fs.colour_range(1, 1, "#fff")], bitmask=1 << 3)
    assert items["c1_slot3"]["property"]["bitmask"] == 8
    _, plain = fs.rect_ranges("r", "t", 0, 0, 1, 1, [])
    assert "bitmask" not in plain["r"]["property"]  # no bitmask = whole value


def test_roller_turns_only_with_a_motor_tag():
    _, items = fs.roller("r1", 0, 0, 10, "t_motor")
    acts = items["r1"]["property"]["actions"]
    assert {(a["type"], a["range"]["min"], a["range"]["max"]) for a in acts} == {("clockwise", 1, 1), ("stop", 0, 0)}
    _, static = fs.roller("r2", 0, 0, 10, None)
    assert static["r2"]["property"]["actions"] == []


def test_alarm_enables_exactly_one_priority_active_at_1():
    a = fs.alarm("filler_X", "t_x", "high", "text", "Filler")
    on = [p for p in fs.PRIORITIES if a[p]["enabled"]]
    assert on == ["high"] and (a["high"]["min"], a["high"]["max"]) == (1, 1)
    assert a["high"]["checkdelay"] > 0 and a["high"]["ackmode"] == "ackactive"
    assert a["property"] == {"variableId": "t_x"}


def test_alarm_table_prefix_and_columns():
    svg, items = fs.alarm_table("act", 0, 0, 800, 200, "alarms", [("ontime", "Time", 150), ("ack", "Ack", 60)])
    item = items["OXC_act"]
    assert 'id="D-OXC_act"' in svg and item["property"]["type"] == "alarms"
    assert [c["id"] for c in item["property"]["options"]["alarmsColumns"]] == ["ontime", "ack"]


def test_chart_markup_has_the_ids_fuxa_looks_for():
    svg, items = fs.chart("trend_x", "ch_x", 10, 20, 300, 200, fs.chart_options(5))
    (item_id, item), = items.items()
    assert item_id == "HXC_trend_x"  # FUXA finds the chart div by the prefix D-HXC_
    for element in ('id="HXC_trend_x"', 'id="H-HXC_trend_x"', 'id="D-HXC_trend_x"', 'type="svg-ext-html_chart"'):
        assert element in svg
    assert item["type"] == "svg-ext-html_chart" and item["label"] == "HtmlChart"
    prop = item["property"]
    assert prop["id"] == "ch_x" and prop["type"] == "realtime1"
    assert prop["options"]["realtime"] == 5 and prop["options"]["loadOldValues"] is True


def test_chart_options_overrides_keep_the_other_defaults():
    opts = fs.chart_options(30, legendMode="bottom")
    assert opts["legendMode"] == "bottom" and opts["realtime"] == 30 and "axisLabelColor" in opts


def test_chart_line_binds_the_tag_by_id_and_holds_values():
    line = fs.chart_line("t_filler_State", "Filler PLC", "State", "#000000", yaxis=2)
    assert (line["id"], line["device"], line["label"], line["yaxis"]) == ("t_filler_State", "Filler PLC", "State", 2)
    assert line["lineInterpolation"] == fs.STEP_AFTER == 1
