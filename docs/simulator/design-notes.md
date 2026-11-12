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
The screen is drawn in the same asyncio loop, so a redraw can delay a tick a little; the next deadline then comes sooner. The physics handles any `dt` up to the 0.3 s slow-tick warning (sub-steps, kept pulses), so this does not change what the PLC sees. Use `--headless` for timing measurements.

**Why no Start / Reset buttons in the panel?**
One-writer rule: commands, modes and setpoints belong to the HMI. The header shows the `tests/hmi.py` command while the PLC is in IDLE.

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
