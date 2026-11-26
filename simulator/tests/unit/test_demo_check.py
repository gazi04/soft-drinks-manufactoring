"""tests/demo_check.py: its pure parts (no Docker, no PLC).  uv run pytest"""

import demo_check
from simulator import config


def test_five_runtimes_with_their_ports():
    ports = {rt.label: rt.port for rt in demo_check.RUNTIMES}
    assert ports == {"filler": config.PLC_PORT, "syrup": config.SYRUP_PORT, "labeler": config.LABELER_PORT,
                     "packer": config.PACKER_PORT, "blowmoulder": config.BLOWMOULDER_PORT}
    assert "plc-packer" in demo_check.SERVICES and "plc-blowmolder" in demo_check.SERVICES
    assert "row 2: Packer" in demo_check.RUNTIMES[3].hmi


def test_every_plc_but_the_blow_moulder_has_a_fuxa_device():
    without = [rt.label for rt in demo_check.RUNTIMES if not rt.fuxa]
    assert without == ["blowmoulder"]  # until its HMI (part C): operated with hmi.py
    assert "hmi.py --blowmoulder" in demo_check.RUNTIMES[-1].hmi


def test_d6_names_the_start_option_behind_the_free_plcs():
    assert "--no-packer?" in demo_check._d6_cause(["packer"])
    assert "--no-labeler?" in demo_check._d6_cause(["labeler", "packer"])
    assert "--no-blowmoulder?" in demo_check._d6_cause(["blowmoulder"])
    assert "--no-packer --no-blowmoulder?" in demo_check._d6_cause(["packer", "blowmoulder"])
    assert "--no-labeler --no-blowmoulder?" in demo_check._d6_cause(["labeler", "packer", "blowmoulder"])
    assert "--filler-only?" in demo_check._d6_cause(["syrup", "labeler", "packer", "blowmoulder"])
    assert demo_check._d6_cause(["syrup"]) == " – restart `uv run simulator`"  # cannot come from one simulator
    assert demo_check._d6_cause(["syrup", "labeler", "packer"]) == " – restart `uv run simulator`"
