# Blow moulder – Ladder roadmap

How to build the blow moulder PLC program in the OpenPLC Editor v4, in small stages. Same method as the other four machines (`docs/packer/ladder-roadmap.md`): the reusable parts (modes, FBs, heartbeat, jog watchdog, `Outfeed_Full` filter, window compare, Stop at safe points, State rungs, alarm latches) follow the packer, the new parts get their own stage. Design: `state-diagram.md`; addresses: `io-list.md`; checklist and decisions: `progress.md`.

Draft 2026-11-25. New for this machine, and why the stages are cut as they are: the **first analog output** (`%QW0`, checked in stage 0 before anything depends on it), **analog math** with a P controller and a DINT scaling (stage 2, testable with numbers alone), **two sequencers** (stages 4 and 5, one each, as the packer), the **lamps** as their own stage (6), and two outputs that do **not** follow `Master_Enable`: the normally-open exhaust and the self-holding mould (stage 7).

## Working rule for every stage

1. Build only the rungs of this stage (the rungs are scripted into `plcs/blowmoulder/` main.ld as for the packer; the Editor must be closed while the files are written).
2. Compile and upload (runtime `plc-blowmolder`, device IP `127.0.0.5`).
3. **Test by forcing** in the debugger where a stage says so.
4. **Release all forces** (a leftover force blocks Modbus writes later).
5. Run the Modbus test script for the stage (`simulator/tests/`, against port **5025**) and add it to `run_blowmoulder.py`.
6. Tick it in `progress.md`; the user commits.

Never start the next stage with a failing test. **Blocks:** used before – `ADD`, `SUB`, `MUL`, `DIV`, `EQ`, `GE`, `LE`, `LT`, `GT`, `XOR`, `MOVE`, `LIMIT`, `TON`, `CTU`, `TO_DINT` (syrup room), P contacts; **new for this machine** – `TO_INT` (DINT → INT; fallback `DINT_TO_INT`) and an `INT` output at `%QW`. Check both in the Editor 4.3.3 library panel before their first rung (pin order in the box).

## Program structure

One program `main`, sections in this order (a value is computed **above** every rung that uses it in the same scan):

```
main
 ├─ 1  Modes and safety        (defaults, mode/cmd decode incl. Cmd 4, Master_Enable, heartbeat, Manual entry edge)
 ├─ 2  Analog values           (Oven_Ready window, Oven_BelowMax, OvenError, P term + LIMIT -> Lamp_Calc,
 │                              pressure scaling in DINT -> BlowPressure, Press_Range_OK, Pressure_Reached, Pressure_Safe)
 ├─ 3  Conditioning            (Outfeed_Full filter on C1_Full)
 ├─ 4  Step decode             (Step -> Oven_Idle ... Oven_Fault; MouldStep -> Mould_Idle ... Mould_Fault; Seq_Enable, Lamps_On)
 ├─ 5  Done bits and timers    (Soak_Timer -> Preform_Hot, Grip_Timer, Release_Timer, Hold_Timer)
 ├─ 6  Sequencer transitions   (abort, Start of both, oven station, mould station; Stop_Request in stage 8)
 ├─ 7  Outputs                 (LampPower, jog watchdog, exhaust, mould toggle + seal-in, request rungs, interlocks, FBs)
 ├─ 8  Counter and status      (BottleCount with Cmd 4 reset, Starved, Alm_NoPreforms, State)
 └─ 9  Alarms                  (timeouts, limits, sensor check, latches, reset, MOVE 900 last)
Function blocks: FB_Motor, FB_Valve (copied from plcs/packer/)
```

---

## Stage 0 – Project, container, and the `%QW0` check

- **Fifth runtime container.** `docker-compose.yml` gets a service `plc-blowmolder`, a copy of `plc-packer` with host Modbus port **5025**, editor at **`127.0.0.5:8443`**, own volumes **`blowmoulder-data` / `blowmoulder-build`** (also in the `volumes:` list at the bottom). **Rename the volumes when copying** – a shared build volume makes one upload replace the other PLC's program (packer, 2026-11-21).
- New project `plcs/blowmoulder/`; *Device*: board **OpenPLC Runtime v4**, IP **127.0.0.5**; *Servers*: Modbus TCP server, port 502 inside the container, same buffer mapping as the packer (`plcs/packer/devices/servers/`), **`mx_bits` = 64** (covers `%MX4` sensors and the `%MX7` alarms). The mapping must also serve the **`%QW` holding registers**.
- Declare every variable from `io-list.md` (located; `LampPower` as `INT` at `%QW0`) and the constants of `state-diagram.md` as internal variables with initial values (`OvenMax`, `ErrMax`, `ErrMin` −1000, `RawFull` / `PressFull` **as DINT** (27648 / 400, used by the scaling MUL / DIV), `RawMin`, `RawMax`, `ExhaustLimit`, `MaxPressure`, `SoakTime`, `GripTime`, `HoldTime`, `MoveTimeout`, `HeatTimeout`, `BlowTimeout`, `ExhaustTimeout`, `StarvedDelay`, `BlockedWarn`).
- Copy `FB_Motor` and `FB_Valve` from `plcs/packer/` (no changes).
- One test rung: `E_Stop_OK → MOVE 42 → LampPower` (the packer's stage 0 needed none; this one proves where `%QW0` lands). Compile, upload, test, then delete it.

**Test** (`test_blowmoulder_stage0.py`, B1–B13): write `OvenTemp` (HR 1036), `BlowPressure_Raw` (HR 1037, a **negative** value −2000: INT is signed), `E_Stop_OK` (coil 8201), `C1_Full` (coil 8228, `%MX4`) on port 5025 and read them back; coil 8250 (`Alm_PressureSensor`, `%MX7.2`) can be read. **`%QW0` check (Q1):** with `E_Stop_OK` FALSE HR 0 is written 0 and stays 0 (a MOVE without EN does nothing, so the 42 is not written back); with `E_Stop_OK` TRUE HR 0 reads 42 → `%QW0` = HR 0 confirmed. If HR 0 is not served, `LampPower` moves to `%MW40` (HR 1064) and `io-list.md` is changed before stage 1. The other four PLCs (502, 5022, 5023, 5024) are unaffected (SKIP when that runtime is not up).

## Stage 1 – Modes and safety  *(weeks IV, VI, VIII, X)*

Same structure as the packer's section 1, with this machine's defaults:

- First-scan defaults (`NOT Init_Done` → MOVE chain → `(S) Init_Done`): `OvenSetpoint` 1050, `OvenBand` 50, `Kp` 10, `Bias` 0, `BlowSetpoint` 350.
- `ModeReq` → `Mode_Stop/Auto/Manual` (EQ 0–2); `Cmd` → `Start_Cmd/Stop_Cmd/Reset_Cmd/Count_Reset_Cmd` (EQ 1–4); then MOVE 0 → `Cmd`.
- Heartbeat watchdog (XOR change detect `Hb_Changed`, TON 3 s on `NOT Hb_Changed` → `Alm_CommLost`), then `Master_Enable := E_Stop_OK AND NOT Alm_CommLost`.
- **New:** `┤P Mode_Manual├ → ( Manual_Entry )` – one scan when Manual is entered (used by the mould toggle in stage 7).

**Test** (`test_blowmoulder_stage1.py`): after upload HR 1054–1058 hold the defaults; a later write is kept; `Cmd` 1–4 each read back as 0 after one scan; heartbeat frozen 3 s → `Alm_CommLost`, frozen at FALSE **and** at TRUE; Reset with a dead heartbeat keeps the alarm. Debugger: `E_Stop_OK` FALSE → `Master_Enable` FALSE.

## Stage 2 – Analog values  *(week VIII: math, compare; week X: data types)*

All rungs combinational, every scan (`state-diagram.md` rules 5 and 6). Nothing drives an output yet: the results go into helper variables, so the math can be tested with numbers alone.

```
Oven window (rule 5, as the packer's Oven_Ready):
──[SUB OvenSetpoint, OvenBand → Oven_Low_Lim]──[ADD OvenSetpoint, OvenBand → Oven_High_Lim]──
──┤ GE OvenTemp,Oven_Low_Lim ├──┤ LE OvenTemp,Oven_High_Lim ├───────────────────────────( Oven_Ready )──
──[LT OvenTemp, OvenMax → Oven_BelowMax]──

P controller (rule 5):
──[SUB OvenSetpoint, OvenTemp → OvenError]──[LIMIT MN:=ErrMin, IN:=OvenError, MX:=ErrMax → Err_Lim]──
──[MUL Kp, Err_Lim → P_x10]──[DIV P_x10, 10 → P_Term]──[ADD Bias, P_Term → Lamp_Sum]──[LIMIT MN:=0, IN:=Lamp_Sum, MX:=100 → Lamp_Calc]──

Pressure scaling (rule 6) – DINT in the middle:
──[TO_DINT BlowPressure_Raw → Raw_D]──[MUL Raw_D, PressFull → Raw_x400]──[DIV Raw_x400, RawFull → Press_D]──[TO_INT Press_D → BlowPressure]──
──┤ GE BlowPressure_Raw,RawMin ├──┤ LE BlowPressure_Raw,RawMax ├──────────────────────────( Press_Range_OK )──
──┤ GE BlowPressure,BlowSetpoint ├────────────────────────────────────────────────────────( Pressure_Reached )──
──┤ LT BlowPressure,ExhaustLimit ├──┤ Press_Range_OK ├────────────────────────────────────( Pressure_Safe )──
```

- **Order of MUL and DIV** is the lesson of this stage: `Kp · error` first, then `/ 10`; `Raw · 400` first, then `/ 27648`. Dividing first truncates to 0.
- `PressFull` / `RawFull` are declared as **DINT** (400, 27648), so the MUL and DIV have one data type on both inputs.
- `Pressure_Safe` uses `Press_Range_OK` now; stage 9 adds `NOT Alm_PressureSensor`, so a sensor that comes back after a wire break still keeps the mould closed until Reset.

**Test** (`test_blowmoulder_values.py`): `OvenTemp` 990 / 1000 / 1050 / 1100 / 1110 → `Oven_Ready` F / T / T / T / F; `OvenBand` 100 → 950 T. **P term** (`Lamp_Calc` located at `%MW40`, HR 1064, decision 2026-11-25): `OvenTemp` 200 → 100 (clamped); 1030 → 20 (`Kp` 10 · 2 °C); 1050 → 0; 1080 → 0 (negative clamped); `Bias` 30 at 1050 → 30; `Kp` 30 at `OvenTemp` −2000 (error 3050 → clamped 1000) → no overflow, 100. **Scaling:** raw 0 → 0, 13824 → 200, 24192 → 350, 27648 → 400, −2000 → −28; `Press_Range_OK` (no address): F at −1001 seen through `Pressure_Safe` (−14 is low, yet F), 32511 / 32512 in the debugger; `Pressure_Reached` at 350 vs 349; `Pressure_Safe` at raw 345 (4 → T) and 346 (5 → F), T at −1000, F at −1001 / −2000; `Oven_BelowMax` (no address) 1299 / 1300 in the debugger. **Result 2026-11-25: 39/39 + both debugger checks.**

## Stage 3 – Conditioning  *(weeks IV, VII: seal-in, timers)*

- **`Outfeed_Full`** (%MX3.0): the standard filter on `C1_Full` – `Full_OnTimer` TON 2 s, `Full_OffTimer` TON 3 s, seal-in (report 5.3). Rungs cloned from the packer's (18–20), only the sensor renamed; every scan in every mode.
- **Moved out (plan review 2026-11-25):** `Feed_Empty_Timer` is built in **stage 8** directly above `Starved`, complete with the step contact `Oven_Feed` – without it the timer runs during HEAT (loader empty) and is already done when FEED starts, so Starved would show at once instead of after 2 s; nothing can read it before stage 8 anyway. The **`Mould_Click`** edge is built in **stage 7.2** as the first toggle rung (as the labeler's bell toggle 1/4): a one-scan pulse is invisible over Modbus and in the debugger, its only user is the toggle.

**Test** (`test_blowmoulder_conditioning.py`, F0–F6): `C1_Full` ON 1 s → `Outfeed_Full` F, ON 3 s → T, OFF 2 s → still T, OFF 4 s → F; blinking 0.5 s / 0.5 s for 5 s → stays F; with `E_Stop_OK` FALSE and Mode Stop the filter still works (ON 3 s → T, OFF 4 s → F).

## Stage 4 – Oven station sequencer  *(weeks VII, VIII, XI: TON as a condition, compare, move, sequencer)*

Plan reviewed in three rounds (2026-11-25); 25 rungs in sections 4–6.

- **Section 4, step decode:** `Step` → `Oven_Idle`, `Oven_Feed`, `Oven_Load`, `Oven_Heat`, `Oven_Hot`, `Oven_Fault` (2 rungs of chained EQ); `MouldStep` → `Mould_Idle`, `Mould_WaitPreform`, `Mould_Grip`, `Mould_Swing`, `Mould_Release`, `Mould_ArmBack`, `Mould_Closing`, `Mould_Stretch`, `Mould_Blow`, `Mould_Hold`, `Mould_Exhaust`, `Mould_RodUp`, `Mould_Opening`, `Mould_Eject`, `Mould_Fault` (5 rungs; 30 and 70 are `Mould_Closing` / `Mould_Opening`, so they never clash with the sensors); `Mould_CloseSteps` = `GE MouldStep,30` → `Mould_From30`, `LE MouldStep,62` → `Mould_To62`, AND rung. `Seq_Enable := Mode_Auto AND Master_Enable`. **`Lamps_On`** (%MX3.7) := `Seq_Enable AND NOT Oven_Fault AND Preform_In_Oven` (also in IDLE: a preform left in the pocket stays warm).
- **Section 5, done bit:** `Soak_Timer := TON(IN := Oven_Heat AND Oven_Ready, PT := SoakTime)` → `Preform_Hot` (%MX3.3). It is TRUE for one scan only, because the step moves on in the same scan, so the test checks it by timing.
- **Section 6, transitions:**
  - **Abort:** `(NOT Mode_Auto OR NOT Master_Enable) AND NOT Oven_Fault` → MOVE 0 → `Step`, MOVE 0 → `MouldStep`. **`NOT Oven_Fault` keeps a FAULT** (both words 900) until Reset in stage 9, as the packer does.
  - **Start accepted:** `Seq_Enable AND Oven_Idle AND Mould_Idle AND Start_Cmd AND No_Fault` → `Start_Accepted`.
  - **Start 1/4–4/4 by sensors (rule 9), default then override:** 1/4 MOVE 10 → both words; 2/4 `Preform_In_Oven` → `Step` 20; 3/4 `Mould_Open AND Bottle_In_Mould` → `MouldStep` 80; 4/4 `Mould_Closed` → `MouldStep` 60. Within one scan the **last write wins**, so the rung order is the priority. No OR branch is needed for the mould's "else", and with both mould sensors TRUE (sensor fault) closed wins: exhaust first.
  - 10 → 15 `Preform_At_Loader AND NOT Preform_In_Oven AND NOT Outfeed_Full AND NOT Stop_Request`; 15 → 20 `Preform_In_Oven`; 20 → 25 `Preform_Hot`; 25 → 10 `NOT Preform_In_Oven`.
- **No later rung edits:** `Stop_Request` (FALSE) and `No_Fault` (initial **TRUE**) are declared now and their contacts are already in the rungs. Stage 8 and stage 9 only **add** the rungs that compute them (the packer had to edit its Start rung in stage 9).

The mould station has no transitions yet, so `MouldStep` stays where the Start put it. The oven is tested alone, and the hand-over is played by writing `Preform_In_Oven` FALSE.

**Test** (`test_blowmoulder_oven_seq.py`, O1–O18, ~45 s):
- `Lamps_On` in IDLE with and without a preform.
- One preform 10 → 15 → 20 → 25 → 10, with C1 full holding FEED and the off-filter releasing it.
- The cold oven holding HEAT, and the soak restarting when `OvenTemp` leaves the window (25 about 1 s after the second entry).
- Start by sensors: (20, 60), (20, 80), (20, 10), and the sensor conflict giving 60.
- Start refused unless both stations are IDLE.
- E-stop in HEAT → both 0, lamps off; Manual → lamps off.
- 900 written: lamps off, Start ignored, Mode Stop keeps 900.

**Checked offline before upload:** an offline scan interpreter of `main.ld` with a fake Modbus client on a virtual clock runs the real test script. It reproduces the stage 1–3 results (8/8, 39/39, 8/8) and gives 18/18 here. Three deliberately broken programs (swapped Start priority, abort without `NOT Oven_Fault`, soak without `Oven_Ready`) each fail the matching checks.

## Stage 5 – Mould station sequencer  *(weeks VII, VIII, XI: TON as step time, compare, sequencer)*

Plan reviewed in three rounds (2026-11-25); 16 rungs.

- **Done bits** (section 5, inserted below `Soak_Timer`): `Grip_Timer := TON(IN := Mould_Grip, PT := GripTime)`, `Release_Timer := TON(IN := Mould_Release, PT := GripTime)`, `Hold_Timer := TON(IN := Mould_Hold, PT := HoldTime)`. **One timer per step:** the roadmap's shared `Grip_Timer` on `Mould_Grip OR Mould_Release` would need an OR branch and a rung-order rule (SWING must reset it between GRIP and RELEASE). Two timers need neither.
- **Transitions** (end of section 6, after the oven's), each `Seq_Enable AND` step bit `AND` condition → MOVE:
  - 10 → 20 `Oven_Hot AND Mould_Open AND NOT Bottle_In_Mould AND Arm_At_Oven AND Rod_Up` (the hand-over);
  - 20 → 22 `Grip_Timer.Q`; 22 → 24 `Arm_At_Mould`; 24 → 26 `Release_Timer.Q AND Bottle_In_Mould`; 26 → 30 `Arm_At_Oven`;
  - 30 → 40 `Mould_Closed`; 40 → 50 `Rod_Down`; 50 → 52 `Pressure_Reached`; 52 → 60 `Hold_Timer.Q`;
  - 60 → 62 `Pressure_Safe`; 62 → 70 `Rod_Up`; 70 → 80 `Mould_Open`; 80 → 10 `NOT Bottle_In_Mould`.
- **No rung-order rule:** every transition reads the step bits decoded at the top of the scan, so each station makes at most one step per scan, and every step has exactly one exit.

**Test** (`test_blowmoulder_mould_seq.py`, M1–M29, ~50 s):
- **Hand-over guards:** the mould stays in 10 while the oven heats, with the rod down, with something in the mould, with the arm away, and with the mould not open. With all conditions met → 20.
- **One bottle 10 → … → 80:**
  - GRIP takes about 0.3 s and HOLD about 1.0 s (measured).
  - RELEASE waits for the photo eye.
  - BLOW waits at raw 24000 (347) and goes on at 24192 (350).
  - EXHAUST waits at raw 400 (5) and goes on at 345 (4).
  - EJECT waits while the bottle is in the mould.
- **Parallel:** the oven loads and heats the next preform during the blow, and the mould goes 80 → 10 → 20 at once.
- **Restarts:** Mode Stop with 35 bar in the closed mould → Start → 60, waits, then 62 → 70 → 80 → 10. E-stop in HOLD → both 0, then Start → 60.

The sensors are written in order: the one that blocks a transition comes first. Otherwise every condition is TRUE for a moment and the PLC moves on. The offline check found this race in the first draft of the test.

**Checked offline before upload:** all blow moulder tests pass on the stage 5 program, and the stage 4 test passes on both the stage 4 and stage 5 programs. In O11–O14 it now leaves 35 bar in the closed mould, because otherwise the restarted mould goes 60 → 62 during the settle time. Four planted bugs each fail the matching checks:
- RELEASE without the photo eye;
- hand-over without `Oven_Hot`;
- hand-over without `Arm_At_Oven`;
- `Hold_Timer` with `GripTime`.

## Stage 6 – The lamps: P controller on the analog output  *(week VIII: math, analog output)*

Plan reviewed in three rounds (2026-11-25). Section 7 starts here: a header and two rungs, above every other output (`state-diagram.md` rule 5):

```
────────────────────────────────────────────────────[MOVE 0 → LampPower]──          (safe default, every scan)
──┤ Lamps_On ├──┤ Oven_BelowMax ├───────────────────[MOVE Lamp_Calc → LampPower]──  (overwrites it)
```

- **Safe default, then override.** The roadmap draft had two rungs with opposite conditions, and the 0 rung needed an OR branch (`NOT Lamps_On OR NOT Oven_BelowMax`). Now rung 1 writes 0 in every scan and rung 2 writes `Lamp_Calc` when every permissive is TRUE. Within one scan the last write wins, and %QW0 is copied to the output only at the end of the scan, so the lamps never see the 0 in between. If nothing else is written, the lamps are off (fail-safe). This is the same pattern as the Start rungs of stage 4.
- The PLC owns %QW0: a Modbus write to HR 0 is overwritten in the next scan.
- No FB: an analog value has no feedback and no run hours.

**Test** (`test_blowmoulder_lamps.py`, L1–L10, ~25 s, reads HR 0; Mode Auto without Start, `Lamps_On` already in IDLE):
- Mode Stop → 0, and HR 0 written 77 → 0 again.
- Auto without a preform → 0.
- Auto + preform, with `LampPower` = `Lamp_Calc`: 200 → 100; 1030 → 20; **1020 → 30** (3 °C below the setpoint gives the 30 % a P controller needs, the steady offset; the draft's "1047 → 30" was wrong: 1047 is 0.3 °C below → 3 %); 1050 → 0; 1080 → 0.
- `Bias` 30 at 1050 → 30.
- **`OvenMax` interlock proven:** Kp 1, Bias 100 → 1299 gives 76, 1300 gives 0 while `Lamp_Calc` is still 75. With Kp 10 the controller alone is already at 0 there, so it would not prove the interlock.
- Manual → 0; E-stop → 0; heartbeat frozen (`Alm_CommLost`) → 0, back + Reset → 100; Step 900 → 0.

**Result 2026-11-25: 14/14 on the PLC.** Checked offline before upload: 14/14, all earlier blow moulder tests still pass. Three planted bugs fail the matching checks: no `Oven_BelowMax`, `Seq_Enable` instead of `Lamps_On`, and the default rung below the override.

## Stage 7 – Outputs and interlocks  *(weeks III, IV, VI, IX: seal-in, interlocks, FBs, toggle)*

Plan reviewed in three rounds (2026-11-25). There are 22 rungs: one in section 2 (`Below_MaxPress`, LT, after `Pressure_Safe`) and 21 in section 7 below the lamps, in this order.

- 7.0 **Jog watchdog:** `Jog_TON` 10 s on the OR of the six hold-to-run `Man_*` → (S) `Jog_Block` (%MX3.2). All six released → (R) `Jog_Block`. **`Man_MouldClose` is not in the list:** it is a toggle click, and the mould is a position, not a motion.
- 7.1 **Exhaust, normally open (rule 3)**, built as two plain rungs because no proven negated-coil template exists:
  - `(Mould_Stretch OR Mould_Blow OR Mould_Hold) AND Master_Enable` → `Exhaust_Shut`;
  - `NOT Exhaust_Shut` → `ExhaustValve`.
  This is the same logic as `NOT(...)`, with `Exhaust_Shut` visible in the debugger. There is **no FB**: its `Enable` = `Master_Enable` would shut the valve on E-stop.
- 7.2 **Mould toggle (rule 11), 3 rungs:**
  - `┤P Man_MouldClose├ → Mould_Click` (moved here from stage 3);
  - `Load_Mould := Manual_Entry OR NOT Master_Enable`;
  - `Man_Mould_Closed := Mode_Manual AND ((Load_Mould AND Mould_Closed) OR (NOT Load_Mould AND Man_Mould_Closed AND NOT Mould_Click) OR (NOT Load_Mould AND NOT Man_Mould_Closed AND Mould_Click))`. The XOR is written as two branches.
- 7.3 **Mould seal-in (rule 4), 4 rungs, a plain coil and no FB:**

  ```
  Close_Cmd  := ((Seq_Enable AND Mould_CloseSteps) OR (Mode_Manual AND Man_Mould_Closed)) AND Master_Enable AND Arm_At_Oven AND NOT Ejector
  Open_Cmd   := ((Seq_Enable AND NOT Mould_CloseSteps AND NOT Mould_Idle AND NOT Mould_Fault) OR (Mode_Manual AND NOT Man_Mould_Closed)) AND Master_Enable
  Open_OK    := Open_Cmd AND Pressure_Safe AND Rod_Up          (own coil: keeps the seal-in rung readable)
  MouldClose := (Close_Cmd OR (MouldClose AND Mould_Closed)) AND NOT Open_OK
  ```

- 7.4 **Request rungs:** every Auto branch has `Seq_Enable` and every Manual branch is `Mode_Manual AND Man_… AND NOT Jog_Block`; the interlocks come in series after the OR (`state-diagram.md` rule 10).
  - `Req_HopperFeed := (Oven_Feed AND NOT Preform_At_Loader) OR Manual`
  - `Req_Loader := (Oven_Load OR Manual) AND NOT Preform_In_Oven AND NOT Gripper`
  - `Req_Gripper := Mould_Grip OR Mould_Swing OR Manual`
  - `Req_Arm := (Mould_Swing OR Mould_Release OR Manual) AND Mould_Open AND Rod_Up AND NOT Ejector`
  - `Req_Rod := (Mould_Stretch OR Mould_Blow OR Mould_Hold OR Mould_Exhaust OR Manual) AND Mould_Closed`
  - `Req_PreBlow := Seq_Enable AND Mould_Stretch AND Mould_Closed` (no Manual branch)
  - `Req_HighBlow := (Mould_Blow OR Mould_Hold) AND Mould_Closed AND NOT ExhaustValve AND Below_MaxPress` (no Manual branch)
  - `Req_Ejector := (Mould_Eject OR Manual) AND Mould_Open AND Arm_At_Oven AND NOT Outfeed_Full`
- 7.5 **FB instances**, 2 chained rungs, `Enable` = `Master_Enable`, one writer per `%QX`:
  - FB_Motor `Mot_HopperFeed`;
  - FB_Valve `Vlv_Loader`, `Vlv_Arm`, `Vlv_Gripper`, `Vlv_Rod`, `Vlv_PreBlow`, `Vlv_HighBlow`, `Vlv_Ejector`.

  Interlocks that read another output (`NOT Gripper`, `NOT Ejector`) see last scan's value, as in the packer.

**Test** (`test_blowmoulder_outputs.py`, Q1–Q41, ~70 s). Every check compares the **complete set** of the ten digital outputs that are ON.
- Mode Stop / Auto IDLE → only the exhaust.
- FEED, LOAD, HEAT.
- Every mould step from GRIP to EJECT, with the exhaust shut only in STRETCH / BLOW / HOLD.
- High blow off at 40.0 bar.
- The mould held in OPEN with 0.5 bar left, and with the rod down; it opens with 0.4 bar and the rod up.
- Ejector held off while C1 is full.
- Mould held in HOLD on Mode Stop and on E-stop; held in Auto IDLE **with 0 bar and the rod up** (only `NOT Mould_Idle` keeps it closed there); a closing mould drops on E-stop.
- **Manual:**
  - entering Manual keeps the mould where it is;
  - rod only with the mould closed; arm only into an open mould with the rod up;
  - ejector, hopper, loader (blocked by a preform in the pocket and by the closed gripper);
  - the toggle opens and closes the mould;
  - E-stop and release move nothing, open or closed;
  - a button held 11 s sets `Jog_Block` and leaves the mould alone.

**Result 2026-11-25: 41/41 on the PLC** after one test fix: the outputs follow a step change one scan later, because section 7 still uses the step bits decoded at the top of the scan in which section 6 moved the step. The test now waits 0.1 s after each step change before it reads the outputs (the first PLC run read inside that scan: 31/41).

**Checked offline before upload:**
- Every OR rung matches its formula for every input combination: 11 rungs, 1,816 cases, 0 wrong.
- The test passes 41/41, and all earlier tests still pass.
- Ten planted bugs are each caught. The first draft missed "`Open_Cmd` without `NOT Mould_Idle`", because Q24 ran with 35 bar, which blocked the opening anyway. Q24 now runs with 0 bar and the rod up.

## Stage 8 – Stop, counter, State  *(weeks IV, VII: latch, CTU)*

- `Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT (Oven_Idle AND Mould_Idle)` (declared in stage 4, where 10 → 15 already reads `NOT Stop_Request`); go-to-0 rungs (rule 7): oven FEED → 0 at once; mould WAIT_PREFORM → 0 **only with `Oven_Idle`**; `NOT Stop_Request` in 10 → 15. No other step gets a Stop branch: the last preform is always blown and ejected.
- `Bottle_Counter` (CTU, `CU := Bottle_Ejected`, `R := Count_Reset_Cmd`, `PV` 32767, `CV` → `BottleCount` %MW53).
- `Feed_Empty_Timer` (moved here from stage 3), directly above `Starved`: `IN := Oven_Feed AND NOT Preform_At_Loader`, `PT := StarvedDelay`; `Starved` (%MX3.6) := `Feed_Empty_Timer.Q AND Mould_WaitPreform`; `Alm_NoPreforms` (%MX5.0) := `Oven_Feed AND Hopper_Empty AND NOT Preform_At_Loader` (warning, not latched).
- `State` (%MW50): mutually exclusive MOVE rungs in the priority of rule 8: Fault (`Oven_Fault`), Stopped (both idle, or Mode Stop / Manual), Blocked (`Outfeed_Full AND (Mould_Eject OR Oven_Feed)`), Starved, Running.

**Test** (`test_blowmoulder_stop_state.py`): Stop in FEED with the mould idle → both 0 at once; Stop in HEAT → the preform is heated, handed over, blown, ejected, then both 0 (no new load); Stop in BLOW → the cycle finishes to 10, then 0; three `Bottle_Ejected` pulses → `BottleCount` +3, `Cmd` 4 → 0, `Cmd` 3 keeps it; no preform 1 s → State 1, 3 s with the mould waiting → 2, 3 s while a bottle is blown → 1; `Hopper_Empty` in FEED → `Alm_NoPreforms` follows the sensor; `Outfeed_Full` in EJECT → State 3, ejector off; Mode Stop / Manual → 0.

## Stage 9 – Alarms and FAULT  *(week VII: timers as timeouts, week VIII: compare)*

One TON per step (each restarts at its own step), `MoveTimeout` 3 s unless stated:

- `Alm_HandlingJam` (%MX5.1): `Load_Timer` (`Oven_Load`), `Swing_Timer` (`Mould_Swing`), `Release_Timer` (`Mould_Release`), `ArmBack_Timer` (`Mould_ArmBack`), `Eject_Timer` (`Mould_Eject AND Ejector`: only while the ejector really pushes, not while C1 is full).
- `Alm_OutfeedBlocked` (%MX5.2): `Blocked_Timer` (TON `BlockedWarn`, `IN := State = 3`) – warning, no FAULT.
- `Alm_MouldFault` (%MX5.4): `Close_Timer` (`Mould_Closing`), `Stretch_Timer` (`Mould_Stretch`), `RodUp_Timer` (`Mould_RodUp`), `Open_Timer` (`Mould_Opening`).
- `Alm_OvenOverTemp` (%MX5.5): `NOT Oven_BelowMax` → FAULT in every mode; Reset only below `OvenMax`.
- `Alm_OvenHeatTimeout` (%MX5.6): `Heat_Timer` (TON `HeatTimeout` 15 s, `IN := Oven_Heat`).
- `Alm_BlowFault` (%MX5.7): `Blow_Timer` (TON `BlowTimeout` 2 s, `IN := Mould_Blow`).
- `Alm_ExhaustFault` (%MX7.0): `Exhaust_Timer` (TON `ExhaustTimeout` 3 s, `IN := Mould_Exhaust`); Reset only with `Pressure_Safe`.
- `Alm_OverPressure` (%MX7.1): `NOT Below_MaxPress` → FAULT in every mode; Reset only below `MaxPressure`.
- `Alm_PressureSensor` (%MX7.2): `NOT Press_Range_OK` → FAULT in every mode; Reset only with the raw value back in range. Then `Pressure_Safe` gets `NOT Alm_PressureSensor` (section 2 reads the alarm of the last scan – one scan late, harmless, the mould needs far longer to move).
- Each FAULT alarm: (S) by its cause, (R) by `Reset_Cmd AND NOT cause`. `No_Fault` (De Morgan over the 9 FAULT bits; declared in stage 4 with initial TRUE, its contact is already in `Start_Accepted`, this stage adds only the rung that computes it), 900 → 0 for both with Auto + `Reset_Cmd`, last rung: `NOT No_Fault` → MOVE 900 → `Step` **and** MOVE 900 → `MouldStep` (last writer of both).

**Test** (`test_blowmoulder_alarms.py`; `--quick` without the 2 min blocked warning): no alarm in a normal cycle; LOAD without `Preform_In_Oven` → FAULT after 3 s, both steps 900, all off, **exhaust open**; CLOSE without `Mould_Closed` → FAULT; HEAT at 900 → FAULT after 15 s, lamps 0; BLOW at raw 20000 → `Alm_BlowFault` after 2 s, **mould stays closed**, exhaust open; Reset → Start → 60 EXHAUST → open → eject; EXHAUST at raw 10000 → `Alm_ExhaustFault` after 3 s, Reset refused until raw ≤ 345 (`Pressure_Safe`); `OvenTemp` 1300 → FAULT also in Mode Stop; raw 27700 (> 400) → `Alm_OverPressure`; raw −2000 → `Alm_PressureSensor`, `Pressure_Safe` FALSE, mould closed, Reset refused until the raw value is back; EJECT with `Outfeed_Full` for 10 s → no jam (timer only with the ejector ON); State 3 for 2 min → warning, no FAULT. Earlier scripts adapted so no step lasts longer than its timeout.

## After stage 9

- Run all stage scripts once more (`tests/run_blowmoulder.py`, shared code `tests/suite.py`); `progress.md` part A done.
- Then **part B, the Python simulator** gets its own plan (`docs/simulator/roadmap.md`, new section "Blow moulder simulator": hopper and loader, the preform's surface temperature under `LampPower` (first-order lag, the P offset visible), arm and mould positions, pressure build-up and exhaust as raw counts, ejector onto C1 replacing `Supply`, `BlowmoulderPlc` on port 5025).
- The **first conveyor link of the line** becomes real: stop the filler → C1 fills → S1 → blow moulder **Blocked** with a blown bottle waiting in the open mould (report 5.5).
