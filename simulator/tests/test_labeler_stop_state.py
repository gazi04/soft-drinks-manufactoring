"""Labeler ladder stage 6: Stop at safe points, GoodCount with Cmd = 4, Starved, State.

Runs against the labeler PLC on port 5023. Plays the machine by writing the sensors (as the stage 4
test) and reads Step (%MW51), State (%MW50) and GoodCount (%MW53). Heartbeat kept alive in a thread,
E_Stop_OK TRUE, glue 170.0 degC (Glue_Ready), PressTurns 2. GoodCount is checked as a difference.

Stop at safe points (rule 6):
    S1  Stop in WRAP -> stays 22 (a started label is finished)
    S2  Label_Mark -> 24, 2 spin pulses -> 26 (Stop does not cut PRESS short)
    S3  bell up at the end of UNCLAMP -> 0 IDLE, not 40 (labelled bottle stays in the pocket)
    S4  Start -> 5 -> 10 -> labelled bottle -> 40 DISCHARGE directly (rule 7, never labelled twice)
    S5  pocket empty -> 10, GoodCount +1
    S6  Stop in WAIT_BOTTLE -> 0 at once;  S7 Stop in WARM_UP (cold glue) -> 0 at once
    S8  Stop in CLAMP -> stays 20;  S9 Bell_Down -> 22 (continues: no stop in CLAMP / WRAP / PRESS)
    S10 Stop in DISCHARGE -> pocket empty -> 0 (not 10), GoodCount +1 (counted on a Stop too)
    S11 Stop in WAIT_OUTFEED -> 0 at once
    S12 Stop in IDLE, then Start -> 5 (Stop_Request is never latched in IDLE)
Counter:
    C1  two bottles -> GoodCount +2;  C2 Cmd 3 (Reset) -> GoodCount unchanged;  C3 Cmd 4 -> GoodCount 0
State (rule 8):
    T1  IDLE -> 0;  T2 WARM_UP -> 1
    T3  bottle leaves the gate in WARM_UP, glue warm -> WAIT_BOTTLE with an empty gate -> 1 at once
    T4  gap at the gate of 1 s -> stays 1;  T5 gap of 3 s -> 2 Starved
    T6  bottle back at the gate -> 1 (it is gated; the pocket sensor follows within 2 s, else Alm_PocketJam)
    T7  Roll_Empty -> 2 at once, no GateMotor pulse
    T8  WAIT_OUTFEED -> 3 Blocked;  T9 Step 900 -> 9 Fault
    T10 Manual mode -> 0;  T11 Mode Stop -> 0

    uv run python tests/test_labeler_stop_state.py      (~45 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_labeler_sequencer import (
    COIL_BELL_DOWN, COIL_BOTTLE_AT_GATE, COIL_BOTTLE_IN_POCKET, COIL_C3_FULL, COIL_COMMLOST,
    COIL_E_STOP_OK, COIL_LABEL_MARK, COIL_LABEL_PRESENT, COIL_ROLL_EMPTY, COIL_SPIN_PULSE, GLUE_COLD,
    GLUE_SETPOINT, GLUE_WARM, HOST, HR_CMD, HR_GLUE_SETPOINT, HR_GLUE_TEMP, HR_MODE_REQ, HR_PRESS_TURNS, HR_STEP,
    MODE_AUTO, MODE_STOP, PORT, PRESS_TURNS, RESET, SETTLE, START, Heartbeat, Plc, connect, reset_to_idle,
)

HR_STATE = 1074  # %MW50
HR_GOOD_COUNT = 1077  # %MW53
COIL_GATE_MOTOR = 1  # %QX0.1
STOP, COUNT_RESET = 2, 4
MODE_MANUAL = 2


def main() -> int:
    claim_plant("test_labeler_stop_state", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_stop_state")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> None:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    def read(address: int) -> int:
        result = client.read_holding_registers(address, count=1)
        if result.isError():
            raise SystemExit(f"Read HR {address} failed: {result}")
        return result.registers[0]

    def reach(name: str, expected: int, timeout: float, what: str) -> bool:
        ok, seen, dt = plc.wait_step(expected, timeout)
        report(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})")
        return ok

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        report(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})")

    def state(name: str, expected: int, what: str) -> None:
        time.sleep(SETTLE)
        got = read(HR_STATE)
        report(name, got == expected, f"{what} -> State {got} (expected {expected}; Step {plc.step()})")

    def count(name: str, before: int, delta: int, what: str) -> None:
        time.sleep(SETTLE)
        got = read(HR_GOOD_COUNT)
        report(name, got == before + delta, f"{what} -> GoodCount {before} -> {got} (expected +{delta})")

    def must(expected: int, timeout: float = 1.0) -> None:
        """Helper step that is not a check of its own: stop the script if the sequencer does not get there."""
        ok, seen, _ = plc.wait_step(expected, timeout)
        if not ok:
            raise SystemExit(f"setup: Step {expected} not reached, seen {seen}")

    def to_wait_bottle() -> None:
        """From any step: Mode Stop -> 0, sensors clear, glue warm, Auto + Start -> 5 -> 10."""
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(START)
        must(10)

    def to_wrap() -> None:
        """From WAIT_BOTTLE: unlabelled bottle in the pocket -> 20, bell down -> 22."""
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        must(20)
        plc.coil(COIL_BELL_DOWN, True)
        must(22)

    def one_bottle() -> None:
        """From WAIT_BOTTLE: one bottle through every step back to WAIT_BOTTLE."""
        to_wrap()
        plc.pulse(COIL_LABEL_MARK)
        must(24)
        plc.pulse(COIL_SPIN_PULSE, PRESS_TURNS)
        must(26)
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BELL_DOWN, False)
        must(40)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        must(10)

    def reset_entering() -> None:
        """A gate pulse without a bottle reaching the pocket leaves Bottle_Entering set (it blocks the next
        pulse); the pocket sensor resets it in every mode. Only in Mode Stop (no transition)."""
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        plc.write(HR_GLUE_SETPOINT, GLUE_SETPOINT)
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        plc.coil(COIL_E_STOP_OK, True)
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
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE after Reset: Master_Enable is off, the sequencer cannot run")

        # ---------- Stop in WRAP: the label is finished, then IDLE ----------
        to_wait_bottle()
        to_wrap()
        plc.cmd(STOP)
        hold("S1", 22, 0.5, "Stop in WRAP")
        plc.pulse(COIL_LABEL_MARK)
        plc.wait_step(24, 1)
        plc.pulse(COIL_SPIN_PULSE, PRESS_TURNS)
        reach("S2", 26, 1, "Label_Mark, then 2 spin pulses after the Stop")
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BELL_DOWN, False)
        ok, seen, _ = plc.wait_step(0, 1)
        report("S3", ok and 40 not in seen, f"bell up at the end of UNCLAMP -> seen {seen} (expected 0, never 40)")
        good = read(HR_GOOD_COUNT)
        plc.cmd(START)
        ok, seen, _ = plc.wait_step(40, 1.5)
        report("S4", ok and 20 not in seen, f"Start with the labelled bottle in the pocket -> seen {seen} (expected 5, 10, 40; never 20)")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        reach("S5", 10, 1, "pocket empty")
        count("S5b", good, 1, "the restarted bottle left the pocket")

        # ---------- Stop at once in WAIT_BOTTLE and WARM_UP ----------
        plc.cmd(STOP)
        reach("S6", 0, 1, "Stop in WAIT_BOTTLE")
        plc.write(HR_GLUE_TEMP, GLUE_COLD)
        plc.cmd(START)
        must(5)
        plc.cmd(STOP)
        reach("S7", 0, 1, "Stop in WARM_UP (glue 150.0 degC)")
        plc.write(HR_GLUE_TEMP, GLUE_WARM)

        # ---------- no Stop in CLAMP ----------
        to_wait_bottle()
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        must(20)
        plc.cmd(STOP)
        hold("S8", 20, 0.5, "Stop in CLAMP")
        plc.coil(COIL_BELL_DOWN, True)
        reach("S9", 22, 1, "Bell_Down after the Stop")
        plc.pulse(COIL_LABEL_MARK)
        plc.wait_step(24, 1)
        plc.pulse(COIL_SPIN_PULSE, PRESS_TURNS)
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BELL_DOWN, False)
        must(0)  # end of UNCLAMP with the Stop still requested

        # ---------- Stop in DISCHARGE: counted, then IDLE ----------
        plc.cmd(START)
        must(40, 1.5)
        good = read(HR_GOOD_COUNT)
        plc.cmd(STOP)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        ok, seen, _ = plc.wait_step(0, 1)
        report("S10", ok and 10 not in seen, f"Stop in DISCHARGE, pocket empty -> seen {seen} (expected 0, never 10)")
        count("S10b", good, 1, "bottle discharged on a Stop")

        # ---------- Stop in WAIT_OUTFEED ----------
        plc.coil(COIL_C3_FULL, True)
        if not plc.wait_outfeed(True):
            report("S11", False, "Outfeed_Full not TRUE within 5 s of C3_Full")
        else:
            plc.coil(COIL_LABEL_PRESENT, True)
            plc.coil(COIL_BOTTLE_IN_POCKET, True)
            plc.cmd(START)
            must(35, 1.5)
            state("T8", 3, "WAIT_OUTFEED (labelled bottle, C3 full)")
            plc.cmd(STOP)
            reach("S11", 0, 1, "Stop in WAIT_OUTFEED")
        plc.coil(COIL_C3_FULL, False)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        plc.wait_outfeed(False)

        # ---------- Stop in IDLE is not remembered ----------
        plc.cmd(STOP)
        plc.cmd(START)
        reach("S12", 10, 1.5, "Stop in IDLE, then Start (glue warm: 5 -> 10)")

        # ---------- counter ----------
        good = read(HR_GOOD_COUNT)
        one_bottle()
        one_bottle()
        count("C1", good, 2, "two bottles")
        good = read(HR_GOOD_COUNT)
        plc.cmd(RESET)
        count("C2", good, 0, "Cmd 3 (Reset)")
        plc.cmd(COUNT_RESET)
        time.sleep(SETTLE)
        got = read(HR_GOOD_COUNT)
        report("C3", got == 0, f"Cmd 4 (counter reset) -> GoodCount {got} (expected 0)")

        # ---------- State ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        must(0)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        state("T1", 0, "Auto IDLE")
        plc.write(HR_GLUE_TEMP, GLUE_COLD)
        plc.cmd(START)
        must(5)
        state("T2", 1, "WARM_UP")
        # A bottle at the gate in WAIT_BOTTLE is gated at once (and must reach the pocket within 2 s), so the
        # gap starts in WARM_UP: the bottle there restarts Gate_Empty_Timer, the gap begins when it leaves.
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        time.sleep(SETTLE)
        plc.coil(COIL_BOTTLE_AT_GATE, False)
        gap_start = time.monotonic()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        must(10)
        state("T3", 1, "WAIT_BOTTLE, gate empty for a moment")
        time.sleep(max(0.0, 1.0 - (time.monotonic() - gap_start)))
        got = read(HR_STATE)
        report("T4", got == 1, f"no bottle at the gate for 1 s -> State {got} (expected 1, gap below StarvedDelay)")
        time.sleep(max(0.0, 3.0 - (time.monotonic() - gap_start)))
        state("T5", 2, "no bottle at the gate for 3 s")
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        state("T6", 1, "bottle back at the gate (gated)")
        reset_entering()  # within 2 s of the gate pulse: no Alm_PocketJam
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.coil(COIL_ROLL_EMPTY, True)
        plc.write(HR_CMD, START)
        gate_seen = False
        end = time.monotonic() + 1.5
        while time.monotonic() < end:  # polled from the Start on: a 0.5 s gate pulse cannot slip through
            gate_seen |= bool(client.read_coils(COIL_GATE_MOTOR, count=1).bits[0])
            time.sleep(0.01)
        step_now, state_now = plc.step(), read(HR_STATE)
        report("T7", step_now == 10 and state_now == 2 and not gate_seen,
               f"Start, label roll empty, bottle at the gate -> Step {step_now}, State {state_now}, "
               f"GateMotor pulse {gate_seen} (expected 10, 2, False)")
        plc.coil(COIL_ROLL_EMPTY, False)
        plc.coil(COIL_BOTTLE_AT_GATE, False)
        plc.write(HR_STEP, 900)
        state("T9", 9, "Step 900 written (FAULT)")
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_MANUAL)
        state("T10", 0, "Manual mode")
        plc.write(HR_MODE_REQ, MODE_STOP)
        state("T11", 0, "Mode Stop")
    finally:
        # end state for the next script: sensors FALSE, E_Stop_OK TRUE, defaults, Step 0, Mode Stop
        plc.clear_sensors()
        plc.coil(COIL_E_STOP_OK, True)
        reset_entering()
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        plc.write(HR_GLUE_TEMP, 0)
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Step 0, Mode Stop, sensors FALSE, GoodCount 0, GlueTemp 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
