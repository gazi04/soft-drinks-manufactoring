"""Packer: collates 6 bottles into a 2x3 group, wraps it in shrink film, shrinks it in an oven.

Part 1 (stage 1): the COLLATION.

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
"""

from dataclasses import dataclass

from simulator import config
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Audit, Event

_EPSILON = 1e-9


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
