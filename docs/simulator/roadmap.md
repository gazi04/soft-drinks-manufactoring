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
- **Layout:** header (filler + syrup PLC lines) / `TabbedContent` `1 Filler`, `2 Syrup room` (keys `1` / `2`; the syrup tab only without `--filler-only`) / one shared scenario row (select shows `name [target]`, syrup scenarios hidden with `--filler-only`) / event log.
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
