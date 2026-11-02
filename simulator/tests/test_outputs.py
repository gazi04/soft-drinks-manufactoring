"""Stage 5b test of the output section (interlocks + outputs) over Modbus.

Needs section 6 "Outputs" of plcs/filler main running in the runtime, below the
transitions:
    6.1 Req_Rinse / Req_Fill / Req_Cap = Auto branch (Mode_Auto AND Step_Work AND
        bottle AND NOT done [AND Product_Available for fill])
        OR Manual branch (Mode_Manual AND Man_... [AND S4 for fill = interlock])
    6.2 Ilk_Index = NOT Req_Rinse AND NOT Req_Fill AND NOT Req_Cap   (INTERLOCK)
    6.3 Req_Index = ((Mode_Auto AND Step_Index) OR (Mode_Manual AND Man_Index)) AND Ilk_Index
        Req_C1    = (NOT Mode_Manual AND NOT Step_Fault) OR (Mode_Manual AND Man_C1)
    6.4 FB instances: Req_... -> Run/Open, Enable = Master_Enable, Out -> %QX output

The physical outputs %QX0.0-0.5 are read as coils 0-5.

    uv run python tests/test_outputs.py

A background thread toggles the heartbeat, otherwise Alm_CommLost would switch
Master_Enable off after 3 s.
"""

import threading
import time

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502

# coils (%MX): sensors and interface
S2, S3, S4, S5, S6, S7, S8 = 8192, 8193, 8194, 8195, 8196, 8197, 8198
PRODUCT_AVAILABLE = 8200  # %MX1.0
E_STOP_OK = 8201  # %MX1.1
HEARTBEAT = 8202  # %MX1.2
ALM_COMMLOST = 8235  # %MX5.3

# coils (%MX2.x): manual buttons
MAN_C1, MAN_DOOR, MAN_RINSE, MAN_FILL, MAN_CAP, MAN_INDEX = 8208, 8209, 8210, 8211, 8212, 8213
MAN_BUTTONS = (MAN_C1, MAN_DOOR, MAN_RINSE, MAN_FILL, MAN_CAP, MAN_INDEX)

# coils (%QX0.x): physical outputs, read only
CONVEYOR1, DOOR, RINSE_VALVE, FILL_VALVE, CAP_MOTOR, INDEX_MOTOR = 0, 1, 2, 3, 4, 5
OUTPUT_NAMES = {
    CONVEYOR1: "ConveyerMotor1",
    DOOR: "DoorMotor",
    RINSE_VALVE: "RinseValve",
    FILL_VALVE: "FillValve",
    CAP_MOTOR: "CapMotor",
    INDEX_MOTOR: "IndexMotor",
}

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


def write_hr(client, address, value):
    client.write_register(address, value)
    time.sleep(SCAN_WAIT)


def set_coil(client, address, value):
    client.write_coil(address, value)
    time.sleep(SCAN_WAIT)


def outputs_on(client):
    """Names of all %QX outputs that are ON right now (sorted, for readable output)."""
    result = client.read_coils(0, count=6)
    if result.isError():
        raise SystemExit(f"Reading outputs failed: {result}")
    return sorted(OUTPUT_NAMES[i] for i in range(6) if result.bits[i])


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def check_outputs(client, name, expected_on):
    """PASS if exactly the outputs in expected_on are ON, all others OFF."""
    return check(name, sorted(OUTPUT_NAMES[o] for o in expected_on), outputs_on(client))


def wait_for_coil(client, address, value, timeout):
    """Poll a coil until it equals value. Return the elapsed time, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if read_coil(client, address) == value:
            return time.monotonic() - start
        time.sleep(0.02)
    return None


def check_duration(name, expected, elapsed):
    if elapsed is None:
        print(f"FAIL  {name}: expected ~{expected:.1f} s, output never switched")
        return False
    ok = abs(elapsed - expected) <= TOLERANCE
    print(f"{'PASS' if ok else 'FAIL'}  {name}: expected ~{expected:.1f} s, got {elapsed:.2f} s")
    return ok


def clear_inputs(client):
    for address in (S2, S3, S4, S5, S6, S7, S8, *MAN_BUTTONS):
        client.write_coil(address, False)
    time.sleep(SCAN_WAIT)


def write_defaults(client):
    for address, value in DEFAULTS.items():
        client.write_register(address, value)
    time.sleep(SCAN_WAIT)


def prepare(client):
    """Healthy signals, default setpoints, inputs off, alarms reset, Auto mode, Step 0."""
    clear_inputs(client)
    client.write_coil(E_STOP_OK, True)
    client.write_coil(PRODUCT_AVAILABLE, True)
    write_hr(client, FILL_LEVEL, 0)
    write_defaults(client)
    write_hr(client, MODE_REQ, 1)
    time.sleep(1.0)  # heartbeat running, Hb_Timer.Q gone
    write_hr(client, CMD, 3)  # Reset clears Alm_CommLost (only when the cause is gone)
    if read_coil(client, ALM_COMMLOST):
        raise SystemExit("Alm_CommLost stays TRUE: check the heartbeat rungs / release forces")
    for address, value in DEFAULTS.items():
        if read_hr(client, address) != value:
            raise SystemExit(f"Register {address} does not follow the write: release all forces")
    write_hr(client, STEP, 0)


def start_work(client):
    """Auto, Step 0 -> Start -> WORK (10)."""
    write_hr(client, MODE_REQ, 1)
    write_hr(client, STEP, 0)
    write_hr(client, CMD, 1)
    if read_hr(client, STEP) != 10:
        raise SystemExit("Start does not reach WORK (10): check stage 4 rungs / release forces")


def auto_checks(client):
    r = []
    prepare(client)

    # A1 IDLE: only C1 runs (it runs always unless E-stop / FAULT)
    r.append(check_outputs(client, "A1 Auto IDLE -> only C1", [CONVEYOR1]))

    # A2 WORK, empty machine: no station job, C1 still runs
    start_work(client)
    r.append(check_outputs(client, "A2 Auto WORK, no bottles -> only C1", [CONVEYOR1]))

    # A3 manual buttons are ignored in Auto
    for address in MAN_BUTTONS:
        client.write_coil(address, True)
    time.sleep(SCAN_WAIT)
    r.append(check_outputs(client, "A3 Auto WORK, all Man_ buttons pressed -> ignored", [CONVEYOR1]))
    clear_inputs(client)

    # A4 all three stations busy (FillLevel 0, so Step stays 10 and each output's
    #    own done bit switches it off, not the step change)
    for address in (S3, S4, S5):
        client.write_coil(address, True)
    t0 = time.monotonic()
    time.sleep(SCAN_WAIT)
    r.append(check_outputs(client, "A4a all busy -> rinse, fill, cap ON",
                           [CONVEYOR1, RINSE_VALVE, FILL_VALVE, CAP_MOTOR]))
    cap_off = wait_for_coil(client, CAP_MOTOR, False, 3.0)
    r.append(check_duration("A4b CapMotor OFF after CapTime 1500", 1.5,
                            None if cap_off is None else time.monotonic() - t0))
    rinse_off = wait_for_coil(client, RINSE_VALVE, False, 3.0)
    r.append(check_duration("A4c RinseValve OFF after RinseTime 2000", 2.0,
                            None if rinse_off is None else time.monotonic() - t0))
    r.append(check_outputs(client, "A4d rinse + cap done, fill not -> only FillValve (+C1)",
                           [CONVEYOR1, FILL_VALVE]))
    r.append(check("A4e still in WORK", 10, read_hr(client, STEP)))

    # A5 Product_Available gates only the valve
    set_coil(client, PRODUCT_AVAILABLE, False)
    r.append(check("A5a no product -> FillValve OFF", False, read_coil(client, FILL_VALVE)))
    r.append(check("A5b no product -> still WORK (waiting, no fault)", 10, read_hr(client, STEP)))
    set_coil(client, PRODUCT_AVAILABLE, True)
    r.append(check("A5c product back -> FillValve ON", True, read_coil(client, FILL_VALVE)))

    # A6 fill level reached -> valve OFF, all jobs done -> INDEX
    write_hr(client, FILL_LEVEL, 500)
    r.append(check("A6a level 500 -> FillValve OFF", False, read_coil(client, FILL_VALVE)))
    r.append(check("A6b all jobs done -> INDEX", 20, read_hr(client, STEP)))

    # A7 INDEX in Auto: station outputs OFF although bottles present, belt runs
    #    (Auto branch of Req_Index, stage 6; door closed because no S2)
    r.append(check_outputs(client, "A7 INDEX, bottles at S3-S5 -> C1 + belt only",
                           [CONVEYOR1, INDEX_MOTOR]))

    # A8 E-stop during a job: Master_Enable off -> everything OFF at once, C1 too
    clear_inputs(client)
    write_hr(client, FILL_LEVEL, 0)
    start_work(client)
    set_coil(client, S4, True)
    r.append(check("A8a WORK, S4 -> FillValve ON", True, read_coil(client, FILL_VALVE)))
    set_coil(client, E_STOP_OK, False)
    r.append(check_outputs(client, "A8b E-stop -> all OFF (incl. C1)", []))
    set_coil(client, E_STOP_OK, True)
    r.append(check_outputs(client, "A8c E-stop released -> IDLE, only C1", [CONVEYOR1]))
    clear_inputs(client)

    # A9 FAULT: C1 stops
    write_hr(client, STEP, 900)
    r.append(check_outputs(client, "A9a FAULT (900) -> all OFF (incl. C1)", []))
    write_hr(client, CMD, 3)
    r.append(check_outputs(client, "A9b Reset -> IDLE, only C1", [CONVEYOR1]))

    # A10 Mode Stop: C1 keeps running, nothing else
    write_hr(client, MODE_REQ, 0)
    set_coil(client, S4, True)
    r.append(check_outputs(client, "A10 Mode Stop, S4 -> only C1", [CONVEYOR1]))
    clear_inputs(client)
    return r


def manual_checks(client):
    r = []
    write_hr(client, MODE_REQ, 2)
    r.append(check("M0 Manual -> Step 0", 0, read_hr(client, STEP)))

    # M1 in Manual, C1 runs only while Man_C1 is held
    r.append(check_outputs(client, "M1a Manual, no button -> all OFF (C1 too)", []))
    set_coil(client, MAN_C1, True)
    r.append(check_outputs(client, "M1b Man_C1 held -> C1", [CONVEYOR1]))
    set_coil(client, MAN_C1, False)
    r.append(check_outputs(client, "M1c Man_C1 released -> all OFF", []))

    # M2 rinse: no interlock (checking the water needs no bottle), hold-to-run
    set_coil(client, MAN_RINSE, True)
    r.append(check_outputs(client, "M2a Man_Rinse held -> RinseValve", [RINSE_VALVE]))
    set_coil(client, MAN_RINSE, False)
    r.append(check_outputs(client, "M2b Man_Rinse released -> all OFF", []))

    # M3 fill: INTERLOCK S4 (no bottle = product on the floor)
    set_coil(client, MAN_FILL, True)
    r.append(check_outputs(client, "M3a Man_Fill, no bottle -> stays OFF (interlock S4)", []))
    set_coil(client, S4, True)
    r.append(check_outputs(client, "M3b Man_Fill + S4 -> FillValve", [FILL_VALVE]))

    # M4 index: INTERLOCK Ilk_Index (belt must not move while fill valve open)
    set_coil(client, MAN_INDEX, True)
    r.append(check_outputs(client, "M4a Man_Fill + S4 + Man_Index -> belt blocked (Ilk_Index)",
                           [FILL_VALVE]))
    set_coil(client, MAN_FILL, False)
    r.append(check_outputs(client, "M4b Man_Fill released -> belt moves", [INDEX_MOTOR]))
    set_coil(client, MAN_RINSE, True)
    r.append(check_outputs(client, "M4c Man_Rinse pressed -> belt stops, rinse ON",
                           [RINSE_VALVE]))
    set_coil(client, MAN_RINSE, False)
    set_coil(client, MAN_CAP, True)
    r.append(check_outputs(client, "M4d Man_Cap pressed -> belt stops, cap ON", [CAP_MOTOR]))
    set_coil(client, MAN_CAP, False)
    r.append(check_outputs(client, "M4e only Man_Index -> belt", [INDEX_MOTOR]))
    set_coil(client, MAN_INDEX, False)
    r.append(check_outputs(client, "M4f Man_Index released -> all OFF (jog)", []))

    # M5 E-stop overrides Manual too
    set_coil(client, MAN_RINSE, True)
    set_coil(client, E_STOP_OK, False)
    r.append(check_outputs(client, "M5a Manual + E-stop, Man_Rinse held -> all OFF", []))
    set_coil(client, E_STOP_OK, True)
    r.append(check_outputs(client, "M5b E-stop released, Man_Rinse still held -> RinseValve",
                           [RINSE_VALVE]))
    clear_inputs(client)
    return r


def main():
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = auto_checks(client) + manual_checks(client)
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
    print("Outputs OK" if all(results) else "Outputs NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
