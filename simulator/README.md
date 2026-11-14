# Simulator – the virtual plant

Python simulator for the soft drink line: the filler and the syrup room (the other machines are stand-ins). It reads the outputs of both PLCs over Modbus TCP, simulates the physics (filler: bottles, conveyors, index belt, door, rinse/fill/cap; syrup room: tank levels, product temperature, CO2 pressure, the buffer the filler drains) and writes the sensor values back. It copies the syrup room's `Product_Available` to the filler like a wire. It makes no control decisions; the OpenPLC programs do.

Design: `docs/project-report.md` section 7. Build stages: `docs/simulator/roadmap.md`. Why things are the way they are: `docs/simulator/design-notes.md`.

## Setup

```bash
cd simulator
uv sync
```

Both PLCs must run in their OpenPLC runtimes (`docker compose up -d` in the repo root): the filler on Modbus `127.0.0.1:502`, the syrup room on `127.0.0.1:5022` (`mx_bits` = 64 in both projects). Release all debugger forces first. Without the syrup room runtime use `--filler-only`.

## Run

```bash
uv run simulator                                # control panel (q quits), both PLCs, 30 bottles/min supply
uv run simulator --filler-only                  # the filler alone, Product_Available held TRUE (old behaviour)
uv run simulator --rate 0 --add 20              # panel, exactly 20 bottles
uv run simulator --headless                     # no panel: console + log, until Ctrl+C
uv run simulator --headless --seconds 120       # stop after 2 minutes
```

**Control panel:** left the physical truth (C1, belt pockets with bottle id, ml, `R` rinsed, `C` capped, sensor lamps, C2, wires), right the controls (supply rate, add bottles, fill flow, switches for pause supply / product available / E-stop / index jam / labeler / heartbeat, scenario + Run), below the event log (`l` cycles the filter: all, bottle, PLC, warnings). Every control only puts a command on the queue; the panel never presses Start or Reset (header shows the `tests/hmi.py` command while the PLC is in IDLE). Use a terminal of at least 120×36. With the syrup room (default) the panel has two tabs (keys `1` / `2`): `2 Syrup room` shows the tanks (level bars), product temperature, CO2 pressure, the syrup PLC's output lamps and red fault markers, and has switches for chiller / water pump / syrup pump failed, CO2 valve stuck, level sensor stuck and the syrup heartbeat, value rows for deliver / drain syrup, draw / fill buffer, draw scale, and the buttons Vent tank and Drain mix tank. The scenario row under the tabs serves both machines (`name [filler]` / `name [syrup]`). Use 140×45 for the syrup tab.

**HMI animation words:** every tick the simulator also writes %MW70–75 (HR 1094–1099) for FUXA: C1 and C2 slot bits and one state code per filler pocket (0 empty, 1 bottle, 2 rinsed, 3 filled, 4 capped). Layout: `docs/filler/io-list.md`. The PLC ignores them.

**Mode:** the header shows the PLC mode. After a PLC restart it is `Stop` and Start is ignored; send `tests/hmi.py auto reset start`.

Headless: events go to the console and to `logs/run-<date>-<time>.log` (`--log` to choose the file). After Ctrl+C a summary shows ticks, `dt` range, GoodCount change, bottles out, lost bottles and warnings.

The simulator never presses Start. The operator uses FUXA (http://localhost:1881: header with Start/Stop/Reset and the mode, screens Line, Filler, Alarms, Trends). Without FUXA, `tests/hmi.py` acts as the HMI (second terminal, while the simulator runs, so the heartbeat can clear `Alm_CommLost`); never use both at once (issue I4, `tests/hmi_guard.py` reminds you):

```bash
uv run python tests/hmi.py auto reset start   # Auto mode, clear alarms, start
uv run python tests/hmi.py status             # step, state, GoodCount, alarms
uv run python tests/hmi.py stop
```

Only one plant writer per PLC: a second simulator, or a test script that writes the sensors while the simulator runs, stops at once with `another plant writer is already writing the sensors of PLC 127.0.0.1:502 (PID …)` (`plant_lock.py`). Before a demo run `uv run python tests/demo_check.py`: it checks the containers, both PLC programs (filler and syrup room: program present, Modbus server on, answering), FUXA, the simulator (a plant lock per PLC; only the filler's = started with `--filler-only`) and per PLC mode and alarms, and prints the fix for each problem. The syrup room's Auto / Reset / Start are in the FUXA header (row 2 = the machine of the screen, e.g. on the `Syrup room` screen); `uv run python tests/hmi.py --syrup` does the same from the command line (never both at once).

## Fault scenarios

```bash
uv run simulator --list-scenarios                          # names + one-line description
uv run simulator --headless --rate 0 --add 6 --scenario index_jam
```

A scenario is a fixed list of commands (`src/simulator/scenarios.py`). Each has a target, shown as `[filler]` or `[syrup]` in the list:

- **filler:** jam the index belt, press the E-stop, stop the syrup, stop the labeler, freeze the heartbeat, slow the fill flow, add bottles in groups;
- **syrup room:** low syrup, chiller failed, CO2 valve stuck open, water pump failed, level sensor stuck, buffer full, buffer starved (the last one needs the filler running too). A syrup scenario cannot run with `--filler-only`.

Faults change the world, never the PLC. A stuck valve or sensor ends with the maintenance action (`vent_tank`, `drain_mix_tank`), because nothing in the model lowers 7 bar or 980 L by itself. A scenario waits until its PLC runs, so press Start before or after. It never presses Reset or Start itself: when a fault scenario has removed the cause it prints what to press (`tests/hmi.py reset start`, for the syrup room `tests/hmi.py --syrup reset start`). Commands are logged as `SIM  command: index_jam = True`, the PLC's reaction as PLC lines next to it (`syrup: ...` for the syrup room).

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
uv run python tests/test_link_syrup.py        # SyrupPlc client against the real syrup room PLC (port 5022)
uv run python tests/test_closed_loop_syrup.py # one batch through BOTH real PLCs, the filler fed by the real Product_Available (~3 min)
uv run python tests/test_syrup_scenarios.py --quick  # 6 syrup fault scenarios against the real syrup PLC
uv run python tests/run_filler.py / run_syrup.py      # every PLC script of one machine in one command
```

Never run a `tests/*.py` script while the simulator runs: both write the sensors (the simulator would report a "foreign writer").
