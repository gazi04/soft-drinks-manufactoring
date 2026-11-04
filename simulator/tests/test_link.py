"""Stage 5: first contact of the simulator's Modbus client (plcs.py) with the filler PLC.

Writes ONLY the heartbeat; no sensor is touched. Checks:
    L1 the client connects and reads the PLC status
    L2 the heartbeat write sticks (no debugger force on %MX1.2)
    L3 heartbeat running + Reset -> Alm_CommLost FALSE
    L4 heartbeat running 30 s -> Alm_CommLost stays FALSE
    L5 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    L6 (--restart) the runtime is restarted by hand -> the client reconnects by itself

    uv run python tests/test_link.py             # L1-L5, about 40 s
    uv run python tests/test_link.py --restart   # + L6: restart the runtime when asked

The simulator side uses FillerPlc + the Heartbeat stand-in, exactly as the real
loop will. The Reset (Cmd = 3) is sent by a separate plain client that ACTS AS
THE HMI: the simulator code itself never writes Cmd. Do not run the simulator or
another test script at the same time. Release all debugger forces first.
"""

import asyncio
import logging
import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant

from simulator import config
from simulator.address_map import CMD, HEARTBEAT
from simulator.plcs import FillerPlc, PlcCommError
from simulator.standins import Heartbeat

TICK = config.TICK_S


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class HeartbeatLink:
    """Heartbeat stand-in + FillerPlc: toggles and writes the bit every tick."""

    def __init__(self, plc: FillerPlc) -> None:
        self.plc = plc
        self.hb = Heartbeat()
        self.last_change = time.monotonic()

    async def run(self, seconds: float, on_tick=None) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            before = self.hb.value
            await self.plc.write_heartbeat(self.hb.step(TICK))
            if self.hb.value != before:
                self.last_change = time.monotonic()
            if on_tick is not None:
                await on_tick()
            await asyncio.sleep(TICK)


async def link_checks(plc: FillerPlc, hmi: ModbusTcpClient) -> list[bool]:
    r = []

    # L1 connect + status
    ok = await plc.ensure_connected()
    if not ok:
        raise SystemExit(f"FAIL  L1: no connection to {plc.host}:{plc.port} (runtime up? PLC program running?)")
    s = await plc.read_status()
    r.append(check("L1 connect + read status", plc.link_ok, f"link_ok {plc.link_ok}, State {s.state}, Step {s.step}, GoodCount {s.good_count}, "
                   f"Alm_CommLost {s.alm_comm_lost}, FillSetpoint {s.fill_setpoint}"))

    # L2 the write sticks (a debugger force would silently reject it)
    for value in (True, False):
        await plc.write_heartbeat(value)
        await asyncio.sleep(0.1)
        if hmi.read_coils(HEARTBEAT, count=1).bits[0] != value:
            raise SystemExit("FAIL  L2: heartbeat write does not stick: release all debugger forces")
    r.append(check("L2 heartbeat write sticks", True, "read back TRUE and FALSE"))

    link = HeartbeatLink(plc)

    # L3 heartbeat running, then Reset (as the HMI) clears a latched Alm_CommLost
    await link.run(3.0)
    hmi.write_register(CMD, 3)
    await link.run(0.5)
    s = await plc.read_status()
    r.append(check("L3 heartbeat + Reset -> Alm_CommLost FALSE", not s.alm_comm_lost, f"got {s.alm_comm_lost}"))

    # L4 30 s with the heartbeat: the alarm must never come
    seen_alarm = False
    polls = 0

    async def watch() -> None:
        nonlocal seen_alarm, polls
        polls += 1
        if polls % 5 == 0 and (await plc.read_status()).alm_comm_lost:  # every 0.5 s
            seen_alarm = True

    print("      ... heartbeat for 30 s")
    await link.run(30.0, on_tick=watch)
    r.append(check("L4 heartbeat 30 s -> no Alm_CommLost", not seen_alarm, f"alarm seen: {seen_alarm}"))

    # L5 heartbeat stopped: alarm about 3 s after the last change of the bit
    stopped_at = time.monotonic()
    elapsed = None
    while time.monotonic() - stopped_at < 6.0:
        if (await plc.read_status()).alm_comm_lost:
            elapsed = time.monotonic() - link.last_change
            break
        await asyncio.sleep(0.05)
    ok = elapsed is not None and 2.5 <= elapsed <= 4.0
    detail = "no alarm within 6 s" if elapsed is None else f"alarm {elapsed:.2f} s after the last change (expected ~3 s)"
    r.append(check("L5 heartbeat stopped -> Alm_CommLost", ok, detail))
    return r


async def restart_check(plc: FillerPlc) -> bool:
    """L6: the student restarts the runtime; the client must notice and reconnect by itself."""
    print("\n>>> Restart the runtime now:  docker compose restart openplc")
    print("    (if the PLC program does not start by itself, start it in the editor/runtime)")
    hb = Heartbeat()
    lost_at = None
    start = time.monotonic()
    while time.monotonic() - start < 120.0:
        if await plc.ensure_connected():
            try:
                await plc.write_heartbeat(hb.step(TICK))
                # "back" = the PLC ANSWERED (link_ok), not just a TCP connect: Docker's port
                # proxy may accept connections while the runtime is still restarting
                if lost_at is not None and plc.link_ok:
                    return check("L6 runtime restart -> reconnect", True,
                                 f"link back {time.monotonic() - lost_at:.1f} s after it was lost")
            except PlcCommError as error:
                if lost_at is None:
                    lost_at = time.monotonic()
                    print(f"      link lost ({error}); waiting for the runtime ...")
        elif lost_at is None:
            lost_at = time.monotonic()
            print("      link lost; waiting for the runtime ...")
        await asyncio.sleep(TICK)
    return check("L6 runtime restart -> reconnect", False,
                 "no restart seen within 120 s" if lost_at is None else "link did not come back within 120 s")


async def main() -> None:
    claim_plant("test_link.py")  # writes the sensors: never with the simulator (plant_lock.py)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # our own lines only
    plc = FillerPlc()
    hmi = ModbusTcpClient(config.PLC_HOST, port=config.PLC_PORT)  # acts as the HMI (Reset only)
    hmi.connect()
    try:
        results = await link_checks(plc, hmi)
        if "--restart" in sys.argv:
            results.append(await restart_check(plc))
    finally:
        plc.close()
        hmi.close()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    print("Note: the heartbeat is stopped now, so Alm_CommLost stays latched until the next Reset.")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
