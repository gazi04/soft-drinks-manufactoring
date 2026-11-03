"""Minimal HMI from the command line, until the FUXA screens exist.

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

The PLC sets Cmd back to 0 after reading it, so each command acts once.
Reset of Alm_CommLost only works while the simulator's heartbeat is running.
"""

import sys
import time

from pymodbus.client import ModbusTcpClient

from simulator import address_map as am
from simulator import config
from simulator.plcs import ALARM_NAMES as ALARMS
from simulator.plcs import STATE_NAMES as STATES
from simulator.plcs import STEP_NAMES as STEPS

COMMANDS = {"start": (am.CMD, 1), "stop": (am.CMD, 2), "reset": (am.CMD, 3)}
MODES = {"stopmode": (am.MODE_REQ, 0), "auto": (am.MODE_REQ, 1), "manual": (am.MODE_REQ, 2)}


def status(client: ModbusTcpClient) -> None:
    mode = client.read_holding_registers(am.MODE_REQ, count=1).registers[0]
    state, step, _unused, good = client.read_holding_registers(am.STATUS_START, count=am.STATUS_COUNT).registers
    alarms = client.read_coils(am.ALARMS_START, count=am.ALARMS_COUNT).bits[: am.ALARMS_COUNT]
    active = [name for name, on in zip(ALARMS, alarms) if on] or ["none"]
    print(f"Mode {mode}, Step {step} {STEPS.get(step, '?')}, State {STATES.get(state, state)}, "
          f"GoodCount {good}, alarms: {', '.join(active)}")


def main() -> None:
    words = sys.argv[1:] or ["status"]
    unknown = [w for w in words if w not in COMMANDS | MODES | {"status": None}]
    if unknown:
        raise SystemExit(f"unknown: {' '.join(unknown)} (use {', '.join([*COMMANDS, *MODES, 'status'])})")
    client = ModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT)
    if not client.connect():
        raise SystemExit(f"no connection to {config.PLC_HOST}:{config.PLC_PORT}")
    try:
        for word in words:
            if word == "status":
                status(client)
                continue
            address, value = (COMMANDS | MODES)[word]
            client.write_register(address, value)
            time.sleep(0.2)  # a few PLC scans
            print(f"{word}: wrote {value} to HR {address}")
        if words[-1] != "status":
            status(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
