"""The tick loop: PLC outputs -> physics -> sensors -> PLC, about every 0.1 s.

One tick (report section 7.3):
    1. apply the queued commands (panel, scenario, test)   commands.CommandQueue.apply()
    2. read the PLC outputs                       plcs.FillerPlc.read_outputs()
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
from simulator.plcs import MODE_NAMES, STATE_NAMES, STEP_NAMES, FillerPlc, PlcCommError, PlcReplyError, PlcStatus

VERIFY_EVERY = 10  # read the sensors back every 10 ticks (1 s): debugger force / second writer


class PlcWatcher:
    """Compares the PLC status with the previous tick and turns changes into events.

    The simulator only REPORTS what the PLC decided ("PLC: step WORK -> INDEX"),
    so the log shows cause (physics) and reaction (PLC) side by side.
    """

    def __init__(self) -> None:
        self.last: PlcStatus | None = None

    def update(self, status: PlcStatus, now: float) -> list[Event]:
        events: list[Event] = []
        last = self.last
        self.last = status
        if last is None:
            text = (
                f"status: mode {MODE_NAMES.get(status.mode, status.mode)}, "
                f"step {STEP_NAMES.get(status.step, status.step)}, "
                f"state {STATE_NAMES.get(status.state, status.state)}, GoodCount {status.good_count}"
            )
            events.append(Event.plc(now, text))
            for name, on in status.alarms.items():
                if on:
                    events.append(Event.plc(now, f"{name} active", warning=True))
            return events
        if status.mode != last.mode:  # e.g. Stop after a PLC restart: Start would be ignored
            events.append(Event.plc(now, f"mode {MODE_NAMES.get(last.mode, last.mode)} -> "
                                         f"{MODE_NAMES.get(status.mode, status.mode)}"))
        if status.step != last.step:
            events.append(Event.plc(now, f"step {STEP_NAMES.get(last.step, last.step)} -> "
                                         f"{STEP_NAMES.get(status.step, status.step)}"))
        if status.state != last.state:
            events.append(Event.plc(now, f"state {STATE_NAMES.get(last.state, last.state)} -> "
                                         f"{STATE_NAMES.get(status.state, status.state)}"))
        for name, on in status.alarms.items():
            if on != last.alarms[name]:
                events.append(Event.plc(now, f"{name} {'ON' if on else 'cleared'}", warning=on))
        return events


@dataclass
class LoopStats:
    ticks: int = 0
    dt_min: float = float("inf")
    dt_max: float = 0.0
    seconds: float = 0.0
    good_count_start: int | None = None
    good_count_end: int | None = None
    status: PlcStatus | None = None  # last PLC status read (None until the first answer)
    problems: set[str] = field(default_factory=set)  # distinct problems already logged

    def summary(self) -> str:
        made = (
            f", GoodCount +{self.good_count_end - self.good_count_start}"
            if self.good_count_start is not None and self.good_count_end is not None
            else ""
        )
        dt = f"dt {self.dt_min * 1000:.0f}-{self.dt_max * 1000:.0f} ms" if self.ticks else "no ticks"
        return f"{self.ticks} ticks in {self.seconds:.1f} s, {dt}{made}"


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
) -> LoopStats:
    """Run the loop until `seconds` have passed, `until()` is true or `stop` is set.

    `commands`: queue filled by the caller (panel, test); applied at the start of every
    tick, the only place where the world changes from outside the physics.
    `scenario`: puts its due commands on that queue first (one is made if none is given);
    its conditions see the PLC status of the previous tick.
    """
    # one plant writer per PLC: a second simulator / test script stops here (plant_lock.py)
    plant_lock.acquire(plc.host, plc.port, "simulator loop")
    stats = LoopStats()
    watcher = PlcWatcher()
    link_was_ok = False
    last_writes = None  # what the PLC should hold from our last write
    if scenario is not None and commands is None:
        commands = CommandQueue()
    start = last = time.monotonic()
    deadline = start

    def problem(key: str, text: str) -> None:
        """Log a problem once per distinct key (no flood while it lasts)."""
        if key not in stats.problems:
            stats.problems.add(key)
            log.add(Event.sim(plant.now, text, warning=True))

    while True:
        now = time.monotonic()
        dt, last = now - last, now
        if stats.ticks > 0:
            stats.dt_min, stats.dt_max = min(stats.dt_min, dt), max(stats.dt_max, dt)

        # 1. Commands, before anything is read or moved
        if scenario is not None:
            due, events = scenario.due(plant, stats.status)
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
                problem(f"read:{type(error).__name__}", f"reading the PLC outputs failed: {error}")
        elif not link_was_ok:
            problem("connect", f"PLC {plc.host}:{plc.port} not reachable, retrying every {plc.reconnect_s:g} s")

        # 3. Physics (the first tick has dt = 0: only the start state is written)
        writes, events = plant.step(out, dt)
        log.extend(events)

        # 4.+5. Write sensors, read the status back
        if plc.connected:
            try:
                # Read-back BEFORE writing: compare the PLC with what we wrote LAST tick. That
                # covers the whole gap between two ticks (a second writer acts there); right
                # after our own write only a ~2 ms window would be checked.
                if last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await plc.verify_writes(last_writes)
                    if wrong:
                        problem(f"foreign:{wrong}", f"written values changed by someone else: {', '.join(wrong)} "
                                                    "(debugger force or a second writer, e.g. a test script)")
                await plc.write(writes)
                last_writes = writes
                status = await plc.read_status()
                stats.status = status
                log.extend(watcher.update(status, plant.now))
                log.extend(plant.apply_setpoints(status.fill_setpoint, status.rinse_time_ms, status.cap_time_ms))
                if stats.good_count_start is None:
                    stats.good_count_start = status.good_count
                stats.good_count_end = status.good_count
            except PlcReplyError as error:
                problem(f"reply:{error}", str(error))
            except PlcCommError:
                last_writes = None  # after a reconnect the PLC may hold anything

        # 6. Link changes (link_ok = the PLC answered, not just TCP)
        if plc.link_ok != link_was_ok:
            link_was_ok = plc.link_ok
            text = f"PLC link up ({plc.host}:{plc.port})" if plc.link_ok else "PLC link lost, outputs taken as OFF"
            log.add(Event.sim(plant.now, text, warning=not plc.link_ok))
            if plc.link_ok:
                stats.problems.clear()  # report problems again after a reconnect

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
