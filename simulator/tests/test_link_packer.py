"""Packer simulator stage 5: first contact of the simulator's Modbus client (PackerPlc) with the packer PLC.

Runs against the packer PLC on port 5024. The PLC must be idle (Step 0, WrapStep 0, State 0): the script
writes sensor values, and a running sequence would react to them. Checks:
    P1 the client connects, reads the status (both steps, computed words, 10 alarms) and the outputs
    P2 the heartbeat write sticks (no debugger force on %MX1.2)
    P3 heartbeat running + Reset -> Alm_CommLost FALSE
    P4 heartbeat running 10 s -> Alm_CommLost stays FALSE
    P5 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    P6 write() of both coil blocks (%MX0-1 and %MX4) + OvenTemp, verify_writes() finds nothing wrong
       (no edge on the counted inputs Bottle_Counted, Film_Pulse, Oven_Entry, Oven_Exit: the PLC's counters
       count in IDLE too; P7 checks that GroupCount, FeedCount, PacksInOven and PackCount did not change)
    P6b the HMI animation words (%MW70-73, HR 1094-1097) written by write() read back by a second client
        (as FUXA reads them); the PLC's status and computed words keep their values
    P7 the PLC reads the sensors at the addresses of the map (no Start needed):
       a) OvenTemp (HR 1036) -> Heat_Demand and Oven_Ready, expected values from the PLC's own setpoints;
          never >= 2300, OvenMax would FAULT the PLC in every mode
       b) Table_Full (coil 8228) -> Outfeed_Full after the 2 s filter, released after 3 s
       c) Airflow_OK (coil 8229): the script switches to Auto for 5 s (as the HMI; the mode is restored after),
          where the idle packer runs its oven without a Start; the script answers the blower like the physics,
          so Alm_AirflowLost never comes
    P8 (--restart) the runtime is restarted by hand -> the client reconnects by itself

    uv run python tests/test_link_packer.py             # P1-P7, about 40 s
    uv run python tests/test_link_packer.py --restart   # + P8: restart the runtime when asked

The simulator side uses PackerPlc + the Heartbeat stand-in, as the real loop will. Reset (Cmd = 3) is sent
by a separate plain client that ACTS AS THE HMI: the simulator code itself never writes Cmd. At the end
the sensors are back at rest (pusher and wrap bar home, OvenTemp 20.0 degC, Airflow_OK as the blower);
Alm_CommLost comes back 3 s after the script ends, because nothing writes the heartbeat any more.
Do not run the simulator or another test script at the same time. Release all debugger forces first.
"""

import asyncio
import logging
import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map_packer as pm
from simulator import config
from simulator.machines.packer import PackerSensors
from simulator.plant import PackerWrites
from simulator.plcs import PackerPlc, PlcCommError
from simulator.standins import Heartbeat

TICK = config.TICK_S
HOST, PORT = config.PLC_HOST, config.PACKER_PORT
RESET, AUTO = 3, 1
# The machine at rest: pusher and wrap bar home, a cold oven (20.0 degC); Airflow_OK follows the blower
REST = dict(bottle_at_gate=False, bottle_counted=False, pusher_home=True, pusher_out=False, pack_at_film=False,
            film_in_slot=False, film_pulse=False, wrap_home=True, film_empty=False, film_low=False,
            oven_entry=False, oven_exit=False, table_full=False, oven_temp=200)


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class World:
    """The sensors the simulator writes, plus the heartbeat, through PackerPlc.write() every tick.

    Airflow_OK answers the OvenBlower output read in the same tick, as the oven physics would: in Auto
    the idle packer runs its blower, and a missing Airflow_OK would FAULT it after 3 s.
    """

    def __init__(self, plc: PackerPlc) -> None:
        self.plc = plc
        self.hb = Heartbeat()
        self.last_change = time.monotonic()
        self.sensors = dict(REST)
        self.blower_seen = False
        self.last: PackerWrites | None = None
        self.anim = (0, 0, 0, 0)  # HMI animation words %MW70-73

    async def run(self, seconds: float, on_tick=None, heartbeat: bool = True) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            before = self.hb.value
            if heartbeat:
                self.hb.step(TICK)
            if self.hb.value != before:
                self.last_change = time.monotonic()
            blower = (await self.plc.read_outputs()).oven_blower
            self.blower_seen |= blower
            self.last = PackerWrites(PackerSensors(airflow_ok=blower, **self.sensors), e_stop_ok=True,
                                     heartbeat=self.hb.value, anim=self.anim)
            await self.plc.write(self.last)
            if on_tick is not None:
                await on_tick()
            await asyncio.sleep(TICK)

    async def set(self, seconds: float = 1.0, **sensors) -> None:
        self.sensors.update(sensors)
        await self.run(seconds)


PLC_WORDS = pm.STATUS_START + pm.STATUS_COUNT - pm.COMPUTED_START  # %MW40-53: computed + status words


def coil(hmi: ModbusTcpClient, address: int) -> bool:
    result = hmi.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"read coil {address} failed: {result}")
    return bool(result.bits[0])


def idle(s) -> bool:
    return s.step == 0 and s.wrap_step == 0 and s.state == 0


async def link_checks(plc: PackerPlc, hmi: ModbusTcpClient) -> list[bool]:
    r = []

    # P1 connect, status, outputs; the PLC must be idle
    if not await plc.ensure_connected():
        raise SystemExit(f"FAIL  P1: no connection to {plc.host}:{plc.port} (runtime up? PLC program running?)")
    s = await plc.read_status()
    out = await plc.read_outputs()
    if not idle(s):
        raise SystemExit(f"FAIL  P1: Step {s.step}, WrapStep {s.wrap_step}, State {s.state}: the PLC is not idle. "
                         "Press Stop / Reset first (this script writes sensor values; a running sequence would react).")
    counts_before = (s.group_count, s.feed_count, s.packs_in_oven, s.pack_count)
    on = [name for name, value in vars(out).items() if value]
    alarms = [name for name, value in s.alarms.items() if value]
    r.append(check("P1 connect + status + outputs", plc.link_ok,
                   f"State {s.state}, Step {s.step}, WrapStep {s.wrap_step}, PackCount {s.pack_count}, "
                   f"GroupCount {s.group_count}, PacksInOven {s.packs_in_oven}, FeedPulses {s.feed_pulses}, "
                   f"FeedCount {s.feed_count}, "
                   f"mode {s.mode}, alarms {alarms or 'none'}, outputs on: {on or 'none'}"))

    # P2 the write sticks (a debugger force would silently reject it)
    for value in (True, False):
        await plc.write_heartbeat(value)
        await asyncio.sleep(0.1)
        if coil(hmi, pm.HEARTBEAT) != value:
            raise SystemExit("FAIL  P2: heartbeat write does not stick: release all debugger forces")
    r.append(check("P2 heartbeat write sticks", True, "read back TRUE and FALSE"))

    world = World(plc)

    # P3 heartbeat running, then Reset (as the HMI) clears a latched Alm_CommLost
    await world.run(3.0)
    hmi.write_register(pm.CMD, RESET)
    await world.run(0.5)
    s = await plc.read_status()
    r.append(check("P3 heartbeat + Reset -> Alm_CommLost FALSE", not s.alm_comm_lost, f"got {s.alm_comm_lost}"))

    # P4 10 s with the heartbeat: the alarm must never come
    seen_alarm = False
    polls = 0

    async def watch() -> None:
        nonlocal seen_alarm, polls
        polls += 1
        if polls % 5 == 0 and (await plc.read_status()).alm_comm_lost:  # every 0.5 s
            seen_alarm = True

    print("      ... heartbeat for 10 s")
    await world.run(10.0, on_tick=watch)
    r.append(check("P4 heartbeat 10 s -> no Alm_CommLost", not seen_alarm, f"alarm seen: {seen_alarm}"))

    # P5 heartbeat stopped: alarm about 3 s after the last change of the bit. Airflow is still answered.
    elapsed = None
    stopped_at = time.monotonic()

    async def poll() -> None:
        nonlocal elapsed
        if elapsed is None and (await plc.read_status()).alm_comm_lost:
            elapsed = time.monotonic() - world.last_change

    while elapsed is None and time.monotonic() - stopped_at < 6.0:
        await world.run(TICK, on_tick=poll, heartbeat=False)
    ok = elapsed is not None and 2.5 <= elapsed <= 4.0
    detail = "no alarm within 6 s" if elapsed is None else f"alarm {elapsed:.2f} s after the last change (expected ~3 s)"
    r.append(check("P5 heartbeat stopped -> Alm_CommLost", ok, detail))

    # the heartbeat runs again, Reset clears the alarm: from here on the PLC is healthy
    await world.run(1.0)
    hmi.write_register(pm.CMD, RESET)
    await world.run(0.5)

    # P6 write the whole world (both coil blocks + OvenTemp), then read it back. Bottle_At_Gate / Pack_At_Film
    # stay FALSE: the PLC is idle, but keep the sequencer inputs quiet anyway. NEVER an edge on a counted
    # input: the PLC's counters count also in IDLE (Bottle_Counted -> GroupCount, Film_Pulse -> FeedCount,
    # which is reset only in READY: the next sheet would be short; Oven_Entry / Oven_Exit -> PacksInOven,
    # PackCount)
    await world.set(1.0, pusher_out=True, film_in_slot=True, film_empty=True, film_low=True, oven_temp=1800)
    wrong = await plc.verify_writes(world.last)
    r.append(check("P6 write() + verify_writes()", wrong == [], f"signals that differ: {wrong or 'none'}"))
    await world.set(0.5, **REST)

    # P6b the animation words: written by the simulator, read by FUXA, ignored by the PLC
    status_before = hmi.read_holding_registers(pm.COMPUTED_START, count=PLC_WORDS).registers
    world.anim = (0b110_0000_0011, 0b100_0000_0001, 5, pm.SHEET_UNDER_PACK)
    await world.run(1.0)
    words = hmi.read_holding_registers(pm.ANIM_START, count=pm.ANIM_COUNT).registers
    status_after = hmi.read_holding_registers(pm.COMPUTED_START, count=PLC_WORDS).registers
    r.append(check("P6b animation words (HR 1094-1097) for FUXA",
                   words == [0b110_0000_0011, 0b100_0000_0001, 5, 3] and status_after == status_before,
                   f"read {words} (expected [1539, 1025, 5, 3]); PLC words %MW40-53 {status_before} -> {status_after}"))
    world.anim = (0, 0, 0, 0)
    await world.run(0.5)

    # P7a OvenTemp: the PLC's hysteresis and window at the map's address, judged with its own setpoints
    setpoint, hyst, band = hmi.read_holding_registers(pm.OVEN_SETPOINT, count=3).registers
    cold_t, warm_t = setpoint - band - 100, setpoint + hyst  # below the window / above the heater band
    assert warm_t < 2300 and cold_t > 0, (setpoint, hyst, band)  # never at OvenMax
    await world.set(oven_temp=cold_t)
    cold = (coil(hmi, pm.HEAT_DEMAND), coil(hmi, pm.OVEN_READY))
    await world.set(oven_temp=warm_t)
    warm = (coil(hmi, pm.HEAT_DEMAND), coil(hmi, pm.OVEN_READY))
    r.append(check("P7a OvenTemp (HR 1036) -> Heat_Demand, Oven_Ready", cold == (True, False) and warm == (False, True),
                   f"{cold_t} -> {cold}, {warm_t} -> {warm} (expected (True, False), (False, True); "
                   f"setpoint {setpoint}, hyst {hyst}, band {band})"))
    await world.set(0.5, oven_temp=REST["oven_temp"])

    # P7b Table_Full: the PLC's Outfeed_Full filter (2 s on, 3 s off) sees the coil of the map
    await world.set(2.5, table_full=True)
    full = coil(hmi, pm.OUTFEED_FULL)
    await world.set(3.5, table_full=False)
    free = coil(hmi, pm.OUTFEED_FULL)
    r.append(check("P7b Table_Full (coil 8228) -> Outfeed_Full", full and not free,
                   f"ON 2.5 s -> {full}, OFF 3.5 s -> {free} (expected True, False)"))

    # P7c Airflow_OK answers the blower: Auto for 5 s (as the HMI; the idle packer runs its oven there), longer
    # than the 3 s AirflowTimeout; the blower must run and Alm_AirflowLost must not come. Mode restored after.
    mode = (await plc.read_status()).mode
    hmi.write_register(pm.MODE_REQ, AUTO)
    world.blower_seen = False
    await world.run(5.0)
    s = await plc.read_status()
    hmi.write_register(pm.MODE_REQ, mode)
    r.append(check("P7c Airflow_OK (coil 8229) answers OvenBlower in Auto", world.blower_seen and not s.alm_airflow_lost,
                   f"blower seen ON: {world.blower_seen}, Alm_AirflowLost {s.alm_airflow_lost} (mode {mode} restored)"))

    # at rest again: idle, and no counter moved
    await world.set(1.0, **REST)
    s = await plc.read_status()
    counts = (s.group_count, s.feed_count, s.packs_in_oven, s.pack_count)
    r.append(check("P7 PLC stayed idle, counters untouched", idle(s) and counts == counts_before,
                   f"Step {s.step}, WrapStep {s.wrap_step}, State {s.state}; GroupCount, FeedCount, PacksInOven, "
                   f"PackCount {counts_before} -> {counts}"))
    return r


async def restart_check(plc: PackerPlc) -> bool:
    """P8: the student restarts the runtime; the client must notice and reconnect by itself."""
    print("\n>>> Restart the runtime now:  docker compose restart plc-packer")
    print("    (if the PLC program does not start by itself, upload it again from the editor)")
    hb = Heartbeat()
    lost_at = None
    start = time.monotonic()
    while time.monotonic() - start < 120.0:
        if await plc.ensure_connected():
            try:
                await plc.write_heartbeat(hb.step(TICK))
                if lost_at is not None and plc.link_ok:  # back = the PLC ANSWERED, not just a TCP connect
                    return check("P8 runtime restart -> reconnect", True,
                                 f"link back {time.monotonic() - lost_at:.1f} s after it was lost")
            except PlcCommError as error:
                if lost_at is None:
                    lost_at = time.monotonic()
                    print(f"      link lost ({error}); waiting for the runtime ...")
        elif lost_at is None:
            lost_at = time.monotonic()
            print("      link lost; waiting for the runtime ...")
        await asyncio.sleep(TICK)
    return check("P8 runtime restart -> reconnect", False,
                 "no restart seen within 120 s" if lost_at is None else "link did not come back within 120 s")


async def main() -> None:
    claim_plant("test_link_packer.py", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_link_packer.py")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # our own lines only
    plc = PackerPlc()
    hmi = ModbusTcpClient(HOST, port=PORT)  # acts as the HMI (Reset only) and reads the PLC's own bits
    if not hmi.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    try:
        results = await link_checks(plc, hmi)
        if "--restart" in sys.argv:
            results.append(await restart_check(plc))
    finally:
        plc.close()
        hmi.close()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    print("Note: the heartbeat is stopped now, so Alm_CommLost comes back in 3 s until the next Reset.")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
