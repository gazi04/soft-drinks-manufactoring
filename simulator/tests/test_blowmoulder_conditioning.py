"""Blow moulder ladder stage 3: conditioning (Outfeed_Full filter on C1_Full).

Runs against the blow moulder PLC on port 5025. Needs the section 3 rungs in the runtime:
    C1_Full -> Full_OnTimer (2 s) / Full_OffTimer (3 s) -> seal-in Outfeed_Full
(Feed_Empty_Timer is built in stage 8 next to Starved, the Mould_Click edge in stage 7.)

    F0 start               C1_Full OFF 3.5 s -> Outfeed_Full FALSE (any old seal-in released)
    F1 one bottle sliding past  C1_Full ON 1 s -> FALSE (on-timer needs 2 s)
    F2 bottles flowing     C1_Full blinking 0.5 s ON / 0.5 s OFF for 5 s -> never TRUE
    F3 bottles standing    C1_Full ON 3 s -> TRUE
    F4 hysteresis          C1_Full OFF 2 s -> still TRUE (seal-in, off-timer needs 3 s)
    F5 really free         C1_Full OFF 4 s in total -> FALSE
    F6 every mode          E_Stop_OK FALSE and ModeReq 0 (no Master_Enable): C1_Full ON 3 s -> TRUE,
                           OFF 4 s -> FALSE (the filter is a sensor, not an output: report 5.3)

Addresses:
    C1_Full        %MX4.4  coil 8228   written by this script (sensor)
    Outfeed_Full   %MX3.0  coil 8216   written by the PLC
    E_Stop_OK      %MX1.1  coil 8201   F6 only, set back afterwards
    ModeReq        %MW1    HR 1025     F6 only, set back afterwards

    uv run python tests/test_blowmoulder_conditioning.py

Takes about 35 s. Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5025

COIL_C1_FULL = 8228  # %MX4.4
COIL_OUTFEED_FULL = 8216  # %MX3.0
COIL_E_STOP_OK = 8201  # %MX1.1
HR_MODE_REQ = 1025  # %MW1

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


def read_hr(client: ModbusTcpClient, address: int) -> int:
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Read HR {address} failed: {result}")
    return result.registers[0]


def hold_c1_full(client: ModbusTcpClient, value: bool, seconds: float) -> None:
    """Keep C1_Full at `value` for `seconds` (bottles standing at the sensor, or none)."""
    client.write_coil(COIL_C1_FULL, value)
    time.sleep(seconds)


def check(name: str, expected: bool, got: bool) -> bool:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: Outfeed_Full {got} (expected {expected})")
    return ok


def main() -> int:
    claim_plant("test_blowmoulder_conditioning", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_conditioning")
    client = connect()
    results = []
    e_stop_before, mode_before = read_coil(client, COIL_E_STOP_OK), read_hr(client, HR_MODE_REQ)
    try:
        # the sensor writes must reach the PLC: a debugger force would block them
        for value in (True, False):
            client.write_coil(COIL_C1_FULL, value)
            time.sleep(SETTLE)
            if read_coil(client, COIL_C1_FULL) != value:
                raise SystemExit("C1_Full does not follow the writes: release all forces in the debugger")

        hold_c1_full(client, False, 3.5)
        results.append(check("F0 start, C1_Full OFF 3.5 s", False, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c1_full(client, True, 1.0)
        results.append(check("F1 C1_Full ON 1 s (one bottle sliding past)", False, read_coil(client, COIL_OUTFEED_FULL)))
        hold_c1_full(client, False, SETTLE)

        was_full = False  # read in both halves of every blink: one TRUE anywhere is a fail
        for _ in range(5):
            hold_c1_full(client, True, 0.5)
            was_full |= read_coil(client, COIL_OUTFEED_FULL)
            hold_c1_full(client, False, 0.5)
            was_full |= read_coil(client, COIL_OUTFEED_FULL)
        results.append(check("F2 C1_Full blinking 5 s (bottles flowing)", False, was_full))

        hold_c1_full(client, True, 3.0)
        results.append(check("F3 C1_Full ON 3 s (bottles standing)", True, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c1_full(client, False, 2.0)
        results.append(check("F4 C1_Full OFF 2 s (seal-in holds)", True, read_coil(client, COIL_OUTFEED_FULL)))

        hold_c1_full(client, False, 2.0)
        results.append(check("F5 C1_Full OFF 4 s (really free)", False, read_coil(client, COIL_OUTFEED_FULL)))

        client.write_coil(COIL_E_STOP_OK, False)
        client.write_register(HR_MODE_REQ, 0)
        hold_c1_full(client, True, 3.0)
        results.append(check("F6 E-stop + Mode Stop, C1_Full ON 3 s", True, read_coil(client, COIL_OUTFEED_FULL)))
        hold_c1_full(client, False, 4.0)
        results.append(check("F6 E-stop + Mode Stop, C1_Full OFF 4 s", False, read_coil(client, COIL_OUTFEED_FULL)))
    finally:
        client.write_coil(COIL_C1_FULL, False)  # idle state: C1 free
        client.write_coil(COIL_E_STOP_OK, e_stop_before)
        client.write_register(HR_MODE_REQ, mode_before)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (C1_Full back to FALSE, E_Stop_OK / ModeReq as before)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
