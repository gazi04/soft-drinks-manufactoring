"""Stage 8 test: alarms, timeouts and FAULT, over Modbus.

Needs section 8 of plcs/filler main running in the runtime:
    Jam_Timer     TON 5 s,  IN = Step_Index               -> (S) Alm_IndexJam      (FAULT)
    Fill_Timer    TON 10 s, IN = Step_Work AND FillValve  -> (S) Alm_FillTimeout   (FAULT)
    Blocked_Timer TON 2 min, IN = Step_Wait               -> (S) Alm_OutfeedBlocked (warning)
    (R) each alarm: Reset_Cmd AND NOT <timer>.Q   (only when the cause is gone)
    last rung: Alm_IndexJam OR Alm_FillTimeout -> MOVE 900 -> Step

Takes about 4 minutes (the blocked alarm needs 2 min of WAIT_OUTFEED).
Helpers and addresses come from test_outputs.py (same folder).

    uv run python tests/test_alarms.py           # all checks
    uv run python tests/test_alarms.py --quick   # without the 2 min blocked alarm
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from test_outputs import (
    CMD, E_STOP_OK, FILL_LEVEL, FILL_VALVE, HOST, INDEX_MOTOR, MAN_FILL, MODE_REQ, PORT,
    PRODUCT_AVAILABLE, S4, S5, S7, S8, STEP, Heartbeat, check, clear_inputs, prepare,
    read_coil, read_hr, set_coil, start_work, write_defaults, write_hr,
)

ALM_INDEXJAM = 8232  # %MX5.0
ALM_FILLTIMEOUT = 8233  # %MX5.1
ALM_OUTFEEDBLOCKED = 8234  # %MX5.2
OUTFEED_FULL = 8216  # %MX3.0
STATE = 1074  # %MW50


def step(client):
    return read_hr(client, STEP)


def wait_for_coil_true(client, address, timeout):
    """Seconds until the coil is TRUE, or None."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if read_coil(client, address):
            return time.monotonic() - start
        time.sleep(0.05)
    return None


def stays_false(client, address, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if read_coil(client, address):
            return False
        time.sleep(0.05)
    return True


def check_time(name, expected, elapsed, tolerance=0.5):
    if elapsed is None:
        print(f"FAIL  {name}: expected ~{expected:.1f} s, alarm never came")
        return False
    ok = abs(elapsed - expected) <= tolerance
    print(f"{'PASS' if ok else 'FAIL'}  {name}: expected ~{expected:.1f} s, got {elapsed:.2f} s")
    return ok


def pulse_s8(client):
    set_coil(client, S8, False)
    set_coil(client, S8, True)
    set_coil(client, S8, False)


def no_false_alarm_checks(client):
    """Waiting is not a fault: long WORK steps without an open fill valve must not alarm."""
    r = []
    prepare(client)
    start_work(client)
    r.append(check("N1 empty machine 12 s in WORK -> no fill timeout", True,
                   stays_false(client, ALM_FILLTIMEOUT, 12.0)))
    r.append(check("N1b still WORK (Starved), no FAULT", 10, step(client)))

    prepare(client)
    start_work(client)
    set_coil(client, PRODUCT_AVAILABLE, False)
    set_coil(client, S4, True)  # bottle at S4, level 0, no product -> valve closed
    r.append(check("N2 bottle at S4, no product 12 s -> no fill timeout", True,
                   stays_false(client, ALM_FILLTIMEOUT, 12.0)))
    r.append(check("N2b still WORK, State 2 Starved", (10, 2), (step(client), read_hr(client, STATE))))

    prepare(client)
    write_hr(client, MODE_REQ, 2)
    set_coil(client, S4, True)
    set_coil(client, MAN_FILL, True)
    r.append(check("N3 Manual, Man_Fill held 12 s -> no fill timeout (Auto only)", True,
                   stays_false(client, ALM_FILLTIMEOUT, 12.0)))
    clear_inputs(client)
    return r


def index_jam_checks(client):
    r = []
    prepare(client)
    start_work(client)

    # J1 S8 comes within 4 s -> no alarm, timer restarts in every INDEX
    write_hr(client, FILL_LEVEL, 500)
    set_coil(client, S4, True)  # filled -> INDEX
    time.sleep(3.7)
    set_coil(client, S4, False)
    pulse_s8(client)
    r.append(check("J1 S8 after ~4 s -> WORK, no jam alarm", (10, False),
                   (step(client), read_coil(client, ALM_INDEXJAM))))

    # J2 no S8 -> jam alarm after 5 s, FAULT, belt OFF
    set_coil(client, S4, True)
    t0 = time.monotonic()
    if step(client) != 20:
        raise SystemExit("S4 filled does not reach INDEX (20): check stages 4/5")
    elapsed = wait_for_coil_true(client, ALM_INDEXJAM, 7.0)
    r.append(check_time("J2a INDEX without S8 -> Alm_IndexJam", 5.0,
                        None if elapsed is None else time.monotonic() - t0))
    time.sleep(0.3)
    r.append(check("J2b jam -> FAULT 900, State 9", (900, 9), (step(client), read_hr(client, STATE))))
    r.append(check("J2c FAULT -> belt OFF", False, read_coil(client, INDEX_MOTOR)))

    # J3 Start in FAULT is ignored
    write_hr(client, CMD, 1)
    r.append(check("J3 Start in FAULT -> ignored", 900, step(client)))

    # J4 one Reset: alarm cleared AND IDLE (reset rungs above the MOVE 900 rung)
    write_hr(client, CMD, 3)
    r.append(check("J4 one Reset -> alarm cleared, IDLE", (False, 0),
                   (read_coil(client, ALM_INDEXJAM), step(client))))

    # J5 Start after the fault -> WORK continues (bottle still at S4, full -> INDEX again)
    write_hr(client, CMD, 1)
    r.append(check("J5 Start after Reset -> runs again (INDEX)", 20, step(client)))
    set_coil(client, S4, False)
    pulse_s8(client)
    return r


def fill_timeout_checks(client):
    r = []
    prepare(client)
    start_work(client)

    # F1 valve open, level stuck at 200 -> fill timeout after 10 s, FAULT, valve closed
    write_hr(client, FILL_LEVEL, 200)
    set_coil(client, S4, True)
    t0 = time.monotonic()
    r.append(check("F1a bottle at S4 -> FillValve ON", True, read_coil(client, FILL_VALVE)))
    elapsed = wait_for_coil_true(client, ALM_FILLTIMEOUT, 12.0)
    r.append(check_time("F1b level stuck -> Alm_FillTimeout", 10.0,
                        None if elapsed is None else time.monotonic() - t0))
    time.sleep(0.3)
    r.append(check("F1c fill timeout -> FAULT 900, valve OFF", (900, False),
                   (step(client), read_coil(client, FILL_VALVE))))

    # F2 Reset + Start -> the half-full bottle is topped up (rule 7), then INDEX
    write_hr(client, CMD, 3)
    r.append(check("F2a Reset -> alarm cleared, IDLE", (False, 0),
                   (read_coil(client, ALM_FILLTIMEOUT), step(client))))
    write_hr(client, CMD, 1)
    r.append(check("F2b Start -> WORK, valve opens again (top up)", (10, True),
                   (step(client), read_coil(client, FILL_VALVE))))
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("F2c level reached -> valve OFF, INDEX", (False, 20),
                   (read_coil(client, FILL_VALVE), step(client))))
    set_coil(client, S4, False)
    pulse_s8(client)
    return r


def blocked_checks(client):
    r = []
    prepare(client)
    set_coil(client, S7, True)
    time.sleep(2.5)
    if not read_coil(client, OUTFEED_FULL):
        raise SystemExit("Outfeed_Full not set after 2.5 s of S7: check stage 3 rungs")
    start_work(client)
    set_coil(client, S5, True)  # cap 1.5 s -> WAIT_OUTFEED
    time.sleep(2.0)
    if step(client) != 15:
        raise SystemExit("cap done with C2 full does not reach WAIT_OUTFEED (15)")
    t0 = time.monotonic()
    print("      waiting about 2 min in WAIT_OUTFEED ...")
    r.append(check("B1 blocked 1 min 55 s -> no alarm yet", True,
                   stays_false(client, ALM_OUTFEEDBLOCKED, 115.0)))
    elapsed = wait_for_coil_true(client, ALM_OUTFEEDBLOCKED, 10.0)
    r.append(check_time("B2a blocked -> Alm_OutfeedBlocked", 120.0,
                        None if elapsed is None else time.monotonic() - t0, tolerance=1.0))
    r.append(check("B2b warning only: still WAIT_OUTFEED, State 3", (15, 3),
                   (step(client), read_hr(client, STATE))))

    # B3 Reset while still blocked -> alarm stays (cause not gone)
    write_hr(client, CMD, 3)
    r.append(check("B3 Reset while still blocked -> alarm stays", True,
                   read_coil(client, ALM_OUTFEEDBLOCKED)))

    # B4 C2 free -> continues by itself; warning stays latched until Reset
    set_coil(client, S7, False)
    time.sleep(3.5)  # Outfeed_Full released after 3 s
    r.append(check("B4a C2 free -> continues by itself (INDEX)", 20, step(client)))
    r.append(check("B4b warning still latched", True, read_coil(client, ALM_OUTFEEDBLOCKED)))
    write_hr(client, CMD, 3)
    r.append(check("B4c Reset -> warning cleared, machine keeps running", (False, 20),
                   (read_coil(client, ALM_OUTFEEDBLOCKED), step(client))))
    set_coil(client, S5, False)
    pulse_s8(client)
    return r


def main():
    quick = "--quick" in sys.argv
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = (no_false_alarm_checks(client) + index_jam_checks(client)
                   + fill_timeout_checks(client))
        if not quick:
            results += blocked_checks(client)
    finally:
        clear_inputs(client)
        client.write_coil(E_STOP_OK, True)
        client.write_coil(PRODUCT_AVAILABLE, True)
        client.write_register(FILL_LEVEL, 0)
        write_defaults(client)
        client.write_register(MODE_REQ, 1)
        client.write_register(CMD, 3)
        time.sleep(0.3)
        client.write_register(STEP, 0)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()

    print()
    print(f"{sum(results)}/{len(results)} checks passed")
    print("Alarms OK" if all(results) else "Alarms NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
