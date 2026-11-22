"""Packer ladder stage 2: oven and film values (Oven_Ready window, Heat_Demand hysteresis, FeedPulses).

Runs against the packer PLC on port 5024. OvenTemp / FilmLength are written step by step; after
each step the result is read back. Defaults: OvenSetpoint 1900, OvenHyst 50, OvenBand 150 (0.1 degC),
FilmLength 850 mm.

    V1 Oven_Ready window   OvenTemp 1740 -> FALSE, 1750 -> TRUE (lower edge, GE), 1900 -> TRUE,
                           2050 -> TRUE (upper edge, LE), 2060 -> FALSE
    V2 window follows      OvenBand 100 (window 1800-2000): 1790 -> FALSE, 1800 -> TRUE,
       OvenBand            2000 -> TRUE, 2010 -> FALSE; OvenBand back to 150: 1790 -> TRUE
    V3 Heat_Demand         OvenTemp 1900 -> FALSE, 1850 -> FALSE (LT: not below 1850),
       hysteresis          1840 -> TRUE (set), 1860 -> TRUE (band: held), 1900 -> FALSE (reset
                           at the setpoint), 1880 -> FALSE (band: stays off)
    V4 hysteresis follows  OvenHyst 100: 1820 -> FALSE (not below 1800), 1799 -> TRUE (set);
       OvenHyst            OvenHyst back to 50: 1880 -> TRUE (band: held), 1900 -> FALSE
    V5 FeedPulses          FilmLength 850 -> 17, 870 -> 17 (INT division truncates), 600 -> 12,
                           1200 -> 24, 0 -> 12, -50 -> 12, 5000 -> 24 (LIMIT keeps 600-1200 mm)

    uv run python tests/test_packer_values.py

The important steps are the "band" ones: between Heat_On_Lim and OvenSetpoint Heat_Demand keeps its
OLD value, so 1860 gives TRUE on the way up from cold and 1880 FALSE on the way down from hot.
All values are computed in every mode (ModeReq is not touched). Oven_BelowMax has no address: check
it in the debugger (OvenTemp 2299 -> TRUE, 2300 -> FALSE). At the end the setpoints go back to their
defaults and OvenTemp back to 0. Release all debugger forces first; do not run the simulator at the
same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5024

HR_OVEN_TEMP = 1036  # %MW12
HR_OVEN_SETPOINT = 1054  # %MW30
HR_OVEN_HYST = 1055  # %MW31
HR_OVEN_BAND = 1056  # %MW32
HR_FILM_LENGTH = 1057  # %MW33
HR_FEED_PULSES = 1066  # %MW42
COIL_HEAT_DEMAND = 8219  # %MX3.3
COIL_OVEN_READY = 8220  # %MX3.4

DEFAULTS = {HR_OVEN_SETPOINT: 1900, HR_OVEN_HYST: 50, HR_OVEN_BAND: 150, HR_FILM_LENGTH: 850}
NAMES = {HR_OVEN_TEMP: "OvenTemp", HR_OVEN_SETPOINT: "OvenSetpoint", HR_OVEN_HYST: "OvenHyst",
         HR_OVEN_BAND: "OvenBand", HR_FILM_LENGTH: "FilmLength"}
SETTLE = 0.2  # s: many scans of the 20 ms task

T = HR_OVEN_TEMP
F = HR_FILM_LENGTH
COIL, WORD = "coil", "word"
CHECKS = {  # name: (kind, address, [(registers to write, expected value, note)]) - writes run in dict order
    "V1 Oven_Ready": (COIL, COIL_OVEN_READY, [
        ({T: 1740}, False, "below 1750"), ({T: 1750}, True, "lower edge 1900-150, GE"),
        ({T: 1900}, True, "at the setpoint"), ({T: 2050}, True, "upper edge 1900+150, LE"),
        ({T: 2060}, False, "above 2050")]),
    "V2 Oven_Ready": (COIL, COIL_OVEN_READY, [
        ({HR_OVEN_BAND: 100, T: 1790}, False, "band 100: window 1800-2000"),
        ({T: 1800}, True, "new lower edge"), ({T: 2000}, True, "new upper edge"),
        ({T: 2010}, False, "above 2000, inside the old window"),
        ({HR_OVEN_BAND: 150, T: 1790}, True, "band back to 150: window 1750-2050")]),
    "V3 Heat_Demand": (COIL, COIL_HEAT_DEMAND, [
        ({T: 1900}, False, "at the setpoint: reset"), ({T: 1850}, False, "not below 1900-50 (LT)"),
        ({T: 1840}, True, "set below 1850"), ({T: 1860}, True, "band: held"),
        ({T: 1900}, False, "reset at the setpoint"), ({T: 1880}, False, "band: stays off")]),
    "V4 Heat_Demand": (COIL, COIL_HEAT_DEMAND, [
        ({HR_OVEN_HYST: 100, T: 1820}, False, "hyst 100: not below 1800"),
        ({T: 1799}, True, "set below 1800"),
        ({HR_OVEN_HYST: 50, T: 1880}, True, "hyst back to 50: band 1850-1900, held"),
        ({T: 1900}, False, "reset at the setpoint")]),
    "V5 FeedPulses": (WORD, HR_FEED_PULSES, [
        ({F: 850}, 17, "850 / 50"), ({F: 870}, 17, "870 / 50 = 17.4, INT truncates"),
        ({F: 600}, 12, "lower limit"), ({F: 1200}, 24, "upper limit"),
        ({F: 0}, 12, "LIMIT: 0 -> 600"), ({F: -50}, 12, "LIMIT: negative -> 600"),
        ({F: 5000}, 24, "LIMIT: 5000 -> 1200")]),
}


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


def read(client: ModbusTcpClient, kind: str, address: int) -> bool | int:
    result = (client.read_coils(address, count=1) if kind == COIL
              else client.read_holding_registers(address, count=1))
    if result.isError():
        raise SystemExit(f"Read {kind} {address} failed: {result}")
    if kind == COIL:
        return bool(result.bits[0])
    value = result.registers[0]
    return value - 0x10000 if value >= 0x8000 else value  # INT is signed


def write(client: ModbusTcpClient, register: int, value: int) -> None:
    client.write_register(register, value & 0xFFFF)  # a negative INT goes over Modbus as two's complement


def main() -> int:
    claim_plant("test_packer_values", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_values")
    client = connect()
    results = []
    try:
        for register, value in DEFAULTS.items():  # start from the defaults the thresholds assume
            write(client, register, value)
        for name, (kind, address, steps) in CHECKS.items():
            for writes, expected, note in steps:
                for register, value in writes.items():
                    write(client, register, value)
                time.sleep(SETTLE)
                got = read(client, kind, address)
                ok = got == expected
                results.append(ok)
                what = ", ".join(f"{NAMES[r]} {v}" for r, v in writes.items())
                print(f"{'PASS' if ok else 'FAIL'}  {name}: {what:<25} -> {got} (expected {expected}, {note})")
    finally:
        for register, value in DEFAULTS.items():
            write(client, register, value)
        write(client, HR_OVEN_TEMP, 0)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (setpoints back to the defaults, OvenTemp back to 0)")
    print("Debugger (127.0.0.4): OvenTemp 2299 -> Oven_BelowMax TRUE, 2300 -> FALSE.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
