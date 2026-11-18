"""Labeler ladder stage 4: sequencer skeleton (step decode, Spin_Counter, transitions).

Runs against the labeler PLC on port 5023. No outputs exist yet: the script plays the machine by
writing the sensors and watches Step (%MW51). It keeps the heartbeat alive in a thread, sets
E_Stop_OK, Auto and Reset, and writes the glue setpoints (GlueSetpoint 1700, GlueBand 100 ->
Glue_Ready window 1600-1800) and PressTurns 2.

One bottle:
    Q1  setup: Auto, heartbeat, Reset -> Step 0, Alm_CommLost FALSE
    Q2  Start -> 5 WARM_UP
    Q3  GlueTemp 1500 -> stays 5;  Q4  1700 (Glue_Ready) -> 10 WAIT_BOTTLE
    Q5  Bottle_In_Pocket, no label -> 20 CLAMP
    Q6  Label_Mark pulse while the bell is still up -> stays 20 (only a mark in WRAP counts)
    Q7  Bell_Down -> 22 WRAP
    Q8  2 Spin_Pulse pulses in WRAP -> stays 22 (and are not counted, see Q10)
    Q9  Label_Mark pulse -> 24 PRESS (proves Mark_Edge from stage 3)
    Q10 1 Spin_Pulse -> stays 24;  Q11 2nd pulse -> 26 UNCLAMP (PressTurns 2)
    Q12 Bell_Down off, C3 free -> 40 DISCHARGE;  Q13 Bottle_In_Pocket off -> 10
Variants:
    Q14 PressTurns 3: 2 pulses -> stays 24, 3rd -> 26 (PV follows the setpoint, CV starts at 0 per bottle)
    Q15 C3_Full until Outfeed_Full, bell up -> 35 WAIT_OUTFEED;  Q16 C3 free (3 s off) -> 40
    Q17 labelled bottle in 10, C3 free -> 40 directly (never 20: no second label)
    Q18 labelled bottle in 10, Outfeed_Full -> 35;  Q19 C3 free -> 40
    Q20 GlueTemp 1500 with an unlabelled bottle in the pocket -> stays 10 (10 -> 20 needs Glue_Ready;
        Step 10 written by the script, the state cannot be reached with one sensor write at a time)
    Q21 GlueTemp 1700 -> 20
    Q22 E_Stop_OK FALSE in CLAMP -> 0 (abort)
    Q23 GlueTemp 1500 in 10 with an empty pocket -> 5;  Q24 Mode Stop in 5 -> 0 (abort)
    Q25 Step 900 (written by the script) stays in Stop mode (abort keeps FAULT); Q26 Auto + Reset -> 0

    uv run python tests/test_labeler_sequencer.py      (~25 s)

Spin_Counter has no address: watch Spin_Counter.CV in the debugger (counts only in PRESS, 0 elsewhere).
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5023

HR_CMD = 1024  # %MW0
HR_MODE_REQ = 1025  # %MW1
HR_GLUE_TEMP = 1036  # %MW12
HR_GLUE_SETPOINT = 1054  # %MW30
HR_GLUE_BAND = 1056  # %MW32
HR_PRESS_TURNS = 1057  # %MW33
HR_STEP = 1075  # %MW51
COIL_BOTTLE_AT_GATE = 8192  # %MX0.0
COIL_BOTTLE_IN_POCKET = 8193  # %MX0.1
COIL_BELL_DOWN = 8194  # %MX0.2
COIL_LABEL_PRESENT = 8195  # %MX0.3
COIL_C3_FULL = 8196  # %MX0.4
COIL_ROLL_EMPTY = 8197  # %MX0.5
COIL_LABEL_MARK = 8198  # %MX0.6
COIL_SPIN_PULSE = 8199  # %MX0.7
COIL_E_STOP_OK = 8201  # %MX1.1
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_OUTFEED_FULL = 8216  # %MX3.0
COIL_COMMLOST = 8235  # %MX5.3

SENSOR_COILS = (COIL_BOTTLE_AT_GATE, COIL_BOTTLE_IN_POCKET, COIL_BELL_DOWN, COIL_LABEL_PRESENT,
                COIL_C3_FULL, COIL_ROLL_EMPTY, COIL_LABEL_MARK, COIL_SPIN_PULSE)

START, RESET = 1, 3
MODE_STOP, MODE_AUTO = 0, 1
GLUE_SETPOINT, GLUE_BAND, PRESS_TURNS = 1700, 100, 2  # defaults (Glue_Ready window 1600-1800)
GLUE_WARM, GLUE_COLD = 1700, 1500
SETTLE = 0.3  # s: many scans of the 20 ms task
PULSE = 0.15  # s: length of one Label_Mark / Spin_Pulse pulse and the gap after it (several scans each)


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


class Heartbeat:
    """Toggles %MX1.2 every 0.5 s in a thread with its own client (a pymodbus client is not thread-safe)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()

    def _run(self) -> None:
        client = connect()
        value = False
        try:
            while not self._stop.is_set():
                value = not value
                client.write_coil(COIL_HEARTBEAT, value)
                self._stop.wait(0.5)
        finally:
            client.close()


class Plc:
    def __init__(self, client: ModbusTcpClient) -> None:
        self.c = client

    def write(self, address: int, value: int) -> None:
        self.c.write_register(address, value)

    def coil(self, address: int, value: bool | None = None) -> bool:
        if value is not None:
            self.c.write_coil(address, value)
            return value
        result = self.c.read_coils(address, count=1)
        if result.isError():
            raise SystemExit(f"Read coil {address} failed: {result}")
        return bool(result.bits[0])

    def pulse(self, address: int, count: int = 1) -> None:
        """Sensor TRUE for PULSE, then FALSE for PULSE, `count` times (Label_Mark, Spin_Pulse)."""
        for _ in range(count):
            self.c.write_coil(address, True)
            time.sleep(PULSE)
            self.c.write_coil(address, False)
            time.sleep(PULSE)

    def step(self) -> int:
        result = self.c.read_holding_registers(HR_STEP, count=1)
        if result.isError():
            raise SystemExit(f"Read Step failed: {result}")
        return result.registers[0]

    def cmd(self, value: int) -> None:
        self.write(HR_CMD, value)
        time.sleep(SETTLE)

    def wait_step(self, expected: int, timeout: float) -> tuple[bool, list[int], float]:
        """Poll Step until it equals expected; returns (reached, distinct steps seen, seconds)."""
        t0 = time.monotonic()
        seen: list[int] = []
        while True:
            s = self.step()
            if not seen or seen[-1] != s:
                seen.append(s)
            if s == expected:
                return True, seen, time.monotonic() - t0
            if time.monotonic() - t0 > timeout:
                return False, seen, time.monotonic() - t0
            time.sleep(0.05)

    def stays(self, expected: int, seconds: float) -> tuple[bool, list[int]]:
        """Step must equal expected the whole time."""
        end = time.monotonic() + seconds
        seen: list[int] = []
        while time.monotonic() < end:
            s = self.step()
            if not seen or seen[-1] != s:
                seen.append(s)
            time.sleep(0.05)
        return seen == [expected], seen

    def wait_outfeed(self, value: bool, timeout: float = 5.0) -> bool:
        """Wait until the Outfeed_Full filter (2 s on / 3 s off) has followed C3_Full."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.coil(COIL_OUTFEED_FULL) == value:
                return True
            time.sleep(0.1)
        return False

    def clear_sensors(self) -> None:
        for address in SENSOR_COILS:
            self.c.write_coil(address, False)


def reset_to_idle(plc: Plc) -> int:
    """Auto, then Reset -> Step 0 (transition 900 -> 0 needs Seq_Enable, i.e. Auto); returns the Step reached.
    Needs the heartbeat running and E_Stop_OK TRUE."""
    plc.write(HR_MODE_REQ, MODE_AUTO)
    time.sleep(SETTLE)
    plc.cmd(RESET)
    return plc.step()


def main() -> int:
    claim_plant("test_labeler_sequencer", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_sequencer")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def reach(name: str, expected: int, timeout: float, what: str) -> tuple[bool, list[int]]:
        ok, seen, dt = plc.wait_step(expected, timeout)
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})"))
        return ok, seen

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        results.append(check(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})"))

    def to_wait_bottle() -> None:
        """From any step: Mode Stop -> 0, sensors clear, glue warm, Auto + Start -> 5 -> 10."""
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(START)
        ok, seen, _ = plc.wait_step(10, 1)
        if not ok:
            raise SystemExit(f"cannot reach WAIT_BOTTLE (10) for the next check, seen {seen}")

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address, value in ((HR_GLUE_TEMP, GLUE_COLD), (HR_GLUE_SETPOINT, GLUE_SETPOINT),
                               (HR_GLUE_BAND, GLUE_BAND), (HR_PRESS_TURNS, PRESS_TURNS)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        # the sensor writes must reach the PLC: a debugger force would block them
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        time.sleep(SETTLE)
        if not plc.coil(COIL_BOTTLE_IN_POCKET):
            raise SystemExit("Bottle_In_Pocket does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        heartbeat.start()
        # at least 2 toggles before Reset: the watchdog's Reset rung clears Alm_CommLost only once the
        # PLC has seen the heartbeat change (Hb_Timer.Q FALSE); the first toggle may write the old value
        time.sleep(1.5)
        if not plc.wait_outfeed(False):  # a seal-in left by another script needs 3 s off
            raise SystemExit("Outfeed_Full stays TRUE with C3_Full FALSE: check the stage 3 rungs")
        reset_to_idle(plc)
        step, lost = plc.step(), plc.coil(COIL_COMMLOST)
        results.append(check("Q1", step == 0 and not lost,
                             f"Auto, heartbeat, Reset -> Step {step}, Alm_CommLost {lost} (expected 0 / False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencer cannot run")

        # ---------- one bottle ----------
        plc.cmd(START)
        reach("Q2", 5, 1, "Start")
        hold("Q3", 5, 1.0, "GlueTemp 150.0 degC (below the window)")
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        reach("Q4", 10, 1, "GlueTemp 170.0 degC -> Glue_Ready")
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        reach("Q5", 20, 1, "Bottle_In_Pocket, no label")
        plc.pulse(COIL_LABEL_MARK)
        hold("Q6", 20, 0.5, "Label_Mark pulse in CLAMP (bell still up)")
        plc.coil(COIL_BELL_DOWN, True)
        reach("Q7", 22, 1, "Bell_Down")
        plc.pulse(COIL_SPIN_PULSE, 2)
        hold("Q8", 22, 0.5, "2 Spin_Pulse pulses in WRAP, no Label_Mark")
        plc.pulse(COIL_LABEL_MARK)
        reach("Q9", 24, 1, "Label_Mark pulse in WRAP (Mark_Edge)")
        plc.pulse(COIL_SPIN_PULSE)
        hold("Q10", 24, 0.5, "1 Spin_Pulse in PRESS (PressTurns 2; WRAP pulses not counted)")
        plc.pulse(COIL_SPIN_PULSE)
        reach("Q11", 26, 1, "2nd Spin_Pulse in PRESS")
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BELL_DOWN, False)
        reach("Q12", 40, 1, "Bell_Down off (bell up), C3 free")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        reach("Q13", 10, 1, "Bottle_In_Pocket off (bottle on C3)")

        # ---------- Q14 PressTurns 3, Q15/Q16 C3 full at the end of UNCLAMP ----------
        plc.write(HR_PRESS_TURNS, 3)
        # C3 full already now: Outfeed_Full needs 2 s, and UNCLAMP must end within ClampTimeout (2 s, stage 7)
        plc.coil(COIL_C3_FULL, True)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        plc.wait_step(20, 1)
        plc.coil(COIL_BELL_DOWN, True)
        plc.wait_step(22, 1)
        plc.pulse(COIL_LABEL_MARK)
        plc.wait_step(24, 1)
        plc.pulse(COIL_SPIN_PULSE, 2)
        held, seen_held = plc.stays(24, 0.3)
        plc.pulse(COIL_SPIN_PULSE)
        ok, seen, _ = plc.wait_step(26, 1)
        results.append(check("Q14", held and ok,
                             f"PressTurns 3: 2 pulses -> stays 24 (seen {seen_held}), 3rd -> {seen[-1]} (expected 26)"))
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        if not plc.wait_outfeed(True):
            results.append(check("Q15", False, "Outfeed_Full not TRUE within 5 s of C3_Full"))
        else:
            plc.coil(COIL_LABEL_PRESENT, True)
            plc.coil(COIL_BELL_DOWN, False)
            reach("Q15", 35, 1, "bell up, Outfeed_Full")
            plc.coil(COIL_C3_FULL, False)
            reach("Q16", 40, 5, "C3_Full off (Outfeed_Full drops after 3 s)")
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        plc.wait_step(10, 1)

        # ---------- Q17 labelled bottle in WAIT_BOTTLE, C3 free ----------
        plc.coil(COIL_LABEL_PRESENT, True)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        ok, seen = reach("Q17", 40, 1, "labelled bottle in the pocket (restart), C3 free")
        if ok and 20 in seen:
            results.append(check("Q17b", False, f"seen {seen}: went through CLAMP, the bottle would get a second label"))
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        plc.wait_step(10, 1)

        # ---------- Q18/Q19 labelled bottle in WAIT_BOTTLE, C3 full ----------
        plc.coil(COIL_C3_FULL, True)
        if not plc.wait_outfeed(True):
            results.append(check("Q18", False, "Outfeed_Full not TRUE within 5 s of C3_Full"))
        else:
            plc.coil(COIL_LABEL_PRESENT, True)
            plc.coil(COIL_BOTTLE_IN_POCKET, True)
            reach("Q18", 35, 1, "labelled bottle in the pocket, Outfeed_Full")
            plc.coil(COIL_C3_FULL, False)
            reach("Q19", 40, 5, "C3_Full off")
        plc.coil(COIL_C3_FULL, False)
        plc.coil(COIL_BOTTLE_IN_POCKET, False)
        plc.coil(COIL_LABEL_PRESENT, False)
        plc.wait_step(10, 1)

        # ---------- Q20/Q21 cold glue with an unlabelled bottle: wait in 10 ----------
        # Not reachable with sensor writes one at a time (cold glue + empty pocket -> 5 first, bottle +
        # warm glue -> 20 at once), so the script parks the machine in IDLE (Auto, no Start: nothing
        # writes Step) with the sensors set, and writes Step 10 itself, as Q25 does with 900.
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.wait_step(0, 1)
        plc.write(HR_GLUE_TEMP, GLUE_COLD)
        plc.coil(COIL_BOTTLE_IN_POCKET, True)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.write(HR_STEP, 10)
        hold("Q20", 10, 1.0, "GlueTemp 150.0 degC, unlabelled bottle in the pocket (no CLAMP, no WARM_UP)")
        plc.write(HR_GLUE_TEMP, GLUE_WARM)
        reach("Q21", 20, 1, "GlueTemp 170.0 degC again")

        # ---------- Q22 E-stop aborts ----------
        plc.coil(COIL_E_STOP_OK, False)
        reach("Q22", 0, 1, "E_Stop_OK FALSE in CLAMP")
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)

        # ---------- Q23/Q24 glue cools down in WAIT_BOTTLE, pocket empty ----------
        to_wait_bottle()
        plc.write(HR_GLUE_TEMP, GLUE_COLD)
        reach("Q23", 5, 1, "GlueTemp 150.0 degC in WAIT_BOTTLE, pocket empty")
        plc.write(HR_MODE_REQ, MODE_STOP)
        reach("Q24", 0, 1, "Mode Stop in WARM_UP")

        # ---------- Q25/Q26 FAULT exit ----------
        plc.write(HR_STEP, 900)
        hold("Q25", 900, 0.5, "Step 900 written, Mode Stop (abort must keep FAULT)")
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(RESET)
        reach("Q26", 0, 1, "Auto + Reset in FAULT")
    finally:
        # end state for the next script: sensors FALSE, E_Stop_OK TRUE, defaults, Step 0, Mode Stop
        plc.clear_sensors()
        plc.coil(COIL_E_STOP_OK, True)
        plc.write(HR_PRESS_TURNS, PRESS_TURNS)
        plc.write(HR_GLUE_TEMP, 0)
        reset_to_idle(plc)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Step 0, Mode Stop, sensors FALSE, PressTurns 2, GlueTemp 0)")
    print("Debugger (127.0.0.3): Spin_Counter.CV counts only in PRESS and is 0 in every other step.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
