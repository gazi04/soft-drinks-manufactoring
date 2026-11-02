"""Modbus addresses of the filler PLC (source: docs/filler/io-list.md).

Every address is written in its IEC form (%QX, %MX, %MW) and converted to the
Modbus number with the OpenPLC v4 mapping below, so the code reads like the I/O
list and no number has to be typed by hand.

The addresses are grouped by WHO WRITES them. The simulator may only write the
addresses in SIM_WRITE_COILS and SIM_WRITE_REGISTERS (one writer per address).
"""

# --- OpenPLC v4 mapping (verified 2026-10-28 with the smoke test) -----------------
MX_COIL_OFFSET = 8192  # %MX0.0 is coil 8192 (after the 8192 %QX coils)
MW_REGISTER_OFFSET = 1024  # %MW0 is holding register 1024 (after the 1024 %QW registers)


def qx(byte: int, bit: int) -> int:
    """%QXbyte.bit -> coil number (PLC output)."""
    return byte * 8 + bit


def mx(byte: int, bit: int) -> int:
    """%MXbyte.bit -> coil number (Boolean memory)."""
    return MX_COIL_OFFSET + byte * 8 + bit


def mw(n: int) -> int:
    """%MWn -> holding register number (16-bit memory word)."""
    return MW_REGISTER_OFFSET + n


# --- Written by the PLC, read by the simulator: actuator outputs -----------------
CONVEYOR1 = qx(0, 0)  # ConveyerMotor1, C1 infeed conveyor
DOOR_MOTOR = qx(0, 1)  # entry door
RINSE_VALVE = qx(0, 2)
FILL_VALVE = qx(0, 3)
CAP_MOTOR = qx(0, 4)
INDEX_MOTOR = qx(0, 5)  # internal index belt

OUTPUTS_START = CONVEYOR1  # one bulk read of coils 0..5
OUTPUTS_COUNT = 6

# --- Written by the simulator: sensors -------------------------------------------
S2 = mx(0, 0)  # bottle waiting at the entry door
S3 = mx(0, 1)  # bottle at the rinse station
S4 = mx(0, 2)  # bottle at the fill station
S5 = mx(0, 3)  # bottle at the cap station
S6 = mx(0, 4)  # bottle discharged (pulse)
S7 = mx(0, 5)  # C2 full sensor
S8 = mx(0, 6)  # index cam, ON once per station pitch
UNUSED_MX0_7 = mx(0, 7)  # not used by the PLC; written 0 to keep the bulk write in one block

FILL_LEVEL = mw(12)  # ml in the bottle at the fill station

# --- Written by the simulator: signals from other machines ("wiring") ------------
PRODUCT_AVAILABLE = mx(1, 0)  # from the syrup room; held TRUE until it exists
E_STOP_OK = mx(1, 1)  # E-stop chain healthy
HEARTBEAT = mx(1, 2)  # toggled by the simulator, watched by the PLC

# One bulk write per tick: coils 8192..8202 in this order.
SENSOR_BLOCK_START = S2
SENSOR_BLOCK = (S2, S3, S4, S5, S6, S7, S8, UNUSED_MX0_7, PRODUCT_AVAILABLE, E_STOP_OK, HEARTBEAT)

# --- Written by the PLC, read by the simulator for the event log -----------------
STATE = mw(50)  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault
STEP = mw(51)  # 0 IDLE, 10 WORK, 15 WAIT_OUTFEED, 20 INDEX, 900 FAULT
GOOD_COUNT = mw(53)  # bottles discharged
STATUS_START = STATE  # one bulk read of %MW50..%MW53 (%MW52 is unused)
STATUS_COUNT = 4

ALM_INDEX_JAM = mx(5, 0)
ALM_FILL_TIMEOUT = mx(5, 1)
ALM_OUTFEED_BLOCKED = mx(5, 2)
ALM_COMM_LOST = mx(5, 3)
ALARMS_START = ALM_INDEX_JAM  # one bulk read of the 4 alarm coils
ALARMS_COUNT = 4

# --- Written by the HMI: the simulator NEVER writes these ------------------------
CMD = mw(0)  # 1 Start, 2 Stop, 3 Reset
MODE_REQ = mw(1)  # 0 Stop, 1 Auto, 2 Manual
FILL_SETPOINT = mw(30)  # ml; also read by the simulator for the underfill check
RINSE_TIME = mw(31)  # ms
CAP_TIME = mw(32)  # ms

MAN_C1 = mx(2, 0)  # manual buttons (hold-to-run)
MAN_DOOR = mx(2, 1)
MAN_RINSE = mx(2, 2)
MAN_FILL = mx(2, 3)
MAN_CAP = mx(2, 4)
MAN_INDEX = mx(2, 5)

# --- Ownership: the only addresses the simulator is allowed to write -------------
SIM_WRITE_COILS = frozenset(SENSOR_BLOCK)
SIM_WRITE_REGISTERS = frozenset({FILL_LEVEL})
