# Syrup room (mixer) – Ladder roadmap

How to build the syrup room PLC program in the OpenPLC Editor v4, in small stages. Same method as the filler (`docs/filler/ladder-roadmap.md`); the reusable parts (modes, FBs, heartbeat, jog watchdog) are copied, not redesigned. Design: `state-diagram.md`; addresses: `io-list.md`; checklist and decisions: `progress.md`.

## Working rule for every stage

1. Build only the rungs of this stage.
2. Compile and upload (runtime `plc-syrup-carb`, device IP `127.0.0.2`).
3. **Test by forcing** in the debugger (the "Test" line of each stage).
4. **Release all forces** (a leftover force blocks Modbus writes later).
5. Run the Modbus test script for the stage, if there is one (`simulator/tests/`, against port 5022).
6. Tick it in `progress.md`; the user commits.

Never start the next stage with a failing test. Before using a block for the first time, check that it exists in the Editor v4 library (`DIV`, `MUL`, `TO_DINT` are new here; `ADD`, `EQ`, `GE`, `LE`, `LT`, `TON`, `CTU`, `MOVE`, `TO_TIME` are already used in the filler).

## Program structure

One program `main`, sections in this order (a value is computed **above** every rung that uses it in the same scan – filler decision 2026-11-01):

```
main
 ├─ 1  Modes and safety        (copied from the filler: defaults, mode/cmd decode, Master_Enable, heartbeat)
 ├─ 2  Math and conditioning   (SyrupTarget, DoseTarget, Buffer_Room, Ingredients_OK, hysteresis bits)
 ├─ 3  Step decode             (Step -> Step_Idle / Step_Wait / Step_Water / ..., Seq_Enable)
 ├─ 4  Step done bits          (Water_Done, Syrup_Done, Mix_Timer, Carb_Timer, Transfer_Done)
 ├─ 5  Sequencer transitions   (Stop_Request, abort, change Step, Batch_Dosed)
 ├─ 6  Outputs                 (jog watchdog, Auto branch OR Manual branch, interlocks, FB instances)
 ├─ 7  Counters and status     (BatchCount, State)
 └─ 8  Alarms                  (limits, timeouts, latches, reset, MOVE 900 last)
Function blocks: FB_Motor, FB_Valve (copied from plcs/filler/)
```

---

## Stage 0 – Project and container setup

- **Second runtime container.** `docker-compose.yml` gets a second OpenPLC service `plc-syrup-carb` (host Modbus port **5022**, editor at **`127.0.0.2:8443`**, own volumes `syrup-data` / `syrup-build`), as planned in report 6.1. Editor v4 always uses port 8443 and only lets you set the IP, so each runtime gets its own loopback IP; the filler's editor mapping is bound to `127.0.0.1` (+ `::1`) for this and filler decision 2026-10-27 ("split when the second machine starts"). The filler service stays as it is for now (renaming it to `plc-filler` with 5023/8443 as in report 6.1 is a separate step, it changes the simulator and FUXA addresses).
- New project `plcs/syrup/`; *Device*: board **OpenPLC Runtime v4**, IP **127.0.0.2**; *Servers*: Modbus TCP server, port 502 (inside the container), **`mx_bits` = 64**.
- Declare every variable from `io-list.md`; constants from `state-diagram.md` (`MixMax`, `MixEmpty`, …) as internal variables with initial values.
- Copy `FB_Motor` and `FB_Valve` from `plcs/filler/` (no changes).
- One empty test rung, compile, upload.

**Test** (`test_syrup_stage0.py`): write one `%MW` sensor (`MixLevel`, HR 1037) and one `%MX` coil (`E_Stop_OK`, coil 8201) on port 5022; the debugger shows them. The filler on its own port is unaffected. Needs the uploaded program: without `libplc_*.so` the runtime starts no Modbus server and the connection is reset.

## Stage 1 – Modes and safety  *(weeks IV, VI, VIII, X)*

Copy section 1 of the filler and change only the defaults:

- First-scan defaults (`NOT Init_Done` → MOVE chain → `(S) Init_Done`): `WaterBatch` 500, `SyrupRatio` 55 (5.5:1), `MixTime` 20, `CoolSetpoint` 40, `TempHyst` 5, `PressSetpoint` 350, `PressHyst` 20, `BufferLow` 800, `BufferMin` 100, `CarbTime` 10 (values to tune with the simulator).
- `ModeReq` → `Mode_Stop/Auto/Manual` (EQ); `Cmd` → `Start_Cmd/Stop_Cmd/Reset_Cmd` + MOVE 0 → `Cmd`.
- `Master_Enable := E_Stop_OK AND NOT Alm_CommLost`; heartbeat watchdog (XOR change detect, TON 3 s).

**Test** (`test_syrup_stage1.py`): after upload HR 1054–1063 hold the defaults; a later write is kept. `E_Stop_OK` FALSE → `Master_Enable` FALSE. Heartbeat frozen 3 s → `Alm_CommLost`.

## Stage 2 – Math and compare  *(week VIII: math, compare)*

The syllabus gap the filler leaves. All rungs combinational, every scan:

```
GE SyrupRatio,10 ──[MUL WaterBatch, 10 → Water_x10]──[DIV Water_x10, SyrupRatio → SyrupTarget]──   (ratio ×10; no division by zero)
                ──[ADD WaterBatch, SyrupTarget → DoseTarget]──
──[ADD BufferLevel, MixLevel → Buffer_Sum]── LE Buffer_Sum,BufferMax ──( Buffer_Room )──
GE SyrupLevel,SyrupTarget ──( Ingredients_OK )──                                (water from the mains)
```

- `SyrupRatio` is ×10, so multiply **before** dividing: `500 / 55 × 10` would give 9 × 10 = 90 by luck, but `500 / 40 × 10` = 120 instead of 125. `WaterBatch × 10` stays inside INT up to 3276 L, far above `MixMax`.
- `MixTime` and `CarbTime` are INT seconds; a TON needs `TIME`. `TO_TIME` reads an integer as **ms**, and `seconds × 1000` overflows INT above 32 s, so: `TO_DINT` → `MUL ×1000` (DINT) → `TO_TIME` → `Mix_PT` / `Carb_PT`. (If `TO_DINT` is missing in the library, limit both setpoints to 32 s and multiply in INT.)

**Test** (`test_syrup_stage2.py`; `Buffer_Room`, `Ingredients_OK`, `Mix_PT` have no address → debugger steps D1–D5): 500, ratio 50 → `SyrupTarget` 100, `DoseTarget` 600; ratio 40 → 125 / 625; ratio 55 → 90 / 590 (truncated); ratio 0 and 5 → old value kept, no PLC fault; `Buffer_Room` TRUE at 1300 + 600 (= 1900), FALSE at 1400 + 600; `Ingredients_OK` FALSE with `SyrupLevel` 99. Debugger: `Mix_PT` = `T#20s`, 60 s → `T#1m`.

## Stage 3 – Hysteresis bits  *(weeks IV, VIII: seal-in, compare)*

Three seal-in rungs (`state-diagram.md` rule 2): `Temp_OK` (%MX3.0), `CO2_Demand` (%MX3.1), `Product_Available` (%MX6.0).

```
A1  ├──┤ LE ProductTemp,CoolSetpoint ├─────────────────────────┬──( Temp_OK )──
       ├──┤ Temp_OK ├──┤ LE ProductTemp,Temp_Upper ├───────────┘      Temp_Upper := CoolSetpoint + TempHyst (ADD)
```

`CO2_Demand` the other way round (set below `PressSetpoint − PressHyst` (SUB), reset at `≥ PressSetpoint`), `Product_Available` on `BufferLevel` with `BufferMin` / `BufferMin + AvailBand`.

**Test** (`test_syrup_hysteresis.py`): temperature ramp 60 → 30 → 60 (×0.1 °C): `Temp_OK` sets at 40, stays TRUE at 44, resets at 46; pressure ramp: `CO2_Demand` sets below 330, stays at 340, resets at 350; buffer ramp: `Product_Available` sets at 120, stays at 105, resets at 99.

## Stage 4 – Sequencer skeleton  *(weeks VIII, XI: compare, move, sequencer)*

- Step decode (EQ → `Step_Idle`, `Step_Wait`, `Step_Water`, `Step_Syrup`, `Step_Mix`, `Step_Cool`, `Step_Carb`, `Step_WaitBuffer`, `Step_Transfer`, `Step_Fault`), `Seq_Enable := Mode_Auto AND Master_Enable`.
- Step done bits (section 4): `Water_Done := MixLevel ≥ WaterBatch`, `Syrup_Done := MixLevel ≥ DoseTarget`, `Water_Timer` / `Syrup_Timer` (TON `DoseTimeout`, `IN := Step_Water` / `Step_Syrup`; two timers, because with one shared timer `IN` would stay TRUE across 10 → 20 and the timer would not restart), `Mix_Timer` (TON, `IN := Step_Mix`), `Carb_Timer` (TON, `IN := Step_Carb AND NOT CO2_Demand`, rule 3), `Transfer_Done := MixLevel ≤ MixEmpty`.
- Transition rungs from the step table, **no outputs yet**. `Batch_Dosed` (%MX3.3): (S) on 20 → 30, (R) on 60 → 5.
- Abort rung: `NOT Master_Enable OR NOT Mode_Auto` → step 0 (as in the filler).

**Test** (`test_syrup_sequencer.py`): drive `MixLevel`, `ProductTemp`, `CO2Pressure`, `BufferLevel` by hand and watch `Step`: 0 → 5 (Start) → stays 5 with buffer 1500 → 10 at buffer 700 → 20 at level 500 → 30 at 600 → 40 after `MixTime` → 50 at 4.0 °C → 60 after `CarbTime` → 5 at level 5. Variants: no buffer room → 55, room → 60; `Batch_Dosed` TRUE in 5 → 30; level 300 without `Batch_Dosed` → 10.

## Stage 5 – Outputs and interlocks  *(weeks III, VI, IX: motor control, combinational logic, FBs)*

Section 6, in this order (as the filler):

- 6.0 Jog watchdog J1/J2 (`Jog_TON` 10 s on any `Man_*`, `Jog_Block` %MX3.2) – copy from the filler with the six syrup buttons.
- 6.1 Request rungs `Req_…` = **Auto branch** (step bits + hysteresis bits, `state-diagram.md` rule 1) **OR Manual branch** (`Mode_Manual AND Man_… AND NOT Jog_Block`), then the **interlock contacts in series after the OR** (rule 7), so they apply to both.
- 6.2 FB instances: `Mot_Water`, `Mot_Syrup`, `Mot_Agitator`, `Mot_Transfer` (FB_Motor), `Vlv_Cooling`, `Vlv_CO2` (FB_Valve); `Enable` = `Master_Enable`; one writer per `%QX`.

**Test** (`test_syrup_outputs.py`): each step switches exactly its outputs; COOL: valve follows `Temp_OK`; CARBONATE: CO2 valve follows `CO2_Demand`, closed while `Temp_OK` FALSE; agitator off below 100 L; pumps off at `MixMax` and with `MixHighHigh` alone; transfer pump off at `MixEmpty` and at `BufferMax`; E-stop → all off. Manual: hold-to-run, interlocks still block (`Man_CO2` warm → closed, `Man_Agitator` at 50 L → off), button held 11 s → `Jog_Block`.

## Stage 6 – Stop, State, counter  *(weeks IV, VII: latch, CTU)*

- `Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT Step_Idle`; in every "step done" transition, a `Stop_Request` branch goes to 0 instead of the next step; WAIT_DEMAND and WAIT_BUFFER go to 0 at once (`state-diagram.md` rule 4). `NOT Stop_Request` in the normal transitions, so one transition per scan.
- `Batch_Counter` (CTU, `CU := Step_Transfer AND Transfer_Done`, `CV` → `BatchCount` %MW53).
- `State` (%MW50): five mutually exclusive MOVE rungs (rule 5): Stopped (IDLE, Mode Stop, Manual), Running, Starved (`Step_Wait AND batch needed AND NOT Ingredients_OK`), Blocked (`Step_WaitBuffer`), Fault.

**Test** (`test_syrup_stop_state.py`): Stop in DOSE_WATER → pump runs until `WaterBatch`, then IDLE; Start → resumes at DOSE_SYRUP via WAIT_DEMAND; Stop in COOL → IDLE after `Temp_OK`, Start → MIX (`Batch_Dosed`); Stop in WAIT_DEMAND → IDLE at once; one full batch → `BatchCount` +1; State 0/1/2/3/9 in each situation.

## Stage 7 – Alarms and FAULT  *(week VII: timers as timeouts, week VIII: compare)*

- `Alm_Overflow` (%MX5.0): `MixLevel ≥ MixMax OR MixHighHigh OR BufferLevel ≥ BufferMax` → FAULT.
- `Alm_OverPressure` (%MX5.1): `CO2Pressure ≥ PressMax` → FAULT (the CO2 valve is already closed by its interlock).
- `Alm_CoolTimeout` (%MX5.2): `Cool_Timer` (TON `CoolTimeout`, `IN := Step_Cool`) → FAULT.
- `Alm_CommLost` (%MX5.3, same bit in every PLC): from stage 1.
- `Alm_LowSyrup` (%MX5.4): `Step_Wait AND batch needed AND SyrupLevel < SyrupTarget` → warning only, no FAULT, no latch (follows its cause, so it clears itself after the refill).
- `Alm_DoseTimeout` (%MX5.5): `Water_Timer.Q OR Syrup_Timer.Q` (stage 4) → FAULT.
- Each FAULT alarm: (S) by its cause, (R) by `Reset_Cmd AND NOT cause`. Last rung: FAULT alarms → `MOVE 900 → Step` (last writer of `Step`).

**Test** (`test_syrup_alarms.py`): no false alarm in a normal batch; `MixLevel` 960 → FAULT, all off, Reset ignored while 960, accepted at 900; `MixHighHigh` TRUE at level 500 → FAULT; DOSE_WATER with level held → FAULT after `DoseTimeout`; pressure 610 → FAULT; COOL with temperature held at 80 → FAULT after `CoolTimeout`; Reset → IDLE → Start resumes at MIX; low syrup → Starved + warning, continues by itself when `SyrupLevel` rises.

## After stage 7

- Run all stage scripts once more (regression), then the Python simulator takes over (`docs/simulator/roadmap.md`: a `machines/syrup.py` physics class, a second PLC client, copying `Product_Available` from this PLC to the filler's `%MX1.0` instead of holding it TRUE).
- The first **PLC ↔ PLC** demo (syllabus week XIII): stop the syrup room in Manual with an almost empty buffer → `Product_Available` FALSE → filler **Starved** in WORK; Start → batch → buffer fills → filler continues by itself.
