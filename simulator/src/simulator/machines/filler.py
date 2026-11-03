"""Filler machine: index belt, entry door, rinse / fill / cap stations, discharge.

    C1 exit                 index belt (IndexMotor)                    C2 entry
    [b] ─► DOOR ─► [ entry ] ─► [ RINSE ] ─► [ FILL ] ─► [  CAP  ] ─► out ─► [ ]
           S2       pocket 0     pocket 1     pocket 2    pocket 3    S6
                    (no sensor)  S3           S4          S5
                     S8 = cam on the belt, ON in the first part of every pitch

All four pockets sit on the same belt, so they move together. While IndexMotor
is ON the belt position grows; each time it crosses a pitch boundary every bottle
moves one pocket forward and the bottle in the cap pocket leaves onto C2.

The filler only knows the PLC outputs (FillerOutputs) and gives back what its
sensors see (FillerSensors). It does not decide anything: if the PLC opens the
fill valve with no bottle under it, the product is spilled and the audit logs a
warning. The decisions stay in the PLC.

C1 and C2 are passed in because the door takes bottles from C1's exit and the
discharge puts them on C2's entry. The filler does not move the conveyors; the
plant steps C1, the filler and C2 one after another in every tick.

Order inside one step (see step()):
    1. S6 pulse runs down
    2. belt moves; at a pitch boundary the pockets shift and the cap bottle leaves
    3. door: the bottle at C1's exit passes into the entry pocket
    4. stations: rinse, fill, cap work on the bottle standing in front of them

Known model limit: the entry pocket has no sensor. Normally it holds a bottle
only during INDEX (the door opens at the start of INDEX, the shift at the S8 edge
moves the bottle to rinse). If the belt stops in between (E-stop) and C1 is
empty, the PLC sees no bottle at all (no S2..S5) and reports Starved.
"""

from dataclasses import dataclass

from simulator import config
from simulator.bottle import Bottle
from simulator.conveyor import Conveyor
from simulator.events import Event

# Same reason as in conveyor.py: sums of 0.1 land a tiny bit below or above the
# exact value (15 x 0.1 is not exactly 1.5), so times are compared with a tolerance.
_EPSILON = 1e-9

# Pocket numbers on the index belt
ENTRY, RINSE, FILL, CAP = 0, 1, 2, 3


@dataclass
class FillerOutputs:
    """PLC outputs %QX0.0-0.5 (coils 0-5), in coil order.

    The filler ignores conveyor1: C1 is stepped by the plant with this value.
    """

    conveyor1: bool = False
    door_motor: bool = False
    rinse_valve: bool = False
    fill_valve: bool = False
    cap_motor: bool = False
    index_motor: bool = False


@dataclass(frozen=True)
class FillerSensors:
    """What the filler's sensors see. S7 (C2 full) is a C2 sensor, built in the plant."""

    s2: bool  # bottle waiting at the door (C1 exit slot)
    s3: bool  # bottle at the rinse station
    s4: bool  # bottle at the fill station
    s5: bool  # bottle at the cap station
    s6: bool  # bottle discharged (short pulse)
    s8: bool  # index cam
    fill_level: int  # ml in the bottle at S4, else 0 (FillLevel, HR 1036)


class Filler:
    def __init__(
        self,
        c1: Conveyor,
        c2: Conveyor,
        pitch_s: float = config.INDEX_PITCH_S,
        cam_window: float = config.CAM_WINDOW,
        station_window: float = config.STATION_WINDOW,
        door_pass_s: float = config.DOOR_PASS_S,
        fill_flow_ml_s: float = config.FILL_FLOW_ML_S,
        rinse_min_s: float = config.RINSE_MIN_S,
        cap_min_s: float = config.CAP_MIN_S,
        s6_pulse_s: float = config.S6_PULSE_S,
    ) -> None:
        self.c1 = c1
        self.c2 = c2
        self.pitch_s = pitch_s
        self.cam_window = cam_window
        self.station_window = station_window
        self.door_pass_s = door_pass_s
        self.fill_flow_ml_s = fill_flow_ml_s
        self.rinse_min_s = rinse_min_s
        self.cap_min_s = cap_min_s
        self.s6_pulse_s = s6_pulse_s
        # Underfill audit limit; the plant later copies the PLC's FillSetpoint here
        self.fill_setpoint_ml = config.DEFAULT_FILL_SETPOINT_ML
        # Fault injection (stage 7): the belt is blocked mechanically. The motor may be ON,
        # but the belt does not move, so S8 never comes and the PLC's jam timer fires.
        self.index_jammed = False

        self.pockets: list[Bottle | None] = [None, None, None, None]  # ENTRY .. CAP
        self.shifts = 0  # pitch boundaries crossed since the start
        self.lost: list[Bottle] = []  # bottles that fell off (crash, two in a pocket)

        self._phase = 0.0  # seconds the belt has run since the last pitch boundary
        self._door_timer = 0.0  # how long the current bottle has been passing the door
        self._rinse_timer = 0.0  # rinse time the bottle at the rinse station has had
        self._cap_timer = 0.0  # cap head time the bottle at the cap station has had
        self._s6_timer = 0.0  # remaining time of the S6 pulse
        self._filling: Bottle | None = None  # bottle that received product last tick
        self._spilling = False  # a spill warning was already given for this flow period

        self._events: list[Event] = []  # events of the current step
        self._now = 0.0  # simulation time of the current step

    # --- One tick ------------------------------------------------------------------
    def step(self, out: FillerOutputs, product_available: bool, dt: float, now: float) -> list[Event]:
        """Advance the filler by dt seconds. Returns the events of this step."""
        self._events = []
        self._now = now

        # 1. The S6 pulse runs down (before a new shift may restart it)
        self._s6_timer = max(0.0, self._s6_timer - dt)

        # 2. Index belt (does not move while jammed, even with the motor ON)
        if out.index_motor and not self.index_jammed:
            self._phase += dt
            # 'while': after a long dt several pitches are due
            while self._phase >= self.pitch_s - _EPSILON:
                self._phase -= self.pitch_s
                self._shift()

        # 3. Door
        self._door(out.door_motor, dt)

        # 4. Stations (product flows only if the valve is open AND the syrup room delivers)
        self._rinse(out.rinse_valve, dt)
        self._fill(out.fill_valve and product_available, dt)
        self._cap(out.cap_motor, dt)

        return self._events

    # --- 3a: index belt ------------------------------------------------------------
    @property
    def cam_on(self) -> bool:
        """S8: the index cam is ON in a short window right after each pitch boundary."""
        return self._phase < self.cam_window * self.pitch_s

    @property
    def at_stations(self) -> bool:
        """The bottles stand in front of their station sensors (S3-S5 can see them).

        Wider than the cam window: the belt may overrun a little after the PLC switched
        it off (the simulator only sees that at the next tick), and the stations must
        still see their bottles then. Between stations the sensors see nothing.
        """
        return self._phase < self.station_window * self.pitch_s

    @property
    def belt_pos(self) -> float:
        """Belt position in pitches (e.g. 3.4), for display only."""
        return self.shifts + self._phase / self.pitch_s

    def _at_station(self, pocket: int) -> Bottle | None:
        """The bottle standing in front of a station, or None (also while the belt is between stations)."""
        return self.pockets[pocket] if self.at_stations else None

    def _shift(self) -> None:
        """The belt crossed a pitch boundary: every bottle moves one pocket forward."""
        self.shifts += 1
        self._rinse_timer = 0.0  # new bottles in front of rinse and cap
        self._cap_timer = 0.0
        leaving = self.pockets[CAP]
        self.pockets = [None] + self.pockets[:CAP]  # entry is empty, cap bottle is out
        if leaving is not None:
            self._discharge(leaving)

    # --- 3b: door ------------------------------------------------------------------
    def _door(self, door_open: bool, dt: float) -> None:
        if not (door_open and self.c1.exit_bottle is not None):
            self._door_timer = 0.0  # door closed or nobody waiting: nothing passes
            return
        self._door_timer += dt
        if self._door_timer < self.door_pass_s - _EPSILON:
            return  # bottle is still passing the door
        self._door_timer = 0.0  # the next bottle starts from 0
        bottle = self.c1.take_at_exit()
        if self.pockets[ENTRY] is None:
            self.pockets[ENTRY] = bottle
            self._log(bottle, "entered filler")
        else:
            # The door stayed open too long (or opened twice without an index)
            self.lost.append(bottle)
            other = self.pockets[ENTRY].name
            self._log(bottle, f"two bottles in one pocket (with {other}), fell off", warning=True)

    # --- 3c: stations --------------------------------------------------------------
    def _rinse(self, valve_open: bool, dt: float) -> None:
        bottle = self._at_station(RINSE)
        if valve_open and bottle is not None and not bottle.rinsed:
            self._rinse_timer += dt
            if self._rinse_timer >= self.rinse_min_s - _EPSILON:
                bottle.rinsed = True
                self._log(bottle, "rinsed")

    def _cap(self, cap_motor: bool, dt: float) -> None:
        bottle = self._at_station(CAP)
        if cap_motor and bottle is not None and not bottle.capped:
            self._cap_timer += dt
            if self._cap_timer >= self.cap_min_s - _EPSILON:
                bottle.capped = True
                self._log(bottle, "capped")

    def _fill(self, flow: bool, dt: float) -> None:
        bottle = self._at_station(FILL)
        receiving = bottle if flow else None  # the bottle that gets product this tick

        # A flow period ended (valve closed, no product, or the bottle moved away)
        if self._filling is not None and self._filling is not receiving:
            self._log(self._filling, f"filled to {round(self._filling.level_ml)} ml")
        self._filling = receiving

        if receiving is not None:
            receiving.level_ml += self.fill_flow_ml_s * dt

        # Product flows but no bottle stands under the valve: spill (one warning per period)
        if flow and bottle is None:
            if not self._spilling:
                self._spilling = True
                self._events.append(
                    Event.sim(self._now, "spill: product flows with no bottle at the fill station", warning=True)
                )
        else:
            self._spilling = False

    # --- 3d: discharge and audit ---------------------------------------------------
    def _discharge(self, bottle: Bottle) -> None:
        self._s6_timer = self.s6_pulse_s  # the bottle passes S6 on its way out
        if not self.c2.put_at_entry(bottle):
            self.lost.append(bottle)
            self._log(bottle, "crash: C2 entry occupied, fell off", warning=True)
            return

        level = round(bottle.level_ml)  # the same rounded number the PLC saw as FillLevel
        self._log(bottle, f"left filler ({level} ml)")
        if not bottle.rinsed:
            self._log(bottle, "left filler not rinsed", warning=True)
        if not bottle.capped:
            self._log(bottle, "left filler uncapped", warning=True)
        if level < self.fill_setpoint_ml:
            self._log(bottle, f"left filler underfilled: {level} ml < {self.fill_setpoint_ml} ml", warning=True)

    # --- Sensors -------------------------------------------------------------------
    def sensors(self) -> FillerSensors:
        at_fill = self._at_station(FILL)
        return FillerSensors(
            s2=self.c1.occupied(-1),
            s3=self._at_station(RINSE) is not None,
            s4=at_fill is not None,
            s5=self._at_station(CAP) is not None,
            s6=self._s6_timer > _EPSILON,
            s8=self.cam_on,
            # round, not int(): a float sum of 499.9999 must read 500, as on a real gauge
            fill_level=round(at_fill.level_ml) if at_fill is not None else 0,
        )

    # --- Helper --------------------------------------------------------------------
    def _log(self, bottle: Bottle, text: str, warning: bool = False) -> None:
        self._events.append(Event.bottle(self._now, bottle.id, text, warning))
