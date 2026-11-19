# Demo script – filler + syrup room + labeler (about 20 minutes)

A fixed order for presenting the line: three PLCs (OpenPLC, one runtime per machine), one plant (the Python simulator drives all three) and one HMI (FUXA shows all three), working as one closed loop. **Rehearse it once before the presentation.**

What was checked when:
- **Filler** steps: all checked live with FUXA as the HMI in HMI stage 7.3 (`roadmap.md`, 2026-11-04).
- **Syrup room** steps: the scenarios on the real PLCs (`buffer_starved` and `level_sensor_stuck` by hand, 2026-11-13), the *Syrup room* screen with `cooling_failure` / `over_pressure` (S3, 2026-11-13), Line block, alarms and trends live on 2026-11-14; the two-machine script was rehearsed on 2026-11-15.
- **Labeler** steps: each screen in the browser with a fault scenario (L2–L6, 2026-11-19); this three-machine script was rehearsed once with FUXA as the only operator on 2026-11-19 (`roadmap.md`, "L7 as built": measured times below come from that run).

**Screens:** browser with FUXA (`http://localhost:1881`), one terminal with the simulator panel (at least 140×45). Optional: OpenPLC editor (filler `https://localhost:8443`, syrup room `https://127.0.0.2:8443`, labeler `https://127.0.0.3:8443`) to show the ladder.

**Rule:** during the demo FUXA is the only operator (Start, Stop, Reset, mode, setpoints) for all three machines. Never run `tests/hmi.py` or a `tests/test_*.py` script at the same time.

**Which machine a button acts on:** header row 1 shows all three machines (chip = state, mode and PLC link; click = its screen) and one E-stop segment per PLC; row 2 (Start / Stop / Reset, Auto / Manual / Stop, alarm chips) belongs to **one** machine. On Line, Alarms and Trends the three buttons on the left of row 2 (*Filler* / *Syrup* / *Labeler*) pick it; on the *Filler*, *Syrup room* and *Labeler* screens it is that machine.

**One scenario at a time:** the panel has one scenario slot; **Run** replaces a scenario that is still running, which then stops where it was (e.g. `packer_stopped` replaced early leaves the packer stopped). Wait until the scenario status in the panel says *done*.

## 1. Before the presentation (15 min before)

1. Start the stack (repo root): `docker compose up -d`
2. Pre-demo check: `cd simulator && uv run python tests/demo_check.py`. If D2 says *Modbus server is OFF* or *no compiled program* (after a container re-creation or a laptop suspend): OpenPLC Editor, upload `plcs/filler` (device `localhost`), `plcs/syrup` (device `127.0.0.2`) and `plcs/labeler` (device `127.0.0.3`).
3. Start the simulator with its panel: `cd simulator && uv run simulator` (drives all three PLCs).
4. FUXA **Alarms**: **Ack** every row, then **Reset** once per machine (picker *Filler*, *Syrup*, *Labeler* in row 2, **Reset** each). A fresh simulator start always leaves `Alm_CommLost` latched on **all three** machines. One Reset per machine is enough, also after a FAULT.
5. `uv run python tests/demo_check.py` again → **0 FAIL** (a WARN tells you the fix).
6. Panel defaults: tab `1 Filler`: **Supply /min** `30`, **Fill flow** `100`, switches **Supply paused** OFF, **Product available** ON, **E-stop** OFF. Tab `2 Syrup room`: no fault switch ON. Tab `3 Labeler`: no fault switch ON, **Packer running** ON, **Heartbeat** ON.
7. FUXA, per machine with the picker: **Filler** → **Auto**, **Start**; **Syrup** → **Auto**, **Start**; **Labeler** → **Auto**, **Start**. The labeler first warms its glue (WARM_UP, under a minute from room temperature); until then C2 fills and the filler may turn orange (Blocked) for a moment.
8. Let it run **at least one syrup batch (~2.5 min)** so all three Trends views have curves, C1 is full and bottles reach C3.
9. Close everything else; set the browser zoom so the whole Line view fits.

## 2. The demo

| # | Time | Do | Show / say |
|---|---|---|---|
| 1 | 1 min | FUXA **Line** view | The architecture in one sentence: *each machine has its own PLC, the simulator is the factory, FUXA is the operator – they only talk through the PLCs' memory over Modbus TCP; the PLCs never read each other.* Point at the three real machines (coloured by their state), the grey stand-ins (blow moulder, packer), C1 / C2 / C3, and the product pipe from the syrup room into the filler. *A conveyor's full sensor is wired to the machine upstream of it; the machine downstream drives the belt (C2 turns with the labeler's motor output).* |
| 2 | 1 min | **Filler** chip → **Filler** view | Bottles step along C1, the filler is green. The pockets (rinse, fill, cap), the fill bar climbing to the 500 ml setpoint, the lamps of the outputs. *Indexing machine: all stations work in parallel (WORK), then the belt moves every bottle one station (INDEX).* |
| 3 | 2 min | **Syrup room** chip → **Syrup room** view | One batch: DOSE_WATER → DOSE_SYRUP (ratio 5.5 : 1 → *Syrup per batch*) → MIX → COOL → CARBONATE → TRANSFER. The pipes colour while their pump or valve runs; temperature in °C and pressure in bar (the PLC keeps 0.1 °C / 0.01 bar integers, FUXA scales). *The buffer is filled batch by batch; the filler takes product from it continuously.* Point at the note under the buffer: *each 0.5 L bottle takes 15 L – the simulator compresses time ×30, otherwise one batch would last hours; the PLCs don't know about it.* |
| 4 | 1.5 min | **Labeler** chip → **Labeler** view | One bottle: the gate lets it into the pocket, the bell comes down (CLAMP), the web feeds one label up to its registration mark (WRAP, the web turns white), the turntable presses it on for two turns (PRESS), the pusher sends it onto C3. The pocket shows *bottle → labelling → labelled*. The melt tank: glue at 170 °C, the red tick is the 200 °C over-temperature limit; the heater switches itself on and off in a narrow band (*hysteresis*, also in Manual). *Labelled* counter with its own **Reset counter** (`Cmd` = 4; Reset in the header keeps the count). |
| 5 | 1 min | Header **Trends**, picker **Labeler**, then **Syrup**, **Filler** | Labeler: the warm-up from room temperature to the setpoint at the start, then the heater's saw tooth; labelled bottles climbing. Syrup room: mixing level in two slopes (water, then syrup), temperature down, pressure up to its setpoint. Filler: one fill ramp per bottle. *FUXA logs these values in its own history, already scaled to °C / bar.* |
| 6 | 3 min | Panel: scenario **`packer_stopped [labeler]`** → Run, FUXA **Line** – wait until the panel says *done* | The packer stands: C3 fills slot by slot, the labeler turns **orange (Blocked)** and takes no more bottles; then C2 fills and the **filler** turns orange too – the blockage travels upstream, machine by machine, without a fault (rehearsal: labeler Blocked 49 s after Run, the filler 43 s later). After 2 min Blocked the labeler's warning *Outfeed blocked* (yellow, low) comes, the packer runs again and both continue **by themselves**. |
| 7 | 2 min | Panel: scenario **`buffer_starved [syrup]`** → Run, FUXA **Line** | The filler draws faster than the syrup room can mix: the buffer runs dry, the pipe turns grey, *no product*, the filler turns **yellow (Starved)** – **no alarm**, nobody did anything wrong (rehearsal: Starved 100 s after Run). The next batch reaches the buffer (30 s later): product back, the filler continues **by itself**. *The only signal between the two PLCs is `Product_Available` – a hardwired handshake, emulated by the simulator.* |
| 8 | 2 min | Panel: scenario **`level_sensor_stuck [syrup]`** → Run (it waits for the next DOSE_WATER – right after step 7 the buffer is low, so that comes at once), FUXA **Syrup room** | The level sensor freezes while the water pump runs; the tank keeps filling. The **high-high switch** (a separate hardware switch, the backup) trips `Alm_Overflow`: **red (Fault)**, every pump off. **Alarms**: the *Machine* column says *Syrup room*; one list for the whole line. When the panel says the tank is drained: **Ack**, **Reset** (picker *Syrup*), **Start**. |
| 9 | 1.5 min | Panel: scenario **`web_torn [labeler]`** → Run, FUXA **Labeler** | The label web tears between two bottles. In WRAP the PLC waits for the registration mark; after 5 s: **red (Fault)**, `Alm_LabelTimeout`, everything off, the heater too (the glue cools slowly, about 5 °C in 20 s, so reset soon). *A watchdog timer on a sensor that must come.* When the panel says *press Reset + Start*: **Reset**, **Start** in the Labeler screen's header – web threaded again, the bottle in the pocket gets its label. |
| 10 | 1.5 min | Panel: scenario **`index_jam [filler]`** → Run, FUXA **Filler** then **Alarms** | The belt motor is ON but the bottles don't move. After 5 s: **red (Fault)**, `Alm_IndexJam`. *A watchdog timer: the cam S8 must come within 5 s.* When the panel says *press Reset + Start*: **Ack**, **Reset**, **Start** (picker *Filler*). No bottle is lost. |
| 11 | 1.5 min | Panel: scenario **`e_stop [filler]`** → Run, FUXA **Line** | One E-stop chain reaches **every** PLC: all three E-stop segments in the header turn red (*E-STOP*), everything off at once on all three machines (*master enable, like an MCR*). State **Stopped**, not Fault. After release: **Start** for the filler, the syrup room **and** the labeler (picker) – the half-filled bottle is topped up to 500 ml. *One red segment alone would be a wiring fault; a grey "no link" segment is a lost PLC, not an E-stop.* |
| 12 | 1 min | Filler: mode **Manual**, **Filler** view | The jog buttons appear only in Manual. Hold **C1** (moves only while held). Hold **Fill** with no bottle at the fill station: nothing happens (*interlock*). Back to **Auto**, **Start**. |
| 13 | 0.5 min | (Optional) OpenPLC editor | One rung, e.g. the syrup room's overflow alarm (`MixLevel ≥ MixMax OR MixHighHigh OR BufferLevel ≥ BufferMax`), the labeler's glue hysteresis or the filler's fill interlock. *Ladder is the main language; every rung was tested over Modbus.* |
| 14 | 0.5 min | – | Wrap-up: built = filler, syrup room and labeler (ladder, simulator, HMI, one closed loop over three PLCs); stretch goals left = packer, blow moulder (grey on the Line view). |

Spare scenarios if there are questions:
- filler: `no_product` (Starved, no alarm, bottle topped up), `slow_fill` (`Alm_FillTimeout` after 10 s), `heartbeat_off` (`Alm_CommLost` after 3 s), `gaps` (needs an empty line, see below);
- syrup room: `low_syrup` (Starved + yellow warning `Alm_LowSyrup`, continues by itself after the refill – takes about two batches), `over_pressure` (CO2 valve stuck open, `Alm_OverPressure` at 6 bar), `cooling_failure` and `pump_failure` (each needs its 60 s timeout), `buffer_full` (Blocked in WAIT_BUFFER, continues when the filler has used product);
- labeler: `roll_empty` (yellow *roll low*, then *roll empty*: Starved, no FAULT, continues after the new roll), `glue_refill` (*glue low* warning, cold glue: WARM_UP after the bottle, no FAULT), `bell_stuck` and `gate_jam` (`Alm_PocketJam`, Reset + Start), `heater_stuck_on` (`Alm_GlueOverTemp` at 200 °C, Reset only once it has cooled below 200 °C), `heater_failed` (needs its 120 s timeout).

## 3. Avoid during the demo

- **Running a second scenario before the first is done:** the panel has one slot; the first one stops where it was. If the packer stays stopped (`packer_stopped` replaced): tab `3 Labeler`, switch **Packer running** ON.
- **Restarting the simulator in the middle of a batch:** the syrup PLC remembers a dosed batch (`Batch_Dosed`), the new simulator starts with an empty mixing tank, and the batch cannot finish. Before stopping the simulator, let the batch end and press **Stop** for the syrup room (IDLE). Recovery if it happened: Manual, water pump in pulses under 10 s to about 590 L, then Auto + Start. (A restart also starts the glue cold: the labeler warms up again, under a minute.)
- **Syrup scenarios wait for their step:** `level_sensor_stuck` and `pump_failure` start at DOSE_WATER, `cooling_failure` at COOL, `over_pressure` at CARBONATE. A new batch starts only when the buffer is below *New batch below* (default 800 L); run them right after `buffer_starved`, or expect a wait.
- **Manual Door and long manual Fill (filler):** known gaps (`docs/filler/ladder-roadmap.md`, "Change: manual interlocks for door and fill", open). In step 12 use only C1 and Fill without a bottle.
- **Holding a manual button > 10 s:** the jog watchdog ignores it until it is released. Correct, but confusing if unexplained.
- **`gaps` on a full line:** nothing visible happens. First set Supply /min to `0`, wait until the filler is yellow (Starved), then Run; set it back to `30` afterwards.
- **Supply paused ON:** added bottles are queued but never placed.

## 4. If something goes wrong

| Symptom | Fix |
|---|---|
| Bottles stand on C1, filler empty, state Stopped | Start was not pressed in Auto: picker **Filler**, **Auto**, **Start**. |
| Syrup room grey, buffer falling, filler soon Starved | Syrup room not started: picker **Syrup**, **Auto**, **Start**. |
| C2 full, filler orange (Blocked), labeler grey | Labeler not started (it drives C2): picker **Labeler**, **Auto**, **Start**; it warms up first. |
| Labeler stays in WARM_UP, red after 2 min (`Alm_GlueHeatTimeout`) | Heater failed switched ON in tab `3 Labeler`: switch it OFF, **Reset**, **Start**. |
| Red alarm chips right after starting the simulator | `Alm_CommLost` from the restart, on all three machines: **Ack**, **Reset** per machine, **Start** per machine. |
| Machine stays red after Reset (other cases) | The cause is still there (panel switch ON: E-stop, a syrup or labeler fault, fill flow < 50, glue still ≥ 200 °C): fix it in the panel, then Reset + Start. |
| A click in FUXA did nothing | About 1–2 % of FUXA's writes time out (issue I16): check the header (state, mode) and press again. |
| Header: *PLC LOST* on a machine chip, grey *no link* E-stop segment | `uv run python tests/demo_check.py`: D2 *Modbus server OFF* → upload that PLC from the Editor; container down → `docker compose up -d`. |
| Trends empty | No data yet (wait one batch) or the page was opened before FUXA reconnected: reload the page. |
| Anything else | Stop the simulator (`q`, after **Stop** for the syrup room at the end of a batch), run `tests/demo_check.py`, follow the printed fix, start again from section 1 step 3. |
