"""Blow moulder simulator stage 4: first contact of the simulator's Modbus client (BlowmoulderPlc) with the PLC.

Runs against the blow moulder PLC on port 5025. The PLC must be idle (Step 0, MouldStep 0, State 0): the script
writes sensor values, and a running sequence would react to them. Checks:
    M1 the client connects, reads the status (both steps, computed words, 11 alarms) and the outputs incl. LampPower
    M2 the heartbeat write sticks (no debugger force on %MX1.2)
    M3 heartbeat running + Reset -> Alm_CommLost FALSE
    M4 heartbeat running 10 s -> Alm_CommLost stays FALSE
    M5 heartbeat stopped -> Alm_CommLost TRUE about 3 s after its last change
    M6 write() of both coil blocks (%MX0-1 and %MX4) + both analog words (a NEGATIVE raw pressure),
       verify_writes() finds nothing wrong
    M7 the PLC reads the sensors at the addresses of the map (no Start needed):
       a) OvenTemp (HR 1036) at three temperatures (below the window, 3 degC low, at the setpoint) -> Oven_Ready,
          OvenError, Lamp_Calc (rule 5 with the PLC's own setpoints: clamped, P term, Bias only);
          LampPower (HR 0) stays 0: the lamps are only on in Auto with a preform
       b) BlowPressure_Raw (HR 1037): 24192 counts -> BlowPressure 350 (35.0 bar), Pressure_Reached against the
          PLC's BlowSetpoint, Pressure_Safe FALSE; -500 counts -> -7 (signed both ways), Pressure_Safe TRUE,
          no Alm_PressureSensor (inside RawMin -1000)
       c) C1_Full (coil 8228) -> Outfeed_Full after the 2 s filter, released after 3 s
       d) at rest: still idle, BottleCount untouched, LampPower 0 on every tick, no alarm that FAULTs
    M8 (--restart) the runtime is restarted by hand -> the client reconnects by itself

    uv run python tests/test_link_blowmoulder.py             # M1-M7, about 40 s
    uv run python tests/test_link_blowmoulder.py --restart   # + M8: restart the runtime when asked

What the script never does (the PLC would FAULT or count): OvenTemp >= OvenMax 1300 (in every mode), a raw
pressure outside -1000..32511 or >= MaxPressure 400, an edge on Bottle_Ejected (Bottle_Counter counts in IDLE too).

The simulator side uses BlowmoulderPlc + the Heartbeat stand-in, as the real loop will. Reset (Cmd = 3) is sent
by a separate plain client that ACTS AS THE HMI: the simulator code itself never writes Cmd. At the end the
sensors are back at rest (arm home, mould open, rod up, OvenTemp 20.0 degC, 0 counts); Alm_CommLost comes back
3 s after the script ends, because nothing writes the heartbeat any more.
Do not run the simulator or another test script at the same time. Release all debugger forces first.
"""

import asyncio
import logging
import sys
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

from simulator import address_map_blowmoulder as bm
from simulator import config
from simulator.machines.blowmoulder import BlowmoulderSensors
from simulator.plant import BlowmoulderWrites
from simulator.plcs import BlowmoulderPlc, PlcCommError, from_register
from simulator.standins import Heartbeat

TICK = config.TICK_S
HOST, PORT = config.PLC_HOST, config.BLOWMOULDER_PORT
RESET = 3
RAW_35_BAR = 24192  # round(35.0 / 40 * 27648)
RAW_NEGATIVE = -500  # below 0 bar, still inside RawMin -1000: no Alm_PressureSensor
# The machine at rest: arm home, mould open, rod up, an empty cold oven (20.0 degC), no pressure
REST = dict(preform_at_loader=False, preform_in_oven=False, arm_at_oven=True, arm_at_mould=False,
            mould_closed=False, mould_open=True, rod_up=True, rod_down=False, bottle_in_mould=False,
            bottle_ejected=False, hopper_empty=False, preform_low=False, c1_full=False, oven_temp=200,
            blow_pressure_raw=0)
FAULT_ALARMS = ("Alm_HandlingJam", "Alm_MouldFault", "Alm_OvenOverTemp", "Alm_OvenHeatTimeout", "Alm_BlowFault",
                "Alm_ExhaustFault", "Alm_OverPressure", "Alm_PressureSensor")


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


class World:
    """The sensors the simulator writes, plus the heartbeat, through BlowmoulderPlc.write() every tick.
    It also reads the outputs every tick, as the loop will, and remembers the highest LampPower seen."""

    def __init__(self, plc: BlowmoulderPlc) -> None:
        self.plc = plc
        self.hb = Heartbeat()
        self.last_change = time.monotonic()
        self.sensors = dict(REST)
        self.lamp_max = 0
        self.last: BlowmoulderWrites | None = None

    async def run(self, seconds: float, on_tick=None, heartbeat: bool = True) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            before = self.hb.value
            if heartbeat:
                self.hb.step(TICK)
            if self.hb.value != before:
                self.last_change = time.monotonic()
            self.lamp_max = max(self.lamp_max, (await self.plc.read_outputs()).lamp_power)
            self.last = BlowmoulderWrites(BlowmoulderSensors(**self.sensors), e_stop_ok=True, heartbeat=self.hb.value)
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


def words(hmi: ModbusTcpClient, address: int, count: int) -> list[int]:
    result = hmi.read_holding_registers(address, count=count)
    if result.isError():
        raise SystemExit(f"read registers {address} failed: {result}")
    return [from_register(r) for r in result.registers]


def idle(s) -> bool:
    return s.step == 0 and s.mould_step == 0 and s.state == 0


def plc_lamp_calc(temp: int, setpoint: int, kp: int, bias: int) -> int:
    """Rule 5 (Lamp_Calc, every scan): LIMIT(0, Bias + Kp * LIMIT(-1000, error, 1000) / 10, 100), INT division."""
    error = max(-1000, min(setpoint - temp, 1000))
    return max(0, min(bias + int(kp * error / 10), 100))


async def link_checks(plc: BlowmoulderPlc, hmi: ModbusTcpClient) -> list[bool]:
    r = []

    # M1 connect, status, outputs; the PLC must be idle
    if not await plc.ensure_connected():
        raise SystemExit(f"FAIL  M1: no connection to {plc.host}:{plc.port} (runtime up? PLC program running?)")
    s = await plc.read_status()
    out = await plc.read_outputs()
    if not idle(s):
        raise SystemExit(f"FAIL  M1: Step {s.step}, MouldStep {s.mould_step}, State {s.state}: the PLC is not idle. "
                         "Press Stop / Reset first (this script writes sensor values; a running sequence would react).")
    count_before = s.bottle_count
    on = [name for name, value in vars(out).items() if value and name != "lamp_power"]
    alarms = [name for name, value in s.alarms.items() if value]
    r.append(check("M1 connect + status + outputs", plc.link_ok,
                   f"State {s.state}, Step {s.step}, MouldStep {s.mould_step}, BottleCount {s.bottle_count}, "
                   f"Lamp_Calc {s.lamp_calc} %, BlowPressure {s.blow_pressure}, OvenError {s.oven_error}, "
                   f"mode {s.mode}, alarms {alarms or 'none'}, outputs on: {on or 'none'}, "
                   f"LampPower {out.lamp_power} %"))

    # M2 the write sticks (a debugger force would silently reject it)
    for value in (True, False):
        await plc.write_heartbeat(value)
        await asyncio.sleep(0.1)
        if coil(hmi, bm.HEARTBEAT) != value:
            raise SystemExit("FAIL  M2: heartbeat write does not stick: release all debugger forces")
    r.append(check("M2 heartbeat write sticks", True, "read back TRUE and FALSE"))

    world = World(plc)

    # M3 heartbeat running (sensors at rest), then Reset (as the HMI) clears a latched Alm_CommLost and anything
    # an earlier test left latched whose cause is gone now
    await world.run(3.0)
    hmi.write_register(bm.CMD, RESET)
    await world.run(0.5)
    s = await plc.read_status()
    r.append(check("M3 heartbeat + Reset -> Alm_CommLost FALSE", not s.alm_comm_lost, f"got {s.alm_comm_lost}"))

    # M4 10 s with the heartbeat: the alarm must never come
    seen_alarm = False
    polls = 0

    async def watch() -> None:
        nonlocal seen_alarm, polls
        polls += 1
        if polls % 5 == 0 and (await plc.read_status()).alm_comm_lost:  # every 0.5 s
            seen_alarm = True

    print("      ... heartbeat for 10 s")
    await world.run(10.0, on_tick=watch)
    r.append(check("M4 heartbeat 10 s -> no Alm_CommLost", not seen_alarm, f"alarm seen: {seen_alarm}"))

    # M5 heartbeat stopped: alarm about 3 s after the last change of the bit
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
    r.append(check("M5 heartbeat stopped -> Alm_CommLost", ok, detail))

    # the heartbeat runs again, Reset clears the alarm: from here on the PLC is healthy
    await world.run(1.0)
    hmi.write_register(bm.CMD, RESET)
    await world.run(0.5)

    # M6 write the whole world (both coil blocks + both analog words, the raw pressure negative), read it back.
    # Never an edge on Bottle_Ejected (BottleCount counts in IDLE too); the sequencer inputs are idle anyway.
    await world.set(1.0, preform_at_loader=True, rod_down=True, bottle_in_mould=True, hopper_empty=True,
                    preform_low=True, oven_temp=900, blow_pressure_raw=RAW_NEGATIVE)
    wrong = await plc.verify_writes(world.last)
    r.append(check("M6 write() + verify_writes()", wrong == [], f"signals that differ: {wrong or 'none'}"))
    await world.set(0.5, **REST)

    # M7a OvenTemp: the window, OvenError and Lamp_Calc at the map's address, judged with the PLC's own setpoints
    setpoint, band, kp, bias, blow_setpoint = words(hmi, bm.OVEN_SETPOINT, bm.SETPOINTS_COUNT)
    # cold: below the window (lamps clamped at 100 %); in the P band: 3 degC low, inside the window (Kp 10 -> 30 %,
    # the steady offset of rule 5); at the setpoint: only Bias
    cold_t, band_t, warm_t = setpoint - band - 100, setpoint - 30, setpoint
    assert 0 < cold_t and warm_t < 1300, (setpoint, band)  # never at OvenMax
    results = []
    for temp in (cold_t, band_t, warm_t):
        await world.set(oven_temp=temp)
        lamp_calc, _, oven_error = words(hmi, bm.COMPUTED_START, bm.COMPUTED_COUNT)
        results.append((temp, coil(hmi, bm.OVEN_READY), oven_error, lamp_calc,
                        plc_lamp_calc(temp, setpoint, kp, bias)))
    ok = (not results[0][1] and results[1][1] == (band >= 30) and results[2][1]
          and all(err == setpoint - t and calc == want for t, _, err, calc, want in results)
          and world.lamp_max == 0)
    r.append(check("M7a OvenTemp (HR 1036) -> Oven_Ready, OvenError, Lamp_Calc; LampPower 0", ok,
                   "; ".join(f"{t} -> ready {rd}, OvenError {err}, Lamp_Calc {calc} % (expected {want} %)"
                             for t, rd, err, calc, want in results)
                   + f"; LampPower max {world.lamp_max} % (setpoint {setpoint}, band {band}, Kp {kp}, Bias {bias})"))
    await world.set(0.5, oven_temp=REST["oven_temp"])

    # M7b BlowPressure_Raw: the PLC's DINT scaling and the signed path, against its own BlowSetpoint
    await world.set(blow_pressure_raw=RAW_35_BAR)
    high = (words(hmi, bm.BLOW_PRESSURE, 1)[0], coil(hmi, bm.PRESSURE_REACHED), coil(hmi, bm.PRESSURE_SAFE))
    await world.set(blow_pressure_raw=RAW_NEGATIVE)
    low = (words(hmi, bm.BLOW_PRESSURE, 1)[0], coil(hmi, bm.PRESSURE_REACHED), coil(hmi, bm.PRESSURE_SAFE))
    sensor_alarm = (await plc.read_status()).alm_pressure_sensor
    ok = high == (350, 350 >= blow_setpoint, False) and low == (-7, False, True) and not sensor_alarm
    r.append(check("M7b BlowPressure_Raw (HR 1037) -> BlowPressure, Pressure_Reached, Pressure_Safe", ok,
                   f"{RAW_35_BAR} -> {high}, {RAW_NEGATIVE} -> {low} (expected (350, {350 >= blow_setpoint}, False), "
                   f"(-7, False, True); BlowSetpoint {blow_setpoint}), Alm_PressureSensor {sensor_alarm}"))
    await world.set(0.5, blow_pressure_raw=0)

    # M7c C1_Full: the PLC's Outfeed_Full filter (2 s on, 3 s off) sees the coil of the map
    await world.set(2.5, c1_full=True)
    full = coil(hmi, bm.OUTFEED_FULL)
    await world.set(3.5, c1_full=False)
    free = coil(hmi, bm.OUTFEED_FULL)
    r.append(check("M7c C1_Full (coil 8228) -> Outfeed_Full", full and not free,
                   f"ON 2.5 s -> {full}, OFF 3.5 s -> {free} (expected True, False)"))

    # M7d at rest again: idle, the counter did not move, the lamps never came on, nothing FAULTed
    await world.set(1.0, **REST)
    s = await plc.read_status()
    faults = [n for n in FAULT_ALARMS if s.alarms[n]]
    r.append(check("M7d PLC stayed idle, BottleCount untouched, LampPower 0, no FAULT",
                   idle(s) and s.bottle_count == count_before and world.lamp_max == 0 and not faults,
                   f"Step {s.step}, MouldStep {s.mould_step}, State {s.state}; BottleCount {count_before} -> "
                   f"{s.bottle_count}; LampPower max {world.lamp_max} %; FAULT alarms {faults or 'none'}"))
    return r


async def restart_check(plc: BlowmoulderPlc) -> bool:
    """M8: the student restarts the runtime; the client must notice and reconnect by itself."""
    print("\n>>> Restart the runtime now:  docker compose restart plc-blowmolder")
    print("    (if the PLC program does not start by itself, upload it again from the editor)")
    hb = Heartbeat()
    lost_at = None
    start = time.monotonic()
    while time.monotonic() - start < 120.0:
        if await plc.ensure_connected():
            try:
                await plc.write_heartbeat(hb.step(TICK))
                if lost_at is not None and plc.link_ok:  # back = the PLC ANSWERED, not just a TCP connect
                    return check("M8 runtime restart -> reconnect", True,
                                 f"link back {time.monotonic() - lost_at:.1f} s after it was lost")
            except PlcCommError as error:
                if lost_at is None:
                    lost_at = time.monotonic()
                    print(f"      link lost ({error}); waiting for the runtime ...")
        elif lost_at is None:
            lost_at = time.monotonic()
            print("      link lost; waiting for the runtime ...")
        await asyncio.sleep(TICK)
    return check("M8 runtime restart -> reconnect", False,
                 "no restart seen within 120 s" if lost_at is None else "link did not come back within 120 s")


async def main() -> None:
    claim_plant("test_link_blowmoulder.py", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_link_blowmoulder.py")
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)  # our own lines only
    plc = BlowmoulderPlc()
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
