"""Syrup room ladder stage 4: sequencer skeleton (step decode, done bits, transitions, Batch_Dosed).

Runs against the syrup PLC on port 5022. No outputs exist yet: the script plays the plant by
writing the sensors and watches Step (%MW51). It keeps the heartbeat alive in a thread, sets
E_Stop_OK, Auto and Reset, and shortens MixTime / CarbTime to 2 s (set back to 20 / 10 at the end).

One batch (defaults: WaterBatch 500, DoseTarget 590, BufferLow 800, CoolSetpoint 40, PressSetpoint 350):
    Q1  setup: Auto, heartbeat, Reset -> Step 0, Alm_CommLost FALSE
    Q2  Start -> 5 WAIT_DEMAND
    Q3  buffer 1500 (above BufferLow) -> stays 5
    Q4  buffer 700 -> 10 DOSE_WATER
    Q5  MixLevel 499 -> stays 10;  Q6  500 -> 20 DOSE_SYRUP
    Q7  MixLevel 589 -> stays 20;  Q8  590 -> 30 MIX, Batch_Dosed TRUE
    Q9  30 -> 40 COOL after MixTime (2 s), not before 1 s
    Q10 ProductTemp 41 -> stays 40; Q11 40 -> 50 CARBONATE
    Q12 pressure 300 (CO2_Demand) for 3 s -> stays 50 (Carb_Timer only runs without CO2 demand)
    Q13 pressure 400, buffer 1400 (1400 + 590 > 1950) -> 55 WAIT_BUFFER after CarbTime
    Q14 buffer 1300 -> 60 TRANSFER
    Q15 MixLevel 6 -> stays 60;  Q16 5 -> 5, Batch_Dosed FALSE
Variants:
    Q17 resume: Mode Stop in MIX -> 0, Batch_Dosed kept; Auto + Start -> 30 without passing 10
    Q18 E_Stop_OK FALSE -> 0 (abort)
    Q19 undosed batch in the tank (MixLevel 300, Batch_Dosed FALSE), buffer full -> 10 anyway
    Q20 batch needed but not enough syrup (SyrupLevel 50 < SyrupTarget 90) -> stays 5; Q21 syrup 1000 -> 10
    Q22 Step 900 (written by the script) stays in Stop mode (abort keeps FAULT); Q23 Auto + Reset -> 0

    uv run python tests/test_syrup_sequencer.py      (~45 s)

Water_Timer / Syrup_Timer have no address: check them in the debugger (ET counts in 10 / 20, resets in 30).
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5022

HR_CMD = 1024  # %MW0
HR_MODE_REQ = 1025  # %MW1
HR_SYRUP_LEVEL = 1036  # %MW12
HR_MIX_LEVEL = 1037  # %MW13
HR_PRODUCT_TEMP = 1038  # %MW14
HR_CO2_PRESSURE = 1039  # %MW15
HR_BUFFER_LEVEL = 1040  # %MW16
HR_MIX_TIME = 1056  # %MW32
HR_CARB_TIME = 1063  # %MW39
HR_STEP = 1075  # %MW51
COIL_MIX_HIGH_HIGH = 8192  # %MX0.0
COIL_E_STOP_OK = 8201  # %MX1.1
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_BATCH_DOSED = 8219  # %MX3.3
COIL_COMMLOST = 8235  # %MX5.3

START, RESET = 1, 3
MODE_STOP, MODE_AUTO = 0, 1
DEFAULT_MIX_TIME, DEFAULT_CARB_TIME = 20, 10
SETTLE = 0.3  # s: many scans of the 20 ms task


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


def reset_to_idle(client: ModbusTcpClient) -> int:
    """Auto, then Reset until Step = 0 (at most 3 Resets); returns the Step reached.

    Auto first: the transition 900 -> 0 needs Seq_Enable (Auto), in Mode Stop the abort rung keeps FAULT.
    Repeated as a safety net: before the ladder fix of 2026-11-14 a FAULT with Alm_CommLost also latched
    needed two Resets (Master_Enable was computed above the watchdog's Reset rung). Since Master_Enable
    comes after that rung, one Reset leaves FAULT; the loop then ends after the first try.
    Needs the heartbeat running and E_Stop_OK TRUE."""
    client.write_register(HR_MODE_REQ, MODE_AUTO)
    time.sleep(SETTLE)
    step = -1
    for _ in range(3):
        client.write_register(HR_CMD, RESET)
        time.sleep(SETTLE)
        step = client.read_holding_registers(HR_STEP, count=1).registers[0]
        if step == 0:
            break
    return step


def leave_idle(client: ModbusTcpClient) -> None:
    """End state of every syrup script, so the next one starts clean: sensors 0, MixHighHigh FALSE,
    E_Stop_OK TRUE, then reset_to_idle and Mode Stop -> Step 0, no alarm latched.
    Call it while the heartbeat still runs (Master_Enable must be on)."""
    for address in (HR_SYRUP_LEVEL, HR_MIX_LEVEL, HR_PRODUCT_TEMP, HR_CO2_PRESSURE, HR_BUFFER_LEVEL):
        client.write_register(address, 0)
    client.write_coil(COIL_MIX_HIGH_HIGH, False)
    client.write_coil(COIL_E_STOP_OK, True)
    reset_to_idle(client)  # causes gone: clears the alarms and leaves FAULT
    client.write_register(HR_MODE_REQ, MODE_STOP)


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


def main() -> int:
    claim_plant("test_syrup_sequencer", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_sequencer")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def reach(name: str, expected: int, timeout: float, what: str) -> bool:
        ok, seen, dt = plc.wait_step(expected, timeout)
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})"))
        return ok

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        results.append(check(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})"))

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        for address, value in ((HR_SYRUP_LEVEL, 1000), (HR_MIX_LEVEL, 0), (HR_PRODUCT_TEMP, 200),
                               (HR_CO2_PRESSURE, 400), (HR_BUFFER_LEVEL, 1500),
                               (HR_MIX_TIME, 2), (HR_CARB_TIME, 2)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_BATCH_DOSED, False)
        heartbeat.start()
        time.sleep(1.0)
        reset_to_idle(client)  # Auto + Reset until Step 0 (a FAULT left by another script)
        lost = plc.coil(COIL_COMMLOST)
        results.append(check("Q1", plc.step() == 0 and not lost,
                             f"Auto, heartbeat, Reset -> Step {plc.step()}, Alm_CommLost {lost} (expected 0 / False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencer cannot run")

        # ---------- one batch ----------
        plc.cmd(START)
        reach("Q2", 5, 1, "Start")
        hold("Q3", 5, 1.0, "buffer 1500, tank empty")
        plc.write(HR_BUFFER_LEVEL, 700)
        reach("Q4", 10, 1, "buffer 700 < BufferLow")
        plc.write(HR_MIX_LEVEL, 499)
        hold("Q5", 10, 0.5, "MixLevel 499")
        plc.write(HR_MIX_LEVEL, 500)
        reach("Q6", 20, 1, "MixLevel 500 = WaterBatch")
        plc.write(HR_MIX_LEVEL, 589)
        hold("Q7", 20, 0.5, "MixLevel 589")
        plc.write(HR_MIX_LEVEL, 590)
        ok = reach("Q8", 30, 1, "MixLevel 590 = DoseTarget")
        dosed = plc.coil(COIL_BATCH_DOSED)
        results.append(check("Q8b", dosed, f"Batch_Dosed = {dosed} after DOSE_SYRUP (expected True)"))
        if ok:
            t0 = time.monotonic()
            ok9, seen, _ = plc.wait_step(40, 5)
            dt = time.monotonic() - t0
            results.append(check("Q9", ok9 and 1.5 <= dt <= 3.0,
                                 f"MIX -> 40 after {dt:.1f} s (MixTime 2 s; seen {seen})"))
        plc.write(HR_PRODUCT_TEMP, 41)
        hold("Q10", 40, 0.5, "ProductTemp 4.1 degC (Temp_OK only at <= 4.0)")
        plc.write(HR_PRODUCT_TEMP, 40)
        reach("Q11", 50, 1, "ProductTemp 4.0 degC -> Temp_OK")
        plc.write(HR_CO2_PRESSURE, 300)
        hold("Q12", 50, 3.0, "pressure 3.00 bar (CO2_Demand) for 3 s, CarbTime 2 s")
        plc.write(HR_BUFFER_LEVEL, 1400)
        plc.write(HR_CO2_PRESSURE, 400)
        reach("Q13", 55, 4, "pressure 4.00 bar, buffer 1400 + 590 > 1950 (no room)")
        plc.write(HR_BUFFER_LEVEL, 1300)
        reach("Q14", 60, 1, "buffer 1300 + 590 <= 1950 (room)")
        plc.write(HR_MIX_LEVEL, 6)
        hold("Q15", 60, 0.5, "MixLevel 6")
        plc.write(HR_MIX_LEVEL, 5)
        reach("Q16", 5, 1, "MixLevel 5 = MixEmpty")
        dosed = plc.coil(COIL_BATCH_DOSED)
        results.append(check("Q16b", not dosed, f"Batch_Dosed = {dosed} after TRANSFER (expected False)"))

        # ---------- Q17 resume a dosed batch ----------
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.write(HR_MIX_LEVEL, 590)  # 10 -> 20 -> 30 in consecutive scans
        plc.wait_step(30, 2)
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        step, dosed = plc.step(), plc.coil(COIL_BATCH_DOSED)
        results.append(check("Q17a", step == 0 and dosed, f"Mode Stop in MIX -> Step {step}, Batch_Dosed {dosed} (expected 0 / True)"))
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.write(HR_CMD, START)
        ok, seen, _ = plc.wait_step(30, 2)
        results.append(check("Q17b", ok and 10 not in seen, f"Auto + Start -> seen {seen} (expected 0, 5, 30: resume at MIX, no water)"))

        # ---------- Q18 E-stop aborts ----------
        plc.coil(COIL_E_STOP_OK, False)
        reach("Q18", 0, 1, "E_Stop_OK FALSE in MIX")
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)

        # ---------- Q19 undosed batch in the tank ----------
        plc.coil(COIL_BATCH_DOSED, False)
        plc.write(HR_MIX_LEVEL, 300)
        plc.write(HR_BUFFER_LEVEL, 1500)
        plc.cmd(START)
        reach("Q19", 10, 1, "MixLevel 300 without Batch_Dosed, buffer full")
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)

        # ---------- Q20/Q21 not enough syrup ----------
        plc.write(HR_MIX_LEVEL, 0)
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.write(HR_SYRUP_LEVEL, 50)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(START)
        hold("Q20", 5, 1.0, "batch needed, SyrupLevel 50 < SyrupTarget 90")
        plc.write(HR_SYRUP_LEVEL, 1000)
        reach("Q21", 10, 1, "SyrupLevel 1000")

        # ---------- Q22/Q23 FAULT exit ----------
        plc.write(HR_STEP, 900)
        plc.write(HR_MODE_REQ, MODE_STOP)
        hold("Q22", 900, 0.5, "Step 900 written, Mode Stop (abort must keep FAULT)")
        plc.write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        plc.cmd(RESET)
        reach("Q23", 0, 1, "Auto + Reset in FAULT")
    finally:
        leave_idle(client)
        plc.write(HR_MIX_TIME, DEFAULT_MIX_TIME)
        plc.write(HR_CARB_TIME, DEFAULT_CARB_TIME)
        plc.coil(COIL_BATCH_DOSED, False)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Step 0, no alarms, Mode Stop, MixTime 20 / CarbTime 10, sensors 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
