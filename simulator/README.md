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
uv run simulator --headless                     # until Ctrl+C, 30 bottles/min supply
uv run simulator --headless --rate 0 --add 20   # exactly 20 bottles
uv run simulator --headless --seconds 120       # stop after 2 minutes
```

Events go to the console and to `logs/run-<date>-<time>.log` (`--log` to choose the file). After Ctrl+C a summary shows ticks, `dt` range, GoodCount change, bottles out, lost bottles and warnings.

The simulator never presses Start. Until the FUXA screens exist, `tests/hmi.py` acts as the HMI (second terminal, while the simulator runs, so the heartbeat can clear `Alm_CommLost`):

```bash
uv run python tests/hmi.py auto reset start   # Auto mode, clear alarms, start
uv run python tests/hmi.py status             # step, state, GoodCount, alarms
uv run python tests/hmi.py stop
```

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
