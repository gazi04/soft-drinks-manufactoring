"""Stage 2 test of FB_Valve over Modbus.

Needs the test rung in plcs/filler main (rung 14) running in the runtime:
    valvetest : FB_Valve
    testopen        (%MX7.3, coil 8251)       -> Open      written by this script
    testvalveenable (%MX7.4, coil 8252)       -> Enable    written by this script
    testvalveout    (%MX7.5, coil 8253)       <- Out       written by the PLC
    testcycles      (%MD2,   HR 2052-2053)    <- Cycles    written by the PLC
    testopentime    (%ML0,   HR 4096-4099)    <- OpenTime  written by the PLC (ms)
    testlastopen    (%ML1,   HR 4100-4103)    <- LastOpen  written by the PLC (ms)

The counters are never reset, so the checks compare differences
("Cycles went up by 1"), not absolute values.

Register layout (OpenPLC v4, default segment sizes), first register = HIGH word:
    %MDn -> HR 2048 + 2n  (2 registers, 32 bit)
    %MLn -> HR 4096 + 4n  (4 registers, 64 bit)
"""

import time

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502

OPEN_COIL = 8251  # %MX7.3
ENABLE_COIL = 8252  # %MX7.4
OUT_COIL = 8253  # %MX7.5
CYCLES_HR = 2052  # %MD2
OPENTIME_HR = 4096  # %ML0
LASTOPEN_HR = 4100  # %ML1

SCAN_WAIT = 0.5  # seconds; gives the PLC time to run a few scans
TOLERANCE_MS = 300  # Modbus write + sleep timing is not exact


def read_coil(client, address):
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading coil {address} failed: {result}")
    return result.bits[0]


def read_int(client, address, registers):
    """Read a 32-bit (2 registers) or 64-bit (4 registers) value, high word first."""
    result = client.read_holding_registers(address, count=registers)
    if result.isError():
        raise SystemExit(f"Reading holding registers from {address} failed: {result}")
    value = 0
    for word in result.registers:
        value = value * 65536 + word
    return value


def read_cycles(client):
    return read_int(client, CYCLES_HR, 2)


def read_opentime(client):
    return read_int(client, OPENTIME_HR, 4)


def read_lastopen(client):
    return read_int(client, LASTOPEN_HR, 4)


def set_inputs(client, open_, enable):
    client.write_coil(OPEN_COIL, open_)
    client.write_coil(ENABLE_COIL, enable)
    time.sleep(SCAN_WAIT)


def check(name, expected, actual):
    status = "PASS" if expected == actual else "FAIL"
    print(f"{status}  {name}: expected {expected}, got {actual}")
    return status == "PASS"


def check_ms(name, expected_ms, actual_ms):
    """PASS if actual is within TOLERANCE_MS of expected."""
    ok = abs(actual_ms - expected_ms) <= TOLERANCE_MS
    print(f"{'PASS' if ok else 'FAIL'}  {name}: expected {expected_ms} ±{TOLERANCE_MS} ms, got {actual_ms}")
    return ok


def open_for(client, seconds):
    """Open the valve, keep it open for `seconds`, close it. Returns the measured open time."""
    client.write_coil(OPEN_COIL, True)
    start = time.monotonic()
    time.sleep(seconds)
    client.write_coil(OPEN_COIL, False)
    measured = round((time.monotonic() - start) * 1000)
    time.sleep(SCAN_WAIT)
    return measured


def main():
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")

    results = []
    try:
        # check the inputs really reach the PLC: a debugger force would block the writes
        for value in (False, True):
            client.write_coil(OPEN_COIL, value)
            client.write_coil(ENABLE_COIL, value)
            time.sleep(SCAN_WAIT)
            if read_coil(client, OPEN_COIL) != value or read_coil(client, ENABLE_COIL) != value:
                raise SystemExit("testopen / testvalveenable do not follow the writes: release all forces in the debugger")

        # start from a closed valve
        set_inputs(client, open_=False, enable=True)
        cycles0 = read_cycles(client)
        opentime0 = read_opentime(client)

        # 1. Open AND Enable -> Out, one cycle counted
        set_inputs(client, open_=True, enable=True)
        results.append(check("1 Open T, Enable T -> Out", True, read_coil(client, OUT_COIL)))
        results.append(check("1 Cycles +1", 1, read_cycles(client) - cycles0))
        set_inputs(client, open_=False, enable=True)

        # 2. Open for 3 s -> LastOpen ≈ 3000 ms, OpenTime grows by the same amount
        opentime1 = read_opentime(client)
        measured = open_for(client, 3)
        results.append(check("2 Open F -> Out", False, read_coil(client, OUT_COIL)))
        results.append(check_ms("2 LastOpen", measured, read_lastopen(client)))
        results.append(check("2 OpenTime grew by LastOpen", read_lastopen(client),
                             read_opentime(client) - opentime1))

        # 3. Open for 2 s -> each opening is measured on its own
        measured = open_for(client, 2)
        results.append(check_ms("3 LastOpen", measured, read_lastopen(client)))
        results.append(check("3 Cycles +3 total", 3, read_cycles(client) - cycles0))

        # 4. Safety: Enable F closes the valve although Open is still TRUE
        set_inputs(client, open_=True, enable=True)
        opentime2 = read_opentime(client)
        set_inputs(client, open_=True, enable=False)
        results.append(check("4 Enable F (Open T) -> Out", False, read_coil(client, OUT_COIL)))
        results.append(check("4 open time added on forced close", True, read_opentime(client) > opentime2))
        results.append(check("4 Cycles unchanged (+4 total)", 4, read_cycles(client) - cycles0))

        print(f"\nOpenTime total during this test: {read_opentime(client) - opentime0} ms")
    finally:
        # leave the test valve closed
        set_inputs(client, open_=False, enable=False)
        client.close()

    print()
    if all(results):
        print("FB_Valve OK")
    else:
        print("FB_Valve NOT OK, see the FAIL lines above")


if __name__ == "__main__":
    main()
