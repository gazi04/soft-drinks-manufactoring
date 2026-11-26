"""Blow moulder simulator stage 6: fault scenarios with the REAL blow moulder PLC and a plant that reacts.

Each scenario runs the simulator loop (Plant(with_blowmoulder=True) + BlowmoulderPlc, as
`uv run simulator --headless --scenario NAME`) with a fresh plant. A separate client ACTS AS THE HMI (Auto,
Reset, Start); the simulator never writes those. The filler runtime is NOT used: its link points at a closed
port, and the test plays the filler's C1 (it runs the belt and takes every bottle at the exit, except while
the filler's door is blocked: test output, like a stand-in). Checks use the PLC's steps / alarms / states as the
loop saw them, with their simulation times, and the plant's true values.

    B1  hopper_empty       Alm_NoPreforms, Starved (State 2) once the mould waits too, no FAULT; the refill alone
                           restarts it
    B2  preform_stuck      LOAD without Preform_In_Oven -> Alm_HandlingJam 3 s after LOAD began, FAULT; a bottle after
                           Reset + Start
    B3  lamps_failed       the preform stays cold -> Alm_OvenHeatTimeout 15 s after HEAT began, FAULT; a bottle after
    B4  lamps_stuck_on     OvenTemp 130 degC -> Alm_OvenOverTemp, FAULT, the PLC's LampPower 0 (no effect: stuck);
                           Reset after cooling, a bottle (the overheated one is flagged: expected)
    B5  arm_stuck          SWING without Arm_At_Mould -> Alm_HandlingJam 3 s after SWING, the preform still in the oven
    B6  preform_dropped    RELEASE without Bottle_In_Mould -> Alm_HandlingJam 3 s after RELEASE, one preform dropped
    B7  mould_stuck        CLOSE without Mould_Closed -> Alm_MouldFault 3 s after CLOSE; restart ejects the unblown
                           preform (flagged: expected), then a good bottle
    B8  air_low            20 bar -> Alm_BlowFault 2 s after BLOW, FAULT: exhaust open and the mould HELD closed on
                           every FAULT tick; restart at EXHAUST, the half-blown bottle (flagged: expected), then a good one
    B9  exhaust_blocked    Alm_ExhaustFault 3 s after EXHAUST, the mould held closed, Reset REFUSED while the pressure
                           is in the bottle; after the repair Reset + Start, a bottle
    B10 regulator_failed   45 bar -> Alm_OverPressure at 40.0 bar, high blow off on every FAULT tick, a bottle after
    B11 sensor_wire_break  raw -2000 -> Alm_PressureSensor at once, the mould held closed, Reset refused while the
                           wire is cut; a bottle after the repair
    B12 ejector_stuck      EJECT without the bottle leaving -> Alm_HandlingJam 3 s after EJECT, a bottle after
    B13 c1_backed_up       filler door blocked: C1 backs up, Blocked (State 3) with a bottle in the open mould,
                           Alm_OutfeedBlocked 2 min later, no FAULT; continues by itself
    Every scenario: never "mould opened under pressure", no bottle warning other than the expected ones.

    uv run python tests/test_blowmoulder_scenarios.py              # all, about 10 minutes
    uv run python tests/test_blowmoulder_scenarios.py --quick      # without c1_backed_up (the 2 min warning)
    uv run python tests/test_blowmoulder_scenarios.py air_low      # some of them

Release all debugger forces first; no simulator or other test script at the same time. At the end the
blow moulder PLC is left in Stop mode.
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

from simulator import address_map_blowmoulder as bm
from simulator import config, loop
from simulator.events import EventLog
from simulator.machines.blowmoulder import BlowmoulderOutputs
from simulator.plant import Plant
from simulator.plcs import BLOWMOULDER_MOULD_STEP_NAMES, BLOWMOULDER_STEP_NAMES, BlowmoulderPlc, FillerPlc
from simulator.scenarios import SCENARIOS, ScenarioRunner

HOST = config.PLC_HOST
FEED, LOAD, HEAT, HOT, FAULT = 10, 15, 20, 25, 900
WAIT_PREFORM, GRIP, SWING, RELEASE, CLOSE, BLOW, HOLD, EXHAUST, EJECT = 10, 20, 22, 24, 30, 50, 52, 60, 80
STARVED, BLOCKED = 2, 3
SLOW = ("c1_backed_up",)  # the 2 min warning: left out by --quick
MANUAL, AUTO, STOP_MODE = 2, 1, 0
START, RESET = 1, 3
NEVER = "mould opened under"  # the one warning no scenario may ever give


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
        path = Path("logs") / time.strftime(f"blowmoulder-scenario-{name}-%Y%m%d-%H%M%S.log")
        path.parent.mkdir(exist_ok=True)
        self.log = EventLog(max_events=100_000, file_path=path)
        self.plant = Plant(with_blowmoulder=True)
        self.plc = FillerPlc(port=_closed_port())  # no filler PLC: the test plays C1
        self.bm_plc = BlowmoulderPlc()
        self.hmi = AsyncModbusTcpClient(HOST, port=config.BLOWMOULDER_PORT)
        self.stats: loop.LoopStats | None = None
        self.runner = ScenarioRunner(SCENARIOS[name])
        self.step_at: list[tuple[int, float]] = []  # (oven step, time entered)
        self.mould_at: list[tuple[int, float]] = []  # (mould step, time entered)
        self.alarm_at: dict[str, float] = {}  # simulation time an alarm was first seen ON
        self.at_alarm: dict[str, tuple[float, float]] = {}  # alarm -> (OvenTemp degC, pressure bar) then
        self.states_seen: set[int] = set()
        self.fault_outputs: list[BlowmoulderOutputs] = []  # the PLC's outputs while in FAULT
        self.taken = 0  # bottles the test took off C1
        self.max_bar = 0.0  # highest cavity pressure seen
        self._was_fault = False
        self.over_temp_at: float | None = None  # first time OvenTemp written >= 130.0 degC (OvenMax)
        self.started_at: float | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        print(f"\n=== {name} (log {path})")

    # --- Simulator in the background -------------------------------------------------
    async def start(self) -> None:
        if not await self.hmi.connect():
            raise SystemExit(f"no connection to {HOST}:{config.BLOWMOULDER_PORT}")
        await self.hmi.write_register(bm.MODE_REQ, MANUAL)  # outputs off before the new plant's first tick

        def on_tick(stats: loop.LoopStats) -> None:
            self.stats = stats
            plant = self.plant
            # the filler's side of C1, played by the test: the belt runs, the door takes the exit bottle
            plant.c1.step(True, config.TICK_S)
            if not plant.filler.door_blocked and plant.c1.take_at_exit() is not None:
                self.taken += 1
            b = plant.blowmoulder
            self.max_bar = max(self.max_bar, b.pressure_bar)
            if self.over_temp_at is None and b.oven_temp >= 1300:
                self.over_temp_at = self.now
            st = stats.blowmoulder_status
            if st is None:
                return
            self.states_seen.add(st.state)
            # the outputs of a tick are read at its START: on the first tick that shows FAULT they are still the
            # outputs from before it, so they are judged from the second FAULT tick on
            if st.step == FAULT and self._was_fault and plant.blowmoulder_outputs is not None:
                self.fault_outputs.append(plant.blowmoulder_outputs)
            self._was_fault = st.step == FAULT
            changed = False
            if not self.step_at or self.step_at[-1][0] != st.step:
                self.step_at.append((st.step, self.now))
                changed = True
            if not self.mould_at or self.mould_at[-1][0] != st.mould_step:
                self.mould_at.append((st.mould_step, self.now))
                changed = True
            b = plant.blowmoulder
            if changed:
                print(f"      {self.now:6.1f} s  {BLOWMOULDER_STEP_NAMES.get(st.step, st.step):<5} / "
                      f"{BLOWMOULDER_MOULD_STEP_NAMES.get(st.mould_step, st.mould_step):<12} oven {b.oven_temp / 10:5.1f} "
                      f"degC  {b.pressure_bar:5.1f} bar  C1 {plant.c1.count}  ejected {b.ejected}  state {st.state}")
            if self.started_at is None:
                return  # an alarm left over from the run before is not this scenario's
            for alarm, on in st.alarms.items():
                if on and alarm not in self.alarm_at and alarm != "Alm_CommLost":
                    self.alarm_at[alarm] = self.now
                    self.at_alarm[alarm] = (b.oven_temp / 10, b.pressure_bar)
                    print(f"      {self.now:6.1f} s  ALARM {alarm}")

        self._task = asyncio.create_task(loop.run(
            self.plc, self.plant, self.log, stop=self._stop, on_tick=on_tick, scenario=self.runner,
            blowmoulder_plc=self.bm_plc))
        await self.start_from_idle()

    async def finish(self, mode_after: int = MANUAL) -> None:
        await self.hmi.write_register(bm.MODE_REQ, MANUAL)  # both sequencers frozen
        await asyncio.sleep(1.0)
        self._stop.set()
        if self._task:
            await self._task
        await self.hmi.write_register(bm.MODE_REQ, mode_after)
        self.bm_plc.close()
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
        await self.press(bm.MODE_REQ, AUTO, "Auto (leaving Manual -> IDLE)")
        await self.press(bm.CMD, RESET, "Reset")
        self.started_at = self.now
        await self.press(bm.CMD, START, "Start")

    async def reset_until_out_of_fault(self, timeout: float = 60.0) -> None:
        """Reset is accepted only when the cause is gone: press it until FAULT is left."""
        end = self.now + timeout
        while self.status is None or self.status.step == FAULT:
            if self.now > end:
                raise ScenarioTimeout(f"FAULT not left within {timeout:g} s of Reset")
            await self.press(bm.CMD, RESET, "Reset")
            await asyncio.sleep(1.5)

    async def reset_start(self) -> None:
        await self.until(lambda: self.runner.done, 30, "scenario finished (cause removed)")
        await asyncio.sleep(1.0)
        await self.reset_until_out_of_fault()
        await self.press(bm.CMD, START, "Start")

    async def reset_refused(self) -> bool:
        """Reset pressed while the cause is still there: the PLC must stay in FAULT."""
        await self.press(bm.CMD, RESET, "Reset (the cause is still there)")
        await asyncio.sleep(1.0)
        return self.status is not None and self.status.step == FAULT

    # --- Waiting and reading -----------------------------------------------------------
    @property
    def status(self):
        return self.stats.blowmoulder_status if self.stats else None

    @property
    def now(self) -> float:
        return self.plant.now

    @property
    def ejected(self) -> int:
        return self.plant.blowmoulder.ejected

    async def until(self, test: Callable[[], bool], timeout: float, what: str) -> None:
        end = self.now + timeout
        while not test():
            if self._task.done():  # the loop died: show why
                self._task.result()
            if self.now > end:
                raise ScenarioTimeout(f"{what} not reached within {timeout:g} s "
                                      f"(steps {self.step_at[-3:]}, mould {self.mould_at[-4:]})")
            await asyncio.sleep(0.1)

    def alarm_on(self, name: str) -> Callable[[], bool]:
        return lambda: self.status is not None and self.status.alarms[name]

    @staticmethod
    def _entered(seen: list[tuple[int, float]], step: int, before: float | None) -> float | None:
        times = [t for s, t in seen if s == step and (before is None or t <= before)]
        return times[-1] if times else None

    def entered(self, step: int, before: float | None = None) -> float | None:
        return self._entered(self.step_at, step, before)

    def mould_entered(self, step: int, before: float | None = None) -> float | None:
        return self._entered(self.mould_at, step, before)

    def faulted(self) -> bool:
        return any(s == FAULT for s, _ in self.step_at)

    def command_at(self, name: str) -> float | None:
        times = [e.time_s for e in self.log.recent(100_000) if e.text.startswith(f"command: {name} = ")]
        return times[0] if times else None

    def since(self, name: str, start: float | None) -> float:
        return self.alarm_at[name] - start if start is not None and name in self.alarm_at else -1.0

    def unexpected(self, *expected: str) -> list[str]:
        """Warnings of the physics other than the expected ones (and the link to the closed filler port)."""
        return [e.format() for e in self.log.filter(warnings_only=True)
                if e.source.value != "plc" and "PLC 127.0.0.1" not in e.text and "PLC link" not in e.text
                and (NEVER in e.text or not any(x in e.text for x in expected))]

    async def a_bottle(self, more: int = 1, timeout: float = 40.0) -> bool:
        target = self.ejected + more
        try:
            await self.until(lambda: self.ejected >= target, timeout, f"{more} more bottle(s)")
            return True
        except ScenarioTimeout as error:
            print(f"      ({error})")
            return False


# === The scenarios ===================================================================
async def jam(run: Run, label: str, alarm: str, step_at: Callable[[float], float | None], limit: tuple[float, float],
              detail: Callable[[], tuple[bool, str]] = lambda: (True, ""), expected: tuple[str, ...] = (),
              more: int = 1) -> list[bool]:
    """The common shape: wait for the alarm, time it from the step entry, FAULT; Reset + Start, `more` bottles."""
    await run.start()
    await run.until(run.alarm_on(alarm), 90, alarm)
    t = run.since(alarm, step_at(run.alarm_at[alarm]))
    await asyncio.sleep(1.0)  # some FAULT ticks for the checks of the outputs in FAULT
    extra_ok, extra = detail()
    r = [check(f"{label}a -> {alarm}", limit[0] <= t <= limit[1] and run.faulted() and extra_ok,
               f"{t:.1f} s after the step began (want {limit[0]:g}-{limit[1]:g} s), FAULT {run.faulted()}{extra}")]
    await run.reset_start()
    ok = await run.a_bottle(more)
    bad = run.unexpected(*expected)
    r.append(check(f"{label}b Reset + Start, production again", ok and not bad,
                   f"ejected {run.ejected}, unexpected warnings {bad[:3] or 'none'}"))
    return r


async def t_hopper_empty(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_NoPreforms"), 60, "Alm_NoPreforms")
    await run.until(lambda: STARVED in run.states_seen, 30, "Starved")
    r = [check("B1a hopper empty -> Alm_NoPreforms, Starved, no FAULT", not run.faulted(),
               f"hopper {run.plant.blowmoulder.hopper.count}, states {sorted(run.states_seen)}, ejected {run.ejected}")]
    ok = await run.a_bottle()
    r.append(check("B1b the refill alone restarts it", ok and not run.faulted() and not run.unexpected(),
                   f"ejected {run.ejected}, unexpected {run.unexpected()[:2] or 'none'}"))
    return r


async def t_preform_stuck(run: Run) -> list[bool]:
    return await jam(run, "B2", "Alm_HandlingJam", lambda before: run.entered(LOAD, before), (2.8, 4.0))


async def t_lamps_failed(run: Run) -> list[bool]:
    def cold() -> tuple[bool, str]:
        temp = run.at_alarm["Alm_OvenHeatTimeout"][0]
        return temp < 30.0, f", OvenTemp {temp:.1f} degC at the alarm"
    return await jam(run, "B3", "Alm_OvenHeatTimeout", lambda before: run.entered(HEAT, before), (14.8, 16.0), cold)


async def t_lamps_stuck_on(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_OvenOverTemp"), 60, "Alm_OvenOverTemp")
    # The stuck lamps heat ~20 degC/s near 130 degC: the temperature seen when the alarm is READ is a few degrees
    # higher. What counts is the reaction: the alarm within 0.3 s (2-3 ticks) of OvenTemp first reaching OvenMax.
    reaction = run.alarm_at["Alm_OvenOverTemp"] - run.over_temp_at if run.over_temp_at is not None else -1.0
    await asyncio.sleep(1.0)
    lamps = sorted({o.lamp_power for o in run.fault_outputs})
    r = [check("B4a stuck lamps -> Alm_OvenOverTemp, the PLC's LampPower 0",
               0.0 <= reaction <= 0.3 and run.faulted() and lamps == [0],
               f"alarm {reaction:.1f} s after OvenTemp first reached 130.0 degC (OvenMax), "
               f"{run.at_alarm['Alm_OvenOverTemp'][0]:.1f} degC when read, LampPower in FAULT {lamps}")]
    await run.reset_start()
    ok = await run.a_bottle(2)  # rule 9 ejects what was in the mould, then a new one
    bad = run.unexpected("overheated", "unblown", "dropped")
    r.append(check("B4b Reset after cooling, production again", ok and not bad,
                   f"ejected {run.ejected}, unexpected {bad[:3] or 'none'}"))
    return r


async def t_arm_stuck(run: Run) -> list[bool]:
    def in_oven() -> tuple[bool, str]:
        return run.plant.blowmoulder.preform_in_oven, f", preform in the oven {run.plant.blowmoulder.preform_in_oven}"
    return await jam(run, "B5", "Alm_HandlingJam", lambda before: run.mould_entered(SWING, before), (2.8, 4.0), in_oven)


async def t_preform_dropped(run: Run) -> list[bool]:
    def dropped() -> tuple[bool, str]:
        n = len(run.plant.blowmoulder.dropped)
        return n == 1, f", dropped {n}"
    return await jam(run, "B6", "Alm_HandlingJam", lambda before: run.mould_entered(RELEASE, before), (2.8, 4.0),
                     dropped)


async def t_mould_stuck(run: Run) -> list[bool]:
    def still_open() -> tuple[bool, str]:
        return run.plant.blowmoulder.mould_open, f", mould open {run.plant.blowmoulder.mould_open}"
    return await jam(run, "B7", "Alm_MouldFault", lambda before: run.mould_entered(CLOSE, before), (2.8, 4.0),
                     still_open, expected=("unblown",), more=2)


def held_closed(run: Run) -> tuple[bool, str]:
    """Rule 3 + 4 in FAULT: the exhaust open and the mould held closed on every FAULT tick seen so far."""
    outs = run.fault_outputs
    ok = bool(outs) and all(o.exhaust_valve and o.mould_close and not o.high_blow_valve for o in outs)
    return ok and run.plant.blowmoulder.mould_closed, (f", {len(outs)} FAULT ticks: exhaust open + mould held "
                                                         f"{ok}, mould closed {run.plant.blowmoulder.mould_closed}")


async def t_air_low(run: Run) -> list[bool]:
    return await jam(run, "B8", "Alm_BlowFault", lambda before: run.mould_entered(BLOW, before), (1.8, 3.0),
                     lambda: held_closed(run), expected=("half-blown",), more=2)


async def refused_then_recover(run: Run, label: str, alarm: str, step_at, limit) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on(alarm), 90, alarm)
    t = run.since(alarm, step_at(run.alarm_at[alarm]))
    await asyncio.sleep(1.0)
    hold_ok, hold = held_closed(run)
    refused = await run.reset_refused()
    r = [check(f"{label}a -> {alarm}, the mould held, Reset refused", limit[0] <= t <= limit[1] and hold_ok and refused,
               f"{t:.1f} s (want {limit[0]:g}-{limit[1]:g} s){hold}, Reset refused {refused}")]
    await run.reset_start()
    ok = await run.a_bottle()
    bad = run.unexpected()
    r.append(check(f"{label}b after the repair Reset + Start, production again", ok and not bad,
                   f"ejected {run.ejected}, unexpected {bad[:3] or 'none'}"))
    return r


async def t_exhaust_blocked(run: Run) -> list[bool]:
    return await refused_then_recover(run, "B9", "Alm_ExhaustFault", lambda before: run.mould_entered(EXHAUST, before),
                                      (2.8, 4.0))


async def t_regulator_failed(run: Run) -> list[bool]:
    def over() -> tuple[bool, str]:
        # the PLC cuts the high blow at MaxPressure (interlock + alarm) one tick after the simulator wrote it: with a
        # 45 bar regulator the pressure climbs on during that tick (found live: peak 42.4 bar), then the exhaust
        # vents it, so the value when the alarm is READ is already lower
        bar = run.max_bar
        hold_ok, hold = held_closed(run)
        return 40.0 <= bar <= 44.0 and hold_ok, (f", peak {bar:.1f} bar (MaxPressure 40.0; regulator 45), "
                                                 f"{run.at_alarm['Alm_OverPressure'][1]:.1f} bar when read{hold}")
    await run.start()
    await run.until(run.alarm_on("Alm_OverPressure"), 90, "Alm_OverPressure")
    await asyncio.sleep(0.5)
    ok, detail = over()
    r = [check("B10a 45 bar regulator -> Alm_OverPressure, high blow off", ok and run.faulted(), detail.lstrip(", "))]
    await run.reset_start()
    ok = await run.a_bottle()
    bad = run.unexpected()
    r.append(check("B10b Reset + Start, production again", ok and not bad,
                   f"ejected {run.ejected}, unexpected {bad[:3] or 'none'}"))
    return r


async def t_sensor_wire_break(run: Run) -> list[bool]:
    return await refused_then_recover(run, "B11", "Alm_PressureSensor",
                                      lambda before: run.command_at("pressure_wire_break"), (0.0, 0.6))


async def t_ejector_stuck(run: Run) -> list[bool]:
    def in_mould() -> tuple[bool, str]:
        return run.plant.blowmoulder.bottle_in_mould, f", bottle in the mould {run.plant.blowmoulder.bottle_in_mould}"
    return await jam(run, "B12", "Alm_HandlingJam", lambda before: run.mould_entered(EJECT, before), (2.8, 4.0),
                     in_mould)


async def t_c1_backed_up(run: Run) -> list[bool]:
    await run.start()
    print("      ... C1 backs up, then 2 min Blocked")
    await run.until(lambda: BLOCKED in run.states_seen, 120, "Blocked")
    blocked_at = run.now
    b = run.plant.blowmoulder
    await run.until(run.alarm_on("Alm_OutfeedBlocked"), 200, "Alm_OutfeedBlocked")
    t = run.alarm_at["Alm_OutfeedBlocked"] - blocked_at
    waiting = run.status.mould_step == EJECT and b.bottle_in_mould and b.mould_open
    r = [check("B13a C1 backed up -> Blocked with a bottle in the open mould, warning after 2 min",
               117 <= t <= 124 and waiting and not run.faulted(),
               f"warning {t:.1f} s after Blocked began, mould step {run.status.mould_step}, bottle in the open mould "
               f"{waiting}, C1 {run.plant.c1.count}")]
    ok = await run.a_bottle(2, timeout=60)
    r.append(check("B13b continues by itself", ok and not run.faulted() and not run.unexpected(),
                   f"ejected {run.ejected}, no Reset pressed, unexpected {run.unexpected()[:2] or 'none'}"))
    return r


SCRIPTS = {
    "hopper_empty": t_hopper_empty,
    "preform_stuck": t_preform_stuck,
    "lamps_failed": t_lamps_failed,
    "lamps_stuck_on": t_lamps_stuck_on,
    "arm_stuck": t_arm_stuck,
    "preform_dropped": t_preform_dropped,
    "mould_stuck": t_mould_stuck,
    "air_low": t_air_low,
    "exhaust_blocked": t_exhaust_blocked,
    "regulator_failed": t_regulator_failed,
    "sensor_wire_break": t_sensor_wire_break,
    "ejector_stuck": t_ejector_stuck,
    "c1_backed_up": t_c1_backed_up,
}


async def main() -> int:
    claim_plant("test_blowmoulder_scenarios.py", HOST, config.BLOWMOULDER_PORT)
    warn_if_fuxa("test_blowmoulder_scenarios.py")  # acts as the HMI (issue I4)
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
            await run.finish(mode_after=STOP_MODE if name == names[-1] else MANUAL)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
