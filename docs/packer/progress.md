# Packer (`plc-packer`) – Progress

Groups single labelled bottles from C3 into **6-packs (2×3)**, wraps each group in shrink film and shrinks the film in a hot-air **oven**. Multipack shrink-wrapper without tray, last machine of the line. Fourth machine, the second stretch goal after scope option B (`docs/scope-options.md`, report section 11 step 5).
Design reference: `docs/project-report.md` sections 4.5 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms).
Packer docs: `io-list.md`, `state-diagram.md`, `ladder-roadmap.md` (part A: stages 0–9 of the PLC program).

**Current step:** part A paper complete (2026-11-21). Ladder stage 0 in progress: container `plc-packer` and project `plcs/packer/` created, test upload done, test rung removed, `FB_Motor` / `FB_Valve` copied from the labeler. `plc-packer` on its own volumes `packer-data` / `packer-build` (volume bug fixed). **Open:** upload the packer and the labeler again (labeler check `run_labeler.py --quick`); declare the variables and constants; `test_packer_stage0.py`.

## Work in three parts

Planned and built one after the other; each part gets its own planning step when it starts, so a later part is never designed before the earlier one has fixed its addresses.

| Part | Content | Planned in | Gate |
| --- | --- | --- | --- |
| **A. PLC (ladder)** | Paper (I/O list, state diagram, ladder roadmap), ladder stages, Modbus test scripts + `run_packer.py`; runtime `plc-packer`, Modbus 5024, editor 127.0.0.4:8443, project `plcs/packer/` | `ladder-roadmap.md` | paper reviewed before the first rung |
| **B. Simulator** | Address map, collation / wrap / oven physics, palletizer stand-in replacing `PackerStandIn`, `PackerPlc` link, closed loop with four PLCs, commands + scenarios, panel tab 4 | `docs/simulator/roadmap.md`, section "Packer simulator" | starts when part A is complete |
| **C. HMI (FUXA)** | Device + tags, header for four machines, Packer screen, packer block on the Line view, alarms, trends (oven temperature) | `docs/hmi/roadmap.md`, section "Packer screens" | plan → user OK → build |

## Concept

Top view, bottles move left to right:

```
   C3 (packer drives the motor)      COLLATION          WRAP CONVEYOR with film slot                 OVEN (hot air)                OUTFEED
 ─► [B][B][B][B][B] ─┤gate├─(count)─► ┌─────┐ group  ► ═══ [P] ═══╗║╔═════ wrap bar ↻ ═══ ─► ═══[▒▒]════[▒▒]═══ ─────► [P][P][P] ─► palletizer
     ▲ C3_Full                        │B B B│ pusher      film stop ║▲ cut sheet            ║ heater + blower  ║ oven     ▲ outfeed   (stand-in in
     (wired to the labeler,           │B B B│                       film roll, feed,        ═════════════════════ conveyor │ full        the simulator)
      exists already)                 └─────┘                       knife                   entry sensor     exit sensor
                                      2×3 group
```

1. **Collation.** A gate on C3 lets bottles in; a count sensor behind the gate pulses once per bottle and a **CTU counts to 6** (lane dividers form the 2×3, the PLC only sees the count). At 6 the gate shuts. When the wrap conveyor is clear, the group pusher puts the group onto it, and collation starts the next group at once.
2. **Wrap** (film-slot wrapper, the machine the user saw). Works **in parallel** with collation: the pack stops before a narrow slot in the conveyor; the film is fed up through the slot to the length the operator set (`FilmLength`, counted with a pulse sensor) and **cut first**; then the pack drives onto the front edge of the sheet, a wrap bar on its own motor lifts the back of the sheet over the top and lays it in front, and the pack runs on into the oven. No wrap station, no transfer pusher: the pack only moves forward.
3. **Oven.** Hot air shrinks the film tight. It runs continuously, separate from both sequences, **only in Auto**: **heater + blower**, the heater only while air really flows (airflow switch), and the blower **keeps running after the oven switches off (TOF run-on)** to cool the heater. The oven conveyor runs while the oven is on or packs are still inside; several packs can be inside at once, counted with a **CTUD** (up at the entry sensor, down at the exit sensor). Packs inside and no exit for too long = oven jam.
4. **Outfeed.** Finished packs collect on an outfeed table with a full sensor; the palletizer stand-in (simulator) takes them away. Table full for long = Blocked.

Timing: the labeler needs ~6 s per bottle, so one pack every ~36 s; the packer is faster and is often Starved (realistic, not a fault).

## Why the packer next

- Report order (section 11 step 5; the blow moulder last).
- New syllabus angles, unused so far (filler / syrup room / labeler use only TON, TP, CTU): **TOF** (blower run-on), **CTUD** (packs in the oven), a CTU with preset 6 that resets per group; **two sequencers in parallel** (collation and wrap) plus a continuous process (the oven).
- Fits the line without changing another PLC: it drives C3, whose full sensor `C3_Full` the labeler already reads; it replaces `PackerStandIn` only.
- Makes the line's result visible: finished packs.

## Decisions

- 2026-11-21 (review): **film-slot wrapper** instead of a wrap station: film fed to length and **cut first**, the moving pack drives onto the sheet, a **wrap bar on its own motor** lays it over the top (one turn, `Wrap_Home`). Wrap station, transfer pusher, `Transfer_Home`, `Group_On_Wrap` and the memory bit `Pack_Wrapped` removed: the pack only moves forward, a restart can never wrap it twice.
- 2026-11-21 (review): **film length = operator setpoint** `FilmLength` (%MW33, mm), **measured** with an encoder `Film_Pulse` (50 mm per pulse) and a CTU (`FeedPulses = FilmLength / 50`); the count is kept through Stop / E-stop, a restart feeds only the rest.
- 2026-11-21 (review): **feed only when a pack waits** at the film stop `Pack_At_Film`; **`Film_In_Slot`** sensor checks "slot empty" before a feed and "sheet there" before the pack drives on.
- 2026-11-21 (review): **knife** ON for the whole CUT step, ended by a 0.3 s TON (not a TP).
- 2026-11-21 (review): next group pushed only when the wrap conveyor is clear: "clear belt" timer `Wrap_Clear` (belt ran empty for `ClearTime`), no extra sensor.
- 2026-11-21 (review): **oven heats only in Auto** (off in Manual and Mode Stop; Cmd Stop keeps it hot).
- 2026-11-21 (review): **oven conveyor := `Oven_On OR Packs_Inside`**: always runs in a hot oven (no belt standing in the heat, a missed count cannot leave a pack inside), empties the oven after the switch-off.
- 2026-11-21: **Q1–Q6 answered as recommended** (`io-list.md`, end): `WrapStep` %MW52 and one State (Fault > Blocked > Starved > Running); oven hysteresis + ready window in 0.1 °C; outfeed full stops only the transfer into the oven; `PackCount` with `Cmd = 4`, no shift total; Stop finishes started work, the oven empties itself; FAULT / warning split incl. the airflow switch `Airflow_OK` + `Alm_AirflowLost` (%MX7.0).
- 2026-11-21: **pipelining**: collation counts the next group while the previous one is wrapped (two sequencers in parallel, one per station), not one group at a time.
- 2026-11-21: the shrink tunnel is called the **oven** (heater + blower + oven conveyor; `Oven*` names). Not to confuse with the blow moulder's preform oven, a different PLC.
- 2026-11-21: **blower run-on with TOF**, heater only while the blower runs.
- 2026-11-21: **CTUD** counts the packs inside the oven.
- 2026-11-21: film: **roll empty sensor plus a film-low warning** (as the labeler's label roll: `Film_Empty` stops the wrap, `Film_Low` is an early HMI warning on the sensor bit).
- 2026-11-21: docs in the same layout as the other machines (`progress.md`, `io-list.md`, `state-diagram.md`, `ladder-roadmap.md`), the progress checklist split into the three parts A / B / C.
- 2026-11-21: one pack = 6 bottles (2×3), fixed (constant, not an HMI setpoint).

## Open questions (answered 2026-11-21: recommended answers in `io-list.md` confirmed)

1. **Two step words**: the standard layout has one `Step` (%MW51). Collation step in %MW51 and wrap step in a second word (%MW52)? Which one decides `State`?
2. **Oven temperature control**: hysteresis on `OvenTemp` (heater on/off) with a ready window, as the labeler's glue? Units 0.1 °C as elsewhere?
3. **Outfeed full**: stop only the transfer into the oven (the oven conveyor keeps running so no pack stays in the heat), or also the oven conveyor? What happens to a pack that reaches the oven exit while the table is full?
4. **Counters**: finished packs as the main counter (%MW53) with `Cmd = 4` reset (as the labeler); a shift total too, or not?
5. **Stop and resume**: Stop finishes the group being wrapped and empties the oven before IDLE? Bottles already counted in the collation stay there (the CTU keeps its count)?
6. **Alarms**: film empty (warning / Starved), oven temperature low / high, oven heat-up timeout, pack jam in the oven (CTUD > 0, no exit), wrap timeout, pusher timeouts, outfeed blocked too long (warning) – which are FAULT?

## Checklist

### A. PLC (ladder)
#### A0. Paper
- [x] Concept: what the machine is, zones, sequence outline, decisions (2026-11-21)
- [x] I/O list draft (`io-list.md`, 2026-11-21; second version after the review, 14 sensors, 10 outputs, 10 alarms)
- [x] State diagram draft (`state-diagram.md`, 2026-11-21; second version after the review: film-slot wrapper)
- [x] Review both drafts, answer the open questions (Q1–Q6 and the review decisions, 2026-11-21)
- [x] Ladder roadmap (`ladder-roadmap.md`, stages 0–9, 2026-11-21; new blocks `TOF`, `CTUD`, `GT`, `LIMIT` checked in the Editor 4.3.3 library)
- [x] Report updated (4.5, 6.2, 8.1, 11), `CLAUDE.md` (2026-11-21)

#### A1. Ladder logic (OpenPLC Editor v4) – stages in `ladder-roadmap.md`
- [ ] Stage 0 – Container `plc-packer` (Modbus 5024, editor 127.0.0.4:8443), project `plcs/packer/`, `mx_bits` = 64, variables, FBs copied
  - [x] Project `plcs/packer/` (device 127.0.0.4, Modbus server port 502, labeler buffer mapping, `mxBits` 64, task 20 ms), test upload (2026-11-21)
  - [x] Test rung and its variables removed, `main` empty (2026-11-21)
  - [x] `FB_Motor` / `FB_Valve` copied unchanged from `plcs/labeler/` (2026-11-21)
  - [x] Compose: own volumes `packer-data` / `packer-build` (2026-11-21)
  - [ ] Upload packer + labeler again, `run_labeler.py --quick`
  - [ ] Variables (`io-list.md`) and constants (`state-diagram.md`) declared
  - [ ] `test_packer_stage0.py`
- [ ] Stage 1 – Modes and safety (copied from the labeler), defaults, `Cmd` 1–4, heartbeat
- [ ] Stage 2 – Oven and film values: `Oven_Ready` window, `Heat_Demand` hysteresis, `Oven_BelowMax`, `LIMIT` + `DIV` → `FeedPulses`
- [ ] Stage 3 – Conditioning: `Outfeed_Full` (`Table_Full`), Starved filter timer, `Wrap_Home` / `Oven_Exit` edges
- [ ] Stage 4 – Collation sequencer: step decodes, `Oven_On`, `Group_Counter`, `Wrap_Clear`, Start of both, transitions 0–25, abort
- [ ] Stage 5 – Wrap sequencer: `Feed_Counter`, `Sheet_Waiting`, `Cut_Timer`, transitions 10–34, restart cases
- [ ] Stage 6 – The oven: CTUD `Oven_Counter`, heater, blower TOF, oven conveyor, FB instances
- [ ] Stage 7 – Outputs and interlocks: jog watchdog, gate, pusher, mutual lock-out bar / feed / knife, wrap conveyor, C3, FB instances
- [ ] Stage 8 – Stop at safe points, `PackCount` with `Cmd = 4`, `Starved`, `State`
- [ ] Stage 9 – Alarms and FAULT (10 alarm bits, both sequencers to 900)

### B. Python simulator – stages in `docs/simulator/roadmap.md`
- [ ] Planned (after part A)

### C. HMI (FUXA) – stages in `docs/hmi/roadmap.md`
- [ ] Planned (after part B), plan approved by the user

## Log

- 2026-11-21: packer chosen as the next machine (report order, new TOF / CTUD, C3 already in place). Concept decided: collation with a CTU to 6, wrap station in parallel (pipelining), oven (heater + blower, TOF run-on, CTUD for the packs inside), outfeed table with a palletizer stand-in; film roll empty + film low. Work split into three parts A PLC / B simulator / C HMI.
- 2026-11-21: I/O list first draft: 12 sensors (`%MX0` + `%MX4.0–4.3`, new airflow switch `Airflow_OK`), `OvenTemp`, 3 setpoints, 7 manual buttons, 10 outputs `%QX0.0–1.1` (gate held open while counting, not a TP pulse), second step word `WrapStep` %MW52, `PackCount` %MW53, computed `GroupCount` / `PacksInOven` %MW40–41, 9 alarms (ninth `Alm_AirflowLost` in `%MX7.0`). Recommended answers to Q1–Q6 marked ★.
- 2026-11-21: Q1–Q6 confirmed. State diagram draft: two sequencers (collation 0/5/10/15/20/25, wrap 0/10/20/25/30/40/45, both 900), hand-over through `Group_On_Wrap` / `Pusher_Home` / `WrapStep = 10`; oven rules (hysteresis, TOF blower run-on, airflow interlock, CTUD with the conveyor running only while packs are inside, jam timer restarted at each pack out); one State from both; resume table. New details marked ★: knife as a timed step (not TP), `Pack_Wrapped` (%MX3.6), oven off in Mode Stop. I/O list synced.
- 2026-11-21: state diagram review with the user: the wrap redesigned as the film-slot wrapper the user saw (cut first, pack drives onto the sheet, wrap bar on its own motor), film length as an operator setpoint measured by a pulse counter, feed only with a pack waiting, `Film_In_Slot` sensor; knife as a timed step; oven only in Auto; oven conveyor `Oven_On OR Packs_Inside`. I/O list and state diagram rewritten (second version): wrap steps 10 WAIT_PACK / 20 FEED / 22 CUT / 24 READY / 30 RUN_ON / 32 WRAP / 34 RUN_OUT, `Wrap_Clear` clear-belt timer for the hand-over, `Sheet_Waiting` (sensor + counter) for the restart, 14 sensors, 8 manual buttons, 10 alarms (`%MX7.0–7.1`).
- 2026-11-21: ladder roadmap written (stages 0–9): two sequencers in separate stages (4 collation, 5 wrap), the oven as its own stage 6 (TOF, CTUD), mutual lock-out of wrap bar / film feed / knife with a fixed rung-order priority, `LIMIT` guard on `FilmLength` before the DIV. New blocks checked in the Editor 4.3.3 library (`TOF`, `CTUD` with pins CU / CD / R / LD / PV, never below 0; `GT`, `LIMIT`). Part A paper complete.
- 2026-11-21: ladder stage 0 started. User added the `plc-packer` service (127.0.0.4:8443, 5024) and created `plcs/packer/` in the Editor; test rung (`a`, `c`, `as`) uploaded. Review found the service mounts the labeler's volumes `labeler-data` / `labeler-build`: the test upload replaced the labeler's compiled program in the shared build volume (source in `plcs/labeler/` intact). Fix: own volumes `packer-data` / `packer-build` (declared under `volumes:`), then upload both projects again. Test rung and its variables removed (`main` empty, same as the labeler's first commit), `FB_Motor` / `FB_Valve` copied unchanged from the labeler (found through `pous/function-blocks/`, no `project.json` entry).
- 2026-11-21: volume bug fixed by the user: `plc-packer` mounts its own `packer-data` / `packer-build` (declared under `volumes:`). Still to do: upload the packer and the labeler again.
