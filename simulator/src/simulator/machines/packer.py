"""Packer: collates 6 bottles into a 2x3 group, wraps it in shrink film, shrinks it in an oven.

Part 1 (stage 1): the COLLATION. Part 2 (stage 3): the OVEN and the OUTFEED TABLE. Part 3
(stage 2, built after stage 3): the WRAP between them (`Wrapper`, `Film`).

    C3 exit ─► GATE (GateMotor, held open) ─► count sensor (Bottle_Counted) ─► COLLATION TABLE (2x3)
                                                                                 │ GroupPusher (spring return)
                                                                                 ▼ Pusher_Home / Pusher_Out
    WRAP CONVEYOR (positions = a pack's FRONT, mm):
      0 push line ─► 400 Pack_At_Film eye ─► 450 film slot ─► wrap zone 650-1050 ─► 1100 oven entry
                                    film roll ─► feed rollers (Film_Pulse) ─► sheet in the slot
                                    (Film_Low, Film_Empty)                    (Film_In_Slot), Knife
                                                                 wrap bar ↻ (WrapBar, Wrap_Home)

    Packer.step() order:
        1. the Bottle_Counted pulse runs down
        2. the bottle in the gate lands on the table (audit: 7th bottle, pusher not home)
        3. gate: a bottle at C3's exit goes through the open gate, new Bottle_Counted pulse
        4. pusher moves: out while the valve is ON, home on the spring when OFF
        5. pusher fully out: the bottles on the table become one Pack at the wrap conveyor's push line
        6. wrap: film (pulse, knife, feed), wrap bar, wrap conveyor, hand-over to the oven
        7. oven: blower, air temperature, oven conveyor, packs onto the outfeed table

    (wrap conveyor) ─► Oven.put_at_entry ─► OVEN CONVEYOR ─────────────────► OUTFEED TABLE ─► palletizer
                       Oven_Entry eye at 0 mm   heater, blower   Oven_Exit eye  (Table_Full)    (stand-in)
                                                OvenTemp, Airflow_OK

The gate is HELD open by the PLC while the group is not complete (not a pulse like the
labeler's gate). It takes a bottle as soon as one stands at C3's exit. Why there is never a
7th bottle: the next bottle of a queue needs a full slot step (0.5 s, see
Conveyor.take_at_exit) to reach the exit, and the PLC shuts the gate 0.1-0.2 s after it saw the
6th count edge. That is also why one `entering` slot is enough: a bottle is on the table
0.4 s after it left C3, before the next one can follow.

The wrap (film-slot wrapper, docs/packer/state-diagram.md rules 4-5): the pack waits at the
film stop, the rollers push film up through the slot (one Film_Pulse per 50 mm), the knife cuts
the sheet, the pack drives over the slot and takes the sheet, the wrap bar's turn lays the sheet
over the pack (at the top of the turn), and the pack runs into the oven.

Like the other machines, the packer decides nothing: if the PLC opens the gate for a 7th
bottle, pushes an incomplete group or drives a pack over an uncut sheet, the physics does it and
the audit logs a warning.

Known model limits:
- Bottles cross the gate even if C3's motor is off (as the labeler's gate).
- A bottle past the gate always reaches the table, even if the gate shuts behind it.
- Bottle_Counted (0.2 s) and Film_Pulse (0.15 s) are timed pulses; the plant passes every rising
  edge of its sub-steps on to the PLC through a PulseWire (plant.py), so a late tick merges none.
- One air temperature for the whole oven (no zones); the heater's speed does not depend on
  the packs inside. The heater heats only while air flows: without air the heat stays in the
  coils (the blower's run-on exists to carry it away, PLC rule 7).
- A pack shrinks by time above SHRINK_C, not by a heat balance of the film.
- Conveyors, feed rollers and the wrap bar stop at once (no inertia). The wrap is judged at one
  moment (the bar passes the top of its turn), not by the film's path around the pack.
- A pack takes the sheet only when its front crosses the slot; a sheet fed under a pack that
  already covers the slot stays there for the next pack.
- A pack handed to the oven starts at the oven's entry eye; the few mm it ran past 1100 are lost.
"""

import math
from dataclasses import dataclass

from simulator import address_map_packer as pm
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
    """A group of bottles pushed off the collation table, wrapped in a sheet and shrunk in the oven."""

    id: int
    bottles: list[Bottle]
    front_mm: float = 0.0  # position of its front on the conveyor it is on (wrap: 0 = push line, oven: 0 = entry eye)
    sheet_mm: float = 0.0  # film the pack took at the slot (0 = none)
    wrapped: bool = False  # the wrap bar laid the sheet over it
    hot_s: float = 0.0  # seconds in the oven with the air at SHRINK_C or hotter
    shrunk: bool = False

    @property
    def name(self) -> str:
        """Name used in the event log, e.g. 'Pack 3 (bottles 13-18)'."""
        if not self.bottles:
            return f"Pack {self.id} (empty)"
        return f"Pack {self.id} (bottles {self.bottles[0].id}-{self.bottles[-1].id})"


@dataclass(frozen=True)
class PackerSensors:
    """What the packer's sensors report, in the order of the I/O list (%MX0, %MX4, %MW12)."""

    bottle_at_gate: bool  # C3 exit slot
    bottle_counted: bool  # pulse per bottle through the gate
    pusher_home: bool
    pusher_out: bool
    pack_at_film: bool
    film_in_slot: bool
    film_pulse: bool  # pulse per 50 mm of film
    wrap_home: bool
    film_empty: bool
    film_low: bool
    oven_entry: bool
    oven_exit: bool
    table_full: bool
    airflow_ok: bool
    oven_temp: int  # 0.1 degC


class Packer:
    """Gate, collation table and group pusher behind C3 (part 1 of the packer)."""

    def __init__(self, c3: Conveyor, check_labels: bool = False) -> None:
        """`check_labels`: warn when a pack leaves the oven with a bottle without a whole label (the
        plant sets it, where the bottles come from the labeler; unit tests use unlabelled bottles)."""
        self.c3 = c3
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.entering: Bottle | None = None  # the bottle between the gate and the table
        self.table: list[Bottle] = []  # bottles on the collation table, in arrival order
        self.pusher = 0.0  # 0 = home, 1 = fully out
        self.groups_pushed = 0
        self._gate_timer = 0.0  # how long the entering bottle has been on its way to the table
        self._count_timer = 0.0  # remaining time of the Bottle_Counted pulse
        self._pushed_this_stroke = False  # the group of this push is already on the wrap conveyor
        self._next_pack_id = 1
        self.pusher_stuck = False  # injected fault (stage 7): valve ON or OFF, the cylinder stays where it is
        self._now = 0.0
        self._audit = Audit()
        self.outfeed = OutfeedTable()
        self.oven = Oven(self.outfeed, check_labels=check_labels)
        self.wrapper = Wrapper(self.oven)

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
        # 6. Wrap: film, wrap bar, wrap conveyor, hand-over to the oven
        events += self.wrapper.step(out, dt, now)
        # 7. Oven and outfeed table
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
        if self.pusher_stuck:
            return
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
        self.groups_pushed += 1
        n = len(pack.bottles)
        events = [Event.sim(self._now, f"{pack.name} pushed onto the wrap conveyor")]
        events += self.wrapper.put_at_entry(pack, self._now)
        if n != config.PACK_BOTTLES:
            events.append(Event.sim(self._now, f"{pack.name} pushed with {n} bottles, not {config.PACK_BOTTLES}",
                                    warning=True))
        return events

    def sensors(self) -> PackerSensors:
        w, oven = self.wrapper, self.oven
        return PackerSensors(
            bottle_at_gate=self.bottle_at_gate,
            bottle_counted=self.bottle_counted,
            pusher_home=self.pusher_home,
            pusher_out=self.pusher_out,
            pack_at_film=w.pack_at_film,
            film_in_slot=w.film.film_in_slot,
            film_pulse=w.film.film_pulse,
            wrap_home=w.wrap_home,
            film_empty=w.film.film_empty,
            film_low=w.film.film_low,
            oven_entry=oven.oven_entry,
            oven_exit=oven.oven_exit,
            table_full=self.outfeed.table_full,
            airflow_ok=oven.airflow_ok,
            oven_temp=oven.oven_temp,
        )

    @property
    def wrap_packs(self) -> list[Pack]:
        """Packs on the wrap conveyor, front pack (nearest the oven) first."""
        return self.wrapper.packs

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

    # --- HMI animation ----------------------------------------------------------------
    def anim_words(self) -> tuple[int, int, int, int]:
        """The 4 HMI animation words %MW70..73 (HMI P0, layout in address_map_packer.py):
        wrap conveyor cells, oven cells, packs on the outfeed table, sheet code."""
        length = config.PACK_LENGTH_MM
        # (tail, front) of every pack in wrap conveyor mm. A pack just handed to the oven starts with its
        # front at the entry eye, so its body still stands on the end of the wrap conveyor: it lights the
        # last wrap cells until it is inside (no gap in the animation at the hand-over).
        wrap = [(p.front_mm - length, p.front_mm) for p in self.wrapper.packs]
        wrap += [(config.OVEN_ENTRY_MM + p.front_mm - length, config.OVEN_ENTRY_MM + p.front_mm)
                 for p in self.oven.packs]
        oven = [(p.front_mm - length, p.front_mm) for p in self.oven.packs]
        return (_cell_bits(wrap, pm.WRAP_CELLS), _cell_bits(oven, pm.OVEN_CELLS), len(self.outfeed.packs),
                self._sheet_code())

    def _sheet_code(self) -> int:
        film = self.wrapper.film
        if film.fed_mm > 0:
            return pm.SHEET_FEEDING
        if film.sheet_mm > _EPSILON:
            return pm.SHEET_IN_SLOT
        if any(p.sheet_mm > _EPSILON and not p.wrapped for p in self.wrapper.packs):
            return pm.SHEET_UNDER_PACK
        return pm.SHEET_NONE


def _cell_bits(spans: list[tuple[float, float]], cells: int) -> int:
    """Bit i set when one of the (tail, front) spans covers part of cell i (ANIM_CELL_MM each, from 0).
    Parts outside the cells are not drawn."""
    bits = 0
    for tail, front in spans:
        for i in range(cells):
            if tail < (i + 1) * pm.ANIM_CELL_MM - _EPSILON and front > i * pm.ANIM_CELL_MM + _EPSILON:
                bits |= 1 << i
    return bits


class Film:
    """Film roll, feed rollers with their pulse encoder, the sheet in the slot and the knife.

    Film.step() order:
        1. the Film_Pulse runs down (before a new pulse may restart it)
        2. knife: cuts the sheet off the roll after KNIFE_CUT_S ON
        3. feed: the rollers push film up through the slot (never more than is left on the roll);
           every FILM_PULSE_MM of roller travel starts a new Film_Pulse
    """

    def __init__(self, roll_mm: float = config.FILM_ROLL_MM) -> None:
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.left_mm = roll_mm  # film still on the roll
        self.roller_mm = 0.0  # feed roller travel since the start: the encoder's position (the knife does not reset it)
        self.sheet_mm = 0.0  # film sticking out of the slot
        self.cut = False  # the film in the slot is cut off the roll
        self.pulses = 0  # Film_Pulse pulses since the start
        self.fed_mm = 0.0  # film moved in the last step (0 = the film stands)
        self.torn = False  # injected fault (stage 7): the rollers turn, the film does not move
        self._pulse_timer = 0.0  # remaining time of the Film_Pulse pulse
        self._knife_s = 0.0  # how long the knife has been ON
        self._now = 0.0
        self._audit = Audit()

    def step(self, feed_on: bool, knife_on: bool, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        events: list[Event] = []
        # 1. The pulse runs down
        self._pulse_timer = max(0.0, self._pulse_timer - dt)
        # 2. Knife
        self._knife_s = self._knife_s + dt if knife_on else 0.0
        if self._knife_s >= config.KNIFE_CUT_S - _EPSILON and not self.cut and self.sheet_mm > _EPSILON:
            self.cut = True
            events.append(Event.sim(now, f"film sheet cut ({self.sheet_mm:.0f} mm)"))
        # 3. Feed
        wanted = config.FILM_FEED_MM_S * dt if feed_on and not self.torn else 0.0
        self.fed_mm = min(wanted, self.left_mm)
        if self.fed_mm > 0:
            self._audit.warn("two_sheets", self.cut, "film fed under a cut sheet: two sheets in the slot")
            self.cut = False  # the new film and the cut sheet go out together
            before = self.roller_mm
            self.roller_mm += self.fed_mm
            self.left_mm -= self.fed_mm
            self.sheet_mm += self.fed_mm
            new_pulses = self._pulse_index(self.roller_mm) - self._pulse_index(before)
            if new_pulses > 0:
                self.pulses += new_pulses
                self._pulse_timer = config.FILM_PULSE_S  # set after step 1: visible at the end of this step
        else:
            self._audit.warn("two_sheets", False, "")
        return events + self._audit.events

    @staticmethod
    def _pulse_index(roller_mm: float) -> int:
        """Number of pulse marks the roller has passed (one every FILM_PULSE_MM)."""
        return math.floor(roller_mm / config.FILM_PULSE_MM + _EPSILON)

    def take_sheet(self) -> tuple[float, bool]:
        """A pack drives over the slot and takes the sheet: (length, cut). The slot is empty after it."""
        sheet = (self.sheet_mm, self.cut)
        self.sheet_mm = 0.0
        self.cut = False
        return sheet

    def drop_sheet(self) -> None:
        """Injected fault (stage 7): the film above the slot tears off at the slot and falls away."""
        self.sheet_mm = 0.0
        self.cut = False

    def put_sheet(self, sheet_mm: float) -> None:
        """Hand action (stage 7): the operator puts a cut sheet of `sheet_mm` back into the slot."""
        if sheet_mm < 0:
            raise ValueError(f"sheet_mm must be >= 0, got {sheet_mm}")
        self.sheet_mm = sheet_mm
        self.cut = sheet_mm > 0

    def load_roll(self, roll_mm: float = config.FILM_ROLL_MM) -> None:
        """Roll change by the operator: a new roll (a sheet already in the slot stays)."""
        if roll_mm < 0:
            raise ValueError(f"roll_mm must be >= 0, got {roll_mm}")
        self.left_mm = roll_mm

    # --- Sensors ----------------------------------------------------------------------
    @property
    def film_pulse(self) -> bool:
        return self._pulse_timer > _EPSILON

    @property
    def film_in_slot(self) -> bool:
        """The slot sensor sees film FILM_SENSOR_MM out of the slot (more than one pulse period)."""
        return self.sheet_mm >= config.FILM_SENSOR_MM - _EPSILON

    @property
    def film_low(self) -> bool:
        return self.left_mm <= config.FILM_LOW_MM + _EPSILON

    @property
    def film_empty(self) -> bool:
        """Only the tail is left: no new sheet, but a started one still finishes on the tail."""
        return self.left_mm <= config.FILM_TAIL_MM + _EPSILON


class Wrapper:
    """Wrap conveyor, film (`Film`) and wrap bar between the collation table and the oven.

    Wrapper.step() order:
        1. film: pulse, knife, feed (audits: feed with the bar out, feed under a pack)
        2. wrap bar turns while WrapBar is ON; passing the top of its turn wraps the pack in reach
        3. wrap conveyor moves the packs (front pack first, the others queue behind it); a pack
           whose front crosses the slot takes the sheet
        4. a pack whose front reached OVEN_ENTRY_MM goes into the oven (audit: not wrapped, no
           film, short sheet; held at the entry while the oven refuses it)
    """

    def __init__(self, oven: "Oven", film: Film | None = None) -> None:
        self.oven = oven
        self.film = film if film is not None else Film()
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.packs: list[Pack] = []  # on the wrap conveyor, front pack (nearest the oven) first
        self.bar = 0.0  # wrap bar position in turns, 0 <= bar < 1, 0 = the middle of home
        self.wrapped = 0  # packs wrapped
        self.bar_stuck = False  # injected fault (stage 7): WrapBar ON, the bar does not turn (film caught)
        self._now = 0.0
        self._audit = Audit()

    def put_at_entry(self, pack: Pack, now: float) -> list[Event]:
        """The pusher sets a pack down at the push line. Never refused (the pusher cannot wait):
        a pack still standing there is pushed into, which the PLC's Wrap_Clear must prevent."""
        events: list[Event] = []
        blocking = [p for p in self.packs if p.front_mm < config.PACK_LENGTH_MM - _EPSILON]
        if blocking:
            events.append(Event.sim(now, f"{pack.name} pushed onto {blocking[0].name} at the wrap conveyor's entry",
                                    warning=True))
        pack.front_mm = 0.0
        self.packs.append(pack)
        return events

    def step(self, out: PackerOutputs, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        events: list[Event] = []
        # 1. Film (the bar and the packs as they were at the start of the step)
        bar_home, covered = self.wrap_home, self.slot_covered
        events += self.film.step(out.film_feed, out.knife, dt, now)
        moving = self.film.fed_mm > 0
        self._audit.warn("feed_bar", moving and not bar_home, "film fed into the wrap bar's path (bar not home)")
        self._audit.warn("feed_under", moving and covered is not None,
                         f"film fed under {covered.name if covered else ''} covering the slot")
        # 2. Wrap bar
        if out.wrap_bar and not self.bar_stuck:
            events += self._turn_bar(dt)
        # 3. Conveyor
        if out.wrap_conveyor:
            events += self._move(dt)
        # 4. Into the oven
        events += self._hand_over()
        return events + self._audit.events

    # --- 2: wrap bar ------------------------------------------------------------------
    def _turn_bar(self, dt: float) -> list[Event]:
        before = self.bar
        after = before + dt / config.WRAP_TURN_S
        self.bar = after % 1.0
        if math.floor(after - 0.5) <= math.floor(before - 0.5):
            return []
        # The bar passed the top of its turn: it lays the sheet over the pack in reach
        low = config.FILM_SLOT_MM + config.PACK_LENGTH_MM  # the pack's tail has passed the slot
        for pack in self.packs:
            if low - _EPSILON <= pack.front_mm <= low + config.WRAP_REACH_MM + _EPSILON:
                if pack.sheet_mm > _EPSILON and not pack.wrapped:
                    pack.wrapped = True
                    self.wrapped += 1
                    return [Event.sim(self._now, f"{pack.name} wrapped ({pack.sheet_mm:.0f} mm sheet)")]
        return []

    # --- 3: conveyor ------------------------------------------------------------------
    def _move(self, dt: float) -> list[Event]:
        """Every pack moves forward; it stops a pack length behind the pack ahead (no overlap), and the
        front pack stops at the oven entry until _hand_over() moves it into the oven."""
        events: list[Event] = []
        limit = config.OVEN_ENTRY_MM
        for pack in self.packs:
            old = pack.front_mm
            pack.front_mm = min(old + config.WRAP_SPEED_MM_S * dt, limit)
            limit = pack.front_mm - config.PACK_LENGTH_MM
            if old < config.FILM_SLOT_MM - _EPSILON <= pack.front_mm:
                events += self._take_sheet(pack)
        return events

    def _take_sheet(self, pack: Pack) -> list[Event]:
        """The pack's front crosses the slot: it drives onto the sheet's front edge and takes it."""
        sheet_mm, cut = self.film.take_sheet()
        if sheet_mm <= _EPSILON:
            return []  # no sheet: judged at the oven entry
        pack.sheet_mm += sheet_mm
        if not cut:
            return [Event.sim(self._now, f"{pack.name} drove over an uncut sheet: film torn ({sheet_mm:.0f} mm)",
                              warning=True)]
        return [Event.sim(self._now, f"{pack.name} onto its sheet ({sheet_mm:.0f} mm)")]

    # --- 4: into the oven -------------------------------------------------------------
    def _hand_over(self) -> list[Event]:
        if not self.packs or self.packs[0].front_mm < config.OVEN_ENTRY_MM - _EPSILON:
            self._audit.warn("entry_blocked", False, "")
            return []
        pack = self.packs[0]
        if not self.oven.put_at_entry(pack):
            self._audit.warn("entry_blocked", True, f"{pack.name} waits at the oven entry: the last pack is still there")
            return []
        self._audit.warn("entry_blocked", False, "")
        self.packs.pop(0)
        events = [Event.sim(self._now, f"{pack.name} into the oven")]
        if pack.sheet_mm <= _EPSILON:
            events.append(Event.sim(self._now, f"{pack.name} entered the oven without film", warning=True))
            return events
        if not pack.wrapped:
            events.append(Event.sim(self._now, f"{pack.name} entered the oven not wrapped", warning=True))
        if pack.sheet_mm < config.SHEET_NEEDED_MM - _EPSILON:
            events.append(Event.sim(self._now, f"{pack.name} entered the oven with a short sheet "
                                               f"({pack.sheet_mm:.0f} mm, needs {config.SHEET_NEEDED_MM:.0f})",
                                    warning=True))
        return events

    # --- Sensors ----------------------------------------------------------------------
    @property
    def pack_at_film(self) -> bool:
        """Film stop eye: ON while a pack covers it."""
        return any(config.FILM_STOP_MM - _EPSILON <= p.front_mm < config.FILM_STOP_MM + config.PACK_LENGTH_MM - _EPSILON
                   for p in self.packs)

    @property
    def wrap_home(self) -> bool:
        """The bar is inside its home window (WRAP_HOME_TURN of a turn, centred on 0)."""
        half = config.WRAP_HOME_TURN / 2
        return self.bar < half or self.bar >= 1.0 - half

    @property
    def slot_covered(self) -> Pack | None:
        """The pack standing over the film slot, if any."""
        for p in self.packs:
            if config.FILM_SLOT_MM - _EPSILON <= p.front_mm < config.FILM_SLOT_MM + config.PACK_LENGTH_MM - _EPSILON:
                return p
        return None


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
        4. a pack whose tail passed the exit goes onto the outfeed table (audit: not shrunk; with
           check_labels also bottles without a whole label, pack quality Q3)
        5. shrink time of the packs inside
    """

    def __init__(self, outfeed: OutfeedTable, temp_c: float = config.ROOM_C, check_labels: bool = False) -> None:
        self.outfeed = outfeed
        self.check_labels = check_labels  # warn about bottles without a whole label (see Packer)
        # True state. Public: tests, the panel and (stage 7) fault commands read / set them.
        self.temp_c = temp_c
        self.packs: list[Pack] = []  # on the oven conveyor, front pack (nearest the exit) first
        self.left = 0  # packs moved onto the outfeed table
        self._spin_s = 0.0  # how long the blower has been running
        self.removed: list[Pack] = []  # packs the operator took out of the oven (clear())
        # Injected faults (stage 7), set only by commands; the PLC only sees their effect
        self.heater_failed = False  # element burnt out: heats nothing (wins over heater_stuck_on)
        self.heater_stuck_on = False  # contactor welded: heats whatever the PLC says (still only with air)
        self.blower_failed = False  # blower motor runs, no air moves (broken belt): Airflow_OK stays FALSE
        self.jammed = False  # a pack caught in the oven: the conveyor runs, the packs stand
        self._now = 0.0
        self._audit = Audit()

    def step(self, heater_on: bool, blower_on: bool, conveyor_on: bool, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        # 1. Blower
        self._spin_s = self._spin_s + dt if blower_on and not self.blower_failed else 0.0
        # 2. Air temperature
        heating = (heater_on or self.heater_stuck_on) and not self.heater_failed
        self._heat(heating and self.air_flowing, dt)
        # 3.-4. Conveyor and outfeed
        events: list[Event] = []
        if conveyor_on and not self.jammed:
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
            unlabelled = [b.id for b in pack.bottles
                          if b.label_mm < config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM - _EPSILON]
            if self.check_labels and unlabelled:
                events.append(Event.sim(self._now, f"{pack.name} left the oven with bottles without a whole label: "
                                                   f"{', '.join(map(str, unlabelled))}", warning=True))
        name = self.packs[0].name if self.packs else ""
        self._audit.warn("exit_blocked", waiting, f"{name} waits at the oven exit: outfeed table full")
        return events

    def clear(self) -> list[Event]:
        """Hand action (stage 7): the operator takes every pack out of the oven (after a jam)."""
        events = [Event.sim(self._now, f"{pack.name} taken out of the oven by hand") for pack in self.packs]
        self.removed += self.packs
        self.packs = []
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

