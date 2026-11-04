"""Jog watchdog (HMI issue I3) over Modbus, acting as the HMI.

Problem: a hold-to-run button writes Man_x = 1 on mouse-down and 0 on mouse-up. If the
browser page is closed while the button is held, the 0 never comes and the actuator
would run on in Manual. The PLC fix (docs/filler/ladder-roadmap.md, "Jog watchdog"):
    Jog_TON (IN := any Man_x, PT := T#10s)   Q -> (S) Jog_Block   (%MX3.2, coil 8218)
    all Man_x released                         -> (R) Jog_Block
    every manual branch: Mode_Manual AND Man_x AND NOT Jog_Block
The PLC never writes Man_x (they belong to the HMI); it only stops obeying them until
every button is released.

    uv run python tests/test_jog_watchdog.py       # about 30 s

Checks:
    J1 Manual + Man_C1 held -> C1 runs
    J2 held 10 s -> C1 stops by itself, Jog_Block = 1 (timing 10 +- 0.5 s)
    J3 still held: blocked; a second button (Man_Rinse) is ignored as well
    J4 all buttons released -> Jog_Block = 0
    J5 Man_C1 pressed again -> C1 runs again (re-armed)
    J6 Auto mode: C1 keeps running although Jog_Block is set (only manual branches are blocked)

Do not run it while the simulator or FUXA is operating the machine (one writer per address).
"""

import time

from pymodbus.client import ModbusTcpClient

from test_outputs import (
    CONVEYOR1, HOST, MAN_C1, MAN_RINSE, MODE_REQ, PORT, Heartbeat, check, check_outputs,
    clear_inputs, prepare, read_coil, set_coil, wait_for_coil, write_hr,
)

JOG_BLOCK = 8218  # %MX3.2
JOG_TIME_S = 10.0
JOG_TOLERANCE_S = 0.5


def watchdog_checks(client) -> list[bool]:
    r = []
    prepare(client)
    write_hr(client, MODE_REQ, 2)  # Manual
    r.append(check("J0 Manual, no button -> all OFF, not blocked", ([], False),
                   ([] if not read_coil(client, CONVEYOR1) else ["C1"], read_coil(client, JOG_BLOCK))))

    client.write_coil(MAN_C1, True)
    pressed = time.monotonic()
    time.sleep(0.3)
    r.append(check_outputs(client, "J1 Man_C1 held -> C1", [CONVEYOR1]))

    off = wait_for_coil(client, CONVEYOR1, False, JOG_TIME_S + 3)
    held = None if off is None else time.monotonic() - pressed
    ok = held is not None and abs(held - JOG_TIME_S) <= JOG_TOLERANCE_S and read_coil(client, JOG_BLOCK)
    print(f"{'PASS' if ok else 'FAIL'}  J2 held {JOG_TIME_S:.0f} s -> C1 OFF, Jog_Block: "
          f"C1 off after {held if held is None else round(held, 2)} s, Jog_Block {read_coil(client, JOG_BLOCK)}")
    r.append(bool(ok))

    set_coil(client, MAN_RINSE, True)
    r.append(check_outputs(client, "J3 still blocked, Man_Rinse ignored too", []))

    set_coil(client, MAN_RINSE, False)
    r.append(check("J3b one button still held -> still blocked", True, read_coil(client, JOG_BLOCK)))
    set_coil(client, MAN_C1, False)
    r.append(check("J4 all released -> Jog_Block reset", False, read_coil(client, JOG_BLOCK)))

    set_coil(client, MAN_C1, True)
    r.append(check_outputs(client, "J5 Man_C1 pressed again -> C1 (re-armed)", [CONVEYOR1]))
    set_coil(client, MAN_C1, False)

    write_hr(client, MODE_REQ, 1)  # Auto, IDLE: C1 runs by itself
    client.write_coil(MAN_C1, True)
    time.sleep(JOG_TIME_S + 0.7)
    blocked = read_coil(client, JOG_BLOCK)
    r.append(check("J6 Auto: Jog_Block set but C1 keeps running", (True, True), (blocked, read_coil(client, CONVEYOR1))))
    set_coil(client, MAN_C1, False)
    r.append(check("J6b released -> Jog_Block reset", False, read_coil(client, JOG_BLOCK)))
    return r


def main() -> None:
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = watchdog_checks(client)
    finally:
        clear_inputs(client)
        client.write_register(MODE_REQ, 1)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()
    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
