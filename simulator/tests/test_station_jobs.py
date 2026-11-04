"""Stage 5a test of the station jobs (done bits) over Modbus.

Needs the plcs/filler main station job rungs running in the runtime, placed after
the step decode and BEFORE Jobs_Done and the transitions (otherwise a new bottle
meets a stale Jobs_Done = TRUE from the previous scan and the machine indexes at once):
    TO_TIME: RinseTime -> Rinse_PT, CapTime -> Cap_PT (INT in ms -> TIME)
    Rinse_Timer (IN = Step_Work AND S3), Rinse_Done = NOT S3 OR Rinse_Timer.Q
    GE FillLevel >= FillSetpoint -> Fill_Reached, Fill_Done = NOT S4 OR Fill_Reached
    Cap_Timer (IN = Step_Work AND S5), Cap_Done = NOT S5 OR Cap_Timer.Q

The done bits have no address, so the test watches their effect instead: in WORK
(step 10) with a bottle present, Step moves to INDEX (20) exactly when all three
jobs are done (rung 28 Jobs_Done, rung 33 transition 10 -> 20). The time from
"bottle arrives" to "Step = 20" is therefore the job duration.

Not covered here (debugger check): Rinse_PT shows T#2s and Cap_PT shows T#1s500ms.

    uv run python tests/test_station_jobs.py

A background thread toggles the heartbeat, otherwise Alm_CommLost would switch
Master_Enable off after 3 s and the abort rung would send Step back to 0.
"""

import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
PORT = 502

# coils (%MX)
S2, S3, S4, S5, S6, S7, S8 = 8192, 8193, 8194, 8195, 8196, 8197, 8198
PRODUCT_AVAILABLE = 8200  # %MX1.0
E_STOP_OK = 8201  # %MX1.1
HEARTBEAT = 8202  # %MX1.2
ALM_COMMLOST = 8235  # %MX5.3

# holding registers (%MW)
CMD = 1024  # %MW0: 1 Start, 2 Stop, 3 Reset
MODE_REQ = 1025  # %MW1: 0 Stop, 1 Auto, 2 Manual
FILL_LEVEL = 1036  # %MW12, ml
FILL_SETPOINT = 1054  # %MW30, ml
RINSE_TIME = 1055  # %MW31, ms
CAP_TIME = 1056  # %MW32, ms
STEP = 1075  # %MW51

DEFAULTS = {FILL_SETPOINT: 500, RINSE_TIME: 2000, CAP_TIME: 1500}

SCAN_WAIT = 0.3  # seconds; a few PLC scans (task interval 20 ms)
TOLERANCE = 0.3  # seconds; Modbus round trips + scan time around a measured duration


class Heartbeat(threading.Thread):
    """Toggle the heartbeat bit every 0.5 s on its own connection."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

    def run(self):
        client = ModbusTcpClient(HOST, port=PORT)
        client.connect()
        value = False
        while not self.stop_event.is_set():
            value = not value
            client.write_coil(HEARTBEAT, value)
            time.sleep(0.5)
        client.close()


def read_coil(client, address):
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading coil {address} failed: {result}")
    return result.bits[0]


def read_hr(client, address):
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading register {address} failed: {result}")
    return result.registers[0]


def read_step(client):
    return read_hr(client, STEP)


def write_hr(client, address, value):
    client.write_register(address, value)
    time.sleep(SCAN_WAIT)


def set_coil(client, address, value):
    client.write_coil(address, value)
    time.sleep(SCAN_WAIT)


def pulse_s8(client):
    """S8 OFF -> ON -> OFF: one rising edge, INDEX (20) -> WORK (10)."""
    set_coil(client, S8, False)
    set_coil(client, S8, True)
    set_coil(client, S8, False)


def wait_for_step(client, target, timeout):
    """Poll Step until it equals target. Return the elapsed time, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if read_step(client) == target:
            return time.monotonic() - start
        time.sleep(0.02)
    return None


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def check_duration(name, expected, elapsed):
    """PASS if the measured job time is within TOLERANCE of the expected time."""
    if elapsed is None:
        print(f"FAIL  {name}: expected ~{expected:.1f} s, Step never reached 20")
        return False
    ok = abs(elapsed - expected) <= TOLERANCE
    print(f"{'PASS' if ok else 'FAIL'}  {name}: expected ~{expected:.1f} s, got {elapsed:.2f} s")
    return ok


def clear_sensors(client):
    for address in (S2, S3, S4, S5, S6, S7, S8):
        client.write_coil(address, False)
    time.sleep(SCAN_WAIT)


def write_defaults(client):
    for address, value in DEFAULTS.items():
        client.write_register(address, value)
    time.sleep(SCAN_WAIT)


def prepare(client):
    """Auto mode, healthy signals, default setpoints, sensors off, Step = 10 (WORK)."""
    clear_sensors(client)
    client.write_coil(E_STOP_OK, True)
    client.write_coil(PRODUCT_AVAILABLE, True)
    write_hr(client, FILL_LEVEL, 0)
    write_defaults(client)
    write_hr(client, MODE_REQ, 1)
    time.sleep(1.0)  # heartbeat running, Hb_Timer.Q gone
    write_hr(client, CMD, 3)  # Reset clears Alm_CommLost (only when the cause is gone)
    if read_coil(client, ALM_COMMLOST):
        raise SystemExit("Alm_CommLost stays TRUE: check the heartbeat rungs / release forces")
    # a debugger force would block the writes
    for address, value in DEFAULTS.items():
        if read_hr(client, address) != value:
            raise SystemExit(f"Register {address} does not follow the write: release all forces")
    write_hr(client, STEP, 0)
    write_hr(client, CMD, 1)  # Start: IDLE -> WORK
    if read_step(client) != 10:
        raise SystemExit("Start does not reach WORK (10): check stage 4 rungs / release forces")


def bottle_and_measure(client, sensors, timeout=4.0):
    """Put bottles on the given station sensors at once, return the time until INDEX (20)."""
    for address in sensors:
        client.write_coil(address, True)
    return wait_for_step(client, 20, timeout)


def back_to_work(client, keep=()):
    """Clear all station sensors except `keep`, then S8 edge: INDEX -> WORK."""
    for address in (S3, S4, S5):
        if address not in keep:
            client.write_coil(address, False)
    time.sleep(SCAN_WAIT)
    pulse_s8(client)
    if read_step(client) != 10:
        raise SystemExit("S8 edge does not return to WORK (10): check stage 4 rungs")


def run_checks(client):
    r = []
    prepare(client)

    # 1. Empty machine: all stations empty = all done, but nothing to move -> stays
    time.sleep(1.0)
    r.append(check("1 WORK, no bottles (all done, nothing to move) -> stays", 10, read_step(client)))

    # 2. Rinse: bottle at S3 -> INDEX after RinseTime (2 s)
    r.append(check_duration("2 rinse with RinseTime 2000", 2.0, bottle_and_measure(client, [S3])))

    # 3. Rinse timer restarts from 0 in every WORK step (bottle stays at S3 through INDEX).
    #    Timed from the S8 edge itself (back_to_work would add its own sleeps).
    set_coil(client, S8, False)
    client.write_coil(S8, True)
    start = time.monotonic()
    if wait_for_step(client, 10, 1.0) is None:
        raise SystemExit("S8 edge does not return to WORK (10): check stage 4 rungs")
    elapsed = time.monotonic() - start if wait_for_step(client, 20, 4.0) is not None else None
    r.append(check_duration("3 next WORK step, rinse again from 0", 2.0, elapsed))
    set_coil(client, S8, False)

    # 4. RinseTime is read from the register (HMI setpoint), not fixed in the program
    back_to_work(client)
    write_hr(client, RINSE_TIME, 1000)
    r.append(check_duration("4 rinse with RinseTime 1000", 1.0, bottle_and_measure(client, [S3])))
    write_hr(client, RINSE_TIME, 2000)

    # 5. Fill: done only when FillLevel >= FillSetpoint (GE, boundary 500)
    back_to_work(client)
    write_hr(client, FILL_LEVEL, 0)
    set_coil(client, S4, True)
    time.sleep(1.0)
    r.append(check("5a fill, level 0 -> stays in WORK", 10, read_step(client)))
    write_hr(client, FILL_LEVEL, 499)
    time.sleep(0.5)
    r.append(check("5b fill, level 499 (< 500) -> stays", 10, read_step(client)))
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("5c fill, level 500 (= setpoint) -> INDEX", 20, read_step(client)))

    # 6. Product_Available is not part of Fill_Done (it only gates the valve, stage 5b)
    back_to_work(client)
    set_coil(client, PRODUCT_AVAILABLE, False)
    write_hr(client, FILL_LEVEL, 500)
    set_coil(client, S4, True)
    r.append(check("6 fill done although Product_Available FALSE -> INDEX", 20, read_step(client)))
    set_coil(client, PRODUCT_AVAILABLE, True)

    # 7. Cap: bottle at S5 -> INDEX after CapTime (1.5 s). C2 free, so no WAIT_OUTFEED
    back_to_work(client)
    r.append(check_duration("7 cap with CapTime 1500", 1.5, bottle_and_measure(client, [S5])))

    # 8. All stations busy: Jobs_Done is an AND, the slowest job decides
    back_to_work(client)
    write_hr(client, FILL_LEVEL, 0)
    for address in (S3, S4, S5):
        client.write_coil(address, True)
    time.sleep(2.5)  # rinse (2 s) and cap (1.5 s) finished, fill not
    r.append(check("8a all busy, rinse+cap done, fill not -> stays", 10, read_step(client)))
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("8b all busy, fill reached too -> INDEX", 20, read_step(client)))
    return r


def main():
    claim_plant("test_station_jobs.py")  # writes the sensors: never with the simulator (plant_lock.py)
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = run_checks(client)
    finally:
        clear_sensors(client)
        client.write_register(FILL_LEVEL, 0)
        write_defaults(client)
        client.write_register(STEP, 0)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()

    print()
    print("Station jobs OK" if all(results) else "Station jobs NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
