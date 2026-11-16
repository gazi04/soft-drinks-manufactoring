"""Labeler ladder stage 0: the third runtime answers on its own Modbus port.

Writes one %MW sensor and two %MX coils on the labeler PLC (port 5023), reads them back,
and checks that the filler (502) and the syrup room (5022) did not receive them. Checks:
    L1 labeler PLC reachable on 5023, Step (HR 1075) readable
    L2 GlueTemp (%MW12, HR 1036) write sticks
    L3 E_Stop_OK (%MX1.1, coil 8201) write sticks
    L4 Roll_Low (%MX4.0, coil 8224) write sticks: the new sensor byte %MX4 is inside mx_bits = 64
    L5 the filler on 502 still holds its own values (separate memory)
    L6 the syrup room on 5022 still holds its own values
    L7 GlueTemp / E_Stop_OK / Roll_Low set back to 0 / FALSE / FALSE

    uv run python tests/test_labeler_stage0.py

L5 / L6 print SKIP (not counted) when that runtime is not up: the labeler does not need the
other PLCs, only this isolation check does. Then look at GlueTemp, E_Stop_OK and Roll_Low in
the Editor debugger (device 127.0.0.3) while the script pauses: that proves the located
variables of the program sit on these registers. Release all debugger forces first; do not
run the simulator at the same time.
"""

import sys

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
LABELER_PORT = 5023
OTHER_PLCS = {"L5": ("filler", 502), "L6": ("syrup room", 5022)}

HR_GLUETEMP = 1036  # %MW12
HR_STEP = 1075  # %MW51
COIL_ESTOP = 8201  # %MX1.1
COIL_ROLL_LOW = 8224  # %MX4.0
TEST_TEMP = 4242  # unusual value, so it cannot be in another PLC by chance


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


def read_coil(client: ModbusTcpClient, address: int) -> bool:
    result = client.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"Read coil {address} failed: {result}")
    return bool(result.bits[0])


def snapshot(client: ModbusTcpClient) -> tuple[int, bool, bool]:
    return read_hr(client, HR_GLUETEMP), read_coil(client, COIL_ESTOP), read_coil(client, COIL_ROLL_LOW)


def main() -> int:
    claim_plant("test_labeler_stage0", HOST, LABELER_PORT)  # writes sensors: never with the simulator
    labeler = connect(LABELER_PORT)
    others = {name: (label, port, try_connect(port)) for name, (label, port) in OTHER_PLCS.items()}
    results = []
    try:
        step = read_hr(labeler, HR_STEP)
        results.append(check("L1", True, f"labeler PLC on {LABELER_PORT} answers, Step = {step}"))

        before = {name: snapshot(client) for name, (_, _, client) in others.items() if client}

        labeler.write_register(HR_GLUETEMP, TEST_TEMP)
        labeler.write_coil(COIL_ESTOP, True)
        labeler.write_coil(COIL_ROLL_LOW, True)
        temp, estop, roll_low = snapshot(labeler)
        results.append(check("L2", temp == TEST_TEMP, f"GlueTemp HR {HR_GLUETEMP} = {temp} (wrote {TEST_TEMP})"))
        results.append(check("L3", estop, f"E_Stop_OK coil {COIL_ESTOP} = {estop} (wrote TRUE)"))
        results.append(check("L4", roll_low, f"Roll_Low coil {COIL_ROLL_LOW} = {roll_low} (wrote TRUE)"))

        for name, (label, port, client) in others.items():
            if client is None:
                print(f"SKIP  {name}: {label} on {port} not up (no runtime or no Modbus server)")
                continue
            after = snapshot(client)
            results.append(check(
                name,
                after == before[name] and after[0] != TEST_TEMP,
                f"{label} on {port}: HR {HR_GLUETEMP} = {after[0]}, coils {COIL_ESTOP} / {COIL_ROLL_LOW} = "
                f"{after[1]} / {after[2]} (unchanged)",
            ))

        if sys.stdin.isatty():
            input("Debugger (127.0.0.3): GlueTemp = 4242, E_Stop_OK = TRUE, Roll_Low = TRUE? "
                  "Press Enter to reset them ... ")

        labeler.write_register(HR_GLUETEMP, 0)
        labeler.write_coil(COIL_ESTOP, False)
        labeler.write_coil(COIL_ROLL_LOW, False)
        results.append(check("L7", snapshot(labeler) == (0, False, False),
                             "GlueTemp = 0, E_Stop_OK = FALSE, Roll_Low = FALSE again"))
    finally:
        labeler.close()
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
                         "127.0.0.3? Without libplc_*.so the runtime starts no Modbus server.") from None
