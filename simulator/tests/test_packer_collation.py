"""Packer ladder stage 4: collation sequencer (step decode, Group_Counter, Wrap_Clear, transitions).

Runs against the packer PLC on port 5024. No outputs exist yet: the script plays the machine by
writing the sensors and watches Step (%MW51) and WrapStep (%MW52). It keeps the heartbeat alive in
a thread, sets E_Stop_OK and Film_Empty (the wrap side then waits in WAIT_PACK), the oven setpoints (defaults 1900 / 50 / 150 -> Oven_Ready window
1750-2050) and resets. With Film_Empty the wrap sequencer stays in WAIT_PACK (10) even with a pack at the film stop.

    G0  start: Group_Counter back to 0 (Step 25 RETRACT written by hand, its reset)
    G1  setup: Mode Stop, heartbeat, Reset -> Step 0, WrapStep 0, Alm_CommLost FALSE, Oven_On FALSE
    G2  Auto -> Oven_On TRUE (no Start needed)
One group:
    G3  Start, wrap bar home, OvenTemp 150.0 degC -> Step 5 WARM_UP, WrapStep 10 WAIT_PACK
    G4  stays 5 for 1 s (oven cold);  G5  OvenTemp 190.0 degC (Oven_Ready) -> 10 COUNT
    G6  pack at the film stop (Wrap_Clear FALSE), 5 Bottle_Counted pulses -> GroupCount 5, Group_Complete FALSE, still 10
    G7  6th pulse -> GroupCount 6, Group_Complete TRUE, 15 WAIT_WRAP
    G8  pack still at the film stop (pusher home) for 4 s more -> Wrap_Clear FALSE, stays 15
    G9  Pack_At_Film FALSE: 2.5 s -> still 15;  G10  -> 20 PUSH about 3 s after (ClearTime)
    G11 Pusher_Home off, Pusher_Out on -> 25 RETRACT, GroupCount 0, Group_Complete FALSE
    G12 Pusher_Out off, Pusher_Home on -> 10 COUNT (next group)
Variants:
    G13 group pushed halfway: 6 bottles, PUSH, Mode Stop -> Step 0, WrapStep 0, GroupCount still 6
    G14 Auto + Start -> 5 -> 10 -> 15 (Group_Complete kept), G15 Wrap_Clear again after 3 s -> 20
    G16 Start with the wrap bar NOT home -> WrapStep 32 WRAP (finish the turn first)
    G17 Mode Stop -> both 0;  G18 Start with WrapStep 10 (written) and Step 0 -> nothing (both must be 0)
    G19 E_Stop_OK FALSE in COUNT -> both 0, Oven_On FALSE
    G20 Step / WrapStep 900 (written) -> Oven_On FALSE;  G21 Mode Stop keeps 900 (abort keeps FAULT;
        900 -> 0 comes with the alarms in stage 9, the script writes 0 back)

    uv run python tests/test_packer_collation.py      (~45 s)

Group_Counter and Clear_Timer have no address: watch them in the debugger if a check fails.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import threading
import time

from pymodbus.client import ModbusTcpClient

from hmi_guard import claim_plant, warn_if_fuxa

HOST = "127.0.0.1"
PORT = 5024

HR_CMD = 1024  # %MW0
HR_MODE_REQ = 1025  # %MW1
HR_OVEN_TEMP = 1036  # %MW12
HR_OVEN_SETPOINT = 1054  # %MW30
HR_OVEN_HYST = 1055  # %MW31
HR_OVEN_BAND = 1056  # %MW32
HR_GROUP_COUNT = 1064  # %MW40
HR_STEP = 1075  # %MW51
HR_WRAP_STEP = 1076  # %MW52
COIL_BOTTLE_AT_GATE = 8192  # %MX0.0
COIL_BOTTLE_COUNTED = 8193  # %MX0.1
COIL_PUSHER_HOME = 8194  # %MX0.2
COIL_PUSHER_OUT = 8195  # %MX0.3
COIL_PACK_AT_FILM = 8196  # %MX0.4
COIL_FILM_IN_SLOT = 8197  # %MX0.5
COIL_FILM_PULSE = 8198  # %MX0.6
COIL_WRAP_HOME = 8199  # %MX0.7
COIL_FILM_EMPTY = 8224  # %MX4.0
COIL_E_STOP_OK = 8201  # %MX1.1
COIL_HEARTBEAT = 8202  # %MX1.2
COIL_GROUP_COMPLETE = 8217  # %MX3.1
COIL_WRAP_CLEAR = 8222  # %MX3.6
COIL_OVEN_ON = 8223  # %MX3.7
COIL_COMMLOST = 8235  # %MX5.3

SENSOR_COILS = (COIL_BOTTLE_AT_GATE, COIL_BOTTLE_COUNTED, COIL_PUSHER_HOME, COIL_PUSHER_OUT,
                COIL_PACK_AT_FILM, COIL_FILM_IN_SLOT, COIL_FILM_PULSE, COIL_WRAP_HOME)

START, RESET = 1, 3
MODE_STOP, MODE_AUTO = 0, 1
OVEN_SETPOINT, OVEN_HYST, OVEN_BAND = 1900, 50, 150  # defaults (Oven_Ready window 1750-2050)
OVEN_HOT, OVEN_COLD = 1900, 1500
SETTLE = 0.3  # s: many scans of the 20 ms task
PULSE = 0.15  # s: length of one Bottle_Counted pulse and the gap after it (several scans each)


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def connect() -> ModbusTcpClient:
    client = ModbusTcpClient(HOST, port=PORT)
    if not client.connect():
        raise SystemExit(f"Cannot connect to OpenPLC Modbus on {HOST}:{PORT}")
    return client


class Heartbeat:
    """Toggles %MX1.2 every 0.5 s in a thread with its own client (a pymodbus client is not thread-safe)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()

    def _run(self) -> None:
        client = connect()
        value = False
        try:
            while not self._stop.is_set():
                value = not value
                client.write_coil(COIL_HEARTBEAT, value)
                self._stop.wait(0.5)
        finally:
            client.close()


class Plc:
    def __init__(self, client: ModbusTcpClient) -> None:
        self.c = client

    def write(self, address: int, value: int) -> None:
        self.c.write_register(address, value)

    def read(self, address: int) -> int:
        result = self.c.read_holding_registers(address, count=1)
        if result.isError():
            raise SystemExit(f"Read register {address} failed: {result}")
        return result.registers[0]

    def coil(self, address: int, value: bool | None = None) -> bool:
        if value is not None:
            self.c.write_coil(address, value)
            return value
        result = self.c.read_coils(address, count=1)
        if result.isError():
            raise SystemExit(f"Read coil {address} failed: {result}")
        return bool(result.bits[0])

    def pulse(self, address: int, count: int = 1) -> None:
        """Sensor TRUE for PULSE, then FALSE for PULSE, `count` times (one bottle per pulse)."""
        for _ in range(count):
            self.c.write_coil(address, True)
            time.sleep(PULSE)
            self.c.write_coil(address, False)
            time.sleep(PULSE)

    def steps(self) -> tuple[int, int]:
        result = self.c.read_holding_registers(HR_STEP, count=2)
        if result.isError():
            raise SystemExit(f"Read Step / WrapStep failed: {result}")
        return result.registers[0], result.registers[1]

    def cmd(self, value: int) -> None:
        self.write(HR_CMD, value)
        time.sleep(SETTLE)

    def mode(self, value: int) -> None:
        self.write(HR_MODE_REQ, value)
        time.sleep(SETTLE)

    def wait_step(self, expected: int, timeout: float, word: int = 0) -> tuple[bool, list[int], float]:
        """Poll Step (word 0) or WrapStep (word 1) until it equals expected;
        returns (reached, distinct steps seen, seconds)."""
        t0 = time.monotonic()
        seen: list[int] = []
        while True:
            s = self.steps()[word]
            if not seen or seen[-1] != s:
                seen.append(s)
            if s == expected:
                return True, seen, time.monotonic() - t0
            if time.monotonic() - t0 > timeout:
                return False, seen, time.monotonic() - t0
            time.sleep(0.05)

    def stays(self, expected: int, seconds: float, word: int = 0) -> tuple[bool, list[int]]:
        """Step (word 0) or WrapStep (word 1) must equal expected the whole time."""
        end = time.monotonic() + seconds
        seen: list[int] = []
        while time.monotonic() < end:
            s = self.steps()[word]
            if not seen or seen[-1] != s:
                seen.append(s)
            time.sleep(0.05)
        return seen == [expected], seen

    def clear_sensors(self) -> None:
        for address in SENSOR_COILS:
            self.c.write_coil(address, False)


def zero_group(plc: Plc) -> int:
    """Group_Counter is reset only in RETRACT: park Step at 25 in Auto (pusher not home, so it stays),
    then Mode Stop -> 0. Returns GroupCount afterwards."""
    plc.mode(MODE_STOP)
    plc.coil(COIL_PUSHER_HOME, False)
    plc.mode(MODE_AUTO)
    plc.write(HR_STEP, 25)
    time.sleep(SETTLE)
    plc.mode(MODE_STOP)
    return plc.read(HR_GROUP_COUNT)


def main() -> int:
    claim_plant("test_packer_collation", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_collation")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def reach(name: str, expected: int, timeout: float, what: str) -> tuple[bool, list[int], float]:
        ok, seen, dt = plc.wait_step(expected, timeout)
        results.append(check(name, ok, f"{what} -> Step {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})"))
        return ok, seen, dt

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds)
        results.append(check(name, ok, f"{what} -> Step stays {expected} for {seconds:g} s (seen {seen})"))

    def start_from_idle(wrap_home: bool = True) -> None:
        """Mode Stop -> both 0, Auto + Start with the oven hot -> Step 10 (or 15 with a complete group)."""
        plc.mode(MODE_STOP)
        plc.coil(COIL_WRAP_HOME, wrap_home)
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        plc.mode(MODE_AUTO)
        plc.cmd(START)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address, value in ((HR_OVEN_TEMP, OVEN_COLD), (HR_OVEN_SETPOINT, OVEN_SETPOINT),
                               (HR_OVEN_HYST, OVEN_HYST), (HR_OVEN_BAND, OVEN_BAND)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        # film roll empty for the whole script: the wrap sequencer then waits in WAIT_PACK (10) with a
        # pack at the film stop instead of feeding (its transitions exist from stage 5)
        plc.coil(COIL_FILM_EMPTY, True)
        # the sensor writes must reach the PLC: a debugger force would block them
        plc.coil(COIL_BOTTLE_COUNTED, True)
        time.sleep(SETTLE)
        if not plc.coil(COIL_BOTTLE_COUNTED):
            raise SystemExit("Bottle_Counted does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_BOTTLE_COUNTED, False)
        heartbeat.start()
        # at least 2 toggles before Reset: Alm_CommLost clears only once the PLC has seen a change
        time.sleep(1.5)
        plc.cmd(RESET)
        for address in (HR_STEP, HR_WRAP_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        count = zero_group(plc)
        results.append(check("G0", count == 0, f"Step 25 (RETRACT) written in Auto -> GroupCount {count} (expected 0)"))
        steps, lost, oven_on = plc.steps(), plc.coil(COIL_COMMLOST), plc.coil(COIL_OVEN_ON)
        results.append(check("G1", steps == (0, 0) and not lost and not oven_on,
                             f"Mode Stop, heartbeat, Reset -> Step / WrapStep {steps}, Alm_CommLost {lost}, "
                             f"Oven_On {oven_on} (expected (0, 0) / False / False)"))
        if lost:
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")

        plc.coil(COIL_PUSHER_HOME, True)
        plc.coil(COIL_WRAP_HOME, True)
        plc.mode(MODE_AUTO)
        oven_on = plc.coil(COIL_OVEN_ON)
        results.append(check("G2", oven_on, f"Auto, no Start -> Oven_On {oven_on} (expected True)"))

        # ---------- one group ----------
        plc.cmd(START)
        ok, seen, _ = plc.wait_step(5, 1)
        wrap = plc.steps()[1]
        results.append(check("G3", ok and wrap == 10,
                             f"Start, bar home, OvenTemp 150.0 degC -> Step {seen[-1]}, WrapStep {wrap} (expected 5 / 10)"))
        hold("G4", 5, 1.0, "oven cold (OvenTemp 150.0 degC)")
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        reach("G5", 10, 1, "OvenTemp 190.0 degC -> Oven_Ready")

        # a pack waits at the film stop while the group is counted: Wrap_Clear stays FALSE (the empty
        # conveyor in WAIT_PACK since the Start would otherwise allow the push at once)
        plc.coil(COIL_PACK_AT_FILM, True)
        plc.pulse(COIL_BOTTLE_COUNTED, 5)
        count, complete, step = plc.read(HR_GROUP_COUNT), plc.coil(COIL_GROUP_COMPLETE), plc.steps()[0]
        results.append(check("G6", count == 5 and not complete and step == 10,
                             f"5 Bottle_Counted pulses -> GroupCount {count}, Group_Complete {complete}, Step {step} "
                             "(expected 5 / False / 10)"))
        plc.pulse(COIL_BOTTLE_COUNTED)
        ok, seen, _ = plc.wait_step(15, 1)
        count, complete = plc.read(HR_GROUP_COUNT), plc.coil(COIL_GROUP_COMPLETE)
        results.append(check("G7", ok and count == 6 and complete,
                             f"6th pulse -> GroupCount {count}, Group_Complete {complete}, Step {seen[-1]} "
                             "(expected 6 / True / 15)"))

        ok, seen = plc.stays(15, 4.0)
        clear = plc.coil(COIL_WRAP_CLEAR)
        results.append(check("G8", ok and not clear,
                             f"pack at the film stop 4 s -> Wrap_Clear {clear}, Step seen {seen} (expected False / [15])"))
        plc.coil(COIL_PACK_AT_FILM, False)
        hold("G9", 15, 2.5, "film stop free for 2.5 s (ClearTime 3 s)")
        ok, seen, dt = plc.wait_step(20, 1.5)
        results.append(check("G10", ok, f"film stop free -> Step {seen[-1]} after {2.5 + dt:.1f} s in total "
                                        f"(expected 20 at about 3 s; seen {seen})"))

        plc.coil(COIL_PUSHER_HOME, False)
        plc.coil(COIL_PUSHER_OUT, True)
        ok, seen, _ = plc.wait_step(25, 1)
        time.sleep(SETTLE)  # the counter resets in the scan after 20 -> 25
        count, complete = plc.read(HR_GROUP_COUNT), plc.coil(COIL_GROUP_COMPLETE)
        results.append(check("G11", ok and count == 0 and not complete,
                             f"Pusher_Out -> Step {seen[-1]}, GroupCount {count}, Group_Complete {complete} "
                             "(expected 25 / 0 / False)"))
        plc.coil(COIL_PUSHER_OUT, False)
        plc.coil(COIL_PUSHER_HOME, True)
        reach("G12", 10, 1, "Pusher_Home (spring return)")

        # ---------- G13-G15 group pushed halfway, restart ----------
        plc.pulse(COIL_BOTTLE_COUNTED, 6)
        plc.wait_step(15, 1)
        ok, seen, _ = plc.wait_step(20, 4)  # Wrap_Clear: film stop free, pusher home, WAIT_PACK
        if not ok:
            raise SystemExit(f"cannot reach PUSH (20) for G13, seen {seen}")
        plc.coil(COIL_PUSHER_HOME, False)  # pusher on its way out
        plc.mode(MODE_STOP)
        steps, count, complete = plc.steps(), plc.read(HR_GROUP_COUNT), plc.coil(COIL_GROUP_COMPLETE)
        results.append(check("G13", steps == (0, 0) and count == 6 and complete,
                             f"Mode Stop in PUSH -> Step / WrapStep {steps}, GroupCount {count}, Group_Complete "
                             f"{complete} (expected (0, 0) / 6 / True)"))
        plc.coil(COIL_PUSHER_HOME, True)  # spring return
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        reach("G14", 15, 1, "Auto + Start with the group still complete (5 -> 10 -> 15, faster than one poll)")
        ok, seen, dt = plc.wait_step(20, 4)
        results.append(check("G15", ok and dt > 2.0,
                             f"Wrap_Clear timed again after the restart -> Step {seen[-1]} after {dt:.1f} s "
                             "(expected 20 after about 3 s)"))
        plc.coil(COIL_PUSHER_HOME, False)
        plc.coil(COIL_PUSHER_OUT, True)
        plc.wait_step(25, 1)
        plc.coil(COIL_PUSHER_OUT, False)
        plc.coil(COIL_PUSHER_HOME, True)
        plc.wait_step(10, 1)

        # ---------- G16-G18 Start cases ----------
        start_from_idle(wrap_home=False)
        time.sleep(SETTLE)
        steps = plc.steps()
        results.append(check("G16", steps[0] in (5, 10) and steps[1] == 32,
                             f"Start with the wrap bar NOT home -> Step / WrapStep {steps} (expected 5 or 10 / 32)"))
        plc.coil(COIL_WRAP_HOME, True)
        plc.mode(MODE_STOP)
        steps = plc.steps()
        results.append(check("G17", steps == (0, 0), f"Mode Stop -> Step / WrapStep {steps} (expected (0, 0))"))
        plc.mode(MODE_AUTO)
        plc.write(HR_WRAP_STEP, 10)
        time.sleep(SETTLE)
        plc.cmd(START)
        time.sleep(SETTLE)
        steps = plc.steps()
        results.append(check("G18", steps == (0, 10),
                             f"Start with Step 0, WrapStep 10 (written) -> Step / WrapStep {steps} (expected (0, 10))"))

        # ---------- G19 E-stop ----------
        start_from_idle()
        plc.wait_step(10, 1)
        plc.coil(COIL_E_STOP_OK, False)
        time.sleep(SETTLE)
        steps, oven_on = plc.steps(), plc.coil(COIL_OVEN_ON)
        results.append(check("G19", steps == (0, 0) and not oven_on,
                             f"E_Stop_OK FALSE in COUNT -> Step / WrapStep {steps}, Oven_On {oven_on} "
                             "(expected (0, 0) / False)"))
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)

        # ---------- G20/G21 FAULT holds ----------
        plc.write(HR_STEP, 900)
        plc.write(HR_WRAP_STEP, 900)
        time.sleep(SETTLE)
        oven_on = plc.coil(COIL_OVEN_ON)
        results.append(check("G20", not oven_on, f"Step / WrapStep 900 (written) in Auto -> Oven_On {oven_on} (expected False)"))
        plc.mode(MODE_STOP)
        time.sleep(0.5)
        steps = plc.steps()
        results.append(check("G21", steps == (900, 900),
                             f"Mode Stop in FAULT -> Step / WrapStep {steps} (expected (900, 900): abort keeps FAULT)"))
    finally:
        # end state for the next script: sensors FALSE, E_Stop_OK TRUE, Step / WrapStep 0, GroupCount 0, Mode Stop
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.clear_sensors()
        plc.coil(COIL_FILM_EMPTY, False)
        plc.coil(COIL_E_STOP_OK, True)
        zero_group(plc)
        plc.write(HR_OVEN_TEMP, 0)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Step / WrapStep 0, GroupCount 0, Mode Stop, sensors FALSE, OvenTemp 0)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
