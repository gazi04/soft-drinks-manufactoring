"""Stand-ins: the filler's neighbours, until the real machines exist.

    Supply ──► C1 ──► FILLER ──► C2 ──► LabelerStandIn
    (blow moulder)                       (labeler)

    + wiring values from other machines: Product_Available (syrup room),
      E_Stop_OK (E-stop chain), Heartbeat (the simulator is alive)

A stand-in only does what the real neighbour would do physically: put bottles
on C1, take bottles off C2, or hold a signal. It makes no decisions for the
filler. Every switch is a plain public attribute (`paused`, `running`,
`enabled`, ...) so the control panel's command queue (stage 7) only has to set
attributes.
"""

from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.events import Event

_EPSILON = 1e-9  # float tolerance for timers, as in conveyor.py


class Supply:
    """Puts new bottles onto C1's entry (replaces the blow moulder).

    Two ways to get bottles:
    - a rate in bottles per minute (`per_min`, 0 = none);
    - `add(n)`: n extra bottles, each placed as soon as the entry is free.

    When the entry slot is taken (C1 full up to its entry), the supply waits;
    it never drops a bottle. The real blow moulder does the same: S1 (C1 full)
    is wired to it and it stops itself.
    """

    def __init__(self, c1: Conveyor, factory: BottleFactory, per_min: float = config.SUPPLY_PER_MIN) -> None:
        self.c1 = c1
        self.factory = factory
        self.per_min = per_min
        self.paused = False
        self.pending = 0  # bottles from add(n) not yet placed
        self._timer = 0.0  # time since the last bottle from the rate

    def add(self, n: int) -> None:
        self.pending += n

    def step(self, dt: float, now: float) -> list[Event]:
        if self.paused:
            return []

        # Rate supply: one bottle every 60 / per_min seconds
        rate_due = False
        if self.per_min > 0:
            interval = 60.0 / self.per_min  # computed every step: a new rate counts at once
            self._timer = min(self._timer + dt, interval)  # held at the interval while waiting
            rate_due = self._timer >= interval - _EPSILON

        if not (rate_due or self.pending > 0):
            return []
        if self.c1.slots[0] is not None:
            return []  # entry taken: wait (the bottle stays "in the blow moulder")

        bottle = self.factory.new()
        self.c1.put_at_entry(bottle)
        if self.pending > 0:
            self.pending -= 1
        else:
            self._timer = 0.0
        return [Event.bottle(now, bottle.id, "put on C1")]


class LabelerStandIn:
    """Drives C2 and takes one bottle from its exit every `take_s` seconds.

    `running = False` (labeler stopped): no bottle is taken, so the bottles queue
    up on C2 until S7 sees them standing still. C2's motor keeps running, like an
    accumulation conveyor in front of a stopped machine.
    """

    def __init__(self, c2: Conveyor, take_s: float = config.LABELER_TAKE_S) -> None:
        self.c2 = c2
        self.take_s = take_s
        self.running = True
        self.taken = 0  # bottles taken off C2 since the start
        self._timer = take_s  # ready to take the first bottle at once

    @property
    def motor_on(self) -> bool:
        return True  # the labeler drives C2 all the time

    def step(self, dt: float, now: float) -> list[Event]:
        if not self.running:
            return []
        self._timer = min(self._timer + dt, self.take_s)
        if self._timer < self.take_s - _EPSILON or self.c2.exit_bottle is None:
            return []
        bottle = self.c2.take_at_exit()
        self._timer = 0.0
        self.taken += 1
        return [Event.bottle(now, bottle.id, "taken by labeler")]


class Heartbeat:
    """A bit that toggles every `period_s` seconds; the PLC's watchdog checks that it changes.

    `enabled = False` freezes it (fault injection: the PLC raises Alm_CommLost after 3 s).
    """

    def __init__(self, period_s: float = config.HEARTBEAT_S) -> None:
        self.period_s = period_s
        self.enabled = True
        self.value = False
        self._timer = 0.0

    def step(self, dt: float) -> bool:
        if self.enabled:
            self._timer += dt
            while self._timer >= self.period_s - _EPSILON:
                self._timer -= self.period_s
                self.value = not self.value
        return self.value
