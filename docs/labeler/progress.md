# Labeler (`plc-labeler`) – Progress

Labels bottles: a roll-fed hot-melt labeler, the bottle spins on a turntable. No inspection and no reject (decision 2026-11-15). Third machine of the line, the first stretch goal after scope option B (`docs/scope-options.md`, report section 11 step 4).
Design reference: `docs/project-report.md` sections 4.4 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms).
Labeler docs: `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (stages 0–7 of the PLC program).

**Current step:** ladder stages 0–7 done, the labeler PLC program is complete (stages 0–2 2026-11-16, stages 3–4 2026-11-17, stages 5–7 2026-11-18, uploaded; `run_labeler.py` 172/172 in 7.3 min: stage 0 5/5, stage 1 8/8, glue 21/21, conditioning 6/6, sequencer 26/26, outputs 50/50, stop / state 28/28, alarms 28/28). **Next:** the loose ends below, then plan the labeler simulator (section 2) and the HMI (section 3).

Loose ends (2026-11-16):
- **Filler and syrup room containers are missing** (only `plc-labeler` runs; volumes still there, probably removed by a `docker compose down`). Fix: `docker compose up -d`, upload both programs again from the Editor (127.0.0.1, 127.0.0.2: a re-created container has the Modbus server off), `demo_check.py`. Then re-run `test_labeler_stage0.py`: its isolation checks L5 / L6 were SKIPPED so far.
- Debugger check of stage 1 not reported yet: force `E_Stop_OK` FALSE → `Master_Enable` FALSE, release all forces.
- The Editor keeps the project in memory: close the project before Claude edits `plcs/labeler/` files, reopen after.
- 12 unused variables left over from Editor experiments (`TP0`, `TP1`, `Bottle_Ent`, `FB_MOTOR0`–`FB_MOTOR8`): referenced by no rung, remove when convenient.
- Optional: the 9 FB instances sit 3 per rung (60–62, chained EN → ENO); the syrup room has one per rung, easier to comment and explain. Works as it is.

## Why the labeler next

- Report order (section 11 step 4): it adds a **heated glue tank** and the **first real conveyor link** (C2 between two PLCs: the filler sees S7, the labeler drives the motor).
- New syllabus angles: **CTU counters** (labelled bottles with an operator reset, turns of the bottle with a reset per step), **two TP pulses** (gate, knife), a **window compare** and **heating hysteresis** on the glue temperature.
- Reuses everything: modes/E-stop section, heartbeat, jog watchdog, `FB_Motor`/`FB_Valve`, the filler's door (TP) and `Outfeed_Full` filter, register layout, simulator framework, FUXA generator.

## Decisions

- 2026-11-18: **DISCHARGE jam only while C3 is free**: `Out_Timer IN := Step_Discharge AND NOT Outfeed_Full`. C3 filling up during DISCHARGE stops the pusher by its interlock; that is Blocked (waiting is not a fault), not a pocket jam. A bell stuck down still raises the jam.
- 2026-11-18: **Reset in FAULT clears `Bottle_Entering`** (rung Entry tracking 3/3): after a gate jam the operator may take the stuck bottle away instead of pushing it into the pocket; `Bottle_In_Pocket` then never rises, `Entry_Timer` would keep running and `Alm_PocketJam` could never be reset.
- 2026-11-18: **glue over-temperature is a FAULT in every mode** (also Stop / Manual: the glue burns anyway), Step 900 from any step; leaving FAULT needs Auto + Reset (transition 900 → 0), as the syrup room's overflow.
- 2026-11-17: **10 → 20 needs `Glue_Ready`**: a bottle that reached the pocket while the glue left the window waits in WAIT_BOTTLE until the heater is back, instead of starting WRAP with feed and glue roller blocked and ending in a label timeout FAULT (`state-diagram.md` step table and rule 4).
- 2026-11-15: **one station** (gate → one pocket → C3), not an indexed two-station machine and not a continuous flow with a shift register. Simplest ladder, one step at a time (report 4.4 sequence).
- 2026-11-15: **no inspection, no reject.** The fill level window and the reject (pusher, chute, reject bin) were removed from the labeler and noted as future enhancements of the filler (`docs/filler/progress.md`); with no reject path, the label / cap inspection, `RejectCount`, `RejectRate` and `Wrap_Open` went too; rejects-in-a-row dropped entirely. Every labelled bottle goes to C3. `Label_Present` stays only for the restart (a labelled bottle is not labelled twice). Known limitation: a label broken off by an E-stop leaves on C3, the operator removes the bottle (`state-diagram.md` rule 7).
- 2026-11-15: **descriptive sensor names** (`Bottle_At_Gate`, `Bell_Down`, `Label_Mark`, …), not line-wide numbers.
- 2026-11-15: one bell sensor (`Bell_Down`, up = NOT down); roll low / glue low are FUXA warnings straight from the sensor bits; the operator zeroes `GoodCount` with `Cmd = 4` (separate from Reset); Starved filtered with a 2 s TON on `NOT Bottle_At_Gate`; animation words decided at the HMI step.
- 2026-11-15: until the packer exists, a **packer stand-in** drives a new conveyor **C3** and takes bottles off its end; the labeler gets a real C3 full sensor (`C3_Full`) with the 2 s / 3 s filter.
- 2026-11-15: **roll-fed hot-melt labeler** (as seen on a real machine): the operator loads a label roll; feed rollers pull the web, a registration mark (`Label_Mark`) triggers the knife (TP); a heated melt tank (glue blocks added by the operator) feeds a glue roller; the bottle **spins on a turntable** so the label wraps around it.
- 2026-11-15: bottle held by a **centering bell** (`ClampValve`, `Bell_Down`) while it spins: steps CLAMP → WRAP → PRESS → UNCLAMP, interlock "spin only with the bell down".
- 2026-11-15: **glue heater on in every mode** while `Master_Enable` and no FAULT (hysteresis `Heat_Demand`, window `Glue_Ready`, `GlueMax`), like the blow moulder's oven; step WARM_UP after Start.
- 2026-11-15: end of the wrap = **turns counted with a CTU** on the spin pulse `Spin_Pulse` (`PressTurns` setpoint), not a timer.
- 2026-11-16: **addresses in build order**: the filler keeps 127.0.0.1 / 502 (no move to 127.0.0.3 / 5023), the labeler takes **127.0.0.3 / 5023**, packer 127.0.0.4 / 5024, blow moulder 127.0.0.5 / 5025 (report 6.1).
- 2026-11-15: 10 digital sensors → the two extra ones (`Roll_Low`, `Glue_Low`) in **`%MX4`** (free in the standard layout); 9 outputs `%QX0.0–1.0`; 7 alarm bits in `%MX5` (`%MX5.7` spare).

## Order of work and why

Same as the other machines: **paper → ladder → simulator → HMI**. Addresses and steps first, because ladder, simulator and FUXA all depend on them.

## Checklist

### 0. Paper
- [x] I/O list draft (`io-list.md`, 2026-11-15)
- [x] State diagram draft (`state-diagram.md`, 2026-11-15)
- [x] Review both drafts, answer the open questions (2026-11-15, list at the end of `io-list.md`)
- [x] Ladder roadmap (`ladder-roadmap.md`, stages 0–7, 2026-11-15)
- [x] Report updated (2026-11-15): 4 table + 4.4 (new design), 6.2 (`%MX4` sensor byte, `Cmd = 4`, labeler counter), 8.1 (labeler screen), 11 (syrup room done, labeler in progress); `CLAUDE.md`

### 1. Ladder logic (OpenPLC Editor v4) – stages in `ladder-roadmap.md`
- [x] Stage 0 – (2026-11-16; L5 / L6 isolation checks still to run with the other PLCs up) Third runtime container `plc-labeler` (Modbus 5023, editor 127.0.0.3:8443), project `plcs/labeler/`, `mx_bits` = 64, variables, FBs copied
- [x] Stage 1 – Modes and safety (copied from the syrup room), defaults, `Cmd = 4` decode, heartbeat (2026-11-16, 8/8)
- [x] Stage 2 – Glue temperature: `Glue_Ready` window, `Heat_Demand` hysteresis (2026-11-16, 21/21)
- [x] Stage 3 – Conditioning: `Outfeed_Full` (`C3_Full`), Starved filter timer, `Label_Mark` edge (2026-11-17, `test_labeler_conditioning.py` 6/6; `Gate_Empty_Timer` debugger only, `Mark_Edge` tested in stage 4 via WRAP 22 → 24)
- [x] Stage 4 – Sequencer skeleton: steps 0–40, 900, `Spin_Counter` (2026-11-17, rungs 21–42, `test_labeler_sequencer.py` 26/26)
- [x] Stage 5 – Outputs: jog watchdog, gate + knife TP, `Bottle_Entering`, Auto OR Manual, interlocks, heater, FB instances (2026-11-18, rungs 43–62, `test_labeler_outputs.py` 50/50)
- [x] Stage 6 – Stop at safe points, `GoodCount` with `Cmd = 4`, `Starved`, `State` (2026-11-18, rungs 29–32 + 67–73, `test_labeler_stop_state.py` 28/28)
- [x] Stage 7 – Alarms and FAULT (2026-11-18, rung 55 + section 9 rungs 75–94, `test_labeler_alarms.py` 28/28)

### 2. Python simulator – stages in `docs/simulator/roadmap.md`
- [ ] Planned 2026-11-18 (`docs/simulator/roadmap.md`, section "Labeler simulator", stages 0–8); not started

### 3. HMI (FUXA) – stages in `docs/hmi/roadmap.md`
- [ ] Not planned yet

## Open questions

None open; the answers are listed at the end of `io-list.md`.

## Log

- 2026-11-15: labeler started; layout, inspection checks and C3 stand-in decided; I/O list and state diagram drafted.
- 2026-11-15: second draft: label roll + registration mark + knife, hot-melt tank with heater, centering bell, turntable spin with turn counter; `Wrap_Open` bit rejects a bottle whose label was broken off by an E-stop / FAULT.
- 2026-11-15: third draft: open questions answered; fill window, reject bin and rejects-in-a-row removed; descriptive sensor names; `Cmd = 4` counter reset; Starved filter.
- 2026-11-15: ladder roadmap written (stages 0–7); gate fix: `Bottle_Entering` stops a second gate pulse while a bottle travels from the gate to the pocket (same idea as the filler's `Entry_Load`); report and `CLAUDE.md` updated.
- 2026-11-15: fourth draft: reject removed (pusher, chute, `Reject_Confirmed`, `Man_Reject`), so inspection (`Cap_Present`, `Bottle_Good`, step INSPECT), `RejectCount`, `RejectRate` and `Wrap_Open` removed too; steps 30 / 50 gone; addresses repacked (sensors `%MX0.0–0.7` + `%MX4.0–4.1`, `Bottle_Entering` `%MX3.1` as the filler's `Entry_Load`); report, ladder roadmap and filler future enhancements updated.
- 2026-11-16: addresses in build order (labeler 127.0.0.3 / 5023, report 6.1). Stage 0 started: service `plc-labeler` + volumes in `docker-compose.yml`, container healthy (EMPTY, no program yet); Editor project `plcs/labeler/` created, device and Modbus server configured and checked against `plcs/syrup/`; `main` still empty.
- 2026-11-16: stage 0 done: 60 variables from `io-list.md` + 9 constants (`GatePulse` … `BlockedWarn`) declared in `main` (setpoints with their defaults as initial values), `FB_Motor` / `FB_Valve` copied unchanged from `plcs/syrup/`, test rung `E_Stop_OK → ConveyorMotor2` uploaded (program scans), then deleted. `test_labeler_stage0.py` (L1–L7): 5/5, L5 / L6 skipped (filler / syrup room down; the script skips instead of failing when another PLC is not up).
- 2026-11-16: stage 1 done: 16 internal variables (`Def_*`, `Init_Done`, mode / command bits incl. `Count_Reset_Cmd`, `Master_Enable`, heartbeat); rungs 0–9 as the syrup room's section 1 (one MOVE rung for the 4 defaults, 4 EQ for `Cmd` 1–4, `Master_Enable` after the watchdog rungs). Bug found by the new test B8: rung 7 had `NOT Heartbeat` instead of `NOT Hb_Changed` in front of `Hb_Timer`, so a heartbeat frozen at TRUE never raised `Alm_CommLost`; fixed, `test_labeler_stage1.py` (B1–B8) 8/8.
- 2026-11-16: stage 2 done: rungs 10–14 (header; SUB / ADD / SUB thresholds `Glue_Low_Lim`, `Glue_High_Lim`, `Heat_On_Lim`; GE / LE / LT / LT compares into 4 helper BOOLs, because a compare block's ENO only copies EN; `Glue_Ready` = two contacts in series; `Heat_Demand` seal-in). `test_labeler_glue.py` (G1–G4) 21/21: window edges 1600 / 1800 inclusive, set strictly below 1650, held in the band, reset at 1700, both bits follow `GlueBand` / `GlueHyst`. Roadmap test value fixed: with `GlueBand` 50 the window is 1650–1750, so 1660 is inside (now 1640 F, 1750 T, 1760 F). Comments added to every rung of section 1; unused `TON0` removed. New `run_labeler.py` runs all labeler scripts (34/34).
- 2026-11-17: stage 4 rungs 21–42 built and uploaded. File review found rungs 36 (22 → 24) and 37 (24 → 26) gated by `Step_Clamp`: WRAP could never end, and a `Label_Mark` during CLAMP would skip WRAP; fixed to `Step_Wrap` / `Step_Press`. Rung 31 (10 → 20) got a `Glue_Ready` contact (decision above).
- 2026-11-17: stage 4 done: re-uploaded, new `test_labeler_sequencer.py` (Q1–Q26) 26/26: one bottle through every step, `Label_Mark` in CLAMP ignored (the 36 / 37 bug), spin pulses in WRAP not counted, `PressTurns` 3, C3 full → 35 from UNCLAMP and from a restart, labelled bottle never through CLAMP, cold glue waits in 10 (state set by writing `Step` 10 in IDLE: not reachable one sensor write at a time), abort by E-stop and Mode Stop, FAULT kept in Stop and left by Auto + Reset. Script fix on the first run: wait 1.5 s of heartbeat before Reset, else `Alm_CommLost` stays latched. `run_labeler.py` 66/66 in 1.0 min.
- 2026-11-17: stage 5 rungs 43–57 built (section 7: jog watchdog, step groups, gate and knife TP, `Bottle_Entering`, request rungs C2 / Clamp / Spin / Feed / Discharge).
- 2026-11-18: stage 5 finished: rungs 58–59 heater (`LT GlueTemp, GlueMax` into the helper `Glue_BelowMax`, because a compare block's ENO only copies EN; `Req_Heater := Heat_Demand AND NOT Step_Fault AND Glue_BelowMax`, no mode contact) and rungs 60–62 with the 9 FB instances (`Enable` = `Master_Enable`, one writer per `%QX`). File review (rungs converted to boolean expressions) found 4 bugs before the first test: (1) `Req_Clamp`, `Req_Spin`, `Req_Feed`, `Req_Discharge` had the Auto contact NC instead of NO (copied from the C2 rung, whose first contact really is `NOT Mode_Manual`), so each output ran in every step except its own; (2) the knife TP instance was named `CutPulse` (the TIME constant) instead of `Cut_TP`; (3) `Clamp_Steps` read `Step_Wrap OR Spin_Steps`, so CLAMP never lowered the bell; (4) the `Enable` pins of all 9 FBs were empty boxes (FALSE: no output could ever switch on), the first fix was not saved in the Editor; plus the spelling `GLue_Ready` (case-insensitive, harmless). All fixed; rungs 0–42 unchanged.
- 2026-11-18: new `test_labeler_outputs.py` (H1–H5, A1–A14, G1–G5, K1–K2, E1–E3, F1, M1–M16, J1–J3) 50/50 on the first run: heater in Stop (on at 150.0, off at 170.0, off at `GlueMax` 200.0 with `Heat_Demand` still TRUE via setpoint 210.0), every step's output set, one gate pulse of 0.49 s with `Bottle_At_Gate` held, no pulse with `Roll_Empty` or while `Bottle_Entering`, one knife pulse of 0.20 s, interlocks (bell, `Glue_Ready`, `Outfeed_Full`, pocket), E-stop and FAULT all off (heater too), Manual hold-to-run with every interlock, jog watchdog. Added to `run_labeler.py`: 116/116 in 1.7 min.
- 2026-11-18: comments written into `main.ld` for rungs 45–46 (step groups), 54–57 (request rungs, replacing the copied C2 comment) and 58–62 (heater, FB instances); only the comment lines changed, rung logic identical.
- 2026-11-18: stage 6 written into `main.ld` by a script (Editor closed), at the user's request: rungs of the same shape cloned (the syrup room's Stop and State rungs, the labeler's own CTU and parallel rungs) with new ids and renamed variables, contacts inserted the way the Editor does. Section 6: `Stop_Request` seal-in (rung 29) and three Stop → IDLE rungs (30 WARM_UP / WAIT_BOTTLE, 31 WAIT_OUTFEED, 32 end of UNCLAMP / end of DISCHARGE), no Stop in CLAMP / WRAP / PRESS; `NOT Stop_Request` added to the 9 transitions out of those steps (5→10, 10→20/40/35/5, 26→40/35, 35→40, 40→10), so a normal transition lower in the scan cannot overwrite the Stop's MOVE 0. New section 8: `Good_Counter` (CTU, CU = `Step_Discharge AND NOT Bottle_In_Pocket`, R = `Count_Reset_Cmd`, PV 32767, CV → `GoodCount`), `Starved`, five State MOVE rungs (0 / 1 / 2 / 3 / 9). New declarations `Stop_Request`, `Good_Counter`. Checked before the upload: rungs as boolean expressions, only the intended changes, ids unique, no dangling wires; the Editor re-saved it with identical logic.
- 2026-11-18: new `test_labeler_stop_state.py` (S1–S12, C1–C3, T1–T11) 28/28 on the first run: Stop in CLAMP / WRAP finishes the label and ends in IDLE at the end of UNCLAMP (never DISCHARGE), Start discharges the labelled bottle (never CLAMP again), Stop at once in WARM_UP / WAIT_BOTTLE / WAIT_OUTFEED, Stop in DISCHARGE counts the bottle and ends in IDLE, Stop in IDLE not remembered; GoodCount +2 for two bottles, Reset keeps it, `Cmd = 4` zeroes it; State 0 / 1 / 2 (gate gap 3 s, not 1 s; empty roll, gate shut) / 3 / 9, Manual and Stop mode 0. `run_labeler.py` 144/144 in 2.3 min.
- 2026-11-18: stage 7 planned, the roadmap design reviewed (two improvements, decisions above), then generated into `main.ld` with the stage 6 method (clone script, Editor closed): rung 55 Entry tracking 3/3 (`Reset_Cmd AND Step_Fault → (R) Bottle_Entering`) and section 9 ALARMS, rungs 75–94: `Wrap_Timer` / `Press_Timer` → `Alm_LabelTimeout`; `Entry_Timer`, `Clamp_Timer`, `Unclamp_Timer`, `Out_Timer` → `Alm_PocketJam`; `Blocked_Timer` → `Alm_OutfeedBlocked` (warning); `Alm_LabelEmpty` (warning, not latched); `Alm_GlueOverTemp` from `Glue_BelowMax`; `Heat_Timer` → `Alm_GlueHeatTimeout`; each latch reset only by `Reset_Cmd AND NOT cause`; `No_Fault` (De Morgan) and `MOVE 900 → Step` as the last rung. 9 new declarations (8 TON, `No_Fault`). Checked before the upload as stage 6 (only the intended changes, ids unique, no dangling wires); the Editor re-saved it with identical logic.
- 2026-11-18: earlier scripts adapted, because some held a step longer than its new timeout: sequencer Q14 sets `C3_Full` early (UNCLAMP < 2 s); outputs H5 (`GlueTemp` 2000 is now a FAULT), gate block G2–G5 within 2 s of the pulse, DISCHARGE block sets `C3_Full` right after A9 (`Out_Timer` stops before 3 s); stop / state T3–T6 start the gate gap in WARM_UP (a bottle at the gate in WAIT_BOTTLE is gated at once). New `test_labeler_alarms.py` (N1, L1–L5, J1–J9, G1–G5, E1–E3, B1–B3, H1–H2; `--quick` without the 2 min and 120 s timeouts): first full run 27/28 – L2 read the outputs in the scan in which the last rung wrote 900 (the outputs follow one scan later, as designed), fixed by waiting a few scans as G1 does. `run_labeler.py` 172/172 in 7.3 min.
