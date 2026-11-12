"""Syrup room ladder stage 7: alarms and FAULT.

Runs against the syrup PLC on port 5022. Plays the plant by writing the sensors (helpers from
test_syrup_sequencer.py) and watches Step (%MW51), State (%MW50), the alarm bits %MX5.0-5.5
(coils 8232-8237) and the outputs %QX0.0-0.5 (coils 0-5). MixTime / CarbTime shortened to 2 s.

    N1  one normal batch (dose, mix, cool, carbonate, transfer): no alarm bit ever TRUE, never Step 900
Overflow (Alm_Overflow, FAULT, latched, Reset only when the cause is gone):
    O1  MixLevel 960 >= MixMax 950 -> Step 900, State 9, all outputs off
    O2  Reset while still 960 -> ignored (alarm and Step 900 stay)
    O3  MixLevel 900 + Reset -> Step 0, alarm cleared
    O4  MixHighHigh TRUE at MixLevel 500 (stuck level sensor) -> FAULT; released + Reset -> 0
    O5  BufferLevel 1950 = BufferMax -> FAULT; 1949 + Reset -> 0
Over-pressure (Alm_OverPressure):
    P1  CO2Pressure 599 -> no alarm;  P2  600 = PressMax -> FAULT, CO2Valve off
    P3  Reset at 600 ignored;         P4  590 + Reset -> 0
Resume after a FAULT:
    R1  FAULT in MIX (MixHighHigh) -> Reset -> Start resumes at MIX (Batch_Dosed), never DOSE_WATER
Low syrup (Alm_LowSyrup, warning only, not latched):
    L1  batch needed, SyrupLevel 50 -> Step stays 5 (no FAULT), State 2, Alm_LowSyrup TRUE
    L2  syrup refilled -> Alm_LowSyrup FALSE by itself (no Reset), Step 10
Timeouts (slow, left out with --quick: CoolTimeout / DoseTimeout are 60 s constants without address):
    D1  DOSE_WATER, level held at 300 L -> Alm_DoseTimeout after DoseTimeout 60 s (not before 55 s)
    D2  Reset -> Step 0, alarm cleared (Water_Timer reset in FAULT)
    C1  COOL, product held warm -> Alm_CoolTimeout after CoolTimeout 60 s, all outputs off
    C2  cold again + Reset -> 0, Start resumes at MIX

    uv run python tests/test_syrup_alarms.py           (~2.5 min)
    uv run python tests/test_syrup_alarms.py --quick   (~30 s, without D1-C2)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_syrup_sequencer import (
    COIL_BATCH_DOSED, COIL_COMMLOST, COIL_E_STOP_OK, DEFAULT_CARB_TIME, DEFAULT_MIX_TIME, HOST,
    HR_BUFFER_LEVEL, HR_CARB_TIME, HR_CO2_PRESSURE, HR_MIX_LEVEL, HR_MIX_TIME, HR_MODE_REQ,
    HR_PRODUCT_TEMP, HR_SYRUP_LEVEL, MODE_AUTO, MODE_STOP, PORT, RESET, SETTLE, START,
    Heartbeat, Plc, check, connect, leave_idle, reset_to_idle,
)

HR_STATE = 1074  # %MW50
HR_BATCH_COUNT = 1077  # %MW53
COIL_MIX_HIGH_HIGH = 8192  # %MX0.0
COIL_ALARMS = 8232  # %MX5.0-5.5
ALARMS = ["Alm_Overflow", "Alm_OverPressure", "Alm_CoolTimeout", "Alm_CommLost", "Alm_LowSyrup", "Alm_DoseTimeout"]
OUTPUTS = ["WaterPump", "SyrupPump", "Agitator", "CoolingValve", "CO2Valve", "TransferPump"]  # coils 0-5
TIMEOUT_S = 60  # CoolTimeout / DoseTimeout in main


def main() -> int:
    quick = "--quick" in sys.argv
    claim_plant("test_syrup_alarms", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_alarms")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def bits(address: int, names: list[str]) -> set[str]:
        result = client.read_coils(address, count=len(names))
        if result.isError():
            raise SystemExit(f"Read coils {address} failed: {result}")
        return {n for n, on in zip(names, result.bits) if on}

    def alarms() -> set[str]:
        return bits(COIL_ALARMS, ALARMS)

    def outputs() -> set[str]:
        return bits(0, OUTPUTS)

    def hr(address: int) -> int:
        result = client.read_holding_registers(address, count=1)
        if result.isError():
            raise SystemExit(f"Read HR {address} failed: {result}")
        return result.registers[0]

    def reach(name: str, expected: int, timeout: float, what: str, never: tuple[int, ...] = ()) -> bool:
        ok, seen, dt = plc.wait_step(expected, timeout)
        ok = ok and not any(s in seen for s in never)
        extra = f", never {list(never)}" if never else ""
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}{extra}; seen {seen})"))
        return ok

    def expect_fault(name: str, alarm: str, what: str) -> None:
        """Step 900, State 9, exactly this alarm, every output off."""
        time.sleep(SETTLE)
        step, state, active, on = plc.step(), hr(HR_STATE), alarms(), outputs()
        ok = step == 900 and state == 9 and active == {alarm} and not on
        results.append(check(name, ok, f"{what} -> Step {step}, State {state}, alarms {sorted(active)}, outputs {sorted(on)} "
                                       f"(expected 900 / 9 / ['{alarm}'] / none)"))

    def expect_cleared(name: str, what: str) -> None:
        plc.cmd(RESET)
        time.sleep(SETTLE)
        step, active = plc.step(), alarms()
        results.append(check(name, step == 0 and not active, f"{what} + Reset -> Step {step}, alarms {sorted(active)} (expected 0 / none)"))

    def expect_ignored(name: str, alarm: str, what: str) -> None:
        plc.cmd(RESET)
        time.sleep(SETTLE)
        step, active = plc.step(), alarms()
        results.append(check(name, step == 900 and alarm in active,
                             f"Reset while {what} -> Step {step}, alarms {sorted(active)} (expected 900, {alarm} kept)"))

    def batch_to(step: int, timeout: float) -> bool:
        """From IDLE with an empty tank: Start, dose everything at once (590 L), run up to `step`."""
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.write(HR_MIX_LEVEL, 590)
        plc.cmd(START)
        return plc.wait_step(step, timeout)[0]

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        for address, value in ((HR_SYRUP_LEVEL, 1000), (HR_MIX_LEVEL, 0), (HR_PRODUCT_TEMP, 200),
                               (HR_CO2_PRESSURE, 400), (HR_BUFFER_LEVEL, 1500),
                               (HR_MIX_TIME, 2), (HR_CARB_TIME, 2)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_MIX_HIGH_HIGH, False)
        plc.coil(COIL_BATCH_DOSED, False)
        heartbeat.start()
        time.sleep(1.0)
        reset_to_idle(client)  # Auto + Reset until Step 0 (a FAULT left by another script)
        if plc.coil(COIL_COMMLOST) or plc.step() != 0 or alarms():
            raise SystemExit(f"Setup failed: Step {plc.step()}, alarms {sorted(alarms())} (expected 0 / none). "
                             "An alarm that comes back after Reset has a cause that is always TRUE: check the section 9 rungs")

        # ---------- N1: a normal batch raises no alarm ----------
        seen_alarms: set[str] = set()
        seen_steps: list[int] = []
        count0 = hr(HR_BATCH_COUNT)
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.cmd(START)
        for level_or_temp, address, target, timeout in ((500, HR_MIX_LEVEL, 20, 1), (590, HR_MIX_LEVEL, 30, 1),
                                                        (None, None, 40, 4), (40, HR_PRODUCT_TEMP, 50, 1),
                                                        (None, None, 60, 4),
                                                        (1500, HR_BUFFER_LEVEL, 60, 0.5),  # full: no next batch
                                                        (5, HR_MIX_LEVEL, 5, 1)):
            if address is not None:
                plc.write(address, level_or_temp)
            ok, seen, _ = plc.wait_step(target, timeout)
            seen_steps += seen
            seen_alarms |= alarms()
            if not ok:
                break
        count = hr(HR_BATCH_COUNT)
        results.append(check("N1", plc.step() == 5 and count == count0 + 1 and not seen_alarms and 900 not in seen_steps,
                             f"normal batch -> Step {plc.step()}, BatchCount +{count - count0}, alarms seen {sorted(seen_alarms)} "
                             f"(expected 5, +1, none; steps {sorted(set(seen_steps))})"))
        plc.write(HR_BUFFER_LEVEL, 1500)
        plc.write(HR_MODE_REQ, MODE_STOP)  # back to IDLE for the alarm checks
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_AUTO)

        # ---------- O1-O5: overflow ----------
        plc.write(HR_MIX_LEVEL, 960)
        expect_fault("O1", "Alm_Overflow", "MixLevel 960 >= MixMax")
        expect_ignored("O2", "Alm_Overflow", "MixLevel still 960")
        plc.write(HR_MIX_LEVEL, 900)
        expect_cleared("O3", "MixLevel 900")
        plc.write(HR_MIX_LEVEL, 500)
        plc.coil(COIL_MIX_HIGH_HIGH, True)
        expect_fault("O4a", "Alm_Overflow", "MixHighHigh TRUE at MixLevel 500")
        plc.coil(COIL_MIX_HIGH_HIGH, False)
        expect_cleared("O4b", "MixHighHigh FALSE")
        plc.write(HR_MIX_LEVEL, 0)
        plc.write(HR_BUFFER_LEVEL, 1950)
        expect_fault("O5a", "Alm_Overflow", "BufferLevel 1950 = BufferMax")
        plc.write(HR_BUFFER_LEVEL, 1949)
        expect_cleared("O5b", "BufferLevel 1949")
        plc.write(HR_BUFFER_LEVEL, 1500)

        # ---------- P1-P4: over-pressure ----------
        plc.write(HR_CO2_PRESSURE, 599)
        time.sleep(SETTLE)
        active = alarms()
        results.append(check("P1", plc.step() == 0 and not active, f"CO2Pressure 5.99 bar -> Step {plc.step()}, alarms {sorted(active)} (expected 0 / none)"))
        plc.write(HR_CO2_PRESSURE, 600)
        expect_fault("P2", "Alm_OverPressure", "CO2Pressure 6.00 bar = PressMax")
        expect_ignored("P3", "Alm_OverPressure", "CO2Pressure still 6.00 bar")
        plc.write(HR_CO2_PRESSURE, 590)
        expect_cleared("P4", "CO2Pressure 5.90 bar")
        plc.write(HR_CO2_PRESSURE, 400)

        # ---------- R1: FAULT in MIX, resume at MIX ----------
        if batch_to(30, 2):
            plc.coil(COIL_MIX_HIGH_HIGH, True)
            time.sleep(SETTLE)
            plc.coil(COIL_MIX_HIGH_HIGH, False)
            plc.cmd(RESET)
            dosed = plc.coil(COIL_BATCH_DOSED)
            plc.write(HR_BUFFER_LEVEL, 1500)
            plc.cmd(START)
            ok, seen, _ = plc.wait_step(30, 2)
            results.append(check("R1", ok and 10 not in seen and 900 not in seen and dosed,
                                 f"FAULT in MIX, Reset, Start -> seen {seen}, Batch_Dosed {dosed} "
                                 "(expected 30 without 10: resume at MIX)"))
        else:
            results.append(check("R1", False, f"batch did not reach MIX (Step {plc.step()})"))
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        plc.write(HR_MODE_REQ, MODE_AUTO)
        plc.write(HR_MIX_LEVEL, 0)
        plc.coil(COIL_BATCH_DOSED, False)

        # ---------- L1-L2: low syrup is a warning ----------
        plc.write(HR_SYRUP_LEVEL, 50)
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.cmd(START)
        ok, seen = plc.stays(5, 1.0)
        state, active = hr(HR_STATE), alarms()
        results.append(check("L1", ok and state == 2 and active == {"Alm_LowSyrup"},
                             f"batch needed, SyrupLevel 50 -> Step {seen}, State {state}, alarms {sorted(active)} "
                             "(expected stays 5, State 2, ['Alm_LowSyrup'])"))
        plc.write(HR_SYRUP_LEVEL, 1000)
        time.sleep(SETTLE)
        step, active = plc.step(), alarms()
        results.append(check("L2", step == 10 and not active,
                             f"syrup refilled (no Reset) -> Step {step}, alarms {sorted(active)} (expected 10 / none)"))

        if not quick:
            # ---------- D1-D2: dosing timeout (pump failure) ----------
            plc.write(HR_MIX_LEVEL, 300)  # in 10 DOSE_WATER, the level never reaches 500
            t0 = time.monotonic()
            print(f"      waiting up to {TIMEOUT_S + 5} s for the dosing timeout ...", flush=True)
            ok, seen, _ = plc.wait_step(900, TIMEOUT_S + 5)
            dt = time.monotonic() - t0
            results.append(check("D1a", ok and dt >= TIMEOUT_S - 5, f"DOSE_WATER, level held at 300 L -> Step 900 after {dt:.1f} s "
                                                                   f"(expected ~{TIMEOUT_S} s; seen {seen})"))
            expect_fault("D1b", "Alm_DoseTimeout", "dosing timeout")
            expect_cleared("D2", "Step 900 (Water_Timer reset)")
            plc.write(HR_MIX_LEVEL, 0)

            # ---------- C1-C2: cooling timeout (chiller broken) ----------
            plc.write(HR_PRODUCT_TEMP, 200)
            if batch_to(40, 4):
                t0 = time.monotonic()
                print(f"      waiting up to {TIMEOUT_S + 5} s for the cooling timeout ...", flush=True)
                ok, seen, _ = plc.wait_step(900, TIMEOUT_S + 5)
                dt = time.monotonic() - t0
                results.append(check("C1a", ok and dt >= TIMEOUT_S - 5, f"COOL, product held at 20.0 degC -> Step 900 after {dt:.1f} s "
                                                                       f"(expected ~{TIMEOUT_S} s; seen {seen})"))
                expect_fault("C1b", "Alm_CoolTimeout", "cooling timeout")
            else:
                results.append(check("C1", False, f"batch did not reach COOL (Step {plc.step()})"))
            plc.write(HR_PRODUCT_TEMP, 40)
            expect_cleared("C2a", "product cold")
            plc.write(HR_BUFFER_LEVEL, 1500)
            plc.cmd(START)
            reach("C2b", 30, 2, "Start after the cooling FAULT", never=(10, 20))
        else:
            print("      --quick: timeouts D1-C2 skipped")
    finally:
        leave_idle(client)
        plc.write(HR_MIX_TIME, DEFAULT_MIX_TIME)
        plc.write(HR_CARB_TIME, DEFAULT_CARB_TIME)
        plc.coil(COIL_BATCH_DOSED, False)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, alarms reset, MixTime 20 / CarbTime 10, sensors 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
