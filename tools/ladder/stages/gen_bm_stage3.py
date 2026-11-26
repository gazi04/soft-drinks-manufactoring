"""Blow moulder ladder stage 3: section 3 conditioning (Outfeed_Full filter on C1_Full).

Clones the packer's rungs 17 (header) and 18-20 (Outfeed_Full filter on Table_Full), renames the
sensor to C1_Full. Feed_Empty_Timer moves to stage 8 (needs Oven_Feed), Mould_Click to stage 7.
Validation: every cloned rung equals the packer's after normalising ids and the one rename.
Dry run by default; --write writes main.ld (Editor must be closed).
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
P = packer["rungs"]
assert len(bm_body["rungs"]) == 22 and bm_body["rungs"][21]["comment"].startswith("Pressure_Safe"), "main.ld not at stage 2"
assert [P[k]["comment"][:28] for k in (18, 19, 20)] == ["Outfeed_Full filter 1/3: Ful", "Outfeed_Full filter 2/3: Ful",
                                                        "Outfeed_Full filter 3/3: sea"]

INTERNALS = [
    ("Full_OnTimer", "TON", "C1_Full ON 2 s without a break -> sets Outfeed_Full"),
    ("Full_OffTimer", "TON", "C1_Full OFF 3 s without a break -> releases Outfeed_Full"),
]
declared = set(re.findall(r"^\s+(\w+) :", bm_head, re.M))
assert not declared & {n for n, *_ in INTERNALS}, "already declared"
new_head = bm_head.rstrip(" ") + "\n".join(f"    {n} : {t}; (* {d} *)" for n, t, d in INTERNALS) + "\n  "

docs = {}
for m in re.finditer(r"^\s+(\w+) : (\w+)(?: AT (%\S+))?(?: := ([^;]+))?; \(\* (.*) \*\)$", new_head, re.M):
    docs[m.group(1)] = {"type": m.group(2), "location": m.group(3) or "", "doc": m.group(5)}

used_numeric = set(re.findall(r'"numericId": "(\d+)"', json.dumps(bm_body)))
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def fresh(obj):
    text = json.dumps(obj)
    for old in set(re.findall(UUID, text)):
        text = text.replace(old, str(uuid.uuid4()))

    def new_id(_m):
        while True:
            k = str(random.randint(1_000_000, 9_999_999))
            if k not in used_numeric:
                used_numeric.add(k)
                return f'"numericId": "{k}"'
    return json.loads(re.sub(r'"numericId": "\d+"', new_id, text))


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
        for v in obj.values():
            fix_meta(v)
    elif isinstance(obj, list):
        for v in obj:
            fix_meta(v)


MAP = {"Table_Full": "C1_Full"}
rungs = []
r = fresh(copy.deepcopy(P[17]))
r["comment"] = ("SECTION 3 CONDITIONING. Raw sensor -> clean signal for the sequencers: Outfeed_Full filter on C1_Full, "
                "every scan in every mode. (Feed_Empty_Timer is built in section 8 next to Starved, it needs the step "
                "contact Oven_Feed; the mould toggle edge Mould_Click in section 7.) (Header rung, no logic)")
rungs.append(r)
COMMENTS = {
    18: "Outfeed_Full filter 1/3: Full_OnTimer runs while C1_Full is ON; a bottle sliding past breaks the signal and "
        "restarts it. Only 2 s without a break means bottles are standing (report 5.3)",
    19: "Outfeed_Full filter 2/3: Full_OffTimer runs while C1_Full is OFF; finishes only after 3 s without a bottle at "
        "the sensor, so a short gap does not release Outfeed_Full",
    20: "Outfeed_Full filter 3/3: seal-in. Full_OnTimer.Q sets, the coil's own contact holds, Full_OffTimer.Q breaks. "
        "Between 2 s on and 3 s off the coil keeps its old value (hysteresis, no flicker). The oven loads no new "
        "preform and the ejector stays off while TRUE (stages 4, 7)",
}
for k, text in COMMENTS.items():
    r = fresh(copy.deepcopy(P[k]))
    rename(r, MAP)
    fix_meta(r)
    r["comment"] = text
    rungs.append(r)


# ---- validation: same logic as the packer's proven rungs (ids, comment, documentation/location normalised)
def norm(rung, mapping=None):
    rung = copy.deepcopy(rung)
    if mapping:
        rename(rung, mapping)
    text = json.dumps(rung)
    seen = {}
    for u in re.findall(UUID, text):
        seen.setdefault(u, f"U{len(seen)}")
    for u, k in seen.items():
        text = text.replace(u, k)
    text = re.sub(r'"numericId": "\d+"', '"numericId": "N"', text)
    rung = json.loads(text)
    rung["comment"] = ""

    def strip(o):
        if isinstance(o, dict):
            for key in ("documentation", "location"):
                o.pop(key, None)
            for v in o.values():
                strip(v)
        elif isinstance(o, list):
            for v in o:
                strip(v)
    strip(rung)
    return rung


for new, k in zip(rungs[1:], (18, 19, 20)):
    assert norm(new) == norm(P[k], MAP), f"rung differs from packer rung {k}"
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
unknown = {n for n in names if n and not re.fullmatch(r"T#\w+", n) and n.split(".")[0] not in docs}
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
    (SCRATCH / "main.ld.stage3").write_text(out)
    print("dry run ->", SCRATCH / "main.ld.stage3")
print(f"{len(rungs)} new rungs ({len(all_rungs)} total), names: {sorted(names)}; packer equality OK")
