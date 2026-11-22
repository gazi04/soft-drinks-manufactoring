"""Packer ladder stage 6: the oven (heater, blower TOF, Oven_Counter CTUD, oven conveyor).

Runs against the packer PLC on port 5024. The oven runs in Auto without a Start (Oven_On =
Master_Enable AND Auto AND NOT Step_Fault), so the sequencers stay in IDLE here. The script keeps
the heartbeat alive, sets E_Stop_OK and the oven setpoints (1900 / 50 / 150: heater on below 1850,
off at 1900), writes OvenTemp / Airflow_OK / Oven_Entry / Oven_Exit and reads the three outputs.

    O0  start: PacksInOven back to 0 (Oven_Exit pulses, the CTUD stops at 0), blower run-on over
    O1  Mode Stop -> heater, blower, oven conveyor OFF
    O2  Auto, OvenTemp 180.0 degC, Airflow_OK -> heater, blower, conveyor ON
    O3  Airflow_OK FALSE -> heater OFF, blower ON (airflow interlock)
    O4  air back, OvenTemp 190.0 -> heater OFF (Heat_Demand reset);  O5  187.0 -> still OFF (hysteresis)
    O6  184.0 -> heater ON again
    O7  OvenSetpoint 2500 (Heat_Demand set), OvenTemp 235.0 -> heater OFF (Oven_BelowMax);  O8  229.0 -> ON
    O9  Mode Stop -> heater OFF at once, blower still ON (run-on), conveyor OFF (oven empty)
    O10 2 Oven_Entry pulses -> PacksInOven 2, conveyor ON in Mode Stop
    O11 1 Oven_Exit -> 1, still ON;  O12 2nd -> 0, conveyor OFF
    O13 extra Oven_Exit at 0 -> stays 0 (CTUD never below 0)
    O14 blower still ON 29 s after Mode Stop;  O15 OFF 31 s after it (TOF BlowerRunOn 30 s)
    O16 Auto again, 1 pack inside, Step 900 written (FAULT) -> heater + conveyor OFF, blower ON (run-on)
    O17 Step 0, E_Stop_OK FALSE -> all three OFF at once, the blower too (Master_Enable)

    uv run python tests/test_packer_oven_outputs.py      (~50 s, up to 30 s more when the blower runs on at the start)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_packer_collation import (COIL_E_STOP_OK, HR_MODE_REQ, HR_OVEN_BAND, HR_OVEN_HYST, HR_OVEN_SETPOINT,
                                   HR_OVEN_TEMP, HR_STEP, HR_WRAP_STEP, MODE_AUTO, MODE_STOP, OVEN_BAND, OVEN_HYST,
                                   OVEN_SETPOINT, RESET, SETTLE, Heartbeat, Plc, check, connect)

HOST = "127.0.0.1"
PORT = 5024

HR_PACKS_IN_OVEN = 1065  # %MW41
COIL_OVEN_ENTRY = 8226  # %MX4.2
COIL_OVEN_EXIT = 8227  # %MX4.3
COIL_AIRFLOW_OK = 8229  # %MX4.5
COIL_COMMLOST = 8235  # %MX5.3
COIL_HEATER = 7  # %QX0.7 OvenHeater
COIL_BLOWER = 8  # %QX1.0 OvenBlower
COIL_OVEN_CONV = 9  # %QX1.1 OvenConveyor
RUN_ON = 30.0  # s, BlowerRunOn


def outputs(plc: Plc) -> tuple[bool, bool, bool]:
    """(heater, blower, oven conveyor) from the %QX outputs."""
    result = plc.c.read_coils(COIL_HEATER, count=3)
    if result.isError():
        raise SystemExit(f"Read outputs failed: {result}")
    return bool(result.bits[0]), bool(result.bits[1]), bool(result.bits[2])


def fmt(out: tuple[bool, bool, bool]) -> str:
    return "heater {} blower {} conveyor {}".format(*("ON" if o else "off" for o in out))


def empty_oven(plc: Plc) -> int:
    """Oven_Exit pulses until PacksInOven is 0 (no reset input before stage 9). Returns the final count."""
    for _ in range(100):
        if plc.read(HR_PACKS_IN_OVEN) == 0:
            break
        plc.pulse(COIL_OVEN_EXIT)
    return plc.read(HR_PACKS_IN_OVEN)


def main() -> int:
    claim_plant("test_packer_oven_outputs", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_oven_outputs")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def expect(name: str, want: tuple[bool, bool, bool], what: str) -> bool:
        got = outputs(plc)
        return results.append(check(name, got == want, f"{what} -> {fmt(got)} (expected {fmt(want)})")) or got == want

    def temp(value: int) -> None:
        plc.write(HR_OVEN_TEMP, value)
        time.sleep(SETTLE)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        for address in (COIL_OVEN_ENTRY, COIL_OVEN_EXIT):
            plc.coil(address, False)
        plc.coil(COIL_AIRFLOW_OK, True)
        for address, value in ((HR_OVEN_TEMP, 1800), (HR_OVEN_SETPOINT, OVEN_SETPOINT),
                               (HR_OVEN_HYST, OVEN_HYST), (HR_OVEN_BAND, OVEN_BAND)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_OVEN_EXIT, True)  # the sensor writes must reach the PLC: a debugger force would block them
        time.sleep(SETTLE)
        if not plc.coil(COIL_OVEN_EXIT):
            raise SystemExit("Oven_Exit does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_OVEN_EXIT, False)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset
        plc.cmd(RESET)
        for address in (HR_STEP, HR_WRAP_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the outputs cannot run")
        count = empty_oven(plc)
        end = time.monotonic() + RUN_ON + 2
        while outputs(plc)[1] and time.monotonic() < end:  # a blower still on its run-on from an earlier Auto
            time.sleep(0.5)
        results.append(check("O0", count == 0 and not outputs(plc)[1],
                             f"Oven_Exit pulses -> PacksInOven {count}, blower {outputs(plc)[1]} (expected 0 / False)"))

        expect("O1", (False, False, False), "Mode Stop, OvenTemp 180.0 degC, Airflow_OK")
        plc.mode(MODE_AUTO)
        expect("O2", (True, True, True), "Auto (no Start)")
        plc.coil(COIL_AIRFLOW_OK, False)
        time.sleep(SETTLE)
        expect("O3", (False, True, True), "Airflow_OK FALSE")
        plc.coil(COIL_AIRFLOW_OK, True)
        temp(1900)
        expect("O4", (False, True, True), "air back, OvenTemp 190.0 degC (setpoint reached)")
        temp(1870)
        expect("O5", (False, True, True), "OvenTemp 187.0 degC (inside the hysteresis band)")
        temp(1840)
        expect("O6", (True, True, True), "OvenTemp 184.0 degC (below 185.0)")
        plc.write(HR_OVEN_SETPOINT, 2500)
        temp(2350)
        expect("O7", (False, True, True), "OvenSetpoint 250.0, OvenTemp 235.0 degC (>= OvenMax 230.0)")
        temp(2290)
        expect("O8", (True, True, True), "OvenTemp 229.0 degC (below OvenMax again)")
        plc.write(HR_OVEN_SETPOINT, OVEN_SETPOINT)
        temp(1800)

        plc.mode(MODE_STOP)
        t_stop = time.monotonic() - SETTLE  # plc.mode() waits SETTLE after the write
        expect("O9", (False, True, False), "Mode Stop (oven empty)")
        plc.pulse(COIL_OVEN_ENTRY, 2)
        n = plc.read(HR_PACKS_IN_OVEN)
        conv = outputs(plc)[2]
        results.append(check("O10", n == 2 and conv, f"2 Oven_Entry pulses -> PacksInOven {n}, conveyor {conv} "
                                                     "(expected 2 / True)"))
        plc.pulse(COIL_OVEN_EXIT)
        n, conv = plc.read(HR_PACKS_IN_OVEN), outputs(plc)[2]
        results.append(check("O11", n == 1 and conv, f"1 Oven_Exit -> PacksInOven {n}, conveyor {conv} (expected 1 / True)"))
        plc.pulse(COIL_OVEN_EXIT)
        n, conv = plc.read(HR_PACKS_IN_OVEN), outputs(plc)[2]
        results.append(check("O12", n == 0 and not conv, f"2nd Oven_Exit -> PacksInOven {n}, conveyor {conv} "
                                                         "(expected 0 / False)"))
        plc.pulse(COIL_OVEN_EXIT)
        n = plc.read(HR_PACKS_IN_OVEN)
        results.append(check("O13", n == 0, f"extra Oven_Exit at 0 -> PacksInOven {n} (expected 0, never negative)"))

        time.sleep(max(0.0, t_stop + RUN_ON - 1.0 - time.monotonic()))
        blower = outputs(plc)[1]
        results.append(check("O14", blower, f"{time.monotonic() - t_stop:.1f} s after Mode Stop -> blower {blower} "
                                            "(expected True, run-on 30 s)"))
        time.sleep(max(0.0, t_stop + RUN_ON + 1.0 - time.monotonic()))
        blower = outputs(plc)[1]
        results.append(check("O15", not blower, f"{time.monotonic() - t_stop:.1f} s after Mode Stop -> blower "
                                                f"{blower} (expected False)"))

        plc.mode(MODE_AUTO)
        plc.pulse(COIL_OVEN_ENTRY)
        expect("O16a", (True, True, True), "Auto, OvenTemp 180.0, 1 pack inside")
        plc.write(HR_STEP, 900)
        plc.write(HR_WRAP_STEP, 900)
        time.sleep(SETTLE)
        expect("O16", (False, True, False), "Step / WrapStep 900 (FAULT, written), 1 pack inside")
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        expect("O17", (False, False, False), "E_Stop_OK FALSE in Auto, 1 pack inside")
    finally:
        # end state for the next script: E_Stop_OK TRUE, PacksInOven 0, steps 0, setpoint 1900, Mode Stop
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.coil(COIL_E_STOP_OK, True)
        plc.write(HR_OVEN_SETPOINT, OVEN_SETPOINT)
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        empty_oven(plc)
        for address in (COIL_OVEN_ENTRY, COIL_OVEN_EXIT, COIL_AIRFLOW_OK):
            plc.coil(address, False)
        plc.write(HR_OVEN_TEMP, 0)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (PacksInOven 0, Mode Stop, E_Stop_OK TRUE, OvenTemp 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
