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
- `Master_Enable := E_Stop_OK AND NOT Comm_Lost` – gates every output later (MCR equivalent).
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

As built: `Req_Index := ((Mode_Auto AND Step_Index) OR (Mode_Manual AND Man_Index)) AND Ilk_Index`; `Door_TP` (TP, **EN/ENO off**, `Q` unconnected) with `IN := Mode_Auto AND P(Step_Index) AND S2`; `Req_Door := Door_TP.Q OR (Mode_Manual AND Man_Door)` → `Mot_Door`; `Good_Counter` (CTU, `CU := S6_Edge`, `PV` = 32767, `CV` → `GoodCount`). The door opens only at the *start* of INDEX: a bottle reaching S2 mid-INDEX waits for the next one.

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
