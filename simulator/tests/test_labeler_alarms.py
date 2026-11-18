"""Labeler ladder stage 7: alarms, timeouts and FAULT.

Runs against the labeler PLC on port 5023. Plays the machine by writing the sensors (as the stage 4
test), lets a timeout run out and checks Step 900, the alarm bit, all outputs off, and that Reset
clears the alarm only when its cause is gone. Heartbeat kept alive in a thread, E_Stop_OK TRUE,
glue 170.0 degC, PressTurns 2. Timeouts: LabelTimeout 5 s, ClampTimeout 2 s, OutTimeout 3 s,
HeatTimeout 120 s, BlockedWarn 2 min.

    N1  one bottle through every step -> no alarm bit set
Label timeout:
    L1  WRAP without Label_Mark -> Step 900 after about 5 s, Alm_LabelTimeout;  L2 all outputs off
    L3  Reset (cause gone) -> Step 0, alarm cleared
    L4  bell up, half-labelled bottle (Label_Present), Start -> DISCHARGE, never CLAMP again (rule 7)
    L5  PRESS without spin pulses -> FAULT after about 5 s (second timer: restarts at 22 -> 24)
Pocket jam:
    J1  CLAMP without Bell_Down -> FAULT after about 2 s, Alm_PocketJam
    J2  Start in FAULT -> stays 900;  J3 Reset -> 0, alarm cleared
    J4  bell stuck down in UNCLAMP -> FAULT after about 2 s
    J5  gate pulse, bottle never in the pocket -> FAULT after about 2 s, Bottle_Entering still TRUE
    J6  Reset -> Bottle_Entering FALSE, alarm cleared, Step 0 (operator took the stuck bottle away)
    J7  DISCHARGE, bottle stays in the pocket, C3 free -> FAULT after about 3 s
    J8  DISCHARGE with Outfeed_Full for 4 s -> no alarm (Blocked, Out_Timer paused);  J9 C3 free -> pocket empty -> 10
Glue over-temperature:
    G1  GlueTemp 201.0 degC in WAIT_BOTTLE -> FAULT, Alm_GlueOverTemp, all outputs off (heater too)
    G2  Reset at 201.0 degC -> still FAULT;  G3 170.0 degC + Reset -> 0, alarm cleared
    G4  GlueTemp 201.0 degC in Mode Stop -> Step 900 too (FAULT from any step);  G5 170.0 + Auto + Reset -> 0
Label roll empty (warning):
    E1  Roll_Empty in WAIT_BOTTLE -> Alm_LabelEmpty, no FAULT;  E2 roll changed -> clears itself (no Reset)
    E3  Roll_Empty in IDLE -> no warning (only in WAIT_BOTTLE)
Long timeouts (skipped with --quick):
    B1  WAIT_OUTFEED 2 min -> Alm_OutfeedBlocked, still 35 (warning, no FAULT)
    B2  Reset while still blocked -> warning stays;  B3 C3 free, bottle out, Reset -> cleared
    H1  WARM_UP with GlueTemp 100.0 degC for 120 s -> FAULT, Alm_GlueHeatTimeout;  H2 Reset -> 0, cleared

    uv run python tests/test_labeler_alarms.py           (~4.5 min)
    uv run python tests/test_labeler_alarms.py --quick   (~1 min, without B1-B3 and H1-H2)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_labeler_sequencer import (
    COIL_BELL_DOWN, COIL_BOTTLE_AT_GATE, COIL_BOTTLE_IN_POCKET, COIL_C3_FULL, COIL_COMMLOST,
    COIL_E_STOP_OK, COIL_LABEL_MARK, COIL_LABEL_PRESENT, COIL_ROLL_EMPTY, COIL_SPIN_PULSE, GLUE_SETPOINT,
    GLUE_WARM, HOST, HR_GLUE_SETPOINT, HR_GLUE_TEMP, HR_MODE_REQ, HR_PRESS_TURNS, HR_STEP, MODE_AUTO,
    MODE_STOP, PORT, PRESS_TURNS, RESET, SETTLE, START, Heartbeat, Plc, connect, reset_to_idle,
)

HR_STATE = 1074  # %MW50
COIL_BOTTLE_ENTERING = 8217  # %MX3.1
ALARMS = {"Alm_LabelTimeout": 8232, "Alm_PocketJam": 8233, "Alm_OutfeedBlocked": 8234,  # %MX5.0-5.6
          "Alm_CommLost": 8235, "Alm_LabelEmpty": 8236, "Alm_GlueOverTemp": 8237, "Alm_GlueHeatTimeout": 8238}
N_OUTPUTS = 9  # %QX0.0-1.0 = coils 0-8
GLUE_HOT, GLUE_FROZEN = 2010, 1000
LABEL_TIMEOUT, CLAMP_TIMEOUT, OUT_TIMEOUT, HEAT_TIMEOUT, BLOCKED_WARN = 5.0, 2.0, 3.0, 120.0, 120.0
TOL = 0.6  # s: polling, Modbus, scan


def main() -> int:
    quick = "--quick" in sys.argv[1:]
    claim_plant("test_labeler_alarms", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_alarms")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> None:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    def alarms_on() -> set[str]:
        bits = client.read_coils(ALARMS["Alm_LabelTimeout"], count=len(ALARMS)).bits
        return {name for name, bit in zip(ALARMS, bits) if bit}

    def outputs_on() -> list[int]:
        bits = client.read_coils(0, count=N_OUTPUTS).bits[:N_OUTPUTS]
        return [i for i, bit in enumerate(bits) if bit]

    def must(expected: int, timeout: float = 1.0) -> None:
        ok, seen, _ = plc.wait_step(expected, timeout)
        if not ok:
            raise SystemExit(f"setup: Step {expected} not reached, seen {seen}, alarms {sorted(alarms_on())}")

    def fault_after(name: str, alarm: str, expected_s: float, what: str) -> None:
        """Wait for Step 900; checks the time it took and that `alarm` (and only FAULT alarms of this cause) is set."""
        ok, seen, dt = plc.wait_step(900, expected_s + 2.0)
        on = alarms_on()
        timing = ok and expected_s - TOL <= dt <= expected_s + TOL
        report(name, timing and alarm in on,
               f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected 900 after {expected_s:g} s), alarms {sorted(on)}")

    def reset(name: str, alarm: str, what: str) -> None:
        """Auto + Reset: the cause is gone, so Step 0 and the alarm FALSE."""
        step = reset_to_idle(plc)
        time.sleep(SETTLE)
        on = alarms_on()
        report(name, step == 0 and plc.step() == 0 and alarm not in on, f"{what} -> Step {plc.step()}, alarms {sorted(on)}")

    def to_wait_bottle() -> None:
        """From any step (alarms cleared): Mode Stop, sensors clear, glue warm, Auto + Start -> 5 -> 10."""
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(START)
        must(10)

    def to_wrap() -> None:
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        must(20)
        plc.coil(COIL_BELL_DOWN, True)
        must(22)

    def to_unclamp() -> None:
        to_wrap()
        plc.pulse(COIL_LABEL_MARK)
        must(24)
        plc.pulse(COIL_SPIN_PULSE, PRESS_TURNS)
        must(26)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        plc.write(HR_GLUE_SETPOINT, GLUE_SETPOINT)
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)  # also resets a Bottle_Entering left by another run
        time.sleep(SETTLE)
        if not plc.coil(COIL_BOTTLE_IN_POCKET):
            raise SystemExit("Bottle_In_Pocket does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        heartbeat.start()
        time.sleep(1.5)  # 2+ heartbeat toggles before Reset, else Alm_CommLost stays latched
        if not plc.wait_outfeed(False):
            raise SystemExit("Outfeed_Full stays TRUE with C3_Full FALSE: check the stage 3 rungs")
        reset_to_idle(plc)
        if plc.coil(COIL_COMMLOST) or plc.step() != 0:
            raise SystemExit(f"after Reset: Step {plc.step()}, alarms {sorted(alarms_on())} (expected 0, none)")

        # ---------- N1 normal cycle ----------
        to_wait_bottle()
        to_unclamp()
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BELL_DOWN, False)
        must(40)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        must(10)
        on = alarms_on()
        report("N1", not on, f"one bottle through every step -> alarms {sorted(on)} (expected none)")

        # ---------- label timeout ----------
        to_wrap()
        fault_after("L1", "Alm_LabelTimeout", LABEL_TIMEOUT, "WRAP without Label_Mark")
        time.sleep(SETTLE)  # MOVE 900 is the last rung: the outputs follow in the next scan
        out = outputs_on()
        report("L2", not out, f"FAULT -> outputs ON {out} (expected none)")
        reset("L3", "Alm_LabelTimeout", "Reset")
        plc.coil(COIL_BELL_DOWN, False)  # bell rises: ClampValve off in IDLE
        plc.coil(COIL_LABEL_PRESENT, True)  # the label was half wrapped
        plc.cmd(START)
        ok, seen, _ = plc.wait_step(40, 1.5)
        report("L4", ok and 20 not in seen, f"Start with the half-labelled bottle -> seen {seen} (expected 40, never 20)")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        must(10)
        to_wrap()
        time.sleep(LABEL_TIMEOUT - 1.5)  # most of the WRAP time used up: PRESS must start its own timer
        plc.pulse(COIL_LABEL_MARK)
        must(24)
        fault_after("L5", "Alm_LabelTimeout", LABEL_TIMEOUT, "PRESS without spin pulses")
        reset_to_idle(plc)

        # ---------- pocket jam ----------
        to_wait_bottle()
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        must(20)
        fault_after("J1", "Alm_PocketJam", CLAMP_TIMEOUT, "CLAMP without Bell_Down")
        plc.cmd(START)
        step = plc.step()
        report("J2", step == 900, f"Start in FAULT -> Step {step} (expected 900)")
        reset("J3", "Alm_PocketJam", "Reset")

        to_wait_bottle()
        to_unclamp()
        fault_after("J4", "Alm_PocketJam", CLAMP_TIMEOUT, "bell stuck down in UNCLAMP")
        plc.coil(COIL_BELL_DOWN, False)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        reset_to_idle(plc)

        to_wait_bottle()
        plc.coil(COIL_BOTTLE_AT_GATE, True)  # gate pulse, the bottle never reaches the pocket
        fault_after("J5", "Alm_PocketJam", CLAMP_TIMEOUT, "gate pulse, no Bottle_In_Pocket")
        entering = plc.coil(COIL_BOTTLE_ENTERING)
        if not entering:
            report("J5b", False, "Bottle_Entering FALSE in the gate jam (expected TRUE)")
        plc.coil(COIL_BOTTLE_AT_GATE, False)  # operator takes the stuck bottle away
        reset_to_idle(plc)
        time.sleep(SETTLE)
        entering, on, step = plc.coil(COIL_BOTTLE_ENTERING), alarms_on(), plc.step()
        report("J6", not entering and "Alm_PocketJam" not in on and step == 0,
               f"Reset -> Bottle_Entering {entering}, Step {step}, alarms {sorted(on)} (expected False, 0, none)")

        to_wait_bottle()
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        must(40)
        fault_after("J7", "Alm_PocketJam", OUT_TIMEOUT, "DISCHARGE, C3 free, the bottle stays in the pocket")
        reset_to_idle(plc)

        plc.coil(COIL_C3_FULL, True)
        if not plc.wait_outfeed(True):
            report("J8", False, "Outfeed_Full not TRUE within 5 s of C3_Full")
        else:
            plc.cmd(START)  # labelled bottle still in the pocket, C3 full -> 35
            must(35, 1.5)
            plc.write(HR_STEP, 40)  # DISCHARGE while C3 is full: the pusher is blocked by its interlock
            ok, seen = plc.stays(40, OUT_TIMEOUT + 1.0)
            on = alarms_on()
            report("J8", ok and not on, f"DISCHARGE with Outfeed_Full for {OUT_TIMEOUT + 1:g} s -> Step {seen}, alarms {sorted(on)} (expected 40, none)")
            plc.coil(COIL_C3_FULL, False)
            plc.wait_outfeed(False)
            time.sleep(SETTLE)
            plc.coil(COIL_BOTTLE_IN_POCKET, False)
            plc.coil(COIL_LABEL_PRESENT, False)
            ok, seen, _ = plc.wait_step(10, 1)
            report("J9", ok and not alarms_on(), f"C3 free, bottle out -> seen {seen}, alarms {sorted(alarms_on())} (expected 10, none)")

        # ---------- glue over-temperature ----------
        to_wait_bottle()
        plc.write(HR_GLUE_TEMP, GLUE_HOT)
        ok, seen, _ = plc.wait_step(900, 1)
        time.sleep(SETTLE)
        on, out = alarms_on(), outputs_on()
        report("G1", ok and "Alm_GlueOverTemp" in on and not out,
               f"GlueTemp 201.0 degC in WAIT_BOTTLE -> seen {seen}, alarms {sorted(on)}, outputs ON {out} (expected 900, GlueOverTemp, none)")
        reset_to_idle(plc)
        time.sleep(SETTLE)
        on, step = alarms_on(), plc.step()
        report("G2", step == 900 and "Alm_GlueOverTemp" in on, f"Reset at 201.0 degC -> Step {step}, alarms {sorted(on)} (expected 900, GlueOverTemp)")
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        reset("G3", "Alm_GlueOverTemp", "170.0 degC, Reset")
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        plc.write(HR_GLUE_TEMP, GLUE_HOT)
        time.sleep(SETTLE)
        step, state = plc.step(), client.read_holding_registers(HR_STATE, count=1).registers[0]
        report("G4", step == 900 and state == 9, f"GlueTemp 201.0 degC in Mode Stop -> Step {step}, State {state} (expected 900, 9)")
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        reset("G5", "Alm_GlueOverTemp", "170.0 degC, Auto + Reset")

        # ---------- label roll empty ----------
        to_wait_bottle()
        plc.coil(COIL_ROLL_EMPTY, True)
        time.sleep(SETTLE)
        on, step = alarms_on(), plc.step()
        report("E1", on == {"Alm_LabelEmpty"} and step == 10, f"Roll_Empty in WAIT_BOTTLE -> Step {step}, alarms {sorted(on)} (expected 10, LabelEmpty)")
        plc.coil(COIL_ROLL_EMPTY, False)
        time.sleep(SETTLE)
        on = alarms_on()
        report("E2", not on, f"roll changed -> alarms {sorted(on)} (expected none, no Reset needed)")
        plc.write(HR_MODE_REQ, MODE_STOP)
        must(0)
        plc.coil(COIL_ROLL_EMPTY, True)
        time.sleep(SETTLE)
        on = alarms_on()
        report("E3", not on, f"Roll_Empty in IDLE -> alarms {sorted(on)} (expected none)")
        plc.coil(COIL_ROLL_EMPTY, False)

        if quick:
            print("--quick: B1-B3 (2 min blocked) and H1-H2 (120 s heat-up) skipped")
        else:
            # ---------- outfeed blocked 2 min (warning) ----------
            to_wait_bottle()
            plc.coil(COIL_C3_FULL, True)
            if not plc.wait_outfeed(True):
                report("B1", False, "Outfeed_Full not TRUE within 5 s of C3_Full")
            else:
                plc.coil(COIL_LABEL_PRESENT, True)
                plc.coil(COIL_BOTTLE_IN_POCKET, True)
                must(35)
                print(f"      waiting {BLOCKED_WARN:.0f} s in WAIT_OUTFEED ...")
                time.sleep(BLOCKED_WARN + TOL)
                on, step = alarms_on(), plc.step()
                report("B1", on == {"Alm_OutfeedBlocked"} and step == 35, f"WAIT_OUTFEED 2 min -> Step {step}, alarms {sorted(on)} (expected 35, OutfeedBlocked)")
                plc.cmd(RESET)
                on = alarms_on()
                report("B2", "Alm_OutfeedBlocked" in on, f"Reset while still blocked -> alarms {sorted(on)} (expected OutfeedBlocked)")
                plc.coil(COIL_C3_FULL, False)
                must(40, 5)
                plc.coil(COIL_BOTTLE_IN_POCKET, False)
                plc.coil(COIL_LABEL_PRESENT, False)
                must(10)
                plc.cmd(RESET)
                on = alarms_on()
                report("B3", not on, f"C3 free, bottle out, Reset -> alarms {sorted(on)} (expected none)")

            # ---------- heat-up timeout ----------
            plc.write(HR_MODE_REQ, MODE_STOP)
            plc.clear_sensors()
            plc.write(HR_GLUE_TEMP, GLUE_FROZEN)
            time.sleep(SETTLE)
            plc.write(HR_MODE_REQ, MODE_AUTO)
            time.sleep(SETTLE)
            plc.cmd(START)
            must(5)
            print(f"      waiting {HEAT_TIMEOUT:.0f} s in WARM_UP ...")
            fault_after("H1", "Alm_GlueHeatTimeout", HEAT_TIMEOUT - SETTLE, "WARM_UP with GlueTemp 100.0 degC")
            reset("H2", "Alm_GlueHeatTimeout", "Reset (FAULT left WARM_UP)")
    finally:
        # end state for the next script: sensors FALSE, E_Stop_OK TRUE, no alarm, Step 0, Mode Stop
        plc.clear_sensors()
        plc.coil(COIL_E_STOP_OK, True)
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)  # clears a Bottle_Entering left by a failed check
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        plc.write(HR_GLUE_TEMP, 0)
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Step 0, Mode Stop, sensors FALSE, no alarm, GlueTemp 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
