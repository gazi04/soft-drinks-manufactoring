"""Blow moulder simulator stage 5: closed loop with the REAL PLCs - preforms through the blow moulder into the filler.

The simulator (loop + Plant(with_blowmoulder=True) + BlowmoulderPlc + FillerPlc) runs exactly as
`uv run simulator --headless --no-labeler --filler-only`-like set-up would, but with the blow moulder: syrup room
off (Product_Available held TRUE), the labeler stand-in takes the bottles off C2. A separate client ACTS AS THE HMI
(Auto, Reset, Start on each PLC, Stop at the end; the simulator never writes commands). The PLCs must be idle.
Checks:
    C1 blow moulder BottleCount +N, and both sequencers went through their steps (oven FEED, LOAD, HEAT, HOT;
       mould WAIT_PREFORM, GRIP, SWING, RELEASE, ARM_BACK, CLOSE, STRETCH, BLOW, HOLD, EXHAUST, ROD_UP, OPEN, EJECT)
    C2 the physics agrees: bottles ejected = the PLC's BottleCount increase, no bottle audit warning (every bottle
       stretched and blown, not cold, not overheated)
    C3 the lamps (the P controller on %QW0): every preform left the oven inside OvenSetpoint +- OvenBand (read from
       the PLC), and the P offset shows: the mean hand-over temperature is below the setpoint, the mean LampPower
       while the preform waits in HOT is a partial power (15-50 %, about 30 % to hold 102 degC)
    C4 the filler filled them (filler GoodCount +1 at least)                [not with --blowmoulder-only]
    C5 no alarm and no warning event (slow tick, foreign writer, link loss) after the first 10 s
    C6 after Stop both PLCs are back in IDLE and the blow moulder is EMPTY (pocket, gripper, mould, dead plate):
       Stop finishes the bottle in work and loads no new preform (rule 7); a preform may wait at the loader

    uv run python tests/test_closed_loop_blowmoulder.py                    # blow moulder + filler, about 1.5 min
    uv run python tests/test_closed_loop_blowmoulder.py --blowmoulder-only # only the blow moulder runtime: the
        test takes every bottle off C1 between two ticks (test output, like a stand-in; else C1 stands: I18)

Release all debugger forces first; do not run another test script or the simulator at the same time.
After the script the heartbeats stop, so Alm_CommLost comes back in 3 s until the next Reset.
"""

import asyncio
import logging
import socket
import sys
import time
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map as am
from simulator import address_map_blowmoulder as bm
from simulator import config, loop
from simulator.events import EventLog
from simulator.machines.blowmoulder import Preform
from simulator.plant import Plant
from simulator.plcs import BLOWMOULDER_MOULD_STEP_NAMES, BLOWMOULDER_STEP_NAMES, BlowmoulderPlc, FillerPlc

ONLY = "--blowmoulder-only" in sys.argv
HOST = config.PLC_HOST
N = 6  # bottles
TIMEOUT_S = 120.0  # first bottle ~11 s after Start, then one every ~5.8 s
SETTLE_S = 40.0  # after Stop: the bottle in work is finished and ejected
START_GRACE_S = 10.0  # the PLCs start with Alm_CommLost latched and no sensors: not judged in the first seconds
OVEN = {10, 15, 20, 25}
MOULD = {10, 20, 22, 24, 26, 30, 40, 50, 52, 60, 62, 70, 80}
HOT = 25


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


async def connect(port: int) -> AsyncModbusTcpClient:
    hmi = AsyncModbusTcpClient(HOST, port=port)
    if not await hmi.connect():
        raise SystemExit(f"no connection to {HOST}:{port}")
    return hmi


async def idle_or_exit(hmi: AsyncModbusTcpClient, name: str, status_start: int) -> None:
    """The PLC answers and is idle, otherwise a clear message (this script writes sensor values)."""
    result = await hmi.read_holding_registers(status_start, count=3)
    if result.isError():
        raise SystemExit(f"{name} PLC does not answer on its Modbus port: is its program uploaded and RUNNING?")
    state, step, second = result.registers  # the third word is MouldStep in the blow moulder, unused in the filler
    if step != 0 or state != 0 or (name == "blow moulder" and second != 0):
        raise SystemExit(f"{name} PLC: Step {step}, State {state}: not idle. Press Stop / Reset first.")


async def press(hmi: AsyncModbusTcpClient, address: int, value: int, label: str) -> None:
    await hmi.write_register(address, value)
    print(f"      HMI: {label}")
    await asyncio.sleep(0.5)


class Recorder:
    """The blow moulder PLC's steps as seen, every preform the arm took out of the oven, the LampPower while a
    preform waits in HOT, and (--blowmoulder-only) the bottles the test takes off C1."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self.steps: set[int] = set()
        self.mould_steps: set[int] = set()
        self.preforms: dict[int, Preform] = {}
        self.hot_lamp: list[int] = []
        self.drained = 0
        self._last = (None, None)

    def __call__(self, stats: loop.LoopStats) -> None:
        bmo = self.plant.blowmoulder
        if ONLY:  # test output between two ticks, like a stand-in: the filler would take them
            for i, bottle in enumerate(self.plant.c1.slots):
                if bottle is not None:
                    self.plant.c1.slots[i] = None
                    self.drained += 1
        for preform in (bmo.held, bmo.cavity, bmo.dead_plate):
            if preform is not None and preform.transfer_c is not None:
                self.preforms[preform.id] = preform
        status = stats.blowmoulder_status
        if status is None:
            return
        out = self.plant.blowmoulder_outputs
        if status.step == HOT and out is not None:
            self.hot_lamp.append(out.lamp_power)
        if (status.step, status.mould_step) == self._last:
            return
        self._last = (status.step, status.mould_step)
        self.steps.add(status.step)
        self.mould_steps.add(status.mould_step)
        print(f"      {stats.seconds:6.1f} s  blow moulder {BLOWMOULDER_STEP_NAMES.get(status.step, status.step):<5} / "
              f"{BLOWMOULDER_MOULD_STEP_NAMES.get(status.mould_step, status.mould_step):<12}  "
              f"oven {bmo.oven_temp / 10:5.1f} degC  lamps {out.lamp_power if out else '-':>3} %  "
              f"{bmo.pressure_bar:5.1f} bar  C1 {self.plant.c1.count}  ejected {bmo.ejected}")


async def main() -> None:
    claim_plant("test_closed_loop_blowmoulder.py", HOST, config.BLOWMOULDER_PORT)
    if not ONLY:
        claim_plant("test_closed_loop_blowmoulder.py", HOST, config.PLC_PORT)
    warn_if_fuxa("test_closed_loop_blowmoulder.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    log_path = Path("logs") / time.strftime("closed-loop-blowmoulder-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=log_path)

    hmis = {"blow moulder": await connect(config.BLOWMOULDER_PORT)}
    if not ONLY:
        hmis["filler"] = await connect(config.PLC_PORT)
    for name, hmi in hmis.items():
        await idle_or_exit(hmi, name, bm.STATUS_START if name == "blow moulder" else am.STATUS_START)
    sp_result = await hmis["blow moulder"].read_holding_registers(bm.OVEN_SETPOINT, count=2)
    setpoint, band = sp_result.registers  # OvenSetpoint, OvenBand (0.1 degC), as the HMI may have set them

    plant = Plant(with_blowmoulder=True)
    closed = _closed_port()
    plc = FillerPlc(port=closed) if ONLY else FillerPlc()  # --blowmoulder-only: the loop still wants a filler link
    blowmoulder_plc = BlowmoulderPlc()
    recorder = Recorder(plant)

    async def operator() -> None:
        await asyncio.sleep(2.0)  # heartbeats running for a while -> Reset can clear Alm_CommLost
        for name, hmi in hmis.items():  # Cmd / ModeReq are the same registers in every PLC
            await press(hmi, am.MODE_REQ, 1, f"{name}: Auto")
            await press(hmi, am.CMD, 3, f"{name}: Reset")
            await press(hmi, am.CMD, 1, f"{name}: Start")

    progress = loop.LoopStats()

    def blown_enough() -> bool:
        return (progress.blowmoulder_count_start is not None
                and progress.blowmoulder_count_end - progress.blowmoulder_count_start >= N)

    def on_tick(stats: loop.LoopStats) -> None:
        recorder(stats)
        progress.blowmoulder_count_start = stats.blowmoulder_count_start
        progress.blowmoulder_count_end = stats.blowmoulder_count_end

    mode = "--blowmoulder-only" if ONLY else "blow moulder + filler"
    print(f"Closed loop with the blow moulder PLC ({mode}), {N} bottles, log {log_path}")
    task = asyncio.create_task(operator())
    stats = await loop.run(plc, plant, log, seconds=TIMEOUT_S, until=blown_enough, on_tick=on_tick,
                           blowmoulder_plc=blowmoulder_plc)
    await task
    bottle_count = (stats.blowmoulder_count_end or 0) - (stats.blowmoulder_count_start or 0)
    filler_good = (stats.good_count_end or 0) - (stats.good_count_start or 0)
    summary = stats.summary()

    # C6: Stop on every PLC WHILE the loop runs (the PLCs keep scanning when the simulator pauses), then run on
    # until both are idle and the blow moulder is empty
    settle = loop.LoopStats()

    async def stop_all() -> None:
        await asyncio.sleep(0.5)
        for name, hmi in hmis.items():
            await press(hmi, am.CMD, 2, f"{name}: Stop")

    def copy_status(s: loop.LoopStats) -> None:
        recorder(s)
        settle.status, settle.blowmoulder_status = s.status, s.blowmoulder_status
        settle.blowmoulder_count_end = s.blowmoulder_count_end

    def empty() -> bool:
        b = plant.blowmoulder
        return b.pocket is None and b.held is None and b.cavity is None and b.dead_plate is None

    def all_idle() -> bool:
        b = settle.blowmoulder_status
        statuses = [b] + ([] if ONLY else [settle.status])
        return all(s is not None and s.step == 0 for s in statuses) and b.mould_step == 0

    calm_since: list[float] = []

    def settled() -> bool:
        if not all_idle() or not empty():
            calm_since.clear()
            return False
        calm_since.append(plant.now)
        return plant.now - calm_since[0] >= 1.0  # one more second of sensors

    stopper = asyncio.create_task(stop_all())
    await loop.run(plc, plant, log, seconds=SETTLE_S, until=lambda: stopper.done() and settled(),
                   on_tick=copy_status, blowmoulder_plc=blowmoulder_plc)
    await stopper
    end = settle.blowmoulder_status
    plc.close()
    blowmoulder_plc.close()
    for hmi in hmis.values():
        hmi.close()
    log.close()

    # ---- the checks -----------------------------------------------------------------
    b = plant.blowmoulder
    r = [check("C1 blow moulder BottleCount", bottle_count >= N and OVEN <= recorder.steps
               and MOULD <= recorder.mould_steps,
               f"+{bottle_count} (want {N}); oven steps seen {sorted(recorder.steps)}, mould steps "
               f"{sorted(recorder.mould_steps)}")]
    on_purpose = f"127.0.0.1:{closed}"  # --blowmoulder-only: the closed filler port is on purpose
    audit = [e.format() for e in log.filter(warnings_only=True)
             if on_purpose not in e.text and e.bottle_id is not None]
    r.append(check("C2 physics agrees", bottle_count == stats.blowmoulder_count_end - stats.blowmoulder_count_start
                   and b.ejected >= bottle_count and not audit,
                   f"ejected {b.ejected} (BottleCount +{bottle_count} at the stop, "
                   f"{(settle.blowmoulder_count_end or 0) - (stats.blowmoulder_count_start or 0)} after it), "
                   f"bottle audit {audit[:3] or 'none'}"))
    temps = [p.transfer_c for p in recorder.preforms.values()]
    low, high = (setpoint - band) / 10, (setpoint + band) / 10
    inside = bool(temps) and all(low <= t <= high for t in temps)
    mean_t = sum(temps) / len(temps) if temps else 0.0
    mean_lamp = sum(recorder.hot_lamp) / len(recorder.hot_lamp) if recorder.hot_lamp else 0.0
    r.append(check("C3 lamps: in the window, the P offset", inside and mean_t < setpoint / 10 and 15 <= mean_lamp <= 50,
                   f"{len(temps)} preforms out of the oven at {min(temps, default=0):.1f}-{max(temps, default=0):.1f} "
                   f"degC (window {low:.1f}-{high:.1f}), mean {mean_t:.1f} degC (setpoint {setpoint / 10:.1f}), "
                   f"mean LampPower in HOT {mean_lamp:.0f} % over {len(recorder.hot_lamp)} ticks"))
    if ONLY:
        print(f"SKIP  C4 filler filled them: --blowmoulder-only ({recorder.drained} bottles taken off C1)")
    else:
        r.append(check("C4 filler filled them", filler_good >= 1, f"filler GoodCount +{filler_good}"))
    warnings = [e.format() for e in log.filter(warnings_only=True) if e.time_s > START_GRACE_S
                and on_purpose not in e.text]
    active = [name for st in (stats.blowmoulder_status, stats.status) if st for name, on in st.alarms.items() if on]
    r.append(check("C5 no alarm / warning", not warnings and not active,
                   f"{len(warnings)} warnings after {START_GRACE_S:.0f} s {warnings[:3]}, alarms at the end "
                   f"{active or 'none'}"))
    r.append(check("C6 back in IDLE and empty after Stop", all_idle() and empty(),
                   f"blow moulder Step {end.step if end else '?'}, MouldStep {end.mould_step if end else '?'}; "
                   f"pocket {b.pocket is not None}, gripper {b.held is not None}, mould {b.cavity is not None}, "
                   f"dead plate {b.dead_plate is not None}; preform at the loader {b.preform_at_loader}"))
    print(summary)
    print(f"{sum(r)}/{len(r)} checks passed (Stop sent; the heartbeats stop now, so Alm_CommLost will latch)")
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
