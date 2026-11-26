# Simulator – the virtual plant

Python simulator for the soft drink line: the blow moulder, the filler, the syrup room, the labeler and the packer (the palletizer is a stand-in). It reads the outputs of the five PLCs over Modbus TCP, simulates the physics (blow moulder: hopper, loader, oven pocket heated by the lamps' analog output, transfer arm, mould, stretch rod, blow pressure as raw transducer counts, ejector; filler: bottles, conveyors, index belt, door, rinse/fill/cap; syrup room: tank levels, product temperature, CO2 pressure, the buffer the filler drains; labeler: gate, pocket, label web, hot-melt tank; packer: collation, wrap conveyor, film, wrap bar, shrink oven, outfeed table) and writes the sensor values back. It copies the syrup room's `Product_Available` to the filler like a wire. It makes no control decisions; the OpenPLC programs do.

Design: `docs/project-report.md` section 7. Build stages: `docs/simulator/roadmap.md`. Why things are the way they are: `docs/simulator/design-notes.md`.

## Setup

```bash
cd simulator
uv sync
```

Both PLCs must run in their OpenPLC runtimes (`docker compose up -d` in the repo root): the filler on Modbus `127.0.0.1:502`, the syrup room on `127.0.0.1:5022` (`mx_bits` = 64 in both projects). Release all debugger forces first. Without the syrup room runtime use `--filler-only`. The labeler (`127.0.0.1:5023`) is the third PLC and part of the default: it drives C2, so it must be in Auto and started (`tests/hmi.py --labeler auto reset start`), otherwise C2 stands and the filler blocks; `--no-labeler` keeps the old labeler stand-in. The packer (`127.0.0.1:5024`) is the fourth PLC, also part of the default: it drives C3, so it must be in Auto and started (from FUXA's header, row 2: Packer, or with `tests/hmi.py --packer auto reset start`), otherwise C3 fills and the labeler blocks; `--no-packer` keeps the packer stand-in. The blow moulder (`127.0.0.1:5025`) is the fifth PLC, also part of the default: it makes the bottles on C1 from preforms, so it must be in Auto and started (`tests/hmi.py --blowmoulder auto reset start`; FUXA has no blow moulder screen yet), otherwise no bottle reaches the filler (Starved); `--no-blowmoulder` keeps the old bottle supply (30 bottles/min, `--rate` / `--add` only with it).

## Run

```bash
uv run simulator                                # control panel (q quits), all five PLCs
uv run simulator --no-blowmoulder               # four PLCs, the bottle supply (30/min) instead of the blow moulder
uv run simulator --no-packer                    # a stand-in takes the bottles off C3
uv run simulator --no-labeler                   # filler + syrup room, a stand-in takes the bottles off C2
uv run simulator --filler-only                  # the filler alone, Product_Available held TRUE, supply + labeler stand-in
uv run simulator --no-blowmoulder --rate 0 --add 20   # panel, exactly 20 bottles
uv run simulator --headless                     # no panel: console + log, until Ctrl+C
uv run simulator --headless --seconds 120       # stop after 2 minutes
```

**Control panel:** left the physical truth (C1, belt pockets with bottle id, ml, `R` rinsed, `C` capped, sensor lamps, C2, wires), right the controls (supply rate, add bottles, fill flow, switches for pause supply / product available / E-stop / index jam / labeler / heartbeat, scenario + Run), below the event log (`l` cycles the filter: all, bottle, PLC, warnings). Every control only puts a command on the queue; the panel never presses Start or Reset (header shows the `tests/hmi.py` command while the PLC is in IDLE). Use a terminal of at least 120×36. With the syrup room (default) the panel has two tabs (keys `1` / `2`): `2 Syrup room` shows the tanks (level bars), product temperature, CO2 pressure, the syrup PLC's output lamps and red fault markers, and has switches for chiller / water pump / syrup pump failed, CO2 valve stuck, level sensor stuck and the syrup heartbeat, value rows for deliver / drain syrup, draw / fill buffer, draw scale, and the buttons Vent tank and Drain mix tank. With the real labeler (default) a third tab `3 Labeler` (key `3`) shows C2 and the gate, the pocket (bottle, label share, bell, turns), the label roll (bar, labels left), glue temperature and glue level, C3 and the packer, the labeler PLC's output lamps and red fault markers, and has switches for label web torn / bell stuck / heater failed / heater stuck on / gate jam / packer running / labeler heartbeat, value rows for load roll, glue level, add glue, and the button Clear gate. The scenario row under the tabs follows the active tab: its select offers only that machine's scenarios, the status shows the running one with its machine (`gaps (filler): step 2/5`) on every tab, and starting a second scenario stops the first (one slot). With the real packer (default) a fourth tab `4 Packer` (key `4`) shows C3 and the gate, the collation table (bottles counted), the pusher, the wrap conveyor and the oven as tracks of 50 mm cells (packs `█`, the film stop eye `¦`, the film slot `|`), the film roll, the sheet in the slot, the wrap bar, the oven temperature, the outfeed table and the palletizer, the packer PLC's output lamps and red fault markers, and has switches for film torn / pusher stuck / wrap bar stuck / oven heater failed / stuck on / blower failed / packs stuck in the oven / palletizer running / packer heartbeat, value rows for load film (m) and put sheet (mm), and the buttons Drop sheet and Clear oven. Use 140×45 for the syrup, labeler and packer tabs. With the real blow moulder (default) the filler tab's first line shows the blow moulder (hopper, oven temperature, bottles onto C1) instead of the supply, and the supply controls are gone; its own tab follows with its stage 7.

**HMI animation words:** every tick the simulator also writes %MW70–75 (HR 1094–1099) for FUXA: C1 and C2 slot bits and one state code per filler pocket (0 empty, 1 bottle, 2 rinsed, 3 filled, 4 capped). Layout: `docs/filler/io-list.md`. The PLC ignores them.

**Mode:** the header shows the PLC mode. After a PLC restart it is `Stop` and Start is ignored; send `tests/hmi.py auto reset start`.

Headless: events go to the console and to `logs/run-<date>-<time>.log` (`--log` to choose the file). After Ctrl+C a summary shows ticks, `dt` range, GoodCount change, bottles out, lost bottles and warnings.

The simulator never presses Start. The operator uses FUXA (http://localhost:1881: header with Start/Stop/Reset and the mode, screens Line, Filler, Alarms, Trends). Without FUXA, `tests/hmi.py` acts as the HMI (second terminal, while the simulator runs, so the heartbeat can clear `Alm_CommLost`); never use both at once (issue I4, `tests/hmi_guard.py` reminds you):

```bash
uv run python tests/hmi.py auto reset start   # Auto mode, clear alarms, start
uv run python tests/hmi.py status             # step, state, GoodCount, alarms
uv run python tests/hmi.py stop
uv run python tests/hmi.py --labeler auto reset start   # the labeler PLC (port 5023); --labeler resetcount = Cmd 4
uv run python tests/hmi.py --packer auto reset start    # the packer PLC (port 5024); --packer resetcount = Cmd 4 (PackCount)
uv run python tests/hmi.py --blowmoulder auto reset start  # the blow moulder PLC (port 5025); --blowmoulder resetcount = Cmd 4 (BottleCount)
```

Only one plant writer per PLC: a second simulator, or a test script that writes the sensors while the simulator runs, stops at once with `another plant writer is already writing the sensors of PLC 127.0.0.1:502 (PID …)` (`plant_lock.py`). Before a demo run `uv run python tests/demo_check.py`: it checks the containers, all four PLC programs (filler, syrup room, labeler, packer: program present, Modbus server on, answering), FUXA's link to the filler, syrup room and labeler (the packer gets its FUXA device with its HMI), the simulator (a plant lock per PLC; packer free = started with `--no-packer`, labeler + packer free = `--no-labeler`, syrup room + labeler + packer free = `--filler-only`) and per PLC mode and alarms (the packer with both step words and all ten alarms), and prints the fix for each problem. The labeler's Auto / Reset / Start: `uv run python tests/hmi.py --labeler auto reset start`. The syrup room's Auto / Reset / Start are in the FUXA header (row 2 = the machine of the screen, e.g. on the `Syrup room` screen); `uv run python tests/hmi.py --syrup` does the same from the command line (never both at once).

## Fault scenarios

```bash
uv run simulator --list-scenarios                          # names + one-line description
uv run simulator --headless --rate 0 --add 6 --scenario index_jam
```

A scenario is a fixed list of commands (`src/simulator/scenarios.py`). Each has a target, shown as `[filler]` or `[syrup]` in the list:

- **filler:** jam the index belt, press the E-stop, stop the syrup, stop the labeler, freeze the heartbeat, slow the fill flow, add bottles in groups;
- **syrup room:** low syrup, chiller failed, CO2 valve stuck open, water pump failed, level sensor stuck, buffer full, buffer starved (the last one needs the filler running too). A syrup scenario cannot run with `--filler-only`.
- **labeler** (the real labeler, not with `--no-labeler`; Reset / Start with `tests/hmi.py --labeler ...`): web torn, bell stuck, heater failed, heater stuck on (over-temperature), roll empty + roll change, glue low + cold glue refill, gate jam, packer stopped. `labeler_stopped` stops the labeler stand-in and needs `--no-labeler`; `packer_stopped` stops the packer stand-in and needs `--no-packer`.
- **blowmoulder** (the real blow moulder, not with `--no-blowmoulder` / `--filler-only`; Reset / Start with `tests/hmi.py --blowmoulder ...`; CLI `--scenario` only until its panel tab): hopper empty + refill, preform stuck in the loader, lamps failed, lamps stuck on (over-temperature), arm stuck, preform dropped in the swing, mould stuck, air low (20 bar), exhaust blocked, regulator failed (45 bar, over-pressure), pressure transducer wire break, ejector stuck, C1 backed up (a bottle across the filler's door: Blocked, warning after 2 min). The recovery ejects what is in the mould (no reject), so `mould_stuck`, `air_low` and `lamps_stuck_on` put one flagged bottle onto C1.
- **packer** (the real packer, not with `--no-packer`; Reset / Start with `tests/hmi.py --packer ...`): film torn, sheet dropped (and put back by hand), film roll empty + roll change, pusher stuck, wrap bar stuck, oven heater failed, oven heater stuck on (over-temperature, blower run-on), blower failed (airflow lost), pack stuck in the oven (cleared by hand), palletizer stopped (table full, Blocked).

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
uv run python tests/test_link_packer.py       # PackerPlc client against the real packer PLC (port 5024)
uv run python tests/test_closed_loop_blowmoulder.py # 6 bottles from preforms through the real blow moulder (and filler) PLC (~1.5 min; --blowmoulder-only)
uv run python tests/test_closed_loop_packer.py # 2 packs through the real filler, labeler and packer PLCs (~2.5 min; --with-syrup = four PLCs, --packer-only)
uv run python tests/test_packer_scenarios.py --quick  # 10 packer fault scenarios against the real packer PLC
uv run python tests/run_filler.py / run_syrup.py / run_labeler.py / run_packer.py  # every PLC script of one machine
```

Never run a `tests/*.py` script while the simulator runs: both write the sensors (the simulator would report a "foreign writer").

## Known open point (packer)

The packer PLC counts the 6th bottle at the gate and pushes the group 0.1–0.2 s later, while that bottle still needs 0.4 s to land on the collation table: the simulator warns `bottle onto the collation table with the pusher out` once per group. The proposed fix is a PLC rung (`Settle_Timer`, TON 0.5 s on WAIT_WRAP before PUSH, `docs/simulator/roadmap.md`, packer stage 4); the closed-loop test counts the warning as a NOTE until then.
