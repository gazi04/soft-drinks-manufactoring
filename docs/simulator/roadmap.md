# Python simulator – Roadmap

How to build the Python simulator in small stages. Each stage builds on the one before. Design: `docs/project-report.md` section 7. Filler addresses: `docs/filler/io-list.md`. Filler behaviour: `docs/filler/state-diagram.md`. Checklist and decisions: `docs/filler/progress.md` (section 2).

The roadmap starts with the filler. The structure is made so that the other machines can be added later with the same pattern (see the end of this file).

**Status (2026-11-13):** filler stages 0–9 done; syrup room stages 0–8 done (section "Syrup room simulator"). Labeler, packer and blow moulder stay stand-ins (stretch goals, `docs/scope-options.md`).

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

Status 2026-11-03: stages 0–9 built (✅): the simulator is complete for the filler.

```
simulator/src/simulator/
├── __init__.py        ✅ entry point main(): CLI, panel (default) or --headless
├── config.py          ✅ all parameters (times, rates, windows, PLC address)
├── address_map.py     ✅ Modbus addresses from io-list.md; which ones the simulator may write
├── bottle.py          ✅ Bottle objects carrying their state
├── events.py          ✅ Event, EventLog (bottle events, PLC changes, audit warnings; file + console echo)
├── conveyor.py        ✅ Conveyor class: slots, accumulation, queue restart, sensors
├── machines/
│   └── filler.py      ✅ index belt, door, stations, discharge, audit
├── standins.py        ✅ bottle supply, labeler stand-in, heartbeat
├── plant.py           ✅ composes C1 + filler + C2 + stand-ins; sub-steps, pulse keeping, setpoints
├── plcs.py            ✅ Modbus client, the only file that knows Modbus (link handling, read-back)
├── loop.py            ✅ tick loop (report section 7.3), PLC watcher
├── commands.py        ✅ command queue: panel/scenario → plant (stage 7)
├── scenarios.py       ✅ fixed command sequences, ScenarioSlot for the panel (stages 7–8)
└── ui/
    ├── view.py        ✅ panel text built from plant + PLC status (pure, testable)
    └── app.py         ✅ Textual control panel (stage 8)

simulator/tests/
├── unit/              ✅ pytest, no PLC (uv run pytest)
├── test_*.py          ✅ ladder stage scripts (one by one, against the PLC)
├── test_link.py       ✅ stage 5 first contact (+ --restart)
├── test_closed_loop.py ✅ stage 6: N bottles through the real PLC
├── test_scenarios.py  ✅ stage 7: 9 fault scenarios through the real PLC
└── hmi.py             ✅ acts as the HMI until FUXA exists (start/stop/reset/mode/status)
```

**Dependency direction.** The arrows point to what a module may import:

```
ui/app.py ──┐
            ├─► loop.py ─► plant.py ─► machines/, conveyor.py, standins.py, bottle.py, events.py
__init__.py ┘      │
                   └─► plcs.py ─► address_map.py, plant.SimWrites, machines.filler.FillerOutputs
```

**Rule:** the pure part (plant and everything below it) never imports pymodbus or Textual. It can therefore be unit-tested in milliseconds without Docker.

## Technical choices

- **One asyncio event loop.** Modbus uses `AsyncModbusTcpClient` (pymodbus), and Textual runs on asyncio as well.
  - `--headless` runs the loop with `asyncio.run(...)`.
  - With the panel, the same loop runs as a Textual worker.
- **Physics is synchronous and pure:** `step(outputs, dt) -> (sensors, events)`.
- **Unit tests** live in `tests/unit/` and use pytest (a dev dependency). They stay apart from the PLC scripts in `tests/`, which pytest would otherwise collect.
- **Parameters** live in `config.py` (values as built; why each one: `docs/simulator/design-notes.md`):

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| Tick | 100 ms, fixed-rate deadlines, measured `dt` (98–102 ms on the real link) | PLC scan 20 ms |
| Sub-step | late ticks split into steps ≤ 0.1 s | same time as the PLC's wall-clock timers |
| Slow tick warning | `dt` > 0.3 s (physics verified clean up to 0.4 s) | – |
| C1 / C2 | 8 slots, 0.5 s per slot step; a queue restarts from rest | – |
| S7 (C2 full) | C2 slot 1, not the entry slot | `Outfeed_Full` filter 2 s vs a 1.5 s WORK |
| Index pitch time | 1.5 s | index jam alarm 5 s |
| S8 cam window | first 15 % of a pitch (0.225 s); S6/S8 kept ON for one write | overrun after S8 ≈ 0.1–0.2 s |
| Station window (S3–S5) | first 40 % of a pitch (0.6 s) | late tick overrun |
| Door pass time | 0.3 s | door TP 0.5 s → exactly one bottle |
| Fill flow | 100 ml/s (500 ml in 5 s) | fill timeout 10 s, setpoint 500 ml |
| Rinse / cap minimum | 1.5 s / 1.0 s | RinseTime 2 s / CapTime 1.5 s |
| S6 pulse | 0.3 s | at least one full tick |
| Heartbeat toggle | 1 s | watchdog 3 s |
| Bottle supply | 30 bottles/min | – |
| Labeler stand-in | takes 1 bottle every 2 s | – |
| Modbus | timeout 1 s, no retries, reconnect every 2 s | – |
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
  - Every 10 ticks: `plc.verify_writes(last_writes)` **before** the next write (compares with what was written last tick, so the whole gap between ticks is checked; right after the write only ~2 ms would be); a non-empty list → SIM warning "written values changed by someone else …".
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

**As built (2026-11-03):**
- `commands.py`: `Command(name, value)`, `CommandQueue.put()` checks name and type at once (bool commands take only `bool`; number commands no `bool`, no negatives), `apply(plant)` runs at tick start and logs one SIM event per command. Nine commands: `supply_rate`, `add_bottles`, `supply_paused`, `fill_flow`, `product_available`, `e_stop`, `index_jam`, `labeler_running`, `heartbeat`. Each only sets an existing plant attribute; new physics: `Filler.index_jammed` (motor ON, belt stands, no S8 edge).
- `scenarios.py`: a scenario is a list of `Step(wait, command, note)`; `wait` is seconds after the previous step **or a named condition** (`plc_running`, `in_step`, `in_state`, `alarm`, `fill_level_at_least`). Every scenario starts with "until the PLC runs". Seven built-in scenarios; `uv run simulator --headless --scenario NAME`, `--list-scenarios`.
- `loop.run(..., commands=, scenario=)`; `LoopStats.status` keeps the last PLC status for scenario conditions and tests.
- **Test:** `tests/test_scenarios.py` (9 scenarios: the 7 above + `stop_during_work` and `manual`, both HMI-only). The normal run stays in `test_closed_loop.py`. Manual buttons are written by the test acting as the HMI, not forced in the debugger.
- **Found:** `index_jam` crashed a bottle on restart (second door pulse into the occupied, sensorless entry pocket). Fixed in the PLC with the `Entry_Load` tracking bit (%MX3.1); `index_jam` passes since.

## Stage 8 – Textual control panel

`ui/app.py` (report section 7.5):
- **Header:** PLC connection, step, state, active alarms, GoodCount.
- **World view:** C1 slots, belt pockets with bottle id and ml, C2 slots, sensor lamps S2–S8.
- **Controls:** inputs and switches; each one only puts a command on the queue.
- **Event log:** `RichLog`, colours per severity, filter all / bottle / PLC / warnings.

The panel never writes `Cmd`, mode or setpoints. Those stay with FUXA.

**Test:** run the stage 7 scenarios from the panel; `--headless` still works unchanged.

**As built (2026-11-03):**
- `ui/view.py`: pure functions (`world_text`, `header_text`, `scenario_text`, `event_style`, `matches`) returning rich `Text`; `ui/app.py`: `SimulatorApp` with only layout and event handlers.
- The tick loop runs as a Textual worker (same asyncio loop: controls call `queue.put()` directly). The screen is redrawn by a 0.25 s timer, not per tick.
- Six switches (pause supply, product available, E-stop pressed, index jam, labeler running, heartbeat), three value rows (supply rate, add bottles, fill flow), scenario select + Run (`ScenarioSlot`). Switches follow the plant (e.g. a scenario) without sending commands back.
- `uv run simulator` starts the panel; `q` / `ctrl+q` stop the loop cleanly and print the summary.
- No Start/Stop/Reset/mode controls (one-writer rule); no tabs yet (one machine).
- **Tests:** `tests/unit/test_view.py`, `tests/unit/test_panel.py` (Textual pilot at 140×45, dead PLC port). Against the PLC: `e_stop` scenario run from the panel. Tick `dt` 85–119 ms while the panel draws (headless 98–102 ms), no slow ticks.

## Stage 9 – HMI support and wrap-up

- Animation slot words for FUXA from `Conveyor.slot_bits()`, report section 6.2 (%MW70–79). Check first that these addresses are free in the filler project, and add them to `io-list.md`.
- Final parameter tuning.
- `simulator/README.md`: how to run it.
- Report section 7 updated with what was built.

**As built (2026-11-03):**
- Animation words %MW70–75 (layout in `docs/filler/io-list.md`): C1 and C2 slot bits from `Conveyor.slot_bits()`, one state code per filler pocket from `Filler.pocket_codes()` (0 empty, 1 bottle, 2 rinsed, 3 filled, 4 capped). Written with one extra `write_registers` per tick; the addresses are in `SIM_WRITE_REGISTERS`. Probe first: an unused %MW keeps a written value (no ladder change needed).
- The PLC mode (`ModeReq`, read only) is now part of `PlcStatus`: the watcher logs `mode Stop -> Auto`, the panel header shows it and warns "Start is ignored" outside Auto (the PLC comes back in Mode 0 after a restart).
- Tuning: checked, nothing changed (table in `design-notes.md`).
- **Tests:** unit tests 131 (`test_address_map`, `test_plant`, `test_plcs_mapping`, `test_loop`, `test_view`); `tests/test_closed_loop.py` C5: after the loop stops, PLC %MW70–75 = plant (5/5, `dt` 98–102 ms); `test_scenarios.py --quick` re-run.

---

## Later machines

Each new machine follows the same pattern:

1. A machine class in `machines/` + unit tests.
2. An entry in `Plant`; a stand-in is replaced by the real machine.
3. A wiring table entry (`wiring.py`, e.g. `Product_Available` from the syrup room's %MX6.0 to the filler's %MX1.0). *Built differently for the syrup room: the one wire is a single AND inside `Plant` (syrup stage 3), no table needed yet.*
4. One more client in `plcs.py`.
5. One tab in the panel.

The filler classes do not change.

---

## Syrup room simulator

Second machine, same pattern as above. The filler classes and `address_map.py` do not change. Addresses: `docs/syrup/io-list.md`; behaviour: `docs/syrup/state-diagram.md`; checklist: `docs/syrup/progress.md` section 2.

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| 0 | Address map | `address_map_syrup.py` (own module, converters imported from `address_map`) | unit: numbers = io-list (parsed from the doc), blocks contiguous, one-writer rule |
| 1 | Tank physics | `machines/syrup.py`: syrup, mixing and buffer volumes from pump flows, `MixHighHigh`, buffer drained by the filler | pump N s = right level, empty tank moves nothing |
| 2 | Temperature + CO2 | first-order cooling, room heat, pressure with dissolving CO2, vent when the tank is empty | cool time < `CoolTimeout`; pressure holds when saturated |
| 3 | Plant + wiring | `SyrupRoom` in `Plant`; `Product_Available` copied syrup `%MX6.0` -> filler `%MX1.0`; shared E-stop, own heartbeat | test controller runs one batch without a PLC |
| 4 | Second link | shared link base class, `SyrupPlc`, 6 alarms | heartbeat only, reconnect |
| 5 | Closed loop, two PLCs | `loop.py` with two links, plant lock per port | one real batch, `BatchCount` +1 |
| 6 | Commands + scenarios | refill syrup, chiller failed, CO2 stuck, pump failed, sensor stuck, buffer draw | scenarios of `docs/syrup/progress.md` section 4 |
| 7 | Panel | syrup rows, new switches | pilot test + one scenario |
| 8 | Wrap-up | design-notes, README, `demo_check.py`, report section 7 | – |

Decisions for stages 1–3 (proposed first, as built in brackets): buffer draw scaled ×50 per simulated bottle (built ×30, stage 3); passive vent to 0 bar when the mixing tank is empty (built: the transfer pump vents, stage 2); `--filler-only` keeps the old stand-in behaviour (built).

**Stage 0 as built (2026-11-12):** `address_map_syrup.py` defines outputs (coils 0–5), the 11-coil sensor block 8192–8202 (`MixHighHigh`, spares and the unused %MX1.0 written FALSE, `E_Stop_OK`, `Heartbeat`), the 5-register analog block HR 1036–1040, status HR 1074–1077, 6 alarms 8232–8237, `PRODUCT_AVAILABLE_OUT` 8240, conditioned bits, setpoints HR 1054–1063 and manual buttons, plus `SIM_WRITE_COILS` / `SIM_WRITE_REGISTERS`. `tests/unit/test_address_map_syrup.py` (9 tests) parses the tables of `io-list.md`, so a typo in code or doc fails. Differences to the filler map: 6 alarms, 5 analog sensors, `Product_Available` is an output here. Unit suite 161 passed.

**Stage 1 as built (2026-11-12):** `machines/syrup.py`: `SyrupOutputs` (6 coils), `SyrupSensors` (levels in L, `product_temp` / `co2_pressure` already present as constants, `mix_high_high`), `SyrupRoom` with public volumes `syrup_l` / `mix_l` / `buffer_l`, ledger counters (`water_in_l`, `delivered_l`, `drawn_l`, `spilled_l`), `step(out, dt, now)`, `sensors()`, `deliver_syrup()`, `draw_buffer()`. Audit warnings (one per period): mixing tank / buffer overflow, syrup pump or transfer pump dry, agitator with the propeller uncovered. Parameters in `config.py`:

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| Water / syrup / transfer flow | 20 / 5 / 25 L/s | doses 25 s, 18 s, 23.4 s vs `DoseTimeout` 60 s; overshoot <= flow x 0.1 s keeps the 5.5:1 ratio |
| Tanks syrup / mix / buffer | 1000 / 1000 / 2000 L | `MixMax` 950, `BufferMax` 1950 are the PLC interlocks |
| `MixHighHigh` switch | 980 L | above `MixMax` 950 |
| Start levels | syrup 600, buffer 500 L | buffer < `BufferLow` 800 (first batch at once), > `BufferMin` + 20 (`Product_Available` TRUE) |

Tests: `tests/unit/test_syrup.py` (20): flows, empty / full tanks, rounding, `MixHighHigh`, PLC timeout fit, dose precision with the real loop order, one scripted batch, and a 5 000-tick conservation test. Unit suite 182 passed.

**Stage 2 as built (2026-11-12):** `SyrupRoom` now also moves the mixing tank's temperature and CO2 pressure (same `step()` and `SyrupSensors`; new public state `temp_c`, `pressure_bar`, `dissolved_bar`, `coolant_c`).
- **Temperature:** first-order lag, solved exactly. Cooling valve pulls toward the chiller (0 degC, tau 20 s), the room always pulls toward 20 degC (tau 600 s). Water (15 degC) and syrup (20 degC) mix in by volume.
- **Pressure:** CO2 valve +0.5 bar/s. The liquid takes CO2 up (`dissolved_bar`, k 0.1 /s with the agitator, 0.01 /s without) and the headspace loses 0.286 x what the liquid gains, so the pressure falls fast while unsaturated and holds when saturated: exactly what the PLC's `Carb_Timer` measures. The transfer pump vents the headspace (tau 5 s). Relief valve at 8 bar.

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| Chiller / jacket tau | 0 degC / 20 s | COOL 15.8 -> 4.0 degC in about 29 s vs `CoolTimeout` 60 s |
| Room tau | 600 s | 0.5 degC take about 19 s: `TempHyst` 0.5 degC is a real band |
| CO2 fill, dissolve k, headspace gain | 0.5 bar/s, 0.1 /s, 0.286 | CARBONATE about 30 s with 3 valve openings vs `CarbTime` 10 s |
| Vent tau | 5 s while TRANSFER runs | tank below 0.1 bar when the next batch starts |

Tests: 20 more in `tests/unit/test_syrup.py` (40 in total, suite 202). A small port of the PLC's latches and `Carb_Timer` drives the physics in tests. Five deliberate breakages (no vent, agitator without effect, no dissolving, slow cooling, fast room heat) each make a test fail.

**Stage 3 as built (2026-11-12):** `Plant(with_syrup=True)` holds a `SyrupRoom`; `Plant()` is unchanged (stand-in `Product_Available` = TRUE), so the filler's code, tests and loop are untouched.
- `Plant.step(out, dt, syrup: SyrupSignals | None = None)`: `SyrupSignals` = the syrup PLC's outputs + its `Product_Available` (%MX6.0). `None` = the PLC does not answer: pumps off and no wire driven.
- **The wire:** the filler sees `plant.product_available` (the pipe, FALSE = cut by command) AND the syrup PLC's signal; the same value goes to `SimWrites.product_available` (filler %MX1.0). `plant.syrup_writes()` gives `SyrupWrites` (sensors, shared `e_stop_ok`, own `syrup_heartbeat`).
- **The buffer is drained by the filler:** `Filler.dispensed_ml` counts every ml through the fill valve (also a spill); after each sub-step the plant takes `ml / 1000 x buffer_draw_scale` litres from the buffer. Empty buffer: one SIM warning, level never negative, and the valve gives no more product (`Plant.product_flows` = pipe AND buffer not empty; the PLC signal only goes to the filler PLC – fixed 2026-11-13).

| Parameter | Value | Why |
| --- | --- | --- |
| `BUFFER_DRAW_SCALE` | 30 L per litre filled (500 ml bottle = 15 L) | whole-line test, 600 s: x50 starves the filler after 114 s, x40 leaves 24 L of margin, x30 leaves 113 L (buffer 233..1049 L) |

Tests: `tests/unit/test_plant_syrup.py` (13) and one in `test_filler.py` (suite 215). The whole-line test runs the filler's test controller and a syrup test controller (the PLC's steps 5-60 and the `Product_Available` hysteresis) for 10 minutes: 3 batches, 89 bottles, no warnings. Three deliberate breakages (wire ignores the syrup PLC, ignores the cut pipe, filler draws nothing) each make a test fail.

**Stage 4 as built (2026-11-12):** `plcs.py` now has a base class `PlcLink` (connect, reconnect, `link_ok`, guarded requests, one-writer sets as class attributes `write_coils` / `write_registers`) and two machines on it: `FillerPlc` (unchanged API, port 502) and `SyrupPlc` (port `config.SYRUP_PORT` 5022).
- `SyrupPlc.read_signals()` -> `SyrupSignals` (coils 0-5 and coil 8240, two requests), `write(SyrupWrites)` (sensor block 8192-8202 + analog HR 1036-1040), `write_heartbeat()`, `read_status()` -> `SyrupStatus` (state, step, batch count, 6 alarms, mode), `verify_writes()`.
- Pure mapping functions (`decode_syrup_outputs`, `encode_syrup_sensor_block`, `encode_analog_block`, `decode_syrup_status`, `foreign_syrup_writes`) and name tables for the log and the panel. Analog values travel as two's complement (a PLC INT is signed, a Modbus register is not).
- The two PLCs share the sensor-coil layout 8192-8202 on purpose, so their coil ownership sets are equal; the registers differ (the filler also owns the animation words).

Tests: `test_plcs_syrup_mapping.py` (11) and `test_plcs_syrup_link.py` (11, base behavior parametrised over both classes), suite 237. Live: `tests/test_link_syrup.py` (11 checks incl. `--restart`), part of `run_syrup.py`. Three deliberate breakages (analog order, no two's complement, alarm index shifted) each make a test fail.

**Stage 5 as built (2026-11-12):** `loop.run(..., syrup_plc=SyrupPlc)` drives two PLCs. The filler path is unchanged (same texts, same tests); per PLC there is a `LinkTrack` (last writes, problems already reported, link state) and its own `try`, so one PLC being down never stops the other.
- Tick: read filler outputs and `syrup_plc.read_signals()` (failed / down -> `None` = pumps off, no `Product_Available`) -> `plant.step(out, dt, signals)` -> write, read-back (every 10th tick) and status for each PLC.
- `PlcWatcher` is configurable (names, prefix, counter): the syrup room logs `syrup: step IDLE -> DOSE_WATER` and `syrup: batch complete (BatchCount n)`. `LoopStats` has `syrup_status` and the batch count; the summary shows `BatchCount +n`.
- CLI: `uv run simulator` drives both PLCs; `--filler-only` keeps the old behavior; `--syrup-host` / `--syrup-port`. Both ports get a plant lock. The panel passes the second link to the loop and shows a second header line (link, mode, step, state, `BatchCount`, alarms, start hint). `tests/hmi.py --syrup auto reset start` operates the syrup PLC.
- Found live and fixed: a PLC that accepts TCP but never answers (filler runtime with the Modbus plugin disabled) cost a 1 s timeout every 2 s and made the loop miss the syrup PLC's heartbeat. `PlcLink` now backs off (2, 4, 8, 16 s); the heartbeat toggles at most once per tick and keeps its phase (see design notes).

Tests: `test_loop_syrup.py` (16, two fake servers incl. a silent PLC), heartbeat tests (suite 256). Live: `tests/test_closed_loop_syrup.py` (in `run_syrup.py`, slow, needs the filler on 502). `--syrup-only` on the real syrup PLC: 5/5, BatchCount +1, dosing 500.5 / 590.0 L, step durations IDLE 3, DOSE_WATER 25, DOSE_SYRUP 18, MIX 20, COOL 29, CARBONATE 32, TRANSFER 23 s (151 s per batch), dt 98-102 ms. **Check B4 (filler fed by the real `Product_Available`) has not run yet:** the filler runtime is in the "Modbus plugin disabled" state and needs its program uploaded from the Editor.

**B4 live (2026-11-13):** `test_closed_loop_syrup.py` with both PLCs 6/6: the filler made 20 bottles from the real `Product_Available`, never without it, buffer minimum 232 L, ticks 98–102 ms. The first run failed B1 only: with the filler drawing, the buffer is below `BufferLow` after TRANSFER (764 L), so the PLC starts the next batch at once (TRANSFER -> DOSE_WATER, correct). The test now ends a batch at the first step after TRANSFER (WAIT_DEMAND or DOSE_WATER) and expects `BatchCount` +1.

**Stage 6 as built (2026-11-12):** faults and hand actions are attributes/methods of `SyrupRoom`, set by commands, used by scenarios.
- **Faults (world, never the PLC):** `water_pump_failed`, `syrup_pump_failed` (output ON, no flow), `co2_valve_stuck` (valve open whatever the PLC says; pressure up to the 8 bar relief), `mix_level_frozen` (the level reading stops; `MixHighHigh` keeps the true level), `coolant_c = COOLANT_FAILED_C` (20 degC: cooling does nothing).
- **Hand actions with ledger (`manual_l`):** `fill_buffer`, `drain_syrup`, `drain_mix_tank` (to waste), `vent_headspace`; `deliver_syrup` / `draw_buffer` as before.
- **12 commands** (`deliver_syrup`, `drain_syrup`, `chiller_failed`, `water_pump_failed`, `syrup_pump_failed`, `co2_valve_stuck`, `vent_tank`, `mix_level_stuck`, `drain_mix_tank`, `draw_buffer`, `fill_buffer`, `buffer_draw_scale`), flagged `syrup=True` in `_Spec`; on a plant without syrup room they are ignored with a SIM warning.
- **`Scenario.target`** ("filler" / "syrup"): conditions see the status of that PLC (`due(plant, status, syrup_status)`; the loop passes both). `--scenario` of a syrup scenario with `--filler-only` stops with a message; the panel's Run button refuses it.
- **7 scenarios:** `low_syrup`, `cooling_failure`, `over_pressure`, `pump_failure`, `level_sensor_stuck`, `buffer_full`, `buffer_starved` (the last needs the filler PLC running and is run by hand).

Tests: `test_syrup_faults.py` (12), commands, scenario steps and one loop test (suite 290). Mutation checks (frozen reading ignored, stuck valve ignored, wrong status routed) each fail a test. Live: `tests/test_syrup_scenarios.py` (6 scenarios on the real syrup PLC, in `run_syrup.py`): over_pressure (alarm at 6.05 bar), low_syrup (Starved, refill restarts alone), level_sensor_stuck (`Alm_Overflow` 49.0 s after DOSE_WATER, true level 980 L, no spill), buffer_full (WAIT_BUFFER, Blocked, continues alone), cooling_failure and pump_failure (alarm exactly 60.0 s after the step began, resume after Reset + Start). `buffer_starved` live (2026-11-13, both PLCs, headless): `Product_Available` dropped at 66 s, filler Starved, Running again at 130 s after the next TRANSFER, no empty-buffer warning. Panel switches: stage 7.

**Stage 7 as built (2026-11-13):** the panel has one tab per machine (report 7.5: "each machine gets its own tab").
- **Layout:** header (filler + syrup PLC lines) / `TabbedContent` `1 Filler`, `2 Syrup room` (keys `1` / `2`; the syrup tab only without `--filler-only`) / one shared scenario row (select shows `name [target]`, syrup scenarios hidden with `--filler-only`) / event log. *Changed 2026-11-23:* the select shows only the active tab's scenarios (`scenario_options(target, ...)`, refilled on `TabActivated`); the status shows `name (target)`.
- **Syrup world** (`view.syrup_world_text`, pure): tank levels with `level_bar`, product temperature, CO2 pressure + dissolved CO2, chiller temperature, the syrup PLC's six output lamps, `MixHighHigh`, the `Product_Available` signal and the wire to the filler, E-stop, heartbeat, ledger, draw scale. Numbers are the sensor values written to the PLC; red markers for every injected fault (`FAILED`, `SENSOR STUCK (true … L)`, `CHILLER FAILED`, `STUCK OPEN`, `FROZEN`) and a line when the syrup PLC does not answer.
- **Syrup controls:** 6 switches (chiller failed, water pump failed, syrup pump failed, CO2 valve stuck, level sensor stuck, syrup heartbeat), 5 value rows (deliver syrup, drain syrup, draw buffer, fill buffer, draw scale), 2 action buttons (vent tank, drain mix tank). Switches follow the plant like the filler's.
- **New commands** `syrup_heartbeat` (freezes only the syrup PLC's heartbeat) and `fill_buffer_to` (tops the buffer up to a level, for `buffer_full`): 14 syrup commands.
- `Plant.syrup_signals` (last `SyrupSignals`, None = no answer) replaces the private `_syrup_pa`: the wire to the filler is derived from it and the panel shows its output lamps.

Tests: `test_view.py` (+4: bar, sensor values and lamps, every fault marker, no answer), `test_panel_syrup.py` (4 pilot tests at 140×45, dead ports: key 2 + fault switch, value row + action button, switches follow the plant on the hidden tab, syrup scenario from the shared row), `test_panel.py` (+1 filler-only: no syrup tab, no syrup scenarios, key 2 only a notice), `test_commands.py`, `test_plant_syrup.py` (suite 302). Mutation checks (sync without the syrup switches, `syrup_signals` not stored) each fail a test. Layout at 140×45: both control columns 20/20 lines without scrolling, world column 86 characters, log 16 lines. **Live (2026-11-13), scenario `buffer_full` started from the panel** (Textual pilot against the real syrup PLC: key `2`, select, Run): DOSE_WATER → … → WAIT_BUFFER (State 3 Blocked) → TRANSFER → WAIT_DEMAND, `BatchCount` +1, no alarm; the syrup tab showed buffer 1785 L, ledger hand +1400 L / drawn 700 L. The first live run found a wrong scenario number: the review fix "+1500 L at MIX" put the buffer at 2000 L ≥ `BufferMax` → `Alm_Overflow`, FAULT. Now the new command `fill_buffer_to 1900` sets a LEVEL (not an amount); a unit test checks the numbers against `BufferMax`, `DoseTarget` and a filler drawing 3 L/s. `test_syrup_scenarios.py buffer_full` 2/2 again.


**Stage 8 as built (2026-11-13):** wrap-up, no new simulator code.
- `tests/demo_check.py`: the syrup runtime is required now (D1–D3 FAIL instead of WARN, `config.SYRUP_PORT` instead of a local constant); D6 checks the plant lock of each PLC (both held PASS, none WARN, only the filler's WARN "started with `--filler-only`?"); D7 runs per PLC with that PLC's address map and step / state / alarm names, the syrup room's hint points to `tests/hmi.py --syrup` (no FUXA screens yet) and "Reset twice" appears when `Alm_CommLost` comes with FAULT. D4 stays on the filler until FUXA has the syrup device.
- `design-notes.md`: new section "Pre-demo check with two PLCs"; the syrup physics, plant, link, loop, fault and panel notes were written with their stages.
- `README.md`: demo check text; the syrup panel and scenarios were written with stage 7.
- Report section 7 and 5.1: syrup status, structure tree (`address_map_syrup.py`, `plant_lock.py`, `machines/syrup.py`, `SyrupPlc`), two links in the loop, the syrup physics as built, panel tabs; the old "simulator holds `Product_Available` TRUE" now only for `--filler-only`.

Test: live `demo_check.py` against both runtimes, all three D6 cases (no simulator; `--filler-only`; both PLCs) seen in a run. Unit suite 304 passed.

---

## Labeler simulator

Third machine, same pattern as the syrup room. Filler and syrup room classes do not change. Addresses: `docs/labeler/io-list.md`; behaviour: `docs/labeler/state-diagram.md`; checklist: `docs/labeler/progress.md` section 2. Planned 2026-11-18; stages 0–6 done 2026-11-18 (stage 2 before stage 1; stage 1 built inside stage 3; stage 5 live with `--labeler-only`, the full three-PLC run waits for the filler / syrup room runtimes).

Before stage 0: close the labeler loose ends (`docs/labeler/progress.md`): filler and syrup room containers up again, programs uploaded, `demo_check.py` green.

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| 0 | Address map | `address_map_labeler.py` (converters from `address_map`): 9 outputs, sensors `%MX0` + `%MX4`, `GlueTemp`, status, 7 alarms, conditioned bits, setpoints, manual buttons, `SIM_WRITE_*` | unit: numbers = io-list (parsed from the doc), blocks contiguous, one-writer rule |
| 1 | Pocket mechanics | `machines/labeler.py` part 1: gate (`Bottle_At_Gate` = C2 exit slot), travel gate -> pocket, bell up/down, turntable with `Spin_Pulse` (one pulse per turn), discharge onto C3 | gate pulse = one bottle in the pocket; bell time < 2 s; N turns = N pulses, each at least one tick |
| 2 | Label + glue | label web: `Label_Mark` after one label length of feed, `Label_Present` when the label was fed with warm glue while spinning; roll counter -> `Roll_Low` / `Roll_Empty`; melt tank: first-order heater, room loss, glue level -> `Glue_Low` | heat-up 20 -> 170 °C well under `HeatTimeout` 120 s; label length fed < `LabelTimeout` 5 s; empty roll gives no mark |
| 3 | Plant + C3 | `Labeler` in `Plant`, replaces `LabelerStandIn`; C2 motor now from the labeler PLC; new conveyor C3 with `C3_Full`; `PackerStandIn` drives C3 and takes bottles; shared E-stop, own heartbeat; `--no-labeler` keeps the stand-in | test controller labels bottles without a PLC; C3 fills when the packer stops |
| 4 | Third link | `LabelerPlc` (shared `PlcLink` base) on port 5023, 7 alarms | heartbeat only, write + read back, reconnect |
| 5 | Closed loop, three PLCs | `loop.py` with `labeler_plc=`, plant lock on 5023 | real bottles filler -> labeler -> C3, `GoodCount` + N |
| 6 | Commands + scenarios | torn web, bell stuck, heater failed, heater stuck on (over-temperature), roll empty + roll change, glue low + refill, gate jam, packer stopped | each scenario gives the alarm / state of `docs/labeler/state-diagram.md` on the real PLC |
| 7 | Panel | tab 3 labeler: rows (step, glue temp, roll, C3), new switches | pilot test + one scenario |
| 8 | Wrap-up | animation words (`Anim_C3`, `Anim_Station`, if the HMI plan wants them), design-notes, README, `demo_check.py` (third PLC), report section 7, `CLAUDE.md` | live `demo_check.py` with three PLCs |

Parameters (spin speed, label length, heater power, bell / travel times) are proposed at each stage and must fit the PLC constants (`ClampTimeout` 2 s, `LabelTimeout` 5 s, `OutTimeout` 3 s, `HeatTimeout` 120 s, `GlueMax` 200 °C), as the syrup room's parameter tables did.

**Stage 0 as built (2026-11-18):** `address_map_labeler.py`: outputs coils 0–8, sensor block 8192–8202, extra sensor block 8224–8225 (`Roll_Low`, `Glue_Low`; a second bulk write), `GlueTemp` HR 1036, status HR 1074–1077, 7 alarms 8232–8238, 6 conditioned bits 8216–8221, setpoints HR 1054–1057, manual buttons, `SIM_WRITE_*`. `tests/unit/test_address_map_labeler.py` (11) checks the code against `io-list.md` and the located variables of `plcs/labeler/pous/programs/main.ld`, both directions.

**Stage 2 as built (2026-11-18, before stage 1: independent of the pocket):** `machines/labeler.py`: `LabelerOutputs` (9 coils), `MeltTank` (exact first-order heater + room loss, `take_glue`, `add_glue` mixes cold blocks by mass, `glue_temp` / `glue_low` / `sticky`), `LabelWeb` (feed while the roll has labels, a timed `Label_Mark` pulse every label length, roll counter, `load_roll`, label glued onto `Bottle.label_mm` only with spin + roller + sticky glue, else `lost_mm`). New field `Bottle.label_mm` (> 0 = `Label_Present`). Audit warnings: label lost (with the reason), glue roller dry, knife did not cut, glue smokes. Parameters in `config.py`:

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| Heater 300 °C, tau 60 s; room tau 600 s | 20 → 160 °C 43.6 s, → 170 °C 48.5 s | `HeatTimeout` 120 s |
| Hysteresis cycle | off 20 s, on 2.5 s, overshoot 0.2 °C per tick | `GlueHyst` 5 °C inside `GlueBand` ±10 °C |
| Heater stuck on | 170 → 200 °C in 18.4 s | `GlueMax` 200 °C reachable (stage 6) |
| 1 kg cold glue into 1 kg at 170 °C | 95 °C, back to 160 °C in 24.5 s | rule 4: back to WARM_UP |
| Label 210 mm, web 200 mm/s | 1.05 s per label (about 1 turn/s of a 65 mm bottle, stage 1) | `LabelTimeout` 5 s |
| `Label_Mark` pulse | 0.3 s | at least one tick; gone before the next WRAP |
| Glue sticks from | 150 °C | below the window's 160 °C |
| Roll 1000 labels, low at 50; tank 5 kg, start 4 kg, low 1 kg; 1 g per label | – | warnings only |

Tests: `tests/unit/test_labeler.py` (20): heat-up, hysteresis with the PLC rule in loop order stays in the window, cooling, exact = short steps, stuck heater + smoke warning, cold glue refill, capacity, glue low / sticky; one label = one mark < 5 s, 20 labels without drift, glue per label, empty roll gives no mark and works after `load_roll`, roll low, label lost (cold glue, no spin, roller off, no bottle), roller dry, knife missed, output order = address map. Unit suite 341 passed.

**Stage 1 as built (2026-11-18, inside stage 3, which needs it):** `Labeler(c2, c3)` in `machines/labeler.py` owns the web and the tank and moves the bottle: gate (the filler door's rule: `GATE_PASS_S` in the open gate, then `GATE_TRAVEL_S` to the pocket; a second bottle onto a full pocket falls off), bell (position 0..1, `Bell_Down` = fully down), turntable (one timed `Spin_Pulse` per whole turn), pusher (onto C3's entry, waits while it is taken, blocked only while the bell is fully down). Audit: two bottles in the pocket, spin without clamp, pusher against the bell, bottle out without / with a broken label. `LabelerSensors` in io-list order.

| Parameter | Value | Must fit with (PLC) |
| --- | --- | --- |
| `GATE_PASS_S` / `GATE_TRAVEL_S` | 0.3 s / 0.5 s | gate TP 0.5 s admits one bottle (the next needs 0.5 + 0.3 s); `ClampTimeout` 2 s |
| `BELL_MOVE_S` | 0.5 s | `ClampTimeout` 2 s |
| `SPIN_TURN_S` / `SPIN_PULSE_S` | 1.0 s / 0.3 s | PRESS 2 turns ≤ 3 s < `LabelTimeout` 5 s; pulse ≥ one tick |
| `DISCHARGE_S` | 0.5 s | `OutTimeout` 3 s |

**Stage 3 as built (2026-11-18):** `Plant(with_labeler=True)`: C3 (`Conveyor("C3")`, `C3_Full` on slot `C3_FULL_SLOT` 1 as S7), `Labeler`, `PackerStandIn` (subclass of `LabelerStandIn`, takes a bottle every `PACKER_TAKE_S` 2 s), own `labeler_heartbeat`, shared `e_stop_ok`. `step(..., labeler=LabelerOutputs | None)`: the labeler PLC's `ConveyorMotor2` drives C2; None (PLC not answering) = all OFF, C2 stands. `labeler_writes()` → `LabelerWrites` with `Label_Mark` / `Spin_Pulse` ORed over the sub-steps (as S6 / S8). The stand-in moved from `plant.labeler` to `plant.labeler_standin` (commands, panel, CLI summary, tests), so `plant.labeler` is the real machine like `plant.filler` / `plant.syrup`; `labeler_running` is ignored with a warning when the real labeler runs. `Plant()` is unchanged by default and the CLI keeps the stand-in until the labeler PLC link exists (`--no-labeler` comes with the closed loop, stage 5).
Measured with the test controllers: labeler 6.0 s per bottle (waits 1.8 s of it for the next bottle), filler 6.3 s: the labeler keeps up, S7 only blinks 0.5 s per passing bottle.
Tests: `tests/unit/test_labeler.py` +12 (stage 1: one bottle per gate pulse, travel, two bottles, bell time, N turns = N pulses, spin unclamped, discharge time, C3 entry taken, pusher against the bell, audit, sensor order); `tests/unit/test_plant_labeler.py` (7) with `LabelerTestController` (port of steps 5–40, TP pulses, CTU, filters): default plant unchanged, wiring + heartbeat, 15 bottles all labelled and taken, labeler keeps up with the filler, packer stopped fills C3 → WAIT_OUTFEED → C2 full → filler Outfeed_Full → continues, silent labeler PLC stops C2, 0.35 s ticks keep the pulse edges. Unit suite 360 passed.

**Stage 4 as built (2026-11-18):** `plcs.LabelerPlc` (shared `PlcLink` base, default port `config.LABELER_PORT` 5023, ownership from `address_map_labeler`): `read_outputs()` (coils 0–8), `write()` = three requests (sensor block 8192–8202, extra block 8224–8225, `GlueTemp` HR 1036), `write_heartbeat()`, `read_status()` (State, Step, GoodCount, 7 alarms, ModeReq read only), `verify_writes()` (reads back both blocks and `GlueTemp`). Pure mapping next to the syrup room's: `LABELER_COIL_FIELDS` / `LABELER_EXTRA_FIELDS` by address (unused `%MX1.0` written FALSE), `LabelerStatus`, `LABELER_STEP_NAMES`, `LABELER_ALARM_NAMES`.
Tests: `tests/unit/test_plcs_labeler_mapping.py` (17: decode / encode by address, padding ignored, status, foreign writes, the client refuses HMI / PLC addresses incl. one coil past each block) and `tests/unit/test_plcs_labeler_link.py` (7, local pymodbus server: no connection, error reply keeps the link, drop + reconnect, ports, every value on its address with `%MX2`/`%MX3` untouched, decode, second writer on each block). Live `tests/test_link_labeler.py` (in `run_labeler.py`) on the real labeler PLC: L1–L7 9/9 (heartbeat watchdog 3.04 s; the PLC's `Heat_Demand` / `Glue_Ready` / `GlueHeater` follow `GlueTemp` 1500 / 1700; `Outfeed_Full` follows `C3_Full` through its 2 s / 3 s filter); L8 `--restart` not run yet (needs a manual `docker compose restart plc-labeler`). Unit suite 384 passed.

**Stage 5 as built (2026-11-18):** `loop.run(..., labeler_plc=)`: third `LinkTrack` ("labeler PLC") and `PlcWatcher` ("labeler: "), plant lock on 5023, step 2c reads its outputs (None while down: C2 stands, heater off; the problem line names `--no-labeler`), 4c/5c writes `plant.labeler_writes()`, verifies every second, reads the status; `LoopStats.labeler_status` and `labeler GoodCount +N` in the summary. `ValueError` both ways: a labeler link without `plant.labeler`, and a plant with the labeler but no link (nobody would drive C2). CLI: default = three PLCs, `--no-labeler` keeps the stand-in, `--filler-only` = the filler alone (stand-ins for both), `--labeler-host` / `--labeler-port`; the summary counts labelled / packed bottles; a scenario that sends `labeler_running` (`labeler_stopped`) needs `--no-labeler` (`scenarios.uses_command`). Panel: third header line (link, mode, step, state, GoodCount, alarms, start hint); the labeler tab is stage 7. `tests/hmi.py --labeler` (+ `resetcount` = Cmd 4).
Found live: the first runs ended with `Alm_PocketJam` a few seconds after the script. The script pressed Stop BETWEEN two loop runs: the PLC, still in WAIT_BOTTLE, fired its gate pulse into a frozen plant, the bottle never moved and `Bottle_Entering` stayed set (the PLC was right). Stop is now pressed while the settle loop runs, and the loop runs on until no bottle is between gate and pocket.
Tests: `tests/unit/test_loop_labeler.py` (10, fake PLCs: three links, the labeler PLC's C2 motor + `Bottle_At_Gate`, dead labeler PLC, watcher + summary, lock + both `ValueError`s, CLI modes, scenario guard, summary, header, panel). Live `tests/test_closed_loop_labeler.py --labeler-only` (filler runtime down, the test puts a bottle on C2 every 7 s): K1, K2, K4, K5 4/4 – 8 bottles, PLC GoodCount +8 = discharged = labelled, warm-up 20 → 160 °C about 45 s, about 5 s per bottle, dt 98–102 ms. Default mode (real filler + labeler) 5/5 incl. K3: 8 labelled, filler GoodCount +10, dt 97–103 ms; `--with-syrup` (three PLCs) 5/5, dt 98–102 ms, BatchCount +0 (the 86 s run ends before a batch finishes; the buffer kept `Product_Available` TRUE) (2026-11-18). Unit suite 394 passed (`test_loop_syrup.py::test_product_available_is_copied…` failed once under load and passed on every re-run: an old timing-sensitive test, not changed here).

**Stage 6 as built (2026-11-18):** fault flags in the physics (the PLC is never told): `LabelWeb.torn`, `Labeler.bell_stuck`, `Labeler.gate_jammed` + hand action `clear_gate()` (`removed` list), `MeltTank.heater_failed` / `heater_stuck_on` (effective heater = (output OR stuck) AND NOT failed). 11 commands with the new `_Spec(labeler=True)` flag (`LABELER_COMMANDS`, ignored with a warning without the real labeler): `web_torn`, `bell_stuck`, `heater_failed`, `heater_stuck_on`, `gate_jam`, `clear_gate`, `load_roll`, `glue_level`, `add_glue`, `packer_running`, `labeler_heartbeat`. Scenarios get `target="labeler"` (conditions see `stats.labeler_status`, passed by the loop; labeler commands only in labeler scenarios); the CLI refuses a labeler scenario without the real labeler, the panel offers only what the plant can run.

| Scenario | On the real labeler PLC (`tests/test_labeler_scenarios.py`, 2026-11-18) |
| --- | --- |
| `web_torn` | `Alm_LabelTimeout` 5.0 s after WRAP began, FAULT; Reset + Start labels the same bottle |
| `bell_stuck` | stuck down in PRESS: `Alm_PocketJam` 2.1 s after UNCLAMP began; restart → IDLE → DISCHARGE, never labelled twice |
| `heater_failed` | `Alm_GlueHeatTimeout` 120.1 s after WARM_UP began (glue 22 °C); after repair warm-up + label |
| `heater_stuck_on` | 170 → 180 °C: back to WARM_UP after the bottle (rule 4); `Alm_GlueOverTemp` at 200.2 °C, FAULT, heater output off; Reset accepted once below 200 °C; WARM_UP 67 s back into the window |
| `roll_empty` | roll of 2: 2 labels, then Starved + `Alm_LabelEmpty`, gate shut, no FAULT; the roll change alone restarts it; since the roll tail fix every label whole (T5c) |
| `glue_refill` | `Glue_Low` only as a sensor; 1 kg cold glue → WARM_UP right after the bottle, labels again, no FAULT |
| `gate_jam` | `Alm_PocketJam` 1.8 s after the bottle passed the gate; operator takes it out, Reset + Start, next bottle labelled |
| `packer_stopped` | C3 full → WAIT_OUTFEED, Blocked, `Alm_OutfeedBlocked` 120.0 s later, no FAULT; continues without Reset |

Found live (test harness, not the PLC): a scenario's `finish()` stopped the loop while a bottle was still between gate and pocket; the PLC kept scanning, `Bottle_Entering` stayed set and the next scenario started with a jam. `finish()` now keeps the loop running (Manual) until the gate is empty, and alarms count only after this run's Start.
Tests: unit `test_labeler_faults.py` (6), `test_commands.py` (+2), `test_scenario_steps.py` (+12), `test_loop_labeler.py` (+3); live `test_labeler_scenarios.py` 16/16 (`--quick` 12/12 in one run, the two slow ones 4/4). Unit suite 425 passed.

**Stage 7 as built (2026-11-18):** the panel's third tab `3 Labeler` (key `3`; only with the real labeler, not with `--no-labeler` / `--filler-only`, where key 3 only gives a notice), built like the syrup room's tab.
- **Labeler world** (`view.labeler_world_text`, pure): C2 + `Bottle_At_Gate`, the bottle between gate and pocket, pocket bottle with its label share, bell up / down, turntable turns, roll (`level_bar`, labels left, web lost), glue temperature (the `GlueTemp` value written to the PLC) + glue kg bar, C3 + `C3_Full`, packer, E-stop, heartbeat, counts (discharged, labelled, lost, removed by hand). Sensor lamps and the labeler PLC's 9 output lamps in pairs from column 46 (`LABELER_LAMP_COL`: "C2" + a 35-character conveyor), lines ≤ 86 characters; red markers `GATE JAMMED`, `BELL STUCK`, `WEB TORN`, `HEATER FAILED`, `HEATER STUCK ON` (only `STUCK ON` next to `HEATER FAILED`, to stay inside 86), packer `STOPPED`, `FROZEN`; a line when the labeler PLC does not answer.
- **Labeler controls:** 7 switches (label web torn, bell stuck, heater failed, heater stuck on, gate jam, packer running, labeler heartbeat), 3 value rows (load roll labels, glue level kg, add glue kg), 1 action button (Clear gate). All 11 labeler commands of stage 6 now have a control; no new command. Switches follow the plant like the others (`_switches()` merges the labeler set only with the labeler).
- The filler tab's labeler line points to tab 3.

Tests: `test_view.py` (+3: values and lamps, every fault marker incl. both heater faults within 86 characters, no answer / no labeler), new `test_panel_labeler.py` (4 pilot tests at 140×45, dead ports: key 3 + `web_torn` switch shown as `WEB TORN`, value rows + Clear gate, switches follow the plant on the hidden tab, `roll_empty` from the shared row), `test_panel.py` (filler-only: no labeler tab, key 3 only a notice); unit suite 432 passed. Mutation checks (sync without the labeler switches, world without `WEB TORN`) each fail a test. Layout at 140×45: labeler controls 20/20 lines without scrolling, world column 86 characters, log 15 lines. **Live (2026-11-18), scenario `roll_empty` started from tab 3** (Textual pilot against the three real PLCs, `hmi.py` auto reset start on each): roll of 2 → `Roll_Empty ●` on the tab, `Alm_LabelEmpty` in the header, Starved; roll change after 5 s → Running, labelling went on; 5/5 checks.
**Found live (physics, stage 2, not the panel):** the second (last) label of the roll left with a broken label (169 of 210 mm, audit warning). The PLC stops the feed one tick after the mark, so the web runs on 20–40 mm past each mark (design-notes "Label_Mark"); `LabelWeb` stops feeding at the roll's last mark, so the last label of a roll is short by that run-on (and the first label after `load_roll` gets it extra). **Fixed 2026-11-18 (option chosen: a tail of web after the roll's last mark):** `config.ROLL_TAIL_MM` 40 mm (the largest run-on), `LabelWeb.tail_mm` and `web_left_mm` (the feed never moves more than the web left); `Roll_Empty` still at the last mark. Unit `test_labeler.py` (+2: the last label is whole with 1 and 2 ticks of run-on; the empty roll feeds only its tail; a tail of 0 fails both), live `test_labeler_scenarios.py roll_empty` new check T5c (no audit warning, every discharged bottle labelled) 3/3 in two runs; unit suite 434 passed.

**Stage 8 as built (2026-11-18):** wrap-up, no new simulator code.
- `tests/demo_check.py`: third `Runtime` (`plc-labeler`, editor 127.0.0.3, `plcs/labeler`, port 5023, labeler address map and step / state / alarm names); D1–D3 FAIL for the labeler like for the others (the default simulator needs it: its PLC drives C2). New `Runtime.fuxa` (labeler False): D4 reads only the PLCs FUXA has a device for. D6 over three locks names the cause: labeler free → `--no-labeler`?, syrup room + labeler free → `--filler-only`?. D7's labeler hint points to `tests/hmi.py --labeler` (no FUXA screen yet).
- **Animation words (`Anim_C3`, `Anim_Station`): not built, decided with the labeler HMI plan** (the filler's %MW70–75 were designed with its screens; design note "Why no HMI animation words for the labeler yet?").
- `design-notes.md`: "Pre-demo check with three PLCs" (+3 Q&As), labeler section (+2: label share on the tab, animation words). `README.md` demo check text. Report section 7: labeler status, structure tree, third link, labeler physics, panel tab 3. `CLAUDE.md`.

Test: live `demo_check.py` against the three runtimes, all D6 cases that matter seen: no simulator (WARN), `--no-labeler` (WARN "labeler … started with --no-labeler?"), all three PLCs (PASS) → 14 PASS, 0 WARN, 0 FAIL with the three PLCs reset to IDLE. Unit suite 432 passed.

---

## Packer simulator

Fourth machine (part B of `docs/packer/progress.md`), same pattern as the labeler. Filler, syrup room and labeler classes do not change; `PackerStandIn` stays for `--no-packer`. Addresses: `docs/packer/io-list.md`; behaviour: `docs/packer/state-diagram.md`; PLC constants the physics must fit: `state-diagram.md`, "Fixed constants". **Planned 2026-11-22, open questions answered the same day (all as recommended); stages 0–1 built 2026-11-23 (below).**

Before stage 0: part A committed (PLC program complete, `run_packer.py` 225/225); the other three runtimes up with their programs (`demo_check.py`).

### What the simulator has to model

Everything the packer PLC reads, nothing more (the PLC decides, the simulator only answers):

```
 C3 (packer PLC drives it) ─► gate ─► collation table ─► pusher ─► WRAP CONVEYOR ──────────────► OVEN CONVEYOR ─────► outfeed table ─► palletizer
  Bottle_At_Gate = exit slot   │       (group of 6)       │         entry   film stop   slot          Oven_Entry ... Oven_Exit   Table_Full    (stand-in)
                               Bottle_Counted pulse     Pusher_Home  0 mm   400 mm (Pack_At_Film)    1100 mm   heater, blower,
                                                        Pusher_Out          film roll -> feed rollers (Film_Pulse)  OvenTemp, Airflow_OK
                                                                            -> sheet in the slot (Film_In_Slot) -> knife
                                                                            wrap bar (Wrap_Home) lays it over the pack
```

New objects: a **`Pack`** (id, its 6 bottles, sheet length, wrapped, seconds hot in the oven, shrunk) that moves like a `Bottle` and is audited when it leaves; positions on the wrap and oven conveyors in **mm** (one pack object with a front position), not slots: the PLC's transitions hang on sensor edges at fixed points (film stop, oven entry), and a 0.5 s slot step would be too coarse.

### Stages

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| 0 | Address map | `address_map_packer.py` (converters from `address_map`): 10 outputs (coils 0–9), sensor block 8192–8202 (`%MX0` + `%MX1.0–1.2`, unused `%MX1.0` written FALSE), extra block 8224–8229 (`%MX4.0–4.5`), `OvenTemp` HR 1036, status HR 1074–1077 (State, **Step, WrapStep**, PackCount), computed HR 1064–1067, 10 alarms in two blocks (8232–8239, 8248–8249), conditioned bits 8216–8223, setpoints HR 1054–1057, manual buttons, `SIM_WRITE_*` | unit: numbers = `io-list.md` and the located variables of `plcs/packer/.../main.ld`, both directions; blocks contiguous; one-writer rule |
| 1 | Collation | `machines/packer.py` part 1: `Pack`; gate on C3's exit (a bottle leaves C3 only while `GateMotor` is ON, `Bottle_Counted` timed pulse per bottle), collation table (group, 7th bottle = audit), group pusher (position 0..1, out while the valve is ON, spring return; the group moves onto the wrap conveyor only at `Pusher_Out`) | 6 gate passes = 6 pulses, each ≥ 1 tick; gate shut after the 6th → no 7th; push / retract times < `PushTimeout` 3 s; a push stopped halfway leaves the group on the table |
| 2 | Wrap | wrap conveyor (pack front in mm, `Pack_At_Film` while the pack covers the stop sensor), film roll (length, `Film_Low`, `Film_Empty`, a tail that still finishes a started sheet), feed rollers + `Film_Pulse` (timed pulse per 50 mm), sheet in the slot (`Film_In_Slot`, attached until the knife cuts it), knife, wrap bar (one turn while ON, `Wrap_Home` window); a pack that drives over a cut sheet takes it, the bar turn wraps it; audits: pack drives over an uncut sheet (torn), sheet too short, feed into a turning bar | 850 mm = 17 pulses < `FeedTimeout` 10 s (1200 mm too); pulses ≥ 0.3 s apart; cut < `CutTime` 0.3 s; entry → film stop < `ClearTime` 3 s; RUN_ON / RUN_OUT < 3 s; one bar turn < `WrapTimeout` 5 s; bar stops inside its home window after the PLC's reaction time |
| 3 | Oven + outfeed | `Oven`: air temperature (first-order, heater only with air flowing, room loss), blower + `Airflow_OK`, oven conveyor (transit time), `Oven_Entry` / `Oven_Exit` pulses, shrink (time above the shrink temperature); outfeed table (capacity, `Table_Full`); `PalletizerStandIn` (takes one pack every N s, can be stopped) | heat-up 20 → 175 °C well under `HeatTimeout` 120 s; heater stuck on reaches `OvenMax` 230 °C; transit ≈ `OvenJamTime` / 2; hysteresis with the PLC rule stays in the window; cold oven = audit "not shrunk" |
| 4 | Plant + test controller | `Plant(with_packer=True)` (needs `with_labeler`): the packer PLC's `ConveyorMotor3` drives C3, the packer replaces `PackerStandIn`; `packer_writes()` (timed pulses ORed over the sub-steps, as `Label_Mark`), shared E-stop, own heartbeat; `PackerTestController` (Python port of both step tables and the oven rules, no alarms, as the labeler's) | test controllers pack N groups without a PLC; palletizer stopped → table full → READY waits (Blocked) → gate stays shut → C3 full → labeler Blocked → C2 full → filler Blocked (the line backs up, report 5.5) |
| 5 | Fourth link | `PackerPlc` (shared `PlcLink` base) on port 5024: `read_outputs()` (coils 0–9), `write()` (sensor block, extra block, `OvenTemp`), heartbeat, `read_status()` (State, Step, WrapStep, PackCount, GroupCount, PacksInOven, 10 alarms), `verify_writes()` | unit mapping + local-server link tests; live `test_link_packer.py`: heartbeat watchdog, write + read back, the PLC reacts to `OvenTemp` / `Table_Full` |
| 6 | Closed loop, four PLCs | `loop.run(..., packer_plc=)`, plant lock on 5024, fourth header line; CLI default = four PLCs, `--no-packer` keeps the stand-in | live `test_closed_loop_packer.py`: real bottles labeler → packer, N packs, `PackCount` + N, every pack wrapped and shrunk; `--packer-only` (the test feeds C3) |
| 7 | Commands + scenarios | faults in the physics (the PLC is never told) and hand actions, `target="packer"` scenarios (list below) | each scenario gives the alarm / state of `state-diagram.md` on the real packer PLC (`test_packer_scenarios.py`) |
| 8 | Panel | tab `4 Packer`: C3 + gate, table group, pusher, wrap conveyor with the pack and the film stop, roll, sheet, bar, oven temperature + packs inside, outfeed table; switches and value rows for every packer command | pilot test + one scenario from the panel |
| 9 | Wrap-up | `demo_check.py` (fourth PLC), design notes, README, report section 7, `CLAUDE.md`; animation words `%MW70–79` decided with the HMI plan (part C), as for the labeler | live `demo_check.py` with four PLCs |

**Stage 0 as built (2026-11-23):** `address_map_packer.py` with the labeler's layout and converters from `address_map`: outputs coils 0–9 (`CONVEYOR_MOTOR3` … `OVEN_CONVEYOR`), `SENSOR_BLOCK` 8192–8202 (`%MX0` + unused `%MX1.0`, `E_Stop_OK`, `Heartbeat`), `EXTRA_SENSOR_BLOCK` 8224–8229 (`%MX4.0–4.5`), `OVEN_TEMP` HR 1036, status 1074–1077 (`STATE`, `STEP`, `WRAP_STEP`, `PACK_COUNT`), computed 1064–1067, alarms `ALARMS_*` 8232 × 8 and `EXTRA_ALARMS_*` 8248 × 2, conditioned 8216 × 8, setpoints 1054 × 4, `MAN_*` 8208–8215. `SIM_WRITE_REGISTERS` = {`OvenTemp`} until the animation words are decided (part C). `tests/unit/test_address_map_packer.py` (11): io-list ↔ code and `main.ld` ↔ code in both directions (67 names), same bits as the other PLCs for the same job, blocks contiguous, one-writer rule.

**Stage 1 as built (2026-11-23):** `machines/packer.py` part 1: `PackerOutputs` (10 outputs in coil order), `Pack` (`id`, `bottles`, `name`), `Packer(c3)`. `step()` order: count pulse runs down → the `entering` bottle lands on the `table` after `PACKER_GATE_S` 0.4 s → held gate takes the bottle at C3's exit (`take_at_exit`, one at a time, new `Bottle_Counted` pulse `BOTTLE_COUNTED_S` 0.2 s set after the run-down so it is visible at the end of its step) → pusher moves (`PUSH_OUT_S` 1.0 s, spring `PUSH_RETURN_S` 0.8 s) → at `Pusher_Out` the table becomes one `Pack` on `wrap_packs` (once per stroke; a push stopped halfway moves nothing). Audits: 7th bottle on the table, bottle landing with the pusher out, pack pushed with ≠ `PACK_BOTTLES` 6. The labeler's `_Audit` became `events.Audit` (shared, no behaviour change). `tests/unit/test_packer.py` (15) with a `Collation` helper playing the PLC's CTU + gate rule in loop order (also with one extra tick of latency): exactly 6 bottles, the 7th waits at C3's exit; pulses 2 ticks ON, ≥ 3 OFF; push / return times < `PushTimeout`; two full cycles = packs 1 and 2 with bottles 1–12. Unit suite 472. Note for stage 4: `plant.packer` is still the `PackerStandIn`; it becomes `plant.packer_standin` (as the labeler did) when the real packer joins the plant.

### Proposed parameters (checked against the PLC constants)

| Parameter | Proposed | Must fit with (PLC) |
| --- | --- | --- |
| Gate: bottle leaves C3 → on the table | 0.4 s, `Bottle_Counted` ON 0.2 s at the start | the next bottle of a queue needs a full slot step (0.5 s) to reach C3's exit; the PLC shuts the gate ≤ 0.2 s after the 6th count (tick + scan): no 7th bottle |
| Pusher out / spring return | 1.0 s / 0.8 s | `PushTimeout` 3 s |
| Wrap conveyor speed, entry → film stop | 200 mm/s, 400 mm (2.0 s) | `ClearTime` 3 s longer than the travel (rule 2) |
| Pack length | 200 mm (3 bottles × 65 mm) | RUN_ON = 1.0 s < 3 s; the conveyor's stop overshoot (≤ 40 mm) keeps `Pack_At_Film` ON |
| Feed speed, pulse | 167 mm/s, one pulse per 50 mm, ON 0.15 s / OFF 0.15 s | `FilmPulseMM` 50; 850 mm 5.1 s, 1200 mm 7.2 s < `FeedTimeout` 10 s; each level ≥ one 0.1 s tick |
| Knife | cuts after 0.2 s ON | `CutTime` 0.3 s |
| Wrap bar | 1.5 s per turn, `Wrap_Home` window 25 % of a turn (0.375 s) | `WrapTimeout` 5 s; the PLC drops `WrapBar` 0.1–0.2 s after the edge, the bar stops inside the window |
| WRAP → oven entry | film stop 400 → oven entry 1100 mm: RUN_OUT about 1.0 s | `PushTimeout` 3 s |
| Sheet needed to wrap a pack | 800 mm | default `FilmLength` 850 wraps; 600 mm = audit "sheet too short" (the recipe value matters) |
| Oven length, conveyor speed | 1000 mm, 100 mm/s: transit 10 s | `OvenJamTime` 20 s ≈ 2 × transit |
| Oven heater, room | target 300 °C, tau 60 s with air flowing; room 20 °C, tau 300 s | 20 → 175 °C in 48 s < `HeatTimeout` 120 s; stuck heater 190 → 230 °C in 27 s (`OvenMax` reachable); 0.19 °C per tick at 185 °C inside `OvenHyst` 5 °C |
| Shrink | ≥ 6 s above 170 °C | `Oven_Ready` window 175–205 °C |
| Blower spin-up / `Airflow_OK` | 1.0 s after `OvenBlower` ON, FALSE at once when OFF | `AirflowTimeout` 3 s |
| Film roll | 300 m (about 350 sheets), `Film_Low` at 10 m, tail 1.2 m | warnings only; the tail finishes a started 1200 mm sheet |
| Outfeed table, palletizer | capacity 6 packs, `Table_Full` at 3; one pack taken every 10 s | Q3: room behind the sensor for every pack past READY (1 on the wrap conveyor + ≤ 2 in the oven) |
| Throughput | wrap cycle about 13 s; the labeler gives one group per about 36 s | the packer keeps up and is often Starved (expected, `progress.md` concept) |

### Commands and scenarios (stage 7)

| Scenario | Fault / hand action | Expected on the real packer PLC |
| --- | --- | --- |
| `film_torn` | feed rollers turn, no film moves (no `Film_Pulse`) | `Alm_FilmFault` 10 s after FEED began; roll fixed, Reset + Start feeds the rest |
| `sheet_dropped` | the cut sheet falls out of the slot in READY | `Alm_FilmFault` after 1 s; sheet put back by hand, Reset + Start |
| `roll_empty` | small roll; `load_roll` | `Film_Empty`: pack waits in WAIT_PACK, Starved + `Alm_FilmEmpty`, no FAULT; the roll change alone restarts it |
| `pusher_stuck` | pusher valve ON, cylinder does not move | `Alm_PackJam` 3 s after PUSH began |
| `wrap_bar_stuck` | bar does not turn (film caught) | `Alm_WrapTimeout` 5 s after WRAP began; no pack wrapped twice after the restart |
| `heater_failed` | heater gives no heat | `Alm_OvenHeatTimeout` 120 s after WARM_UP began |
| `heater_stuck_on` | heater heats whatever the PLC says | `Alm_OvenOverTemp` at 230 °C in any mode, FAULT, blower runs 30 s more |
| `blower_failed` | blower ON, no air (`Airflow_OK` FALSE) | heater off by its interlock, `Alm_AirflowLost` after 3 s |
| `pack_stuck_in_oven` | one pack stops in the oven; `clear_oven` | `Alm_OvenJam` 20 s after the last pack out; Reset zeroes `PacksInOven` |
| `palletizer_stopped` | palletizer stand-in stops | table full → READY waits, Blocked, `Alm_OutfeedBlocked` 2 min later, no FAULT; C3 fills → labeler Blocked (the line backs up); continues without Reset |

Commands get a `_Spec(packer=True)` flag (ignored with a warning without the real packer); `packer_heartbeat` freezes only the packer's heartbeat.

### Open questions (answered 2026-11-22: all as recommended ★)

1. **CLI default:** `uv run simulator` drives four PLCs (`--no-packer` keeps the stand-in), like the labeler became the default? ★ yes – but then the packer runtime must run, else C3 stands and the labeler is soon Blocked.
2. **Test controller:** port the packer's two step tables and oven rules to Python (`PackerTestController`) for the unit tests of stages 4–6, like the labeler's? ★ yes – it lets stages 1–4 be tuned and tested without the PLC and gives the line-backup test.
3. **Pack quality:** audit warnings when a pack leaves with a short sheet, no wrap, not shrunk, or an unlabelled bottle (the PLC cannot see these)? ★ yes, warnings only, counted on the panel.
4. **Conveyor model:** wrap and oven conveyors with positions in mm (above) instead of the slot `Conveyor`? ★ mm.
5. **Scenario list** above: complete, or add / drop one (e.g. `bottles_short`: fewer bottles from the labeler = Starved)?
