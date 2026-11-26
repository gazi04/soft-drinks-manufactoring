"""Blow moulder ladder stage 8: Stop at safe points, bottle counter, Starved, Alm_NoPreforms, State.

Both_Idle in section 4 (after Lamps_On), Stop latch + stop rungs in section 6 (after Start 4/4), new
section 8 at the end. Dry run by default; --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
R = body["rungs"]
assert len(R) == 92 and R[91]["comment"].startswith("Outputs 2/2"), "main.ld not at stage 7"


def index(prefix):
    return next(i for i, r in enumerate(R) if r["comment"].startswith(prefix))


i_lamps_on, i_start4 = index("Lamps_On = "), index("Start 4/4")

INTERNALS = [
    ("Both_Idle", "BOOL", "FALSE", "Step = 0 AND MouldStep = 0: the machine is stopped (releases Stop_Request, State 0)"),
    ("Bottle_Counter", "CTU", None, "Counts Bottle_Ejected rising edges -> BottleCount; R = Count_Reset_Cmd (Cmd 4)"),
    ("Feed_Empty_Timer", "TON", None, "FEED without a preform at the loader for StarvedDelay (Starved filter)"),
]
B = L.Builder(head, body, INTERNALS)

sec4 = [B.series("Both_Idle = Oven_Idle AND Mould_Idle: the machine is stopped only when BOTH stations are IDLE "
                 "(releases Stop_Request, State 0 Stopped)", ["Oven_Idle", "Mould_Idle"], ("coil", "Both_Idle"))]

sec6 = [
    B.rung("Stop request (seal-in latch): Stop_Cmd is a one-scan pulse, so it is remembered here until BOTH stations "
           "reached IDLE (NOT Both_Idle). A Stop in IDLE does nothing. Declared in stage 4: FEED -> LOAD already reads "
           "NOT Stop_Request", [["Stop_Cmd"], ["Stop_Request"]], ["!Both_Idle"], ("coil", "Stop_Request")),
    B.series("Stop at a safe point 1/2, oven: FEED -> IDLE at once (no new preform is loaded). LOAD, HEAT and HOT have "
             "no stop: a preform in the oven is heated, handed over and blown first (rule 7)",
             ["Seq_Enable", "Oven_Feed", "Stop_Request"], ("move", [("0", "Step")])),
    B.series("Stop at a safe point 2/2, mould: WAIT_PREFORM -> IDLE only when the oven station is already IDLE (no hot "
             "preform left for it). Every other mould step finishes the cycle up to EJECT first: Stop always ends "
             "with an empty oven and an empty mould",
             ["Seq_Enable", "Mould_WaitPreform", "Oven_Idle", "Stop_Request"], ("move", [("0", "MouldStep")])),
]

sec8 = [
    B.header("SECTION 8 COUNTER AND STATUS. BottleCount, Starved, Alm_NoPreforms and one State for both stations "
             "(rule 8). (Header rung, no logic)"),
    B.clone(B.P[89], "Bottle_Counter (CTU) counts bottles pushed onto C1 (rising edge of Bottle_Ejected, counted "
                     "inside the block) -> BottleCount. R = Count_Reset_Cmd (Cmd = 4); Reset (Cmd = 3) never touches "
                     "it. PV 32767 = no limit",
            {"Oven_Exit": "Bottle_Ejected", "Pack_Counter": "Bottle_Counter", "PackCount": "BottleCount"}),
    B.series("Feed_Empty_Timer (moved here from stage 3): runs in FEED while no preform waits at the loader; a "
             "preform restarts it. The step contact keeps it from running during HEAT (loader empty there), which "
             "would show Starved the moment FEED starts",
             ["Oven_Feed", "!Preform_At_Loader"], ("ton", "Feed_Empty_Timer", "StarvedDelay")),
    B.series("Starved = Feed_Empty_Timer.Q AND Mould_WaitPreform: no preform for StarvedDelay (2 s) AND the mould has "
             "nothing to do either. A missing preform while a bottle is still blown is not Starved (rule 8)",
             ["Feed_Empty_Timer.Q", "Mould_WaitPreform"], ("coil", "Starved")),
    B.series("Alm_NoPreforms (warning, not latched): FEED, hopper empty and no preform at the loader. Follows the "
             "sensor, clears itself after a refill; no FAULT (answer 1)",
             ["Oven_Feed", "Hopper_Empty", "!Preform_At_Loader"], ("coil", "Alm_NoPreforms")),
    B.series("State 1/5, default: State := 1 Running in every scan. The rungs below overwrite it in rising priority "
             "(rule 8): within one scan the LAST write wins and Modbus sees only the value at the end of the scan",
             [], ("move", [("1", "State")])),
    B.series("State 2/5: Starved -> 2 (no preforms, nothing to blow; continues by itself)",
             ["Starved"], ("move", [("2", "State")])),
    B.rung("State 3/5: Blocked -> 3: C1 full AND (a bottle waits in EJECT OR the oven waits in FEED, no new preform "
           "loaded). Higher priority than Starved",
           [["Mould_Eject"], ["Oven_Feed"]], ["Outfeed_Full"], ("move", [("3", "State")])),
    B.series("State 4/5: both stations IDLE -> 0 Stopped (also in Mode Stop and Manual: the abort keeps both at 0)",
             ["Both_Idle"], ("move", [("0", "State")])),
    B.series("State 5/5: FAULT (Step 900) -> 9, the highest priority, so the last rung",
             ["Oven_Fault"], ("move", [("9", "State")])),
]
new = sec4 + sec6 + sec8
B.check(new)
rungs = R[:i_lamps_on + 1] + sec4 + R[i_lamps_on + 1:i_start4 + 1] + sec6 + R[i_start4 + 1:] + sec8
out = L.join(B.head, rungs)
if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage8").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage8")
print(f"{len(new)} new rungs ({len(rungs)} total), {len(INTERNALS)} declarations")
