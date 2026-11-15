# Demo script – filler + syrup room (about 15 minutes)

A fixed order for presenting the line: two PLCs (OpenPLC, one runtime per machine), one plant (the Python simulator drives both) and one HMI (FUXA shows both), working as one closed loop. **Rehearse it once before the presentation.**

What was checked when:
- **Filler** steps: all checked live with FUXA as the HMI in HMI stage 7.3 (`roadmap.md`, 2026-11-04).
- **Syrup room** steps: the scenarios on the real PLCs (`buffer_starved` and `level_sensor_stuck` by hand, 2026-11-13), the *Syrup room* screen with `cooling_failure` / `over_pressure` (S3, 2026-11-13). The Line block (S4), the syrup alarms (S5) and the syrup trends (S6) were checked live on 2026-11-14 (`roadmap.md`, "Live verification S4–S6"); the whole script was rehearsed on 2026-11-15.

**Screens:** browser with FUXA (`http://localhost:1881`), one terminal with the simulator panel (at least 140×45 for the syrup tab). Optional: OpenPLC editor (filler `https://localhost:8443`, syrup room `https://127.0.0.2:8443`) to show the ladder.

**Rule:** during the demo FUXA is the only operator (Start, Stop, Reset, mode, setpoints) for both machines. Never run `tests/hmi.py` or a `tests/test_*.py` script at the same time.

**Which machine a button acts on:** header row 1 shows both machines; row 2 (Start / Stop / Reset, Auto / Manual / Stop, alarm chips) belongs to the machine of the screen. On Line, Alarms and Trends the two buttons on the left of row 2 pick the machine; on *Filler* / *Syrup room* it is that machine.

## 1. Before the presentation (15 min before)

1. Start the stack (repo root): `docker compose up -d`
2. Pre-demo check: `cd simulator && uv run python tests/demo_check.py`. If D2 says *Modbus server is OFF* or *no compiled program* (after a container re-creation or a laptop suspend): OpenPLC Editor, upload `plcs/filler` (device `localhost`) and `plcs/syrup` (device `127.0.0.2`).
3. Start the simulator with its panel: `cd simulator && uv run simulator` (drives both PLCs).
4. FUXA **Alarms**: **Ack** every row, **Reset** – then pick **Syrup room** in row 2 and **Reset** again. A fresh simulator start always leaves `Alm_CommLost` latched on **both** machines. One Reset per machine is enough, also after a FAULT (ladder fix 2026-11-14).
5. `uv run python tests/demo_check.py` again → **0 FAIL** (a WARN tells you the fix).
6. Panel defaults, tab `1 Filler`: **Supply /min** `30`, **Fill flow** `100`, switches **Supply paused** OFF, **Product available** ON, **E-stop** OFF. Tab `2 Syrup room`: no fault switch ON.
7. FUXA: **Filler** in row 2 → **Auto**, **Start**; **Syrup room** in row 2 → **Auto**, **Start**.
8. Let it run **at least one syrup batch (~2.5 min)** so both Trends views have curves and C1 is full.
9. Close everything else; set the browser zoom so the whole Line view fits.

## 2. The demo

| # | Time | Do | Show / say |
|---|---|---|---|
| 1 | 1 min | FUXA **Line** view | The architecture in one sentence: *each machine has its own PLC, the simulator is the factory, FUXA is the operator – they only talk through the PLCs' memory over Modbus TCP; the two PLCs never read each other.* Point at the two real machines (coloured by their state), the grey stand-ins for the machines not built yet, C1 / C2, and the product pipe from the syrup room into the filler. |
| 2 | 1 min | **Filler** view | Bottles step along C1, the filler is green. The pockets (rinse, fill, cap), the fill bar climbing to the 500 ml setpoint, the lamps of the outputs. *Indexing machine: all stations work in parallel (WORK), then the belt moves every bottle one station (INDEX).* |
| 3 | 2 min | Syrup chip (row 1) → **Syrup room** view | One batch: DOSE_WATER → DOSE_SYRUP (ratio 5.5 : 1 → *Syrup per batch*) → MIX → COOL → CARBONATE → TRANSFER. The pipes colour while their pump or valve runs; temperature in °C and pressure in bar (the PLC keeps 0.1 °C / 0.01 bar integers, FUXA scales). *The buffer is filled batch by batch; the filler takes product from it continuously.* Point at the note under the buffer: *each 0.5 L bottle takes 15 L – the simulator compresses time ×30, otherwise one batch would last hours; the PLCs don't know about it.* |
| 4 | 1 min | Header **Trends**, picker **Syrup room** | Mixing level in two slopes (water, then syrup), temperature down to its setpoint, pressure up to its setpoint, then the batch moves into the buffer. Picker **Filler**: one fill ramp per bottle, good-bottle counter. *FUXA logs these values in its own history.* |
| 5 | 1 min | Panel: scenario **`labeler_stopped [filler]`** → Run; go on with step 6 while it runs | C2 fills up, the filler turns **orange (Blocked)** and waits. *Not a fault: no room downstream; the warning comes only after 2 min.* The filler restarts by itself later. |
| 6 | 2 min | Panel: scenario **`buffer_starved [syrup]`** → Run, FUXA **Line** | The filler draws faster than the syrup room can mix: the buffer runs dry (~1 min), the pipe turns grey, *no product*, the filler turns **yellow (Starved)** – **no alarm**, nobody did anything wrong. The next batch reaches the buffer (~1 min later): product back, the filler continues **by itself**. *The only signal between the two PLCs is `Product_Available` – a hardwired handshake, emulated by the simulator.* |
| 7 | 2 min | Panel: scenario **`level_sensor_stuck [syrup]`** → Run (it waits for the next DOSE_WATER – right after step 6 the buffer is low, so that comes at once), FUXA **Syrup room** | The level sensor freezes while the water pump runs; the tank keeps filling. The **high-high switch** (a separate hardware switch, the backup) trips `Alm_Overflow`: **red (Fault)**, every pump off. **Alarms**: the *Machine* column says *Syrup room*; one list for the whole line. When the panel says the tank is drained: **Ack**, **Reset** (Syrup room in row 2), **Start**. |
| 8 | 1.5 min | Panel: scenario **`index_jam [filler]`** → Run, FUXA **Filler** then **Alarms** | The belt motor is ON but the bottles don't move. After 5 s: **red (Fault)**, `Alm_IndexJam`. *A watchdog timer: the cam S8 must come within 5 s.* When the panel says *press Reset + Start*: **Ack**, **Reset**, **Start** (Filler in row 2). No bottle is lost. |
| 9 | 1.5 min | Panel: scenario **`e_stop [filler]`** → Run, FUXA **Line** | One E-stop chain reaches **both** PLCs: the header chip turns red, everything off at once on both machines (*master enable, like an MCR*). State **Stopped**, not Fault. After release: **Start** for the filler **and** for the syrup room – the half-filled bottle is topped up to 500 ml. |
| 10 | 1 min | Filler: mode **Manual**, **Filler** view | The jog buttons appear only in Manual. Hold **C1** (moves only while held). Hold **Fill** with no bottle at the fill station: nothing happens (*interlock*). Back to **Auto**, **Start**. |
| 11 | 0.5 min | (Optional) OpenPLC editor | One rung, e.g. the syrup room's overflow alarm (`MixLevel ≥ MixMax OR MixHighHigh OR BufferLevel ≥ BufferMax`) or the filler's fill interlock. *Ladder is the main language; every rung was tested over Modbus.* |
| 12 | 0.5 min | – | Wrap-up: built = filler and syrup room (ladder, simulator, HMI, one closed loop over two PLCs); stretch goals = labeler, packer, blow moulder (grey on the Line view). |

Spare scenarios if there are questions:
- filler: `no_product` (Starved, no alarm, bottle topped up), `slow_fill` (`Alm_FillTimeout` after 10 s), `heartbeat_off` (`Alm_CommLost` after 3 s), `gaps` (needs an empty line, see below);
- syrup room: `low_syrup` (Starved + yellow warning `Alm_LowSyrup`, continues by itself after the refill – takes about two batches), `over_pressure` (CO2 valve stuck open, `Alm_OverPressure` at 6 bar), `cooling_failure` and `pump_failure` (each needs its 60 s timeout), `buffer_full` (Blocked in WAIT_BUFFER, continues when the filler has used product).

## 3. Avoid during the demo

- **Restarting the simulator in the middle of a batch:** the PLC remembers a dosed batch (`Batch_Dosed`), the new simulator starts with an empty mixing tank, and the batch cannot finish. Before stopping the simulator, let the batch end and press **Stop** for the syrup room (IDLE). Recovery if it happened: Manual, water pump in pulses under 10 s to about 590 L, then Auto + Start.
- **Syrup scenarios wait for their step:** `level_sensor_stuck` and `pump_failure` start at DOSE_WATER, `cooling_failure` at COOL, `over_pressure` at CARBONATE. A new batch starts only when the buffer is below *New batch below* (default 800 L); run them right after `buffer_starved`, or expect a wait.
- **Manual Door and long manual Fill (filler):** known gaps (`docs/filler/ladder-roadmap.md`, "Change: manual interlocks for door and fill", open). In step 10 use only C1 and Fill without a bottle.
- **Holding a manual button > 10 s:** the jog watchdog ignores it until it is released. Correct, but confusing if unexplained.
- **`gaps` on a full line:** nothing visible happens. First set Supply /min to `0`, wait until the filler is yellow (Starved), then Run; set it back to `30` afterwards.
- **Supply paused ON:** added bottles are queued but never placed.

## 4. If something goes wrong

| Symptom | Fix |
|---|---|
| Bottles stand on C1, filler empty, state Stopped | Start was not pressed in Auto: **Filler** in row 2, **Auto**, **Start**. |
| Syrup room grey, buffer falling, filler soon Starved | Syrup room not started: **Syrup room** in row 2, **Auto**, **Start**. |
| Red alarm chips right after starting the simulator | `Alm_CommLost` from the restart, on both machines: **Ack**, **Reset** per machine, **Start** per machine. |
| Machine stays red after Reset (other cases) | The cause is still there (panel switch ON: E-stop, a syrup fault, fill flow < 50): fix it in the panel, then Reset + Start. |
| A click in FUXA did nothing | About 1–2 % of FUXA's writes time out (issue I16): check the header (state, mode) and press again. |
| Header: *Filler PLC LOST* / *Syrup PLC LOST* | `uv run python tests/demo_check.py`: D2 *Modbus server OFF* → upload that PLC from the Editor; container down → `docker compose up -d`. |
| Trends empty | No data yet (wait one batch) or the page was opened before FUXA reconnected: reload the page. |
| Anything else | Stop the simulator (`q`, after **Stop** for the syrup room), run `tests/demo_check.py`, follow the printed fix, start again from section 1 step 3. |
