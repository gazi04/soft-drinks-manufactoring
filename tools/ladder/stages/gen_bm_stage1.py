"""Blow moulder ladder stage 1: remove the stage 0 test rung, write section 1 (modes and safety).

Clones the packer's rungs 0-9 (header, defaults, mode / command decode, Cmd clear, heartbeat
watchdog 1-4, Master_Enable) and its Wrap_Home edge rung (-> Manual_Entry), renames the
setpoint names, adds one rung for the fifth default (BlowSetpoint), declares the internals.
"""
import copy
import json
import random
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tools/ladder/stages/x.py -> repository root
PACKER = ROOT / "plcs/packer/pous/programs/main.ld"
BM = ROOT / "plcs/blowmoulder/pous/programs/main.ld"


def split(path):
    s = path.read_text()
    i = s.index("END_VAR")
    j = s.rindex("END_PROGRAM")
    return s[:i], json.loads(s[i + len("END_VAR"):j])


bm_head, bm_body = split(BM)
_, packer = split(PACKER)
assert [r["comment"][:16] for r in bm_body["rungs"]] == ["STAGE 0 TEST RUN"], "unexpected rungs in main.ld"
test_rung = bm_body["rungs"][0]

# ---- internal variables of stage 1
INTERNALS = [
    ("Def_OvenSetpoint", "INT", "1050", "Default for OvenSetpoint, written once at the first scan"),
    ("Def_OvenBand", "INT", "50", "Default for OvenBand, written once at the first scan"),
    ("Def_Kp", "INT", "10", "Default for Kp, written once at the first scan"),
    ("Def_Bias", "INT", "0", "Default for Bias, written once at the first scan"),
    ("Def_BlowSetpoint", "INT", "350", "Default for BlowSetpoint, written once at the first scan"),
    ("Init_Done", "BOOL", "FALSE", "First-scan flag: setpoint defaults already written"),
    ("Mode_Stop", "BOOL", "FALSE", "Decoded ModeReq = 0 (Stop mode; the logic itself uses NOT Mode_Auto / NOT Mode_Manual)"),
    ("Mode_Auto", "BOOL", "FALSE", "Decoded ModeReq = 1: the sequencers control the machine"),
    ("Mode_Manual", "BOOL", "FALSE", "Decoded ModeReq = 2: the operator jogs single actuators"),
    ("Start_Cmd", "BOOL", "FALSE", "Decoded Cmd = 1, TRUE for one scan"),
    ("Stop_Cmd", "BOOL", "FALSE", "Decoded Cmd = 2, TRUE for one scan"),
    ("Reset_Cmd", "BOOL", "FALSE", "Decoded Cmd = 3, TRUE for one scan"),
    ("Count_Reset_Cmd", "BOOL", "FALSE", "Decoded Cmd = 4, TRUE for one scan: zero BottleCount (separate from Reset)"),
    ("Master_Enable", "BOOL", "FALSE", "MCR equivalent: E_Stop_OK AND NOT Alm_CommLost, gates every output FB (not ExhaustValve / MouldClose)"),
    ("Hb_Last", "BOOL", "FALSE", "Heartbeat value of the previous scan"),
    ("Hb_Changed", "BOOL", "FALSE", "Heartbeat changed in this scan (Heartbeat XOR Hb_Last)"),
    ("Hb_Timer", "TON", None, "Heartbeat watchdog: 3 s without a change -> Alm_CommLost"),
    ("Manual_Entry", "BOOL", "FALSE", "Rising edge of Mode_Manual: TRUE for one scan when Manual is entered (loads the mould toggle)"),
]
declared = set(re.findall(r"^\s+(\w+) :", bm_head, re.M))
assert not declared & {n for n, *_ in INTERNALS}, "already declared"
decl_lines = []
for n, t, init, doc in INTERNALS:
    decl_lines.append(f"    {n} : {t}{'' if init is None else ' := ' + init}; (* {doc} *)")
head = bm_head.rstrip()
assert head.endswith("\n  ") or head.endswith("  ") or True
# bm_head ends just before "END_VAR" (with its indentation)
cut = bm_head.rstrip(" ")
new_head = cut + "\n".join(decl_lines) + "\n  "

# documentation / location / type of every variable the rungs use
docs = {}
for m in re.finditer(r"^\s+(\w+) : (\w+)(?: AT (%\S+))?(?: := ([^;]+))?; \(\* (.*) \*\)$", new_head + "\n".join(decl_lines), re.M):
    docs[m.group(1)] = {"type": m.group(2), "location": m.group(3) or "", "init": m.group(4), "doc": m.group(5)}

used_numeric = set()


def fresh(rung):
    text = json.dumps(rung)
    for old in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        text = text.replace(old, str(uuid.uuid4()))

    def new_id(_m):
        while True:
            k = str(random.randint(1_000_000, 9_999_999))
            if k not in used_numeric:
                used_numeric.add(k)
                return f'"numericId": "{k}"'
    return json.loads(re.sub(r'"numericId": "\d+"', new_id, text))


def rename(obj, mapping):
    """Rename variable names (dicts with a 'name' key) everywhere in a rung, in one pass."""
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and obj["name"] in mapping:
            obj["name"] = mapping[obj["name"]]
        for v in obj.values():
            rename(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            rename(v, mapping)


def fix_meta(obj):
    """Variable dicts (with a 'class') get this project's location / type / documentation."""
    if isinstance(obj, dict):
        name = obj.get("name")
        if "class" in obj and name in docs and obj.get("class") == "local":
            d = docs[name]
            obj["location"] = d["location"]
            obj["documentation"] = d["doc"]
            if isinstance(obj.get("type"), dict) and obj["type"].get("definition") == "base-type":
                obj["type"]["value"] = d["type"]
        for v in obj.values():
            fix_meta(v)
    elif isinstance(obj, list):
        for v in obj:
            fix_meta(v)


rungs = []
p = packer["rungs"]

r = fresh(p[0])
r["comment"] = ("SECTION 1 MODES AND SAFETY. First-scan defaults, mode and command decode, master enable, "
                "heartbeat watchdog, Manual entry edge. (Header rung, no logic)")
rungs.append(r)

# fifth default: NOT Init_Done -> MOVE Def_BlowSetpoint -> BlowSetpoint (from the stage 0 test rung's shape)
r = fresh(copy.deepcopy(test_rung))
rename(r, {"E_Stop_OK": "Init_Done", "42": "Def_BlowSetpoint", "LampPower": "BlowSetpoint"})
for node in r["nodes"]:
    if node["type"] == "contact":
        node["data"]["variant"] = "negated"
        v = node["data"]["variable"]
        v.update(location="", documentation=docs["Init_Done"]["doc"])
    if node["type"] == "block":
        for cv in node["data"]["connectedVariables"]:
            if cv["handleId"] == "IN":
                cv["variable"] = {"name": "Def_BlowSetpoint", "class": "local",
                                  "type": {"definition": "base-type", "value": "INT"}, "location": "",
                                  "initialValue": "350", "documentation": docs["Def_BlowSetpoint"]["doc"], "debug": False}
            else:
                cv["variable"].update(location="%MW34", initialValue="350", documentation=docs["BlowSetpoint"]["doc"])
    if node["type"] == "variable" and node["data"]["variant"] == "input":
        node["data"]["variable"] = {"name": "Def_BlowSetpoint"}
r["comment"] = ("First scan 1/2: write the BlowSetpoint default once (the fifth setpoint does not fit the "
                "MOVE chain of the next rung). Must stay above First scan 2/2, which sets Init_Done")
rungs.append(r)

r = fresh(p[1])
rename(r, {"Def_OvenHyst": "Def_OvenBand", "OvenHyst": "OvenBand",
           "Def_OvenBand": "Def_Kp", "OvenBand": "Kp",
           "Def_FilmLength": "Def_Bias", "FilmLength": "Bias"})
r["comment"] = ("First scan 2/2: write the setpoint defaults once (located variables ignore their initial values): "
                "OvenSetpoint, OvenBand, Kp, Bias, then set Init_Done so the defaults are never written again and "
                "HMI changes are kept. Runs only while Init_Done = FALSE")
rungs.append(r)

for n in range(2, 10):
    r = fresh(p[n])
    r["comment"] = r["comment"].replace("PackCount", "BottleCount")
    rungs.append(r)

edge = next(x for x in p if x["comment"].startswith("Wrap_Home edge"))
r = fresh(edge)
rename(r, {"Wrap_Home": "Mode_Manual", "Wrap_Edge": "Manual_Entry"})
r["comment"] = ("Manual entry edge: P contact, Manual_Entry TRUE for one scan when Manual is entered. The mould "
                "toggle (stage 7) then copies the real mould position, so a mode change never moves the mould. "
                "Must stay below Mode decode")
rungs.append(r)

for r in rungs:
    fix_meta(r)

# ---- checks: every variable name used is declared (or a literal), ids unique
all_decl = set(docs)
text = json.dumps(rungs)
names = set()
for r in rungs:
    for node in r["nodes"]:
        v = node["data"].get("variable", {}).get("name")
        if v:
            names.add(v)
        for cv in node["data"].get("connectedVariables", []):
            names.add(cv["variable"].get("name"))
unknown = {n for n in names if n and not re.fullmatch(r"-?\d+|T#\w+", n) and n.split(".")[0] not in all_decl}
assert not unknown, f"undeclared: {unknown}"
ids = re.findall(r'"id": "((?:CONTACT|COIL|BLOCK|VARIABLE)_[^"]+)"', text)
node_ids = [node["id"] for r in rungs for node in r["nodes"]]
assert len(node_ids) == len(set(node_ids)), "duplicate node id"
for r in rungs:
    nid = {node["id"] for node in r["nodes"]}
    for e in r["edges"]:
        assert e["source"] in nid and e["target"] in nid, "dangling edge"

body = json.dumps({"name": "main", "rungs": rungs}, indent=2)
BM.write_text(new_head + "END_VAR\n\n" + body + "\nEND_PROGRAM")
print(f"{len(rungs)} rungs, {len(INTERNALS)} new declarations, names used: {sorted(names)}")
