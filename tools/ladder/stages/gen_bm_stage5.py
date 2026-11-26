"""Blow moulder ladder stage 5: mould station sequencer (Grip_Timer, Release_Timer, Hold_Timer in section 5,
transitions 10 -> 20 -> ... -> 80 -> 10 at the end of section 6).

Dry run by default (main.ld.stage5 in the temp folder); --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
R = body["rungs"]
assert len(R) == 51 and R[50]["comment"].startswith("Transition 25 -> 10"), "main.ld not at stage 4"
soak = next(i for i, r in enumerate(R) if r["comment"].startswith("Soak_Timer"))
sec5 = next(i for i, r in enumerate(R) if r["comment"].startswith("SECTION 5"))
assert soak == sec5 + 1 and R[soak + 1]["comment"].startswith("SECTION 6")

INTERNALS = [
    ("Grip_Timer", "TON", None, "GRIP step time: gripper closes on the preform for GripTime"),
    ("Release_Timer", "TON", None, "RELEASE step time: gripper opens for GripTime before the arm goes back"),
    ("Hold_Timer", "TON", None, "HOLD step time: high pressure held for HoldTime, the PET cools against the mould"),
]
B = L.Builder(head, body, INTERNALS)

timers = [
    B.series("Grip_Timer (TON): GRIP lasts GripTime (0.3 s), the gripper has no sensor. A step time: the timer runs "
             "only in its own step and is reset when the step is left",
             ["Mould_Grip"], ("ton", "Grip_Timer", "GripTime")),
    B.series("Release_Timer (TON): RELEASE opens the gripper for GripTime (0.3 s). Its own timer, not Grip_Timer: "
             "each step time belongs to one step, so no rung order or reset in between is needed",
             ["Mould_Release"], ("ton", "Release_Timer", "GripTime")),
    B.series("Hold_Timer (TON): HOLD keeps the high pressure for HoldTime (1.0 s), the PET cools against the cold "
             "mould walls and keeps its shape",
             ["Mould_Hold"], ("ton", "Hold_Timer", "HoldTime")),
]

T = [  # (from, to, name, contacts after Seq_Enable + step bit, comment)
    (10, 20, "Mould_WaitPreform", ["Oven_Hot", "Mould_Open", "!Bottle_In_Mould", "Arm_At_Oven", "Rod_Up"],
     "WAIT_PREFORM -> GRIP: hand-over. The oven station holds a hot preform (Step 25 HOT), the mould is open and "
     "empty, the arm is home above the pocket and the rod is up (out of the way)"),
    (20, 22, "Mould_Grip", ["Grip_Timer.Q"], "GRIP -> SWING: gripper closed (Grip_Timer)"),
    (22, 24, "Mould_Swing", ["Arm_At_Mould"], "SWING -> RELEASE: the arm is inside the open mould. The preform has "
     "left the oven pocket, the oven station goes back to FEED (25 -> 10)"),
    (24, 26, "Mould_Release", ["Release_Timer.Q", "Bottle_In_Mould"], "RELEASE -> ARM_BACK: gripper open "
     "(Release_Timer) AND the photo eye sees the preform in the mould; without it RELEASE waits (timeout in stage 9)"),
    (26, 30, "Mould_ArmBack", ["Arm_At_Oven"], "ARM_BACK -> CLOSE: the arm is home, out of the mould"),
    (30, 40, "Mould_Closing", ["Mould_Closed"], "CLOSE -> STRETCH: mould halves closed and locked"),
    (40, 50, "Mould_Stretch", ["Rod_Down"], "STRETCH -> BLOW: the rod reached the bottom, the pre-blow has formed "
     "the bubble"),
    (50, 52, "Mould_Blow", ["Pressure_Reached"], "BLOW -> HOLD: BlowPressure >= BlowSetpoint (35.0 bar)"),
    (52, 60, "Mould_Hold", ["Hold_Timer.Q"], "HOLD -> EXHAUST: HoldTime over (Hold_Timer)"),
    (60, 62, "Mould_Exhaust", ["Pressure_Safe"], "EXHAUST -> ROD_UP: pressure below ExhaustLimit (0.5 bar) and the "
     "sensor in range"),
    (62, 70, "Mould_RodUp", ["Rod_Up"], "ROD_UP -> OPEN: the rod is out of the bottle"),
    (70, 80, "Mould_Opening", ["Mould_Open"], "OPEN -> EJECT: mould halves fully open"),
    (80, 10, "Mould_Eject", ["!Bottle_In_Mould"], "EJECT -> WAIT_PREFORM: the bottle left the mould (onto C1); with "
     "C1 full the ejector is held off (stage 7) and the bottle waits here (Blocked)"),
]
transitions = [B.series(f"Mould transition {a} -> {b} {c}", ["Seq_Enable", step, *cs], ("move", [(str(b), "MouldStep")]))
               for a, b, step, cs, c in T]

new = timers + transitions
B.check(new)
rungs = list(R)
rungs[sec5] = dict(rungs[sec5], comment=rungs[sec5]["comment"].replace(
    "the mould station's Grip_Timer / Hold_Timer in stage 5", "the mould station's Grip_Timer, Release_Timer, Hold_Timer"))
assert "Release_Timer" in rungs[sec5]["comment"]
rungs = rungs[:soak + 1] + timers + rungs[soak + 1:] + transitions
out = L.join(B.head, rungs)

if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage5").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage5")
print(f"{len(new)} new rungs ({len(rungs)} total), {len(INTERNALS)} declarations")
