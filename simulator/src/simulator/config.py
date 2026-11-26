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
PLC_PORT = 502  # the filler keeps 502 (report section 6.1, addresses in build order)
SYRUP_PORT = 5022  # the syrup room's own runtime (plc-syrup-carb), same host
LABELER_PORT = 5023  # the labeler's own runtime (plc-labeler), same host
PACKER_PORT = 5024  # the packer's own runtime (plc-packer), same host

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

# --- Syrup room: tanks and pumps (docs/syrup/state-diagram.md, constants in the PLC's main.ld) -----
SYRUP_TANK_L = 1000.0  # syrup storage tank capacity
MIX_TANK_L = 1000.0  # mixing tank / carbonator capacity; PLC MixMax 950 L is the interlock, this the steel
BUFFER_TANK_L = 2000.0  # buffer tank capacity; PLC BufferMax 1950 L
MIX_HIGH_HIGH_L = 980.0  # MixHighHigh switch: above the PLC's MixMax (950 L), below the brim
AGITATOR_MIN_L = 100.0  # propeller uncovered below this (PLC AgitatorMin); only used for the audit
# Flows. The PLC doses by ABSOLUTE level (water to 500 L, syrup to 590 L) and sees one level per
# tick, so a pump overshoots by up to flow x 0.1 s: a small flow keeps the 5.5:1 ratio accurate
# (water <= 2 L, syrup <= 0.5 L). Each dose must stay far below the PLC's DoseTimeout (60 s).
WATER_FLOW_L_S = 20.0  # 500 L in 25 s
SYRUP_FLOW_L_S = 5.0  # 90 L in 18 s
TRANSFER_FLOW_L_S = 25.0  # a 590 L batch down to MixEmpty (5 L) in 23.4 s
# Start state: buffer below BufferLow (800 L) so the first batch starts at once, and above
# BufferMin + AvailBand (120 L) so Product_Available is TRUE.
SYRUP_START_L = 600.0
BUFFER_START_L = 500.0
MIX_EMPTY_L = 5.0  # the mixing tank counts as empty at or below this (PLC MixEmpty)

# --- Syrup room: temperature (PLC: CoolSetpoint 4.0 degC, TempHyst 0.5 degC, CoolTimeout 60 s) ------
ROOM_C = 20.0  # room temperature: syrup is stored here, and it slowly warms the mixing tank
ROOM_TAU_S = 600.0  # heat leaks in slowly: 0.5 degC take about 19 s at 4 degC, so the PLC's band is real
WATER_TEMP_C = 15.0  # mains water
SYRUP_TEMP_C = 20.0  # syrup tank, room temperature
COOLANT_C = 0.0  # chiller; "chiller failed" (stage 6) raises it
COOLANT_FAILED_C = 20.0  # failed chiller: the jacket only has room temperature, cooling does nothing
COOL_TAU_S = 20.0  # jacket: 15.8 -> 4.0 degC in about 29 s, well below CoolTimeout (60 s)

# --- Syrup room: CO2 pressure (PLC: PressSetpoint 3.50 bar, PressHyst 0.20 bar, CarbTime 10 s, PressMax 6 bar)
CO2_FILL_BAR_S = 0.5  # CO2 valve open: pressure rise per second
DISSOLVE_K_STIRRED = 0.10  # 1/s, liquid takes CO2 up this fast with the agitator on ...
DISSOLVE_K_STILL = 0.01  # ... and 10x slower without it
HEADSPACE_GAIN = 0.286  # bar of headspace pressure lost per bar of CO2 pressure the liquid gains.
#                         With the two values above: pressure falls fast at first, so the PLC's valve
#                         opens 2-3 times, and holds after about 20 s, so Carb_Timer (10 s) can finish.
VENT_TAU_S = 5.0  # transfer pump running: the headspace empties into the buffer, pressure -> 0
PRESS_RELIEF_BAR = 8.0  # relief valve lifts here (the PLC faults at PressMax 6.00 bar long before)

# --- Syrup room <-> filler ------------------------------------------------------------------
BUFFER_DRAW_SCALE = 30.0  # litres taken from the buffer per litre the filler fills (a 500 ml bottle = 15 L).
#                           Without it a 590 L batch would last hours. The batch only arrives at the END of its
#                           cycle (about 145 s), so the buffer must carry the filler through the whole cycle:
#                           measured with the whole-line test (600 s): x50 starves the filler after 114 s,
#                           x40 keeps only 24 L above Product_Available's reset, x30 keeps 113 L (lowest level
#                           233 L, highest 1049 L, batches wait in WAIT_DEMAND now and then).

# --- Labeler: melt tank (docs/labeler/state-diagram.md rule 4; PLC: GlueSetpoint 170.0 degC, GlueHyst 5.0,
#     GlueBand 10.0, HeatTimeout 120 s, GlueMax 200.0 degC) ------------------------------------------------
GLUE_HEATER_C = 300.0  # heater pulls toward this ...
GLUE_HEAT_TAU_S = 60.0  # ... with this time constant; with the room loss the balance is 274.5 degC:
#                         20 -> 170 degC in 48.5 s (HeatTimeout 120 s); a stuck heater reaches GlueMax
#                         (200 degC) 18 s after 170 degC, so the over-temperature scenario is reachable
GLUE_ROOM_TAU_S = 600.0  # heat loss to the room: 170 -> 165 degC in 20 s with the heater off
GLUE_STICK_MIN_C = 150.0  # below this the glue does not hold a label (PLC window starts at 160 degC)
GLUE_SMOKE_C = 200.0  # audit warning; the PLC faults at GlueMax 200.0 degC
GLUE_TANK_KG = 5.0
GLUE_START_KG = 4.0
GLUE_LOW_KG = 1.0  # Glue_Low below this
GLUE_PER_LABEL_G = 1.0  # glue on one label (both edges)

# --- Labeler: label web (PLC: LabelTimeout 5 s, CutPulse 0.2 s) ---------------------------------------------
LABEL_LENGTH_MM = 210.0  # one label: a 65 mm bottle (204 mm round) plus overlap
LABEL_FEED_MM_S = 200.0  # web speed = bottle surface speed at about 1 turn/s: 1.05 s per label (LabelTimeout 5 s)
LABEL_MARK_PULSE_S = 0.3  # Label_Mark pulse; at least one full tick, so the PLC sees the edge (as S6)
ROLL_LABELS = 1000  # labels on a new roll
ROLL_LOW_LABELS = 50  # Roll_Low at or below this
ROLL_TAIL_MM = 40.0  # web after the roll's last mark: the PLC stops the feed 1-2 ticks after the Label_Mark edge
# (20-40 mm run-on), so the last label needs this much web past its mark to be whole (Roll_Empty is ON at the mark)
KNIFE_MISSED_FACTOR = 1.5  # audit: more than 1.5 label lengths without a cut = the knife did not cut

# --- Labeler: pocket (PLC: GatePulse 0.5 s, ClampTimeout 2 s, LabelTimeout 5 s, OutTimeout 3 s, PressTurns 2) --
GATE_PASS_S = 0.3  # a bottle needs this long in the open gate; the gate TP is 0.5 s and the next bottle needs a
#                    0.5 s slot step to reach the gate, so one pulse admits exactly one bottle (as the filler door)
GATE_TRAVEL_S = 0.5  # gate -> pocket sensor (Bottle_Entering); ClampTimeout 2 s
BELL_MOVE_S = 0.5  # centering bell up <-> down; ClampTimeout 2 s
SPIN_TURN_S = 1.0  # one turn of the turntable: a 65 mm bottle (204 mm round) at about the web speed (200 mm/s);
#                    PressTurns 2 take at most 3 s with the phase (LabelTimeout 5 s)
SPIN_PULSE_S = 0.3  # Spin_Pulse per turn: at least one tick, and a 0.7 s gap before the next one
DISCHARGE_S = 0.5  # pusher: pocket -> C3 entry; OutTimeout 3 s
BROKEN_LABEL_SHARE = 0.9  # audit: a bottle leaving with less than 90 % of a label has a broken label

# --- Conveyor C3 and the packer stand-in -----------------------------------------------------------------
C3_FULL_SLOT = 1  # C3 slot watched by C3_Full, same reason as S7_SLOT (2 s Outfeed_Full filter)
PACKER_TAKE_S = 2.0  # the packer stand-in takes one bottle from the end of C3 every 2 s (the labeler needs ~6 s)

# --- Packer: collation (PLC: PackSize 6, PushTimeout 3 s; the gate is HELD open, not a pulse) -----------
PACKER_GATE_S = 0.4  # a bottle from C3's exit through the gate onto the collation table; the next bottle of a
#                      queue needs a full slot step (0.5 s) to reach the exit, so one bottle is in the gate at a time
BOTTLE_COUNTED_S = 0.2  # Bottle_Counted ON while the bottle passes the count sensor: 2 ticks ON, >= 3 ticks OFF
#                         before the next bottle (the CTU needs both edges); the PLC shuts the gate 0.1-0.2 s after
#                         the 6th edge, before the 7th bottle reaches the exit (0.5 s)
PUSH_OUT_S = 1.0  # group pusher home -> out (valve ON); PushTimeout 3 s
PUSH_RETURN_S = 0.8  # out -> home on the spring (valve OFF); PushTimeout 3 s
PACK_BOTTLES = 6  # places on the collation table (2 x 3 lane dividers): physical, the PLC's PackSize must match

# --- Packer: wrap (docs/packer/state-diagram.md rules 2, 4, 5; PLC: FilmPulseMM 50, FilmLength 850 mm in
#     600-1200, ClearTime 3 s, PushTimeout 3 s, FeedTimeout 10 s, CutTime 0.3 s, WrapTimeout 5 s) ------------
# Positions on the wrap conveyor are those of a pack's FRONT, in mm; the pusher sets a pack down at 0.
WRAP_SPEED_MM_S = 200.0  # push line -> film stop 2.0 s (ClearTime 3 s); the PLC's 1-2 tick stop = 20-40 mm
FILM_STOP_MM = 400.0  # Pack_At_Film eye: ON while a pack covers it (front 400-600)
FILM_SLOT_MM = 450.0  # the film slot, 50 mm ahead of the stop: the stop overshoot never reaches it
WRAP_REACH_MM = 400.0  # the bar wraps a pack whose tail is past the slot by at most this (front 650-1050)
OVEN_ENTRY_MM = 1100.0  # the pack's front reaches the oven's entry eye: handed to Oven.put_at_entry
FILM_FEED_MM_S = 150.0  # feed rollers; a pulse boundary every 3.33 ticks (>= 3: every pulse level is seen),
#                         a late 0.3 s tick moves 45 mm < one pulse; 1200 mm in 8.0 s (FeedTimeout 10 s)
FILM_PULSE_MM = 50.0  # one Film_Pulse per 50 mm of film (encoder on the feed roller; PLC FilmPulseMM)
FILM_PULSE_S = 0.15  # Film_Pulse ON time: 2 tick ends ON, at least 1 OFF before the next pulse
FILM_SENSOR_MM = 80.0  # Film_In_Slot sees film this far out of the slot: more than one pulse period, so a
#                        sheet the PLC sees always has FeedCount >= 1 unless it was cut (Sheet_Waiting)
KNIFE_CUT_S = 0.2  # the knife cuts after this long ON (CUT step: CutTime 0.3 s)
SHEET_NEEDED_MM = 800.0  # film needed to wrap a pack: the default 850 mm wraps, 600 mm is a short sheet
WRAP_TURN_S = 1.5  # one turn of the wrap bar (WrapTimeout 5 s)
WRAP_HOME_TURN = 0.25  # Wrap_Home window: a quarter of a turn around home (0.375 s); the PLC drops WrapBar
#                        0.1-0.2 s after the rising edge, the bar stops inside the window
FILM_ROLL_MM = 300_000.0  # film on a new roll (about 350 sheets)
FILM_LOW_MM = 10_000.0  # Film_Low at or below this
FILM_TAIL_MM = 1250.0  # Film_Empty at or below this: the largest sheet (1200 mm) plus its run-on still fits

# --- Packer: oven + outfeed (docs/packer/state-diagram.md rules 6-8; PLC: OvenSetpoint 190.0 degC, OvenHyst
#     5.0, OvenBand 15.0 (Oven_Ready 175-205 degC), OvenMax 230.0 degC, HeatTimeout 120 s, AirflowTimeout 3 s,
#     OvenJamTime 20 s) -----------------------------------------------------------------------------------------
OVEN_HEATER_C = 300.0  # the heater pulls the air toward this (only while air flows) ...
OVEN_HEAT_TAU_S = 60.0  # ... with this time constant; with the room loss the balance is 253.3 degC:
#                         20 -> 175 degC in 54.6 s (HeatTimeout 120 s); a heater left on reaches OvenMax
#                         (230 degC) 49.9 s after 190 degC, so the over-temperature scenario is reachable
OVEN_ROOM_TAU_S = 300.0  # heat loss to the room: -0.06 degC per tick at 190 degC, the 5 degC band cycles ~13 s
BLOWER_SPINUP_S = 1.0  # OvenBlower ON -> air flows (Airflow_OK); AirflowTimeout 3 s. OFF -> no air at once
OVEN_LENGTH_MM = 1000.0  # Oven_Entry eye at 0 mm, Oven_Exit eye at the end
OVEN_SPEED_MM_S = 100.0  # entry -> exit edge 10 s, about half of OvenJamTime 20 s
PACK_LENGTH_MM = 200.0  # 3 bottles x 65 mm: each eye is ON about 2 s per pack
OVEN_GAP_MM = 50.0  # a new pack only when the last one is this far past the entry eye: the eye is OFF >= 0.5 s
#                     between two packs, so the CTUD sees two rising edges
SHRINK_C = 170.0  # the film shrinks above this air temperature (Oven_Ready starts at 175 degC) ...
SHRINK_S = 6.0  # ... after this many seconds; a pack is in the oven 12 s
TABLE_PACKS = 6  # places on the outfeed table
TABLE_FULL_PACKS = 3  # Table_Full from this many packs: 3 places stay behind the sensor for every pack that is
#                       already past READY (1 on the wrap conveyor + up to 2 in the oven, Q3)
PALLETIZER_TAKE_S = 10.0  # the palletizer stand-in takes one pack every 10 s (a wrap cycle takes ~13 s)

# --- Blow moulder: oven station (docs/blowmoulder/state-diagram.md rules 1, 5; PLC: MoveTimeout 3 s,
#     OvenSetpoint 105.0 degC, OvenBand 5.0 (Oven_Ready 100-110 degC), SoakTime 1 s, HeatTimeout 15 s,
#     OvenMax 130.0 degC, Kp 10 %/degC, Bias 0 %) ----------------------------------------------------------
HOPPER_PREFORMS = 1000  # preforms in a full hopper (about 90 min at ~11 bottles/min)
PREFORM_LOW = 50  # Preform_Low at or below this many preforms in the hopper (warning in FUXA); Hopper_Empty at 0
CHUTE_S = 0.6  # HopperFeed ON -> the next preform reaches the loader. The PLC feeds only in FEED (after the
#                hand-over), so it is part of the oven's cycle: chute 0.6 + LOAD 0.4 + heat ~2.8 + soak 1.0
#                ~ 4.8 s, inside the ~5.1 s the mould needs between two hand-overs
LOADER_OUT_S = 0.4  # loader home -> full stroke (Loader ON); MoveTimeout 3 s
LOADER_RETURN_S = 0.3  # full stroke -> home on the spring (Loader OFF)
LAMP_GAIN_C_PER_PCT = 2.83  # balance temperature above the room per % of LampPower: 100 % -> 303 degC,
#                             30 % -> 105 degC (so P controller Kp 10 / Bias 0 settles ~3 degC low, rule 5)
PREFORM_TAU_S = 8.0  # preform in the pocket: 20 -> 100 degC at 100 % in 2.8 s, + SoakTime 1 s (HeatTimeout 15 s);
#                      100 % held reaches OvenMax 130 degC in 3.9 s
POCKET_WALL_TAU_S = 30.0  # empty pocket: the pyrometer sees the wall, which cools slowly to the room
