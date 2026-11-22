"""Packer ladder stage 0: the fourth runtime answers on its own Modbus port.

Writes one %MW sensor and two %MX coils on the packer PLC (port 5024), reads them back,
and checks that the filler (502), the syrup room (5022) and the labeler (5023) did not
receive them. Checks:
    P1 packer PLC reachable on 5024, Step (HR 1075) and WrapStep (HR 1076) readable
    P2 OvenTemp (%MW12, HR 1036) write sticks
    P3 E_Stop_OK (%MX1.1, coil 8201) write sticks
    P4 Film_Low (%MX4.1, coil 8225) write sticks: the sensor byte %MX4 is inside mx_bits = 64
    P5 Alm_WrapTimeout (%MX7.1, coil 8249) can be read and is FALSE: the %MX7 alarm byte is served
    P6 the filler on 502 still holds its own values (separate memory)
    P7 the syrup room on 5022 still holds its own values
    P8 the labeler on 5023 still holds its own values
    P9 OvenTemp / E_Stop_OK / Film_Low set back to 0 / FALSE / FALSE

    uv run python tests/test_packer_stage0.py

P6-P8 print SKIP (not counted) when that runtime is not up: the packer does not need the
other PLCs, only this isolation check does. Then look at OvenTemp, E_Stop_OK and Film_Low in
the Editor debugger (device 127.0.0.4) while the script pauses: that proves the located
variables of the program sit on these registers. Release all debugger forces first; do not
run the simulator at the same time.
"""

import sys

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
PACKER_PORT = 5024
OTHER_PLCS = {"P6": ("filler", 502), "P7": ("syrup room", 5022), "P8": ("labeler", 5023)}

HR_OVENTEMP = 1036  # %MW12
HR_STEP = 1075  # %MW51
HR_WRAPSTEP = 1076  # %MW52
COIL_ESTOP = 8201  # %MX1.1
COIL_FILM_LOW = 8225  # %MX4.1
COIL_WRAP_TIMEOUT = 8249  # %MX7.1
TEST_TEMP = 4343  # unusual value, so it cannot be in another PLC by chance


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
    return read_hr(client, HR_OVENTEMP), read_coil(client, COIL_ESTOP), read_coil(client, COIL_FILM_LOW)


def main() -> int:
    claim_plant("test_packer_stage0", HOST, PACKER_PORT)  # writes sensors: never with the simulator
    packer = connect(PACKER_PORT)
    others = {name: (label, port, try_connect(port)) for name, (label, port) in OTHER_PLCS.items()}
    results = []
    try:
        step, wrap_step = read_hr(packer, HR_STEP), read_hr(packer, HR_WRAPSTEP)
        results.append(check("P1", True, f"packer PLC on {PACKER_PORT} answers, Step = {step}, WrapStep = {wrap_step}"))

        before = {name: snapshot(client) for name, (_, _, client) in others.items() if client}

        packer.write_register(HR_OVENTEMP, TEST_TEMP)
        packer.write_coil(COIL_ESTOP, True)
        packer.write_coil(COIL_FILM_LOW, True)
        temp, estop, film_low = snapshot(packer)
        results.append(check("P2", temp == TEST_TEMP, f"OvenTemp HR {HR_OVENTEMP} = {temp} (wrote {TEST_TEMP})"))
        results.append(check("P3", estop, f"E_Stop_OK coil {COIL_ESTOP} = {estop} (wrote TRUE)"))
        results.append(check("P4", film_low, f"Film_Low coil {COIL_FILM_LOW} = {film_low} (wrote TRUE)"))

        wrap_timeout = read_coil(packer, COIL_WRAP_TIMEOUT)
        results.append(check("P5", not wrap_timeout,
                             f"Alm_WrapTimeout coil {COIL_WRAP_TIMEOUT} readable, = {wrap_timeout} (expected FALSE)"))

        for name, (label, port, client) in others.items():
            if client is None:
                print(f"SKIP  {name}: {label} on {port} not up (no runtime or no Modbus server)")
                continue
            after = snapshot(client)
            results.append(check(
                name,
                after == before[name] and after[0] != TEST_TEMP,
                f"{label} on {port}: HR {HR_OVENTEMP} = {after[0]}, coils {COIL_ESTOP} / {COIL_FILM_LOW} = "
                f"{after[1]} / {after[2]} (unchanged)",
            ))

        if sys.stdin.isatty():
            input("Debugger (127.0.0.4): OvenTemp = 4343, E_Stop_OK = TRUE, Film_Low = TRUE? "
                  "Press Enter to reset them ... ")

        packer.write_register(HR_OVENTEMP, 0)
        packer.write_coil(COIL_ESTOP, False)
        packer.write_coil(COIL_FILM_LOW, False)
        results.append(check("P9", snapshot(packer) == (0, False, False),
                             "OvenTemp = 0, E_Stop_OK = FALSE, Film_Low = FALSE again"))
    finally:
        packer.close()
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
                         "127.0.0.4? Without libplc_*.so the runtime starts no Modbus server.") from None
