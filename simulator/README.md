# Simulator – the virtual plant

Python simulator for the soft drink line (filler machine so far). It reads the PLC outputs over Modbus TCP, simulates the physics (bottles, conveyors, index belt, door, rinse/fill/cap) and writes the sensor values back. It makes no control decisions; the OpenPLC program does.

Design: `docs/project-report.md` section 7. Build stages: `docs/simulator/roadmap.md`. Why things are the way they are: `docs/simulator/design-notes.md`.

## Setup

```bash
cd simulator
uv sync
```

The filler PLC must run in the OpenPLC runtime (`docker compose up -d` in the repo root, Modbus on `127.0.0.1:502`, `mx_bits` = 64). Release all debugger forces first.

## Run

```bash
uv run simulator                                # control panel (q quits), 30 bottles/min supply
uv run simulator --rate 0 --add 20              # panel, exactly 20 bottles
uv run simulator --headless                     # no panel: console + log, until Ctrl+C
uv run simulator --headless --seconds 120       # stop after 2 minutes
```

**Control panel:** left the physical truth (C1, belt pockets with bottle id, ml, `R` rinsed, `C` capped, sensor lamps, C2, wires), right the controls (supply rate, add bottles, fill flow, switches for pause supply / product available / E-stop / index jam / labeler / heartbeat, scenario + Run), below the event log (`l` cycles the filter: all, bottle, PLC, warnings). Every control only puts a command on the queue; the panel never presses Start or Reset (header shows the `tests/hmi.py` command while the PLC is in IDLE). Use a terminal of at least 120×36.

**HMI animation words:** every tick the simulator also writes %MW70–75 (HR 1094–1099) for FUXA: C1 and C2 slot bits and one state code per filler pocket (0 empty, 1 bottle, 2 rinsed, 3 filled, 4 capped). Layout: `docs/filler/io-list.md`. The PLC ignores them.

**Mode:** the header shows the PLC mode. After a PLC restart it is `Stop` and Start is ignored; send `tests/hmi.py auto reset start`.

Headless: events go to the console and to `logs/run-<date>-<time>.log` (`--log` to choose the file). After Ctrl+C a summary shows ticks, `dt` range, GoodCount change, bottles out, lost bottles and warnings.

The simulator never presses Start. The operator uses FUXA (http://localhost:1881: header with Start/Stop/Reset and the mode, screens Line, Filler, Alarms, Trends). Without FUXA, `tests/hmi.py` acts as the HMI (second terminal, while the simulator runs, so the heartbeat can clear `Alm_CommLost`); never use both at once (issue I4, `tests/hmi_guard.py` reminds you):

```bash
uv run python tests/hmi.py auto reset start   # Auto mode, clear alarms, start
uv run python tests/hmi.py status             # step, state, GoodCount, alarms
uv run python tests/hmi.py stop
```

Only one plant writer per PLC: a second simulator, or a test script that writes the sensors while the simulator runs, stops at once with `another plant writer is already writing the sensors of PLC 127.0.0.1:502 (PID …)` (`plant_lock.py`). Before a demo run `uv run python tests/demo_check.py`: it checks containers, PLC program, FUXA and simulator and prints the fix for each problem.

## Fault scenarios

```bash
uv run simulator --list-scenarios                          # names + one-line description
uv run simulator --headless --rate 0 --add 6 --scenario index_jam
```

A scenario is a fixed list of commands (`src/simulator/scenarios.py`): jam the index belt, press the E-stop, stop the syrup, stop the labeler, freeze the heartbeat, slow the fill flow, or add bottles in groups. It waits until the PLC runs, so press Start before or after. It never presses Reset or Start itself: when a fault scenario has removed the cause it prints what to press (`tests/hmi.py reset start`). Commands are logged as `SIM  command: index_jam = True`, the PLC's reaction as PLC lines next to it.

## Reading the log

```
   42.5 s   Bottle 7     entered filler
   55.2 s   Bottle 7     filled to 500 ml
   63.1 s   Bottle 7     left filler (500 ml)
    6.2 s   PLC          step WORK -> INDEX
    0.0 s ! SIM          PLC link lost, outputs taken as OFF
```

`!` marks a warning. **Bottle** warnings are audit findings (spill, two bottles in one pocket, crash on C2, left not rinsed / uncapped / underfilled): the physics shows a control error. **PLC** lines report what the PLC decided. **SIM** warnings concern the simulator itself (slow tick, foreign writer, link). An audit warning right after a "slow tick" may be caused by the simulator, not by the PLC.

## Tests

```bash
uv run pytest                                # unit tests, no PLC needed (some use a local pymodbus server)
uv run python tests/test_link.py             # Modbus first contact with the real PLC (heartbeat only)
uv run python tests/test_link.py --restart   # + reconnect after `docker compose restart openplc`
uv run python tests/test_closed_loop.py      # 20 bottles through the real PLC, ~2.5 min
uv run python tests/test_scenarios.py --quick  # 9 fault scenarios against the real PLC, ~12 min (without --quick ~15 min)
uv run python tests/test_scenarios.py e_stop   # one or a few by name
```

Never run a `tests/*.py` script while the simulator runs: both write the sensors (the simulator would report a "foreign writer").
