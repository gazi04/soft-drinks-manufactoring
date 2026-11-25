"""Blow moulder ladder stage 0: the fifth runtime answers on its own Modbus port, and %QW0 = HR 0.

Writes sensors on the blow moulder PLC (port 5025), reads them back, checks that the analog
output LampPower (%QW0) is served as holding register 0, and that the other four PLCs did not
receive the values. Checks:
    B1  blow moulder PLC reachable on 5025, Step (HR 1075) and MouldStep (HR 1076) readable
    B2  OvenTemp (%MW12, HR 1036) write sticks
    B3  BlowPressure_Raw (%MW13, HR 1037) holds a negative value (-2000): INT is signed
    B4  E_Stop_OK (%MX1.1, coil 8201) write sticks
    B5  C1_Full (%MX4.4, coil 8228) write sticks: the sensor byte %MX4 is inside mx_bits = 64
    B6  Alm_PressureSensor (%MX7.2, coil 8250) can be read and is FALSE: the %MX7 alarm byte is served
    B7  LampPower (%QW0) readable as holding register 0
    B8  (only with --qw0 and the stage 0 test rung E_Stop_OK -> MOVE 42 -> LampPower)
        E_Stop_OK FALSE, HR 0 written to 0 -> stays 0; E_Stop_OK TRUE -> HR 0 = 42.
        Passed 2026-11-25 (13/13): %QW0 = HR 0 in OpenPLC v4; the test rung was then deleted.
    B9-B12  filler 502, syrup room 5022, labeler 5023, packer 5024 still hold their own values
    B13 OvenTemp / BlowPressure_Raw / E_Stop_OK / C1_Full set back to 0 / 0 / FALSE / FALSE

    uv run python tests/test_blowmoulder_stage0.py
    uv run python tests/test_blowmoulder_stage0.py --qw0   # only with the test rung uploaded

B9-B12 print SKIP (not counted) when that runtime is not up. Then look at
OvenTemp, E_Stop_OK and LampPower in the Editor debugger (device 127.0.0.5) while the script
pauses. Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
BM_PORT = 5025
OTHER_PLCS = {"B9": ("filler", 502), "B10": ("syrup room", 5022), "B11": ("labeler", 5023), "B12": ("packer", 5024)}

HR_LAMPPOWER = 0  # %QW0 (to be confirmed by B7/B8)
HR_OVENTEMP = 1036  # %MW12
HR_RAW = 1037  # %MW13
HR_STEP = 1075  # %MW51
HR_MOULDSTEP = 1076  # %MW52
COIL_ESTOP = 8201  # %MX1.1
COIL_C1_FULL = 8228  # %MX4.4
COIL_PRESSURE_SENSOR = 8250  # %MX7.2
TEST_TEMP = 1111  # unusual value, below OvenMax 1300
TEST_RAW = -2000
MOVE_VALUE = 42  # the test rung's constant


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect(port: int) -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=port)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{port}")
    return client


def try_connect(port: int) -> ModbusTcpClient | None:
    """A client for another PLC, or None when its runtime / Modbus server is not up."""
    client = ModbusTcpClient(HOST, port=port, retries=0)
    try:
        if client.connect() and not client.read_holding_registers(HR_STEP, count=1).isError():
            return client
    except Exception:  # docker-proxy accepts, then the runtime resets: same as "not up"
        pass
    client.close()
    return None


def read_hr(client: ModbusTcpClient, address: int) -> int:
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Read HR {address} failed: {result}")
    return result.registers[0]


def signed(value: int) -> int:
    """A Modbus register is 16 bits unsigned; the PLC's INT is signed."""
    return value - 0x10000 if value >= 0x8000 else value


def read_coil(client: ModbusTcpClient, address: int) -> bool:
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Read coil {address} failed: {result}")
    return bool(result.bits[0])


def snapshot(client: ModbusTcpClient) -> tuple[int, int, bool, bool]:
    return (read_hr(client, HR_OVENTEMP), read_hr(client, HR_RAW),
            read_coil(client, COIL_ESTOP), read_coil(client, COIL_C1_FULL))


def main() -> int:
    claim_plant("test_blowmoulder_stage0", HOST, BM_PORT)  # writes sensors: never with the simulator
    bm = connect(BM_PORT)
    others = {name: (label, port, try_connect(port)) for name, (label, port) in OTHER_PLCS.items()}
    results = []
    try:
        step, mould_step = read_hr(bm, HR_STEP), read_hr(bm, HR_MOULDSTEP)
        results.append(check("B1", True, f"blow moulder PLC on {BM_PORT} answers, Step = {step}, MouldStep = {mould_step}"))

        before = {name: snapshot(client) for name, (_, _, client) in others.items() if client}

        bm.write_register(HR_OVENTEMP, TEST_TEMP)
        bm.write_register(HR_RAW, TEST_RAW & 0xFFFF)
        bm.write_coil(COIL_C1_FULL, True)
        temp, raw, _, c1_full = snapshot(bm)
        results.append(check("B2", temp == TEST_TEMP, f"OvenTemp HR {HR_OVENTEMP} = {temp} (wrote {TEST_TEMP})"))
        results.append(check("B3", signed(raw) == TEST_RAW,
                             f"BlowPressure_Raw HR {HR_RAW} = {signed(raw)} (register {raw}, wrote {TEST_RAW})"))

        qw0_rung = "--qw0" in sys.argv[1:]
        if qw0_rung:  # %QW0 check: first without EN (E_Stop_OK FALSE), then with it
            bm.write_coil(COIL_ESTOP, False)
            bm.write_register(HR_LAMPPOWER, 0)
            time.sleep(0.3)
            lamp_off = read_hr(bm, HR_LAMPPOWER)
        bm.write_coil(COIL_ESTOP, True)
        time.sleep(0.3)
        estop = read_coil(bm, COIL_ESTOP)
        lamp_on = read_hr(bm, HR_LAMPPOWER)

        results.append(check("B4", estop, f"E_Stop_OK coil {COIL_ESTOP} = {estop} (wrote TRUE)"))
        results.append(check("B5", c1_full, f"C1_Full coil {COIL_C1_FULL} = {c1_full} (wrote TRUE)"))
        sensor_alarm = read_coil(bm, COIL_PRESSURE_SENSOR)
        results.append(check("B6", not sensor_alarm,
                             f"Alm_PressureSensor coil {COIL_PRESSURE_SENSOR} readable, = {sensor_alarm} (expected FALSE)"))
        results.append(check("B7", True, f"LampPower %QW0 readable as HR {HR_LAMPPOWER} = {lamp_on}"))
        if qw0_rung:
            results.append(check("B8", lamp_off == 0 and lamp_on == MOVE_VALUE,
                                 f"test rung: HR {HR_LAMPPOWER} = {lamp_off} without EN, {lamp_on} with E_Stop_OK "
                                 f"(expected 0 / {MOVE_VALUE}: %QW0 = holding register 0)"))
        else:
            print("SKIP  B8: %QW0 = HR 0 proven with the test rung (2026-11-25); --qw0 repeats it")

        for name, (label, port, client) in others.items():
            if client is None:
                print(f"SKIP  {name}: {label} on {port} not up (no runtime or no Modbus server)")
                continue
            after = snapshot(client)
            results.append(check(
                name,
                after == before[name] and after[0] != TEST_TEMP,
                f"{label} on {port}: HR {HR_OVENTEMP} / {HR_RAW} = {after[0]} / {after[1]}, "
                f"coils {COIL_ESTOP} / {COIL_C1_FULL} = {after[2]} / {after[3]} (unchanged)",
            ))

        if sys.stdin.isatty():
            input("Debugger (127.0.0.5): OvenTemp = 1111, E_Stop_OK = TRUE? "
                  "Press Enter to reset them ... ")

        bm.write_register(HR_OVENTEMP, 0)
        bm.write_register(HR_RAW, 0)
        bm.write_coil(COIL_ESTOP, False)
        bm.write_coil(COIL_C1_FULL, False)
        results.append(check("B13", snapshot(bm) == (0, 0, False, False),
                             "OvenTemp = 0, BlowPressure_Raw = 0, E_Stop_OK = FALSE, C1_Full = FALSE again"))
    finally:
        bm.close()
        for _, _, client in others.values():
            if client:
                client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        # docker-proxy accepts the TCP connection even when the runtime's Modbus server is not up
        raise SystemExit(f"Modbus connection dropped ({error}). Is a program uploaded and RUNNING on "
                         "127.0.0.5? Without libplc_*.so the runtime starts no Modbus server.") from None
