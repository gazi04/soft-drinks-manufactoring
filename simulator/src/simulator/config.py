"""Simulator parameters (default values).

All times are in seconds, unless the name says otherwise. Every value must fit with
the times in the filler PLC program (docs/filler/state-diagram.md); the comment
names the PLC time it is checked against.

These are start values only. Values that the control panel changes at runtime
(for example the supply rate) are copied into the objects that use them, so this
file never changes while the simulator runs.
"""

# --- Connection -------------------------------------------------------------------
PLC_HOST = "127.0.0.1"  # later "plc-filler" inside the Docker network
PLC_PORT = 502  # later 5023 from the host (report section 6.1)

# --- Tick loop --------------------------------------------------------------------
TICK_S = 0.1  # one simulator cycle; the PLC scans every 20 ms, so ~5 scans per tick
SUBSTEP_S = 0.1  # a longer (late) tick is split into physics steps of at most this length
SLOW_TICK_S = 0.3  # a tick longer than this is logged as a SIM warning: the PLC outputs were old
#                    that long, so the physics may differ from what the PLC really did

# --- Conveyors C1, C2 -------------------------------------------------------------
CONVEYOR_SLOTS = 8  # bottle places per conveyor (also the HMI animation slots)
SLOT_STEP_S = 0.5  # time a bottle needs to move one slot while the motor runs
S7_SLOT = 1  # C2 slot watched by S7 (C2 full). Not slot 0: when the queue stands at slot 1,
#             slot 0 still has room for the one bottle that may already be on its way while
#             the PLC's 2 s Outfeed_Full filter runs (a 1.5 s WORK is shorter than 2 s)

# --- Filler: internal index belt --------------------------------------------------
INDEX_PITCH_S = 1.5  # time for one station pitch; must be well below the 5 s index jam alarm
CAM_WINDOW = 0.15  # S8 is ON in the first 15 % of each pitch (0.225 s at 1.5 s/pitch),
#                    long enough to cover the PLC's reaction delay after the S8 edge
STATION_WINDOW = 0.4  # S3-S5 see their bottle in the first 40 % of a pitch (0.6 s of belt overrun).
#                       Wider than the cam: a late tick lets the belt run on with old outputs, and
#                       S3-S5 must not go dark then, or the PLC would skip half-processed bottles

# --- Filler: entry door -----------------------------------------------------------
DOOR_PASS_S = 0.3  # time a bottle needs to pass the open door; the PLC's door TP is 0.5 s,
#                    so one pulse admits exactly one bottle (two would need 0.6 s)

# --- Filler: stations -------------------------------------------------------------
FILL_FLOW_ML_S = 100.0  # fill flow; 500 ml take 5 s, well below the 10 s fill timeout
DEFAULT_FILL_SETPOINT_ML = 500  # used for the underfill check until the PLC's FillSetpoint is read
RINSE_MIN_S = 1.5  # rinse water a bottle needs to count as rinsed; PLC RinseTime is 2 s
CAP_MIN_S = 1.0  # cap head time a bottle needs to count as capped; PLC CapTime is 1.5 s
S6_PULSE_S = 0.3  # discharge sensor pulse; at least one full tick, so the PLC sees the edge

# --- Interface signals ------------------------------------------------------------
HEARTBEAT_S = 1.0  # heartbeat toggle period; the PLC's watchdog fires after 3 s without change

# --- Neighbour stand-ins ----------------------------------------------------------
SUPPLY_PER_MIN = 30  # bottles per minute put onto C1 (replaces the blow moulder)
LABELER_TAKE_S = 2.0  # the labeler stand-in takes one bottle from the end of C2 every 2 s
