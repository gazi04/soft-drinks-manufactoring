"""Plant simulator for the soft drink line: the filler, the syrup room, the labeler and the packer.

    uv run simulator                                 # control panel (q quits), all four PLCs
    uv run simulator --no-packer                     # three PLCs, a stand-in takes the bottles off C3
    uv run simulator --no-labeler                    # filler + syrup room, a stand-in takes the bottles off C2
    uv run simulator --filler-only                   # the filler alone, Product_Available held TRUE, labeler stand-in
    uv run simulator --headless                      # no panel: console + log, until Ctrl+C
    uv run simulator --headless --add 20 --rate 0    # exactly 20 bottles, no rate supply
    uv run simulator --headless --seconds 120 --log logs/run.log
    uv run simulator --headless --scenario index_jam # a fixed fault sequence (scenarios.py)
    uv run simulator --list-scenarios

Panel and headless take the same options (--rate, --add, --scenario, --log, ...).
Press Reset / Start with the HMI (tests/hmi.py until FUXA exists; `hmi.py --syrup ...` for the
syrup room, `hmi.py --labeler ...` for the labeler, `hmi.py --packer ...` for the packer): the simulator never writes commands, modes or setpoints. A scenario waits until the PLC runs,
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
    parser.add_argument("--labeler-host", default=None, help="labeler PLC host (default: same as --host)")
    parser.add_argument("--labeler-port", type=int, default=config.LABELER_PORT)
    parser.add_argument("--packer-host", default=None, help="packer PLC host (default: same as --host)")
    parser.add_argument("--packer-port", type=int, default=config.PACKER_PORT)
    parser.add_argument("--filler-only", action="store_true",
                        help="the filler alone: no syrup room (Product_Available held TRUE), labeler stand-in")
    parser.add_argument("--no-labeler", action="store_true",
                        help="keep the labeler stand-in (no labeler PLC): it drives C2 and takes its bottles")
    parser.add_argument("--no-packer", action="store_true",
                        help="keep the packer stand-in (no packer PLC): it drives C3 and takes its bottles")
    parser.add_argument("--seconds", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    parser.add_argument("--rate", type=float, default=config.SUPPLY_PER_MIN, help="bottle supply per minute")
    parser.add_argument("--add", type=int, default=0, help="extra bottles put on C1 at the start")
    parser.add_argument("--log", type=Path, help="log file (default: logs/run-<date>-<time>.log)")
    parser.add_argument("--scenario", help="run a fixed command sequence (see --list-scenarios)")
    parser.add_argument("--list-scenarios", action="store_true", help="print the scenarios and exit")
    args = parser.parse_args(argv)
    if args.syrup_host is None:
        args.syrup_host = args.host
    if args.labeler_host is None:
        args.labeler_host = args.host
    if args.packer_host is None:
        args.packer_host = args.host
    args.with_labeler = not (args.filler_only or args.no_labeler)
    args.with_packer = args.with_labeler and not args.no_packer  # the packer takes its bottles from the labeler
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
    labeler_plc = _labeler_plc(args)
    packer_plc = _packer_plc(args)

    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)  # Ctrl+C: clean stop
    syrup_text = f" + syrup PLC {args.syrup_host}:{args.syrup_port}" if syrup_plc else " (no syrup room)"
    labeler_text = f" + labeler PLC {args.labeler_host}:{args.labeler_port}" if labeler_plc else " (labeler stand-in)"
    packer_text = f" + packer PLC {args.packer_host}:{args.packer_port}" if packer_plc else " (packer stand-in)"
    print(f"Simulator headless -> PLC {args.host}:{args.port}{syrup_text}{labeler_text}{packer_text}, log {log_path}. "
          "Ctrl+C to stop.")
    try:
        stats = await loop.run(plc, plant, log, seconds=args.seconds, stop=stop, scenario=runner, syrup_plc=syrup_plc,
                               labeler_plc=labeler_plc, packer_plc=packer_plc)
    finally:
        plc.close()
        if syrup_plc is not None:
            syrup_plc.close()
        if labeler_plc is not None:
            labeler_plc.close()
        if packer_plc is not None:
            packer_plc.close()
        log.close()
    print(f"\n{_summary(stats, plant, log.warning_count)}")


def _log_path(args: argparse.Namespace) -> Path:
    path = args.log or Path("logs") / time.strftime("run-%Y%m%d-%H%M%S.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _plant(args: argparse.Namespace):
    from simulator.plant import Plant

    plant = Plant(with_syrup=not args.filler_only, with_labeler=args.with_labeler, with_packer=args.with_packer)
    plant.supply.per_min = args.rate
    plant.supply.add(args.add)
    return plant


def _syrup_plc(args: argparse.Namespace):
    """The second PLC link, or None with --filler-only."""
    from simulator.plcs import SyrupPlc

    return None if args.filler_only else SyrupPlc(args.syrup_host, args.syrup_port)


def _labeler_plc(args: argparse.Namespace):
    """The third PLC link, or None when the labeler stand-in is used (--no-labeler, --filler-only)."""
    from simulator.plcs import LabelerPlc

    return LabelerPlc(args.labeler_host, args.labeler_port) if args.with_labeler else None


def _packer_plc(args: argparse.Namespace):
    """The fourth PLC link, or None when the packer stand-in is used (--no-packer and the options above)."""
    from simulator.plcs import PackerPlc

    return PackerPlc(args.packer_host, args.packer_port) if args.with_packer else None


def _summary(stats, plant, warnings: int) -> str:
    if plant.labeler is None:
        return (f"{stats.summary()}, bottles out {plant.labeler_standin.taken + plant.c2.count}, "
                f"lost {len(plant.filler.lost)}, warnings {warnings}")
    lab = plant.labeler
    if plant.packer is None:
        packed = f"packed {plant.packer_standin.taken}"
    else:
        packed = f"packs {plant.packer.groups_pushed} pushed / {plant.palletizer.taken} palletized"
    return (f"{stats.summary()}, labelled {lab.labelled} of {lab.discharged} discharged, {packed}, "
            f"lost filler {len(plant.filler.lost)} / labeler {len(lab.lost)}, warnings {warnings}")


def run_panel(args: argparse.Namespace) -> None:
    import logging

    from simulator.plcs import FillerPlc
    from simulator.ui.app import SimulatorApp

    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # stderr lines would garble the screen
    log_path = _log_path(args)
    plant = _plant(args)
    app = SimulatorApp(plant, FillerPlc(args.host, args.port), log_path, args.scenario, syrup_plc=_syrup_plc(args),
                       labeler_plc=_labeler_plc(args), packer_plc=_packer_plc(args))
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
    from simulator.scenarios import uses_command

    if args.scenario and args.with_labeler and uses_command(SCENARIOS[args.scenario], "labeler_running"):
        raise SystemExit(f"scenario {args.scenario!r} stops the labeler stand-in: add --no-labeler")
    if args.scenario and args.with_packer and uses_command(SCENARIOS[args.scenario], "packer_running"):
        raise SystemExit(f"scenario {args.scenario!r} stops the packer stand-in: add --no-packer")
    if args.scenario and not args.with_packer and SCENARIOS[args.scenario].target == "packer":
        raise SystemExit(f"scenario {args.scenario!r} needs the real packer: leave out --no-packer / --no-labeler / "
                         "--filler-only")
    if args.scenario and not args.with_labeler and SCENARIOS[args.scenario].target == "labeler":
        raise SystemExit(f"scenario {args.scenario!r} needs the real labeler: leave out --no-labeler / --filler-only")
    from simulator import plant_lock

    who = "simulator" + (" --headless" if args.headless else " (panel)")
    try:  # before Textual starts, so a second simulator gets a plain message
        plant_lock.acquire(args.host, args.port, who)
        if not args.filler_only:
            plant_lock.acquire(args.syrup_host, args.syrup_port, who)
        if args.with_labeler:
            plant_lock.acquire(args.labeler_host, args.labeler_port, who)
        if args.with_packer:
            plant_lock.acquire(args.packer_host, args.packer_port, who)
    except plant_lock.PlantLockError as error:
        raise SystemExit(f"simulator: {error}") from None
    if args.headless:
        asyncio.run(run_headless(args))
    else:
        run_panel(args)
