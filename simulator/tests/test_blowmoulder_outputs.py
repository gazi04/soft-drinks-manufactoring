"""Blow moulder ladder stage 7: outputs and interlocks (exhaust, mould seal-in + toggle, requests, FBs).

Runs against the blow moulder PLC on port 5025. The script plays the machine by writing the sensors and
reads all ten digital outputs (%QX0.0-1.1 = coils 0-9) at once; every check compares the COMPLETE set of
outputs that are ON. Heartbeat thread, E_Stop_OK, setpoint defaults as in test_blowmoulder_oven_seq.py.

Safe states:
    Q1  Mode Stop, IDLE -> only ExhaustValve (normally open)      Q2  Auto, IDLE -> only ExhaustValve
Oven station:
    Q3  FEED, no preform at the loader -> HopperFeed              Q4  LOAD -> Loader
    Q5  HEAT -> nothing but the exhaust (the lamps are analog, stage 6)
Mould station, one bottle (the oven stays HOT: its preform is not taken out):
    Q6  GRIP -> Gripper            Q7  SWING -> Gripper, ArmToMould      Q8  RELEASE -> ArmToMould
    Q9  ARM_BACK -> exhaust only   Q10 CLOSE -> MouldClose
    Q11 STRETCH -> MouldClose, StretchRod, PreBlowValve (exhaust SHUT)
    Q12 BLOW -> MouldClose, StretchRod, HighBlowValve     Q13 HOLD -> the same
    Q14 HOLD at 40.0 bar (MaxPressure) -> HighBlowValve off
    Q15 EXHAUST -> MouldClose, StretchRod, ExhaustValve   Q16 ROD_UP -> MouldClose, ExhaustValve
    Q17 OPEN with 0.5 bar left -> MouldClose held        Q18 OPEN, 0.4 bar, rod down -> still held
    Q19 rod up -> the mould opens (MouldClose off)
    Q20 EJECT -> Ejector           Q21 C1 full (Outfeed_Full) -> ejector held off   Q22 C1 free -> Ejector again
The mould never moves in a safe state:
    Q23 Mode Stop in HOLD -> MouldClose held, exhaust open, all else off
    Q24 Auto, both IDLE, 0 bar, rod up (opening would be safe) -> still held: IDLE never moves the mould
    Q25 E-stop in HOLD -> MouldClose held, exhaust open
    Q26 E-stop while CLOSING (not yet Mould_Closed) -> MouldClose drops (back to open)
Manual:
    Q27 Manual entered with the mould closed -> MouldClose stays ON (toggle loaded)
    Q28 Man_Rod, mould closed -> StretchRod              Q29 click Mould -> opens (MouldClose off)
    Q30 Man_Rod, mould open -> nothing (interlock)       Q31 Man_Arm, mould open, rod up -> ArmToMould
    Q32 Man_Arm, rod down -> nothing                     Q33 Man_Ejector -> Ejector
    Q34 Man_HopperFeed -> HopperFeed     Q35 Man_Loader, preform in the pocket -> nothing   Q36 pocket empty -> Loader
    Q37 Man_Gripper -> Gripper; with Man_Loader as well -> no Loader (never against the closed gripper)
    Q38 click Mould -> closes            Q39 E-stop + release, mould closed -> stays closed, no motion
    Q40 click, mould open; E-stop + release -> stays open
    Q41 Man_HopperFeed held 11 s -> Jog_Block, HopperFeed off, the mould toggle untouched; released -> Jog_Block off

    uv run python tests/test_blowmoulder_outputs.py      (~70 s)

Each step check waits 0.1 s after the step changed: the outputs follow one scan later (the transition writes
the step in section 6, section 7 still decodes the old one in that scan).
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_blowmoulder_oven_seq import (COIL_BOTTLE_IN_MOULD, COIL_C1_FULL, COIL_COMMLOST, COIL_E_STOP_OK,
                                       COIL_MOULD_CLOSED, COIL_MOULD_OPEN, COIL_PREFORM_AT_LOADER, COIL_PREFORM_IN_OVEN,
                                       HOST, HR_MODE_REQ, HR_MOULD_STEP, HR_OVEN_BAND, HR_OVEN_SETPOINT, HR_OVEN_TEMP,
                                       HR_STEP, MODE_AUTO, MODE_MANUAL, MODE_STOP, OVEN_BAND, OVEN_COLD, OVEN_HOT,
                                       OVEN_SETPOINT, PORT, RESET, SETTLE, START, Heartbeat, Plc, check, connect)

HR_RAW = 1037  # %MW13
HR_BLOW_SETPOINT = 1058  # %MW34
COIL_ARM_AT_OVEN, COIL_ARM_AT_MOULD, COIL_ROD_UP, COIL_ROD_DOWN = 8194, 8195, 8198, 8199
COIL_MAN = {"feed": 8208, "loader": 8209, "arm": 8210, "gripper": 8211, "mould": 8212, "rod": 8213, "ejector": 8214}
COIL_JOG_BLOCK = 8218  # %MX3.2
OUTPUTS = ["HopperFeed", "Loader", "ArmToMould", "Gripper", "MouldClose", "StretchRod", "PreBlowValve",
           "HighBlowValve", "ExhaustValve", "Ejector"]  # coils 0-9 (%QX0.0-0.7, %QX1.0-1.1)
RAW_35_BAR, RAW_40_BAR, RAW_05_BAR, RAW_04_BAR = 24192, 27648, 400, 345
EX = "ExhaustValve"
OUTPUT_LAG = 0.1  # s: wait after a step change before reading the outputs (one scan lag, 20 ms task)


def main() -> int:
    claim_plant("test_blowmoulder_outputs", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_outputs")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)
    names = {"loader": COIL_PREFORM_AT_LOADER, "in_oven": COIL_PREFORM_IN_OVEN, "arm_oven": COIL_ARM_AT_OVEN,
             "arm_mould": COIL_ARM_AT_MOULD, "closed": COIL_MOULD_CLOSED, "open": COIL_MOULD_OPEN, "rod_up": COIL_ROD_UP,
             "rod_down": COIL_ROD_DOWN, "bottle": COIL_BOTTLE_IN_MOULD, "c1_full": COIL_C1_FULL}

    def outputs() -> set[str]:
        result = client.read_coils(0, count=len(OUTPUTS))
        if result.isError():
            raise SystemExit(f"Read outputs failed: {result}")
        return {n for n, bit in zip(OUTPUTS, result.bits) if bit}

    def expect(name: str, expected: set[str], what: str, settle: float = SETTLE) -> None:
        time.sleep(settle)
        got = outputs()
        results.append(check(name, got == expected, f"{what} -> ON {sorted(got)} (expected {sorted(expected)})"))

    def sensors(**values: bool) -> None:
        """Written in the given order: put the one that blocks a transition first."""
        for key, value in values.items():
            plc.coil(names[key], value)

    def button(key: str, value: bool) -> None:
        plc.coil(COIL_MAN[key], value)

    def click() -> None:
        button("mould", True)
        time.sleep(SETTLE)
        button("mould", False)

    def wait_mould(expected: int, timeout: float = 2.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if plc.steps()[1] == expected:
                return True
            time.sleep(0.02)
        return False

    def at_mould(name: str, step: int, expected: set[str], what: str, timeout: float = 2.0) -> None:
        reached = wait_mould(step, timeout)
        # the outputs follow one scan after the step: the transition (section 6) writes the new step, but
        # section 7 still uses the step bits decoded at the top of that scan. 0.1 s = 5 scans, well inside
        # GRIP's 0.3 s
        time.sleep(OUTPUT_LAG)
        got = outputs()
        results.append(check(name, reached and got == expected,
                             f"{what}: MouldStep {plc.steps()[1]} -> ON {sorted(got)} (expected {step}, {sorted(expected)})"))

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for key in COIL_MAN:
            button(key, False)
        for address, value in ((HR_OVEN_TEMP, OVEN_COLD), (HR_OVEN_SETPOINT, OVEN_SETPOINT), (HR_OVEN_BAND, OVEN_BAND),
                               (HR_BLOW_SETPOINT, 350), (HR_RAW, 0)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        time.sleep(3.5)  # Outfeed_Full and Jog_Block left by a broken run are released
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the outputs cannot run")
        sensors(open=True, arm_oven=True, rod_up=True)
        expect("Q1", {EX}, "Mode Stop, IDLE, mould open")
        plc.mode(MODE_AUTO)
        expect("Q2", {EX}, "Auto, IDLE (no Start)")

        # ---------- oven station ----------
        plc.cmd(START)
        expect("Q3", {"HopperFeed", EX}, f"Start -> Step {plc.steps()[0]} FEED, no preform at the loader", 0)
        sensors(loader=True)
        plc.wait_step(15, 1)
        expect("Q4", {"Loader", EX}, "preform at the loader -> LOAD", 0.1)
        sensors(loader=False, in_oven=True)
        plc.wait_step(20, 1)
        expect("Q5", {EX}, "preform in the pocket -> HEAT", 0.1)

        # ---------- mould station, one bottle ----------
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        at_mould("Q6", 20, {"Gripper", EX}, "oven HOT -> GRIP", 3)
        at_mould("Q7", 22, {"Gripper", "ArmToMould", EX}, "GripTime over -> SWING")
        sensors(arm_oven=False, arm_mould=True)
        at_mould("Q8", 24, {"ArmToMould", EX}, "arm at the mould -> RELEASE")
        sensors(bottle=True)
        at_mould("Q9", 26, {EX}, "preform in the mould -> ARM_BACK")
        sensors(arm_mould=False, arm_oven=True)
        at_mould("Q10", 30, {"MouldClose", EX}, "arm home -> CLOSE")
        sensors(open=False, closed=True)
        at_mould("Q11", 40, {"MouldClose", "StretchRod", "PreBlowValve"}, "mould closed -> STRETCH")
        sensors(rod_up=False, rod_down=True)
        at_mould("Q12", 50, {"MouldClose", "StretchRod", "HighBlowValve"}, "rod down -> BLOW")
        plc.write(HR_RAW, RAW_35_BAR)
        at_mould("Q13", 52, {"MouldClose", "StretchRod", "HighBlowValve"}, "35.0 bar -> HOLD")
        plc.write(HR_RAW, RAW_40_BAR)
        expect("Q14", {"MouldClose", "StretchRod"}, "HOLD at 40.0 bar (MaxPressure)", 0.15)
        plc.write(HR_RAW, RAW_35_BAR)
        at_mould("Q15", 60, {"MouldClose", "StretchRod", EX}, "HoldTime over -> EXHAUST")
        plc.write(HR_RAW, RAW_04_BAR)
        at_mould("Q16", 62, {"MouldClose", EX}, "0.4 bar -> ROD_UP")
        plc.write(HR_RAW, RAW_05_BAR)  # pressure back above the limit before the rod is up
        sensors(rod_down=False, rod_up=True)
        at_mould("Q17", 70, {"MouldClose", EX}, "rod up -> OPEN, 0.5 bar left")
        sensors(rod_up=False)
        plc.write(HR_RAW, RAW_04_BAR)
        expect("Q18", {"MouldClose", EX}, "OPEN, 0.4 bar, rod down")
        sensors(rod_up=True)
        expect("Q19", {EX}, "OPEN, 0.4 bar, rod up")
        sensors(closed=False, open=True)
        at_mould("Q20", 80, {"Ejector", EX}, "mould open -> EJECT")
        sensors(c1_full=True)
        expect("Q21", {EX}, "C1 full 2.5 s (Outfeed_Full)", 2.5)
        sensors(c1_full=False)
        expect("Q22", {"Ejector", EX}, "C1 free 3.5 s", 3.5)

        # ---------- the mould never moves in a safe state ----------
        plc.mode(MODE_STOP)
        sensors(open=False, closed=True, rod_up=False, rod_down=True)
        plc.write(HR_RAW, RAW_35_BAR)
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 52)
        time.sleep(SETTLE)
        plc.mode(MODE_STOP)
        expect("Q23", {"MouldClose", EX}, "HOLD, Mode Stop (35 bar inside)")
        plc.write(HR_RAW, 0)  # opening would now be safe: only NOT Mould_Idle keeps it closed
        sensors(rod_down=False, rod_up=True)
        plc.mode(MODE_AUTO)
        expect("Q24", {"MouldClose", EX}, "Auto, both IDLE, 0 bar, rod up")
        plc.write(HR_RAW, RAW_35_BAR)
        sensors(rod_up=False, rod_down=True)
        plc.write(HR_MOULD_STEP, 52)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, False)
        expect("Q25", {"MouldClose", EX}, "HOLD, E-stop")
        plc.coil(COIL_E_STOP_OK, True)
        plc.mode(MODE_STOP)
        sensors(closed=False)  # the mould is moving: neither closed nor open
        plc.write(HR_RAW, 0)
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 30)
        time.sleep(SETTLE)
        closing = outputs()
        plc.coil(COIL_E_STOP_OK, False)
        expect("Q26", {EX}, f"CLOSE, not yet closed ({sorted(closing)}), E-stop")
        results[-1] = results[-1] and "MouldClose" in closing
        plc.coil(COIL_E_STOP_OK, True)

        # ---------- Manual ----------
        plc.mode(MODE_STOP)
        sensors(rod_down=False, rod_up=True, in_oven=True)
        sensors(closed=True)
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 30)  # close the mould once in Auto, then hand over to Manual
        time.sleep(SETTLE)
        plc.mode(MODE_MANUAL)
        expect("Q27", {"MouldClose", EX}, "Manual entered with the mould closed")
        button("rod", True)
        expect("Q28", {"MouldClose", "StretchRod", EX}, "Man_Rod, mould closed")
        button("rod", False)
        click()
        expect("Q29", {EX}, "click Mould (0 bar, rod up)")
        sensors(closed=False, open=True)
        button("rod", True)
        expect("Q30", {EX}, "Man_Rod, mould open")
        button("rod", False)
        button("arm", True)
        expect("Q31", {"ArmToMould", EX}, "Man_Arm, mould open, rod up")
        sensors(rod_up=False)
        expect("Q32", {EX}, "Man_Arm, rod down")
        sensors(rod_up=True)
        button("arm", False)
        button("ejector", True)
        expect("Q33", {"Ejector", EX}, "Man_Ejector, mould open, arm home")
        button("ejector", False)
        button("feed", True)
        expect("Q34", {"HopperFeed", EX}, "Man_HopperFeed")
        button("feed", False)
        button("loader", True)
        expect("Q35", {EX}, "Man_Loader, preform in the pocket")
        sensors(in_oven=False)
        expect("Q36", {"Loader", EX}, "Man_Loader, pocket empty")
        button("gripper", True)
        expect("Q37", {"Gripper", EX}, "Man_Gripper + Man_Loader")
        button("gripper", False)
        button("loader", False)
        click()
        expect("Q38", {"MouldClose", EX}, "click Mould (arm home)")
        sensors(open=False, closed=True)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        during = outputs()
        plc.coil(COIL_E_STOP_OK, True)
        expect("Q39", {"MouldClose", EX}, f"E-stop ({sorted(during)}) and release, mould closed")
        results[-1] = results[-1] and during == {"MouldClose", EX}
        click()
        time.sleep(SETTLE)
        sensors(closed=False, open=True)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, True)
        expect("Q40", {EX}, "click (open), E-stop and release")
        click()
        time.sleep(SETTLE)
        sensors(open=False, closed=True)
        button("feed", True)
        time.sleep(11)
        blocked, held = plc.coil(COIL_JOG_BLOCK), outputs()
        button("feed", False)
        time.sleep(SETTLE)
        released = plc.coil(COIL_JOG_BLOCK)
        results.append(check("Q41", blocked and held == {"MouldClose", EX} and not released,
                             f"Man_HopperFeed held 11 s -> Jog_Block {blocked}, ON {sorted(held)}; released -> Jog_Block "
                             f"{released} (expected True, ['ExhaustValve', 'MouldClose'], False)"))
    finally:
        for key in COIL_MAN:
            button(key, False)
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        for address in (HR_STEP, HR_MOULD_STEP):
            plc.write(address, 0)
        plc.clear_sensors()
        plc.write(HR_OVEN_TEMP, 0)
        plc.write(HR_RAW, 0)
        plc.coil(COIL_E_STOP_OK, e_stop_before)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, both steps 0, buttons and sensors FALSE, OvenTemp and "
          "raw 0, E_Stop_OK as before). MouldClose may stay ON: a closed mould holds itself in Mode Stop")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
