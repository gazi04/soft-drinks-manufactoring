"""Stage 7 test: Stop request at safe points, and the State word, over Modbus.

Needs these rungs of plcs/filler main running in the runtime:
    Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT Step_Idle      (above the transitions)
    Transition -> 0: Seq_Enable AND Stop_Request AND
                     (Step_Work AND Jobs_Done OR Step_Wait OR Step_Index AND S8_Edge)
    NOT Stop_Request in the transitions 10 -> 15, 10 -> 20, 15 -> 20, 20 -> 10
    Starved := Step_Work AND (NOT Bottles_Present OR NOT Fill_Done AND NOT Product_Available)
    State (%MW50): 0 Stopped (IDLE), 1 Running (WORK not starved, INDEX),
                   2 Starved, 3 Blocked (WAIT_OUTFEED), 9 Fault

Helpers and addresses come from test_outputs.py (same folder).

    uv run python tests/test_stop_state.py
"""

import time

from pymodbus.client import ModbusTcpClient

from test_outputs import (
    CMD, E_STOP_OK, FILL_LEVEL, HOST, INDEX_MOTOR, MODE_REQ, PORT, PRODUCT_AVAILABLE,
    RINSE_VALVE, S3, S4, S5, S7, S8, STEP, Heartbeat, check, clear_inputs, prepare,
    read_coil, read_hr, set_coil, start_work, write_defaults, write_hr,
)

STATE = 1074  # %MW50
STOP = 2  # Cmd value
OUTFEED_FULL = 8216  # %MX3.0


def read_step(client):
    return read_hr(client, STEP)


def read_state(client):
    return read_hr(client, STATE)


def press_stop(client):
    write_hr(client, CMD, STOP)


def pulse_s8(client):
    set_coil(client, S8, False)
    set_coil(client, S8, True)
    set_coil(client, S8, False)


def watch_steps(client, seconds):
    """Poll Step for `seconds`, return the list of distinct steps seen, in order."""
    seen = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        step = read_step(client)
        if not seen or seen[-1] != step:
            seen.append(step)
        time.sleep(0.02)
    return seen


def stop_checks(client):
    r = []
    prepare(client)
    start_work(client)

    # S1 Stop while rinsing: the job finishes, then IDLE instead of INDEX
    set_coil(client, S3, True)
    time.sleep(0.5)
    press_stop(client)
    r.append(check("S1a Stop during rinse -> still WORK", 10, read_step(client)))
    r.append(check("S1b Stop during rinse -> valve still open (job finishes)", True,
                   read_coil(client, RINSE_VALVE)))
    steps = watch_steps(client, 2.0)  # rinse ends ~2 s after the bottle arrived
    r.append(check("S1c rinse done -> IDLE, never INDEX", [10, 0], steps))

    # S2 Start again with the bottle still inside -> WORK continues (rinse again), then INDEX
    write_hr(client, CMD, 1)
    r.append(check("S2a Start after Stop -> WORK", 10, read_step(client)))
    steps = watch_steps(client, 2.5)
    r.append(check("S2b normal cycle again: WORK -> INDEX (latch cleared in IDLE)", [10, 20], steps))

    # S3 Stop during INDEX: the belt finishes its move, S8 edge -> IDLE instead of WORK
    press_stop(client)
    r.append(check("S3a Stop during INDEX -> still INDEX", 20, read_step(client)))
    r.append(check("S3b Stop during INDEX -> belt keeps running", True, read_coil(client, INDEX_MOTOR)))
    set_coil(client, S3, False)
    pulse_s8(client)
    r.append(check("S3c S8 edge -> IDLE, not WORK", 0, read_step(client)))
    r.append(check("S3d IDLE -> belt OFF", False, read_coil(client, INDEX_MOTOR)))

    # S4 Stop in IDLE does nothing and is not remembered: a later Start works normally
    press_stop(client)
    r.append(check("S4a Stop in IDLE -> stays IDLE", 0, read_step(client)))
    write_hr(client, CMD, 1)
    set_coil(client, S3, True)
    steps = watch_steps(client, 2.5)
    r.append(check("S4b Start after Stop in IDLE -> normal WORK -> INDEX", [10, 20], steps))
    set_coil(client, S3, False)
    pulse_s8(client)

    # S5 Stop in an empty machine (WORK, all stations empty = all jobs done) -> IDLE at once
    press_stop(client)
    r.append(check("S5 Stop in empty WORK -> IDLE at once", 0, read_step(client)))
    return r


def wait_checks(client):
    """WAIT_OUTFEED: State Blocked, and Stop leaves it at once."""
    r = []
    set_coil(client, S7, True)
    time.sleep(2.5)  # S7 ON > 2 s -> Outfeed_Full
    if not read_coil(client, OUTFEED_FULL):
        raise SystemExit("Outfeed_Full not set after 2.5 s of S7: check stage 3 rungs")
    start_work(client)
    set_coil(client, S5, True)  # cap 1.5 s, then S5 AND Outfeed_Full -> 15
    steps = watch_steps(client, 2.0)
    r.append(check("W1 cap done, C2 full -> WAIT_OUTFEED", [10, 15], steps))
    r.append(check("W2 WAIT_OUTFEED -> State 3 Blocked", 3, read_state(client)))
    press_stop(client)
    r.append(check("W3 Stop in WAIT_OUTFEED -> IDLE at once", 0, read_step(client)))
    r.append(check("W4 IDLE -> State 0 Stopped", 0, read_state(client)))
    clear_inputs(client)
    time.sleep(3.5)  # S7 OFF > 3 s -> Outfeed_Full released
    return r


def state_checks(client):
    r = []
    prepare(client)
    r.append(check("T1 IDLE -> State 0 Stopped", 0, read_state(client)))

    start_work(client)
    r.append(check("T2 WORK, no bottles -> State 2 Starved", 2, read_state(client)))

    set_coil(client, S3, True)
    r.append(check("T3 WORK, rinsing -> State 1 Running", 1, read_state(client)))
    set_coil(client, S3, False)

    write_hr(client, FILL_LEVEL, 0)
    set_coil(client, S4, True)
    r.append(check("T4a WORK, filling -> State 1 Running", 1, read_state(client)))
    set_coil(client, PRODUCT_AVAILABLE, False)
    r.append(check("T4b bottle at S4, no product -> State 2 Starved", 2, read_state(client)))
    r.append(check("T4c no product -> still WORK, no fault", 10, read_step(client)))
    set_coil(client, PRODUCT_AVAILABLE, True)
    r.append(check("T4d product back -> State 1 Running", 1, read_state(client)))

    write_hr(client, FILL_LEVEL, 500)  # fill done -> INDEX
    r.append(check("T5a fill done -> INDEX", 20, read_step(client)))
    r.append(check("T5b INDEX -> State 1 Running", 1, read_state(client)))
    set_coil(client, S4, False)
    pulse_s8(client)

    write_hr(client, STEP, 900)
    r.append(check("T6 FAULT -> State 9 Fault", 9, read_state(client)))
    write_hr(client, CMD, 3)
    r.append(check("T6b Reset -> State 0 Stopped", 0, read_state(client)))

    start_work(client)
    write_hr(client, MODE_REQ, 2)
    r.append(check("T7 WORK -> Manual -> State 0 Stopped", 0, read_state(client)))
    write_hr(client, MODE_REQ, 1)

    start_work(client)
    set_coil(client, E_STOP_OK, False)
    r.append(check("T8 E-stop -> State 0 Stopped", 0, read_state(client)))
    set_coil(client, E_STOP_OK, True)
    return r


def main():
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = stop_checks(client) + wait_checks(client) + state_checks(client)
    finally:
        clear_inputs(client)
        client.write_coil(E_STOP_OK, True)
        client.write_coil(PRODUCT_AVAILABLE, True)
        client.write_register(FILL_LEVEL, 0)
        write_defaults(client)
        client.write_register(MODE_REQ, 1)
        client.write_register(STEP, 0)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()

    print()
    print(f"{sum(results)}/{len(results)} checks passed")
    print("Stop and State OK" if all(results) else "Stop and State NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
