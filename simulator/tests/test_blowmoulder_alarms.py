"""Blow moulder ladder stage 9: alarms and FAULT (timeouts, limit alarms, resets, No_Fault, MOVE 900).

Runs against the blow moulder PLC on port 5025, playing the machine by writing the sensors (heartbeat
thread, E_Stop_OK, setpoint defaults as in test_blowmoulder_oven_seq.py).

    A1  setup: Reset -> no FAULT alarm, no warning
Timeouts (-> FAULT: both steps 900, State 9):
    A2  LOAD without Preform_In_Oven: no alarm at 2.5 s, Alm_HandlingJam after 3 s, only the exhaust ON
    A3  Start while in FAULT -> still 900; Reset in Mode Stop -> alarm off, steps stay 900;
        Auto + Reset -> both 0, Start runs again
    A4  CLOSE without Mould_Closed -> Alm_MouldFault after 3 s
    A5  HEAT with the oven cold (lamps 100 %) -> Alm_OvenHeatTimeout after 15 s, LampPower 0
    A6  BLOW at 28.9 bar (raw 20000) -> Alm_BlowFault after 2 s; the mould stays CLOSED, the exhaust opens
    A7  Auto + Reset -> both 0; Start -> MouldStep 60 EXHAUST (closed mould); 14.4 bar stays inside ->
        Alm_ExhaustFault after 3 s, mould closed
    A8  Reset with 14.4 bar inside -> refused (alarm and 900 stay); 0.4 bar + Reset -> both 0
Limit alarms (every mode):
    A9  OvenTemp 130.0 degC in Mode Stop -> Alm_OvenOverTemp, steps 900; Reset at 130.0 refused; 129.9 +
        Reset -> alarm off, steps stay 900 (Mode Stop); Auto + Reset -> 0
    A10 raw 27700 (40.0 bar) -> Alm_OverPressure; Reset refused; 0 bar + Reset -> off
    A11 raw -2000 (wire break) -> Alm_PressureSensor, Pressure_Safe FALSE; raw 0 -> Pressure_Safe still
        FALSE (latched: a sensor that failed once is not trusted); Reset -> both back
Not a fault:
    A12 bottle in EJECT, C1 full 10 s -> no Alm_HandlingJam (the jam timer runs only while the ejector
        pushes), State 3
    A13 (not with --quick) Blocked 2 min -> Alm_OutfeedBlocked (warning), still no FAULT; C1 free -> off

    uv run python tests/test_blowmoulder_alarms.py           (~3 min)
    uv run python tests/test_blowmoulder_alarms.py --quick   (~70 s, without A13)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_blowmoulder_oven_seq import (COIL_BOTTLE_IN_MOULD, COIL_C1_FULL, COIL_COMMLOST, COIL_E_STOP_OK,
                                       COIL_MOULD_CLOSED, COIL_MOULD_OPEN, COIL_PREFORM_AT_LOADER, COIL_PREFORM_IN_OVEN,
                                       HOST, HR_MODE_REQ, HR_MOULD_STEP, HR_OVEN_BAND, HR_OVEN_SETPOINT, HR_OVEN_TEMP,
                                       HR_STEP, MODE_AUTO, MODE_STOP, OVEN_BAND, OVEN_COLD, OVEN_SETPOINT, PORT, RESET,
                                       SETTLE, START, Heartbeat, Plc, check, connect)

HR_LAMP_POWER = 0  # %QW0
HR_RAW = 1037  # %MW13
HR_BLOW_SETPOINT = 1058  # %MW34
HR_STATE = 1074  # %MW50
COIL_ARM_AT_OVEN, COIL_ROD_UP, COIL_ROD_DOWN = 8194, 8198, 8199
COIL_PRESSURE_SAFE = 8221  # %MX3.5
ALARMS = {"Alm_HandlingJam": 8233, "Alm_OutfeedBlocked": 8234, "Alm_MouldFault": 8236, "Alm_OvenOverTemp": 8237,
          "Alm_OvenHeatTimeout": 8238, "Alm_BlowFault": 8239, "Alm_ExhaustFault": 8248, "Alm_OverPressure": 8249,
          "Alm_PressureSensor": 8250}
OUTPUTS = ["HopperFeed", "Loader", "ArmToMould", "Gripper", "MouldClose", "StretchRod", "PreBlowValve",
           "HighBlowValve", "ExhaustValve", "Ejector"]
RAW_289, RAW_144, RAW_04_BAR, RAW_400, RAW_WIRE_BREAK = 20000, 10000, 345, 27700, -2000


def main() -> int:
    quick = "--quick" in sys.argv[1:]
    claim_plant("test_blowmoulder_alarms", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_alarms")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)
    names = {"loader": COIL_PREFORM_AT_LOADER, "in_oven": COIL_PREFORM_IN_OVEN, "arm_oven": COIL_ARM_AT_OVEN,
             "closed": COIL_MOULD_CLOSED, "open": COIL_MOULD_OPEN, "rod_up": COIL_ROD_UP, "rod_down": COIL_ROD_DOWN,
             "bottle": COIL_BOTTLE_IN_MOULD, "c1_full": COIL_C1_FULL}

    def sensors(**values: bool) -> None:
        for key, value in values.items():
            plc.coil(names[key], value)

    def raw(value: int) -> None:
        plc.write(HR_RAW, value & 0xFFFF)

    def alarms() -> set[str]:
        return {name for name, coil in ALARMS.items() if plc.coil(coil)}

    def outputs() -> set[str]:
        result = client.read_coils(0, count=len(OUTPUTS))
        if result.isError():
            raise SystemExit(f"Read outputs failed: {result}")
        return {n for n, bit in zip(OUTPUTS, result.bits) if bit}

    def home() -> None:
        """Mode Stop, mould open and empty, arm home, rod up, oven empty, C1 free, 0 bar, oven cold."""
        plc.mode(MODE_STOP)
        plc.clear_sensors()
        raw(0)
        plc.write(HR_OVEN_TEMP, OVEN_COLD)
        sensors(open=True, arm_oven=True, rod_up=True)

    def reset_auto() -> tuple[int, int]:
        plc.mode(MODE_AUTO)
        plc.cmd(RESET)
        return plc.steps()

    def fault(name: str, alarm: str, before: float, after: float, what: str, outs: set[str] | None = None) -> None:
        """Nothing at `before` s, then `alarm`, both steps 900 and State 9 at `after` s (and the outputs)."""
        time.sleep(before)
        early = alarms()
        time.sleep(after - before)
        late, steps, st = alarms(), plc.steps(), plc.read(HR_STATE)
        got = outputs()
        ok = not early and late == {alarm} and steps == (900, 900) and st == 9 and (outs is None or got == outs)
        results.append(check(name, ok, f"{what}: alarms {sorted(early)} at {before:g} s, {sorted(late)} at {after:g} s, "
                                       f"steps {steps}, State {st}, ON {sorted(got)} (expected [{alarm}], (900, 900), 9"
                                       f"{', ' + str(sorted(outs)) if outs is not None else ''})"))

    try:
        # ---------- setup ----------
        home()
        for address, value in ((HR_OVEN_SETPOINT, OVEN_SETPOINT), (HR_OVEN_BAND, OVEN_BAND), (HR_BLOW_SETPOINT, 350)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        time.sleep(2.0)  # Outfeed_Full off-filter
        steps = reset_auto()
        plc.mode(MODE_STOP)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")
        active = alarms()
        results.append(check("A1", not active and steps == (0, 0), f"Reset -> alarms {sorted(active)}, steps {steps} "
                                                                    "(expected none, (0, 0))"))

        # ---------- timeouts ----------
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        sensors(loader=True)  # LOAD, but the preform never reaches the pocket
        fault("A2", "Alm_HandlingJam", 2.5, 3.4, "LOAD without Preform_In_Oven", {"ExhaustValve"})
        plc.cmd(START)
        in_fault = plc.steps()
        plc.mode(MODE_STOP)
        plc.cmd(RESET)
        stop_reset, stop_steps = alarms(), plc.steps()
        steps = reset_auto()
        sensors(loader=False)
        plc.cmd(START)
        restart = plc.steps()
        results.append(check("A3", in_fault == (900, 900) and not stop_reset and stop_steps == (900, 900)
                             and steps == (0, 0) and restart == (10, 10),
                             f"Start in FAULT -> {in_fault}; Reset in Mode Stop -> alarms {sorted(stop_reset)}, steps "
                             f"{stop_steps}; Auto + Reset -> {steps}; Start -> {restart} "
                             "(expected (900, 900); none, (900, 900); (0, 0); (10, 10))"))

        home()
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 30)  # CLOSE, but the mould never reports closed
        fault("A4", "Alm_MouldFault", 2.5, 3.4, "CLOSE without Mould_Closed")
        reset_auto()

        home()
        sensors(in_oven=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(SETTLE)
        lamps_before = plc.read(HR_LAMP_POWER)
        fault("A5", "Alm_OvenHeatTimeout", 14.0, 15.5, f"HEAT, oven cold (lamps {lamps_before} %)")
        lamps_after = plc.read(HR_LAMP_POWER)
        results[-1] = results[-1] and check("A5", lamps_before == 100 and lamps_after == 0,
                                            f"LampPower {lamps_before} -> {lamps_after} (expected 100 -> 0)")
        reset_auto()

        home()
        sensors(open=False, closed=True, rod_up=False, rod_down=True)
        raw(RAW_289)
        plc.mode(MODE_AUTO)
        plc.write(HR_MOULD_STEP, 50)  # BLOW, the pressure stops at 28.9 bar
        fault("A6", "Alm_BlowFault", 1.5, 2.4, "BLOW at 28.9 bar", {"MouldClose", "ExhaustValve"})

        steps = reset_auto()
        raw(RAW_144)  # the exhaust cannot vent: 14.4 bar stays inside
        plc.cmd(START)
        restart = plc.steps()
        fault("A7", "Alm_ExhaustFault", 2.4, 3.4, f"Reset -> {steps}, Start -> {restart}, EXHAUST at 14.4 bar",
              {"MouldClose", "ExhaustValve"})
        results[-1] = results[-1] and steps == (0, 0) and restart[1] == 60
        plc.cmd(RESET)
        refused, refused_steps = alarms(), plc.steps()
        raw(RAW_04_BAR)
        steps = reset_auto()
        cleared = alarms()
        results.append(check("A8", refused == {"Alm_ExhaustFault"} and refused_steps == (900, 900)
                             and not cleared and steps == (0, 0),
                             f"Reset at 14.4 bar -> {sorted(refused)}, {refused_steps}; 0.4 bar + Reset -> "
                             f"{sorted(cleared)}, {steps} (expected [Alm_ExhaustFault], (900, 900); none, (0, 0))"))

        # ---------- limit alarms ----------
        home()
        plc.write(HR_OVEN_TEMP, 1300)
        time.sleep(SETTLE)
        hot, hot_steps = alarms(), plc.steps()
        plc.cmd(RESET)
        refused = alarms()
        plc.write(HR_OVEN_TEMP, 1299)
        plc.cmd(RESET)
        cleared, stop_steps = alarms(), plc.steps()
        steps = reset_auto()
        results.append(check("A9", hot == {"Alm_OvenOverTemp"} and hot_steps == (900, 900) and refused == hot
                             and not cleared and stop_steps == (900, 900) and steps == (0, 0),
                             f"Mode Stop, 130.0 degC -> {sorted(hot)}, {hot_steps}; Reset -> {sorted(refused)}; 129.9 + "
                             f"Reset -> {sorted(cleared)}, {stop_steps}; Auto + Reset -> {steps}"))

        home()
        raw(RAW_400)
        time.sleep(SETTLE)
        high = alarms()
        plc.cmd(RESET)
        refused = alarms()
        raw(0)
        steps = reset_auto()
        cleared = alarms()
        results.append(check("A10", high == {"Alm_OverPressure"} and refused == high and not cleared and steps == (0, 0),
                             f"40.0 bar -> {sorted(high)}; Reset -> {sorted(refused)}; 0 bar + Reset -> {sorted(cleared)}, "
                             f"{steps} (expected [Alm_OverPressure] twice, then none and (0, 0))"))

        home()
        raw(RAW_WIRE_BREAK)
        time.sleep(SETTLE)
        broken, safe_broken = alarms(), plc.coil(COIL_PRESSURE_SAFE)
        raw(0)
        time.sleep(SETTLE)
        safe_back = plc.coil(COIL_PRESSURE_SAFE)
        steps = reset_auto()
        cleared, safe_reset = alarms(), plc.coil(COIL_PRESSURE_SAFE)
        results.append(check("A11", broken == {"Alm_PressureSensor"} and not safe_broken and not safe_back
                             and not cleared and safe_reset and steps == (0, 0),
                             f"raw -2000 -> {sorted(broken)}, Pressure_Safe {safe_broken}; raw 0 -> Pressure_Safe "
                             f"{safe_back}; Reset -> {sorted(cleared)}, Pressure_Safe {safe_reset}, {steps} "
                             "(expected [Alm_PressureSensor], False; False; none, True, (0, 0))"))

        # ---------- not a fault ----------
        home()
        sensors(bottle=True, c1_full=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(10.0)
        active, steps, st = alarms(), plc.steps(), plc.read(HR_STATE)
        results.append(check("A12", not active and steps == (10, 80) and st == 3,
                             f"bottle in EJECT, C1 full 10 s -> alarms {sorted(active)}, steps {steps}, State {st} "
                             "(expected none, (10, 80), 3)"))
        if quick:
            print("SKIP  A13: Blocked 2 min -> Alm_OutfeedBlocked (--quick)")
        else:
            time.sleep(114.0)  # Blocked starts after the 2 s on-filter: 10 + 114 - 2 = 122 s > BlockedWarn 2 min
            active, steps = alarms(), plc.steps()
            sensors(c1_full=False)
            time.sleep(3.5)  # off-filter: no longer Blocked
            after = alarms()
            results.append(check("A13", active == {"Alm_OutfeedBlocked"} and steps == (10, 80)
                                 and "Alm_OutfeedBlocked" not in after,
                                 f"Blocked 2 min -> {sorted(active)}, steps {steps}; C1 free -> {sorted(after)} "
                                 "(expected [Alm_OutfeedBlocked] without FAULT, then off)"))
    finally:
        plc.write(HR_MODE_REQ, MODE_STOP)
        time.sleep(SETTLE)
        plc.clear_sensors()
        plc.write(HR_OVEN_TEMP, 0)
        raw(0)
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):
            plc.write(address, 0)
        plc.coil(COIL_E_STOP_OK, e_stop_before)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, alarms reset, both steps 0, sensors FALSE, OvenTemp and "
          "raw 0, E_Stop_OK as before)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
