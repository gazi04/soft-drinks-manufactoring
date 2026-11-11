# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

University PLC course project (master's, *PLC Programming*): a full soft drink production line (blow moulder → syrup/carbonator → filler/capper → labeler → 6-pack packer) controlled by OpenPLC, with a Python plant simulator and a FUXA web HMI/SCADA. Runs natively on Arch Linux with Docker.

Read before proposing structure or code:
- `docs/project-report.md` — the design: machines, handshakes, address map, simulator, HMI, modes/alarms, syllabus coverage, plan. **Source of truth for design decisions.**
- `docs/<machine>/progress.md` — per-machine checklist, decisions, open questions and log (`docs/filler/` complete, `docs/syrup/` in progress: paper done, ladder next). Check the current step there before starting work on a machine, and update it as steps complete.
- `docs/simulator/roadmap.md` — staged build plan for the Python simulator (stages 0–9, test per stage); progress is ticked in `docs/filler/progress.md` section 2.
- `docs/simulator/design-notes.md` — short answers to the simulator's non-obvious decisions (timing, windows, Modbus link); keep it updated when such a decision changes.
- `docs/hmi/roadmap.md` — staged build plan for the FUXA HMI (stages 0–7, test per stage, open issues I1–I11); progress is ticked there and in `docs/filler/progress.md` section 3.
- `docs/scope-options.md` — scope decision (2026-11-09): option B, filler + syrup room committed; labeler, packer, blow moulder are stretch goals (report section 11).
- `docs/project-context.md` — course requirements, syllabus topics the project must visibly demonstrate, tool-stack decisions and rejected alternatives.

The student must be able to explain every part of the code (plagiarism rules), so prefer explaining and showing steps over silently doing large changes.

## Commands

```bash
# Runtime stack (currently: one OpenPLC runtime + FUXA)
docker compose up -d          # filler: editor 127.0.0.1:8443, Modbus 502; syrup room: editor 127.0.0.2:8443, Modbus 5022; FUXA http://localhost:1881
docker compose logs -f openplc
# The PLC program (libplc_*.so) lives in the build volume (openplc-build / syrup-build) and starts by itself at boot: stop/start/restart
# keep everything. A container RE-CREATION (compose change, down/up, new image) keeps the program but resets /workdir/plugins.conf
# (container layer): the program runs, the Modbus server is OFF, port drops every connection -> upload once more from the Editor.
# A deliberate runtime update (new image digest) also needs `docker volume rm <project>_openplc-build` + upload.
(cd simulator && uv run python tests/demo_check.py)  # before every demo: containers, per PLC program present + Modbus server enabled + answering, FUXA linked + current screens, simulator, mode/alarms (read only, prints the fix)

# Simulator (uv project, Python 3.13, pymodbus)
cd simulator
uv sync
uv run simulator                          # Textual control panel against the PLC (q quits); same options as headless
uv run simulator --headless               # tick loop without panel (console + logs/run-*.log); --seconds, --rate, --add; Ctrl+C stops
uv run pytest                             # simulator unit tests in tests/unit (no PLC needed; link/loop tests use a local pymodbus server)
uv run python tests/test_signal_conditioning.py  # stage 3: Outfeed_Full filter + S6 edge (counted via GoodCount)
uv run python tests/test_sequencer.py         # stage 4 (add --done with done bits forced in the debugger)
uv run python tests/test_station_jobs.py      # stage 5a: done bits / job durations
uv run python tests/test_outputs.py           # stage 5b: outputs, interlocks, manual mode (36 checks)
uv run python tests/test_index_door.py        # stage 6: INDEX belt, door TP pulse, GoodCount (19 checks)
uv run python tests/test_stop_state.py        # stage 7: Stop at safe points, State word (29 checks)
uv run python tests/test_alarms.py            # stage 8: alarms, timeouts, FAULT (25 checks, ~4 min; --quick skips the 2 min alarm)
uv run python tests/test_estop_resume.py      # scenario: E-stop with a partly filled bottle, restart tops it up (16 checks)
uv run python tests/test_jog_watchdog.py      # jog watchdog: manual button held > 10 s ignored until released (HMI issue I3, 9 checks)
uv run python tests/test_syrup_stage0.py      # syrup ladder stage 0: MixLevel / E_Stop_OK on port 5022, filler unaffected (5 checks; needs the uploaded program)
uv run python tests/test_syrup_stage1.py      # syrup ladder stage 1: defaults, Cmd clear, heartbeat watchdog on port 5022 (7 checks, ~15 s)
uv run python tests/test_syrup_stage2.py      # syrup ladder stage 2: ratio math, guard against division by zero (8 checks) + debugger steps D1-D5 in a terminal
uv run python tests/test_syrup_hysteresis.py  # syrup ladder stage 3: Temp_OK / CO2_Demand / Product_Available set, hold and reset thresholds on port 5022 (21 checks)
uv run python tests/test_link.py              # simulator stage 5: plcs.py first contact, heartbeat only (5 checks; --restart adds the reconnect check)
uv run python tests/hmi.py auto reset start   # command-line HMI for the test scripts (Cmd / ModeReq only; FUXA's header does the same – never both at once); `status` prints step, state, alarms
uv run python tests/test_closed_loop.py       # simulator stage 6: 20 bottles through the REAL PLC (5 checks incl. animation words, ~2.5 min; arg = number of bottles)
uv run python tests/test_scenarios.py --quick # simulator stage 7: 9 fault scenarios through the REAL PLC (~12 min; names to pick some)
uv run simulator --headless --scenario index_jam  # one fault scenario by hand (--list-scenarios)

# FUXA project, run from the repo root (editor http://localhost:1881/editor): export after every change; import replaces the whole project
curl -s http://localhost:1881/api/project | python3 -m json.tool --sort-keys > docs/hmi/fuxa-project.json
curl -X POST -H 'Content-Type: application/json' --data @docs/hmi/fuxa-project.json http://localhost:1881/api/project
(cd simulator && uv run python tests/fuxa_tags.py)      # regenerate device + 44 tags (address_map.py; one `Machine` per PLC) + views Line (start)/Filler/Alarms/Trends/Tags with header + 4 alarm definitions + 3 trend charts (DAQ on 4 tags) + side menu (fuxa_views.py, fuxa_shapes.py) into the export (export first, import after)
(cd simulator && uv run python tests/test_hmi_tags.py)  # FUXA: tags = address_map (H1) + header/Filler/Line/Alarms/Trends (H5-H9) offline (--offline); FUXA value = PLC value, write path, connected, DAQ history (H2-H4, H10)
```

The test scripts are plain Modbus scripts run one by one against the filler PLC in the runtime (not collected by pytest; `test_outputs.py` also holds the shared helpers the later scripts import). Release all debugger forces first. No linter or formatter is configured yet.

## Architecture

Three separate programs form one closed loop over **Modbus TCP**:

- **OpenPLC Runtime v4** (Docker, `ghcr.io/autonomy-logic/openplc-runtime`) — the only component that makes control decisions. Modbus server. PLC programs are written in the OpenPLC Editor v4 (AppImage) and uploaded via port 8443 (Editor v4 hard-codes the port, so each runtime has its own loopback IP: filler `localhost`/127.0.0.1, syrup room 127.0.0.2 – report 6.1); projects live in `plcs/<machine>/` (filler: `plcs/filler/`). `plc/` on disk is the old throw-away `test-mapping` project (never committed).
- **Python simulator** (`simulator/`, how to run: `simulator/README.md`) — the "factory": reads PLC outputs, simulates physics, writes sensor values back. Complete for the filler (stages 0–9): pure physics (`plant.py` composes conveyors, `machines/filler.py`, `standins.py`; no Modbus/UI, returns sensors + events), `plcs.py` (the only Modbus file), `loop.py` (fixed-rate tick loop), `commands.py` (command queue, the only way to change the world at runtime, applied at tick start), `scenarios.py` (fixed command sequences with time or condition steps), Textual control panel `ui/` (`view.py` pure text builders, `app.py` the screen; controls only put commands on the queue; loop runs as a Textual worker), CLI `uv run simulator [--headless] [--scenario NAME]`. It also writes the HMI animation words %MW70–75 (C1/C2 slot bits, filler pocket state codes; layout in `docs/filler/io-list.md`) and reads `ModeReq` (read only) for the panel header. Never run the `tests/` scripts while the simulator runs (both write the sensors) – enforced by `plant_lock.py` (OS file lock per PLC host:port; a second simulator or sensor-writing test script stops with a message). Decisions that look odd (sub-steps, S7 on C2 slot 1, lazy Modbus client, read-back before write) are explained in `docs/simulator/design-notes.md` – keep them.
- **FUXA** (`frangoteam/fuxa:1.3.5`, pinned) — operator screens. `hmi/` is mounted as FUXA's `_appdata` (live SQLite DBs + settings, owned by root, git-ignored); the trend history (DAQ, `server/_db`) is on the volume `fuxa-daq`, the Modbus plugin on `fuxa-plugins`; the project is versioned only as `docs/hmi/fuxa-project.json` – export it after every editor change (command above). Generated by `simulator/tests/fuxa_tags.py` (devices/tags, `Machine` per PLC) + `fuxa_views.py` (views, alarms, trend charts, side menu) + `fuxa_shapes.py` (FUXA shape formats); build/test steps and FUXA 1.3.5 findings in `docs/hmi/roadmap.md`. FUXA is an HMI writer (Cmd, ModeReq, setpoints, Man_*): `tests/hmi_guard.py` reminds `hmi.py` and the test scripts not to run while an operator uses it.

Rules that span components:
- Simulator and FUXA **never talk to each other** — everything goes through PLC memory.
- External Modbus clients cannot write `%IX` inputs, so **sensor values go into `%MW` memory words** that the PLC reads as sensors. Verify OpenPLC v4's `%MW` → holding-register offset before fixing addresses (v3: `%MW0` = HR 1024).
- Use **IEC addresses** (`%IX`, `%QX`, `%MW`), not Delta's X/Y/M/D.
- **Ladder Diagram is the primary language**; Structured Text only where ladder is clearly unsuitable.

Planned target (per `docs/project-report.md`, not yet implemented):
- **Committed scope: filler + syrup room/carbonator** (option B, 2026-11-09). Labeler, packer, blow moulder are stretch goals; until then the simulator keeps stand-ins for them.
- **One OpenPLC project + runtime container per machine**, each with its own volumes, host Modbus port (5021–5025) and editor IP `127.0.0.n:8443`. Running today: `openplc` (filler, Modbus 502, editor 127.0.0.1) and `plc-syrup-carb` (Modbus 5022, editor 127.0.0.2, volumes `syrup-data`/`syrup-build`). Renaming the filler service to `plc-filler` (5023, 127.0.0.3) is a separate later step.
- Every PLC uses the **same standard register layout** (report 6.2: Booleans as `%MX` coils, numbers as `%MW`; `Alm_CommLost` = `%MX5.3` everywhere; commands, sensors, interface in/out, state, step, alarms, animation slots) so simulator and HMI code is shared.
- Machines never read each other's memory. Bottle flow: each conveyor's full sensor is wired to the **upstream** machine (which stops itself, state Blocked); the downstream machine drives the conveyor motor. Liquid flow: one active-high handshake, `Product_Available` (syrup room → filler). The simulator copies it between PLCs to emulate hardwiring, plus `E_Stop_OK` and a heartbeat bit. Signals from machines that don't exist yet are held at their healthy value by the simulator.
- Simulator: one process, one class per machine plus `Conveyor` objects, one tick loop (~100 ms).
- HMI: one overview screen with slot-based conveyor animation, plus a detail screen per machine.
