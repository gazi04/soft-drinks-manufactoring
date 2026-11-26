"""Write plcs/blowmoulder/pous/programs/main.ld for ladder stage 0.

Variables from docs/blowmoulder/io-list.md (located) + constants from state-diagram.md,
and one test rung cloned from the packer's "Start 1/3" rung (contact -> MOVE const -> INT):
E_Stop_OK -> MOVE 42 -> LampPower (%QW0).
"""
import json
import random
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tools/ladder/stages/x.py -> repository root
PACKER = ROOT / "plcs/packer/pous/programs/main.ld"
OUT = ROOT / "plcs/blowmoulder/pous/programs/main.ld"

# name, type, location, initial, doc
LOCATED = [
    ("Preform_At_Loader", "BOOL", "%MX0.0", "FALSE", "Sensor: a preform waits at the loader (end of the hopper chute)"),
    ("Preform_In_Oven", "BOOL", "%MX0.1", "FALSE", "Sensor: a preform sits in the oven pocket (ends LOAD, pocket empty before the next load)"),
    ("Arm_At_Oven", "BOOL", "%MX0.2", "FALSE", "Sensor: transfer arm home above the oven pocket, out of the mould (the mould only closes with it)"),
    ("Arm_At_Mould", "BOOL", "%MX0.3", "FALSE", "Sensor: transfer arm inside the open mould, the preform can be released"),
    ("Mould_Closed", "BOOL", "%MX0.4", "FALSE", "Sensor: mould halves closed and locked (blow only with it)"),
    ("Mould_Open", "BOOL", "%MX0.5", "FALSE", "Sensor: mould halves fully open (arm and ejector only with it)"),
    ("Rod_Up", "BOOL", "%MX0.6", "FALSE", "Sensor: stretch rod up, out of the bottle (the mould opens only with it)"),
    ("Rod_Down", "BOOL", "%MX0.7", "FALSE", "Sensor: stretch rod at the bottom of the mould, stretch finished"),
    ("Bottle_In_Mould", "BOOL", "%MX4.0", "FALSE", "Sensor: photo eye across the open mould, a preform or bottle is in it"),
    ("Bottle_Ejected", "BOOL", "%MX4.1", "FALSE", "Sensor: eye at the entry of C1, one pulse per bottle (BottleCount counts the rising edge)"),
    ("Hopper_Empty", "BOOL", "%MX4.2", "FALSE", "Sensor: no preforms left in the hopper"),
    ("Preform_Low", "BOOL", "%MX4.3", "FALSE", "Sensor: hopper low, refill soon (warning in FUXA only)"),
    ("C1_Full", "BOOL", "%MX4.4", "FALSE", "Sensor S1: C1 full (raw, filtered into Outfeed_Full)"),
    ("OvenTemp", "INT", "%MW12", "0", "Analog sensor: preform surface temperature in the oven pocket, 0.1 degC (1050 = 105.0 degC)"),
    ("BlowPressure_Raw", "INT", "%MW13", "0", "Analog sensor: blow pressure transducer, raw counts 0-27648 = 0-40.0 bar (below 0 = wire break)"),
    ("E_Stop_OK", "BOOL", "%MX1.1", "FALSE", "Interface: E-stop chain healthy (FALSE = stop everything at once)"),
    ("Heartbeat", "BOOL", "%MX1.2", "FALSE", "Interface: toggled every 1 s by the simulator, watched by the heartbeat watchdog"),
    ("Cmd", "INT", "%MW0", "0", "HMI command: 1 Start, 2 Stop, 3 Reset, 4 Reset counter (PLC sets it back to 0 after reading)"),
    ("ModeReq", "INT", "%MW1", "0", "HMI mode: 0 Stop, 1 Auto, 2 Manual"),
    ("OvenSetpoint", "INT", "%MW30", "1050", "HMI setpoint: preform temperature, 0.1 degC"),
    ("OvenBand", "INT", "%MW31", "50", "HMI setpoint: Oven_Ready window +- around OvenSetpoint, 0.1 degC"),
    ("Kp", "INT", "%MW32", "10", "HMI setpoint: P gain of the lamp controller, % per degC"),
    ("Bias", "INT", "%MW33", "0", "HMI setpoint: lamp power added to the P term, % (0 shows the P offset)"),
    ("BlowSetpoint", "INT", "%MW34", "350", "HMI setpoint: high-blow pressure to reach, 0.1 bar"),
    ("Man_HopperFeed", "BOOL", "%MX2.0", "FALSE", "HMI manual button (hold-to-run): HopperFeed"),
    ("Man_Loader", "BOOL", "%MX2.1", "FALSE", "HMI manual button (hold-to-run): Loader"),
    ("Man_Arm", "BOOL", "%MX2.2", "FALSE", "HMI manual button (hold-to-run): ArmToMould"),
    ("Man_Gripper", "BOOL", "%MX2.3", "FALSE", "HMI manual button (hold-to-run): Gripper"),
    ("Man_MouldClose", "BOOL", "%MX2.4", "FALSE", "HMI manual button (toggle): each click flips Man_Mould_Closed"),
    ("Man_Rod", "BOOL", "%MX2.5", "FALSE", "HMI manual button (hold-to-run): StretchRod (only with the mould closed)"),
    ("Man_Ejector", "BOOL", "%MX2.6", "FALSE", "HMI manual button (hold-to-run): Ejector"),
    ("HopperFeed", "BOOL", "%QX0.0", "FALSE", "Output: hopper elevator / chute vibrator"),
    ("Loader", "BOOL", "%QX0.1", "FALSE", "Output: pushes the waiting preform into the oven pocket (spring return)"),
    ("ArmToMould", "BOOL", "%QX0.2", "FALSE", "Output: transfer arm, ON = into the mould, OFF = home above the oven"),
    ("Gripper", "BOOL", "%QX0.3", "FALSE", "Output: gripper on the arm, ON = closed"),
    ("MouldClose", "BOOL", "%QX0.4", "FALSE", "Output: mould halves, ON = closed and locked (seal-in, opens only with Pressure_Safe and Rod_Up)"),
    ("StretchRod", "BOOL", "%QX0.5", "FALSE", "Output: stretch rod, ON = down (only with the mould closed)"),
    ("PreBlowValve", "BOOL", "%QX0.6", "FALSE", "Output: low-pressure air during the stretch"),
    ("HighBlowValve", "BOOL", "%QX0.7", "FALSE", "Output: high-pressure air up to BlowSetpoint, then held"),
    ("ExhaustValve", "BOOL", "%QX1.0", "FALSE", "Output: vents the bottle, normally open (ON except STRETCH, BLOW, HOLD)"),
    ("Ejector", "BOOL", "%QX1.1", "FALSE", "Output: pushes the bottle out of the open mould onto C1"),
    ("LampPower", "INT", "%QW0", "0", "Analog output: IR lamp power 0-100 % (P controller)"),
    ("State", "INT", "%MW50", "0", "Machine state: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault"),
    ("Step", "INT", "%MW51", "0", "Oven station: 0 IDLE, 10 FEED, 15 LOAD, 20 HEAT, 25 HOT, 900 FAULT"),
    ("MouldStep", "INT", "%MW52", "0", "Mould station: 0 IDLE, 10 WAIT_PREFORM, 20-26 transfer, 30 CLOSE ... 80 EJECT, 900 FAULT"),
    ("BottleCount", "INT", "%MW53", "0", "Bottles put onto C1 (rising edge of Bottle_Ejected), zeroed by Cmd = 4"),
    ("Alm_NoPreforms", "BOOL", "%MX5.0", "FALSE", "Warning: hopper empty and the oven waits for a preform (Starved, not latched)"),
    ("Alm_HandlingJam", "BOOL", "%MX5.1", "FALSE", "Alarm: loader, arm or ejector did not reach its sensor in time (FAULT)"),
    ("Alm_OutfeedBlocked", "BOOL", "%MX5.2", "FALSE", "Warning: Blocked longer than BlockedWarn"),
    ("Alm_CommLost", "BOOL", "%MX5.3", "FALSE", "Alarm: heartbeat not toggled for 3 s (outputs off via Master_Enable)"),
    ("Alm_MouldFault", "BOOL", "%MX5.4", "FALSE", "Alarm: mould or stretch rod did not reach its position in time (FAULT)"),
    ("Alm_OvenOverTemp", "BOOL", "%MX5.5", "FALSE", "Alarm: OvenTemp >= OvenMax (FAULT in every mode, lamp power 0)"),
    ("Alm_OvenHeatTimeout", "BOOL", "%MX5.6", "FALSE", "Alarm: HEAT did not reach Preform_Hot within HeatTimeout (FAULT)"),
    ("Alm_BlowFault", "BOOL", "%MX5.7", "FALSE", "Alarm: BLOW did not reach BlowSetpoint within BlowTimeout (FAULT)"),
    ("Alm_ExhaustFault", "BOOL", "%MX7.0", "FALSE", "Alarm: EXHAUST did not reach Pressure_Safe within ExhaustTimeout (FAULT, mould stays closed)"),
    ("Alm_OverPressure", "BOOL", "%MX7.1", "FALSE", "Alarm: BlowPressure >= MaxPressure (FAULT)"),
    ("Alm_PressureSensor", "BOOL", "%MX7.2", "FALSE", "Alarm: BlowPressure_Raw outside RawMin..RawMax, wire break or over range (FAULT)"),
    ("Outfeed_Full", "BOOL", "%MX3.0", "FALSE", "Conditioned: C1_Full ON 2 s without a break, released after OFF 3 s"),
    ("Oven_Ready", "BOOL", "%MX3.1", "FALSE", "Window compare: OvenTemp within OvenSetpoint +- OvenBand"),
    ("Jog_Block", "BOOL", "%MX3.2", "FALSE", "Jog watchdog: a hold-to-run button held > 10 s, manual outputs ignored until released"),
    ("Preform_Hot", "BOOL", "%MX3.3", "FALSE", "Conditioned: preform inside Oven_Ready for SoakTime without a break (Soak_Timer.Q)"),
    ("Pressure_Reached", "BOOL", "%MX3.4", "FALSE", "Conditioned: BlowPressure >= BlowSetpoint"),
    ("Pressure_Safe", "BOOL", "%MX3.5", "FALSE", "Conditioned: BlowPressure < ExhaustLimit and the sensor in range (the mould may open)"),
    ("Starved", "BOOL", "%MX3.6", "FALSE", "Conditioned: no preform at the loader for StarvedDelay while the mould waits too -> State 2"),
    ("Lamps_On", "BOOL", "%MX3.7", "FALSE", "Conditioned: Master_Enable AND Auto AND no FAULT AND Preform_In_Oven, enables LampPower"),
    ("BlowPressure", "INT", "%MW41", "0", "Computed: BlowPressure_Raw scaled to 0.1 bar in DINT (Raw * 400 / 27648)"),
    ("OvenError", "INT", "%MW42", "0", "Computed: OvenSetpoint - OvenTemp, 0.1 degC (input of the P controller)"),
]
CONSTANTS = [
    ("OvenMax", "INT", "1300", "Constant: over-temperature limit, 0.1 degC (130.0 degC, PET crystallises)"),
    ("ErrMax", "INT", "1000", "Constant: upper LIMIT on OvenError before the MUL (Kp * error stays inside INT)"),
    ("ErrMin", "INT", "-1000", "Constant: lower LIMIT on OvenError before the MUL"),
    ("RawFull", "DINT", "27648", "Constant: raw counts at full scale (20 mA), DINT for the scaling MUL / DIV"),
    ("PressFull", "DINT", "400", "Constant: full-scale pressure, 0.1 bar (40.0 bar), DINT for the scaling MUL / DIV"),
    ("RawMin", "INT", "-1000", "Constant: below this raw value = wire break (Alm_PressureSensor)"),
    ("RawMax", "INT", "32511", "Constant: above this raw value = over range (Alm_PressureSensor)"),
    ("ExhaustLimit", "INT", "5", "Constant: below this pressure (0.1 bar) the mould may open"),
    ("MaxPressure", "INT", "400", "Constant: over-pressure limit, 0.1 bar (40.0 bar)"),
    ("SoakTime", "TIME", "T#1s", "Constant: preform inside Oven_Ready this long = heated through"),
    ("GripTime", "TIME", "T#300ms", "Constant: gripper closes / opens"),
    ("HoldTime", "TIME", "T#1s", "Constant: high pressure held, the PET cools against the mould"),
    ("MoveTimeout", "TIME", "T#3s", "Constant: loader, arm, mould, rod, ejector must reach their sensor within this time"),
    ("HeatTimeout", "TIME", "T#15s", "Constant: HEAT must reach Preform_Hot within this time"),
    ("BlowTimeout", "TIME", "T#2s", "Constant: BLOW must reach BlowSetpoint within this time"),
    ("ExhaustTimeout", "TIME", "T#3s", "Constant: EXHAUST must reach Pressure_Safe within this time"),
    ("StarvedDelay", "TIME", "T#2s", "Constant: no preform at the loader this long = Starved"),
    ("BlockedWarn", "TIME", "T#2m", "Constant: Blocked this long -> Alm_OutfeedBlocked"),
]

# ---- sanity checks: no duplicate name or address
names = [v[0] for v in LOCATED] + [c[0] for c in CONSTANTS]
assert len(names) == len(set(names)), "duplicate name"
locs = [v[2] for v in LOCATED]
assert len(locs) == len(set(locs)), "duplicate address"

lines = ["PROGRAM main", "  VAR"]
for n, t, loc, init, doc in LOCATED:
    lines.append(f"    {n} : {t} AT {loc} := {init}; (* {doc} *)")
for n, t, init, doc in CONSTANTS:
    lines.append(f"    {n} : {t} := {init}; (* {doc} *)")
lines.append("  END_VAR")
header = "\n".join(lines) + "\n\n"

# ---- clone the packer rung "Start 1/3" (contact -> MOVE 5 -> Step)
src = PACKER.read_text()
i = src.index("END_VAR") + len("END_VAR")
j = src.rindex("END_PROGRAM")
packer = json.loads(src[i:j])
rung = next(r for r in packer["rungs"] if r["comment"].startswith("Start 1/3"))
text = json.dumps(rung)
for old in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
    text = text.replace(old, str(uuid.uuid4()))
used = set()
def fresh_id(_m):
    while True:
        k = str(random.randint(1_000_000, 9_999_999))
        if k not in used:
            used.add(k)
            return f'"numericId": "{k}"'
text = re.sub(r'"numericId": "\d+"', fresh_id, text)
rung = json.loads(text)
rung["comment"] = "STAGE 0 TEST RUNG, delete after the %QW0 check: E_Stop_OK -> MOVE 42 -> LampPower (holding register 0?)"

doc = {n: d for n, _, _, _, d in LOCATED}
for node in rung["nodes"]:
    data = node["data"]
    if node["type"] == "contact":
        data["variable"].update(name="E_Stop_OK", location="%MX1.1", documentation=doc["E_Stop_OK"])
    elif node["type"] == "block":
        for cv in data["connectedVariables"]:
            if cv["handleId"] == "IN":
                cv["variable"] = {"name": "42"}
            else:
                cv["variable"].update(name="LampPower", location="%QW0", initialValue="0", documentation=doc["LampPower"])
    elif node["type"] == "variable":
        if data["variant"] == "input":
            data["variable"] = {"name": "42"}
        else:
            data["variable"] = {"name": "LampPower"} | {k: v for k, v in data["variable"].items() if k != "name"}
            if "location" in data["variable"]:
                data["variable"].update(location="%QW0", documentation=doc["LampPower"])

body = json.dumps({"name": "main", "rungs": [rung]}, indent=2)
OUT.write_text(header + body + "\nEND_PROGRAM")
print(f"{len(LOCATED)} located, {len(CONSTANTS)} constants, 1 rung -> {OUT}")
print(json.dumps([n["data"].get("variable") for n in rung["nodes"]], indent=0)[:1500])
