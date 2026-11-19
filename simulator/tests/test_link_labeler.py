"""Labeler simulator stage 4: first contact of the simulator's Modbus client (LabelerPlc) with the labeler PLC.

Runs against the labeler PLC on port 5023. The PLC must be idle (Step 0, State 0): the script writes
sensor values, and a running sequence would react to them. Checks:
    L1 the client connects, reads the status and the outputs
    L2 the heartbeat write sticks (no debugger force on %MX1.2)
    L3 heartbeat running + Reset -> Alm_CommLost FALSE
    L4 heartbeat running 10 s -> Alm_CommLost stays FALSE
    L5 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    L6 write() of both coil blocks (%MX0-1 and %MX4) + GlueTemp, verify_writes() finds nothing wrong
    L6b the HMI animation words (%MW70-71, HR 1094-1095) written by write() read back by a second client
        (as FUXA reads them); the PLC's status words (HR 1074-1077) keep their values
    L7 the PLC reads the sensors at the addresses of the map (no Start needed):
       a) GlueTemp (HR 1036) -> Heat_Demand, Glue_Ready and the GlueHeater output (the heater works in
          every mode); never >= 2000, GlueMax would FAULT the PLC
       b) C3_Full (coil 8196) -> Outfeed_Full after the 2 s filter, released after 3 s
    L8 (--restart) the runtime is restarted by hand -> the client reconnects by itself

    uv run python tests/test_link_labeler.py             # L1-L7, about 40 s
    uv run python tests/test_link_labeler.py --restart   # + L8: restart the runtime when asked

The simulator side uses LabelerPlc + the Heartbeat stand-in, as the real loop will. Reset (Cmd = 3)
is sent by a separate plain client that ACTS AS THE HMI: the simulator code itself never writes Cmd.
At the end the sensors are back to neutral (all FALSE, GlueTemp 0, E_Stop_OK TRUE); the alarm comes
back 3 s after the script ends, because nothing writes the heartbeat any more (the next script resets it).
Do not run the simulator or another test script at the same time. Release all debugger forces first.
"""

import asyncio
import logging
import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map_labeler as lm
from simulator import config
from simulator.machines.labeler import LabelerSensors
from simulator.plant import LabelerWrites
from simulator.plcs import LabelerPlc, PlcCommError
from simulator.standins import Heartbeat

TICK = config.TICK_S
HOST, PORT = config.PLC_HOST, config.LABELER_PORT
RESET = 3
NEUTRAL = dict(bottle_at_gate=False, bottle_in_pocket=False, bell_down=False, label_present=False, c3_full=False,
               roll_empty=False, label_mark=False, spin_pulse=False, roll_low=False, glue_low=False, glue_temp=0)


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class World:
    """The sensors the simulator writes, plus the heartbeat, through LabelerPlc.write() every tick."""

    def __init__(self, plc: LabelerPlc) -> None:
        self.plc = plc
        self.hb = Heartbeat()
        self.last_change = time.monotonic()
        self.sensors = dict(NEUTRAL)
        self.anim = (0, 0)  # %MW70 C3 slots, %MW71 pocket code
        self.last: LabelerWrites | None = None

    def writes(self) -> LabelerWrites:
        return LabelerWrites(LabelerSensors(**self.sensors), e_stop_ok=True, heartbeat=self.hb.value, anim=self.anim)

    async def run(self, seconds: float, on_tick=None, heartbeat: bool = True) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            before = self.hb.value
            if heartbeat:
                self.hb.step(TICK)
            if self.hb.value != before:
                self.last_change = time.monotonic()
            self.last = self.writes()
            await self.plc.write(self.last)
            if on_tick is not None:
                await on_tick()
            await asyncio.sleep(TICK)

    async def set(self, seconds: float = 1.0, **sensors) -> None:
        self.sensors.update(sensors)
        await self.run(seconds)


def coil(hmi: ModbusTcpClient, address: int) -> bool:
    result = hmi.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"read coil {address} failed: {result}")
    return bool(result.bits[0])


async def link_checks(plc: LabelerPlc, hmi: ModbusTcpClient) -> list[bool]:
    r = []

    # L1 connect, status, outputs; the PLC must be idle
    if not await plc.ensure_connected():
        raise SystemExit(f"FAIL  L1: no connection to {plc.host}:{plc.port} (runtime up? PLC program running?)")
    s = await plc.read_status()
    out = await plc.read_outputs()
    if s.step != 0 or s.state != 0:
        raise SystemExit(f"FAIL  L1: Step {s.step}, State {s.state}: the PLC is not idle. Press Stop / Reset first "
                         "(this script writes sensor values; a running sequence would react).")
    on = [name for name, value in vars(out).items() if value]
    r.append(check("L1 connect + status + outputs", plc.link_ok,
                   f"State {s.state}, Step {s.step}, GoodCount {s.good_count}, mode {s.mode}, "
                   f"Alm_CommLost {s.alm_comm_lost}, outputs on: {on or 'none'}"))

    # L2 the write sticks (a debugger force would silently reject it)
    for value in (True, False):
        await plc.write_heartbeat(value)
        await asyncio.sleep(0.1)
        if coil(hmi, lm.HEARTBEAT) != value:
            raise SystemExit("FAIL  L2: heartbeat write does not stick: release all debugger forces")
    r.append(check("L2 heartbeat write sticks", True, "read back TRUE and FALSE"))

    world = World(plc)

    # L3 heartbeat running, then Reset (as the HMI) clears a latched Alm_CommLost
    await world.run(3.0)
    hmi.write_register(lm.CMD, RESET)
    await world.run(0.5)
    s = await plc.read_status()
    r.append(check("L3 heartbeat + Reset -> Alm_CommLost FALSE", not s.alm_comm_lost, f"got {s.alm_comm_lost}"))

    # L4 10 s with the heartbeat: the alarm must never come
    seen_alarm = False
    polls = 0

    async def watch() -> None:
        nonlocal seen_alarm, polls
        polls += 1
        if polls % 5 == 0 and (await plc.read_status()).alm_comm_lost:  # every 0.5 s
            seen_alarm = True

    print("      ... heartbeat for 10 s")
    await world.run(10.0, on_tick=watch)
    r.append(check("L4 heartbeat 10 s -> no Alm_CommLost", not seen_alarm, f"alarm seen: {seen_alarm}"))

    # L5 heartbeat stopped: alarm about 3 s after the last change of the bit
    stopped_at = time.monotonic()
    elapsed = None
    while time.monotonic() - stopped_at < 6.0:
        if (await plc.read_status()).alm_comm_lost:
            elapsed = time.monotonic() - world.last_change
            break
        await asyncio.sleep(0.05)
    ok = elapsed is not None and 2.5 <= elapsed <= 4.0
    detail = "no alarm within 6 s" if elapsed is None else f"alarm {elapsed:.2f} s after the last change (expected ~3 s)"
    r.append(check("L5 heartbeat stopped -> Alm_CommLost", ok, detail))

    # the heartbeat runs again, Reset clears the alarm: from here on the PLC is healthy
    await world.run(1.0)
    hmi.write_register(lm.CMD, RESET)
    await world.run(0.5)

    # L6 write the whole world (both coil blocks + GlueTemp), then read it back. Bottle_At_Gate stays FALSE:
    # the PLC is idle, but keep the sensors quiet anyway
    await world.set(1.0, bell_down=True, label_present=True, roll_low=True, glue_low=True, spin_pulse=True,
                    glue_temp=1650)
    wrong = await plc.verify_writes(world.last)
    r.append(check("L6 write() + verify_writes()", wrong == [], f"signals that differ: {wrong or 'none'}"))
    await world.set(0.5, **NEUTRAL)

    # L6b the animation words: written by the simulator, read by FUXA, ignored by the PLC
    status_before = hmi.read_holding_registers(lm.STATUS_START, count=lm.STATUS_COUNT).registers
    world.anim = (0b1010_0101, lm.POCKET_LABELLED)
    await world.run(1.0)
    words = hmi.read_holding_registers(lm.ANIM_START, count=lm.ANIM_COUNT).registers
    status_after = hmi.read_holding_registers(lm.STATUS_START, count=lm.STATUS_COUNT).registers
    r.append(check("L6b animation words (HR 1094-1095) for FUXA", words == [0b1010_0101, 3] and status_after == status_before,
                   f"read {words} (expected [165, 3]); status {status_before} -> {status_after}"))
    world.anim = (0, 0)
    await world.run(0.5)

    # L7a GlueTemp: the PLC's heater hysteresis and window, and the heater output, at the map's address
    await world.set(glue_temp=1500)
    cold = (coil(hmi, lm.HEAT_DEMAND), coil(hmi, lm.GLUE_READY), (await plc.read_outputs()).glue_heater)
    await world.set(glue_temp=1700)
    warm = (coil(hmi, lm.HEAT_DEMAND), coil(hmi, lm.GLUE_READY), (await plc.read_outputs()).glue_heater)
    r.append(check("L7a GlueTemp (HR 1036) -> Heat_Demand, Glue_Ready, GlueHeater",
                   cold == (True, False, True) and warm == (False, True, False),
                   f"1500 -> {cold}, 1700 -> {warm} (expected (True, False, True), (False, True, False))"))

    # L7b C3_Full: the PLC's Outfeed_Full filter (2 s on, 3 s off) sees the coil of the map
    await world.set(2.5, c3_full=True)
    full = coil(hmi, lm.OUTFEED_FULL)
    await world.set(3.5, c3_full=False)
    free = coil(hmi, lm.OUTFEED_FULL)
    r.append(check("L7b C3_Full (coil 8196) -> Outfeed_Full", full and not free,
                   f"ON 2.5 s -> {full}, OFF 3.5 s -> {free} (expected True, False)"))

    # neutral again
    await world.set(1.0, **NEUTRAL)
    s = await plc.read_status()
    r.append(check("L7 PLC stayed idle", s.step == 0 and s.state == 0, f"Step {s.step}, State {s.state}"))
    return r


async def restart_check(plc: LabelerPlc) -> bool:
    """L8: the student restarts the runtime; the client must notice and reconnect by itself."""
    print("\n>>> Restart the runtime now:  docker compose restart plc-labeler")
    print("    (if the PLC program does not start by itself, upload it again from the editor)")
    hb = Heartbeat()
    lost_at = None
    start = time.monotonic()
    while time.monotonic() - start < 120.0:
        if await plc.ensure_connected():
            try:
                await plc.write_heartbeat(hb.step(TICK))
                if lost_at is not None and plc.link_ok:  # back = the PLC ANSWERED, not just a TCP connect
                    return check("L8 runtime restart -> reconnect", True,
                                 f"link back {time.monotonic() - lost_at:.1f} s after it was lost")
            except PlcCommError as error:
                if lost_at is None:
                    lost_at = time.monotonic()
                    print(f"      link lost ({error}); waiting for the runtime ...")
        elif lost_at is None:
            lost_at = time.monotonic()
            print("      link lost; waiting for the runtime ...")
        await asyncio.sleep(TICK)
    return check("L8 runtime restart -> reconnect", False,
                 "no restart seen within 120 s" if lost_at is None else "link did not come back within 120 s")


async def main() -> None:
    claim_plant("test_link_labeler.py", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_link_labeler.py")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # our own lines only
    plc = LabelerPlc()
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
