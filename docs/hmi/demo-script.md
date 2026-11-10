# Demo script – filler line (about 10 minutes)

A fixed order for presenting the filler: PLC (OpenPLC), plant (Python simulator) and HMI (FUXA) working as one closed loop. Rehearse it once before the presentation. Every step was checked in HMI stage 7.3 (`roadmap.md`, 2026-11-04).

**Screens:** browser with FUXA (`http://localhost:1881`), one terminal with the simulator panel. Optional: OpenPLC editor (`https://localhost:8443`) to show the ladder.

**Rule:** during the demo FUXA is the only operator (Start, Stop, Reset, mode, setpoints). Never run `tests/hmi.py` or a `tests/test_*.py` script at the same time.

## 1. Before the presentation (15 min before)

1. Start the stack (repo root): `docker compose up -d`
2. Start the simulator with its panel: `cd simulator && uv run simulator`
3. FUXA: open **Alarms**, press **Ack** and **Reset**. A fresh simulator start always leaves `Alm_CommLost` latched (the heartbeat stopped while it was down).
4. Pre-demo check in a second terminal: `cd simulator && uv run python tests/demo_check.py` → all PASS (a WARN tells you the fix).
5. Panel defaults: **Supply /min** `30`, **Fill flow** `100`, switch **Supply paused** OFF, **Product available** ON, **E-stop** OFF.
6. FUXA: mode **Auto**, **Start**. Let it run a minute so C1 and the filler are full of bottles and the Trends charts have data.
7. Close everything else; set the browser zoom so the whole Line view fits.

## 2. The demo

| # | Time | Do | Show / say |
|---|---|---|---|
| 1 | 1 min | FUXA **Line** view | The architecture in one sentence: *the PLC decides, the simulator is the factory, FUXA is the operator – they only talk through the PLC's memory over Modbus TCP.* Point at C1, the filler, C2 and the grey stand-ins for the machines not built yet. |
| 2 | 1 min | Normal run, **Line** then **Filler** view | Bottles step along C1, the filler is green (Running). On **Filler**: the pockets (rinse, fill, cap), the fill bar climbing to the 500 ml setpoint, the lamps of the outputs. *Indexing machine: all stations work in parallel (WORK), then the belt moves every bottle one station (INDEX).* |
| 3 | 1 min | Panel terminal | The event log: every bottle is followed and audited (`rinsed`, `filled to 500 ml`, `capped`). *The simulator checks the PLC: a bad bottle would show up as a `!` warning.* |
| 4 | 1 min | Panel: scenario **`labeler_stopped`** → Run, then go on with step 5 while it runs | C2 fills up, the filler turns **orange (Blocked)** and waits. *Not a fault: the machine stops itself because there is no room downstream; the alarm comes only after 2 min.* The alarm and the automatic restart appear during step 5. |
| 5 | 1 min | FUXA **Trends** | Fill level against the setpoint (one ramp per bottle), good-bottle counter, machine state. *FUXA stores these four values in its own history.* Back to **Line** when the alarm `Alm_OutfeedBlocked` appears: C2 empties, the filler goes green **by itself**. **Alarms**: Ack, Reset. |
| 6 | 1.5 min | Panel: scenario **`index_jam`** → Run, FUXA **Filler** then **Alarms** | The belt motor is ON but the bottles don't move. After 5 s: **red (Fault)**, `Alm_IndexJam`, everything off. *A watchdog timer: the cam S8 must come within 5 s.* When the panel says *press Reset + Start*: **Ack**, **Reset**, **Start** – the machine finishes the move, no bottle is lost. |
| 7 | 1.5 min | Panel: scenario **`e_stop`** → Run, FUXA **Filler** | While a bottle is half filled: E-stop – everything off at once (*master enable, like an MCR*). State **Stopped**, not Fault. After release: only **Start** – the same bottle is topped up from ~200 ml to 500 ml. |
| 8 | 1 min | FUXA mode **Manual**, **Filler** view | The jog buttons appear only in Manual. Hold **C1** (moves only while held). Hold **Fill** with no bottle at the fill station: nothing happens (*interlock: no bottle, no fill*). Back to **Auto**, **Start**. |
| 9 | 0.5 min | (Optional) OpenPLC editor | One rung, e.g. the fill request with its interlock, or the alarm timers. *Ladder is the main language; every rung was tested over Modbus.* |
| 10 | 0.5 min | – | Wrap-up: what is built (filler: ladder, simulator, HMI), what comes next (syrup room → real PLC-to-PLC handshake `Product_Available`). |

Spare scenarios if there are questions: `no_product` (Starved, no alarm, bottle topped up), `slow_fill` (`Alm_FillTimeout` after 10 s), `heartbeat_off` (`Alm_CommLost` after 3 s), `gaps` (needs an empty line, see below).

## 3. Avoid during the demo

- **Manual Door and long manual Fill:** known gaps (`docs/filler/ladder-roadmap.md`, "Change: manual interlocks for door and fill", open). The door can drop a second bottle into a loaded pocket, and Fill has no upper limit. In step 8 use only C1 and Fill without a bottle, until the fix is built.
- **Holding a manual button > 10 s:** the jog watchdog ignores it until it is released. Correct behaviour, but confusing if unexplained.
- **`gaps` on a full line:** nothing visible happens. First set Supply /min to `0`, wait until the filler is yellow (Starved), then Run. The scenario leaves the supply at 0: set it back to `30` afterwards.
- **Supply paused ON:** added bottles are queued but never placed.

## 4. If something goes wrong

| Symptom | Fix |
|---|---|
| Bottles stand on C1, filler empty, state Stopped | Start was not pressed in Auto: mode **Auto**, **Start**. |
| Red alarm chip right after starting the simulator | `Alm_CommLost` from the restart: **Ack**, **Reset**, **Start**. |
| A click in FUXA did nothing | About 1–2 % of FUXA's writes time out (issue I16): check the header (state, mode) and press again. |
| Nothing moves, header shows the PLC link down | `docker compose ps`; restart with `docker compose restart openplc`, then `tests/demo_check.py`. |
| Machine stays red after Reset | The cause is still there (e.g. panel switch E-stop ON, fill flow < 50): fix it in the panel, then Reset + Start. |
| Anything else | Stop the simulator (`q`), run `tests/demo_check.py`, follow the printed fix, start again from section 1 step 2. |
