# Labeler (`plc-labeler`) – Progress

Labels bottles: a roll-fed hot-melt labeler, the bottle spins on a turntable. No inspection and no reject (decision 2026-11-15). Third machine of the line, the first stretch goal after scope option B (`docs/scope-options.md`, report section 11 step 4).
Design reference: `docs/project-report.md` sections 4.4 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms).
Labeler docs: `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (stages 0–7 of the PLC program).

**Current step:** paper done (2026-11-15: I/O list, state diagram, ladder roadmap, report 4.4 / 6.2 / 11, `CLAUDE.md`); next: ladder stage 0 (container `plc-labeler`, Editor project `plcs/labeler/`, variables, `test_labeler_stage0.py`).

## Why the labeler next

- Report order (section 11 step 4): it adds a **heated glue tank** and the **first real conveyor link** (C2 between two PLCs: the filler sees S7, the labeler drives the motor).
- New syllabus angles: **CTU counters** (labelled bottles with an operator reset, turns of the bottle with a reset per step), **two TP pulses** (gate, knife), a **window compare** and **heating hysteresis** on the glue temperature.
- Reuses everything: modes/E-stop section, heartbeat, jog watchdog, `FB_Motor`/`FB_Valve`, the filler's door (TP) and `Outfeed_Full` filter, register layout, simulator framework, FUXA generator.

## Decisions

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
- [ ] Stage 0 – Third runtime container `plc-labeler` (Modbus 5023, editor 127.0.0.3:8443), project `plcs/labeler/`, `mx_bits` = 64, variables, FBs copied
- [ ] Stage 1 – Modes and safety (copied from the syrup room), defaults, `Cmd = 4` decode, heartbeat
- [ ] Stage 2 – Glue temperature: `Glue_Ready` window, `Heat_Demand` hysteresis
- [ ] Stage 3 – Conditioning: `Outfeed_Full` (`C3_Full`), Starved filter timer, `Label_Mark` edge
- [ ] Stage 4 – Sequencer skeleton: steps 0–40, 900, `Spin_Counter`
- [ ] Stage 5 – Outputs: jog watchdog, gate + knife TP, `Bottle_Entering`, Auto OR Manual, interlocks, heater, FB instances
- [ ] Stage 6 – Stop at safe points, `GoodCount` with `Cmd = 4`, `Starved`, `State`
- [ ] Stage 7 – Alarms and FAULT

### 2. Python simulator – stages in `docs/simulator/roadmap.md`
- [ ] Not planned yet (C3 conveyor, packer stand-in, `machines/labeler.py` with label roll, glue tank temperature, bell and turntable, fault injection (torn web, stuck bell, heater failure), `LabelerPlc` on 5023)

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
