# Labeler – Ladder roadmap

How to build the labeler PLC program in the OpenPLC Editor v4, in small stages. Same method as the filler and the syrup room (`docs/filler/ladder-roadmap.md`, `docs/syrup/ladder-roadmap.md`); the reusable parts (modes, FBs, heartbeat, jog watchdog, door TP + `Entry_Load`, `Outfeed_Full` filter) are copied, not redesigned. Design: `state-diagram.md`; addresses: `io-list.md`; checklist and decisions: `progress.md`.

## Working rule for every stage

1. Build only the rungs of this stage.
2. Compile and upload (runtime `plc-labeler`, device IP `127.0.0.4`).
3. **Test by forcing** in the debugger (the "Test" line of each stage).
4. **Release all forces** (a leftover force blocks Modbus writes later).
5. Run the Modbus test script for the stage (`simulator/tests/`, against port 5024).
6. Tick it in `progress.md`; the user commits.

Never start the next stage with a failing test. Before using a block for the first time, check that it exists in the Editor v4 library. All blocks of this machine are already used in the filler / syrup room: `ADD`, `SUB`, `EQ`, `GE`, `LE`, `LT`, `TON`, `TP`, `CTU`, `MOVE`, P contacts.

## Program structure

One program `main`, sections in this order (a value is computed **above** every rung that uses it in the same scan – filler decision 2026-11-01):

```
main
 ├─ 1  Modes and safety        (copied from the syrup room: defaults, mode/cmd decode incl. Cmd 4, Master_Enable, heartbeat)
 ├─ 2  Glue temperature        (Glue_Ready window, Heat_Demand hysteresis)
 ├─ 3  Conditioning            (Outfeed_Full filter, Starved filter timer, Label_Mark edge)
 ├─ 4  Step decode             (Step -> Step_Idle / Step_Warm / Step_Wait / ..., Seq_Enable)
 ├─ 5  Step done bits          (Spin_Counter, timeout timers)
 ├─ 6  Sequencer transitions   (abort, change Step; Stop_Request in stage 6)
 ├─ 7  Outputs                 (jog watchdog, gate + knife TP, Bottle_Entering, Auto OR Manual, interlocks, heater, FB instances)
 ├─ 8  Counter and status      (GoodCount with Cmd 4 reset, Starved, State)
 └─ 9  Alarms                  (timeouts, over-temperature, latches, reset, MOVE 900 last)
Function blocks: FB_Motor, FB_Valve (copied from plcs/syrup/)
```

---

## Stage 0 – Project and container setup

- **Third runtime container.** `docker-compose.yml` gets a service `plc-labeler`, a copy of `plc-syrup-carb` with host Modbus port **5024**, editor at **`127.0.0.4:8443`**, own volumes `labeler-data` / `labeler-build` (report 6.1). On Linux every `127.x.x.x` address is loopback, no network setup needed.
- New project `plcs/labeler/`; *Device*: board **OpenPLC Runtime v4**, IP **127.0.0.4**; *Servers*: Modbus TCP server, port 502 (inside the container), same buffer mapping as the syrup room (`plcs/syrup/devices/servers/modbusServer.json`), **`mx_bits` = 64** (covers the new sensor byte `%MX4`). Without the mapping the compile aborts with "Nothing produces %MW / %QX on this target".
- Declare every variable from `io-list.md`; constants from `state-diagram.md` (`GatePulse`, `CutPulse`, `StarvedDelay`, `ClampTimeout`, `LabelTimeout`, `OutTimeout`, `HeatTimeout`, `GlueMax`, `BlockedWarn`) as internal variables with initial values.
- Copy `FB_Motor` and `FB_Valve` from `plcs/syrup/` (no changes).
- One empty test rung, compile, upload.

**Test** (`test_labeler_stage0.py`): write `GlueTemp` (HR 1036), `E_Stop_OK` (coil 8201) and `Roll_Low` (coil 8224, the new `%MX4` byte) on port 5024 and read them back; the debugger shows them. The filler (502) and the syrup room (5022) are unaffected. Needs the uploaded program: without `libplc_*.so` the runtime starts no Modbus server and the connection is reset.

## Stage 1 – Modes and safety  *(weeks IV, VI, VIII, X)*

Copy section 1 of the syrup room and change only the defaults and the command decode:

- First-scan defaults (`NOT Init_Done` → MOVE chain → `(S) Init_Done`): `GlueSetpoint` 1700, `GlueHyst` 50, `GlueBand` 100, `PressTurns` 2.
- `ModeReq` → `Mode_Stop/Auto/Manual` (EQ); `Cmd` → `Start_Cmd/Stop_Cmd/Reset_Cmd` **+ `Count_Reset_Cmd` (EQ `Cmd`, 4)**; then MOVE 0 → `Cmd` (the clear rung must also clear 4).
- `Master_Enable := E_Stop_OK AND NOT Alm_CommLost`; heartbeat watchdog (XOR change detect, TON 3 s). **Rung order:** the `Master_Enable` rung comes after the watchdog's `Alm_CommLost` set / reset rungs (fixed in both PLCs 2026-11-14).

**Test** (`test_labeler_stage1.py`): after upload HR 1054–1057 hold the defaults; a later write is kept; `Cmd` 1–4 each read back as 0 after one scan; `E_Stop_OK` FALSE → `Master_Enable` FALSE; heartbeat frozen 3 s → `Alm_CommLost`.

## Stage 2 – Glue temperature  *(weeks IV, VIII: math, compare, seal-in)*

All rungs combinational, every scan (`state-diagram.md` rule 4):

```
──[SUB GlueSetpoint, GlueBand → Glue_Low_Lim]──[ADD GlueSetpoint, GlueBand → Glue_High_Lim]──[SUB GlueSetpoint, GlueHyst → Heat_On_Lim]──

──┤ GE GlueTemp,Glue_Low_Lim ├──┤ LE GlueTemp,Glue_High_Lim ├─────────────────────( Glue_Ready )──   window = two compares in series

──┤ LT GlueTemp,Heat_On_Lim ├────────────────────────────────────┬────────────────( Heat_Demand )──  hysteresis (heating)
──┤ Heat_Demand ├──┤ LT GlueTemp,GlueSetpoint ├──────────────────┘
```

Same shape as the syrup room's `Temp_OK`, reversed (heating instead of cooling).

**Test** (`test_labeler_glue.py`): `GlueTemp` 1590 / 1600 / 1700 / 1800 / 1810 → `Glue_Ready` F / T / T / T / F; `GlueBand` 50 → 1660 F; ramp 1700 → 1640 → 1660 → 1700 → 1680 → `Heat_Demand` F → T → T → F → F.

## Stage 3 – Conditioning  *(weeks IV, VII: seal-in, timers)*

- **`Outfeed_Full`** (%MX3.0): copy of the filler's filter with `C3_Full` instead of S7: `Full_OnTimer` TON 2 s, `Full_OffTimer` TON 3 s, seal-in.
- **Starved filter timer:** `Gate_Empty_Timer := TON(IN := NOT Bottle_At_Gate, PT := StarvedDelay)` (the `Starved` coil itself comes in stage 6, it needs the step bits).
- **Edge:** `┤P Label_Mark├ → ( Mark_Edge )` (one scan per registration mark).

**Test** (`test_labeler_conditioning.py`): `C3_Full` ON 1 s → `Outfeed_Full` F, ON 3 s → T, OFF 2 s → still T, OFF 4 s → F; blinking `C3_Full` (0.5 s on / 0.5 s off) 5 s → stays F. Debugger: `Gate_Empty_Timer.ET` runs while `Bottle_At_Gate` is FALSE and restarts at every bottle.

## Stage 4 – Sequencer skeleton  *(weeks VII, VIII, XI: counter, compare, move, sequencer)*

- **Step decode** (EQ → `Step_Idle`, `Step_Warm`, `Step_Wait`, `Step_Clamp`, `Step_Wrap`, `Step_Press`, `Step_Unclamp`, `Step_WaitOut`, `Step_Discharge`, `Step_Fault`), `Seq_Enable := Mode_Auto AND Master_Enable`.
- **Done bits** (section 5): `Spin_Counter := CTU(CU := Spin_Pulse, RESET := NOT Step_Press, PV := PressTurns)` (`state-diagram.md` rule 3).
- **Transitions** from the step table, **no outputs yet**: 0 → 5 (Start), 5 → 10, 10 → 20 / 40 / 35 / 5, 20 → 22, 22 → 24 (`Mark_Edge`), 24 → 26, 26 → 40 / 35, 35 → 40, 40 → 10, 900 → 0.
- Abort rung: `NOT Master_Enable OR NOT Mode_Auto` → step 0 (as in the other machines).

**Test** (`test_labeler_sequencer.py`): drive the sensors by hand and watch `Step`: Start with `GlueTemp` 1500 → stays 5; 1700 → 10; `Bottle_In_Pocket` → 20; `Bell_Down` → 22; `Label_Mark` pulse → 24; 2 `Spin_Pulse` pulses → 26 (1 pulse → stays 24); `Bell_Down` off → 40; `Bottle_In_Pocket` off → 10. Variants: `C3_Full` 3 s (`Outfeed_Full`) at the end of UNCLAMP → 35, free → 40; bottle with label in 10 → 40 directly; `GlueTemp` 1500 in 10 with an empty pocket → 5.

## Stage 5 – Outputs and interlocks  *(weeks III, VI, VII, IX: motor control, combinational logic, TP, FBs)*

Section 7, in this order (as the syrup room):

- 7.0 **Jog watchdog** (`Jog_TON` 10 s on any `Man_*`, `Jog_Block` %MX3.2) – copy with the seven labeler buttons.
- 7.1 **Step groups:** `Spin_Steps := Step_Wrap OR Step_Press`, `Clamp_Steps := Step_Clamp OR Spin_Steps` (smallest first, as `CO2_Steps`).
- 7.2 **Gate** (`state-diagram.md` rule 2): `Gate_TP` (TP, `PT := GatePulse`, `IN := Mode_Auto AND Step_Wait AND Bottle_At_Gate AND NOT Bottle_In_Pocket AND NOT Bottle_Entering AND NOT Roll_Empty AND Glue_Ready`); `Req_Gate := (Gate_TP.Q OR (Mode_Manual AND Man_Gate AND NOT Jog_Block)) AND NOT Bottle_In_Pocket`; below it `Req_Gate → (S) Bottle_Entering`, `Bottle_In_Pocket → (R) Bottle_Entering` (%MX3.1). `NOT Bottle_Entering` sits only in the TP's `IN`, not in the interlock after the OR: the running pulse sets `Bottle_Entering` itself, so as an interlock it would cut its own pulse after one scan. In the TP's `IN` it blocks the **next** pulse, which is what rule 2 needs.
- 7.3 **Knife:** `Cut_TP` (TP, `PT := CutPulse`, `IN := Mode_Auto AND Step_Press`); `Req_Cutter := Cut_TP.Q OR Manual`, interlock `Bell_Down`.
- 7.4 **Request rungs** `Req_…` = Auto branch (step bits / groups, rule 1) OR Manual branch (`Mode_Manual AND Man_… AND NOT Jog_Block`), **interlock contacts in series after the OR** (rule 9): C2 (Auto: NOT `Step_Fault`; runs in IDLE too), Clamp (`Bottle_In_Pocket`), Spin (`Bell_Down`), Feed and GlueRoller (one request, `Man_Feed`; `Bell_Down AND Glue_Ready`), Discharge (`NOT Bell_Down AND NOT Outfeed_Full`).
- 7.5 **Heater** (rule 4): `Req_Heater := Heat_Demand AND NOT Step_Fault AND LT GlueTemp,GlueMax` – no mode contact: it runs in Stop, Auto and Manual.
- 7.6 **FB instances**, `Enable` = `Master_Enable`, one writer per `%QX`: FB_Motor `Mot_C2`, `Mot_Gate`, `Mot_Spin`, `Mot_Feed`, `Mot_Glue`, `Mot_Discharge`, `Mot_Heater` (run hours = heater hours); FB_Valve `Vlv_Clamp`, `Vlv_Cutter`.

**Test** (`test_labeler_outputs.py`): each step switches exactly its outputs; gate pulse 0.5 s even with `Bottle_At_Gate` held; `Bottle_At_Gate` drops and rises while `Bottle_Entering` → **no** second pulse; knife one 0.2 s pulse per PRESS; spin off when `Bell_Down` drops; feed off when `Glue_Ready` drops; discharge blocked by `Outfeed_Full`; heater follows `Heat_Demand` in Stop mode, off at 2000; E-stop → all off (heater too). Manual: hold-to-run, interlocks still block (`Man_Spin` with the bell up → off), button held 11 s → `Jog_Block`.

## Stage 6 – Stop, counter, State  *(weeks IV, VII: latch, CTU)*

- `Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT Step_Idle`; "go to 0" rungs at the safe points of rule 6 (WARM_UP / WAIT_BOTTLE / WAIT_OUTFEED at once; end of UNCLAMP, DISCHARGE); `NOT Stop_Request` in those normal transitions; **CLAMP, WRAP, PRESS get no Stop branch** (a started label is finished).
- `Good_Counter` (CTU, `CU := Step_Discharge AND NOT Bottle_In_Pocket`, `RESET := Count_Reset_Cmd`, `PV` 32767, `CV` → `GoodCount`).
- `Starved` (%MX3.5) := `Step_Wait AND (Gate_Empty_Timer.Q OR Roll_Empty)`.
- `State` (%MW50): five mutually exclusive MOVE rungs (rule 8): Stopped (IDLE, Mode Stop, Manual), Running, Starved, Blocked (`Step_WaitOut`), Fault.

**Test** (`test_labeler_stop_state.py`): Stop in WRAP → continues through PRESS and UNCLAMP, then IDLE (no DISCHARGE); Start → bottle has a label → DISCHARGE; Stop in WAIT_BOTTLE → IDLE at once; two bottles → `GoodCount` +2; `Cmd` 4 → 0, `Cmd` 3 does not touch it; `Bottle_At_Gate` gap of 1 s → State stays 1, gap of 3 s → 2; `Roll_Empty` → 2 at once and no gate pulse; WAIT_OUTFEED → 3.

## Stage 7 – Alarms and FAULT  *(week VII: timers as timeouts, week VIII: compare)*

- `Alm_LabelTimeout` (%MX5.0): `Wrap_Timer` (TON `LabelTimeout`, `IN := Step_Wrap`) OR `Press_Timer` (`IN := Step_Press`) – two timers, so the timer restarts at 22 → 24 (syrup room lesson with `Water_Timer` / `Syrup_Timer`).
- `Alm_PocketJam` (%MX5.1): `Entry_Timer` (`IN := Bottle_Entering`, 2 s), `Clamp_Timer` (`IN := Step_Clamp`, 2 s), `Unclamp_Timer` (`IN := Step_Unclamp`, 2 s), `Out_Timer` (`IN := Step_Discharge`, 3 s).
- `Alm_OutfeedBlocked` (%MX5.2): `Blocked_Timer` (TON 2 min, `IN := Step_WaitOut`) – warning, no FAULT (copy of the filler).
- `Alm_CommLost` (%MX5.3): from stage 1.
- `Alm_LabelEmpty` (%MX5.4): `= Step_Wait AND Roll_Empty`, warning, not latched (follows its cause).
- `Alm_GlueOverTemp` (%MX5.5): `GE GlueTemp,GlueMax` → FAULT; Reset only below `GlueMax`.
- `Alm_GlueHeatTimeout` (%MX5.6): `Heat_Timer` (TON `HeatTimeout`, `IN := Step_Warm`) → FAULT.
- Each FAULT alarm: (S) by its cause, (R) by `Reset_Cmd AND NOT cause`. `No_Fault` (De Morgan), last rung: `NOT No_Fault` → `MOVE 900 → Step` (last writer of `Step`).

**Test** (`test_labeler_alarms.py`, `--quick` without the 120 s and 2 min timeouts): no false alarm in a normal cycle; no `Label_Mark` in WRAP → FAULT after 5 s, all off; Reset → Start → the half-labelled bottle (`Label_Present`) goes to DISCHARGE; `Bell_Down` stuck in UNCLAMP → FAULT after 2 s; gate pulse without `Bottle_In_Pocket` → FAULT after 2 s; `GlueTemp` 2010 → FAULT, heater off, Reset ignored at 2010, accepted at 1700; WARM_UP with `GlueTemp` held at 1000 → FAULT after 120 s; WAIT_OUTFEED 2 min → warning, still in 35.

## After stage 7

- Run all stage scripts once more (regression; a `tests/run_labeler.py` like `run_syrup.py`), then the Python simulator takes over (`docs/simulator/roadmap.md`, new section "Labeler simulator": conveyor C3 + packer stand-in, `machines/labeler.py` with label roll, glue tank temperature, turntable and bell, `LabelerPlc` on port 5024, C2 driven by the labeler PLC instead of the stand-in).
- The first **real conveyor link** between two PLCs: stop the labeler → C2 fills → the filler's S7 → filler **Blocked**; Start → C2 drains → the filler continues by itself (report 5.5).
