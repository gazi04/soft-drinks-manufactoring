"""Syrup room ladder stage 2: math and compare (ratio, dose target, guard against division by zero).

Runs against the syrup PLC on port 5022. Automatic checks (Modbus):
    B1 WaterBatch 500, ratio 50 (5.0:1) -> SyrupTarget 100, DoseTarget 600
    B2 ratio 40 -> 125 / 625 (MUL before DIV: dividing first would give 120)
    B3 ratio 55 (default) -> 90 / 590 (5000 / 55 = 90.9, INT division truncates)
    B4 ratio 10 (lowest allowed) -> 500 / 1000
    B5 ratio 0 -> old values kept (guard: no division by zero), PLC still answers
    B6 ratio 5 -> old values kept (below the guard)
    B7 WaterBatch 800, ratio 55 -> 145 / 945

Debugger checks (no address; the script sets the sensors and waits for Enter, only in a terminal):
    D1 BufferLevel 1300 + MixLevel 600 = 1900 <= 1950 -> Buffer_Room TRUE
    D2 BufferLevel 1400 + MixLevel 600 = 2000 >  1950 -> Buffer_Room FALSE
    D3 ratio 50 (target 100), SyrupLevel 99  -> Ingredients_OK FALSE
    D4                         SyrupLevel 100 -> Ingredients_OK TRUE
    D5 Mix_PT = T#20s, Carb_PT = T#10s (defaults)

    uv run python tests/test_syrup_stage2.py

Setpoints and sensors are set back to the defaults / 0 at the end.
Release all debugger forces first; do not run the simulator at the same time (both write the sensors).
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5022

HR_SYRUP_LEVEL = 1036  # %MW12
HR_MIX_LEVEL = 1037  # %MW13
HR_BUFFER_LEVEL = 1040  # %MW16
HR_WATER_BATCH = 1054  # %MW30
HR_SYRUP_RATIO = 1055  # %MW31
HR_SYRUP_TARGET = 1064  # %MW40
HR_DOSE_TARGET = 1065  # %MW41

DEFAULT_WATER_BATCH = 500
DEFAULT_SYRUP_RATIO = 55
SETTLE = 0.2  # s: many scans of the 20 ms task


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


def read_hr(client: ModbusTcpClient, address: int) -> int:
    result = client.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"Read HR {address} failed: {result}")
    return result.registers[0]


def targets(client: ModbusTcpClient, water: int, ratio: int) -> tuple[int, int]:
    """Write WaterBatch and SyrupRatio, wait a few scans, read SyrupTarget and DoseTarget."""
    client.write_register(HR_WATER_BATCH, water)
    client.write_register(HR_SYRUP_RATIO, ratio)
    time.sleep(SETTLE)
    return read_hr(client, HR_SYRUP_TARGET), read_hr(client, HR_DOSE_TARGET)


def expect(client: ModbusTcpClient, name: str, water: int, ratio: int, syrup: int, dose: int, note: str = "") -> bool:
    got = targets(client, water, ratio)
    text = f"WaterBatch {water}, ratio {ratio} -> SyrupTarget {got[0]}, DoseTarget {got[1]} (expected {syrup} / {dose})"
    return check(name, got == (syrup, dose), text + (f" – {note}" if note else ""))


def debugger_step(client: ModbusTcpClient, name: str, writes: dict[int, int], question: str) -> None:
    for address, value in writes.items():
        client.write_register(address, value)
    time.sleep(SETTLE)
    input(f"      {name}: {question}  [Enter] ")


def main() -> int:
    claim_plant("test_syrup_stage2", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_syrup_stage2")
    client = connect()
    results = []
    try:
        results.append(expect(client, "B1", 500, 50, 100, 600))
        results.append(expect(client, "B2", 500, 40, 125, 625, "dividing first would give 120"))
        results.append(expect(client, "B3", 500, 55, 90, 590, "5000 / 55 = 90.9 truncated"))
        results.append(expect(client, "B4", 500, 10, 500, 1000, "lowest ratio the guard allows"))

        # B5/B6: start from a known result, then an invalid ratio must leave it unchanged
        targets(client, 500, 55)
        results.append(expect(client, "B5", 500, 0, 90, 590, "ratio 0: guard blocks DIV, old values kept"))
        results.append(check("B5b", read_hr(client, HR_SYRUP_RATIO) == 0, "PLC still answers after ratio 0 (no fault)"))
        results.append(expect(client, "B6", 500, 5, 90, 590, "ratio 5 < 10: old values kept"))
        results.append(expect(client, "B7", 800, 55, 145, 945, "8000 / 55 = 145.5 truncated"))

        if sys.stdin.isatty():
            print("\nDebugger checks (OpenPLC Editor, device 127.0.0.2, watch the variables named below):")
            targets(client, DEFAULT_WATER_BATCH, DEFAULT_SYRUP_RATIO)
            debugger_step(client, "D1", {HR_BUFFER_LEVEL: 1300, HR_MIX_LEVEL: 600},
                          "Buffer_Sum = 1900, Buffer_Room = TRUE?")
            debugger_step(client, "D2", {HR_BUFFER_LEVEL: 1400}, "Buffer_Sum = 2000, Buffer_Room = FALSE?")
            targets(client, 500, 50)
            debugger_step(client, "D3", {HR_SYRUP_LEVEL: 99}, "SyrupTarget = 100, Ingredients_OK = FALSE?")
            debugger_step(client, "D4", {HR_SYRUP_LEVEL: 100}, "Ingredients_OK = TRUE?")
            targets(client, DEFAULT_WATER_BATCH, DEFAULT_SYRUP_RATIO)
            debugger_step(client, "D5", {}, "Mix_PT = T#20s, Carb_PT = T#10s?")
        else:
            print("\n(no terminal: debugger checks D1-D5 skipped)")
    finally:
        client.write_register(HR_WATER_BATCH, DEFAULT_WATER_BATCH)
        client.write_register(HR_SYRUP_RATIO, DEFAULT_SYRUP_RATIO)
        for address in (HR_SYRUP_LEVEL, HR_MIX_LEVEL, HR_BUFFER_LEVEL):
            client.write_register(address, 0)
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} automatic checks passed (setpoints back to 500 / 55, levels 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.2? "
                         "`uv run python tests/demo_check.py` shows whether the Modbus server is on.") from None
