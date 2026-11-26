"""Blow moulder: turns PET preforms into empty bottles at the start of the line.

Part 1 (stage 1): the OVEN STATION. Part 2 (stage 2): the MOULD STATION and the dead plate onto C1.

    HOPPER ──chute──► LOADER PLACE ──Loader──► OVEN POCKET  ◄── IR lamps (LampPower %QW0, 0-100 %)
    count             Preform_At_Loader        Preform_In_Oven
    Preform_Low       (HopperFeed)             OvenTemp = pyrometer: the preform's surface,
    Hopper_Empty                               the pocket wall when the pocket is empty
                                                   │ gripper (Gripper) on the transfer arm (ArmToMould)
                                                   ▼ Arm_At_Oven ─── swing ───► Arm_At_Mould
    MOULD (MouldClose: Mould_Open / Mould_Closed) ◄── stretch rod (StretchRod: Rod_Up / Rod_Down)
      cavity: Bottle_In_Mould                      ◄── pre-blow 8 bar / high blow / exhaust valves
      BlowPressure_Raw (transducer, counts)
          │ Ejector
          ▼
    DEAD PLATE (one bottle) ──► C1 slot 0 (Bottle_Ejected pulse) ──► ... C1_Full = slot 1 (the filler drives C1)

    Blowmoulder.step() order:
        1. chute: while HopperFeed is ON the elevator brings the next preform; it drops onto the
           loader place after CHUTE_S, only when the place is empty and the loader is home
        2. loader: out while Loader is ON, home on the spring when OFF; at full stroke it pushes
           the waiting preform into the pocket (refused into an occupied pocket: audit)
        3. lamps: LampPower heats the preform in the pocket (or the wall when it is empty)
        4. gripper: closes / opens; a held preform is let go as soon as it opens: into an open,
           empty mould with the arm at the mould, else it drops (audit)
        5. arm: swings / returns; its first move with the gripper closed takes the preform out of
           the pocket (the hand-over: Preform_In_Oven drops now)
        6. mould: closes / opens; only fully closed it seals, an opening mould vents at once
           (audit: opened under pressure)
        7. stretch rod: down / up; reaching the bottom of a closed mould stretches the preform
        8. blow pressure: the open valves pull the cavity pressure toward their sources
        9. ejector: at full stroke the content of the open mould goes onto the dead plate (a full
           plate stops the ejector halfway: it cannot finish)
       10. dead plate: the bottle drops onto C1's slot 0 when it is free (Bottle_Ejected pulse) and
           is audited: unblown / half-blown, blown cold, overheated
       11. collision audit: the arm inside a mould that is not open, with the rod down or the ejector out

The lamps are the PLC's P controller (state-diagram.md rule 5): the simulator only turns the
power the PLC writes into heat, so the steady offset, the Kp needed to reach the window and an
oscillation at high Kp all come out of the physics, not out of code written for them.

Like the other machines, the blow moulder decides nothing: if the PLC opens the mould under
pressure, swings the arm into a closed mould or blows with the exhaust open, the physics does
it and the audit logs a warning.

Known model limits:
- One temperature per preform (its surface, what the pyrometer sees); the soak through the wall
  is the PLC's SoakTime, not modelled. A preform does not cool outside the oven: it is judged by
  its temperature at the hand-over (transfer_c).
- The pocket wall is only seen while the pocket is empty: when a preform leaves, the wall starts
  at the preform's temperature (no jump on the trend) and then follows the lamps with its own,
  slower time constant.
- A preform waiting at the loader or in the chute stays at room temperature.
- The elevator stops where it is when HopperFeed drops (the time already spent is kept).
- Moving parts move at a constant speed and are never stopped by a collision (audit only), except
  the ejector against a full dead plate. An empty closed mould holds pressure like a full one.
"""

import math
from dataclasses import dataclass

from simulator import address_map_blowmoulder as bm
from simulator import config
from simulator.bottle import BottleFactory
from simulator.conveyor import Conveyor
from simulator.events import Audit, Event

_EPSILON = 1e-9
_INT_MAX = 32767  # the transducer's raw value is a signed 16-bit register


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
    peak_c: float = config.ROOM_C  # hottest it has been (overheated audit)
    transfer_c: float | None = None  # temperature when the arm took it out of the pocket (None = never heated)
    stretched: bool = False  # the rod reached the bottom of the closed mould with it inside
    max_bar: float = 0.0  # highest blow pressure while stretched in the closed mould

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


@dataclass(frozen=True)
class BlowmoulderSensors:
    """What the blow moulder's sensors report, in the order of the I/O list (%MX0, %MX4, %MW12-13)."""

    preform_at_loader: bool
    preform_in_oven: bool
    arm_at_oven: bool
    arm_at_mould: bool
    mould_closed: bool
    mould_open: bool
    rod_up: bool
    rod_down: bool
    bottle_in_mould: bool
    bottle_ejected: bool  # pulse per bottle onto C1
    hopper_empty: bool
    preform_low: bool
    c1_full: bool  # C1 slot C1_FULL_SLOT
    oven_temp: int  # 0.1 degC
    blow_pressure_raw: int  # transducer counts, signed


def _move(pos: float, on: bool, out_s: float, back_s: float, dt: float) -> float:
    """A moving part as a position 0..1: toward 1 while its output is ON, back toward 0 when OFF."""
    if on:
        return min(1.0, pos + dt / out_s)
    return max(0.0, pos - dt / back_s)


class Hopper:
    """Preform hopper with its elevator / chute down to the loader place."""

    def __init__(self, count: int = config.HOPPER_PREFORMS) -> None:
        # True state. Public: tests, the panel and (stage 6) commands read / set them.
        self.count = count  # preforms still in the hopper (not the one in the chute or at the loader)
        self._chute_s = 0.0  # how long the elevator has run for the next preform
        self._next_id = 1

    def load(self, count: int) -> None:
        """Hand action (stage 6): the hopper is filled (or emptied) to `count` preforms."""
        self.count = count

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
    """The blow moulder: oven station (hopper, loader, oven pocket, lamps) and mould station (arm,
    gripper, mould, stretch rod, blow air, ejector), with the dead plate onto C1."""

    def __init__(self, c1: Conveyor | None = None, factory: BottleFactory | None = None,
                 hopper_preforms: int = config.HOPPER_PREFORMS) -> None:
        """`c1`: the conveyor the bottles go onto, `factory`: where their ids come from (both the plant's;
        a test may leave them out)."""
        self.c1 = c1 if c1 is not None else Conveyor("C1")
        # True state. Public: tests, the panel and (stage 6) fault commands read / set them.
        self.hopper = Hopper(hopper_preforms)
        self.at_loader: Preform | None = None  # preform on the loader place, in front of the pocket
        self.loader = 0.0  # 0 = home, 1 = full stroke
        self.pocket: Preform | None = None  # preform in the oven pocket
        self.thermal = OvenThermal()
        self.loaded = 0  # preforms pushed into the pocket
        # Mould station
        self.gripper = 0.0  # 0 = open, 1 = closed
        self.arm = 0.0  # 0 = home above the oven pocket, 1 = inside the mould
        self.mould = 0.0  # 0 = open, 1 = closed and locked
        self.rod = 0.0  # 0 = up, 1 = down
        self.ejector = 0.0  # 0 = home, 1 = full stroke
        self.held: Preform | None = None  # preform in the gripper
        self.cavity: Preform | None = None  # preform or blown bottle in the mould
        self.dead_plate: Preform | None = None  # blown bottle waiting to drop onto C1
        self.dropped: list[Preform] = []  # preforms the gripper let fall (taken away by hand)
        self.pressure_bar = 0.0  # cavity pressure
        self.high_blow_bar = config.HIGH_BLOW_BAR  # the high-blow regulator (air_low / regulator_failed change it)
        # Injected faults (stage 6), set only by commands; the PLC only sees their effect
        self.preform_stuck = False  # the loader strokes, the preform sticks in the throat: it never reaches the pocket
        self.lamps_failed = False  # lamps burnt out: no heat whatever the PLC writes (wins over lamps_stuck_on)
        self.lamps_stuck_on = False  # lamp driver welded: 100 % whatever the PLC writes
        self.arm_stuck = False  # transfer arm does not move (output ON or OFF)
        self.mould_stuck = False  # mould halves do not move
        self.ejector_stuck = False  # ejector does not move
        self.exhaust_blocked = False  # the exhaust valve opens, no air leaves (silencer blocked)
        self.pressure_wire_break = False  # the transducer's wire is cut: it reads WIRE_BREAK_RAW
        self.bottles = factory if factory is not None else BottleFactory()  # bottle ids 1, 2, 3 ... on C1
        self.ejected = 0  # bottles put onto C1
        self._ejected_timer = 0.0  # remaining time of the Bottle_Ejected pulse
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
        lamp_pct = self._lamp_pct(out.lamp_power)
        if self.lamps_stuck_on:
            lamp_pct = bm.LAMP_POWER_MAX
        if self.lamps_failed:
            lamp_pct = 0
        self.thermal.step(self.pocket, lamp_pct, dt)
        # 4.-5. Gripper and arm: the hand-over and the transfer into the mould
        events += self._move_gripper(out.gripper, dt)
        events += self._move_arm(out.arm_to_mould, dt)
        # 6.-8. Mould, stretch rod, blow air
        self._move_mould(out.mould_close, dt)
        events += self._move_rod(out.stretch_rod, dt)
        self._blow(out, dt)
        # 9.-10. Ejector, dead plate, C1
        self._ejected_timer = max(0.0, self._ejected_timer - dt)
        events += self._move_ejector(out.ejector, dt)
        events += self._drop_onto_c1()
        # 11. Collisions
        self._collision_audit()
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
        pushing = self.loader_out and self.at_loader is not None and not self.preform_stuck
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


    # --- 4: gripper -------------------------------------------------------------------
    def _move_gripper(self, closed: bool, dt: float) -> list[Event]:
        self.gripper = _move(self.gripper, closed, config.GRIPPER_S, config.GRIPPER_S, dt)
        if self.held is None or self.gripper_closed:
            return []
        preform, self.held = self.held, None
        if self.arm_at_mould and self.mould_open and self.cavity is None:
            self.cavity = preform
            return [Event.sim(self._now, f"{preform.name} into the mould")]
        self.dropped.append(preform)
        where = "the mould is not open" if self.arm_at_mould and not self.mould_open else (
            "the mould is not empty" if self.arm_at_mould else "the arm is not at the mould")
        return [Event.sim(self._now, f"{preform.name} dropped: the gripper opened and {where}", warning=True)]

    # --- 5: arm -----------------------------------------------------------------------
    def _move_arm(self, to_mould: bool, dt: float) -> list[Event]:
        was_home = self.arm_at_oven
        if not self.arm_stuck:
            self.arm = _move(self.arm, to_mould, config.ARM_SWING_S, config.ARM_RETURN_S, dt)
        if not (was_home and not self.arm_at_oven and self.gripper_closed and self.held is None):
            return []
        preform = self.take_preform()  # the hand-over: Preform_In_Oven drops now
        if preform is None:
            return []
        preform.transfer_c = preform.temp_c
        self.held = preform
        return [Event.sim(self._now, f"{preform.name} out of the oven at {preform.temp_c:.1f} degC")]

    # --- 6: mould ---------------------------------------------------------------------
    def _move_mould(self, close: bool, dt: float) -> None:
        if not self.mould_stuck:
            self.mould = _move(self.mould, close, config.MOULD_CLOSE_S, config.MOULD_OPEN_S, dt)
        if self.mould_closed:
            return
        under_pressure = self.pressure_bar > config.OPEN_SAFE_BAR
        self._audit.warn("open_pressure", under_pressure,
                         f"mould opened under {self.pressure_bar:.1f} bar")
        self.pressure_bar = 0.0  # not sealed: the air escapes at once

    # --- 7: stretch rod ---------------------------------------------------------------
    def _move_rod(self, down: bool, dt: float) -> list[Event]:
        self.rod = _move(self.rod, down, config.ROD_DOWN_S, config.ROD_UP_S, dt)
        self._audit.warn("rod_open", not self.rod_up and not self.mould_closed,
                         "stretch rod down in a mould that is not closed")
        preform = self.cavity
        if not (self.rod_down and self.mould_closed and preform is not None and not preform.stretched):
            return []
        preform.stretched = True
        return [Event.sim(self._now, f"{preform.name} stretched")]

    # --- 8: blow air ------------------------------------------------------------------
    def _blow(self, out: BlowmoulderOutputs, dt: float) -> None:
        """dp/dt = sum over the open valves of (source - p) / tau, solved exactly for one step (the valves
        are constant during it): an exponential toward the weighted mean of the sources."""
        blowing = out.pre_blow_valve or out.high_blow_valve
        self._audit.warn("blow_exhaust", out.high_blow_valve and out.exhaust_valve,
                         "high blow with the exhaust open")
        self._audit.warn("blow_open", blowing and not self.mould_closed, "blow air into a mould that is not closed")
        if not self.mould_closed:
            return  # the pressure is 0 (step 6)
        paths = []
        if out.pre_blow_valve:
            paths.append((config.PRE_BLOW_BAR, config.PRE_BLOW_TAU_S))
        if out.high_blow_valve:
            paths.append((self.high_blow_bar, config.HIGH_BLOW_TAU_S))
        if out.exhaust_valve and not self.exhaust_blocked:
            paths.append((0.0, config.EXHAUST_TAU_S))
        if paths:
            rate = sum(1.0 / tau for _, tau in paths)
            target = sum(src / tau for src, tau in paths) / rate
            self.pressure_bar = target + (self.pressure_bar - target) * math.exp(-rate * dt)
        preform = self.cavity
        if preform is not None and preform.stretched and self.rod_down:
            preform.max_bar = max(preform.max_bar, self.pressure_bar)

    # --- 9: ejector -------------------------------------------------------------------
    def _move_ejector(self, on: bool, dt: float) -> list[Event]:
        if not self.ejector_stuck:
            self.ejector = _move(self.ejector, on, config.EJECTOR_OUT_S, config.EJECTOR_RETURN_S, dt)
        self._audit.warn("eject_closed", not self.ejector_home and not self.mould_open,
                         "ejector out with the mould not open")
        blocked = self.cavity is not None and self.dead_plate is not None and self.mould_open
        if blocked and self.ejector > config.EJECTOR_BLOCKED:
            self.ejector = config.EJECTOR_BLOCKED  # the bottle on the dead plate is in the way
        self._audit.warn("plate_full", blocked and self.ejector >= config.EJECTOR_BLOCKED - _EPSILON,
                         "ejector stopped: a bottle still stands on the dead plate")
        if not (self.ejector_out and self.mould_open and self.cavity is not None):
            return []
        preform, self.cavity = self.cavity, None
        self.dead_plate = preform
        return [Event.sim(self._now, f"{preform.name} out of the mould onto the dead plate")]

    # --- 10: dead plate -> C1 ---------------------------------------------------------
    def _drop_onto_c1(self) -> list[Event]:
        if self.dead_plate is None or self.c1.occupied(0):
            return []
        bottle = self.bottles.new()
        assert self.c1.put_at_entry(bottle)
        preform, self.dead_plate = self.dead_plate, None
        self.ejected += 1
        self._ejected_timer = config.BOTTLE_EJECTED_S  # set after the run-down: visible at the end of this step
        events = [Event.bottle(self._now, bottle.id, f"blown from {preform.name}, onto C1")]
        for problem in self._bottle_problems(preform):
            events.append(Event.bottle(self._now, bottle.id, problem, warning=True))
        return events

    @staticmethod
    def _bottle_problems(preform: Preform) -> list[str]:
        """What is wrong with the bottle made from this preform (the PLC cannot see it; no reject, Q4)."""
        problems = []
        if not preform.stretched or preform.max_bar < _EPSILON:
            problems.append(f"left the blow moulder as an unblown preform ({preform.name})")
        elif preform.max_bar < config.BLOWN_BAR - _EPSILON:
            problems.append(f"half-blown: {preform.max_bar:.1f} bar (needs {config.BLOWN_BAR:.0f})")
        if preform.transfer_c is not None and preform.transfer_c < config.BLOW_MIN_C - _EPSILON:
            problems.append(f"blown cold: {preform.transfer_c:.1f} degC at the hand-over "
                            f"(needs {config.BLOW_MIN_C:.0f})")
        if preform.peak_c > config.OVERHEAT_C + _EPSILON:
            problems.append(f"overheated: {preform.peak_c:.1f} degC in the oven (crystallised above "
                            f"{config.OVERHEAT_C:.0f})")
        return problems

    # --- 11: collisions ---------------------------------------------------------------
    def _collision_audit(self) -> None:
        in_mould = self.arm > config.ARM_CLEAR + _EPSILON
        self._audit.warn("arm_mould", in_mould and not self.mould_open, "arm inside a mould that is not open")
        self._audit.warn("arm_rod", in_mould and not self.rod_up, "arm inside the mould with the stretch rod down")
        self._audit.warn("arm_ejector", in_mould and not self.ejector_home, "arm inside the mould with the ejector out")

    # --- The hand-over ----------------------------------------------------------------
    def drop_preform(self) -> None:
        """Injected fault (stage 6): the gripper loses the preform it holds (it falls beside the mould)."""
        if self.held is not None:
            self.dropped.append(self.held)
            self.held = None

    def take_preform(self) -> Preform | None:
        """The gripper takes the preform out of the pocket (None if empty). The pyrometer now sees the
        wall, which starts at the preform's temperature: no jump on the trend."""
        preform, self.pocket = self.pocket, None
        if preform is not None:
            self.thermal.wall_c = preform.temp_c
        return preform

    def sensors(self) -> BlowmoulderSensors:
        return BlowmoulderSensors(
            preform_at_loader=self.preform_at_loader,
            preform_in_oven=self.preform_in_oven,
            arm_at_oven=self.arm_at_oven,
            arm_at_mould=self.arm_at_mould,
            mould_closed=self.mould_closed,
            mould_open=self.mould_open,
            rod_up=self.rod_up,
            rod_down=self.rod_down,
            bottle_in_mould=self.bottle_in_mould,
            bottle_ejected=self.bottle_ejected,
            hopper_empty=self.hopper_empty,
            preform_low=self.preform_low,
            c1_full=self.c1_full,
            oven_temp=self.oven_temp,
            blow_pressure_raw=self.blow_pressure_raw,
        )

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

    @property
    def gripper_closed(self) -> bool:
        return self.gripper >= 1.0 - _EPSILON

    @property
    def arm_at_oven(self) -> bool:
        return self.arm <= _EPSILON

    @property
    def arm_at_mould(self) -> bool:
        return self.arm >= 1.0 - _EPSILON

    @property
    def mould_closed(self) -> bool:
        return self.mould >= 1.0 - _EPSILON

    @property
    def mould_open(self) -> bool:
        return self.mould <= _EPSILON

    @property
    def rod_up(self) -> bool:
        return self.rod <= _EPSILON

    @property
    def rod_down(self) -> bool:
        return self.rod >= 1.0 - _EPSILON

    @property
    def ejector_home(self) -> bool:
        return self.ejector <= _EPSILON

    @property
    def ejector_out(self) -> bool:
        return self.ejector >= 1.0 - _EPSILON

    @property
    def bottle_in_mould(self) -> bool:
        """Eye across the mould: a preform or a bottle is in the cavity."""
        return self.cavity is not None

    @property
    def bottle_ejected(self) -> bool:
        return self._ejected_timer > _EPSILON

    @property
    def c1_full(self) -> bool:
        return self.c1.occupied(config.C1_FULL_SLOT)

    @property
    def blow_pressure_raw(self) -> int:
        """BlowPressure_Raw: transducer counts, RAW_FULL at 40.0 bar (PRESS_FULL in 0.1 bar), signed INT."""
        if self.pressure_wire_break:
            return config.WIRE_BREAK_RAW  # a cut 4-20 mA loop reads below the live zero
        raw = round(self.pressure_bar * bm.RAW_FULL * 10 / bm.PRESS_FULL)
        return max(-_INT_MAX - 1, min(raw, _INT_MAX))
