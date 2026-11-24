"""Labeler ladder stage 5: outputs, interlocks, gate / knife pulses, heater, manual mode, jog watchdog.

Runs against the labeler PLC on port 5023. The outputs %QX0.0-0.7 and %QX1.0 are read as coils 0-8.
Every check compares the COMPLETE set of outputs that are ON, so an output that switches on in the
wrong step is caught too. Heartbeat kept alive in a thread, E_Stop_OK TRUE, Reset first, glue
setpoints at their defaults (GlueSetpoint 1700, GlueHyst 50, GlueBand 100: Glue_Ready 1600-1800,
Heat_Demand set below 1650, reset at 1700). ConveyorMotor2 (C2) runs in Stop and Auto, so it is in
almost every expected set.

Heater (Mode Stop: no mode contact in Req_Heater):
    H1  1700 -> C2                       H2  1500 -> C2 + GlueHeater (Heat_Demand)
    H3  1700 again -> C2 (Heat_Demand reset at the setpoint)
    H4  GlueSetpoint 2100, GlueTemp 1999 -> C2 + GlueHeater
    H5  GlueTemp 2000 = GlueMax -> Alm_GlueOverTemp, FAULT in Mode Stop: nothing ON (C2 and heater off);
        back to 170.0 degC + Auto + Reset -> IDLE (the Glue_BelowMax interlock stays as a second layer)
Auto, one bottle (the sensors move the sequencer; Step is written only for 24 and 35):
    A1  IDLE -> C2;  A2  IDLE, Man_Spin held with the bell down -> C2 (manual buttons ignored in Auto)
    A2b IDLE, bottle in the pocket, Bell clicked -> C2 (the bell toggle works in Manual only)
    A3  Start with cold glue -> 5 WARM_UP: C2 + GlueHeater;  A4  1700 -> 10 WAIT_BOTTLE: C2
    G1  bottle at the gate but Roll_Empty -> no gate pulse
    G2  Roll_Empty off -> exactly ONE GateMotor pulse of GatePulse (0.5 s), Bottle_At_Gate held
        (G2-G5 within 2 s of the pulse: a bottle not in the pocket by then is Alm_PocketJam, stage 7)
    G3  Bottle_Entering TRUE after the pulse
    G4  Bottle_At_Gate drops and rises while Bottle_Entering -> no second pulse
    G5  Bottle_In_Pocket -> Bottle_Entering FALSE, Step 20 CLAMP: C2 + ClampValve (no gate: never two bottles)
    A5  Bell_Down -> 22 WRAP: C2 + ClampValve + SpinMotor + LabelFeed + GlueRoller
    A6  bell up in WRAP -> C2 + ClampValve (spin and feed need Bell_Down)
    A7  bell down, glue 150.0 degC -> C2 + ClampValve + SpinMotor + GlueHeater (feed needs Glue_Ready)
    K1  Step 24 PRESS -> exactly ONE Cutter pulse of CutPulse (0.2 s);  K2  then C2 + ClampValve + SpinMotor
    A8  Step 26 UNCLAMP, bell still down -> C2
    A9  bell up, C3 free -> 40 DISCHARGE: C2 + DischargeMotor (C3_Full set right after: Outfeed_Full within
        2 s, so Out_Timer (3 s, runs only while C3 is free) never ends in this block)
    A10 bell down in 40 -> C2 (discharge needs the bell up)
    A11 bell up, Outfeed_Full -> C2 (discharge blocked by C3 full)
    A12 Step 35 WAIT_OUTFEED -> C2;  A13 C3 free again -> 40: C2 + DischargeMotor
    A14 pocket empty -> 10: C2
    E1  glue 150.0 degC -> 5: C2 + GlueHeater;  E2 E_Stop_OK FALSE -> nothing (heater too), Step 0
    E3  E_Stop_OK TRUE again -> C2 + GlueHeater (Auto IDLE)
    F1  Step 900 FAULT, cold glue -> nothing (C2 and heater off in FAULT)
Manual (ModeReq 2, hold-to-run except the bell, interlocks still apply; glue 170.0 degC unless noted).
The bell (Man_Clamp) is a toggle: a click (press + release) latches Man_Bell_Down, the next click
drops it; reset when leaving Manual, without Master_Enable or without a bottle in the pocket.
    M1  no button -> nothing (C2 only with Man_C2 in Manual);  M2  Man_C2 -> C2
    M3  Man_Gate, pocket empty -> GateMotor;  M4  pocket full -> nothing
    M5  Bell clicked, pocket empty -> nothing;  M5b bottle arrives -> still nothing (no surprise drop)
    M6  Bell clicked, pocket full -> ClampValve;  M6b button released 0.5 s -> ClampValve stays
    (M7-M13 with the bell latched, so ClampValve is ON in all of them)
    M7  Man_Spin, bell up -> ClampValve;  M8  bell down -> + SpinMotor
    M9  Man_Feed, bell down -> + SpinMotor + LabelFeed + GlueRoller (the feed turns the table);  M10 bell up -> ClampValve
    M11 Man_Feed, bell down, glue 150.0 degC -> + SpinMotor + GlueHeater (feed needs Glue_Ready)
    M12 Man_Cut, bell up -> ClampValve;  M13 bell down -> + Cutter (held, no pulse in Manual)
    M13b Bell clicked again -> nothing (bell up)
    B1  latched, Mode Auto and back to Manual -> nothing;  B2  latched, E_Stop_OK off and on -> nothing
    B3  latched, pocket empty and full again -> nothing
    B4  latched, Man_Spin held 11 s with the bell down -> Jog_Block, the bell stays (ClampValve only);
        B4b released, Bell clicked -> nothing
    M14 Man_Discharge, bell up, C3 free -> DischargeMotor;  M15 bell down -> nothing
    M16 no button, glue 150.0 degC -> GlueHeater (heater runs in Manual too)
Jog watchdog:
    J1 Man_C2 held 11 s -> Jog_Block TRUE, C2 off;  J2 released -> Jog_Block FALSE;  J3 pressed again -> C2

    uv run python tests/test_labeler_outputs.py      (~60 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_labeler_sequencer import (
    COIL_BELL_DOWN, COIL_BOTTLE_AT_GATE, COIL_BOTTLE_IN_POCKET, COIL_C3_FULL, COIL_COMMLOST,
    COIL_E_STOP_OK, COIL_ROLL_EMPTY, GLUE_COLD, GLUE_SETPOINT, GLUE_WARM, HOST, HR_GLUE_SETPOINT,
    HR_GLUE_TEMP, HR_MODE_REQ, HR_STEP, MODE_AUTO, MODE_STOP, PORT, SETTLE, START, Heartbeat, Plc,
    connect, reset_to_idle,
)

COIL_MAN = {"Man_C2": 8208, "Man_Gate": 8209, "Man_Clamp": 8210, "Man_Spin": 8211,  # %MX2.0-2.6
            "Man_Feed": 8212, "Man_Cut": 8213, "Man_Discharge": 8214}
COIL_BOTTLE_ENTERING = 8217  # %MX3.1
COIL_JOG_BLOCK = 8218  # %MX3.2
COIL_GLUE_OVER_TEMP = 8237  # %MX5.5
OUTPUTS = ["ConveyorMotor2", "GateMotor", "ClampValve", "SpinMotor", "LabelFeed",  # coils 0-8
           "Cutter", "GlueRoller", "GlueHeater", "DischargeMotor"]
C2 = "ConveyorMotor2"
WRAP_SET = {C2, "ClampValve", "SpinMotor", "LabelFeed", "GlueRoller"}

MODE_MANUAL = 2
GLUE_MAX = 2000  # constant GlueMax in the program
GATE_PULSE, CUT_PULSE = 0.5, 0.2  # s: constants GatePulse, CutPulse
PULSE_TOL = 0.1  # s: 20 ms scan + Modbus polling


def main() -> int:
    claim_plant("test_labeler_outputs", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_outputs")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> None:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    def outputs_on() -> set[str]:
        result = client.read_coils(0, count=len(OUTPUTS))
        if result.isError():
            raise SystemExit(f"Read outputs failed: {result}")
        return {name for name, bit in zip(OUTPUTS, result.bits) if bit}

    def show(s: set[str]) -> str:
        return "{" + ", ".join(sorted(s)) + "}" if s else "nothing"

    def expect(name: str, expected: set[str], what: str) -> None:
        time.sleep(SETTLE)
        got = outputs_on()
        detail = f"{what} -> {show(got)} (Step {plc.step()})"
        report(name, got == expected, detail + ("" if got == expected else f", expected {show(expected)}"))

    def pulses(output: str, seconds: float) -> list[float]:
        """Poll one output for `seconds`; returns the length of every ON period (an open one counts to the end)."""
        index = OUTPUTS.index(output)
        lengths: list[float] = []
        on_since: float | None = None
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            now = time.monotonic()
            on = bool(client.read_coils(index, count=1).bits[0])
            if on and on_since is None:
                on_since = now
            elif not on and on_since is not None:
                lengths.append(now - on_since)
                on_since = None
            time.sleep(0.005)
        if on_since is not None:
            lengths.append(time.monotonic() - on_since)
        return lengths

    def one_pulse(name: str, output: str, length: float, lengths: list[float], what: str) -> None:
        ok = len(lengths) == 1 and abs(lengths[0] - length) <= PULSE_TOL
        shown = ", ".join(f"{x:.2f} s" for x in lengths) or "none"
        report(name, ok, f"{what} -> {output} pulses: {shown} (expected one of {length:g} s)")

    def glue(value: int) -> None:
        plc.write(HR_GLUE_TEMP, value)

    def press(button: str, value: bool = True) -> None:
        plc.coil(COIL_MAN[button], value)
        if not value:
            time.sleep(0.1)  # a few scans with no button: Jog_TON restarts

    def click(button: str) -> None:
        """One click on an HMI button: press, hold 0.2 s (many 20 ms scans), release."""
        plc.coil(COIL_MAN[button], True)
        time.sleep(0.2)
        plc.coil(COIL_MAN[button], False)

    def release_all() -> None:
        for button in COIL_MAN:
            plc.coil(COIL_MAN[button], False)
        time.sleep(0.1)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        release_all()
        plc.clear_sensors()
        plc.write(HR_GLUE_SETPOINT, GLUE_SETPOINT)
        glue(GLUE_WARM)
        plc.coil(COIL_E_STOP_OK, True)
        # the sensor writes must reach the PLC (a debugger force would block them); the pocket sensor
        # also resets a Bottle_Entering left over from an earlier run
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        time.sleep(SETTLE)
        if not plc.coil(COIL_BOTTLE_IN_POCKET):
            raise SystemExit("Bottle_In_Pocket does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        heartbeat.start()
        time.sleep(1.5)  # 2+ heartbeat toggles before Reset, else Alm_CommLost stays latched
        if not plc.wait_outfeed(False):
            raise SystemExit("Outfeed_Full stays TRUE with C3_Full FALSE: check the stage 3 rungs")
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_STOP)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE after Reset: Master_Enable is off, all outputs stay off")

        # ---------- heater, Mode Stop ----------
        expect("H1", {C2}, "Stop, glue 170.0 degC")
        glue(GLUE_COLD)
        expect("H2", {C2, "GlueHeater"}, "Stop, glue 150.0 degC")
        glue(GLUE_WARM)
        expect("H3", {C2}, "Stop, glue 170.0 degC again")
        plc.write(HR_GLUE_SETPOINT, 2100)
        glue(GLUE_MAX - 1)
        expect("H4", {C2, "GlueHeater"}, "Stop, GlueSetpoint 210.0, glue 199.9 degC")
        glue(GLUE_MAX)
        time.sleep(SETTLE)
        over = plc.coil(COIL_GLUE_OVER_TEMP)
        expect("H5", set(), f"Stop, glue 200.0 degC = GlueMax, Alm_GlueOverTemp {over} (FAULT)")
        if not over or plc.step() != 900:
            report("H5b", False, f"Alm_GlueOverTemp {over}, Step {plc.step()} (expected True, 900)")
        plc.write(HR_GLUE_SETPOINT, GLUE_SETPOINT)
        glue(GLUE_WARM)
        time.sleep(SETTLE)
        reset_to_idle(plc)  # cause gone: Auto + Reset clears the alarm and leaves FAULT
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)

        # ---------- Auto, one bottle ----------
        plc.write(HR_MODE_REQ, MODE_AUTO)
        expect("A1", {C2}, "Auto IDLE")
        plc.coil(COIL_BELL_DOWN, True)
        press("Man_Spin")
        expect("A2", {C2}, "Auto IDLE, Man_Spin held, bell down")
        press("Man_Spin", False)
        plc.coil(COIL_BELL_DOWN, False)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        click("Man_Clamp")
        expect("A2b", {C2}, "Auto IDLE, bottle in the pocket, Bell clicked")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        glue(GLUE_COLD)
        plc.cmd(START)
        plc.wait_step(5, 1)
        expect("A3", {C2, "GlueHeater"}, "Start, glue 150.0 degC (WARM_UP)")
        glue(GLUE_WARM)
        plc.wait_step(10, 1)
        expect("A4", {C2}, "glue 170.0 degC (WAIT_BOTTLE)")

        # gate: one pulse per bottle
        plc.coil(COIL_ROLL_EMPTY, True)
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        one = pulses("GateMotor", 0.6)
        report("G1", not one, f"bottle at the gate, Roll_Empty TRUE -> GateMotor pulses {len(one)} (expected 0)")
        plc.coil(COIL_ROLL_EMPTY, False)
        one_pulse("G2", "GateMotor", GATE_PULSE, pulses("GateMotor", 0.8), "Roll_Empty off, Bottle_At_Gate held")
        entering = plc.coil(COIL_BOTTLE_ENTERING)
        report("G3", entering, f"after the gate pulse -> Bottle_Entering {entering} (expected True)")
        plc.coil(COIL_BOTTLE_AT_GATE, False)
        time.sleep(0.2)
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        again = pulses("GateMotor", 0.5)
        report("G4", not again, f"Bottle_At_Gate off and on while Bottle_Entering -> GateMotor pulses {len(again)} (expected 0)")
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        plc.wait_step(20, 1)
        entering = plc.coil(COIL_BOTTLE_ENTERING)
        if entering:
            report("G5b", False, "Bottle_In_Pocket TRUE but Bottle_Entering still TRUE")
        expect("G5", {C2, "ClampValve"}, "Bottle_In_Pocket, next bottle still at the gate (CLAMP)")

        # clamp, wrap, interlocks
        plc.coil(COIL_BELL_DOWN, True)
        plc.wait_step(22, 1)
        expect("A5", WRAP_SET, "Bell_Down (WRAP)")
        plc.coil(COIL_BELL_DOWN, False)
        expect("A6", {C2, "ClampValve"}, "bell up in WRAP")
        plc.coil(COIL_BELL_DOWN, True)
        glue(GLUE_COLD)
        expect("A7", {C2, "ClampValve", "SpinMotor", "GlueHeater"}, "bell down, glue 150.0 degC in WRAP")
        glue(GLUE_WARM)
        time.sleep(SETTLE)

        # knife: one pulse when PRESS starts
        plc.write(HR_STEP, 24)
        one_pulse("K1", "Cutter", CUT_PULSE, pulses("Cutter", 0.8), "Step 24 PRESS written")
        expect("K2", {C2, "ClampValve", "SpinMotor"}, "PRESS after the knife pulse")

        # unclamp, discharge, C3 full
        plc.write(HR_STEP, 26)
        expect("A8", {C2}, "Step 26 UNCLAMP, bell still down")
        plc.coil(COIL_BELL_DOWN, False)
        plc.wait_step(40, 1)
        expect("A9", {C2, "DischargeMotor"}, "bell up, C3 free (DISCHARGE)")
        plc.coil(COIL_C3_FULL, True)  # Outfeed_Full after 2 s: Out_Timer stops before its 3 s
        plc.coil(COIL_BELL_DOWN, True)
        expect("A10", {C2}, "bell down in DISCHARGE")
        plc.coil(COIL_BELL_DOWN, False)
        if not plc.wait_outfeed(True):
            report("A11", False, "Outfeed_Full not TRUE within 5 s of C3_Full")
        else:
            expect("A11", {C2}, "bell up, Outfeed_Full in DISCHARGE")
            plc.write(HR_STEP, 35)
            expect("A12", {C2}, "Step 35 WAIT_OUTFEED")
        plc.coil(COIL_C3_FULL, False)
        plc.wait_outfeed(False)
        plc.wait_step(40, 1)
        expect("A13", {C2, "DischargeMotor"}, "C3 free again")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_BOTTLE_AT_GATE, False)
        plc.wait_step(10, 1)
        expect("A14", {C2}, "pocket empty (WAIT_BOTTLE)")

        # E-stop and FAULT
        glue(GLUE_COLD)
        plc.wait_step(5, 1)
        expect("E1", {C2, "GlueHeater"}, "glue 150.0 degC (WARM_UP)")
        plc.coil(COIL_E_STOP_OK, False)
        expect("E2", set(), "E_Stop_OK FALSE")
        plc.coil(COIL_E_STOP_OK, True)
        expect("E3", {C2, "GlueHeater"}, "E_Stop_OK TRUE again (Auto IDLE)")
        plc.write(HR_STEP, 900)
        expect("F1", set(), "Step 900 FAULT, glue 150.0 degC")
        glue(GLUE_WARM)
        reset_to_idle(plc)

        # ---------- Manual ----------
        plc.write(HR_MODE_REQ, MODE_MANUAL)
        plc.clear_sensors()
        expect("M1", set(), "Manual, no button")
        press("Man_C2")
        expect("M2", {C2}, "Manual Man_C2")
        press("Man_C2", False)

        press("Man_Gate")
        expect("M3", {"GateMotor"}, "Manual Man_Gate, pocket empty")
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        expect("M4", set(), "Manual Man_Gate, pocket full")
        press("Man_Gate", False)

        # bell toggle: a click latches the bell, the next click lifts it
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        click("Man_Clamp")
        expect("M5", set(), "Manual Bell clicked, pocket empty")
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        expect("M5b", set(), "Manual, a bottle arrives after the click")
        click("Man_Clamp")
        expect("M6", {"ClampValve"}, "Manual Bell clicked, pocket full")
        time.sleep(0.5)
        expect("M6b", {"ClampValve"}, "Manual Bell released 0.5 s ago")
        B = {"ClampValve"}  # the bell stays latched in M7-M13

        press("Man_Spin")
        expect("M7", B, "Manual Man_Spin, bell up")
        plc.coil(COIL_BELL_DOWN, True)
        expect("M8", B | {"SpinMotor"}, "Manual Man_Spin, bell down")
        press("Man_Spin", False)

        press("Man_Feed")
        expect("M9", B | {"SpinMotor", "LabelFeed", "GlueRoller"}, "Manual Man_Feed, bell down")
        plc.coil(COIL_BELL_DOWN, False)
        expect("M10", B, "Manual Man_Feed, bell up")
        plc.coil(COIL_BELL_DOWN, True)
        glue(GLUE_COLD)
        expect("M11", B | {"SpinMotor", "GlueHeater"}, "Manual Man_Feed, bell down, glue 150.0 degC")
        glue(GLUE_WARM)
        press("Man_Feed", False)
        plc.coil(COIL_BELL_DOWN, False)

        press("Man_Cut")
        expect("M12", B, "Manual Man_Cut, bell up")
        plc.coil(COIL_BELL_DOWN, True)
        expect("M13", B | {"Cutter"}, "Manual Man_Cut, bell down")
        press("Man_Cut", False)
        plc.coil(COIL_BELL_DOWN, False)
        click("Man_Clamp")
        expect("M13b", set(), "Manual Bell clicked again")

        # the latch drops by itself: leaving Manual, E-stop, no bottle; a held button (Jog_Block) keeps it
        click("Man_Clamp")
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_MANUAL)
        expect("B1", set(), "Bell latched, Mode Auto and back to Manual")
        click("Man_Clamp")
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, True)
        expect("B2", set(), "Bell latched, E_Stop_OK off and on again")
        click("Man_Clamp")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        expect("B3", set(), "Bell latched, pocket empty and full again")
        click("Man_Clamp")
        plc.coil(COIL_BELL_DOWN, True)
        press("Man_Spin")
        time.sleep(11)
        blocked = plc.coil(COIL_JOG_BLOCK)
        expect("B4", {"ClampValve"}, f"Bell latched, Man_Spin held 11 s, Jog_Block {blocked}")
        if not blocked:
            report("B4j", False, "Jog_Block FALSE after 11 s (expected True)")
        press("Man_Spin", False)
        plc.coil(COIL_BELL_DOWN, False)
        click("Man_Clamp")
        expect("B4b", set(), "Man_Spin released, Bell clicked")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)

        press("Man_Discharge")
        expect("M14", {"DischargeMotor"}, "Manual Man_Discharge, bell up, C3 free")
        plc.coil(COIL_BELL_DOWN, True)
        expect("M15", set(), "Manual Man_Discharge, bell down")
        press("Man_Discharge", False)
        plc.coil(COIL_BELL_DOWN, False)

        glue(GLUE_COLD)
        expect("M16", {"GlueHeater"}, "Manual, no button, glue 150.0 degC")
        glue(GLUE_WARM)
        time.sleep(SETTLE)

        # ---------- jog watchdog ----------
        press("Man_C2")
        time.sleep(11)
        blocked = plc.coil(COIL_JOG_BLOCK)
        report("J1", blocked, f"Man_C2 held 11 s -> Jog_Block {blocked} (expected True)")
        expect("J1b", set(), "Man_C2 still held, Jog_Block")
        press("Man_C2", False)
        time.sleep(SETTLE)
        blocked = plc.coil(COIL_JOG_BLOCK)
        report("J2", not blocked, f"all buttons released -> Jog_Block {blocked} (expected False)")
        press("Man_C2")
        expect("J3", {C2}, "Man_C2 pressed again")
        press("Man_C2", False)
    finally:
        # end state for the next script: buttons and sensors FALSE, E_Stop_OK TRUE, defaults, Step 0, Mode Stop
        release_all()
        plc.clear_sensors()
        plc.coil(COIL_E_STOP_OK, True)
        plc.write(HR_GLUE_SETPOINT, GLUE_SETPOINT)
        plc.write(HR_GLUE_TEMP, 0)
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (buttons and sensors FALSE, Step 0, Mode Stop, GlueTemp 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
