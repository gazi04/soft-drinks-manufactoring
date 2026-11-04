"""Pre-demo check: is everything up that the line needs? Read only, writes nothing.

    uv run python tests/demo_check.py

Each line is PASS / WARN / FAIL and says how to fix it. The checks follow the things
that broke during development:
    D1 containers openplc and fuxa are running
    D2 a compiled PLC program exists in the runtime (gone after a container re-creation
       before the openplc-build volume existed; then: upload it again)
    D3 the PLC program is running (Modbus answers)
    D4 FUXA is up and connected to the PLC (connection status 5; no Modbus plugin = 0)
    D5 FUXA has the current screens (live project = docs/hmi/fuxa-project.json)
    D6 one simulator is running (plant lock held) - WARN if none
    D7 the PLC is ready: mode Auto, no active alarm
Exit code 1 if any FAIL.
"""

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from pymodbus.client import ModbusTcpClient

from simulator import address_map as am
from simulator import config, plant_lock
from simulator.plcs import ALARM_NAMES, MODE_NAMES, STATE_NAMES, STEP_NAMES

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "docs" / "hmi" / "fuxa-project.json"
FUXA = "http://localhost:1881"
STATUS_TAG = "t_filler_connection_status"
COLOURS = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}
results: list[str] = []


def report(kind: str, name: str, text: str) -> None:
    results.append(kind)
    print(f"{COLOURS[kind]}{kind}\033[0m  {name}: {text}")


def compose(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_json(path: str):
    with urllib.request.urlopen(FUXA + path, timeout=3) as r:
        return json.load(r)


def d1_containers() -> bool:
    ps = compose("ps", "--format", "json")
    if ps is None or ps.returncode != 0:
        report("WARN", "D1 containers", "docker compose not available here – check by hand: `docker compose ps`")
        return True
    lines = [json.loads(l) for l in ps.stdout.splitlines() if l.strip()]
    running = {c["Service"] for c in lines if c.get("State") == "running"}
    missing = {"openplc", "fuxa"} - running
    if missing:
        report("FAIL", "D1 containers", f"not running: {', '.join(sorted(missing))} – start: `docker compose up -d` "
               "(stop/start keep the PLC program; `down` keeps it too since the openplc-build volume)")
        return False
    report("PASS", "D1 containers", "openplc and fuxa running")
    return True


def d2_program() -> None:
    ls = compose("exec", "-T", "openplc", "sh", "-c", "ls /workdir/build/libplc_*.so")
    if ls is None:
        report("WARN", "D2 PLC program", "skipped (no docker)")
    elif ls.returncode != 0 or "libplc_" not in ls.stdout:
        report("FAIL", "D2 PLC program", "no compiled program in the runtime – OpenPLC Editor: connect to `localhost`, "
               "compile and upload `plcs/filler` (the container was re-created)")
    else:
        report("PASS", "D2 PLC program", Path(ls.stdout.split()[0]).name)


def d3_plc() -> ModbusTcpClient | None:
    client = ModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT, timeout=2)
    try:
        if client.connect():
            client.read_holding_registers(am.STATE, count=1)  # the runtime accepts TCP even when stopped
            report("PASS", "D3 PLC running", f"Modbus answers on {config.PLC_HOST}:{config.PLC_PORT}")
            return client
    except Exception:  # noqa: BLE001 - any Modbus/socket error means "not answering"
        pass
    client.close()
    report("FAIL", "D3 PLC running", "the PLC program does not answer – OpenPLC Editor (localhost): Start PLC; "
           "if D2 failed: upload first")
    return None


def d4_fuxa() -> bool:
    try:
        value = get_json(f"/api/getTagValue?ids={urllib.parse.quote(json.dumps([STATUS_TAG]))}")[0].get("value")
    except OSError:
        report("FAIL", "D4 FUXA", f"FUXA does not answer on {FUXA} – `docker compose up -d fuxa`")
        return False
    if value == 5:
        report("PASS", "D4 FUXA", "running, connected to the PLC")
    else:
        report("FAIL", "D4 FUXA", f"not connected to the PLC (status {value}): is the PLC running (D3)? Else FUXA editor "
               "→ Settings → Plugins → install modbus-serial; then wait a few seconds")
    return True


def d5_project() -> None:
    try:
        live = json.dumps(get_json("/api/project"), indent=4, sort_keys=True) + "\n"
    except OSError:
        return
    if live == EXPORT.read_text():
        report("PASS", "D5 FUXA screens", "live project = docs/hmi/fuxa-project.json")
    else:
        report("WARN", "D5 FUXA screens", "live project differs from docs/hmi/fuxa-project.json – edited in the editor? "
               "Export it, or import the file: curl -X POST -H 'Content-Type: application/json' "
               "--data @docs/hmi/fuxa-project.json http://localhost:1881/api/project")


def d6_simulator() -> None:
    holder = plant_lock.is_held(config.PLC_HOST, config.PLC_PORT)
    if holder:
        report("PASS", "D6 simulator", f"one plant writer running ({holder})")
    else:
        report("WARN", "D6 simulator", "no simulator running – start it: `uv run simulator` (panel) or "
               "`uv run simulator --headless`; without it the heartbeat alarm latches")


def d7_ready(client: ModbusTcpClient) -> None:
    mode = client.read_holding_registers(am.MODE_REQ, count=1).registers[0]
    state, step, _unused, _good = client.read_holding_registers(am.STATUS_START, count=am.STATUS_COUNT).registers
    alarms = [n for n, on in zip(ALARM_NAMES, client.read_coils(am.ALARMS_START, count=am.ALARMS_COUNT).bits) if on]
    where = f"mode {MODE_NAMES.get(mode, mode)}, step {STEP_NAMES.get(step, step)}, state {STATE_NAMES.get(state, state)}"
    fixes = []
    if mode != 1:
        fixes.append("press Auto in the FUXA header")
    if alarms:
        fixes.append(f"alarms {', '.join(alarms)}: remove the cause, then Reset (Alarms page)")
    if fixes:
        report("WARN", "D7 PLC ready", f"{where} – {'; '.join(fixes)}")
    else:
        report("PASS", "D7 PLC ready", f"{where}, no alarm – press Start")


def main() -> None:
    print("Demo check (read only)\n")
    if d1_containers():
        d2_program()
    client = d3_plc()
    if d4_fuxa():
        d5_project()
    d6_simulator()
    if client is not None:
        d7_ready(client)
        client.close()
    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\n{len(results) - fails - warns} PASS, {warns} WARN, {fails} FAIL"
          + (" – ready for the demo" if not fails else " – fix the FAIL lines first"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
