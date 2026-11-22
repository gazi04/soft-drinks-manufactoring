"""Packer ladder stage 5: wrap sequencer (Feed_Counter, Sheet_Waiting, Cut_Timer, transitions 10-34).

Runs against the packer PLC on port 5024. No outputs exist yet: the script plays the machine by
writing the sensors and watches WrapStep (%MW52). The collation side gets no bottles and waits in
COUNT. Heartbeat, E_Stop_OK, oven setpoints (window 1750-2050) and FilmLength 850 (17 pulses) as in
test_packer_collation.py, whose helpers this script imports.

    W0  start: Feed_Counter back to 0 (WrapStep 24 READY written by hand, its reset), FeedPulses 17
One pack:
    W1  Auto + Start, bar home, oven hot -> WrapStep 10 WAIT_PACK
    W2  Pack_At_Film, slot empty -> 20 FEED
    W3  16 Film_Pulse pulses -> stays 20, FeedCount 16;  W4  17th -> 22 CUT
    W5  0.3 s later -> 24 READY, FeedCount 0 (reset in READY)
    W6  no Film_In_Slot -> stays 24;  W7  Film_In_Slot, oven ready, outfeed free, bar home -> 30 RUN_ON
    W8  Pack_At_Film off -> 32 WRAP
    W9  Wrap_Home off -> stays 32;  W10  Wrap_Home on (rising edge) -> 34 RUN_OUT
    W11 Oven_Entry pulse -> 10 WAIT_PACK
Variants:
    W12 Film_Empty with a pack at the film, slot empty -> stays 10;  W13 Film_Empty off -> 20
    W14 Mode Stop after 8 pulses -> 0, FeedCount 8 kept;  W15 Start -> 10 -> 20 (film half fed in the slot)
    W16 8 more pulses -> stays 20;  W17 9th -> 22 (only the rest is fed), then 24
    W18 restart with a cut sheet in the slot (Film_In_Slot, FeedCount 0) -> 10 -> 24 directly, never 20
    W19 Outfeed_Full in 24 -> stays 24;  W20 Outfeed_Full released -> 30
    W21 Start with the oven cold: wrap goes on (10 -> 24 with a cut sheet), OvenTemp 150.0 degC -> stays 24
    W22 bar not home in 24 -> stays 24;  W23 oven hot and bar home -> 30
    W24 Start with the bar NOT home -> 32;  W25 Wrap_Home rising -> 34
    W26 FilmLength 600 -> FeedPulses 12: 11 pulses -> stays 20, 12th -> 22
    W27 Mode Stop -> both steps 0

    uv run python tests/test_packer_wrap.py      (~60 s)

Feed_Counter and Cut_Timer have no address: watch them in the debugger if a check fails.
Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_packer_collation import (COIL_E_STOP_OK, COIL_FILM_IN_SLOT, COIL_FILM_PULSE, COIL_PACK_AT_FILM,
                                   COIL_PUSHER_HOME, COIL_WRAP_HOME, HR_MODE_REQ, HR_OVEN_BAND, HR_OVEN_HYST,
                                   HR_OVEN_SETPOINT, HR_OVEN_TEMP, HR_STEP, HR_WRAP_STEP, MODE_AUTO, MODE_STOP,
                                   OVEN_BAND, OVEN_COLD, OVEN_HOT, OVEN_HYST, OVEN_SETPOINT, RESET, SETTLE, START,
                                   Heartbeat, Plc, check, connect, zero_group)

HOST = "127.0.0.1"
PORT = 5024

HR_FILM_LENGTH = 1057  # %MW33
HR_FEED_PULSES = 1066  # %MW42
HR_FEED_COUNT = 1067  # %MW43
COIL_FILM_EMPTY = 8224  # %MX4.0
COIL_OVEN_ENTRY = 8226  # %MX4.2
COIL_TABLE_FULL = 8228  # %MX4.4
COIL_OUTFEED_FULL = 8216  # %MX3.0
COIL_COMMLOST = 8235  # %MX5.3
EXTRA_SENSORS = (COIL_FILM_EMPTY, COIL_OVEN_ENTRY, COIL_TABLE_FULL)

FILM_LENGTH = 850  # default -> 17 pulses
WRAP = 1  # word index of WrapStep in Plc.steps()


def zero_feed(plc: Plc) -> int:
    """Feed_Counter is reset only in READY: park WrapStep at 24 in Auto (no sheet in the slot, so it
    stays), then Mode Stop -> 0. Returns FeedCount afterwards."""
    plc.mode(MODE_STOP)
    plc.coil(COIL_FILM_IN_SLOT, False)
    plc.mode(MODE_AUTO)
    plc.write(HR_WRAP_STEP, 24)
    time.sleep(SETTLE)
    plc.mode(MODE_STOP)
    return plc.read(HR_FEED_COUNT)


def wait_outfeed(plc: Plc, value: bool, timeout: float = 5.0) -> bool:
    """Wait until the Outfeed_Full filter (2 s on / 3 s off) has followed Table_Full."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if plc.coil(COIL_OUTFEED_FULL) == value:
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    claim_plant("test_packer_wrap", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_wrap")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def reach(name: str, expected: int, timeout: float, what: str) -> tuple[bool, list[int]]:
        ok, seen, dt = plc.wait_step(expected, timeout, WRAP)
        results.append(check(name, ok, f"{what} -> WrapStep {seen[-1]} after {dt:.1f} s (expected {expected}; seen {seen})"))
        return ok, seen

    def hold(name: str, expected: int, seconds: float, what: str) -> None:
        ok, seen = plc.stays(expected, seconds, WRAP)
        results.append(check(name, ok, f"{what} -> WrapStep stays {expected} for {seconds:g} s (seen {seen})"))

    def must(expected: int, timeout: float = 1.0) -> None:
        ok, seen, _ = plc.wait_step(expected, timeout, WRAP)
        if not ok:
            raise SystemExit(f"cannot reach WrapStep {expected} for the next check, seen {seen}")

    def start(wrap_home: bool = True, oven: int = OVEN_HOT) -> None:
        """Mode Stop -> both 0, Auto + Start (sensors as set by the caller)."""
        plc.mode(MODE_STOP)
        plc.coil(COIL_WRAP_HOME, wrap_home)
        plc.write(HR_OVEN_TEMP, oven)
        plc.mode(MODE_AUTO)
        plc.cmd(START)

    def finish_pack() -> None:
        """From RUN_ON (30): pack leaves the stop, bar turn, oven entry -> back in WAIT_PACK (10)."""
        must(30)
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)
        must(32)
        plc.coil(COIL_WRAP_HOME, False)
        time.sleep(SETTLE)
        plc.coil(COIL_WRAP_HOME, True)
        must(34)
        plc.pulse(COIL_OVEN_ENTRY)
        must(10)

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
        plc.coil(COIL_FILM_PULSE, True)  # the sensor writes must reach the PLC: a debugger force would block them
        time.sleep(SETTLE)
        if not plc.coil(COIL_FILM_PULSE):
            raise SystemExit("Film_Pulse does not follow the writes: release all forces in the debugger")
        plc.coil(COIL_FILM_PULSE, False)
        heartbeat.start()
        time.sleep(1.5)  # at least 2 toggles before Reset
        plc.cmd(RESET)
        for address in (HR_STEP, HR_WRAP_STEP):  # a 900 left by a broken run (no 900 -> 0 before stage 9)
            if plc.read(address) == 900:
                plc.write(address, 0)
        if plc.coil(COIL_COMMLOST):
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the sequencers cannot run")
        if not wait_outfeed(plc, False):
            raise SystemExit("Outfeed_Full stays TRUE with Table_Full FALSE: check the stage 3 rungs")
        zero_group(plc)
        count, pulses = zero_feed(plc), plc.read(HR_FEED_PULSES)
        results.append(check("W0", count == 0 and pulses == 17,
                             f"WrapStep 24 (READY) written in Auto -> FeedCount {count}, FeedPulses {pulses} "
                             "(expected 0 / 17)"))

        # ---------- one pack ----------
        plc.coil(COIL_PUSHER_HOME, True)
        start()
        reach("W1", 10, 1, "Auto + Start, bar home, oven hot")
        plc.coil(COIL_PACK_AT_FILM, True)
        reach("W2", 20, 1, "Pack_At_Film, slot empty")
        plc.pulse(COIL_FILM_PULSE, 1)
        plc.coil(COIL_FILM_IN_SLOT, True)  # the film tip comes out of the slot
        plc.pulse(COIL_FILM_PULSE, 15)
        ok, seen = plc.stays(20, 0.3, WRAP)
        count = plc.read(HR_FEED_COUNT)
        results.append(check("W3", ok and count == 16,
                             f"16 Film_Pulse pulses -> WrapStep {seen}, FeedCount {count} (expected [20] / 16)"))
        # sheet sensor off before the cut: READY must wait for it (W6), else 24 -> 30 in the next scan
        plc.coil(COIL_FILM_IN_SLOT, False)
        plc.pulse(COIL_FILM_PULSE)
        ok, seen, dt = plc.wait_step(22, 1, WRAP)
        results.append(check("W4", ok, f"17th pulse -> WrapStep {seen[-1]} (expected 22; seen {seen})"))
        ok, seen, dt = plc.wait_step(24, 1, WRAP)
        time.sleep(SETTLE)  # the counter resets in the scan after 22 -> 24
        count = plc.read(HR_FEED_COUNT)
        results.append(check("W5", ok and dt < 0.6 and count == 0,
                             f"CutTime -> WrapStep {seen[-1]} after {dt:.2f} s in CUT polling, FeedCount {count} "
                             f"(expected 24 after about 0.3 s / 0; seen {seen})"))
        hold("W6", 24, 0.5, "no sheet in the slot (Film_In_Slot FALSE)")
        plc.coil(COIL_FILM_IN_SLOT, True)
        reach("W7", 30, 1, "Film_In_Slot, Oven_Ready, outfeed free, bar home")
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)  # the pack drove the sheet out of the slot
        reach("W8", 32, 1, "Pack_At_Film off (pack on the sheet)")
        plc.coil(COIL_WRAP_HOME, False)
        hold("W9", 32, 0.5, "wrap bar left home (no rising edge yet)")
        plc.coil(COIL_WRAP_HOME, True)
        reach("W10", 34, 1, "Wrap_Home back on (one turn)")
        plc.pulse(COIL_OVEN_ENTRY)
        reach("W11", 10, 1, "Oven_Entry pulse")

        # ---------- W12/W13 film roll empty ----------
        plc.coil(COIL_FILM_EMPTY, True)
        plc.coil(COIL_PACK_AT_FILM, True)
        hold("W12", 10, 1.0, "pack at the film, slot empty, Film_Empty")
        plc.coil(COIL_FILM_EMPTY, False)
        reach("W13", 20, 1, "new roll (Film_Empty off)")

        # ---------- W14-W17 Stop in the middle of FEED, restart feeds the rest ----------
        # table full from now on (Outfeed_Full after 2 s): READY at the end of W17 must wait there with
        # its cut sheet for W18, else 24 -> 30 in the next scan
        plc.coil(COIL_TABLE_FULL, True)
        plc.pulse(COIL_FILM_PULSE, 1)
        plc.coil(COIL_FILM_IN_SLOT, True)
        plc.pulse(COIL_FILM_PULSE, 7)
        plc.mode(MODE_STOP)
        steps, count = plc.steps(), plc.read(HR_FEED_COUNT)
        results.append(check("W14", steps == (0, 0) and count == 8,
                             f"Mode Stop after 8 pulses -> Step / WrapStep {steps}, FeedCount {count} "
                             "(expected (0, 0) / 8)"))
        start()
        ok, seen = reach("W15", 20, 1, "Start, pack at the film, half-fed sheet in the slot (FeedCount 8)")
        plc.pulse(COIL_FILM_PULSE, 8)
        ok, seen = plc.stays(20, 0.3, WRAP)
        count = plc.read(HR_FEED_COUNT)
        results.append(check("W16", ok and count == 16,
                             f"8 more pulses -> WrapStep {seen}, FeedCount {count} (expected [20] / 16)"))
        plc.pulse(COIL_FILM_PULSE)
        ok, seen = reach("W17", 22, 1, "9th pulse after the restart (17 in total)")
        if not wait_outfeed(plc, True):
            raise SystemExit("Outfeed_Full not TRUE 2 s after Table_Full")
        must(24)

        # ---------- W18-W20 Stop in READY: the cut sheet stays; outfeed full ----------
        plc.mode(MODE_STOP)
        if not wait_outfeed(plc, True):
            results.append(check("W19", False, "Outfeed_Full not TRUE within 5 s of Table_Full"))
        start()
        ok, seen = reach("W18", 24, 1, "Start with a cut sheet in the slot (Film_In_Slot, FeedCount 0)")
        if 20 in seen:
            results.append(check("W18b", False, f"seen {seen}: went through FEED, a second sheet on top"))
        hold("W19", 24, 1.0, "Outfeed_Full (table full)")
        plc.coil(COIL_TABLE_FULL, False)
        reach("W20", 30, 5, "table free (Outfeed_Full drops after 3 s)")
        finish_pack()

        # ---------- W21-W23 oven cold, bar not home in READY ----------
        plc.coil(COIL_FILM_IN_SLOT, True)  # cut sheet waiting (FeedCount 0); before the pack, else 10 -> 20
        plc.coil(COIL_PACK_AT_FILM, True)
        start(oven=OVEN_COLD)
        ok, seen, _ = plc.wait_step(24, 1, WRAP)
        step = plc.steps()[0]
        held, seen_held = plc.stays(24, 1.0, WRAP)
        results.append(check("W21", ok and held and step == 5,
                             f"Start with OvenTemp 150.0 degC -> WrapStep {seen} then stays (seen {seen_held}), "
                             f"Step {step} (expected 24, stays 24, Step 5 WARM_UP)"))
        plc.write(HR_OVEN_TEMP, OVEN_HOT)
        plc.coil(COIL_WRAP_HOME, False)
        hold("W22", 24, 1.0, "oven hot, wrap bar NOT home")
        plc.coil(COIL_WRAP_HOME, True)
        reach("W23", 30, 1, "wrap bar home")
        finish_pack()

        # ---------- W24/W25 bar stopped in the middle of a turn ----------
        start(wrap_home=False)
        reach("W24", 32, 1, "Start with the wrap bar NOT home")
        plc.coil(COIL_WRAP_HOME, True)
        reach("W25", 34, 1, "Wrap_Home rising (turn finished)")
        plc.pulse(COIL_OVEN_ENTRY)
        must(10)

        # ---------- W26 shorter sheet ----------
        plc.write(HR_FILM_LENGTH, 600)
        time.sleep(SETTLE)
        pulses = plc.read(HR_FEED_PULSES)
        plc.coil(COIL_PACK_AT_FILM, True)
        must(20)
        plc.pulse(COIL_FILM_PULSE, 11)
        held, seen_held = plc.stays(20, 0.3, WRAP)
        plc.pulse(COIL_FILM_PULSE)
        ok, seen, _ = plc.wait_step(22, 1, WRAP)
        results.append(check("W26", pulses == 12 and held and ok,
                             f"FilmLength 600 -> FeedPulses {pulses}; 11 pulses -> {seen_held}, 12th -> {seen[-1]} "
                             "(expected 12 / [20] / 22)"))
        must(24)
        plc.write(HR_FILM_LENGTH, FILM_LENGTH)

        plc.mode(MODE_STOP)
        steps = plc.steps()
        results.append(check("W27", steps == (0, 0), f"Mode Stop -> Step / WrapStep {steps} (expected (0, 0))"))
    finally:
        # end state for the next script: sensors FALSE, E_Stop_OK TRUE, steps 0, GroupCount / FeedCount 0, Mode Stop
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        plc.coil(COIL_E_STOP_OK, True)
        plc.write(HR_FILM_LENGTH, FILM_LENGTH)
        zero_group(plc)
        zero_feed(plc)
        plc.write(HR_OVEN_TEMP, 0)
        plc.write(HR_MODE_REQ, MODE_STOP)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (steps 0, FeedCount 0, Mode Stop, sensors FALSE, FilmLength 850)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
