"""Stage 6: closed loop with the REAL filler PLC – 20 bottles through the machine.

The simulator (loop + plant + FillerPlc) runs exactly as `uv run simulator
--headless`. A separate client ACTS AS THE HMI: Auto, Reset, Start (the
simulator itself never writes commands). Checks:
    C1 GoodCount +20 (the PLC counted every S6 pulse)
    C2 every bottle left the filler with >= FillSetpoint, rinsed and capped (0 audit warnings)
    C3 no simulator warnings (no slow tick, no foreign writer, no link loss)
    C4 no bottle lost
    C5 the HMI animation words %MW70..75 in the PLC equal the plant (C1/C2 slots, pocket codes)

    uv run python tests/test_closed_loop.py          # about 3 minutes
    uv run python tests/test_closed_loop.py 5        # fewer bottles

Release all debugger forces first; do not run another test script at the same
time (the read-back check would report it).
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from simulator import address_map as am
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.plant import Plant
from simulator.plcs import FillerPlc

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
TIMEOUT_S = 30 + N * 12  # ~7 s per bottle when all stations are busy, generous margin


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


async def hmi_press(hmi: AsyncModbusTcpClient, address: int, value: int, label: str) -> None:
    await hmi.write_register(address, value)
    print(f"      HMI: {label}")
    await asyncio.sleep(0.5)


async def main() -> None:
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    log_path = Path("logs") / time.strftime("closed-loop-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=log_path)

    plant = Plant()
    plant.supply.per_min = 0
    plant.supply.add(N)
    plc = FillerPlc()
    hmi = AsyncModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT)
    if not await hmi.connect():
        raise SystemExit(f"no connection to {config.PLC_HOST}:{config.PLC_PORT}")

    def all_out() -> bool:
        return plant.labeler.taken + plant.c2.count >= N

    async def operator() -> None:
        await asyncio.sleep(2.0)  # heartbeat running for a while -> Reset can clear Alm_CommLost
        await hmi_press(hmi, am.MODE_REQ, 1, "Auto")
        await hmi_press(hmi, am.CMD, 3, "Reset")
        await hmi_press(hmi, am.CMD, 1, "Start")

    print(f"Closed loop, {N} bottles, log {log_path}")
    task = asyncio.create_task(operator())
    progress = {"last": -1}

    def on_tick(stats: loop.LoopStats) -> None:
        done = plant.labeler.taken + plant.c2.count
        if done != progress["last"]:
            progress["last"] = done
            print(f"      {stats.seconds:6.1f} s  bottles out {done}/{N}")

    stats = await loop.run(plc, plant, log, seconds=TIMEOUT_S, until=all_out, on_tick=on_tick)
    stats_tail = await loop.run(plc, plant, log, seconds=1.0)  # let the PLC count the last S6 pulse
    await task
    # C5: the loop has stopped, so its last write is the plant's final state (no tick in between)
    anim = (await hmi.read_holding_registers(am.ANIM_START, count=am.ANIM_COUNT)).registers
    await hmi_press(hmi, am.CMD, 2, "Stop")
    plc.close()
    hmi.close()
    log.close()

    good = (stats_tail.good_count_end or 0) - (stats.good_count_start or 0)
    out = [e for e in log.filter(source=Source.BOTTLE) if e.text.startswith("left filler (")]
    levels = sorted({int(e.text.split("(")[1].split()[0]) for e in out})
    bottle_warn = [e.format() for e in log.filter(source=Source.BOTTLE, warnings_only=True)]
    sim_warn = [e.format() for e in log.filter(source=Source.SIM, warnings_only=True)]
    r = [
        check("C1 GoodCount", good == N, f"+{good}, expected +{N}"),
        check("C2 audit", len(out) == N and not bottle_warn,
              f"{len(out)} bottles out, levels {levels} ml, {len(bottle_warn)} audit warnings {bottle_warn[:3]}"),
        check("C3 simulator", not sim_warn, f"{len(sim_warn)} warnings {sim_warn[:3]}"),
        check("C4 nothing lost", not plant.filler.lost, f"lost {[b.id for b in plant.filler.lost]}"),
        check("C5 animation words", anim == list(plant.writes().anim),
              f"PLC %MW70..75 {anim}, plant {list(plant.writes().anim)}"),
    ]
    # the summary covers both runs: main run + the 1 s tail that counts the last S6 pulse
    stats.good_count_end = stats_tail.good_count_end
    stats.ticks += stats_tail.ticks
    stats.seconds += stats_tail.seconds
    print(f"\n{stats.summary()}")
    print(f"{sum(r)}/{len(r)} checks passed (Stop sent; the heartbeat stops now, so Alm_CommLost will latch)")
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
