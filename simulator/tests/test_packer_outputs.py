"""Packer ladder stage 7: outputs and interlocks (jog watchdog, request rungs, lock-out, FB instances).

Runs against the packer PLC on port 5024. The script plays the machine by writing the sensors (as in
test_packer_collation.py / test_packer_wrap.py, whose helpers it imports) and reads the outputs
%QX0.0-0.6 (C3, gate, pusher, wrap conveyor, film feed, knife, wrap bar). The oven outputs are tested
in test_packer_oven_outputs.py and ignored here, except the Manual branch of the oven conveyor (M14).

Auto, one group and one pack (Film_Empty holds the wrap side in WAIT_PACK while the group is counted):
    A1  Mode Stop, IDLE -> C3 only (C3 runs in Stop mode and IDLE)
    A2  Auto + Start, oven hot, no pack at the film -> COUNT + WAIT_PACK: C3, gate, wrap conveyor
    A3  Pack_At_Film -> wrap conveyor stops (film stop), C3 + gate
    A4  5 Bottle_Counted pulses -> gate still open;  A5  6th -> gate shut at once, GroupCount 6, Step 15
    A6  pack off the film stop -> wrap conveyor again;  3 s later (Wrap_Clear) PUSH -> C3, pusher, wrap conveyor
    A7  Pusher_Out -> RETRACT: pusher off (spring return);  A8  Pusher_Home -> COUNT: gate open again
    A9  Pusher_Home FALSE in COUNT -> gate shut (interlock), back on with the pusher home
    A10 Film_Empty off, Pack_At_Film -> FEED: film feed, wrap conveyor off
    A11 Wrap_Home FALSE in FEED -> film feed off (interlock);  back home -> on
    A12 17th Film_Pulse -> feed off, CUT: knife ON (never together with the feed) -> READY: knife off
    A13 Film_In_Slot -> RUN_ON: wrap conveyor;  A14  pack leaves the film -> WRAP: wrap conveyor + wrap bar
    A15 Wrap_Home rising -> RUN_OUT: bar off;  A16  Oven_Entry -> WAIT_PACK: wrap conveyor (no pack at the film)
    A17 Step / WrapStep 900 written (FAULT) -> all off, C3 too;  A18  E_Stop_OK FALSE in Auto -> all off
Manual (hold-to-run, interlocks still apply):
    M1  Manual, no button -> all off (C3 too)
    M2  Man_C3 -> C3;  M3  Man_Gate -> gate, M3b with Pusher_Home FALSE -> off;  M4  Man_GroupPush -> pusher
    M5  Man_WrapConveyor -> wrap conveyor;  M6  Man_FilmFeed -> feed, M6b with the bar not home -> off
    M7  Man_Knife -> knife;  M8  Man_WrapBar -> wrap bar
    M9  Man_FilmFeed + Man_WrapBar in the same write -> only the bar, steady (no flicker)
    M10 Man_FilmFeed held, Man_Knife pressed -> feed only;  M11 Man_Knife held, Man_WrapConveyor -> knife only
    M12 Man_FilmFeed held, Man_WrapConveyor -> feed only;  M13 Man_FilmFeed held, Man_WrapBar -> feed only
    M14 Man_OvenConveyor -> oven conveyor (oven empty, no Oven_On in Manual)
    M15 Man_WrapConveyor held, E_Stop_OK FALSE -> all off
Jog watchdog:
    J1  Man_C3 held 11 s -> Jog_Block TRUE, C3 off;  J2  released -> Jog_Block FALSE;  J3  pressed again -> C3

    uv run python tests/test_packer_outputs.py      (~45 s)

Release all debugger forces first; do not run the simulator at the same time.
"""

import sys
import time

from hmi_guard import claim_plant, warn_if_fuxa
from test_packer_collation import (COIL_BOTTLE_COUNTED, COIL_E_STOP_OK, COIL_FILM_IN_SLOT, COIL_FILM_PULSE,
                                   COIL_PACK_AT_FILM, COIL_PUSHER_HOME, COIL_PUSHER_OUT, COIL_WRAP_HOME,
                                   HR_GROUP_COUNT, HR_MODE_REQ, HR_OVEN_BAND, HR_OVEN_HYST, HR_OVEN_SETPOINT,
                                   HR_OVEN_TEMP, HR_STEP, HR_WRAP_STEP, MODE_AUTO, MODE_STOP, OVEN_BAND, OVEN_HOT,
                                   OVEN_HYST, OVEN_SETPOINT, RESET, SETTLE, START, Heartbeat, Plc, check, connect,
                                   zero_group)
from test_packer_oven_outputs import empty_oven
from test_packer_wrap import (COIL_FILM_EMPTY, COIL_OVEN_ENTRY, EXTRA_SENSORS, FILM_LENGTH, HR_FEED_COUNT,
                              HR_FILM_LENGTH, wait_outfeed, zero_feed)

HOST = "127.0.0.1"
PORT = 5024

MODE_MANUAL = 2
COIL_JOG_BLOCK = 8218  # %MX3.2
COIL_COMMLOST = 8235  # %MX5.3
COIL_MAN = {"Man_C3": 8208, "Man_Gate": 8209, "Man_GroupPush": 8210, "Man_WrapConveyor": 8211,  # %MX2.0-2.7
            "Man_FilmFeed": 8212, "Man_Knife": 8213, "Man_WrapBar": 8214, "Man_OvenConveyor": 8215}
# %QX0.0-0.6 = coils 0-6; OvenHeater / OvenBlower / OvenConveyor (7-9) belong to the oven test
OUTPUTS = ("C3", "Gate", "Pusher", "WrapConv", "FilmFeed", "Knife", "WrapBar")
C3, GATE, PUSHER, WRAP_CONV, FEED, KNIFE, BAR = OUTPUTS
COIL_OVEN_CONV = 9  # %QX1.1
FEED_PULSES = 17  # FilmLength 850 / 50


def on(plc: Plc) -> set[str]:
    """Names of the sequencer outputs that are ON (one Modbus read, so the set is from one moment)."""
    result = plc.c.read_coils(0, count=len(OUTPUTS))
    if result.isError():
        raise SystemExit(f"Read outputs failed: {result}")
    return {name for name, bit in zip(OUTPUTS, result.bits) if bit}


def fmt(outputs: set[str]) -> str:
    return "{" + ", ".join(n for n in OUTPUTS if n in outputs) + "}"


def main() -> int:
    claim_plant("test_packer_outputs", HOST, PORT)  # writes the sensors: never with the simulator
    warn_if_fuxa("test_packer_outputs")
    client = connect()
    plc = Plc(client)
    heartbeat = Heartbeat()
    results: list[bool] = []

    def report(name: str, ok: bool, detail: str) -> bool:
        results.append(check(name, ok, detail))
        return ok

    def expect(name: str, want: set[str], what: str, seconds: float = 0.0) -> bool:
        """Outputs must equal `want` now (seconds = 0) or at every sample for `seconds` (steady, no flicker)."""
        time.sleep(SETTLE)
        samples = [on(plc)]
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(0.05)
            samples.append(on(plc))
        bad = [s for s in samples if s != want]
        got = bad[0] if bad else samples[0]
        steady = f" for {seconds:g} s ({len(samples)} samples)" if seconds else ""
        return report(name, not bad, f"{what} -> {fmt(got)}{steady} (expected {fmt(want)})")

    def must(word: int, expected: int, timeout: float = 1.0) -> None:
        ok, seen, _ = plc.wait_step(expected, timeout, word)
        if not ok:
            raise SystemExit(f"cannot reach {'WrapStep' if word else 'Step'} {expected} for the next check, seen {seen}")

    def button(name: str, value: bool) -> None:
        plc.coil(COIL_MAN[name], value)

    def release_all() -> None:
        plc.c.write_coils(COIL_MAN["Man_C3"], [False] * len(COIL_MAN))
        time.sleep(SETTLE)

    try:
        # ---------- setup ----------
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        release_all()
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
            raise SystemExit("Alm_CommLost still TRUE: Master_Enable is off, the outputs cannot run")
        if not wait_outfeed(plc, False):
            raise SystemExit("Outfeed_Full stays TRUE with Table_Full FALSE: check the stage 3 rungs")
        if plc.coil(COIL_JOG_BLOCK):
            raise SystemExit("Jog_Block TRUE with all Man_* released: check the jog watchdog rungs")
        zero_group(plc)
        zero_feed(plc)
        if plc.read(HR_GROUP_COUNT) or plc.read(HR_FEED_COUNT):
            raise SystemExit("GroupCount / FeedCount not 0 after the counter resets")

        # ---------- Auto: one group ----------
        plc.coil(COIL_PUSHER_HOME, True)
        plc.coil(COIL_WRAP_HOME, True)
        plc.coil(COIL_FILM_EMPTY, True)  # wrap side waits in WAIT_PACK even with a pack at the film
        expect("A1", {C3}, "Mode Stop, both steps 0")
        plc.mode(MODE_AUTO)
        plc.cmd(START)
        must(0, 10)
        must(1, 10)
        expect("A2", {C3, GATE, WRAP_CONV}, "Auto + Start -> COUNT / WAIT_PACK, no pack at the film")
        plc.coil(COIL_PACK_AT_FILM, True)
        expect("A3", {C3, GATE}, "Pack_At_Film in WAIT_PACK (Film_Empty)")
        plc.pulse(COIL_BOTTLE_COUNTED, 5)
        expect("A4", {C3, GATE}, "5 Bottle_Counted pulses")
        plc.c.write_coil(COIL_BOTTLE_COUNTED, True)
        time.sleep(0.1)  # 5 scans: the gate must be off well before the pulse ends
        out = on(plc)
        plc.c.write_coil(COIL_BOTTLE_COUNTED, False)
        time.sleep(SETTLE)
        count, step = plc.read(HR_GROUP_COUNT), plc.steps()[0]
        report("A5", out == {C3} and count == 6 and step == 15,
               f"6th pulse -> {fmt(out)} 0.1 s into the pulse, GroupCount {count}, Step {step} "
               "(expected {C3} / 6 / 15)")
        plc.coil(COIL_PACK_AT_FILM, False)
        expect("A6a", {C3, WRAP_CONV}, "pack off the film stop, WAIT_WRAP")
        must(0, 20, 5)
        expect("A6", {C3, PUSHER, WRAP_CONV}, "Wrap_Clear (3 s empty belt) -> PUSH")
        plc.coil(COIL_PUSHER_HOME, False)
        plc.coil(COIL_PUSHER_OUT, True)
        must(0, 25)
        expect("A7", {C3, WRAP_CONV}, "Pusher_Out -> RETRACT (pusher valve off, gate waits for the pusher home)")
        plc.coil(COIL_PUSHER_OUT, False)
        plc.coil(COIL_PUSHER_HOME, True)
        must(0, 10)
        expect("A8", {C3, GATE, WRAP_CONV}, "Pusher_Home -> COUNT, GroupCount 0")
        plc.coil(COIL_PUSHER_HOME, False)
        expect("A9", {C3, WRAP_CONV}, "Pusher_Home FALSE in COUNT")
        plc.coil(COIL_PUSHER_HOME, True)
        expect("A9b", {C3, GATE, WRAP_CONV}, "Pusher_Home back")

        # ---------- Auto: one pack ----------
        plc.coil(COIL_FILM_EMPTY, False)
        plc.coil(COIL_PACK_AT_FILM, True)
        must(1, 20)
        expect("A10", {C3, GATE, FEED}, "Pack_At_Film, film on the roll -> FEED")
        plc.coil(COIL_WRAP_HOME, False)
        expect("A11", {C3, GATE}, "Wrap_Home FALSE in FEED")
        plc.coil(COIL_WRAP_HOME, True)
        expect("A11b", {C3, GATE, FEED}, "Wrap_Home back")
        plc.pulse(COIL_FILM_PULSE, FEED_PULSES - 1)
        samples: list[tuple[int, set[str]]] = []
        plc.c.write_coil(COIL_FILM_PULSE, True)
        end = time.monotonic() + 1.0
        while time.monotonic() < end:  # FEED -> CUT (0.3 s) -> READY: sample faster than the step changes
            out = on(plc)
            samples.append((plc.steps()[1], out))
            time.sleep(0.02)
        plc.c.write_coil(COIL_FILM_PULSE, False)
        knife_seen = any(KNIFE in o and FEED not in o for _, o in samples)
        together = [o for _, o in samples if KNIFE in o and FEED in o]
        steps_seen = sorted({s for s, _ in samples})
        last = samples[-1]
        report("A12", knife_seen and not together and last == (24, {C3, GATE}),
               f"17th Film_Pulse -> WrapSteps {steps_seen}, knife alone seen {knife_seen}, feed + knife together "
               f"{len(together)} times, end WrapStep {last[0]} {fmt(last[1])} (expected knife alone, 0, 24 {{C3, Gate}})")
        plc.coil(COIL_FILM_IN_SLOT, True)
        must(1, 30)
        expect("A13", {C3, GATE, WRAP_CONV}, "Film_In_Slot in READY -> RUN_ON")
        plc.coil(COIL_PACK_AT_FILM, False)
        plc.coil(COIL_FILM_IN_SLOT, False)
        must(1, 32)
        expect("A14", {C3, GATE, WRAP_CONV, BAR}, "pack off the film stop -> WRAP")
        plc.coil(COIL_WRAP_HOME, False)
        time.sleep(SETTLE)
        plc.coil(COIL_WRAP_HOME, True)
        must(1, 34)
        expect("A15", {C3, GATE, WRAP_CONV}, "Wrap_Home rising -> RUN_OUT")
        plc.pulse(COIL_OVEN_ENTRY)
        must(1, 10)
        expect("A16", {C3, GATE, WRAP_CONV}, "Oven_Entry -> WAIT_PACK, no pack at the film")
        plc.write(HR_STEP, 900)
        plc.write(HR_WRAP_STEP, 900)
        expect("A17", set(), "Step / WrapStep 900 (FAULT, written)")
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.cmd(START)
        must(0, 10)
        plc.coil(COIL_E_STOP_OK, False)
        expect("A18", set(), "E_Stop_OK FALSE in COUNT / WAIT_PACK")
        plc.coil(COIL_E_STOP_OK, True)

        # ---------- Manual ----------
        plc.mode(MODE_MANUAL)
        empty_oven(plc)  # the pack of A16 is still counted inside: no oven conveyor in Manual without M14
        must(0, 0)
        must(1, 0)
        expect("M1", set(), "Manual, no button")
        for name, (button_name, want) in {"M2": ("Man_C3", {C3}), "M3": ("Man_Gate", {GATE}),
                                          "M4": ("Man_GroupPush", {PUSHER}),
                                          "M5": ("Man_WrapConveyor", {WRAP_CONV}),
                                          "M6": ("Man_FilmFeed", {FEED}), "M7": ("Man_Knife", {KNIFE}),
                                          "M8": ("Man_WrapBar", {BAR})}.items():
            button(button_name, True)
            expect(name, want, f"{button_name} held (pusher home, bar home)")
            if name == "M3":
                plc.coil(COIL_PUSHER_HOME, False)
                expect("M3b", set(), "Man_Gate held, Pusher_Home FALSE")
                plc.coil(COIL_PUSHER_HOME, True)
            if name == "M6":
                plc.coil(COIL_WRAP_HOME, False)
                expect("M6b", set(), "Man_FilmFeed held, Wrap_Home FALSE")
                plc.coil(COIL_WRAP_HOME, True)
            button(button_name, False)
        expect("M8b", set(), "all buttons released")

        plc.c.write_coils(COIL_MAN["Man_FilmFeed"], [True, False, True])  # FilmFeed + WrapBar, one write
        expect("M9", {BAR}, "Man_FilmFeed + Man_WrapBar pressed in the same scan", seconds=1.0)
        release_all()
        for name, first, second, want in (("M10", "Man_FilmFeed", "Man_Knife", {FEED}),
                                          ("M11", "Man_Knife", "Man_WrapConveyor", {KNIFE}),
                                          ("M12", "Man_FilmFeed", "Man_WrapConveyor", {FEED}),
                                          ("M13", "Man_FilmFeed", "Man_WrapBar", {FEED})):
            button(first, True)
            time.sleep(SETTLE)
            button(second, True)
            expect(name, want, f"{first} held, then {second}", seconds=0.5)
            release_all()

        button("Man_OvenConveyor", True)
        time.sleep(SETTLE)
        oven_conv = plc.coil(COIL_OVEN_CONV)
        button("Man_OvenConveyor", False)
        time.sleep(SETTLE)
        oven_after = plc.coil(COIL_OVEN_CONV)
        report("M14", oven_conv and not oven_after,
               f"Man_OvenConveyor held -> OvenConveyor {oven_conv}, released -> {oven_after} (expected True / False)")

        button("Man_WrapConveyor", True)
        time.sleep(SETTLE)
        plc.coil(COIL_E_STOP_OK, False)
        expect("M15", set(), "Man_WrapConveyor held, E_Stop_OK FALSE")
        plc.coil(COIL_E_STOP_OK, True)
        release_all()

        # ---------- jog watchdog ----------
        button("Man_C3", True)
        time.sleep(11)
        blocked = plc.coil(COIL_JOG_BLOCK)
        report("J1", blocked, f"Man_C3 held 11 s -> Jog_Block {blocked} (expected True)")
        expect("J1b", set(), "Man_C3 still held, Jog_Block")
        release_all()
        blocked = plc.coil(COIL_JOG_BLOCK)
        report("J2", not blocked, f"all buttons released -> Jog_Block {blocked} (expected False)")
        button("Man_C3", True)
        expect("J3", {C3}, "Man_C3 pressed again")
        release_all()
    finally:
        # end state for the next script: buttons released, Mode Stop, steps 0, oven empty, sensors off
        release_all()
        plc.write(HR_MODE_REQ, MODE_STOP)
        plc.write(HR_STEP, 0)
        plc.write(HR_WRAP_STEP, 0)
        plc.coil(COIL_E_STOP_OK, True)
        time.sleep(SETTLE)
        empty_oven(plc)
        plc.clear_sensors()
        for address in EXTRA_SENSORS:
            plc.coil(address, False)
        heartbeat.stop()
        client.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed (Mode Stop, buttons released, sensors off, E_Stop_OK TRUE)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectionError as error:
        raise SystemExit(f"Modbus connection dropped ({error}). Program uploaded and RUNNING on 127.0.0.4? "
                         "Without libplc_*.so the runtime starts no Modbus server.") from None
