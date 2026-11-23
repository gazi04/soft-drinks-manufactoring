"""tests/demo_check.py: its pure parts (no Docker, no PLC).  uv run pytest"""

import demo_check
from simulator import config


def test_four_runtimes_with_their_ports_and_the_packer_without_fuxa():
    ports = {rt.label: rt.port for rt in demo_check.RUNTIMES}
    assert ports == {"filler": config.PLC_PORT, "syrup": config.SYRUP_PORT, "labeler": config.LABELER_PORT,
                     "packer": config.PACKER_PORT}
    assert {rt.label for rt in demo_check.RUNTIMES if not rt.fuxa} == {"packer"}  # its HMI comes with part C
    assert "plc-packer" in demo_check.SERVICES and "hmi.py --packer" in demo_check.RUNTIMES[-1].hmi


def test_d6_names_the_start_option_behind_the_free_plcs():
    assert "--no-packer" in demo_check._d6_cause(["packer"])
    assert "--no-labeler" in demo_check._d6_cause(["labeler", "packer"])
    assert "--filler-only" in demo_check._d6_cause(["syrup", "labeler", "packer"])
    assert demo_check._d6_cause(["syrup"]) == " – restart `uv run simulator`"  # cannot come from one simulator
