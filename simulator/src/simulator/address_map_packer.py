"""Modbus addresses of the packer PLC (source: docs/packer/io-list.md).

Same idea as address_map.py (the filler), address_map_syrup.py and address_map_labeler.py:
every address is written in its IEC form (%QX, %MX, %MW) and converted with the OpenPLC v4
mapping, so the code reads like the I/O list. The converters are imported from address_map:
the mapping exists once.

The packer is its own PLC (runtime plc-packer, Modbus port 5024), so its addresses overlap
the other PLCs' on purpose (e.g. %MX1.0 is the filler's Product_Available input and unused
here). Never mix the modules in one request.

The addresses are grouped by WHO WRITES them. The simulator may only write the
addresses in SIM_WRITE_COILS and SIM_WRITE_REGISTERS (one writer per address).

Differences to the other machines that matter when reusing their code:
- 10 outputs (%QX0.0-%QX1.1): two more than fit in the first output byte;
- 14 digital sensors: 8 in %MX0, six more in %MX4, so the simulator writes TWO coil
  blocks per tick (%MX2/%MX3 between them are HMI and PLC coils and must never be
  written by the simulator);
- 1 analog sensor (OvenTemp, INT in 0.1 degC, the same %MW12 as the labeler's GlueTemp);
- two sequencers: Step (%MW51, collation) and WrapStep (%MW52, the word that is unused
  in every other PLC);
- 10 alarms: 8 in %MX5, the ninth and tenth in %MX7.0-7.1 (two bulk reads);
- 8 conditioned bits (%MX3.0-3.7) and 4 computed words (%MW40-43);
- Cmd = 4 resets PackCount (Cmd 1-3 as in every PLC), as the labeler's GoodCount.
"""

from simulator.address_map import mw, mx, qx

# --- Written by the PLC, read by the simulator: actuator outputs -----------------
CONVEYOR_MOTOR3 = qx(0, 0)  # C3 infeed conveyor (runs continuously, bottles queue at the gate)
GATE_MOTOR = qx(0, 1)  # infeed gate, HELD open while the group is not complete
GROUP_PUSHER = qx(0, 2)  # pushes the group onto the wrap conveyor, spring return when OFF
WRAP_CONVEYOR = qx(0, 3)  # collation table -> film slot -> oven
FILM_FEED = qx(0, 4)  # feed rollers push film up through the slot
KNIFE = qx(0, 5)  # cuts the sheet off the roll (whole CUT step)
WRAP_BAR = qx(0, 6)  # one turn per pack lays the sheet over the top
OVEN_HEATER = qx(0, 7)  # hysteresis, only in Auto and only with Airflow_OK
OVEN_BLOWER = qx(1, 0)  # with the oven on, plus a 30 s run-on (TOF)
OVEN_CONVEYOR = qx(1, 1)  # through the oven onto the outfeed table

OUTPUTS_START = CONVEYOR_MOTOR3  # one bulk read of coils 0..9
OUTPUTS_COUNT = 10

# --- Written by the simulator: digital sensors (%MX0) and wiring (%MX1) ----------
BOTTLE_AT_GATE = mx(0, 0)  # bottle waiting at the gate (end of C3), for the Starved filter
BOTTLE_COUNTED = mx(0, 1)  # count sensor behind the gate, one pulse per bottle
PUSHER_HOME = mx(0, 2)
PUSHER_OUT = mx(0, 3)  # the group is on the wrap conveyor
PACK_AT_FILM = mx(0, 4)  # pack at the stop point before the film slot
FILM_IN_SLOT = mx(0, 5)  # a film sheet sticks out of the slot
FILM_PULSE = mx(0, 6)  # one pulse per 50 mm of film
WRAP_HOME = mx(0, 7)  # wrap bar at home
UNUSED_MX1_0 = mx(1, 0)  # the filler's Product_Available input; not used here, written FALSE
E_STOP_OK = mx(1, 1)  # E-stop chain healthy
HEARTBEAT = mx(1, 2)  # toggled by the simulator, watched by the PLC

# First bulk write per tick: coils 8192..8202.
SENSOR_BLOCK_START = BOTTLE_AT_GATE
SENSOR_BLOCK = tuple(range(SENSOR_BLOCK_START, HEARTBEAT + 1))

# --- Written by the simulator: the six extra sensors (%MX4) ----------------------
FILM_EMPTY = mx(4, 0)  # no new sheet is started
FILM_LOW = mx(4, 1)  # warning in FUXA only
OVEN_ENTRY = mx(4, 2)  # pulse per pack: CTUD up
OVEN_EXIT = mx(4, 3)  # pulse per pack: CTUD down, PackCount up
TABLE_FULL = mx(4, 4)  # outfeed table full (raw, filtered into Outfeed_Full)
AIRFLOW_OK = mx(4, 5)  # air flow switch in the oven duct (heater interlock)

# Second bulk write per tick: coils 8224..8229 (%MX4.6-4.7 spare, not written).
EXTRA_SENSOR_BLOCK_START = FILM_EMPTY
EXTRA_SENSOR_BLOCK = tuple(range(EXTRA_SENSOR_BLOCK_START, AIRFLOW_OK + 1))

# --- Written by the simulator: analog sensor (INT) -------------------------------
OVEN_TEMP = mw(12)  # 0.1 degC, e.g. 1900 = 190.0 degC
ANALOG_START = OVEN_TEMP  # one write of %MW12 per tick
ANALOG_COUNT = 1

# --- Written by the PLC, read by the simulator for the event log -----------------
STATE = mw(50)  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault (both sequencers)
STEP = mw(51)  # collation: 0 IDLE, 5 WARM_UP, 10 COUNT, 15 WAIT_WRAP, 20 PUSH, 25 RETRACT, 900 FAULT
WRAP_STEP = mw(52)  # wrap: 0 IDLE, 10 WAIT_PACK, 20 FEED, 22 CUT, 24 READY, 30 RUN_ON, 32 WRAP,
#                     34 RUN_OUT, 900 FAULT
PACK_COUNT = mw(53)  # finished packs at the oven exit; zeroed by Cmd = 4
STATUS_START = STATE  # one bulk read of %MW50..%MW53 (all four used here)
STATUS_COUNT = 4

GROUP_COUNT = mw(40)  # bottles in the current group, 0-6 (Group_Counter.CV)
PACKS_IN_OVEN = mw(41)  # Oven_Counter.CV (CTUD)
FEED_PULSES = mw(42)  # pulses for one sheet: LIMIT(600, FilmLength, 1200) / 50
FEED_COUNT = mw(43)  # pulses fed for the current sheet (kept through a stop)
COMPUTED_START = GROUP_COUNT  # one bulk read of %MW40..%MW43
COMPUTED_COUNT = 4

ALM_FILM_FAULT = mx(5, 0)  # FilmLength not counted in 10 s, or no sheet in READY for 1 s -> FAULT
ALM_PACK_JAM = mx(5, 1)  # PUSH / RETRACT / RUN_ON / RUN_OUT not finished in 3 s -> FAULT
ALM_OUTFEED_BLOCKED = mx(5, 2)  # Blocked > 2 min, warning
ALM_COMM_LOST = mx(5, 3)  # same bit in every PLC
ALM_FILM_EMPTY = mx(5, 4)  # film empty and a pack waits, warning, not latched
ALM_OVEN_OVER_TEMP = mx(5, 5)  # OvenTemp >= OvenMax in every mode -> FAULT
ALM_OVEN_HEAT_TIMEOUT = mx(5, 6)  # Oven_Ready not reached in WARM_UP within 120 s -> FAULT
ALM_OVEN_JAM = mx(5, 7)  # packs in the oven and no Oven_Exit for OvenJamTime -> FAULT
ALARMS_START = ALM_FILM_FAULT  # first bulk read: the 8 alarm coils of %MX5
ALARMS_COUNT = 8

ALM_AIRFLOW_LOST = mx(7, 0)  # blower on and no Airflow_OK within 3 s -> FAULT
ALM_WRAP_TIMEOUT = mx(7, 1)  # wrap bar not back home within 5 s -> FAULT
EXTRA_ALARMS_START = ALM_AIRFLOW_LOST  # second bulk read: the 2 alarm coils of %MX7
EXTRA_ALARMS_COUNT = 2

# --- Written by the PLC, read only by the HMI and tests: conditioned values ------
OUTFEED_FULL = mx(3, 0)  # Table_Full filtered (2 s on, 3 s off)
GROUP_COMPLETE = mx(3, 1)  # Group_Counter reached 6: the gate closes
JOG_BLOCK = mx(3, 2)  # jog watchdog
HEAT_DEMAND = mx(3, 3)  # heater hysteresis latch
OVEN_READY = mx(3, 4)  # OvenTemp inside OvenSetpoint +- OvenBand
STARVED = mx(3, 5)  # wrap waits and (no bottle at the gate for 2 s, or film empty)
WRAP_CLEAR = mx(3, 6)  # wrap conveyor ran empty for ClearTime: the next group may be pushed
OVEN_ON = mx(3, 7)  # Master_Enable AND Auto AND no FAULT
CONDITIONED_START = OUTFEED_FULL  # one bulk read of %MX3.0-3.7
CONDITIONED_COUNT = 8

# --- Written by the HMI: the simulator NEVER writes these ------------------------
CMD = mw(0)  # 1 Start, 2 Stop, 3 Reset, 4 Reset counter
MODE_REQ = mw(1)  # 0 Stop, 1 Auto, 2 Manual

OVEN_SETPOINT = mw(30)  # 0.1 degC
OVEN_HYST = mw(31)  # 0.1 degC
OVEN_BAND = mw(32)  # 0.1 degC, Oven_Ready window
FILM_LENGTH = mw(33)  # mm, recipe value
SETPOINTS_START = OVEN_SETPOINT
SETPOINTS_COUNT = 4

MAN_C3 = mx(2, 0)  # manual buttons (hold-to-run)
MAN_GATE = mx(2, 1)
MAN_GROUP_PUSH = mx(2, 2)
MAN_WRAP_CONVEYOR = mx(2, 3)
MAN_FILM_FEED = mx(2, 4)
MAN_KNIFE = mx(2, 5)
MAN_WRAP_BAR = mx(2, 6)
MAN_OVEN_CONVEYOR = mx(2, 7)

# Animation words (%MW70-79) are kept free: they are decided with the HMI plan (part C),
# as for the labeler, and get their constants then.

# --- Ownership: the only addresses the simulator is allowed to write -------------
SIM_WRITE_COILS = frozenset(SENSOR_BLOCK + EXTRA_SENSOR_BLOCK)
SIM_WRITE_REGISTERS = frozenset(range(ANALOG_START, ANALOG_START + ANALOG_COUNT))

# Cheap checks at import: the bulk blocks are contiguous and owned by the simulator
assert list(SENSOR_BLOCK) == list(range(SENSOR_BLOCK_START, SENSOR_BLOCK_START + 11))
assert list(EXTRA_SENSOR_BLOCK) == list(range(EXTRA_SENSOR_BLOCK_START, EXTRA_SENSOR_BLOCK_START + 6))
assert {BOTTLE_AT_GATE, WRAP_HOME, UNUSED_MX1_0, E_STOP_OK, HEARTBEAT, FILM_EMPTY, AIRFLOW_OK} <= SIM_WRITE_COILS
assert OVEN_CONVEYOR == OUTPUTS_START + OUTPUTS_COUNT - 1
assert ALM_OVEN_JAM == ALARMS_START + ALARMS_COUNT - 1
assert ALM_WRAP_TIMEOUT == EXTRA_ALARMS_START + EXTRA_ALARMS_COUNT - 1
assert OVEN_ON == CONDITIONED_START + CONDITIONED_COUNT - 1
assert FEED_COUNT == COMPUTED_START + COMPUTED_COUNT - 1
assert FILM_LENGTH == SETPOINTS_START + SETPOINTS_COUNT - 1
