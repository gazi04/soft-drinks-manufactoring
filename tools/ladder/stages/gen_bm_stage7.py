"""Blow moulder ladder stage 7: outputs and interlocks (section 7 below the lamps; Below_MaxPress in section 2).

Dry run by default (main.ld.stage7 in the temp folder); --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
R = body["rungs"]
assert len(R) == 70 and R[69]["comment"].startswith("Lamps 2/2"), "main.ld not at stage 6"
psafe = next(i for i, r in enumerate(R) if r["comment"].startswith("Pressure_Safe"))
assert R[psafe + 1]["comment"].startswith("SECTION 3")

FBS = [("FB_Motor", "Mot_HopperFeed", "Req_HopperFeed", "HopperFeed"),
       ("FB_Valve", "Vlv_Loader", "Req_Loader", "Loader"), ("FB_Valve", "Vlv_Arm", "Req_Arm", "ArmToMould"),
       ("FB_Valve", "Vlv_Gripper", "Req_Gripper", "Gripper"), ("FB_Valve", "Vlv_Rod", "Req_Rod", "StretchRod"),
       ("FB_Valve", "Vlv_PreBlow", "Req_PreBlow", "PreBlowValve"), ("FB_Valve", "Vlv_HighBlow", "Req_HighBlow", "HighBlowValve"),
       ("FB_Valve", "Vlv_Ejector", "Req_Ejector", "Ejector")]
INTERNALS = (
    [("Below_MaxPress", "BOOL", "FALSE", "BlowPressure < MaxPressure (40.0 bar): high blow allowed"),
     ("Jog_TON", "TON", None, "Jog watchdog: a hold-to-run button held 10 s -> (S) Jog_Block"),
     ("Exhaust_Shut", "BOOL", "FALSE", "STRETCH, BLOW or HOLD with Master_Enable: the only case the exhaust closes"),
     ("Mould_Click", "BOOL", "FALSE", "Rising edge of Man_MouldClose: one click on the HMI Mould button (one scan)"),
     ("Load_Mould", "BOOL", "FALSE", "Manual entered or no Master_Enable: the toggle copies the real mould position"),
     ("Man_Mould_Closed", "BOOL", "FALSE", "Manual mould toggle latch: TRUE = the operator wants the mould closed"),
     ("Close_Cmd", "BOOL", "FALSE", "Mould close request: Auto in CLOSE..ROD_UP or the Manual toggle, arm home, no ejector"),
     ("Open_Cmd", "BOOL", "FALSE", "Mould open request: Auto outside CLOSE..ROD_UP (not IDLE / FAULT) or the Manual toggle"),
     ("Open_OK", "BOOL", "FALSE", "Open_Cmd AND Pressure_Safe AND Rod_Up: the mould may really open")]
    + [(req, "BOOL", "FALSE", f"Request for {out}: Auto branch OR Manual branch, interlocks after the OR")
       for _, _, req, out in FBS]
    + [(inst, kind, None, f"{out}: {'Run' if kind == 'FB_Motor' else 'Open'} = {req}, Enable = Master_Enable")
       for kind, inst, req, out in FBS]
)
B = L.Builder(head, body, INTERNALS)

sec2 = [B.chain("Below_MaxPress = BlowPressure < MaxPressure (40.0 bar): the high blow valve only below the limit "
                "(interlock, stage 7). With the regulator failed the pressure stops rising at the limit",
                [("LT", {"IN1": "BlowPressure", "IN2": "MaxPressure", "OUT": "Below_MaxPress"})])]

MAN = ["Mode_Manual"]
sec7 = [
    B.rung("Jog watchdog 1/2: any of the six hold-to-run buttons ON for 10 s (Jog_TON) -> (S) Jog_Block: a button "
           "stuck in the HMI does not keep a motion going. Man_MouldClose is not in the list: it is a toggle click, "
           "the mould is a position, not a motion",
           [["Man_HopperFeed"], ["Man_Loader"], ["Man_Arm"], ["Man_Gripper"], ["Man_Rod"], ["Man_Ejector"]],
           [], ("ton", "Jog_TON", "T#10s", "Jog_Block", "set")),
    B.series("Jog watchdog 2/2: all six hold-to-run buttons released -> (R) Jog_Block, jogging allowed again",
             ["!Man_HopperFeed", "!Man_Loader", "!Man_Arm", "!Man_Gripper", "!Man_Rod", "!Man_Ejector"],
             ("coil", "Jog_Block", "reset")),
    B.rung("Exhaust 1/2: Exhaust_Shut = (STRETCH OR BLOW OR HOLD) AND Master_Enable, the only case the exhaust closes "
           "(rule 3)", [["Mould_Stretch"], ["Mould_Blow"], ["Mould_Hold"]], ["Master_Enable"], ("coil", "Exhaust_Shut")),
    B.series("Exhaust 2/2: ExhaustValve = NOT Exhaust_Shut, a plain coil and NO FB: normally open, it vents in IDLE, "
             "Manual, Mode Stop, FAULT, E-stop and comm lost. An FB's Enable = Master_Enable would shut it on E-stop, "
             "the opposite of what is wanted",
             ["!Exhaust_Shut"], ("coil", "ExhaustValve")),
    B.series("Mould toggle 1/3: P contact, Mould_Click TRUE for one scan per click on the HMI Mould button "
             "(Man_MouldClose 0 -> 1). Releasing the button does nothing",
             ["^Man_MouldClose"], ("coil", "Mould_Click")),
    B.rung("Mould toggle 2/3: Load_Mould = Manual_Entry OR NOT Master_Enable: on entering Manual and during an "
           "E-stop / comm lost the toggle copies the real mould position, so neither the mode change nor the release "
           "of the E-stop moves the mould", [["Manual_Entry"], ["!Master_Enable"]], [], ("coil", "Load_Mould")),
    B.rung("Mould toggle 3/3: Man_Mould_Closed := Mode_Manual AND ((Load_Mould AND Mould_Closed) OR (NOT Load_Mould "
           "AND (Man_Mould_Closed XOR Mould_Click))). The XOR is written as two branches: kept without a click, "
           "flipped by a click. Outside Manual the latch is FALSE. The jog watchdog does not touch it",
           [["Load_Mould", "Mould_Closed"], ["!Load_Mould", "Man_Mould_Closed", "!Mould_Click"],
            ["!Load_Mould", "!Man_Mould_Closed", "Mould_Click"]], MAN, ("coil", "Man_Mould_Closed")),
    B.rung("Mould 1/4, Close_Cmd: Auto in CLOSE..ROD_UP (Mould_CloseSteps) OR the Manual toggle; interlocks after the "
           "OR: Master_Enable, arm home (never close on the arm), no ejector (rule 10)",
           [["Seq_Enable", "Mould_CloseSteps"], ["Mode_Manual", "Man_Mould_Closed"]],
           ["Master_Enable", "Arm_At_Oven", "!Ejector"], ("coil", "Close_Cmd")),
    B.rung("Mould 2/4, Open_Cmd: Auto outside CLOSE..ROD_UP but not in IDLE or FAULT (a stopped machine never moves "
           "the mould) OR the Manual toggle asks to open; Master_Enable after the OR: an E-stop never opens it",
           [["Seq_Enable", "!Mould_CloseSteps", "!Mould_Idle", "!Mould_Fault"], ["Mode_Manual", "!Man_Mould_Closed"]],
           ["Master_Enable"], ("coil", "Open_Cmd")),
    B.series("Mould 3/4, Open_OK = Open_Cmd AND Pressure_Safe AND Rod_Up: the mould never opens under pressure or "
             "onto the rod (rule 4)", ["Open_Cmd", "Pressure_Safe", "Rod_Up"], ("coil", "Open_OK")),
    B.rung("Mould 4/4, seal-in, a plain coil and NO FB: MouldClose := (Close_Cmd OR (MouldClose AND Mould_Closed)) AND "
           "NOT Open_OK. A fully closed mould holds itself until an open is asked AND safe: in FAULT, Mode Stop, "
           "E-stop and comm lost it stays closed. A mould still closing (not Mould_Closed) drops and opens again",
           [["Close_Cmd"], ["MouldClose", "Mould_Closed"]], ["!Open_OK"], ("coil", "MouldClose")),
    B.rung("Req_HopperFeed: Auto in FEED until a preform waits at the loader OR Manual Man_HopperFeed (NOT Jog_Block). "
           "No interlock (rule 10)",
           [["Seq_Enable", "Oven_Feed", "!Preform_At_Loader"], ["Mode_Manual", "Man_HopperFeed", "!Jog_Block"]],
           [], ("coil", "Req_HopperFeed")),
    B.rung("Req_Loader: Auto in LOAD OR Manual Man_Loader. Interlocks: never a second preform into the pocket, never "
           "against the closed gripper",
           [["Seq_Enable", "Oven_Load"], ["Mode_Manual", "Man_Loader", "!Jog_Block"]],
           ["!Preform_In_Oven", "!Gripper"], ("coil", "Req_Loader")),
    B.rung("Req_Gripper: Auto in GRIP or SWING (the gripper holds the preform while the arm swings) OR Manual "
           "Man_Gripper. No interlock",
           [["Seq_Enable", "Mould_Grip"], ["Seq_Enable", "Mould_Swing"], ["Mode_Manual", "Man_Gripper", "!Jog_Block"]],
           [], ("coil", "Req_Gripper")),
    B.rung("Req_Arm: Auto in SWING or RELEASE OR Manual Man_Arm. Interlocks: only into an open mould, with the rod up "
           "and the ejector out of the way (the arm returns when this drops, spring return)",
           [["Seq_Enable", "Mould_Swing"], ["Seq_Enable", "Mould_Release"], ["Mode_Manual", "Man_Arm", "!Jog_Block"]],
           ["Mould_Open", "Rod_Up", "!Ejector"], ("coil", "Req_Arm")),
    B.rung("Req_Rod: Auto in STRETCH, BLOW, HOLD or EXHAUST OR Manual Man_Rod. Interlock: only inside a closed mould",
           [["Seq_Enable", "Mould_Stretch"], ["Seq_Enable", "Mould_Blow"], ["Seq_Enable", "Mould_Hold"],
            ["Seq_Enable", "Mould_Exhaust"], ["Mode_Manual", "Man_Rod", "!Jog_Block"]],
           ["Mould_Closed"], ("coil", "Req_Rod")),
    B.series("Req_PreBlow: Auto in STRETCH, only into a closed mould. No Manual button: no blowing by hand",
             ["Seq_Enable", "Mould_Stretch", "Mould_Closed"], ("coil", "Req_PreBlow")),
    B.rung("Req_HighBlow: Auto in BLOW or HOLD. Interlocks: closed mould, exhaust shut (never both open), below "
           "MaxPressure. No Manual button",
           [["Seq_Enable", "Mould_Blow"], ["Seq_Enable", "Mould_Hold"]],
           ["Mould_Closed", "!ExhaustValve", "Below_MaxPress"], ("coil", "Req_HighBlow")),
    B.rung("Req_Ejector: Auto in EJECT OR Manual Man_Ejector. Interlocks: only out of an open mould, arm home, onto a "
           "C1 with room (Outfeed_Full: the bottle waits in the mould, Blocked)",
           [["Seq_Enable", "Mould_Eject"], ["Mode_Manual", "Man_Ejector", "!Jog_Block"]],
           ["Mould_Open", "Arm_At_Oven", "!Outfeed_Full"], ("coil", "Req_Ejector")),
    B.fb_chain("Outputs 1/2: FB instances Mot_HopperFeed -> HopperFeed (run time), Vlv_Loader -> Loader, Vlv_Arm -> "
               "ArmToMould, Vlv_Gripper -> Gripper (cycles). Out = request AND Enable; Enable = Master_Enable, so an "
               "E-stop switches them off at once. One writer per %QX", FBS[:4]),
    B.fb_chain("Outputs 2/2: FB instances Vlv_Rod -> StretchRod, Vlv_PreBlow -> PreBlowValve, Vlv_HighBlow -> "
               "HighBlowValve (cycles = blows), Vlv_Ejector -> Ejector. ExhaustValve and MouldClose are the plain "
               "coils above (they must not follow Master_Enable)", FBS[4:]),
]
new = sec2 + sec7
B.check(new)
rungs = R[:psafe + 1] + sec2 + R[psafe + 1:] + sec7
out = L.join(B.head, rungs)
if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage7").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage7")
print(f"{len(new)} new rungs ({len(rungs)} total), {len(INTERNALS)} declarations")
