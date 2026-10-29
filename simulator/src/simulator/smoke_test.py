"""One-shot check of the OpenPLC v4 Modbus address mapping.

Needs the test program from plc/test-mapping/ running in the runtime:
    Rung 1:  TestBit (%MX0.0)            -> OutBit  (%QX0.0)
    Rung 2:  TestWord (%MW0) > 0         -> OutWord (%QX0.1)

The script writes the memory variables over Modbus and reads the outputs back.
If all four checks pass, the address formulas in docs/filler/io-list.md are confirmed:
    %MXa.b -> coil 8192 + a*8 + b
    %MWn   -> holding register 1024 + n
"""

import time

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 502

TEST_BIT_COIL = 8192  # %MX0.0, written by this script
TEST_WORD_HR = 1024  # %MW0, written by this script
OUT_BIT_COIL = 0  # %QX0.0, written by the PLC
OUT_WORD_COIL = 1  # %QX0.1, written by the PLC

SCAN_WAIT = 0.2  # seconds; gives the PLC time to run at least one scan


def read_coil(client, address):
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading coil {address} failed: {result}")
    return result.bits[0]


def read_register(client, address):
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Reading holding register {address} failed: {result}")
    return result.registers[0]


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
        # %MX test: coil 8192 should drive OutBit (coil 0) through rung 1
        for value in (True, False):
            client.write_coil(TEST_BIT_COIL, value)
            time.sleep(SCAN_WAIT)
            # read back the written address: did the value stay in PLC memory?
            results.append(
                check(f"coil {TEST_BIT_COIL} = {value} -> read back", value, read_coil(client, TEST_BIT_COIL))
            )
            results.append(
                check(f"coil {TEST_BIT_COIL} = {value} -> OutBit", value, read_coil(client, OUT_BIT_COIL))
            )

        # %MW test: HR 1024 should drive OutWord (coil 1) through rung 2 (TestWord > 0)
        for value, expected in ((5, True), (0, False)):
            client.write_register(TEST_WORD_HR, value)
            time.sleep(SCAN_WAIT)
            results.append(
                check(f"HR {TEST_WORD_HR} = {value} -> read back", value, read_register(client, TEST_WORD_HR))
            )
            results.append(
                check(f"HR {TEST_WORD_HR} = {value} -> OutWord", expected, read_coil(client, OUT_WORD_COIL))
            )
    finally:
        client.close()

    print()
    if all(results):
        print("Mapping confirmed: %MX0.0 = coil 8192, %MW0 = HR 1024")
    else:
        print("Mapping NOT confirmed, see the FAIL lines above")


if __name__ == "__main__":
    main()
