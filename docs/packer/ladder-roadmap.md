# Packer – Ladder roadmap

How to build the packer PLC program in the OpenPLC Editor v4, in small stages. Same method as the filler, the syrup room and the labeler (`docs/labeler/ladder-roadmap.md`); the reusable parts (modes, FBs, heartbeat, jog watchdog, `Outfeed_Full` filter, hysteresis + window, Stop at safe points, State rungs, alarm latches) are copied, not redesigned. Design: `state-diagram.md`; addresses: `io-list.md`; checklist and decisions: `progress.md`.

New for this machine, and why the stages are cut as they are: **two sequencers** (stages 4 and 5, one each, so each can be tested alone), a **continuous oven** with **TOF** and **CTUD** (stage 6, its own stage before the other outputs), and **10 stages** instead of 8.

## Working rule for every stage

1. Build only the rungs of this stage.
2. Compile and upload (runtime `plc-packer`, device IP `127.0.0.4`).
3. **Test by forcing** in the debugger (the "Debugger" line of each stage).
4. **Release all forces** (a leftover force blocks Modbus writes later).
5. Run the Modbus test script for the stage (`simulator/tests/`, against port 5024) and add it to `run_packer.py`.
6. Tick it in `progress.md`; the user commits.

Never start the next stage with a failing test. **Blocks checked in the Editor 4.3.3 library (2026-11-21):** used before – `ADD`, `SUB`, `DIV`, `EQ`, `GE`, `LE`, `LT`, `XOR`, `MOVE`, `TON`, `CTU`, P contacts; **new for this machine** – `TOF` (pins `IN`, `PT` → `Q`, `ET`), `CTUD` (`CU`, `CD`, `R`, `LD`, `PV` → `QU`, `QD`, `CV`; never counts below 0; a rising edge on `CU` and `CD` in the same scan changes nothing), `GT`, `LIMIT` (`MN`, `IN`, `MX`). Still look at each new block once in the library panel before its first rung (pin order in the box).

## Program structure

One program `main`, sections in this order (a value is computed **above** every rung that uses it in the same scan – filler decision 2026-11-01):

```
main
 ├─ 1  Modes and safety        (copied from the labeler: defaults, mode/cmd decode incl. Cmd 4, Master_Enable, heartbeat)
 ├─ 2  Oven and film values    (Oven_Ready window, Heat_Demand hysteresis, Oven_BelowMax, FilmLength LIMIT + DIV -> FeedPulses)
 ├─ 3  Conditioning            (Outfeed_Full filter, Starved filter timer, Wrap_Home / Oven_Exit edges)
 ├─ 4  Step decode             (Step -> Step_Idle ... Step_Fault; WrapStep -> Wrap_Idle ... Wrap_Fault; Seq_Enable, Oven_On)
 ├─ 5  Counters and done bits  (Group_Counter, Feed_Counter, Cut_Timer, Clear_Timer, Sheet_Waiting, Oven_Counter CTUD)
 ├─ 6  Sequencer transitions   (abort, Start of both, collation, wrap; Stop_Request in stage 8)
 ├─ 7  Outputs                 (jog watchdog, oven: heater / blower TOF / oven conveyor, request rungs, interlocks, FB instances)
 ├─ 8  Counter and status      (PackCount with Cmd 4 reset, Starved, State)
 └─ 9  Alarms                  (timeouts, over-temperature, airflow, oven jam, latches, reset, MOVE 900 last)
Function blocks: FB_Motor, FB_Valve (copied from plcs/labeler/)
```

---

## Stage 0 – Project and container setup

- **Fourth runtime container.** `docker-compose.yml` gets a service `plc-packer`, a copy of `plc-labeler` with host Modbus port **5024**, editor at **`127.0.0.4:8443`**, own volumes `packer-data` / `packer-build` (report 6.1), also declared in the `volumes:` list at the bottom. **Change the volume names when copying the labeler service:** with `labeler-data` / `labeler-build` both runtimes share one build volume and an upload to one replaces the other's program (happened 2026-11-21).
- New project `plcs/packer/`; *Device*: board **OpenPLC Runtime v4**, IP **127.0.0.4**; *Servers*: Modbus TCP server, port 502 (inside the container), same buffer mapping as the labeler (`plcs/labeler/devices/servers/server.json`; the packer's file is `modbusServer.json`, like the syrup room's – the name does not matter), **`mx_bits` = 64** (covers `%MX4` sensors and the `%MX7` alarms). Without the mapping the compile aborts with "Nothing produces %MW / %QX on this target".
- Declare every variable from `io-list.md` (located), and the constants of `state-diagram.md` (`PackSize`, `FilmPulseMM`, `StarvedDelay`, `ClearTime`, `PushTimeout`, `FeedTimeout`, `CutTime`, `SheetCheck`, `WrapTimeout`, `HeatTimeout`, `OvenMax`, `BlowerRunOn`, `AirflowTimeout`, `OvenJamTime`, `BlockedWarn`) plus `FilmMin` 600 / `FilmMax` 1200 as internal variables with initial values.
- Copy `FB_Motor` and `FB_Valve` from `plcs/labeler/` (no changes).
- One test rung (`E_Stop_OK → ConveyorMotor3`), compile, upload, then delete it.

**Test** (`test_packer_stage0.py`): write `OvenTemp` (HR 1036), `E_Stop_OK` (coil 8201), `Film_Low` (coil 8225, `%MX4`) on port 5024 and read them back; coil 8249 (`Alm_WrapTimeout`, `%MX7.1`) can be read (FALSE: the `%MX7` byte is served). The filler (502), syrup room (5022) and labeler (5023) are unaffected (SKIP when that runtime is not up). Needs the uploaded program: without `libplc_*.so` the runtime starts no Modbus server.

## Stage 1 – Modes and safety  *(weeks IV, VI, VIII, X)*

Copy section 1 of the labeler and change only the defaults:

- First-scan defaults (`NOT Init_Done` → MOVE chain → `(S) Init_Done`): `OvenSetpoint` 1900, `OvenHyst` 50, `OvenBand` 150, `FilmLength` 850.
- `ModeReq` → `Mode_Stop/Auto/Manual` (EQ); `Cmd` → `Start_Cmd/Stop_Cmd/Reset_Cmd/Count_Reset_Cmd` (EQ 1–4); then MOVE 0 → `Cmd`.
- `Master_Enable := E_Stop_OK AND NOT Alm_CommLost`; heartbeat watchdog (XOR change detect `Hb_Changed`, TON 3 s on `NOT Hb_Changed`). **Rung order:** `Master_Enable` after the watchdog's `Alm_CommLost` rungs.

**Test** (`test_packer_stage1.py`): after upload HR 1054–1057 hold the defaults; a later write is kept; `Cmd` 1–4 each read back as 0 after one scan; heartbeat frozen 3 s → `Alm_CommLost`, frozen at FALSE **and** at TRUE (the labeler's bug B8); Reset with a dead heartbeat keeps the alarm. Debugger: `E_Stop_OK` FALSE → `Master_Enable` FALSE.

## Stage 2 – Oven and film values  *(weeks IV, VIII: math, compare, seal-in)*

All rungs combinational, every scan (`state-diagram.md` rules 4 and 6):

```
──[SUB OvenSetpoint, OvenBand → Oven_Low_Lim]──[ADD OvenSetpoint, OvenBand → Oven_High_Lim]──[SUB OvenSetpoint, OvenHyst → Heat_On_Lim]──

──┤ GE OvenTemp,Oven_Low_Lim ├──┤ LE OvenTemp,Oven_High_Lim ├────────────────────( Oven_Ready )──     window = two compares in series

──┤ LT OvenTemp,Heat_On_Lim ├────────────────────────────────────┬────────────────( Heat_Demand )──    hysteresis (heating)
──┤ Heat_Demand ├──┤ LT OvenTemp,OvenSetpoint ├──────────────────┘

──[LT OvenTemp, OvenMax → Oven_BelowMax]──                                                             used by the heater (stage 6) and the alarm (stage 9)

──[LIMIT MN:=FilmMin, IN:=FilmLength, MX:=FilmMax → Film_Len_Used]──[DIV Film_Len_Used, FilmPulseMM → FeedPulses]──
```

Copy of the labeler's stage 2 with the oven names (compare outputs into helper BOOLs: a compare block's ENO only copies EN). **New:** `LIMIT` keeps a wrong `FilmLength` (0, negative, 5000 typed in the HMI) inside 600–1200 mm before the division, so `FeedPulses` is always 12–24 – the same idea as the syrup room's guard against division by zero, with one block.

**Test** (`test_packer_values.py`): `OvenTemp` 1740 / 1750 / 1900 / 2050 / 2060 → `Oven_Ready` F / T / T / T / F; `OvenBand` 100 → 1790 F, 1800 T; ramp 1900 → 1840 → 1860 → 1900 → 1880 → `Heat_Demand` F → T → T → F → F; `FilmLength` 850 → `FeedPulses` 17, 870 → 17 (truncation), 600 → 12, 1200 → 24, 0 → 12, 5000 → 24.

## Stage 3 – Conditioning  *(weeks IV, VII: seal-in, timers)*

- **`Outfeed_Full`** (%MX3.0): copy of the labeler's filter with `Table_Full` instead of `C3_Full`: `Full_OnTimer` TON 2 s, `Full_OffTimer` TON 3 s, seal-in.
- **Starved filter timer:** `Gate_Empty_Timer := TON(IN := NOT Bottle_At_Gate, PT := StarvedDelay)` (the `Starved` coil comes in stage 8).
- **Edges:** `┤P Wrap_Home├ → ( Wrap_Edge )` (end of one bar turn), `┤P Oven_Exit├ → ( Exit_Edge )` (restarts the jam timer). The counters count their own edges; these two are for a step transition and a timer.

**Test** (`test_packer_conditioning.py`): `Table_Full` ON 1 s → `Outfeed_Full` F, ON 3 s → T, OFF 2 s → still T, OFF 4 s → F; blinking 0.5 s / 0.5 s for 5 s → stays F. Debugger: `Gate_Empty_Timer.ET` runs while `Bottle_At_Gate` is FALSE; `Wrap_Edge` is TRUE for one scan per rising edge (watch with a counter or force slowly).

## Stage 4 – Collation sequencer  *(weeks VII, VIII, XI: CTU with a preset, compare, move, sequencer)*

- **Step decode** of `Step` (EQ → `Step_Idle`, `Step_Warm`, `Step_Count`, `Step_WaitWrap`, `Step_Push`, `Step_Retract`, `Step_Fault`) and of `WrapStep` (EQ → `Wrap_Idle`, `Wrap_WaitPack`, `Wrap_Feed`, `Wrap_Cut`, `Wrap_Ready`, `Wrap_RunOn`, `Wrap_Wrap`, `Wrap_RunOut`, `Wrap_Fault`; the wrap decode is used from stage 5, but `Wrap_WaitPack` is the hand-over condition here). `Seq_Enable := Mode_Auto AND Master_Enable`.
- **`Oven_On`** (%MX3.7) := `Master_Enable AND Mode_Auto AND NOT Step_Fault` (rule 6; used from stage 6).
- **Done bits** (section 5): `Group_Counter := CTU(CU := Bottle_Counted, R := Step_Retract, PV := PackSize)`, `Group_Complete := Group_Counter.Q` (%MX3.1), `GroupCount := Group_Counter.CV` (MOVE); `Clear_Timer := TON(IN := Wrap_WaitPack AND Pusher_Home AND NOT Pack_At_Film, PT := ClearTime)`, `Wrap_Clear := Clear_Timer.Q` (%MX3.6) – rule 2.
- **Transitions** (section 6), no outputs yet: **Start** (Auto, `Start_Cmd`, both steps 0, no FAULT alarm – the alarm contact comes in stage 9) → MOVE 5 → `Step` and, in the same rung's branches, `Wrap_Home` → MOVE 10 → `WrapStep`, `NOT Wrap_Home` → MOVE 32 → `WrapStep`; 5 → 10 (`Oven_Ready`); 10 → 15 (`Group_Complete`); 15 → 20 (`Wrap_WaitPack AND Wrap_Clear`); 20 → 25 (`Pusher_Out`); 25 → 10 (`Pusher_Home`); 900 → 0 (stage 9).
- **Abort rung:** `NOT Master_Enable OR NOT Mode_Auto` → MOVE 0 → `Step` **and** MOVE 0 → `WrapStep`.

To test the collation alone, write `WrapStep` = 10 by hand (no wrap transitions exist yet, so it stays). From stage 5 on, hold `Film_Empty` TRUE instead: a pack at the film stop then waits in WAIT_PACK.

**Test** (`test_packer_collation.py`, G0–G21, done 2026-11-22; built with `Start_Accepted` + three MOVE rungs instead of one Start rung with branches): Start with `OvenTemp` 1500 → `Step` 5, `WrapStep` 10 (bar home) or 32 (`Wrap_Home` FALSE); `OvenTemp` 1900 → 10; 5 `Bottle_Counted` pulses → `GroupCount` 5, still 10; 6th → `Group_Complete`, 15; `Wrap_Clear` needs 3 s of `WrapStep` 10, pusher home, no pack at the film: with `Pack_At_Film` TRUE → stays 15, FALSE for 2 s → 15, for 3 s → 20; `Pusher_Out` → 25 and `GroupCount` 0; `Pusher_Home` → 10. A group "pushed halfway" (Mode Stop in 20, Start) → back to 15 with `Group_Complete` still TRUE. Mode Stop → both steps 0.

## Stage 5 – Wrap sequencer  *(weeks VII, VIII, XI: CTU with a computed preset, TON as a step time, compare)*

- **Done bits** (section 5): `Feed_Counter := CTU(CU := Film_Pulse, R := Wrap_Ready, PV := FeedPulses)`, `FeedCount := Feed_Counter.CV` (MOVE, %MW43); `EQ FeedCount, 0 → Feed_Zero`, `Sheet_Waiting := Film_In_Slot AND Feed_Zero` (rule 4); `Cut_Timer := TON(IN := Wrap_Cut, PT := CutTime)`.
- **Transitions** from the step table: 10 → 24 (`Pack_At_Film AND Sheet_Waiting`), 10 → 20 (`Pack_At_Film AND NOT Sheet_Waiting AND NOT Film_Empty`); 20 → 22 (`Feed_Counter.Q`); 22 → 24 (`Cut_Timer.Q`); 24 → 30 (`Film_In_Slot AND Oven_Ready AND NOT Outfeed_Full AND Wrap_Home`); 30 → 32 (`NOT Pack_At_Film`); 32 → 34 (`Wrap_Edge`); 34 → 10 (`Oven_Entry`).
- **Rung order inside 10:** the two transitions out of WAIT_PACK are exclusive (`Sheet_Waiting` / `NOT Sheet_Waiting`), so their order does not matter; still write 10 → 24 first (the restart case) for readability.

**Test** (`test_packer_wrap.py`, W0–W27, done 2026-11-22; the collation test then holds `Film_Empty` TRUE so the wrap side waits in 10), collation idle, sensors written by hand: `Pack_At_Film` → 20; 16 `Film_Pulse` pulses → still 20, 17th → 22; after 0.3 s → 24 and `FeedCount` 0; without `Film_In_Slot` → stays 24; with it, `Oven_Ready` and the bar home → 30; `Pack_At_Film` off → 32; `Wrap_Home` off then on → 34; `Oven_Entry` → 10. Variants: `Outfeed_Full` in 24 → stays 24; `OvenTemp` 1500 in 24 → stays 24; `Film_Empty` with a pack at the film → stays 10; **restart cases** (Mode Stop, Start): 8 pulses fed → back in 20 with `FeedCount` 8, 9 more → 22 (only the rest); cut sheet in the slot (`Film_In_Slot`, `FeedCount` 0) → 10 → 24 directly, no feed; bar not home at Start → 32, `Wrap_Home` rising → 34. `FilmLength` 600 → 12 pulses end FEED.

## Stage 6 – The oven: heater, blower TOF, CTUD, oven conveyor  *(weeks III, VII, VIII, IX: TOF, CTUD, GT, FBs)*

The oven is independent of both sequencers, so it is built and tested as one block (`state-diagram.md` rules 6–8). Its rungs sit in section 5 (CTUD) and section 7 (outputs):

- **CTUD** (section 5): `Oven_Counter := CTUD(CU := Oven_Entry, CD := Oven_Exit, R := Oven_Count_Reset, LD := FALSE, PV := 32767)`, `PacksInOven := Oven_Counter.CV` (MOVE, %MW41), `GT PacksInOven, 0 → Packs_Inside`. `Oven_Count_Reset` stays FALSE until stage 9 (Reset of `Alm_OvenJam`).
- **Heater** (section 7): `Req_Heater := Heat_Demand AND Oven_On AND Airflow_OK AND Oven_BelowMax` – no Manual branch (no button, Auto only).
- **Blower:** `Blower_TOF := TOF(IN := Oven_On, PT := BlowerRunOn)`, `Req_Blower := Blower_TOF.Q`.
- **Oven conveyor:** `Req_OvenConv := (Oven_On OR Packs_Inside OR (Mode_Manual AND Man_OvenConveyor AND NOT Jog_Block)) AND NOT Step_Fault` (the Manual branch is added once the jog watchdog exists in stage 7).
- **FB instances**, `Enable` = `Master_Enable`: FB_Motor `Mot_Heater` (run hours = heater hours), `Mot_Blower`, `Mot_OvenConv`.

**Test** (`test_packer_oven_outputs.py`, O0–O17, done 2026-11-22; `Oven_BelowMax` tested with `OvenSetpoint` 2500 so `Heat_Demand` is TRUE above `OvenMax`): Mode Stop → heater, blower, conveyor off; Auto (no Start needed) with `OvenTemp` 1800 and `Airflow_OK` → heater, blower, conveyor on; `Airflow_OK` FALSE → heater off, blower on; `OvenTemp` 1900 → heater off (hysteresis), blower on; `OvenTemp` 2300 → heater off (`Oven_BelowMax`); Mode Stop → heater off at once, **blower still on after 29 s, off after 31 s** (TOF); conveyor off with no packs; 2 `Oven_Entry` pulses → `PacksInOven` 2 → conveyor runs in Mode Stop; 1 `Oven_Exit` → 1, still running; 2nd → 0, conveyor stops; an extra `Oven_Exit` at 0 → stays 0 (CTUD floor); E-stop → all off at once, blower too.

## Stage 7 – Outputs and interlocks  *(weeks III, IV, VI, IX: motor control, interlocks, mutual lock-out, FBs)*

Section 7, in this order (as the labeler):

- 7.0 **Jog watchdog** (`Jog_TON` 10 s on any of the eight `Man_*`, `Jog_Block` %MX3.2) – copy; then add the Manual branch to `Req_OvenConv` (stage 6).
- 7.1 **Gate:** `Req_Gate := ((Seq_Enable AND Step_Count AND NOT Group_Complete) OR Manual) AND Pusher_Home AND NOT Group_Complete` – held, not a TP.
- 7.2 **Group pusher:** `Req_Push := (Step_Push OR Manual)` – no interlock (rule 12).
- 7.3 **Wrap bar, film feed, knife – mutual lock-out with a fixed priority** (rule 12; the order of the rungs *is* the priority):

  ```
  Req_WrapBar  := (Wrap_Wrap OR Manual) AND NOT FilmFeed AND NOT Knife          (FilmFeed / Knife = the %QX outputs, last scan:
  Req_FilmFeed := ((Wrap_Feed AND NOT Feed_Counter.Q) OR Manual)                   an "auxiliary contact" of the other drive)
                  AND Wrap_Home AND NOT Req_WrapBar
  Req_Knife    := (Wrap_Cut OR Manual) AND NOT Req_FilmFeed AND NOT Req_WrapBar
  ```

  If two of them are asked for in the same scan (two Manual buttons), the one written higher wins and the others stay off – no flicker. With only the %QX outputs in every rung, both would start in the same scan, block each other in the next one, and toggle.
- 7.4 **Wrap conveyor:** `Req_WrapConv := ((Wrap_WaitPack AND NOT Pack_At_Film) OR Wrap_RunOn OR Wrap_Wrap OR Wrap_RunOut OR Manual) AND NOT Req_FilmFeed AND NOT Req_Knife`.
- 7.5 **C3:** `Req_C3 := (NOT Step_Fault AND NOT Mode_Manual) OR Manual` (runs in IDLE too, as the labeler's C2).
- Every Auto branch carries `Seq_Enable`; every Manual branch is `Mode_Manual AND Man_… AND NOT Jog_Block`; interlocks in series **after** the OR.
- 7.6 **FB instances**, `Enable` = `Master_Enable`, one writer per `%QX`: FB_Motor `Mot_C3`, `Mot_Gate`, `Mot_WrapConv`, `Mot_FilmFeed`, `Mot_WrapBar`; FB_Valve `Vlv_Push`, `Vlv_Knife`.

**Test** (`test_packer_outputs.py`): each step of both sequencers switches exactly its outputs (one group and one pack driven through by writing the sensors); the gate shuts in the scan of the 6th count; gate blocked while the pusher is out; FEED stops at `Feed_Counter.Q`; wrap conveyor stops when `Pack_At_Film` rises in WAIT_PACK; feed blocked with the bar not home. Manual: hold-to-run; `Man_FilmFeed` + `Man_WrapBar` together → only the bar runs; `Man_Knife` while feeding → off; `Man_WrapConveyor` while the knife is held → off; button held 11 s → `Jog_Block`. E-stop → all off.

## Stage 8 – Stop, counter, State  *(weeks IV, VII: latch, CTU)*

- `Stop_Request := (Stop_Cmd OR Stop_Request) AND NOT (Step_Idle AND Wrap_Idle)`; "go to 0" rungs at the safe points of rule 9: collation WARM_UP / COUNT / WAIT_WRAP at once, end of RETRACT; wrap WAIT_PACK / FEED / READY at once, end of RUN_OUT (`Oven_Entry`); `NOT Stop_Request` in the normal transitions out of those steps, so a lower rung cannot overwrite the MOVE 0. **PUSH, CUT, RUN_ON, WRAP get no Stop branch.**
- `Pack_Counter` (CTU, `CU := Oven_Exit`, `R := Count_Reset_Cmd`, `PV` 32767, `CV` → `PackCount` %MW53).
- `Starved` (%MX3.5) := `Wrap_WaitPack AND ((Step_Count AND Gate_Empty_Timer.Q AND NOT Pack_At_Film) OR Film_Empty)` (rule 10).
- `State` (%MW50): mutually exclusive MOVE rungs in the priority of rule 10: Fault (`Step_Fault`), Stopped (both idle, or Mode Stop / Manual), Blocked (`Wrap_Ready AND Outfeed_Full`), Starved, Running.

**Test** (`test_packer_stop_state.py`): Stop in COUNT → `Step` 0 at once, `GroupCount` kept, Start counts on; Stop in PUSH → RETRACT, then 0; Stop in FEED → 0 with `FeedCount` kept, Start feeds the rest; Stop in WRAP → RUN_OUT, `WrapStep` 0 at `Oven_Entry`; Stop in READY → 0, the sheet stays, Start → 10 → 24; two `Oven_Exit` → `PackCount` +2, `Cmd` 4 → 0, `Cmd` 3 does not touch it; gate gap of 1 s → State 1, of 3 s (no pack at the film) → 2; gap of 3 s while a pack is wrapped → 1; `Film_Empty` with a pack at the film → 2; `Outfeed_Full` in READY → 3; Mode Stop / Manual → 0.

## Stage 9 – Alarms and FAULT  *(week VII: timers as timeouts, week VIII: compare)*

- `Alm_FilmFault` (%MX5.0): `Feed_Timer` (TON `FeedTimeout`, `IN := Wrap_Feed`) OR `Sheet_Timer` (TON `SheetCheck`, `IN := Wrap_Ready AND NOT Film_In_Slot`).
- `Alm_PackJam` (%MX5.1): `Push_Timer` (`IN := Step_Push`), `Retract_Timer` (`IN := Step_Retract`), `RunOn_Timer` (`IN := Wrap_RunOn`), `RunOut_Timer` (`IN := Wrap_RunOut`), all `PushTimeout` – one timer per step, so each restarts at its own step (syrup room lesson).
- `Alm_OutfeedBlocked` (%MX5.2): `Blocked_Timer` (TON `BlockedWarn`, `IN := Wrap_Ready AND Outfeed_Full`) – warning, no FAULT.
- `Alm_FilmEmpty` (%MX5.4): `= Wrap_WaitPack AND Pack_At_Film AND Film_Empty` – warning, not latched.
- `Alm_OvenOverTemp` (%MX5.5): `NOT Oven_BelowMax` → FAULT in every mode; Reset only below `OvenMax`.
- `Alm_OvenHeatTimeout` (%MX5.6): `Heat_Timer` (TON `HeatTimeout`, `IN := Step_Warm`).
- `Alm_OvenJam` (%MX5.7): `Jam_Timer := TON(IN := Packs_Inside AND OvenConveyor AND NOT Exit_Edge, PT := OvenJamTime)`; Reset also resets the CTUD: `Oven_Count_Reset := Reset_Cmd AND Alm_OvenJam` (rule 8; the rung goes **above** the CTUD, in section 5).
- `Alm_AirflowLost` (%MX7.0): `Air_Timer := TON(IN := OvenBlower AND NOT Airflow_OK, PT := AirflowTimeout)`; Reset always allowed (rule 14).
- `Alm_WrapTimeout` (%MX7.1): `Wrap_Timer` (TON `WrapTimeout`, `IN := Wrap_Wrap`).
- Each FAULT alarm: (S) by its cause, (R) by `Reset_Cmd AND NOT cause`. `No_Fault` (De Morgan over the 7 FAULT bits), `NOT No_Fault` in the Start rung, last rung: `NOT No_Fault` → MOVE 900 → `Step` **and** MOVE 900 → `WrapStep` (last writer of both); 900 → 0 for both with Auto + `Reset_Cmd` + `No_Fault`.

**Test** (`test_packer_alarms.py`, `--quick` without the 120 s and 2 min timeouts): no alarm in a normal cycle; FEED without pulses → FAULT after 10 s, both steps 900, all off but the blower; Reset → Start → FEED continues with the kept count; READY without a sheet → FAULT after 1 s; PUSH without `Pusher_Out`, RETRACT without `Pusher_Home`, RUN_ON with the pack staying, RUN_OUT without `Oven_Entry` → each FAULT after 3 s; WRAP without the bar returning → FAULT after 5 s (`%MX7.1`); `OvenTemp` 2310 → FAULT also in Mode Stop, heater off, **blower runs 30 s more**, Reset ignored at 2310, accepted at 1900; WARM_UP held at 1000 → FAULT after 120 s; blower on without `Airflow_OK` → FAULT after 3 s, Reset accepted at once; 1 pack in, no exit for 20 s → `Alm_OvenJam`, Reset → `PacksInOven` 0; `Film_Empty` with a pack at the film → `Alm_FilmEmpty` follows the sensor; READY with `Outfeed_Full` 2 min → warning, still 24. Earlier scripts adapted so no step lasts longer than its timeout (as for the labeler).

## After stage 9

- Run all stage scripts once more (`tests/run_packer.py`, shared code `tests/suite.py`, like `run_labeler.py`); `progress.md` part A done.
- Then **part B, the Python simulator** gets its own plan (`docs/simulator/roadmap.md`, new section "Packer simulator": collation table, wrap conveyor with film slot and wrap bar, film roll with the pulse encoder, oven temperature and airflow, oven conveyor and outfeed table, palletizer stand-in replacing `PackerStandIn`, `PackerPlc` on port 5024).
- The second **real conveyor link**: stop the packer → C3 fills → the labeler's `C3_Full` → labeler **Blocked** → C2 fills → filler Blocked (report 5.5).
