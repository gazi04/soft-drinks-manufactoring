"""Plant simulator for the soft drink line: the filler and the syrup room.

    uv run simulator                                 # control panel (q quits), both PLCs
    uv run simulator --filler-only                   # the filler alone, Product_Available held TRUE
    uv run simulator --headless                      # no panel: console + log, until Ctrl+C
    uv run simulator --headless --add 20 --rate 0    # exactly 20 bottles, no rate supply
    uv run simulator --headless --seconds 120 --log logs/run.log
    uv run simulator --headless --scenario index_jam # a fixed fault sequence (scenarios.py)
    uv run simulator --list-scenarios

Panel and headless take the same options (--rate, --add, --scenario, --log, ...).
Press Reset / Start with the HMI (tests/hmi.py until FUXA exists; `hmi.py --syrup ...` for the
syrup room): the simulator never writes commands, modes or setpoints. A scenario waits until the PLC runs,
so it does not matter whether Start is pressed before or after the simulator starts.
"""

import argparse
import asyncio
import signal
import time
from pathlib import Path

from simulator import config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="simulator", description="Soft drink line plant simulator")
    parser.add_argument("--headless", action="store_true", help="run without the control panel (console + log file)")
    parser.add_argument("--host", default=config.PLC_HOST)
    parser.add_argument("--port", type=int, default=config.PLC_PORT)
    parser.add_argument("--syrup-host", default=None, help="syrup room PLC host (default: same as --host)")
    parser.add_argument("--syrup-port", type=int, default=config.SYRUP_PORT)
    parser.add_argument("--filler-only", action="store_true",
                        help="leave the syrup room out: only the filler PLC, Product_Available held TRUE")
    parser.add_argument("--seconds", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    parser.add_argument("--rate", type=float, default=config.SUPPLY_PER_MIN, help="bottle supply per minute")
    parser.add_argument("--add", type=int, default=0, help="extra bottles put on C1 at the start")
    parser.add_argument("--log", type=Path, help="log file (default: logs/run-<date>-<time>.log)")
    parser.add_argument("--scenario", help="run a fixed command sequence (see --list-scenarios)")
    parser.add_argument("--list-scenarios", action="store_true", help="print the scenarios and exit")
    args = parser.parse_args(argv)
    if args.syrup_host is None:
        args.syrup_host = args.host
    return args


async def run_headless(args: argparse.Namespace) -> None:
    import logging

    from simulator import loop
    from simulator.events import EventLog
    from simulator.plcs import FillerPlc
    from simulator.scenarios import SCENARIOS, ScenarioRunner

    runner = ScenarioRunner(SCENARIOS[args.scenario]) if args.scenario else None

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # link problems are logged as events
    log_path = _log_path(args)
    log = EventLog(file_path=log_path, echo=lambda e: print(e.format(), flush=True))

    plant = _plant(args)
    plc = FillerPlc(args.host, args.port)
    syrup_plc = _syrup_plc(args)

    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)  # Ctrl+C: clean stop
    syrup_text = f" + syrup PLC {args.syrup_host}:{args.syrup_port}" if syrup_plc else " (filler only)"
    print(f"Simulator headless -> PLC {args.host}:{args.port}{syrup_text}, log {log_path}. Ctrl+C to stop.")
    try:
        stats = await loop.run(plc, plant, log, seconds=args.seconds, stop=stop, scenario=runner, syrup_plc=syrup_plc)
    finally:
        plc.close()
        if syrup_plc is not None:
            syrup_plc.close()
        log.close()
    print(f"\n{_summary(stats, plant, log.warning_count)}")


def _log_path(args: argparse.Namespace) -> Path:
    path = args.log or Path("logs") / time.strftime("run-%Y%m%d-%H%M%S.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _plant(args: argparse.Namespace):
    from simulator.plant import Plant

    plant = Plant(with_syrup=not args.filler_only)
    plant.supply.per_min = args.rate
    plant.supply.add(args.add)
    return plant


def _syrup_plc(args: argparse.Namespace):
    """The second PLC link, or None with --filler-only."""
    from simulator.plcs import SyrupPlc

    return None if args.filler_only else SyrupPlc(args.syrup_host, args.syrup_port)


def _summary(stats, plant, warnings: int) -> str:
    return (f"{stats.summary()}, bottles out {plant.labeler.taken + plant.c2.count}, "
            f"lost {len(plant.filler.lost)}, warnings {warnings}")


def run_panel(args: argparse.Namespace) -> None:
    import logging

    from simulator.plcs import FillerPlc
    from simulator.ui.app import SimulatorApp

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # stderr lines would garble the screen
    log_path = _log_path(args)
    plant = _plant(args)
    app = SimulatorApp(plant, FillerPlc(args.host, args.port), log_path, args.scenario, syrup_plc=_syrup_plc(args))
    stats = app.run()  # returns after q: the loop has stopped and the log file is closed
    print(f"Log {log_path}")
    if stats is not None:
        print(_summary(stats, plant, app.events.warning_count))


def main() -> None:
    args = parse_args()
    from simulator.scenarios import SCENARIOS

    if args.list_scenarios:
        for scenario in SCENARIOS.values():
            print(f"{scenario.name:<18} [{scenario.target}] {scenario.description}")
        return
    if args.scenario and args.scenario not in SCENARIOS:
        raise SystemExit(f"unknown scenario {args.scenario!r} (known: {', '.join(SCENARIOS)})")
    if args.scenario and args.filler_only and SCENARIOS[args.scenario].target == "syrup":
        raise SystemExit(f"scenario {args.scenario!r} needs the syrup room: leave out --filler-only")
    from simulator import plant_lock

    who = "simulator" + (" --headless" if args.headless else " (panel)")
    try:  # before Textual starts, so a second simulator gets a plain message
        plant_lock.acquire(args.host, args.port, who)
        if not args.filler_only:
            plant_lock.acquire(args.syrup_host, args.syrup_port, who)
    except plant_lock.PlantLockError as error:
        raise SystemExit(f"simulator: {error}") from None
    if args.headless:
        asyncio.run(run_headless(args))
    else:
        run_panel(args)
