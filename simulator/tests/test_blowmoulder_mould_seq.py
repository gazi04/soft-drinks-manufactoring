"""Blow moulder ladder stage 5: mould station sequencer (Grip_Timer, Release_Timer, Hold_Timer, transitions).

Runs against the blow moulder PLC on port 5025. No outputs exist yet: the script plays the machine by
writing the sensors (arm, mould, rod, photo eye, raw blow pressure) and watches MouldStep (%MW52) and
Step (%MW51). Heartbeat thread, E_Stop_OK, setpoint defaults as in test_blowmoulder_oven_seq.py.

Hand-over guards (oven station in HEAT, then HOT):
    M1  setup: Mode Stop, heartbeat, Reset -> both 0, Alm_CommLost FALSE
    M2  Start, preform in the pocket, oven cold, mould open + empty, arm home, rod up -> Step 20, MouldStep 10
    M3  oven not HOT yet -> MouldStep stays 10
    M4  oven HOT (25), rod not up -> stays 10      M5  something in the mould -> stays 10
    M6  arm not home -> stays 10                   M7  mould not open -> stays 10
    M8  all conditions -> 20 GRIP
One bottle:
    M9  GRIP lasts GripTime (0.3 s) -> 22 SWING
    M10 arm at the mould -> 24 RELEASE; preform out of the pocket -> oven back to 10 FEED
    M11 RELEASE, photo eye sees nothing -> stays 24     M12 preform in the mould -> 26 ARM_BACK
    M13 arm home -> 30 CLOSE                       M14 mould closed -> 40 STRETCH
    M15 rod down -> 50 BLOW
    M16 raw 24000 (34.7 bar) -> stays 50           M17 raw 24192 (35.0 bar) -> 52 HOLD
    M18 HOLD lasts HoldTime (1.0 s) -> 60 EXHAUST
    M19 raw 400 (0.5 bar, not below ExhaustLimit) -> stays 60   M20 raw 345 (0.4 bar) -> 62 ROD_UP
    M21 rod up -> 70 OPEN                          M22 mould open -> 80 EJECT
    M23 bottle still in the mould -> stays 80
Both stations in parallel:
    M24 during CLOSE the oven loads and heats the next preform -> Step 25 before the EJECT ends
    M25 bottle out -> MouldStep 10 and at once 20 (the next hot preform waits): no idle cycle
Restart by sensors (rule 9) with the new transitions:
    M26 Mode Stop in GRIP; mould closed, rod down, 35 bar inside, Start -> MouldStep 60, stays 60 under
        pressure                                   M27 pressure gone -> 62, rod up -> 70, open -> 80
    M28 bottle out -> 10
    M29 E_Stop_OK FALSE in HOLD (MouldStep 52 written, mould closed) -> both 0; E-stop released, Start
        -> MouldStep 60 (exhaust first)

    uv run python tests/test_blowmoulder_mould_seq.py      (~50 s)

Grip_Timer, Release_Timer and Hold_Timer have no address: M9 and M18 measure the step times.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_blowmoulder_oven_seq import (COIL_COMMLOST, COIL_E_STOP_OK, COIL_MOULD_CLOSED, COIL_MOULD_OPEN,
                                       COIL_BOTTLE_IN_MOULD, COIL_PREFORM_AT_LOADER, COIL_PREFORM_IN_OVEN, HOST,
                                       HR_MODE_REQ, HR_MOULD_STEP, HR_OVEN_BAND, HR_OVEN_SETPOINT, HR_OVEN_TEMP,
                                       HR_STEP, MODE_AUTO, MODE_STOP, OVEN_BAND, OVEN_COLD, OVEN_HOT, OVEN_SETPOINT,
                                       PORT, RESET, SETTLE, START, Heartbeat, Plc, check, connect)

HR_RAW = 1037  # %MW13 BlowPressure_Raw
HR_BLOW_SETPOINT = 1058  # %MW34
COIL_ARM_AT_OVEN = 8194  # %MX0.2
COIL_ARM_AT_MOULD = 8195  # %MX0.3
COIL_ROD_UP = 8198  # %MX0.6
COIL_ROD_DOWN = 8199  # %MX0.7

BLOW_SETPOINT = 350
RAW_349, RAW_350 = 24000, 24192  # 24000 * 400 / 27648 = 347, 24192 -> 350
RAW_05_BAR, RAW_04_BAR = 400, 345  # 400 -> 5 (not below ExhaustLimit 5), 345 -> 4
GRIP_MIN, GRIP_MAX = 0.2, 0.7  # s: GRIP with GripTime 0.3 s (poll 50 ms, Modbus delays)
HOLD_MIN, HOLD_MAX = 0.9, 1.5  # s: HOLD with HoldTime 1.0 s


def main() -> int:
    claim_plant("test_blowmoulder_mould_seq", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_mould_seq")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)

    def mould() -> int:
        return plc.steps()[1]

    def wait_mould(expected: int, timeout: float) -> tuple[bool, list[int], float]:
        t0 = time.monotonic()
        seen: list[int] = []
        while True:
            s = mould()
            if not seen or seen[-1] != s:
                seen.append(s)
            if s == expected:
                return True, seen, time.monotonic() - t0
            if time.monotonic() - t0 > timeout:
                return False, seen, time.monotonic() - t0
            time.sleep(0.05)

    def reach(name: str, expected: int, what: str, timeout: float = 1.0) -> float:
        ok, seen, dt = wait_mould(expected, timeout)
        results.append(check(name, ok, f"{what} -> MouldStep {seen[-1]} after {dt:.2f} s (expected {expected}; seen {seen})"))
        return dt

    def hold(name: str, expected: int, what: str, seconds: float = 1.0) -> None:
        end = time.monotonic() + seconds
        seen: list[int] = []
        while time.monotonic() < end:
            s = mould()
            if not seen or seen[-1] != s:
                seen.append(s)
            time.sleep(0.05)
        results.append(check(name, seen == [expected], f"{what} -> MouldStep stays {expected} for {seconds:g} s (seen {seen})"))

    def sensors(**values: bool) -> None:
        """Write the sensors in the given order: put the one that blocks a transition first."""
        names = {"loader": COIL_PREFORM_AT_LOADER, "in_oven": COIL_PREFORM_IN_OVEN, "arm_oven": COIL_ARM_AT_OVEN,
                 "arm_mould": COIL_ARM_AT_MOULD, "closed": COIL_MOULD_CLOSED, "open": COIL_MOULD_OPEN,
                 "rod_up": COIL_ROD_UP, "rod_down": COIL_ROD_DOWN, "bottle": COIL_BOTTLE_IN_MOULD}
        for key, value in values.items():
            plc.coil(names[key], value)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address, value in ((HR_OVEN_TEMP, OVEN_COLD), (HR_OVEN_SETPOINT, OVEN_SETPOINT), (HR_OVEN_BAND, OVEN_BAND),
                               (HR_BLOW_SETPOINT, BLOW_SETPOINT), (HR_RAW, 0)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_ROD_UP, True)
        time.sleep(SETTLE)
        if not plc.coil(COIL_ROD_UP):
            raise SystemExit("Rod_Up does not follow the writes: release all forces in the debugger")
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        time.sleep(3.5)  # an Outfeed_Full left sealed in by C1_Full is released by the off-filter
        steps, lost = plc.steps(), plc.coil(COIL_COMMLOST)
        results.append(check("M1", steps == (0, 0) and not lost,
                             f"Mode Stop, heartbeat, Reset -> Step / MouldStep {steps}, Alm_CommLost {lost} "
                             "(expected (0, 0) / False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")

        # ---------- hand-over guards ----------
        sensors(in_oven=True, open=True, arm_oven=True, rod_up=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        steps = plc.steps()
        results.append(check("M2", steps == (20, 10), f"Start, preform in the pocket, oven cold, mould open + empty -> "
                                                      f"Step / MouldStep {steps} (expected (20, 10))"))
        hold("M3", 10, "oven in HEAT (not HOT)")
        sensors(rod_up=False)
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        ok, _, _ = plc.wait_step(25, 2)
        hold("M4", 10, f"oven HOT ({'reached' if ok else 'NOT reached'}), rod not up")
        sensors(bottle=True, rod_up=True)  # blocking sensor first: never all conditions TRUE in between
        hold("M5", 10, "something in the mould")
        sensors(arm_oven=False, bottle=False)
        hold("M6", 10, "arm not home")
        sensors(open=False, arm_oven=True)
        hold("M7", 10, "mould not open")
        sensors(open=True)
        reach("M8", 20, "all hand-over conditions")

        # ---------- one bottle ----------
        dt = reach("M9", 22, "GRIP (gripper has no sensor)")
        results[-1] = results[-1] and check("M9", GRIP_MIN <= dt <= GRIP_MAX,
                                            f"GRIP took {dt:.2f} s (expected {GRIP_MIN}-{GRIP_MAX} s, GripTime 0.3 s)")
        sensors(arm_oven=False, arm_mould=True)
        ok, seen, _ = wait_mould(24, 1)
        sensors(in_oven=False)
        oven_ok, _, _ = plc.wait_step(10, 1)
        results.append(check("M10", ok and oven_ok, f"arm at the mould -> MouldStep {seen[-1]}; preform out of the "
                                                    f"pocket -> Step {plc.steps()[0]} (expected 24 / 10)"))
        hold("M11", 24, "RELEASE, photo eye sees nothing")
        sensors(bottle=True)
        reach("M12", 26, "preform in the mould")
        sensors(arm_mould=False, arm_oven=True)
        reach("M13", 30, "arm home")
        # the oven station takes the next preform meanwhile (parallel)
        sensors(loader=True)
        plc.wait_step(15, 1)
        sensors(loader=False, in_oven=True)
        sensors(open=False, closed=True)
        reach("M14", 40, "mould closed")
        sensors(rod_up=False, rod_down=True)
        reach("M15", 50, "rod down")
        plc.write(HR_RAW, RAW_349)
        hold("M16", 50, "raw 24000 = 34.7 bar, below BlowSetpoint 35.0")
        plc.write(HR_RAW, RAW_350)
        reach("M17", 52, "raw 24192 = 35.0 bar")
        dt = reach("M18", 60, "HOLD", timeout=2.0)
        results[-1] = results[-1] and check("M18", HOLD_MIN <= dt <= HOLD_MAX,
                                            f"HOLD took {dt:.2f} s (expected {HOLD_MIN}-{HOLD_MAX} s, HoldTime 1.0 s)")
        plc.write(HR_RAW, RAW_05_BAR)
        hold("M19", 60, "raw 400 = 0.5 bar, not below ExhaustLimit")
        plc.write(HR_RAW, RAW_04_BAR)
        reach("M20", 62, "raw 345 = 0.4 bar (Pressure_Safe)")
        plc.write(HR_RAW, 0)
        sensors(rod_down=False, rod_up=True)
        reach("M21", 70, "rod up")
        sensors(closed=False, open=True)
        reach("M22", 80, "mould open")
        hold("M23", 80, "bottle still in the mould")

        # ---------- both stations in parallel ----------
        oven = plc.steps()[0]
        results.append(check("M24", oven == 25, f"next preform loaded and heated during the blow -> Step {oven} "
                                                "(expected 25 HOT)"))
        sensors(bottle=False)
        ok, seen, dt = wait_mould(20, 1)
        results.append(check("M25", ok, f"bottle out, next hot preform waiting -> MouldStep {seen[-1]} after {dt:.2f} s "
                                        f"(expected 20 at once; seen {seen})"))

        # ---------- restart by sensors ----------
        plc.mode(MODE_STOP)
        sensors(open=False, closed=True, rod_up=False, rod_down=True)
        plc.write(HR_RAW, RAW_350)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        start_steps = plc.steps()
        ok_start = start_steps[1] == 60
        hold("M26", 60, f"Mode Stop in GRIP, mould closed under 35 bar, Start -> {start_steps}")
        results[-1] = results[-1] and ok_start
        plc.write(HR_RAW, 0)
        ok1, _, _ = wait_mould(62, 1)
        sensors(rod_down=False, rod_up=True)
        ok2, _, _ = wait_mould(70, 1)
        sensors(closed=False, open=True, bottle=True)
        ok3, seen, _ = wait_mould(80, 1)
        results.append(check("M27", ok1 and ok2 and ok3, f"pressure gone -> 62, rod up -> 70, open -> {seen[-1]} "
                                                         f"(expected 62 / 70 / 80)"))
        sensors(in_oven=False, bottle=False)  # pocket empty first, else the next transfer starts at once
        ok, seen, _ = wait_mould(10, 1)
        results.append(check("M28", ok, f"bottle out -> MouldStep {seen[-1]} (expected 10; no hot preform in the oven)"))

        sensors(open=False, closed=True, rod_up=False, rod_down=True)
        plc.write(HR_RAW, RAW_350)  # pressure inside: after the restart the cycle waits in 60 EXHAUST
        plc.write(HR_MOULD_STEP, 52)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        after_estop = plc.steps()
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)
        plc.cmd(START)
        after_start = plc.steps()
        results.append(check("M29", after_estop == (0, 0) and after_start[1] == 60,
                             f"E-stop in HOLD -> {after_estop}, released + Start -> {after_start} "
                             "(expected (0, 0), then MouldStep 60)"))
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
