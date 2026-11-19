"""Modbus addresses of the labeler PLC (source: docs/labeler/io-list.md).

Same idea as address_map.py (the filler) and address_map_syrup.py: every address is
written in its IEC form (%QX, %MX, %MW) and converted with the OpenPLC v4 mapping, so
the code reads like the I/O list. The converters are imported from address_map: the
mapping exists once.

The labeler is its own PLC (runtime plc-labeler, Modbus port 5023), so its addresses
overlap the other PLCs' on purpose (e.g. %MX1.0 is the filler's Product_Available input
and unused here). Never mix the modules in one request.

The addresses are grouped by WHO WRITES them. The simulator may only write the
addresses in SIM_WRITE_COILS and SIM_WRITE_REGISTERS (one writer per address).

Differences to the filler and the syrup room that matter when reusing their code:
- 9 outputs (%QX0.0-%QX1.0): one more than fits in the first output byte;
- 10 digital sensors: 8 in %MX0, the two extra ones (Roll_Low, Glue_Low) in %MX4,
  so the simulator writes TWO coil blocks per tick (%MX2/%MX3 between them are HMI
  and PLC coils and must never be written by the simulator);
- 1 analog sensor (GlueTemp, INT in 0.1 degC);
- 7 alarms (%MX5.0-5.6) and 6 conditioned bits (%MX3.0-3.5);
- Cmd = 4 resets GoodCount (Cmd 1-3 as in every PLC).
"""

from simulator.address_map import mw, mx, qx

# --- Written by the PLC, read by the simulator: actuator outputs -----------------
CONVEYOR_MOTOR2 = qx(0, 0)  # C2 infeed conveyor (runs continuously, bottles queue at the gate)
GATE_MOTOR = qx(0, 1)  # infeed gate, TP pulse = one bottle
CLAMP_VALVE = qx(0, 2)  # centering bell down
SPIN_MOTOR = qx(0, 3)  # turntable
LABEL_FEED = qx(0, 4)  # feed rollers pull the label web
CUTTER = qx(0, 5)  # knife, TP pulse
GLUE_ROLLER = qx(0, 6)
GLUE_HEATER = qx(0, 7)  # melt tank heater (hysteresis)
DISCHARGE_MOTOR = qx(1, 0)  # pushes the bottle out of the pocket onto C3

OUTPUTS_START = CONVEYOR_MOTOR2  # one bulk read of coils 0..8
OUTPUTS_COUNT = 9

# --- Written by the simulator: digital sensors (%MX0) and wiring (%MX1) ----------
BOTTLE_AT_GATE = mx(0, 0)  # bottle waiting at the gate (end of C2)
BOTTLE_IN_POCKET = mx(0, 1)
BELL_DOWN = mx(0, 2)  # bell up = NOT Bell_Down
LABEL_PRESENT = mx(0, 3)  # label on the bottle in the pocket
C3_FULL = mx(0, 4)  # outfeed conveyor C3 full
ROLL_EMPTY = mx(0, 5)
LABEL_MARK = mx(0, 6)  # registration mark: one label length fed
SPIN_PULSE = mx(0, 7)  # one pulse per turn of the turntable
UNUSED_MX1_0 = mx(1, 0)  # the filler's Product_Available input; not used here, written FALSE
E_STOP_OK = mx(1, 1)  # E-stop chain healthy
HEARTBEAT = mx(1, 2)  # toggled by the simulator, watched by the PLC

# First bulk write per tick: coils 8192..8202.
SENSOR_BLOCK_START = BOTTLE_AT_GATE
SENSOR_BLOCK = tuple(range(SENSOR_BLOCK_START, HEARTBEAT + 1))

# --- Written by the simulator: the two extra sensors (%MX4) ----------------------
ROLL_LOW = mx(4, 0)  # warning in FUXA only
GLUE_LOW = mx(4, 1)  # warning in FUXA only

# Second bulk write per tick: coils 8224..8225 (%MX4.2-4.7 spare, not written).
EXTRA_SENSOR_BLOCK_START = ROLL_LOW
EXTRA_SENSOR_BLOCK = (ROLL_LOW, GLUE_LOW)

# --- Written by the simulator: analog sensor (INT) -------------------------------
GLUE_TEMP = mw(12)  # 0.1 degC, e.g. 1700 = 170.0 degC
ANALOG_START = GLUE_TEMP  # one write of %MW12 per tick
ANALOG_COUNT = 1

# --- Written by the PLC, read by the simulator for the event log -----------------
STATE = mw(50)  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault
STEP = mw(51)  # 0 IDLE, 5 WARM_UP, 10 WAIT_BOTTLE, 20 CLAMP, 22 WRAP, 24 PRESS,
#                26 UNCLAMP, 35 WAIT_OUTFEED, 40 DISCHARGE, 900 FAULT
GOOD_COUNT = mw(53)  # labelled bottles discharged onto C3; zeroed by Cmd = 4
STATUS_START = STATE  # one bulk read of %MW50..%MW53 (%MW52 is unused)
STATUS_COUNT = 4

ALM_LABEL_TIMEOUT = mx(5, 0)  # no Label_Mark / PressTurns within 5 s -> FAULT
ALM_POCKET_JAM = mx(5, 1)  # gate, bell or discharge movement not finished -> FAULT
ALM_OUTFEED_BLOCKED = mx(5, 2)  # Blocked > 2 min, warning
ALM_COMM_LOST = mx(5, 3)  # same bit in every PLC
ALM_LABEL_EMPTY = mx(5, 4)  # roll empty, warning, not latched
ALM_GLUE_OVER_TEMP = mx(5, 5)  # GlueTemp >= GlueMax -> FAULT
ALM_GLUE_HEAT_TIMEOUT = mx(5, 6)  # Glue_Ready not reached in WARM_UP within 120 s -> FAULT
ALARMS_START = ALM_LABEL_TIMEOUT  # one bulk read of the 7 alarm coils
ALARMS_COUNT = 7

# --- Written by the PLC, read only by the HMI and tests: conditioned values ------
OUTFEED_FULL = mx(3, 0)  # C3_Full filtered (2 s on, 3 s off)
BOTTLE_ENTERING = mx(3, 1)  # bottle between gate and pocket
JOG_BLOCK = mx(3, 2)  # jog watchdog
HEAT_DEMAND = mx(3, 3)  # heater hysteresis latch
GLUE_READY = mx(3, 4)  # GlueTemp inside GlueSetpoint +- GlueBand
STARVED = mx(3, 5)  # filtered: no bottle at the gate for 2 s, or roll empty
CONDITIONED_START = OUTFEED_FULL  # one bulk read of %MX3.0-3.5
CONDITIONED_COUNT = 6

# --- Written by the HMI: the simulator NEVER writes these ------------------------
CMD = mw(0)  # 1 Start, 2 Stop, 3 Reset, 4 Reset counter
MODE_REQ = mw(1)  # 0 Stop, 1 Auto, 2 Manual

GLUE_SETPOINT = mw(30)  # 0.1 degC
GLUE_HYST = mw(31)  # 0.1 degC
GLUE_BAND = mw(32)  # 0.1 degC, Glue_Ready window
PRESS_TURNS = mw(33)  # turns after the cut
SETPOINTS_START = GLUE_SETPOINT
SETPOINTS_COUNT = 4

MAN_C2 = mx(2, 0)  # manual buttons (hold-to-run)
MAN_GATE = mx(2, 1)
MAN_CLAMP = mx(2, 2)
MAN_SPIN = mx(2, 3)
MAN_FEED = mx(2, 4)  # LabelFeed + GlueRoller together
MAN_CUT = mx(2, 5)
MAN_DISCHARGE = mx(2, 6)

# --- Written by the simulator, read by the HMI, ignored by the PLC: animation ------
# Same idea as the filler's %MW70-75: only the simulator knows where each bottle is. The PLC
# program does not declare these words; the Modbus server serves every %MW anyway.
ANIM_C3 = mw(70)  # C3 slots, bit i = bottle in slot i (bit 0 = entry after the pusher, bit 7 = packer end)
ANIM_STATION = mw(71)  # pocket: 0 empty, 1 bottle (unlabelled), 2 labelling, 3 labelled
ANIM_START = ANIM_C3  # one write of %MW70..71 per tick
ANIM_COUNT = 2
POCKET_EMPTY, POCKET_BOTTLE, POCKET_LABELLING, POCKET_LABELLED = 0, 1, 2, 3  # codes in ANIM_STATION

# --- Ownership: the only addresses the simulator is allowed to write -------------
SIM_WRITE_COILS = frozenset(SENSOR_BLOCK + EXTRA_SENSOR_BLOCK)
SIM_WRITE_REGISTERS = frozenset(
    {*range(ANALOG_START, ANALOG_START + ANALOG_COUNT), *range(ANIM_START, ANIM_START + ANIM_COUNT)}
)

# Cheap checks at import: the bulk blocks are contiguous and owned by the simulator
assert list(SENSOR_BLOCK) == list(range(SENSOR_BLOCK_START, SENSOR_BLOCK_START + 11))
assert list(EXTRA_SENSOR_BLOCK) == list(range(EXTRA_SENSOR_BLOCK_START, EXTRA_SENSOR_BLOCK_START + 2))
assert {BOTTLE_AT_GATE, SPIN_PULSE, UNUSED_MX1_0, E_STOP_OK, HEARTBEAT, ROLL_LOW, GLUE_LOW} <= SIM_WRITE_COILS
assert DISCHARGE_MOTOR == OUTPUTS_START + OUTPUTS_COUNT - 1
assert PRESS_TURNS == SETPOINTS_START + SETPOINTS_COUNT - 1
assert ANIM_STATION == ANIM_START + ANIM_COUNT - 1
