"""Stage 4 test of the sequencer skeleton over Modbus.

Needs plcs/filler main rungs 21-35 running in the runtime:
    21-25  Step decode (EQ) -> Step_Idle / Step_Work / Step_Wait / Step_Index / Step_Fault
    26-28  Seq_Enable, Jobs_Done, Bottles_Present
    29     abort: NOT Mode_Auto OR NOT Master_Enable -> Step := 0 (not in FAULT)
    30-35  transitions 0->10, 10->15, 10->20, 15->20, 20->10, 900->0

The done bits (Rinse_Done, Fill_Done, Cap_Done) are internal variables without an
address, so Modbus cannot write them. Run in two parts:
    uv run python tests/test_sequencer.py          # done bits NOT forced (all FALSE)
    uv run python tests/test_sequencer.py --done   # done bits forced TRUE in the debugger

A background thread toggles the heartbeat, otherwise Alm_CommLost would switch
Master_Enable off after 3 s and the abort rung would send Step back to 0.
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
PORT = 502

# coils (%MX)
S2, S3, S4, S5, S6, S7, S8 = 8192, 8193, 8194, 8195, 8196, 8197, 8198
E_STOP_OK = 8201  # %MX1.1
HEARTBEAT = 8202  # %MX1.2
OUTFEED_FULL = 8216  # %MX3.0
ALM_COMMLOST = 8235  # %MX5.3

# holding registers (%MW)
CMD = 1024  # %MW0: 1 Start, 2 Stop, 3 Reset
MODE_REQ = 1025  # %MW1: 0 Stop, 1 Auto, 2 Manual
STEP = 1075  # %MW51

SCAN_WAIT = 0.3  # seconds; a few PLC scans (task interval 20 ms)


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


def read_step(client):
    result = client.read_holding_registers(STEP, count=1)
    if result.isError():
        raise SystemExit(f"Reading Step failed: {result}")
    return result.registers[0]


def write_hr(client, address, value):
    client.write_register(address, value)
    time.sleep(SCAN_WAIT)


def set_coil(client, address, value):
    client.write_coil(address, value)
    time.sleep(SCAN_WAIT)


def pulse_s8(client):
    """S8 OFF -> ON: one rising edge (S8_Edge for one scan)."""
    set_coil(client, S8, False)
    set_coil(client, S8, True)
    set_coil(client, S8, False)


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def clear_sensors(client):
    for address in (S2, S3, S4, S5, S6, S7, S8):
        client.write_coil(address, False)
    time.sleep(SCAN_WAIT)


def prepare(client):
    """Auto mode, healthy E-stop, no comm alarm, sensors off, Step = 0."""
    clear_sensors(client)
    client.write_coil(E_STOP_OK, True)
    write_hr(client, MODE_REQ, 1)
    time.sleep(1.0)  # heartbeat running, Hb_Timer.Q gone
    write_hr(client, CMD, 3)  # Reset clears Alm_CommLost (only when the cause is gone)
    if read_coil(client, ALM_COMMLOST):
        raise SystemExit("Alm_CommLost stays TRUE: check the heartbeat rungs / release forces")
    write_hr(client, STEP, 0)
    # a debugger force on Step or a sensor would block the writes
    if read_step(client) != 0:
        raise SystemExit("Step does not follow the write: release all forces in the debugger")


def part_without_done(client):
    """Done bits all FALSE (not forced)."""
    r = []
    prepare(client)

    # 1. Start in IDLE -> WORK
    write_hr(client, CMD, 1)
    r.append(check("1 IDLE + Start -> WORK", 10, read_step(client)))

    # 2. WORK with a bottle but jobs not done -> stays in WORK
    set_coil(client, S3, True)
    time.sleep(0.5)
    r.append(check("2 WORK, bottle at S3, jobs not done -> stays", 10, read_step(client)))
    set_coil(client, S3, False)

    # 3. INDEX + S8 edge -> WORK; INDEX without edge -> stays
    write_hr(client, STEP, 20)
    time.sleep(0.5)
    r.append(check("3a INDEX without S8 edge -> stays", 20, read_step(client)))
    pulse_s8(client)
    r.append(check("3b INDEX + S8 rising edge -> WORK", 10, read_step(client)))

    # 4. WAIT_OUTFEED: stays while Outfeed_Full, -> INDEX when released
    set_coil(client, S7, True)
    time.sleep(2.5)  # Full_OnTimer 2 s
    if not read_coil(client, OUTFEED_FULL):
        raise SystemExit("Outfeed_Full did not set: check stage 3 rungs")
    write_hr(client, STEP, 15)
    time.sleep(0.5)
    r.append(check("4a WAIT_OUTFEED while C2 full -> stays", 15, read_step(client)))
    set_coil(client, S7, False)
    time.sleep(3.5)  # Full_OffTimer 3 s
    r.append(check("4b WAIT_OUTFEED, C2 free -> INDEX", 20, read_step(client)))

    # 5. Start is ignored outside IDLE
    write_hr(client, CMD, 1)
    r.append(check("5 Start in INDEX -> ignored", 20, read_step(client)))

    # 6. Manual mode -> IDLE, Start ignored in Manual
    write_hr(client, MODE_REQ, 2)
    r.append(check("6a Manual mode -> IDLE", 0, read_step(client)))
    write_hr(client, CMD, 1)
    r.append(check("6b Start in Manual -> ignored", 0, read_step(client)))
    write_hr(client, MODE_REQ, 1)

    # 7. FAULT: kept on mode change, Reset -> IDLE
    write_hr(client, STEP, 900)
    write_hr(client, MODE_REQ, 2)
    r.append(check("7a FAULT kept when switching to Manual", 900, read_step(client)))
    write_hr(client, MODE_REQ, 1)
    write_hr(client, CMD, 1)
    r.append(check("7b Start in FAULT -> ignored", 900, read_step(client)))
    write_hr(client, CMD, 3)
    r.append(check("7c FAULT + Reset -> IDLE", 0, read_step(client)))

    # 8. E-stop in WORK -> IDLE, no restart without Start
    write_hr(client, CMD, 1)
    set_coil(client, E_STOP_OK, False)
    r.append(check("8a E-stop in WORK -> IDLE", 0, read_step(client)))
    set_coil(client, E_STOP_OK, True)
    time.sleep(0.5)
    r.append(check("8b E-stop released -> stays IDLE", 0, read_step(client)))
    return r


def part_with_done(client):
    """Done bits forced TRUE in the debugger (Jobs_Done = TRUE)."""
    r = []
    prepare(client)
    write_hr(client, CMD, 1)
    r.append(check("D0 IDLE + Start, empty machine -> WORK", 10, read_step(client)))

    # empty machine: nothing to move -> stays in WORK (Starved later, stage 7)
    time.sleep(0.5)
    r.append(check("D1 WORK, jobs done, no bottle -> stays", 10, read_step(client)))

    # bottle at S3, C2 free -> INDEX
    set_coil(client, S3, True)
    r.append(check("D2 WORK, jobs done, S3 -> INDEX", 20, read_step(client)))
    set_coil(client, S3, False)

    # bottle at the capper but C2 free -> INDEX (not WAIT)
    write_hr(client, STEP, 10)
    set_coil(client, S5, True)
    r.append(check("D3 WORK, S5, C2 free -> INDEX", 20, read_step(client)))

    # bottle at the capper and C2 full -> WAIT_OUTFEED, then INDEX when free
    set_coil(client, S7, True)
    time.sleep(2.5)
    write_hr(client, STEP, 10)
    time.sleep(0.5)
    r.append(check("D4 WORK, S5, C2 full -> WAIT_OUTFEED", 15, read_step(client)))
    set_coil(client, S7, False)
    time.sleep(3.5)
    r.append(check("D5 C2 free again -> INDEX", 20, read_step(client)))

    # full cycle: S8 edge -> WORK -> (jobs done, S5, C2 free) -> INDEX again
    pulse_s8(client)
    r.append(check("D6 S8 edge -> WORK -> INDEX (one transition per scan)", 20, read_step(client)))
    return r


def main():
    claim_plant("test_sequencer.py")  # writes the sensors: never with the simulator (plant_lock.py)
    with_done = "--done" in sys.argv
    heartbeat = Heartbeat()
    heartbeat.start()
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = part_with_done(client) if with_done else part_without_done(client)
    finally:
        clear_sensors(client)
        client.write_register(STEP, 0)
        client.close()
        heartbeat.stop_event.set()
        heartbeat.join()

    print()
    print("Sequencer OK" if all(results) else "Sequencer NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
