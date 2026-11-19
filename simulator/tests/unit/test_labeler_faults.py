"""Labeler physics, stage 6: injected faults and hand actions.  uv run pytest

The PLC is never told about a fault; these tests check only what the world does.
"""

import pytest
from test_labeler import PLC_GATE_PULSE_S, WRAP, Pocket, Station, wrap_one_label

from simulator import config
from simulator.bottle import Bottle
from simulator.machines.labeler import LabelerOutputs, MeltTank

TICK = 0.1


def test_torn_web_feeds_nothing_gives_no_mark_and_no_warning():
    st = Station()
    st.web.torn = True
    for _ in range(round(6 / TICK)):  # longer than LabelTimeout
        st.tick(WRAP)
        assert not st.web.label_mark
    assert st.bottle.label_mm == 0 and st.web.labels_left == config.ROLL_LABELS and not st.warnings
    st.web.torn = False  # threaded again
    wrap_one_label(st)
    assert st.bottle.label_mm >= config.LABEL_LENGTH_MM


@pytest.mark.parametrize("start", [0.0, 1.0])
def test_stuck_bell_keeps_its_position(start):
    p = Pocket()
    p.lab.bell, p.lab.bell_stuck = start, True
    p.run(2.0, LabelerOutputs(clamp_valve=start == 0.0))  # the valve pulls the other way
    assert p.lab.bell == start
    p.lab.bell_stuck = False
    p.run(1.0, LabelerOutputs(clamp_valve=start == 0.0))
    assert p.lab.bell == 1.0 - start


def test_failed_heater_heats_nothing_and_a_stuck_one_heats_always():
    failed, stuck = MeltTank(temp_c=100.0), MeltTank(temp_c=100.0)
    failed.heater_failed, stuck.heater_stuck_on = True, True
    for i in range(100):
        failed.step(True, TICK, i * TICK)  # the PLC asks for heat
        stuck.step(False, TICK, i * TICK)  # the PLC switched the heater off
    assert failed.temp_c < 100.0  # only the room loss
    assert stuck.temp_c > 110.0
    both = MeltTank(temp_c=100.0)
    both.heater_failed = both.heater_stuck_on = True  # a dead element heats nothing, whatever the contactor does
    both.step(False, 10.0, 10.0)
    assert both.temp_c < 100.0


def test_jammed_gate_keeps_the_bottle_and_clear_gate_removes_it():
    p = Pocket(queue=3)
    p.lab.gate_jammed = True
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    p.run(3.0)  # longer than ClampTimeout
    assert p.lab.entering is not None and p.lab.pocket is None  # stuck between gate and pocket
    stuck = p.lab.clear_gate()
    assert stuck is not None and p.lab.removed == [stuck] and p.lab.entering is None and not p.lab.lost
    assert p.lab.clear_gate() is None  # nothing left to clear
    p.lab.gate_jammed = False  # repaired: the next bottle passes normally
    p.run(PLC_GATE_PULSE_S, LabelerOutputs(gate_motor=True))
    p.run(1.5)
    assert p.lab.pocket is not None and p.lab.pocket.id == 2 and not p.warnings


def test_a_jam_released_without_clearing_lets_the_bottle_arrive():
    p = Pocket()
    p.lab.entering = Bottle(id=5)
    p.lab.gate_jammed = True
    p.run(2.0)
    p.lab.gate_jammed = False
    p.run(1.0)
    assert p.lab.pocket is not None and p.lab.pocket.id == 5
