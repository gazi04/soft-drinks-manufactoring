"""All filler PLC tests in one command (port 502), in build order.

    uv run python tests/run_filler.py              # all scripts, about 25 min
    uv run python tests/run_filler.py --quick      # ladder scripts only, short alarm test, about 6 min
    uv run python tests/run_filler.py alarms stop  # only the scripts whose name contains "alarms" or "stop"
    uv run python tests/run_filler.py --fail-fast  # stop at the first failing script
    uv run python tests/run_filler.py --list       # show the list

Left out on purpose: test_sequencer.py --done (needs done bits forced in the debugger),
test_link.py --restart (needs a hand restart of the runtime) and test_hmi_tags.py (tests
FUXA, not the PLC). No simulator and no FUXA operator at the same time.
"""

from suite import Script, main

SCRIPTS = [
    Script("test_signal_conditioning.py", "Outfeed_Full filter, S6 edge -> GoodCount"),
    Script("test_sequencer.py", "step transitions, done bits not forced"),
    Script("test_station_jobs.py", "rinse / fill / cap job durations"),
    Script("test_outputs.py", "outputs, interlocks, manual mode"),
    Script("test_index_door.py", "INDEX belt, door pulse, GoodCount"),
    Script("test_stop_state.py", "Stop at safe points, State word"),
    Script("test_alarms.py", "alarms, timeouts, FAULT", quick_args=("--quick",)),
    Script("test_estop_resume.py", "E-stop with a partly filled bottle, resume"),
    Script("test_jog_watchdog.py", "manual button held > 10 s ignored"),
    Script("test_link.py", "simulator Modbus link, heartbeat"),
    Script("test_closed_loop.py", "20 bottles through the real PLC with the simulator", slow=True),
    Script("test_scenarios.py", "9 fault scenarios through the real PLC", args=("--quick",), slow=True),
]

if __name__ == "__main__":
    main("Filler", "127.0.0.1", 502, SCRIPTS)
