"""Generate the FUXA Modbus devices and tags of the PLCs (filler: address_map.py, syrup room:
address_map_syrup.py, labeler: address_map_labeler.py, packer: address_map_packer.py) + the views (fuxa_views.py).

FUXA needs one tag per PLC address it shows or writes. Typing ~40 tags in the
editor would copy the I/O list a third time (io-list.md, address_map.py, FUXA),
and a typo would show a wrong value on a screen. So the tags are generated from
address_map.py, the same numbers the simulator uses, and checked by
tests/test_hmi_tags.py.

    uv run python tests/fuxa_tags.py          # updates docs/hmi/fuxa-project.json
    # then import it into FUXA (replaces the whole project):
    curl -X POST -H 'Content-Type: application/json' \
         --data @../docs/hmi/fuxa-project.json http://localhost:1881/api/project

What it does:
- starts from FUXA's current project (GET /api/project) when FUXA answers, so
  views built in the editor are kept; otherwise from the export file;
- replaces only the devices of MACHINES ("d_filler", "d_syrup", "d_labeler", "d_packer"), their connection-status tags and the views,
  alarms and charts built by fuxa_views.py; other ones made in the editor stay;
- turns FUXA's DAQ (history logging) on for each machine's trend_tags only (Trends views, stages 6,
  S6, L6 and P6: TREND_TAGS of the filler, SYRUP_TREND_TAGS of the syrup room, LABELER_TREND_TAGS of the
  labeler, PACKER_TREND_TAGS of the packer);
- writes the export with sorted keys (same format as the stage 0 export).

FUXA facts used here (FUXA 1.3.5, runtime/devices/modbus/index.js):
- the tag address is the Modbus number + 1 ("settings address from 1 to 65536
  but communication start from 0"), e.g. coil 8192 (S2) -> FUXA address 8193;
- memaddress selects the area: "000000" coils, "400000" holding registers.
Tag ids are fixed ("t_filler_<name>"): the screens bind shapes by tag id, so a
second run must give the same ids or every screen loses its bindings.
"""

import json
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from simulator import address_map as am
from simulator import address_map_labeler as lm
from simulator import address_map_packer as pm
from simulator import address_map_syrup as sm

FUXA_URL = "http://localhost:1881"
EXPORT = Path(__file__).resolve().parents[2] / "docs" / "hmi" / "fuxa-project.json"

POLLING_MS = 200  # editor choices 50/100/200/350/500/...; slots move every 500 ms
TIMESTAMP = 1793750400000  # fixed (2026-11-03) so a second run gives the same file

COIL, HOLDING = "000000", "400000"

# (name = IEC variable name, area, Modbus number, FUXA type, description)
TAGS: list[tuple[str, str, int, str, str]] = [
    # status, written by the PLC
    ("State", HOLDING, am.STATE, "Int16", "status: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault"),
    ("Step", HOLDING, am.STEP, "Int16", "status: 0 IDLE, 10 WORK, 15 WAIT_OUTFEED, 20 INDEX, 900 FAULT"),
    ("GoodCount", HOLDING, am.GOOD_COUNT, "Int16", "status: bottles discharged (S6)"),
    # alarms, written by the PLC
    ("Alm_IndexJam", COIL, am.ALM_INDEX_JAM, "Bool", "alarm: no S8 within 5 s in INDEX (FAULT)"),
    ("Alm_FillTimeout", COIL, am.ALM_FILL_TIMEOUT, "Bool", "alarm: setpoint not reached in 10 s (FAULT)"),
    ("Alm_OutfeedBlocked", COIL, am.ALM_OUTFEED_BLOCKED, "Bool", "alarm: Blocked longer than 2 min (warning)"),
    ("Alm_CommLost", COIL, am.ALM_COMM_LOST, "Bool", "alarm: heartbeat not toggled for 3 s"),
    # outputs, written by the PLC
    ("ConveyerMotor1", COIL, am.CONVEYOR1, "Bool", "output: C1 infeed conveyor"),
    ("DoorMotor", COIL, am.DOOR_MOTOR, "Bool", "output: entry door pulse"),
    ("RinseValve", COIL, am.RINSE_VALVE, "Bool", "output: rinse valve at S3"),
    ("FillValve", COIL, am.FILL_VALVE, "Bool", "output: fill valve at S4"),
    ("CapMotor", COIL, am.CAP_MOTOR, "Bool", "output: capping head at S5"),
    ("IndexMotor", COIL, am.INDEX_MOTOR, "Bool", "output: internal index belt"),
    # conditioned signals, written by the PLC
    ("Outfeed_Full", COIL, am.OUTFEED_FULL, "Bool", "conditioned: C2 really full (S7 filtered)"),
    ("Entry_Load", COIL, am.ENTRY_LOAD, "Bool", "conditioned: entry pocket loaded, belt not moved yet"),
    ("Jog_Block", COIL, am.JOG_BLOCK, "Bool", "conditioned: manual button held > 10 s, manual outputs ignored until all released"),
    # sensors and wiring, written by the simulator (read only for the HMI)
    ("S2", COIL, am.S2, "Bool", "sensor: bottle waiting at the entry door"),
    ("S3", COIL, am.S3, "Bool", "sensor: bottle at the rinse station"),
    ("S4", COIL, am.S4, "Bool", "sensor: bottle at the fill station"),
    ("S5", COIL, am.S5, "Bool", "sensor: bottle at the cap station"),
    ("S6", COIL, am.S6, "Bool", "sensor: bottle discharged (pulse)"),
    ("S7", COIL, am.S7, "Bool", "sensor: C2 full"),
    ("S8", COIL, am.S8, "Bool", "sensor: index cam, once per station pitch"),
    ("FillLevel", HOLDING, am.FILL_LEVEL, "Int16", "sensor: ml in the bottle at the fill station"),
    ("Product_Available", COIL, am.PRODUCT_AVAILABLE, "Bool", "wiring: syrup room has product ready"),
    ("E_Stop_OK", COIL, am.E_STOP_OK, "Bool", "wiring: E-stop chain healthy (FALSE = stop)"),
    ("Heartbeat", COIL, am.HEARTBEAT, "Bool", "wiring: toggled every 1 s by the simulator"),
    # commands and setpoints, written by the HMI (FUXA is the only writer)
    ("Cmd", HOLDING, am.CMD, "Int16", "command: 1 Start, 2 Stop, 3 Reset (the PLC sets it back to 0)"),
    ("ModeReq", HOLDING, am.MODE_REQ, "Int16", "command: 0 Stop, 1 Auto, 2 Manual"),
    ("FillSetpoint", HOLDING, am.FILL_SETPOINT, "Int16", "setpoint: target fill level, ml"),
    ("RinseTime", HOLDING, am.RINSE_TIME, "Int16", "setpoint: rinse duration, ms"),
    ("CapTime", HOLDING, am.CAP_TIME, "Int16", "setpoint: capping duration, ms"),
    # manual buttons, written by the HMI (hold-to-run, Manual mode only)
    ("Man_C1", COIL, am.MAN_C1, "Bool", "manual: jog C1"),
    ("Man_Door", COIL, am.MAN_DOOR, "Bool", "manual: open the entry door"),
    ("Man_Rinse", COIL, am.MAN_RINSE, "Bool", "manual: rinse valve"),
    ("Man_Fill", COIL, am.MAN_FILL, "Bool", "manual: fill valve (only with a bottle at S4)"),
    ("Man_Cap", COIL, am.MAN_CAP, "Bool", "manual: capping head"),
    ("Man_Index", COIL, am.MAN_INDEX, "Bool", "manual: index belt (only with valves closed, cap stopped)"),
    # animation, written by the simulator, ignored by the PLC (bits picked on the shape)
    ("Anim_C1", HOLDING, am.ANIM_C1, "Int16", "animation: C1 slots, bit 0 entry .. bit 7 at the door"),
    ("Anim_C2", HOLDING, am.ANIM_C2, "Int16", "animation: C2 slots, bit 0 entry .. bit 7 exit"),
    ("Anim_Entry", HOLDING, am.ANIM_POCKETS, "Int16", "animation: entry pocket 0 empty 1 bottle 2 rinsed 3 filled 4 capped"),
    ("Anim_Rinse", HOLDING, am.ANIM_POCKETS + 1, "Int16", "animation: rinse pocket state code"),
    ("Anim_Fill", HOLDING, am.ANIM_POCKETS + 2, "Int16", "animation: fill pocket state code"),
    ("Anim_Cap", HOLDING, am.ANIM_POCKETS + 3, "Int16", "animation: cap pocket state code"),
]

# Syrup room PLC (docs/syrup/io-list.md). Same description prefixes as above (the Tags page groups by
# them). Numbers are fixed-point INT in the PLC (L, 0.1 degC, 0.01 bar); FUXA scales the degC / bar
# tags itself (SYRUP_SCALES, scale()), so screens, inputs and trends work in degC and bar.
SYRUP_TAGS: list[tuple[str, str, int, str, str]] = [
    # status, written by the PLC
    ("State", HOLDING, sm.STATE, "Int16", "status: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault"),
    ("Step", HOLDING, sm.STEP, "Int16", "status: 0 IDLE, 5 WAIT_DEMAND, 10 DOSE_WATER, 20 DOSE_SYRUP, 30 MIX, "
                                        "40 COOL, 50 CARBONATE, 55 WAIT_BUFFER, 60 TRANSFER, 900 FAULT"),
    ("BatchCount", HOLDING, sm.BATCH_COUNT, "Int16", "status: batches transferred to the buffer"),
    # alarms, written by the PLC
    ("Alm_Overflow", COIL, sm.ALM_OVERFLOW, "Bool", "alarm: mixing tank or buffer above max (FAULT)"),
    ("Alm_OverPressure", COIL, sm.ALM_OVER_PRESSURE, "Bool", "alarm: CO2 pressure above PressMax (FAULT)"),
    ("Alm_CoolTimeout", COIL, sm.ALM_COOL_TIMEOUT, "Bool", "alarm: CoolSetpoint not reached in 60 s (FAULT)"),
    ("Alm_CommLost", COIL, sm.ALM_COMM_LOST, "Bool", "alarm: heartbeat not toggled for 3 s"),
    ("Alm_LowSyrup", COIL, sm.ALM_LOW_SYRUP, "Bool", "alarm: syrup too low for the next batch (warning, Starved)"),
    ("Alm_DoseTimeout", COIL, sm.ALM_DOSE_TIMEOUT, "Bool", "alarm: dosing level not reached in 60 s (FAULT)"),
    # outputs, written by the PLC
    ("WaterPump", COIL, sm.WATER_PUMP, "Bool", "output: mains water -> mixing tank"),
    ("SyrupPump", COIL, sm.SYRUP_PUMP, "Bool", "output: syrup tank -> mixing tank"),
    ("Agitator", COIL, sm.AGITATOR, "Bool", "output: mixer motor"),
    ("CoolingValve", COIL, sm.COOLING_VALVE, "Bool", "output: cooling jacket of the mixing tank"),
    ("CO2Valve", COIL, sm.CO2_VALVE, "Bool", "output: CO2 into the carbonator"),
    ("TransferPump", COIL, sm.TRANSFER_PUMP, "Bool", "output: mixing tank -> buffer tank"),
    ("Product_Available", COIL, sm.PRODUCT_AVAILABLE_OUT, "Bool", "output: buffer holds product (wired to the filler)"),
    # conditioned and computed values, written by the PLC
    ("Temp_OK", COIL, sm.TEMP_OK, "Bool", "conditioned: product cold enough (cooling hysteresis)"),
    ("CO2_Demand", COIL, sm.CO2_DEMAND, "Bool", "conditioned: pressure below the band, CO2 valve wanted"),
    ("Jog_Block", COIL, sm.JOG_BLOCK, "Bool", "conditioned: manual button held > 10 s, manual outputs ignored until all released"),
    ("Batch_Dosed", COIL, sm.BATCH_DOSED, "Bool", "conditioned: water and syrup of this batch are in the tank"),
    ("SyrupTarget", HOLDING, sm.SYRUP_TARGET, "Int16", "conditioned: syrup per batch, L (WaterBatch x 10 / SyrupRatio)"),
    ("DoseTarget", HOLDING, sm.DOSE_TARGET, "Int16", "conditioned: level at the end of DOSE_SYRUP, L"),
    # sensors and wiring, written by the simulator (read only for the HMI)
    ("SyrupLevel", HOLDING, sm.SYRUP_LEVEL, "Int16", "sensor: syrup tank level, L"),
    ("MixLevel", HOLDING, sm.MIX_LEVEL, "Int16", "sensor: mixing tank level, L"),
    ("ProductTemp", HOLDING, sm.PRODUCT_TEMP, "Int16", "sensor: product temperature, degC in FUXA (PLC 0.1 degC: 40 = 4.0)"),
    ("CO2Pressure", HOLDING, sm.CO2_PRESSURE, "Int16", "sensor: carbonator pressure, bar in FUXA (PLC 0.01 bar: 350 = 3.50)"),
    ("BufferLevel", HOLDING, sm.BUFFER_LEVEL, "Int16", "sensor: buffer tank level, L"),
    ("MixHighHigh", COIL, sm.MIX_HIGH_HIGH, "Bool", "sensor: high-high level switch on the mixing tank"),
    ("E_Stop_OK", COIL, sm.E_STOP_OK, "Bool", "wiring: E-stop chain healthy (FALSE = stop)"),
    ("Heartbeat", COIL, sm.HEARTBEAT, "Bool", "wiring: toggled every 1 s by the simulator"),
    # commands and recipe setpoints, written by the HMI (FUXA is the only writer)
    ("Cmd", HOLDING, sm.CMD, "Int16", "command: 1 Start, 2 Stop, 3 Reset (the PLC sets it back to 0)"),
    ("ModeReq", HOLDING, sm.MODE_REQ, "Int16", "command: 0 Stop, 1 Auto, 2 Manual"),
    ("WaterBatch", HOLDING, sm.WATER_BATCH, "Int16", "setpoint: water per batch, L"),
    ("SyrupRatio", HOLDING, sm.SYRUP_RATIO, "Int16", "setpoint: water : syrup x10 (55 = 5.5:1)"),
    ("MixTime", HOLDING, sm.MIX_TIME, "Int16", "setpoint: agitator time in MIX, s"),
    ("CoolSetpoint", HOLDING, sm.COOL_SETPOINT, "Int16", "setpoint: target product temperature, degC in FUXA (PLC 0.1 degC)"),
    ("TempHyst", HOLDING, sm.TEMP_HYST, "Int16", "setpoint: cooling band, degC in FUXA (PLC 0.1 degC)"),
    ("PressSetpoint", HOLDING, sm.PRESS_SETPOINT, "Int16", "setpoint: target CO2 pressure, bar in FUXA (PLC 0.01 bar)"),
    ("PressHyst", HOLDING, sm.PRESS_HYST, "Int16", "setpoint: CO2 valve band, bar in FUXA (PLC 0.01 bar)"),
    ("BufferLow", HOLDING, sm.BUFFER_LOW, "Int16", "setpoint: new batch when the buffer is below, L"),
    ("BufferMin", HOLDING, sm.BUFFER_MIN, "Int16", "setpoint: Product_Available off below, L"),
    ("CarbTime", HOLDING, sm.CARB_TIME, "Int16", "setpoint: pressure held without CO2 = saturated, s"),
    # manual buttons, written by the HMI (hold-to-run, Manual mode only)
    ("Man_WaterPump", COIL, sm.MAN_WATER_PUMP, "Bool", "manual: water pump"),
    ("Man_SyrupPump", COIL, sm.MAN_SYRUP_PUMP, "Bool", "manual: syrup pump"),
    ("Man_Agitator", COIL, sm.MAN_AGITATOR, "Bool", "manual: agitator (only above the minimum level)"),
    ("Man_Cooling", COIL, sm.MAN_COOLING, "Bool", "manual: cooling valve"),
    ("Man_CO2", COIL, sm.MAN_CO2, "Bool", "manual: CO2 valve (only when the product is cold)"),
    ("Man_Transfer", COIL, sm.MAN_TRANSFER, "Bool", "manual: transfer pump"),
]

# Labeler PLC (docs/labeler/io-list.md, HMI stage L1). Same description prefixes (the Tags page groups by
# them). The glue temperatures are INT in 0.1 degC in the PLC; FUXA scales them (LABELER_SCALES).
LABELER_TAGS: list[tuple[str, str, int, str, str]] = [
    # status, written by the PLC
    ("State", HOLDING, lm.STATE, "Int16", "status: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault"),
    ("Step", HOLDING, lm.STEP, "Int16", "status: 0 IDLE, 5 WARM_UP, 10 WAIT_BOTTLE, 20 CLAMP, 22 WRAP, 24 PRESS, "
                                        "26 UNCLAMP, 35 WAIT_OUTFEED, 40 DISCHARGE, 900 FAULT"),
    ("GoodCount", HOLDING, lm.GOOD_COUNT, "Int16", "status: labelled bottles discharged onto C3 (Cmd 4 zeroes it)"),
    # alarms, written by the PLC
    ("Alm_LabelTimeout", COIL, lm.ALM_LABEL_TIMEOUT, "Bool", "alarm: no label mark / press turns within 5 s (FAULT)"),
    ("Alm_PocketJam", COIL, lm.ALM_POCKET_JAM, "Bool", "alarm: gate, bell or discharge movement not finished (FAULT)"),
    ("Alm_OutfeedBlocked", COIL, lm.ALM_OUTFEED_BLOCKED, "Bool", "alarm: Blocked longer than 2 min (warning)"),
    ("Alm_CommLost", COIL, lm.ALM_COMM_LOST, "Bool", "alarm: heartbeat not toggled for 3 s"),
    ("Alm_LabelEmpty", COIL, lm.ALM_LABEL_EMPTY, "Bool", "alarm: label roll empty (warning, Starved)"),
    ("Alm_GlueOverTemp", COIL, lm.ALM_GLUE_OVER_TEMP, "Bool", "alarm: glue at or above GlueMax 200 degC (FAULT)"),
    ("Alm_GlueHeatTimeout", COIL, lm.ALM_GLUE_HEAT_TIMEOUT, "Bool", "alarm: glue not ready within 120 s of WARM_UP (FAULT)"),
    # outputs, written by the PLC
    ("ConveyorMotor2", COIL, lm.CONVEYOR_MOTOR2, "Bool", "output: C2 infeed conveyor"),
    ("GateMotor", COIL, lm.GATE_MOTOR, "Bool", "output: infeed gate pulse (one bottle)"),
    ("ClampValve", COIL, lm.CLAMP_VALVE, "Bool", "output: centering bell down"),
    ("SpinMotor", COIL, lm.SPIN_MOTOR, "Bool", "output: turntable"),
    ("LabelFeed", COIL, lm.LABEL_FEED, "Bool", "output: label web feed rollers"),
    ("Cutter", COIL, lm.CUTTER, "Bool", "output: knife pulse"),
    ("GlueRoller", COIL, lm.GLUE_ROLLER, "Bool", "output: glue roller"),
    ("GlueHeater", COIL, lm.GLUE_HEATER, "Bool", "output: melt tank heater"),
    ("DischargeMotor", COIL, lm.DISCHARGE_MOTOR, "Bool", "output: pusher, pocket -> C3"),
    # conditioned values, written by the PLC
    ("Outfeed_Full", COIL, lm.OUTFEED_FULL, "Bool", "conditioned: C3 really full (C3_Full filtered)"),
    ("Bottle_Entering", COIL, lm.BOTTLE_ENTERING, "Bool", "conditioned: bottle between gate and pocket"),
    ("Jog_Block", COIL, lm.JOG_BLOCK, "Bool", "conditioned: manual button held > 10 s, manual outputs ignored until all released"),
    ("Heat_Demand", COIL, lm.HEAT_DEMAND, "Bool", "conditioned: heater hysteresis wants heat"),
    ("Glue_Ready", COIL, lm.GLUE_READY, "Bool", "conditioned: glue inside GlueSetpoint +- GlueBand"),
    ("Starved", COIL, lm.STARVED, "Bool", "conditioned: no bottle at the gate for 2 s, or roll empty"),
    # sensors and wiring, written by the simulator (read only for the HMI)
    ("Bottle_At_Gate", COIL, lm.BOTTLE_AT_GATE, "Bool", "sensor: bottle waiting at the gate (end of C2)"),
    ("Bottle_In_Pocket", COIL, lm.BOTTLE_IN_POCKET, "Bool", "sensor: bottle in the pocket on the turntable"),
    ("Bell_Down", COIL, lm.BELL_DOWN, "Bool", "sensor: centering bell down (bottle clamped)"),
    ("Label_Present", COIL, lm.LABEL_PRESENT, "Bool", "sensor: label on the bottle in the pocket"),
    ("C3_Full", COIL, lm.C3_FULL, "Bool", "sensor: C3 outfeed conveyor full"),
    ("Roll_Empty", COIL, lm.ROLL_EMPTY, "Bool", "sensor: label roll empty"),
    ("Label_Mark", COIL, lm.LABEL_MARK, "Bool", "sensor: registration mark on the label web (pulse)"),
    ("Spin_Pulse", COIL, lm.SPIN_PULSE, "Bool", "sensor: one pulse per turn of the turntable"),
    ("Roll_Low", COIL, lm.ROLL_LOW, "Bool", "sensor: label roll low (FUXA warning)"),
    ("Glue_Low", COIL, lm.GLUE_LOW, "Bool", "sensor: glue low in the melt tank (FUXA warning)"),
    ("GlueTemp", HOLDING, lm.GLUE_TEMP, "Int16", "sensor: melt temperature, degC in FUXA (PLC 0.1 degC: 1700 = 170.0)"),
    ("E_Stop_OK", COIL, lm.E_STOP_OK, "Bool", "wiring: E-stop chain healthy (FALSE = stop)"),
    ("Heartbeat", COIL, lm.HEARTBEAT, "Bool", "wiring: toggled every 1 s by the simulator"),
    # commands and setpoints, written by the HMI (FUXA is the only writer)
    ("Cmd", HOLDING, lm.CMD, "Int16", "command: 1 Start, 2 Stop, 3 Reset, 4 Reset counter (the PLC sets it back to 0)"),
    ("ModeReq", HOLDING, lm.MODE_REQ, "Int16", "command: 0 Stop, 1 Auto, 2 Manual"),
    ("GlueSetpoint", HOLDING, lm.GLUE_SETPOINT, "Int16", "setpoint: melt temperature, degC in FUXA (PLC 0.1 degC)"),
    ("GlueHyst", HOLDING, lm.GLUE_HYST, "Int16", "setpoint: heater band, degC in FUXA (PLC 0.1 degC)"),
    ("GlueBand", HOLDING, lm.GLUE_BAND, "Int16", "setpoint: Glue_Ready window +-, degC in FUXA (PLC 0.1 degC)"),
    ("PressTurns", HOLDING, lm.PRESS_TURNS, "Int16", "setpoint: bottle turns after the cut"),
    # manual buttons, written by the HMI (hold-to-run, Manual mode only; the PLC turns Man_Clamp clicks into a toggle)
    ("Man_C2", COIL, lm.MAN_C2, "Bool", "manual: C2 conveyor"),
    ("Man_Gate", COIL, lm.MAN_GATE, "Bool", "manual: open the gate"),
    ("Man_Clamp", COIL, lm.MAN_CLAMP, "Bool", "manual: bell toggle (click = down / up, only with a bottle)"),
    ("Man_Spin", COIL, lm.MAN_SPIN, "Bool", "manual: turntable (only with the bell down)"),
    ("Man_Feed", COIL, lm.MAN_FEED, "Bool", "manual: label feed + glue roller + turntable (only with the bell down, glue ready)"),
    ("Man_Cut", COIL, lm.MAN_CUT, "Bool", "manual: knife"),
    ("Man_Discharge", COIL, lm.MAN_DISCHARGE, "Bool", "manual: pusher (only with the bell up and C3 free)"),
    # animation, written by the simulator, ignored by the PLC (HMI L0)
    ("Anim_C3", HOLDING, lm.ANIM_C3, "Int16", "animation: C3 slots, bit 0 entry .. bit 7 packer end"),
    ("Anim_Station", HOLDING, lm.ANIM_STATION, "Int16", "animation: pocket 0 empty 1 bottle 2 labelling 3 labelled"),
]

# Packer PLC (docs/packer/io-list.md, HMI stage P1). Same description prefixes; the computed words
# (%MW40-43) are listed as "status" so the Tags page keeps every column within 20 rows. The oven
# temperatures are INT in 0.1 degC in the PLC; FUXA scales them (PACKER_SCALES).
PACKER_TAGS: list[tuple[str, str, int, str, str]] = [
    # status, written by the PLC
    ("State", HOLDING, pm.STATE, "Int16", "status: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault"),
    ("Step", HOLDING, pm.STEP, "Int16", "status: collation 0 IDLE, 5 WARM_UP, 10 COUNT, 15 WAIT_WRAP, 20 PUSH, "
                                        "25 RETRACT, 900 FAULT"),
    ("WrapStep", HOLDING, pm.WRAP_STEP, "Int16", "status: wrap 0 IDLE, 10 WAIT_PACK, 20 FEED, 22 CUT, 24 READY, "
                                                 "30 RUN_ON, 32 WRAP, 34 RUN_OUT, 900 FAULT"),
    ("PackCount", HOLDING, pm.PACK_COUNT, "Int16", "status: finished packs out of the oven (Cmd 4 zeroes it)"),
    ("GroupCount", HOLDING, pm.GROUP_COUNT, "Int16", "status: bottles in the current group, 0-6"),
    ("PacksInOven", HOLDING, pm.PACKS_IN_OVEN, "Int16", "status: packs inside the oven (CTUD)"),
    ("FeedPulses", HOLDING, pm.FEED_PULSES, "Int16", "status: film pulses for one sheet (FilmLength / 50 mm)"),
    ("FeedCount", HOLDING, pm.FEED_COUNT, "Int16", "status: film pulses fed for the current sheet"),
    # alarms, written by the PLC
    ("Alm_FilmFault", COIL, pm.ALM_FILM_FAULT, "Bool", "alarm: sheet not fed in 10 s, or no sheet in READY for 1 s (FAULT)"),
    ("Alm_PackJam", COIL, pm.ALM_PACK_JAM, "Bool", "alarm: pusher / pack movement not finished in 3 s (FAULT)"),
    ("Alm_OutfeedBlocked", COIL, pm.ALM_OUTFEED_BLOCKED, "Bool", "alarm: Blocked longer than 2 min (warning)"),
    ("Alm_CommLost", COIL, pm.ALM_COMM_LOST, "Bool", "alarm: heartbeat not toggled for 3 s"),
    ("Alm_FilmEmpty", COIL, pm.ALM_FILM_EMPTY, "Bool", "alarm: film roll empty and a pack waits (warning, Starved)"),
    ("Alm_OvenOverTemp", COIL, pm.ALM_OVEN_OVER_TEMP, "Bool", "alarm: oven at or above OvenMax 230 degC (FAULT)"),
    ("Alm_OvenHeatTimeout", COIL, pm.ALM_OVEN_HEAT_TIMEOUT, "Bool", "alarm: oven not ready within 120 s of WARM_UP (FAULT)"),
    ("Alm_OvenJam", COIL, pm.ALM_OVEN_JAM, "Bool", "alarm: packs in the oven and none out for 20 s (FAULT)"),
    ("Alm_AirflowLost", COIL, pm.ALM_AIRFLOW_LOST, "Bool", "alarm: blower on and no air flow within 3 s (FAULT)"),
    ("Alm_WrapTimeout", COIL, pm.ALM_WRAP_TIMEOUT, "Bool", "alarm: wrap bar not back home within 5 s (FAULT)"),
    # outputs, written by the PLC
    ("ConveyorMotor3", COIL, pm.CONVEYOR_MOTOR3, "Bool", "output: C3 infeed conveyor"),
    ("GateMotor", COIL, pm.GATE_MOTOR, "Bool", "output: infeed gate held open while the group is not complete"),
    ("GroupPusher", COIL, pm.GROUP_PUSHER, "Bool", "output: group pusher, table -> wrap conveyor"),
    ("WrapConveyor", COIL, pm.WRAP_CONVEYOR, "Bool", "output: wrap conveyor, table -> film slot -> oven"),
    ("FilmFeed", COIL, pm.FILM_FEED, "Bool", "output: film feed rollers"),
    ("Knife", COIL, pm.KNIFE, "Bool", "output: knife (whole CUT step)"),
    ("WrapBar", COIL, pm.WRAP_BAR, "Bool", "output: wrap bar, one turn per pack"),
    ("OvenHeater", COIL, pm.OVEN_HEATER, "Bool", "output: oven heater (Auto only, with air flow)"),
    ("OvenBlower", COIL, pm.OVEN_BLOWER, "Bool", "output: oven blower (30 s run-on)"),
    ("OvenConveyor", COIL, pm.OVEN_CONVEYOR, "Bool", "output: conveyor through the oven"),
    # conditioned values, written by the PLC
    ("Outfeed_Full", COIL, pm.OUTFEED_FULL, "Bool", "conditioned: outfeed table really full (Table_Full filtered)"),
    ("Group_Complete", COIL, pm.GROUP_COMPLETE, "Bool", "conditioned: 6 bottles counted, gate closed"),
    ("Jog_Block", COIL, pm.JOG_BLOCK, "Bool", "conditioned: manual button held > 10 s, manual outputs ignored until all released"),
    ("Heat_Demand", COIL, pm.HEAT_DEMAND, "Bool", "conditioned: heater hysteresis wants heat"),
    ("Oven_Ready", COIL, pm.OVEN_READY, "Bool", "conditioned: oven inside OvenSetpoint +- OvenBand"),
    ("Starved", COIL, pm.STARVED, "Bool", "conditioned: wrap waits and (no bottle at the gate for 2 s, or film empty)"),
    ("Wrap_Clear", COIL, pm.WRAP_CLEAR, "Bool", "conditioned: wrap conveyor ran empty: the next group may be pushed"),
    ("Oven_On", COIL, pm.OVEN_ON, "Bool", "conditioned: oven switched on (Auto, no FAULT)"),
    # sensors and wiring, written by the simulator (read only for the HMI)
    ("Bottle_At_Gate", COIL, pm.BOTTLE_AT_GATE, "Bool", "sensor: bottle waiting at the gate (end of C3)"),
    ("Bottle_Counted", COIL, pm.BOTTLE_COUNTED, "Bool", "sensor: count sensor behind the gate (pulse per bottle)"),
    ("Pusher_Home", COIL, pm.PUSHER_HOME, "Bool", "sensor: group pusher home"),
    ("Pusher_Out", COIL, pm.PUSHER_OUT, "Bool", "sensor: group pusher fully out"),
    ("Pack_At_Film", COIL, pm.PACK_AT_FILM, "Bool", "sensor: pack at the stop before the film slot"),
    ("Film_In_Slot", COIL, pm.FILM_IN_SLOT, "Bool", "sensor: film sheet sticks out of the slot"),
    ("Film_Pulse", COIL, pm.FILM_PULSE, "Bool", "sensor: feed roller encoder, one pulse per 50 mm"),
    ("Wrap_Home", COIL, pm.WRAP_HOME, "Bool", "sensor: wrap bar at home"),
    ("Film_Empty", COIL, pm.FILM_EMPTY, "Bool", "sensor: film roll empty"),
    ("Film_Low", COIL, pm.FILM_LOW, "Bool", "sensor: film roll low (FUXA warning)"),
    ("Oven_Entry", COIL, pm.OVEN_ENTRY, "Bool", "sensor: pack at the oven entry (pulse per pack)"),
    ("Oven_Exit", COIL, pm.OVEN_EXIT, "Bool", "sensor: pack at the oven exit (pulse per pack)"),
    ("Table_Full", COIL, pm.TABLE_FULL, "Bool", "sensor: outfeed table full"),
    ("Airflow_OK", COIL, pm.AIRFLOW_OK, "Bool", "sensor: air flow switch in the oven duct"),
    ("OvenTemp", HOLDING, pm.OVEN_TEMP, "Int16", "sensor: oven air temperature, degC in FUXA (PLC 0.1 degC: 1900 = 190.0)"),
    ("E_Stop_OK", COIL, pm.E_STOP_OK, "Bool", "wiring: E-stop chain healthy (FALSE = stop)"),
    ("Heartbeat", COIL, pm.HEARTBEAT, "Bool", "wiring: toggled every 1 s by the simulator"),
    # commands and setpoints, written by the HMI (FUXA is the only writer)
    ("Cmd", HOLDING, pm.CMD, "Int16", "command: 1 Start, 2 Stop, 3 Reset, 4 Reset counter (the PLC sets it back to 0)"),
    ("ModeReq", HOLDING, pm.MODE_REQ, "Int16", "command: 0 Stop, 1 Auto, 2 Manual"),
    ("OvenSetpoint", HOLDING, pm.OVEN_SETPOINT, "Int16", "setpoint: oven air temperature, degC in FUXA (PLC 0.1 degC)"),
    ("OvenHyst", HOLDING, pm.OVEN_HYST, "Int16", "setpoint: heater band, degC in FUXA (PLC 0.1 degC)"),
    ("OvenBand", HOLDING, pm.OVEN_BAND, "Int16", "setpoint: Oven_Ready window +-, degC in FUXA (PLC 0.1 degC)"),
    ("FilmLength", HOLDING, pm.FILM_LENGTH, "Int16", "setpoint: film sheet length, mm (recipe value)"),
    # manual buttons, written by the HMI (hold-to-run, Manual mode only)
    ("Man_C3", COIL, pm.MAN_C3, "Bool", "manual: C3 conveyor"),
    ("Man_Gate", COIL, pm.MAN_GATE, "Bool", "manual: open the gate"),
    ("Man_GroupPush", COIL, pm.MAN_GROUP_PUSH, "Bool", "manual: group pusher"),
    ("Man_WrapConveyor", COIL, pm.MAN_WRAP_CONVEYOR, "Bool", "manual: wrap conveyor"),
    ("Man_FilmFeed", COIL, pm.MAN_FILM_FEED, "Bool", "manual: film feed rollers"),
    ("Man_Knife", COIL, pm.MAN_KNIFE, "Bool", "manual: knife"),
    ("Man_WrapBar", COIL, pm.MAN_WRAP_BAR, "Bool", "manual: wrap bar"),
    ("Man_OvenConveyor", COIL, pm.MAN_OVEN_CONVEYOR, "Bool", "manual: oven conveyor"),
    # animation, written by the simulator, ignored by the PLC (HMI P0)
    ("Anim_Wrap", HOLDING, pm.ANIM_WRAP, "Int16", "animation: wrap conveyor cells, bit 0 push line .. bit 10 oven entry"),
    ("Anim_Oven", HOLDING, pm.ANIM_OVEN, "Int16", "animation: oven cells, bit 0 entry .. bit 9, bit 10 over the exit"),
    ("Anim_Table", HOLDING, pm.ANIM_TABLE, "Int16", "animation: packs on the outfeed table, 0-6"),
    ("Anim_Film", HOLDING, pm.ANIM_FILM, "Int16", "animation: sheet 0 none 1 feeding 2 in slot 3 under a pack"),
]



@dataclass(frozen=True)
class Machine:
    """One PLC = one FUXA Modbus device (report 3: one OpenPLC runtime per machine).

    A later machine (e.g. the syrup room) is one more Machine with its own key, PLC address
    (host ports 502, 5022-5025, report 6.1) and tag table built from its address map; the
    views draw it with conveyor()/machine_block() in fuxa_views.py (roadmap issue I11).
    Ids are derived from the key: device "d_<key>", tags "t_<key>_<name>"."""

    key: str
    name: str
    address: str
    tags: tuple
    trend_tags: tuple = ()  # tags FUXA's DAQ logs for the Trends view; all others off
    scales: tuple = ()  # (tag name, factor): FUXA shows and writes PLC value / factor (see scale())

    def factor(self, name: str) -> int:
        """1 for a tag FUXA shows as the PLC stores it, 10 / 100 for a fixed-point one."""
        return dict(self.scales).get(name, 1)

    @property
    def device_id(self) -> str:
        return f"d_{self.key}"

    @property
    def status_tag_id(self) -> str:
        return f"t_{self.key}_connection_status"

    def tag_id(self, name: str) -> str:
        return f"t_{self.key}_{name}"


# Logged by FUXA's DAQ for the Trends view (stage 6). FillSetpoint draws the target line.
TREND_TAGS = ("FillLevel", "FillSetpoint", "GoodCount", "State")

# Address = Docker service name + the CONTAINER port (FUXA runs in the same Docker network, so it
# never uses the host ports 502 / 5022).
FILLER = Machine("filler", "Filler PLC", "openplc:502", tuple(TAGS), TREND_TAGS)
# Fixed-point tags of the syrup room (io-list: INT in 0.1 degC / 0.01 bar, "the HMI does the scaling")
SYRUP_SCALES = (("ProductTemp", 10), ("CoolSetpoint", 10), ("TempHyst", 10),
                ("CO2Pressure", 100), ("PressSetpoint", 100), ("PressHyst", 100))
# Logged for the syrup room's Trends view (S6). The setpoints draw the target lines. FUXA scales a
# value before it stores it (modbus/index.js: tagValueCompose, then addDaq): the history is in degC / bar.
SYRUP_TREND_TAGS = ("MixLevel", "BufferLevel", "ProductTemp", "CoolSetpoint", "CO2Pressure", "PressSetpoint",
                    "State")
SYRUP = Machine("syrup", "Syrup room PLC", "plc-syrup-carb:502", tuple(SYRUP_TAGS), SYRUP_TREND_TAGS,
                scales=SYRUP_SCALES)
# Fixed-point tags of the labeler (io-list: INT in 0.1 degC)
LABELER_SCALES = (("GlueTemp", 10), ("GlueSetpoint", 10), ("GlueHyst", 10), ("GlueBand", 10))
# Logged for the labeler's Trends view (HMI L6). GlueSetpoint draws the target line; scaled first,
# so the history is in degC, as for the syrup room.
LABELER_TREND_TAGS = ("GlueTemp", "GlueSetpoint", "GoodCount", "State")
LABELER = Machine("labeler", "Labeler PLC", "plc-labeler:502", tuple(LABELER_TAGS), LABELER_TREND_TAGS,
                  scales=LABELER_SCALES)
# Fixed-point tags of the packer (io-list: INT in 0.1 degC).
PACKER_SCALES = (("OvenTemp", 10), ("OvenSetpoint", 10), ("OvenHyst", 10), ("OvenBand", 10))
# Logged for the packer's Trends view (HMI P6). OvenSetpoint draws the target line; scaled first,
# so the history is in degC, as for the labeler.
PACKER_TREND_TAGS = ("OvenTemp", "OvenSetpoint", "PackCount", "State")
PACKER = Machine("packer", "Packer PLC", "plc-packer:502", tuple(PACKER_TAGS), PACKER_TREND_TAGS,
                 scales=PACKER_SCALES)
MACHINES = (FILLER, SYRUP, LABELER, PACKER)

# the filler's ids under their old names (views and checks use them)
DEVICE_ID, DEVICE_NAME, PLC_ADDRESS = FILLER.device_id, FILLER.name, FILLER.address
STATUS_TAG_ID = FILLER.status_tag_id


def tag_id(name: str) -> str:
    return FILLER.tag_id(name)


def fuxa_address(modbus_number: int) -> int:
    """FUXA counts from 1, the Modbus protocol from 0."""
    return modbus_number + 1


def daq_off() -> dict:
    return {"enabled": False, "interval": 60, "changed": False, "restored": False}


def scale(factor: int) -> dict:
    """FUXA tag scaling for a fixed-point INT: show PLC value / factor, write round(value x factor).

    Not FUXA's Modbus "divisor": on writing it passes value x factor to Node's writeInt16BE, which
    cuts the fraction off (0.29 bar x 100 = 28.999... -> 28). The expression scale (FUXA tag dialog
    -> Scale -> Expression; FUXA evaluates it with `this` = the value) rounds instead. `format` =
    decimals of the shown value."""
    return {"scale": {"mode": "expression", "readExpression": f"this / {factor}",
                      "writeExpression": f"Math.round(this * {factor})"},
            "format": len(str(factor)) - 1}


def daq_on() -> dict:
    """Store every change, and at least once a minute (so a flat stretch still has points)."""
    return {"enabled": True, "interval": 60, "changed": True, "restored": False}


def device(machine: Machine = FILLER) -> dict:
    tags = {
        machine.tag_id(name): {
            "id": machine.tag_id(name),
            "name": name,
            "type": kind,
            "memaddress": area,
            "address": fuxa_address(number),
            "divisor": None,
            "daq": daq_on() if name in machine.trend_tags else daq_off(),
            "description": description,
            "timestamp": TIMESTAMP,
            **(scale(machine.factor(name)) if machine.factor(name) != 1 else {}),
        }
        for name, area, number, kind, description in machine.tags
    }
    return {
        "id": machine.device_id,
        "name": machine.name,
        "type": "ModbusTCP",
        "enabled": True,
        "polling": POLLING_MS,
        "property": {
            "address": machine.address,
            "port": None, "slot": None, "rack": None,
            "slaveid": "1",
            "baudrate": 9600, "databits": 8, "stopbits": 1, "parity": "None",
            "connectionOption": "TcpPort",
            "delay": 10,
            "forceFC16": False,
            "timeout": 2000,
        },
        "tags": tags,
    }


def connection_status_tag(machine: Machine = FILLER) -> dict:
    """Tag of the FUXA server device that shows whether the machine's PLC is connected (header)."""
    return {
        "id": machine.status_tag_id,
        "name": f"{machine.name} Connection Status",
        "label": f"{machine.name} Connection Status",
        "type": "number",
        "memaddress": machine.device_id,
        "init": "",
        "sysType": 1,
        "daq": daq_off(),
        "timestamp": TIMESTAMP,
    }


def load_project() -> tuple[dict, str]:
    try:
        with urllib.request.urlopen(f"{FUXA_URL}/api/project", timeout=3) as response:
            return json.load(response), "FUXA (live project)"
    except OSError:
        return json.loads(EXPORT.read_text()), f"{EXPORT.name} (FUXA not reachable)"


def apply(project: dict) -> list[str]:
    """Put every machine's device and status tag into the project; return what was replaced."""
    replaced = []
    server_tags = project["devices"]["0"].setdefault("tags", {})
    for machine in MACHINES:
        if machine.device_id in project["devices"]:
            replaced.append(f"device {machine.device_id} ({len(project['devices'][machine.device_id]['tags'])} tags)")
        project["devices"][machine.device_id] = device(machine)
        if machine.status_tag_id in server_tags:
            replaced.append(f"{machine.key} connection status tag")
        server_tags[machine.status_tag_id] = connection_status_tag(machine)
    return replaced


def write_export(project: dict) -> None:
    EXPORT.write_text(json.dumps(project, indent=4, sort_keys=True) + "\n")


def main() -> None:
    project, source = load_project()
    import fuxa_views  # the views use this file's tag table; imported here to avoid a cycle

    replaced = (apply(project) + fuxa_views.apply_views(project) + fuxa_views.apply_alarms(project)
                + fuxa_views.apply_charts(project))
    write_export(project)
    names = ", ".join(v["name"] for v in project["hmi"]["views"])
    devices = ", ".join(f"'{m.name}' ({m.address}, {len(m.tags)} tags)" for m in MACHINES)
    print(f"from {source}: devices {devices}, polling {POLLING_MS} ms; views: {names}")
    print(f"replaced: {', '.join(replaced) or 'nothing (new device)'}")
    print(f"written {EXPORT}; import it with the curl in this file's docstring")


if __name__ == "__main__":
    sys.exit(main())
