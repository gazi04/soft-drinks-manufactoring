"""Syrup room ladder stage 5: outputs, interlocks, manual mode, jog watchdog.

Runs against the syrup PLC on port 5022. The outputs %QX0.0-0.5 are read as coils 0-5.
Every check compares the COMPLETE set of outputs that are ON, so an output that switches
on in the wrong step is caught too. Heartbeat kept alive in a thread, E_Stop_OK TRUE, Reset first.

Auto (the script writes Step directly; the sensors are chosen so the step does not move on):
    O1  10 DOSE_WATER  -> WaterPump            O2  20 DOSE_SYRUP -> SyrupPump
    O3  30 MIX         -> Agitator             O4  40 COOL (warm) -> Agitator + CoolingValve
    O5  50 CARBONATE, cold, pressure low       -> Agitator + CO2Valve (cooling off: Temp_OK)
    O6  55 WAIT_BUFFER, same                   -> Agitator + CO2Valve
    O7  55 warm again                          -> Agitator + CoolingValve, CO2 off (interlock Temp_OK)
    O8  60 TRANSFER    -> TransferPump         O9  900 FAULT, O10 0 IDLE -> nothing
    O11 30 MIX with MixLevel 50                -> nothing (agitator interlock)
    O12 10 with MixHighHigh TRUE               -> nothing (high-high switch alone stops the pump)
    O13 10, E_Stop_OK FALSE                    -> nothing
Manual (ModeReq 2, hold-to-run, interlocks still apply):
    M1-M3  Man_WaterPump: 300 L on, 950 L off (MixMax), MixHighHigh off
    M4-M5  Man_SyrupPump: 949 L on, 950 L off
    M6-M7  Man_Agitator: 100 L on, 99 L off (AgitatorMin)
    M8     Man_Cooling: on (no interlock)
    M9-M12 Man_CO2: warm off, cold on, 6.00 bar off, 5.99 bar on
    M13-M16 Man_Transfer: 5 L off (MixEmpty), 6 L on, buffer 1950 off, 1949 on
    M17    Auto mode: a held Man_ button does nothing
Jog watchdog:
    J1 Man_Cooling held 11 s -> Jog_Block TRUE, CoolingValve off
    J2 released -> Jog_Block FALSE;  J3 pressed again -> CoolingValve on

    uv run python tests/test_syrup_outputs.py      (~25 s)

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
HR_STEP = 1075  # %MW51
COIL_MIX_HIGH_HIGH = 8192  # %MX0.0
COIL_E_STOP_OK = 8201  # %MX1.1
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_MAN = {"Man_WaterPump": 8208, "Man_SyrupPump": 8209, "Man_Agitator": 8210,  # %MX2.0-2.5
            "Man_Cooling": 8211, "Man_CO2": 8212, "Man_Transfer": 8213}
COIL_JOG_BLOCK = 8218  # %MX3.2
COIL_COMMLOST = 8235  # %MX5.3
OUTPUTS = ["WaterPump", "SyrupPump", "Agitator", "CoolingValve", "CO2Valve", "TransferPump"]  # coils 0-5

RESET = 3
MODE_STOP, MODE_AUTO, MODE_MANUAL = 0, 1, 2
SETTLE = 0.3  # s: many scans of the 20 ms task


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


def main() -> int:
    claim_plant("test_syrup_outputs", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_outputs")
    client = connect()
    heartbeat = Heartbeat()
    results: list[bool] = []

    def write(address: int, value: int) -> None:
        client.write_register(address, value)

    def coil(address: int, value: bool | None = None) -> bool:
        if value is not None:
            client.write_coil(address, value)
            return value
        result = client.read_coils(address, count=1)
        if result.isError():
            raise SystemExit(f"Read coil {address} failed: {result}")
        return bool(result.bits[0])

    def outputs_on() -> set[str]:
        result = client.read_coils(0, count=len(OUTPUTS))
        if result.isError():
            raise SystemExit(f"Read outputs failed: {result}")
        return {name for name, bit in zip(OUTPUTS, result.bits) if bit}

    def expect(name: str, expected: set[str], what: str) -> None:
        time.sleep(SETTLE)
        got = outputs_on()
        ok = got == expected
        results.append(ok)
        show = lambda s: "{" + ", ".join(sorted(s)) + "}" if s else "nothing"
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {what} -> {show(got)}" + ("" if ok else f" (expected {show(expected)})"))

    def sensors(mix: int | None = None, temp: int | None = None, pressure: int | None = None, buffer: int | None = None) -> None:
        for address, value in ((HR_MIX_LEVEL, mix), (HR_PRODUCT_TEMP, temp), (HR_CO2_PRESSURE, pressure), (HR_BUFFER_LEVEL, buffer)):
            if value is not None:
                write(address, value)

    def step(value: int) -> None:
        write(HR_STEP, value)

    def press(button: str, value: bool = True) -> None:
        coil(COIL_MAN[button], value)
        if not value:
            time.sleep(0.1)  # a few scans with no button: Jog_TON restarts, it must not add up across buttons

    try:
        # ---------- setup ----------
        write(HR_MODE_REQ, MODE_STOP)
        for button in COIL_MAN:
            press(button, False)
        coil(COIL_MIX_HIGH_HIGH, False)
        coil(COIL_E_STOP_OK, True)
        write(HR_SYRUP_LEVEL, 1000)
        sensors(mix=300, temp=200, pressure=400, buffer=1700)
        heartbeat.start()
        time.sleep(1.0)
        write(HR_CMD, RESET)
        time.sleep(SETTLE)
        if coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE after Reset: Master_Enable is off, all outputs stay off")

        # ---------- Auto: each step switches exactly its outputs ----------
        write(HR_MODE_REQ, MODE_AUTO)
        time.sleep(SETTLE)
        step(10)
        expect("O1", {"WaterPump"}, "Auto step 10, MixLevel 300")
        step(20)
        expect("O2", {"SyrupPump"}, "Auto step 20, MixLevel 300")
        step(30)
        expect("O3", {"Agitator"}, "Auto step 30")
        step(40)
        expect("O4", {"Agitator", "CoolingValve"}, "Auto step 40, 20.0 degC")
        sensors(temp=40, pressure=300)
        step(50)
        expect("O5", {"Agitator", "CO2Valve"}, "Auto step 50, 4.0 degC, 3.00 bar")
        step(55)
        expect("O6", {"Agitator", "CO2Valve"}, "Auto step 55 (buffer 1700 + 300 > 1950), same")
        sensors(temp=200)
        expect("O7", {"Agitator", "CoolingValve"}, "Auto step 55, warm again 20.0 degC")
        sensors(pressure=400)
        step(60)
        expect("O8", {"TransferPump"}, "Auto step 60, MixLevel 300, buffer 1700")
        step(900)
        expect("O9", set(), "Auto step 900 FAULT")
        step(0)
        expect("O10", set(), "Auto step 0 IDLE")
        sensors(mix=50)
        step(30)
        expect("O11", set(), "Auto step 30, MixLevel 50 < AgitatorMin")
        sensors(mix=300)
        coil(COIL_MIX_HIGH_HIGH, True)
        step(10)
        expect("O12", set(), "Auto step 10, MixLevel 300 but MixHighHigh TRUE")
        coil(COIL_MIX_HIGH_HIGH, False)
        expect("O12b", {"WaterPump"}, "Auto step 10, MixHighHigh FALSE again")
        coil(COIL_E_STOP_OK, False)
        expect("O13", set(), "Auto step 10, E_Stop_OK FALSE")
        coil(COIL_E_STOP_OK, True)

        # ---------- Manual: hold-to-run, interlocks still apply ----------
        write(HR_MODE_REQ, MODE_MANUAL)
        press("Man_WaterPump")
        expect("M1", {"WaterPump"}, "Manual Man_WaterPump, MixLevel 300")
        sensors(mix=950)
        expect("M2", set(), "Manual Man_WaterPump, MixLevel 950 = MixMax")
        sensors(mix=300)
        coil(COIL_MIX_HIGH_HIGH, True)
        expect("M3", set(), "Manual Man_WaterPump, MixHighHigh TRUE")
        coil(COIL_MIX_HIGH_HIGH, False)
        press("Man_WaterPump", False)

        press("Man_SyrupPump")
        sensors(mix=949)
        expect("M4", {"SyrupPump"}, "Manual Man_SyrupPump, MixLevel 949")
        sensors(mix=950)
        expect("M5", set(), "Manual Man_SyrupPump, MixLevel 950")
        press("Man_SyrupPump", False)

        press("Man_Agitator")
        sensors(mix=100)
        expect("M6", {"Agitator"}, "Manual Man_Agitator, MixLevel 100 = AgitatorMin")
        sensors(mix=99)
        expect("M7", set(), "Manual Man_Agitator, MixLevel 99")
        press("Man_Agitator", False)

        press("Man_Cooling")
        expect("M8", {"CoolingValve"}, "Manual Man_Cooling (no interlock)")
        press("Man_Cooling", False)

        press("Man_CO2")
        sensors(temp=200, pressure=300)
        expect("M9", set(), "Manual Man_CO2, 20.0 degC (NOT Temp_OK)")
        sensors(temp=40)
        expect("M10", {"CO2Valve"}, "Manual Man_CO2, 4.0 degC, 3.00 bar")
        sensors(pressure=600)
        expect("M11", set(), "Manual Man_CO2, 6.00 bar = PressMax")
        sensors(pressure=599)
        expect("M12", {"CO2Valve"}, "Manual Man_CO2, 5.99 bar")
        press("Man_CO2", False)

        press("Man_Transfer")
        sensors(mix=5, buffer=1700)
        expect("M13", set(), "Manual Man_Transfer, MixLevel 5 = MixEmpty")
        sensors(mix=6)
        expect("M14", {"TransferPump"}, "Manual Man_Transfer, MixLevel 6")
        sensors(buffer=1950)
        expect("M15", set(), "Manual Man_Transfer, buffer 1950 = BufferMax")
        sensors(buffer=1949)
        expect("M16", {"TransferPump"}, "Manual Man_Transfer, buffer 1949")
        press("Man_Transfer", False)

        write(HR_MODE_REQ, MODE_AUTO)
        sensors(mix=300)
        press("Man_WaterPump")
        expect("M17", set(), "Auto mode (step 0), Man_WaterPump held")
        press("Man_WaterPump", False)

        # ---------- jog watchdog ----------
        write(HR_MODE_REQ, MODE_MANUAL)
        time.sleep(SETTLE)
        press("Man_Cooling")
        time.sleep(11)
        blocked = coil(COIL_JOG_BLOCK)
        results.append(blocked)
        print(f"{'PASS' if blocked else 'FAIL'}  J1: Man_Cooling held 11 s -> Jog_Block = {blocked} (expected True)")
        expect("J1b", set(), "Man_Cooling still held, Jog_Block")
        press("Man_Cooling", False)
        time.sleep(SETTLE)
        blocked = coil(COIL_JOG_BLOCK)
        results.append(not blocked)
        print(f"{'PASS' if not blocked else 'FAIL'}  J2: all buttons released -> Jog_Block = {blocked} (expected False)")
        press("Man_Cooling")
        expect("J3", {"CoolingValve"}, "Man_Cooling pressed again")
        press("Man_Cooling", False)
    finally:
        for button in COIL_MAN:
            press(button, False)
        coil(COIL_MIX_HIGH_HIGH, False)
        write(HR_MODE_REQ, MODE_STOP)
        for address in (HR_SYRUP_LEVEL, HR_MIX_LEVEL, HR_PRODUCT_TEMP, HR_CO2_PRESSURE, HR_BUFFER_LEVEL):
            write(address, 0)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (buttons released, Mode Stop, sensors 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
