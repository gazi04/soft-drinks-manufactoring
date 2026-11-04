"""Reminder for scripts that act as the HMI (roadmap issue I4: one writer per address).

tests/hmi.py and the PLC test scripts write Cmd, ModeReq, setpoints and Man_* - the same
addresses FUXA writes when an operator clicks. Two HMIs at once fight over them (e.g. a
test switches to Manual while FUXA sends Start), and the result looks like a PLC fault.
It cannot be prevented here (FUXA writes only on a click), so this prints a reminder
when FUXA is running. It never blocks: FUXA may run with nobody operating it.
"""

import sys
import urllib.request

FUXA_URL = "http://localhost:1881"
_warned = False


def fuxa_running(timeout: float = 0.5) -> bool:
    try:
        urllib.request.urlopen(FUXA_URL, timeout=timeout).close()
        return True
    except OSError:
        return False


def warn_if_fuxa(script: str) -> None:
    """Print the reminder once per process (stderr, so the script's own output stays clean)."""
    global _warned
    if _warned or not fuxa_running():
        return
    _warned = True
    print(f"\033[33mNOTE {script}: FUXA is running ({FUXA_URL}). Do not operate it while this script "
          "writes Cmd / ModeReq / setpoints / Man_* (one writer per address).\033[0m", file=sys.stderr)


def claim_plant(script: str, host: str = "127.0.0.1", port: int = 502) -> None:
    """Take the plant lock before the first write (src/simulator/plant_lock.py): a script that
    writes the sensors must never run together with the simulator or another such script."""
    from simulator import plant_lock

    try:
        plant_lock.acquire(host, port, script)
    except plant_lock.PlantLockError as error:
        raise SystemExit(f"{script}: {error}") from None
