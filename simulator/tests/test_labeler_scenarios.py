"""Labeler simulator stage 6: fault scenarios with the REAL labeler PLC and a plant that reacts.

Each scenario runs the simulator loop (Plant(with_labeler=True) + LabelerPlc, as
`uv run simulator --headless --scenario NAME`) with a fresh plant. A separate client ACTS AS THE HMI
(Auto, Reset, Start); the simulator never writes those. The filler runtime is NOT used: its link points
at a closed port, and the test puts a bottle on C2's entry every 7 s (test input, like a stand-in).
The glue starts warm (170 degC) to save the warm-up, except in heater_failed. Checks use the PLC's steps /
alarms / states as the loop saw them, with their simulation times, and the plant's true values.

    T1 web_torn         WRAP without Label_Mark -> Alm_LabelTimeout 5 s after WRAP began, FAULT;
                        Reset + Start labels the same bottle, whole label
    T2 bell_stuck       bell stuck down in PRESS -> Alm_PocketJam 2 s after UNCLAMP began, FAULT;
                        Reset + Start discharges the labelled bottle (never labelled twice)
    T3 heater_failed    Alm_GlueHeatTimeout 120 s after WARM_UP began, FAULT; after repair warm-up + labels
    T4 heater_stuck_on  Alm_GlueOverTemp at 200 degC, FAULT, heater output off; Reset only below 200 degC;
                        Start -> WARM_UP until the glue is back in the window, then labels
    T5 roll_empty       2 labels, then Starved (State 2) + Alm_LabelEmpty, no FAULT, no bottle admitted;
                        the roll change alone restarts it (no Reset); every label whole, also the roll's last (T5c)
    T6 glue_refill      Glue_Low only as a sensor (no PLC alarm); cold glue -> WARM_UP after the bottle,
                        labels again, never FAULT
    T7 gate_jam         Alm_PocketJam about 2 s after the bottle stuck, FAULT; the operator takes it out,
                        Reset + Start labels the next bottle
    T8 packer_stopped   C3 full -> WAIT_OUTFEED (State 3 Blocked), Alm_OutfeedBlocked 120 s later (no FAULT);
                        continues by itself when the packer runs again

    uv run python tests/test_labeler_scenarios.py               # all, about 12 minutes
    uv run python tests/test_labeler_scenarios.py --quick       # without heater_failed (120 s) and packer_stopped (2 min)
    uv run python tests/test_labeler_scenarios.py gate_jam      # some of them

Release all debugger forces first; no simulator or other test script at the same time. At the end the
labeler PLC is left in Stop mode.
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
from simulator.machines.labeler import LabelerSensors
from simulator.plant import LabelerWrites, Plant
from simulator.plcs import LABELER_STEP_NAMES, FillerPlc, LabelerPlc
from simulator.scenarios import SCENARIOS, ScenarioRunner

HOST = config.PLC_HOST
IDLE, WARM_UP, WAIT_BOTTLE, CLAMP, WRAP, PRESS, UNCLAMP, WAIT_OUTFEED, DISCHARGE, FAULT = \
    0, 5, 10, 20, 22, 24, 26, 35, 40, 900
STARVED, BLOCKED = 2, 3
SLOW = ("heater_failed", "packer_stopped")  # 120 s timeouts: left out by --quick
FEED_S = 7.0  # one bottle onto C2 every 7 s
MANUAL, AUTO, STOP_MODE = 2, 1, 0
START, RESET = 1, 3


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

    def __init__(self, name: str, warm: bool = True) -> None:
        self.name = name
        path = Path("logs") / time.strftime(f"labeler-scenario-{name}-%Y%m%d-%H%M%S.log")
        path.parent.mkdir(exist_ok=True)
        self.log = EventLog(max_events=100_000, file_path=path)
        self.plant = Plant(with_labeler=True)
        self.plant.supply.per_min = 0  # no filler PLC: the test feeds C2 itself
        if warm:
            self.plant.labeler.tank.temp_c = 170.0  # start state of the fresh plant, not a runtime change
        self.plc = FillerPlc(port=_closed_port())
        self.labeler_plc = LabelerPlc()
        self.hmi = AsyncModbusTcpClient(HOST, port=config.LABELER_PORT)
        self.stats: loop.LoopStats | None = None
        self.runner = ScenarioRunner(SCENARIOS[name])
        self.steps: list[int] = []  # steps in the order seen
        self.step_at: list[tuple[int, float]] = []  # (step, simulation time it was entered), every entry
        self.alarm_at: dict[str, float] = {}  # simulation time an alarm was first seen ON
        self.states_seen: set[int] = set()
        self.temp_at_alarm: dict[str, float] = {}
        self.heater_in_fault: list[bool] = []  # GlueHeater output while the PLC is in FAULT
        self.started_at: float | None = None  # simulation time Start was pressed: alarms count from here on
        self.fed = 0
        self.feeding = True
        self._next_feed = 1.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        print(f"\n=== {name} (log {path})")

    # --- Simulator in the background -------------------------------------------------
    async def start(self) -> None:
        if not await self.hmi.connect():
            raise SystemExit(f"no connection to {HOST}:{config.LABELER_PORT}")
        await self.hmi.write_register(lm.MODE_REQ, MANUAL)  # outputs off before the new plant's first tick

        def on_tick(stats: loop.LoopStats) -> None:
            self.stats = stats
            if self.feeding and self.now >= self._next_feed and self.plant.c2.put_at_entry(self.plant.factory.new()):
                self.fed += 1
                self._next_feed = self.now + FEED_S
            st = stats.labeler_status
            if st is None:
                return
            self.states_seen.add(st.state)
            if st.step == FAULT and self.plant.labeler_outputs is not None:
                self.heater_in_fault.append(self.plant.labeler_outputs.glue_heater)
            if not self.steps or self.steps[-1] != st.step:
                self.steps.append(st.step)
                self.step_at.append((st.step, self.now))
                lab = self.plant.labeler
                print(f"      {self.now:6.1f} s  step -> {LABELER_STEP_NAMES.get(st.step, st.step):<12} "
                      f"glue {lab.tank.temp_c:5.1f} degC  roll {lab.web.labels_left:4d}  C3 {self.plant.c3.count}  "
                      f"labelled {lab.labelled}")
            if self.started_at is None:
                return  # an alarm left over from the run before is not this scenario's
            for alarm, on in st.alarms.items():
                if on and alarm not in self.alarm_at and alarm != "Alm_CommLost":
                    self.alarm_at[alarm] = self.now
                    self.temp_at_alarm[alarm] = self.plant.labeler.tank.temp_c
                    print(f"      {self.now:6.1f} s  ALARM {alarm}")

        self._task = asyncio.create_task(loop.run(
            self.plc, self.plant, self.log, stop=self._stop, on_tick=on_tick, scenario=self.runner,
            labeler_plc=self.labeler_plc))
        await self.start_from_idle()

    async def finish(self, mode_after: int = MANUAL) -> None:
        await self.hmi.write_register(lm.MODE_REQ, MANUAL)  # every output off, sequencer frozen
        # The PLC keeps scanning after the loop stops: a bottle still on its way from the gate would leave
        # Bottle_Entering set and the PLC would raise Alm_PocketJam during the pause (found live: the next
        # scenario started with that jam). So the loop runs on until no bottle is between gate and pocket.
        self.feeding = False
        await asyncio.sleep(1.0)  # a gate pulse already running ends (TP 0.5 s)
        if not self.plant.labeler.gate_jammed:
            await self.until(lambda: self.plant.labeler.entering is None, 5, "no bottle in the gate")
        self._stop.set()
        if self._task:
            await self._task
        neutral = LabelerWrites(LabelerSensors(False, False, False, False, False, False, False, False, False, False,
                                               1700), e_stop_ok=True, heartbeat=True)
        if self.labeler_plc.connected or await self.labeler_plc.ensure_connected():
            await self.labeler_plc.write(neutral)
        await self.hmi.write_register(lm.MODE_REQ, mode_after)
        self.labeler_plc.close()
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
        await self.press(lm.MODE_REQ, AUTO, "Auto (leaving Manual -> IDLE)")
        await self.press(lm.CMD, RESET, "Reset")
        self.started_at = self.now
        await self.press(lm.CMD, START, "Start")

    async def reset_until_out_of_fault(self, timeout: float = 30.0) -> None:
        """Reset is accepted only when the cause is gone (e.g. GlueTemp < 200 degC): press it until FAULT is left."""
        end = self.now + timeout
        while self.status is None or self.status.step == FAULT:
            if self.now > end:
                raise ScenarioTimeout(f"FAULT not left within {timeout:g} s of Reset")
            await self.press(lm.CMD, RESET, "Reset")
            await asyncio.sleep(1.5)

    async def reset_start(self) -> None:
        await self.until(lambda: self.runner.done, 30, "scenario finished (cause removed)")
        await asyncio.sleep(1.0)
        await self.reset_until_out_of_fault()
        await self.press(lm.CMD, START, "Start")

    # --- Waiting and reading -----------------------------------------------------------
    @property
    def status(self):
        return self.stats.labeler_status if self.stats else None

    @property
    def now(self) -> float:
        return self.plant.now

    @property
    def labelled(self) -> int:
        return self.plant.labeler.labelled

    async def until(self, test: Callable[[], bool], timeout: float, what: str) -> None:
        end = self.now + timeout
        while not test():
            if self._task.done():  # the loop died: show why
                self._task.result()
            if self.now > end:
                raise ScenarioTimeout(f"{what} not reached within {timeout:g} s (steps {self.steps[-8:]})")
            await asyncio.sleep(0.1)

    def alarm_on(self, name: str) -> Callable[[], bool]:
        return lambda: self.status is not None and self.status.alarms[name]

    def entered(self, step: int, before: float | None = None) -> float | None:
        """Simulation time `step` was last entered (before `before`, if given)."""
        times = [t for s, t in self.step_at if s == step and (before is None or t <= before)]
        return times[-1] if times else None

    def sim_warnings(self, *words: str) -> list[str]:
        return [e.format() for e in self.log.filter(source=Source.SIM, warnings_only=True)
                if any(w in e.text for w in words)]


LABEL_AUDIT = ("without a label", "broken label", "two bottles", "label fed but lost", "against the bell", "unclamped")


# === The scenarios ===================================================================
async def t_web_torn(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_LabelTimeout"), 60, "Alm_LabelTimeout")
    t = run.alarm_at["Alm_LabelTimeout"] - run.entered(WRAP, run.alarm_at["Alm_LabelTimeout"])
    r = [check("T1a torn web -> Alm_LabelTimeout", 4.5 <= t <= 6.0 and FAULT in run.steps,
               f"{t:.1f} s after WRAP began (LabelTimeout 5 s), FAULT {FAULT in run.steps}")]
    await run.reset_start()
    await run.until(lambda: run.labelled >= 1, 40, "the bottle labelled after Reset + Start")
    r.append(check("T1b same bottle labelled after repair", run.labelled == 1 and not run.sim_warnings(*LABEL_AUDIT),
                   f"labelled {run.labelled}, audit {run.sim_warnings(*LABEL_AUDIT)[:2] or 'none'}"))
    return r


async def t_bell_stuck(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_PocketJam"), 60, "Alm_PocketJam")
    t = run.alarm_at["Alm_PocketJam"] - run.entered(UNCLAMP, run.alarm_at["Alm_PocketJam"])
    r = [check("T2a bell stuck down -> Alm_PocketJam", 1.8 <= t <= 2.8 and FAULT in run.steps,
               f"{t:.1f} s after UNCLAMP began (ClampTimeout 2 s), FAULT {FAULT in run.steps}")]
    marks = run.plant.labeler.web.marks
    await run.reset_start()
    await run.until(lambda: run.labelled >= 1, 30, "the labelled bottle discharged")
    after = run.steps[run.steps.index(FAULT):]
    r.append(check("T2b labelled bottle discharged, not labelled twice",
                   run.plant.labeler.web.marks == marks and CLAMP not in after[:after.index(DISCHARGE)]
                   if DISCHARGE in after else False,
                   f"steps after FAULT {[LABELER_STEP_NAMES.get(s, s) for s in after[:5]]}, marks {marks} -> "
                   f"{run.plant.labeler.web.marks}"))
    return r


async def t_heater_failed(run: Run) -> list[bool]:
    await run.start()
    print("      ... WARM_UP with a dead heater (120 s)")
    await run.until(run.alarm_on("Alm_GlueHeatTimeout"), 180, "Alm_GlueHeatTimeout")
    t = run.alarm_at["Alm_GlueHeatTimeout"] - run.entered(WARM_UP)
    r = [check("T3a dead heater -> Alm_GlueHeatTimeout", 118 <= t <= 124 and FAULT in run.steps
               and run.temp_at_alarm["Alm_GlueHeatTimeout"] < 160,
               f"{t:.1f} s after WARM_UP began (HeatTimeout 120 s), glue {run.temp_at_alarm['Alm_GlueHeatTimeout']:.1f} degC")]
    await run.reset_start()
    await run.until(lambda: run.labelled >= 1, 120, "a bottle labelled after the warm-up")
    r.append(check("T3b warm-up + label after repair", run.labelled >= 1, f"labelled {run.labelled}"))  # cold: none before
    return r


async def t_heater_stuck_on(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_GlueOverTemp"), 60, "Alm_GlueOverTemp")
    temp = run.temp_at_alarm["Alm_GlueOverTemp"]
    await run.until(lambda: len(run.heater_in_fault) >= 5, 10, "a few ticks in FAULT")
    r = [check("T4a stuck heater -> Alm_GlueOverTemp, heater output off", 199.9 <= temp <= 202.0
               and not any(run.heater_in_fault[2:]),
               f"glue {temp:.1f} degC at the alarm (GlueMax 200), GlueHeater in FAULT {run.heater_in_fault[2:6]}")]
    before = run.labelled  # a bottle may have been labelled before the glue got too hot
    start = run.now  # before Reset + Start: the WARM_UP after Start counts
    await run.reset_start()
    await run.until(lambda: run.labelled > before, 150, "a bottle labelled after the glue cooled down")
    warm_up = [t for s, t in run.step_at if s == WARM_UP and t >= start]
    ready = [t for s, t in run.step_at if s == WAIT_BOTTLE and t >= start]
    wait = ready[0] - warm_up[0] if warm_up and ready else None
    r.append(check("T4b Reset below 200 degC, WARM_UP until the window, then labels",
                   wait is not None and wait < 120 and run.steps.count(FAULT) == 1,
                   f"WARM_UP {wait and round(wait)} s (< HeatTimeout 120 s), FAULTs {run.steps.count(FAULT)}"))
    return r


async def t_roll_empty(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_LabelEmpty"), 60, "Alm_LabelEmpty")
    labelled = run.labelled
    await asyncio.sleep(3.0)  # the scenario changes the roll 5 s after the alarm
    in_pocket = run.plant.labeler.pocket is not None or run.plant.labeler.entering is not None
    r = [check("T5a empty roll -> Starved + Alm_LabelEmpty, no FAULT, gate shut",
               labelled == 2 and STARVED in run.states_seen and FAULT not in run.steps and not in_pocket,
               f"labelled {labelled} (roll of 2), states {sorted(run.states_seen)}, bottle admitted {in_pocket}")]
    await run.until(lambda: run.labelled >= 3, 40, "labels again after the roll change")
    cleared = not run.status.alm_label_empty
    r.append(check("T5b roll change restarts without Reset", run.labelled >= 3 and cleared and FAULT not in run.steps,
                   f"labelled {run.labelled}, Alm_LabelEmpty cleared {cleared}"))
    # The last label of the roll must be whole (found in the panel check: without the roll's tail the PLC's
    # feed run-on was cut off and it left with 169 of 210 mm). Bottle and simulator warnings both count.
    audit = [e.format() for e in run.log.filter(warnings_only=True) if any(w in e.text for w in LABEL_AUDIT)]
    discharged = run.plant.labeler.discharged
    r.append(check("T5c every label whole, also the last of the roll", not audit and run.labelled == discharged,
                   f"discharged {discharged}, labelled {run.labelled}, audit {audit[:2] or 'none'}"))
    return r


async def t_glue_refill(run: Run) -> list[bool]:
    await run.start()
    await asyncio.sleep(1.0)
    low = run.plant.labeler.sensors().glue_low
    no_alarm = run.status is not None and not any(on for n, on in run.status.alarms.items() if n != "Alm_CommLost")
    await run.until(lambda: run.runner.done, 60, "cold glue added")
    added_at = run.now
    await run.until(lambda: run.labelled >= 2, 90, "labels again after the warm-up")
    back = [t for s, t in run.step_at if s == WARM_UP and t >= added_at]
    r = [check("T6a Glue_Low is a sensor warning only", low and no_alarm, f"Glue_Low {low}, PLC alarms none {no_alarm}")]
    r.append(check("T6b cold glue -> WARM_UP after the bottle, labels again, no FAULT",
                   bool(back) and FAULT not in run.steps and not run.plant.labeler.sensors().glue_low
                   and not run.sim_warnings(*LABEL_AUDIT),
                   f"WARM_UP at {back and round(back[0] - added_at, 1)} s after the refill, labelled {run.labelled}, "
                   f"Glue_Low now {run.plant.labeler.sensors().glue_low}"))
    return r


async def t_gate_jam(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_PocketJam"), 60, "Alm_PocketJam")
    gate_events = [e.time_s for e in run.log.filter(source=Source.BOTTLE) if "through the labeler gate" in e.text]
    t = run.alarm_at["Alm_PocketJam"] - gate_events[0] if gate_events else -1
    r = [check("T7a stuck bottle -> Alm_PocketJam, FAULT", 1.5 <= t <= 2.8 and FAULT in run.steps,
               f"{t:.1f} s after the bottle passed the gate (ClampTimeout 2 s)")]
    await run.reset_start()
    await run.until(lambda: run.labelled >= 1, 40, "the next bottle labelled")
    lab = run.plant.labeler
    r.append(check("T7b operator took it out, the next bottle is labelled",
                   len(lab.removed) == 1 and not lab.lost and lab.labelled >= 1 and not run.sim_warnings(*LABEL_AUDIT),
                   f"removed {len(lab.removed)}, lost {len(lab.lost)}, labelled {lab.labelled}"))
    return r


async def t_packer_stopped(run: Run) -> list[bool]:
    await run.start()
    print("      ... C3 fills up, then 2 min Blocked")
    await run.until(run.alarm_on("Alm_OutfeedBlocked"), 300, "Alm_OutfeedBlocked")
    blocked_at = run.entered(WAIT_OUTFEED)
    t = run.alarm_at["Alm_OutfeedBlocked"] - blocked_at if blocked_at is not None else -1
    r = [check("T8a C3 full -> WAIT_OUTFEED, Blocked, warning after 2 min",
               BLOCKED in run.states_seen and 118 <= t <= 124 and FAULT not in run.steps,
               f"warning {t:.1f} s after WAIT_OUTFEED began, states {sorted(run.states_seen)}")]
    labelled = run.labelled
    await run.until(lambda: run.labelled > labelled, 40, "continues after the packer runs again")
    r.append(check("T8b continues by itself", run.labelled > labelled and FAULT not in run.steps,
                   f"labelled {labelled} -> {run.labelled}, no Reset pressed"))
    return r


SCRIPTS = {
    "web_torn": t_web_torn,
    "bell_stuck": t_bell_stuck,
    "heater_failed": t_heater_failed,
    "heater_stuck_on": t_heater_stuck_on,
    "roll_empty": t_roll_empty,
    "glue_refill": t_glue_refill,
    "gate_jam": t_gate_jam,
    "packer_stopped": t_packer_stopped,
}


async def main() -> int:
    claim_plant("test_labeler_scenarios.py", HOST, config.LABELER_PORT)
    warn_if_fuxa("test_labeler_scenarios.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    quick = "--quick" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name in wanted:
        if name not in SCRIPTS:
            raise SystemExit(f"unknown scenario {name!r} (known: {', '.join(SCRIPTS)})")
    names = wanted or [n for n in SCRIPTS if not (quick and n in SLOW)]
    results: list[bool] = []
    for name in names:
        run = Run(name, warm=name != "heater_failed")
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
