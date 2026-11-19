"""Labeler simulator stage 5: closed loop with the REAL PLCs - bottles from the filler through the labeler onto C3.

The simulator (loop + Plant(with_labeler=True) + FillerPlc + LabelerPlc) runs exactly as
`uv run simulator --headless`. A separate client ACTS AS THE HMI (Auto, Reset, Start on each PLC,
Stop at the end; the simulator never writes commands). All PLCs used must be idle. Checks:
    K1 labeler GoodCount +N, and the labeler PLC went through WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS,
       UNCLAMP and DISCHARGE
    K2 the physics agrees: every bottle the PLC counted left the pocket with a whole label
       (discharged = labelled = the PLC's GoodCount increase), none lost, no audit warning
    K3 the filler made the bottles (filler GoodCount >= N)                    [not with --labeler-only]
    K4 no alarm and no warning event (slow tick, foreign writer, link loss) after the first 10 s
    K5 after Stop every PLC is back in IDLE (so the next script finds them idle)
    K6 the HMI animation words in the labeler PLC (HR 1094-1095, read as FUXA reads them): the pocket
       code goes through 1 bottle, 2 labelling, 3 labelled, and C3 slot bits other than 0 appear

    uv run python tests/test_closed_loop_labeler.py                 # filler + labeler, about 2 min
    uv run python tests/test_closed_loop_labeler.py --with-syrup    # + the syrup room PLC (three PLCs)
    uv run python tests/test_closed_loop_labeler.py --labeler-only  # the filler runtime is not available:
        the test puts a bottle on C2's entry every 7 s itself (test input, like a stand-in), K3 is skipped

Release all debugger forces first; do not run another test script or the simulator at the same time.
After the script the heartbeat stops, so Alm_CommLost comes back in 3 s until the next Reset.
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
from simulator import address_map_labeler as lm
from simulator import address_map_syrup as sm
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.machines.labeler import LabelerSensors
from simulator.plant import LabelerWrites, Plant
from simulator.plcs import LABELER_STEP_NAMES, FillerPlc, LabelerPlc, SyrupPlc

LABELER_ONLY = "--labeler-only" in sys.argv
WITH_SYRUP = "--with-syrup" in sys.argv and not LABELER_ONLY
HOST = config.PLC_HOST
N = 8  # bottles to label
TIMEOUT_S = 300.0  # warm-up about 50 s + 8 bottles of about 6.5 s
SETTLE_S = 30.0  # after Stop: the labeler finishes its label (no Stop in CLAMP / WRAP / PRESS)
START_GRACE_S = 10.0  # the PLCs start with Alm_CommLost latched and no sensors: not judged in the first seconds
FEED_S = 7.0  # --labeler-only: one bottle onto C2 every 7 s (the labeler needs about 6 s per bottle)
WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS, UNCLAMP, DISCHARGE = 5, 10, 20, 22, 24, 26, 40
EXPECTED = {WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS, UNCLAMP, DISCHARGE}


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
    result = await hmi.read_holding_registers(status_start, count=2)
    if result.isError():
        raise SystemExit(f"{name} PLC does not answer on its Modbus port: is its program uploaded and RUNNING?")
    state, step = result.registers
    if step != 0 or state != 0:
        raise SystemExit(f"{name} PLC: Step {step}, State {state}: not idle. Press Stop / Reset first.")


async def press(hmi: AsyncModbusTcpClient, address: int, value: int, label: str) -> None:
    await hmi.write_register(address, value)
    print(f"      HMI: {label}")
    await asyncio.sleep(0.5)


class Recorder:
    """The labeler PLC's steps as seen, and (--labeler-only) the bottles the test puts on C2."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self.steps: list[int] = []
        self.fed = 0
        self._next_feed = START_GRACE_S

    def __call__(self, stats: loop.LoopStats) -> None:
        if LABELER_ONLY and self.fed < N + 1 and stats.seconds >= self._next_feed:
            # test input between two ticks, like a stand-in: the next tick's physics moves it
            if self.plant.c2.put_at_entry(self.plant.factory.new()):
                self.fed += 1
                self._next_feed = stats.seconds + FEED_S
        status = stats.labeler_status
        if status is None or (self.steps and status.step == self.steps[-1]):
            return
        self.steps.append(status.step)
        lab = self.plant.labeler
        print(f"      {stats.seconds:6.1f} s  labeler step -> {LABELER_STEP_NAMES.get(status.step, status.step):<12}"
              f" glue {lab.tank.temp_c:5.1f} degC  C2 {self.plant.c2.count}  C3 {self.plant.c3.count}  "
              f"labelled {lab.labelled}")


async def main() -> None:
    claim_plant("test_closed_loop_labeler.py", HOST, config.LABELER_PORT)
    if not LABELER_ONLY:
        claim_plant("test_closed_loop_labeler.py", HOST, config.PLC_PORT)
    if WITH_SYRUP:
        claim_plant("test_closed_loop_labeler.py", HOST, config.SYRUP_PORT)
    warn_if_fuxa("test_closed_loop_labeler.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    log_path = Path("logs") / time.strftime("closed-loop-labeler-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=log_path)

    hmis = {"labeler": await connect(config.LABELER_PORT)}
    if not LABELER_ONLY:
        hmis["filler"] = await connect(config.PLC_PORT)
    if WITH_SYRUP:
        hmis["syrup"] = await connect(config.SYRUP_PORT)
    for name, hmi in hmis.items():
        start = {"labeler": lm.STATUS_START, "filler": am.STATUS_START, "syrup": sm.STATUS_START}[name]
        await idle_or_exit(hmi, name, start)

    plant = Plant(with_syrup=WITH_SYRUP, with_labeler=True)
    plant.supply.per_min = 0
    if not LABELER_ONLY:
        plant.supply.add(N + 2)  # a little more than needed: the last ones may still be in the filler
    # --labeler-only: the loop still wants a filler link; point it at a closed port (refused at once)
    closed = _closed_port()
    plc = FillerPlc(port=closed) if LABELER_ONLY else FillerPlc()
    syrup_plc = SyrupPlc() if WITH_SYRUP else None
    labeler_plc = LabelerPlc()
    recorder = Recorder(plant)

    async def operator() -> None:
        await asyncio.sleep(2.0)  # heartbeats running for a while -> Reset can clear Alm_CommLost
        for name, hmi in hmis.items():  # Cmd / ModeReq are the same registers in every PLC
            await press(hmi, am.MODE_REQ, 1, f"{name}: Auto")
            await press(hmi, am.CMD, 3, f"{name}: Reset")
            await press(hmi, am.CMD, 1, f"{name}: Start")

    def labelled_enough() -> bool:
        return stats_now.labeler_count_start is not None and \
            stats_now.labeler_count_end - stats_now.labeler_count_start >= N

    stats_now = loop.LoopStats()

    def on_tick(stats: loop.LoopStats) -> None:
        recorder(stats)
        stats_now.labeler_count_start, stats_now.labeler_count_end = stats.labeler_count_start, stats.labeler_count_end

    # K6: a second client reads the animation words from the PLC while the loop runs, as FUXA does
    anim_codes: set[int] = set()
    anim_c3: set[int] = set()
    loop_done = asyncio.Event()

    async def watch_animation() -> None:
        reader = await connect(config.LABELER_PORT)
        while not loop_done.is_set():
            result = await reader.read_holding_registers(lm.ANIM_START, count=lm.ANIM_COUNT)
            if not result.isError():
                anim_c3.add(result.registers[0])
                anim_codes.add(result.registers[1])
            await asyncio.sleep(0.2)  # FUXA's polling interval
        reader.close()

    watcher = asyncio.create_task(watch_animation())
    mode = "--labeler-only" if LABELER_ONLY else ("filler + syrup room + labeler" if WITH_SYRUP else "filler + labeler")
    print(f"Closed loop with the labeler PLC ({mode}), {N} bottles, log {log_path}")
    task = asyncio.create_task(operator())
    stats = await loop.run(plc, plant, log, seconds=TIMEOUT_S, until=labelled_enough, on_tick=on_tick,
                           syrup_plc=syrup_plc, labeler_plc=labeler_plc)
    await task
    labeler_good = (stats.labeler_count_end or 0) - (stats.labeler_count_start or 0)
    filler_good = (stats.good_count_end or 0) - (stats.good_count_start or 0)
    lab = plant.labeler
    discharged, labelled, lost = lab.discharged, lab.labelled, len(lab.lost)
    summary = stats.summary()

    # K5: Stop on every PLC WHILE the loop runs. The PLCs keep scanning when the simulator pauses: Stop
    # pressed in a gap between two loop runs let the labeler's gate pulse fire into a frozen plant, the
    # bottle never moved, Bottle_Entering stayed set and the PLC raised Alm_PocketJam (found live).
    settle_stats = loop.LoopStats()

    async def stop_all() -> None:
        await asyncio.sleep(0.5)
        for name, hmi in hmis.items():
            await press(hmi, am.CMD, 2, f"{name}: Stop")

    def all_idle() -> bool:
        statuses = [settle_stats.labeler_status]
        if not LABELER_ONLY:
            statuses.append(settle_stats.status)
        if WITH_SYRUP:
            statuses.append(settle_stats.syrup_status)
        return all(s is not None and s.step == 0 for s in statuses)

    def copy_status(s: loop.LoopStats) -> None:
        settle_stats.status, settle_stats.syrup_status, settle_stats.labeler_status = \
            s.status, s.syrup_status, s.labeler_status

    # Idle is not enough: Stop in WAIT_BOTTLE goes to IDLE at once, also while a bottle the gate just let
    # through is still on its way to the pocket; the loop runs on until it has arrived.
    calm_since: list[float] = []

    def settled() -> bool:
        if not all_idle() or plant.labeler.entering is not None:
            calm_since.clear()
            return False
        calm_since.append(plant.now)
        return plant.now - calm_since[0] >= 1.0  # one more second of sensors

    stopper = asyncio.create_task(stop_all())
    await loop.run(plc, plant, log, seconds=SETTLE_S, until=lambda: stopper.done() and settled(),
                   on_tick=copy_status, syrup_plc=syrup_plc, labeler_plc=labeler_plc)
    await stopper
    # the K6 reader stops only now: awaiting it between the two loop runs froze the plant while the PLC
    # scanned on, the gate pulse fired into it and the PLC raised Alm_PocketJam (found live, 2026-11-19)
    loop_done.set()
    await watcher
    idle = all_idle() and plant.labeler.entering is None
    # neutral sensors for the next script (the labeler PLC keeps its last values otherwise)
    neutral = LabelerWrites(LabelerSensors(False, False, False, False, False, False, False, False, False, False, 0),
                            e_stop_ok=True, heartbeat=True)
    if labeler_plc.connected or await labeler_plc.ensure_connected():
        await labeler_plc.write(neutral)
    for link in (plc, syrup_plc, labeler_plc):
        if link is not None:
            link.close()
    for hmi in hmis.values():
        hmi.close()
    log.close()

    # ---- the checks -----------------------------------------------------------------
    seen = " -> ".join(LABELER_STEP_NAMES.get(s, str(s)) for s in recorder.steps[:9])
    r = [check("K1 labeler GoodCount", labeler_good >= N and EXPECTED <= set(recorder.steps),
               f"+{labeler_good} (want {N}); first steps {seen}")]
    # the physics' audit warnings (source SIM), not the PLC's own alarm events
    audit = [e.format() for e in log.filter(source=Source.SIM, warnings_only=True)
             if any(word in e.text for word in ("label", "pocket", "bell", "turntable", "knife", "glue"))]
    r.append(check("K2 physics agrees", discharged == labelled == labeler_good and lost == 0 and not audit,
                   f"discharged {discharged}, labelled {labelled}, PLC +{labeler_good}, lost {lost}, "
                   f"audit warnings {audit[:3] or 'none'}"))
    if LABELER_ONLY:
        print("SKIP  K3 filler made the bottles: --labeler-only")
    else:
        r.append(check("K3 filler made the bottles", filler_good >= N, f"filler GoodCount +{filler_good}"))
    warnings = [e.format() for e in log.filter(warnings_only=True) if e.time_s > START_GRACE_S
                and not (LABELER_ONLY and f"127.0.0.1:{closed}" in e.text)]  # the closed filler port is on purpose
    active = [name for st in (stats.labeler_status, stats.status, stats.syrup_status) if st
              for name, on in st.alarms.items() if on]
    r.append(check("K4 no alarm / warning", not warnings and not active,
                   f"{len(warnings)} warnings after {START_GRACE_S:.0f} s {warnings[:3]}, alarms at the end {active or 'none'}"))
    r.append(check("K5 back in IDLE after Stop", idle,
                   "every PLC Step 0, no bottle in the gate" if idle else "a PLC is still running or a bottle in the gate"))
    codes_ok = {lm.POCKET_BOTTLE, lm.POCKET_LABELLING, lm.POCKET_LABELLED} <= anim_codes
    r.append(check("K6 animation words in the PLC", codes_ok and any(anim_c3 - {0}),
                   f"pocket codes seen {sorted(anim_codes)} (want 1, 2, 3), C3 words seen {len(anim_c3 - {0})} non-zero"))
    print(summary)
    print(f"{sum(r)}/{len(r)} checks passed (Stop sent; the heartbeat stops now, so Alm_CommLost will latch)")
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
