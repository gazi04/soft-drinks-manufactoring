"""ui/view.py: panel text built from the plant and the PLC status (no terminal).  uv run pytest"""

from simulator import config
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


# --- Syrup room tab --------------------------------------------------------------------------
def test_level_bar_is_rounded_and_clamped():
    assert view.level_bar(0, 1000) == "▕" + "░" * 10 + "▏"
    assert view.level_bar(500, 1000) == "▕" + "█" * 5 + "░" * 5 + "▏"
    assert view.level_bar(1000, 1000) == "▕" + "█" * 10 + "▏"
    assert view.level_bar(1500, 1000) == view.level_bar(1000, 1000)  # never longer than the tank
    assert view.level_bar(-5, 1000) == view.level_bar(0, 1000)
    assert len(view.level_bar(333, 2000, width=4)) == 6


def _syrup_plant():
    from simulator.machines.filler import FillerOutputs
    from simulator.machines.syrup import SyrupOutputs
    from simulator.plant import SyrupSignals

    plant = Plant(with_syrup=True)
    room = plant.syrup
    room.mix_l, room.temp_c, room.pressure_bar, room.dissolved_bar = 590.0, 4.1, 3.48, 3.2
    plant.step(FillerOutputs(), 0.0, SyrupSignals(SyrupOutputs(agitator=True, co2_valve=True), True))
    return plant


def test_syrup_world_shows_sensor_values_and_output_lamps():
    plant = _syrup_plant()
    text = view.syrup_world_text(plant).plain
    assert "Syrup tank    600 L" in text and "Mixing tank   590 L" in text and "Buffer tank   500 L" in text
    assert "4.1 °C" in text and "3.48 bar" in text and "dissolved 3.20 bar" in text
    assert "Agitator ●" in text and "CO2Valve ●" in text and "WaterPump ○" in text and "TransferPump ○" in text
    assert "syrup PLC ●" in text and "to filler ●" in text
    assert "not answering" not in text
    for marker in ("FAILED", "STUCK", "FROZEN"):
        assert marker not in text
    assert max(len(line) for line in text.splitlines()) <= 86  # fits the world column at 140 columns


def test_syrup_world_marks_every_injected_fault():
    plant = _syrup_plant()
    room = plant.syrup
    room.water_pump_failed = room.syrup_pump_failed = room.co2_valve_stuck = True
    room.coolant_c = 20.0
    room.mix_level_frozen = 5
    plant.syrup_heartbeat.enabled = False
    text = view.syrup_world_text(plant).plain
    assert "SyrupPump ○  FAILED" in text and "WaterPump ○  FAILED" in text
    assert "Mixing tank     5 L" in text and "SENSOR STUCK (true 590 L)" in text  # what the PLC sees + the truth
    assert "CHILLER FAILED" in text and "STUCK OPEN" in text and "FROZEN" in text
    assert max(len(line) for line in text.splitlines()) <= 86


def test_syrup_world_without_an_answer_and_without_a_syrup_room():
    plant = Plant(with_syrup=True)  # no step yet: the PLC never answered
    text = view.syrup_world_text(plant).plain
    assert "syrup PLC not answering" in text and "Agitator ○" in text
    assert "--filler-only" in view.syrup_world_text(Plant()).plain


def _labeler_plant():
    from simulator.machines.filler import FillerOutputs
    from simulator.machines.labeler import LabelerOutputs

    plant = Plant(with_labeler=True)
    lab = plant.labeler
    lab.pocket = Bottle(11, label_mm=config.LABEL_LENGTH_MM)  # a labelled bottle in the pocket
    lab.tank.temp_c = 165.2
    lab.web.labels_left = 998
    plant.c3.slots[config.C3_FULL_SLOT] = Bottle(10)
    plant.step(FillerOutputs(), 0.0, labeler=LabelerOutputs(conveyor_motor2=True, clamp_valve=True, glue_heater=True))
    return plant


def test_labeler_world_shows_sensor_values_and_output_lamps():
    plant = _labeler_plant()
    text = view.labeler_world_text(plant).plain
    assert "Pocket   11  label 100%" in text and "Bottle_In_Pocket ●" in text and "Label_Present ●" in text
    assert "165.2 °C" in text and "4.0 / 5 kg" in text and "998 / 1000 labels" in text
    assert "ConveyorMotor2 ●" in text and "ClampValve ●" in text and "GlueHeater ●" in text
    assert "SpinMotor ○" in text and "Cutter ○" in text and "C3_Full ●" in text  # C3 sensor slot taken
    assert "Packer   running" in text and "Count    discharged 0" in text
    assert "not answering" not in text
    for marker in ("JAMMED", "STUCK", "TORN", "FAILED", "STOPPED", "FROZEN"):
        assert marker not in text
    assert max(len(line) for line in text.splitlines()) <= 86  # fits the world column at 140 columns


def test_labeler_world_marks_every_injected_fault():
    plant = _labeler_plant()
    lab = plant.labeler
    lab.web.torn = lab.bell_stuck = lab.gate_jammed = True
    lab.tank.heater_failed = lab.tank.heater_stuck_on = True
    plant.packer.running = False
    plant.labeler_heartbeat.enabled = False
    text = view.labeler_world_text(plant).plain
    assert "GATE JAMMED" in text and "BELL STUCK" in text and "WEB TORN" in text
    assert "HEATER FAILED  STUCK ON" in text and "Packer   STOPPED" in text and "FROZEN" in text
    assert max(len(line) for line in text.splitlines()) <= 86
    lab.tank.heater_failed = False
    assert "HEATER STUCK ON" in view.labeler_world_text(plant).plain


def test_labeler_world_without_an_answer_and_without_a_labeler():
    plant = Plant(with_labeler=True)  # no step yet: the PLC never answered
    text = view.labeler_world_text(plant).plain
    assert "labeler PLC not answering" in text and "ConveyorMotor2 ○" in text
    assert "--no-labeler" in view.labeler_world_text(Plant()).plain
