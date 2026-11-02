"""Stage 6 test: INDEX in Auto, door pulse, good-bottle counter, over Modbus.

Needs these rungs of plcs/filler main running in the runtime:
    section 6  Req_Index = ((Mode_Auto AND Step_Index) OR (Mode_Manual AND Man_Index)) AND Ilk_Index
               Door_TP: TP (PT = 500 ms), IN = Mode_Auto AND P(Step_Index) AND S2
               Req_Door = Door_TP.Q OR (Mode_Manual AND Man_Door)
               Mot_Door: FB_Motor, Run = Req_Door, Enable = Master_Enable, Out -> DoorMotor
    section 7  Good_Counter: CTU, CU = S6_Edge, PV = 32767, CV -> GoodCount (%MW53)

Shortcut used to reach INDEX quickly: a bottle only at S2 (all stations empty =
all jobs done, something to move) sends WORK to INDEX in the next scan; a bottle at
S4 with FillLevel = FillSetpoint does the same without S2.

Helpers and addresses come from test_outputs.py (same folder).

    uv run python tests/test_index_door.py
"""

import time

from pymodbus.client import ModbusTcpClient

from test_outputs import (
    CONVEYOR1, DOOR, E_STOP_OK, FILL_LEVEL, HOST, INDEX_MOTOR, MAN_DOOR, MODE_REQ, PORT,
    S2, S4, S6, S8, STEP, Heartbeat, check, check_outputs, clear_inputs, prepare,
    read_coil, read_hr, set_coil, start_work, wait_for_coil, write_defaults, write_hr,
)

GOOD_COUNT = 1077  # %MW53
DOOR_PULSE = 0.5  # seconds, Door_TP PT
PULSE_TOLERANCE = 0.15  # seconds; polling every 20 ms + Modbus round trips


def pulse_s8(client):
    """One rising edge of S8: the belt moved one station, INDEX (20) -> WORK (10)."""
    set_coil(client, S8, False)
    set_coil(client, S8, True)
    set_coil(client, S8, False)


def s8_edge_and_measure_door(client):
    """S8 rising edge, then measure the door pulse at once.

    With a bottle at S2 (and nothing else to do) WORK goes back to INDEX one scan
    after the S8 edge, so the door pulse starts right away; pulse_s8() would sleep
    through it.
    """
    set_coil(client, S8, False)
    client.write_coil(S8, True)
    elapsed = measure_door_pulse(client)
    set_coil(client, S8, False)
    return elapsed


def measure_door_pulse(client, timeout=1.0):
    """Wait for DoorMotor ON, then OFF. Return the ON time in s, or None if it never opened."""
    if wait_for_coil(client, DOOR, True, timeout) is None:
        return None
    start = time.monotonic()
    if wait_for_coil(client, DOOR, False, 3.0) is None:
        return float("inf")  # door never closed
    return time.monotonic() - start


def check_pulse(name, elapsed):
    if elapsed is None:
        print(f"FAIL  {name}: expected ~{DOOR_PULSE} s, door never opened")
        return False
    ok = abs(elapsed - DOOR_PULSE) <= PULSE_TOLERANCE
    print(f"{'PASS' if ok else 'FAIL'}  {name}: expected ~{DOOR_PULSE} s, got {elapsed:.2f} s")
    return ok


def door_stays_closed(client, seconds):
    """True if DoorMotor stays OFF for the whole time."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if read_coil(client, DOOR):
            return False
        time.sleep(0.02)
    return True


def index_checks(client):
    r = []
    prepare(client)
    start_work(client)

    # I1 jobs done (fill reached), no bottle at the door -> INDEX, belt ON, door closed
    write_hr(client, FILL_LEVEL, 500)
    set_coil(client, S4, True)
    r.append(check("I1a S4 filled -> INDEX", 20, read_hr(client, STEP)))
    r.append(check_outputs(client, "I1b INDEX, no S2 -> belt ON, door closed",
                           [CONVEYOR1, INDEX_MOTOR]))

    # I2 belt keeps running until S8
    time.sleep(1.0)
    r.append(check("I2 1 s without S8 -> belt still ON", True, read_coil(client, INDEX_MOTOR)))

    # I3 S8 rising edge -> WORK, belt OFF in the same scan as the step change
    set_coil(client, S4, False)
    pulse_s8(client)
    r.append(check("I3a S8 edge -> WORK", 10, read_hr(client, STEP)))
    r.append(check("I3b S8 edge -> belt OFF", False, read_coil(client, INDEX_MOTOR)))
    return r


def door_checks(client):
    r = []

    # D1 bottle waiting at S2 when INDEX starts -> one 0.5 s pulse, although S2 stays ON
    client.write_coil(S2, True)
    r.append(check_pulse("D1a S2 at INDEX start -> door pulse", measure_door_pulse(client)))
    r.append(check("D1b in INDEX", 20, read_hr(client, STEP)))
    r.append(check("D1c S2 still ON 1 s -> door stays closed", True, door_stays_closed(client, 1.0)))

    # D2 next INDEX (S2 still ON, next bottle) -> a new pulse
    # WORK, only S2 -> INDEX again at once
    r.append(check_pulse("D2 next INDEX with S2 -> new door pulse", s8_edge_and_measure_door(client)))
    client.write_coil(S2, False)
    pulse_s8(client)
    r.append(check("D2b back in WORK, door empty", 10, read_hr(client, STEP)))

    # D3 bottle arrives at S2 while the belt is already moving -> no door this INDEX
    write_hr(client, FILL_LEVEL, 500)
    set_coil(client, S4, True)  # -> INDEX without S2
    if read_hr(client, STEP) != 20:
        raise SystemExit("S4 filled does not reach INDEX (20): check stages 4/5")
    client.write_coil(S2, True)
    r.append(check("D3a S2 arrives mid-INDEX -> door stays closed", True,
                   door_stays_closed(client, 1.0)))
    client.write_coil(S4, False)
    # WORK with S2 -> next INDEX -> now the door opens
    r.append(check_pulse("D3b next INDEX -> waiting bottle admitted", s8_edge_and_measure_door(client)))
    clear_inputs(client)
    pulse_s8(client)
    return r


def estop_checks(client):
    r = []
    write_hr(client, FILL_LEVEL, 500)
    start_work(client)
    set_coil(client, S4, True)
    r.append(check("E1a in INDEX, belt ON", True, read_coil(client, INDEX_MOTOR)))
    set_coil(client, E_STOP_OK, False)
    r.append(check_outputs(client, "E1b E-stop in INDEX -> all OFF", []))
    set_coil(client, E_STOP_OK, True)
    clear_inputs(client)
    return r


def manual_door_checks(client):
    r = []
    write_hr(client, MODE_REQ, 2)
    set_coil(client, MAN_DOOR, True)
    r.append(check_outputs(client, "M1a Manual, Man_Door held -> door open", [DOOR]))
    time.sleep(1.0)
    r.append(check("M1b held 1 s -> still open (no TP in Manual)", True, read_coil(client, DOOR)))
    set_coil(client, MAN_DOOR, False)
    r.append(check_outputs(client, "M1c Man_Door released -> closed", []))
    write_hr(client, MODE_REQ, 1)
    return r


def counter_checks(client):
    r = []
    count0 = read_hr(client, GOOD_COUNT)
    for _ in range(5):
        set_coil(client, S6, True)
        set_coil(client, S6, False)
    r.append(check("C1 5 bottles discharged (S6 pulses) -> GoodCount +5", 5,
                   read_hr(client, GOOD_COUNT) - count0))
    set_coil(client, S6, True)
    time.sleep(1.0)
    r.append(check("C2 S6 held ON 1 s -> counts once", count0 + 6, read_hr(client, GOOD_COUNT)))
    set_coil(client, S6, False)
    return r


def main():
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = (index_checks(client) + door_checks(client) + estop_checks(client)
                   + manual_door_checks(client) + counter_checks(client))
    finally:
        clear_inputs(client)
        client.write_coil(E_STOP_OK, True)
        client.write_register(FILL_LEVEL, 0)
        write_defaults(client)
        client.write_register(MODE_REQ, 1)
        client.write_register(STEP, 0)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()

    print()
    print(f"{sum(results)}/{len(results)} checks passed")
    print("INDEX, door, counter OK" if all(results) else "INDEX, door, counter NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
