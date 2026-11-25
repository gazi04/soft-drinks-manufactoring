"""Blow moulder ladder stage 8: Stop at safe points, BottleCount, Starved, Alm_NoPreforms, State.

Runs against the blow moulder PLC on port 5025, playing the machine by writing the sensors (heartbeat
thread, E_Stop_OK, setpoint defaults as in test_blowmoulder_oven_seq.py).

Counter:
    C1  three Bottle_Ejected pulses -> BottleCount +3     C2  Cmd 3 (Reset) -> unchanged     C3  Cmd 4 -> 0
State and Starved:
    T1  Mode Stop -> State 0
    T2  Auto, Start, no preform, mould waiting, 1 s -> State 1 Running, Starved FALSE (filter 2 s)
    T3  3 s -> Starved TRUE, State 2         T4  preform at the loader -> Starved FALSE, State 1
    T5  FEED + Hopper_Empty -> Alm_NoPreforms TRUE; hopper refilled -> FALSE (warning, not latched)
    T6  no preform for 2.5 s while a bottle is blown (MouldStep 60) -> Starved FALSE, State 1
    T7  bottle waits in EJECT, C1 full -> State 3 Blocked, ejector off; C1 free -> ejector, bottle out -> 10
    T8  Step 900 (FAULT, written) -> State 9
    T9  HEAT 3 s, loader empty, mould waiting -> not Starved, State 1 (Feed_Empty_Timer only in FEED)
    T10 FEED, no preform, mould waiting, C1 full 3 s -> Starved TRUE but State 3 (Blocked wins)
Stop at safe points:
    P1  Stop in IDLE does nothing: Start afterwards, preform at the loader -> 15 LOAD (no latch left)
    P2  Stop in FEED with the mould waiting -> both 0, State 0
    P3  Stop in HEAT (a new preform already waits at the loader): the preform is heated, handed over and
        blown; the oven goes FEED -> 0 without loading the waiting one; the bottle is ejected -> both 0
    P4  restart after the Stop: Start -> FEED -> 15 LOAD (Stop_Request released)
    P5  Stop in EXHAUST (restart with 35 bar in the closed mould): oven FEED -> 0 at once, the mould
        finishes 62 -> 70 -> 80 -> 10 -> 0

    uv run python tests/test_blowmoulder_stop_state.py      (~80 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_blowmoulder_oven_seq import (COIL_BOTTLE_IN_MOULD, COIL_C1_FULL, COIL_COMMLOST, COIL_E_STOP_OK,
                                       COIL_MOULD_CLOSED, COIL_MOULD_OPEN, COIL_PREFORM_AT_LOADER, COIL_PREFORM_IN_OVEN,
                                       HOST, HR_MODE_REQ, HR_MOULD_STEP, HR_OVEN_BAND, HR_OVEN_SETPOINT, HR_OVEN_TEMP,
                                       HR_STEP, MODE_AUTO, MODE_STOP, OVEN_BAND, OVEN_COLD, OVEN_HOT, OVEN_SETPOINT,
                                       PORT, RESET, SETTLE, START, Heartbeat, Plc, check, connect)

STOP, COUNT_RESET = 2, 4
HR_RAW = 1037  # %MW13
HR_BLOW_SETPOINT = 1058  # %MW34
HR_STATE = 1074  # %MW50
HR_BOTTLE_COUNT = 1077  # %MW53
COIL_ARM_AT_OVEN, COIL_ARM_AT_MOULD, COIL_ROD_UP, COIL_ROD_DOWN = 8194, 8195, 8198, 8199
COIL_BOTTLE_EJECTED = 8225  # %MX4.1
COIL_HOPPER_EMPTY = 8226  # %MX4.2
COIL_STARVED = 8222  # %MX3.6
COIL_ALM_NO_PREFORMS = 8232  # %MX5.0
COIL_EJECTOR = 9  # %QX1.1
RAW_35_BAR = 24192


def main() -> int:
    claim_plant("test_blowmoulder_stop_state", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_stop_state")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []
    e_stop_before = plc.coil(COIL_E_STOP_OK)
    names = {"loader": COIL_PREFORM_AT_LOADER, "in_oven": COIL_PREFORM_IN_OVEN, "arm_oven": COIL_ARM_AT_OVEN,
             "arm_mould": COIL_ARM_AT_MOULD, "closed": COIL_MOULD_CLOSED, "open": COIL_MOULD_OPEN, "rod_up": COIL_ROD_UP,
             "rod_down": COIL_ROD_DOWN, "bottle": COIL_BOTTLE_IN_MOULD, "c1_full": COIL_C1_FULL,
             "hopper_empty": COIL_HOPPER_EMPTY}

    def sensors(**values: bool) -> None:
        """Written in the given order: put the one that blocks a transition first."""
        for key, value in values.items():
            plc.coil(names[key], value)

    def wait(expected: tuple[int | None, int | None], timeout: float = 2.0) -> tuple[bool, tuple[int, int]]:
        end = time.monotonic() + timeout
        while True:
            steps = plc.steps()
            if all(e is None or e == s for e, s in zip(expected, steps)):
                return True, steps
            if time.monotonic() > end:
                return False, steps
            time.sleep(0.02)

    def state() -> int:
        return plc.read(HR_STATE)

    def home() -> None:
        """Mode Stop, mould open and empty, arm home, rod up, oven pocket and loader empty, C1 free, 0 bar."""
        plc.mode(MODE_STOP)
        plc.clear_sensors()
        plc.write(HR_RAW, 0)
        plc.write(HR_OVEN_TEMP, OVEN_COLD)
        sensors(open=True, arm_oven=True, rod_up=True)

    def mould_cycle_from_grip() -> bool:
        """Drive the mould station by its sensors from GRIP (20) to WAIT_PREFORM (10)."""
        ok = wait((None, 22))[0]
        sensors(arm_oven=False, arm_mould=True)
        ok &= wait((None, 24))[0]
        sensors(in_oven=False, bottle=True)  # the arm took the preform out of the pocket, it is in the mould
        ok &= wait((None, 26))[0]
        sensors(arm_mould=False, arm_oven=True)
        ok &= wait((None, 30))[0]
        sensors(open=False, closed=True)
        ok &= wait((None, 40))[0]
        sensors(rod_up=False, rod_down=True)
        ok &= wait((None, 50))[0]
        plc.write(HR_RAW, RAW_35_BAR)
        ok &= wait((None, 60), 3)[0]
        return ok & finish_from_exhaust()

    def finish_from_exhaust() -> bool:
        plc.write(HR_RAW, 0)
        ok = wait((None, 62))[0]
        sensors(rod_down=False, rod_up=True)
        ok &= wait((None, 70))[0]
        sensors(closed=False, open=True)
        ok &= wait((None, 80))[0]
        sensors(bottle=False)
        return ok

    try:
        # ---------- setup ----------
        home()
        for address, value in ((HR_OVEN_SETPOINT, OVEN_SETPOINT), (HR_OVEN_BAND, OVEN_BAND), (HR_BLOW_SETPOINT, 350)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        plc.cmd(RESET)
        for address in (HR_STEP, HR_MOULD_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        time.sleep(3.5)  # an Outfeed_Full left sealed in is released by the off-filter
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")

        # ---------- counter ----------
        before = plc.read(HR_BOTTLE_COUNT)
        for _ in range(3):
            plc.coil(COIL_BOTTLE_EJECTED, True)
            time.sleep(0.15)
            plc.coil(COIL_BOTTLE_EJECTED, False)
            time.sleep(0.15)
        after = plc.read(HR_BOTTLE_COUNT)
        results.append(check("C1", after == min(before + 3, 32767),
                             f"3 Bottle_Ejected pulses -> BottleCount {before} -> {after} (expected +3)"))
        plc.cmd(RESET)
        kept = plc.read(HR_BOTTLE_COUNT)
        results.append(check("C2", kept == after, f"Cmd 3 (Reset) -> BottleCount {kept} (expected {after}: kept)"))
        plc.cmd(COUNT_RESET)
        zero = plc.read(HR_BOTTLE_COUNT)
        results.append(check("C3", zero == 0, f"Cmd 4 -> BottleCount {zero} (expected 0)"))

        # ---------- State and Starved ----------
        results.append(check("T1", state() == 0, f"Mode Stop -> State {state()} (expected 0 Stopped)"))
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(1.0)
        steps, starved, st = plc.steps(), plc.coil(COIL_STARVED), state()
        results.append(check("T2", steps == (10, 10) and not starved and st == 1,
                             f"Start, no preform, 1 s -> steps {steps}, Starved {starved}, State {st} "
                             "(expected (10, 10) / False / 1)"))
        time.sleep(2.0)
        starved, st = plc.coil(COIL_STARVED), state()
        results.append(check("T3", starved and st == 2, f"3 s without a preform -> Starved {starved}, State {st} "
                                                        "(expected True / 2)"))
        sensors(loader=True)
        time.sleep(SETTLE)
        starved, st = plc.coil(COIL_STARVED), state()
        results.append(check("T4", not starved and st == 1, f"preform at the loader -> Starved {starved}, State {st} "
                                                            "(expected False / 1)"))
        home()
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        sensors(hopper_empty=True)
        time.sleep(SETTLE)
        empty = plc.coil(COIL_ALM_NO_PREFORMS)
        sensors(hopper_empty=False)
        time.sleep(SETTLE)
        refilled = plc.coil(COIL_ALM_NO_PREFORMS)
        results.append(check("T5", empty and not refilled, f"FEED, Hopper_Empty -> Alm_NoPreforms {empty}; refilled -> "
                                                           f"{refilled} (expected True / False)"))
        home()
        sensors(open=False, closed=True, rod_up=False, rod_down=True)
        plc.write(HR_RAW, RAW_35_BAR)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(2.5)  # > StarvedDelay 2 s, < ExhaustTimeout 3 s (stage 9)
        steps, starved, st = plc.steps(), plc.coil(COIL_STARVED), state()
        results.append(check("T6", steps == (10, 60) and not starved and st == 1,
                             f"no preform 2.5 s while the mould blows (EXHAUST) -> steps {steps}, Starved {starved}, "
                             f"State {st} (expected (10, 60) / False / 1)"))
        home()
        sensors(bottle=True, c1_full=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(2.5)  # Outfeed_Full on-filter 2 s
        steps, st, ejector = plc.steps(), state(), plc.coil(COIL_EJECTOR)
        sensors(c1_full=False)
        time.sleep(3.5)  # off-filter 3 s
        ejector_after = plc.coil(COIL_EJECTOR)
        sensors(bottle=False)
        ok, after_eject = wait((None, 10), 1)
        results.append(check("T7", steps == (10, 80) and st == 3 and not ejector and ejector_after and ok,
                             f"bottle in EJECT, C1 full -> steps {steps}, State {st}, Ejector {ejector}; C1 free -> "
                             f"Ejector {ejector_after}; bottle out -> {after_eject} (expected (10, 80) / 3 / False / "
                             "True / MouldStep 10)"))
        home()
        sensors(in_oven=True)  # oven cold: HEAT for 3 s, loader empty, the mould waits
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(3.0)
        steps, starved, st = plc.steps(), plc.coil(COIL_STARVED), state()
        results.append(check("T9", steps == (20, 10) and not starved and st == 1,
                             f"HEAT 3 s, loader empty, mould waiting -> steps {steps}, Starved {starved}, State {st} "
                             "(expected (20, 10) / False / 1: the timer runs only in FEED)"))
        home()
        sensors(c1_full=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        time.sleep(3.0)
        starved, st = plc.coil(COIL_STARVED), state()
        results.append(check("T10", starved and st == 3, f"FEED, no preform, mould waiting, C1 full 3 s -> Starved "
                                                         f"{starved}, State {st} (expected True / 3: Blocked wins)"))
        sensors(c1_full=False)
        time.sleep(3.5)  # Outfeed_Full off-filter 3 s: else FEED -> LOAD stays blocked in the Stop checks
        plc.write(HR_STEP, 900)
        time.sleep(SETTLE)
        st = state()
        plc.mode(MODE_STOP)
        plc.write(HR_STEP, 0)
        plc.write(HR_MOULD_STEP, 0)
        results.append(check("T8", st == 9, f"Step 900 (FAULT) -> State {st} (expected 9)"))

        # ---------- Stop at safe points ----------
        home()
        plc.mode(MODE_AUTO)
        plc.cmd(STOP)  # in IDLE: must not leave a latch behind
        plc.cmd(START)
        sensors(loader=True)
        ok, steps = wait((15, None), 1)
        results.append(check("P1", ok, f"Stop in IDLE, then Start + preform at the loader -> steps {steps} "
                                       "(expected Step 15 LOAD)"))
        home()
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        plc.cmd(STOP)
        ok, steps = wait((0, 0), 1)
        results.append(check("P2", ok and state() == 0, f"Stop in FEED, mould waiting -> steps {steps}, State {state()} "
                                                        "(expected (0, 0) / 0)"))
        home()
        sensors(in_oven=True)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        heat_ok, _ = wait((20, 10), 1)
        plc.cmd(STOP)
        sensors(loader=True)  # a new preform already waits: it must NOT be loaded
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        grip_ok, _ = wait((None, 20), 3)
        cycle_ok = mould_cycle_from_grip()
        ok, steps = wait((0, 0), 1)
        results.append(check("P3", heat_ok and grip_ok and cycle_ok and ok,
                             f"Stop in HEAT -> heated, handed over, blown, ejected; oven not loaded again -> steps "
                             f"{steps} (expected (0, 0); HEAT {heat_ok}, GRIP {grip_ok}, cycle {cycle_ok})"))
        plc.cmd(START)
        ok, steps = wait((15, None), 1)
        results.append(check("P4", ok, f"Start again, preform at the loader -> steps {steps} (expected Step 15: "
                                       "Stop_Request released)"))
        home()
        sensors(open=False, closed=True, rod_up=False, rod_down=True, bottle=True)
        plc.write(HR_RAW, RAW_35_BAR)
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        start_ok, _ = wait((10, 60), 1)
        plc.cmd(STOP)
        oven_ok, _ = wait((0, 60), 1)
        cycle_ok = finish_from_exhaust()
        ok, steps = wait((0, 0), 1)
        results.append(check("P5", start_ok and oven_ok and cycle_ok and ok,
                             f"Stop in EXHAUST -> oven 0 at once, mould 62 -> 70 -> 80 -> 10 -> 0 -> steps {steps} "
                             f"(expected (0, 0); start {start_ok}, oven {oven_ok}, cycle {cycle_ok})"))
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
          "BottleCount 0, E_Stop_OK as before)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
