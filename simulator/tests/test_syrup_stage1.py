"""Syrup room ladder stage 1: modes and safety (first-scan defaults, Cmd clear, heartbeat watchdog).

Runs against the syrup PLC on port 5022. Checks:
    A1 setpoint defaults in HR 1054-1063 (written once at the first scan after the upload)
    A2 a setpoint written by the HMI is kept (the default rung does not run again)
    A3 Cmd = 3 is cleared to 0 by the PLC (command acts for one scan only)
    A4 heartbeat toggling, Reset -> Alm_CommLost FALSE
    A5 heartbeat toggling 5 s -> Alm_CommLost stays FALSE
    A6 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    A7 Reset while the heartbeat is still dead -> Alm_CommLost stays TRUE (cause not gone)

    uv run python tests/test_syrup_stage1.py

A1 holds only right after an upload (or a PLC restart): a setpoint changed since then keeps
its new value, which is correct behaviour - the script then prints which ones differ.
Master_Enable has no address: check it in the debugger (E_Stop_OK FALSE -> Master_Enable FALSE).
Release all debugger forces first; do not run the simulator at the same time (both write the heartbeat).
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5022

HR_CMD = 1024  # %MW0
HR_SETPOINTS = 1054  # %MW30..39
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_COMMLOST = 8235  # %MX5.3

DEFAULTS = {  # name: default, in register order %MW30..39
    "WaterBatch": 500, "SyrupRatio": 55, "MixTime": 20, "CoolSetpoint": 40, "TempHyst": 5,
    "PressSetpoint": 350, "PressHyst": 20, "BufferLow": 800, "BufferMin": 100, "CarbTime": 10,
}
RESET = 3


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


def read_hr(client: ModbusTcpClient, address: int, count: int = 1) -> list[int]:
    result = client.read_holding_registers(address, count=count)
    if result.isError():
        raise SystemExit(f"Read HR {address} failed: {result}")
    return result.registers


def read_coil(client: ModbusTcpClient, address: int) -> bool:
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Read coil {address} failed: {result}")
    return bool(result.bits[0])


class Heartbeat:
    """Toggles %MX1.2 every 0.5 s in a thread with its own client (a pymodbus client is not thread-safe)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_change = time.monotonic()

    def start(self) -> None:
        self._stop.clear()
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
                self.last_change = time.monotonic()
                self._stop.wait(0.5)
        finally:
            client.close()


def main() -> int:
    claim_plant("test_syrup_stage1", HOST, PORT)  # writes the heartbeat: never with the simulator
    warn_if_fuxa("test_syrup_stage1")
    client = connect()
    heartbeat = Heartbeat()
    results = []
    try:
        # A1 defaults
        values = dict(zip(DEFAULTS, read_hr(client, HR_SETPOINTS, len(DEFAULTS))))
        wrong = {n: v for n, v in values.items() if v != DEFAULTS[n]}
        results.append(check("A1", not wrong, "all 10 defaults present" if not wrong else
                             f"differ: {wrong} (changed since the upload? restart the PLC to re-run the first scan)"))

        # A2 a later write is kept
        client.write_register(HR_SETPOINTS, 600)
        time.sleep(0.5)  # many scans (20 ms task)
        kept = read_hr(client, HR_SETPOINTS)[0]
        client.write_register(HR_SETPOINTS, DEFAULTS["WaterBatch"])
        results.append(check("A2", kept == 600, f"WaterBatch written 600, after 0.5 s = {kept} (set back to 500)"))

        # A3 command cleared
        client.write_register(HR_CMD, RESET)
        time.sleep(0.2)
        cmd = read_hr(client, HR_CMD)[0]
        results.append(check("A3", cmd == 0, f"Cmd written 3, after 0.2 s = {cmd}"))

        # A4 heartbeat running + Reset clears the alarm
        heartbeat.start()
        time.sleep(1.5)
        client.write_register(HR_CMD, RESET)
        time.sleep(0.3)
        lost = read_coil(client, COIL_COMMLOST)
        results.append(check("A4", not lost, f"heartbeat running, Reset -> Alm_CommLost = {lost}"))

        # A5 stays clear while the heartbeat runs
        stayed = True
        end = time.monotonic() + 5
        while time.monotonic() < end:
            stayed &= not read_coil(client, COIL_COMMLOST)
            time.sleep(0.2)
        results.append(check("A5", stayed, "Alm_CommLost stayed FALSE for 5 s" if stayed else "Alm_CommLost went TRUE"))

        # A6 heartbeat stops -> alarm after ~3 s
        heartbeat.stop()
        last = heartbeat.last_change
        latched_after = None
        while time.monotonic() - last < 6:
            if read_coil(client, COIL_COMMLOST):
                latched_after = time.monotonic() - last
                break
            time.sleep(0.05)
        ok = latched_after is not None and 2.8 <= latched_after <= 3.6
        results.append(check("A6", ok, f"Alm_CommLost TRUE {latched_after:.2f} s after the last change (expected ~3 s)"
                             if latched_after is not None else "Alm_CommLost not TRUE within 6 s"))

        # A7 Reset while the cause is still there
        client.write_register(HR_CMD, RESET)
        time.sleep(0.3)
        lost = read_coil(client, COIL_COMMLOST)
        results.append(check("A7", lost, f"heartbeat dead, Reset -> Alm_CommLost = {lost} (must stay TRUE)"))
    finally:
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    print("Debugger (127.0.0.2): force E_Stop_OK FALSE -> Master_Enable FALSE; then release all forces.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
