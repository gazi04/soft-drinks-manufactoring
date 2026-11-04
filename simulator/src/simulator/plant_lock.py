"""One plant writer per PLC: an OS lock file (demo risk "two writers").

The simulator and the PLC test scripts all write the sensor coils (S2..S8, wiring,
heartbeat). Two of them at once write different values into the same addresses: the
PLC then sees a world that does not exist (bottles were lost in a test this way), and
the simulator's read-back only notices it afterwards. So every plant writer takes this
lock before its first write, and a second one stops at once with a clear message.

How: a non-blocking fcntl.flock on <tempdir>/soft-drink-plant-<host>-<port>.lock.
- The OS releases the lock when the process ends, also after a crash or kill -9, so a
  stale lock can never block the next run (a plain "lock file exists" check could).
- The file name contains host and port: the unit tests talk to fake PLC servers on
  other ports and never collide with a running simulator; a later second PLC
  (ports 5021-5025) gets its own lock.
- acquire() is idempotent inside one process (test_scenarios.py calls loop.run often);
  the lock is held until the process ends.
"""

import fcntl
import os
import tempfile
from pathlib import Path

_held: dict[str, int] = {}  # lock path -> open file descriptor (kept open = lock held)


class PlantLockError(RuntimeError):
    """Another process already writes the plant signals of this PLC."""


def lock_path(host: str, port: int) -> Path:
    safe_host = "".join(c if c.isalnum() or c in ".-" else "_" for c in host)
    return Path(tempfile.gettempdir()) / f"soft-drink-plant-{safe_host}-{port}.lock"


def acquire(host: str, port: int, who: str = "plant writer") -> None:
    """Take the lock for this PLC or raise PlantLockError naming the process that holds it."""
    path = lock_path(host, port)
    if str(path) in _held:
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = os.pread(fd, 200, 0).decode(errors="replace").strip() or "unknown process"
        os.close(fd)
        raise PlantLockError(
            f"another plant writer is already writing the sensors of PLC {host}:{port} ({holder}). "
            "Stop it first: only one simulator or test script at a time (one writer per address)."
        ) from None
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"PID {os.getpid()}: {who}".encode(), 0)
    _held[str(path)] = fd


def is_held(host: str, port: int) -> str | None:
    """Who holds the lock (text), or None if it is free. Does not take it."""
    path = lock_path(host, port)
    if str(path) in _held:
        return f"PID {os.getpid()} (this process)"
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return os.pread(fd, 200, 0).decode(errors="replace").strip() or "unknown process"
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def release(host: str, port: int) -> None:
    """Give the lock back early (normally the process end does it)."""
    fd = _held.pop(str(lock_path(host, port)), None)
    if fd is not None:
        os.close(fd)
