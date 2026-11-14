# Syrup room / mixer (`plc-syrup-carb`) – Progress

Batch mixer and carbonator with a buffer tank. Second machine of the line, after the filler (scope option B, `docs/scope-options.md`).
Design reference: `docs/project-report.md` sections 4.2 (machine), 5.1–5.2 (`Product_Available` handshake), 6.2 (register layout), 9 (modes/alarms).
Syrup docs: `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (stages for the ladder program).

**Current step:** ladder stages 0–7 done (2026-11-12), the syrup room PLC program is complete. Simulator stages 0 (address map) and 1 (tank physics) done; next: stage 2, temperature and CO2 pressure (section 2).
- The filler's leftovers (HMI stage 7, manual interlocks) are independent of this machine.

## Why the syrup room second

- Closes the two syllabus gaps the filler leaves: **math and analog process control** (week VIII: ratio `DIV`, `ADD`, level/temperature/pressure compares, hysteresis) and **PLC-to-PLC networking** (week XIII: `Product_Available` becomes a real signal from a second PLC).
- A different kind of machine: a **batch process** with one step at a time and analog values, next to the filler's indexing machine with parallel stations.
- Reuses everything the filler built: `FB_Motor`, `FB_Valve`, modes/E-stop section, heartbeat, jog watchdog, register layout, simulator framework, FUXA generator (`fuxa_tags.Machine`, HMI issue I11).

## Order of work and why

Same as the filler (`docs/filler/progress.md`): **paper → ladder → simulator → HMI**. Addresses and steps first, because ladder, simulator and FUXA all depend on them.

## Checklist

### 0. Paper
- [x] I/O list draft (`io-list.md`, 2026-11-08)
- [x] State diagram draft (`state-diagram.md`, 2026-11-08)
- [x] Ladder roadmap draft (`ladder-roadmap.md`, 2026-11-08)
- [x] Review all three drafts, answer the open questions (2026-11-09)
- [x] Record the scope decision (report section 11, `CLAUDE.md`, 2026-11-09)
- [x] Report section 4.2 / 6.2 updated (2026-11-09; 6.2 now also matches the filler's real `%MX` layout) to the decisions below (`Product_Available` in `%MX`, one vessel, buffer tank, mains water, ratio ×10, `MixHighHigh`, `Alm_DoseTimeout`)

### 1. Ladder logic (OpenPLC Editor v4) – stages in `ladder-roadmap.md`
- [x] Stage 0 – Second runtime container (`plc-syrup-carb`, Modbus 5022, editor 127.0.0.2:8443), project `plcs/syrup/`, `mx_bits` = 64, variables, FBs copied
  - [x] Container `plc-syrup-carb` running (2026-11-09; state EMPTY until the first upload)
  - [x] Modbus server in the project: TCP, 0.0.0.0:502, Buffer Mapping as the filler (`mxBits` 64 + `qxBits`/`mwCount`/… – without them the compile aborts with "Nothing produces %MW / %QX on this target") (`devices/servers/modbusServer.json`)
  - [x] Device board **OpenPLC Runtime v4**, IP **127.0.0.2** (2026-11-09; Editor login with the filler's credentials works)
  - [x] FBs copied (`FB_Motor`, `FB_Valve` unchanged), 56 variables declared (io-list + constants), empty test rung (2026-11-09, written into `plcs/syrup/pous/` on disk)
  - [x] Modbus test script `simulator/tests/test_syrup_stage0.py` (Z1–Z5, port 5022)
  - [x] Open in the Editor, compile, upload to 127.0.0.2 → runtime RUNNING (2026-11-09)
  - [x] `test_syrup_stage0.py` 5/5 (2026-11-09) + debugger shows MixLevel 4242 / E_Stop_OK TRUE
- [x] Stage 1 – Modes and safety (copied from the filler, new defaults), heartbeat watchdog
  - [x] Variables declared (2026-11-09): `Init_Done`, 10 × `Def_…`, `Mode_*`, `*_Cmd`, `Master_Enable`, `Hb_Last`/`Hb_Changed`/`Hb_Timer` (77 in `main`)
  - [x] Rungs built in the Editor (filler section 1 as template), compiled, uploaded (11 rungs, incl. rung 5 `Cmd` clear)
  - [x] Test script `test_syrup_stage1.py` passes (A1–A7): 7/7 on 2026-11-11 (first run 6/7: A3 FAIL, `Cmd` clear rung missing → added as rung 5, copy of filler R7). Debugger check of `Master_Enable` = `E_Stop_OK` AND NOT `Alm_CommLost` (all 4 combinations, `Alm_CommLost` forced) passed 2026-11-11.
- [x] Stage 2 – Math and compare: `SyrupTarget` (DIV with zero guard), `DoseTarget`, `Buffer_Room`, `Ingredients_OK`, seconds → `TIME`
  - [x] Variables declared (2026-11-09): `Ratio_OK`, `Water_x10`, `Buffer_Sum`, `Buffer_Room`, `Ingredients_OK`, `Ms_Per_s` (DINT 1000), `MixTime_D`/`Mix_ms`/`Mix_PT`, `CarbTime_D`/`Carb_ms`/`Carb_PT` (89 in `main`)
  - [x] Rungs 11–17 built in the Editor (header, ratio guard, ratio math, buffer room, ingredients, two time conversions), compiled, uploaded
  - [x] Test script `test_syrup_stage2.py` passes (B1–B7 automatic 8/8 on 2026-11-10; debugger checks D1–D5 passed 2026-11-11)
- [x] Stage 3 – Hysteresis bits: `Temp_OK`, `CO2_Demand`, `Product_Available`
  - [x] Variables declared (2026-11-11): `Temp_Upper`/`Temp_Cold`/`Temp_InBand`, `Press_Lower`/`Press_Low`/`Press_Below`, `Avail_Upper`/`Buf_Enough`/`Buf_AboveMin` (98 in `main`)
  - [x] Rungs 18–24 written into `main.ld` (header; per bit one threshold rung ADD/SUB + 2 compares → helper BOOLs, one seal-in rung SET ∥ (bit AND HOLD) → coil; `SUB` / `LT` checked in the Editor 4.3.3 library)
  - [x] Opened in the Editor, compiled, uploaded to 127.0.0.2 (2026-11-11)
  - [x] Debugger check `ProductTemp` 60 → 40 → 44 → 46: `Temp_OK` F → T → T → F (covered by H1 of the script)
  - [x] Test script `test_syrup_hysteresis.py` passes (H1–H3): 21/21 on 2026-11-11; regression stage 1 7/7, stage 2 8/8
- [x] Stage 4 – Sequencer skeleton: steps 0, 5, 10–60, 55, 900; done bits; `Batch_Dosed`
  - [x] Variables declared (2026-11-11): 10 step bits, `Seq_Enable`, `Water_Done`, `Syrup_Done`, `Tank_Empty` (roadmap: `Transfer_Done`; also used negated in WAIT_DEMAND), `Batch_Needed`, `Water_Timer`/`Syrup_Timer`/`Mix_Timer`/`Carb_Timer` (117 in `main`)
  - [x] Rungs 25–48 written into `main.ld`: section 4 step decode (4 EQ rungs + `Seq_Enable`), section 5 done bits (level compares, `Batch_Needed`, 4 TONs), section 6 transitions (abort, 0→5, 5→30, 5→10, 10→20, 20→30 + (S) `Batch_Dosed`, 30→40, 40→50, 50→60, 50→55, 55→60, 60→5 + (R) `Batch_Dosed`, 900→0); no Stop yet (stage 6), no MOVE 900 yet (stage 7)
  - [x] Opened in the Editor, compiled, uploaded to 127.0.0.2 (2026-11-11)
  - [x] Test script `test_syrup_sequencer.py` passes (Q1–Q23): 26/26 on 2026-11-11; regression stage 2 8/8, stage 3 21/21. Debugger: `Water_Timer` / `Syrup_Timer` ET counts in 10 / 20
- [x] Stage 5 – Outputs: jog watchdog, Auto OR Manual requests, interlocks, FB instances
  - [x] Variables declared (2026-11-11): `Jog_TON`, interlock compares `Mix_BelowMax`/`Mix_Covered`/`Press_BelowMax`/`Buf_BelowMax`, step groups `CO2_Steps`/`Cool_Steps`/`Agit_Steps`, 6 × `Req_…`, `Mot_Water`/`Mot_Syrup`/`Mot_Agitator`/`Mot_Transfer` (FB_Motor), `Vlv_Cooling`/`Vlv_CO2` (FB_Valve) (137 in `main`)
  - [x] Rungs 49–67 written into `main.ld`: section 7 jog watchdog (cloned from filler rungs 42–43), interlock compares, step groups (each a 2-branch OR of a step bit and the next smaller group), 6 request rungs (Auto OR Manual, interlocks in series after the OR), 6 FB instance rungs (Enable = `Master_Enable`, one writer per `%QX`)
  - [x] Opened in the Editor, compiled, uploaded to 127.0.0.2 (2026-11-12)
  - [x] Test script `test_syrup_outputs.py` passes (O1–O13, M1–M17, J1–J3): 35/35 on 2026-11-12
- [x] Stage 6 – Stop at step ends, `State`, `BatchCount`
  - [x] Variables declared (2026-11-12): `Stop_Request`, `Starved`, `Batch_Counter` (CTU) (140 in `main`)
  - [x] Rungs written into `main.ld` (82 rungs): section 6 Stop latch (rung 37, copy of filler rung 24) + 4 "Stop at a safe point -> 0" rungs (38–41, two step ends each); the 10 normal transitions 5→30 … 60→5 rebuilt with `NOT Stop_Request` (same rung ids); `(S)` / `(R) Batch_Dosed` moved out of 20→30 / 60→5 into their own rungs 47 / 54, so a Stop at the end of DOSE_SYRUP / TRANSFER still sets / resets it; new section 8 (rungs 75–81): `Batch_Counter` (CU = `Step_Transfer AND Tank_Empty`, CV → `BatchCount`), `Starved`, 5 State MOVE rungs
  - [x] Opened in the Editor, compiled, uploaded to 127.0.0.2 (2026-11-12)
  - [x] Test script `test_syrup_stop_state.py` passes (S1–S12, T1–T9, C1) on 2026-11-12; regression `tests/run_syrup.py` all passed
- [x] Stage 7 – Alarms and FAULT
  - [x] Variables declared (2026-11-12): `Cool_Timer` (TON), `Mix_Overflow`, `Overflow_Cause`, `No_Fault` (144 in `main`)
  - [x] Rungs 82–94 built in the Editor by hand, compiles: overflow cause (stage 5 interlock compares, negated), set / reset pairs per FAULT alarm (Reset only when the cause is gone), `Alm_LowSyrup` = `Starved` (warning, not latched), `No_Fault` (De Morgan), last rung `NOT No_Fault` → MOVE 900 → `Step`
  - [x] Rung 88: `Step_Cool` contact added before `Cool_Timer.IN` (was missing: timer ran in every step → `Alm_CoolTimeout` after 60 s)
  - [x] Uploaded to 127.0.0.2; test script `test_syrup_alarms.py` passes (N1, O1–O5, P1–P4, R1, L1–L2, D1–D2, C1–C2; `--quick` without the timeouts) on 2026-11-12

### 2. Python simulator – stages in `docs/simulator/roadmap.md` (section "Syrup room simulator")
- [x] Stage 0 – Address map `address_map_syrup.py` + `tests/unit/test_address_map_syrup.py` (2026-11-12; doc guard against `io-list.md`, live read of all blocks on 5022 ok)
- [x] Stage 1 – `machines/syrup.py` tank volumes from pump flows, `MixHighHigh`, audit warnings, `deliver_syrup` / `draw_buffer` (2026-11-12)
- [ ] Stage 2 – physics class `machines/syrup.py` (rest): temperature (first-order toward coolant / room), pressure (CO2 in, dissolving out), buffer drained by the filler's `FillValve`
- [ ] Second PLC client; copy `Product_Available` (this PLC `%MX6.0`) → filler `%MX1.0` every tick, replacing the stand-in
- [ ] Fault scenarios (low syrup, cooling failure, over-pressure, buffer full, pump failure, level sensor stuck → `MixHighHigh`)
- [ ] Panel: syrup room rows (tanks, temperature, pressure)

### 3. FUXA HMI
Stages S0–S7 in `docs/hmi/roadmap.md`, section "Syrup room screens".
- [x] Device + tags from `address_map_syrup.py` (`fuxa_tags.SYRUP`, 48 tags, `plc-syrup-carb:502`) + test page `Syrup tags` (S0 + S1, 2026-11-13; live H2 48/48, H4 connected)
- [x] Header for two machines (S2, 2026-11-13): row 1 both machines + E-stop + both links, row 2 the machine of the view, picker = sibling views of Line / Alarms / Trends; Auto / Reset / Start / Stop of the syrup room from FUXA verified in the browser
- [x] Syrup room detail screen (S3, 2026-11-13): tanks as level bars, pipes on their outputs, pumps / valves as lamps, temperature (°C) + `Temp_OK`, pressure (bar) + `CO2_Demand`, 10 recipe setpoints with limits, 6 hold-to-run buttons; `cooling_failure` and `over_pressure` followed on the screen
- [x] Line overview (S4, 2026-11-14): syrup room block coloured by `State` (click → Syrup room) with step and buffer level, product pipe from the syrup PLC's `Product_Available`, "filler input" lamp on the filler's; H7 + 8 mutations. Open: live `buffer_starved` on the Line view (PLCs need an upload)
- [x] Alarms (S5, 2026-11-14): 6 FUXA alarm definitions `syrup_Alm_*` (group Syrup room; FAULT bits highhigh, CommLost high, LowSyrup low), one unfiltered alarm list for the whole line with a Machine column, Reset per machine; H8 + 8 mutations. Open: live `level_sensor_stuck` (PLCs need an upload)
- [x] Trends (S6, 2026-11-14): DAQ on MixLevel, BufferLevel, ProductTemp, CoolSetpoint, CO2Pressure, PressSetpoint, State (stored in °C / bar: FUXA scales first); charts tank levels, temperature, pressure (15 min) and state (30 min); H9 per machine + H10 syrup, 8 mutations. Open: one live batch as a curve (PLCs need an upload)
- [x] Wrap-up (S7, 2026-11-14): `demo_check.py` D4 per PLC (one line per FUXA link), demo script for the whole line (`docs/hmi/demo-script.md`, ~15 min), ISA-101 review of the syrup screens, report 8.1. Open: `demo_check.py` 0 FAIL and one rehearsal with live PLCs

### 4. Tests (scenarios)
- [ ] Normal batch: water → syrup (ratio) → mix → cool → carbonate → transfer, `BatchCount` +1
- [ ] Buffer above `BufferLow` → waits in WAIT_DEMAND, Running, no batch
- [ ] Low syrup → Starved + `Alm_LowSyrup` warning, continues when refilled
- [ ] Buffer has no room for the batch → WAIT_BUFFER (Blocked), continues when the filler used product
- [ ] Cooling failure → `Alm_CoolTimeout`, FAULT; Reset + Start resumes at MIX
- [ ] Over-pressure → FAULT, CO2 valve closed
- [ ] Pump failure in DOSE_WATER → `Alm_DoseTimeout`, FAULT
- [ ] Level sensor stuck low while dosing → `MixHighHigh` → `Alm_Overflow`, pumps off
- [ ] Stop in the middle of a batch → IDLE at the end of the step; Start continues (no double water)
- [ ] E-stop and release; resume
- [ ] Manual mode with interlocks (agitator, CO2, transfer)
- [ ] **Two PLCs:** syrup room stopped, buffer runs empty → filler Starved; restarted → filler continues by itself

## Neighbour stand-ins (until the real sources exist)

| Missing source | Stand-in |
| --- | --- |
| Water supply | Mains water, always available: the simulator only adds `WaterPump` flow to the mixing tank |
| Syrup supply | Simulator: syrup tank, refill from the panel ("deliver syrup"); a scenario lets it run low |
| Chiller | Simulator: coolant always cold; "chiller failure" scenario keeps the temperature from falling |
| CO2 supply | Simulator: always available |
| Filler (consumer) | The real filler PLC: the simulator drains the buffer while the filler's `FillValve` is open |
| E-stop chain, heartbeat | Simulator, as for the filler |
| HMI commands | Forced in the debugger, later the test scripts' `hmi.py` and FUXA |

## Decisions

Drafted 2026-11-08 from the design talk; to confirm in the review.

| Date | Decision |
| --- | --- |
| 2026-11-08 | **Batch process with a buffer tank.** The mixing tank makes one batch at a time; the buffer decouples it from the filler, which draws product all the time. Without a buffer the filler would be Starved during every batch. |
| 2026-11-08 | **One vessel** for mixing, cooling and carbonating (mixing tank = carbonator). One level, one temperature, one pressure sensor. |
| 2026-11-08 | **Carbonation is set by pressure, not by a ratio:** temperature fixed cold (`CoolSetpoint` ≈ 4 °C), more CO2 = higher `PressSetpoint`. CO2 only when cold (interlock). |
| 2026-11-08 | **Hysteresis** (two thresholds + seal-in) for cooling (`Temp_OK`), CO2 (`CO2_Demand`) and `Product_Available`, so valves and the handshake do not chatter. |
| 2026-11-08 | Same address layout as the filler (`%MX` for Booleans, same byte groups), so `address_map.py`, `plcs.py` and the FUXA generator are reused. `Product_Available` out = `%MX6.0` (coil), not `%MW60` as in report 6.2. |
| 2026-11-08 | **Stop at the end of the current step** (every step end is safe), not at the end of the batch. Resume is decided from the tank (levels) plus one memory bit `Batch_Dosed`, so a finished batch is never diluted. |
| 2026-11-08 | WAIT_DEMAND with a full buffer is **Running** (green), as in report 5.5; Starved = ingredients missing; Blocked = WAIT_BUFFER (batch does not fit). |
| 2026-11-09 | **INT fixed-point units** (L, 0.1 °C, 0.01 bar), no REAL: one register per value, plain INT compares. |
| 2026-11-09 | **`SyrupRatio` ×10** (55 = 5.5:1): `SyrupTarget = WaterBatch × 10 / SyrupRatio` (MUL before DIV), guard `SyrupRatio ≥ 10`. |
| 2026-11-09 | **Mains water**: `WaterLevel` removed, analog sensors now `%MW12–16`; `Ingredients_OK` = syrup only. |
| 2026-11-09 | **`Alm_CommLost` at `%MX5.3` in every PLC** (same as the filler); other alarm bits per machine: `Alm_LowSyrup` 5.4, `Alm_DoseTimeout` 5.5. |
| 2026-11-09 | **High-high switch `MixHighHigh`** (%MX0.0) as hardware backup: in `Alm_Overflow` and the dosing-pump interlock. |
| 2026-11-09 | **`Alm_DoseTimeout`** (FAULT): two TONs (`Water_Timer`, `Syrup_Timer`) with constant `DoseTimeout`; enables the pump-failure scenario. |
| 2026-11-09 | **Severity:** `Alm_CoolTimeout` = FAULT (chiller broken); `Alm_LowSyrup` = warning, State Starved, not latched, clears itself after refill (waiting is not a fault). |
| 2026-11-09 | **Editor address per PLC = loopback IP:** Editor 4.3.3 hard-codes port 8443 (`RUNTIME_API_PORT`), only the IP is configurable, so a host port 8442 is unreachable from the editor. Filler editor bound to `127.0.0.1` + `::1`, syrup to `127.0.0.2` (both port 8443); container IPs were rejected because they can change on re-create. |
| 2026-11-09 | **Docker service rename** (`openplc` → `plc-filler`, 5023/8443 per report 6.1) **postponed**: separate step later, not mixed with syrup stage 0. |
| 2026-11-09 | Timeouts and times (`CoolTimeout`, `DoseTimeout`, `MixTime`, `CarbTime`) are in **simulator time** (batch ~2 min instead of ~30 min). |
| 2026-11-12 | **`Alm_LowSyrup` not latched** (normal coil = `Starved`): waiting for syrup is not a malfunction, the machine continues by itself after the refill, and the refill is the operator's acknowledgement. The four FAULT alarms are latched because their cause (a timer Q) disappears as soon as the step changes, and a fault needs an operator Reset. Limitation: `Ingredients_OK` has no hysteresis, so the warning may flicker at the exact limit. |
| 2026-11-12 | **Stop rungs like the filler:** one Stop latch, separate "-> 0 at a safe point" rungs (two step ends per rung), `NOT Stop_Request` in every normal transition (one transition per scan). `Batch_Dosed` set / reset and `BatchCount` hang on the step-end condition, not on the transition, so they also happen when a Stop ends the step. |
| 2026-11-08 | `Product_Available` depends on the buffer level only (no pressure sensor on the buffer); it stays TRUE during FAULT / E-stop of the syrup room, because the product in the buffer is still good. |

## Open questions

All answered 2026-11-09 (see Decisions). Left for later:
- **Double Reset after comm loss in FAULT:** `Master_Enable` (rung 6) is computed above the watchdog Reset (rung 10), so with `Alm_CommLost` and FAULT both latched the first Reset only clears `Alm_CommLost`, a second one leaves FAULT. Fix if wanted: move the `Master_Enable` rung below rung 10 (the filler has the same order). For now documented; the test scripts reset until Step 0.
- Animation words `%MW70–79` for the HMI: probably none needed (`io-list.md`), decided at the HMI step.

## Log

| Date | Done |
| --- | --- |
| 2026-11-08 | Syrup room chosen as the second machine (option B). I/O list, state diagram, ladder roadmap and this progress file drafted. |
| 2026-11-09 | Stage 0 started: `plcs/mixer/` renamed to `plcs/syrup/`; service `plc-syrup-carb` + volumes added to `docker-compose.yml`, container healthy (EMPTY). Editor port problem found and solved with loopback IPs; filler still RUNNING after the re-create. |
| 2026-11-09 | Scope decision (option B) recorded in report 11 and `CLAUDE.md`; report 4.2, 5.1, 6.2, 9.3, 10 updated to the syrup design. |
| 2026-11-09 | Stage 0 files: FBs copied, `main` with all io-list variables + constants and one empty rung, `test_syrup_stage0.py`. First run before the upload: connection reset on 5022 (no program → no Modbus server inside the runtime). |
| 2026-11-09 | Stage 0 done: compile first failed ("Nothing produces %MW / %QX": server Buffer Mapping had only `mxBits`), fixed with the filler's mapping; uploaded, RUNNING. `test_syrup_stage0.py` 5/5 after re-uploading the filler: its re-create at 06:04 had reset `/workdir/plugins.conf` (container layer, not a volume) → `modbus_slave` disabled, program still RUNNING but port 502 dropped every connection. |
| 2026-11-09 | Open questions answered (units, ratio ×10, mains water, alarm bits, high-high switch, dosing timeout, severities, service rename); drafts updated. |
| 2026-11-11 | Stage 1 re-test: first attempt connection reset on 5022. Cause: both runtimes hit the runtime watchdog at 15:39:41 ("dispatcher stalled, no tick for ~2.7 s", same moment in filler and syrup → host stall, not the program) and restarted in SAFE MODE without the program. `docker compose restart plc-syrup-carb` loaded the program again (RUNNING); `test_syrup_stage1.py` 7/7. |
| 2026-11-11 | Stage 1 debugger check (`Master_Enable` truth table) and stage 2 debugger checks D1–D5 (`Buffer_Room`, `Ingredients_OK` boundaries, `Mix_PT` / `Carb_PT`) passed. Stages 0–2 done. |
| 2026-11-11 | Stage 3 done: hysteresis variables and rungs 18–24 written into `main.ld` (cloned from existing rungs), compiled and uploaded; `test_syrup_hysteresis.py` 21/21, stages 1–2 re-run green. |
| 2026-11-11 | Stage 4 done: 19 variables and rungs 25–48 (step decode, done bits, transitions) written into `main.ld` with a rung builder from existing nodes, compiled and uploaded; `test_syrup_sequencer.py` 26/26 (one batch, WAIT_BUFFER, resume with `Batch_Dosed`, abort, FAULT exit). |
| 2026-11-12 | Stage 5 done: 20 variables and rungs 49–67 (jog watchdog, interlock compares, step groups, 6 request rungs Auto OR Manual with interlocks after the OR, 6 FB instances) compiled and uploaded; `test_syrup_outputs.py` 35/35 (outputs per step, interlocks in Auto and Manual, jog watchdog, E-stop). |
| 2026-11-12 | Test runners `tests/run_filler.py` / `tests/run_syrup.py` (one command per PLC, shared `tests/suite.py`); filler scripts now exit 0/1. |
| 2026-11-12 | Stage 6 written: 3 variables, Stop latch + 4 safe-point rungs, 10 transitions with `NOT Stop_Request`, `Batch_Dosed` set/reset in own rungs, section 8 counter + State; `test_syrup_stop_state.py` written. Compiled, uploaded; `test_syrup_stop_state.py` and `tests/run_syrup.py` pass. Stage 6 done. |
| 2026-11-12 | Stage 7 done: 4 variables and rungs 82–94 built by hand in the Editor (alarm causes from the stage 5 interlock compares, set / reset pair per FAULT alarm, `Alm_LowSyrup` = `Starved` as a non-latched warning, `No_Fault` by De Morgan, last rung MOVE 900). Review found the missing `Step_Cool` contact in the `Cool_Timer` rung, fixed. `test_syrup_alarms.py` passes. Ladder stages 0–7 complete. |
| 2026-11-12 | Suite regression after stage 7: `test_syrup_outputs.py` O12b and `test_syrup_stop_state.py` failed. Not the PLC: the stage 5/6 scripts predate the latched alarms. O12b now expects the latch (O12b nothing, O12c Reset → WaterPump; 36 checks); every syrup script now resets with `reset_to_idle` (Auto first, Reset until Step 0) and ends with `leave_idle` (Step 0, no alarms), both in `test_syrup_sequencer.py`. `tests/run_syrup.py` all green twice (stage 0 not run: filler Modbus off). |
| 2026-11-12 | Syrup simulator stage 0: `address_map_syrup.py` (same block shapes as the filler: sensor coils 8192–8202, analog HR 1036–1040, outputs 0–5, status HR 1074–1077, 6 alarms 8232–8237, `Product_Available` out 8240) and 9 unit tests incl. a parser that compares every name with the tables of `io-list.md`. Read-only live check on 5022: all blocks answer, setpoint defaults and `SyrupTarget` 90 / `DoseTarget` 590 as expected. |
| 2026-11-12 | Syrup simulator stage 1: `machines/syrup.py` + `config.py` parameters + 20 unit tests (suite 182). Flows 20 / 5 / 25 L/s chosen after an overshoot analysis (level-based dosing, one level per tick). Found while testing: overflow warnings must be evaluated every step, not only while a pump runs, or they never re-arm. |
| 2026-11-14 | Agitator animation on the *Syrup room* screen: a static shaft from the tank top and an impeller (`fs.impeller`, 4 blades leaning one way so the turning shows, tips on one circle so FUXA turns it in place) that turns while the syrup `Agitator` output is ON – the same FUXA actions (clockwise / stop) as the conveyor rollers, now shared in `fs.spinning_path_item`. Unit test +1 (310), H11 checks the binding, export: only `v_syrup_main` changed, round trip identical. Browser: drawn in the liquid, no transform while the agitator is OFF; turning not yet seen live (syrup room was Stopped). |
| 2026-11-14 | Buffer draw factor made visible (user question: "15 L per 500 ml bottle?"): not a unit error – the simulator converts ml to L and takes ×30 on purpose (`config.BUFFER_DRAW_SCALE`, time compression, design note); measured live 7 bottles = 92 L. The HMI never said so: a muted two-line note under the buffer tank on the *Syrup room* screen, built from the simulator config (`fuxa_views.buffer_scale_note`), H11 checks it is there with the current numbers (a changed factor without regeneration fails). Report section 7 + demo script step 3 mention it. |
| 2026-11-14 | FUXA S7, wrap-up: `demo_check.py` D4 checks FUXA's link to every PLC (before only the filler's), plugin hint only when all links are down; checked live (2 FAIL, PLCs down) and with simulated FUXA answers. Demo script rewritten for the whole line (syrup room screen, trends, `buffer_starved`, `level_sensor_stuck`, E-stop on both machines). ISA-101 review of the syrup screens: no change. Report 8.1 updated. The syrup room HMI is built; what remains is live: upload both programs, `demo_check.py` 0 FAIL, the S4–S6 checks, one rehearsal. |
| 2026-11-14 | FUXA S6, trends: DAQ on 7 syrup tags (levels, temperature + setpoint, pressure + setpoint, State); FUXA scales before it stores, so the history is in °C / bar. 4 charts on `Trends (syrup room)` (2 × 2), filler charts unchanged. H9 per machine (incl. "every chart line is logged"), H10 syrup (points + display units), 8 mutations caught, offline 8/8, round trip identical. Found: the empty charts / `loadWatch` errors since S2 were Playwright's headless Firefox without a locale (`invalid language tag` stopped FUXA's script bundle before `uPlot`); with a locale the charts draw, 0 console errors. Not live: PLCs without Modbus server (upload). |
| 2026-11-14 | FUXA S5, alarms: 6 syrup alarm definitions (`syrup_<bit>`, group Syrup room, priority = FAULT / comm lost / warning as for the filler); names per machine because both PLCs have `Alm_CommLost`; filler definitions unchanged. Both Alarms views show the same unfiltered list (all machines) with a new Machine column; Reset stays per machine, with a help line. H8 per machine, 8 mutations caught, offline 8/8, round trip identical, browser shows the column. Not live: PLCs without Modbus server (upload) – `level_sensor_stuck` open. |
| 2026-11-14 | FUXA S4, Line overview: the grey syrup stand-in became the real syrup PLC's block (State colour, step, buffer level, click → Syrup room); the product pipe and "no product" now follow the syrup PLC's output `Product_Available` (the source), a "filler input" lamp shows the filler's copy. H7 extended (`h7_syrup`), 8 mutations caught, offline 8/8, import round trip identical; browser: clicks on the block texts open the syrup screen. Not live: after `docker compose up -d` both runtimes had the Modbus server OFF (D2) – upload both, then `buffer_starved` on the Line view. |
| 2026-11-13 | FUXA S3, syrup room screen: process (3 tanks, pipes, lamps), batch values, 10 setpoints, manual buttons. °C / bar shown and typed in FUXA through the tag's expression scale with `Math.round` (the Modbus `divisor` cuts 0.29 bar to 28). H11 + scale checks in H1 / H2 / H3, 7 mutations caught. Browser: setpoint 4.5 °C → 45, hold-to-run with and without interlock, `cooling_failure` 10/10 and `over_pressure` followed on the screen. Found: a simulator restart in the middle of a batch leaves `Batch_Dosed` set with an empty tank (cannot finish); recovered with Manual water pulses, then Auto + Start. Rule: let the batch end and Stop before restarting the simulator. |
| 2026-11-13 | FUXA S2, header for two machines: machine chips (state, step, mode incl. "Stop – press Auto") for both machines, E-stop chip on both PLCs, a link chip per PLC; row 2 = the machine of the view; Line / Alarms / Trends once per machine (the picker opens the sibling view, chosen over a shared FUXA server tag). H5 rewritten (incl. a layout check), 6 mutations caught. Browser 10/10 with the syrup PLC: Auto / Reset / Start / Stop from FUXA, E-stop red / OK. The filler runtime did not answer (SAFE MODE after a suspend): its part of the live checks waits for an upload. |
