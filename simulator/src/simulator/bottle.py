"""Bottles: objects that carry their own state through the line.

A bottle remembers what happened to it (how much was filled, whether it was
rinsed and capped). When it moves from one station or conveyor to the next, its
state moves with it. This makes two things possible:

- a half-filled bottle stays half-filled after an E-stop and is topped up later;
- the simulator can check every bottle that leaves the filler (audit), e.g.
  "Bottle 23 left with 320 ml" means the PLC logic has a bug.
"""

from dataclasses import dataclass


@dataclass
class Bottle:
    id: int
    level_ml: float = 0.0  # liquid in the bottle
    rinsed: bool = False
    capped: bool = False

    @property
    def name(self) -> str:
        """Name used in the event log, e.g. 'Bottle 192'."""
        return f"Bottle {self.id}"


class BottleFactory:
    """Creates bottles with increasing ids: 1, 2, 3, ...

    Each factory has its own counter, so every test (and later the plant)
    starts at 1, independent of the others.
    """

    def __init__(self) -> None:
        self._next_id = 1

    def new(self) -> Bottle:
        bottle = Bottle(id=self._next_id)
        self._next_id += 1
        return bottle
