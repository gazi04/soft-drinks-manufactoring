"""Stage 3 test of the signal conditioning rungs over Modbus.

Needs plcs/filler main rungs 15-20 running in the runtime:
    15-17  S7 -> Full_OnTimer (2 s) / Full_OffTimer (3 s) -> seal-in Outfeed_Full
    18     rising edge of S6 -> S6_Edge  (one scan per bottle)
    19     rising edge of S8 -> S8_Edge  (not tested here: same rung as 18 with S8)
    S6_Edge is counted by the stage 6 counter: S6_Edge -> CTU Good_Counter -> GoodCount
    (one-scan pulses are invisible over Modbus, the counter makes them countable)

Addresses:
    S6             %MX0.4  coil 8196        written by this script (sensor)
    S7             %MX0.5  coil 8197        written by this script (sensor)
    Outfeed_Full   %MX3.0  coil 8216        written by the PLC
    GoodCount      %MW53   HR 1077          written by the PLC (CTU CV)
"""

import time

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502

S6_COIL = 8196  # %MX0.4
S7_COIL = 8197  # %MX0.5
OUTFEED_FULL_COIL = 8216  # %MX3.0
GOODCOUNT_HR = 1077  # %MW53

SCAN_WAIT = 0.3  # seconds; gives the PLC time to run a few scans


def read_coil(client, address):
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading coil {address} failed: {result}")
    return result.bits[0]


def read_hr(client, address):
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading holding register {address} failed: {result}")
    return result.registers[0]


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def outfeed_full(client):
    return read_coil(client, OUTFEED_FULL_COIL)


def hold_s7(client, value, seconds):
    """Keep S7 at `value` for `seconds` (a bottle standing still, or no bottle)."""
    client.write_coil(S7_COIL, value)
    time.sleep(seconds)


def main():
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")

    results = []
    try:
        # check the sensor writes really reach the PLC: a debugger force would block them
        for value in (True, False):
            client.write_coil(S6_COIL, value)
            client.write_coil(S7_COIL, value)
            time.sleep(SCAN_WAIT)
            if read_coil(client, S6_COIL) != value or read_coil(client, S7_COIL) != value:
                raise SystemExit("S6 / S7 do not follow the writes: release all forces in the debugger")

        # start from a free conveyor: S7 OFF long enough to release any old Outfeed_Full
        hold_s7(client, False, 3.5)
        results.append(check("0 start: conveyor free", False, outfeed_full(client)))

        # 1. one bottle passes (S7 ON 1 s) -> not full
        hold_s7(client, True, 1.0)
        results.append(check("1 S7 ON 1 s (one bottle passing)", False, outfeed_full(client)))
        hold_s7(client, False, SCAN_WAIT)

        # 2. bottles flowing: S7 blinks 0.5 s ON / 0.3 s OFF for about 5 s -> not full
        was_full = False
        for _ in range(6):
            hold_s7(client, True, 0.5)
            was_full |= outfeed_full(client)
            hold_s7(client, False, 0.3)
            was_full |= outfeed_full(client)
        results.append(check("2 S7 blinking 5 s (bottles flowing)", False, was_full))

        # 3. bottles stand still: S7 ON 3 s -> full
        hold_s7(client, True, 3.0)
        results.append(check("3 S7 ON 3 s (bottles standing)", True, outfeed_full(client)))

        # 4. S7 OFF 2 s -> still full (seal-in holds, off-timer needs 3 s)
        hold_s7(client, False, 2.0)
        results.append(check("4 S7 OFF 2 s (hysteresis)", True, outfeed_full(client)))

        # 5. S7 OFF 4 s in total -> free again
        hold_s7(client, False, 2.0)
        results.append(check("5 S7 OFF 4 s (really free)", False, outfeed_full(client)))

        # 6. 5 bottles leave the machine: 5 pulses on S6 -> 5 edges, not one per scan
        count0 = read_hr(client, GOODCOUNT_HR)
        for _ in range(5):
            client.write_coil(S6_COIL, True)
            time.sleep(SCAN_WAIT)
            client.write_coil(S6_COIL, False)
            time.sleep(SCAN_WAIT)
        results.append(check("6 5 pulses on S6 -> GoodCount +5", 5, read_hr(client, GOODCOUNT_HR) - count0))
    finally:
        # leave the sensors in their idle state
        client.write_coil(S6_COIL, False)
        client.write_coil(S7_COIL, False)
        client.close()

    print()
    if all(results):
        print("Signal conditioning OK")
    else:
        print("Signal conditioning NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
