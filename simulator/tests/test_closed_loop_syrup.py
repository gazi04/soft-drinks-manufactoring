"""Syrup simulator stage 5: closed loop with the REAL PLCs - one batch through the syrup room, the filler fed by it.

The simulator (loop + plant + FillerPlc + SyrupPlc) runs exactly as `uv run simulator --headless`.
A separate client ACTS AS THE HMI (Auto, Reset, Start on each PLC; the simulator never writes
commands). Both PLCs must be idle. Checks:
    B1 BatchCount +1, and the syrup PLC went through 10 -> 20 -> 30 -> 40 -> 50 -> 60 -> 5, or -> 10 when
       the running filler already drew the buffer below BufferLow (the next batch starts at once)
    B2 dosing: level at the end of DOSE_WATER 500 L (+4/-1), at the end of DOSE_SYRUP 590 L (+2/-1)
    B3 cooling and carbonation: <= 4.5 degC when CARBONATE starts, pressure up to 3.3 bar, never 6 bar
    B4 the filler made bottles from the real Product_Available, was never without it after the first
       10 s, and the buffer was drawn but stayed above the signal's reset level
    B5 no alarm and no warning event (slow tick, foreign writer, link loss) after the first 10 s
    B6 after Stop both PLCs are back in IDLE (so the next script finds them idle)

    uv run python tests/test_closed_loop_syrup.py                # both PLCs, about 3 minutes
    uv run python tests/test_closed_loop_syrup.py --syrup-only   # the filler runtime is not available:
                                                                 # B4 is skipped (the loop copes with a dead PLC)

Release all debugger forces first; do not run another test script or the simulator at the same time.
After the script the heartbeat stops, so Alm_CommLost comes back in 3 s until the next Reset.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map as am
from simulator import address_map_syrup as sm
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.machines.syrup import SyrupSensors
from simulator.plant import Plant, SyrupWrites
from simulator.plcs import SYRUP_STEP_NAMES, FillerPlc, SyrupPlc

SYRUP_ONLY = "--syrup-only" in sys.argv
HOST = config.PLC_HOST
TIMEOUT_S = 300.0  # one batch takes about 150 s
SETTLE_S = 60.0  # after Stop: the PLC finishes its step and goes to IDLE
START_GRACE_S = 10.0  # the PLCs start with Alm_CommLost latched and no sensors: not judged in the first seconds
PLC_BUFFER_MIN = 100  # Product_Available resets below this
DOSE_WATER, DOSE_SYRUP, MIX, COOL, CARBONATE, TRANSFER, WAIT_DEMAND = 10, 20, 30, 40, 50, 60, 5
# The PLC leaves IDLE / WAIT_DEMAND within one scan when a batch is needed, so the first steps are often
# never seen: the batch is judged from DOSE_WATER to the first step after TRANSFER
EXPECTED_STEPS = [DOSE_WATER, DOSE_SYRUP, MIX, COOL, CARBONATE, TRANSFER]  # + the step after TRANSFER:
AFTER_TRANSFER = (WAIT_DEMAND, DOSE_WATER)  # buffer full enough / the filler drew it below BufferLow already


def _closed_port() -> int:
    import socket

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


async def idle_or_exit(hmi: AsyncModbusTcpClient, name: str, status_start: int, batch_dosed: int | None) -> None:
    """The PLC answers and is idle, otherwise a clear message (this script writes sensor values)."""
    result = await hmi.read_holding_registers(status_start, count=2)
    if result.isError():
        raise SystemExit(f"{name} PLC does not answer on its Modbus port: is its program uploaded and RUNNING?")
    state, step = result.registers
    if step != 0 or state not in (0,):
        raise SystemExit(f"{name} PLC: Step {step}, State {state}: not idle. Press Stop / Reset first.")
    if batch_dosed is not None and (await hmi.read_coils(batch_dosed, count=1)).bits[0]:
        raise SystemExit(f"{name} PLC: Batch_Dosed is TRUE (a batch is still in the tank). Finish or reset it first.")


async def press(hmi: AsyncModbusTcpClient, address: int, value: int, label: str) -> None:
    await hmi.write_register(address, value)
    print(f"      HMI: {label}")
    await asyncio.sleep(0.5)


class Recorder:
    """What the run looked like, sampled every tick (the plant is the truth, the PLC the reaction)."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self.steps: list[int] = []  # syrup PLC steps in the order seen
        self.step_at: list[float] = []  # loop seconds when each step began
        self.at_step_end: dict[int, dict] = {}  # step -> plant values when the NEXT step began
        self.max_pressure = 0.0
        self.min_buffer = plant.syrup.buffer_l if plant.syrup else 0.0
        self.no_product_s = 0.0  # time the filler PLC was without Product_Available after the grace time
        self.done = False

    def __call__(self, stats: loop.LoopStats) -> None:
        room = self.plant.syrup
        self.max_pressure = max(self.max_pressure, room.pressure_bar)
        self.min_buffer = min(self.min_buffer, room.buffer_l)
        if stats.seconds > START_GRACE_S and not self.plant.filler_product_available and not SYRUP_ONLY:
            self.no_product_s += 0.1
        status = stats.syrup_status
        if status is None or (self.steps and status.step == self.steps[-1]):
            return
        if self.steps:  # remember the plant at the moment a step ends
            self.at_step_end[self.steps[-1]] = dict(
                mix=room.mix_l, temp=room.temp_c, pressure=room.pressure_bar, t=stats.seconds)
        self.steps.append(status.step)
        self.step_at.append(stats.seconds)
        print(f"      {stats.seconds:6.1f} s  syrup step -> {SYRUP_STEP_NAMES.get(status.step, status.step)}"
              f"   mix {room.mix_l:5.0f} L  {room.temp_c:4.1f} degC  {room.pressure_bar:4.2f} bar  buffer {room.buffer_l:5.0f} L")

    def batch_steps(self) -> list[int]:
        """The steps from DOSE_WATER to the first one after TRANSFER."""
        if DOSE_WATER not in self.steps:
            return self.steps
        seq = self.steps[self.steps.index(DOSE_WATER):]
        if TRANSFER in seq:
            seq = seq[: seq.index(TRANSFER) + 2]
        return seq


async def main() -> None:
    claim_plant("test_closed_loop_syrup.py", HOST, config.SYRUP_PORT)
    if not SYRUP_ONLY:
        claim_plant("test_closed_loop_syrup.py", HOST, config.PLC_PORT)
    warn_if_fuxa("test_closed_loop_syrup.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    log_path = Path("logs") / time.strftime("closed-loop-syrup-%Y%m%d-%H%M%S.log")
    log_path.parent.mkdir(exist_ok=True)
    log = EventLog(max_events=100_000, file_path=log_path)

    syrup_hmi = await connect(config.SYRUP_PORT)
    filler_hmi = None if SYRUP_ONLY else await connect(config.PLC_PORT)
    await idle_or_exit(syrup_hmi, "syrup", sm.STATUS_START, sm.BATCH_DOSED)
    if filler_hmi is not None:
        await idle_or_exit(filler_hmi, "filler", am.STATUS_START, None)

    plant = Plant(with_syrup=True)
    if SYRUP_ONLY:
        plant.supply.per_min = 0  # no filler PLC: nothing takes bottles
    # --syrup-only: the loop still wants a filler link; point it at a closed port (refused at once, no
    # stalls) so the filler runtime is not touched at all
    closed = _closed_port()
    plc, syrup_plc = (FillerPlc(port=closed) if SYRUP_ONLY else FillerPlc()), SyrupPlc()
    recorder = Recorder(plant)

    async def operator() -> None:
        await asyncio.sleep(2.0)  # heartbeats running for a while -> Reset can clear Alm_CommLost
        for hmi, name in ((syrup_hmi, "syrup"), (filler_hmi, "filler")):
            if hmi is not None:
                await press(hmi, am.MODE_REQ, 1, f"{name}: Auto")
                await press(hmi, am.CMD, 3, f"{name}: Reset")
                await press(hmi, am.CMD, 1, f"{name}: Start")

    def batch_done() -> bool:
        s = recorder.steps
        return TRANSFER in s and s.index(TRANSFER) < len(s) - 1  # the first step after TRANSFER was seen

    print(f"Closed loop with {'the syrup PLC' if SYRUP_ONLY else 'both PLCs'}, one batch, log {log_path}")
    task = asyncio.create_task(operator())
    stats = await loop.run(plc, plant, log, seconds=TIMEOUT_S, until=batch_done, on_tick=recorder,
                           syrup_plc=syrup_plc)
    await task
    good_filler = (stats.good_count_end or 0) - (stats.good_count_start or 0)
    batches = (stats.batch_count_end or 0) - (stats.batch_count_start or 0)
    summary = stats.summary()
    delivered_ml = plant.filler.dispensed_ml

    # B6: Stop on both PLCs; the loop keeps running (they need their sensors) until both are back in IDLE
    await press(syrup_hmi, am.CMD, 2, "syrup: Stop")
    if filler_hmi is not None:
        await press(filler_hmi, am.CMD, 2, "filler: Stop")

    def all_idle() -> bool:
        s, f = stats_settle.syrup_status, stats_settle.status
        return s is not None and s.step == 0 and (SYRUP_ONLY or (f is not None and f.step == 0))

    stats_settle = loop.LoopStats()  # filled by the second run below
    settle = loop.run(plc, plant, log, seconds=SETTLE_S, until=lambda: all_idle(), syrup_plc=syrup_plc,
                      on_tick=lambda s: _copy_status(s, stats_settle))
    await settle
    idle = all_idle()
    # neutral sensors for the next script (the syrup PLC keeps its last values otherwise)
    neutral = SyrupWrites(SyrupSensors(0, 0, 0, 0, 0, False), e_stop_ok=True, heartbeat=True)
    if syrup_plc.connected or await syrup_plc.ensure_connected():
        await syrup_plc.write(neutral)
    syrup_plc.close()
    plc.close()
    syrup_hmi.close()
    if filler_hmi is not None:
        filler_hmi.close()
    log.close()

    # ---- the checks -----------------------------------------------------------------
    seq = recorder.batch_steps()
    names = " -> ".join(SYRUP_STEP_NAMES.get(s, str(s)) for s in seq)
    water, syrup_end = recorder.at_step_end.get(DOSE_WATER), recorder.at_step_end.get(DOSE_SYRUP)
    cool_end, carb_end = recorder.at_step_end.get(COOL), recorder.at_step_end.get(CARBONATE)
    carb_start = recorder.step_at[recorder.steps.index(CARBONATE)] if CARBONATE in recorder.steps else None
    r = [check("B1 batch", batches == 1 and seq[:-1] == EXPECTED_STEPS and seq[-1] in AFTER_TRANSFER,
               f"BatchCount +{batches}; steps {names}")]
    r.append(check("B2 dosing", water is not None and syrup_end is not None
                   and -1 <= water["mix"] - 500 <= 4 and -1 <= syrup_end["mix"] - 590 <= 2,
                   f"end of DOSE_WATER {water and round(water['mix'], 1)} L (500), end of DOSE_SYRUP "
                   f"{syrup_end and round(syrup_end['mix'], 1)} L (590)"))
    r.append(check("B3 cooling + carbonation", cool_end is not None and cool_end["temp"] <= 4.5
                   and 3.3 <= recorder.max_pressure < 6.0,
                   f"{cool_end and round(cool_end['temp'], 2)} degC when COOL ended, max pressure "
                   f"{recorder.max_pressure:.2f} bar"))
    if SYRUP_ONLY:
        print("SKIP  B4 filler fed by the real Product_Available: --syrup-only")
    else:
        r.append(check("B4 filler fed", good_filler >= 10 and recorder.no_product_s == 0 and delivered_ml > 0
                       and recorder.min_buffer >= PLC_BUFFER_MIN,
                       f"GoodCount +{good_filler}, {delivered_ml / 1000:.1f} l filled, no Product_Available lost for "
                       f"{recorder.no_product_s:.1f} s, buffer minimum {recorder.min_buffer:.0f} L"))
    warnings = [e.format() for e in log.filter(warnings_only=True) if e.time_s > START_GRACE_S
                and not (SYRUP_ONLY and f"127.0.0.1:{closed}" in e.text)]  # the closed filler port is on purpose
    active = [name for st in (stats.syrup_status, stats.status) if st for name, on in st.alarms.items() if on]
    r.append(check("B5 no alarm / warning", not warnings and not active,
                   f"{len(warnings)} warnings after {START_GRACE_S:.0f} s {warnings[:3]}, alarms at the end {active or 'none'}"))
    r.append(check("B6 back in IDLE after Stop", idle, "both PLCs Step 0" if idle else "a PLC is still running"))

    print("\nStep durations (s): " + ", ".join(
        f"{SYRUP_STEP_NAMES.get(s, s)} {t2 - t1:.0f}"
        for s, t1, t2 in zip(recorder.steps, recorder.step_at, recorder.step_at[1:])))
    print(summary)
    print(f"{sum(r)}/{len(r)} checks passed (Stop sent; the heartbeat stops now, so Alm_CommLost will latch)")
    sys.exit(0 if all(r) else 1)


def _copy_status(stats: loop.LoopStats, target: loop.LoopStats) -> None:
    target.status, target.syrup_status = stats.status, stats.syrup_status


if __name__ == "__main__":
    asyncio.run(main())
