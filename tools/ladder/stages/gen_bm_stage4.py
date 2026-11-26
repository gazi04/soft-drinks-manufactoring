"""Blow moulder ladder stage 4: sections 4-6 for the oven station (step decode of both words, Seq_Enable,
Lamps_On, Soak_Timer -> Preform_Hot, abort, Start with restart by sensors, oven transitions 10-25).

Dry run by default (main.ld.stage4 in the temp folder); --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
assert len(body["rungs"]) == 26 and body["rungs"][25]["comment"].startswith("Outfeed_Full filter 3/3"), "main.ld not at stage 3"

OVEN_BITS = [(0, "Oven_Idle", "IDLE"), (10, "Oven_Feed", "FEED: hopper feeds a preform to the loader"),
             (15, "Oven_Load", "LOAD: loader pushes the preform into the oven pocket"),
             (20, "Oven_Heat", "HEAT: lamps heat the preform until Preform_Hot"),
             (25, "Oven_Hot", "HOT: hot preform waits for the transfer arm"), (900, "Oven_Fault", "FAULT")]
MOULD_BITS = [(0, "Mould_Idle", "IDLE"), (10, "Mould_WaitPreform", "WAIT_PREFORM: open, empty mould waits for a hot preform"),
              (20, "Mould_Grip", "GRIP: gripper closes on the preform"), (22, "Mould_Swing", "SWING: arm carries the preform into the mould"),
              (24, "Mould_Release", "RELEASE: gripper opens, preform stays in the mould"),
              (26, "Mould_ArmBack", "ARM_BACK: arm returns to the oven"), (30, "Mould_Closing", "CLOSE: mould halves close"),
              (40, "Mould_Stretch", "STRETCH: rod stretches, pre-blow"), (50, "Mould_Blow", "BLOW: high blow up to BlowSetpoint"),
              (52, "Mould_Hold", "HOLD: high pressure held for HoldTime"), (60, "Mould_Exhaust", "EXHAUST: pressure vented"),
              (62, "Mould_RodUp", "ROD_UP: stretch rod back up"), (70, "Mould_Opening", "OPEN: mould halves open"),
              (80, "Mould_Eject", "EJECT: ejector pushes the bottle onto C1"), (900, "Mould_Fault", "FAULT")]

INTERNALS = (
    [(n, "BOOL", "FALSE", f"Step bit: Step = {v} ({d})") for v, n, d in OVEN_BITS]
    + [(n, "BOOL", "FALSE", f"Step bit: MouldStep = {v} ({d})") for v, n, d in MOULD_BITS]
    + [("Mould_From30", "BOOL", "FALSE", "MouldStep >= 30 (CLOSE or later)"),
       ("Mould_To62", "BOOL", "FALSE", "MouldStep <= 62 (ROD_UP or earlier)"),
       ("Mould_CloseSteps", "BOOL", "FALSE", "MouldStep 30-62: the steps that need the mould closed (mould seal-in, stage 7)"),
       ("Seq_Enable", "BOOL", "FALSE", "Mode_Auto AND Master_Enable: the sequencers may move"),
       ("Soak_Timer", "TON", None, "Preform inside Oven_Ready for SoakTime without a break in HEAT -> Preform_Hot"),
       ("Start_Accepted", "BOOL", "FALSE", "Start_Cmd in Auto with both stations IDLE and No_Fault (one scan)"),
       ("No_Fault", "BOOL", "TRUE", "No FAULT alarm latched; TRUE until the alarm section (stage 9) computes it"),
       ("Stop_Request", "BOOL", "FALSE", "Stop latch; FALSE until the Stop rungs (stage 8) compute it")]
)
B = L.Builder(head, body, INTERNALS)


def decode(word, bits, part, total, what):
    text = ", ".join(f"{v} -> {n}" for v, n, _ in bits)
    return B.chain(f"{what} step decode {part}/{total}: {word} = {text}. EQ chained EN -> ENO, every compare writes "
                   "its own BOOL, so the later rungs read a contact instead of comparing a number",
                   [("EQ", {"IN1": word, "IN2": str(v), "OUT": n}) for v, n, _ in bits])


rungs = [B.header("SECTION 4 STEP DECODE. Step (oven station) and MouldStep (mould station) -> one BOOL per step (EQ), "
                  "Mould_CloseSteps, Seq_Enable, Lamps_On. (Header rung, no logic)")]
rungs.append(decode("Step", OVEN_BITS[:3], 1, 2, "Oven"))
rungs.append(decode("Step", OVEN_BITS[3:], 2, 2, "Oven"))
for i in range(5):
    rungs.append(decode("MouldStep", MOULD_BITS[3 * i:3 * i + 3], i + 1, 5, "Mould"))
rungs.append(B.chain("Mould close range 1/2: Mould_From30 = MouldStep >= 30, Mould_To62 = MouldStep <= 62",
                     [("GE", {"IN1": "MouldStep", "IN2": "30", "OUT": "Mould_From30"}),
                      ("LE", {"IN1": "MouldStep", "IN2": "62", "OUT": "Mould_To62"})]))
rungs.append(B.series("Mould close range 2/2: Mould_CloseSteps = MouldStep 30-62 (CLOSE ... ROD_UP), the steps that keep "
                      "the mould closed. Used by the mould seal-in (stage 7)",
                      ["Mould_From30", "Mould_To62"], ("coil", "Mould_CloseSteps")))
rungs.append(B.clone(B.P[31], "Seq_Enable: the sequencers may move only in Auto with Master_Enable (E-stop chain healthy, "
                              "heartbeat alive). Every transition rung starts with this contact"))
rungs.append(B.series("Lamps_On = Seq_Enable AND NOT Oven_Fault AND Preform_In_Oven: the lamps heat only in Auto, not in "
                      "FAULT, and only with a preform in the pocket (rule 5; also in IDLE, so a preform left there stays "
                      "warm). LampPower follows Lamp_Calc only while this is TRUE (stage 6)",
                      ["Seq_Enable", "!Oven_Fault", "Preform_In_Oven"], ("coil", "Lamps_On")))

rungs.append(B.header("SECTION 5 DONE BITS AND TIMERS. Timers that end a step, above the transitions that read them "
                      "(Soak_Timer; the mould station's Grip_Timer / Hold_Timer in stage 5). (Header rung, no logic)"))
rungs.append(B.series("Soak_Timer (TON): runs in HEAT while OvenTemp is inside the Oven_Ready window; leaving the window "
                      "restarts it. Preform_Hot after SoakTime (1 s) without a break: heated through, not only on the "
                      "surface. TRUE for one scan only (the step moves on in the same scan)",
                      ["Oven_Heat", "Oven_Ready"], ("ton", "Soak_Timer", "SoakTime", "Preform_Hot")))

rungs.append(B.header("SECTION 6 SEQUENCER TRANSITIONS. Abort, Start of both stations, then each transition as Seq_Enable "
                      "AND step bit AND condition -> MOVE next step. (Header rung, no logic)"))
rungs.append(B.clone(B.P[46], "Abort: leaving Auto (Mode Stop or Manual) or losing Master_Enable (E-stop, comm lost) -> "
                              "Step := 0 AND MouldStep := 0 (two MOVEs chained ENO -> EN). NOT Oven_Fault: a FAULT (both "
                              "words 900) is kept until Reset (stage 9)",
                     {"Step_Fault": "Oven_Fault", "WrapStep": "MouldStep"}))
rungs.append(B.clone(B.P[47], "Start accepted: Start_Cmd in Auto with BOTH stations in IDLE (one Start for the machine) "
                              "and no FAULT alarm latched (No_Fault, computed in stage 9; TRUE until then). The four rungs "
                              "below use this bit",
                     {"Step_Idle": "Oven_Idle", "Wrap_Idle": "Mould_Idle"}))
rungs.append(B.series("Start 1/4: default for both stations, Step := 10 FEED, MouldStep := 10 WAIT_PREFORM. The rungs "
                      "below overwrite it where a sensor shows a half-done cycle (rule 9): within one scan the LAST write "
                      "wins, so the rung order is the priority",
                      ["Start_Accepted"], ("move", [("10", "Step"), ("10", "MouldStep")])))
rungs.append(B.series("Start 2/4: a preform is already in the oven pocket (cold after Manual, or warm) -> Step := 20 HEAT, "
                      "it is heated again to Preform_Hot (rule 9)",
                      ["Start_Accepted", "Preform_In_Oven"], ("move", [("20", "Step")])))
rungs.append(B.series("Start 3/4: mould open with something in it (bottle or preform) -> MouldStep := 80 EJECT (rule 9; "
                      "no reject, it goes onto C1)",
                      ["Start_Accepted", "Mould_Open", "Bottle_In_Mould"], ("move", [("80", "MouldStep")])))
rungs.append(B.series("Start 4/4: mould closed (FAULT or E-stop during a blow, pressure may be inside) -> MouldStep := 60 "
                      "EXHAUST, then rod up, open, eject (rule 9). Last rung = highest priority: with both mould "
                      "sensors TRUE (sensor fault) the safe way wins, exhaust first",
                      ["Start_Accepted", "Mould_Closed"], ("move", [("60", "MouldStep")])))
rungs.append(B.series("Transition 10 -> 15 FEED -> LOAD: a preform waits at the loader, the pocket is empty, C1 is not "
                      "full (Blocked: no new preform) and no Stop is requested (Stop_Request from stage 8: FEED -> IDLE "
                      "instead)",
                      ["Seq_Enable", "Oven_Feed", "Preform_At_Loader", "!Preform_In_Oven", "!Outfeed_Full", "!Stop_Request"],
                      ("move", [("15", "Step")])))
rungs.append(B.series("Transition 15 -> 20 LOAD -> HEAT: the preform arrived in the oven pocket",
                      ["Seq_Enable", "Oven_Load", "Preform_In_Oven"], ("move", [("20", "Step")])))
rungs.append(B.series("Transition 20 -> 25 HEAT -> HOT: Preform_Hot (soaked inside the window for SoakTime)",
                      ["Seq_Enable", "Oven_Heat", "Preform_Hot"], ("move", [("25", "Step")])))
rungs.append(B.series("Transition 25 -> 10 HOT -> FEED: the pocket is empty, the transfer arm took the preform (mould "
                      "station SWING, stage 5). The lamps go off with Preform_In_Oven",
                      ["Seq_Enable", "Oven_Hot", "!Preform_In_Oven"], ("move", [("10", "Step")])))

names = B.check(rungs)
out = B.output(rungs)
if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage4").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage4")
print(f"{len(rungs)} new rungs ({len(body['rungs']) + len(rungs)} total), {len(INTERNALS)} declarations")
