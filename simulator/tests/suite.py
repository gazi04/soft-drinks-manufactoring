"""Shared runner for the per-PLC test suites (run_filler.py, run_syrup.py).

Runs the test scripts of one machine one after the other, each in its own Python
process (exactly as when started by hand), shows their output live and prints a
summary at the end. Exit code 0 only if every script passed.

Why separate processes and not pytest: the scripts are plain Modbus scripts against
the real PLC; each one takes the plant lock (plant_lock.py), starts its own heartbeat
and resets the PLC in its `finally` block. One process per script keeps them
exactly as they are documented and as they run alone.

stdin is closed for the child processes, so the scripts skip their interactive
debugger steps (they only ask in a terminal, `sys.stdin.isatty()`).
"""

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pymodbus.client import ModbusTcpClient

TESTS_DIR = Path(__file__).resolve().parent
# "12/14 checks passed" or "8/8 automatic checks passed" (last such line of a script)
COUNT_LINE = re.compile(r"(\d+)/(\d+) (?:automatic )?checks passed")


@dataclass(frozen=True)
class Script:
    name: str  # file in tests/
    about: str  # one line for --list and the summary
    args: tuple[str, ...] = ()  # arguments in a normal run
    quick_args: tuple[str, ...] | None = None  # arguments with --quick (None = same as args)
    slow: bool = False  # left out with --quick
    needs: tuple[int, ...] = ()  # other PLC ports (same host) the script also talks to


@dataclass
class Result:
    script: Script
    code: int | None  # None = not run (a needed PLC does not answer)
    seconds: float
    count: str  # "35/35" or "-" when the script prints no count
    note: str = ""


def plc_answers(host: str, port: int) -> bool:
    """One Modbus read: docker-proxy accepts TCP even when the runtime's Modbus server is off."""
    client = ModbusTcpClient(host, port=port, timeout=2)
    try:
        if not client.connect():
            return False
        return not client.read_holding_registers(1024, count=1).isError()
    except Exception:
        return False
    finally:
        client.close()


def run_one(script: Script, quick: bool) -> Result:
    args = script.quick_args if quick and script.quick_args is not None else script.args
    command = [sys.executable, str(TESTS_DIR / script.name), *args]
    print(f"\n\033[1m=== {script.name} {' '.join(args)}\033[0m  ({script.about})", flush=True)
    start = time.monotonic()
    count = "-"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # live output through the pipe
    with subprocess.Popen(command, cwd=TESTS_DIR.parent, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as child:
        assert child.stdout is not None
        for line in child.stdout:
            print(line, end="", flush=True)
            match = COUNT_LINE.search(line)
            if match:
                count = f"{match.group(1)}/{match.group(2)}"
    return Result(script, child.returncode, time.monotonic() - start, count)


def print_summary(machine: str, results: list[Result], skipped: list[Script]) -> None:
    width = max(len(r.script.name) for r in results)
    print(f"\n\033[1m=== {machine}: summary\033[0m")
    for r in results:
        if r.code is None:
            status = f"\033[33mSKIP\033[0m ({r.note})"
        elif r.code == 0:
            status = "\033[32mPASS\033[0m"
        else:
            status = f"\033[31mFAIL\033[0m (exit {r.code})"
        print(f"  {r.script.name:<{width}}  {r.count:>7}  {r.seconds:6.1f} s  {status}")
    for s in skipped:
        print(f"  {s.name:<{width}}  {'':>7}  {'':>8}  skipped")
    failed = [r for r in results if r.code not in (0, None)]
    not_run = [r for r in results if r.code is None]
    total = sum(r.seconds for r in results)
    verdict = "ALL PASSED" if not failed and not not_run else ", ".join(
        part for part in (f"{len(failed)} FAILED" if failed else "",
                          f"{len(not_run)} NOT RUN" if not_run else "") if part)
    print(f"  {len(results)} scripts, {total / 60:.1f} min: {verdict}")


def main(machine: str, host: str, port: int, scripts: list[Script]) -> None:
    parser = argparse.ArgumentParser(
        description=f"Run all {machine} PLC test scripts against the runtime on {host}:{port}.",
        epilog="Do not run the simulator at the same time, release all debugger forces, "
               "and do not operate FUXA while the tests run.")
    parser.add_argument("names", nargs="*",
                        help="run only these scripts (part of the file name, e.g. 'outputs alarms')")
    parser.add_argument("--quick", action="store_true", help="leave out the slow scripts")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failing script")
    parser.add_argument("--list", action="store_true", help="list the scripts and exit")
    options = parser.parse_args()

    if options.list:
        for s in scripts:
            extra = " (slow, left out with --quick)" if s.slow else ""
            print(f"{s.name:<28} {s.about}{extra}")
        return

    chosen = [s for s in scripts if not options.names or any(n in s.name for n in options.names)]
    if not chosen:
        raise SystemExit(f"No script matches {options.names} (see --list)")
    skipped = [s for s in chosen if options.quick and s.slow]
    chosen = [s for s in chosen if s not in skipped]

    if not plc_answers(host, port):
        raise SystemExit(f"{machine} PLC does not answer Modbus on {host}:{port}. "
                         "Run `uv run python tests/demo_check.py` for the fix.")

    results = []
    try:
        for script in chosen:
            missing = [p for p in script.needs if not plc_answers(host, p)]
            if missing:
                note = f"PLC on port {', '.join(map(str, missing))} does not answer"
                print(f"\n\033[1m=== {script.name}\033[0m  \033[33mnot run: {note}\033[0m", flush=True)
                results.append(Result(script, None, 0.0, "-", note))
                continue
            result = run_one(script, options.quick)
            results.append(result)
            if result.code != 0 and options.fail_fast:
                break
    except KeyboardInterrupt:
        print("\nInterrupted.")
    if results:
        print_summary(machine, results, skipped)
    sys.exit(0 if results and all(r.code == 0 for r in results) and len(results) == len(chosen) else 1)
