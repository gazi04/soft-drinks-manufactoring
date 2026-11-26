"""Modbus addresses of the blow moulder PLC (source: docs/blowmoulder/io-list.md).

Same idea as address_map.py (the filler) and the other machines' maps: every address is
written in its IEC form (%QX, %QW, %MX, %MW) and converted with the OpenPLC v4 mapping, so
the code reads like the I/O list. The converters are imported from address_map: the mapping
exists once.

The blow moulder is its own PLC (runtime plc-blowmolder, Modbus port 5025), so its addresses
overlap the other PLCs' on purpose (e.g. %MX1.0 is the filler's Product_Available input and
unused here). Never mix the modules in one request.

The addresses are grouped by WHO WRITES them. The simulator may only write the
addresses in SIM_WRITE_COILS and SIM_WRITE_REGISTERS (one writer per address).

Differences to the other machines that matter when reusing their code:
- the first ANALOG OUTPUT: LampPower at %QW0 = holding register 0 (0-100 %). The simulator
  READS it every tick together with the 10 digital outputs (two requests: coils + HR 0);
  it never writes it (the PLC overwrites a Modbus write anyway, ladder stage 6);
- the first RAW analog input: BlowPressure_Raw (%MW13) in counts 0-27648 = 0-40.0 bar,
  SIGNED: a wire break is a negative value (two's complement on the wire);
- two analog sensors (%MW12-13) in one write, 13 digital sensors (%MX0 + five in %MX4);
- two sequencers: Step (%MW51, oven station) and MouldStep (%MW52, mould station);
- 11 alarms: 8 in %MX5, three more in %MX7.0-7.2 (two bulk reads);
- 8 conditioned bits (%MX3.0-3.7) and 3 computed words (%MW40-42);
- 5 setpoints (%MW30-34); Cmd = 4 resets BottleCount (Cmd 1-3 as in every PLC).
"""

from simulator.address_map import mw, mx, qw, qx

# --- Written by the PLC, read by the simulator: actuator outputs -----------------
HOPPER_FEED = qx(0, 0)  # hopper elevator / chute: runs while no preform waits at the loader
LOADER = qx(0, 1)  # pushes the waiting preform into the oven pocket, spring return when OFF
ARM_TO_MOULD = qx(0, 2)  # transfer arm: ON = into the mould, OFF = home above the oven pocket
GRIPPER = qx(0, 3)  # gripper on the arm: ON = closed
MOULD_CLOSE = qx(0, 4)  # mould halves: ON = close and lock, OFF = open (double acting)
STRETCH_ROD = qx(0, 5)  # ON = rod down, spring return
PRE_BLOW_VALVE = qx(0, 6)  # low-pressure air (8 bar regulator) during the stretch
HIGH_BLOW_VALVE = qx(0, 7)  # high-pressure air (high-blow regulator)
EXHAUST_VALVE = qx(1, 0)  # vents the bottle; normally open (ON except STRETCH, BLOW, HOLD)
EJECTOR = qx(1, 1)  # pushes the bottle out of the open mould onto C1

OUTPUTS_START = HOPPER_FEED  # one bulk read of coils 0..9
OUTPUTS_COUNT = 10

LAMP_POWER = qw(0)  # IR lamp power 0-100 % (P controller), HR 0: a second read per tick
LAMP_POWER_MAX = 100

# --- Written by the simulator: digital sensors (%MX0) and wiring (%MX1) ----------
PREFORM_AT_LOADER = mx(0, 0)  # a preform waits at the loader (end of the hopper chute)
PREFORM_IN_OVEN = mx(0, 1)  # a preform sits in the oven pocket
ARM_AT_OVEN = mx(0, 2)  # arm home above the oven pocket, out of the mould
ARM_AT_MOULD = mx(0, 3)  # arm inside the open mould
MOULD_CLOSED = mx(0, 4)  # mould closed and locked
MOULD_OPEN = mx(0, 5)  # mould fully open
ROD_UP = mx(0, 6)
ROD_DOWN = mx(0, 7)
UNUSED_MX1_0 = mx(1, 0)  # the filler's Product_Available input; not used here, written FALSE
E_STOP_OK = mx(1, 1)  # E-stop chain healthy
HEARTBEAT = mx(1, 2)  # toggled by the simulator, watched by the PLC

# First bulk write per tick: coils 8192..8202.
SENSOR_BLOCK_START = PREFORM_AT_LOADER
SENSOR_BLOCK = tuple(range(SENSOR_BLOCK_START, HEARTBEAT + 1))

# --- Written by the simulator: the five extra sensors (%MX4) ---------------------
BOTTLE_IN_MOULD = mx(4, 0)  # eye across the mould: a preform or bottle is in it
BOTTLE_EJECTED = mx(4, 1)  # eye at C1's entry: one pulse per bottle (BottleCount)
HOPPER_EMPTY = mx(4, 2)
PREFORM_LOW = mx(4, 3)  # warning in FUXA only
C1_FULL = mx(4, 4)  # S1, raw (filtered into Outfeed_Full by the PLC)

# Second bulk write per tick: coils 8224..8228 (%MX4.5-4.7 spare, not written).
EXTRA_SENSOR_BLOCK_START = BOTTLE_IN_MOULD
EXTRA_SENSOR_BLOCK = tuple(range(EXTRA_SENSOR_BLOCK_START, C1_FULL + 1))

# --- Written by the simulator: analog sensors (INT) ------------------------------
OVEN_TEMP = mw(12)  # preform surface temperature, 0.1 degC, e.g. 1050 = 105.0 degC
BLOW_PRESSURE_RAW = mw(13)  # transducer counts, SIGNED: 0-27648 = 0-40.0 bar, < 0 = wire break
ANALOG_START = OVEN_TEMP  # one write of %MW12..%MW13 per tick
ANALOG_COUNT = 2

RAW_FULL = 27648  # counts at 40.0 bar (20 mA), the PLC's RawFull
PRESS_FULL = 400  # 0.1 bar at RAW_FULL, the PLC's PressFull

# --- Written by the PLC, read by the simulator for the event log -----------------
STATE = mw(50)  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault (both stations)
STEP = mw(51)  # oven station: 0 IDLE, 10 FEED, 15 LOAD, 20 HEAT, 25 HOT, 900 FAULT
MOULD_STEP = mw(52)  # mould station: 0 IDLE, 10 WAIT_PREFORM, 20 GRIP, 22 SWING, 24 RELEASE,
#                      26 ARM_BACK, 30 CLOSE, 40 STRETCH, 50 BLOW, 52 HOLD, 60 EXHAUST, 62 ROD_UP,
#                      70 OPEN, 80 EJECT, 900 FAULT
BOTTLE_COUNT = mw(53)  # bottles put onto C1; zeroed by Cmd = 4
STATUS_START = STATE  # one bulk read of %MW50..%MW53
STATUS_COUNT = 4

LAMP_CALC = mw(40)  # lamp power the P controller asks for, % (LampPower follows it with Lamps_On)
BLOW_PRESSURE = mw(41)  # BlowPressure_Raw scaled to 0.1 bar (signed)
OVEN_ERROR = mw(42)  # OvenSetpoint - OvenTemp, 0.1 degC (signed)
COMPUTED_START = LAMP_CALC  # one bulk read of %MW40..%MW42
COMPUTED_COUNT = 3

ALM_NO_PREFORMS = mx(5, 0)  # hopper empty and the oven waits, warning, not latched
ALM_HANDLING_JAM = mx(5, 1)  # loader / arm / ejector not at its sensor in time -> FAULT
ALM_OUTFEED_BLOCKED = mx(5, 2)  # Blocked > 2 min, warning
ALM_COMM_LOST = mx(5, 3)  # same bit in every PLC
ALM_MOULD_FAULT = mx(5, 4)  # mould / rod not at its sensor in time -> FAULT
ALM_OVEN_OVER_TEMP = mx(5, 5)  # OvenTemp >= OvenMax in every mode -> FAULT
ALM_OVEN_HEAT_TIMEOUT = mx(5, 6)  # Preform_Hot not reached in HEAT within 15 s -> FAULT
ALM_BLOW_FAULT = mx(5, 7)  # BlowSetpoint not reached in BLOW within 2 s -> FAULT
ALARMS_START = ALM_NO_PREFORMS  # first bulk read: the 8 alarm coils of %MX5
ALARMS_COUNT = 8

ALM_EXHAUST_FAULT = mx(7, 0)  # Pressure_Safe not reached in EXHAUST within 3 s -> FAULT
ALM_OVER_PRESSURE = mx(7, 1)  # BlowPressure >= MaxPressure -> FAULT
ALM_PRESSURE_SENSOR = mx(7, 2)  # raw outside -1000..32511 (wire break / over range) -> FAULT
EXTRA_ALARMS_START = ALM_EXHAUST_FAULT  # second bulk read: the 3 alarm coils of %MX7
EXTRA_ALARMS_COUNT = 3

# --- Written by the PLC, read only by the HMI and tests: conditioned values ------
OUTFEED_FULL = mx(3, 0)  # C1_Full filtered (2 s on, 3 s off)
OVEN_READY = mx(3, 1)  # OvenTemp inside OvenSetpoint +- OvenBand
JOG_BLOCK = mx(3, 2)  # jog watchdog
PREFORM_HOT = mx(3, 3)  # inside Oven_Ready for SoakTime
PRESSURE_REACHED = mx(3, 4)  # BlowPressure >= BlowSetpoint
PRESSURE_SAFE = mx(3, 5)  # BlowPressure < ExhaustLimit and the sensor in range
STARVED = mx(3, 6)  # no preform at the loader for 2 s while the mould waits too
LAMPS_ON = mx(3, 7)  # Master_Enable AND Auto AND no FAULT AND Preform_In_Oven
CONDITIONED_START = OUTFEED_FULL  # one bulk read of %MX3.0-3.7
CONDITIONED_COUNT = 8

# --- Written by the HMI: the simulator NEVER writes these ------------------------
CMD = mw(0)  # 1 Start, 2 Stop, 3 Reset, 4 Reset counter
MODE_REQ = mw(1)  # 0 Stop, 1 Auto, 2 Manual

OVEN_SETPOINT = mw(30)  # 0.1 degC
OVEN_BAND = mw(31)  # 0.1 degC, Oven_Ready window
KP = mw(32)  # % per degC
BIAS = mw(33)  # %
BLOW_SETPOINT = mw(34)  # 0.1 bar
SETPOINTS_START = OVEN_SETPOINT
SETPOINTS_COUNT = 5

MAN_HOPPER_FEED = mx(2, 0)  # manual buttons (hold-to-run, the mould a toggle)
MAN_LOADER = mx(2, 1)
MAN_ARM = mx(2, 2)
MAN_GRIPPER = mx(2, 3)
MAN_MOULD_CLOSE = mx(2, 4)
MAN_ROD = mx(2, 5)
MAN_EJECTOR = mx(2, 6)

# --- Ownership: the only addresses the simulator is allowed to write -------------
# The animation words (%MW70-79) are added with the HMI plan (part C), as for the labeler and the packer.
SIM_WRITE_COILS = frozenset(SENSOR_BLOCK + EXTRA_SENSOR_BLOCK)
SIM_WRITE_REGISTERS = frozenset(range(ANALOG_START, ANALOG_START + ANALOG_COUNT))

# Cheap checks at import: the bulk blocks are contiguous and owned by the simulator
assert list(SENSOR_BLOCK) == list(range(SENSOR_BLOCK_START, SENSOR_BLOCK_START + 11))
assert list(EXTRA_SENSOR_BLOCK) == list(range(EXTRA_SENSOR_BLOCK_START, EXTRA_SENSOR_BLOCK_START + 5))
assert {PREFORM_AT_LOADER, ROD_DOWN, UNUSED_MX1_0, E_STOP_OK, HEARTBEAT, BOTTLE_IN_MOULD, C1_FULL} <= SIM_WRITE_COILS
assert EJECTOR == OUTPUTS_START + OUTPUTS_COUNT - 1
assert ALM_BLOW_FAULT == ALARMS_START + ALARMS_COUNT - 1
assert ALM_PRESSURE_SENSOR == EXTRA_ALARMS_START + EXTRA_ALARMS_COUNT - 1
assert LAMPS_ON == CONDITIONED_START + CONDITIONED_COUNT - 1
assert OVEN_ERROR == COMPUTED_START + COMPUTED_COUNT - 1
assert BLOW_SETPOINT == SETPOINTS_START + SETPOINTS_COUNT - 1
assert BLOW_PRESSURE_RAW == ANALOG_START + ANALOG_COUNT - 1
assert LAMP_POWER not in SIM_WRITE_REGISTERS  # the analog output belongs to the PLC
