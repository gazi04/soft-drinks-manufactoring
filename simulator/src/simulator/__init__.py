"""Plant simulator for the soft drink line (filler first).

    uv run simulator --headless                      # run until Ctrl+C
    uv run simulator --headless --add 20 --rate 0    # exactly 20 bottles, no rate supply
    uv run simulator --headless --seconds 120 --log logs/run.log
    uv run simulator --headless --scenario index_jam # a fixed fault sequence (scenarios.py)
    uv run simulator --list-scenarios

The Textual control panel comes in stage 8; until then only --headless exists.
Press Reset / Start with the HMI (tests/hmi.py until FUXA exists): the simulator
never writes commands, modes or setpoints. A scenario waits until the PLC runs,
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
    parser.add_argument("--seconds", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    parser.add_argument("--rate", type=float, default=config.SUPPLY_PER_MIN, help="bottle supply per minute")
    parser.add_argument("--add", type=int, default=0, help="extra bottles put on C1 at the start")
    parser.add_argument("--log", type=Path, help="log file (default: logs/run-<date>-<time>.log)")
    parser.add_argument("--scenario", help="run a fixed command sequence (see --list-scenarios)")
    parser.add_argument("--list-scenarios", action="store_true", help="print the scenarios and exit")
    return parser.parse_args(argv)


async def run_headless(args: argparse.Namespace) -> None:
    import logging

    from simulator import loop
    from simulator.events import EventLog
    from simulator.plant import Plant
    from simulator.plcs import FillerPlc
    from simulator.scenarios import SCENARIOS, ScenarioRunner

    runner = ScenarioRunner(SCENARIOS[args.scenario]) if args.scenario else None

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # link problems are logged as events
    log_path = args.log or Path("logs") / time.strftime("run-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = EventLog(file_path=log_path, echo=lambda e: print(e.format(), flush=True))

    plant = Plant()
    plant.supply.per_min = args.rate
    plant.supply.add(args.add)
    plc = FillerPlc(args.host, args.port)

    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)  # Ctrl+C: clean stop
    print(f"Simulator headless -> PLC {args.host}:{args.port}, log {log_path}. Ctrl+C to stop.")
    try:
        stats = await loop.run(plc, plant, log, seconds=args.seconds, stop=stop, scenario=runner)
    finally:
        plc.close()
        log.close()
    print(f"\n{stats.summary()}, bottles out {plant.labeler.taken + plant.c2.count}, "
          f"lost {len(plant.filler.lost)}, warnings {log.warning_count}")


def main() -> None:
    args = parse_args()
    from simulator.scenarios import SCENARIOS

    if args.list_scenarios:
        for scenario in SCENARIOS.values():
            print(f"{scenario.name:<16} {scenario.description}")
        return
    if args.scenario and args.scenario not in SCENARIOS:
        raise SystemExit(f"unknown scenario {args.scenario!r} (known: {', '.join(SCENARIOS)})")
    if not args.headless:
        raise SystemExit("The control panel comes in stage 8. Use --headless for now.")
    asyncio.run(run_headless(args))
