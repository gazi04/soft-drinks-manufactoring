"""Syrup room simulator stage 6: fault scenarios with the REAL syrup PLC and a plant that reacts.

Each scenario runs the simulator loop (plant + SyrupPlc, as `uv run simulator --headless --scenario NAME`)
with a fresh plant. A separate client ACTS AS THE HMI (Auto, Reset, Start); the simulator never writes those.
The filler runtime is NOT used: its link points at a closed port (refused at once). Checks use the PLC's
steps / alarms / states as the loop saw them, with their simulation times, and the plant's true values.

    S1 low_syrup           Starved (State 2) + Alm_LowSyrup, never FAULT; the refill alone restarts the batch
    S2 cooling_failure     Alm_CoolTimeout 60 +/- 5 s after COOL began, FAULT; Reset + Start resumes at MIX
    S3 over_pressure       Alm_OverPressure at 6 bar, FAULT; after repair + vent Reset + Start finishes the batch
    S4 pump_failure        Alm_DoseTimeout 60 +/- 5 s after DOSE_WATER began; resume continues the dose
    S5 level_sensor_stuck  Alm_Overflow from the MixHighHigh switch before DoseTimeout; no brim spill
    S6 buffer_full         WAIT_BUFFER (State 3 Blocked); TRANSFER by itself once product is used

    uv run python tests/test_syrup_scenarios.py                  # all, about 15 minutes
    uv run python tests/test_syrup_scenarios.py --quick          # without the two 60 s timeout scenarios
    uv run python tests/test_syrup_scenarios.py over_pressure    # some of them

`buffer_starved` needs the filler PLC running as well and is run by hand (`uv run simulator --scenario buffer_starved`).
Release all debugger forces first; no simulator or other test script at the same time. At the end the syrup PLC
is left in Stop mode.
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

from simulator import address_map as am
from simulator import address_map_syrup as sm
from simulator import config, loop
from simulator.events import EventLog
from simulator.machines.syrup import SyrupSensors
from simulator.plant import Plant, SyrupWrites
from simulator.plcs import SYRUP_STEP_NAMES, FillerPlc, SyrupPlc
from simulator.scenarios import SCENARIOS, ScenarioRunner

HOST = config.PLC_HOST
IDLE, FAULT, DOSE_WATER, MIX, COOL, CARBONATE, WAIT_BUFFER, TRANSFER, WAIT_DEMAND = 0, 900, 10, 30, 40, 50, 55, 60, 5
STARVED, BLOCKED = 2, 3
SLOW = ("cooling_failure", "pump_failure")  # 60 s timeouts: left out by --quick


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class ScenarioTimeout(Exception):
    pass


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


class Run:
    """One scenario: fresh plant + simulator loop in the background + an HMI client."""

    def __init__(self, name: str) -> None:
        self.name = name
        path = Path("logs") / time.strftime(f"syrup-scenario-{name}-%Y%m%d-%H%M%S.log")
        path.parent.mkdir(exist_ok=True)
        self.log = EventLog(max_events=100_000, file_path=path)
        self.plant = Plant(with_syrup=True)
        self.plant.supply.per_min = 0  # no filler PLC: nothing takes bottles
        self.plc = FillerPlc(port=_closed_port())
        self.syrup_plc = SyrupPlc()
        self.hmi = AsyncModbusTcpClient(HOST, port=config.SYRUP_PORT)
        self.stats: loop.LoopStats | None = None
        self.runner = ScenarioRunner(SCENARIOS[name])
        self.steps: list[int] = []  # steps in the order seen
        self.step_at: dict[int, float] = {}  # simulation time a step was FIRST entered
        self.alarm_at: dict[str, float] = {}  # simulation time an alarm was first seen ON
        self.states_seen: set[int] = set()
        self.max_pressure = 0.0
        self.pressure_at_alarm: dict[str, float] = {}
        self.batches_start: int | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        print(f"\n=== {name} (log {path})")

    # --- Simulator in the background -------------------------------------------------
    async def start(self) -> None:
        if not await self.hmi.connect():
            raise SystemExit(f"no connection to {HOST}:{config.SYRUP_PORT}")
        await self.hmi.write_register(am.MODE_REQ, 2)  # Manual: outputs off before the new plant's first tick
        await self.hmi.write_coil(sm.BATCH_DOSED, False)  # a batch left in the old PLC would be "resumed"

        def on_tick(stats: loop.LoopStats) -> None:
            self.stats = stats
            self.max_pressure = max(self.max_pressure, self.plant.syrup.pressure_bar)
            st = stats.syrup_status
            if st is None:
                return
            if self.batches_start is None:
                self.batches_start = st.batch_count
            self.states_seen.add(st.state)
            if not self.steps or self.steps[-1] != st.step:
                self.steps.append(st.step)
                self.step_at.setdefault(st.step, self.now)
                print(f"      {self.now:6.1f} s  step -> {SYRUP_STEP_NAMES.get(st.step, st.step)}"
                      f"   mix {self.plant.syrup.mix_l:5.0f} L  {self.plant.syrup.temp_c:4.1f} degC"
                      f"  {self.plant.syrup.pressure_bar:4.2f} bar")
            for alarm, on in st.alarms.items():
                if on and alarm not in self.alarm_at:
                    self.alarm_at[alarm] = self.now
                    self.pressure_at_alarm[alarm] = self.plant.syrup.pressure_bar
                    print(f"      {self.now:6.1f} s  ALARM {alarm}")

        self._task = asyncio.create_task(loop.run(
            self.plc, self.plant, self.log, stop=self._stop, on_tick=on_tick, scenario=self.runner,
            syrup_plc=self.syrup_plc))

    async def finish(self, mode_after: int = 2) -> None:
        await self.hmi.write_register(am.MODE_REQ, 2)  # Manual: every output off, sequencer frozen
        await asyncio.sleep(0.5)
        self._stop.set()
        if self._task:
            await self._task
        neutral = SyrupWrites(SyrupSensors(0, 0, 0, 0, 0, False), e_stop_ok=True, heartbeat=True)
        if self.syrup_plc.connected or await self.syrup_plc.ensure_connected():
            await self.syrup_plc.write(neutral)
        await self.hmi.write_coil(sm.BATCH_DOSED, False)
        await self.hmi.write_register(am.MODE_REQ, mode_after)
        self.syrup_plc.close()
        self.plc.close()
        self.hmi.close()
        self.log.close()

    # --- HMI side --------------------------------------------------------------------
    async def press(self, address: int, value: int, label: str) -> None:
        await self.hmi.write_register(address, value)
        print(f"      {self.now:6.1f} s  HMI: {label}")
        await asyncio.sleep(0.5)

    async def start_from_idle(self) -> None:
        await asyncio.sleep(2.0)  # heartbeat running a while, so Reset can clear Alm_CommLost
        await self.press(am.MODE_REQ, 1, "Auto (leaving Manual -> IDLE)")
        await self.press(am.CMD, 3, "Reset")
        await self.press(am.CMD, 1, "Start")

    async def reset_start(self) -> None:
        await asyncio.sleep(1.0)
        await self.press(am.CMD, 3, "Reset")
        await self.press(am.CMD, 1, "Start")

    # --- Waiting and reading -----------------------------------------------------------
    @property
    def status(self):
        return self.stats.syrup_status if self.stats else None

    @property
    def now(self) -> float:
        return self.plant.now

    @property
    def batches(self) -> int:
        st = self.status
        return 0 if st is None or self.batches_start is None else st.batch_count - self.batches_start

    async def until(self, test: Callable[[], bool], timeout: float, what: str) -> None:
        end = self.now + timeout
        while not test():
            if self._task.done():  # the loop died: show why
                self._task.result()
            if self.now > end:
                raise ScenarioTimeout(f"{what} not reached within {timeout:g} s (steps {self.steps[-6:]})")
            await asyncio.sleep(0.1)

    def alarm_on(self, name: str) -> Callable[[], bool]:
        return lambda: self.status is not None and self.status.alarms[name]

    def steps_after(self, mark: int) -> list[int]:
        return self.steps[mark:]


async def finish_batch(run: Run, timeout: float = 250.0) -> None:
    """After the fault: the operator presses Reset + Start; the batch must complete by itself."""
    await run.until(lambda: run.runner.done, 30, "scenario finished (cause removed)")
    mark = len(run.steps)
    await run.reset_start()
    done = run.batches + 1
    await run.until(lambda: run.batches >= done and run.status.step == WAIT_DEMAND, timeout, "batch completed")
    after = [step for step in run.steps_after(mark) if step != IDLE]  # Reset passes through IDLE
    run.after = after[1:] if after[:1] == [WAIT_DEMAND] and len(after) > 1 else after  # Start may pass WAIT_DEMAND first


# === The scenarios ===================================================================
async def s_low_syrup(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(run.alarm_on("Alm_LowSyrup"), 400, "Alm_LowSyrup")
    starved = run.status.state == STARVED
    no_fault = FAULT not in run.steps
    t_alarm = run.now
    await run.until(lambda: not run.status.alarms["Alm_LowSyrup"] and run.status.step == DOSE_WATER, 60,
                    "next batch after the refill, without Reset")
    return [
        check("S1 Starved", starved and no_fault,
              f"State {run.status.state if not starved else STARVED} at the alarm, no FAULT in {run.steps}"),
        check("S1 refill restarts the batch alone", run.now - t_alarm < 60 and not run.status.alarms["Alm_LowSyrup"],
              f"{run.now - t_alarm:.0f} s after the alarm the PLC dosed water again; syrup tank "
              f"{run.plant.syrup.syrup_l:.0f} L"),
    ]


async def s_cooling_failure(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(run.alarm_on("Alm_CoolTimeout"), 300, "Alm_CoolTimeout")
    took = run.alarm_at["Alm_CoolTimeout"] - run.step_at[COOL]
    temp = run.plant.syrup.temp_c
    await run.until(lambda: run.status.step == FAULT, 5, "FAULT")
    await finish_batch(run)
    return [
        check("S2 timeout", 55 <= took <= 65 and temp > 4.5, f"{took:.1f} s after COOL began, product {temp:.1f} degC"),
        check("S2 resume at MIX", run.after[:1] == [MIX] and DOSE_WATER not in run.after,
              f"steps after Reset + Start: {[SYRUP_STEP_NAMES.get(s, s) for s in run.after]}"),
        check("S2 batch completed", run.batches == 1, f"BatchCount +{run.batches}"),
    ]


async def s_over_pressure(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(run.alarm_on("Alm_OverPressure"), 300, "Alm_OverPressure")
    at_alarm = run.pressure_at_alarm["Alm_OverPressure"]
    await run.until(lambda: run.status.step == FAULT, 5, "FAULT")
    await finish_batch(run)
    return [
        check("S3 over-pressure", 6.0 <= at_alarm < config.PRESS_RELIEF_BAR and run.max_pressure >= 6.0,
              f"alarm at {at_alarm:.2f} bar, maximum {run.max_pressure:.2f} bar (PressMax 6.00, relief 8)"),
        check("S3 batch completed", run.batches == 1, f"BatchCount +{run.batches}, steps after Reset "
              f"{[SYRUP_STEP_NAMES.get(s, s) for s in run.after]}"),
    ]


async def s_pump_failure(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(run.alarm_on("Alm_DoseTimeout"), 300, "Alm_DoseTimeout")
    took = run.alarm_at["Alm_DoseTimeout"] - run.step_at[DOSE_WATER]
    level = run.plant.syrup.mix_l
    await run.until(lambda: run.status.step == FAULT, 5, "FAULT")
    await finish_batch(run)
    return [
        check("S4 timeout", 55 <= took <= 65 and level < 50,
              f"{took:.1f} s after DOSE_WATER began, mixing tank {level:.0f} L"),
        check("S4 dose continues", run.after[:1] == [DOSE_WATER] and run.batches == 1,
              f"BatchCount +{run.batches}, steps after Reset {[SYRUP_STEP_NAMES.get(s, s) for s in run.after]}"),
    ]


async def s_level_sensor_stuck(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(run.alarm_on("Alm_Overflow"), 200, "Alm_Overflow")
    took = run.alarm_at["Alm_Overflow"] - run.step_at[DOSE_WATER]
    room = run.plant.syrup
    true_level, spilled = room.mix_l, room.spilled_l
    no_dose_timeout = "Alm_DoseTimeout" not in run.alarm_at
    await run.until(lambda: run.status.step == FAULT, 5, "FAULT")
    await finish_batch(run)
    return [
        check("S5 switch before timeout", no_dose_timeout and 40 <= took < 60,
              f"Alm_Overflow {took:.1f} s after DOSE_WATER began, no Alm_DoseTimeout"),
        check("S5 pumps off in time", config.MIX_HIGH_HIGH_L <= true_level < config.MIX_TANK_L and spilled < 1,
              f"true level {true_level:.0f} L at the alarm, spilled {spilled:.1f} L"),
        check("S5 batch completed", run.batches == 1, f"BatchCount +{run.batches} after drain + Reset + Start"),
    ]


async def s_buffer_full(run: Run) -> list[bool]:
    await run.start()
    await run.start_from_idle()
    await run.until(lambda: run.status.step == WAIT_BUFFER, 300, "WAIT_BUFFER")
    blocked = run.status.state == BLOCKED
    no_alarm = not any(run.status.alarms.values())
    await run.until(lambda: run.status.step == TRANSFER, 30, "TRANSFER after product was used")
    await run.until(lambda: run.batches >= 1 and run.status.step == WAIT_DEMAND, 60, "batch completed")
    return [
        check("S6 Blocked", blocked and no_alarm, f"State {BLOCKED if blocked else run.status.state} in WAIT_BUFFER, no alarm"),
        check("S6 continues by itself", run.batches == 1, f"BatchCount +{run.batches}, no Reset or Start needed"),
    ]


SCRIPTS = {
    "low_syrup": s_low_syrup,
    "cooling_failure": s_cooling_failure,
    "over_pressure": s_over_pressure,
    "pump_failure": s_pump_failure,
    "level_sensor_stuck": s_level_sensor_stuck,
    "buffer_full": s_buffer_full,
}


async def main() -> int:
    claim_plant("test_syrup_scenarios.py", HOST, config.SYRUP_PORT)
    warn_if_fuxa("test_syrup_scenarios.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    quick = "--quick" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name in wanted:
        if name not in SCRIPTS:
            raise SystemExit(f"unknown scenario {name!r} (known: {', '.join(SCRIPTS)})")
    names = wanted or [n for n in SCRIPTS if not (quick and n in SLOW)]
    results: list[bool] = []
    for name in names:
        run = Run(name)
        try:
            results += await SCRIPTS[name](run)
        except ScenarioTimeout as error:
            results.append(check(name, False, str(error)))
        finally:
            await run.finish(mode_after=0 if name == names[-1] else 2)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
