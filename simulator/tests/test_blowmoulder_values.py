"""Blow moulder ladder stage 2: analog values (oven window, lamp P controller, pressure scaling).

Runs against the blow moulder PLC on port 5025. OvenTemp / BlowPressure_Raw and the setpoints are
written step by step; after each step the result is read back. Defaults: OvenSetpoint 1050,
OvenBand 50 (0.1 degC), Kp 10, Bias 0, BlowSetpoint 350 (0.1 bar).

    V1 Oven_Ready window   OvenTemp 990 -> FALSE, 1000 -> TRUE (lower edge, GE), 1050 -> TRUE,
                           1100 -> TRUE (upper edge, LE), 1110 -> FALSE
    V2 window follows      OvenBand 100: 950 -> TRUE, 940 -> FALSE; OvenBand back to 50: 950 -> FALSE
    V3 OvenError           OvenTemp 1030 -> 20, 1080 -> -30, -2000 -> 3050 (not limited: HMI trend)
    V4 Lamp_Calc           OvenTemp 200 -> 100 (clamped), 1030 -> 20 (Kp 10 * 2.0 degC), 1049 -> 1
                           (MUL before DIV), 1050 -> 0, 1080 -> 0 (negative clamped)
    V5 Lamp_Calc gains     Bias 30: 1050 -> 30, 1080 -> 0 (30 - 30); Kp 5: 1049 -> 30 (5 / 10 truncates
                           to 0); Kp 30, Bias 0: 1040 -> 30, -2000 -> 100 (error 3050 limited to 1000,
                           30 * 1000 = 30000 fits INT: no overflow)
    V6 BlowPressure        raw 0 -> 0, 13824 -> 200, 24192 -> 350, 27648 -> 400, 32511 -> 470,
                           -2000 -> -28 (DINT division truncates toward 0)
    V7 Pressure_Reached    raw 24192 (350) -> TRUE, 24123 (349) -> FALSE; BlowSetpoint 300: 20736 (300)
                           -> TRUE, 20667 (299) -> FALSE
    V8 Pressure_Safe       raw 345 (4) -> TRUE, 346 (5) -> FALSE, 0 -> TRUE, -1000 (lower edge RawMin)
                           -> TRUE, -1001 -> FALSE (wire break: low but not trusted), -2000 -> FALSE

    uv run python tests/test_blowmoulder_values.py

Lamp_Calc is located at %MW40 (HR 1064) so the P controller can be tested without the lamps
(LampPower follows it from stage 6). All values are computed in every mode (ModeReq is not
touched). Oven_BelowMax and Press_Range_OK have no address: check them in the debugger
(OvenTemp 1299 -> Oven_BelowMax TRUE, 1300 -> FALSE; raw 32511 -> Press_Range_OK TRUE,
32512 -> FALSE). At the end the setpoints go back to their defaults and OvenTemp / raw back to 0.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5025

HR_OVEN_TEMP = 1036  # %MW12
HR_RAW = 1037  # %MW13
HR_OVEN_SETPOINT = 1054  # %MW30
HR_OVEN_BAND = 1055  # %MW31
HR_KP = 1056  # %MW32
HR_BIAS = 1057  # %MW33
HR_BLOW_SETPOINT = 1058  # %MW34
HR_LAMP_CALC = 1064  # %MW40
HR_BLOW_PRESSURE = 1065  # %MW41
HR_OVEN_ERROR = 1066  # %MW42
COIL_OVEN_READY = 8217  # %MX3.1
COIL_PRESSURE_REACHED = 8220  # %MX3.4
COIL_PRESSURE_SAFE = 8221  # %MX3.5

DEFAULTS = {HR_OVEN_SETPOINT: 1050, HR_OVEN_BAND: 50, HR_KP: 10, HR_BIAS: 0, HR_BLOW_SETPOINT: 350}
NAMES = {HR_OVEN_TEMP: "OvenTemp", HR_RAW: "raw", HR_OVEN_SETPOINT: "OvenSetpoint", HR_OVEN_BAND: "OvenBand",
         HR_KP: "Kp", HR_BIAS: "Bias", HR_BLOW_SETPOINT: "BlowSetpoint"}
SETTLE = 0.2  # s: many scans of the 20 ms task

T = HR_OVEN_TEMP
R = HR_RAW
COIL, WORD = "coil", "word"
CHECKS = {  # name: (kind, address, [(registers to write, expected value, note)]) - writes run in dict order
    "V1 Oven_Ready": (COIL, COIL_OVEN_READY, [
        ({T: 990}, False, "below 1000"), ({T: 1000}, True, "lower edge 1050-50, GE"),
        ({T: 1050}, True, "at the setpoint"), ({T: 1100}, True, "upper edge 1050+50, LE"),
        ({T: 1110}, False, "above 1100")]),
    "V2 Oven_Ready": (COIL, COIL_OVEN_READY, [
        ({HR_OVEN_BAND: 100, T: 950}, True, "band 100: lower edge 950"), ({T: 940}, False, "below 950"),
        ({HR_OVEN_BAND: 50, T: 950}, False, "band back to 50: window 1000-1100")]),
    "V3 OvenError": (WORD, HR_OVEN_ERROR, [
        ({T: 1030}, 20, "1050 - 1030"), ({T: 1080}, -30, "negative: too hot"),
        ({T: -2000}, 3050, "not limited (only Err_Lim is)")]),
    "V4 Lamp_Calc": (WORD, HR_LAMP_CALC, [
        ({T: 200}, 100, "error 850 -> 850 %, clamped to 100"), ({T: 1030}, 20, "Kp 10 * 20 / 10"),
        ({T: 1049}, 1, "10 * 1 / 10: MUL before DIV"), ({T: 1050}, 0, "no error"),
        ({T: 1080}, 0, "-30 clamped to 0")]),
    "V5 Lamp_Calc": (WORD, HR_LAMP_CALC, [
        ({HR_BIAS: 30, T: 1050}, 30, "Bias 30, no error"), ({T: 1080}, 0, "30 + (-30)"),
        ({HR_KP: 5, T: 1049}, 30, "Kp 5: 5 / 10 = 0, + Bias 30"),
        ({HR_KP: 30, HR_BIAS: 0, T: 1040}, 30, "Kp 30 * 10 / 10"),
        ({T: -2000}, 100, "error 3050 -> 1000, 30 * 1000 = 30000, no overflow"),
        ({HR_KP: 10}, 100, "Kp back to 10: 1000 % clamped")]),
    "V6 BlowPressure": (WORD, HR_BLOW_PRESSURE, [
        ({R: 0}, 0, "0 bar"), ({R: 13824}, 200, "half scale, 20.0 bar"), ({R: 24192}, 350, "35.0 bar"),
        ({R: 27648}, 400, "full scale, needs DINT"), ({R: 32511}, 470, "RawMax"),
        ({R: -2000}, -28, "-28.9 truncated toward 0")]),
    "V7 Pressure_Reached": (COIL, COIL_PRESSURE_REACHED, [
        ({R: 24192}, True, "350 >= 350"), ({R: 24123}, False, "349"),
        ({HR_BLOW_SETPOINT: 300, R: 20736}, True, "setpoint 300: 300"), ({R: 20667}, False, "299"),
        ({HR_BLOW_SETPOINT: 350}, False, "setpoint back to 350")]),
    "V8 Pressure_Safe": (COIL, COIL_PRESSURE_SAFE, [
        ({R: 345}, True, "4 < ExhaustLimit 5"), ({R: 346}, False, "5, not below 5"),
        ({R: 0}, True, "0 bar"), ({R: -1000}, True, "RawMin, inclusive"),
        ({R: -1001}, False, "wire break: -14 is low but not trusted"), ({R: -2000}, False, "wire break")]),
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
    claim_plant("test_blowmoulder_values", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_blowmoulder_values")
    client = connect()
    results = []
    try:
        for register, value in DEFAULTS.items():  # start from the defaults the expected values assume
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
                print(f"{'PASS' if ok else 'FAIL'}  {name}: {what:<32} -> {got} (expected {expected}, {note})")
    finally:
        for register, value in DEFAULTS.items():
            write(client, register, value)
        write(client, HR_OVEN_TEMP, 0)
        write(client, HR_RAW, 0)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (setpoints back to the defaults, OvenTemp and raw back to 0)")
    print("Debugger (127.0.0.5): OvenTemp 1299 -> Oven_BelowMax TRUE, 1300 -> FALSE; "
          "BlowPressure_Raw 32511 -> Press_Range_OK TRUE, 32512 -> FALSE.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.5? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
