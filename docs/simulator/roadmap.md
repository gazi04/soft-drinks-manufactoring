# Python simulator – Roadmap

How to build the Python simulator in small stages. Each stage builds on the one before. Design: `docs/project-report.md` section 7. Filler addresses: `docs/filler/io-list.md`. Filler behaviour: `docs/filler/state-diagram.md`. Checklist and decisions: `docs/filler/progress.md` (section 2).

The roadmap starts with the filler, the only machine that exists so far. The structure is made so that the other four machines can be added later with the same pattern (see the end of this file).

## Working rule for every stage

1. **Explain first:** what the stage models and why, before any code is written.
2. Write only the code of this stage.
3. **Unit tests** (`simulator/tests/unit/`, pytest) must pass. They are pure Python and need no PLC.
4. From stage 5 on, also run the **closed-loop test** against the real filler PLC:
   - Release all debugger forces first. A leftover force blocks Modbus writes.
   - Never run the PLC test scripts (`simulator/tests/test_*.py`) at the same time. They write the same sensors, which would mean two writers.
5. Tick the stage in `docs/filler/progress.md`, then commit.

Never start the next stage with a failing test.

## Program structure

```
simulator/src/simulator/
├── main.py            ← entry point: panel, or --headless
├── config.py          ← all parameters (times, rates, PLC address)
├── address_map.py     ← Modbus addresses from io-list.md; which ones the simulator may write
├── bottle.py          ← Bottle objects carrying their state
├── events.py          ← Event, EventLog (bottle events, PLC changes, audit warnings)
├── conveyor.py        ← Conveyor class: slots, accumulation, sensors
├── machines/
│   └── filler.py      ← index belt, door, stations, discharge
├── standins.py        ← bottle supply, labeler stand-in, wiring values, heartbeat
├── plant.py           ← composes C1 + filler + C2 + stand-ins, one step() for the whole world
├── commands.py        ← command queue: panel/scenario → plant
├── plcs.py            ← Modbus client(s), the only file that knows Modbus
├── loop.py            ← tick loop (report section 7.3)
└── ui/
    └── app.py         ← Textual control panel
```

**Dependency direction.** The arrows point to what a module may import:

```
ui/app.py ─┐
           ├─► loop.py ─► plant.py ─► machines/, conveyor.py, standins.py, bottle.py, events.py
main.py ───┘      │
                  └─► plcs.py ─► address_map.py
```

**Rule:** the pure part (plant and everything below it) never imports pymodbus or Textual. It can therefore be unit-tested in milliseconds without Docker.

## Technical choices

- **One asyncio event loop.** Modbus uses `AsyncModbusTcpClient` (pymodbus), and Textual runs on asyncio as well.
  - `--headless` runs the loop with `asyncio.run(...)`.
  - With the panel, the same loop runs as a Textual worker.
- **Physics is synchronous and pure:** `step(outputs, dt) -> (sensors, events)`.
- **Unit tests** live in `tests/unit/` and use pytest (a dev dependency). They stay apart from the PLC scripts in `tests/`, which pytest would otherwise collect.
- **Starting parameters** live in `config.py` and are tuned later:

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| Tick | 100 ms, measured `dt` | PLC scan 20 ms |
| C1 / C2 | 8 slots, 0.5 s per slot step | – |
| Index pitch time | 1.5 s | index jam alarm 5 s |
| S8 cam window | first 15 % of a pitch | overrun after S8 ≈ 0.1–0.2 s |
| Door pass time | 0.3 s | door TP 0.5 s → exactly one bottle |
| Fill flow | 100 ml/s (500 ml in 5 s) | fill timeout 10 s, setpoint 500 ml |
| S6 pulse | 0.3 s | at least one full tick |
| Heartbeat toggle | 1 s | watchdog 3 s |
| Bottle supply | 30 bottles/min | – |
| Labeler stand-in | takes 1 bottle every 2 s | – |
| PLC address | `127.0.0.1:502` (later `plc-filler:502`) | – |

---

## Stage 0 – Setup and address map

- `uv add textual`, `uv add --dev pytest`; empty package files; `config.py`.
- `address_map.py`, all filler addresses from `io-list.md`, grouped by who writes them:
  - **Read (PLC outputs):** coils 0–5 (`ConveyerMotor1`, `DoorMotor`, `RinseValve`, `FillValve`, `CapMotor`, `IndexMotor`).
  - **Written by the simulator:**
    - coils 8192–8198 (S2–S8)
    - coils 8200–8202 (`Product_Available`, `E_Stop_OK`, `Heartbeat`)
    - HR 1036 (`FillLevel`)
  - **Read for the PLC watcher:**
    - HR 1074–1077 (`State`, `Step`, `GoodCount`)
    - coils 8232–8235 (alarms)
    - HR 1054 (`FillSetpoint`, for the underfill audit)
  - **Never written by the simulator:** HR 1024/1025 (`Cmd`, `ModeReq`), coils 8208–8213 (manual buttons), HR 1054–1056 (setpoints).

**Test:** every write address is in the simulator-owned set. The bulk write 8192–8202 does not reach 8208 or HR 1024. Coil 8199 (`%MX0.7`, unused) is the only gap.

## Stage 1 – Bottle and events

- `Bottle` dataclass: `id`, `level_ml`, `rinsed`, `capped`. Ids count up from 1.
- `Event`: time, source (`bottle` / `plc` / `sim`), `bottle_id` (optional), text, severity (`info` / `warning`).
- `EventLog`: append, last N events, filter (one bottle, PLC only, warnings only), optional log file.

**Test:** ids increase; the filters return the right events; the log file gets one line per event.

## Stage 2 – Conveyor

- A list of slots (`None` or a `Bottle`); motor input; slot-step timer (0.5 s).
- One slot step moves every bottle one slot forward **only if the next slot is free**. This gives accumulation by itself.
- `put_at_entry(bottle)` returns False if the entry slot is taken. The caller logs the crash.
- `take_at_exit()` returns the bottle at the exit, if any.
- `sensor(slot)` works for any presence or full sensor (S2 = C1 head slot, S7 = C2 entry slot).
- `slot_bits()` gives one bit per slot, for the HMI animation in stage 9.

**Test:**
- Motor off → nothing moves.
- Exit blocked → bottles queue at the end.
- The full sensor flickers while bottles flow and stays ON when they stand still. This is the S7 behaviour the PLC's 2 s / 3 s filter was built for.
- Putting into an occupied entry is refused.

## Stage 3 – Filler physics

Four parts, each tested before the next. The filler class gets the PLC outputs and the two conveyors; it returns the filler's sensors and events.

**3a – Index belt** (`state-diagram.md`, layout):
- `belt_pos` is a continuous number that grows while `IndexMotor` is ON.
- Pockets: `[entry, rinse, fill, cap]`. Crossing a pitch boundary shifts every pocket one place forward.
- S8 cam: ON in the first 15 % of each pitch, so it stays ON when the belt stops just after the boundary.
- S3/S4/S5: bottle in the pocket **and** belt inside the window. They are OFF while bottles move between stations.

**Test:**
- `IndexMotor` ON for 1.5 s → one shift and exactly one S8 rising edge.
- An overrun of 0.15 s after the edge → S8 still ON.
- S3–S5 OFF mid-move.

**3b – Door** (`state-diagram.md` rule 6):
- S2 = a bottle in C1's head slot.
- While `DoorMotor` is ON, the head bottle passes into the empty entry pocket after 0.3 s.
- If a second bottle enters during the same opening → audit warning "two bottles in one pocket".

**Test:** a 0.5 s pulse admits exactly one bottle; the door held open 2 s → warning.

**3c – Stations:**
- Fill: while `FillValve` AND `Product_Available` AND a bottle at fill, `level_ml += flow × dt`.
- `FillLevel` = level of the bottle at S4, else 0.
- `FillValve` ON with no bottle → audit warning "spill".
- Rinse and cap: recorded on the bottle (`rinsed` / `capped`) once the output has been ON for a minimum time while the bottle stood there.

**Test:** valve ON for 5 s → 500 ml. A half-filled bottle keeps its level when it moves (the E-stop scenario).

**3d – Discharge:**
- At a shift, the cap pocket's bottle goes onto C2's entry. S6 is ON for 0.3 s.
- Audit warnings:
  - bottle left uncapped
  - bottle underfilled (< `FillSetpoint`, default 500 ml)
  - crash: C2 entry occupied

**Test:** a full sequence driven by hand-written outputs (no PLC) gives the bottle events in the right order: entered → rinse → fill → cap → discharged.

## Stage 4 – Stand-ins and plant

- `Supply`: puts bottles onto C1's entry. Rate in bottles/min, "add N bottles", pause. This replaces the blow moulder.
- `LabelerStandIn`: keeps C2's motor ON and takes a bottle from C2's exit every 2 s. "Stopped" lets C2 fill up.
- Wiring values `Product_Available` and `E_Stop_OK` (TRUE by default), and `Heartbeat` (toggles every 1 s).
- `Plant`: composes C1, filler, C2 and the stand-ins: `plant.step(plc_outputs, dt) -> (writes, events)`, where `writes` = every value the simulator writes to the PLC.

**Test:** a small **test controller** (test-only, in `test_plant.py`, a short copy of the WORK/WAIT_OUTFEED/INDEX rules; not part of the simulator) drives the plant for 20 bottles → 20 discharged, 0 warnings. This proves the physics before Modbus is involved. S7 watches C2 slot 1 (`config.S7_SLOT`), see the stage 4 log in `progress.md`.

## Stage 5 – Modbus I/O and first contact

`plcs.py`:
- connect and reconnect;
- one bulk read of coils 0–5;
- one bulk write of coils 8192–8202 plus HR 1036;
- a status read (State, Step, GoodCount, alarms, FillSetpoint).

The first run with the real PLC writes only the heartbeat.

**Test:**
- 30 s with the heartbeat running → no `Alm_CommLost`.
- Simulator stopped → alarm after 3 s.
- Runtime restarted → the client reconnects by itself.

## Stage 6 – Headless closed loop

- `loop.py`: the six steps from report section 7.3, with measured `dt`.
  - **Fixed rate:** sleep until the next 0.1 s deadline (not "work, then sleep 0.1 s", which drifts to 102–110 ms); log min / max `dt` at the end.
  - Late ticks are already handled by `Plant.step` (sub-steps ≤ 0.1 s, S6/S8 pulses kept for one write, "slow tick" SIM warning above 0.3 s; physics verified clean up to 0.4 s stalls).
  - Every status read: `plant.apply_setpoints(status.fill_setpoint, status.rinse_time_ms, status.cap_time_ms)`.
  - Every ~10 ticks: `plc.verify_writes(writes)` right after the write; a non-empty list → SIM warning "foreign writer / debugger force on …".
  - Link: `plc.ensure_connected()` every tick, log changes of `plc.link_ok` (not of the TCP state); `PlcReplyError` → log once, keep running.
- PLC watcher: compares the status with the previous tick and logs changes (`PLC: step 10 → 20`, state changes, alarm on/off).
- Output goes to the console and a log file. `uv run simulator --headless`.

**Test:** put the PLC in Auto and press Start with `tests/hmi.py` (acts as the HMI until FUXA exists: `uv run python tests/hmi.py auto reset start`). After 20 bottles:
- `GoodCount` +20;
- 0 audit warnings;
- every bottle 500 ml, rinsed and capped.

## Stage 7 – Commands and fault injection

`commands.py`: a queue of commands, applied at the start of a tick (report section 7.3):
- supply rate, add N bottles, pause supply
- fill flow
- `Product_Available` on/off
- E-stop
- belt jam
- labeler stopped
- heartbeat off

An optional `--scenario` CLI runs a fixed sequence of commands, for repeatable headless tests.

**Test:** the open items of `progress.md` section 4.

| Scenario | Expected (PLC) |
| --- | --- |
| Gaps in the supply | Empty pockets skipped, no false job |
| `Product_Available` off | Starved, valve closed, no alarm |
| Labeler stopped | C2 fills, Blocked; alarm after 2 min; continues when the labeler runs again |
| Belt jam | `Alm_IndexJam` after 5 s → FAULT; Reset + Start → resumes |
| Stop during WORK | Jobs finish, IDLE with bottles inside; Start continues |
| E-stop with a half-filled bottle | All off; after release + Start the bottle is topped up |
| Heartbeat off | `Alm_CommLost` after 3 s, outputs off |
| Normal run, all stations busy | Good bottles only |
| Manual mode (buttons forced in the debugger) | Jog moves the belt / opens valves; `Man_Fill` without a bottle at S4 → no spill; `Man_Index` blocked while a valve is open |

## Stage 8 – Textual control panel

`ui/app.py` (report section 7.5):
- **Header:** PLC connection, step, state, active alarms, GoodCount.
- **World view:** C1 slots, belt pockets with bottle id and ml, C2 slots, sensor lamps S2–S8.
- **Controls:** inputs and switches; each one only puts a command on the queue.
- **Event log:** `RichLog`, colours per severity, filter all / bottle / PLC / warnings.

The panel never writes `Cmd`, mode or setpoints. Those stay with FUXA.

**Test:** run the stage 7 scenarios from the panel; `--headless` still works unchanged.

## Stage 9 – HMI support and wrap-up

- Animation slot words for FUXA from `Conveyor.slot_bits()`, report section 6.2 (%MW70–79). Check first that these addresses are free in the filler project, and add them to `io-list.md`.
- Final parameter tuning.
- `simulator/README.md`: how to run it.
- Report section 7 updated with what was built.

---

## Later machines

Each new machine follows the same pattern:

1. A machine class in `machines/` + unit tests.
2. An entry in `Plant`; a stand-in is replaced by the real machine.
3. A wiring table entry (`wiring.py`, e.g. `Product_Available` from the syrup room's %MW60 to the filler).
4. One more client in `plcs.py`.
5. One tab in the panel.

The filler classes do not change.
