"""Labeler ladder stage 2: glue temperature (Glue_Ready window, Heat_Demand hysteresis).

Runs against the labeler PLC on port 5023. GlueTemp is written step by step; after each step
the bit is read back. Defaults: GlueSetpoint 1700, GlueHyst 50, GlueBand 100 (0.1 degC).

    G1 Glue_Ready window   GlueTemp 1590 -> FALSE, 1600 -> TRUE (lower edge, GE), 1700 -> TRUE,
                           1800 -> TRUE (upper edge, LE), 1810 -> FALSE
    G2 window follows      GlueBand 50 (window 1650-1750): 1640 -> FALSE, 1650 -> TRUE,
       GlueBand            1750 -> TRUE, 1760 -> FALSE; GlueBand back to 100: 1640 -> TRUE
    G3 Heat_Demand         GlueTemp 1750 -> FALSE, 1650 -> FALSE (LT: not below 1650),
       hysteresis          1649 -> TRUE (set), 1660 / 1699 -> TRUE (band: held),
                           1700 -> FALSE (reset at the setpoint), 1680 -> FALSE (band: stays off)
    G4 hysteresis follows  GlueHyst 100: 1620 -> FALSE (not below 1600), 1599 -> TRUE (set);
       GlueHyst            GlueHyst back to 50: 1680 -> TRUE (band: held), 1700 -> FALSE

    uv run python tests/test_labeler_glue.py

The important steps are the "band" ones: between Heat_On_Lim and GlueSetpoint the bit keeps its
OLD value, so 1680 gives TRUE on the way up from cold and FALSE on the way down from hot.
Both bits are computed in every mode (ModeReq is not touched). At the end the setpoints go back
to their defaults and GlueTemp back to 0. Release all debugger forces first; do not run the
simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5023

HR_GLUE_TEMP = 1036  # %MW12
HR_GLUE_SETPOINT = 1054  # %MW30
HR_GLUE_HYST = 1055  # %MW31
HR_GLUE_BAND = 1056  # %MW32
COIL_HEAT_DEMAND = 8219  # %MX3.3
COIL_GLUE_READY = 8220  # %MX3.4

DEFAULTS = {HR_GLUE_SETPOINT: 1700, HR_GLUE_HYST: 50, HR_GLUE_BAND: 100}
NAMES = {HR_GLUE_TEMP: "GlueTemp", HR_GLUE_SETPOINT: "GlueSetpoint", HR_GLUE_HYST: "GlueHyst",
         HR_GLUE_BAND: "GlueBand"}
SETTLE = 0.2  # s: many scans of the 20 ms task

T = HR_GLUE_TEMP
CHECKS = {  # name: (coil, [(registers to write, expected bit, note)]) - writes run in dict order
    "G1 Glue_Ready": (COIL_GLUE_READY, [
        ({T: 1590}, False, "below 1600"), ({T: 1600}, True, "lower edge 1700-100, GE"),
        ({T: 1700}, True, "at the setpoint"), ({T: 1800}, True, "upper edge 1700+100, LE"),
        ({T: 1810}, False, "above 1800")]),
    "G2 Glue_Ready": (COIL_GLUE_READY, [
        ({HR_GLUE_BAND: 50, T: 1640}, False, "band 50: window 1650-1750"),
        ({T: 1650}, True, "new lower edge"), ({T: 1750}, True, "new upper edge"),
        ({T: 1760}, False, "above 1750, inside the old window"),
        ({HR_GLUE_BAND: 100, T: 1640}, True, "band back to 100: window 1600-1800")]),
    "G3 Heat_Demand": (COIL_HEAT_DEMAND, [
        ({T: 1750}, False, "hot: reset"), ({T: 1650}, False, "not below 1700-50 (LT)"),
        ({T: 1649}, True, "set below 1650"), ({T: 1660}, True, "band: held"),
        ({T: 1699}, True, "band: held"), ({T: 1700}, False, "reset at the setpoint"),
        ({T: 1680}, False, "band: stays off")]),
    "G4 Heat_Demand": (COIL_HEAT_DEMAND, [
        ({HR_GLUE_HYST: 100, T: 1620}, False, "hyst 100: not below 1600"),
        ({T: 1599}, True, "set below 1600"),
        ({HR_GLUE_HYST: 50, T: 1680}, True, "hyst back to 50: band 1650-1700, held"),
        ({T: 1700}, False, "reset at the setpoint")]),
}


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


def main() -> int:
    claim_plant("test_labeler_glue", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_labeler_glue")
    client = connect()
    results = []
    try:
        for register, value in DEFAULTS.items():  # start from the defaults the thresholds assume
            client.write_register(register, value)
        for name, (coil, steps) in CHECKS.items():
            for writes, expected, note in steps:
                for register, value in writes.items():
                    client.write_register(register, value)
                time.sleep(SETTLE)
                got = read_coil(client, coil)
                ok = got == expected
                results.append(ok)
                what = ", ".join(f"{NAMES[r]} {v}" for r, v in writes.items())
                print(f"{'PASS' if ok else 'FAIL'}  {name}: {what:<25} -> {got} (expected {expected}, {note})")
    finally:
        for register, value in DEFAULTS.items():
            client.write_register(register, value)
        client.write_register(HR_GLUE_TEMP, 0)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (setpoints back to the defaults, GlueTemp back to 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.3? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
