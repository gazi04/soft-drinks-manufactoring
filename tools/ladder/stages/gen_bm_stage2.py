"""Blow moulder ladder stage 2: section 2 analog values (oven window, P controller, pressure scaling).

Appends 10 rungs to plcs/blowmoulder main.ld. Block chains are built from template blocks of the
packer (SUB, ADD, GE, LE, LT, LIMIT, DIV) and the syrup room (MUL, TO_DINT; TO_INT = TO_DINT with
an INT output, same box in the Editor 4.3.3 library). Contact -> coil rungs clone the packer's
Oven_Ready rung. Dry run by default (writes main.ld.stage2 into the temp folder); --write writes main.ld.
"""
import copy
import json
import random
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tools/ladder/stages/x.py -> repository root
BM = ROOT / "plcs/blowmoulder/pous/programs/main.ld"
SCRATCH = Path(tempfile.gettempdir())  # dry runs write main.ld.stageN here


def split(path):
    s = path.read_text()
    i = s.index("END_VAR")
    j = s.rindex("END_PROGRAM")
    return s[:i], json.loads(s[i + len("END_VAR"):j])


bm_head, bm_body = split(BM)
_, packer = split(ROOT / "plcs/packer/pous/programs/main.ld")
_, syrup = split(ROOT / "plcs/syrup/pous/programs/main.ld")
P, S = packer["rungs"], syrup["rungs"]
assert len(bm_body["rungs"]) == 12 and bm_body["rungs"][11]["comment"].startswith("Manual entry edge"), \
    "main.ld is not at stage 1"

INTERNALS = [
    ("Oven_Low_Lim", "INT", "0", "Oven window lower edge: OvenSetpoint - OvenBand, 0.1 degC"),
    ("Oven_High_Lim", "INT", "0", "Oven window upper edge: OvenSetpoint + OvenBand, 0.1 degC"),
    ("Oven_AboveLow", "BOOL", "FALSE", "OvenTemp >= Oven_Low_Lim (window edge, inclusive)"),
    ("Oven_BelowHigh", "BOOL", "FALSE", "OvenTemp <= Oven_High_Lim (window edge, inclusive)"),
    ("Oven_BelowMax", "BOOL", "FALSE", "OvenTemp < OvenMax: lamps allowed (no over-temperature)"),
    ("Err_Lim", "INT", "0", "OvenError limited to ErrMin..ErrMax, so Kp * error stays inside INT"),
    ("P_x10", "INT", "0", "Kp * Err_Lim, ten times the P term (MUL before DIV)"),
    ("P_Term", "INT", "0", "P_x10 / 10: lamp power from the error, % (INT division truncates)"),
    ("Lamp_Sum", "INT", "0", "Bias + P_Term, % before the 0..100 limit"),
    ("Lamp_Calc", "INT AT %MW40", "0", "Computed: LIMIT(0, Lamp_Sum, 100), lamp power the P controller asks for, % (LampPower in stage 6)"),
    ("Raw_D", "DINT", "0", "BlowPressure_Raw as DINT (TO_DINT)"),
    ("Raw_x400", "DINT", "0", "Raw_D * PressFull, DINT: up to 13 004 400, too big for INT"),
    ("Press_D", "DINT", "0", "Raw_x400 / RawFull: pressure in 0.1 bar, still DINT"),
    ("Raw_AboveMin", "BOOL", "FALSE", "BlowPressure_Raw >= RawMin (no wire break)"),
    ("Raw_BelowMax", "BOOL", "FALSE", "BlowPressure_Raw <= RawMax (not over range)"),
    ("Press_Range_OK", "BOOL", "FALSE", "Pressure sensor in range: Raw_AboveMin AND Raw_BelowMax"),
    ("Press_Low", "BOOL", "FALSE", "BlowPressure < ExhaustLimit"),
]
declared = set(re.findall(r"^\s+(\w+) :", bm_head, re.M))
assert not declared & {n for n, *_ in INTERNALS}, "already declared"
decl_lines = [f"    {n} : {t} := {init}; (* {doc} *)" for n, t, init, doc in INTERNALS]
new_head = bm_head.rstrip(" ") + "\n".join(decl_lines) + "\n  "

docs = {}
for m in re.finditer(r"^\s+(\w+) : (\w+)(?: AT (%\S+))?(?: := ([^;]+))?; \(\* (.*) \*\)$", new_head, re.M):
    docs[m.group(1)] = {"type": m.group(2), "location": m.group(3) or "", "doc": m.group(5)}

used_numeric = set(re.findall(r'"numericId": "(\d+)"', json.dumps(bm_body)))


def fresh(obj):
    text = json.dumps(obj)
    for old in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        text = text.replace(old, str(uuid.uuid4()))

    def new_id(_m):
        while True:
            k = str(random.randint(1_000_000, 9_999_999))
            if k not in used_numeric:
                used_numeric.add(k)
                return f'"numericId": "{k}"'
    return json.loads(re.sub(r'"numericId": "\d+"', new_id, text))


def is_literal(name):
    return re.fullmatch(r"-?\d+", name) is not None


def var(name):
    if is_literal(name):
        return {"name": name}
    d = docs[name]
    return {"id": "", "name": name, "type": {"definition": "base-type", "value": d["type"]}, "class": "local",
            "location": d["location"], "documentation": d["doc"], "debug": False}


def shift(obj, dx, dy):
    """Move a node: its position and every glbPosition of its handles."""
    if isinstance(obj, dict):
        for key in ("position", "glbPosition"):
            if isinstance(obj.get(key), dict) and "x" in obj[key]:
                obj[key]["x"] += dx
                obj[key]["y"] += dy
        for k, v in obj.items():
            if k not in ("position", "glbPosition", "relPosition", "style"):
                shift(v, dx, dy)
    elif isinstance(obj, list):
        for v in obj:
            shift(v, dx, dy)


def find_block(rungs, k, name):
    r = rungs[k]
    b = next(n for n in r["nodes"] if n["type"] == "block" and n["data"]["variant"]["name"] == name)
    vs = [n for n in r["nodes"] if n["type"] == "variable" and n["data"]["block"]["id"] == b["id"]]
    return b, vs


TEMPLATES = {
    "SUB": (P, 11), "ADD": (P, 11), "GE": (P, 12), "LE": (P, 12), "LT": (P, 12),
    "LIMIT": (P, 16), "DIV": (P, 16), "MUL": (S, 13), "TO_DINT": (S, 16), "TO_INT": (S, 16),
}


def block_group(kind, x, wiring):
    rungs, k = TEMPLATES[kind]
    b, vs = find_block(rungs, k, "TO_DINT" if kind == "TO_INT" else kind)
    b, vs = fresh(copy.deepcopy([b, vs]))
    if kind == "TO_INT":
        b["data"]["variant"]["name"] = "TO_INT"
        for v in b["data"]["variant"]["variables"]:
            if v["name"] == "OUT":
                v["type"] = {"definition": "base-type", "value": "INT"}
        for v in vs:
            if v["data"]["block"]["handleId"] == "OUT":
                v["data"]["block"]["variableType"]["type"] = {"definition": "base-type", "value": "INT"}
    dx, dy = x - b["position"]["x"], 14 - b["position"]["y"]
    shift(b, dx, dy)
    for v in vs:
        shift(v, dx, dy)
    assert set(wiring) == {c["handleId"] for c in b["data"]["connectedVariables"]}, (kind, wiring)
    for c in b["data"]["connectedVariables"]:
        c["variable"] = var(wiring[c["handleId"]])
    for v in vs:
        v["data"]["variable"] = var(wiring[v["data"]["block"]["handleId"]])
    return b, vs


def edge(s, t, sh, th):
    return {"id": f"e_{s}_{t}__{sh}_{th}", "source": s, "target": t, "sourceHandle": sh, "targetHandle": th}


def chain_rung(comment, blocks):
    """Blocks in series on one rung: rail -> EN/ENO chain -> rail, each with its own variables."""
    tmpl = P[11]
    left = copy.deepcopy(tmpl["nodes"][0])
    right = copy.deepcopy(next(n for n in tmpl["nodes"] if n["type"] == "powerRail" and n["data"]["variant"] == "right"))
    skel = fresh({"id": tmpl["id"], "left": left, "right": right})
    left, right = skel["left"], skel["right"]
    nodes, var_nodes, edges = [left], [], []
    prev, prev_h = left["id"], "left-rail"
    x = 143
    for kind, wiring in blocks:
        b, vs = block_group(kind, x, wiring)
        nodes.append(b)
        var_nodes += vs
        edges.append(edge(prev, b["id"], prev_h, "EN"))
        for v in vs:
            h = v["data"]["block"]["handleId"]
            if v["data"]["variant"] == "input":
                edges.append(edge(v["id"], b["id"], "output", h))
            else:
                edges.append(edge(b["id"], v["id"], h, "input"))
        prev, prev_h = b["id"], "ENO"
        x += 330
    shift(right, x + 20 - right["position"]["x"], 0)
    edges.append(edge(prev, right["id"], prev_h, "right-rail"))
    nodes.append(right)
    r = {k: copy.deepcopy(v) for k, v in tmpl.items() if k not in ("nodes", "edges")}
    r.update(id=skel["id"], comment=comment, nodes=nodes + var_nodes, edges=edges)
    return r


def rename(obj, mapping):
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and obj["name"] in mapping:
            obj["name"] = mapping[obj["name"]]
        for v in obj.values():
            rename(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            rename(v, mapping)


def fix_meta(obj):
    if isinstance(obj, dict):
        name = obj.get("name")
        if obj.get("class") == "local" and name in docs:
            obj["location"] = docs[name]["location"]
            obj["documentation"] = docs[name]["doc"]
            if isinstance(obj.get("type"), dict) and obj["type"].get("definition") == "base-type":
                obj["type"]["value"] = docs[name]["type"]
        for v in obj.values():
            fix_meta(v)
    elif isinstance(obj, list):
        for v in obj:
            fix_meta(v)


def and_rung(comment, a, b, coil):
    """Two NO contacts in series -> coil (the packer's Oven_Ready rung)."""
    r = fresh(copy.deepcopy(P[13]))
    rename(r, {"Oven_AboveLow": a, "Oven_BelowHigh": b, "Oven_Ready": coil})
    r["comment"] = comment
    fix_meta(r)
    return r


rungs = []
r = fresh(copy.deepcopy(P[10]))
r["comment"] = ("SECTION 2 ANALOG VALUES. Oven window, Oven_BelowMax, the lamp P controller (Lamp_Calc), pressure "
                "scaling in DINT, Press_Range_OK, Pressure_Reached, Pressure_Safe; every scan in every mode. "
                "Nothing here drives an output. (Header rung, no logic)")
rungs.append(r)

rungs.append(chain_rung(
    "Oven window edges: Oven_Low_Lim = OvenSetpoint - OvenBand, Oven_High_Lim = OvenSetpoint + OvenBand. Every "
    "scan, so an HMI setpoint change takes effect at once",
    [("SUB", {"IN1": "OvenSetpoint", "IN2": "OvenBand", "OUT": "Oven_Low_Lim"}),
     ("ADD", {"IN1": "OvenSetpoint", "IN2": "OvenBand", "OUT": "Oven_High_Lim"})]))

rungs.append(chain_rung(
    "Oven compares: Oven_AboveLow / Oven_BelowHigh = window edges (inclusive), Oven_BelowMax = OvenTemp < OvenMax "
    "(130.0 degC; lamps off at or above it, stage 6). A compare OUT goes into a helper BOOL, the contacts are on the "
    "next rung",
    [("GE", {"IN1": "OvenTemp", "IN2": "Oven_Low_Lim", "OUT": "Oven_AboveLow"}),
     ("LE", {"IN1": "OvenTemp", "IN2": "Oven_High_Lim", "OUT": "Oven_BelowHigh"}),
     ("LT", {"IN1": "OvenTemp", "IN2": "OvenMax", "OUT": "Oven_BelowMax"})]))

rungs.append(and_rung(
    "Oven_Ready window: OvenTemp within OvenSetpoint +- OvenBand (two compares in series = AND). The soak timer "
    "(stage 5) counts only while this is TRUE",
    "Oven_AboveLow", "Oven_BelowHigh", "Oven_Ready"))

rungs.append(chain_rung(
    "P controller 1/2: OvenError = OvenSetpoint - OvenTemp (for the HMI trend), Err_Lim = LIMIT(ErrMin, OvenError, "
    "ErrMax), P_x10 = Kp * Err_Lim. The LIMIT comes BEFORE the MUL: Kp 30 * 1000 = 30000 still fits INT (32767), "
    "an unlimited error of 3000 would overflow",
    [("SUB", {"IN1": "OvenSetpoint", "IN2": "OvenTemp", "OUT": "OvenError"}),
     ("LIMIT", {"MN": "ErrMin", "IN": "OvenError", "MX": "ErrMax", "OUT": "Err_Lim"}),
     ("MUL", {"IN1": "Kp", "IN2": "Err_Lim", "OUT": "P_x10"})]))

rungs.append(chain_rung(
    "P controller 2/2: P_Term = P_x10 / 10, Lamp_Sum = Bias + P_Term, Lamp_Calc = LIMIT(0, Lamp_Sum, 100) %. MUL "
    "before DIV (Kp 10, error 1: 10 / 10 = 1; dividing first gives 1 / 10 = 0). Lamp_Calc is the demand only: "
    "LampPower follows it in stage 6, while Lamps_On",
    [("DIV", {"IN1": "P_x10", "IN2": "10", "OUT": "P_Term"}),
     ("ADD", {"IN1": "Bias", "IN2": "P_Term", "OUT": "Lamp_Sum"}),
     ("LIMIT", {"MN": "0", "IN": "Lamp_Sum", "MX": "100", "OUT": "Lamp_Calc"})]))

rungs.append(chain_rung(
    "Pressure scaling in DINT: BlowPressure = Raw * PressFull / RawFull (0.1 bar). TO_DINT first: 27648 * 400 = "
    "11 059 200 does not fit an INT (32767). MUL before DIV, then TO_INT back (the result is at most about 470)",
    [("TO_DINT", {"IN": "BlowPressure_Raw", "OUT": "Raw_D"}),
     ("MUL", {"IN1": "Raw_D", "IN2": "PressFull", "OUT": "Raw_x400"}),
     ("DIV", {"IN1": "Raw_x400", "IN2": "RawFull", "OUT": "Press_D"}),
     ("TO_INT", {"IN": "Press_D", "OUT": "BlowPressure"})]))

rungs.append(chain_rung(
    "Pressure compares: Raw_AboveMin / Raw_BelowMax = sensor in range (live zero: below RawMin = wire break), "
    "Pressure_Reached = BlowPressure >= BlowSetpoint (starts the hold timer, stage 5), Press_Low = BlowPressure < "
    "ExhaustLimit",
    [("GE", {"IN1": "BlowPressure_Raw", "IN2": "RawMin", "OUT": "Raw_AboveMin"}),
     ("LE", {"IN1": "BlowPressure_Raw", "IN2": "RawMax", "OUT": "Raw_BelowMax"}),
     ("GE", {"IN1": "BlowPressure", "IN2": "BlowSetpoint", "OUT": "Pressure_Reached"}),
     ("LT", {"IN1": "BlowPressure", "IN2": "ExhaustLimit", "OUT": "Press_Low"})]))

rungs.append(and_rung(
    "Press_Range_OK: the raw value is inside RawMin..RawMax. Must stay above Pressure_Safe",
    "Raw_AboveMin", "Raw_BelowMax", "Press_Range_OK"))

rungs.append(and_rung(
    "Pressure_Safe: low pressure AND a sensor that can be trusted, only then may the mould open (stage 7). A wire "
    "break reads about -14 (low) but Press_Range_OK is FALSE, so the mould stays closed. Stage 9 adds NOT "
    "Alm_PressureSensor",
    "Press_Low", "Press_Range_OK", "Pressure_Safe"))

# ---- checks
names = set()
for r in rungs:
    nid = {n["id"] for n in r["nodes"]}
    for e in r["edges"]:
        assert e["source"] in nid and e["target"] in nid, "dangling edge"
    for n in r["nodes"]:
        v = n["data"].get("variable", {}).get("name")
        if v:
            names.add(v)
        for c in n["data"].get("connectedVariables", []):
            names.add(c["variable"]["name"])
unknown = {n for n in names if not is_literal(n) and n not in docs}
assert not unknown, f"undeclared: {unknown}"
all_rungs = bm_body["rungs"] + rungs
ids = [n["id"] for r in all_rungs for n in r["nodes"]] + [r["id"] for r in all_rungs]
assert len(ids) == len(set(ids)), "duplicate id"
nums = re.findall(r'"numericId": "(\d+)"', json.dumps(all_rungs))
assert len(nums) == len(set(nums)), "duplicate numericId"

out = new_head + "END_VAR\n\n" + json.dumps({"name": "main", "rungs": all_rungs}, indent=2) + "\nEND_PROGRAM"
if "--write" in sys.argv:
    if subprocess.run("ps -eo comm= | grep -qi open-plc", shell=True).returncode == 0:
        raise SystemExit("Editor is open: close it first")
    BM.write_text(out)
    print("written", BM)
else:
    (SCRATCH / "main.ld.stage2").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage2")
print(f"{len(rungs)} new rungs ({len(all_rungs)} total), {len(INTERNALS)} declarations, names: {sorted(names)}")
