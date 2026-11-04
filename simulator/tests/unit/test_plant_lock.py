"""plant_lock.py: one plant writer per PLC (OS lock, released when the process ends).  uv run pytest"""

import subprocess
import sys
import textwrap

import pytest

from simulator import plant_lock

HOST = "127.0.0.9"  # an address no real plant writer uses


def hold_in_child(port: int) -> subprocess.Popen:
    """Start a second process that takes the lock and waits for a line on stdin."""
    code = textwrap.dedent(f"""
        import sys
        from simulator import plant_lock
        plant_lock.acquire({HOST!r}, {port}, "test child")
        print("held", flush=True)
        sys.stdin.readline()
    """)
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def test_same_process_twice_is_fine():
    plant_lock.acquire(HOST, 40001)
    plant_lock.acquire(HOST, 40001)  # idempotent: test_scenarios.py runs the loop many times
    assert "this process" in plant_lock.is_held(HOST, 40001)
    plant_lock.release(HOST, 40001)
    assert plant_lock.is_held(HOST, 40001) is None


def test_second_process_is_refused_and_named_then_free_after_it_ends():
    child = hold_in_child(40002)
    try:
        assert f"PID {child.pid}" in plant_lock.is_held(HOST, 40002)
        with pytest.raises(plant_lock.PlantLockError, match=f"PID {child.pid}: test child"):
            plant_lock.acquire(HOST, 40002)
    finally:
        child.communicate("\n")  # the child ends; the OS drops its lock
    assert plant_lock.is_held(HOST, 40002) is None
    plant_lock.acquire(HOST, 40002)
    plant_lock.release(HOST, 40002)


def test_other_port_is_independent():
    child = hold_in_child(40003)
    try:
        plant_lock.acquire(HOST, 40004)  # e.g. a second PLC, or the unit tests' fake servers
        plant_lock.release(HOST, 40004)
    finally:
        child.communicate("\n")
