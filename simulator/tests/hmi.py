"""Minimal HMI from the command line (FUXA's header buttons do the same since FUXA stage 2).

Do not use it while an operator works in FUXA: both write Cmd / ModeReq (one writer per
address, roadmap issue I4). The test scripts still use it.

The simulator must never write commands, modes or setpoints (one-writer rule):
those belong to the HMI. For the closed-loop tests (stage 6 on) somebody still has
to press Reset / Start, so this script ACTS AS THE HMI. It writes only the HMI's
addresses (Cmd, ModeReq) and is not part of the simulator package.

    uv run python tests/hmi.py status
    uv run python tests/hmi.py auto            # ModeReq = 1 (also: manual, stopmode)
    uv run python tests/hmi.py reset           # Cmd = 3 (clears alarms whose cause is gone)
    uv run python tests/hmi.py start           # Cmd = 1
    uv run python tests/hmi.py stop            # Cmd = 2
    uv run python tests/hmi.py auto reset start   # several in a row
    uv run python tests/hmi.py --syrup auto reset start   # the same for the syrup room PLC (port 5022)
    uv run python tests/hmi.py --labeler auto reset start # the same for the labeler PLC (port 5023)
    uv run python tests/hmi.py --labeler resetcount       # Cmd = 4: GoodCount -> 0 (labeler only)

The PLC sets Cmd back to 0 after reading it, so each command acts once.
Reset of Alm_CommLost only works while the simulator's heartbeat is running.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import warn_if_fuxa

from simulator import address_map as am
from simulator import config
from simulator import address_map_labeler as lm
from simulator import address_map_syrup as sm
from simulator.plcs import ALARM_NAMES as ALARMS
from simulator.plcs import STATE_NAMES as STATES
from simulator.plcs import STEP_NAMES as STEPS
from simulator.plcs import LABELER_ALARM_NAMES, LABELER_STATE_NAMES, LABELER_STEP_NAMES
from simulator.plcs import SYRUP_ALARM_NAMES, SYRUP_STATE_NAMES, SYRUP_STEP_NAMES

COMMANDS = {"start": (am.CMD, 1), "stop": (am.CMD, 2), "reset": (am.CMD, 3)}
MODES = {"stopmode": (am.MODE_REQ, 0), "auto": (am.MODE_REQ, 1), "manual": (am.MODE_REQ, 2)}


def status(client: ModbusTcpClient) -> None:
    mode = client.read_holding_registers(am.MODE_REQ, count=1).registers[0]
    state, step, _unused, good = client.read_holding_registers(am.STATUS_START, count=am.STATUS_COUNT).registers
    alarms = client.read_coils(am.ALARMS_START, count=am.ALARMS_COUNT).bits[: am.ALARMS_COUNT]
    active = [name for name, on in zip(ALARMS, alarms) if on] or ["none"]
    print(f"Mode {mode}, Step {step} {STEPS.get(step, '?')}, State {STATES.get(state, state)}, "
          f"GoodCount {good}, alarms: {', '.join(active)}")


def syrup_status(client: ModbusTcpClient) -> None:
    mode = client.read_holding_registers(sm.MODE_REQ, count=1).registers[0]
    state, step, _unused, batches = client.read_holding_registers(sm.STATUS_START, count=sm.STATUS_COUNT).registers
    alarms = client.read_coils(sm.ALARMS_START, count=sm.ALARMS_COUNT).bits[: sm.ALARMS_COUNT]
    active = [name for name, on in zip(SYRUP_ALARM_NAMES, alarms) if on] or ["none"]
    print(f"Syrup room: Mode {mode}, Step {step} {SYRUP_STEP_NAMES.get(step, '?')}, "
          f"State {SYRUP_STATE_NAMES.get(state, state)}, BatchCount {batches}, alarms: {', '.join(active)}")


def labeler_status(client: ModbusTcpClient) -> None:
    mode = client.read_holding_registers(lm.MODE_REQ, count=1).registers[0]
    state, step, _unused, good = client.read_holding_registers(lm.STATUS_START, count=lm.STATUS_COUNT).registers
    alarms = client.read_coils(lm.ALARMS_START, count=lm.ALARMS_COUNT).bits[: lm.ALARMS_COUNT]
    active = [name for name, on in zip(LABELER_ALARM_NAMES, alarms) if on] or ["none"]
    print(f"Labeler: Mode {mode}, Step {step} {LABELER_STEP_NAMES.get(step, '?')}, "
          f"State {LABELER_STATE_NAMES.get(state, state)}, GoodCount {good}, alarms: {', '.join(active)}")


def main() -> None:
    args = sys.argv[1:]
    # Cmd / ModeReq are the same registers (HR 1024 / 1025) in every PLC; only the port differs
    syrup, labeler = "--syrup" in args, "--labeler" in args
    if syrup and labeler:
        raise SystemExit("one PLC at a time: --syrup or --labeler")
    words = [a for a in args if a not in ("--syrup", "--labeler")] or ["status"]
    if syrup:
        port, show = config.SYRUP_PORT, syrup_status
    elif labeler:
        port, show = config.LABELER_PORT, labeler_status
    else:
        port, show = config.PLC_PORT, status
    commands = COMMANDS | ({"resetcount": (lm.CMD, 4)} if labeler else {})  # Cmd = 4 exists only in the labeler
    unknown = [w for w in words if w not in commands | MODES | {"status": None}]
    if unknown:
        raise SystemExit(f"unknown: {' '.join(unknown)} (use {', '.join([*commands, *MODES, 'status'])})")
    client = ModbusTcpClient(config.PLC_HOST, port=port)
    if not client.connect():
        raise SystemExit(f"no connection to {config.PLC_HOST}:{port}")
    try:
        for word in words:
            if word == "status":
                show(client)
                continue
            warn_if_fuxa("hmi.py")  # this script is a second HMI next to FUXA (issue I4)
            address, value = (commands | MODES)[word]
            client.write_register(address, value)
            time.sleep(0.2)  # a few PLC scans
            print(f"{word}: wrote {value} to HR {address}")
        if words[-1] != "status":
            show(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
