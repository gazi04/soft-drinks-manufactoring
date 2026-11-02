"""Conveyor: a belt divided into slots, one bottle per slot.

    entry                                   exit
    slot 0   1     2     3     4     5     6   7
    [ b ][   ][ b ][   ][   ][ b ][ b ][ b ]  ──► consumer (door, labeler)
      ▲
    producer (supply, filler discharge)

While the motor runs, the belt moves one slot every `step_s` seconds. A bottle
moves forward only if the slot in front of it is free, so bottles queue up
(accumulate) at the exit when nobody takes them, exactly like on a real
accumulation conveyor. The exit bottle never falls off on its own: the consumer
takes it.

A sensor is simply "is this slot occupied?" (`occupied(slot)`). Which slot a
sensor watches (S2 = C1 exit, S7 = a C2 slot near the entry) is decided where
the plant is built, not here.

Known model limit: bottles that touch each other and move keep a sensor ON as if
they stood still (a real sensor sees a small gap between two bottles).
"""

from simulator import config
from simulator.bottle import Bottle

# Floating point: adding 0.1 eight times gives 0.7999999999999999, not 0.8, so
# with a 0.8 s slot step the step would come one tick late. The step timer is
# therefore compared with a tiny tolerance.
_EPSILON = 1e-9


class Conveyor:
    def __init__(
        self,
        name: str,
        n_slots: int = config.CONVEYOR_SLOTS,
        step_s: float = config.SLOT_STEP_S,
    ) -> None:
        self.name = name
        self.step_s = step_s
        self.slots: list[Bottle | None] = [None] * n_slots  # 0 = entry, -1 = exit
        self.motor_on = False  # last motor input, for the HMI roller animation
        self._phase = 0.0  # time the belt has run since its last slot step

    # --- Physics ---------------------------------------------------------------
    def step(self, motor_on: bool, dt: float) -> None:
        """Advance the belt by dt seconds with the motor ON or OFF."""
        self.motor_on = motor_on
        if not motor_on:
            return  # a stopped belt keeps its position (phase is kept)
        self._phase += dt
        # 'while', not 'if': after a long dt (laptop stall) several steps are due
        while self._phase >= self.step_s - _EPSILON:
            self._phase -= self.step_s
            self._move_one_slot()

    def _move_one_slot(self) -> None:
        # Walk from the exit back to the entry. Each bottle moves into the slot its
        # front neighbour has just left, so a touching queue moves as one, like
        # bottles riding on the same belt. The exit bottle (last slot) stays.
        for i in range(len(self.slots) - 2, -1, -1):
            if self.slots[i] is not None and self.slots[i + 1] is None:
                self.slots[i + 1] = self.slots[i]
                self.slots[i] = None

    # --- Producer and consumer -------------------------------------------------
    def put_at_entry(self, bottle: Bottle) -> bool:
        """Place a bottle on the entry slot. False if the slot is taken (caller decides)."""
        if self.slots[0] is not None:
            return False
        self.slots[0] = bottle
        return True

    @property
    def exit_bottle(self) -> Bottle | None:
        """The bottle waiting at the exit, without removing it (e.g. the door looks first)."""
        return self.slots[-1]

    def take_at_exit(self) -> Bottle | None:
        """Remove and return the bottle at the exit (None if there is none).

        If the next bottle stood right behind it (a queue), that bottle was standing
        still while the belt slid under it. It starts from rest now, so it needs a
        full slot step to reach the exit: the step timer starts again from 0.
        Without this, the next bottle could reach the exit at once, which made a
        correct 0.5 s door pulse admit two bottles when a tick came late.
        """
        bottle = self.slots[-1]
        self.slots[-1] = None
        if bottle is not None and self.slots[-2] is not None:
            self._phase = 0.0
        return bottle

    # --- Sensors and display ---------------------------------------------------
    def occupied(self, slot: int) -> bool:
        """Presence / full sensor on one slot (negative index allowed: -1 = exit)."""
        return self.slots[slot] is not None

    def slot_bits(self) -> int:
        """One bit per slot (bit 0 = entry), for the HMI animation word."""
        bits = 0
        for i, bottle in enumerate(self.slots):
            if bottle is not None:
                bits |= 1 << i
        return bits

    @property
    def count(self) -> int:
        """Number of bottles on the belt."""
        return sum(1 for bottle in self.slots if bottle is not None)
