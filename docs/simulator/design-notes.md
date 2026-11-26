# Python simulator – design notes (questions you should be able to answer)

Short answers to the non-obvious decisions in `simulator/src/simulator/`. Read the code next to each answer; the details and dates are in `docs/filler/progress.md` (log).

## Architecture

**Why is the physics "pure" (no Modbus, no UI)?**
So it can be tested in milliseconds without Docker (`uv run pytest`). Only `plcs.py` imports pymodbus; `plant.py` knows signals by *name*, `plcs.py` maps names to addresses.

**Does the simulator decide anything?**
No. It applies the PLC outputs to the physical world and reports sensors. The `TestController` in `tests/unit/test_plant.py` copies the PLC rules *only* to drive the physics in unit tests; from stage 6 the real OpenPLC program decides.

**What is the audit?**
Bottles carry their state (`level_ml`, `rinsed`, `capped`). When a bottle leaves the filler, the simulator checks it. A warning means the physics shows a control error (spill, two bottles in a pocket, crash on C2, not rinsed / uncapped / underfilled).

## Filler physics (`machines/filler.py`)

**How does the index belt work?**
A step timer `_phase` (seconds since the last pitch boundary) grows while `IndexMotor` is ON; at `pitch_s` every pocket shifts one place and the cap bottle leaves onto C2. Same pattern as the conveyor.

**Why two windows, `CAM_WINDOW` (15 %) and `STATION_WINDOW` (40 %)?**
Different sensors. S8 is the index cam (index position). S3–S5 are presence sensors and must still see their bottle when the belt overruns a little after the PLC switched it off (the simulator sees the switch-off only at the next tick). With a narrow window a late tick made S3–S5 go dark and the PLC indexed half-processed bottles on.

**Why does the audit compare the *rounded* fill level?**
The PLC sees `FillLevel` as an integer. A float sum can be 499.9999 ml when the PLC already read 500 and closed the valve correctly; comparing the raw float would blame the PLC wrongly.

**What is the known blind spot?**
The entry pocket has no sensor. A bottle left there by an E-stop during INDEX with C1 empty is invisible to the PLC (Starved). With bottles waiting on C1 it was worse: the restart's door pulse dropped a second bottle onto it (found by the `index_jam` scenario). The PLC now tracks the pocket with the `Entry_Load` bit (set by the door, reset by the S8 edge), so the door stays closed until the belt has moved. The simulator still models no sensor there: the PLC must remember, not see.

## Conveyors and stand-ins

**Why does `take_at_exit` reset the step timer?**
A queue standing at the door does not move; the belt slides under it. When the front bottle is taken, the next one starts from rest and needs a full slot step (0.5 s). Without this, it could appear at the door at once and a correct 0.5 s door pulse admitted two bottles on a late tick.

**Why does S7 watch C2 slot 1, not slot 0?**
With only the cap busy, WORK lasts 1.5 s, shorter than the PLC's 2 s `Outfeed_Full` filter. With S7 on slot 0 the next bottle crashed onto the one standing at the entry. Slot 1 leaves one free slot as reserve. Proven by `test_s7_slot_choice_short_work_cycle`.

## Timing (`plant.py`)

**Why sub-steps?**
The real loop's `dt` is uneven. One long step lets two things that happen one after the other happen at once (next bottle arrives at the door *and* the door timer counts the whole `dt`). Splitting into steps ≤ 0.1 s keeps the order. The total time stays `dt`, so the simulator's clock keeps pace with the PLC's timers.

**Why not simply cap `dt`?**
Then simulator time runs slower than the PLC's wall-clock timers: the PLC rinses 2 s, the simulator counts less → false "not rinsed". Tried and rejected.

**Why are S6 and S8 "kept for one write"?**
The PLC uses their rising edges, and the simulator writes once per tick. A pulse that starts and ends inside one late tick would be lost (missed S8 → belt runs on; missed S6 → `GoodCount` too low). Presence sensors are *not* stretched: a stale ON would let the PLC open a valve over a moved bottle.

**What does "slow tick" mean?**
For that long the PLC outputs were old; any audit warning right after it may be caused by the simulator, not the PLC. Physics is verified clean up to 0.4 s; the warning starts at 0.3 s.

## Modbus link (`plcs.py`)

**Why is the pymodbus client created only in `ensure_connected()`?**
pymodbus binds a client to the asyncio loop that exists when it is created. Creating it lazily (a fresh one per connection) makes `FillerPlc()` work anywhere and leaves no stale state after a PLC restart.

**Why `_live_client()` before every request?**
pymodbus raises "not connected" *synchronously* while creating a request, outside a `try` around the `await`. Asyncio switches tasks only at an `await`, so checking the link and creating the request with no `await` in between makes that raise impossible.

**`link_ok` vs `connected`?**
`connected` = TCP open. `link_ok` = the last request was answered. Docker's port proxy may accept TCP while the runtime restarts, so only an answer proves the PLC is back.

**Why is `PlcReplyError` not a lost link?**
The PLC answered with a Modbus error, e.g. illegal address when `mx_bits` = 0 in the runtime. Reconnecting would not help and would hide a configuration error.

**How is the one-writer rule enforced?**
`FillerPlc` refuses to write any address outside `SIM_WRITE_COILS` / `SIM_WRITE_REGISTERS` (`ValueError`), and `verify_writes()` reads the sensors back to detect debugger forces or a second writer. Commands and modes are written only by the HMI (`tests/hmi.py` until FUXA exists).

## Tick loop (`loop.py`)

**Why "sleep until the next deadline"?**
"Do the work, then sleep 0.1 s" adds the work time to every tick (measured: 102–110 ms). Sleeping until a fixed deadline keeps 98–102 ms. A late tick does not cause catch-up ticks; `Plant.step` handles its long `dt`.

**What happens when the PLC is not reachable?**
The physics keeps running with all outputs OFF, which is what a stopped PLC does to real machines. The problem is logged once, the link comes back by itself.

**Why is the read-back done before the next write?**
It compares the PLC with what we wrote last tick, so it covers the whole gap between two ticks, where a test script or a debugger force acts. Right after our own write it would only check ~2 ms (found by `test_loop.py`).

**Who presses Start?**
Never the simulator. Until FUXA exists, `tests/hmi.py` (or the HMI client inside `test_closed_loop.py`) acts as the HMI.

## Commands and scenarios (`commands.py`, `scenarios.py`)

**Why are commands applied only at the start of a tick?**
So the world changes in one place only, between two physics steps. A panel button pressed in the middle of a tick cannot change a value the physics is halfway through using.

**Why does `put()` check the value, and why so strictly?**
A typo fails where it was made (panel, scenario, test), not inside the tick. In Python `True` is an `int`, so `fill_flow = True` would silently mean 1 ml/s; number commands therefore refuse `bool`.

**Why are injected faults logged as info, not warnings?**
The fault is intended. Warnings mean "something went wrong in the physics" (spill, crash, underfill); what a fault scenario checks is the PLC's reaction, which the watcher logs next to the command.

**Why can a step wait for a condition, not only for seconds?**
Some faults must hit a certain moment: the jam during INDEX, the E-stop and the heartbeat loss while a bottle is half filled. A pure timeline would hit a different moment every run. Every scenario also starts with "until the PLC runs", so it does not matter whether Start is pressed before or after the simulator starts.

**Why can a scenario not press Reset or Start?**
Those are HMI commands; the simulator never writes them (one-writer rule). A fault scenario ends by removing the cause and prints what the operator has to press.

**Why does `tests/test_scenarios.py` switch to Manual before each new plant starts?**
The PLC still holds the last run's sensor values (e.g. S4 TRUE) and may be finishing a job. Without Manual it opened the fill valve over the new, empty plant in its first tick (spill at 0.0 s, found in the first run).

## Control panel (`ui/`)

**Why is the screen redrawn by a timer and not on every tick?**
Textual and the tick loop share one asyncio loop. Drawing 10 times per second inside the tick would take time from the loop and make ticks late; 4 times per second is enough for the eye. `on_tick` only stores the stats.

**Why are the text builders in `view.py` and not in the app?**
They are pure functions of the plant and the PLC status, so they are tested without a terminal (`test_view.py`) and the app keeps only layout and event handlers.

**Why `switch.prevent(Switch.Changed)` when the switches are synced?**
A scenario can change the plant (e.g. labeler stopped). The panel then moves the switch to match. Setting a switch normally fires `Switch.Changed`, which would put the same command on the queue again; `prevent` marks the sync as "not an operator action".

**Why are switches not synced while a command is queued?**
A command is applied at the next tick. Until then the plant still holds the old value, and a sync would flip the switch back for a moment.

**Why `ScenarioSlot`?**
`loop.run()` gets its scenario once, at the start. The panel starts scenarios later, so it passes an empty slot that it fills on "Run"; the loop only calls `due()` on it.

**Why is the tick less regular with the panel (85–119 ms instead of 98–102 ms)?**
The screen is drawn in the same asyncio loop, so a redraw can delay a tick a little; the next deadline then comes sooner. The physics handles any `dt` up to the 0.3 s slow-tick warning (sub-steps, kept pulses; the packer's fast counted pulses through a `PulseWire`, see the packer section), so this does not change what the PLC sees. Use `--headless` for timing measurements.

**Why no Start / Reset buttons in the panel?**
One-writer rule: commands, modes and setpoints belong to the HMI. The header shows the `tests/hmi.py` command while the PLC is in IDLE.

**Why one tab per machine and one shared scenario row?**
Report 7.5 asks for "each machine its own tab with the same layout": world on the left, controls on the right. Both machines in one screen would not fit 140×45 next to the log. Scenarios stay in one row under the tabs because a scenario is not tied to the tab you are looking at (`buffer_starved` needs both machines), and the event log shows its steps anyway. The syrup tab and the syrup scenarios are left out with `--filler-only`, so nothing in the panel can point at a syrup room that does not exist.

**Why are vent tank and drain mix tank buttons, not switches?**
They are one-off hand actions, not states: there is nothing to show afterwards, and `False` does nothing (`commands.py`). A switch would suggest a state that the plant does not have.

**Why does the plant keep `syrup_signals`?**
The pump lamps of the syrup tab need the syrup PLC's outputs of the last tick. The plant already kept one bit of them (`_syrup_pa`, the Product_Available signal); it now keeps the whole `SyrupSignals` and derives the wire from it, so the fact is stored once. It is display and wiring state, not physics: the physics still gets the outputs as an argument of `step()`.

## HMI animation data (stage 9)

**Why does the simulator write anything for the HMI at all?**
FUXA only reads PLC memory, and the PLC does not know where each bottle is (it only sees sensors). The simulator is the only place that knows, so it writes this "physical truth" into spare words (%MW70–75) that the PLC ignores.

**Why one state code per pocket instead of bits?**
A small number (0 empty … 4 capped) binds directly to an icon or colour in FUXA. Packing four attributes × four pockets into one word would use bit 15, which an INT reads as negative.

**Why is the mode read, when the simulator never writes it?**
After a PLC restart `ModeReq` is 0 (Stop) and Start is ignored. Without the mode in the panel this looked like "nothing happens"; now the header says why. Reading is allowed; only writing belongs to the HMI.

**Parameters: confirmed 2026-11-03 (no change in stage 9)**

| Parameter | Value | Checked against (PLC) | Covered by |
| --- | --- | --- | --- |
| `TICK_S` / `SUBSTEP_S` | 0.1 s | scan 20 ms; wall-clock timers | `test_loop.py`, closed loop `dt` 98–102 ms |
| `SLOW_TICK_S` | 0.3 s | – | jitter tests in `test_plant.py` |
| `SLOT_STEP_S` | 0.5 s | door TP 0.5 s admits one bottle | `test_conveyor.py`, `gaps` |
| `S7_SLOT` | C2 slot 1 | `Outfeed_Full` 2 s / 3 s filter | `labeler_stopped` (0 crashes) |
| `INDEX_PITCH_S` | 1.5 s | index jam 5 s | `index_jam` (alarm 5.0 s after INDEX) |
| `CAM_WINDOW` / `STATION_WINDOW` | 15 % / 40 % | belt overrun after S8 | `test_filler.py`, closed loop |
| `DOOR_PASS_S` | 0.3 s | door TP 0.5 s (two bottles need 0.6 s) | `test_filler.py`, 0 "two bottles" in all runs |
| `FILL_FLOW_ML_S` | 100 ml/s | fill timeout 10 s (500 ml = 5 s) | `slow_fill` (40 ml/s → alarm 10.0 s) |
| `RINSE_MIN_S` / `CAP_MIN_S` | 1.5 s / 1.0 s | RinseTime 2 s / CapTime 1.5 s | closed loop (all rinsed, capped) |
| `S6_PULSE_S` | 0.3 s | one tick minimum for the edge | GoodCount +20 = 20 bottles |
| `HEARTBEAT_S` | 1.0 s | watchdog 3 s | `heartbeat_off` (alarm 3.1 s) |
| `SUPPLY_PER_MIN` / `LABELER_TAKE_S` | 30/min / 2 s | faster than the filler (~6.5 s/bottle) | closed loop, `labeler_stopped` |

All values keep a margin to the PLC time they meet, and every one is exercised by a test that passes against the real PLC, so nothing needed tuning. The only known trade-off is the panel's tick jitter (see "Control panel").

## One plant writer at a time (`plant_lock.py`)

**Why a lock and not only the read-back check?** The read-back (`verify_writes`) notices a second writer only after it has written: by then the PLC has acted on a world that does not exist (two simulators lost bottles in a test). The lock stops the second simulator or sensor-writing test script before its first write, with a message naming the first one.

**Why `fcntl.flock` on a file in the temp dir?** The OS releases it when the process ends, also after a crash or `kill -9`, so a stale lock can never block the next run (a "file exists" check could). The file name has the PLC host and port: the unit tests use fake PLC servers on other ports and never collide with a running simulator, and a later second PLC gets its own lock. Taken in `loop.run` (idempotent per process), in the CLI before the panel starts, and by every test script that writes sensors (`tests/hmi_guard.claim_plant`). FUXA writes no sensors and takes no lock (FUXA vs scripts: HMI issue I4, a reminder).

## Syrup room physics (`machines/syrup.py`)

**Why are the flows 20 / 5 / 25 L/s and not faster?**
The PLC doses by absolute level (water to 500 L, syrup to 590 L) and sees one level per tick, so a pump runs one step too long: overshoot up to flow x 0.1 s. A water overshoot is taken away from the syrup dose, so 25 L/s would shift the ratio from 5.5:1 to about 5.8:1. At 20 L/s water the error is at most 2 L. Each dose still takes under half of the PLC's 60 s `DoseTimeout`. It is a real property of level-based dosing, not only of the simulator.

**Why does the mixing tank keep a little product after TRANSFER?**
The PLC stops TRANSFER at `MixLevel <= 5` (the sensor is rounded), so up to 5 L stay. The next water dose is that much shorter. The physics does not clean it; stage 2 has to carry its temperature and pressure.

**Why `MixHighHigh` from the true level but `MixLevel` rounded?**
The switch is hardware and sees the real liquid; the analog sensor reports an INT. `sensors()` is the only place where the true state becomes reported values, so a stuck level sensor (stage 6) is a change in one place and the switch still works.

**Why a ledger (`water_in_l`, `drawn_l`, `spilled_l`, ...)?**
So conservation can be tested: `syrup + mix + buffer + spilled = start + water_in + delivered - drawn` after any sequence of outputs. It catches liquid created or lost in clamping or in the last partial step.

**When is a pump "dry"?**
When it is on and its source is empty at the START of the step. The last partial step (0.25 L left, 0.5 L wanted) is normal operation and gives no warning.

**How does `Carb_Timer` get a meaning in the simulator?**
The PLC calls the product saturated when the pressure holds for `CarbTime` without the CO2 valve. So the physics must make the pressure fall after every fill while the liquid is not saturated, and stop falling when it is. The liquid keeps a "dissolved CO2" value (`dissolved_bar`); every second it moves toward the headspace pressure (`k`, 10x faster with the agitator), and the headspace loses a share of what the liquid gains. With the chosen numbers the valve opens 2-3 times and the timer finishes after about 30 s.

**Why is the temperature solved exactly and the pressure with Euler?**
The temperature lag has constant inputs inside one step, so the exponential is exact: it cannot overshoot the chiller temperature and a late tick equals several short ones. The gas part has two coupled terms and a valve; with `dt <= 0.1 s` (the plant's sub-step) Euler is accurate to 1e-3.

**Why does the transfer pump vent the tank?**
The tank is emptied into the unpressurised buffer and the headspace goes with it. A first idea, "vent only when the tank is empty", left 3.43 bar in the tank: the PLC leaves TRANSFER at 5 L, the next water dose lifts the level over 5 L within one tick, and the vent closed again. Venting while the pump runs gives 0.03 bar at the end. It is a simplification of a real counter-pressure line, not a PLC output.

**What does the model leave out?** Headspace expansion while the tank empties, CO2 solubility depending on the temperature ("warm product gives its CO2 back"), and a cooling speed that depends on the batch volume. None of them changes how the PLC logic is exercised.

## Labeler physics (`machines/labeler.py`)

**Why was stage 2 (label web + melt tank) built before stage 1 (pocket)?**
They are independent: the web only needs the PLC outputs and "is a clamped bottle spinning", which the caller passes in. Stage 1 builds the pocket and stage 3 puts both into `Labeler`.

**Why is `Label_Mark` a timed pulse, not a mark of fixed width on the web?**
The PLC stops the feed one tick after it sees the edge, so the web runs on 20-40 mm. A mark with a width in mm could stay ON while the web stands, and the next WRAP would then see no rising edge and feed a whole extra label. A 0.3 s pulse (as S6) lasts at least one tick and is gone long before the next WRAP (PRESS takes 2 s). The marks stay every 210 mm of web, so the run-on of one label does not shift the next mark (tested over 20 labels).

**Where is `Label_Present` kept?**
On the bottle (`Bottle.label_mm`), like the filler's `level_ml`: a label glued by an E-stop half-way stays on the bottle, so the restart sees `Label_Present` and does not label it twice (`docs/labeler/state-diagram.md` rule 7).

**When does a label stick?**
Only when the web moves, the glue roller is ON, the glue is at least 150 degC with glue in the tank, and a clamped bottle spins. Otherwise the fed web is lost (`lost_mm`) and the audit names the reason. The roller uses glue whenever it runs with the web, also for a lost label.

**Why heater 300 degC with tau 60 s?**
With the room loss (tau 600 s) the balance is 274.5 degC: 20 -> 170 degC in 48.5 s (`HeatTimeout` 120 s); the PLC's 5 degC hysteresis cycles about 20 s off / 2.5 s on with 0.2 degC overshoot per tick; a heater stuck on reaches `GlueMax` (200 degC) 18 s after 170 degC, so the over-temperature scenario (stage 6) is reachable. 1 kg of cold glue into 1 kg at 170 degC gives 95 degC: `Glue_Ready` drops and the PLC goes back to WARM_UP (rule 4), as on a real machine.

**When is the bottle free of the bell?**
As soon as `Bell_Down` drops. The PLC has one bell sensor and leaves UNCLAMP on `NOT Bell_Down` (decision 2026-11-15), so it starts the pusher while the bell still rises. The physics blocks the pusher only while the bell is fully down; the first version waited until the bell was fully up and warned "pusher against the bell" on every bottle.

**Why is `Spin_Pulse` timed, like `Label_Mark`?**
A cam has a width in degrees and could stay ON when the turntable stops on it; the PLC's CTU counts edges and resets outside PRESS, so a pulse that stays ON would hide the first turn of the next PRESS. A 0.3 s pulse per whole turn is at least one tick and OFF for 0.7 s before the next.

**Why does `Plant()` keep the stand-in and the CLI not get `--no-labeler` yet?**
With the real labeler, C2's motor belongs to the labeler PLC. Until the simulator talks to that PLC (stages 4–5), no one would switch it on and the filler would block. So `Plant(with_labeler=True)` is used by the tests only; the stand-in moved to `plant.labeler_standin`, so `plant.labeler` names the real machine like `plant.filler` and `plant.syrup`.

**What does the model leave out?** A heating speed that depends on the glue mass, roller inertia (the web stops at once) and a dangling uncut label (a missing cut is only reported).

**Why does `LabelerPlc.write()` send three requests and not one?**
The two extra sensors sit in `%MX4` (coils 8224–8225); between them and the first block lie the manual buttons (`%MX2`, HMI) and the conditioned bits (`%MX3`, PLC). One write over the whole range would overwrite both, so the one-writer guard refuses it. The cost is two more small requests per tick (about 1 ms each on localhost).

**Why must a plant with the labeler have a labeler link (and why `--no-labeler`)?**
C2's motor is a labeler PLC output. A plant with the real labeler but no link would leave C2 standing and the filler blocked without any message, so `loop.run` refuses it (`ValueError`). `--no-labeler` and `--filler-only` keep the stand-in, which drives C2 itself.

**Why may a test never press a button between two loop runs?**
The PLCs keep scanning while the simulator pauses. Found live: Stop pressed between the main run and the settle run let the labeler's gate pulse fire into a frozen plant; the bottle never moved, `Bottle_Entering` stayed set and the PLC raised `Alm_PocketJam`. Every press now happens while a loop runs (the operator task during the run, `stop_all()` during the settle run).

**How are the labeler faults modelled?**
As in the syrup room: one flag per broken part, set only by a command; the PLC sees nothing but the effect. A torn web lets the rollers turn without moving the web (no mark, no label); a stuck bell keeps its position; a jammed gate stops the bottle between gate and pocket, and `clear_gate` is the operator taking it out (a `removed` bottle, not a `lost` one); a failed heater heats nothing and a stuck one heats whatever the PLC says (a dead element wins over a welded contactor).

**Why does the live scenario script start with warm glue?**
Every scenario starts from a fresh plant; a cold melt tank would add 48 s of warm-up to each. The glue temperature of the new plant is set to 170 °C before the loop starts (a start state, not a change at runtime); `heater_failed` starts cold, because the warm-up is what it tests.

**Why does the labeler panel tab show the label share of the pocket bottle?**
A wrap can be interrupted (E-stop, torn web, the end of a roll). The PLC only knows `Label_Present` (any label at all); the share (`label_mm` / 210 mm) is the truth the audit judges, so a short label is visible on the tab before the bottle leaves.

**Why does the roll have a tail after its last mark (`ROLL_TAIL_MM` 40 mm)?**
The PLC stops the feed one or two ticks after the `Label_Mark` edge, so the web runs on 20–40 mm past every mark. In normal running that cancels out: each bottle gets the rest of its label up to the next mark plus the next run-on, 210 mm. At the end of a roll it did not: the web stopped dead at the last mark (no label left), the run-on was missing and the last label left with 169–190 mm, against the 189 mm "broken label" limit, so it failed or passed by timing luck (found 2026-11-18 in the panel check; the stage 6 test counted labels, not audit warnings). A real roll carries some web after its last registration mark; the model gives it 40 mm (the largest run-on). `Roll_Empty` still comes at the last mark. `LabelWeb.web_left_mm` = rest of the current label + the labels after it + the tail; the feed never moves more than that. The first label after a roll change still gets one run-on extra (about 250 mm); nothing judges a long label.

**Why do the labeler's animation words (`Anim_C3`, `Anim_Station`) come so late?**
The filler's words %MW70–75 were designed together with its FUXA screens, so the labeler's waited for its HMI plan (2026-11-19, `docs/hmi/roadmap.md` section "Labeler screens", stage L0). Words written earlier would have fixed a layout the screens might not want.

**Labeler animation words: what and why (HMI L0)**
- `Anim_C3` %MW70 = C3 slot bits, `Anim_Station` %MW71 = pocket code (0 empty, 1 bottle, 2 labelling, 3 labelled). Same addresses as the filler's `Anim_C1` / `Anim_C2`: each PLC has its own memory, and the same place for the same job keeps the FUXA generator simple.
- **Not declared in the PLC program.** The Modbus server serves every %MW, declared or not (the filler's %MW70–75 aren't declared either), so no ladder change and no upload.
- **"Labelled" = at least 90 % of a label** (`BROKEN_LABEL_SHARE`), the audit's limit for a whole label, so the screen and the event log never disagree about a bottle.
- **A fourth write per tick**, not part of the GlueTemp write: %MW13–69 lie between them, and the PLC's status words %MW50–53 are among them, so the simulator must not write one block across them.
- **Not read back by `verify_writes()`** (as for the filler): the PLC ignores the words, so a wrong value cannot change the machine, and the read-back stays one request smaller.
- **Slots 0 and 7 of C3 are rarely seen at a tick end:** the pusher's tick also moves the belt (sub-steps), and the packer stand-in takes the bottle as soon as it reaches slot 7. On the screen a bottle appears in slot 1 and leaves after slot 6; the unit test checks slots 1–6.

**Packer animation words: what and why (HMI P0)**
- `Anim_Wrap` %MW70 / `Anim_Oven` %MW71 = cell bits, `Anim_Table` %MW72 = packs on the outfeed table, `Anim_Film` %MW73 = sheet code (0 none, 1 feeding, 2 in the slot, 3 under a pack). Same start address as the other PLCs; not declared in the PLC, a fourth write per tick, not read back – all as the labeler's, for the same reasons.
- **Cells, not slots.** The packer's conveyors are modelled in mm, not in slots like C1–C3. A word cuts them into 100 mm cells (`ANIM_CELL_MM`): 11 on the wrap conveyor (push line → oven entry at 1100 mm), 10 in the oven + 1 for a pack leaving over the exit eye. A pack (200 mm) lights the 2–3 cells it covers, so it moves smoothly on the screen.
- **No gap at the hand-over.** The oven takes a pack with its front at the entry eye (oven mm 0), its body still behind the eye. That part is drawn on the last wrap cells until it is inside; the plant test checks every tick that a moving pack lights at least one cell.
- **Sheet code priority** feeding > in the slot > under a pack: the screen shows the newest sheet first. "Feeding" means the film really moved in the last step (`Film.fed_mm`), so a torn film (rollers turn, film stands) does not show as feeding.
- **Collation table:** no word; the screen draws it from the PLC's `GroupCount`.

## Packer physics (`machines/packer.py`)

Collation (stage 1) and oven (stage 3) parameters and their checks: `roadmap.md`, "Stage 1 / 3 as built". The wrap (stage 2):

**Why are wrap positions in mm, and why the pack's front?**
The PLC's wrap transitions hang on sensor edges at fixed points (`Pack_At_Film`, the slot, `Oven_Entry`); a 0.5 s slot step would move a pack 100 mm at once. One number per pack (its front) is enough: an eye at x sees a pack while front ≥ x and front − 200 mm < x.

**Why is the film slot 50 mm ahead of the film stop eye?**
The PLC stops the wrap conveyor one or two ticks after `Pack_At_Film` rises: 20–40 mm of overshoot. The slot must lie beyond that, or the waiting pack would already stand on the slot when the film is fed.

**Why 150 mm/s feed and not 167?**
`Film_Pulse` is a timed 0.15 s pulse (as `Label_Mark`), ON at two tick ends. At 167 mm/s the 50 mm marks come 2.99 ticks apart, so sometimes after 2 ticks: the pulse would still be ON and the CTU would miss an edge (one sheet 50 mm too long). 150 mm/s gives 3–4 ticks, at least one OFF tick, and a late 0.3 s tick moves 45 mm, never past two marks. 1200 mm take 8.0 s against the 10 s `FeedTimeout`.

**Why does the encoder count roller travel and not film since the cut?**
A real encoder sits on the roller; the knife does not reset it. So the first pulse of a sheet comes after 0–50 mm and a sheet is pulses × 50 mm minus up to 50 mm plus the run-on: 850 mm gives 817–883 mm, always above the 800 mm needed. The PLC measures to one pulse, as it should.

**Why does `Film_In_Slot` need 80 mm of film, more than one pulse?**
The PLC reads `Film_In_Slot AND FeedCount = 0` as "a cut sheet waits" (`Sheet_Waiting`, rule 4) and then never feeds again. With the sensor below one pulse period, an interrupted feed of 40 mm (no pulse yet) would look like a cut sheet, and the pack would drive over an uncut stub. Above 50 mm, every sheet the sensor sees has been counted (tested at all 50 encoder phases).

**When is a pack wrapped?**
When the bar passes the top of its turn and the pack, with a sheet, has its tail past the slot by at most 400 mm (front 650–1050). That is one moment, not the film's path; a pack is never wrapped twice. A stuck bar lets the conveyor carry the pack on into the oven, unwrapped, as on a real machine; the PLC's `WrapTimeout` reports it.

**Why does the roll's tail hold 1.25 m?**
`Film_Empty` stops new sheets, but a started one must finish: the largest is 1200 mm plus 15–30 mm of run-on. The labeler's 2026-11-18 roll-end bug (a last label cut short) is the reason the tail is sized to the run-on too.

**What happens after an E-stop in RUN_ON?**
If the pack's front already passed the slot it has its sheet but still covers `Pack_At_Film`. The restart's WAIT_PACK sees an empty slot and feeds a new sheet under it (warning "film fed under …"). The PLC is not changed: the pack is wrapped with its own sheet, the extra sheet waits cut in the slot and the next pack takes it (`Sheet_Waiting` → READY), so nothing is lost but one sheet's place in the order.

**Why does the packer need a `PulseWire` and not the OR the other pulses use?**
The plant writes one value per tick and the PLC counts rising edges. The OR ("ON if ON at any sub-step") merges two pulses whose ticks touch: no OFF tick, one edge lost for good. The filler's and labeler's pulses come about once a second, so that never happens; `Film_Pulse` comes every 0.33 s and `Bottle_Counted` every 0.5 s, so one late tick of 0.2–0.3 s, still below the slow-tick warning, could lose 50 mm of film or a bottle (a 7th bottle on the table). The wire counts the edges of the sub-steps and passes each one on: an edge behind an ON comes one tick later, after an OFF tick. Limit: one bit per tick carries at most one edge per two ticks, so ticks that STAY slower than half the pulse period (film: about 0.17 s) cannot keep pace whatever the wire does; the sheet then gets longer, never shorter (tested: sustained 0.2 s gives 38 marks instead of 34).

**Why does `PackerPlc` read across gaps but never write across them?**
A read changes nothing, so one request over %MW40–53 (computed and status words with six unused words between) or over %MX5.0–7.1 (both alarm bytes with the unused %MX6) is cheaper than two and returns the same values. A write over a gap would overwrite the HMI's manual buttons (%MX2) and the PLC's conditioned bits (%MX3): the one-writer guard refuses it, so the sensors stay three writes.

**Why does the live link test answer `Airflow_OK` and avoid the counted inputs?**
In Auto the idle packer runs its oven (`Oven_On` needs no Start): the blower is ON, and without `Airflow_OK` the PLC raises `Alm_AirflowLost` after 3 s and FAULTs. The test answers the blower every tick, as the physics does. And the PLC's counters count in IDLE too: a pulse on `Film_Pulse` would stay in FeedCount until the next READY and make the next sheet short, so the test never gives an edge on `Bottle_Counted`, `Film_Pulse`, `Oven_Entry` or `Oven_Exit` and checks that the counters did not move.

**Why does the packer closed-loop test supply exactly 12 bottles?**
The PLC's counters outlive the test: bottles left on the collation table stay in GroupCount, a pack left in the oven in PacksInOven (the next run's `Alm_OvenJam` after 20 s, with no pack to come out). With exactly 6 N bottles every bottle ends in a pack, and the test runs on after Stop until both steps are 0 and the oven is empty. It writes no "neutral" sensors at the end: the last tick is the settled machine, and an `Airflow_OK` FALSE would FAULT the idle packer in Auto.

**Why do WAIT_WRAP and READY not show in the event log?**
Ladder rungs run top to bottom in one scan: when the wrap side already waits, COUNT → WAIT_WRAP in one rung and WAIT_WRAP → PUSH in a later rung of the same scan (CUT → READY → RUN_ON likewise). The step word never holds 15 or 24 at the end of a scan, so the simulator, which reads it once per tick, cannot see them. They show only when the step really waits (a pack still on the wrap conveyor, a hot oven missing).

**How are the packer faults modelled?**
As for the other machines: one flag per broken part, set only by a command; the PLC only sees the effect. A torn film lets the rollers turn without film or pulses; a stuck pusher or wrap bar does not move; a failed oven heater heats nothing and wins over a stuck one (a dead element beats a welded contactor); a stuck heater heats whenever air flows, so the blower's run-on after the over-temperature FAULT keeps heating for 30 s (on a real machine too, which is why the heater contactor must be checked); a failed blower moves no air, so `Airflow_OK` stays FALSE and nothing heats; a jammed oven holds its packs while the conveyor runs. Hand actions (`put_sheet`, `clear_oven`, `load_film`) are what the operator does before Reset.

**Why does `sheet_dropped` drop the sheet in CUT, not in READY?**
READY lasts one PLC scan when the oven is ready and the outfeed free (CUT → READY → RUN_ON in one scan), so no tick can hit it. CUT lasts 0.3 s: the simulator sees it at the end of a tick, drops the sheet at the start of the next, and the write reaches the PLC about 0.2 s after CUT began. At CUT's end the slot is empty, READY waits and `SheetCheck` (1 s) raises `Alm_FilmFault` (live: 1.0 s).

**Why does the scenario test feed exactly 6 bottles per pack and drain after each scenario?**
Every scenario starts with a fresh plant, but the PLC keeps its counters. A bottle left on the collation table or a pack left in the oven would stay in GroupCount / PacksInOven and the next scenario would start with a group short or an oven jam. So each scenario feeds exactly what it needs and runs on until all is packed and out; "PLC left clean" is a check of its own.

**Why does `palletizer_stopped` need 7 packs?**
`Table_Full` comes at 3 packs on the table, and by design (Q3) up to 3 more packs may already be past READY (one on the wrap conveyor, two in the oven): the table holds 6. Only the 7th pack is sure to wait in READY. With 4 (first version) all four passed before `Outfeed_Full` latched and no Blocked state came.

**Why does the test controller wait 0.5 s in WAIT_WRAP (`SETTLE_S`)?**
Stage 4 found it: the PLC counts the 6th bottle at the gate and pushes in the same scan if the wrap side waits, but the bottle reaches the table 0.4 s later. The proposed PLC rung is a settle timer on WAIT_WRAP (roadmap, "Stage 4 as built"); until the user adds it, the real PLC makes the simulator warn "bottle onto the collation table with the pusher out" on every group. The physics was not bent to hide it: a count sensor at the table instead would let a 7th bottle through.

## Blow moulder physics (`machines/blowmoulder.py`)

Oven station (stage 1) parameters and their checks: `roadmap.md`, "Stage 1 as built".

**Why does a preform only drop onto the loader place with the loader home?**
The loader's pusher face covers the place while it is out. Without this rule a preform could arrive during the stroke and be pushed in by the same stroke, or land behind the pusher. Preforms only back up in the chute meanwhile; the chute keeps the time it already ran.

**What does `OvenTemp` show with an empty pocket?**
The pyrometer looks into the pocket: with a preform it sees the preform's surface, without one the pocket wall. When the arm takes the preform the wall starts at the preform's temperature (no jump on the HMI trend) and follows the lamps with τ 30 s. The PLC switches the lamps off without a preform (rule 5), so the wall only matters for the trend and for a lamp stuck on.

**Why can the P controller not reach the window at Kp 5?**
A preform at 100 °C needs about 28 % lamp power (`(100 − 20) / 2.83`). With `Bias` 0 that power comes only from the error: `Kp · error ≥ 28` needs an error of 5.7 °C at Kp 5, which is below 100 °C, outside the window: HEAT never ends (`Alm_OvenHeatTimeout` on the PLC). Kp ≥ 6 or a `Bias` near 30 % fixes it. That is the P offset of rule 5 taken to its end, not a model bug.

**Why does the lamp loop oscillate at Kp 30?**
The loop has dead time: the PLC's `LampPower` reaches the physics one tick (0.1 s) after the `OvenTemp` it was computed from, two when a scan is missed. With τ 8 s and 2.83 °C per % the loop gain is 2.83 · Kp, about 85 at Kp 30: the correction for one tick's error overshoots before the next reading arrives. With two ticks of dead time the temperature swings 102.9–105.3 °C (still inside the window, far below `OvenMax`); at Kp ≤ 25 it settles. Kept as a teaching point (P gain vs. dead time, roadmap Q5); the HMI limit of `Kp` (1–30) stays.

**When does the preform leave the oven pocket?**
At the arm's first move with the gripper fully closed, not when the gripper closes. The PLC's GRIP step only closes the gripper; `Preform_In_Oven` must stay TRUE until SWING, where the oven station sees it drop and goes back to FEED. An arm that leaves with the gripper open leaves the preform in the pocket.

**Why is the preform let go as soon as the gripper starts to open?**
It makes the release a single moment: either the arm is at an open, empty mould (the preform is in the mould, `Bottle_In_Mould`) or it is not (the preform drops, a warning). RELEASE waits `GripTime` 0.3 s anyway, longer than the 0.2 s the gripper needs.

**How is the blow pressure modelled?**
One cavity pressure, sealed only while the mould is fully closed; an opening mould vents at once (audit above 0.5 bar). Each open valve is a path toward its source (8 bar, the high-blow regulator, 0 bar) with its own time constant; several open valves give an exponential toward their weighted mean, solved exactly per step as the lamps. That is why high blow with the exhaust open settles at about 19.5 bar: below `BlowSetpoint`, so the PLC's `Alm_BlowFault` catches it. The regulator is an attribute (`high_blow_bar`), so the stage 6 faults (`air_low`, `regulator_failed`) only change one number.

**Why a dead plate between the mould and C1?**
The filler drives C1 and can stop it (E-stop, FAULT) while its `C1_Full` slot is still free. The ejector always finishes its stroke onto the plate; the bottle drops onto C1's slot 0 when that is free (`Bottle_Ejected` then). Only a second bottle with the plate still occupied stops the ejector halfway: `Bottle_In_Mould` stays and the PLC sees a real `Alm_HandlingJam` (roadmap Q3).

**Why is the bottle judged by its temperature at the hand-over?**
A preform does not cool outside the oven in the model (a held one would lose ~16 °C at τ 8 s before the blow, a cooling the real machine keeps small with a fast transfer). So `transfer_c` (blown cold below 95 °C) and the peak in the oven (overheated above 120 °C) are what the audit uses.

**What does the blow moulder do when its PLC does not answer?**
Every output OFF except the exhaust, which vents (`plant.BLOWMOULDER_DEAD`). The exhaust is a normally-open valve (rule 3): without the PLC there is no air to keep it shut, so a bottle under pressure vents by itself. The other machines' "no answer = all OFF" would have kept the pressure in. Known limit: the mould opens on its valve's spring side; the real PLC holds a closed mould (rule 4), but a PLC that does not answer holds nothing.

**Why does the backup test use a filler that stops taking, not a filler FAULT?**
The filler stops C1 only on E-stop or FAULT, and `C1_Full` watches slot 1: a bottle put onto a standing C1 stays in slot 0, so `Outfeed_Full` never comes (HMI issue I18). The blow moulder's dead plate takes one more bottle, then the ejector stops halfway and the PLC raises `Alm_HandlingJam`; `test_plant_blowmoulder.py` documents that. The backup of rule 8 happens when the filler does not take bottles while C1 runs (Stop mode, a slow filler): the queue reaches slot 1, the oven loads nothing new, the bottle waits in the open mould.

**Why does the blow moulder's link read two blocks for its outputs and decode signed words?**
`LampPower` is an analog OUTPUT (`%QW0`), and OpenPLC puts `%QWn` at holding register n, below the `%MW` area: one more read per tick next to the coils 0–9. The simulator only reads it; it is not in `SIM_WRITE_REGISTERS`, so the one-writer guard raises before any write to HR 0 (the PLC would overwrite it anyway). Modbus registers are unsigned 16-bit, the PLC's INT is signed: `BlowPressure_Raw` is written with `to_register()` (a wire break, −2000, travels as 63536), and every status word is read with `from_register()` because `BlowPressure` and `OvenError` can be negative (the packer's status has no negative word, so its decoder skips that).

**Why does the CLI refuse `--rate` / `--add` with the blow moulder?**
With the blow moulder the bottle supply does not exist (`plant.supply` is None): the bottles come from preforms. A silent `--add 20` would do nothing and the user would wonder where the 20 bottles went, so the CLI stops with "add --no-blowmoulder"; the commands themselves are ignored with a warning (`_Spec(supply=True)`), and scenarios that set the supply are refused or hidden in the panel (`scenarios.uses_supply`).

**Why do some blow moulder scenarios end with a warning about a bad bottle?**
The recovery follows rule 9: after Reset + Start the PLC ejects whatever is in the mould (there is no reject, Q4). `mould_stuck` leaves an unblown preform there, `air_low` a half-blown bottle, `lamps_stuck_on` an overheated one; the simulator flags them on C1 as it should. `test_blowmoulder_scenarios.py` lists these expected warnings per scenario and fails on any other, and always on "mould opened under pressure".

**Why `c1_backed_up` and not `c1_stopped`?**
A C1 belt that stands keeps the bottle in slot 0, and `C1_Full` watches slot 1: the blow moulder never sees a full conveyor and jams (issue I18). A real backup happens when C1 runs and nobody takes: the scenario blocks the filler's door (`filler_door_blocked`), the queue reaches slot 1, the blow moulder is Blocked with its bottle in the open mould and warns after 2 min.

## Syrup room in the plant (`plant.py`)

**Where is the cable between the two PLCs?**
In `Plant`: the filler sees `plant.product_available AND the syrup PLC's Product_Available`. The first term is the pipe itself (the panel switch and the scenario `product_available False` cut it), the second is the real signal. The simulator copies the signal because the two PLCs never talk to each other; on a real line this is one wire. Without a syrup room (`Plant()`) the second term does not exist and the pipe alone decides, which keeps the filler tests and the filler-only run as they were.

**Signal and liquid are two things.** `filler_product_available` is only the SIGNAL to the filler PLC (%MX1.0). Whether product really comes out of the open fill valve is `product_flows`: the pipe is connected AND the buffer holds product. The PLC signal does not move liquid. Before (2026-11-13 review) the valve flowed whenever the signal was TRUE: with `BufferMin` 0 on the HMI the signal never drops, the buffer ran to 0 L and the filler still filled full bottles from nothing (one warning only). Now the bottle stays short and the filler PLC's own fill timeout catches it, as on a real line.

**What does `syrup=None` mean in `Plant.step`?**
The syrup PLC does not answer. Its pumps and valves are OFF (like a stopped controller's outputs) and it drives no `Product_Available` wire, so the filler starves. A PLC that was only stopped with `Cmd = 2` still answers and keeps `Product_Available` by its hysteresis.

**Why does the filler drain the buffer, and why a scale?**
The syrup room's buffer only makes sense if something uses it. The fill valve counts every ml (`dispensed_ml`, a spill too) and the plant takes `ml / 1000 x buffer_draw_scale` litres out of the buffer. A real 500 ml bottle would take 0.5 L, and a 590 L batch would last hours; the scale makes one batch last a few minutes. It is a plant attribute (a command comes in stage 6).

**Why is the scale 30 and not 50?**
My first estimate forgot that a batch arrives at the buffer only at the END of its cycle (about 145 s: dose, mix, cool, carbonate, then transfer). In between the filler lives on what is in the buffer. The whole-line test (600 s, both test controllers) showed: x50 starves the filler after 114 s, x40 leaves 24 L above the reset of `Product_Available` (100 L), x30 leaves 113 L. At x30 the buffer swings between about 230 and 1050 L and batches wait in WAIT_DEMAND now and then, as in the design.

**Why two heartbeats and one E-stop?**
One E-stop chain stops the whole line, so both PLCs see the same `E_Stop_OK`. A comm failure is a fault of one link, so each PLC has its own heartbeat and a scenario can freeze only one.

## Second Modbus link (`plcs.py`)

**Why a base class `PlcLink` and not a copy of `FillerPlc`?**
Connecting, reconnecting, "link_ok means the PLC answered", and "check the link and create the request with no await in between" are the same for every PLC, and they are the parts that were hard to get right (stage 5 of the filler). The base keeps them once; a machine class only adds its addresses, its default port and its reads and writes. The one-writer rule is a pair of class attributes (`write_coils`, `write_registers`) that the guard in the base class checks, so a machine cannot forget it.

**Why is `read_signals()` two requests?**
The outputs are coils 0-5 and `Product_Available` is coil 8240. One request over that range would read 8000 coils. The two values may be one PLC scan (20 ms) apart; `Product_Available` follows a hysteresis on the buffer level, so that does not matter.

**Why two's complement for the analog values?**
A PLC `INT` is signed, a Modbus register is unsigned 16 bit. `-5` is sent as 65531 and the PLC reads it as -5. The temperature cannot be negative with a 0 degC chiller, but a fault scenario may drive any sensor anywhere, and `write_registers` raises on a negative number instead of wrapping.

**Why do the two PLCs have the same coil ownership?**
Both keep the sensors in `%MX0` and the wiring in `%MX1.1/1.2`, so the block 8192-8202 is the same. The registers differ: the filler also owns the animation words (`%MW70-75`), the syrup room only the five analog sensors. Each machine class has its own sets, and a test pins that the syrup client cannot reach the animation words.

**How do I know the syrup client reads the right addresses?**
The live script `tests/test_link_syrup.py` writes `ProductTemp`, `CO2Pressure` and `BufferLevel` through `SyrupPlc.write()` and watches the PLC's own `Temp_OK`, `CO2_Demand` and `Product_Available` follow them (including the hysteresis hold at 110 L). Those bits run without Start, so no batch is needed. The script refuses to run unless the PLC is idle.

## The loop with two PLCs (`loop.py`)

**How does the loop stay independent of one PLC being down?**
Each PLC has its own link (`PlcLink`), its own bookkeeping (`LinkTrack`: last writes, which problems were already reported, whether the link was up) and its own `try` around write, read-back and status. A failure in one never skips the other. A dead filler means "all outputs OFF"; a dead syrup PLC means `syrup=None` in `Plant.step`: its pumps are off and it drives no `Product_Available`, so the filler starves. The syrup message says so and names the way out: `--filler-only`.

**What does `uv run simulator` do by default now?**
It drives both PLCs. `--filler-only` gives the old behavior (`Plant()` with the stand-in `Product_Available`); all older scripts (`test_closed_loop.py`, `test_scenarios.py`, the filler suite) use `Plant()` + `FillerPlc` and are unchanged. The syrup PLC does not have to be started for the filler to get product: `Product_Available` is a hysteresis on the buffer level that runs in IDLE, it only needs the syrup PLC powered and its sensors written. To start the syrup room: `uv run python tests/hmi.py --syrup auto reset start`.

**How long does the wire take?**
The syrup PLC's `Product_Available` is read at the start of tick n, passes through `plant.step`, and reaches the filler PLC in the write of the same tick: about one tick plus a PLC scan (roughly 120 ms). Irrelevant for a hysteresis signal.

**Why are the requests not made in parallel?**
They run one after the other. A refused connection fails at once and costs nothing. A PLC that accepts the connection and never answers costs one request timeout (1 s). Running the two links in parallel would only move that wait, because the tick has to wait for both anyway. What helps is trying that PLC less often (next question).

**Found live: a "silent" PLC starved the healthy one.** The filler runtime was in the state "program RUNNING, Modbus plugin disabled": Docker's proxy accepts the connection, the runtime never answers. Every 2 s the loop reconnected and lost a full 1 s timeout, and the syrup PLC raised `Alm_CommLost` after a few seconds although the loop was running. Two causes, two fixes:
1. **Backoff in `PlcLink`:** a connection that never got an answer doubles the wait before the next attempt (2, 4, 8, 16 s, then 16 s), and the first answer resets it. A refused port and a restarting runtime recover as fast as before.
2. **The heartbeat could cancel itself:** it was stepped per sub-step and caught up with every missed toggle. After a stall of about 2 s it toggled twice inside one tick, so the value written was the same as the one before and the PLC saw no change for 3 s. Now each heartbeat is stepped once per tick and toggles at most once per step; it keeps its phase, so the gap between two visible changes stays below the 3 s watchdog for stalls up to about 2 s. A stall of 3 s or more is a real communication loss and the PLC is right to say so. This was also a latent weakness of the filler.

**Why is the live closed-loop script able to run with the syrup PLC alone (`--syrup-only`)?**
Because the loop copes with a dead PLC, and because the filler runtime can be in the "Modbus plugin disabled" state after a container re-creation (the program is then uploaded again from the Editor). With `--syrup-only` the script points the unused filler link at a closed port, so the filler runtime is not touched at all, and skips the check that needs it.

## Syrup room faults and scenarios (stage 6)

**Why are faults attributes of the room and not PLC changes?**
A fault is a property of the world: a pump that runs without flow, a valve that stays open, a sensor that reports an old value. The PLC is never told; it can only see the effect (a timeout, a pressure, a level). So `SyrupRoom` has `water_pump_failed`, `syrup_pump_failed`, `co2_valve_stuck`, `mix_level_frozen` and `coolant_c` (failed chiller = 20 degC = room temperature, so cooling does nothing). A failed pump is not an audit warning: the fault is intended, like the injected faults of the filler.

**Why does `MixHighHigh` use the true level when the level sensor is frozen?**
It is a separate switch, the hardware backup for exactly this case (`io-list.md`). If it followed the frozen reading the scenario `level_sensor_stuck` could never show the backup working. Timing: water at 20 L/s reaches the 980 L switch in about 49 s, before the 60 s `DoseTimeout`, so `Alm_Overflow` (not `Alm_DoseTimeout`) is the alarm that appears.

**Why are `vent_tank` and `drain_mix_tank` commands?**
The PLC's Reset only works when the cause is gone. After a stuck CO2 valve the headspace holds about 7 bar (the liquid takes some CO2 up, but `pressure + 0.286 x dissolved` is conserved), and nothing in the model lowers it: a real plant vents by hand. After a stuck level sensor the mixing tank holds 980 L of plain water: the drain valve sends it to waste (into the buffer it would dilute the product). Both are maintenance actions at the end of the scenario, before the operator presses Reset + Start. `vent_tank False` and `drain_mix_tank False` do nothing: they are actions, not states.

**Ledger with hand actions.** `fill_buffer`, `drain_syrup` and `drain_mix_tank` change the volumes outside the pumps, so they go into `manual_l` (net litres in minus out) and the conservation formula is `syrup + mix + buffer + spilled = start + water_in + delivered - drawn + manual`.

**Why does a scenario have a `target`?**
Its conditions (`in_step`, `alarm`, `in_state`, `plc_running`) look at one PLC's status. A syrup scenario must see the syrup PLC's step numbers and alarms, not the filler's. `SyrupStatus` and `PlcStatus` both have `.step`, `.state` and `.alarms`, so the same condition functions work; the runner just hands over the right status. A scenario checks at definition time that a filler scenario uses no syrup command. A syrup command on a plant without syrup room (`--filler-only`) is ignored with a SIM warning instead of crashing the tick.

**What cannot be a scenario?** Reset and Start (the HMI writes them), and `buffer_starved` is not in the live test script because it needs the filler PLC running too; run it by hand with both PLCs.


## Pre-demo check with three PLCs (`tests/demo_check.py`, stage 8; labeler 2026-11-18; five PLCs since the blow moulder 2026-11-26)

**Why is the syrup room now a FAIL and not a WARN?**
Since stage 5 the default `uv run simulator` drives both PLCs, and the filler only fills while the syrup room's real `Product_Available` is TRUE. Without the syrup runtime the demo line starves, so a missing container, program or Modbus server of `plc-syrup-carb` must stop the demo like the filler's does.

**Why does D4 print one line per PLC?**
FUXA has one Modbus device per PLC, and each device has its own connection status tag (`t_<key>_connection_status`). When this check was written FUXA had no syrup device, so D4 read only the filler's link, and a FUXA that had lost the syrup PLC passed. Since the HMI wrap-up (S7, 2026-11-14, `docs/hmi/roadmap.md`) D4 reads the status tags of every runtime in one request and prints PASS / FAIL per PLC. The hint "install the Modbus plugin" comes only when every link is down: a missing plugin breaks all devices, while one link down means that PLC is down (D3). D7 points to the FUXA header for both machines.

**Why is "only the filler lock held" a WARN of its own?**
The plant lock is per PLC port. Filler lock held but syrup lock free means the simulator was started with `--filler-only`: the syrup PLC gets no heartbeat (`Alm_CommLost`) and the filler runs on the stand-in `Product_Available`, so the demo would not show the two-PLC handshake. The check says so instead of a generic "no simulator".

**Why was there a "Reset twice" hint, and why is it gone?**
Both ladders computed `Master_Enable` above the watchdog's Reset rung. With `Alm_CommLost` and FAULT both latched, the Reset scan saw the old `Alm_CommLost` there, so `Seq_Enable` stayed FALSE, the FAULT → IDLE rung did not fire, and `Cmd` was already cleared for the next scan: a second Reset was needed, and D7 said so. Fixed in the Editor on 2026-11-14 (both PLCs): the `Master_Enable` rung now comes after the `Alm_CommLost` reset rung, so one Reset leaves FAULT. D7 no longer adds the hint.

**Why is the labeler a FAIL like the others?**
Since labeler stage 5 the default `uv run simulator` drives the labeler PLC, and that PLC drives C2. Without the labeler runtime C2 stands, C2 fills up and the filler blocks: the demo line stops after a few bottles. So a missing container, program or Modbus server of `plc-labeler` stops the demo like the other two (D1–D3); D7 reads its mode, step, state and 7 alarms with the labeler's names.

**Why does D4 skip the labeler?**
FUXA has no labeler device yet (the labeler HMI is not planned). A connection status tag that does not exist would read as "not connected" and fail D4 forever. `Runtime.fuxa = False` keeps the labeler out of D4 until its device exists; D7 then points to `tests/hmi.py --labeler` instead of the FUXA header.

**Why is the packer's FUXA link a note in D4, not a check?**
FUXA has no packer device until the packer HMI (part C). A missing connection status tag would read as "not connected" and fail D4 forever, as it would have for the labeler before its HMI. The note says where the packer is operated instead (`tests/hmi.py --packer`); D7 still checks its mode and alarms, reading both step words and the second alarm byte (%MX7) that only the packer has.

**Why is the blow moulder's FUXA link a note in D4, and how does D6 name the start options with five PLCs?**
As the packer before its HMI: the blow moulder has no FUXA device until its part C, so D4 prints where it is operated (`tests/hmi.py --blowmoulder`) and D7 still checks its mode, both step words (`Step` / `MouldStep`) and the alarms in `%MX5` + `%MX7`. The CLI leaves PLCs out in fixed groups (`--filler-only` all but the filler, `--no-labeler` labeler + packer, `--no-packer` the packer, `--no-blowmoulder` the blow moulder, combinable with the two before), so D6 builds the option text from the set of free plant locks, e.g. "started with --no-packer --no-blowmoulder?"; a set no single simulator can produce gets the generic "restart `uv run simulator`".

**Why does D6 name `--no-packer`, `--no-labeler` or `--filler-only`?**
There is one plant lock per PLC port. The CLI can leave PLCs out in three ways: `--no-packer` (packer lock free), `--no-labeler` (labeler and packer free) and `--filler-only` (syrup room, labeler and packer free). D6 names the option that fits the free locks, so the fix is one restart without it instead of a search; other combinations (cannot come from one simulator) get the generic text.
