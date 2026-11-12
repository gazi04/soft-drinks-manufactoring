"""Modbus addresses of the syrup room PLC (source: docs/syrup/io-list.md).

Same idea as address_map.py (the filler): every address is written in its IEC form
(%QX, %MX, %MW) and converted with the OpenPLC v4 mapping, so the code reads like the
I/O list. The converters are imported from address_map: the mapping exists once.

The syrup room is its own PLC (runtime plc-syrup-carb, Modbus port 5022), so its
addresses overlap the filler's on purpose (e.g. %MX1.0 is the filler's Product_Available
input and unused here). Never mix the two modules in one request.

The addresses are grouped by WHO WRITES them. The simulator may only write the
addresses in SIM_WRITE_COILS and SIM_WRITE_REGISTERS (one writer per address).

Differences to the filler that matter when reusing its code:
- 6 alarms (%MX5.0-5.5) instead of 4;
- 5 analog sensors (INT, fixed point: L, 0.1 degC, 0.01 bar) instead of one FillLevel;
- Product_Available is an OUTPUT here (%MX6.0, written by the PLC, copied to the filler).
"""

from simulator.address_map import mw, mx, qx

# --- Written by the PLC, read by the simulator: actuator outputs -----------------
WATER_PUMP = qx(0, 0)  # mains water -> mixing tank
SYRUP_PUMP = qx(0, 1)  # syrup tank -> mixing tank
AGITATOR = qx(0, 2)
COOLING_VALVE = qx(0, 3)
CO2_VALVE = qx(0, 4)
TRANSFER_PUMP = qx(0, 5)  # mixing tank -> buffer tank

OUTPUTS_START = WATER_PUMP  # one bulk read of coils 0..5
OUTPUTS_COUNT = 6

# --- Written by the simulator: digital sensors and wiring ------------------------
MIX_HIGH_HIGH = mx(0, 0)  # high-high level switch on the mixing tank (hardware backup)
UNUSED_MX1_0 = mx(1, 0)  # the filler's Product_Available input; not used here, written FALSE
E_STOP_OK = mx(1, 1)  # E-stop chain healthy
HEARTBEAT = mx(1, 2)  # toggled by the simulator, watched by the PLC

# One bulk write per tick: coils 8192..8202. %MX0.1-0.7 (spare) are written FALSE.
SENSOR_BLOCK_START = MIX_HIGH_HIGH
SENSOR_BLOCK = tuple(range(SENSOR_BLOCK_START, HEARTBEAT + 1))

# --- Written by the simulator: analog sensors (INT) ------------------------------
SYRUP_LEVEL = mw(12)  # L, syrup tank
MIX_LEVEL = mw(13)  # L, mixing tank
PRODUCT_TEMP = mw(14)  # 0.1 degC, e.g. 40 = 4.0 degC
CO2_PRESSURE = mw(15)  # 0.01 bar, e.g. 350 = 3.50 bar
BUFFER_LEVEL = mw(16)  # L, buffer tank

ANALOG_START = SYRUP_LEVEL  # one bulk write of %MW12..16 per tick
ANALOG_COUNT = 5

# --- Written by the PLC, read by the simulator: handshake to the filler ----------
PRODUCT_AVAILABLE_OUT = mx(6, 0)  # copied by the simulator to the filler's %MX1.0 (stage 3)

# --- Written by the PLC, read by the simulator for the event log -----------------
STATE = mw(50)  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault
STEP = mw(51)  # 0 IDLE, 5 WAIT_DEMAND, 10 DOSE_WATER, 20 DOSE_SYRUP, 30 MIX, 40 COOL,
#                50 CARBONATE, 55 WAIT_BUFFER, 60 TRANSFER, 900 FAULT
BATCH_COUNT = mw(53)  # batches transferred to the buffer
STATUS_START = STATE  # one bulk read of %MW50..%MW53 (%MW52 is unused)
STATUS_COUNT = 4

ALM_OVERFLOW = mx(5, 0)
ALM_OVER_PRESSURE = mx(5, 1)
ALM_COOL_TIMEOUT = mx(5, 2)
ALM_COMM_LOST = mx(5, 3)  # same bit in every PLC
ALM_LOW_SYRUP = mx(5, 4)  # warning only, not latched
ALM_DOSE_TIMEOUT = mx(5, 5)
ALARMS_START = ALM_OVERFLOW  # one bulk read of the 6 alarm coils
ALARMS_COUNT = 6

# --- Written by the PLC, read only by the HMI and tests: conditioned values ------
TEMP_OK = mx(3, 0)  # cooling hysteresis latch
CO2_DEMAND = mx(3, 1)  # CO2 valve hysteresis latch
JOG_BLOCK = mx(3, 2)  # jog watchdog
BATCH_DOSED = mx(3, 3)  # water and syrup of the batch in the tank are dosed
SYRUP_TARGET = mw(40)  # L, WaterBatch * 10 / SyrupRatio
DOSE_TARGET = mw(41)  # L, WaterBatch + SyrupTarget

# --- Written by the HMI: the simulator NEVER writes these ------------------------
CMD = mw(0)  # 1 Start, 2 Stop, 3 Reset
MODE_REQ = mw(1)  # 0 Stop, 1 Auto, 2 Manual

WATER_BATCH = mw(30)  # L
SYRUP_RATIO = mw(31)  # x10, 55 = 5.5:1
MIX_TIME = mw(32)  # s
COOL_SETPOINT = mw(33)  # 0.1 degC
TEMP_HYST = mw(34)  # 0.1 degC
PRESS_SETPOINT = mw(35)  # 0.01 bar
PRESS_HYST = mw(36)  # 0.01 bar
BUFFER_LOW = mw(37)  # L
BUFFER_MIN = mw(38)  # L
CARB_TIME = mw(39)  # s
SETPOINTS_START = WATER_BATCH
SETPOINTS_COUNT = 10

MAN_WATER_PUMP = mx(2, 0)  # manual buttons (hold-to-run)
MAN_SYRUP_PUMP = mx(2, 1)
MAN_AGITATOR = mx(2, 2)
MAN_COOLING = mx(2, 3)
MAN_CO2 = mx(2, 4)
MAN_TRANSFER = mx(2, 5)

# --- Ownership: the only addresses the simulator is allowed to write -------------
SIM_WRITE_COILS = frozenset(SENSOR_BLOCK)
SIM_WRITE_REGISTERS = frozenset(range(ANALOG_START, ANALOG_START + ANALOG_COUNT))

# Cheap checks at import: the bulk blocks are contiguous and owned by the simulator
assert list(SENSOR_BLOCK) == list(range(SENSOR_BLOCK_START, SENSOR_BLOCK_START + 11))
assert {MIX_HIGH_HIGH, UNUSED_MX1_0, E_STOP_OK, HEARTBEAT} <= SIM_WRITE_COILS
assert CARB_TIME == SETPOINTS_START + SETPOINTS_COUNT - 1
