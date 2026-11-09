"""Syrup room ladder stage 0: the second runtime answers on its own Modbus port.

Writes one %MW sensor and one %MX coil on the syrup PLC (port 5022), reads them
back, and checks that the filler PLC (port 502) did not receive them. Checks:
    Z1 syrup PLC reachable on 5022, Step (HR 1075) readable
    Z2 MixLevel (%MW13, HR 1037) write sticks
    Z3 E_Stop_OK (%MX1.1, coil 8201) write sticks
    Z4 the filler on 502 still holds its own values (separate memory)
    Z5 MixLevel / E_Stop_OK set back to 0 / FALSE

    uv run python tests/test_syrup_stage0.py

Then look at MixLevel and E_Stop_OK in the Editor debugger (device 127.0.0.2) while the
script pauses: that proves the located variables of the program sit on these registers.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

HOST = "127.0.0.1"
SYRUP_PORT = 5022
FILLER_PORT = 502

HR_MIXLEVEL = 1037  # %MW13
HR_STEP = 1075  # %MW51
COIL_ESTOP = 8201  # %MX1.1
TEST_LEVEL = 4242  # unusual value, so it cannot be in the filler by chance


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect(port: int) -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=port)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{port}")
    return client


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


def main() -> int:
    claim_plant("test_syrup_stage0", HOST, SYRUP_PORT)  # writes a sensor: never with the simulator
    syrup = connect(SYRUP_PORT)
    filler = connect(FILLER_PORT)
    results = []
    try:
        step = read_hr(syrup, HR_STEP)
        results.append(check("Z1", True, f"syrup PLC on {SYRUP_PORT} answers, Step = {step}"))

        filler_level_before = read_hr(filler, HR_MIXLEVEL)
        filler_estop_before = read_coil(filler, COIL_ESTOP)

        syrup.write_register(HR_MIXLEVEL, TEST_LEVEL)
        syrup.write_coil(COIL_ESTOP, True)
        level = read_hr(syrup, HR_MIXLEVEL)
        estop = read_coil(syrup, COIL_ESTOP)
        results.append(check("Z2", level == TEST_LEVEL, f"MixLevel HR {HR_MIXLEVEL} = {level} (wrote {TEST_LEVEL})"))
        results.append(check("Z3", estop, f"E_Stop_OK coil {COIL_ESTOP} = {estop} (wrote TRUE)"))

        filler_level = read_hr(filler, HR_MIXLEVEL)
        filler_estop = read_coil(filler, COIL_ESTOP)
        results.append(check(
            "Z4",
            filler_level == filler_level_before != TEST_LEVEL and filler_estop == filler_estop_before,
            f"filler HR {HR_MIXLEVEL} = {filler_level}, coil {COIL_ESTOP} = {filler_estop} (unchanged)",
        ))

        if sys.stdin.isatty():
            input("Debugger (127.0.0.2): MixLevel = 4242, E_Stop_OK = TRUE? Press Enter to reset them ... ")

        syrup.write_register(HR_MIXLEVEL, 0)
        syrup.write_coil(COIL_ESTOP, False)
        results.append(check(
            "Z5",
            read_hr(syrup, HR_MIXLEVEL) == 0 and not read_coil(syrup, COIL_ESTOP),
            "MixLevel = 0, E_Stop_OK = FALSE again",
        ))
    finally:
        syrup.close()
        filler.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        # docker-proxy accepts the TCP connection even when the runtime's Modbus server is not up
        raise SystemExit(f"Modbus connection dropped ({error}). Is a program uploaded and RUNNING on "
                         "127.0.0.2? Without libplc_*.so the runtime starts no Modbus server.") from None
