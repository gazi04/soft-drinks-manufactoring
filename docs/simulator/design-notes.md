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
The entry pocket has no sensor. A bottle left there by an E-stop during INDEX with C1 empty is invisible to the PLC (Starved).

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
