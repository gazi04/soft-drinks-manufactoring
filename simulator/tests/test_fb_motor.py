"""Stage 2 test of FB_Motor over Modbus.

Needs the test rung in plcs/filler main (rung 13) running in the runtime:
    motortest : FB_Motor
    testrun     (%MX7.0, coil 8248)        -> Run      written by this script
    testenable  (%MX7.1, coil 8249)        -> Enable   written by this script
    testout     (%MX7.2, coil 8250)        <- Out      written by the PLC
    teststart   (%MD0,   HR 2048-2049)     <- Starts   written by the PLC
    testruntime (%MD1,   HR 2050-2051)     <- RunTime  written by the PLC

The counters are never reset, so the checks compare differences
("Starts went up by 1"), not absolute values.

%MD word order (found in this test): the first register is the HIGH word,
    value = HR[n] * 65536 + HR[n+1]
"""

import time

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502

RUN_COIL = 8248  # %MX7.0
ENABLE_COIL = 8249  # %MX7.1
OUT_COIL = 8250  # %MX7.2
STARTS_HR = 2048  # %MD0
RUNTIME_HR = 2050  # %MD1

SCAN_WAIT = 0.5  # seconds; gives the PLC time to run a few scans
RUN_SECONDS = 10  # how long the motor runs for the run-time check


def read_coil(client, address):
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading coil {address} failed: {result}")
    return result.bits[0]


def read_dint(client, address):
    """Read a 32-bit %MD value: two holding registers, high word first."""
    result = client.read_holding_registers(address, count=2)
    if result.isError():
        raise SystemExit(f"Reading holding registers {address}-{address + 1} failed: {result}")
    high, low = result.registers
    return high * 65536 + low


def set_inputs(client, run, enable):
    client.write_coil(RUN_COIL, run)
    client.write_coil(ENABLE_COIL, enable)
    time.sleep(SCAN_WAIT)


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def main():
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")

    results = []
    try:
        # start from a stopped motor
        set_inputs(client, run=False, enable=False)
        starts0 = read_dint(client, STARTS_HR)

        # 1. Run AND Enable -> Out, one start counted
        set_inputs(client, run=True, enable=True)
        results.append(check("1 Run T, Enable T -> Out", True, read_coil(client, OUT_COIL)))
        results.append(check("1 Starts +1", 1, read_dint(client, STARTS_HR) - starts0))

        # 2. RunTime counts about 1 per second while running
        runtime0 = read_dint(client, RUNTIME_HR)
        time.sleep(RUN_SECONDS)
        delta = read_dint(client, RUNTIME_HR) - runtime0
        results.append(check(f"2 RunTime +{RUN_SECONDS - 1}..{RUN_SECONDS + 1} in {RUN_SECONDS} s",
                             True, RUN_SECONDS - 1 <= delta <= RUN_SECONDS + 1))
        print(f"      (RunTime went up by {delta})")

        # 3. Run F -> Out F, RunTime stops
        set_inputs(client, run=False, enable=True)
        results.append(check("3 Run F -> Out", False, read_coil(client, OUT_COIL)))
        runtime1 = read_dint(client, RUNTIME_HR)
        time.sleep(3)
        results.append(check("3 RunTime stays while stopped", 0, read_dint(client, RUNTIME_HR) - runtime1))

        # 4. Run T again -> one more start
        set_inputs(client, run=True, enable=True)
        results.append(check("4 Run T again -> Starts +2 total", 2, read_dint(client, STARTS_HR) - starts0))

        # 5. Safety: Enable F wins over Run T
        set_inputs(client, run=True, enable=False)
        results.append(check("5 Enable F (Run T) -> Out", False, read_coil(client, OUT_COIL)))

        # 6. Enable T again -> motor restarts, counted as a new start
        set_inputs(client, run=True, enable=True)
        results.append(check("6 Enable T -> Out", True, read_coil(client, OUT_COIL)))
        results.append(check("6 Starts +3 total", 3, read_dint(client, STARTS_HR) - starts0))
    finally:
        # leave the test motor off
        set_inputs(client, run=False, enable=False)
        client.close()

    print()
    if all(results):
        print("FB_Motor OK")
    else:
        print("FB_Motor NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
