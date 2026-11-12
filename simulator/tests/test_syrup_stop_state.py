"""Syrup room ladder stage 6: Stop at the end of the current step, State word, BatchCount.

Runs against the syrup PLC on port 5022. Plays the plant by writing the sensors (as
test_syrup_sequencer.py, whose helpers it imports) and watches Step (%MW51), State (%MW50),
BatchCount (%MW53) and Batch_Dosed (%MX3.3). MixTime / CarbTime shortened to 2 s.

Stop (Cmd = 2) is remembered in Stop_Request and acted on at the end of the current step:
    S1  Stop in IDLE does nothing: a later Start goes to 5 and stays there (no stale Stop_Request)
    S2  Stop in WAIT_DEMAND -> 0 at once
    S3  Stop in DOSE_WATER: stays 10, WaterPump keeps running until MixLevel = WaterBatch, then 0 (not 20)
    S4  Start -> resumes through WAIT_DEMAND and DOSE_WATER (level already reached) to 20
    S5  Stop in DOSE_SYRUP: 0 at DoseTarget (not 30), Batch_Dosed set anyway
    S6  Start -> 30 MIX without passing DOSE_WATER (resume a dosed batch)
    S7  Stop in MIX -> 0 after MixTime (not 40)
    S8  Stop in COOL: stays 40 while warm, 0 at Temp_OK (not 50)
    S9  Stop in CARBONATE -> 0 after CarbTime (not 55 / 60)
    S10 Stop in WAIT_BUFFER -> 0 at once
    S11 Stop in TRANSFER: stays 60, 0 when empty (not 5); BatchCount +1, Batch_Dosed reset
    S12 Start after S11 with a full buffer -> 5 and stays (not 30: the empty tank is not resumed)
State (one value per situation):
    T1 IDLE -> 0   T2 WAIT_DEMAND, buffer full enough -> 1 Running
    T3 WAIT_DEMAND, batch needed, SyrupLevel 50 -> 2 Starved; T4 syrup refilled -> 10, State 1
    T5 WAIT_BUFFER -> 3 Blocked   T6 TRANSFER -> 1   T7 FAULT (Step 900 written) -> 9
    T8 Reset -> 0 / State 0       T9 Manual mode -> State 0
Counter:
    C1 one full batch, TRANSFER until MixEmpty -> BatchCount +1, Step 5, Batch_Dosed FALSE

    uv run python tests/test_syrup_stop_state.py      (~45 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_syrup_sequencer import (
    COIL_BATCH_DOSED, COIL_COMMLOST, COIL_E_STOP_OK, DEFAULT_CARB_TIME, DEFAULT_MIX_TIME, HOST,
    HR_BUFFER_LEVEL, HR_CARB_TIME, HR_CO2_PRESSURE, HR_MIX_LEVEL, HR_MIX_TIME, HR_MODE_REQ,
    HR_PRODUCT_TEMP, HR_STEP, HR_SYRUP_LEVEL, MODE_AUTO, MODE_STOP, PORT, RESET, SETTLE, START,
    Heartbeat, Plc, check, connect, leave_idle, reset_to_idle,
)

HR_STATE = 1074  # %MW50
HR_BATCH_COUNT = 1077  # %MW53
COIL_WATER_PUMP = 0  # %QX0.0
STOP = 2
MODE_MANUAL = 2
STATE_NAMES = {0: "Stopped", 1: "Running", 2: "Starved", 3: "Blocked", 9: "Fault"}


def main() -> int:
    claim_plant("test_syrup_stop_state", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_stop_state")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def hr(address: int) -> int:
        result = client.read_holding_registers(address, count=1)
        if result.isError():
            raise SystemExit(f"Read HR {address} failed: {result}")
        return result.registers[0]

    def reach(name: str, expected: int, timeout: float, what: str, never: tuple[int, ...] = ()) -> bool:
        """Step reaches expected within timeout and never passes a step in `never` on the way."""
        ok, seen, dt = plc.wait_step(expected, timeout)
        ok = ok and not any(s in seen for s in never)
        extra = f", never {list(never)}" if never else ""
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}{extra}; seen {seen})"))
        return ok

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        results.append(check(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})"))

    def state(name: str, expected: int, what: str) -> None:
        time.sleep(SETTLE)
        got = hr(HR_STATE)
        results.append(check(name, got == expected, f"{what} -> State {got} {STATE_NAMES.get(got, '?')} "
                                                    f"(expected {expected} {STATE_NAMES[expected]})"))

    def stop() -> None:
        plc.write(1024, STOP)  # HR_CMD; the PLC clears Cmd itself
        time.sleep(SETTLE)

    try:
        # ---------- setup (as test_syrup_sequencer.py) ----------
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
        if plc.coil(COIL_COMMLOST) or plc.step() != 0:
            raise SystemExit(f"Setup failed: Step {plc.step()}, Alm_CommLost {plc.coil(COIL_COMMLOST)} (expected 0 / False)")
        state("T1", 0, "Auto, IDLE")

        # ---------- S1 / S2: Stop in IDLE and WAIT_DEMAND ----------
        stop()
        plc.cmd(START)
        reach("S1a", 5, 1, "Stop in IDLE, then Start")
        hold("S1b", 5, 0.5, "WAIT_DEMAND after a Stop in IDLE (Stop_Request must not be latched)")
        state("T2", 1, "WAIT_DEMAND, buffer 1500 above BufferLow")
        stop()
        reach("S2", 0, 1, "Stop in WAIT_DEMAND")

        # ---------- T3 / T4: Starved ----------
        plc.write(HR_SYRUP_LEVEL, 50)
        plc.write(HR_BUFFER_LEVEL, 700)
        plc.cmd(START)
        hold("T3a", 5, 0.5, "batch needed, SyrupLevel 50 < SyrupTarget 90")
        state("T3b", 2, "WAIT_DEMAND without syrup")
        plc.write(HR_SYRUP_LEVEL, 1000)
        reach("T4a", 10, 1, "syrup refilled")
        state("T4b", 1, "DOSE_WATER")

        # ---------- S3 / S4: Stop in DOSE_WATER, resume ----------
        plc.write(HR_MIX_LEVEL, 300)
        stop()
        hold("S3a", 10, 1.0, "Stop in DOSE_WATER, MixLevel 300 < WaterBatch 500")
        pump = plc.coil(COIL_WATER_PUMP)
        results.append(check("S3b", pump, f"WaterPump = {pump} while the stopped step finishes its dose (expected True)"))
        plc.write(HR_MIX_LEVEL, 500)
        reach("S3c", 0, 1, "MixLevel 500 = WaterBatch", never=(20,))
        plc.cmd(START)
        reach("S4", 20, 1, "Start with 500 L water in the tank (5 -> 10 -> 20, level already reached)")

        # ---------- S5 / S6: Stop in DOSE_SYRUP, resume at MIX ----------
        stop()
        plc.write(HR_MIX_LEVEL, 590)
        reach("S5a", 0, 1, "Stop in DOSE_SYRUP, MixLevel 590 = DoseTarget", never=(30,))
        dosed = plc.coil(COIL_BATCH_DOSED)
        results.append(check("S5b", dosed, f"Batch_Dosed = {dosed} after a Stop at the end of DOSE_SYRUP (expected True)"))
        plc.cmd(START)
        reach("S6", 30, 1, "Start with a dosed batch", never=(10, 20))

        # ---------- S7: Stop in MIX ----------
        stop()
        reach("S7", 0, 4, "Stop in MIX -> after MixTime 2 s", never=(40,))

        # ---------- S8: Stop in COOL ----------
        plc.cmd(START)
        plc.wait_step(40, 4)  # 5 -> 30 -> 40 after MixTime
        stop()
        hold("S8a", 40, 1.0, "Stop in COOL, ProductTemp 20.0 degC (not cold)")
        plc.write(HR_PRODUCT_TEMP, 40)
        reach("S8b", 0, 1, "ProductTemp 4.0 degC -> Temp_OK", never=(50,))

        # ---------- S9: Stop in CARBONATE ----------
        plc.cmd(START)
        plc.wait_step(50, 4)  # 5 -> 30 -> 40 -> 50 (already cold)
        stop()
        reach("S9", 0, 4, "Stop in CARBONATE, pressure 4.00 bar -> after CarbTime 2 s", never=(55, 60))

        # ---------- T5 / S10: WAIT_BUFFER = Blocked, Stop there ----------
        plc.write(HR_BUFFER_LEVEL, 1400)  # 1400 + 590 > 1950: no room
        plc.cmd(START)
        reach("T5a", 55, 7, "buffer 1400, batch 590 does not fit (MIX 2 s + CARBONATE 2 s)")
        state("T5b", 3, "WAIT_BUFFER")
        stop()
        reach("S10", 0, 1, "Stop in WAIT_BUFFER")

        # ---------- C1 / T6: one full batch is counted ----------
        count0 = hr(HR_BATCH_COUNT)
        plc.write(HR_BUFFER_LEVEL, 1300)  # room now
        plc.cmd(START)
        reach("C1a", 60, 7, "buffer 1300 + 590 fits")
        state("T6", 1, "TRANSFER")
        plc.write(HR_MIX_LEVEL, 5)
        reach("C1b", 5, 1, "MixLevel 5 = MixEmpty")
        count, dosed = hr(HR_BATCH_COUNT), plc.coil(COIL_BATCH_DOSED)
        results.append(check("C1c", count == count0 + 1 and not dosed,
                             f"BatchCount {count0} -> {count}, Batch_Dosed {dosed} (expected +1 / False)"))

        # ---------- S11 / S12: Stop in TRANSFER ----------
        plc.write(HR_BUFFER_LEVEL, 700)  # batch needed
        plc.write(HR_MIX_LEVEL, 590)  # 10 -> 20 -> 30 in consecutive scans
        reach("S11a", 60, 7, "next batch: dose, MIX 2 s, cold, CARBONATE 2 s, 700 + 590 fits")
        plc.write(HR_MIX_LEVEL, 300)
        stop()
        hold("S11b", 60, 0.5, "Stop in TRANSFER, MixLevel 300")
        count0 = hr(HR_BATCH_COUNT)
        plc.write(HR_MIX_LEVEL, 5)
        reach("S11c", 0, 1, "MixLevel 5 = MixEmpty", never=(5,))
        count, dosed = hr(HR_BATCH_COUNT), plc.coil(COIL_BATCH_DOSED)
        results.append(check("S11d", count == count0 + 1 and not dosed,
                             f"BatchCount {count0} -> {count}, Batch_Dosed {dosed} after a Stop at the end of TRANSFER (expected +1 / False)"))
        plc.write(HR_BUFFER_LEVEL, 1500)
        plc.cmd(START)
        hold("S12", 5, 1.0, "Start, buffer 1500, tank empty (an empty tank must not resume at MIX)")

        # ---------- T7 - T9: Fault, Reset, Manual ----------
        plc.write(HR_STEP, 900)
        state("T7", 9, "Step 900 (FAULT) written")
        plc.cmd(RESET)
        reach("T8a", 0, 1, "Reset in FAULT")
        state("T8b", 0, "IDLE after Reset")
        plc.cmd(START)
        plc.wait_step(5, 1)
        plc.write(HR_MODE_REQ, MODE_MANUAL)
        state("T9", 0, "Manual mode (abort -> IDLE)")
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
