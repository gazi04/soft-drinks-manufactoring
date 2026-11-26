"""Blow moulder: turns PET preforms into empty bottles at the start of the line.

Part 1 (stage 1): the OVEN STATION. Part 2 (stage 2): the mould station (transfer arm, mould,
stretch rod, blow pressure, ejector, dead plate onto C1).

    HOPPER ──chute──► LOADER PLACE ──Loader──► OVEN POCKET  ◄── IR lamps (LampPower %QW0, 0-100 %)
    count             Preform_At_Loader        Preform_In_Oven
    Preform_Low       (HopperFeed)             OvenTemp = pyrometer: the preform's surface,
    Hopper_Empty                               the pocket wall when the pocket is empty

    Blowmoulder.step() order:
        1. chute: while HopperFeed is ON the elevator brings the next preform; it drops onto the
           loader place after CHUTE_S, only when the place is empty and the loader is home
        2. loader: out while Loader is ON, home on the spring when OFF; at full stroke it pushes
           the waiting preform into the pocket (refused into an occupied pocket: audit)
        3. lamps: LampPower heats the preform in the pocket (or the wall when it is empty)

The lamps are the PLC's P controller (state-diagram.md rule 5): the simulator only turns the
power the PLC writes into heat, so the steady offset, the Kp needed to reach the window and an
oscillation at high Kp all come out of the physics, not out of code written for them.

Like the other machines, the blow moulder decides nothing: if the PLC pushes a preform into an
occupied pocket or overheats one, the physics does it and the audit logs a warning.

Known model limits:
- One temperature per preform (its surface, what the pyrometer sees); the soak through the wall
  is the PLC's SoakTime, not modelled.
- The pocket wall is only seen while the pocket is empty: when a preform leaves, the wall starts
  at the preform's temperature (no jump on the trend) and then follows the lamps with its own,
  slower time constant.
- A preform waiting at the loader or in the chute stays at room temperature.
- The elevator stops where it is when HopperFeed drops (the time already spent is kept).
"""

import math
from dataclasses import dataclass

from simulator import address_map_blowmoulder as bm
from simulator import config
from simulator.events import Audit, Event

_EPSILON = 1e-9


@dataclass
class BlowmoulderOutputs:
    """PLC outputs %QX0.0-%QX1.1 (coils 0-9) in coil order, plus the analog output LampPower (%QW0 = HR 0)."""

    hopper_feed: bool = False
    loader: bool = False
    arm_to_mould: bool = False
    gripper: bool = False
    mould_close: bool = False
    stretch_rod: bool = False
    pre_blow_valve: bool = False
    high_blow_valve: bool = False
    exhaust_valve: bool = False
    ejector: bool = False
    lamp_power: int = 0  # % (0-100)


@dataclass
class Preform:
    """A PET preform on its way through the oven. It becomes a Bottle (new id) at the ejector (stage 2)."""

    id: int
    temp_c: float = config.ROOM_C  # surface temperature
    peak_c: float = config.ROOM_C  # hottest it has been (stage 2: overheated audit)

    @property
    def name(self) -> str:
        """Name used in the event log, e.g. 'Preform 3'."""
        return f"Preform {self.id}"


def _first_order(temp_c: float, target_c: float, tau_s: float, dt: float) -> float:
    """dT/dt = (target - T) / tau, solved exactly for one step with a constant target.

    Same method as the labeler's MeltTank and the packer's Oven: it cannot overshoot, and one late
    0.3 s step gives the same result as three 0.1 s steps.
    """
    return target_c + (temp_c - target_c) * math.exp(-dt / tau_s)


class Hopper:
    """Preform hopper with its elevator / chute down to the loader place."""

    def __init__(self, count: int = config.HOPPER_PREFORMS) -> None:
        # True state. Public: tests, the panel and (stage 6) commands read / set them.
        self.count = count  # preforms still in the hopper (not the one in the chute or at the loader)
        self._chute_s = 0.0  # how long the elevator has run for the next preform
        self._next_id = 1

    def step(self, feed_on: bool, place_free: bool, dt: float) -> Preform | None:
        """The next preform when it reaches the loader place in this step, else None.

        The elevator runs only while HopperFeed is ON, there is a preform left and the loader place
        is free (else the preforms only back up in the chute)."""
        if not feed_on or not place_free or self.count == 0:
            return None
        self._chute_s += dt
        if self._chute_s < config.CHUTE_S - _EPSILON:
            return None
        self._chute_s = 0.0
        self.count -= 1
        preform = Preform(self._next_id)
        self._next_id += 1
        return preform

    @property
    def hopper_empty(self) -> bool:
        return self.count == 0

    @property
    def preform_low(self) -> bool:
        return self.count <= config.PREFORM_LOW


class OvenThermal:
    """The IR lamps heating the oven pocket: the preform in it, or the pocket wall when it is empty."""

    def __init__(self, wall_c: float = config.ROOM_C) -> None:
        self.wall_c = wall_c

    @staticmethod
    def balance_c(lamp_pct: float) -> float:
        """Temperature the lamps hold at this power (room + gain x power)."""
        return config.ROOM_C + config.LAMP_GAIN_C_PER_PCT * lamp_pct

    def step(self, preform: Preform | None, lamp_pct: float, dt: float) -> None:
        target = self.balance_c(lamp_pct)
        if preform is None:
            self.wall_c = _first_order(self.wall_c, target, config.POCKET_WALL_TAU_S, dt)
            return
        preform.temp_c = _first_order(preform.temp_c, target, config.PREFORM_TAU_S, dt)
        preform.peak_c = max(preform.peak_c, preform.temp_c)


class Blowmoulder:
    """Oven station of the blow moulder (part 1): hopper, loader, oven pocket and lamps."""

    def __init__(self, hopper_preforms: int = config.HOPPER_PREFORMS) -> None:
        # True state. Public: tests, the panel and (stage 6) fault commands read / set them.
        self.hopper = Hopper(hopper_preforms)
        self.at_loader: Preform | None = None  # preform on the loader place, in front of the pocket
        self.loader = 0.0  # 0 = home, 1 = full stroke
        self.pocket: Preform | None = None  # preform in the oven pocket
        self.thermal = OvenThermal()
        self.loaded = 0  # preforms pushed into the pocket
        self._now = 0.0
        self._audit = Audit()

    def step(self, out: BlowmoulderOutputs, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        events: list[Event] = []
        # 1. Chute
        events += self._chute(out.hopper_feed, dt)
        # 2. Loader
        self._move_loader(out.loader, dt)
        events += self._load()
        # 3. Lamps
        self.thermal.step(self.pocket, self._lamp_pct(out.lamp_power), dt)
        return events + self._audit.events

    # --- 1: chute ---------------------------------------------------------------------
    def _chute(self, feed_on: bool, dt: float) -> list[Event]:
        """The loader's pusher face covers the place while it is out: a preform only drops with it home."""
        place_free = self.at_loader is None and self.loader_home
        preform = self.hopper.step(feed_on, place_free, dt)
        if preform is None:
            return []
        self.at_loader = preform
        return [Event.sim(self._now, f"{preform.name} at the loader ({self.hopper.count} left in the hopper)")]

    # --- 2: loader --------------------------------------------------------------------
    def _move_loader(self, valve_on: bool, dt: float) -> None:
        if valve_on:
            self.loader = min(1.0, self.loader + dt / config.LOADER_OUT_S)
        else:
            self.loader = max(0.0, self.loader - dt / config.LOADER_RETURN_S)

    def _load(self) -> list[Event]:
        """At full stroke the waiting preform goes into the pocket, never into an occupied one."""
        pushing = self.loader_out and self.at_loader is not None
        refused = pushing and self.pocket is not None
        self._audit.warn("pocket_full", refused, "loader pushed a preform against the preform in the oven pocket")
        if not pushing or refused:
            return []
        self.pocket, self.at_loader = self.at_loader, None
        self.loaded += 1
        return [Event.sim(self._now, f"{self.pocket.name} into the oven pocket")]

    # --- 3: lamps ---------------------------------------------------------------------
    def _lamp_pct(self, lamp_power: int) -> int:
        """LampPower as the lamp driver takes it: 0-100 %. The PLC clamps it (LIMIT); a value outside is a bug."""
        inside = 0 <= lamp_power <= bm.LAMP_POWER_MAX
        self._audit.warn("lamp_range", not inside, f"LampPower {lamp_power} % outside 0-{bm.LAMP_POWER_MAX} %")
        return min(max(lamp_power, 0), bm.LAMP_POWER_MAX)

    # --- The arm's side of the hand-over (stage 2) ------------------------------------
    def take_preform(self) -> Preform | None:
        """The gripper takes the preform out of the pocket (None if empty). The pyrometer now sees the
        wall, which starts at the preform's temperature: no jump on the trend."""
        preform, self.pocket = self.pocket, None
        if preform is not None:
            self.thermal.wall_c = preform.temp_c
        return preform

    # --- Sensors ----------------------------------------------------------------------
    @property
    def preform_at_loader(self) -> bool:
        return self.at_loader is not None

    @property
    def preform_in_oven(self) -> bool:
        return self.pocket is not None

    @property
    def oven_temp(self) -> int:
        """OvenTemp in 0.1 degC (pyrometer): the preform's surface, the pocket wall when it is empty."""
        temp_c = self.pocket.temp_c if self.pocket is not None else self.thermal.wall_c
        return round(temp_c * 10)

    @property
    def hopper_empty(self) -> bool:
        return self.hopper.hopper_empty

    @property
    def preform_low(self) -> bool:
        return self.hopper.preform_low

    @property
    def loader_home(self) -> bool:
        return self.loader <= _EPSILON

    @property
    def loader_out(self) -> bool:
        return self.loader >= 1.0 - _EPSILON
