# Filler machine – Ladder roadmap

How to build the filler PLC program in the OpenPLC Editor v4, in small stages. Each stage builds on the one before. Design: `state-diagram.md`; addresses: `io-list.md`; checklist and decisions: `progress.md`.

## Working rule for every stage

1. Build only the rungs of this stage.
2. Compile and upload.
3. **Test by forcing** in the debugger (the "Test" line of each stage).
4. **Release all forces** (a leftover force blocks Modbus writes later).
5. Run the Modbus test script for the stage (`simulator/tests/`), if there is one.
6. Tick it in `progress.md`, commit to git.

Never start the next stage with a failing test: errors pile up and become hard to find.

## Program structure

One program `main`, divided into **sections**. A section is a group of rungs with a comment rung as its title, in this order (the order matters, the PLC runs the rungs top to bottom in every scan):

```
main
 ├─ 1  Modes and safety        (what is allowed at all)
 ├─ 2  Signal conditioning     (clean up raw sensors: filters, edges)
 ├─ 3  Step decode             (Step -> Step_Idle / Step_Work / ..., Seq_Enable)
 ├─ 4  Station jobs            (rinse / fill / cap done bits, Jobs_Done, Bottles_Present)
 ├─ 5  Sequencer transitions   (abort, change Step)
 ├─ 6  Outputs                 (Auto branch OR Manual branch, with interlocks)
 ├─ 7  Counters and status     (GoodCount, State)
 └─ 8  Alarms                  (timeouts, latches, reset)
Function blocks: FB_Motor, FB_Valve
```

**Rule for the order:** a value is computed *above* every rung that uses it in the same scan. Station jobs therefore come before the transitions: if the done bits were computed below them, a bottle arriving during WORK would meet a `Jobs_Done` left over from the previous scan (empty stations = done) and the machine would index without doing the job (found by `test_station_jobs.py`, 2026-11-01). Comments refer to other rungs by name, not by number, because numbers change when rungs are moved.

Variables: located ones (`AT %…`) exactly as in `io-list.md`; helper bits and timers without an address (internal only).

---

## Stage 0 – Project setup

- New project `plcs/filler/`.
- *Device → Servers → RuntimeServer*: Modbus TCP, port 502, **`mx_bits` = 64**.
- Declare every variable from `io-list.md` (name, type, address).
- One empty test rung, compile, upload.

**Test:** `smoke_test.py`-style write of one `%MX` sensor and one `%MW` value reaches the program (debugger shows it).

## Stage 1 – Modes and safety  *(weeks IV, VI, VIII: latch, logic gates, MCR equivalent)*

- **First-scan defaults:** initial values of located variables (`AT %M…`) are ignored by the v4 runtime (the image, which starts at 0, is copied into the variables before every scan). So the setpoints get their defaults from a rung: internal BOOL `Init_Done` (no address); `NOT Init_Done` → MOVE 500 → `FillSetpoint`, MOVE 2000 → `RinseTime`, MOVE 1500 → `CapTime`, then set `Init_Done`. *(week X: data handling)*
- Decode `ModeReq` with EQ: `Mode_Auto`, `Mode_Manual`, `Mode_Stop`.
- Decode `Cmd` with EQ into one-scan pulses: `Start_Cmd`, `Stop_Cmd`, `Reset_Cmd`. The PLC sets `Cmd` back to 0 after reading it (MOVE 0), so a command acts only once.
- `Master_Enable := E_Stop_OK AND NOT Comm_Lost` – gates every output later (MCR equivalent). **Rung order:** after the watchdog's set / reset rungs, so one Reset leaves FAULT even when `Alm_CommLost` was latched too (fixed 2026-11-14, see `docs/syrup/progress.md`).
- Heartbeat watchdog: TON 3 s on "Heartbeat did not change" → `Comm_Lost`.

**Test:** after upload, `FillSetpoint` = 500, `RinseTime` = 2000, `CapTime` = 1500 (read over Modbus, HR 1054–1056). Force `E_Stop_OK` FALSE → `Master_Enable` FALSE. Write `Cmd` = 1 → `Start_Cmd` TRUE for one scan, `Cmd` back to 0.

## Stage 2 – Function blocks  *(weeks III, IX: motor control, subroutines)*

- `FB_Motor`: inputs `Run`, `Enable`; output `Out`; counts starts (CTU) and run time. Used for `ConveyerMotor1`, `IndexMotor`, `CapMotor`.
- `FB_Valve`: inputs `Open`, `Enable`; output `Out`; counts open time. Used for `RinseValve`, `FillValve`.
- One instance each, temporarily driven from a test bit.

**Test:** `Run` TRUE → `Out` TRUE; `Enable` FALSE → `Out` FALSE although `Run` TRUE; start counter +1 per start.

## Stage 3 – Signal conditioning  *(week VII: timers)*

Raw sensors say what they see *right now*; the sequencer needs facts ("C2 is full", "a new bottle arrived"). This section turns one into the other, once, above the sequencer, so every later rung uses the same clean signal in the same scan.

**Part A – `Outfeed_Full` (%MX3.0)**, time filter + seal-in (`state-diagram.md` rule 5):

```
A1  ├──┤ S7 ├──────[TON Full_OnTimer  PT=T#2s]──┤      S7 ON 2 s without a break
A2  ├──┤/ S7 ├─────[TON Full_OffTimer PT=T#3s]──┤      S7 OFF 3 s without a break
A3  ├──┬──┤ Full_OnTimer.Q ├──┬──┤/ Full_OffTimer.Q ├──( Outfeed_Full )──┤
       └──┤ Outfeed_Full   ├──┘                         seal-in = memory
```

**Part B – edges:** `┤P S6├ → ( S6_Edge )`, `┤P S8├ → ( S8_Edge )`. One-scan pulses are invisible in the debugger and over Modbus, so a TEST ONLY counter (`S6_Edge` → ADD → `%MD3`) makes them countable.

**Test** (`simulator/tests/test_signal_conditioning.py`): S7 ON 1 s → `Outfeed_Full` stays FALSE; S7 blinking (bottles flowing) 5 s → stays FALSE; S7 ON 3 s → TRUE; S7 OFF 2 s → still TRUE; OFF 4 s → FALSE. 5 pulses on S6 → edge counter +5.

## Stage 4 – Sequencer skeleton  *(weeks VIII, XI: compare, move, sequencer)*

- `Step` (%MW51) with transition rungs only (EQ + condition → MOVE), **no outputs yet**:
  0 → 10 → 15/20 → 10 …, using the conditions from the step table in `state-diagram.md`.
- Transitions only run with `Mode_Auto AND Master_Enable`.

**Test:** step through a full cycle by forcing sensors and done bits by hand; watch `Step` follow the diagram. This is the most important test: the whole machine hangs on this skeleton.

## Stage 5 – Station jobs and outputs  *(week VI: combinational logic, interlocks)*

Built in two parts, each tested before the next.

**5a – Station jobs** (section 4, rungs 28–34, above `Jobs_Done`):
- Job done bits: `Rinse_Done`, `Fill_Done`, `Cap_Done` (TON for rinse/cap, `FillLevel ≥ FillSetpoint` for fill, empty station = done).
- Setpoints `RinseTime` / `CapTime` stay `INT` (ms) at `%MW31/32`; `TO_TIME` converts them into `Rinse_PT` / `Cap_PT` (Editor v4.3.3 has no `INT_TO_TIME`; `TO_TIME` reads an integer as ms).
- `Product_Available` is **not** part of `Fill_Done`: no product = bottle not full = wait. It only gates the valve (5b).

**Test 5a** (`simulator/tests/test_station_jobs.py`, 11 checks): empty machine stays in WORK; S3 → INDEX after `RinseTime` (2 s, and 1 s after writing 1000); rinse restarts from 0 in the next WORK step; S4 stays until `FillLevel` = 500 (499 not enough); fill done without product; S5 → INDEX after 1.5 s; all three busy → the slowest job decides. Debugger: `Rinse_PT` = `T#2s`, `Cap_PT` = `T#1s500ms`.

**5b – Interlocks and outputs** (section 6, below the transitions), in this order:
- 6.1 Request rungs `Req_Rinse` / `Req_Fill` / `Req_Cap`: **Auto branch** (`Mode_Auto AND Step_Work AND` bottle `AND NOT` done, fill also `AND Product_Available`) **OR Manual branch** (`Mode_Manual AND Man_…`, fill also `AND S4` = interlock).
- 6.2 INTERLOCK `Ilk_Index := NOT Req_Rinse AND NOT Req_Fill AND NOT Req_Cap` (from the requests, which are already computed in this scan; the outputs are not).
- 6.3 `Req_Index := Mode_Manual AND Man_Index AND Ilk_Index` (Auto branch in stage 6); `Req_C1 := (NOT Mode_Manual AND NOT Step_Fault) OR (Mode_Manual AND Man_C1)` – C1 runs always unless E-stop/FAULT.
- 6.4 FB instances (`Vlv_Rinse`, `Vlv_Fill`, `Mot_Cap`, `Mot_Index`, `Mot_C1`): request → `Run`/`Open`, `Enable` = `Master_Enable`, `Out` → output. Each `%QX` has exactly one writer.
- TEST ONLY FB rungs and their variables removed.

**Test 5b** (`simulator/tests/test_outputs.py`, 36 checks): Auto: S3/S4/S5 in step 10 → the right outputs, each switches off on its own done bit; no product → valve closed, still WORK; E-stop / FAULT → all off incl. C1; Mode Stop → only C1. Manual: hold-to-run; `Man_Fill` without S4 → valve stays closed; `Man_Index` with a rinse/fill/cap button held → belt blocked (`Ilk_Index`).

## Stage 6 – INDEX, door and counter  *(week VII: TP, CTU)*

- Step 20: `IndexMotor` until `S8_Edge`.
- Door: TP 0.5 s at the start of step 20 if S2.
- `S6_Edge` → CTU → `GoodCount` (%MW53).

As built: `Req_Index := ((Mode_Auto AND Step_Index) OR (Mode_Manual AND Man_Index)) AND Ilk_Index`; `Door_TP` (TP, **EN/ENO off**, `Q` unconnected) with `IN := Mode_Auto AND P(Step_Index) AND S2`; `Req_Door := Door_TP.Q OR (Mode_Manual AND Man_Door)` → `Mot_Door`; `Good_Counter` (CTU, `CU := S6_Edge`, `PV` = 32767, `CV` → `GoodCount`). The door opens only at the *start* of INDEX: a bottle reaching S2 mid-INDEX waits for the next one. **Added 2026-11-03 (entry tracking):** `Door_TP.IN` also needs `NOT Entry_Load`; two rungs below `Req_Door`: `Req_Door → (S) Entry_Load`, `S8_Edge → (R) Entry_Load` (%MX3.1). Without it a stop inside INDEX (jam, E-stop) left a bottle in the sensorless entry pocket and the restart's door pulse dropped a second one onto it (found by `simulator/tests/test_scenarios.py index_jam`).

**Test** (`simulator/tests/test_index_door.py`, 19 checks): belt runs in INDEX and stops on the S8 edge; door pulse 0.5 s even if S2 stays TRUE; next INDEX → new pulse; S2 arriving mid-INDEX → no door; Manual door hold-to-run; E-stop in INDEX → all off; 5 S6 pulses → `GoodCount` +5, S6 held → counts once.

## Stage 7 – Stop, Starved, Blocked

- `Stop_Request` latch, checked only at the safe points (`state-diagram.md` rule 2).
- `State` (%MW50): Running / Starved / Blocked / Stopped / Fault from the step and conditions (rule 4).

As built: `Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT Step_Idle` (above the transitions); *Transition -> 0*: `Seq_Enable AND Stop_Request AND (Step_Work AND Jobs_Done OR Step_Wait OR Step_Index AND S8_Edge)`; `NOT Stop_Request` in the transitions 10 -> 15, 10 -> 20, 15 -> 20, 20 -> 10 so that at most one transition fires per scan. Section 7: `Starved := Step_Work AND (NOT Bottles_Present OR NOT Fill_Done AND NOT Product_Available)` above five mutually exclusive `MOVE n → State` rungs (order among them does not matter).

**Test** (`simulator/tests/test_stop_state.py`, 29 checks): Stop during rinse → job finishes, IDLE, never INDEX; Start continues; Stop during INDEX → belt finishes, S8 → IDLE; Stop in IDLE ignored and not remembered; Stop in empty WORK / WAIT_OUTFEED → IDLE at once; State 0/1/2/3/9 for IDLE, WORK, INDEX, no bottles, no product, WAIT, FAULT, Manual, E-stop.

## Stage 8 – Alarms and FAULT  *(week VII: timers as timeouts)*

- Timeouts: index jam (5 s in step 20 without S8), fill timeout (10 s valve open), blocked too long (2 min in step 15), comm lost.
- Latch each into its alarm bit (%MX5.x); FAULT alarms move `Step` to 900; `Reset_Cmd` clears the latches (only when the cause is gone) and goes to step 0.

As built (section 8, last section of `main`): `Jam_Timer` (TON 5 s, `IN := Step_Index`) → (S) `Alm_IndexJam`; `Fill_Timer` (TON 10 s, `IN := Step_Work AND FillValve` – only while the valve is open, so Starved never alarms) → (S) `Alm_FillTimeout`; `Blocked_Timer` (TON 2 min, `IN := Step_Wait`) → (S) `Alm_OutfeedBlocked` (warning, no FAULT). Each alarm: (R) with `Reset_Cmd AND NOT <timer>.Q`. Last rung: `Alm_IndexJam OR Alm_FillTimeout → MOVE 900 → Step` – deliberately the last writer of `Step` (FAULT has the highest priority); the reset rungs sit above it, so one Reset is enough.

**Test** (`simulator/tests/test_alarms.py`, 25 checks, ≈4 min; `--quick` skips the 2 min alarm): no false alarm (empty machine, no product, Manual fill, 12 s each); jam after 5 s → FAULT, belt off, Start ignored, one Reset → IDLE, restart runs; fill timeout after 10 s of open valve → FAULT, valve off, restart tops the bottle up (rule 7); blocked 2 min → warning, still WAIT_OUTFEED, Reset ignored while blocked, continues by itself, then Reset clears.

## After stage 8

Run the full test list in `progress.md` section 4 by forcing, then continue with the Python simulator, which replaces the hand forcing.

## Change: jog watchdog  *(HMI issue I3, week VII: timers as watchdogs)*

**Why.** In Manual, a FUXA hold-to-run button writes `Man_x = 1` on mouse-down and `0` on mouse-up or when the pointer leaves the button. If the browser tab is closed (or the network drops) while the button is held, the `0` never comes: `Man_x` stays 1 and the actuator runs on. FUXA cannot notice a closed tab, so the PLC must protect itself: **a manual button that stays ON longer than 10 s is ignored until every manual button is released.** The PLC never writes `Man_x` itself (they belong to the HMI, one writer per address); it only stops obeying them. 10 s is longer than any normal jog (a full manual fill takes 6 s, one index pitch 1.5 s, the longest hold in `test_scenarios.py manual` is C1 with at most 8 s).

```
Jog_TON (TON):  IN := Man_C1 OR Man_Door OR Man_Rinse OR Man_Fill OR Man_Cap OR Man_Index,  PT := T#10s
Rung J1:  Man_C1 | Man_Door | Man_Rinse | Man_Fill | Man_Cap | Man_Index  ──[Jog_TON]── Q ──(S) Jog_Block
Rung J2:  /Man_C1  /Man_Door  /Man_Rinse  /Man_Fill  /Man_Cap  /Man_Index ───────────────(R) Jog_Block
Each manual branch:   Mode_Manual ─ Man_x ─ /Jog_Block ─ (rest unchanged)
```

Set and reset never fight: while a button is held, J2 is false; when all are released, `Jog_TON.IN` is false, so `Q` is false and J1 cannot set.

**Steps in the OpenPLC Editor** (blocks used: TON, NO/NC contact, parallel branch, set/reset coil – all used in this program already):
1. **Variables** (Variables table of `main`):
   - `Jog_Block : BOOL AT %MX3.2 := FALSE;` – comment: `Jog watchdog (HMI issue I3): a manual button held > 10 s, e.g. HMI page closed while pressing it. Manual outputs ignored until all Man_* are released`
   - `Jog_TON : TON;` – comment: `Jog watchdog timer: any Man_* ON continuously for 10 s`
2. **Rung J1** – place it in section 6 OUTPUTS **before** the first request rung (Rinse request), so the block acts in the same scan:
   - six NO contacts `Man_C1`, `Man_Door`, `Man_Rinse`, `Man_Fill`, `Man_Cap`, `Man_Index` **in parallel** (one under the other, joined left and right),
   - then the `TON` block `Jog_TON` on `IN`, `PT` = `T#10s`,
   - from `Q` a **Set** coil `Jog_Block`.
   - Rung comment: `Jog watchdog 1/2: any manual button ON for 10 s (lost mouse-up, closed HMI page) -> set Jog_Block`
3. **Rung J2** – directly below J1:
   - six NC contacts `/Man_C1` … `/Man_Index` **in series**,
   - a **Reset** coil `Jog_Block`.
   - Rung comment: `Jog watchdog 2/2: all manual buttons released -> reset Jog_Block (manual operation possible again)`
4. **Six manual branches** – in each rung below, find the lower branch `Mode_Manual … Man_x` and insert an **NC contact `Jog_Block`** directly after the `Man_x` contact:
   - Rinse request (`Man_Rinse`), Fill request (`Man_Fill`, before the `S4` interlock contact), Cap request (`Man_Cap`), Belt request (`Man_Index`), Door 2/2 (`Man_Door`), C1 request (`Man_C1`).
   - Add to each rung comment: `Manual branch also needs NOT Jog_Block (jog watchdog).`
5. **Compile, upload, start** (Runtime v4, address `localhost`).
6. **Test** (simulator and FUXA not operating the machine):
   ```
   cd simulator
   uv run python tests/test_jog_watchdog.py     # J0-J6, ~30 s
   uv run python tests/test_outputs.py          # regression: 36 checks, manual part M1-M5
   ```

## Change: manual interlocks for door and fill  *(planned – found in HMI stage 7.3, week VI: interlocks, week VIII: compare)*

**Status:** open, not built yet. Found 2026-11-04 while operating the filler by hand from FUXA (HMI stage 7.3, scenario 9). In Auto both problems are already prevented; only the **Manual** branches miss the check. Auto does not change.

An **interlock** is a condition in the PLC that blocks a command when carrying it out would cause damage – also in Manual, so an operator cannot break the machine by mistake.

### Finding 1 – Door: a second bottle falls into a loaded entry pocket
- **Seen:** `Bottle 11 two bottles in one pocket (with Bottle 10), fell off` – Door was held while bottle 10 was still in the entry pocket.
- **Why:** in Auto the door pulse needs `NOT Entry_Load` (the PLC's memory "entry pocket is loaded", set by the door, reset by the index cam edge `S8_Edge`). The Manual branch `Mode_Manual AND Man_Door` ignores it.
- **Trap:** simply adding `NOT Entry_Load` to the hold-to-run branch does not work: the door sets `Entry_Load` in the same scan it opens, so the next scan closes the door again before the bottle has passed.
- **Proposed fix (recommended):** in Manual the door also becomes a **pulse**, one bottle per press, through the same `Door_TP`:
  ```
  Door_TP.IN := ((Mode_Auto AND P(Step_Index) AND S2) OR (Mode_Manual AND P(Man_Door) AND NOT Jog_Block)) AND NOT Entry_Load
  Req_Door   := Door_TP.Q          (the hold-to-run Manual branch is removed)
  ```
  `P(Man_Door)` is a rising-edge contact. Holding the button longer no longer lets more bottles through. To verify before building: `S8_Edge` (and so the `Entry_Load` reset) also works when the belt is jogged in Manual.
- **Alternative:** keep hold-to-run and set `Entry_Load` only when the door closes (falling edge of `Req_Door`). Weaker: one long press can still let two bottles through.

### Finding 2 – Fill: manual fill overflows the bottle
- **Seen:** `Bottle 7 filled to 811 ml` – Fill held too long (setpoint 500 ml).
- **Why:** in Auto the valve closes at the setpoint (fill done bit). The Manual branch `Mode_Manual AND Man_Fill AND /Jog_Block AND S4` has no upper limit.
- **Proposed fix:** one compare block in series in the Manual branch of the Fill request:
  ```
  Req_Fill := (Auto branch, unchanged) OR (Mode_Manual AND Man_Fill AND NOT Jog_Block AND S4 AND FillLevel < FillSetpoint)
  ```
  `LT` with `IN1 = FillLevel`, `IN2 = FillSetpoint` (both INT in ml, no conversion). Check that `LT` is in the Editor v4 library before building. A bottle can then be topped up by hand, but never past the setpoint.

### Test (to write when built)
New script or extension of `test_jog_watchdog.py` / `test_outputs.py` manual part: (1) door press with `Entry_Load` TRUE → door stays closed; (2) one long door press → exactly one pulse; (3) `Man_Fill` held with `FillLevel` ≥ `FillSetpoint` → valve closed; below it → valve open. Regression: `test_outputs.py` 36, `test_jog_watchdog.py` 9, `test_scenarios.py manual`.
