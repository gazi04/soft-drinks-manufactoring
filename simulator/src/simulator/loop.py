"""The tick loop: PLC outputs -> physics -> sensors -> PLC, about every 0.1 s.

One tick (report section 7.3):
    1. apply the queued commands (panel, scenario, test)   commands.CommandQueue.apply()
    2. read the PLC outputs                       plcs.FillerPlc.read_outputs()
       (and the syrup room PLC's: plcs.SyrupPlc.read_signals(), when there is one;
        the labeler PLC's: plcs.LabelerPlc.read_outputs(), when there is one;
        the packer PLC's: plcs.PackerPlc.read_outputs(), when there is one;
        the blow moulder PLC's: plcs.BlowmoulderPlc.read_outputs(), when there is one)
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
from simulator.machines.blowmoulder import BlowmoulderOutputs
from simulator.machines.labeler import LabelerOutputs
from simulator.machines.packer import PackerOutputs
from simulator.plant import SyrupSignals
from simulator.plcs import (
    BLOWMOULDER_MOULD_STEP_NAMES,
    BLOWMOULDER_STATE_NAMES,
    BLOWMOULDER_STEP_NAMES,
    BlowmoulderPlc,
    BlowmoulderStatus,
    LABELER_STATE_NAMES,
    LABELER_STEP_NAMES,
    MODE_NAMES,
    PACKER_STATE_NAMES,
    PACKER_STEP_NAMES,
    PACKER_WRAP_STEP_NAMES,
    STATE_NAMES,
    STEP_NAMES,
    SYRUP_STATE_NAMES,
    SYRUP_STEP_NAMES,
    FillerPlc,
    LabelerPlc,
    LabelerStatus,
    PackerPlc,
    PackerStatus,
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
    `wrap_step_names`: the second sequencer's step names (the packer's WrapStep, the blow moulder's MouldStep),
    reported like the step; `second_attr` is its status field, `second_label` its name in the log.
    """

    def __init__(
        self,
        step_names: dict[int, str] = STEP_NAMES,
        state_names: dict[int, str] = STATE_NAMES,
        prefix: str = "",
        count_label: str = "GoodCount",
        count=lambda status: status.good_count,
        log_count: bool = False,
        wrap_step_names: dict[int, str] | None = None,
        second_attr: str = "wrap_step",
        second_label: str = "wrap step",
    ) -> None:
        self.step_names = step_names
        self.state_names = state_names
        self.prefix = prefix
        self.count_label = count_label
        self.count = count
        self.log_count = log_count
        self.wrap_step_names = wrap_step_names
        self.second_attr = second_attr
        self.second_label = second_label
        self.last = None

    def update(self, status, now: float) -> list[Event]:
        events: list[Event] = []
        last = self.last
        self.last = status
        step, state = self.step_names, self.state_names

        def plc(text: str, warning: bool = False) -> None:
            events.append(Event.plc(now, self.prefix + text, warning=warning))

        if last is None:
            wrap = self.wrap_step_names
            second = getattr(status, self.second_attr) if wrap is not None else None
            wrap_text = f"{self.second_label} {wrap.get(second, second)}, " if wrap is not None else ""
            plc(f"status: mode {MODE_NAMES.get(status.mode, status.mode)}, "
                f"step {step.get(status.step, status.step)}, {wrap_text}"
                f"state {state.get(status.state, status.state)}, {self.count_label} {self.count(status)}")
            for name, on in status.alarms.items():
                if on:
                    plc(f"{name} active", warning=True)
            return events
        if status.mode != last.mode:  # e.g. Stop after a PLC restart: Start would be ignored
            plc(f"mode {MODE_NAMES.get(last.mode, last.mode)} -> {MODE_NAMES.get(status.mode, status.mode)}")
        if status.step != last.step:
            plc(f"step {step.get(last.step, last.step)} -> {step.get(status.step, status.step)}")
        if self.wrap_step_names is not None:
            wrap, attr = self.wrap_step_names, self.second_attr
            before, after = getattr(last, attr), getattr(status, attr)
            if after != before:
                plc(f"{self.second_label} {wrap.get(before, before)} -> {wrap.get(after, after)}")
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
    labeler_status: LabelerStatus | None = None  # same for the labeler PLC (None without a labeler link)
    labeler_count_start: int | None = None  # the labeler's GoodCount
    labeler_count_end: int | None = None
    packer_status: PackerStatus | None = None  # same for the packer PLC (None without a packer link)
    packer_count_start: int | None = None  # the packer's PackCount
    packer_count_end: int | None = None
    blowmoulder_status: BlowmoulderStatus | None = None  # same for the blow moulder PLC (None without its link)
    blowmoulder_count_start: int | None = None  # the blow moulder's BottleCount
    blowmoulder_count_end: int | None = None
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
        labelled = (
            f", labeler GoodCount +{self.labeler_count_end - self.labeler_count_start}"
            if self.labeler_count_start is not None and self.labeler_count_end is not None
            else ""
        )
        packed = (
            f", PackCount +{self.packer_count_end - self.packer_count_start}"
            if self.packer_count_start is not None and self.packer_count_end is not None
            else ""
        )
        blown = (
            f", BottleCount +{self.blowmoulder_count_end - self.blowmoulder_count_start}"
            if self.blowmoulder_count_start is not None and self.blowmoulder_count_end is not None
            else ""
        )
        dt = f"dt {self.dt_min * 1000:.0f}-{self.dt_max * 1000:.0f} ms" if self.ticks else "no ticks"
        return f"{self.ticks} ticks in {self.seconds:.1f} s, {dt}{blown}{made}{batches}{labelled}{packed}"


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
    labeler_plc: LabelerPlc | None = None,
    packer_plc: PackerPlc | None = None,
    blowmoulder_plc: BlowmoulderPlc | None = None,
) -> LoopStats:
    """Run the loop until `seconds` have passed, `until()` is true or `stop` is set.

    `syrup_plc`: the second PLC. The plant must have a syrup room (Plant(with_syrup=True)). Each
    PLC has its own link: one that is down never stops the other. A syrup PLC that does not answer
    switches the syrup room's pumps off and drops Product_Available (a dead PLC drives no wire).

    `labeler_plc`: the third PLC, needed exactly when the plant has the real labeler
    (Plant(with_labeler=True)): it drives C2's motor, so without it the filler would block. A labeler
    PLC that does not answer switches all its outputs off: C2 stands, the heater is off.

    `packer_plc`: the fourth PLC, needed exactly when the plant has the real packer
    (Plant(with_packer=True)): it drives C3's motor, so without it the labeler would block. A packer
    PLC that does not answer switches all its outputs off: C3 stands, the oven is off.

    `blowmoulder_plc`: the fifth PLC, needed exactly when the plant has the real blow moulder
    (Plant(with_blowmoulder=True)): it makes the bottles on C1, so without it the filler would starve. A blow
    moulder PLC that does not answer switches all its outputs off except the exhaust, which vents
    (plant.BLOWMOULDER_DEAD).

    `commands`: queue filled by the caller (panel, test); applied at the start of every
    tick, the only place where the world changes from outside the physics.
    `scenario`: puts its due commands on that queue first (one is made if none is given);
    its conditions see the PLC status of the previous tick.
    """
    # one plant writer per PLC: a second simulator / test script stops here (plant_lock.py)
    if syrup_plc is not None and plant.syrup is None:
        raise ValueError("a syrup PLC link needs a plant with a syrup room: Plant(with_syrup=True)")
    if labeler_plc is not None and plant.labeler is None:
        raise ValueError("a labeler PLC link needs a plant with the labeler: Plant(with_labeler=True)")
    if labeler_plc is None and plant.labeler is not None:
        raise ValueError("a plant with the labeler needs its PLC link (labeler_plc=): nobody else drives C2")
    if packer_plc is not None and plant.packer is None:
        raise ValueError("a packer PLC link needs a plant with the packer: Plant(with_labeler=True, with_packer=True)")
    if packer_plc is None and plant.packer is not None:
        raise ValueError("a plant with the packer needs its PLC link (packer_plc=): nobody else drives C3")
    if blowmoulder_plc is not None and plant.blowmoulder is None:
        raise ValueError("a blow moulder PLC link needs a plant with the blow moulder: Plant(with_blowmoulder=True)")
    if blowmoulder_plc is None and plant.blowmoulder is not None:
        raise ValueError("a plant with the blow moulder needs its PLC link (blowmoulder_plc=): nobody else makes "
                         "the bottles")
    plant_lock.acquire(plc.host, plc.port, "simulator loop")
    if syrup_plc is not None:
        plant_lock.acquire(syrup_plc.host, syrup_plc.port, "simulator loop")
    if labeler_plc is not None:
        plant_lock.acquire(labeler_plc.host, labeler_plc.port, "simulator loop")
    if packer_plc is not None:
        plant_lock.acquire(packer_plc.host, packer_plc.port, "simulator loop")
    if blowmoulder_plc is not None:
        plant_lock.acquire(blowmoulder_plc.host, blowmoulder_plc.port, "simulator loop")
    stats = LoopStats()
    watcher = PlcWatcher()
    filler = LinkTrack(plc, "PLC")
    syrup = LinkTrack(syrup_plc, "syrup PLC") if syrup_plc is not None else None
    syrup_watcher = PlcWatcher(SYRUP_STEP_NAMES, SYRUP_STATE_NAMES, "syrup: ", "BatchCount",
                               lambda status: status.batch_count, log_count=True)
    labeler = LinkTrack(labeler_plc, "labeler PLC") if labeler_plc is not None else None
    labeler_watcher = PlcWatcher(LABELER_STEP_NAMES, LABELER_STATE_NAMES, "labeler: ")
    packer = LinkTrack(packer_plc, "packer PLC") if packer_plc is not None else None
    packer_watcher = PlcWatcher(PACKER_STEP_NAMES, PACKER_STATE_NAMES, "packer: ", "PackCount",
                                lambda status: status.pack_count, wrap_step_names=PACKER_WRAP_STEP_NAMES)
    blowmoulder = LinkTrack(blowmoulder_plc, "blow moulder PLC") if blowmoulder_plc is not None else None
    blowmoulder_watcher = PlcWatcher(BLOWMOULDER_STEP_NAMES, BLOWMOULDER_STATE_NAMES, "blow moulder: ", "BottleCount",
                                     lambda status: status.bottle_count, wrap_step_names=BLOWMOULDER_MOULD_STEP_NAMES,
                                     second_attr="mould_step", second_label="mould step")
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
            due, events = scenario.due(plant, stats.status, stats.syrup_status, stats.labeler_status,
                                       stats.packer_status)
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

        # 2c. The labeler PLC: its outputs (link down: None = all OFF, C2 stands)
        labeler_out: LabelerOutputs | None = None
        if labeler is not None:
            if await labeler_plc.ensure_connected():
                try:
                    labeler_out = await labeler_plc.read_outputs()
                except PlcCommError as error:
                    labeler.problem(stats, log, plant.now, f"read:{type(error).__name__}",
                                    f"reading the labeler PLC outputs failed: {error}")
            elif not labeler.was_ok:
                labeler.problem(stats, log, plant.now, "connect",
                                f"labeler PLC {labeler_plc.host}:{labeler_plc.port} not reachable, retrying every "
                                f"{labeler_plc.reconnect_s:g} s: C2 stands, the filler will block "
                                "(run with --no-labeler to keep the labeler stand-in)")

        # 2d. The packer PLC: its outputs (link down: None = all OFF, C3 stands, the oven is off)
        packer_out: PackerOutputs | None = None
        if packer is not None:
            if await packer_plc.ensure_connected():
                try:
                    packer_out = await packer_plc.read_outputs()
                except PlcCommError as error:
                    packer.problem(stats, log, plant.now, f"read:{type(error).__name__}",
                                   f"reading the packer PLC outputs failed: {error}")
            elif not packer.was_ok:
                packer.problem(stats, log, plant.now, "connect",
                               f"packer PLC {packer_plc.host}:{packer_plc.port} not reachable, retrying every "
                               f"{packer_plc.reconnect_s:g} s: C3 stands, the labeler will block "
                               "(run with --no-packer to keep the packer stand-in)")

        # 2e. The blow moulder PLC: its outputs incl. LampPower (link down: None = all OFF, the exhaust vents)
        blowmoulder_out: BlowmoulderOutputs | None = None
        if blowmoulder is not None:
            if await blowmoulder_plc.ensure_connected():
                try:
                    blowmoulder_out = await blowmoulder_plc.read_outputs()
                except PlcCommError as error:
                    blowmoulder.problem(stats, log, plant.now, f"read:{type(error).__name__}",
                                        f"reading the blow moulder PLC outputs failed: {error}")
            elif not blowmoulder.was_ok:
                blowmoulder.problem(stats, log, plant.now, "connect",
                                    f"blow moulder PLC {blowmoulder_plc.host}:{blowmoulder_plc.port} not reachable, "
                                    f"retrying every {blowmoulder_plc.reconnect_s:g} s: no bottles onto C1, the filler "
                                    "will starve (run with --no-blowmoulder to keep the bottle supply)")

        # 3. Physics (the first tick has dt = 0: only the start state is written)
        writes, events = plant.step(out, dt, signals, labeler_out, packer_out, blowmoulder=blowmoulder_out)
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

        # 4c.+5c. The same for the labeler PLC, on its own
        if labeler is not None and labeler_plc.connected:
            labeler_writes = plant.labeler_writes()
            try:
                if labeler.last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await labeler_plc.verify_writes(labeler.last_writes)
                    if wrong:
                        labeler.problem(stats, log, plant.now, f"foreign:{wrong}",
                                        f"labeler PLC: written values changed by someone else: {', '.join(wrong)} "
                                        "(debugger force or a second writer, e.g. a test script)")
                await labeler_plc.write(labeler_writes)
                labeler.last_writes = labeler_writes
                labeler_status = await labeler_plc.read_status()
                stats.labeler_status = labeler_status
                log.extend(labeler_watcher.update(labeler_status, plant.now))
                if stats.labeler_count_start is None:
                    stats.labeler_count_start = labeler_status.good_count
                stats.labeler_count_end = labeler_status.good_count
            except PlcReplyError as error:
                labeler.problem(stats, log, plant.now, f"reply:{error}", str(error))
            except PlcCommError:
                labeler.last_writes = None

        # 4d.+5d. The same for the packer PLC, on its own
        if packer is not None and packer_plc.connected:
            packer_writes = plant.packer_writes()
            try:
                if packer.last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await packer_plc.verify_writes(packer.last_writes)
                    if wrong:
                        packer.problem(stats, log, plant.now, f"foreign:{wrong}",
                                       f"packer PLC: written values changed by someone else: {', '.join(wrong)} "
                                       "(debugger force or a second writer, e.g. a test script)")
                await packer_plc.write(packer_writes)
                packer.last_writes = packer_writes
                packer_status = await packer_plc.read_status()
                stats.packer_status = packer_status
                log.extend(packer_watcher.update(packer_status, plant.now))
                if stats.packer_count_start is None:
                    stats.packer_count_start = packer_status.pack_count
                stats.packer_count_end = packer_status.pack_count
            except PlcReplyError as error:
                packer.problem(stats, log, plant.now, f"reply:{error}", str(error))
            except PlcCommError:
                packer.last_writes = None

        # 4e.+5e. The same for the blow moulder PLC, on its own
        if blowmoulder is not None and blowmoulder_plc.connected:
            blowmoulder_writes = plant.blowmoulder_writes()
            try:
                if blowmoulder.last_writes is not None and stats.ticks % VERIFY_EVERY == 0:
                    wrong = await blowmoulder_plc.verify_writes(blowmoulder.last_writes)
                    if wrong:
                        blowmoulder.problem(stats, log, plant.now, f"foreign:{wrong}",
                                            "blow moulder PLC: written values changed by someone else: "
                                            f"{', '.join(wrong)} (debugger force or a second writer, e.g. a test script)")
                await blowmoulder_plc.write(blowmoulder_writes)
                blowmoulder.last_writes = blowmoulder_writes
                blowmoulder_status = await blowmoulder_plc.read_status()
                stats.blowmoulder_status = blowmoulder_status
                log.extend(blowmoulder_watcher.update(blowmoulder_status, plant.now))
                if stats.blowmoulder_count_start is None:
                    stats.blowmoulder_count_start = blowmoulder_status.bottle_count
                stats.blowmoulder_count_end = blowmoulder_status.bottle_count
            except PlcReplyError as error:
                blowmoulder.problem(stats, log, plant.now, f"reply:{error}", str(error))
            except PlcCommError:
                blowmoulder.last_writes = None

        # 6. Link changes
        filler.report_link(stats, log, plant.now)
        if syrup is not None:
            syrup.report_link(stats, log, plant.now)
        if labeler is not None:
            labeler.report_link(stats, log, plant.now)
        if packer is not None:
            packer.report_link(stats, log, plant.now)
        if blowmoulder is not None:
            blowmoulder.report_link(stats, log, plant.now)

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
