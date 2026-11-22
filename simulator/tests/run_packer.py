"""All packer PLC tests in one command (port 5024), in ladder-stage order.

    uv run python tests/run_packer.py              # all scripts
    uv run python tests/run_packer.py stage0       # only the scripts whose name contains "stage0"
    uv run python tests/run_packer.py --fail-fast  # stop at the first failing script
    uv run python tests/run_packer.py --list       # show the list

Stage 0 checks the filler (502), the syrup room (5022) and the labeler (5023) only when they
are up and prints SKIP otherwise, so it needs no other PLC. Needs the uploaded program; no
simulator at the same time. Add a new stage script to SCRIPTS below.
"""

from suite import Script, main

SCRIPTS = [
    Script("test_packer_stage0.py", "own Modbus port, %MX4 sensor byte, %MX7 alarm byte, other PLCs' memory separate"),
    Script("test_packer_stage1.py", "defaults, Cmd 1-4 clear, heartbeat watchdog frozen at FALSE and TRUE"),
    Script("test_packer_values.py", "Oven_Ready window, Heat_Demand hysteresis, both follow the setpoints, FeedPulses with LIMIT + DIV"),
    Script("test_packer_conditioning.py", "Outfeed_Full filter on Table_Full: 2 s on, 3 s off, blinking ignored"),
    Script("test_packer_collation.py", "collation sequencer: one group 0->5->10->15->20->25->10, Wrap_Clear, restart, Start cases, abort"),
    Script("test_packer_wrap.py", "wrap sequencer: one pack 10->20->22->24->30->32->34->10, film empty, restart cases, READY waits, FilmLength 600"),
    Script("test_packer_oven_outputs.py", "oven: heater interlocks (airflow, hysteresis, OvenMax), blower TOF run-on 30 s, CTUD packs inside, oven conveyor, FAULT, E-stop"),
    Script("test_packer_outputs.py", "outputs per step of both sequencers, gate / feed / conveyor interlocks, bar > feed > knife lock-out, Manual, jog watchdog, E-stop"),
    Script("test_packer_stop_state.py", "Stop at safe points of both sequencers (none in PUSH / CUT / RUN_ON / WRAP), restart cases, PackCount with Cmd 4, Starved, State 0/1/2/3/9"),
]

if __name__ == "__main__":
    main("Packer", "127.0.0.1", 5024, SCRIPTS)
