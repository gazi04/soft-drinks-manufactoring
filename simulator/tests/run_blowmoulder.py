"""All blow moulder PLC tests in one command (port 5025), in ladder-stage order.

    uv run python tests/run_blowmoulder.py              # all scripts
    uv run python tests/run_blowmoulder.py stage0       # only the scripts whose name contains "stage0"
    uv run python tests/run_blowmoulder.py --fail-fast  # stop at the first failing script
    uv run python tests/run_blowmoulder.py --list       # show the list

Stage 0 checks the other four PLCs (502, 5022, 5023, 5024) only when they are up and prints
SKIP otherwise, so it needs no other PLC. Needs the uploaded program; no simulator at the
same time. Add a new stage script to SCRIPTS below.
"""

from suite import Script, main

SCRIPTS = [
    Script("test_blowmoulder_stage0.py", "own Modbus port, %MX4 sensor byte, %MX7 alarm byte, %QW0 = HR 0, other PLCs' memory separate"),
    Script("test_blowmoulder_stage1.py", "defaults, Cmd 1-4 clear, heartbeat watchdog frozen at FALSE and TRUE"),
    Script("test_blowmoulder_values.py", "Oven_Ready window, OvenError, lamp P controller Lamp_Calc, DINT pressure scaling, Pressure_Reached, Pressure_Safe"),
    Script("test_blowmoulder_conditioning.py", "Outfeed_Full filter on C1_Full: 2 s on, 3 s off, blinking ignored, works without Master_Enable"),
    Script("test_blowmoulder_oven_seq.py", "oven station 10 -> 15 -> 20 -> 25 -> 10, soak restart, Lamps_On, Start by sensors, abort keeps FAULT"),
    Script("test_blowmoulder_mould_seq.py", "mould station 10 -> ... -> 80 -> 10, hand-over guards, GRIP / HOLD times, BLOW and EXHAUST pressures, both stations in parallel, restart by sensors"),
]

if __name__ == "__main__":
    main("Blow moulder", "127.0.0.1", 5025, SCRIPTS)
