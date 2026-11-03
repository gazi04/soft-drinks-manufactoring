"""ui/view.py: panel text built from the plant and the PLC status (no terminal).  uv run pytest"""

from simulator.bottle import Bottle
from simulator.events import Event
from simulator.loop import LoopStats
from simulator.machines.filler import CAP, FILL, RINSE
from simulator.plant import Plant
from simulator.plcs import FillerPlc, PlcStatus
from simulator.scenarios import SCENARIOS, ScenarioSlot
from simulator.ui import view


def status(step=10, state=1, jam=False, comm=False, mode=1) -> PlcStatus:
    return PlcStatus(state, step, 193, jam, False, False, comm, 500, 2000, 1500, mode)


def test_conveyor_slots_from_entry_to_exit():
    plant = Plant()
    plant.c1.slots[0] = Bottle(9)
    plant.c1.slots[-1] = Bottle(3)
    assert view.conveyor_text(plant.c1) == "[   9   .   .   .   .   .   .   3 ]"


def test_pocket_labels_show_ml_and_flags():
    assert view.bottle_label(None) == "."
    assert view.bottle_label(Bottle(7)) == "7"
    assert view.bottle_label(Bottle(7), show_ml=True) == "7 0ml"
    assert view.bottle_label(Bottle(6, 230.4, rinsed=True)) == "6 230ml R"
    assert view.bottle_label(Bottle(5, 500, rinsed=True, capped=True)) == "5 500ml RC"


def test_world_text_shows_pockets_and_lamps_as_written_to_the_plc():
    plant = Plant()
    plant.filler.pockets[RINSE] = Bottle(7, rinsed=True)
    plant.filler.pockets[FILL] = Bottle(6, 230)
    plant.filler.pockets[CAP] = Bottle(5)
    text = view.world_text(plant).plain
    assert "7 R" in text and "6 230ml" in text
    w = plant.writes()
    assert w.s3 and w.s4 and w.s5  # belt at rest: the stations see their bottles
    assert "S3 ●" in text and "S4 ●" in text and "S5 ●" in text and "S6 ○" in text
    assert "fill level 230 ml" in text
    assert "JAMMED" not in text
    plant.filler.index_jammed = True
    plant.heartbeat.enabled = False
    text = view.world_text(plant).plain
    assert "JAMMED" in text and "FROZEN" in text


def test_header_without_status_shows_only_the_link():
    plc = FillerPlc(port=1)
    text = view.header_text(None, plc).plain
    assert "not connected" in text and "step" not in text


def test_header_with_status_alarms_and_idle_hint():
    stats = LoopStats(ticks=5, dt_min=0.098, dt_max=0.102, status=status(step=900, state=9, jam=True))
    text = view.header_text(stats, FillerPlc()).plain
    assert "step FAULT" in text and "state Fault" in text and "alarms: Alm_IndexJam" in text
    assert "dt 98-102 ms" in text and "hmi.py" not in text
    stats.status = status(step=0, state=0)
    assert "hmi.py auto reset start" in view.header_text(stats, FillerPlc()).plain


def test_scenario_text():
    slot = ScenarioSlot()
    assert view.scenario_text(slot) == "no scenario"
    slot.start(SCENARIOS["gaps"])
    assert view.scenario_text(slot) == "gaps: step 1/5"


def test_event_style_and_filters():
    warning = Event.bottle(1.0, 3, "crash", warning=True)
    plc = Event.plc(1.0, "step IDLE -> WORK")
    sim = Event.sim(1.0, "command: e_stop = True")
    bottle = Event.bottle(1.0, 3, "entered filler")
    assert view.event_style(warning) == "bold red"
    assert view.event_style(plc) == "cyan" and view.event_style(sim) == "yellow"
    assert [view.matches(e, "bottle") for e in (warning, plc, sim, bottle)] == [True, False, False, True]
    assert [view.matches(e, "plc") for e in (warning, plc, sim, bottle)] == [False, True, False, False]
    assert [view.matches(e, "warnings") for e in (warning, plc, sim, bottle)] == [True, False, False, False]
    assert all(view.matches(e, "all") for e in (warning, plc, sim, bottle))


def test_header_shows_the_mode_and_warns_when_start_would_be_ignored():
    stats = LoopStats(ticks=5, dt_min=0.1, dt_max=0.1, status=status(step=0, state=0, mode=0))
    text = view.header_text(stats, FillerPlc()).plain
    assert "mode Stop" in text and "Start is ignored" in text and "hmi.py auto reset start" in text
    stats.status = status(mode=1)
    assert "mode Auto" in view.header_text(stats, FillerPlc()).plain
