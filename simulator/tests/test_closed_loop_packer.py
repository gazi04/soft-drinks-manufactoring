"""Packer simulator stage 6: closed loop with the REAL PLCs - bottles from the filler through the labeler into packs.

The simulator (loop + Plant(with_labeler=True, with_packer=True) + FillerPlc + LabelerPlc + PackerPlc) runs
exactly as `uv run simulator --headless`. A separate client ACTS AS THE HMI (Auto, Reset, Start on each
PLC, Stop at the end; the simulator never writes commands). All PLCs used must be idle. Checks:
    C1 packer PackCount +N, and both sequencers went through their steps (WARM_UP, COUNT,
       WAIT_WRAP, PUSH, RETRACT; WAIT_PACK, FEED, CUT, RUN_ON, WRAP, RUN_OUT; READY lasts one scan here)
    C2 the physics agrees: packs pushed = the PLC's PackCount increase, every pack has 6 bottles, a sheet
       of 800-900 mm, is wrapped and shrunk; no packer audit warning (the settle gap is C6)
    C3 the labeler labelled the bottles (labeler GoodCount >= 6 N)          [not with --packer-only]
    C4 no alarm and no warning event (slow tick, foreign writer, link loss) after the first 10 s
    C5 after Stop every PLC is back in IDLE, and the packer is EMPTY: PacksInOven, GroupCount and
       FeedCount 0 (its counters keep their values; a pack left in the oven would raise Alm_OvenJam in the
       next run). That is why the supply is exactly 6 N bottles: every bottle ends in a pack.

    C6 no PUSH before the 6th bottle lies on the table: the count sensor sits in front of the table,
       the bottle needs 0.4 s to land, Settle_Timer (SettleTime 0.5 s in WAIT_WRAP) waits for it. Without
       it the simulator warns "bottle onto the collation table with the pusher out" once per group.

    C7 the HMI animation words in the packer PLC (HR 1094-1097, read as FUXA reads them, HMI P0): the first
       and the last cell of the wrap conveyor and of the oven light up, the sheet code goes through 1 feeding,
       2 in the slot, 3 under a pack (the table count is printed only: the palletizer may take a pack in
       the tick it lands)

    uv run python tests/test_closed_loop_packer.py                # filler + labeler + packer, about 3 min
    uv run python tests/test_closed_loop_packer.py --with-syrup   # + the syrup room PLC (four PLCs)
    uv run python tests/test_closed_loop_packer.py --packer-only  # only the packer runtime: the test puts a
        labelled bottle on C3's entry every 2 s itself (test input, like a stand-in), C3 is skipped

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
from simulator import address_map_labeler as lm
from simulator import address_map_packer as pm
from simulator import address_map_syrup as sm
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.machines.packer import Pack
from simulator.plant import Plant
from simulator.plcs import PACKER_STEP_NAMES, PACKER_WRAP_STEP_NAMES, FillerPlc, LabelerPlc, PackerPlc, SyrupPlc

PACKER_ONLY = "--packer-only" in sys.argv
WITH_SYRUP = "--with-syrup" in sys.argv and not PACKER_ONLY
HOST = config.PLC_HOST
N = 2  # packs
BOTTLES = N * config.PACK_BOTTLES  # exactly: every bottle ends in a pack, the collation table is empty at the end
TIMEOUT_S = 400.0  # warm-up about 55 s + 12 bottles from the labeler (about 6 s each) + wrap + oven
SETTLE_S = 40.0  # after Stop: the wrap finishes its pack, the oven empties itself
START_GRACE_S = 10.0  # the PLCs start with Alm_CommLost latched and no sensors: not judged in the first seconds
FEED_S = 2.0  # --packer-only: one labelled bottle onto C3 every 2 s
SETTLE_GAP = "bottle onto the collation table with the pusher out"  # C6
# Steps a tick must see. WAIT_WRAP and READY are left out: the PLC passes them within ONE scan when the
# other side already waits (COUNT -> WAIT_WRAP -> PUSH, CUT -> READY -> RUN_ON in the same scan, found live),
# so the simulator, which reads the status once per tick, never sees them
COLLATION = {5, 10, 15, 20, 25}  # WARM_UP, COUNT, WAIT_WRAP (SettleTime 0.5 s), PUSH, RETRACT
WRAP = {10, 20, 22, 30, 32, 34}  # WAIT_PACK, FEED, CUT, RUN_ON, WRAP, RUN_OUT


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
    state, step, wrap_step = result.registers  # the third word is WrapStep in the packer, unused elsewhere
    if step != 0 or state != 0 or (name == "packer" and wrap_step != 0):
        raise SystemExit(f"{name} PLC: Step {step}, State {state}: not idle. Press Stop / Reset first.")


async def press(hmi: AsyncModbusTcpClient, address: int, value: int, label: str) -> None:
    await hmi.write_register(address, value)
    print(f"      HMI: {label}")
    await asyncio.sleep(0.5)


class Recorder:
    """The packer PLC's steps as seen, the packs that reach the outfeed table, and (--packer-only) the
    bottles the test puts on C3."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self.steps: set[int] = set()
        self.wrap_steps: set[int] = set()
        self.packs: dict[int, Pack] = {}
        self.fed = 0
        self._next_feed = START_GRACE_S
        self._last = (None, None)

    def __call__(self, stats: loop.LoopStats) -> None:
        packer = self.plant.packer
        if PACKER_ONLY and self.fed < BOTTLES and stats.seconds >= self._next_feed:
            # test input between two ticks, like a stand-in: a bottle the labeler would have labelled
            bottle = self.plant.factory.new()
            bottle.label_mm = config.LABEL_LENGTH_MM
            if self.plant.c3.put_at_entry(bottle):
                self.fed += 1
                self._next_feed = stats.seconds + FEED_S
        # every pack wherever it is (the palletizer takes a pack in the tick it lands on the table)
        for pack in packer.wrap_packs + packer.oven.packs + packer.outfeed.packs:
            self.packs[pack.id] = pack
        status = stats.packer_status
        if status is None or (status.step, status.wrap_step) == self._last:
            return
        self._last = (status.step, status.wrap_step)
        self.steps.add(status.step)
        self.wrap_steps.add(status.wrap_step)
        print(f"      {stats.seconds:6.1f} s  packer {PACKER_STEP_NAMES.get(status.step, status.step):<9} / "
              f"{PACKER_WRAP_STEP_NAMES.get(status.wrap_step, status.wrap_step):<9}  oven {packer.oven.temp_c:5.1f} degC"
              f"  C3 {self.plant.c3.count}  table {len(packer.table)}  pushed {packer.groups_pushed}"
              f"  in oven {len(packer.oven.packs)}  out {packer.oven.left}")


async def main() -> None:
    claim_plant("test_closed_loop_packer.py", HOST, config.PACKER_PORT)
    if not PACKER_ONLY:
        claim_plant("test_closed_loop_packer.py", HOST, config.PLC_PORT)
        claim_plant("test_closed_loop_packer.py", HOST, config.LABELER_PORT)
    if WITH_SYRUP:
        claim_plant("test_closed_loop_packer.py", HOST, config.SYRUP_PORT)
    warn_if_fuxa("test_closed_loop_packer.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    log_path = Path("logs") / time.strftime("closed-loop-packer-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=log_path)

    hmis = {"packer": await connect(config.PACKER_PORT)}
    if not PACKER_ONLY:
        hmis["filler"] = await connect(config.PLC_PORT)
        hmis["labeler"] = await connect(config.LABELER_PORT)
    if WITH_SYRUP:
        hmis["syrup"] = await connect(config.SYRUP_PORT)
    for name, hmi in hmis.items():
        start = {"packer": pm.STATUS_START, "labeler": lm.STATUS_START, "filler": am.STATUS_START,
                 "syrup": sm.STATUS_START}[name]
        await idle_or_exit(hmi, name, start)

    plant = Plant(with_syrup=WITH_SYRUP, with_labeler=True, with_packer=True)
    plant.supply.per_min = 0
    if not PACKER_ONLY:
        plant.supply.add(BOTTLES)
    # --packer-only: the loop still wants a filler and a labeler link; point them at a closed port
    closed = _closed_port()
    plc = FillerPlc(port=closed) if PACKER_ONLY else FillerPlc()
    labeler_plc = LabelerPlc(port=closed) if PACKER_ONLY else LabelerPlc()
    syrup_plc = SyrupPlc() if WITH_SYRUP else None
    packer_plc = PackerPlc()
    recorder = Recorder(plant)

    async def operator() -> None:
        await asyncio.sleep(2.0)  # heartbeats running for a while -> Reset can clear Alm_CommLost
        for name, hmi in hmis.items():  # Cmd / ModeReq are the same registers in every PLC
            await press(hmi, am.MODE_REQ, 1, f"{name}: Auto")
            await press(hmi, am.CMD, 3, f"{name}: Reset")
            await press(hmi, am.CMD, 1, f"{name}: Start")

    progress = loop.LoopStats()

    def packed_enough() -> bool:
        return progress.packer_count_start is not None and progress.packer_count_end - progress.packer_count_start >= N

    def on_tick(stats: loop.LoopStats) -> None:
        recorder(stats)
        progress.packer_count_start, progress.packer_count_end = stats.packer_count_start, stats.packer_count_end

    mode = "--packer-only" if PACKER_ONLY else ("all four PLCs" if WITH_SYRUP else "filler + labeler + packer")
    print(f"Closed loop with the packer PLC ({mode}), {N} packs = {BOTTLES} bottles, log {log_path}")
    # C7: a second client reads the animation words from the PLC while the loop runs, as FUXA does. It
    # stops with this loop run, not later: an await between two loop runs freezes the plant (K6 trap).
    anim_seen = {"wrap": 0, "oven": 0, "table": set(), "sheet": set()}
    loop_done = asyncio.Event()

    async def watch_animation() -> None:
        reader = await connect(config.PACKER_PORT)
        while not loop_done.is_set():
            result = await reader.read_holding_registers(pm.ANIM_START, count=pm.ANIM_COUNT)
            if not result.isError():
                wrap, oven, table, sheet = result.registers
                anim_seen["wrap"] |= wrap
                anim_seen["oven"] |= oven
                anim_seen["table"].add(table)
                anim_seen["sheet"].add(sheet)
            await asyncio.sleep(0.2)  # FUXA's polling interval
        reader.close()

    watcher = asyncio.create_task(watch_animation())
    task = asyncio.create_task(operator())
    stats = await loop.run(plc, plant, log, seconds=TIMEOUT_S, until=packed_enough, on_tick=on_tick,
                           syrup_plc=syrup_plc, labeler_plc=labeler_plc, packer_plc=packer_plc)
    loop_done.set()
    await task
    await watcher
    pack_count = (stats.packer_count_end or 0) - (stats.packer_count_start or 0)
    labeler_good = (stats.labeler_count_end or 0) - (stats.labeler_count_start or 0)
    summary = stats.summary()

    # C5: Stop on every PLC WHILE the loop runs (the PLCs keep scanning when the simulator pauses), then
    # run on until every PLC is idle and the packer is empty (the wrap finishes, the oven empties itself)
    settle = loop.LoopStats()

    async def stop_all() -> None:
        await asyncio.sleep(0.5)
        for name, hmi in hmis.items():
            await press(hmi, am.CMD, 2, f"{name}: Stop")

    def copy_status(s: loop.LoopStats) -> None:
        recorder(s)
        settle.status, settle.syrup_status, settle.labeler_status, settle.packer_status = \
            s.status, s.syrup_status, s.labeler_status, s.packer_status

    def all_idle() -> bool:
        statuses = [settle.packer_status]
        if not PACKER_ONLY:
            statuses += [settle.status, settle.labeler_status]
        if WITH_SYRUP:
            statuses.append(settle.syrup_status)
        p = settle.packer_status
        return all(s is not None and s.step == 0 for s in statuses) and p.wrap_step == 0 and p.packs_in_oven == 0

    calm_since: list[float] = []

    def settled() -> bool:
        packer = plant.packer
        empty = not packer.oven.packs and not packer.wrap_packs and packer.entering is None
        if not all_idle() or not empty or (plant.labeler.entering is not None):
            calm_since.clear()
            return False
        calm_since.append(plant.now)
        return plant.now - calm_since[0] >= 1.0  # one more second of sensors

    stopper = asyncio.create_task(stop_all())
    await loop.run(plc, plant, log, seconds=SETTLE_S, until=lambda: stopper.done() and settled(),
                   on_tick=copy_status, syrup_plc=syrup_plc, labeler_plc=labeler_plc, packer_plc=packer_plc)
    await stopper
    end = settle.packer_status
    for link in (plc, syrup_plc, labeler_plc, packer_plc):
        if link is not None:
            link.close()
    for hmi in hmis.values():
        hmi.close()
    log.close()

    # ---- the checks -----------------------------------------------------------------
    r = [check("C1 packer PackCount", pack_count >= N and COLLATION <= recorder.steps and WRAP <= recorder.wrap_steps,
               f"+{pack_count} (want {N}); steps seen {sorted(recorder.steps)} / wrap {sorted(recorder.wrap_steps)}")]
    packer = plant.packer
    packs = [recorder.packs[i] for i in sorted(recorder.packs)]
    bad = [p.name for p in packs if len(p.bottles) != config.PACK_BOTTLES or not p.wrapped or not p.shrunk
           or not 800 <= p.sheet_mm <= 900]
    gap = [e for e in log.filter(source=Source.SIM, warnings_only=True) if SETTLE_GAP in e.text]
    on_purpose = f"127.0.0.1:{closed}"  # --packer-only: the closed filler / labeler port is on purpose
    audit = [e.format() for e in log.filter(source=Source.SIM, warnings_only=True)
             if SETTLE_GAP not in e.text and on_purpose not in e.text
             and any(w in e.text for w in ("Pack", "film", "sheet", "oven", "collation", "pusher", "wrap", "table",
                                           "label"))]
    r.append(check("C2 physics agrees", packer.groups_pushed == pack_count == len(packs) and not bad and not audit,
                   f"pushed {packer.groups_pushed}, PLC +{pack_count}, packs seen {len(packs)} "
                   f"(sheets {[round(p.sheet_mm) for p in packs]} mm), bad {bad or 'none'}, audit {audit[:3] or 'none'}"))
    if PACKER_ONLY:
        print("SKIP  C3 labeler labelled the bottles: --packer-only")
    else:
        r.append(check("C3 labeler labelled the bottles", labeler_good >= BOTTLES, f"labeler GoodCount +{labeler_good}"))
    warnings = [e.format() for e in log.filter(warnings_only=True) if e.time_s > START_GRACE_S
                and SETTLE_GAP not in e.text and not (PACKER_ONLY and f"127.0.0.1:{closed}" in e.text)]
    active = [name for st in (stats.packer_status, stats.labeler_status, stats.status, stats.syrup_status) if st
              for name, on in st.alarms.items() if on]
    r.append(check("C4 no alarm / warning", not warnings and not active,
                   f"{len(warnings)} warnings after {START_GRACE_S:.0f} s {warnings[:3]}, alarms at the end {active or 'none'}"))
    empty = end is not None and (end.packs_in_oven, end.group_count, end.feed_count) == (0, 0, 0)
    r.append(check("C5 back in IDLE and empty after Stop", all_idle() and empty,
                   f"packer Step {end.step if end else '?'}, WrapStep {end.wrap_step if end else '?'}, "
                   f"PacksInOven / GroupCount / FeedCount {(end.packs_in_oven, end.group_count, end.feed_count) if end else '?'}"))
    r.append(check("C6 PUSH only with the group on the table", not gap,
                   f"{len(gap)} 'pusher out' warning(s)" + (f", first at {gap[0].time_s:.1f} s" if gap else "")
                   + " (Settle_Timer: expected 0)"))
    first_last = lambda bits, cells: bool(bits & 1) and bool(bits & (1 << (cells - 1)))  # noqa: E731
    sheets = {pm.SHEET_FEEDING, pm.SHEET_IN_SLOT, pm.SHEET_UNDER_PACK}
    r.append(check("C7 animation words for FUXA",
                   first_last(anim_seen["wrap"], pm.WRAP_CELLS) and first_last(anim_seen["oven"], pm.OVEN_CELLS)
                   and sheets <= anim_seen["sheet"],
                   f"wrap cells seen {anim_seen['wrap']:011b}, oven cells {anim_seen['oven']:011b} (bit 0 and bit 10 "
                   f"wanted), sheet codes {sorted(anim_seen['sheet'])} (1, 2, 3 wanted), table counts "
                   f"{sorted(anim_seen['table'])}"))
    print(summary)
    print(f"{sum(r)}/{len(r)} checks passed (Stop sent; the heartbeats stop now, so Alm_CommLost will latch)")
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
