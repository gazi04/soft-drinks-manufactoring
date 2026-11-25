"""Blow moulder ladder stage 6: the lamps, LampPower (%QW0 = holding register 0) from the P controller.

Runs against the blow moulder PLC on port 5025. Mode Auto without Start (both stations stay IDLE):
Lamps_On needs only Auto, Master_Enable, no FAULT and a preform in the pocket. OvenTemp is written,
LampPower read from HR 0 and compared with Lamp_Calc (%MW40, HR 1064). Defaults: OvenSetpoint 1050,
Kp 10, Bias 0.

    L1  setup: Mode Stop, heartbeat, Reset, Alm_CommLost FALSE
    L2  Mode Stop, preform, OvenTemp 20.0 degC -> 0; HR 0 written 77 -> 0 again (the PLC owns %QW0)
    L3  Auto, no preform -> 0
    L4  Auto + preform: 20.0 degC -> 100 (clamped), 103.0 -> 20, 102.0 -> 30 (3 degC below the setpoint
        gives 30 %: the steady offset a P controller needs), 105.0 -> 0, 108.0 -> 0; LampPower = Lamp_Calc
    L5  Bias 30 at 105.0 degC -> 30
    L6  OvenMax interlock: Kp 1, Bias 100: 129.9 degC -> 76 (the controller alone still heats),
        130.0 degC -> 0 (Oven_BelowMax)
    L7  Manual -> 0            L8  E_Stop_OK FALSE -> 0
    L9  heartbeat frozen 3.5 s (Alm_CommLost) -> 0; heartbeat back + Reset -> 100 again
    L10 Step 900 written (FAULT) -> 0

    uv run python tests/test_blowmoulder_lamps.py      (~25 s)

At the end: Mode Stop, Kp / Bias back to 10 / 0, sensors FALSE, OvenTemp 0, E_Stop_OK as before.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_blowmoulder_oven_seq import (COIL_COMMLOST, COIL_E_STOP_OK, COIL_PREFORM_IN_OVEN, HOST, HR_MODE_REQ,
                                       HR_MOULD_STEP, HR_OVEN_SETPOINT, HR_OVEN_TEMP, HR_STEP, MODE_AUTO,
                                       MODE_MANUAL, MODE_STOP, PORT, RESET, SETTLE, Heartbeat, Plc, check, connect)

HR_LAMP_POWER = 0  # %QW0
HR_KP = 1056  # %MW32
HR_BIAS = 1057  # %MW33
HR_LAMP_CALC = 1064  # %MW40
DEFAULTS = {HR_OVEN_SETPOINT: 1050, HR_KP: 10, HR_BIAS: 0}


def main() -> int:
    claim_plant("test_blowmoulder_lamps", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_lamps")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)

    def power() -> int:
        return plc.read(HR_LAMP_POWER)

    def at(temp: int) -> tuple[int, int]:
        plc.write(HR_OVEN_TEMP, temp)
        time.sleep(SETTLE)
        return power(), plc.read(HR_LAMP_CALC)

    try:
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address, value in DEFAULTS.items():
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        lost = plc.coil(COIL_COMMLOST)
        results.append(check("L1", not lost, f"Mode Stop, heartbeat, Reset -> Alm_CommLost {lost} (expected False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the lamps cannot run")

        plc.coil(COIL_PREFORM_IN_OVEN, True)
        stop_power, _ = at(200)
        plc.write(HR_LAMP_POWER, 77)
        time.sleep(SETTLE)
        after_write = power()
        results.append(check("L2", stop_power == 0 and after_write == 0,
                             f"Mode Stop, preform, 20.0 degC -> LampPower {stop_power}; HR 0 written 77 -> {after_write} "
                             "(expected 0 / 0: the PLC owns %QW0)"))

        plc.coil(COIL_PREFORM_IN_OVEN, False)
        plc.mode(MODE_AUTO)
        no_preform, _ = at(200)
        results.append(check("L3", no_preform == 0, f"Auto, no preform -> LampPower {no_preform} (expected 0)"))

        plc.coil(COIL_PREFORM_IN_OVEN, True)
        for temp, expected, note in ((200, 100, "clamped"), (1030, 20, "Kp 10 * 2.0 degC"),
                                     (1020, 30, "3 degC below: the P offset"), (1050, 0, "at the setpoint"),
                                     (1080, 0, "too hot, clamped")):
            got, calc = at(temp)
            results.append(check("L4", got == expected and got == calc,
                                 f"Auto + preform, OvenTemp {temp / 10:.1f} degC -> LampPower {got}, Lamp_Calc {calc} "
                                 f"(expected {expected}, {note}, LampPower = Lamp_Calc)"))

        plc.write(HR_BIAS, 30)
        got, _ = at(1050)
        results.append(check("L5", got == 30, f"Bias 30 at 105.0 degC -> LampPower {got} (expected 30)"))

        plc.write(HR_KP, 1)
        plc.write(HR_BIAS, 100)
        below, calc_below = at(1299)
        at_max, calc_max = at(1300)
        results.append(check("L6", below == 76 and at_max == 0 and calc_max > 0,
                             f"Kp 1, Bias 100: 129.9 degC -> {below}, 130.0 degC -> {at_max} with Lamp_Calc {calc_max} "
                             "(expected 76 / 0: Oven_BelowMax cuts the lamps although the controller still asks)"))
        for address, value in DEFAULTS.items():
            plc.write(address, value)
        plc.write(HR_OVEN_TEMP, 200)
        plc.cmd(RESET)  # 130.0 degC latched Alm_OvenOverTemp (stage 9): Reset in Auto clears it and 900 -> 0

        plc.mode(MODE_MANUAL)
        manual, _ = at(200)
        results.append(check("L7", manual == 0, f"Manual, preform, 20.0 degC -> LampPower {manual} (expected 0)"))

        plc.mode(MODE_AUTO)
        on_before, _ = at(200)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        e_stop = power()
        plc.coil(COIL_E_STOP_OK, True)
        results.append(check("L8", on_before == 100 and e_stop == 0,
                             f"Auto 100 % -> E_Stop_OK FALSE -> LampPower {e_stop} (expected 0)"))

        heartbeat.stop()
        time.sleep(3.5)  # watchdog 3 s
        lost, comm_power = plc.coil(COIL_COMMLOST), power()
        heartbeat = Heartbeat()
        heartbeat.start()
        time.sleep(1.5)
        plc.cmd(RESET)
        back = power()
        results.append(check("L9", lost and comm_power == 0 and back == 100,
                             f"heartbeat frozen -> Alm_CommLost {lost}, LampPower {comm_power}; heartbeat back + Reset -> "
                             f"{back} (expected True / 0 / 100)"))

        plc.write(HR_STEP, 900)
        time.sleep(SETTLE)
        fault = power()
        plc.mode(MODE_STOP)
        plc.write(HR_STEP, 0)
        results.append(check("L10", fault == 0, f"Step 900 (FAULT) -> LampPower {fault} (expected 0)"))
    finally:
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        for address in (HR_STEP, HR_MOULD_STEP):
            plc.write(address, 0)
        for address, value in DEFAULTS.items():
            plc.write(address, value)
        plc.clear_sensors()
        plc.write(HR_OVEN_TEMP, 0)
        plc.coil(COIL_E_STOP_OK, e_stop_before)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, Kp / Bias 10 / 0, sensors FALSE, OvenTemp 0, "
          "E_Stop_OK as before)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
