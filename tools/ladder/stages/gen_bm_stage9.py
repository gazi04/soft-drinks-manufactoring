"""Blow moulder ladder stage 9: alarms and FAULT (section 9) + Pressure_Safe with NOT Alm_PressureSensor.

Dry run by default (main.ld.stage9 in the temp folder); --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
R = body["rungs"]
assert len(R) == 106 and R[105]["comment"].startswith("State 5/5"), "main.ld not at stage 8"
psafe = next(i for i, r in enumerate(R) if r["comment"].startswith("Pressure_Safe"))

TIMEOUTS = [  # (timer, contacts, PT, alarm, what)
    ("Load_TO", ["Oven_Load"], "MoveTimeout", "Alm_HandlingJam", "LOAD: preform not in the pocket"),
    ("Swing_TO", ["Mould_Swing"], "MoveTimeout", "Alm_HandlingJam", "SWING: arm not at the mould"),
    ("Release_TO", ["Mould_Release"], "MoveTimeout", "Alm_HandlingJam", "RELEASE: preform not seen in the mould"),
    ("ArmBack_TO", ["Mould_ArmBack"], "MoveTimeout", "Alm_HandlingJam", "ARM_BACK: arm not home"),
    ("Eject_TO", ["Mould_Eject", "Ejector"], "MoveTimeout", "Alm_HandlingJam",
     "EJECT: bottle still in the mould while the ejector really pushes (not while C1 is full and it is held off)"),
    ("Close_TO", ["Mould_Closing"], "MoveTimeout", "Alm_MouldFault", "CLOSE: mould not closed"),
    ("Stretch_TO", ["Mould_Stretch"], "MoveTimeout", "Alm_MouldFault", "STRETCH: rod not down"),
    ("RodUp_TO", ["Mould_RodUp"], "MoveTimeout", "Alm_MouldFault", "ROD_UP: rod not up"),
    ("Open_TO", ["Mould_Opening"], "MoveTimeout", "Alm_MouldFault", "OPEN: mould not open"),
    ("Heat_TO", ["Oven_Heat"], "HeatTimeout", "Alm_OvenHeatTimeout", "HEAT: Preform_Hot not reached in 15 s (lamps failed)"),
    ("Blow_TO", ["Mould_Blow"], "BlowTimeout", "Alm_BlowFault", "BLOW: BlowSetpoint not reached in 2 s"),
    ("Exhaust_TO", ["Mould_Exhaust"], "ExhaustTimeout", "Alm_ExhaustFault", "EXHAUST: not below 0.5 bar in 3 s"),
]
FAULTS = ["Alm_HandlingJam", "Alm_MouldFault", "Alm_OvenOverTemp", "Alm_OvenHeatTimeout", "Alm_BlowFault",
          "Alm_ExhaustFault", "Alm_OverPressure", "Alm_PressureSensor"]
INTERNALS = ([(t, "TON", None, f"Timeout {what} -> {alm}") for t, _, _, alm, what in TIMEOUTS]
             + [("Blocked_TO", "TON", None, "Blocked (State 3) longer than BlockedWarn -> Alm_OutfeedBlocked")])
B = L.Builder(head, body, INTERNALS)

new_psafe = B.series("Pressure_Safe: low pressure AND a sensor that can be trusted AND no latched sensor alarm, only "
                     "then may the mould open (stage 7). A wire break reads about -14 (low) but Press_Range_OK is "
                     "FALSE; after the wire comes back, NOT Alm_PressureSensor (section 9, last scan's value) keeps the "
                     "mould closed until Reset",
                     ["Press_Low", "Press_Range_OK", "!Alm_PressureSensor"], ("coil", "Pressure_Safe"))

sec9 = [B.header("SECTION 9 ALARMS AND FAULT. One TON per step timeout -> (S) alarm, limit alarms in every mode, "
                 "resets only when the cause is gone, No_Fault, Reset 900 -> 0, MOVE 900 last. (Header rung, no logic)")]
for t, contacts, pt, alm, what in TIMEOUTS:
    sec9.append(B.series(f"Timeout {t} ({pt}): {what} -> (S) {alm} (FAULT). The timer runs only in its own step and "
                         "restarts there", contacts, ("ton", t, pt, alm, "set")))
sec9.append(B.rung("Alm_OutfeedBlocked (warning, not latched, no FAULT): Blocked (C1 full AND a bottle waits in EJECT "
                   "OR the oven waits in FEED, State 3) longer than BlockedWarn (2 min)",
                   [["Mould_Eject"], ["Oven_Feed"]], ["Outfeed_Full"],
                   ("ton", "Blocked_TO", "BlockedWarn", "Alm_OutfeedBlocked")))
sec9 += [
    B.series("Alm_OvenOverTemp set: OvenTemp >= OvenMax (130.0 degC) in EVERY mode (PET crystallises); the lamps are "
             "already cut by Oven_BelowMax (stage 6)", ["!Oven_BelowMax"], ("coil", "Alm_OvenOverTemp", "set")),
    B.series("Alm_OverPressure set: BlowPressure >= MaxPressure (40.0 bar) in every mode (regulator failed); high blow "
             "is already cut by Below_MaxPress", ["!Below_MaxPress"], ("coil", "Alm_OverPressure", "set")),
    B.series("Alm_PressureSensor set: raw value outside RawMin..RawMax (wire break, over range) in every mode; "
             "Pressure_Safe is FALSE, a closed mould stays closed", ["!Press_Range_OK"],
             ("coil", "Alm_PressureSensor", "set")),
]
for alm in ["Alm_HandlingJam", "Alm_MouldFault", "Alm_OvenHeatTimeout", "Alm_BlowFault"]:
    sec9.append(B.series(f"{alm} reset: Reset_Cmd. A timeout's cause is always gone in FAULT (every step bit is FALSE "
                         "at 900, so its timer is off)", ["Reset_Cmd"], ("coil", alm, "reset")))
for alm, ok, why in [("Alm_ExhaustFault", "Pressure_Safe", "only with the pressure gone (Pressure_Safe)"),
                     ("Alm_OvenOverTemp", "Oven_BelowMax", "only below OvenMax"),
                     ("Alm_OverPressure", "Below_MaxPress", "only below MaxPressure"),
                     ("Alm_PressureSensor", "Press_Range_OK", "only with the raw value back in range")]:
    sec9.append(B.series(f"{alm} reset: Reset_Cmd {why}; a Reset with the cause still there is ignored",
                         ["Reset_Cmd", ok], ("coil", alm, "reset")))
sec9 += [
    B.series("No_Fault = NOT any of the 8 FAULT alarms (8 NC contacts in series = De Morgan of the OR). Start_Accepted "
             "(section 6) reads it from the last scan. Alm_CommLost works through Master_Enable, the warnings never "
             "stop the machine", [f"!{a}" for a in FAULTS], ("coil", "No_Fault")),
    B.series("Reset 900 -> 0 for both stations: Auto + Reset after a FAULT. If a cause is still there, its alarm stays "
             "latched and the rung below puts both back to 900 in the same scan",
             ["Seq_Enable", "Oven_Fault", "Reset_Cmd"], ("move", [("0", "Step"), ("0", "MouldStep")])),
    B.series("FAULT: any FAULT alarm -> Step := 900 AND MouldStep := 900, the LAST writer of both step words (also in "
             "Mode Stop and Manual). All outputs off except the open exhaust and a closed mould (held)",
             ["!No_Fault"], ("move", [("900", "Step"), ("900", "MouldStep")])),
]
new = [new_psafe] + sec9
B.check(new)
rungs = R[:psafe] + [new_psafe] + R[psafe + 1:] + sec9
out = L.join(B.head, rungs)
if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage9").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage9")
print(f"{len(sec9)} new rungs + Pressure_Safe replaced ({len(rungs)} total), {len(INTERNALS)} declarations")
