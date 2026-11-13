"""The tick loop: PLC outputs -> physics -> sensors -> PLC, about every 0.1 s.

One tick (report section 7.3):
    1. apply the queued commands (panel, scenario, test)   commands.CommandQueue.apply()
    2. read the PLC outputs                       plcs.FillerPlc.read_outputs()
       (and the syrup room PLC's: plcs.SyrupPlc.read_signals(), when there is one)
    3. move the world by the measured dt          plant.Plant.step()
    4. write sensors + wiring values to the PLC   plcs.FillerPlc.write()
    5. read the PLC status: watcher events, setpoints for the audit
    6. pass all events to the log

Rules from the analysis of the simulator (docs/simulator/design-notes.md):
- Fixed rate: the loop sleeps until the next 0.1 s deadline, not "work, then sleep
  0.1 s" (that drifts to ~108 ms). If a tick comes late, the next deadline starts
  from now (no burst of catch-up ticks); Plant.step handles the long dt.
- PLC not reachable: the physics keeps running with all outputs OFF, which is what
  really happens when a PLC stops (its outputs drop out). Bottles stay where they
  are; the link comes back by itself (ensure_connected).
- Every distinct problem is logged once, not every tick (no log flood while the
  PLC is away).

The loop knows nothing about Modbus addresses and nothing about the screen: it
calls FillerPlc and Plant and hands Events to the EventLog.
"""

import asyncio
import time
from dataclasses import dataclass, field

from simulator import config, plant_lock
from simulator.commands import CommandQueue
from simulator.events import Event, EventLog
from simulator.machines.filler import FillerOutputs
from simulator.plant import Plant
from simulator.scenarios import ScenarioRunner, ScenarioSlot
from simulator.plant import SyrupSignals
from simulator.plcs import (
    MODE_NAMES,
    STATE_NAMES,
    STEP_NAMES,
    SYRUP_STATE_NAMES,
    SYRUP_STEP_NAMES,
    FillerPlc,
    PlcCommError,
    PlcReplyError,
    PlcStatus,
    SyrupPlc,
    SyrupStatus,
)

VERIFY_EVERY = 10  # read the sensors back every 10 ticks (1 s): debugger force / second writer


class PlcWatcher:
    """Compares the PLC status with the previous tick and turns changes into events.

    The simulator only REPORTS what the PLC decided ("PLC: step WORK -> INDEX"),
    so the log shows cause (physics) and reaction (PLC) side by side.

    The same class watches every PLC: the names, a text prefix ("syrup: ") and the counter
    that is shown in the first line differ. `log_count` also reports every increase of that
    counter (the syrup room's batches; the filler's GoodCount would flood the log).
    """

    def __init__(
        self,
        step_names: dict[int, str] = STEP_NAMES,
        state_names: dict[int, str] = STATE_NAMES,
        prefix: str = "",
        count_label: str = "GoodCount",
        count=lambda status: status.good_count,
        log_count: bool = False,
    ) -> None:
        self.step_names = step_names
        self.state_names = state_names
        self.prefix = prefix
        self.count_label = count_label
        self.count = count
        self.log_count = log_count
        self.last = None

    def update(self, status, now: float) -> list[Event]:
        events: list[Event] = []
        last = self.last
        self.last = status
        step, state = self.step_names, self.state_names

        def plc(text: str, warning: bool = False) -> None:
            events.append(Event.plc(now, self.prefix + text, warning=warning))

        if last is None:
            plc(f"status: mode {MODE_NAMES.get(status.mode, status.mode)}, "
                f"step {step.get(status.step, status.step)}, "
                f"state {state.get(status.state, status.state)}, {self.count_label} {self.count(status)}")
            for name, on in status.alarms.items():
                if on:
                    plc(f"{name} active", warning=True)
            return events
        if status.mode != last.mode:  # e.g. Stop after a PLC restart: Start would be ignored
            plc(f"mode {MODE_NAMES.get(last.mode, last.mode)} -> {MODE_NAMES.get(status.mode, status.mode)}")
        if status.step != last.step:
            plc(f"step {step.get(last.step, last.step)} -> {step.get(status.step, status.step)}")
        if status.state != last.state:
            plc(f"state {state.get(last.state, last.state)} -> {state.get(status.state, status.state)}")
        if self.log_count and self.count(status) > self.count(last):
            plc(f"batch complete ({self.count_label} {self.count(status)})")
        for name, on in status.alarms.items():
            if on != last.alarms[name]:
                plc(f"{name} {'ON' if on else 'cleared'}", warning=on)
        return events


class LinkTrack:
    """Loop bookkeeping of ONE PLC link: what it last wrote, which problems it already reported.

    `label` starts every message ("PLC" for the filler, "syrup PLC" for the syrup room), and the
    problem keys carry it, so a reconnect of one PLC does not announce the other one's problems again.
    """

    def __init__(self, plc, label: str) -> None:
        self.plc = plc
        self.label = label
        self.was_ok = False  # link_ok at the end of the previous tick
        self.last_writes = None  # what the PLC should hold from our last write

    def problem(self, stats: "LoopStats", log: EventLog, now: float, key: str, text: str) -> None:
        """Log a problem once per distinct key (no flood while it lasts)."""
        key = f"{self.label}:{key}"
        if key not in stats.problems:
            stats.problems.add(key)
            log.add(Event.sim(now, text, warning=True))

    def report_link(self, stats: "LoopStats", log: EventLog, now: float) -> None:
        """Log link changes (link_ok = the PLC answered, not just TCP)."""
        plc = self.plc
        if plc.link_ok == self.was_ok:
            return
        self.was_ok = plc.link_ok
        text = (f"{self.label} link up ({plc.host}:{plc.port})" if plc.link_ok
                else f"{self.label} link lost, outputs taken as OFF")
        log.add(Event.sim(now, text, warning=not plc.link_ok))
        if plc.link_ok:  # report this PLC's problems again after a reconnect
            stats.problems = {k for k in stats.problems if not k.startswith(f"{self.label}:")}


@dataclass
class LoopStats:
    ticks: int = 0
    dt_min: float = float("inf")
    dt_max: float = 0.0
    seconds: float = 0.0
    good_count_start: int | None = None
    good_count_end: int | None = None
    status: PlcStatus | None = None  # last PLC status read (None until the first answer)
    syrup_status: SyrupStatus | None = None  # same for the syrup room PLC (None without a syrup link)
    batch_count_start: int | None = None
    batch_count_end: int | None = None
    problems: set[str] = field(default_factory=set)  # distinct problems already logged

    def summary(self) -> str:
        made = (
            f", GoodCount +{self.good_count_end - self.good_count_start}"
            if self.good_count_start is not None and self.good_count_end is not None
            else ""
        )
        batches = (
            f", BatchCount +{self.batch_count_end - self.batch_count_start}"
            if self.batch_count_start is not None and self.batch_count_end is not None
            else ""
        )
        dt = f"dt {self.dt_min * 1000:.0f}-{self.dt_max * 1000:.0f} ms" if self.ticks else "no ticks"
        return f"{self.ticks} ticks in {self.seconds:.1f} s, {dt}{made}{batches}"


async def run(
    plc: FillerPlc,
    plant: Plant,
    log: EventLog,
    *,
    seconds: float | None = None,
    until=None,
    stop: asyncio.Event | None = None,
    tick_s: float = config.TICK_S,
    on_tick=None,
    commands: CommandQueue | None = None,
    scenario: ScenarioRunner | ScenarioSlot | None = None,
    syrup_plc: SyrupPlc | None = None,
) -> LoopStats:
    """Run the loop until `seconds` have passed, `until()` is true or `stop` is set.

    `syrup_plc`: the second PLC. The plant must have a syrup room (Plant(with_syrup=True)). Each
    PLC has its own link: one that is down never stops the other. A syrup PLC that does not answer
    switches the syrup room's pumps off and drops Product_Available (a dead PLC drives no wire).

    `commands`: queue filled by the caller (panel, test); applied at the start of every
    tick, the only place where the world changes from outside the physics.
    `scenario`: puts its due commands on that queue first (one is made if none is given);
    its conditions see the PLC status of the previous tick.
    """
    # one plant writer per PLC: a second simulator / test script stops here (plant_lock.py)
    if syrup_plc is not None and plant.syrup is None:
        raise ValueError("a syrup PLC link needs a plant with a syrup room: Plant(with_syrup=True)")
    plant_lock.acquire(plc.host, plc.port, "simulator loop")
    if syrup_plc is not None:
        plant_lock.acquire(syrup_plc.host, syrup_plc.port, "simulator loop")
    stats = LoopStats()
    watcher = PlcWatcher()
    filler = LinkTrack(plc, "PLC")
    syrup = LinkTrack(syrup_plc, "syrup PLC") if syrup_plc is not None else None
    syrup_watcher = PlcWatcher(SYRUP_STEP_NAMES, SYRUP_STATE_NAMES, "syrup: ", "BatchCount",
                               lambda status: status.batch_count, log_count=True)
    if scenario is not None and commands is None:
        commands = CommandQueue()
    start = last = time.monotonic()
    deadline = start

    while True:
        now = time.monotonic()
        dt, last = now - last, now
        if stats.ticks > 0:
            stats.dt_min, stats.dt_max = min(stats.dt_min, dt), max(stats.dt_max, dt)

        # 1. Commands, before anything is read or moved
        if scenario is not None:
            due, events = scenario.due(plant, stats.status, stats.syrup_status)
            log.extend(events)
            for command in due:
                commands.put_command(command)
        if commands is not None:
            log.extend(commands.apply(plant))

        # 2. Read the PLC outputs (link down: outputs OFF, like a stopped PLC)
        out = FillerOutputs()
        if await plc.ensure_connected():
            try:
                out = await plc.read_outputs()
            except PlcCommError as error:
                filler.problem(stats, log, plant.now, f"read:{type(error).__name__}",
                               f"reading the PLC outputs failed: {error}")
        elif not filler.was_ok:
            filler.problem(stats, log, plant.now, "connect",
                           f"PLC {plc.host}:{plc.port} not reachable, retrying every {plc.reconnect_s:g} s")

        # 2b. The syrup room PLC: its outputs and Product_Available (link down: None = a dead PLC)
        signals: SyrupSignals | None = None
        if syrup is not None:
            if await syrup_plc.ensure_connected():
                try:
                    signals = await syrup_plc.read_signals()
                except PlcCommError as error:
                    syrup.problem(stats, log, plant.now, f"read:{type(error).__name__}",
                                  f"reading the syrup PLC outputs failed: {error}")
            elif not syrup.was_ok:
                syrup.problem(stats, log, plant.now, "connect",
                              f"syrup PLC {syrup_plc.host}:{syrup_plc.port} not reachable, retrying every "
                              f"{syrup_plc.reconnect_s:g} s: the filler gets no Product_Available "
                              "(run with --filler-only to leave the syrup room out)")

        # 3. Physics (the first tick has dt = 0: only the start state is written)
        writes, events = plant.step(out, dt, signals)
        log.extend(events)

        # 4.+5. Write sensors, read the status back
        if plc.connected:
            try:
                # Read-back BEFORE writing: compare the PLC with what we wrote LAST tick. That
                # covers the whole gap between two ticks (a second writer acts there); right
                # after our own write only a ~2 ms window would be checked.
                if filler.last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await plc.verify_writes(filler.last_writes)
                    if wrong:
                        filler.problem(stats, log, plant.now, f"foreign:{wrong}",
                                       f"written values changed by someone else: {', '.join(wrong)} "
                                       "(debugger force or a second writer, e.g. a test script)")
                await plc.write(writes)
                filler.last_writes = writes
                status = await plc.read_status()
                stats.status = status
                log.extend(watcher.update(status, plant.now))
                log.extend(plant.apply_setpoints(status.fill_setpoint, status.rinse_time_ms, status.cap_time_ms))
                if stats.good_count_start is None:
                    stats.good_count_start = status.good_count
                stats.good_count_end = status.good_count
            except PlcReplyError as error:
                filler.problem(stats, log, plant.now, f"reply:{error}", str(error))
            except PlcCommError:
                filler.last_writes = None  # after a reconnect the PLC may hold anything

        # 4b.+5b. The same for the syrup room PLC, on its own: a failure here never skips the filler
        if syrup is not None and syrup_plc.connected:
            syrup_writes = plant.syrup_writes()
            try:
                if syrup.last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await syrup_plc.verify_writes(syrup.last_writes)
                    if wrong:
                        syrup.problem(stats, log, plant.now, f"foreign:{wrong}",
                                      f"syrup PLC: written values changed by someone else: {', '.join(wrong)} "
                                      "(debugger force or a second writer, e.g. a test script)")
                await syrup_plc.write(syrup_writes)
                syrup.last_writes = syrup_writes
                syrup_status = await syrup_plc.read_status()
                stats.syrup_status = syrup_status
                log.extend(syrup_watcher.update(syrup_status, plant.now))
                if stats.batch_count_start is None:
                    stats.batch_count_start = syrup_status.batch_count
                stats.batch_count_end = syrup_status.batch_count
            except PlcReplyError as error:
                syrup.problem(stats, log, plant.now, f"reply:{error}", str(error))
            except PlcCommError:
                syrup.last_writes = None

        # 6. Link changes
        filler.report_link(stats, log, plant.now)
        if syrup is not None:
            syrup.report_link(stats, log, plant.now)

        stats.ticks += 1
        stats.seconds = time.monotonic() - start
        if on_tick is not None:
            on_tick(stats)
        if (seconds is not None and stats.seconds >= seconds) or (until is not None and until()) \
                or (stop is not None and stop.is_set()):
            return stats

        # Fixed rate: sleep until the next deadline; a late tick does not cause catch-up ticks
        deadline += tick_s
        now = time.monotonic()
        if deadline < now:
            deadline = now
        await asyncio.sleep(deadline - now)
