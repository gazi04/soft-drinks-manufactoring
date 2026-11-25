"""Blow moulder ladder stage 4: oven station sequencer (step decode, Lamps_On, Soak_Timer, Start, abort).

Runs against the blow moulder PLC on port 5025. No outputs exist yet: the script plays the machine by
writing the sensors and watches Step (%MW51) and MouldStep (%MW52). It keeps the heartbeat alive in a
thread, sets E_Stop_OK, the setpoint defaults (Oven_Ready window 1000-1100) and resets. The mould
station has no transitions yet (stage 5): MouldStep stays where the Start put it.

    O1  setup: Mode Stop, heartbeat, Reset -> Step 0, MouldStep 0, Alm_CommLost FALSE
    O2  Lamps_On: Auto + preform in the pocket (IDLE) -> TRUE; no preform -> FALSE
One preform:
    O3  Start, pocket empty, mould neither open nor closed -> Step 10 FEED, MouldStep 10 WAIT_PREFORM
    O4  C1 full (Outfeed_Full) + preform at the loader -> stays 10 (Blocked)
    O5  C1 free again (off-filter 3 s) -> 15 LOAD
    O6  preform in the pocket -> 20 HEAT, Lamps_On TRUE
    O7  OvenTemp 90.0 degC (cold) -> stays 20
    O8  soak restart: 105.0 degC 0.6 s, out of the window, back in -> 25 HOT about SoakTime (1 s) after
        the second entry, not after the first
    O9  pocket empty (the arm took the preform) -> 10 FEED, Lamps_On FALSE
Start by sensors (rule 9), Mode Stop between them:
    O10 Mode Stop -> both 0
    O11 preform in the pocket, mould closed (35 bar left inside) -> Step 20, MouldStep 60 EXHAUST
    O12 mould open with a bottle -> MouldStep 80 EJECT
    O13 mould open, empty -> MouldStep 10
    O14 mould open AND closed with a bottle (sensor fault) -> MouldStep 60 (closed wins, exhaust first)
Guards:
    O15 MouldStep 10 written, Step 0, Start -> nothing (both stations must be IDLE)
    O16 E_Stop_OK FALSE in HEAT -> both 0, Lamps_On FALSE
    O17 Manual with a preform in the pocket -> Lamps_On FALSE
    O18 Step / MouldStep 900 (written in Auto) -> Lamps_On FALSE, Start ignored; Mode Stop keeps 900
        (abort keeps FAULT; 900 -> 0 comes with Reset in stage 9, the script writes 0 back)

    uv run python tests/test_blowmoulder_oven_seq.py      (~45 s)

Preform_Hot is TRUE for one scan only (the step moves on in the same scan): O8 checks it by timing.
Soak_Timer, the step bits and Seq_Enable have no address: watch them in the debugger if a check fails.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5025

HR_CMD = 1024  # %MW0
HR_MODE_REQ = 1025  # %MW1
HR_OVEN_TEMP = 1036  # %MW12
HR_RAW = 1037  # %MW13 BlowPressure_Raw
HR_OVEN_SETPOINT = 1054  # %MW30
HR_OVEN_BAND = 1055  # %MW31
HR_STEP = 1075  # %MW51
HR_MOULD_STEP = 1076  # %MW52
COIL_PREFORM_AT_LOADER = 8192  # %MX0.0
COIL_PREFORM_IN_OVEN = 8193  # %MX0.1
COIL_MOULD_CLOSED = 8196  # %MX0.4
COIL_MOULD_OPEN = 8197  # %MX0.5
COIL_E_STOP_OK = 8201  # %MX1.1
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_LAMPS_ON = 8223  # %MX3.7
COIL_BOTTLE_IN_MOULD = 8224  # %MX4.0
COIL_C1_FULL = 8228  # %MX4.4
COIL_COMMLOST = 8235  # %MX5.3

SENSOR_COILS = tuple(range(8192, 8200)) + (COIL_BOTTLE_IN_MOULD, 8225, 8226, 8227, COIL_C1_FULL)  # %MX0.0-0.7, %MX4.0-4.4

START, RESET = 1, 3
MODE_STOP, MODE_AUTO, MODE_MANUAL = 0, 1, 2
OVEN_SETPOINT, OVEN_BAND = 1050, 50  # defaults (Oven_Ready window 1000-1100)
OVEN_HOT, OVEN_COLD = 1050, 900
RAW_35_BAR = 24192  # pressure left in a closed mould: from stage 5 on the restart waits in 60 EXHAUST
SETTLE = 0.3  # s: many scans of the 20 ms task
SOAK_MIN, SOAK_MAX = 0.9, 1.5  # s: window entry -> HOT with SoakTime 1 s (poll 50 ms, Modbus delays)


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

    def read(self, address: int) -> int:
        result = self.c.read_holding_registers(address, count=1)
        if result.isError():
            raise SystemExit(f"Read register {address} failed: {result}")
        return result.registers[0]

    def coil(self, address: int, value: bool | None = None) -> bool:
        if value is not None:
            self.c.write_coil(address, value)
            return value
        result = self.c.read_coils(address, count=1)
        if result.isError():
            raise SystemExit(f"Read coil {address} failed: {result}")
        return bool(result.bits[0])

    def steps(self) -> tuple[int, int]:
        result = self.c.read_holding_registers(HR_STEP, count=2)
        if result.isError():
            raise SystemExit(f"Read Step / MouldStep failed: {result}")
        return result.registers[0], result.registers[1]

    def cmd(self, value: int) -> None:
        self.write(HR_CMD, value)
        time.sleep(SETTLE)

    def mode(self, value: int) -> None:
        self.write(HR_MODE_REQ, value)
        time.sleep(SETTLE)

    def wait_step(self, expected: int, timeout: float) -> tuple[bool, list[int], float]:
        """Poll Step until it equals expected; returns (reached, distinct steps seen, seconds)."""
        t0 = time.monotonic()
        seen: list[int] = []
        while True:
            s = self.steps()[0]
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
            s = self.steps()[0]
            if not seen or seen[-1] != s:
                seen.append(s)
            time.sleep(0.05)
        return seen == [expected], seen

    def clear_sensors(self) -> None:
        for address in SENSOR_COILS:
            self.c.write_coil(address, False)


def main() -> int:
    claim_plant("test_blowmoulder_oven_seq", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_oven_seq")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)

    def reach(name: str, expected: int, timeout: float, what: str) -> tuple[bool, list[int], float]:
        ok, seen, dt = plc.wait_step(expected, timeout)
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})"))
        return ok, seen, dt

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        results.append(check(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})"))

    def start_with(name: str, sensors: dict[int, bool], expected: tuple[int, int], what: str) -> None:
        """Mode Stop -> both 0, set the sensors, Auto + Start, read both step words."""
        plc.mode(MODE_STOP)
        for address, value in sensors.items():
            plc.coil(address, value)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        steps = plc.steps()
        results.append(check(name, steps == expected, f"{what}, Start -> Step / MouldStep {steps} (expected {expected})"))

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address, value in ((HR_OVEN_TEMP, OVEN_COLD), (HR_OVEN_SETPOINT, OVEN_SETPOINT), (HR_OVEN_BAND, OVEN_BAND)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        # the sensor writes must reach the PLC: a debugger force would block them
        plc.coil(COIL_PREFORM_IN_OVEN, True)
        time.sleep(SETTLE)
        if not plc.coil(COIL_PREFORM_IN_OVEN):
            raise SystemExit("Preform_In_Oven does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_PREFORM_IN_OVEN, False)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        time.sleep(3.5)  # an Outfeed_Full left sealed in by C1_Full is released by the off-filter
        steps, lost = plc.steps(), plc.coil(COIL_COMMLOST)
        results.append(check("O1", steps == (0, 0) and not lost,
                             f"Mode Stop, heartbeat, Reset -> Step / MouldStep {steps}, Alm_CommLost {lost} "
                             "(expected (0, 0) / False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")

        plc.mode(MODE_AUTO)
        plc.coil(COIL_PREFORM_IN_OVEN, True)
        time.sleep(SETTLE)
        with_preform = plc.coil(COIL_LAMPS_ON)
        plc.coil(COIL_PREFORM_IN_OVEN, False)
        time.sleep(SETTLE)
        without = plc.coil(COIL_LAMPS_ON)
        results.append(check("O2", with_preform and not without,
                             f"Auto, IDLE: preform in the pocket -> Lamps_On {with_preform}, none -> {without} "
                             "(expected True / False)"))

        # ---------- one preform ----------
        plc.cmd(START)
        steps = plc.steps()
        results.append(check("O3", steps == (10, 10), f"Start, pocket empty, mould neither open nor closed -> "
                                                       f"Step / MouldStep {steps} (expected (10, 10))"))
        plc.coil(COIL_C1_FULL, True)
        time.sleep(2.5)  # on-filter 2 s: Outfeed_Full TRUE
        plc.coil(COIL_PREFORM_AT_LOADER, True)
        hold("O4", 10, 1.0, "C1 full (Outfeed_Full), preform at the loader")
        plc.coil(COIL_C1_FULL, False)
        reach("O5", 15, 4.5, "C1 free (off-filter 3 s)")
        plc.coil(COIL_PREFORM_AT_LOADER, False)
        plc.coil(COIL_PREFORM_IN_OVEN, True)
        ok, _, _ = plc.wait_step(20, 1)
        lamps = plc.coil(COIL_LAMPS_ON)
        results.append(check("O6", ok and lamps, f"preform in the pocket -> Step {plc.steps()[0]}, Lamps_On {lamps} "
                                                 "(expected 20 / True)"))
        hold("O7", 20, 1.5, "OvenTemp 90.0 degC (below the window 100.0-110.0)")
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        time.sleep(0.6)  # soak runs, but only 0.6 s of 1 s
        plc.write(HR_OVEN_TEMP, OVEN_COLD)  # out of the window: Soak_Timer restarts
        time.sleep(SETTLE)
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        ok, seen, dt = plc.wait_step(25, 3)
        results.append(check("O8", ok and SOAK_MIN <= dt <= SOAK_MAX and seen[0] == 20,
                             f"105.0 degC 0.6 s, out, in again -> 25 after {dt:.2f} s (expected {SOAK_MIN}-{SOAK_MAX} s: "
                             f"the soak restarted; seen {seen})"))
        plc.coil(COIL_PREFORM_IN_OVEN, False)
        ok, _, _ = plc.wait_step(10, 1)
        lamps = plc.coil(COIL_LAMPS_ON)
        results.append(check("O9", ok and not lamps, f"pocket empty (arm took the preform) -> Step {plc.steps()[0]}, "
                                                     f"Lamps_On {lamps} (expected 10 / False)"))

        # ---------- Start by sensors ----------
        plc.mode(MODE_STOP)
        plc.write(HR_RAW, RAW_35_BAR)
        steps = plc.steps()
        results.append(check("O10", steps == (0, 0), f"Mode Stop -> Step / MouldStep {steps} (expected (0, 0))"))
        start_with("O11", {COIL_PREFORM_IN_OVEN: True, COIL_MOULD_CLOSED: True}, (20, 60), "preform in the pocket, mould closed")
        start_with("O12", {COIL_MOULD_CLOSED: False, COIL_MOULD_OPEN: True, COIL_BOTTLE_IN_MOULD: True}, (20, 80),
                   "mould open with a bottle")
        start_with("O13", {COIL_BOTTLE_IN_MOULD: False}, (20, 10), "mould open, empty")
        start_with("O14", {COIL_MOULD_CLOSED: True, COIL_BOTTLE_IN_MOULD: True}, (20, 60),
                   "mould open AND closed with a bottle (sensor fault)")

        # ---------- guards ----------
        plc.mode(MODE_STOP)
        plc.clear_sensors()
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 10)
        plc.cmd(START)
        steps = plc.steps()
        results.append(check("O15", steps == (0, 10), f"MouldStep 10 written, Step 0, Start -> Step / MouldStep {steps} "
                                                      "(expected (0, 10): both must be IDLE)"))
        plc.mode(MODE_STOP)
        plc.mode(MODE_AUTO)
        plc.coil(COIL_PREFORM_IN_OVEN, True)
        plc.cmd(START)
        ok, _, _ = plc.wait_step(20, 1)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        steps, lamps = plc.steps(), plc.coil(COIL_LAMPS_ON)
        results.append(check("O16", ok and steps == (0, 0) and not lamps,
                             f"E_Stop_OK FALSE in HEAT -> Step / MouldStep {steps}, Lamps_On {lamps} "
                             "(expected (0, 0) / False)"))
        plc.coil(COIL_E_STOP_OK, True)
        plc.mode(MODE_MANUAL)
        lamps = plc.coil(COIL_LAMPS_ON)
        results.append(check("O17", not lamps, f"Manual, preform in the pocket -> Lamps_On {lamps} (expected False)"))

        plc.mode(MODE_AUTO)
        plc.write(HR_STEP, 900)
        plc.write(HR_MOULD_STEP, 900)
        time.sleep(SETTLE)
        lamps = plc.coil(COIL_LAMPS_ON)
        plc.cmd(START)
        in_auto = plc.steps()
        plc.mode(MODE_STOP)
        after_stop = plc.steps()
        results.append(check("O18", not lamps and in_auto == (900, 900) and after_stop == (900, 900),
                             f"900 written in Auto: Lamps_On {lamps}, Start -> {in_auto}, Mode Stop -> {after_stop} "
                             "(expected False / (900, 900) / (900, 900): FAULT kept until Reset)"))
    finally:
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
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, both steps 0, sensors FALSE, OvenTemp and raw 0, "
          "E_Stop_OK as before)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
