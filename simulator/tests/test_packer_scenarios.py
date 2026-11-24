"""Packer simulator stage 7: fault scenarios with the REAL packer PLC and a plant that reacts.

Each scenario runs the simulator loop (Plant(with_labeler=True, with_packer=True) + PackerPlc, as
`uv run simulator --headless --scenario NAME`) with a fresh plant. A separate client ACTS AS THE HMI (Auto,
Reset, Start); the simulator never writes those. The filler and labeler runtimes are NOT used: their links
point at a closed port, and the test puts a labelled bottle on C3's entry every 2 s (test input, like a
stand-in), EXACTLY 6 per pack the scenario needs: after its checks every scenario drains (all bottles
packed, the oven empty, PacksInOven / GroupCount 0), so the PLC's counters fit the next fresh plant.
The oven starts warm (190 degC) except in oven_heater_failed. Checks use the PLC's steps / alarms / states
as the loop saw them, with their simulation times, and the plant's true values.

    P1 film_torn            FEED without Film_Pulse -> Alm_FilmFault 10 s after FEED began, FAULT;
                            Reset + Start feeds the sheet, the pack is wrapped (17 film marks, one sheet)
    P2 sheet_dropped        sheet lost at the cut -> READY without Film_In_Slot -> Alm_FilmFault 1 s later;
                            the sheet put back by hand wraps the pack, no second feed (17 marks)
    P3 film_empty           1.3 m roll: one sheet, then Starved (State 2) + Alm_FilmEmpty, no FAULT;
                            the roll change alone restarts it (2 packs)
    P4 pusher_stuck         PUSH without Pusher_Out -> Alm_PackJam 3 s after PUSH began, FAULT;
                            Reset + Start pushes the same group of 6
    P5 wrap_bar_stuck       WRAP without the bar home again -> Alm_WrapTimeout 5 s after WRAP began, FAULT;
                            that pack reached the oven unwrapped (expected warning), the next one is wrapped
    P6 oven_heater_failed   cold start: Alm_OvenHeatTimeout 120 s after WARM_UP began, FAULT; after repair
                            warm-up + a pack
    P7 oven_heater_stuck_on Alm_OvenOverTemp at 230 degC, FAULT, heater output off, blower runs on ~30 s;
                            Reset only below 230 degC; Start -> back in the window, a pack
    P8 blower_failed        blower without air -> heater off, Alm_AirflowLost 3 s after the failure, FAULT;
                            Reset + Start, a pack
    P9 pack_stuck_in_oven   no Oven_Exit -> Alm_OvenJam 20 s after the pack entered, FAULT; oven cleared,
                            Reset zeroes PacksInOven, the next pack comes out
    P10 palletizer_stopped  table full -> READY waits (State 3 Blocked), Alm_OutfeedBlocked 120 s later,
                            no FAULT; continues by itself when the palletizer runs again

    uv run python tests/test_packer_scenarios.py              # all, about 20 minutes
    uv run python tests/test_packer_scenarios.py --quick      # without oven_heater_failed and palletizer_stopped
    uv run python tests/test_packer_scenarios.py pusher_stuck # some of them

Release all debugger forces first; no simulator or other test script at the same time. At the end the
packer PLC is left in Stop mode.
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

from simulator import address_map_packer as pm
from simulator import config, loop
from simulator.events import EventLog, Source
from simulator.plant import Plant
from simulator.plcs import PACKER_STEP_NAMES, PACKER_WRAP_STEP_NAMES, FillerPlc, LabelerPlc, PackerPlc
from simulator.scenarios import SCENARIOS, ScenarioRunner

HOST = config.PLC_HOST
IDLE, WARM_UP, COUNT, PUSH, RETRACT, FAULT = 0, 5, 10, 20, 25, 900
WAIT_PACK, FEED, CUT, READY, RUN_ON, WRAP, RUN_OUT = 10, 20, 22, 24, 30, 32, 34
STARVED, BLOCKED = 2, 3
SLOW = ("oven_heater_failed", "palletizer_stopped")  # 120 s timeouts: left out by --quick
FEED_S = 2.0  # one labelled bottle onto C3 every 2 s
MANUAL, AUTO, STOP_MODE = 2, 1, 0
START, RESET = 1, 3
FEED_PULSES = 17  # FilmLength 850 / FilmPulseMM 50 (PLC defaults)


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

    def __init__(self, name: str, packs: int, warm: bool = True) -> None:
        self.name = name
        path = Path("logs") / time.strftime(f"packer-scenario-{name}-%Y%m%d-%H%M%S.log")
        path.parent.mkdir(exist_ok=True)
        self.log = EventLog(max_events=100_000, file_path=path)
        self.plant = Plant(with_labeler=True, with_packer=True)
        self.plant.supply.per_min = 0  # no filler / labeler PLC: the test feeds C3 itself
        if warm:
            self.plant.packer.oven.temp_c = 190.0  # start state of the fresh plant, not a runtime change
        closed = _closed_port()
        self.plc, self.labeler_plc = FillerPlc(port=closed), LabelerPlc(port=closed)
        self.packer_plc = PackerPlc()
        self.hmi = AsyncModbusTcpClient(HOST, port=config.PACKER_PORT)
        self.stats: loop.LoopStats | None = None
        self.runner = ScenarioRunner(SCENARIOS[name])
        self.bottles = packs * config.PACK_BOTTLES  # exactly: every bottle ends in a pack
        self.step_at: list[tuple[int, float]] = []  # (collation step, time entered)
        self.wrap_at: list[tuple[int, float]] = []  # (wrap step, time entered)
        self.alarm_at: dict[str, float] = {}  # simulation time an alarm was first seen ON
        self.temp_at_alarm: dict[str, float] = {}
        self.states_seen: set[int] = set()
        self.fault_outputs: list[tuple[float, bool, bool]] = []  # (time, heater, blower) while in FAULT
        self.oven_entry_at: float | None = None  # the first time a pack was in the oven
        self.started_at: float | None = None
        self.fed = 0
        self._next_feed = 1.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        print(f"\n=== {name} ({packs} pack(s), log {path})")

    # --- Simulator in the background -------------------------------------------------
    async def start(self) -> None:
        if not await self.hmi.connect():
            raise SystemExit(f"no connection to {HOST}:{config.PACKER_PORT}")
        await self.hmi.write_register(pm.MODE_REQ, MANUAL)  # outputs off before the new plant's first tick

        def on_tick(stats: loop.LoopStats) -> None:
            self.stats = stats
            plant = self.plant
            if self.fed < self.bottles and self.now >= self._next_feed:
                bottle = plant.factory.new()
                bottle.label_mm = config.LABEL_LENGTH_MM  # labelled, as the labeler would deliver it
                if plant.c3.put_at_entry(bottle):
                    self.fed += 1
                    self._next_feed = self.now + FEED_S
            if self.oven_entry_at is None and plant.packer.oven.packs:
                self.oven_entry_at = self.now
            st = stats.packer_status
            if st is None:
                return
            self.states_seen.add(st.state)
            if st.step == FAULT and plant.packer_outputs is not None:
                self.fault_outputs.append((self.now, plant.packer_outputs.oven_heater, plant.packer_outputs.oven_blower))
            changed = False
            if not self.step_at or self.step_at[-1][0] != st.step:
                self.step_at.append((st.step, self.now))
                changed = True
            if not self.wrap_at or self.wrap_at[-1][0] != st.wrap_step:
                self.wrap_at.append((st.wrap_step, self.now))
                changed = True
            if changed:
                p = plant.packer
                print(f"      {self.now:6.1f} s  {PACKER_STEP_NAMES.get(st.step, st.step):<9} / "
                      f"{PACKER_WRAP_STEP_NAMES.get(st.wrap_step, st.wrap_step):<9} oven {p.oven.temp_c:5.1f} degC  "
                      f"table {len(p.table)}  pushed {p.groups_pushed}  wrapped {p.wrapper.wrapped}  "
                      f"in oven {len(p.oven.packs)}  out {p.oven.left}")
            if self.started_at is None:
                return  # an alarm left over from the run before is not this scenario's
            for alarm, on in st.alarms.items():
                if on and alarm not in self.alarm_at and alarm != "Alm_CommLost":
                    self.alarm_at[alarm] = self.now
                    self.temp_at_alarm[alarm] = plant.packer.oven.temp_c
                    print(f"      {self.now:6.1f} s  ALARM {alarm}")

        self._task = asyncio.create_task(loop.run(
            self.plc, self.plant, self.log, stop=self._stop, on_tick=on_tick, scenario=self.runner,
            labeler_plc=self.labeler_plc, packer_plc=self.packer_plc))
        await self.start_from_idle()

    def clean(self) -> bool:
        """Every bottle fed is packed and out of the oven, and the PLC's counters are 0 (see the module text)."""
        p, st = self.plant.packer, self.status
        physics = (self.fed == self.bottles and self.plant.c3.count == 0 and p.entering is None and not p.table
                   and not p.wrap_packs and not p.oven.packs)
        return physics and st is not None and st.packs_in_oven == 0 and st.group_count == 0 \
            and st.step in (COUNT, IDLE) and st.wrap_step in (WAIT_PACK, IDLE)

    async def finish(self, mode_after: int = MANUAL) -> bool:
        """Drain (see clean()), then Manual and stop the loop. Returns whether the PLC was left clean."""
        try:
            await self.until(self.clean, 120, "every bottle packed, the oven empty, the PLC's counters 0")
            ok = True
        except ScenarioTimeout as error:
            print(f"      ({error})")
            ok = False
        await self.hmi.write_register(pm.MODE_REQ, MANUAL)  # sequencers frozen, the oven off (blower run-on)
        await asyncio.sleep(1.0)
        self._stop.set()
        if self._task:
            await self._task
        await self.hmi.write_register(pm.MODE_REQ, mode_after)
        for link in (self.packer_plc, self.labeler_plc, self.plc):
            link.close()
        self.hmi.close()
        self.log.close()
        return ok

    # --- HMI side --------------------------------------------------------------------
    async def press(self, address: int, value: int, label: str) -> None:
        await self.hmi.write_register(address, value)
        print(f"      {self.now:6.1f} s  HMI: {label}")
        await asyncio.sleep(0.5)

    async def start_from_idle(self) -> None:
        await asyncio.sleep(2.0)  # heartbeat running a while, so Reset can clear Alm_CommLost
        await self.press(pm.MODE_REQ, AUTO, "Auto (leaving Manual -> IDLE)")
        await self.press(pm.CMD, RESET, "Reset")
        self.started_at = self.now
        await self.press(pm.CMD, START, "Start")

    async def reset_until_out_of_fault(self, timeout: float = 60.0) -> None:
        """Reset is accepted only when the cause is gone (e.g. OvenTemp < 230 degC): press it until FAULT is left."""
        end = self.now + timeout
        while self.status is None or self.status.step == FAULT:
            if self.now > end:
                raise ScenarioTimeout(f"FAULT not left within {timeout:g} s of Reset")
            await self.press(pm.CMD, RESET, "Reset")
            await asyncio.sleep(1.5)

    async def reset_start(self) -> None:
        await self.until(lambda: self.runner.done, 30, "scenario finished (cause removed)")
        await asyncio.sleep(1.0)
        await self.reset_until_out_of_fault()
        await self.press(pm.CMD, START, "Start")

    # --- Waiting and reading -----------------------------------------------------------
    @property
    def status(self):
        return self.stats.packer_status if self.stats else None

    @property
    def now(self) -> float:
        return self.plant.now

    @property
    def wrapped(self) -> int:
        return self.plant.packer.wrapper.wrapped

    @property
    def packs_out(self) -> int:
        return self.plant.packer.oven.left

    async def until(self, test: Callable[[], bool], timeout: float, what: str) -> None:
        end = self.now + timeout
        while not test():
            if self._task.done():  # the loop died: show why
                self._task.result()
            if self.now > end:
                raise ScenarioTimeout(f"{what} not reached within {timeout:g} s "
                                      f"(steps {self.step_at[-4:]}, wrap {self.wrap_at[-4:]})")
            await asyncio.sleep(0.1)

    def alarm_on(self, name: str) -> Callable[[], bool]:
        return lambda: self.status is not None and self.status.alarms[name]

    @staticmethod
    def _entered(seen: list[tuple[int, float]], step: int, before: float | None) -> float | None:
        times = [t for s, t in seen if s == step and (before is None or t <= before)]
        return times[-1] if times else None

    def entered(self, step: int, before: float | None = None) -> float | None:
        """Simulation time the collation step was last entered (before `before`, if given)."""
        return self._entered(self.step_at, step, before)

    def wrap_entered(self, step: int, before: float | None = None) -> float | None:
        return self._entered(self.wrap_at, step, before)

    def faulted(self) -> bool:
        return any(s == FAULT for s, _ in self.step_at)

    def command_at(self, name: str) -> float | None:
        times = [e.time_s for e in self.log.filter(source=Source.SIM) if e.text.startswith(f"command: {name} = True")]
        return times[0] if times else None

    def sim_warnings(self, *words: str) -> list[str]:
        return [e.format() for e in self.log.filter(source=Source.SIM, warnings_only=True)
                if any(w in e.text for w in words)]

    def since(self, name: str, start: float | None) -> float:
        return self.alarm_at[name] - start if start is not None and name in self.alarm_at else -1.0


PACK_AUDIT = ("not wrapped", "without film", "short sheet", "film torn", "two sheets", "fed under", "fed into",
              "pusher out", "pushed with", "pushed onto", "not shrunk", "without a whole label", "waits at")


def audit(run: Run, *expected: str) -> list[str]:
    """Pack-quality warnings other than the expected ones (the PLC's Settle_Timer: no "pusher out")."""
    return [w for w in run.sim_warnings(*PACK_AUDIT) if not any(x in w for x in expected)]


# === The scenarios ===================================================================
async def t_film_torn(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_FilmFault"), 90, "Alm_FilmFault")
    t = run.since("Alm_FilmFault", run.wrap_entered(FEED, run.alarm_at.get("Alm_FilmFault")))
    r = [check("P1a torn film -> Alm_FilmFault", 9.5 <= t <= 11.0 and run.faulted(),
               f"{t:.1f} s after FEED began (FeedTimeout 10 s), FAULT {run.faulted()}")]
    await run.reset_start()
    await run.until(lambda: run.wrapped >= 1, 60, "the pack wrapped after Reset + Start")
    film = run.plant.packer.wrapper.film
    r.append(check("P1b film threaded again: one sheet, the pack wrapped",
                   run.wrapped == 1 and film.pulses == FEED_PULSES and not audit(run),
                   f"wrapped {run.wrapped}, film marks {film.pulses} (want {FEED_PULSES}), audit {audit(run)[:2] or 'none'}"))
    return r


async def t_sheet_dropped(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_FilmFault"), 90, "Alm_FilmFault (if the sheet fell too late, the pack "
                                                       "drove on: see the log)")
    ready = run.wrap_entered(READY, run.alarm_at["Alm_FilmFault"])
    t = run.since("Alm_FilmFault", ready)
    r = [check("P2a sheet lost at the cut -> READY waits -> Alm_FilmFault", 0.8 <= t <= 1.8 and run.faulted(),
               f"{t:.1f} s after READY began (SheetCheck 1 s), FAULT {run.faulted()}")]
    await run.reset_start()
    await run.until(lambda: run.wrapped >= 1, 60, "the pack wrapped with the sheet put back")
    p = run.plant.packer
    pack = (p.wrap_packs + p.oven.packs + p.outfeed.packs + [None])[0]
    sheet = pack.sheet_mm if pack is not None else None
    r.append(check("P2b the sheet put back wraps the pack, no second feed",
                   run.wrapped == 1 and p.wrapper.film.pulses == FEED_PULSES and sheet == 850.0 and not audit(run),
                   f"wrapped {run.wrapped}, film marks {p.wrapper.film.pulses} (want {FEED_PULSES}), sheet {sheet} mm"))
    return r


async def t_film_empty(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_FilmEmpty"), 90, "Alm_FilmEmpty")
    await asyncio.sleep(2.0)  # the scenario loads the new roll 5 s after the warning
    r = [check("P3a empty film -> Starved + Alm_FilmEmpty, no FAULT",
               run.wrapped == 1 and STARVED in run.states_seen and not run.faulted(),
               f"wrapped {run.wrapped} (one sheet from a 1.3 m roll), states {sorted(run.states_seen)}")]
    await run.until(lambda: run.wrapped >= 2, 60, "the second pack wrapped after the roll change")
    cleared = not run.status.alm_film_empty
    r.append(check("P3b roll change restarts without Reset", run.wrapped == 2 and cleared and not run.faulted()
                   and not audit(run), f"wrapped {run.wrapped}, Alm_FilmEmpty cleared {cleared}"))
    return r


async def t_pusher_stuck(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_PackJam"), 90, "Alm_PackJam")
    t = run.since("Alm_PackJam", run.entered(PUSH, run.alarm_at["Alm_PackJam"]))
    r = [check("P4a stuck pusher -> Alm_PackJam", 2.8 <= t <= 4.0 and run.faulted(),
               f"{t:.1f} s after PUSH began (PushTimeout 3 s), FAULT {run.faulted()}")]
    await run.reset_start()
    await run.until(lambda: run.wrapped >= 1, 60, "the group pushed and wrapped")
    p = run.plant.packer
    sizes = [len(x.bottles) for x in p.wrap_packs + p.oven.packs + p.outfeed.packs]
    r.append(check("P4b the same group of 6 pushed after repair", p.groups_pushed == 1 and sizes == [6]
                   and not audit(run), f"groups pushed {p.groups_pushed}, pack sizes {sizes}"))
    return r


async def t_wrap_bar_stuck(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_WrapTimeout"), 90, "Alm_WrapTimeout")
    t = run.since("Alm_WrapTimeout", run.wrap_entered(WRAP, run.alarm_at["Alm_WrapTimeout"]))
    r = [check("P5a stuck bar -> Alm_WrapTimeout", 4.8 <= t <= 6.0 and run.faulted(),
               f"{t:.1f} s after WRAP began (WrapTimeout 5 s), FAULT {run.faulted()}")]
    await run.reset_start()
    await run.until(lambda: run.wrapped >= 1, 60, "the next pack wrapped")
    lost = run.sim_warnings("not wrapped")
    r.append(check("P5b that pack unwrapped (expected), the next one wrapped once",
                   run.wrapped == 1 and len(lost) == 1 and not audit(run, "not wrapped"),
                   f"wrapped {run.wrapped}, 'not wrapped' warnings {len(lost)}, other audit {audit(run, 'not wrapped')[:2] or 'none'}"))
    return r


async def t_oven_heater_failed(run: Run) -> list[bool]:
    await run.start()
    print("      ... WARM_UP with a dead heater (120 s)")
    await run.until(run.alarm_on("Alm_OvenHeatTimeout"), 180, "Alm_OvenHeatTimeout")
    t = run.since("Alm_OvenHeatTimeout", run.entered(WARM_UP))
    temp = run.temp_at_alarm["Alm_OvenHeatTimeout"]
    r = [check("P6a dead heater -> Alm_OvenHeatTimeout", 118 <= t <= 124 and run.faulted() and temp < 175,
               f"{t:.1f} s after WARM_UP began (HeatTimeout 120 s), oven {temp:.1f} degC")]
    await run.reset_start()
    await run.until(lambda: run.packs_out >= 1, 150, "warm-up and a pack out of the oven")
    r.append(check("P6b warm-up + a pack after repair", run.packs_out == 1 and not audit(run),
                   f"packs out {run.packs_out}, audit {audit(run)[:2] or 'none'}"))
    return r


async def t_oven_heater_stuck_on(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_OvenOverTemp"), 120, "Alm_OvenOverTemp")
    temp = run.temp_at_alarm["Alm_OvenOverTemp"]
    print("      ... FAULT: the blower runs on (30 s)")
    await run.until(lambda: any(not b for _, _, b in run.fault_outputs), 45, "the blower off after its run-on")
    fault_at = run.fault_outputs[0][0]
    blower_off = next(t for t, _, b in run.fault_outputs if not b)
    heaters = [h for _, h, _ in run.fault_outputs[2:]]
    run_on = blower_off - fault_at
    r = [check("P7a stuck heater -> Alm_OvenOverTemp, heater off, blower runs on",
               229.9 <= temp <= 231.5 and not any(heaters) and 28.0 <= run_on <= 33.0,
               f"oven {temp:.1f} degC at the alarm (OvenMax 230), OvenHeater in FAULT {any(heaters)}, "
               f"blower on {run_on:.1f} s after FAULT (BlowerRunOn 30 s)")]
    await run.reset_start()
    await run.until(lambda: run.packs_out >= 1, 150, "a pack out after the oven is back in its window")
    r.append(check("P7b Reset below 230 degC, back in the window, a pack", run.packs_out == 1
                   and sum(1 for s, _ in run.step_at if s == FAULT) == 1,
                   f"packs out {run.packs_out}, FAULTs {sum(1 for s, _ in run.step_at if s == FAULT)}"))
    return r


async def t_blower_failed(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_AirflowLost"), 90, "Alm_AirflowLost")
    t = run.since("Alm_AirflowLost", run.command_at("blower_failed"))
    r = [check("P8a blower without air -> Alm_AirflowLost", 2.8 <= t <= 4.2 and run.faulted(),
               f"{t:.1f} s after the blower failed (AirflowTimeout 3 s), FAULT {run.faulted()}")]
    await run.reset_start()
    await run.until(lambda: run.packs_out >= 1, 120, "a pack out after the repair")
    r.append(check("P8b Reset (always allowed) + Start, a pack", run.packs_out == 1 and not audit(run),
                   f"packs out {run.packs_out}, audit {audit(run)[:2] or 'none'}"))
    return r


async def t_pack_stuck_in_oven(run: Run) -> list[bool]:
    await run.start()
    await run.until(run.alarm_on("Alm_OvenJam"), 120, "Alm_OvenJam")
    t = run.since("Alm_OvenJam", run.oven_entry_at)
    r = [check("P9a pack caught in the oven -> Alm_OvenJam", 19.0 <= t <= 22.0 and run.faulted(),
               f"{t:.1f} s after the pack entered the oven (OvenJamTime 20 s), FAULT {run.faulted()}")]
    await run.until(lambda: run.runner.done, 30, "oven cleared")
    await asyncio.sleep(1.0)
    await run.reset_until_out_of_fault()
    await asyncio.sleep(0.5)
    zeroed = run.status.packs_in_oven
    await run.press(pm.CMD, START, "Start")
    await run.until(lambda: run.packs_out >= 1, 90, "the next pack out of the oven")
    r.append(check("P9b oven cleared, Reset zeroes PacksInOven, the next pack comes out",
                   zeroed == 0 and len(run.plant.packer.oven.removed) == 1 and run.packs_out == 1,
                   f"PacksInOven after Reset {zeroed}, taken out {len(run.plant.packer.oven.removed)}, "
                   f"packs out {run.packs_out}"))
    return r


async def t_palletizer_stopped(run: Run) -> list[bool]:
    await run.start()
    print("      ... the outfeed table fills, then 2 min Blocked")
    await run.until(run.alarm_on("Alm_OutfeedBlocked"), 360, "Alm_OutfeedBlocked")
    t = run.since("Alm_OutfeedBlocked", run.wrap_entered(READY, run.alarm_at["Alm_OutfeedBlocked"]))
    r = [check("P10a table full -> READY waits, Blocked, warning after 2 min",
               BLOCKED in run.states_seen and 118 <= t <= 124 and not run.faulted(),
               f"warning {t:.1f} s after READY began, states {sorted(run.states_seen)}")]
    out = run.packs_out
    await run.until(lambda: run.packs_out > out, 60, "continues after the palletizer runs again")
    r.append(check("P10b continues by itself", run.packs_out > out and not run.faulted() and not audit(run),
                   f"packs out {out} -> {run.packs_out}, no Reset pressed"))
    return r


SCRIPTS = {  # name: (test, packs it needs, warm oven)
    "film_torn": (t_film_torn, 1, True),
    "sheet_dropped": (t_sheet_dropped, 1, True),
    "film_empty": (t_film_empty, 2, True),
    "pusher_stuck": (t_pusher_stuck, 1, True),
    "wrap_bar_stuck": (t_wrap_bar_stuck, 2, True),
    "oven_heater_failed": (t_oven_heater_failed, 1, False),
    "oven_heater_stuck_on": (t_oven_heater_stuck_on, 1, True),
    "blower_failed": (t_blower_failed, 1, True),
    "pack_stuck_in_oven": (t_pack_stuck_in_oven, 2, True),
    # 7 packs: Table_Full comes at 3 on the table, up to 3 more are already past READY (Q3: the table holds 6),
    # so only the 7th surely waits in READY (found live: with 4, all passed before Outfeed_Full)
    "palletizer_stopped": (t_palletizer_stopped, 7, True),
}


async def main() -> int:
    claim_plant("test_packer_scenarios.py", HOST, config.PACKER_PORT)
    warn_if_fuxa("test_packer_scenarios.py")  # acts as the HMI (issue I4)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
    quick = "--quick" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name in wanted:
        if name not in SCRIPTS:
            raise SystemExit(f"unknown scenario {name!r} (known: {', '.join(SCRIPTS)})")
    names = wanted or [n for n in SCRIPTS if not (quick and n in SLOW)]
    results: list[bool] = []
    for name in names:
        test, packs, warm = SCRIPTS[name]
        run = Run(name, packs, warm)
        try:
            results += await test(run)
        except ScenarioTimeout as error:
            results.append(check(name, False, str(error)))
        finally:
            clean = await run.finish(mode_after=STOP_MODE if name == names[-1] else MANUAL)
            results.append(check(f"{name}: PLC left clean", clean, "all packed, oven empty, PacksInOven / GroupCount 0"
                                 if clean else "see the line above: the next scenario may start with old counts"))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
