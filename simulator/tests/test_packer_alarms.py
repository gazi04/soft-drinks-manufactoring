"""Packer ladder stage 9: alarms and FAULT.

Runs against the packer PLC on port 5024. Plays the machine by writing the sensors (helpers from
test_packer_collation.py / test_packer_wrap.py) and reads the alarm bits %MX5.0-5.7 / %MX7.0-7.1,
Step / WrapStep and the outputs %QX0.0-1.1.

    N0  one pack through the wrap sequencer -> no alarm
Film fault (FEED 10 s, READY without a sheet 1 s):
    F1  FEED, 5 pulses, then none -> FAULT after about 10 s: Alm_FilmFault, both steps 900, all off but the blower
    F2  Reset in Auto -> both 0, alarm FALSE; Start -> FEED again with FeedCount 5 kept
    F3  rest of the pulses, READY without Film_In_Slot -> FAULT after about 1 s; Reset -> 0
Pack jam (3 s per step) and wrap timeout (5 s):
    J1  PUSH without Pusher_Out;  J2  RETRACT without Pusher_Home;  J3  RUN_ON, pack stays at the film;
    J4  RUN_OUT without Oven_Entry -> each Alm_PackJam after about 3 s, Reset -> 0
    J5  Start with the wrap bar not home -> WRAP, no Wrap_Home -> Alm_WrapTimeout (%MX7.1) after about 5 s
Oven:
    O1  Auto, OvenTemp 231.0 degC -> Alm_OvenOverTemp, FAULT: heater + oven conveyor off, blower ON
    O2  Reset at 231.0 -> ignored (alarm and 900 stay)
    O3  blower still ON 29 s after the FAULT, OFF after 31 s (TOF run-on)
    O4  OvenTemp 190.0 + Reset -> alarm FALSE, both 0
    O5  Mode Stop, 231.0 -> FAULT in Mode Stop too (State 9); 190.0, Auto + Reset -> 0
    A1  Airflow_OK FALSE for 2 s -> no alarm;  A2  FALSE for 3 s -> Alm_AirflowLost (%MX7.0), FAULT;
    A3  air back, Reset -> cleared at once
    J6  1 pack in the oven, no Oven_Exit: no alarm at 18 s, Alm_OvenJam by 22 s;  J7  Reset -> PacksInOven 0
Warnings (no FAULT):
    W1  Film_Empty with a pack at the film -> Alm_FilmEmpty, WrapStep 10;  W2  roll changed -> FALSE
    W3  (not --quick) READY with Outfeed_Full for 2 min -> Alm_OutfeedBlocked, still 24;  table free + Reset -> FALSE
    H1  (not --quick) WARM_UP at 100.0 degC -> no alarm at 115 s, Alm_OvenHeatTimeout by 123 s; Reset at 190.0 -> 0

    uv run python tests/test_packer_alarms.py           (~6.5 min)
    uv run python tests/test_packer_alarms.py --quick   (~2 min, without W3 and H1)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_packer_collation import (COIL_AIRFLOW_OK, COIL_BOTTLE_AT_GATE, COIL_BOTTLE_COUNTED, COIL_E_STOP_OK,
                                   COIL_FILM_IN_SLOT, COIL_FILM_PULSE, COIL_PACK_AT_FILM, COIL_PUSHER_HOME,
                                   COIL_PUSHER_OUT, COIL_WRAP_HOME, HR_MODE_REQ, HR_OVEN_BAND, HR_OVEN_HYST,
                                   HR_OVEN_SETPOINT, HR_OVEN_TEMP, HR_STEP, HR_WRAP_STEP, MODE_AUTO, MODE_STOP,
                                   OVEN_BAND, OVEN_HOT, OVEN_HYST, OVEN_SETPOINT, RESET, SETTLE, START, Heartbeat,
                                   Plc, check, connect, zero_group)
from test_packer_oven_outputs import COIL_OVEN_EXIT, HR_PACKS_IN_OVEN, empty_oven
from test_packer_wrap import (COIL_FILM_EMPTY, COIL_OVEN_ENTRY, COIL_TABLE_FULL, EXTRA_SENSORS, FILM_LENGTH,
                              HR_FEED_COUNT, HR_FILM_LENGTH, wait_outfeed, zero_feed)

HOST = "127.0.0.1"
PORT = 5024

HR_STATE = 1074  # %MW50
COIL_COMMLOST = 8235  # %MX5.3
ALARMS = {"FilmFault": 8232, "PackJam": 8233, "OutfeedBlocked": 8234, "FilmEmpty": 8236, "OvenOverTemp": 8237,
          "OvenHeatTimeout": 8238, "OvenJam": 8239, "AirflowLost": 8248, "WrapTimeout": 8249}  # %MX5.x, %MX7.0-7.1
OUTPUTS = ("C3", "Gate", "Pusher", "WrapConv", "FilmFeed", "Knife", "WrapBar", "Heater", "Blower", "OvenConv")
COLL, WRAP = 0, 1
OVER_TEMP = 2310  # 231.0 degC >= OvenMax 230.0
QUICK = "--quick" in sys.argv


def main() -> int:
    claim_plant("test_packer_alarms", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_alarms")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> bool:
        results.append(check(name, ok, detail))
        return ok

    def alarms() -> set[str]:
        """Names of the alarm bits that are TRUE (Alm_CommLost left out)."""
        low = plc.c.read_coils(8232, count=8)
        high = plc.c.read_coils(8248, count=2)
        if low.isError() or high.isError():
            raise SystemExit("Read alarm bits failed")
        bits = {8232 + i: b for i, b in enumerate(low.bits[:8])} | {8248 + i: b for i, b in enumerate(high.bits[:2])}
        return {name for name, address in ALARMS.items() if bits[address]}

    def outputs() -> set[str]:
        result = plc.c.read_coils(0, count=len(OUTPUTS))
        if result.isError():
            raise SystemExit(f"Read outputs failed: {result}")
        return {name for name, bit in zip(OUTPUTS, result.bits) if bit}

    def must(word: int, expected: int, timeout: float = 1.0) -> None:
        ok, seen, _ = plc.wait_step(expected, timeout, word)
        if not ok:
            raise SystemExit(f"cannot reach {'WrapStep' if word else 'Step'} {expected} for the next check, seen {seen}")

    def fault(name: str, alarm: str, low: float, high: float, what: str, t0: float | None = None) -> None:
        """Wait for both steps 900; the time since t0 (default: now) must lie in [low, high]."""
        t0 = time.monotonic() if t0 is None else t0
        while time.monotonic() - t0 < high + 1.0 and plc.steps() != (900, 900):
            time.sleep(0.05)
        dt = time.monotonic() - t0
        # MOVE 900 is the last rung: Step shows 900 one scan before the output rungs (earlier in the
        # scan) switch off. Wait a few 20 ms scans so the next reads (F1b, O1b) see the FAULT outputs.
        time.sleep(0.1)
        steps, active = plc.steps(), alarms()
        report(name, steps == (900, 900) and alarm in active and low <= dt <= high,
               f"{what} -> steps {steps} after {dt:.1f} s, alarms {sorted(active)} "
               f"(expected (900, 900) after {low:g}-{high:g} s, {alarm})")

    def reset_ok(name: str, what: str = "Reset in Auto") -> None:
        plc.mode(MODE_AUTO)
        plc.cmd(RESET)
        steps, active = plc.steps(), alarms()
        report(name, steps == (0, 0) and not active, f"{what} -> steps {steps}, alarms {sorted(active)} "
                                                     "(expected (0, 0), none)")

    def start() -> None:
        plc.mode(MODE_AUTO)
        plc.cmd(START)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        for address, value in ((HR_OVEN_TEMP, OVEN_HOT), (HR_OVEN_SETPOINT, OVEN_SETPOINT),
                               (HR_OVEN_HYST, OVEN_HYST), (HR_OVEN_BAND, OVEN_BAND), (HR_FILM_LENGTH, FILM_LENGTH)):
            plc.write(address, value)
        plc.coil(COIL_E_STOP_OK, True)
        plc.coil(COIL_AIRFLOW_OK, True)
        plc.coil(COIL_FILM_PULSE, True)  # the sensor writes must reach the PLC: a debugger force would block them
        time.sleep(SETTLE)
        if not plc.coil(COIL_FILM_PULSE):
            raise SystemExit("Film_Pulse does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_FILM_PULSE, False)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset
        plc.mode(MODE_AUTO)
        plc.cmd(RESET)  # clears alarms left by an earlier script and moves 900 -> 0
        plc.mode(MODE_STOP)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")
        if not wait_outfeed(plc, False):
            raise SystemExit("Outfeed_Full stays TRUE with Table_Full FALSE: check the stage 3 rungs")
        zero_group(plc)
        zero_feed(plc)
        empty_oven(plc)
        if alarms() or plc.steps() != (0, 0):
            raise SystemExit(f"alarms {sorted(alarms())} / steps {plc.steps()} left after Reset")

        # ---------- N0 one pack, no alarm ----------
        for address in (COIL_PUSHER_HOME, COIL_WRAP_HOME, COIL_BOTTLE_AT_GATE, COIL_PACK_AT_FILM):
            plc.coil(address, True)
        start()
        must(WRAP, 20)
        plc.pulse(COIL_FILM_PULSE, 1)
        plc.coil(COIL_FILM_IN_SLOT, True)
        plc.pulse(COIL_FILM_PULSE, 15)
        plc.coil(COIL_FILM_IN_SLOT, False)
        plc.pulse(COIL_FILM_PULSE)
        must(WRAP, 24)
        plc.coil(COIL_FILM_IN_SLOT, True)
        must(WRAP, 30)
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)
        must(WRAP, 32)
        plc.coil(COIL_WRAP_HOME, False)
        time.sleep(SETTLE)
        plc.coil(COIL_WRAP_HOME, True)
        must(WRAP, 34)
        plc.pulse(COIL_OVEN_ENTRY)
        must(WRAP, 10)
        plc.pulse(COIL_OVEN_EXIT)
        steps, active = plc.steps(), alarms()
        report("N0", steps == (10, 10) and not active,
               f"one pack FEED -> CUT -> READY -> RUN_ON -> WRAP -> RUN_OUT -> oven -> steps {steps}, alarms "
               f"{sorted(active)} (expected (10, 10), none)")

        # ---------- F1-F3 film fault ----------
        plc.coil(COIL_PACK_AT_FILM, True)
        must(WRAP, 20)
        t0 = time.monotonic()
        plc.pulse(COIL_FILM_PULSE, 5)
        fault("F1", "FilmFault", 9.5, 10.8, "FEED, 5 pulses, then none", t0)
        out = outputs()
        report("F1b", out == {"Blower"}, f"outputs in FAULT {sorted(out)} (expected only the blower, run-on)")
        reset_ok("F2a")
        plc.cmd(START)
        must(WRAP, 20)
        count = plc.read(HR_FEED_COUNT)
        report("F2", count == 5, f"Start after the Reset -> WrapStep 20, FeedCount {count} (expected 5: the rest is fed)")
        plc.pulse(COIL_FILM_PULSE, 12)
        must(WRAP, 24)
        fault("F3", "FilmFault", 0.5, 1.5, "READY without Film_In_Slot")
        reset_ok("F3b")

        # ---------- J1-J5 pack jam, wrap timeout ----------
        plc.coil(COIL_PACK_AT_FILM, False)  # wrap waits in WAIT_PACK, its belt runs empty: Wrap_Clear after 3 s
        plc.cmd(START)
        must(COLL, 10)
        plc.pulse(COIL_BOTTLE_COUNTED, 6)
        must(COLL, 20, 5)
        fault("J1", "PackJam", 2.5, 3.6, "PUSH without Pusher_Out")
        reset_ok("J1b")
        plc.cmd(START)
        must(COLL, 20, 5)  # group still complete: 15 -> 20 after Wrap_Clear
        plc.coil(COIL_PUSHER_HOME, False)
        plc.coil(COIL_PUSHER_OUT, True)
        must(COLL, 25)
        plc.coil(COIL_PUSHER_OUT, False)  # pusher stuck halfway back
        fault("J2", "PackJam", 2.3, 3.6, "RETRACT without Pusher_Home")
        plc.coil(COIL_PUSHER_HOME, True)
        reset_ok("J2b")
        plc.coil(COIL_FILM_IN_SLOT, True)  # cut sheet waiting (FeedCount 0): 10 -> 24 -> 30 at once
        plc.coil(COIL_PACK_AT_FILM, True)
        plc.cmd(START)
        must(WRAP, 30)
        fault("J3", "PackJam", 2.5, 3.6, "RUN_ON, the pack stays at the film stop")
        reset_ok("J3b")
        plc.cmd(START)
        must(WRAP, 30)
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)
        must(WRAP, 32)
        plc.coil(COIL_WRAP_HOME, False)
        time.sleep(SETTLE)
        plc.coil(COIL_WRAP_HOME, True)
        must(WRAP, 34)
        fault("J4", "PackJam", 2.5, 3.6, "RUN_OUT without Oven_Entry")
        reset_ok("J4b")
        plc.coil(COIL_WRAP_HOME, False)
        plc.cmd(START)
        must(WRAP, 32)
        fault("J5", "WrapTimeout", 4.5, 5.6, "WRAP, wrap bar never back home")
        plc.coil(COIL_WRAP_HOME, True)
        reset_ok("J5b")

        # ---------- O1-O5 over-temperature ----------
        plc.write(HR_OVEN_TEMP, OVER_TEMP)
        t_fault = time.monotonic()
        fault("O1", "OvenOverTemp", 0.0, 0.6, "Auto, OvenTemp 231.0 degC")
        out = outputs() & {"Heater", "Blower", "OvenConv"}
        report("O1b", out == {"Blower"}, f"oven outputs in FAULT {sorted(out)} (expected only the blower)")
        plc.cmd(RESET)
        steps, active = plc.steps(), alarms()
        report("O2", steps == (900, 900) and "OvenOverTemp" in active,
               f"Reset at 231.0 degC -> steps {steps}, alarms {sorted(active)} (expected (900, 900), OvenOverTemp)")
        time.sleep(max(0.0, t_fault + 29 - time.monotonic()))
        on_29 = "Blower" in outputs()
        time.sleep(max(0.0, t_fault + 31 - time.monotonic()))
        on_31 = "Blower" in outputs()
        report("O3", on_29 and not on_31, f"blower 29 s after the FAULT {on_29}, 31 s after {on_31} "
                                          "(expected True / False: TOF run-on 30 s)")
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        reset_ok("O4", "OvenTemp 190.0 degC, Reset in Auto")
        plc.mode(MODE_STOP)
        plc.write(HR_OVEN_TEMP, OVER_TEMP)
        time.sleep(SETTLE)
        steps, active, state = plc.steps(), alarms(), plc.read(HR_STATE)
        report("O5", steps == (900, 900) and "OvenOverTemp" in active and state == 9,
               f"Mode Stop, OvenTemp 231.0 degC -> steps {steps}, State {state}, alarms {sorted(active)} "
               "(expected (900, 900), 9, OvenOverTemp)")
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        reset_ok("O5b", "OvenTemp 190.0 degC, Auto + Reset")

        # ---------- A1-A3 airflow ----------
        plc.coil(COIL_AIRFLOW_OK, False)
        time.sleep(2.0)
        plc.coil(COIL_AIRFLOW_OK, True)
        time.sleep(SETTLE)
        active = alarms()
        report("A1", not active, f"blower ON, Airflow_OK FALSE for 2 s -> alarms {sorted(active)} (expected none)")
        t0 = time.monotonic()
        plc.coil(COIL_AIRFLOW_OK, False)
        fault("A2", "AirflowLost", 2.5, 3.6, "blower ON, Airflow_OK FALSE", t0)
        plc.coil(COIL_AIRFLOW_OK, True)
        reset_ok("A3", "air back, Reset in Auto")

        # ---------- J6/J7 oven jam ----------
        plc.pulse(COIL_OVEN_ENTRY)
        t0 = time.monotonic()
        time.sleep(18.0)
        early = alarms()
        fault("J6", "OvenJam", 19.0, 21.5, f"1 pack in the oven, no Oven_Exit (alarms at 18 s: {sorted(early)})", t0)
        if early:
            report("J6a", False, f"alarm before OvenJamTime: {sorted(early)}")
        plc.mode(MODE_AUTO)
        plc.cmd(RESET)
        n, steps, active = plc.read(HR_PACKS_IN_OVEN), plc.steps(), alarms()
        report("J7", n == 0 and steps == (0, 0) and not active,
               f"Reset -> PacksInOven {n}, steps {steps}, alarms {sorted(active)} (expected 0, (0, 0), none)")

        # ---------- W1/W2 film empty warning ----------
        plc.coil(COIL_FILM_IN_SLOT, False)
        plc.coil(COIL_FILM_EMPTY, True)
        plc.coil(COIL_PACK_AT_FILM, True)
        plc.cmd(START)
        must(WRAP, 10)
        time.sleep(SETTLE)
        steps, active = plc.steps(), alarms()
        report("W1", active == {"FilmEmpty"} and steps[WRAP] == 10,
               f"Film_Empty, pack at the film -> alarms {sorted(active)}, steps {steps} (expected FilmEmpty, WrapStep 10)")
        plc.coil(COIL_FILM_EMPTY, False)
        time.sleep(SETTLE)
        steps, active = plc.steps(), alarms()
        report("W2", not active and steps[WRAP] == 20,
               f"roll changed -> alarms {sorted(active)}, steps {steps} (expected none, WrapStep 20: FEED)")
        plc.mode(MODE_STOP)
        zero_feed(plc)

        if not QUICK:
            # ---------- W3 outfeed blocked warning (2 min) ----------
            plc.coil(COIL_TABLE_FULL, True)
            if not wait_outfeed(plc, True):
                raise SystemExit("Outfeed_Full not TRUE within 5 s of Table_Full")
            plc.coil(COIL_FILM_IN_SLOT, True)
            start()
            must(WRAP, 24)
            t0 = time.monotonic()
            time.sleep(115)
            early = alarms()
            while time.monotonic() - t0 < 125 and "OutfeedBlocked" not in alarms():
                time.sleep(0.2)
            dt = time.monotonic() - t0
            steps, active = plc.steps(), alarms()
            report("W3", not early and active == {"OutfeedBlocked"} and steps[WRAP] == 24 and dt <= 122,
                   f"READY with Outfeed_Full -> alarms at 115 s {sorted(early)}, at {dt:.0f} s {sorted(active)}, "
                   f"steps {steps} (expected none, OutfeedBlocked at about 120 s, WrapStep 24)")
            plc.coil(COIL_TABLE_FULL, False)
            must(WRAP, 30, 5)
            plc.cmd(RESET)
            active = alarms()
            report("W3b", not active, f"table free (RUN_ON), Reset -> alarms {sorted(active)} (expected none)")
            plc.mode(MODE_STOP)
            plc.coil(COIL_PACK_AT_FILM, False)
            plc.coil(COIL_FILM_IN_SLOT, False)

            # ---------- H1 heat-up timeout (120 s) ----------
            plc.write(HR_OVEN_TEMP, 1000)
            start()
            must(COLL, 5)
            t0 = time.monotonic()
            time.sleep(115)
            early = alarms()
            fault("H1", "OvenHeatTimeout", 119.0, 122.0, f"WARM_UP at 100.0 degC (alarms at 115 s: {sorted(early)})", t0)
            if early:
                report("H1a", False, f"alarm before HeatTimeout: {sorted(early)}")
            plc.write(HR_OVEN_TEMP, OVEN_HOT)
            reset_ok("H1b", "OvenTemp 190.0 degC, Reset in Auto")
    finally:
        # end state for the next script: Mode Stop, steps 0, no alarm, oven empty, sensors off, E_Stop_OK TRUE
        plc.coil(COIL_AIRFLOW_OK, True)
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        plc.coil(COIL_E_STOP_OK, True)
        plc.mode(MODE_AUTO)
        plc.cmd(RESET)
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        time.sleep(SETTLE)
        empty_oven(plc)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, steps 0, no alarm, sensors off, E_Stop_OK TRUE)"
          + ("  [--quick: W3 and H1 skipped]" if QUICK else ""))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
