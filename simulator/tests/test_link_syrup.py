"""Syrup simulator stage 4: first contact of the simulator's Modbus client (SyrupPlc) with the syrup PLC.

Runs against the syrup PLC on port 5022. The PLC must be idle (Step 0, State 0): the script writes
sensor values, and a running batch would react to them. Checks:
    S1 the client connects, reads the status and the signals (outputs + Product_Available)
    S2 the heartbeat write sticks (no debugger force on %MX1.2)
    S3 heartbeat running + Reset -> Alm_CommLost FALSE
    S4 heartbeat running 10 s -> Alm_CommLost stays FALSE
    S5 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    S6 write() of the whole sensor block + analog registers, verify_writes() finds nothing wrong
    S7 the PLC reads the registers at the addresses of the map: ProductTemp -> Temp_OK,
       CO2Pressure -> CO2_Demand, BufferLevel -> Product_Available (incl. the hysteresis hold)
    S8 (--restart) the runtime is restarted by hand -> the client reconnects by itself

    uv run python tests/test_link_syrup.py             # S1-S7, about 40 s
    uv run python tests/test_link_syrup.py --restart   # + S8: restart the runtime when asked

The simulator side uses SyrupPlc + the Heartbeat stand-in, as the real loop will. Reset (Cmd = 3)
is sent by a separate plain client that ACTS AS THE HMI: the simulator code itself never writes Cmd.
At the end the sensors are back to neutral (all zero, E_Stop_OK TRUE); the alarm comes back 3 s
after the script ends, because nothing writes the heartbeat any more (the next script resets it).
Do not run the simulator or another test script at the same time. Release all debugger forces first.
"""

import asyncio
import logging
import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map_syrup as sm
from simulator import config
from simulator.machines.syrup import SyrupSensors
from simulator.plant import SyrupWrites
from simulator.plcs import PlcCommError, SyrupPlc
from simulator.standins import Heartbeat

TICK = config.TICK_S
HOST, PORT = config.PLC_HOST, config.SYRUP_PORT
RESET = 3


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class World:
    """The sensors the simulator writes, plus the heartbeat, through SyrupPlc.write() every tick."""

    def __init__(self, plc: SyrupPlc) -> None:
        self.plc = plc
        self.hb = Heartbeat()
        self.last_change = time.monotonic()
        self.sensors = dict(syrup_level=0, mix_level=0, buffer_level=0, product_temp=0, co2_pressure=0)
        self.last: SyrupWrites | None = None

    def writes(self) -> SyrupWrites:
        return SyrupWrites(SyrupSensors(mix_high_high=False, **self.sensors), e_stop_ok=True, heartbeat=self.hb.value)

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

    async def set(self, seconds: float = 1.0, **sensors: int) -> None:
        self.sensors.update(sensors)
        await self.run(seconds)


def coil(hmi: ModbusTcpClient, address: int) -> bool:
    result = hmi.read_coils(address, count=1)
    if result.isError():
        raise SystemExit(f"read coil {address} failed: {result}")
    return bool(result.bits[0])


def register(hmi: ModbusTcpClient, address: int) -> int:
    result = hmi.read_holding_registers(address, count=1)
    if result.isError():
        raise SystemExit(f"read HR {address} failed: {result}")
    return result.registers[0]


async def link_checks(plc: SyrupPlc, hmi: ModbusTcpClient) -> list[bool]:
    r = []

    # S1 connect, status, signals; the PLC must be idle
    if not await plc.ensure_connected():
        raise SystemExit(f"FAIL  S1: no connection to {plc.host}:{plc.port} (runtime up? PLC program running?)")
    s = await plc.read_status()
    sig = await plc.read_signals()
    if s.step != 0 or s.state not in (0,):
        raise SystemExit(f"FAIL  S1: Step {s.step}, State {s.state}: the PLC is not idle. Press Stop / Reset first "
                         "(this script writes sensor values; a running batch would react).")
    r.append(check("S1 connect + status + signals", plc.link_ok,
                   f"State {s.state}, Step {s.step}, BatchCount {s.batch_count}, mode {s.mode}, "
                   f"Alm_CommLost {s.alm_comm_lost}, Product_Available {sig.product_available}, "
                   f"outputs all off {not any(vars(sig.outputs).values())}"))

    # S2 the write sticks (a debugger force would silently reject it)
    for value in (True, False):
        await plc.write_heartbeat(value)
        await asyncio.sleep(0.1)
        if coil(hmi, sm.HEARTBEAT) != value:
            raise SystemExit("FAIL  S2: heartbeat write does not stick: release all debugger forces")
    r.append(check("S2 heartbeat write sticks", True, "read back TRUE and FALSE"))

    world = World(plc)

    # S3 heartbeat running, then Reset (as the HMI) clears a latched Alm_CommLost
    await world.run(3.0)
    hmi.write_register(sm.CMD, RESET)
    await world.run(0.5)
    s = await plc.read_status()
    r.append(check("S3 heartbeat + Reset -> Alm_CommLost FALSE", not s.alm_comm_lost, f"got {s.alm_comm_lost}"))

    # S4 10 s with the heartbeat: the alarm must never come
    seen_alarm = False
    polls = 0

    async def watch() -> None:
        nonlocal seen_alarm, polls
        polls += 1
        if polls % 5 == 0 and (await plc.read_status()).alm_comm_lost:  # every 0.5 s
            seen_alarm = True

    print("      ... heartbeat for 10 s")
    await world.run(10.0, on_tick=watch)
    r.append(check("S4 heartbeat 10 s -> no Alm_CommLost", not seen_alarm, f"alarm seen: {seen_alarm}"))

    # S5 heartbeat stopped: alarm about 3 s after the last change of the bit
    stopped_at = time.monotonic()
    elapsed = None
    while time.monotonic() - stopped_at < 6.0:
        if (await plc.read_status()).alm_comm_lost:
            elapsed = time.monotonic() - world.last_change
            break
        await asyncio.sleep(0.05)
    ok = elapsed is not None and 2.5 <= elapsed <= 4.0
    detail = "no alarm within 6 s" if elapsed is None else f"alarm {elapsed:.2f} s after the last change (expected ~3 s)"
    r.append(check("S5 heartbeat stopped -> Alm_CommLost", ok, detail))

    # the heartbeat runs again, Reset clears the alarm: from here on the PLC is healthy
    await world.run(1.0)
    hmi.write_register(sm.CMD, RESET)
    await world.run(0.5)

    # S6 write the whole world, then read it back
    await world.set(1.0, syrup_level=600, mix_level=250, buffer_level=500, product_temp=158, co2_pressure=350)
    wrong = await plc.verify_writes(world.last)
    r.append(check("S6 write() + verify_writes()", wrong == [], f"signals that differ: {wrong or 'none'}"))

    # S7 the PLC reads the analog registers where the map says (its own hysteresis bits, no Start needed)
    await world.set(product_temp=30)
    t_cold = coil(hmi, sm.TEMP_OK)
    await world.set(product_temp=60)
    t_warm = coil(hmi, sm.TEMP_OK)
    r.append(check("S7a ProductTemp (HR 1038) -> Temp_OK", t_cold and not t_warm,
                   f"30 -> {t_cold}, 60 -> {t_warm} (expected True, False)"))
    await world.set(co2_pressure=0)
    p_low = coil(hmi, sm.CO2_DEMAND)
    await world.set(co2_pressure=400)
    p_high = coil(hmi, sm.CO2_DEMAND)
    r.append(check("S7b CO2Pressure (HR 1039) -> CO2_Demand", p_low and not p_high,
                   f"0 -> {p_low}, 400 -> {p_high} (expected True, False)"))
    seen = []
    for level in (500, 110, 99):  # set at >= 120, hold between 100 and 120, reset below 100
        await world.set(buffer_level=level)
        seen.append((await plc.read_signals()).product_available)
    r.append(check("S7c BufferLevel (HR 1040) -> Product_Available via read_signals()", seen == [True, True, False],
                   f"500 -> {seen[0]}, 110 -> {seen[1]}, 99 -> {seen[2]} (expected True, True, False)"))

    # neutral again: all sensors zero, E_Stop_OK TRUE
    await world.set(1.0, syrup_level=0, mix_level=0, buffer_level=0, product_temp=0, co2_pressure=0)
    s = await plc.read_status()
    r.append(check("S7 PLC stayed idle", s.step == 0 and s.state == 0, f"Step {s.step}, State {s.state}"))
    return r


async def restart_check(plc: SyrupPlc) -> bool:
    """S8: the student restarts the runtime; the client must notice and reconnect by itself."""
    print("\n>>> Restart the runtime now:  docker compose restart plc-syrup-carb")
    print("    (if the PLC program does not start by itself, upload it again from the editor)")
    hb = Heartbeat()
    lost_at = None
    start = time.monotonic()
    while time.monotonic() - start < 120.0:
        if await plc.ensure_connected():
            try:
                await plc.write_heartbeat(hb.step(TICK))
                if lost_at is not None and plc.link_ok:  # back = the PLC ANSWERED, not just a TCP connect
                    return check("S8 runtime restart -> reconnect", True,
                                 f"link back {time.monotonic() - lost_at:.1f} s after it was lost")
            except PlcCommError as error:
                if lost_at is None:
                    lost_at = time.monotonic()
                    print(f"      link lost ({error}); waiting for the runtime ...")
        elif lost_at is None:
            lost_at = time.monotonic()
            print("      link lost; waiting for the runtime ...")
        await asyncio.sleep(TICK)
    return check("S8 runtime restart -> reconnect", False,
                 "no restart seen within 120 s" if lost_at is None else "link did not come back within 120 s")


async def main() -> None:
    claim_plant("test_link_syrup.py", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_link_syrup.py")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # our own lines only
    plc = SyrupPlc()
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
