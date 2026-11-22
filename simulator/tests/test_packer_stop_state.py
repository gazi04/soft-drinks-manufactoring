"""Packer ladder stage 8: Stop at safe points, PackCount with Cmd = 4, Starved, State.

Runs against the packer PLC on port 5024. Plays the machine by writing the sensors (helpers from
test_packer_collation.py / test_packer_wrap.py) and watches Step (%MW51), WrapStep (%MW52), State
(%MW50), GroupCount, FeedCount and PackCount (%MW53).

Stop at safe points (Cmd = 2, mode stays Auto):
    S1  Stop in COUNT after 3 bottles -> Step 0 and WrapStep 0 (WAIT_PACK) at once, GroupCount 3 kept
    S2  Start -> counts on: 3 more bottles -> Group_Complete, Step 15
    S3  Stop in PUSH -> Step stays 20 (a push is finished), WrapStep 0 at once
    S4  Pusher_Out -> 25, stays 25 while the pusher is out;  S5  Pusher_Home -> Step 0 (not 10)
    S6  Stop in FEED after 8 pulses -> WrapStep 0 at once, FeedCount 8 kept
    S7  Start -> 20 again, 8 pulses -> stays 20, 9th -> 22 -> 24 (only the rest is fed)
    S8  Stop in READY -> WrapStep 0, FeedCount 0 (sheet cut, waits in the slot)
    S9  Start with the cut sheet in the slot -> 10 -> 24 -> 30, never 20 (no second sheet)
    S10 Stop in RUN_ON -> stays 30;  S11  pack on the sheet -> 32, stays 32 (no stop in WRAP)
    S12 bar home -> 34, stays 34;  S13  Oven_Entry -> WrapStep 0 (not 10), Step 0
    S14 Start afterwards -> both run again (Stop_Request released when both were 0)
PackCount:
    P1  2 Oven_Exit pulses -> PackCount +2;  P2  Cmd 3 (Reset) -> unchanged;  P3  Cmd 4 -> 0
State (0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault):
    T1  Mode Stop -> 0;  T2  Auto + Start, bottles at the gate -> 1
    T3  gate gap 1 s -> still 1;  T4  gap 3 s, no pack at the film -> 2
    T5  same gap, a pack at the film (FEED, being wrapped) -> 1
    T6  Film_Empty with a pack waiting at the film -> 2
    T7  READY with Outfeed_Full -> 3;  T8  table free -> RUN_ON, 1
    T9  Step / WrapStep 900 (written) -> 9, Mode Stop keeps 9;  T10 Manual -> 0

    uv run python tests/test_packer_stop_state.py      (~60 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_packer_collation import (COIL_BOTTLE_AT_GATE, COIL_BOTTLE_COUNTED, COIL_E_STOP_OK, COIL_FILM_IN_SLOT,
                                   COIL_FILM_PULSE, COIL_PACK_AT_FILM, COIL_PUSHER_HOME, COIL_PUSHER_OUT,
                                   COIL_WRAP_HOME, HR_GROUP_COUNT, HR_MODE_REQ, HR_OVEN_BAND, HR_OVEN_HYST,
                                   HR_OVEN_SETPOINT, HR_OVEN_TEMP, HR_STEP, HR_WRAP_STEP, MODE_AUTO, MODE_STOP,
                                   OVEN_BAND, OVEN_HOT, OVEN_HYST, OVEN_SETPOINT, RESET, SETTLE, START, Heartbeat,
                                   Plc, check, connect, zero_group)
from test_packer_oven_outputs import COIL_OVEN_EXIT, empty_oven
from test_packer_wrap import (COIL_FILM_EMPTY, COIL_OVEN_ENTRY, COIL_TABLE_FULL, EXTRA_SENSORS, FILM_LENGTH,
                              HR_FEED_COUNT, HR_FILM_LENGTH, wait_outfeed, zero_feed)

HOST = "127.0.0.1"
PORT = 5024

STOP, COUNT_RESET = 2, 4
MODE_MANUAL = 2
HR_STATE = 1074  # %MW50
HR_PACK_COUNT = 1077  # %MW53
COIL_COMMLOST = 8235  # %MX5.3
COLL, WRAP = 0, 1  # word index in Plc.steps()


def main() -> int:
    claim_plant("test_packer_stop_state", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_stop_state")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> bool:
        results.append(check(name, ok, detail))
        return ok

    def must(word: int, expected: int, timeout: float = 1.0) -> None:
        ok, seen, _ = plc.wait_step(expected, timeout, word)
        if not ok:
            raise SystemExit(f"cannot reach {'WrapStep' if word else 'Step'} {expected} for the next check, seen {seen}")

    def reach(name: str, word: int, expected: int, timeout: float, what: str) -> list[int]:
        ok, seen, dt = plc.wait_step(expected, timeout, word)
        report(name, ok, f"{what} -> {'WrapStep' if word else 'Step'} {seen[-1]} after {dt:.1f} s "
                         f"(expected {expected}; seen {seen})")
        return seen

    def hold(name: str, word: int, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds, word)
        report(name, ok, f"{what} -> {'WrapStep' if word else 'Step'} stays {expected} for {seconds:g} s (seen {seen})")

    def state(name: str, expected: int, what: str) -> None:
        time.sleep(SETTLE)
        value = plc.read(HR_STATE)
        report(name, value == expected, f"{what} -> State {value} (expected {expected}), steps {plc.steps()}")

    def start() -> None:
        plc.mode(MODE_STOP)
        plc.mode(MODE_AUTO)
        plc.cmd(START)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        for address, value in ((HR_OVEN_TEMP, OVEN_HOT), (HR_OVEN_SETPOINT, OVEN_SETPOINT),
                               (HR_OVEN_HYST, OVEN_HYST), (HR_OVEN_BAND, OVEN_BAND), (HR_FILM_LENGTH, FILM_LENGTH)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_FILM_PULSE, True)  # the sensor writes must reach the PLC: a debugger force would block them
        time.sleep(SETTLE)
        if not plc.coil(COIL_FILM_PULSE):
            raise SystemExit("Film_Pulse does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_FILM_PULSE, False)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset
        plc.cmd(RESET)
        for address in (HR_STEP, HR_WRAP_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")
        if not wait_outfeed(plc, False):
            raise SystemExit("Outfeed_Full stays TRUE with Table_Full FALSE: check the stage 3 rungs")
        zero_group(plc)
        zero_feed(plc)
        empty_oven(plc)

        # ---------- Stop in the collation ----------
        plc.coil(COIL_PUSHER_HOME, True)
        plc.coil(COIL_WRAP_HOME, True)
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        plc.coil(COIL_FILM_EMPTY, True)  # wrap side waits in WAIT_PACK with the pack at the film
        plc.coil(COIL_PACK_AT_FILM, True)
        start()
        must(COLL, 10)
        must(WRAP, 10)
        plc.pulse(COIL_BOTTLE_COUNTED, 3)
        plc.cmd(STOP)
        steps, count = plc.steps(), plc.read(HR_GROUP_COUNT)
        report("S1", steps == (0, 0) and count == 3,
               f"Stop in COUNT / WAIT_PACK -> steps {steps}, GroupCount {count} (expected (0, 0) / 3)")
        plc.cmd(START)
        must(COLL, 10)
        plc.pulse(COIL_BOTTLE_COUNTED, 3)
        ok, seen, _ = plc.wait_step(15, 1.0, COLL)
        count = plc.read(HR_GROUP_COUNT)
        report("S2", ok and count == 6, f"Start, 3 more bottles -> Step {seen[-1]}, GroupCount {count} (expected 15 / 6)")
        plc.coil(COIL_PACK_AT_FILM, False)
        must(COLL, 20, 5)
        plc.coil(COIL_PUSHER_HOME, False)
        plc.cmd(STOP)
        ok, seen = plc.stays(20, 0.5, COLL)
        wrap = plc.steps()[WRAP]
        report("S3", ok and wrap == 0, f"Stop in PUSH -> Step {seen} for 0.5 s, WrapStep {wrap} (expected [20] / 0)")
        plc.coil(COIL_PUSHER_OUT, True)
        must(COLL, 25)
        hold("S4", COLL, 25, 0.5, "Pusher_Out (stop pending), pusher still out")
        plc.coil(COIL_PUSHER_OUT, False)
        plc.coil(COIL_PUSHER_HOME, True)
        reach("S5", COLL, 0, 1.0, "Pusher_Home at the end of RETRACT, stop pending")

        # ---------- Stop in the wrap ----------
        plc.coil(COIL_FILM_EMPTY, False)
        plc.coil(COIL_PACK_AT_FILM, True)
        plc.cmd(START)
        must(WRAP, 20)
        plc.pulse(COIL_FILM_PULSE, 1)
        plc.coil(COIL_FILM_IN_SLOT, True)  # the film tip comes out of the slot
        plc.pulse(COIL_FILM_PULSE, 7)
        plc.cmd(STOP)
        steps, count = plc.steps(), plc.read(HR_FEED_COUNT)
        report("S6", steps == (0, 0) and count == 8,
               f"Stop in FEED after 8 pulses -> steps {steps}, FeedCount {count} (expected (0, 0) / 8)")
        plc.cmd(START)
        must(WRAP, 20)
        plc.pulse(COIL_FILM_PULSE, 8)
        ok, seen = plc.stays(20, 0.3, WRAP)
        plc.coil(COIL_FILM_IN_SLOT, False)  # READY must wait for the sheet sensor (else 24 -> 30 at once)
        plc.pulse(COIL_FILM_PULSE)
        ok2, seen2, _ = plc.wait_step(24, 1.5, WRAP)
        report("S7", ok and ok2, f"restart, 8 pulses -> WrapStep {seen}, 9th -> {seen2} (expected [20] / ... 22, 24)")
        plc.cmd(STOP)
        time.sleep(SETTLE)
        steps, count = plc.steps(), plc.read(HR_FEED_COUNT)
        report("S8", steps == (0, 0) and count == 0,
               f"Stop in READY -> steps {steps}, FeedCount {count} (expected (0, 0) / 0)")
        plc.coil(COIL_FILM_IN_SLOT, True)
        plc.cmd(START)
        ok, seen, _ = plc.wait_step(30, 1.0, WRAP)
        report("S9", ok and 20 not in seen, f"Start with a cut sheet in the slot -> WrapSteps {seen} (expected 30, never 20)")
        plc.cmd(STOP)
        hold("S10", WRAP, 30, 0.5, "Stop in RUN_ON")
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)
        must(WRAP, 32)
        hold("S11", WRAP, 32, 0.5, "pack on the sheet, stop pending")
        plc.coil(COIL_WRAP_HOME, False)
        time.sleep(SETTLE)
        plc.coil(COIL_WRAP_HOME, True)
        must(WRAP, 34)
        hold("S12", WRAP, 34, 0.5, "bar home, stop pending")
        plc.pulse(COIL_OVEN_ENTRY)
        time.sleep(SETTLE)
        steps = plc.steps()
        report("S13", steps == (0, 0), f"Oven_Entry at the end of RUN_OUT -> steps {steps} (expected (0, 0))")
        plc.cmd(START)
        ok, seen = plc.stays(10, 0.5, COLL)
        wrap = plc.steps()[WRAP]
        report("S14", ok and wrap == 10, f"Start after the stop -> Step {seen}, WrapStep {wrap} "
                                         "(expected [10] / 10: the stop request is gone)")

        # ---------- PackCount ----------
        plc.mode(MODE_STOP)
        p0 = plc.read(HR_PACK_COUNT)
        plc.pulse(COIL_OVEN_EXIT, 2)
        p1 = plc.read(HR_PACK_COUNT)
        report("P1", p1 == p0 + 2, f"2 Oven_Exit pulses -> PackCount {p0} -> {p1} (expected {p0 + 2})")
        plc.cmd(RESET)
        p2 = plc.read(HR_PACK_COUNT)
        report("P2", p2 == p1, f"Cmd 3 (Reset) -> PackCount {p2} (expected {p1})")
        plc.cmd(COUNT_RESET)
        p3 = plc.read(HR_PACK_COUNT)
        report("P3", p3 == 0, f"Cmd 4 -> PackCount {p3} (expected 0)")
        empty_oven(plc)

        # ---------- State ----------
        state("T1", 0, "Mode Stop")
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        start()
        must(COLL, 10)
        must(WRAP, 10)
        state("T2", 1, "Auto + Start, COUNT / WAIT_PACK, bottles at the gate")
        plc.coil(COIL_BOTTLE_AT_GATE, False)
        time.sleep(1.0)
        state("T3", 1, "no bottle at the gate for 1 s")
        time.sleep(2.0)
        state("T4", 2, "no bottle at the gate for 3 s, no pack at the film")
        plc.coil(COIL_PACK_AT_FILM, True)
        must(WRAP, 20)
        state("T5", 1, "same gap, pack at the film (FEED)")
        plc.coil(COIL_FILM_EMPTY, True)
        plc.coil(COIL_FILM_IN_SLOT, False)
        plc.coil(COIL_BOTTLE_AT_GATE, True)
        zero_feed(plc)  # FEED interrupted above: WAIT_PACK would feed the rest, Film_Empty blocks only a new sheet
        start()
        must(WRAP, 10)
        state("T6", 2, "Film_Empty, pack waiting at the film, bottles at the gate")
        plc.mode(MODE_STOP)
        plc.coil(COIL_FILM_EMPTY, False)
        plc.coil(COIL_TABLE_FULL, True)
        if not wait_outfeed(plc, True):
            raise SystemExit("Outfeed_Full not TRUE within 5 s of Table_Full")
        plc.coil(COIL_FILM_IN_SLOT, True)  # cut sheet waiting: WAIT_PACK -> READY directly
        start()
        must(WRAP, 24)
        state("T7", 3, "READY, Outfeed_Full")
        plc.coil(COIL_TABLE_FULL, False)
        must(WRAP, 30, 5)
        state("T8", 1, "table free -> RUN_ON")
        plc.write(HR_STEP, 900)
        plc.write(HR_WRAP_STEP, 900)
        state("T9", 9, "Step / WrapStep 900 (FAULT, written)")
        plc.mode(MODE_STOP)
        state("T9b", 9, "Mode Stop in FAULT (abort keeps 900)")
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.mode(MODE_MANUAL)
        state("T10", 0, "Manual")
    finally:
        # end state for the next script: Mode Stop, steps 0, oven empty, sensors off, E_Stop_OK TRUE
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)
        empty_oven(plc)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, steps 0, sensors off, E_Stop_OK TRUE)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
