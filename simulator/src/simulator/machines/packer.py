"""Packer: collates 6 bottles into a 2x3 group, wraps it in shrink film, shrinks it in an oven.

Part 1 (stage 1): the COLLATION. Part 2 (stage 3): the OVEN and the OUTFEED TABLE.
The wrap conveyor between them (stage 2) is not built yet: packs pushed off the collation
table wait in `wrap_packs`, and `Oven.put_at_entry()` is the door the wrap conveyor will use.

    C3 exit ─► GATE (GateMotor, held open) ─► count sensor (Bottle_Counted) ─► COLLATION TABLE (2x3)
                                                                                 │ GroupPusher (spring return)
                                                                                 ▼ Pusher_Home / Pusher_Out
                                                                    WRAP CONVEYOR entry (stage 2 moves the packs)

    Packer.step() order:
        1. the Bottle_Counted pulse runs down
        2. the bottle in the gate lands on the table (audit: 7th bottle, pusher not home)
        3. gate: a bottle at C3's exit goes through the open gate, new Bottle_Counted pulse
        4. pusher moves: out while the valve is ON, home on the spring when OFF
        5. pusher fully out: the bottles on the table become one Pack on the wrap conveyor
        6. oven: blower, air temperature, oven conveyor, packs onto the outfeed table

    (wrap conveyor) ─► Oven.put_at_entry ─► OVEN CONVEYOR ─────────────────► OUTFEED TABLE ─► palletizer
                       Oven_Entry eye at 0 mm   heater, blower   Oven_Exit eye  (Table_Full)    (stand-in)
                                                OvenTemp, Airflow_OK

The gate is HELD open by the PLC while the group is not complete (not a pulse like the
labeler's gate). It takes a bottle as soon as one stands at C3's exit. Why there is never a
7th bottle: the next bottle of a queue needs a full slot step (0.5 s, see
Conveyor.take_at_exit) to reach the exit, and the PLC shuts the gate 0.1-0.2 s after it saw the
6th count edge. That is also why one `entering` slot is enough: a bottle is on the table
0.4 s after it left C3, before the next one can follow.

Like the other machines, the packer decides nothing: if the PLC opens the gate for a 7th
bottle or pushes an incomplete group, the physics does it and the audit logs a warning.

Known model limits:
- Bottles cross the gate even if C3's motor is off (as the labeler's gate).
- A bottle past the gate always reaches the table, even if the gate shuts behind it.
- Bottle_Counted is 0.2 s long, longer than one sub-step (SUBSTEP_S); the plant keeps it ON
  for a whole tick if it was ON at any sub-step (stage 4), like the labeler's Label_Mark.
- One air temperature for the whole oven (no zones); the heater's speed does not depend on
  the packs inside. The heater heats only while air flows: without air the heat stays in the
  coils (the blower's run-on exists to carry it away, PLC rule 7).
- A pack shrinks by time above SHRINK_C, not by a heat balance of the film.
"""

import math
from dataclasses import dataclass

from simulator import config
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Audit, Event

_EPSILON = 1e-9
# The front of an oven pack is here when its tail has passed the exit eye: it goes onto the outfeed table.
_EXIT_LEAVE_MM = config.OVEN_LENGTH_MM + config.PACK_LENGTH_MM


@dataclass
class PackerOutputs:
    """PLC outputs %QX0.0-%QX1.1 (coils 0-9), in coil order."""

    conveyor_motor3: bool = False
    gate_motor: bool = False
    group_pusher: bool = False
    wrap_conveyor: bool = False
    film_feed: bool = False
    knife: bool = False
    wrap_bar: bool = False
    oven_heater: bool = False
    oven_blower: bool = False
    oven_conveyor: bool = False


@dataclass
class Pack:
    """A group of bottles pushed off the collation table; later stages wrap and shrink it."""

    id: int
    bottles: list[Bottle]
    front_mm: float = 0.0  # position of its front on the conveyor it is on (oven: 0 = entry eye)
    hot_s: float = 0.0  # seconds in the oven with the air at SHRINK_C or hotter
    shrunk: bool = False

    @property
    def name(self) -> str:
        """Name used in the event log, e.g. 'Pack 3 (bottles 13-18)'."""
        if not self.bottles:
            return f"Pack {self.id} (empty)"
        return f"Pack {self.id} (bottles {self.bottles[0].id}-{self.bottles[-1].id})"


class Packer:
    """Gate, collation table and group pusher behind C3 (part 1 of the packer)."""

    def __init__(self, c3: Conveyor) -> None:
        self.c3 = c3
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.entering: Bottle | None = None  # the bottle between the gate and the table
        self.table: list[Bottle] = []  # bottles on the collation table, in arrival order
        self.pusher = 0.0  # 0 = home, 1 = fully out
        self.wrap_packs: list[Pack] = []  # packs pushed onto the wrap conveyor's entry (stage 2 moves them)
        self.groups_pushed = 0
        self._gate_timer = 0.0  # how long the entering bottle has been on its way to the table
        self._count_timer = 0.0  # remaining time of the Bottle_Counted pulse
        self._pushed_this_stroke = False  # the group of this push is already on the wrap conveyor
        self._next_pack_id = 1
        self._now = 0.0
        self._audit = Audit()
        self.outfeed = OutfeedTable()
        self.oven = Oven(self.outfeed)

    def step(self, out: PackerOutputs, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        events: list[Event] = []
        # 1. The count pulse runs down (before a new bottle may restart it)
        self._count_timer = max(0.0, self._count_timer - dt)
        # 2.-3. Bottle movement
        events += self._land(dt)
        events += self._gate(out.gate_motor)
        # 4.-5. Pusher
        self._move_pusher(out.group_pusher, dt)
        events += self._push_group()
        # 6. Oven and outfeed table
        events += self.oven.step(out.oven_heater, out.oven_blower, out.oven_conveyor, dt, now)
        return events + self._audit.events

    # --- 2: the bottle in the gate lands on the table ----------------------------------
    def _land(self, dt: float) -> list[Event]:
        if self.entering is None:
            return []
        self._gate_timer += dt
        if self._gate_timer < config.PACKER_GATE_S - _EPSILON:
            return []
        bottle, self.entering = self.entering, None
        self._audit.warn("pusher_out", not self.pusher_home, "bottle onto the collation table with the pusher out")
        self.table.append(bottle)
        n = len(self.table)
        self._audit.warn("seventh", n > config.PACK_BOTTLES, f"{n} bottles on the collation table (room for "
                                                            f"{config.PACK_BOTTLES})")
        return [Event.bottle(self._now, bottle.id, f"on the collation table ({n}/{config.PACK_BOTTLES})")]

    # --- 3: gate ----------------------------------------------------------------------
    def _gate(self, gate_open: bool) -> list[Event]:
        if not gate_open or self.entering is not None or self.c3.exit_bottle is None:
            return []
        self.entering = self.c3.take_at_exit()
        self._gate_timer = 0.0
        self._count_timer = config.BOTTLE_COUNTED_S  # set after step 1: visible at the end of this step
        return [Event.bottle(self._now, self.entering.id, "through the packer gate")]

    # --- 4: pusher --------------------------------------------------------------------
    def _move_pusher(self, valve_on: bool, dt: float) -> None:
        if valve_on:
            self.pusher = min(1.0, self.pusher + dt / config.PUSH_OUT_S)
        else:
            self.pusher = max(0.0, self.pusher - dt / config.PUSH_RETURN_S)
        if self.pusher_home:
            self._pushed_this_stroke = False  # home again: the next stroke may push a new group

    # --- 5: the group onto the wrap conveyor ------------------------------------------
    def _push_group(self) -> list[Event]:
        """Only a full stroke moves the group: a push stopped halfway leaves it on the table."""
        if not self.pusher_out or self._pushed_this_stroke or not self.table:
            return []
        self._pushed_this_stroke = True
        pack = Pack(self._next_pack_id, self.table)
        self._next_pack_id += 1
        self.table = []
        self.wrap_packs.append(pack)
        self.groups_pushed += 1
        n = len(pack.bottles)
        events = [Event.sim(self._now, f"{pack.name} pushed onto the wrap conveyor")]
        if n != config.PACK_BOTTLES:
            events.append(Event.sim(self._now, f"{pack.name} pushed with {n} bottles, not {config.PACK_BOTTLES}",
                                    warning=True))
        return events

    # --- Sensors ----------------------------------------------------------------------
    @property
    def bottle_at_gate(self) -> bool:
        """A bottle waits at C3's exit, in front of the gate."""
        return self.c3.exit_bottle is not None

    @property
    def bottle_counted(self) -> bool:
        return self._count_timer > _EPSILON

    @property
    def pusher_home(self) -> bool:
        return self.pusher <= _EPSILON

    @property
    def pusher_out(self) -> bool:
        return self.pusher >= 1.0 - _EPSILON


class OutfeedTable:
    """The table behind the oven: finished packs wait here for the palletizer."""

    def __init__(self) -> None:
        self.packs: list[Pack] = []  # oldest first

    @property
    def has_room(self) -> bool:
        return len(self.packs) < config.TABLE_PACKS

    @property
    def table_full(self) -> bool:
        """Table_Full sensor: placed so that the packs already past READY still fit behind it (Q3)."""
        return len(self.packs) >= config.TABLE_FULL_PACKS

    def take(self) -> Pack | None:
        """The palletizer takes the oldest pack (None if the table is empty)."""
        return self.packs.pop(0) if self.packs else None


class Oven:
    """Shrink tunnel: blower, heater, one air temperature and a conveyor through it.

    Oven.step() order:
        1. blower: air flows BLOWER_SPINUP_S after OvenBlower ON, stops at once when OFF
        2. air temperature (heater only with air flowing, loss to the room)
        3. oven conveyor moves the packs (front pack first, the others queue behind it)
        4. a pack whose tail passed the exit goes onto the outfeed table (audit: not shrunk)
        5. shrink time of the packs inside
    """

    def __init__(self, outfeed: OutfeedTable, temp_c: float = config.ROOM_C) -> None:
        self.outfeed = outfeed
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.temp_c = temp_c
        self.packs: list[Pack] = []  # on the oven conveyor, front pack (nearest the exit) first
        self.left = 0  # packs moved onto the outfeed table
        self._spin_s = 0.0  # how long the blower has been running
        self._now = 0.0
        self._audit = Audit()

    def step(self, heater_on: bool, blower_on: bool, conveyor_on: bool, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        # 1. Blower
        self._spin_s = self._spin_s + dt if blower_on else 0.0
        # 2. Air temperature
        self._heat(heater_on and self.air_flowing, dt)
        # 3.-4. Conveyor and outfeed
        events: list[Event] = []
        if conveyor_on:
            self._move(dt)
        events += self._leave()
        # 5. Shrink
        if self.temp_c >= config.SHRINK_C:
            for pack in self.packs:
                pack.hot_s += dt
                pack.shrunk = pack.hot_s >= config.SHRINK_S - _EPSILON
        return events + self._audit.events

    @property
    def air_flowing(self) -> bool:
        return self._spin_s >= config.BLOWER_SPINUP_S - _EPSILON

    # --- 2: air temperature -----------------------------------------------------------
    def _heat(self, heating: bool, dt: float) -> None:
        """dT/dt = -heating (T - heater) / tau_heat - (T - room) / tau_room, solved exactly.

        Same method as the labeler's MeltTank: the heater state is constant during one step, so the
        solution is an exponential toward the balance temperature. It cannot overshoot, and a late
        tick gives the same result as several short ones.
        """
        rate_heat = (1.0 / config.OVEN_HEAT_TAU_S) if heating else 0.0
        rate_room = 1.0 / config.OVEN_ROOM_TAU_S
        rate = rate_heat + rate_room
        balance = (rate_heat * config.OVEN_HEATER_C + rate_room * config.ROOM_C) / rate
        self.temp_c = balance + (self.temp_c - balance) * math.exp(-rate * dt)

    # --- 3: conveyor ------------------------------------------------------------------
    def _move(self, dt: float) -> None:
        """Every pack moves forward; it stops a pack length behind the pack ahead (no overlap), and the
        front pack stops at the exit edge (_EXIT_LEAVE_MM) until _leave() moves it onto the table."""
        limit = _EXIT_LEAVE_MM  # whether the front pack may leave is decided in _leave()
        for pack in self.packs:
            pack.front_mm = min(pack.front_mm + config.OVEN_SPEED_MM_S * dt, limit)
            limit = pack.front_mm - config.PACK_LENGTH_MM

    # --- 4: onto the outfeed table ----------------------------------------------------
    def _leave(self) -> list[Event]:
        events: list[Event] = []
        waiting = False
        while self.packs and self.packs[0].front_mm >= _EXIT_LEAVE_MM - _EPSILON:
            if not self.outfeed.has_room:
                waiting = True
                break
            pack = self.packs.pop(0)
            self.outfeed.packs.append(pack)
            self.left += 1
            events.append(Event.sim(self._now, f"{pack.name} onto the outfeed table "
                                               f"({len(self.outfeed.packs)}/{config.TABLE_PACKS})"))
            if not pack.shrunk:
                events.append(Event.sim(self._now, f"{pack.name} left the oven not shrunk ({pack.hot_s:.1f} s hot)",
                                        warning=True))
        name = self.packs[0].name if self.packs else ""
        self._audit.warn("exit_blocked", waiting, f"{name} waits at the oven exit: outfeed table full")
        return events

    # --- The door for the wrap conveyor (stage 2) -------------------------------------
    @property
    def entry_clear(self) -> bool:
        """The last pack is far enough past the entry eye for the next one (the eye is OFF >= 0.5 s)."""
        return all(p.front_mm >= config.PACK_LENGTH_MM + config.OVEN_GAP_MM - _EPSILON for p in self.packs)

    def put_at_entry(self, pack: Pack) -> bool:
        """A pack arrives at the oven entry (front at the entry eye). False if the entry is not clear:
        the caller keeps the pack and tries again."""
        if not self.entry_clear:
            return False
        pack.front_mm = 0.0
        self.packs.append(pack)
        return True

    # --- Sensors ----------------------------------------------------------------------
    @property
    def oven_temp(self) -> int:
        """OvenTemp in 0.1 degC, e.g. 1900 = 190.0 degC."""
        return round(self.temp_c * 10)

    @property
    def airflow_ok(self) -> bool:
        return self.air_flowing

    @property
    def oven_entry(self) -> bool:
        """Entry eye: ON while a pack's tail is still at the entry."""
        return any(p.front_mm < config.PACK_LENGTH_MM - _EPSILON for p in self.packs)

    @property
    def oven_exit(self) -> bool:
        """Exit eye: ON while a pack covers the exit (also a pack waiting there for room on the table)."""
        return any(p.front_mm >= config.OVEN_LENGTH_MM - _EPSILON for p in self.packs)

