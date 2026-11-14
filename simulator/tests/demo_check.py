"""Pre-demo check: is everything up that the line needs? Read only, writes nothing.

    uv run python tests/demo_check.py

Each line is PASS / WARN / FAIL and says how to fix it. The checks follow the things
that broke during development; D2, D3, D6 and D7 run once per PLC (filler, syrup room):
    D1 containers openplc, plc-syrup-carb and fuxa are running
    D2 per runtime: a compiled PLC program exists AND its Modbus server is enabled. A container
       re-creation resets /workdir/plugins.conf (container layer, not a volume): the program
       still runs from the build volume, but modbus_slave is off until the next upload
    D3 per runtime: the PLC program answers on Modbus
    D4 FUXA is up and connected to each PLC (one line per PLC, connection status 5). One link at 0 =
       that PLC is down (D3); all at 0 can also be FUXA's missing Modbus plugin
    D5 FUXA has the current screens (live project = docs/hmi/fuxa-project.json)
    D6 per PLC: one simulator writes its sensors (plant lock held) - WARN if none
    D7 per PLC: ready, mode Auto, no active alarm
Exit code 1 if any FAIL.
"""

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from types import ModuleType

from pymodbus.client import ModbusTcpClient

from simulator import address_map as am
from simulator import address_map_syrup as sm
from simulator import config, plant_lock
from simulator.plcs import (ALARM_NAMES, MODE_NAMES, STATE_NAMES, STEP_NAMES, SYRUP_ALARM_NAMES,
                            SYRUP_STATE_NAMES, SYRUP_STEP_NAMES)

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "docs" / "hmi" / "fuxa-project.json"
FUXA = "http://localhost:1881"

def status_tag(label: str) -> str:
    """FUXA's connection status tag of one PLC device; the runtime labels are the FUXA machine keys
    (tests/fuxa_tags.py: Machine.status_tag_id = "t_<key>_connection_status")."""
    return f"t_{label}_connection_status"


class Runtime:
    """One OpenPLC runtime container and what D7 needs to read its status."""

    def __init__(self, service: str, label: str, editor_ip: str, project: str, port: int,
                 amap: ModuleType, steps: dict, states: dict, alarms: tuple, hmi: str):
        self.service, self.label, self.editor_ip = service, label, editor_ip
        self.project, self.port = project, port
        self.amap, self.steps, self.states, self.alarms = amap, steps, states, alarms
        self.hmi = hmi  # where the operator presses Auto / Reset / Start for this PLC


RUNTIMES = [
    Runtime("openplc", "filler", "localhost", "plcs/filler", config.PLC_PORT,
            am, STEP_NAMES, STATE_NAMES, ALARM_NAMES, "the FUXA header"),
    Runtime("plc-syrup-carb", "syrup", "127.0.0.2", "plcs/syrup", config.SYRUP_PORT,
            sm, SYRUP_STEP_NAMES, SYRUP_STATE_NAMES, SYRUP_ALARM_NAMES,
            "the FUXA header (row 2: Syrup room, e.g. on the Syrup room screen)"),
]
SERVICES = {"fuxa", *(rt.service for rt in RUNTIMES)}
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
    missing = SERVICES - running
    if missing:
        report("FAIL", "D1 containers", f"not running: {', '.join(sorted(missing))} – start: `docker compose up -d` "
               "(stop/start keep the PLC programs; `down` keeps them too since the build volumes)")
        return False
    report("PASS", "D1 containers", ", ".join(sorted(SERVICES)) + " running")
    return True


def d2_program(rt: Runtime) -> None:
    name = f"D2 {rt.label} program"
    upload = f"OpenPLC Editor: open `{rt.project}`, device `{rt.editor_ip}`, compile and upload"
    # one exec: the program file, then the enabled flag (3rd field) of the modbus_slave line in plugins.conf
    out = compose("exec", "-T", rt.service, "sh", "-c",
                  "ls /workdir/build/libplc_*.so; grep '^modbus_slave,' /workdir/plugins.conf | cut -d, -f3")
    if out is None:
        report("WARN", name, "skipped (no docker)")
        return
    lines = out.stdout.split()
    program = next((Path(l).name for l in lines if "libplc_" in l), None)
    if program is None:
        report("FAIL", name, f"no compiled program in the runtime – {upload} (the container was re-created)")
    elif lines[-1] != "1":
        report("FAIL", name, f"{program} present, but the Modbus server is OFF (plugins.conf reset by a container "
               f"re-creation; the program still runs) – {upload} once more")
    else:
        report("PASS", name, f"{program}, Modbus server enabled")


def d3_plc(rt: Runtime) -> ModbusTcpClient | None:
    name = f"D3 {rt.label} running"
    client = ModbusTcpClient(config.PLC_HOST, port=rt.port, timeout=2)
    try:
        if client.connect():
            client.read_holding_registers(rt.amap.STATE, count=1)  # docker-proxy accepts TCP without a Modbus server
            report("PASS", name, f"Modbus answers on {config.PLC_HOST}:{rt.port}")
            return client
    except Exception:  # noqa: BLE001 - any Modbus/socket error means "not answering"
        pass
    client.close()
    report("FAIL", name, f"no Modbus answer on port {rt.port} – if D2 says Modbus OFF or no program: upload; "
           f"SAFE MODE after a laptop suspend: upload too; else OpenPLC Editor ({rt.editor_ip}): Start PLC")
    return None


def d4_fuxa() -> bool:
    """One line per PLC device. True when FUXA answers (then D5 runs)."""
    ids = [status_tag(rt.label) for rt in RUNTIMES]
    try:
        values = {v["id"]: v.get("value") for v in get_json(f"/api/getTagValue?ids={urllib.parse.quote(json.dumps(ids))}")}
    except OSError:
        report("FAIL", "D4 FUXA", f"FUXA does not answer on {FUXA} – `docker compose up -d fuxa`")
        return False
    all_down = not any(values.get(i) == 5 for i in ids)
    for rt, tag in zip(RUNTIMES, ids):
        name, value = f"D4 FUXA -> {rt.label} PLC", values.get(tag)
        if value == 5:
            report("PASS", name, "connected")
            continue
        hint = (" If D3 passes: FUXA editor → Settings → Plugins → install modbus-serial (a missing plugin "
                "breaks every device)" if all_down else "")
        report("FAIL", name, f"not connected (status {value}): is the {rt.label} PLC answering (D3)? "
               f"FUXA reconnects by itself a few seconds after the PLC is back.{hint}")
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
    """One plant lock per PLC (plant_lock.py). The filler alone held = simulator started with --filler-only."""
    holders = {rt.label: plant_lock.is_held(config.PLC_HOST, rt.port) for rt in RUNTIMES}
    if all(holders.values()):
        report("PASS", "D6 simulator", "; ".join(f"{label}: {who}" for label, who in holders.items()))
    elif not any(holders.values()):
        report("WARN", "D6 simulator", "no simulator running – start it: `uv run simulator` (panel, both PLCs) or "
               "`uv run simulator --headless`; without it the heartbeat alarms latch")
    else:
        free = ", ".join(label for label, who in holders.items() if not who)
        report("WARN", "D6 simulator", f"nobody writes the {free} sensors (simulator started with --filler-only?) – "
               "restart `uv run simulator` without it; else that PLC's heartbeat alarm latches")


def d7_ready(rt: Runtime, client: ModbusTcpClient) -> None:
    m = rt.amap
    mode = client.read_holding_registers(m.MODE_REQ, count=1).registers[0]
    state, step, _unused, _count = client.read_holding_registers(m.STATUS_START, count=m.STATUS_COUNT).registers
    alarms = [n for n, on in zip(rt.alarms, client.read_coils(m.ALARMS_START, count=m.ALARMS_COUNT).bits) if on]
    where = (f"mode {MODE_NAMES.get(mode, mode)}, step {rt.steps.get(step, step)}, "
             f"state {rt.states.get(state, state)}")
    fixes = []
    if mode != 1:
        fixes.append(f"Auto via {rt.hmi}")
    if alarms:
        again = " (twice when Alm_CommLost came with FAULT)" if "Alm_CommLost" in alarms and step == 900 else ""
        fixes.append(f"alarms {', '.join(alarms)}: remove the cause, then Reset{again} via {rt.hmi}")
    if fixes:
        report("WARN", f"D7 {rt.label} ready", f"{where} – {'; '.join(fixes)}")
    else:
        report("PASS", f"D7 {rt.label} ready", f"{where}, no alarm – Start via {rt.hmi}")


def main() -> None:
    print("Demo check (read only)\n")
    if d1_containers():
        for rt in RUNTIMES:
            d2_program(rt)
    clients = [(rt, d3_plc(rt)) for rt in RUNTIMES]
    if d4_fuxa():
        d5_project()
    d6_simulator()
    for rt, client in clients:
        if client is not None:
            d7_ready(rt, client)
            client.close()
    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\n{len(results) - fails - warns} PASS, {warns} WARN, {fails} FAIL"
          + (" – ready for the demo" if not fails else " – fix the FAIL lines first"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
