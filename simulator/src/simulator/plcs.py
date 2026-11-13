"""Modbus links to the PLCs. The ONLY file of the simulator that knows Modbus.

    PlcLink     connection handling shared by every PLC (connect, reconnect, guarded requests)
    FillerPlc   the filler (port 502), described below
    SyrupPlc    the syrup room (port 5022): same pattern, own addresses (address_map_syrup.py);
                its reads and writes are documented at the class

    PLC (OpenPLC runtime, Modbus TCP server)
      coils 0-5            ── read_outputs() ──►  FillerOutputs   ──► plant.step()
      coils 8192-8202      ◄── write() ────────   SimWrites       ◄──
      HR 1036 (FillLevel)  ◄──
      HR 1074-1077, coils 8232-8235, HR 1054 ── read_status() ──► PlcStatus (event log, audit)

Two parts:
- Pure mapping functions (encode/decode): names <-> Modbus values, no network,
  unit-tested in tests/unit/test_plcs_mapping.py.
- FillerPlc: the async client. One request per block, own reconnect.

One-writer rule: the simulator may only write the addresses in
address_map.SIM_WRITE_COILS / SIM_WRITE_REGISTERS. FillerPlc checks this on every
write, so a wrong address raises an error instead of overwriting an HMI command.

The sensor coils and FillLevel are two separate requests, so the PLC can scan
once between them (20 ms). This is harmless: FillLevel only changes while S4 is
steady ON, or drops to 0 while the belt moves (S4 OFF); it never changes in the
same tick as an S4 edge.

How the link handles a PLC that goes away (see FillerPlc for details):
- the pymodbus client is created lazily, a fresh one for every connection, inside the
  running asyncio loop (pymodbus binds a client to the loop that exists when it is made);
- every request first gets the client from _live_client() and then creates and awaits
  the request with no `await` in between, so pymodbus can never raise "not connected"
  outside our error handling;
- `link_ok` means "the last request was answered", not "TCP is connected" (Docker's
  port proxy accepts connections even while the runtime restarts);
- an error REPLY (PlcReplyError) keeps the link: the PLC answered, the request is wrong.
"""

import asyncio
import time
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from simulator import config
from simulator.address_map import (
    ANIM_COUNT,
    ANIM_START,
    MODE_REQ,
    ALARMS_COUNT,
    ALARMS_START,
    E_STOP_OK,
    FILL_LEVEL,
    CAP_TIME,
    FILL_SETPOINT,
    HEARTBEAT,
    OUTPUTS_COUNT,
    OUTPUTS_START,
    PRODUCT_AVAILABLE,
    RINSE_TIME,
    S2,
    S3,
    S4,
    S5,
    S6,
    S7,
    S8,
    SENSOR_BLOCK,
    SENSOR_BLOCK_START,
    SIM_WRITE_COILS,
    SIM_WRITE_REGISTERS,
    STATUS_COUNT,
    STATUS_START,
    UNUSED_MX0_7,
)
from simulator import address_map_syrup as sm
from simulator.machines.filler import FillerOutputs
from simulator.machines.syrup import SyrupOutputs
from simulator.plant import SimWrites, SyrupSignals, SyrupWrites

RECONNECT_S = 2.0  # default: at most one connection attempt every 2 s while the PLC is away
TIMEOUT_S = 1.0  # one request may take at most 1 s (a tick is 0.1 s; pymodbus default is 3 s)
SETPOINTS_COUNT = 3  # FillSetpoint, RinseTime, CapTime = %MW30..32, read in one request
INT_MAX = 32767  # %MW is a signed 16-bit INT in the PLC

# The sensor block must be one contiguous run of coils, all owned by the simulator
assert list(SENSOR_BLOCK) == list(range(SENSOR_BLOCK_START, SENSOR_BLOCK_START + len(SENSOR_BLOCK)))
assert set(SENSOR_BLOCK) <= SIM_WRITE_COILS
assert (RINSE_TIME, CAP_TIME) == (FILL_SETPOINT + 1, FILL_SETPOINT + 2)  # one block


class PlcCommError(Exception):
    """A Modbus request failed: no connection, timeout or broken link. The client is dropped."""


class PlcReplyError(PlcCommError):
    """The PLC answered with a Modbus error (e.g. illegal address). The link stays up.

    This is a configuration problem, not a network one: reconnecting would not help.
    """


# --- Pure mapping ------------------------------------------------------------------
def decode_outputs(bits: list[bool]) -> FillerOutputs:
    """Coils 0-5 (%QX0.0-0.5) -> FillerOutputs. pymodbus pads bits to 8, so slice."""
    return FillerOutputs(*bits[:OUTPUTS_COUNT])


# Which SimWrites field goes onto which coil of the sensor block (None = always False)
BLOCK_FIELDS = {
    S2: "s2",
    S3: "s3",
    S4: "s4",
    S5: "s5",
    S6: "s6",
    S7: "s7",
    S8: "s8",
    UNUSED_MX0_7: None,
    PRODUCT_AVAILABLE: "product_available",
    E_STOP_OK: "e_stop_ok",
    HEARTBEAT: "heartbeat",
}
assert set(BLOCK_FIELDS) == set(SENSOR_BLOCK)


def encode_sensor_block(w: SimWrites) -> list[bool]:
    """SimWrites -> the 11 coil values of the bulk write, in SENSOR_BLOCK order.

    Each value is looked up by its ADDRESS (BLOCK_FIELDS), so the order of
    SENSOR_BLOCK can never shift a value onto the wrong coil.
    """
    return [bool(getattr(w, BLOCK_FIELDS[a])) if BLOCK_FIELDS[a] else False for a in SENSOR_BLOCK]


def foreign_writes(w: SimWrites, coils: list[bool], fill_level: int) -> list[str]:
    """Signals whose read-back value differs from what the simulator wrote.

    The PLC never writes these addresses, so a difference means somebody else does:
    a debugger force (the PLC rejects our write silently) or a second writer such as a
    test script running at the same time.
    """
    expected = encode_sensor_block(w)
    wrong = [BLOCK_FIELDS[a] or f"coil {a}" for a, e, c in zip(SENSOR_BLOCK, expected, coils) if e != c]
    if fill_level != encode_fill_level(w.fill_level):
        wrong.append("fill_level")
    return wrong


def encode_fill_level(ml: float) -> int:
    """ml -> register value, clamped to what a PLC INT can hold."""
    return max(0, min(INT_MAX, round(ml)))


def encode_animation(w: SimWrites) -> list[int]:
    """The 6 animation words %MW70..75 (C1 slots, C2 slots, 4 pocket codes)."""
    words = [int(v) for v in w.anim]
    assert len(words) == ANIM_COUNT and all(0 <= v <= INT_MAX for v in words)
    return words


MODE_NAMES = {0: "Stop", 1: "Auto", 2: "Manual"}
STEP_NAMES = {0: "IDLE", 10: "WORK", 15: "WAIT_OUTFEED", 20: "INDEX", 900: "FAULT"}
STATE_NAMES = {0: "Stopped", 1: "Running", 2: "Starved", 3: "Blocked", 9: "Fault"}
ALARM_NAMES = ("Alm_IndexJam", "Alm_FillTimeout", "Alm_OutfeedBlocked", "Alm_CommLost")


@dataclass(frozen=True)
class PlcStatus:
    """What the PLC reports about itself (read for the event log and the audit)."""

    state: int  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault
    step: int  # 0 IDLE, 10 WORK, 15 WAIT_OUTFEED, 20 INDEX, 900 FAULT
    good_count: int
    alm_index_jam: bool
    alm_fill_timeout: bool
    alm_outfeed_blocked: bool
    alm_comm_lost: bool
    fill_setpoint: int  # ml
    rinse_time_ms: int
    cap_time_ms: int
    mode: int = 1  # ModeReq as the HMI set it: 0 Stop, 1 Auto, 2 Manual (default only for tests)

    @property
    def alarms(self) -> dict[str, bool]:
        flags = (self.alm_index_jam, self.alm_fill_timeout, self.alm_outfeed_blocked, self.alm_comm_lost)
        return dict(zip(ALARM_NAMES, flags))


def decode_status(regs: list[int], alarm_bits: list[bool], setpoints: list[int], mode: int) -> PlcStatus:
    """regs = %MW50..%MW53 (State, Step, unused, GoodCount), alarm_bits = %MX5.0..5.3,
    setpoints = %MW30..%MW32 (FillSetpoint, RinseTime, CapTime), mode = %MW1 (ModeReq)."""
    state, step, _unused, good_count = regs[:STATUS_COUNT]
    jam, fill_timeout, blocked, comm_lost = alarm_bits[:ALARMS_COUNT]
    fill, rinse, cap = setpoints[:SETPOINTS_COUNT]
    return PlcStatus(state, step, good_count, jam, fill_timeout, blocked, comm_lost, fill, rinse, cap, mode)


# === Syrup room: pure mapping ==========================================================
SYRUP_STEP_NAMES = {0: "IDLE", 5: "WAIT_DEMAND", 10: "DOSE_WATER", 20: "DOSE_SYRUP", 30: "MIX", 40: "COOL",
                    50: "CARBONATE", 55: "WAIT_BUFFER", 60: "TRANSFER", 900: "FAULT"}
SYRUP_STATE_NAMES = {0: "Stopped", 1: "Running", 2: "Starved", 3: "Blocked", 9: "Fault"}
SYRUP_ALARM_NAMES = ("Alm_Overflow", "Alm_OverPressure", "Alm_CoolTimeout", "Alm_CommLost", "Alm_LowSyrup",
                     "Alm_DoseTimeout")  # address order %MX5.0 .. %MX5.5

# The three digital signals the simulator really writes; every other coil of the block (the spare
# bits %MX0.1-0.7 and the unused %MX1.0) is sent FALSE. Looked up BY ADDRESS, so the order of the
# block can never put a value on the wrong coil.
SYRUP_COIL_FIELDS = {
    sm.MIX_HIGH_HIGH: ("mix_high_high", lambda w: w.sensors.mix_high_high),
    sm.E_STOP_OK: ("e_stop_ok", lambda w: w.e_stop_ok),
    sm.HEARTBEAT: ("heartbeat", lambda w: w.heartbeat),
}
# The five analog sensors in register order %MW12..16 (the order of the bulk write)
SYRUP_ANALOG_FIELDS = ("syrup_level", "mix_level", "product_temp", "co2_pressure", "buffer_level")
INT_MIN = -32768  # %MW is a signed 16-bit INT in the PLC, a Modbus register is unsigned

assert set(SYRUP_COIL_FIELDS) <= set(sm.SENSOR_BLOCK) <= sm.SIM_WRITE_COILS
assert len(SYRUP_ANALOG_FIELDS) == sm.ANALOG_COUNT
assert sm.SYRUP_LEVEL + 4 == sm.BUFFER_LEVEL  # one contiguous block


def decode_syrup_outputs(bits: list[bool]) -> SyrupOutputs:
    """Coils 0-5 (%QX0.0-0.5) -> SyrupOutputs. pymodbus pads bits to 8, so slice."""
    return SyrupOutputs(*bits[: sm.OUTPUTS_COUNT])


def encode_syrup_sensor_block(w: SyrupWrites) -> list[bool]:
    """SyrupWrites -> the 11 coil values of the bulk write, in sm.SENSOR_BLOCK order."""
    return [bool(SYRUP_COIL_FIELDS[a][1](w)) if a in SYRUP_COIL_FIELDS else False for a in sm.SENSOR_BLOCK]


def to_register(value: int) -> int:
    """Signed INT -> the unsigned 16-bit register value (two's complement), clamped to the INT range."""
    return max(INT_MIN, min(INT_MAX, int(value))) & 0xFFFF


def from_register(raw: int) -> int:
    """Unsigned register value -> the signed INT the PLC sees."""
    return raw - 0x10000 if raw >= 0x8000 else raw


def encode_analog_block(w: SyrupWrites) -> list[int]:
    """SyrupWrites -> the 5 analog registers %MW12..16 (levels L, 0.1 degC, 0.01 bar)."""
    return [to_register(getattr(w.sensors, name)) for name in SYRUP_ANALOG_FIELDS]


def foreign_syrup_writes(w: SyrupWrites, coils: list[bool], registers: list[int]) -> list[str]:
    """Signals whose read-back value differs from what the simulator wrote (see foreign_writes)."""
    wrong = []
    for address, expected, actual in zip(sm.SENSOR_BLOCK, encode_syrup_sensor_block(w), coils):
        if expected != actual:
            wrong.append(SYRUP_COIL_FIELDS[address][0] if address in SYRUP_COIL_FIELDS else f"coil {address}")
    for name, expected, raw in zip(SYRUP_ANALOG_FIELDS, encode_analog_block(w), registers):
        if expected != raw:
            wrong.append(name)
    return wrong


@dataclass(frozen=True)
class SyrupStatus:
    """What the syrup PLC reports about itself (event log, panel)."""

    state: int  # 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault
    step: int  # 0 IDLE, 5 WAIT_DEMAND ... 60 TRANSFER, 900 FAULT
    batch_count: int
    alm_overflow: bool
    alm_over_pressure: bool
    alm_cool_timeout: bool
    alm_comm_lost: bool
    alm_low_syrup: bool  # a warning, not latched
    alm_dose_timeout: bool
    mode: int = 1  # ModeReq as the HMI set it: 0 Stop, 1 Auto, 2 Manual (default only for tests)

    @property
    def alarms(self) -> dict[str, bool]:
        flags = (self.alm_overflow, self.alm_over_pressure, self.alm_cool_timeout, self.alm_comm_lost,
                 self.alm_low_syrup, self.alm_dose_timeout)
        return dict(zip(SYRUP_ALARM_NAMES, flags))


def decode_syrup_status(regs: list[int], alarm_bits: list[bool], mode: int) -> SyrupStatus:
    """regs = %MW50..%MW53 (State, Step, unused, BatchCount), alarm_bits = %MX5.0..5.5, mode = %MW1."""
    state, step, _unused, batch_count = regs[: sm.STATUS_COUNT]
    return SyrupStatus(state, step, batch_count, *alarm_bits[: sm.ALARMS_COUNT], mode)


# --- Async client ------------------------------------------------------------------
class PlcLink:
    """Everything a Modbus link to ONE OpenPLC runtime needs, whatever the machine.

    Can be created anywhere (also outside asyncio): the pymodbus client is only made in
    ensure_connected(), which runs inside the loop, and a new one is made for every
    connection. "No client" simply means "not connected".

    A subclass names the machine: it sets `write_coils` / `write_registers` (the only
    addresses the simulator may write in THAT PLC, the one-writer rule), gives the default
    host and port, and adds its own reads and writes (read_outputs / write / read_status ...).
    """

    write_coils: frozenset[int] = frozenset()  # set by the subclass
    write_registers: frozenset[int] = frozenset()  # set by the subclass

    def __init__(self, host: str, port: int, reconnect_s: float, timeout_s: float) -> None:
        self.host = host
        self.port = port
        self.reconnect_s = reconnect_s
        self.timeout_s = timeout_s
        self.link_ok = False  # True = the last request was answered by the PLC
        self._client: AsyncModbusTcpClient | None = None
        self._last_try = float("-inf")  # monotonic time of the last connect attempt

    @property
    def connected(self) -> bool:
        """TCP connection open (not yet proof that the PLC answers: see link_ok)."""
        return self._client is not None and self._client.connected

    async def ensure_connected(self) -> bool:
        """Connect if needed, at most once per reconnect_s. True if a connection is open."""
        if self.connected:
            return True
        if time.monotonic() - self._last_try < self.reconnect_s:
            return False  # tried a moment ago: do not block every tick
        self._last_try = time.monotonic()
        self._drop()  # forget a dead client, if any
        # reconnect_delay=0: pymodbus does NOT reconnect in the background; we do it here,
        # with a fresh client, so no state of the old connection is carried over.
        client = AsyncModbusTcpClient(
            self.host, port=self.port, timeout=self.timeout_s, retries=0, reconnect_delay=0
        )
        try:
            ok = await client.connect()
        except (ModbusException, OSError, asyncio.TimeoutError):
            ok = False
        if ok:
            self._client = client
        else:
            client.close()
        return ok

    def close(self) -> None:
        """Close the link (safe to call twice)."""
        self._drop()

    def _drop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self.link_ok = False

    # --- Requests ----------------------------------------------------------------
    # Pattern of every request:
    #     client = self._live_client(what)                  # 1. link check
    #     result = await self._finish(what, client.xxx(...)) # 2. create + await, no await between
    # pymodbus raises "not connected" synchronously while CREATING a request. Asyncio switches
    # tasks only at an `await`, so the connection cannot drop between step 1 and the creation
    # in step 2: that raise can never happen. Errors in building a request (a programming
    # mistake, e.g. a wrong count) are not caught and stay loud.

    def _live_client(self, what: str) -> AsyncModbusTcpClient:
        if not self.connected:
            self._drop()
            raise PlcCommError(f"{what}: not connected to {self.host}:{self.port}")
        return self._client

    async def _finish(self, what: str, pending):
        """Await a request that was just created; convert failures into PlcCommError."""
        try:
            result = await pending
        except (ModbusException, OSError, asyncio.TimeoutError) as error:
            self._drop()  # timeout or broken link: the next ensure_connected() makes a new client
            raise PlcCommError(f"{what}: {error}") from error
        self.link_ok = True  # the PLC answered (even an error reply is an answer)
        if result.isError():
            raise PlcReplyError(f"{what}: PLC replied {result} (check mx_bits in the runtime / the address map)")
        return result

    # --- Writes, guarded by the one-writer rule -------------------------------------
    # The guard runs BEFORE the link check, so it also fires without a connection.
    async def _write_coils(self, start: int, values: list[bool]) -> None:
        addresses = set(range(start, start + len(values)))
        if not addresses <= self.write_coils:
            raise ValueError(f"simulator may not write coils {sorted(addresses - self.write_coils)}")
        what = f"write coils {start}"
        client = self._live_client(what)
        await self._finish(what, client.write_coils(start, values))

    async def _write_registers(self, start: int, values: list[int]) -> None:
        addresses = set(range(start, start + len(values)))
        if not addresses <= self.write_registers:
            raise ValueError(f"simulator may not write registers {sorted(addresses - self.write_registers)}")
        what = f"write registers {start}"
        client = self._live_client(what)
        await self._finish(what, client.write_registers(start, values))

    async def _write_register(self, address: int, value: int) -> None:
        if address not in self.write_registers:
            raise ValueError(f"simulator may not write register {address}")
        what = f"write register {address}"
        client = self._live_client(what)
        await self._finish(what, client.write_register(address, value))


class FillerPlc(PlcLink):
    """Async Modbus client of the filler PLC (port 502)."""

    write_coils = SIM_WRITE_COILS
    write_registers = SIM_WRITE_REGISTERS

    def __init__(
        self,
        host: str = config.PLC_HOST,
        port: int = config.PLC_PORT,
        reconnect_s: float = RECONNECT_S,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        super().__init__(host, port, reconnect_s, timeout_s)

    async def read_outputs(self) -> FillerOutputs:
        client = self._live_client("read outputs")
        result = await self._finish("read outputs", client.read_coils(OUTPUTS_START, count=OUTPUTS_COUNT))
        return decode_outputs(result.bits)

    async def write(self, w: SimWrites) -> None:
        """One bulk write of the sensor block, FillLevel, and the 6 animation words."""
        await self._write_coils(SENSOR_BLOCK_START, encode_sensor_block(w))
        await self._write_register(FILL_LEVEL, encode_fill_level(w.fill_level))
        await self._write_registers(ANIM_START, encode_animation(w))

    async def write_heartbeat(self, value: bool) -> None:
        """Only the heartbeat coil (first contact, stage 5)."""
        await self._write_coils(HEARTBEAT, [value])

    async def read_status(self) -> PlcStatus:
        client = self._live_client("read status")
        regs = await self._finish("read status", client.read_holding_registers(STATUS_START, count=STATUS_COUNT))
        client = self._live_client("read alarms")
        alarms = await self._finish("read alarms", client.read_coils(ALARMS_START, count=ALARMS_COUNT))
        client = self._live_client("read setpoints")
        setpoints = await self._finish(
            "read setpoints", client.read_holding_registers(FILL_SETPOINT, count=SETPOINTS_COUNT)
        )
        client = self._live_client("read mode")
        mode = await self._finish("read mode", client.read_holding_registers(MODE_REQ, count=1))  # read only
        return decode_status(regs.registers, alarms.bits, setpoints.registers, mode.registers[0])

    async def verify_writes(self, w: SimWrites) -> list[str]:
        """Read the sensor block and FillLevel back; names of the signals that differ.

        Pass the values written LAST tick and call it before the next write: the
        check then covers the whole gap between two ticks, where a second writer
        acts. Empty list = nobody else writes our addresses (the PLC never does).
        """
        client = self._live_client("read back sensors")
        coils = await self._finish(
            "read back sensors", client.read_coils(SENSOR_BLOCK_START, count=len(SENSOR_BLOCK))
        )
        client = self._live_client("read back FillLevel")
        level = await self._finish("read back FillLevel", client.read_holding_registers(FILL_LEVEL, count=1))
        return foreign_writes(w, coils.bits[: len(SENSOR_BLOCK)], level.registers[0])


class SyrupPlc(PlcLink):
    """Async Modbus client of the syrup room PLC (port 5022).

        coils 0-5, 8240                ── read_signals() ──►  SyrupSignals (plant.step)
        coils 8192-8202, HR 1036-1040  ◄── write() ────────   SyrupWrites  (plant.syrup_writes)
        HR 1074-1077, coils 8232-8237, HR 1025 ── read_status() ──► SyrupStatus

    The same pattern as FillerPlc; only the addresses (address_map_syrup.py) differ.
    """

    write_coils = sm.SIM_WRITE_COILS
    write_registers = sm.SIM_WRITE_REGISTERS

    def __init__(
        self,
        host: str = config.PLC_HOST,
        port: int = config.SYRUP_PORT,
        reconnect_s: float = RECONNECT_S,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        super().__init__(host, port, reconnect_s, timeout_s)

    async def read_signals(self) -> SyrupSignals:
        """The outputs (one request) and Product_Available (a second one: it sits 8000 addresses away).

        Product_Available may be one PLC scan (20 ms) newer than the outputs. It follows a
        hysteresis on the buffer level, so that makes no difference.
        """
        client = self._live_client("read outputs")
        outputs = await self._finish("read outputs", client.read_coils(sm.OUTPUTS_START, count=sm.OUTPUTS_COUNT))
        client = self._live_client("read Product_Available")
        available = await self._finish("read Product_Available", client.read_coils(sm.PRODUCT_AVAILABLE_OUT, count=1))
        return SyrupSignals(decode_syrup_outputs(outputs.bits), bool(available.bits[0]))

    async def write(self, w: SyrupWrites) -> None:
        """One bulk write of the sensor block and one of the five analog registers."""
        await self._write_coils(sm.SENSOR_BLOCK_START, encode_syrup_sensor_block(w))
        await self._write_registers(sm.ANALOG_START, encode_analog_block(w))

    async def write_heartbeat(self, value: bool) -> None:
        """Only the heartbeat coil (first contact)."""
        await self._write_coils(sm.HEARTBEAT, [value])

    async def read_status(self) -> SyrupStatus:
        client = self._live_client("read status")
        regs = await self._finish("read status", client.read_holding_registers(sm.STATUS_START, count=sm.STATUS_COUNT))
        client = self._live_client("read alarms")
        alarms = await self._finish("read alarms", client.read_coils(sm.ALARMS_START, count=sm.ALARMS_COUNT))
        client = self._live_client("read mode")
        mode = await self._finish("read mode", client.read_holding_registers(sm.MODE_REQ, count=1))  # read only
        return decode_syrup_status(regs.registers, alarms.bits, mode.registers[0])

    async def verify_writes(self, w: SyrupWrites) -> list[str]:
        """Read the sensor block and the analog registers back; names of the signals that differ.

        Pass the values written LAST tick and call it before the next write (see FillerPlc).
        """
        client = self._live_client("read back sensors")
        coils = await self._finish(
            "read back sensors", client.read_coils(sm.SENSOR_BLOCK_START, count=len(sm.SENSOR_BLOCK))
        )
        client = self._live_client("read back analog")
        analog = await self._finish("read back analog", client.read_holding_registers(sm.ANALOG_START, count=sm.ANALOG_COUNT))
        return foreign_syrup_writes(w, coils.bits[: len(sm.SENSOR_BLOCK)], analog.registers[: sm.ANALOG_COUNT])
