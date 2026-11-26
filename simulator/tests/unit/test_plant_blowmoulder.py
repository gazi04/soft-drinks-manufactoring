"""Plant with the real blow moulder: blow moulder -> C1 -> filler -> C2 -> ...  uv run pytest

No PLC here. `BlowmoulderTestController` (test_blowmoulder.py) plays the blow moulder PLC's two sequencers,
lamps, Outfeed_Full filter and interlocks, the filler's `TestController` (test_plant.py) the filler PLC, so
the line runs from preform to filled bottle without Docker. From the closed-loop stage on, the real OpenPLC
programs set the outputs and these classes are not used.

Like the real loop, every controller sees the sensors of the previous tick.
"""

from test_blowmoulder import EJECT, OVEN_HOT, BlowmoulderTestController, Preform, oven_ready
from test_plant import TestController

from simulator import config
from simulator.commands import CommandQueue
from simulator.events import EventLog
from simulator.machines.filler import FillerOutputs
from simulator.plant import BLOWMOULDER_DEAD, BlowmoulderWrites, Plant

TICK = 0.1


class Line:
    """Plant with the blow moulder in front of C1 (labeler stand-in behind C2), both test controllers.
    `filler` = None: the filler PLC does not take bottles (C1 runs, the door stays shut, as in Stop mode)."""

    def __init__(self, **plant_kw) -> None:
        self.plant = Plant(with_blowmoulder=True, **plant_kw)
        self.filler: TestController | None = TestController()
        self.bm = BlowmoulderTestController()
        self.log = EventLog(max_events=100_000)
        self.w = self.plant.writes()
        self.bw: BlowmoulderWrites = self.plant.blowmoulder_writes()
        self.bm_out = None
        self.filler_out = None  # overrides the filler controller (e.g. FillerOutputs(): FAULT, C1 stands)
        self.blocked_s = 0.0  # time the blow moulder's Outfeed_Full was TRUE

    def tick(self, dt: float = TICK) -> None:
        if self.filler_out is not None:
            out = self.filler_out
        elif self.filler is not None:
            out = self.filler.outputs(self.w, dt)
        else:
            out = FillerOutputs(conveyor1=True)
        self.bm_out = self.bm.outputs(self.bw.sensors, dt)
        self.w, events = self.plant.step(out, dt, blowmoulder=self.bm_out)
        self.bw = self.plant.blowmoulder_writes()
        self.log.extend(events)
        self.blocked_s += dt if self.bm.outfeed_full else 0.0

    def run(self, seconds: float, until=None) -> None:
        for _ in range(round(seconds / TICK)):
            self.tick()
            if until is not None and until():
                return

    def warnings(self) -> list[str]:
        return [e.format() for e in self.log.filter(warnings_only=True)]

    def discharged(self) -> list[int]:
        """Ids of the bottles that left the filler, in order."""
        return [e.bottle_id for e in self.log.recent(100_000) if e.text.startswith("left filler (")]

    def accounted(self) -> bool:
        """Every preform taken from the hopper is somewhere: loader, pocket, gripper, mould, dead plate or a bottle."""
        bm = self.plant.blowmoulder
        inside = [bm.at_loader, bm.pocket, bm.held, bm.cavity, bm.dead_plate]
        used = config.HOPPER_PREFORMS - bm.hopper.count
        return used == sum(p is not None for p in inside) + bm.ejected + len(bm.dropped)


# --- Plant set-up and writes ---------------------------------------------------------------------
def test_plants_without_the_blow_moulder_keep_the_supply():
    plant = Plant(with_labeler=True, with_packer=True)
    assert plant.supply is not None and plant.blowmoulder is None and plant.blowmoulder_writes() is None


def test_the_blow_moulder_replaces_the_supply_and_combines_with_the_others():
    plant = Plant(with_blowmoulder=True, with_syrup=True, with_labeler=True, with_packer=True)
    assert plant.supply is None and plant.blowmoulder.c1 is plant.c1
    assert plant.blowmoulder.bottles is plant.factory  # bottle ids from the plant's factory
    assert None not in (plant.syrup_writes(), plant.labeler_writes(), plant.packer_writes(),
                        plant.blowmoulder_writes())
    plant.step(FillerOutputs(), TICK)  # no PLC answers: nothing breaks


def test_writes_carry_wiring_and_the_blow_moulder_heartbeat():
    line = Line()
    bw = line.bw
    assert bw.e_stop_ok and not bw.heartbeat
    s = bw.sensors
    assert s.arm_at_oven and s.mould_open and s.rod_up and not s.c1_full and s.blow_pressure_raw == 0
    line.plant.e_stop_ok = False  # one E-stop chain for every PLC
    line.run(config.HEARTBEAT_S)
    assert not line.bw.e_stop_ok and not line.w.e_stop_ok and line.bw.heartbeat


def test_bottle_ejected_is_written_even_when_a_late_tick_hides_it():
    plant = Plant(with_blowmoulder=True)
    bm = plant.blowmoulder
    bm.cavity = Preform(1, stretched=True, max_bar=39.0, transfer_c=103.0)
    _, events = plant.step(FillerOutputs(), 0.5, blowmoulder=BLOWMOULDER_DEAD.__class__(ejector=True,
                                                                                         exhaust_valve=True))
    assert plant.c1.slots[0].id == 1 and not bm.bottle_ejected  # the 0.2 s pulse is over inside the tick ...
    assert plant.blowmoulder_writes().sensors.bottle_ejected  # ... but the PLC still sees it once
    assert not [e for e in events if e.is_warning and "slow tick" not in e.text]
    plant.step(FillerOutputs(), TICK)
    assert not plant.blowmoulder_writes().sensors.bottle_ejected


def test_a_blow_moulder_plc_that_does_not_answer_moves_nothing_and_vents():
    plant = Plant(with_blowmoulder=True)
    for _ in range(30):
        plant.step(FillerOutputs(), TICK)  # blowmoulder=None
    bm = plant.blowmoulder
    assert plant.blowmoulder_outputs is None
    assert not bm.preform_at_loader and bm.arm_at_oven and bm.loader_home and bm.mould_open
    # A closed mould under pressure opens on its valve's spring (known model limit, the PLC would hold it)
    bm.mould, bm.pressure_bar = 1.0, 30.0
    _, events = plant.step(FillerOutputs(), TICK)
    assert bm.pressure_bar == 0.0 and any("mould opened under 30.0 bar" in e.text for e in events)


def test_supply_commands_are_ignored_with_the_real_blow_moulder():
    plant = Plant(with_blowmoulder=True)
    q = CommandQueue()
    q.put("add_bottles", 5)
    q.put("e_stop", True)
    events = q.apply(plant)
    assert [e.text for e in events] == [
        "command add_bottles = 5 ignored: the real blow moulder makes the bottles (run with --no-blowmoulder)",
        "command: e_stop = True"]
    assert not plant.e_stop_ok


# --- The line ------------------------------------------------------------------------------------
def test_from_preform_to_filled_bottle():
    line = Line()
    line.run(600.0)
    ids = line.discharged()
    assert len(ids) >= 78 and ids == list(range(1, len(ids) + 1))  # ~8/min, in order, none lost
    assert not line.warnings(), line.warnings()  # every bottle blown, every one filled
    assert line.plant.filler.lost == [] and line.accounted()
    # The blow moulder (~10/min) is faster than the filler (~8/min): C1 fills, the blow moulder is Blocked at times
    assert line.plant.blowmoulder.ejected > len(ids) and line.blocked_s > 0


def test_filler_not_taking_backs_the_blow_moulder_up_then_it_continues():
    """Rule 8 / report 5.3: C1 fills, Outfeed_Full, the oven loads no new preform; the bottle in the mould waits
    there (EJECT, ejector off). The preform the oven had already loaded when Outfeed_Full came cannot be handed over
    (the mould is busy): it waits in HOT, the lamps hold it in the window (state-diagram.md expects an empty oven,
    which only happens when Outfeed_Full comes before that load). When the filler takes again, the cycle runs on."""
    line = Line()
    line.filler = None  # C1 runs, the door stays shut
    bm = line.plant.blowmoulder
    line.run(150.0, until=lambda: line.bm.mould_step == EJECT and line.bm.outfeed_full)
    line.run(20.0)  # and it stays so
    assert line.bm.outfeed_full and line.bw.sensors.c1_full
    assert line.bm.step == OVEN_HOT and oven_ready(bm.oven_temp) and bm.pocket.peak_c < config.OVERHEAT_C
    assert not bm.preform_at_loader  # the chute runs only in FEED
    assert line.bm.mould_step == EJECT and bm.bottle_in_mould and bm.mould_open  # the bottle waits in the mould
    assert 0 < line.bm_out.lamp_power < 50 and not line.bm_out.ejector  # lamps hold ~30 %
    assert plant_c1_queue(line) == config.CONVEYOR_SLOTS - config.C1_FULL_SLOT
    ejected = bm.ejected
    line.filler = TestController()
    line.run(60.0)
    assert bm.ejected > ejected + 3  # flows again without Reset
    assert not line.warnings(), line.warnings()
    assert line.plant.filler.lost == [] and line.accounted()


def plant_c1_queue(line: Line) -> int:
    return line.plant.c1.count


def test_a_filler_fault_jams_the_blow_moulder_issue_i18():
    """HMI issue I18 for the blow moulder: a filler FAULT stops C1 at once; C1_Full watches slot 1, so the bottle
    put onto the standing belt stays in slot 0 and Outfeed_Full never comes. The dead plate takes one more bottle,
    the next ejection is stopped halfway: Bottle_In_Mould stays, the real PLC raises Alm_HandlingJam."""
    line = Line()
    line.filler_out = FillerOutputs()  # filler FAULT: every output OFF, C1 stands
    bm = line.plant.blowmoulder
    line.run(60.0)
    assert line.plant.c1.slots[0] is not None and bm.dead_plate is not None and bm.bottle_in_mould
    assert not line.bw.sensors.c1_full
    assert any("ejector stopped: a bottle still stands on the dead plate" in w for w in line.warnings())
