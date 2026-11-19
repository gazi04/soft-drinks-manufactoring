"""Labeler: roll-fed hot-melt labeler with one pocket on a turntable.

    label roll ─► feed rollers (LabelFeed) ─► Label_Mark ─► knife (Cutter)
                                                  │ one label
    melt tank (GlueHeater, GlueTemp, Glue_Low) ─► glue roller (GlueRoller)
                                                  ▼
                                   bottle clamped and spinning in the pocket

Stage 2: LABEL WEB and MELT TANK. Two self-contained parts that only know the PLC
outputs and, from the caller, whether a clamped bottle is spinning in the pocket.
Stage 1: the POCKET (class Labeler), which owns both and moves the bottle:

    C2 exit ─► gate (GateMotor) ─► travel ─► POCKET ─► pusher (DischargeMotor) ─► C3 entry
                                             bell (ClampValve), turntable (SpinMotor)

    Labeler.step() order:
        1. Spin_Pulse runs down        4. turntable: turns, one Spin_Pulse per turn
        2. gate and travel             5. web + melt tank (the label onto a clamped, spinning bottle)
        3. bell moves                  6. pusher: pocket -> C3, audit of the label

    MeltTank   glue temperature: first-order lag toward the heater (heater ON) and toward
               the room (always), solved exactly; glue mass taken by the roller, cold glue
               blocks added by the operator mix in by mass
    LabelWeb   the web moves while LabelFeed is ON and the roll is not empty; every label
               length a registration mark passes (timed Label_Mark pulse, one label off the
               roll); the fed label sticks to the bottle (Bottle.label_mm) only when the bottle
               spins and the glue roller puts warm glue on it, otherwise it is lost

Like the other machines, the labeler decides nothing: if the PLC feeds a label with cold
glue, the label is lost and the audit logs a warning.

Known model limits:
- The heater's speed does not depend on how much glue is in the tank.
- The knife only resets "length since the last cut" (audit); the cut label is already on
  the bottle, a missing cut is reported, not modelled as a dangling label.
- The web stops at once when LabelFeed drops (no roller inertia).
"""

import math
from dataclasses import dataclass

from simulator import config
from simulator.address_map_labeler import POCKET_BOTTLE, POCKET_EMPTY, POCKET_LABELLED, POCKET_LABELLING
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Event

_EPSILON = 1e-9


@dataclass
class LabelerOutputs:
    """PLC outputs %QX0.0-%QX1.0 (coils 0-8), in coil order."""

    conveyor_motor2: bool = False
    gate_motor: bool = False
    clamp_valve: bool = False
    spin_motor: bool = False
    label_feed: bool = False
    cutter: bool = False
    glue_roller: bool = False
    glue_heater: bool = False
    discharge_motor: bool = False


class _Audit:
    """One warning per period: logged when the condition starts, re-armed when it ends."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self.events: list[Event] = []
        self.now = 0.0

    def start(self, now: float) -> None:
        self.now = now
        self.events = []

    def warn(self, key: str, condition: bool, text: str) -> None:
        if not condition:
            self._active.discard(key)
        elif key not in self._active:
            self._active.add(key)
            self.events.append(Event.sim(self.now, text, warning=True))


class MeltTank:
    """Hot-melt tank with its heater. The operator adds solid glue blocks; the roller takes glue."""

    def __init__(self, temp_c: float = config.ROOM_C, glue_kg: float = config.GLUE_START_KG) -> None:
        # True state. Public: tests and (stage 6) fault commands set them directly.
        self.temp_c = temp_c
        self.glue_kg = glue_kg
        # Injected faults (commands.py / scenarios.py). The PLC is never told: it can only see the effect.
        self.heater_failed = False  # the heater output heats nothing (element burnt out)
        self.heater_stuck_on = False  # the heater heats whatever the PLC says (contactor welded)
        self._audit = _Audit()

    def step(self, heater_on: bool, dt: float, now: float) -> list[Event]:
        """dT/dt = -heater (T - heater) / tau_heat - (T - room) / tau_room, solved exactly.

        The heater state is constant during one step, so the solution is an exponential
        toward the balance temperature (same method as the syrup room's cooling): it cannot
        overshoot, and a late tick gives the same result as several short ones.
        """
        self._audit.start(now)
        heating = (heater_on or self.heater_stuck_on) and not self.heater_failed
        rate_heat = (1.0 / config.GLUE_HEAT_TAU_S) if heating else 0.0
        rate_room = 1.0 / config.GLUE_ROOM_TAU_S
        rate = rate_heat + rate_room
        balance = (rate_heat * config.GLUE_HEATER_C + rate_room * config.ROOM_C) / rate
        self.temp_c = balance + (self.temp_c - balance) * math.exp(-rate * dt)
        self._audit.warn(
            "smoke",
            self.temp_c >= config.GLUE_SMOKE_C,
            f"hot melt smokes at {self.temp_c:.0f} degC (>= {config.GLUE_SMOKE_C:g} degC)",
        )
        return self._audit.events

    @property
    def sticky(self) -> bool:
        """Glue on the roller is liquid enough to hold a label."""
        return self.temp_c >= config.GLUE_STICK_MIN_C and self.glue_kg > _EPSILON

    def take_glue(self, grams: float) -> float:
        """The glue roller takes glue. Returns the grams that were there."""
        taken = min(grams / 1000.0, self.glue_kg)
        self.glue_kg -= taken
        return taken * 1000.0

    def add_glue(self, kg: float) -> float:
        """Cold glue blocks (room temperature) put in by the operator; they mix in by mass.

        Returns the kg that fitted into the tank.
        """
        if kg < 0:
            raise ValueError(f"kg must be >= 0, got {kg}")
        put = min(kg, config.GLUE_TANK_KG - self.glue_kg)
        total = self.glue_kg + put
        if total > _EPSILON:
            self.temp_c = (self.temp_c * self.glue_kg + config.ROOM_C * put) / total
        self.glue_kg = total
        return put

    # --- Sensors ----------------------------------------------------------------------
    @property
    def glue_temp(self) -> int:
        """GlueTemp, INT in 0.1 degC."""
        return round(self.temp_c * 10)

    @property
    def glue_low(self) -> bool:
        return self.glue_kg < config.GLUE_LOW_KG


class LabelWeb:
    """Label roll, feed rollers, registration mark and knife."""

    def __init__(self, labels: int = config.ROLL_LABELS) -> None:
        self.labels_left = labels  # labels still on the roll
        self.web_mm = 0.0  # position inside the current label period (a mark every LABEL_LENGTH_MM)
        self.tail_mm = config.ROLL_TAIL_MM if labels > 0 else 0.0  # web after the last mark (none on a bare core)
        self.since_cut_mm = 0.0  # web fed since the last cut (audit only)
        self.marks = 0  # registration marks passed (= labels pulled off the roll)
        self.lost_mm = 0.0  # label fed but not stuck to a bottle
        self.torn = False  # injected fault: the web is torn, the rollers turn but nothing moves
        self._mark_timer = 0.0  # remaining time of the Label_Mark pulse
        self._cutter_was_on = False
        self._audit = _Audit()

    def step(
        self,
        out: LabelerOutputs,
        bottle: Bottle | None,
        spinning: bool,
        tank: MeltTank,
        dt: float,
        now: float,
    ) -> list[Event]:
        """Feed, mark, glue and knife for dt seconds.

        `bottle` is the bottle in the pocket (None = empty); `spinning` = it is clamped and
        the turntable turns (decided by the pocket, stage 1).
        """
        self._audit.start(now)

        # 1. The mark pulse runs down (before a new mark may restart it)
        self._mark_timer = max(0.0, self._mark_timer - dt)

        # 2. Knife: a rising edge of Cutter cuts the web
        if out.cutter and not self._cutter_was_on:
            self.since_cut_mm = 0.0
        self._cutter_was_on = out.cutter

        # 3. Feed: the web moves only while the roll has web left (the labels plus the tail after the last mark)
        wanted = config.LABEL_FEED_MM_S * dt if out.label_feed and not self.torn else 0.0
        fed = min(wanted, self.web_left_mm)
        if fed > 0:
            self.web_mm += fed
            self.since_cut_mm += fed
            while self.web_mm >= config.LABEL_LENGTH_MM and self.labels_left > 0:
                # a registration mark passes: one label length is off the roll
                self.web_mm -= config.LABEL_LENGTH_MM
                self.labels_left -= 1
                self.marks += 1
                self._mark_timer = config.LABEL_MARK_PULSE_S

        # 4. Glue and bottle: the fed length sticks only with warm glue on a spinning bottle
        glued = False
        if fed > 0 and out.glue_roller:
            sticky = tank.sticky  # before taking: the last gram still holds
            tank.take_glue(config.GLUE_PER_LABEL_G * fed / config.LABEL_LENGTH_MM)
            glued = sticky
        if fed > 0:
            if glued and spinning and bottle is not None:
                bottle.label_mm += fed
            else:
                self.lost_mm += fed
        lost = fed > 0 and not (glued and spinning and bottle is not None)
        self._audit.warn("label_lost", lost, f"label fed but lost: {self._lost_reason(out, bottle, spinning, tank)}")
        self._audit.warn("roller_dry", out.glue_roller and tank.glue_kg <= _EPSILON, "glue roller runs dry: melt tank empty")
        self._audit.warn(
            "no_cut",
            self.since_cut_mm > config.KNIFE_MISSED_FACTOR * config.LABEL_LENGTH_MM,
            f"knife did not cut: {self.since_cut_mm:.0f} mm of web since the last cut",
        )
        return self._audit.events

    @staticmethod
    def _lost_reason(out: LabelerOutputs, bottle: Bottle | None, spinning: bool, tank: MeltTank) -> str:
        if bottle is None:
            return "no bottle in the pocket"
        if not spinning:
            return "bottle not clamped and spinning"
        if not out.glue_roller:
            return "glue roller off"
        return f"glue not sticky ({tank.temp_c:.0f} degC, {tank.glue_kg:.2f} kg)"

    def load_roll(self, labels: int = config.ROLL_LABELS) -> None:
        """Roll change by the operator: a new roll, the web starts at a label boundary."""
        if labels < 0:
            raise ValueError(f"labels must be >= 0, got {labels}")
        self.labels_left = labels
        self.web_mm = 0.0
        self.tail_mm = config.ROLL_TAIL_MM if labels > 0 else 0.0

    @property
    def web_left_mm(self) -> float:
        """Web still on the roll: the rest of the current label, the labels after it and the tail.

        `Roll_Empty` comes at the last mark (no label left), but the PLC stops the feed one or two ticks
        later; the tail lets that run-on finish the last label instead of cutting it short.
        """
        return max(0.0, self.labels_left * config.LABEL_LENGTH_MM - self.web_mm + self.tail_mm)

    # --- Sensors ----------------------------------------------------------------------
    @property
    def label_mark(self) -> bool:
        return self._mark_timer > _EPSILON

    @property
    def roll_low(self) -> bool:
        return self.labels_left <= config.ROLL_LOW_LABELS

    @property
    def roll_empty(self) -> bool:
        return self.labels_left == 0


@dataclass(frozen=True)
class LabelerSensors:
    """What the labeler's sensors report, in the order of the I/O list."""

    bottle_at_gate: bool  # C2 exit slot
    bottle_in_pocket: bool
    bell_down: bool
    label_present: bool  # label on the bottle in the pocket
    c3_full: bool  # C3 slot C3_FULL_SLOT
    roll_empty: bool
    label_mark: bool  # short pulse
    spin_pulse: bool  # short pulse
    roll_low: bool
    glue_low: bool
    glue_temp: int  # 0.1 degC


class Labeler:
    """One pocket on a turntable between C2 (in) and C3 (out); owns the label web and the melt tank."""

    def __init__(self, c2: Conveyor, c3: Conveyor) -> None:
        self.c2 = c2
        self.c3 = c3
        self.web = LabelWeb()
        self.tank = MeltTank()
        # True state. Public: tests, the panel and (stage 6) fault commands read / set them.
        self.pocket: Bottle | None = None  # the bottle on the turntable
        self.entering: Bottle | None = None  # a bottle between the gate and the pocket
        self.bell = 0.0  # 0 = up, 1 = down (clamped)
        self.turns = 0.0  # turntable angle in turns since the start
        self.lost: list[Bottle] = []  # bottles that fell off (two in the pocket)
        self.removed: list[Bottle] = []  # bottles the operator took out by hand (clear_gate)
        # Injected faults (commands.py / scenarios.py). The PLC is never told: it can only see the effect.
        self.bell_stuck = False  # the bell keeps its position whatever ClampValve says
        self.gate_jammed = False  # a bottle through the gate stops on its way to the pocket
        self.discharged = 0  # bottles pushed onto C3
        self.labelled = 0  # ... of them with a whole label
        self._gate_timer = 0.0  # how long the current bottle has been passing the gate
        self._travel_timer = 0.0  # how long the entering bottle has been on its way
        self._push_timer = 0.0  # how long the pusher has pushed the pocket bottle
        self._spin_timer = 0.0  # remaining time of the Spin_Pulse
        self._now = 0.0
        self._audit = _Audit()

    def step(self, out: LabelerOutputs, dt: float, now: float) -> list[Event]:
        self._now = now
        self._audit.start(now)
        events: list[Event] = []
        # 1. The spin pulse runs down (before a new turn may restart it)
        self._spin_timer = max(0.0, self._spin_timer - dt)
        # 2.-4. Bottle movement
        events += self._gate(out.gate_motor, dt)
        self._bell(out.clamp_valve, dt)
        self._turntable(out.spin_motor, dt)
        # 5. Label and glue
        events += self.tank.step(out.glue_heater, dt, now)
        events += self.web.step(out, self.pocket, self.spinning(out), self.tank, dt, now)
        # 6. Pusher
        events += self._discharge(out.discharge_motor, dt)
        return events + self._audit.events

    @property
    def bell_down(self) -> bool:
        return self.bell >= 1.0 - _EPSILON

    def spinning(self, out: LabelerOutputs) -> bool:
        """A bottle is clamped and turns: the label can wrap around it."""
        return out.spin_motor and self.bell_down and self.pocket is not None

    # --- 2: gate and travel -----------------------------------------------------------
    def _gate(self, gate_open: bool, dt: float) -> list[Event]:
        events: list[Event] = []
        # the bottle on its way reaches the pocket
        if self.entering is not None and not self.gate_jammed:
            self._travel_timer += dt
            if self._travel_timer >= config.GATE_TRAVEL_S - _EPSILON:
                bottle, self.entering = self.entering, None
                if self.pocket is None:
                    self.pocket = bottle
                    events.append(Event.bottle(self._now, bottle.id, "in the labeler pocket"))
                else:
                    self.lost.append(bottle)
                    events.append(Event.bottle(self._now, bottle.id, "fell off: two bottles in the labeler pocket", warning=True))
        # a bottle passes the open gate (same rule as the filler's door)
        if not (gate_open and self.c2.exit_bottle is not None):
            self._gate_timer = 0.0
            return events
        self._gate_timer += dt
        if self._gate_timer < config.GATE_PASS_S - _EPSILON:
            return events
        self._gate_timer = 0.0
        bottle = self.c2.take_at_exit()
        if self.entering is not None:  # the gate let two through before the first arrived
            self.lost.append(self.entering)
            events.append(Event.bottle(self._now, self.entering.id, "fell off: pushed by the next bottle in the gate", warning=True))
        self.entering, self._travel_timer = bottle, 0.0
        events.append(Event.bottle(self._now, bottle.id, "through the labeler gate"))
        return events

    # --- 3: bell ----------------------------------------------------------------------
    def _bell(self, clamp: bool, dt: float) -> None:
        if self.bell_stuck:
            return
        move = dt / config.BELL_MOVE_S
        self.bell = min(1.0, self.bell + move) if clamp else max(0.0, self.bell - move)

    # --- 4: turntable -----------------------------------------------------------------
    def _turntable(self, spin: bool, dt: float) -> None:
        self._audit.warn(
            "unclamped_spin",
            spin and self.pocket is not None and not self.bell_down,
            "turntable spins an unclamped bottle",
        )
        if not spin:
            return
        before = math.floor(self.turns + _EPSILON)
        self.turns += dt / config.SPIN_TURN_S
        if math.floor(self.turns + _EPSILON) > before:  # the cam passed: one more whole turn
            self._spin_timer = config.SPIN_PULSE_S

    # --- 6: pusher --------------------------------------------------------------------
    def _discharge(self, push: bool, dt: float) -> list[Event]:
        # The bottle is free as soon as the bell lifts off it (Bell_Down drops): the PLC has one bell
        # sensor and treats "up" as NOT Bell_Down, so the pusher may start while the bell still rises.
        against_bell = push and self.pocket is not None and self.bell_down
        self._audit.warn("push_bell", against_bell, "discharge pusher pushes against the bell")
        if not push or self.pocket is None or against_bell:
            self._push_timer = 0.0
            return []
        self._push_timer = min(self._push_timer + dt, config.DISCHARGE_S)
        if self._push_timer < config.DISCHARGE_S - _EPSILON or not self.c3.put_at_entry(self.pocket):
            return []  # still pushing, or C3's entry is taken: the bottle waits at the edge
        bottle, self.pocket, self._push_timer = self.pocket, None, 0.0
        self.discharged += 1
        if bottle.label_mm <= _EPSILON:
            return [Event.bottle(self._now, bottle.id, "left the labeler without a label", warning=True)]
        if bottle.label_mm < config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM:
            return [Event.bottle(self._now, bottle.id, f"left the labeler with a broken label ({bottle.label_mm:.0f} mm)",
                                 warning=True)]
        self.labelled += 1
        return [Event.bottle(self._now, bottle.id, "left labeler (labelled)")]

    # --- Hand actions -----------------------------------------------------------------
    def clear_gate(self) -> Bottle | None:
        """The operator takes a bottle that is stuck between gate and pocket away (returns it, or None)."""
        bottle, self.entering, self._travel_timer = self.entering, None, 0.0
        if bottle is not None:
            self.removed.append(bottle)
        return bottle

    # --- Sensors ----------------------------------------------------------------------
    def sensors(self) -> LabelerSensors:
        return LabelerSensors(
            bottle_at_gate=self.c2.exit_bottle is not None,
            bottle_in_pocket=self.pocket is not None,
            bell_down=self.bell_down,
            label_present=self.pocket is not None and self.pocket.label_mm > _EPSILON,
            c3_full=self.c3.occupied(config.C3_FULL_SLOT),
            roll_empty=self.web.roll_empty,
            label_mark=self.web.label_mark,
            spin_pulse=self._spin_timer > _EPSILON,
            roll_low=self.web.roll_low,
            glue_low=self.tank.glue_low,
            glue_temp=self.tank.glue_temp,
        )

    # --- HMI animation ----------------------------------------------------------------
    def pocket_code(self) -> int:
        """State code of the pocket for the HMI (%MW71): 0 empty, 1 bottle without label,
        2 labelling (part of a label on it), 3 labelled. "Labelled" uses the audit's limit of a
        whole label (BROKEN_LABEL_SHARE), so the screen and the event log agree."""
        if self.pocket is None:
            return POCKET_EMPTY
        if self.pocket.label_mm <= _EPSILON:
            return POCKET_BOTTLE
        if self.pocket.label_mm < config.BROKEN_LABEL_SHARE * config.LABEL_LENGTH_MM:
            return POCKET_LABELLING
        return POCKET_LABELLED
