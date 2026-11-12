"""All syrup room PLC tests in one command (port 5022), in ladder-stage order.

    uv run python tests/run_syrup.py              # all scripts, about 2.5 min
    uv run python tests/run_syrup.py outputs      # only the scripts whose name contains "outputs"
    uv run python tests/run_syrup.py --fail-fast  # stop at the first failing script
    uv run python tests/run_syrup.py --list       # show the list

The debugger steps of stage 0 / stage 2 are skipped (they need a terminal: run those
scripts on their own for them). Needs the uploaded program; no simulator at the same time.
Add a new stage script to SCRIPTS below.
"""

from suite import Script, main

SCRIPTS = [
    Script("test_syrup_stage0.py", "own Modbus port, filler memory separate", needs=(502,)),
    Script("test_syrup_stage1.py", "defaults, Cmd clear, heartbeat watchdog"),
    Script("test_syrup_stage2.py", "ratio math, division-by-zero guard"),
    Script("test_syrup_hysteresis.py", "Temp_OK / CO2_Demand / Product_Available"),
    Script("test_syrup_sequencer.py", "one batch through all steps, resume, abort"),
    Script("test_syrup_outputs.py", "outputs per step, interlocks, jog watchdog, E-stop"),
]

if __name__ == "__main__":
    main("Syrup room", "127.0.0.1", 5022, SCRIPTS)
