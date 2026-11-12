# Syrup room / mixer (`plc-syrup-carb`) – Progress

Batch mixer and carbonator with a buffer tank. Second machine of the line, after the filler (scope option B, `docs/scope-options.md`).
Design reference: `docs/project-report.md` sections 4.2 (machine), 5.1–5.2 (`Product_Available` handshake), 6.2 (register layout), 9 (modes/alarms).
Syrup docs: `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (stages for the ladder program).

**Current step:** ladder stages 0–6 done (2026-11-12). Next: ladder stage 7 (alarms and FAULT).
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
- [ ] Stage 7 – Alarms and FAULT

### 2. Python simulator
- [ ] Physics class `machines/syrup.py`: tank levels from pump flows, temperature (first-order toward coolant / room), pressure (CO2 in, dissolving out), buffer drained by the filler's `FillValve`
- [ ] Second PLC client; copy `Product_Available` (this PLC `%MX6.0`) → filler `%MX1.0` every tick, replacing the stand-in
- [ ] Fault scenarios (low syrup, cooling failure, over-pressure, buffer full, pump failure, level sensor stuck → `MixHighHigh`)
- [ ] Panel: syrup room rows (tanks, temperature, pressure)

### 3. FUXA HMI
- [ ] Device + tags from `address_map.py` (`fuxa_tags.Machine` for the second PLC)
- [ ] Syrup room detail screen: tanks with level fill, pumps/valves, temperature, pressure, recipe setpoints
- [ ] Line overview: syrup room block coloured by `State`, product pipe from `Product_Available` of the real PLC
- [ ] Alarms and trends (temperature, pressure, buffer level)

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
| 2026-11-12 | **Stop rungs like the filler:** one Stop latch, separate "-> 0 at a safe point" rungs (two step ends per rung), `NOT Stop_Request` in every normal transition (one transition per scan). `Batch_Dosed` set / reset and `BatchCount` hang on the step-end condition, not on the transition, so they also happen when a Stop ends the step. |
| 2026-11-08 | `Product_Available` depends on the buffer level only (no pressure sensor on the buffer); it stays TRUE during FAULT / E-stop of the syrup room, because the product in the buffer is still good. |

## Open questions

All answered 2026-11-09 (see Decisions). Left for later:
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
