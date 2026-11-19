"""All labeler PLC tests in one command (port 5023), in ladder-stage order.

    uv run python tests/run_labeler.py              # all scripts, about 8 min (--quick: about 3.5 min, alarms without the long timeouts)
    uv run python tests/run_labeler.py glue         # only the scripts whose name contains "glue"
    uv run python tests/run_labeler.py --fail-fast  # stop at the first failing script
    uv run python tests/run_labeler.py --list       # show the list

Stage 0 checks the filler (502) and the syrup room (5022) only when they are up and prints
SKIP otherwise, so it needs no other PLC. Needs the uploaded program; no simulator at the
same time. Add a new stage script to SCRIPTS below.
"""

from suite import Script, main

SCRIPTS = [
    Script("test_labeler_stage0.py", "own Modbus port, %MX4 sensor byte, filler / syrup room memory separate"),
    Script("test_labeler_stage1.py", "defaults, Cmd 1-4 clear, heartbeat watchdog frozen at FALSE and TRUE"),
    Script("test_labeler_glue.py", "Glue_Ready window, Heat_Demand hysteresis, both follow the setpoints"),
    Script("test_labeler_conditioning.py", "Outfeed_Full filter on C3_Full: 2 s on, 3 s off, blinking ignored"),
    Script("test_labeler_sequencer.py", "one bottle 0->5->10->20->...->40->10 by writing the sensors, C3 full, restart, cold glue, abort, FAULT exit"),
    Script("test_labeler_outputs.py", "outputs per step, gate / knife pulses, interlocks, heater in every mode, Manual, jog watchdog, E-stop"),
    Script("test_labeler_stop_state.py", "Stop at safe points (none in CLAMP / WRAP / PRESS), GoodCount + Cmd 4, Starved filter, State 0/1/2/3/9"),
    Script("test_labeler_alarms.py", "label / pocket / glue timeouts, over-temperature, FAULT, Reset only when the cause is gone, warnings",
           quick_args=("--quick",)),
    Script("test_link_labeler.py", "simulator's LabelerPlc client: heartbeat, write + read back of both blocks, PLC reads the sensors"),
]

if __name__ == "__main__":
    main("Labeler", "127.0.0.1", 5023, SCRIPTS)
