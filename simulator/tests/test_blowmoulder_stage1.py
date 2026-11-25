"""Blow moulder ladder stage 1: modes and safety (first-scan defaults, Cmd clear, heartbeat watchdog).

Runs against the blow moulder PLC on port 5025. Checks:
    B1 setpoint defaults in HR 1054-1058 (written once at the first scan after the upload)
    B2 a setpoint written by the HMI is kept (the default rung does not run again)
    B3 Cmd = 1, 2, 3 and 4 are each cleared to 0 by the PLC (a command acts for one scan only)
    B4 heartbeat toggling, Reset -> Alm_CommLost FALSE
    B5 heartbeat toggling 5 s -> Alm_CommLost stays FALSE
    B6 heartbeat frozen at FALSE -> Alm_CommLost TRUE about 3 s after its last change
    B7 Reset while the heartbeat is still dead -> Alm_CommLost stays TRUE (cause not gone)
    B8 heartbeat frozen at TRUE -> Alm_CommLost TRUE about 3 s after its last change too
       (the watchdog must look at the CHANGE, Hb_Changed, not at the heartbeat value)

    uv run python tests/test_blowmoulder_stage1.py

B1 holds only right after an upload (or a PLC restart): a setpoint changed since then keeps
its new value, which is correct behaviour - the script then prints which ones differ.
Cmd = 1 (Start) and 4 (reset counter) change nothing yet: the sequencers and BottleCount come later.
Master_Enable has no address: check it in the debugger (E_Stop_OK FALSE -> Master_Enable FALSE).
Release all debugger forces first; do not run the simulator at the same time (both write the heartbeat).
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5025

HR_CMD = 1024  # %MW0
HR_SETPOINTS = 1054  # %MW30..34
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_COMMLOST = 8235  # %MX5.3

DEFAULTS = {"OvenSetpoint": 1050, "OvenBand": 50, "Kp": 10, "Bias": 0, "BlowSetpoint": 350}  # %MW30..34
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
    """Toggles %MX1.2 every 0.5 s in a thread with its own client (a pymodbus client is not thread-safe).
    stop(value) leaves the bit frozen at that value, so both frozen cases can be tested."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._value = False
        self.last_change = time.monotonic()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, freeze_at: bool | None = None) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None
        if freeze_at is not None and freeze_at != self._value:
            client = connect()  # one last toggle, so the bit ends at the wanted value
            client.write_coil(COIL_HEARTBEAT, freeze_at)
            client.close()
            self._value = freeze_at
            self.last_change = time.monotonic()

    def _run(self) -> None:
        client = connect()
        try:
            while not self._stop.is_set():
                self._value = not self._value
                client.write_coil(COIL_HEARTBEAT, self._value)
                self.last_change = time.monotonic()
                self._stop.wait(0.5)
        finally:
            client.close()


def wait_for_alarm(client: ModbusTcpClient, last_change: float) -> float | None:
    """Seconds from the heartbeat's last change until Alm_CommLost is TRUE, None if not within 6 s."""
    while time.monotonic() - last_change < 6:
        if read_coil(client, COIL_COMMLOST):
            return time.monotonic() - last_change
        time.sleep(0.05)
    return None


def alarm_check(name: str, latched_after: float | None, frozen: bool) -> bool:
    ok = latched_after is not None and 2.8 <= latched_after <= 3.6
    detail = (f"heartbeat frozen at {frozen}: Alm_CommLost TRUE {latched_after:.2f} s after the last change "
              "(expected ~3 s)" if latched_after is not None else
              f"heartbeat frozen at {frozen}: Alm_CommLost not TRUE within 6 s")
    return check(name, ok, detail)


def reset_with_heartbeat(client: ModbusTcpClient, heartbeat: Heartbeat) -> bool:
    """Heartbeat running, then Reset: returns Alm_CommLost afterwards (FALSE = cleared)."""
    heartbeat.start()
    time.sleep(1.5)
    client.write_register(HR_CMD, RESET)
    time.sleep(0.3)
    return read_coil(client, COIL_COMMLOST)


def main() -> int:
    claim_plant("test_blowmoulder_stage1", HOST, PORT)  # writes the heartbeat: never with the simulator
    warn_if_fuxa("test_blowmoulder_stage1")
    client = connect()
    heartbeat = Heartbeat()
    results = []
    try:
        # B1 defaults
        values = dict(zip(DEFAULTS, read_hr(client, HR_SETPOINTS, len(DEFAULTS))))
        wrong = {n: v for n, v in values.items() if v != DEFAULTS[n]}
        results.append(check("B1", not wrong, "all 4 defaults present" if not wrong else
                             f"differ: {wrong} (changed since the upload? restart the PLC to re-run the first scan)"))

        # B2 a later write is kept
        client.write_register(HR_SETPOINTS, 1000)
        time.sleep(0.5)  # many scans (20 ms task)
        kept = read_hr(client, HR_SETPOINTS)[0]
        client.write_register(HR_SETPOINTS, DEFAULTS["OvenSetpoint"])
        results.append(check("B2", kept == 1000, f"OvenSetpoint written 1000, after 0.5 s = {kept} (set back to 1050)"))

        # B3 every command cleared
        left = {}
        for cmd in (1, 2, 3, 4):
            client.write_register(HR_CMD, cmd)
            time.sleep(0.2)
            left[cmd] = read_hr(client, HR_CMD)[0]
        results.append(check("B3", all(v == 0 for v in left.values()),
                             f"Cmd written 1 / 2 / 3 / 4, after 0.2 s = {' / '.join(map(str, left.values()))}"))

        # B4 heartbeat running + Reset clears the alarm
        lost = reset_with_heartbeat(client, heartbeat)
        results.append(check("B4", not lost, f"heartbeat running, Reset -> Alm_CommLost = {lost}"))

        # B5 stays clear while the heartbeat runs
        stayed = True
        end = time.monotonic() + 5
        while time.monotonic() < end:
            stayed &= not read_coil(client, COIL_COMMLOST)
            time.sleep(0.2)
        results.append(check("B5", stayed, "Alm_CommLost stayed FALSE for 5 s" if stayed else "Alm_CommLost went TRUE"))

        # B6 heartbeat frozen at FALSE -> alarm after ~3 s
        heartbeat.stop(freeze_at=False)
        results.append(alarm_check("B6", wait_for_alarm(client, heartbeat.last_change), frozen=False))

        # B7 Reset while the cause is still there
        client.write_register(HR_CMD, RESET)
        time.sleep(0.3)
        lost = read_coil(client, COIL_COMMLOST)
        results.append(check("B7", lost, f"heartbeat dead, Reset -> Alm_CommLost = {lost} (must stay TRUE)"))

        # B8 heartbeat frozen at TRUE -> alarm after ~3 s as well
        if reset_with_heartbeat(client, heartbeat):
            results.append(check("B8", False, "could not clear Alm_CommLost before the test (see B4)"))
        else:
            heartbeat.stop(freeze_at=True)
            results.append(alarm_check("B8", wait_for_alarm(client, heartbeat.last_change), frozen=True))
    finally:
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    print("Debugger (127.0.0.4): force E_Stop_OK FALSE -> Master_Enable FALSE; then release all forces.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
