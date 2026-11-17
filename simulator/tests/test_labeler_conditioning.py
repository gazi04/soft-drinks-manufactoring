"""Labeler ladder stage 3: conditioning (Outfeed_Full filter on C3_Full).

Runs against the labeler PLC on port 5023. Needs the section 3 rungs in the runtime:
    A1-A3  C3_Full -> Full_OnTimer (2 s) / Full_OffTimer (3 s) -> seal-in Outfeed_Full
    B1     NOT Bottle_At_Gate -> Gate_Empty_Timer (StarvedDelay)   debugger only (internal timer)
    C1     rising edge of Label_Mark -> Mark_Edge                   tested in stage 4 (WRAP 22 -> 24):
                                                                    a one-scan pulse is invisible over Modbus

    F0 start               C3_Full OFF 3.5 s -> Outfeed_Full FALSE (any old seal-in released)
    F1 one bottle passing  C3_Full ON 1 s -> FALSE (on-timer needs 2 s)
    F2 bottles flowing     C3_Full blinking 0.5 s ON / 0.5 s OFF for 5 s -> never TRUE
    F3 bottles standing    C3_Full ON 3 s -> TRUE
    F4 hysteresis          C3_Full OFF 2 s -> still TRUE (seal-in, off-timer needs 3 s)
    F5 really free         C3_Full OFF 4 s in total -> FALSE

Addresses:
    C3_Full        %MX0.4  coil 8196   written by this script (sensor)
    Outfeed_Full   %MX3.0  coil 8216   written by the PLC

    uv run python tests/test_labeler_conditioning.py

Takes about 25 s. Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5023

COIL_C3_FULL = 8196  # %MX0.4
COIL_OUTFEED_FULL = 8216  # %MX3.0

SETTLE = 0.2  # s: many scans of the 20 ms task


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


def read_coil(client: ModbusTcpClient, address: int) -> bool:
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Read coil {address} failed: {result}")
    return bool(result.bits[0])


def hold_c3_full(client: ModbusTcpClient, value: bool, seconds: float) -> None:
    """Keep C3_Full at `value` for `seconds` (bottles standing at the sensor, or none)."""
    client.write_coil(COIL_C3_FULL, value)
    time.sleep(seconds)


def check(name: str, expected: bool, got: bool) -> bool:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: Outfeed_Full {got} (expected {expected})")
    return ok


def main() -> int:
    claim_plant("test_labeler_conditioning", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_conditioning")
    client = connect()
    results = []
    try:
        # the sensor writes must reach the PLC: a debugger force would block them
        for value in (True, False):
            client.write_coil(COIL_C3_FULL, value)
            time.sleep(SETTLE)
            if read_coil(client, COIL_C3_FULL) != value:
                raise SystemExit("C3_Full does not follow the writes: release all forces in the debugger")

        hold_c3_full(client, False, 3.5)
        results.append(check("F0 start, C3_Full OFF 3.5 s", False, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c3_full(client, True, 1.0)
        results.append(check("F1 C3_Full ON 1 s (one bottle passing)", False, read_coil(client, COIL_OUTFEED_FULL)))
        hold_c3_full(client, False, SETTLE)

        was_full = False  # read in both halves of every blink: one TRUE anywhere is a fail
        for _ in range(5):
            hold_c3_full(client, True, 0.5)
            was_full |= read_coil(client, COIL_OUTFEED_FULL)
            hold_c3_full(client, False, 0.5)
            was_full |= read_coil(client, COIL_OUTFEED_FULL)
        results.append(check("F2 C3_Full blinking 5 s (bottles flowing)", False, was_full))

        hold_c3_full(client, True, 3.0)
        results.append(check("F3 C3_Full ON 3 s (bottles standing)", True, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c3_full(client, False, 2.0)
        results.append(check("F4 C3_Full OFF 2 s (seal-in holds)", True, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c3_full(client, False, 2.0)
        results.append(check("F5 C3_Full OFF 4 s (really free)", False, read_coil(client, COIL_OUTFEED_FULL)))
    finally:
        client.write_coil(COIL_C3_FULL, False)  # idle state: conveyor free
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (C3_Full back to FALSE)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
