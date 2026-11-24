"""Manual label cycle on the real labeler PLC (check of the bell toggle, 2026-11-24).

The simulator loop runs (Plant(with_labeler=True) + LabelerPlc, filler link on a closed port, one bottle
put on C2 by the script). A separate client ACTS AS THE HMI in Manual mode, exactly as an operator with
one mouse: hold C2, hold Gate, click Bell, hold Feed + glue to the mark, hold Knife briefly, hold
Turntable for two turns, click Bell, hold Pusher. Never two buttons at once.

    M1  bell down after one click and still down after the release
    M2  feed + glue turns the table: one whole label on the bottle, nothing lost
    M3  bell up after the second click
    M4  the bottle leaves on C3 labelled (Labeler.labelled 1, C3 1)
    M5  no simulator warning (label lost, unclamped spin, push against the bell, knife missed)

    uv run python tests/manual_label_cycle.py      (~40 s)

No simulator, other test script or FUXA operator at the same time. Leaves the PLC in Stop mode.
"""

import asyncio
import logging
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map_labeler as lm
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.plant import Plant
from simulator.plcs import FillerPlc, LabelerPlc

HOST = config.PLC_HOST
MANUAL, STOP_MODE = 2, 0


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


async def main() -> int:
    claim_plant("manual_label_cycle", HOST, config.LABELER_PORT)
    warn_if_fuxa("manual_label_cycle")
    path = Path("logs") / time.strftime("manual-label-cycle-%Y%m%d-%H%M%S.log")
    path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=path)
    plant = Plant(with_labeler=True)
    plant.supply.per_min = 0  # no filler PLC: the script puts the bottle on C2
    plant.labeler.tank.temp_c = 170.0  # warm glue at the start
    plc, labeler_plc = FillerPlc(port=_closed_port()), LabelerPlc()
    hmi = AsyncModbusTcpClient(HOST, port=config.LABELER_PORT)
    if not await hmi.connect():
        raise SystemExit(f"no connection to {HOST}:{config.LABELER_PORT}")
    await hmi.write_register(lm.MODE_REQ, MANUAL)
    stop = asyncio.Event()
    task = asyncio.create_task(loop.run(plc, plant, log, stop=stop, labeler_plc=labeler_plc))
    lab = plant.labeler
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    async def until(test: Callable[[], bool], timeout: float, what: str) -> None:
        end = plant.now + timeout
        while not test():
            if task.done():
                task.result()
            if plant.now > end:
                raise SystemExit(f"{what} not reached within {timeout:g} s")
            await asyncio.sleep(0.05)

    async def hold(button: int, test: Callable[[], bool], timeout: float, what: str) -> None:
        """Hold one button until `test` (< 10 s: the jog watchdog), then release."""
        await hmi.write_coil(button, True)
        try:
            await until(test, timeout, what)
        finally:
            await hmi.write_coil(button, False)
        print(f"      {plant.now:6.1f} s  released after: {what}")
        await asyncio.sleep(0.3)

    async def click(button: int, what: str) -> None:
        await hmi.write_coil(button, True)
        await asyncio.sleep(0.2)
        await hmi.write_coil(button, False)
        print(f"      {plant.now:6.1f} s  click: {what}")
        await asyncio.sleep(0.3)

    try:
        await asyncio.sleep(2.0)  # loop running, heartbeat alive a while
        await hmi.write_register(lm.CMD, 3)  # Reset: clears an Alm_CommLost left by the script before
        await asyncio.sleep(0.5)
        await until(lambda: plant.c2.put_at_entry(plant.factory.new()), 5, "bottle on C2")
        await hold(lm.MAN_C2, lambda: lab.sensors().bottle_at_gate, 9, "bottle at the gate (C2)")
        await hold(lm.MAN_GATE, lambda: lab.pocket is not None, 3, "bottle in the pocket (Gate)")
        await click(lm.MAN_CLAMP, "Bell")
        await until(lambda: lab.bell_down, 2, "bell down")
        await asyncio.sleep(1.0)
        check("M1", lab.bell_down, f"one click -> bell down, 1 s after the release still down: {lab.bell_down}")
        marks, turns0 = lab.web.marks, lab.turns
        await hold(lm.MAN_FEED, lambda: lab.web.marks > marks, 4, "label mark (Feed + glue)")
        label_mm, spun = lab.pocket.label_mm, lab.turns - turns0
        await hold(lm.MAN_CUT, lambda: True, 1, "knife")
        turns = lab.turns
        await hold(lm.MAN_SPIN, lambda: lab.turns >= turns + 2, 4, "two turns (Turntable)")
        check("M2", label_mm >= config.LABEL_LENGTH_MM - 1 and spun > 0.9 and lab.web.lost_mm == 0,
              f"Feed + glue: table turned {spun:.2f} turns, label on the bottle {label_mm:.0f} mm "
              f"(one label {config.LABEL_LENGTH_MM:.0f} mm), lost {lab.web.lost_mm:.0f} mm")
        await click(lm.MAN_CLAMP, "Bell")
        await until(lambda: not lab.bell_down, 2, "bell up")
        check("M3", not lab.bell_down, "second click -> bell up")
        await hold(lm.MAN_DISCHARGE, lambda: lab.pocket is None, 3, "pocket empty (Pusher)")
        await asyncio.sleep(0.5)
        check("M4", lab.labelled == 1 and plant.c3.count == 1,
              f"bottle on C3 labelled: labelled {lab.labelled}, C3 {plant.c3.count} (expected 1, 1)")
        warnings = [e.format() for e in log.filter(source=Source.SIM, warnings_only=True)
                    if "not reachable" not in e.text]  # the filler link points at a closed port on purpose
        check("M5", not warnings, f"simulator warnings: {warnings or 'none'}")
    finally:
        await hmi.write_register(lm.MODE_REQ, STOP_MODE)
        stop.set()
        await task
        labeler_plc.close()
        plc.close()
        hmi.close()
        log.close()
    print(f"\n{sum(results)}/{len(results)} checks passed (log {path})")
    return 0 if all(results) else 1


if __name__ == "__main__":
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    sys.exit(asyncio.run(main()))
