"""Scenario test: E-stop with a partly filled bottle, then restart (rule 7).

Expected behaviour (state-diagram.md rules 2, 7; stage 1 Master_Enable, abort rung):
    E-stop (E_Stop_OK FALSE) -> Master_Enable FALSE -> every output OFF at once (C1 too),
    the abort rung sends Step to IDLE (0), no FAULT and no alarm.
    Releasing the E-stop does NOT restart the machine; Start is ignored while E-stop is active.
    After release + Start, WORK looks at the sensors again: the half-full bottle at S4
    is filled up until FillLevel reaches the setpoint, then INDEX.
    The fill timeout starts again from 0 after the restart (no false alarm).

Helpers and addresses come from test_outputs.py (same folder).

    uv run python tests/test_estop_resume.py
"""

import time

from pymodbus.client import ModbusTcpClient

from test_outputs import (
    CMD, CONVEYOR1, E_STOP_OK, FILL_LEVEL, FILL_VALVE, HOST, MODE_REQ, PORT, PRODUCT_AVAILABLE,
    S4, STEP, Heartbeat, check, check_outputs, clear_inputs, outputs_on, prepare, read_coil,
    read_hr, set_coil, start_work, write_defaults, write_hr,
)

STATE = 1074  # %MW50
ALM_INDEXJAM = 8232  # %MX5.0
ALM_FILLTIMEOUT = 8233  # %MX5.1


def run_checks(client):
    r = []
    prepare(client)
    start_work(client)

    # 1. A bottle at S4 is being filled, level stops at 200 ml (setpoint 500)
    write_hr(client, FILL_LEVEL, 0)
    set_coil(client, S4, True)
    r.append(check("1a filling -> FillValve ON", True, read_coil(client, FILL_VALVE)))
    write_hr(client, FILL_LEVEL, 200)
    r.append(check("1b level 200 (< 500) -> valve still ON, WORK", (True, 10),
                   (read_coil(client, FILL_VALVE), read_hr(client, STEP))))

    # 2. E-stop: everything OFF at once, IDLE, no FAULT, no alarm
    set_coil(client, E_STOP_OK, False)
    r.append(check_outputs(client, "2a E-stop -> all outputs OFF (incl. C1)", []))
    r.append(check("2b E-stop -> Step 0 IDLE (not FAULT 900)", 0, read_hr(client, STEP)))
    r.append(check("2c E-stop -> State 0 Stopped", 0, read_hr(client, STATE)))
    r.append(check("2d no alarm latched", (False, False),
                   (read_coil(client, ALM_INDEXJAM), read_coil(client, ALM_FILLTIMEOUT))))

    # 3. Start while the E-stop is still pressed -> ignored
    write_hr(client, CMD, 1)
    r.append(check("3 Start during E-stop -> ignored, all OFF", (0, []),
                   (read_hr(client, STEP), outputs_on(client))))

    # 4. E-stop released -> no automatic restart, only C1 runs again
    set_coil(client, E_STOP_OK, True)
    time.sleep(0.5)
    r.append(check("4a E-stop released -> stays IDLE", 0, read_hr(client, STEP)))
    r.append(check_outputs(client, "4b E-stop released -> only C1, fill valve stays closed",
                           [CONVEYOR1]))

    # 5. Start -> WORK, the half-full bottle is filled up (rule 7)
    write_hr(client, CMD, 1)
    t0 = time.monotonic()
    r.append(check("5a Start -> WORK, valve opens again (top up)", (10, True),
                   (read_hr(client, STEP), read_coil(client, FILL_VALVE))))
    write_hr(client, FILL_LEVEL, 400)
    r.append(check("5b level 400 -> still filling", (10, True),
                   (read_hr(client, STEP), read_coil(client, FILL_VALVE))))
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("5c level 500 -> valve OFF, INDEX", (False, 20),
                   (read_coil(client, FILL_VALVE), read_hr(client, STEP))))

    # 6. Fill timeout restarted from 0 after the E-stop: no false alarm
    #    (total valve-open time before + after the E-stop is < 10 s anyway, so also check
    #    that the 10 s are counted from the restart, not from the first opening)
    r.append(check("6 no fill timeout after restart", False, read_coil(client, ALM_FILLTIMEOUT)))
    print(f"      (refill took {time.monotonic() - t0:.1f} s after Start)")
    return r


def timer_restart_checks(client):
    """The fill timer must start again from 0 after an E-stop, not continue."""
    r = []
    prepare(client)
    start_work(client)
    write_hr(client, FILL_LEVEL, 200)
    set_coil(client, S4, True)
    time.sleep(8.0)  # valve open 8 s, level stuck
    set_coil(client, E_STOP_OK, False)
    set_coil(client, E_STOP_OK, True)
    write_hr(client, CMD, 1)
    r.append(check("7a 8 s fill, E-stop, restart -> WORK, valve ON", (10, True),
                   (read_hr(client, STEP), read_coil(client, FILL_VALVE))))
    time.sleep(5.0)  # 8 + 5 = 13 s total, but only 5 s since restart
    r.append(check("7b 5 s after restart (13 s in total) -> no fill timeout", False,
                   read_coil(client, ALM_FILLTIMEOUT)))
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("7c level reached -> INDEX", 20, read_hr(client, STEP)))
    clear_inputs(client)
    return r


def main():
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = run_checks(client) + timer_restart_checks(client)
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
    print("E-stop + resume OK" if all(results) else "E-stop + resume NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
