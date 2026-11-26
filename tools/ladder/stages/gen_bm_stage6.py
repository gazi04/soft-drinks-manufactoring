"""Blow moulder ladder stage 6: section 7 starts with the lamps (LampPower %QW0 from Lamp_Calc).

Dry run by default (main.ld.stage6 in the temp folder); --write writes main.ld (Editor must be closed).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/ladder: bm_ladder
import bm_ladder as L  # noqa: E402

SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here
head, body = L.split(L.BM)
R = body["rungs"]
assert len(R) == 67 and R[66]["comment"].startswith("Mould transition 80 -> 10"), "main.ld not at stage 5"
B = L.Builder(head, body)

new = [
    B.header("SECTION 7 OUTPUTS. The lamps first (analog output %QW0); jog watchdog, exhaust, mould seal-in, mould "
             "toggle, request rungs, interlocks and FB instances follow in stage 7. (Header rung, no logic)"),
    B.series("Lamps 1/2, safe default: LampPower := 0 in every scan (no contact). The rung below overwrites it only "
             "when every permissive is TRUE; within one scan the LAST write wins and %QW0 is copied to the output "
             "only at the end of the scan, so the lamps never see this 0 while they are on. Nothing written = lamps "
             "off (fail-safe). The PLC owns %QW0: a Modbus write is overwritten",
             [], ("move", [("0", "LampPower")])),
    B.series("Lamps 2/2: LampPower := Lamp_Calc (P controller, section 2) while Lamps_On (Auto, Master_Enable, no "
             "FAULT, preform in the pocket) AND Oven_BelowMax (OvenTemp < 130.0 degC, in every mode: with a big Bias "
             "the controller alone would still heat). No FB: an analog value has no feedback and no run hours",
             ["Lamps_On", "Oven_BelowMax"], ("move", [("Lamp_Calc", "LampPower")])),
]
B.check(new)
out = L.join(B.head, R + new)
if "--write" in sys.argv:
    if L.editor_open():
        raise SystemExit("Editor is open: close it first")
    L.BM.write_text(out)
    print("written", L.BM)
else:
    (SCRATCH / "main.ld.stage6").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage6")
print(f"{len(new)} new rungs ({len(R) + len(new)} total)")
