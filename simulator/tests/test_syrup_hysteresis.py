"""Syrup room ladder stage 3: hysteresis bits (two thresholds + seal-in).

Runs against the syrup PLC on port 5022. Each value is ramped down and up again; the bit
must switch only at its two thresholds and keep its value in the band between them.
Defaults: CoolSetpoint 40, TempHyst 5, PressSetpoint 350, PressHyst 20, BufferMin 100, AvailBand 20.

    H1 Temp_OK            ProductTemp 60 -> FALSE, 41 -> FALSE, 40 -> TRUE (set),
                          44 / 45 -> TRUE (band), 46 -> FALSE (reset), 44 -> FALSE (band, stays off)
    H2 CO2_Demand         CO2Pressure 360 -> FALSE, 330 -> FALSE, 329 -> TRUE (set),
                          340 / 349 -> TRUE (band), 350 -> FALSE (reset), 340 -> FALSE (band, stays off)
    H3 Product_Available  BufferLevel 0 -> FALSE, 119 -> FALSE, 120 -> TRUE (set),
                          105 / 100 -> TRUE (band), 99 -> FALSE (reset), 110 -> FALSE (band, stays off)

    uv run python tests/test_syrup_hysteresis.py

The last step of each ramp is the important one: inside the band the bit keeps its OLD value,
so the same value (44, 340, 110) gives TRUE on the way in and FALSE on the way back.
Sensors are set back to 0 at the end. Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5022

HR_PRODUCT_TEMP = 1038  # %MW14
HR_CO2_PRESSURE = 1039  # %MW15
HR_BUFFER_LEVEL = 1040  # %MW16
HR_SETPOINTS = 1057  # %MW33..38: CoolSetpoint, TempHyst, PressSetpoint, PressHyst, BufferLow, BufferMin
COIL_TEMP_OK = 8216  # %MX3.0
COIL_CO2_DEMAND = 8217  # %MX3.1
COIL_PRODUCT_AVAILABLE = 8240  # %MX6.0

EXPECTED_SETPOINTS = [40, 5, 350, 20, 800, 100]
SETTLE = 0.2  # s: many scans of the 20 ms task

RAMPS = {  # name: (sensor register, coil, [(value, expected bit, note)])
    "H1 Temp_OK": (HR_PRODUCT_TEMP, COIL_TEMP_OK, [
        (60, False, "warm"), (41, False, "above set threshold 40"), (40, True, "set at <= 40"),
        (44, True, "band: held"), (45, True, "band edge <= 45: held"), (46, False, "reset above 45"),
        (44, False, "band: stays off")]),
    "H2 CO2_Demand": (HR_CO2_PRESSURE, COIL_CO2_DEMAND, [
        (360, False, "pressure high"), (330, False, "not below 330"), (329, True, "set below 330"),
        (340, True, "band: held"), (349, True, "band edge < 350: held"), (350, False, "reset at 350"),
        (340, False, "band: stays off")]),
    "H3 Product_Available": (HR_BUFFER_LEVEL, COIL_PRODUCT_AVAILABLE, [
        (0, False, "buffer empty"), (119, False, "below 120"), (120, True, "set at >= 120"),
        (105, True, "band: held"), (100, True, "band edge >= 100: held"), (99, False, "reset below 100"),
        (110, False, "band: stays off")]),
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
    claim_plant("test_syrup_hysteresis", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_hysteresis")
    client = connect()
    results = []
    try:
        setpoints = client.read_holding_registers(HR_SETPOINTS, count=6).registers
        if setpoints != EXPECTED_SETPOINTS:
            print(f"NOTE setpoints %MW33-38 = {setpoints}, expected defaults {EXPECTED_SETPOINTS}: thresholds below assume the defaults")
        for name, (register, coil, steps) in RAMPS.items():
            for value, expected, note in steps:
                client.write_register(register, value)
                time.sleep(SETTLE)
                got = read_coil(client, coil)
                ok = got == expected
                results.append(ok)
                print(f"{'PASS' if ok else 'FAIL'}  {name}: {value:>4} -> {got} (expected {expected}, {note})")
    finally:
        for register in (HR_PRODUCT_TEMP, HR_CO2_PRESSURE, HR_BUFFER_LEVEL):
            client.write_register(register, 0)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (sensors back to 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
