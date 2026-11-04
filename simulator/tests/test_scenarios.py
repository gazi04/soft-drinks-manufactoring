"""Stage 7: fault scenarios with the REAL filler PLC and bottles that really move.

Each scenario runs the simulator (loop + plant + FillerPlc, as `uv run simulator
--headless`) with a fresh plant. A separate client ACTS AS THE HMI (Auto, Reset,
Start, Stop, Manual buttons); the simulator itself never writes those. Faults are
injected through the command queue, mostly by the built-in scenarios of
scenarios.py. The checks use the PLC events in the log (step / state / alarm
changes with their simulation time), the last PLC status and the plant's audit.

    uv run python tests/test_scenarios.py                    # all, ~15 min
    uv run python tests/test_scenarios.py --quick            # labeler without the 2 min alarm
    uv run python tests/test_scenarios.py index_jam e_stop   # some of them

Scenarios: gaps, no_product, index_jam, slow_fill, e_stop, heartbeat_off,
stop_during_work, manual, labeler_stopped.

Every scenario starts from IDLE: Manual before the new plant's first tick
(outputs off), then Manual -> Auto puts the PLC in IDLE (state-diagram.md
rule 8), then Reset + Start. Release all debugger forces
first; do not run the simulator or another test script at the same time.
"""

import asyncio
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from hmi_guard import warn_if_fuxa

from simulator import address_map as am
from simulator import config, loop
from simulator.commands import Command
from simulator.events import EventLog, Source
from simulator.machines.filler import CAP, FILL, RINSE
from simulator.plant import Plant
from simulator.plcs import FillerPlc
from simulator.scenarios import SCENARIOS, BLOCKED, Scenario, ScenarioRunner, Step, in_state, plc_running

IDLE, FAULT = 0, 900
MAN_BUTTONS = (am.MAN_C1, am.MAN_DOOR, am.MAN_RINSE, am.MAN_FILL, am.MAN_CAP, am.MAN_INDEX)


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class ScenarioTimeout(Exception):
    pass


class Run:
    """One scenario: plant + simulator loop in the background + an HMI client."""

    def __init__(self, name: str, bottles: int, rate: float = 0) -> None:
        self.name = name
        path = Path("logs") / time.strftime(f"scenario-{name}-%Y%m%d-%H%M%S.log")
        path.parent.mkdir(exist_ok=True)
        self.log = EventLog(max_events=100_000, file_path=path)
        self.plant = Plant()
        self.plant.supply.per_min = rate
        self.plant.supply.add(bottles)
        self.bottles = bottles
        self.plc = FillerPlc()
        self.hmi = AsyncModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT)
        self.stats: loop.LoopStats | None = None
        self.on_tick_extra: Callable[[], None] | None = None
        self.good_start = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        print(f"\n=== {name} (log {path})")

    # --- Simulator in the background -------------------------------------------------
    async def start(self, scenario: Scenario | None = None) -> None:
        if not await self.hmi.connect():
            raise SystemExit(f"no connection to {config.PLC_HOST}:{config.PLC_PORT}")
        # Manual first: the PLC still holds the LAST run's sensors (e.g. S4 TRUE) and may be
        # finishing a job; in Manual all outputs are off, so the new, empty plant does not
        # get a fill valve opened over nothing in its first tick.
        await self.press(am.MODE_REQ, 2, "Manual (outputs off before the new plant starts)")
        runner = ScenarioRunner(scenario) if scenario else None
        self.runner = runner

        def on_tick(stats: loop.LoopStats) -> None:
            self.stats = stats
            if self.on_tick_extra:
                self.on_tick_extra()

        self._task = asyncio.create_task(
            loop.run(self.plc, self.plant, self.log, stop=self._stop, on_tick=on_tick, scenario=runner)
        )

    async def finish(self) -> None:
        await self.press(am.CMD, 2, "Stop")
        for coil in MAN_BUTTONS:  # never leave a manual button pressed
            await self.hmi.write_coil(coil, False)
        self._stop.set()
        if self._task:
            await self._task
        self.plc.close()
        self.hmi.close()
        self.log.close()

    # --- HMI side --------------------------------------------------------------------
    async def press(self, address: int, value: int, label: str) -> None:
        await self.hmi.write_register(address, value)
        print(f"      {self.now:6.1f} s  HMI: {label}")
        await asyncio.sleep(0.5)

    async def button(self, coil: int, on: bool) -> None:
        await self.hmi.write_coil(coil, on)

    async def start_from_idle(self) -> None:
        await asyncio.sleep(2.0)  # heartbeat running a while, so Reset can clear Alm_CommLost
        await self.press(am.MODE_REQ, 1, "Auto (leaving Manual -> IDLE)")
        await self.press(am.CMD, 3, "Reset")
        await self.until(lambda: self.status is not None and self.status.step == IDLE, 5, "IDLE")
        self.good_start = self.status.good_count
        await self.press(am.CMD, 1, "Start")

    async def reset_start(self) -> None:
        await asyncio.sleep(1.0)
        await self.press(am.CMD, 3, "Reset")
        await self.press(am.CMD, 1, "Start")

    # --- Waiting and reading -----------------------------------------------------------
    @property
    def status(self):
        return self.stats.status if self.stats else None

    @property
    def now(self) -> float:
        return self.plant.now

    @property
    def out(self) -> int:
        return self.plant.labeler.taken + self.plant.c2.count

    @property
    def good(self) -> int:
        return (self.status.good_count if self.status else 0) - self.good_start

    async def until(self, condition: Callable[[], bool], timeout: float, what: str) -> None:
        end = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > end:
                raise ScenarioTimeout(f"timeout after {timeout:g} s waiting for {what}")
            await asyncio.sleep(0.05)

    async def all_out(self, n: int | None = None, timeout: float = 150) -> None:
        n = self.bottles if n is None else n
        # lost bottles count as "done", so a crash fails the audit instead of a timeout
        await self.until(lambda: self.out + len(self.plant.filler.lost) >= n, timeout, f"{n} bottles out")
        await asyncio.sleep(1.0)  # the PLC counts the last S6 pulse

    def plc_time(self, text: str, after: float = 0.0, last_before: float | None = None) -> float | None:
        """Time of the first PLC event containing `text` at or after `after`
        (or of the last one before `last_before`)."""
        times = [e.time_s for e in self.log.filter(source=Source.PLC) if text in e.text and e.time_s >= after]
        if last_before is not None:
            times = [t for t in times if t <= last_before]
            return times[-1] if times else None
        return times[0] if times else None

    def sim_time(self, text: str) -> float | None:
        times = [e.time_s for e in self.log.filter(source=Source.SIM) if text in e.text]
        return times[0] if times else None

    # --- Standard checks -------------------------------------------------------------
    def audit_checks(self, n: int | None = None) -> list[bool]:
        n = self.bottles if n is None else n
        left = [e for e in self.log.filter(source=Source.BOTTLE) if e.text.startswith("left filler (")]
        levels = sorted({int(e.text.split("(")[1].split()[0]) for e in left})
        warn = [e.format().strip() for e in self.log.filter(warnings_only=True) if e.source != Source.PLC]
        return [
            check("GoodCount", self.good == n, f"+{self.good}, expected +{n}"),
            check("audit", len(left) == n and not warn,
                  f"{len(left)} bottles out, levels {levels} ml, warnings {warn[:3]}"),
            check("nothing lost", not self.plant.filler.lost, f"lost {[b.id for b in self.plant.filler.lost]}"),
        ]


def near(name: str, actual: float | None, expected: float, tol: float) -> bool:
    if actual is None:
        return check(name, False, "event not found")
    return check(name, abs(actual - expected) <= tol, f"{actual:.2f} s, expected {expected:g} ± {tol:g} s")


def no_alarm(run: Run, name: str) -> bool:
    t = run.plc_time(f"{name} ON")
    return check(f"no {name}", t is None, "none" if t is None else f"ON at {t:.1f} s")


# --- Scenarios ---------------------------------------------------------------------
async def gaps(quick: bool) -> list[bool]:
    run = Run("gaps", bottles=0)  # the scenario adds 3 + 2 + 3 in groups
    await run.start(SCENARIOS["gaps"])
    try:
        await run.start_from_idle()
        await run.until(lambda: run.runner.done, 140, "all groups added")
        await run.all_out(8)
    finally:
        await run.finish()
    # each gap: after the group ran (Running), the empty filler reports Starved before the next group
    groups = [e.time_s for e in run.log.filter(source=Source.SIM) if e.text.startswith("command: add_bottles")]
    gap_ok = []
    for start, next_group in zip(groups, groups[1:]):
        running = run.plc_time("-> Running", after=start)
        starved = run.plc_time("-> Starved", after=running) if running is not None else None
        gap_ok.append(starved is not None and starved < next_group)
    return [
        check("Starved in both gaps", len(gap_ok) == 2 and all(gap_ok), f"groups at {groups}, gaps {gap_ok}"),
        *[no_alarm(run, a) for a in ("Alm_IndexJam", "Alm_FillTimeout")],
        *run.audit_checks(8),
    ]


async def no_product(quick: bool) -> list[bool]:
    run = Run("no_product", bottles=6)
    await run.start(SCENARIOS["no_product"])
    try:
        await run.start_from_idle()
        await run.until(lambda: run.runner.done, 90, "product back")
        await run.all_out()
    finally:
        await run.finish()
    t_off = run.sim_time("command: product_available = False")
    t_on = run.sim_time("command: product_available = True")
    starved = run.plc_time("-> Starved", after=t_off or 0)
    return [
        check("Starved while no product", starved is not None and t_on is not None and starved < t_on,
              f"off {t_off}, Starved {starved}, on {t_on}"),
        no_alarm(run, "Alm_FillTimeout"),
        *run.audit_checks(),  # includes "no spill" and "topped up to >= 500 ml"
    ]


async def index_jam(quick: bool) -> list[bool]:
    run = Run("index_jam", bottles=6)
    await run.start(SCENARIOS["index_jam"])
    try:
        await run.start_from_idle()
        await run.until(lambda: run.runner.done, 60, "jam injected and cleared")
        await run.reset_start()
        await run.all_out()
    finally:
        await run.finish()
    t_alarm = run.plc_time("Alm_IndexJam ON")
    t_index = run.plc_time("-> INDEX", last_before=t_alarm) if t_alarm else None
    return [
        near("Alm_IndexJam after INDEX start", t_alarm - t_index if t_alarm and t_index else None, 5.0, 0.5),
        check("FAULT", run.plc_time("-> FAULT") is not None, "step FAULT seen"),
        *run.audit_checks(),
    ]


async def slow_fill(quick: bool) -> list[bool]:
    run = Run("slow_fill", bottles=4)
    await run.start(SCENARIOS["slow_fill"])
    try:
        await run.start_from_idle()
        await run.until(lambda: run.runner.done, 90, "fill timeout and flow back")
        await run.reset_start()
        await run.all_out()
    finally:
        await run.finish()
    t_alarm = run.plc_time("Alm_FillTimeout ON")
    t_work = run.plc_time("-> WORK", last_before=t_alarm) if t_alarm else None
    return [
        near("Alm_FillTimeout after WORK start", t_alarm - t_work if t_alarm and t_work else None, 10.0, 1.0),
        check("FAULT", run.plc_time("-> FAULT") is not None, "step FAULT seen"),
        *run.audit_checks(),
    ]


async def frozen_checks(run: Run, active: Callable[[], bool], what: str) -> list[bool]:
    """While `active()`: belt, fill level and C1 stand still (all outputs off)."""
    await run.until(active, 60, what)
    await asyncio.sleep(0.5)  # PLC reaction + belt overrun
    f = run.plant.filler
    before = (f.shifts, f.sensors().fill_level, run.plant.c1.count, run.out)
    await asyncio.sleep(1.5)
    after = (f.shifts, f.sensors().fill_level, run.plant.c1.count, run.out)
    step = run.status.step if run.status else None
    return [
        check(f"{what}: nothing moves", before == after, f"(shifts, ml, on C1, out) {before} -> {after}"),
        check(f"{what}: step", step in (IDLE, FAULT), f"step {step}"),
    ]


async def e_stop(quick: bool) -> list[bool]:
    run = Run("e_stop", bottles=6)
    await run.start(SCENARIOS["e_stop"])
    try:
        await run.start_from_idle()
        r = await frozen_checks(run, lambda: not run.plant.e_stop_ok, "E-stop")
        await run.until(lambda: run.runner.done, 10, "E-stop released")
        await asyncio.sleep(1.0)
        await run.press(am.CMD, 1, "Start (no Reset needed after an E-stop)")
        await run.all_out()
    finally:
        await run.finish()
    return [*r, no_alarm(run, "Alm_FillTimeout"), *run.audit_checks()]


async def heartbeat_off(quick: bool) -> list[bool]:
    run = Run("heartbeat_off", bottles=6)
    toggles: list[float] = []
    last = {"v": None}

    def watch_heartbeat() -> None:
        v = run.plant.heartbeat.value
        if v != last["v"]:
            last["v"] = v
            toggles.append(run.now)

    run.on_tick_extra = watch_heartbeat
    await run.start(SCENARIOS["heartbeat_off"])
    try:
        await run.start_from_idle()
        r = await frozen_checks(run, lambda: run.status is not None and run.status.alm_comm_lost, "CommLost")
        await run.until(lambda: run.runner.done, 15, "heartbeat back")
        await run.reset_start()
        await run.all_out()
    finally:
        await run.finish()
    t_off = run.sim_time("command: heartbeat = False")
    t_alarm = run.plc_time("Alm_CommLost ON")
    last_toggle = max((t for t in toggles if t_off is not None and t <= t_off), default=None)
    gap = t_alarm - last_toggle if t_alarm is not None and last_toggle is not None else None
    return [near("Alm_CommLost after the last toggle", gap, 3.0, 0.5), *r, *run.audit_checks()]


async def stop_during_work(quick: bool) -> list[bool]:
    run = Run("stop_during_work", bottles=6)
    await run.start()
    try:
        await run.start_from_idle()
        # all three stations busy, the bottle at S4 half filled
        await run.until(lambda: run.plant.filler.sensors().fill_level >= 200
                        and all(run.plant.filler.pockets[p] for p in (RINSE, FILL, CAP)), 60, "stations busy")
        t_stop = run.now
        await run.press(am.CMD, 2, "Stop during WORK")
        await run.until(lambda: run.status.step == IDLE, 15, "IDLE after Stop")
        await asyncio.sleep(0.5)
        p = run.plant.filler.pockets
        r = [
            check("safe point: no INDEX after Stop", run.plc_time("-> INDEX", after=t_stop) is None,
                  "jobs finished, belt not moved"),
            check("IDLE with bottles inside", sum(b is not None for b in p) >= 3, f"pockets {[b and b.id for b in p]}"),
            check("jobs finished", p[RINSE].rinsed and round(p[FILL].level_ml) >= 500 and p[CAP].capped,
                  f"rinsed {p[RINSE].rinsed}, {round(p[FILL].level_ml)} ml, capped {p[CAP].capped}"),
        ]
        await run.press(am.CMD, 1, "Start again")
        await run.all_out()
    finally:
        await run.finish()
    return [*r, *run.audit_checks()]


async def manual(quick: bool) -> list[bool]:
    run = Run("manual", bottles=1)
    f = run.plant.filler
    r: list[bool] = []

    async def hold(coil: int, condition: Callable[[], bool], timeout: float, what: str) -> None:
        await run.button(coil, True)
        try:
            await run.until(condition, timeout, what)
        finally:
            await run.button(coil, False)
        await asyncio.sleep(0.3)  # PLC + one tick: output off, belt stopped

    async def jog_index() -> None:
        shifts = f.shifts
        await hold(am.MAN_INDEX, lambda: f.shifts > shifts, 4, "one index pitch")

    await run.start()
    try:
        await asyncio.sleep(2.0)  # start() already switched to Manual
        await hold(am.MAN_C1, lambda: f.sensors().s2, 8, "bottle at the door (S2)")
        await hold(am.MAN_DOOR, lambda: f.pockets[0] is not None, 3, "bottle through the door")
        await jog_index()
        await jog_index()
        r.append(check("Man_C1 / Man_Door / Man_Index bring the bottle to S4", f.sensors().s4, f"pockets {f.pockets}"))

        await run.button(am.MAN_FILL, True)
        await asyncio.sleep(1.0)
        level = f.sensors().fill_level
        shifts = f.shifts
        await run.button(am.MAN_INDEX, True)  # interlock: the belt must not move while the valve is open
        await asyncio.sleep(2.0)
        await run.button(am.MAN_INDEX, False)
        await run.button(am.MAN_FILL, False)
        await asyncio.sleep(0.3)
        r.append(check("Man_Fill fills the bottle at S4", level > 0, f"{level} ml after 1 s"))
        r.append(check("Man_Index blocked while the fill valve is open", f.shifts == shifts,
                       f"shifts {shifts} -> {f.shifts}"))

        await jog_index()  # bottle moves on to the cap station: S4 empty
        spills = len([e for e in run.log.filter(source=Source.SIM) if "spill" in e.text])
        await run.button(am.MAN_FILL, True)
        await asyncio.sleep(1.0)
        await run.button(am.MAN_FILL, False)
        await asyncio.sleep(0.3)
        spills_after = len([e for e in run.log.filter(source=Source.SIM) if "spill" in e.text])
        r.append(check("Man_Fill without a bottle at S4: no spill", not f.sensors().s4 and spills_after == spills,
                       f"S4 {f.sensors().s4}, spill events {spills} -> {spills_after}"))

        await run.press(am.MODE_REQ, 1, "Auto")
        await run.until(lambda: run.status.step == IDLE, 3, "IDLE")
        r.append(check("Manual -> Auto gives IDLE", run.status.step == IDLE, f"step {run.status.step}"))
    finally:
        await run.finish()
    return r


async def labeler_stopped(quick: bool) -> list[bool]:
    run = Run("labeler_stopped", bottles=20)
    scenario = SCENARIOS["labeler_stopped"]
    if quick:  # no 2 min alarm: labeler back 10 s after Blocked
        scenario = Scenario("labeler_stopped_quick", "", (
            Step(plc_running()),
            Step(10, Command("labeler_running", False)),
            Step(in_state(BLOCKED)),
            Step(10, Command("labeler_running", True)),
        ))
    await run.start(scenario)
    try:
        await run.start_from_idle()
        await run.until(lambda: run.runner.done, 100 if quick else 230, "labeler running again")
        good_on = run.good
        await run.until(lambda: run.good >= good_on + 3, 60, "3 more bottles after the labeler restarts")
    finally:
        await run.finish()
    t_alarm = run.plc_time("Alm_OutfeedBlocked ON")
    t_blocked = run.plc_time("-> Blocked", last_before=t_alarm) if t_alarm else run.plc_time("-> Blocked")
    left = [e for e in run.log.filter(source=Source.BOTTLE) if e.text.startswith("left filler (")]
    warn = [e.format().strip() for e in run.log.filter(warnings_only=True) if e.source != Source.PLC]
    r = [
        check("Blocked", t_blocked is not None, f"Blocked at {t_blocked}"),
        check("no crash, nothing lost", not run.plant.filler.lost and not warn, f"warnings {warn[:3]}"),
        check("continues after the labeler restarts", True, f"GoodCount +{run.good}, {len(left)} bottles out"),
        no_alarm(run, "Alm_IndexJam"),
    ]
    if not quick:
        r.append(near("Alm_OutfeedBlocked after Blocked", t_alarm - t_blocked if t_alarm and t_blocked else None,
                      120.0, 2.0))
    return r


ALL = {f.__name__: f for f in (gaps, no_product, index_jam, slow_fill, e_stop, heartbeat_off,
                                stop_during_work, manual, labeler_stopped)}


async def main() -> None:
    warn_if_fuxa("test_scenarios.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    quick = "--quick" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or list(ALL)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        raise SystemExit(f"unknown: {unknown} (known: {', '.join(ALL)})")
    results: dict[str, list[bool]] = {}
    for name in names:
        try:
            results[name] = await ALL[name](quick)
        except ScenarioTimeout as error:
            results[name] = [check(name, False, str(error))]
    print("\n=== Summary")
    for name, r in results.items():
        print(f"{'PASS' if all(r) else 'FAIL'}  {name}: {sum(r)}/{len(r)}")
    sys.exit(0 if all(all(r) for r in results.values()) else 1)


if __name__ == "__main__":
    asyncio.run(main())
