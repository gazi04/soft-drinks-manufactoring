"""Events and the event log.

The physics never prints. Whenever something happens (a bottle enters the
filler, the PLC changes its step, a bottle leaves uncapped), it creates an
Event. The EventLog collects them; the console, the log file and later the
control panel only display what is in the log.

Event sources:
    bottle  - something happened to one bottle ("Bottle 192: filled 500 ml")
    plc     - a change read from the PLC ("step 10 -> 20"); the simulator only
              reports it, the decision was made by the PLC
    sim     - the simulator itself (connected, fault injected, ...)

Severity:
    info     - normal operation
    warning  - audit warning: the physics shows that something went wrong
               (spill, crash, bottle left uncapped or underfilled)
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Source(StrEnum):
    BOTTLE = "bottle"
    PLC = "plc"
    SIM = "sim"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class Event:
    time_s: float  # simulation time in seconds since the start
    source: Source
    text: str
    bottle_id: int | None = None  # only for bottle events
    severity: Severity = Severity.INFO

    # --- Short constructors, so the physics code stays readable ---------------
    @classmethod
    def bottle(cls, time_s: float, bottle_id: int, text: str, warning: bool = False) -> "Event":
        severity = Severity.WARNING if warning else Severity.INFO
        return cls(time_s, Source.BOTTLE, text, bottle_id, severity)

    @classmethod
    def plc(cls, time_s: float, text: str, warning: bool = False) -> "Event":
        severity = Severity.WARNING if warning else Severity.INFO
        return cls(time_s, Source.PLC, text, None, severity)

    @classmethod
    def sim(cls, time_s: float, text: str, warning: bool = False) -> "Event":
        severity = Severity.WARNING if warning else Severity.INFO
        return cls(time_s, Source.SIM, text, None, severity)

    @property
    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def format(self) -> str:
        """One log line, e.g. '   12.3 s  Bottle 192   entered filler'."""
        if self.source == Source.BOTTLE:
            who = f"Bottle {self.bottle_id}"
        else:
            who = self.source.value.upper()  # "PLC" or "SIM"
        mark = "!" if self.is_warning else " "
        return f"{self.time_s:7.1f} s {mark} {who:<12} {self.text}"


class EventLog:
    """Collects events: the last `max_events` in memory, optionally all in a file.

    The memory limit keeps a long run from filling the RAM; the warning
    counter counts over the whole run, so "0 warnings after 1000 bottles"
    can still be checked when older events have been dropped.
    """

    def __init__(
        self,
        max_events: int = 1000,
        file_path: Path | None = None,
        echo: Callable[[Event], None] | None = None,
    ) -> None:
        self._events: deque[Event] = deque(maxlen=max_events)
        self.warning_count = 0
        self._file = open(file_path, "a", encoding="utf-8") if file_path else None
        self._echo = echo  # e.g. print each event to the console (headless mode)

    def add(self, event: Event) -> None:
        self._events.append(event)  # the oldest event drops out when full
        if event.is_warning:
            self.warning_count += 1
        if self._file:
            self._file.write(event.format() + "\n")
            self._file.flush()  # the line is on disk even if the simulator crashes
        if self._echo:
            self._echo(event)

    def extend(self, events: list[Event]) -> None:
        for event in events:
            self.add(event)

    def recent(self, n: int) -> list[Event]:
        """The last n events, oldest first."""
        return list(self._events)[-n:] if n > 0 else []

    def filter(
        self,
        source: Source | None = None,
        bottle_id: int | None = None,
        warnings_only: bool = False,
    ) -> list[Event]:
        """Events kept in memory that match every given condition."""
        result = []
        for event in self._events:
            if source is not None and event.source != source:
                continue
            if bottle_id is not None and event.bottle_id != bottle_id:
                continue
            if warnings_only and not event.is_warning:
                continue
            result.append(event)
        return result

    def __len__(self) -> int:
        return len(self._events)

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
