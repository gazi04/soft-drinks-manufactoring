# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

University PLC course project (master's, *PLC Programming*): a full soft drink production line (blow moulder → syrup/carbonator → filler/capper → labeler → 6-pack packer) controlled by OpenPLC, with a Python plant simulator and a FUXA web HMI/SCADA. Runs natively on Arch Linux with Docker.

Read before proposing structure or code:
- `docs/project-report.md` — the design: machines, handshakes, address map, simulator, HMI, modes/alarms, syllabus coverage, plan. **Source of truth for design decisions.**
- `docs/<machine>/progress.md` — per-machine checklist, decisions, open questions and log (currently only `docs/filler/`). Check the current step there before starting work on a machine, and update it as steps complete.
- `docs/simulator/roadmap.md` — staged build plan for the Python simulator (stages 0–9, test per stage); progress is ticked in `docs/filler/progress.md` section 2.
- `docs/simulator/design-notes.md` — short answers to the simulator's non-obvious decisions (timing, windows, Modbus link); keep it updated when such a decision changes.
- `docs/project-context.md` — course requirements, syllabus topics the project must visibly demonstrate, tool-stack decisions and rejected alternatives.

The student must be able to explain every part of the code (plagiarism rules), so prefer explaining and showing steps over silently doing large changes.

## Commands

```bash
# Runtime stack (currently: one OpenPLC runtime + FUXA)
docker compose up -d          # OpenPLC editor port 8443, Modbus TCP 502, FUXA http://localhost:1881
docker compose logs -f openplc

# Simulator (uv project, Python 3.13, pymodbus)
cd simulator
uv sync
uv run simulator                          # entry point simulator:main (currently a stub); --headless planned
uv run pytest                             # pure-Python simulator unit tests in tests/unit (no PLC needed)
uv run python tests/test_signal_conditioning.py  # stage 3: Outfeed_Full filter + S6 edge (counted via GoodCount)
uv run python tests/test_sequencer.py         # stage 4 (add --done with done bits forced in the debugger)
uv run python tests/test_station_jobs.py      # stage 5a: done bits / job durations
uv run python tests/test_outputs.py           # stage 5b: outputs, interlocks, manual mode (36 checks)
uv run python tests/test_index_door.py        # stage 6: INDEX belt, door TP pulse, GoodCount (19 checks)
uv run python tests/test_stop_state.py        # stage 7: Stop at safe points, State word (29 checks)
uv run python tests/test_alarms.py            # stage 8: alarms, timeouts, FAULT (25 checks, ~4 min; --quick skips the 2 min alarm)
uv run python tests/test_estop_resume.py      # scenario: E-stop with a partly filled bottle, restart tops it up (16 checks)
uv run python tests/test_link.py              # simulator stage 5: plcs.py first contact, heartbeat only (5 checks; --restart adds the reconnect check)
uv run python tests/hmi.py auto reset start   # acts as the HMI until FUXA exists (Cmd / ModeReq only); `status` prints step, state, alarms
```

The test scripts are plain Modbus scripts run one by one against the filler PLC in the runtime (not collected by pytest; `test_outputs.py` also holds the shared helpers the later scripts import). Release all debugger forces first. No linter or formatter is configured yet.

## Architecture

Three separate programs form one closed loop over **Modbus TCP**:

- **OpenPLC Runtime v4** (Docker, `ghcr.io/autonomy-logic/openplc-runtime`) — the only component that makes control decisions. Modbus server. PLC programs are written in the OpenPLC Editor v4 (AppImage) and uploaded via port 8443; projects live in `plcs/<machine>/` (filler: `plcs/filler/`). `plc/` on disk is the old throw-away `test-mapping` project (never committed).
- **Python simulator** (`simulator/`) — the "factory": reads PLC outputs, simulates physics, writes sensor values back. Optional Textual control panel (engineer's test bench: supply, faults, per-bottle event log); `--headless` without it. Physics classes are pure Python (no Modbus/UI) and return sensors + events; panel → simulator via a command queue. Never run the `tests/` scripts while the simulator runs (both write the sensors).
- **FUXA** (`hmi/`) — operator screens. `hmi/` is mounted as FUXA's `_appdata` (live SQLite DBs + settings); `docs/fuxa-project-v2.json` is a project export.

Rules that span components:
- Simulator and FUXA **never talk to each other** — everything goes through PLC memory.
- External Modbus clients cannot write `%IX` inputs, so **sensor values go into `%MW` memory words** that the PLC reads as sensors. Verify OpenPLC v4's `%MW` → holding-register offset before fixing addresses (v3: `%MW0` = HR 1024).
- Use **IEC addresses** (`%IX`, `%QX`, `%MW`), not Delta's X/Y/M/D.
- **Ladder Diagram is the primary language**; Structured Text only where ladder is clearly unsuitable.

Planned target (per `docs/project-report.md`, not yet implemented):
- **One OpenPLC project + runtime container per machine** (5 total), each with its own volume and host ports (Modbus 5021–5025, editor 8441–8445). The current `docker-compose.yml` still has a single `openplc` service.
- Every PLC uses the **same standard register layout** (commands, sensors, interface in/out, state, step, alarms, animation slots) so simulator and HMI code is shared.
- Machines never read each other's memory. Bottle flow: each conveyor's full sensor is wired to the **upstream** machine (which stops itself, state Blocked); the downstream machine drives the conveyor motor. Liquid flow: one active-high handshake, `Product_Available` (syrup room → filler). The simulator copies it between PLCs to emulate hardwiring, plus `E_Stop_OK` and a heartbeat bit. Signals from machines that don't exist yet are held at their healthy value by the simulator.
- Simulator: one process, one class per machine plus `Conveyor` objects, one tick loop (~100 ms).
- HMI: one overview screen with slot-based conveyor animation, plus a detail screen per machine.
