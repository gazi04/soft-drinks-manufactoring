# Blow moulder (`plc-blowmolder`) – Progress

Turns PET **preforms** into empty bottles: an infrared **oven** heats one preform while the **mould** stretches and blows the one before. First machine of the line, the fifth and last PLC (third stretch goal after scope option B, `docs/scope-options.md`, report section 11 step 6). It puts the bottles onto C1, which the filler drives.
Design reference: `docs/project-report.md` sections 4.1 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms).
Blow moulder docs (planned): `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (part A: stages of the PLC program).

**Current step:** `io-list.md` reviewed (2026-11-25); `state-diagram.md` reviewed (2026-11-25, answers 1–5 as proposed); `ladder-roadmap.md` drafted (2026-11-25, stages 0–9). **Ladder stage 0 done (2026-11-25):** container up, program uploaded, `test_blowmoulder_stage0.py` 13/13 (`%QW0` = HR 0 verified); test rung deleted. **Ladder stage 1 done (2026-11-25):** `test_blowmoulder_stage1.py` passed, debugger `E_Stop_OK` FALSE → `Master_Enable` FALSE. **Ladder stage 2 done (2026-11-25):** `test_blowmoulder_values.py` 39/39 on the PLC, debugger checks `Oven_BelowMax` (OvenTemp 1299 / 1300) and `Press_Range_OK` (raw 32511 / 32512) done. **Ladder stage 3 done (2026-11-25):** `test_blowmoulder_conditioning.py` 8/8. **Ladder stage 4 done (2026-11-25):** `test_blowmoulder_oven_seq.py` 18/18 on the PLC. **Ladder stage 5 done (2026-11-25):** `test_blowmoulder_mould_seq.py` 29/29 on the PLC. **Ladder stage 6 done (2026-11-25):** `test_blowmoulder_lamps.py` 14/14 on the PLC. Next: stage 7 (outputs and interlocks).

## Work in three parts

As the packer: planned and built one after the other; each part gets its own planning step when it starts, so a later part is never designed before the earlier one has fixed its addresses.

| Part | Content | Planned in | Gate |
| --- | --- | --- | --- |
| **A. PLC (ladder)** | Paper (I/O list, state diagram, ladder roadmap), ladder stages, Modbus test scripts + `run_blowmoulder.py`; runtime `plc-blowmolder`, Modbus 5025, editor 127.0.0.5:8443, project `plcs/blowmoulder/` | `ladder-roadmap.md` | paper reviewed before the first rung |
| **B. Simulator** | Address map, preform hopper / oven / mould physics, replaces `Supply` on C1, `BlowmoulderPlc` link, closed loop with five PLCs, commands + scenarios, panel tab 5 | `docs/simulator/roadmap.md`, new section "Blow moulder simulator" | starts when part A is complete |
| **C. HMI (FUXA)** | Device + tags, header for five machines, Blow moulder screen, real block on the Line view instead of the grey stand-in, alarms, trends (oven temperature, lamp power, blow pressure) | `docs/hmi/roadmap.md`, new section "Blow moulder screens" | plan → user OK → build |

## Concept

Top view, preforms and bottles move left to right:

```
  HOPPER            LOADER          OVEN (IR lamps)            TRANSFER          MOULD                       C1 (filler drives the motor)
 ┌───────┐ chute   ┌─────┐  load   ┌───────────────────┐ grip  ┌──────┐  put  ┌─────────────────┐ eject   ─► [B][B][B][B][B] ─► filler
 │ ▼ ▼ ▼ │ ──────► │  ▼  │ ──────► │  ▼  lamps ≈≈≈≈≈   │ ────► │ arm  │ ────► │ ┃ rod ↓  ▼ blow │ ──────►   ▲ S1 = C1 full
 │ ▼ ▼ ▼ │         └─────┘         │  pocket  OvenTemp │       └──────┘       │ mould halves    │            (wired to the blow moulder,
 └───────┘          preform at      └───────────────────┘                      │ BlowPressure    │             filtered 2 s / 3 s)
  low / empty       the loader       lamp power 0–100 %                         └─────────────────┘
```

1. **Load.** A preform from the hopper waits at the loader (sensor). When the oven pocket is empty, the loader puts it in.
2. **Heat (oven station).** The preform sits in the oven pocket for a heating time (TON) while the oven is in its ready window. The **IR lamp power is an analog output 0–100 %** set by a **P controller**: `Power = LIMIT(0, Kp · (OvenSetpoint − OvenTemp), 100)`, the first analog output of the project. Over-temperature cuts the power in every mode.
3. **Transfer.** When the preform is hot **and** the mould is open and empty, the transfer arm carries it from the oven pocket into the mould. The oven pocket is free again and loads the next preform at once (**the two stations work in parallel**).
4. **Blow (mould station).** Close the mould → stretch rod down with **pre-blow** (low pressure) → **high blow** until the pressure reaches its setpoint, held by a TON → **exhaust** until the pressure is below a safe limit → rod up → open the mould. Blow pressure comes from a transducer as **raw counts 0–27648** and the PLC **scales** them to 0.1 bar (SUB / MUL / DIV, as a real 4–20 mA input card).
5. **Eject.** The ejector pushes the bottle onto the entry of C1. S1 (C1 full) is wired to this PLC: filtered and latched (2 s on / 3 s off, report 5.3) it stops the loading of new preforms; the bottle in the mould and the preform in the oven are finished first, then the machine is **Blocked** with the oven still hot.

Timing target: one bottle every ~6 s (mould cycle ~5.5 s, oven dwell ~5 s in parallel), about the labeler's rate, so the blow moulder does not slow the line.

## Why this design

- Completes the start of the line (report section 11 step 6) and replaces the simulator's `Supply` without changing another PLC: the filler already drives C1, and S1 was always meant for this PLC (report 5.3).
- New syllabus angles, unused in the other four machines (which only switch heaters on/off and read pressures already in engineering units):
  - **analog output** with a **P controller** (week VIII math: SUB, MUL, `LIMIT`), and the P controller's steady offset as something to explain;
  - **scaling a raw analog input** (counts → 0.1 bar), as a real analog card delivers it;
  - a safety interlock with a real consequence: **the mould never opens under pressure** (exhaust first, pressure below the limit, rod up).
- Two stations in parallel (oven and mould) keep the rate realistic; the hand-over between them is one more example of a handshake inside one PLC.

## Decisions

- 2026-11-24: **oven and mould in parallel** (two stations, hand-over by the transfer arm when the preform is hot and the mould is open and empty), not one preform at a time (that would give ~12 s per bottle and make the moulder the slowest machine).
- 2026-11-24: **oven lamps: P controller with an analog output 0–100 %**, not on/off hysteresis like the labeler and packer ovens.
- 2026-11-24: **blow pressure analog, raw counts 0–27648 scaled in the PLC** to 0.1 bar; two blow stages (pre-blow during the stretch, high blow held by a TON); exhaust until below a limit before the mould may open.
- 2026-11-24: **preform hopper with an empty sensor plus a low warning** (`Preform_Low` as an early HMI warning on the sensor bit, as `Roll_Low` / `Film_Low`); "no preforms" as an alarm.
- 2026-11-25 (io-list review): `Alm_NoPreforms` a warning (Starved, clears itself); exhaust valve ON in FAULT / E-stop / comm lost; `Alm_PressureSensor` (wire break, over range) kept; manual mould close a toggle (`Man_Mould_Closed`); `LampPower` %QW0 with %MW40 as the fallback.
- 2026-11-25 (ladder stage 2): **`Lamp_Calc` located at `%MW40`** (HR 1064, the unused `LampPower` fallback): the P controller is tested over Modbus without a temporary MOVE rung, and the HMI can show demand vs. `LampPower`. `TO_INT` checked in the Editor 4.3.3 library (TypeConversion, `IN` ANY → `OUT` INT).
- 2026-11-25 (ladder stage 6 plan review, three rounds): lamps as safe default + override (MOVE 0 every scan, then `Lamps_On AND Oven_BelowMax` → MOVE `Lamp_Calc`; no OR branch, fail-safe); roadmap test value fixed (1047 is 0.3 °C below the setpoint → 3 %, the 30 % point is 1020); the `OvenMax` interlock proven with Kp 1 / Bias 100 (the controller alone still asks for 75 %); comm lost and a Modbus write to HR 0 tested.
- 2026-11-25 (ladder stage 5 plan review, three rounds): one step timer per step (`Grip_Timer`, new `Release_Timer`, `Hold_Timer`) instead of a shared `Grip_Timer` on GRIP OR RELEASE (no OR branch, no rung-order rule); timers inserted in section 5, transitions at the end of section 6; the test gets more hand-over guards (arm, mould open), measured GRIP / HOLD times, a parallel cycle and restarts through the new transitions.
- 2026-11-25 (ladder stage 4 plan review, three rounds):
  - The abort keeps FAULT (`NOT Oven_Fault`, as the packer); the roadmap's abort would have cleared 900 without Reset.
  - `Stop_Request` and `No_Fault` (initial TRUE) are declared now with their contacts in place, so stages 8 / 9 only add rungs and never edit one.
  - Start by sensors as default-then-override (last write wins: closed mould has the highest priority, no OR branch).
  - `Step_Heat` renamed `Oven_Heat`.
  - `Lamps_On` tested.
  - `Preform_Hot` checked by timing (one-scan bit).
- 2026-11-25 (ladder stage 3 plan review): stage 3 is the `Outfeed_Full` filter only. `Feed_Empty_Timer` moves to stage 8 (complete with `Oven_Feed`, above `Starved`: without the step contact it runs during HEAT and gives a false Starved the moment FEED starts), the `Mould_Click` edge to stage 7.2 (one-scan pulse, only the toggle uses it).
- 2026-11-25 (state-diagram review): C1 full → the blown bottle waits in the open mould; exhaust normally open (also IDLE / Manual); lamps only with a preform in the pocket, no WARM_UP; restart ejects whatever is in the mould (no reject); a closing mould opens again on E-stop, a closed one stays closed.
- 2026-11-24: names: service `plc-blowmolder` (report 6.1), folders `docs/blowmoulder/`, `plcs/blowmoulder/`, simulator `machines/blowmoulder.py` (British spelling as in the report's text).

## Open questions

None open. Answers (2026-11-24, all as proposed):

- **Q1 – analog output:** `LampPower` at **`%QW0`**, the real analog output image; its Modbus offset in OpenPLC v4 is verified in stage 0 like `%MW` was (fallback: computed word `%MW40`).
- **Q2 – P plus bias:** `Power = LIMIT(0, Bias + Kp · (OvenSetpoint − OvenTemp), 100)`; `Bias` and `Kp` are setpoints. With `Bias` = 0 the P offset shows on the trend, a bias removes it.
- **Q3 – oven temperature = preform surface temperature** at the oven pocket (IR pyrometer): setpoint ~105 °C, ready window ± a band, `OvenMax` ~130 °C (PET crystallises above).
- **Q4 – preform waiting in the oven:** lamps stay on, the preform stays in the pocket and is blown when the machine continues; the simulator warns if it was overheated. No reject flap.
- **Q5 – Stop:** finish the bottle in the mould and the preform in the oven, then IDLE; no new preform after Stop.
- **Q6 – counter:** `BottleCount` %MW53 (ejected bottles), `Cmd = 4` resets it, as labeler and packer.

## Log

- 2026-11-24: started. Report 4.1 read; concept chosen with four answers (oven + mould in parallel, P controller with an analog output, analog blow pressure with scaling, hopper empty + low sensors). Open questions Q1–Q6 written and answered as proposed (`%QW0`, P + bias, preform surface temperature, preform stays in the oven).
- 2026-11-25: `io-list.md` drafted: 13 digital sensors (`%MX0`, `%MX4.0–4.4`), `OvenTemp` %MW12 + `BlowPressure_Raw` %MW13 (counts), 5 setpoints %MW30–34 (safety limits as constants), 7 manual buttons (none for blow valves or lamps), 10 BOOL outputs + `LampPower` %QW0, `Step` / `MouldStep` %MW51–52, `BottleCount` %MW53, 11 alarms (`%MX5`, `%MX7.0–7.2`), 8 conditioned bits, `BlowPressure` scaled in DINT %MW41, `OvenError` %MW42. Five review questions in the file.
- 2026-11-25: io-list reviewed, answers 1–5 built in; a closed mould stays closed in FAULT / E-stop / comm lost (to settle in the state diagram).
- 2026-11-25: `state-diagram.md` drafted: oven station `Step` 0/10 FEED/15 LOAD/20 HEAT/25 HOT/900, mould station `MouldStep` 0/10/20–26 transfer/30 CLOSE/40 STRETCH/50 BLOW/52 HOLD/60 EXHAUST/62 ROD_UP/70 OPEN/80 EJECT/900; hand-over at `Step = 25`; exhaust normally open; mould as a seal-in that opens only with `Pressure_Safe AND Rod_Up`; P controller with `LIMIT` on the error (INT overflow); pressure scaled in DINT; C1 full → bottle waits in the open mould; ~5.4 s per bottle. io-list rows `Starved`, `Lamps_On`, `ExhaustValve` aligned. Five review questions in the file.
- 2026-11-25: state diagram reviewed, answers 1–5 as proposed.
- 2026-11-25: `ladder-roadmap.md` drafted, stages 0–9: 0 setup + `%QW0` = HR 0 check, 1 modes, 2 analog values (window, P term with `LIMIT` before `MUL`, pressure in DINT with `TO_DINT` / `TO_INT`), 3 conditioning, 4 oven station, 5 mould station, 6 lamps on `%QW0`, 7 outputs (exhaust and mould as plain coils outside `Master_Enable`, mould toggle reloaded on Manual entry and E-stop), 8 Stop / counter / State, 9 eleven alarms. Step bits 30 / 70 named `Mould_Closing` / `Mould_Opening` (no clash with the sensors).
- 2026-11-25: stage 0 files (Editor not running): `docker-compose.yml` service `plc-blowmolder` (5025, 127.0.0.5:8443, volumes `blowmoulder-data` / `blowmoulder-build`); project `plcs/blowmoulder/` (device files and `FB_Motor` / `FB_Valve` copied from the packer, IP 127.0.0.5, `mx_bits` 64, `qwCount` 1024); `main.ld` by a script: 67 located variables from `io-list.md` (no duplicate name or address), 18 constants (`RawFull` / `PressFull` as DINT), one test rung `E_Stop_OK → MOVE 42 → LampPower` cloned from the packer's Start rung with fresh ids. `test_blowmoulder_stage0.py` (B1–B13, %QW0 check B7/B8, four PLCs isolation with SKIP) and `run_blowmoulder.py`.
- 2026-11-25: stage 0 uploaded, `test_blowmoulder_stage0.py` 13/13 incl. B9–B12 (all four other PLCs unaffected): **`%QW0` = holding register 0** in OpenPLC v4, `%MW40` fallback dropped. Script changed: B7 = HR 0 readable, the rung check is now B8 with `--qw0` (SKIP otherwise), so the script still passes after the test rung is deleted.
- 2026-11-25: stage 0 test rung deleted and section 1 written into `main.ld` by a script (Editor closed): 12 rungs – header, First scan 1/2 (`NOT Init_Done` → MOVE `Def_BlowSetpoint`, the fifth default; shape of the old test rung), First scan 2/2 (the packer's MOVE chain renamed to `OvenSetpoint` / `OvenBand` / `Kp` / `Bias`, then `(S) Init_Done`), mode decode, Cmd decode 1–4, Cmd clear, heartbeat watchdog 1/4–4/4, `Master_Enable`, new `Manual_Entry` edge (P contact on `Mode_Manual`, the packer's edge rung renamed); fresh ids, every name declared, no dangling edge. 18 internals (`Def_*` 1050 / 50 / 10 / 0 / 350, `Init_Done`, mode / cmd bits, `Master_Enable`, `Hb_Last`, `Hb_Changed`, `Hb_Timer`, `Manual_Entry`). `test_blowmoulder_stage1.py` (B1–B8, from the packer's) in `run_blowmoulder.py`.
- 2026-11-25: stage 1 uploaded, `test_blowmoulder_stage1.py` passed, debugger check `E_Stop_OK` FALSE → `Master_Enable` FALSE done.
- 2026-11-25: stage 2 prepared: generator for section 2 (10 rungs: header, window edges SUB / ADD, compares GE / LE / LT → `Oven_AboveLow` / `Oven_BelowHigh` / `Oven_BelowMax`, `Oven_Ready` AND rung, P controller 1/2 SUB → `OvenError`, LIMIT → `Err_Lim`, MUL → `P_x10`, P controller 2/2 DIV 10, ADD `Bias`, LIMIT 0..100 → `Lamp_Calc`, scaling TO_DINT → MUL `PressFull` → DIV `RawFull` → TO_INT → `BlowPressure`, pressure compares → `Raw_AboveMin` / `Raw_BelowMax` / `Pressure_Reached` / `Press_Low`, `Press_Range_OK` and `Pressure_Safe` AND rungs), 17 internals; block boxes cloned from the packer (SUB, ADD, GE, LE, LT, LIMIT, DIV) and the syrup room (MUL, TO_DINT; TO_INT from TO_DINT). `test_blowmoulder_values.py` (V1–V8) in `run_blowmoulder.py`.
- 2026-11-25: first stage 2 run 14/39: the uploaded program was still stage 1 (`main.ld` not yet written while the Editor was open; the 14 passes were the checks that expect 0 / FALSE). Section 2 now written (22 rungs). Before upload, an offline ladder interpreter (scratchpad `validate_stage2.py`) ran the generated rungs against the test's own CHECKS: 39/39, debugger checks `Oven_BelowMax` / `Press_Range_OK` too; generated nodes have the same keys as the Editor-saved ones.
- 2026-11-25: stage 2 uploaded, `test_blowmoulder_values.py` 39/39 (oven window, OvenError, Lamp_Calc incl. Kp 30 without overflow, DINT scaling incl. -28 at raw -2000, Pressure_Reached, Pressure_Safe incl. wire break). `TO_INT` compiles in Editor 4.3.3.
- 2026-11-25: stage 2 debugger checks done by the user: `Oven_BelowMax` TRUE at 1299, FALSE at 1300; `Press_Range_OK` TRUE at raw 32511, FALSE at 32512. CLAUDE.md updated (blow moulder status, `run_blowmoulder.py` and the three test scripts, `plc-blowmolder` in the running list).
- 2026-11-25: stage 3 planned in three rounds (roadmap as written → timer and edge moved to stages 8 / 7 → test F6 for the filter without `Master_Enable`) and generated: header + `Outfeed_Full` filter 1/3–3/3 cloned from the packer's rungs 17–20 (`Table_Full` → `C1_Full`), checked equal to the packer's after normalising ids; `Full_OnTimer` / `Full_OffTimer` declared. `test_blowmoulder_conditioning.py` (F0–F6) in `run_blowmoulder.py`.
- 2026-11-25: stage 3 uploaded, `test_blowmoulder_conditioning.py` 8/8 (filter 2 s on / 3 s off, blinking ignored, also with E-stop and Mode Stop).
- 2026-11-25: stage 4 generated: 25 rungs (decode of both words, `Mould_CloseSteps`, `Seq_Enable`, `Lamps_On`, `Soak_Timer` → `Preform_Hot`, abort, `Start_Accepted`, Start 1/4–4/4, transitions 10 → 15 → 20 → 25 → 10), 29 declarations; new rung builder for contacts in series → coil / MOVE chain / TON (layout from the packer's rungs). New offline check: scan interpreter of `main.ld` + fake Modbus client on a virtual clock runs the real test scripts; reproduces stages 1–3 (8/8, 39/39, 8/8), stage 4 18/18, three planted bugs each caught. `test_blowmoulder_stage1.py`: debugger IP 127.0.0.4 → 127.0.0.5 and "all 5 defaults" fixed. `test_blowmoulder_oven_seq.py` (O1–O18) in `run_blowmoulder.py`.
- 2026-11-25: docs aligned with stage 4: `state-diagram.md` (Mode Stop / E-stop keep a FAULT, abort has `NOT Oven_Fault`), `CLAUDE.md` (stage 4 written, waiting for upload).
- 2026-11-25: stage 5 generated: 16 rungs (3 step timers in section 5, 13 mould transitions), 3 TON declarations; section 5 header comment names the three timers. Offline: `test_blowmoulder_mould_seq.py` (M1–M29) 29/29 after fixing write-order races in the test (the blocking sensor must be written first); `test_blowmoulder_oven_seq.py` O11–O14 now leave 35 bar in the closed mould (else 60 → 62 during the settle time with the stage 5 transitions), passes on the stage 4 and 5 programs; four planted bugs caught. Harness: helper modules imported by a test run on the virtual clock too.
- 2026-11-25: stages 4 + 5 uploaded together (runtime restart 12:28:39), tests run on the PLC: `test_blowmoulder_oven_seq.py` 18/18, `test_blowmoulder_mould_seq.py` 29/29 (GRIP 0.31 s, HOLD 0.99 s, hand-over guards, parallel cycle 80 → 20 at once, restarts by sensors); same results as the offline check.
- 2026-11-25: stage 6 generated: section 7 header + 2 lamp rungs (a rung without contacts writes the default). `test_blowmoulder_lamps.py` (L1–L10, 14 results) offline 14/14; earlier tests unchanged; three planted bugs caught.
- 2026-11-25: stage 6 uploaded, `test_blowmoulder_lamps.py` 14/14 on the PLC (the first analog output drives: LampPower = Lamp_Calc with Lamps_On, OvenMax cut at 130.0 degC with Kp 1 / Bias 100, a Modbus write to HR 0 overwritten, 0 in every safe state).
