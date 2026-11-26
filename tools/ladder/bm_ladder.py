"""Shared tools for the blow moulder ladder generators: load / save main.ld, rung builders, an offline
scan interpreter, and a fake Modbus client on a virtual clock that runs the real test scripts."""
import copy
import importlib
import json
import random
import re
import subprocess
import sys
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tools/ladder/bm_ladder.py -> repository root
BM = ROOT / "plcs/blowmoulder/pous/programs/main.ld"
TESTS = ROOT / "simulator/tests"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
DECL = re.compile(r"^\s+(\w+) : (\w+)(?: AT (%\S+))?(?: := ([^;]+))?; \(\* (.*) \*\)$", re.M)


def split_text(s):
    i = s.index("END_VAR")
    j = s.rindex("END_PROGRAM")
    return s[:i], json.loads(s[i + len("END_VAR"):j])


def split(path):
    return split_text(Path(path).read_text())


def join(head, rungs):
    return head + "END_VAR\n\n" + json.dumps({"name": "main", "rungs": rungs}, indent=2) + "\nEND_PROGRAM"


def editor_open():
    return subprocess.run("ps -eo comm= | grep -qi open-plc", shell=True).returncode == 0


def decls(head):
    return {m.group(1): {"type": m.group(2), "location": m.group(3) or "", "init": m.group(4), "doc": m.group(5)}
            for m in DECL.finditer(head)}


class Builder:
    """Rung builder working on one program: fresh ids, variable metadata from the declarations."""

    def __init__(self, head, body, internals=()):
        declared = set(decls(head))
        clash = declared & {n for n, *_ in internals}
        assert not clash, f"already declared: {clash}"
        lines = []
        for n, t, init, doc in internals:
            lines.append(f"    {n} : {t}{'' if init is None else ' := ' + init}; (* {doc} *)")
        self.head = head.rstrip(" ") + "".join(line + "\n" for line in lines) + "  " if lines else head
        self.docs = decls(self.head)
        self.used = set(re.findall(r'"numericId": "(\d+)"', json.dumps(body)))
        self.old_rungs = body["rungs"]
        _, pk = split(ROOT / "plcs/packer/pous/programs/main.ld")
        _, sy = split(ROOT / "plcs/syrup/pous/programs/main.ld")
        self.P, self.S = pk["rungs"], sy["rungs"]

    # ---- helpers
    def fresh(self, obj):
        text = json.dumps(obj)
        for old in set(re.findall(UUID, text)):
            text = text.replace(old, str(uuid.uuid4()))

        def new_id(_m):
            while True:
                k = str(random.randint(1_000_000, 9_999_999))
                if k not in self.used:
                    self.used.add(k)
                    return f'"numericId": "{k}"'
        return json.loads(re.sub(r'"numericId": "\d+"', new_id, text))

    @staticmethod
    def literal(name):
        return re.fullmatch(r"-?\d+|T#\w+|TRUE|FALSE", name) is not None

    def var(self, name):
        if self.literal(name):
            return {"name": name}
        d = self.docs[name.split(".")[0]]
        typ = d["type"] if "." not in name else "BOOL"
        return {"id": "", "name": name, "type": {"definition": "base-type", "value": typ}, "class": "local",
                "location": d["location"], "documentation": d["doc"], "debug": False}

    @staticmethod
    def shift(obj, dx, dy):
        if isinstance(obj, dict):
            for key in ("position", "glbPosition"):
                if isinstance(obj.get(key), dict) and "x" in obj[key]:
                    obj[key]["x"] += dx
                    obj[key]["y"] += dy
            for k, v in obj.items():
                if k not in ("position", "glbPosition", "relPosition", "style"):
                    Builder.shift(v, dx, dy)
        elif isinstance(obj, list):
            for v in obj:
                Builder.shift(v, dx, dy)

    @staticmethod
    def edge(s, t, sh, th):
        return {"id": f"e_{s}_{t}__{sh}_{th}", "source": s, "target": t, "sourceHandle": sh, "targetHandle": th}

    def rename(self, obj, mapping):
        if isinstance(obj, dict):
            if isinstance(obj.get("name"), str) and obj["name"] in mapping:
                obj["name"] = mapping[obj["name"]]
            for v in obj.values():
                self.rename(v, mapping)
        elif isinstance(obj, list):
            for v in obj:
                self.rename(v, mapping)

    def fix_meta(self, obj):
        if isinstance(obj, dict):
            name = obj.get("name")
            if obj.get("class") == "local" and isinstance(name, str) and name.split(".")[0] in self.docs:
                d = self.docs[name.split(".")[0]]
                obj["location"] = d["location"]
                obj["documentation"] = d["doc"]
                if "." not in name and isinstance(obj.get("type"), dict) and obj["type"].get("definition") == "base-type":
                    obj["type"]["value"] = d["type"]
            for v in obj.values():
                self.fix_meta(v)
        elif isinstance(obj, list):
            for v in obj:
                self.fix_meta(v)

    def clone(self, rung, comment, mapping=None):
        r = self.fresh(copy.deepcopy(rung))
        if mapping:
            self.rename(r, mapping)
        self.fix_meta(r)
        r["comment"] = comment
        return r

    def header(self, comment):
        return self.clone(self.P[17], comment)

    # ---- templates
    def _find(self, rungs, k, pred):
        return next(n for n in rungs[k]["nodes"] if pred(n))

    def _block_group(self, rungs, k, name):
        r = rungs[k]
        b = next(n for n in r["nodes"] if n["type"] == "block" and n["data"]["variant"]["name"] == name)
        vs = [n for n in r["nodes"] if n["type"] == "variable" and n["data"]["block"]["id"] == b["id"]]
        return b, vs

    BLOCK_TEMPLATES = {"SUB": ("P", 11), "ADD": ("P", 11), "GE": ("P", 12), "LE": ("P", 12), "LT": ("P", 12),
                       "LIMIT": ("P", 16), "DIV": ("P", 16), "MUL": ("S", 13), "TO_DINT": ("S", 16),
                       "EQ": ("P", 25), "MOVE": ("P", 56), "TON": ("P", 37)}

    def block(self, kind, x, wiring, y=14):
        src, k = self.BLOCK_TEMPLATES[kind]
        b, vs = self._block_group(getattr(self, src), k, kind)
        b, vs = self.fresh(copy.deepcopy([b, vs]))
        self.shift(b, x - b["position"]["x"], y - b["position"]["y"])
        b0 = self._block_group(getattr(self, src), k, kind)[0]["position"]
        for v in vs:
            self.shift(v, x - b0["x"], y - b0["y"])
        if kind == "TON":
            inst = wiring.pop("instance")
            b["data"]["variable"] = {"name": inst}
        handles = {c["handleId"] for c in b["data"]["connectedVariables"]}
        assert set(wiring) <= handles, (kind, wiring, handles)
        for c in b["data"]["connectedVariables"]:
            c["variable"] = self.var(wiring[c["handleId"]]) if c["handleId"] in wiring else {"name": ""}
        for v in vs:
            h = v["data"]["block"]["handleId"]
            v["data"]["variable"] = self.var(wiring[h]) if h in wiring else {"name": ""}
        return b, vs

    def _rails(self):
        tmpl = self.P[56]
        left = copy.deepcopy(tmpl["nodes"][0])
        right = copy.deepcopy(next(n for n in tmpl["nodes"] if n["type"] == "powerRail" and n["data"]["variant"] == "right"))
        skel = self.fresh({"id": tmpl["id"], "left": left, "right": right})
        r = {k: copy.deepcopy(v) for k, v in tmpl.items() if k not in ("nodes", "edges")}
        r["id"] = skel["id"]
        return r, skel["left"], skel["right"]

    def chain(self, comment, blocks):
        """Function blocks in series (EN -> ENO), each with its own input / output variables."""
        r, left, right = self._rails()
        nodes, var_nodes, edges = [left], [], []
        prev, prev_h, x = left["id"], "left-rail", 143
        for kind, wiring in blocks:
            b, vs = self.block(kind, x, dict(wiring))
            nodes.append(b)
            var_nodes += vs
            edges.append(self.edge(prev, b["id"], prev_h, "EN"))
            edges += self._var_edges(b, vs)
            prev, prev_h, x = b["id"], "ENO", x + 330
        self.shift(right, x + 20 - right["position"]["x"], 0)
        edges.append(self.edge(prev, right["id"], prev_h, "right-rail"))
        r.update(comment=comment, nodes=nodes + [right] + var_nodes, edges=edges)
        return r

    def _var_edges(self, b, vs):
        out = []
        for v in vs:
            h = v["data"]["block"]["handleId"]
            if v["data"]["variant"] == "input":
                out.append(self.edge(v["id"], b["id"], "output", h))
            else:
                out.append(self.edge(b["id"], v["id"], h, "input"))
        return out

    def _contact(self, name, variant, x):
        c = self.fresh(copy.deepcopy(self._find(self.P, 56, lambda n: n["type"] == "contact")))
        self.shift(c, x - c["position"]["x"], 0)
        c["data"]["variant"] = variant
        c["data"]["variable"] = self.var(name)
        return c

    def _coil(self, name, variant, x):
        c = self.fresh(copy.deepcopy(self._find(self.P, 47, lambda n: n["type"] == "coil")))
        self.shift(c, x - c["position"]["x"], 0)
        c["data"]["variant"] = variant
        c["data"]["variable"] = self.var(name)
        return c

    def series(self, comment, contacts, out):
        """Contacts in series -> one output: ("coil", name[, variant]) | ("move", [(in, out), ...]) |
        ("ton", instance, pt[, coil]). Contacts: "Name" (NO) or "!Name" (NC) or "^Name" (rising edge)."""
        r, left, right = self._rails()
        nodes, var_nodes, edges = [left], [], []
        prev, prev_h, x = left["id"], "left-rail", 68
        for c in contacts:
            variant = {"!": "negated", "^": "risingEdge"}.get(c[0], "default")
            node = self._contact(c.lstrip("!^"), variant, x)
            nodes.append(node)
            edges.append(self.edge(prev, node["id"], prev_h, "input"))
            prev, prev_h, x = node["id"], "output", x + 114
        x -= 114  # x = last contact
        kind = out[0]
        if kind == "coil":
            x += 114
            node = self._coil(out[1], out[2] if len(out) > 2 else "default", x)
            nodes.append(node)
            edges.append(self.edge(prev, node["id"], prev_h, "input"))
            prev, prev_h, rail = node["id"], "output", x + 138
        elif kind == "move":
            x += 189
            for i, (src, dst) in enumerate(out[1]):
                b, vs = self.block("MOVE", x, {"IN": src, "OUT": dst})
                nodes.append(b)
                var_nodes += vs
                edges.append(self.edge(prev, b["id"], prev_h, "EN"))
                edges += self._var_edges(b, vs)
                prev, prev_h = b["id"], "ENO"
                x += 318
            rail = x - 318 + 338
        elif kind == "ton":
            x += 189
            b, vs = self.block("TON", x, {"instance": out[1], "PT": out[2]})
            nodes.append(b)
            var_nodes += vs
            edges.append(self.edge(prev, b["id"], prev_h, "IN"))
            edges += self._var_edges(b, vs)
            prev, prev_h = b["id"], "Q"
            if len(out) > 3:
                x += 231
                node = self._coil(out[3], out[4] if len(out) > 4 else "default", x)
                nodes.append(node)
                edges.append(self.edge(prev, node["id"], prev_h, "input"))
                prev, prev_h, rail = node["id"], "output", x + 138
            else:
                rail = x + 326
        else:
            raise ValueError(kind)
        self.shift(right, rail - right["position"]["x"], 0)
        edges.append(self.edge(prev, right["id"], prev_h, "right-rail"))
        r.update(comment=comment, nodes=nodes + [right] + var_nodes, edges=edges)
        return r

    # ---- checks + output
    def check(self, rungs):
        names = set()
        for r in rungs:
            nid = {n["id"] for n in r["nodes"]}
            for e in r["edges"]:
                assert e["source"] in nid and e["target"] in nid, f"dangling edge in {r['comment'][:40]}"
            for n in r["nodes"]:
                v = n["data"].get("variable", {}).get("name")
                if v and n["type"] != "block":
                    names.add(v)
                if n["type"] == "block" and n["data"]["variant"]["type"] == "function-block":
                    names.add(n["data"]["variable"]["name"])
                for c in n["data"].get("connectedVariables", []):
                    names.add(c["variable"]["name"])
        unknown = {n for n in names if n and not self.literal(n) and n.split(".")[0] not in self.docs}
        assert not unknown, f"undeclared: {unknown}"
        all_rungs = self.old_rungs + rungs
        ids = [n["id"] for r in all_rungs for n in r["nodes"]] + [r["id"] for r in all_rungs]
        assert len(ids) == len(set(ids)), "duplicate id"
        nums = re.findall(r'"numericId": "(\d+)"', json.dumps(all_rungs))
        assert len(nums) == len(set(nums)), "duplicate numericId"
        return names

    def output(self, rungs):
        return join(self.head, self.old_rungs + rungs)


# ================================================================== offline PLC
def lit(v):
    if v in ("TRUE", "FALSE"):
        return v == "TRUE"
    m = re.fullmatch(r"T#(\d+)(ms|s|m)", v or "")
    if m:
        return int(m.group(1)) * {"ms": 1, "s": 1000, "m": 60000}[m.group(2)]
    return int(v)


def i16(x):
    x &= 0xFFFF
    return x - 0x10000 if x >= 0x8000 else x


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


FUNCS = {
    "SUB": lambda a: a["IN1"] - a["IN2"], "ADD": lambda a: a["IN1"] + a["IN2"], "MUL": lambda a: a["IN1"] * a["IN2"],
    "DIV": lambda a: tdiv(a["IN1"], a["IN2"]), "GE": lambda a: a["IN1"] >= a["IN2"], "LE": lambda a: a["IN1"] <= a["IN2"],
    "LT": lambda a: a["IN1"] < a["IN2"], "GT": lambda a: a["IN1"] > a["IN2"], "EQ": lambda a: a["IN1"] == a["IN2"],
    "LIMIT": lambda a: max(a["MN"], min(a["IN"], a["MX"])), "MOVE": lambda a: a["IN"],
    "TO_DINT": lambda a: a["IN"], "TO_INT": lambda a: i16(a["IN"]), "XOR": lambda a: bool(a["IN1"]) != bool(a["IN2"]),
}
POWER_IN = {"TON": "IN", "TOF": "IN", "TP": "IN"}


class OfflinePlc:
    """Scan interpreter for main.ld (contacts, coils, parallel branches, EQ/MOVE/math blocks, TON)."""

    def __init__(self, text, scan_ms=20):
        head, body = split_text(text)
        self.decl = decls(head)
        self.rungs = body["rungs"]
        self.scan_ms = scan_ms
        self.mem, self.prev_edge = {}, {}
        for n, d in self.decl.items():
            if d["type"] == "CTU":
                self.mem[n] = {"CU": False, "CV": 0, "Q": False}
                continue
            if d["type"] in ("FB_Motor", "FB_Valve"):
                self.mem[n] = {}
                continue
            if d["type"] in ("TON", "TOF", "TP"):
                self.mem[n] = {"Q": False, "ET": 0, "IN": False}
            elif d["init"] is not None:
                self.mem[n] = lit(d["init"].strip())
            else:
                self.mem[n] = False if d["type"] == "BOOL" else 0
        self.order = [self._prepare(r) for r in self.rungs]

    def _prepare(self, r):
        nodes = {n["id"]: n for n in r["nodes"]}
        incoming = {}
        for e in r["edges"]:
            incoming.setdefault(e["target"], []).append(e)
        def key(n):
            close = n["type"] == "parallel" and n["data"].get("type") == "close"
            return (n["position"]["x"], -n["position"]["y"] if close else n["position"]["y"])
        seq = sorted((n for n in r["nodes"] if n["type"] in ("contact", "coil", "block", "parallel")), key=key)
        return nodes, incoming, seq

    def get(self, name):
        if Builder.literal(name):
            return lit(name)
        if "." in name:
            inst, field = name.split(".")
            return self.mem[inst][field]
        return self.mem[name]

    def set(self, name, value):
        d = self.decl.get(name)
        if d and d["type"] == "INT" and not isinstance(value, bool):
            value = i16(value)
        self.mem[name] = value

    def scan(self):
        for nodes, incoming, seq in self.order:
            out = {}  # (node id, handle) -> power

            def power(nid, handles=None):
                p = False
                for e in incoming.get(nid, []):
                    if handles and e["targetHandle"] not in handles:
                        continue
                    src = nodes[e["source"]]
                    if src["type"] == "variable":
                        continue
                    if src["type"] == "powerRail":
                        p = True
                    else:
                        p = p or out.get((e["source"], e["sourceHandle"]), False)
                return p

            for n in seq:
                d, t = n["data"], n["type"]
                if t == "parallel":
                    p = power(n["id"])
                    for h in ("output-right", "output-down", "output-top"):
                        out[(n["id"], h)] = p
                elif t == "contact":
                    p, name, v = power(n["id"]), d["variable"]["name"], d["variant"]
                    val = bool(self.get(name))
                    if v == "default":
                        q = val
                    elif v == "negated":
                        q = not val
                    elif v in ("risingEdge", "fallingEdge"):
                        before = self.prev_edge.get(n["id"], False)
                        q = (val and not before) if v == "risingEdge" else (before and not val)
                        self.prev_edge[n["id"]] = val
                    else:
                        raise ValueError(v)
                    out[(n["id"], "output")] = p and q
                elif t == "coil":
                    p, name, v = power(n["id"]), d["variable"]["name"], d["variant"]
                    if v == "default":
                        self.set(name, p)
                    elif v == "negated":
                        self.set(name, not p)
                    elif v == "set":
                        if p:
                            self.set(name, True)
                    elif v == "reset":
                        if p:
                            self.set(name, False)
                    else:
                        raise ValueError(v)
                    out[(n["id"], "output")] = p
                elif t == "block":
                    kind = d["variant"]["name"]
                    cv = {c["handleId"]: c["variable"].get("name", "") for c in d["connectedVariables"]}
                    if kind in ("FB_Motor", "FB_Valve"):
                        en = power(n["id"], {"EN"})
                        if en:
                            req = self.get(cv["Run" if kind == "FB_Motor" else "Open"])
                            self.set(cv["Out"], bool(req) and bool(self.get(cv["Enable"])))
                        out[(n["id"], "ENO")] = en
                        continue
                    if kind == "CTU":
                        inst = self.mem[d["variable"]["name"]]
                        cu = power(n["id"], {"CU"})
                        if self.get(cv["R"]):
                            inst["CV"] = 0
                        elif cu and not inst["CU"] and inst["CV"] < 32767:
                            inst["CV"] += 1
                        inst["CU"] = cu
                        inst["Q"] = inst["CV"] >= self.get(cv["PV"])
                        if cv.get("CV"):
                            self.set(cv["CV"], inst["CV"])
                        out[(n["id"], "Q")] = inst["Q"]
                        continue
                    if kind in POWER_IN:
                        inst = self.mem[d["variable"]["name"]]
                        p = power(n["id"], {POWER_IN[kind]})
                        pt = self.get(cv["PT"])
                        if kind == "TON":
                            inst["ET"] = min(inst["ET"] + self.scan_ms, pt) if p else 0
                            inst["Q"] = p and inst["ET"] >= pt
                        else:
                            raise NotImplementedError(kind)
                        inst["IN"] = p
                        if cv.get("ET"):
                            self.set(cv["ET"], inst["ET"])
                        if cv.get("Q"):
                            self.set(cv["Q"], inst["Q"])
                        out[(n["id"], "Q")] = inst["Q"]
                    else:
                        en = power(n["id"], {"EN"})
                        if en:
                            args = {h: self.get(nm) for h, nm in cv.items() if h != "OUT"}
                            self.set(cv["OUT"], FUNCS[kind](args))
                        out[(n["id"], "ENO")] = en


class Clock:
    def __init__(self, plc):
        self.plc, self.t = plc, 0.0
        self.hb_period, self.hb_next, self.hb_on, self.hb_obj = None, 0.0, False, None

    def sleep(self, s):
        """Advance the clock by s; a scan runs at every 20 ms boundary crossed (a short Modbus request
        between two boundaries runs none, as on the real PLC)."""
        end = self.t + max(s, 0)
        period = self.plc.scan_ms / 1000
        if not hasattr(self, "next_scan"):
            self.next_scan = period
        while self.next_scan <= end + 1e-12:
            self.t = self.next_scan
            self.next_scan += period
            if self.hb_on and self.t >= self.hb_next:
                self.plc.mem["Heartbeat"] = not self.plc.mem["Heartbeat"]
                self.hb_next = self.t + 0.5
                if self.hb_obj is not None:
                    self.hb_obj.last_change = self.t
            self.plc.scan()
        self.t = end

    def monotonic(self):
        return self.t


class Result:
    def __init__(self, registers=None, bits=None):
        self.registers, self.bits = registers, bits

    def isError(self):
        return False


class FakeClient:
    """pymodbus ModbusTcpClient look-alike on the offline PLC (addresses from the located variables)."""
    plc = clock = None

    def __init__(self, *a, **k):
        pass

    def connect(self):
        return True

    def close(self):
        pass

    @classmethod
    def maps(cls):
        hr, coil = {}, {}
        for n, d in cls.plc.decl.items():
            loc = d["location"]
            if loc.startswith("%MW"):
                hr[1024 + int(loc[3:])] = n
            elif loc.startswith("%QW"):
                hr[int(loc[3:])] = n
            elif loc.startswith("%MX") or loc.startswith("%QX"):
                a, b = loc[3:].split(".")
                coil[(8192 if loc[1] == "M" else 0) + 8 * int(a) + int(b)] = n
        return hr, coil

    def _tick(self):
        self.clock.sleep(0.002)

    def write_register(self, address, value, **k):
        hr, _ = self.maps()
        self.plc.set(hr[address], i16(value))
        self._tick()

    def read_holding_registers(self, address, count=1, **k):
        hr, _ = self.maps()
        self._tick()
        return Result(registers=[(self.plc.get(hr[a]) if a in hr else 0) & 0xFFFF for a in range(address, address + count)])

    def write_coil(self, address, value, **k):
        _, coil = self.maps()
        self.plc.set(coil[address], bool(value))
        self._tick()

    def read_coils(self, address, count=1, **k):
        _, coil = self.maps()
        self._tick()
        return Result(bits=[bool(self.plc.get(coil[a])) if a in coil else False for a in range(address, address + count)])


def run_test(text, script, argv=()):
    """Run a real test script's main() against the offline PLC. Returns its exit code."""
    plc = OfflinePlc(text)
    clock = Clock(plc)
    clock.sleep(0.2)  # the program has been running before the test starts (first-scan defaults done)
    FakeClient.plc, FakeClient.clock = plc, clock
    sys.path.insert(0, str(TESTS))
    fake_guard = types.ModuleType("hmi_guard")
    fake_guard.claim_plant = lambda *a, **k: None
    fake_guard.warn_if_fuxa = lambda *a, **k: None
    sys.modules["hmi_guard"] = fake_guard
    import pymodbus.client
    pymodbus.client.ModbusTcpClient = FakeClient
    import time as real_time
    fake_time = types.ModuleType("time")
    fake_time.sleep, fake_time.monotonic, fake_time.time = clock.sleep, clock.monotonic, clock.monotonic
    for name in [n for n, m in sys.modules.items() if getattr(m, '__file__', None) and str(TESTS) in str(m.__file__)]:
        sys.modules.pop(name)
    sys.modules.pop(script, None)
    saved_time = sys.modules["time"]
    sys.modules["time"] = fake_time
    try:
        mod = importlib.import_module(script)
    finally:
        sys.modules["time"] = saved_time
    for m in list(sys.modules.values()):  # helper modules the script imported from tests/ run on the same clock
        if getattr(m, "__file__", None) and str(TESTS) in str(m.__file__):
            if hasattr(m, "time"):
                m.time = fake_time
            if hasattr(m, "ModbusTcpClient"):
                m.ModbusTcpClient = FakeClient
            if hasattr(m, "Heartbeat") and m is not mod:
                pass
    mod.time = fake_time
    mod.ModbusTcpClient = FakeClient
    if hasattr(mod, "Heartbeat"):
        class HB:
            def __init__(self, *a, **k):
                self.stop_event = types.SimpleNamespace(set=self.stop)
                clock.hb_obj = self

            last_change = 0.0

            def start(self):
                clock.hb_on, clock.hb_next = True, clock.t + 0.5
                self.last_change = clock.t

            def stop(self, freeze_at=None):
                clock.hb_on = False
                if freeze_at is not None and plc.mem["Heartbeat"] != freeze_at:
                    plc.mem["Heartbeat"] = freeze_at
                    self.last_change = clock.t

            def join(self, *a):
                pass
        mod.Heartbeat = HB
    old_argv = sys.argv
    sys.argv = [script, *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv
        del real_time


# ================================================================== stage 7 extensions: OR branches, FB chains
def _par_templates(self):
    r = self.P[78]
    op = next(n for n in r["nodes"] if n["type"] == "parallel" and n["data"]["type"] == "open")
    cl = next(n for n in r["nodes"] if n["type"] == "parallel" and n["data"]["type"] == "close")
    return op, cl


def _place(self, tmpl, x, y):
    n = self.fresh(copy.deepcopy(tmpl))
    self.shift(n, x - n["position"]["x"], y - n["position"]["y"])
    return n


def rung(self, comment, branches, series=(), out=("coil", "X")):
    """(branch1 OR branch2 OR ...) AND series -> out. A branch is a list of contacts ("Name", "!Name", "^Name")."""
    branches = [list(b) for b in branches]
    if len(branches) == 1:
        return self.series(comment, branches[0] + list(series), out)
    r, left, right = self._rails()
    nodes, var_nodes, edges = [left], [], []
    op_t, cl_t = self._par_templates()
    width = max(len(b) for b in branches)
    close_x = 72 + 114 * width - 45
    opens = [self._place(op_t, 23, 49 + 92 * i) for i in range(len(branches) - 1)]
    closes = [self._place(cl_t, close_x, 49 + 92 * i) for i in range(len(branches) - 1)]
    for o, c in zip(opens, closes):
        o["data"]["parallelCloseReference"] = c["id"]
        c["data"]["parallelOpenReference"] = o["id"]
    nodes += opens + closes
    edges.append(self.edge(left["id"], opens[0]["id"], "left-rail", "input"))
    for i in range(len(opens) - 1):
        edges.append(self.edge(opens[i]["id"], opens[i + 1]["id"], "output-down", "input-top"))
        edges.append(self.edge(closes[i + 1]["id"], closes[i]["id"], "output-top", "input-down"))
    for i, b in enumerate(branches):
        last = i == len(branches) - 1
        src, sh = (opens[-1]["id"], "output-down") if last else (opens[i]["id"], "output-right")
        for j, c in enumerate(b):
            variant = {"!": "negated", "^": "risingEdge"}.get(c[0], "default")
            node = self._contact(c.lstrip("!^"), variant, 72 + 114 * j)
            self.shift(node, 0, 38 + 92 * i - node["position"]["y"])
            nodes.append(node)
            edges.append(self.edge(src, node["id"], sh, "input"))
            src, sh = node["id"], "output"
        dst, th = (closes[-1]["id"], "input-down") if last else (closes[i]["id"], "input")
        edges.append(self.edge(src, dst, sh, th))
    prev, prev_h, x = closes[0]["id"], "output-right", close_x + 49
    for c in series:
        variant = {"!": "negated", "^": "risingEdge"}.get(c[0], "default")
        node = self._contact(c.lstrip("!^"), variant, x)
        nodes.append(node)
        edges.append(self.edge(prev, node["id"], prev_h, "input"))
        prev, prev_h, x = node["id"], "output", x + 114
    x -= 114
    kind = out[0]
    if kind == "coil":
        x += 114
        node = self._coil(out[1], out[2] if len(out) > 2 else "default", x)
        nodes.append(node)
        edges.append(self.edge(prev, node["id"], prev_h, "input"))
        prev, prev_h, rail = node["id"], "output", x + 138
    elif kind == "move":
        x += 189
        for src_v, dst in out[1]:
            b, vs = self.block("MOVE", x, {"IN": src_v, "OUT": dst})
            nodes.append(b)
            var_nodes += vs
            edges.append(self.edge(prev, b["id"], prev_h, "EN"))
            edges += self._var_edges(b, vs)
            prev, prev_h = b["id"], "ENO"
            x += 318
        rail = x - 318 + 338
    elif kind == "ton":
        x += 189
        b, vs = self.block("TON", x, {"instance": out[1], "PT": out[2]})
        nodes.append(b)
        var_nodes += vs
        edges.append(self.edge(prev, b["id"], prev_h, "IN"))
        edges += self._var_edges(b, vs)
        prev, prev_h = b["id"], "Q"
        x += 231
        node = self._coil(out[3], out[4] if len(out) > 4 else "default", x)
        nodes.append(node)
        edges.append(self.edge(prev, node["id"], prev_h, "input"))
        prev, prev_h, rail = node["id"], "output", x + 138
    else:
        raise ValueError(kind)
    self.shift(right, rail - right["position"]["x"], 0)
    edges.append(self.edge(prev, right["id"], prev_h, "right-rail"))
    r.update(comment=comment, nodes=nodes + [right] + var_nodes, edges=edges)
    return r


def fb_chain(self, comment, fbs):
    """FB instances chained EN -> ENO: (kind, instance, request, output). Enable = Master_Enable."""
    r, left, right = self._rails()
    nodes, var_nodes, edges = [left], [], []
    prev, prev_h, x = left["id"], "left-rail", 143
    src = {}
    for n in self.P[85]["nodes"] + self.P[87]["nodes"]:
        if n["type"] == "block":
            src.setdefault(n["data"]["variant"]["name"], (n, [v for v in self.P[85]["nodes"] + self.P[87]["nodes"]
                                                          if v["type"] == "variable" and v["data"]["block"]["id"] == n["id"]]))
    for kind, inst, req, outv in fbs:
        b0, vs0 = src[kind]
        b, vs = self.fresh(copy.deepcopy([b0, vs0]))
        dx, dy = x - b0["position"]["x"], 14 - b0["position"]["y"]
        self.shift(b, dx, dy)
        for v in vs:
            self.shift(v, dx, dy)
        b["data"]["variable"] = {"name": inst}
        wiring = {("Run" if kind == "FB_Motor" else "Open"): req, "Enable": "Master_Enable", "Out": outv}
        for c in b["data"]["connectedVariables"]:
            c["variable"] = self.var(wiring[c["handleId"]])
        for v in vs:
            h = v["data"]["block"]["handleId"]
            v["data"]["variable"] = self.var(wiring[h]) if h in wiring else {"name": ""}
        nodes.append(b)
        var_nodes += vs
        edges.append(self.edge(prev, b["id"], prev_h, "EN"))
        edges += self._var_edges(b, vs)
        prev, prev_h = b["id"], "ENO"
        x += 414 if kind == "FB_Motor" else 426
    self.shift(right, x - 414 + 446 - right["position"]["x"], 0)
    edges.append(self.edge(prev, right["id"], prev_h, "right-rail"))
    r.update(comment=comment, nodes=nodes + [right] + var_nodes, edges=edges)
    return r


Builder._par_templates = _par_templates
Builder._place = _place
Builder.rung = rung
Builder.fb_chain = fb_chain


# ================================================================== command line
BLOWMOULDER_TESTS = ["test_blowmoulder_stage1", "test_blowmoulder_values", "test_blowmoulder_conditioning",
                     "test_blowmoulder_oven_seq", "test_blowmoulder_mould_seq", "test_blowmoulder_lamps",
                     "test_blowmoulder_outputs", "test_blowmoulder_stop_state", "test_blowmoulder_alarms"]


def _cli():
    """Offline check of a main.ld with the real Modbus test scripts (no PLC, no Editor needed).

        cd simulator
        uv run python ../tools/ladder/bm_ladder.py test test_blowmoulder_alarms --quick
        uv run python ../tools/ladder/bm_ladder.py all                      # every blow moulder script
        uv run python ../tools/ladder/bm_ladder.py all --file /tmp/main.ld.stage9   # a dry-run output
    """
    import argparse
    import contextlib
    import io
    parser = argparse.ArgumentParser(description=_cli.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["test", "all"])
    parser.add_argument("script", nargs="?", help="test script name without .py (command test)")
    parser.add_argument("--file", default=str(BM), help="main.ld to run (default: the blow moulder project)")
    args, rest = parser.parse_known_args()
    text = Path(args.file).read_text()
    if args.command == "test":
        if not args.script:
            parser.error("test needs a script name")
        sys.exit(run_test(text, args.script, rest))
    failed = 0
    for script in BLOWMOULDER_TESTS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_test(text, script, ["--quick"] if script.endswith("alarms") else [])
        summary = next((line for line in reversed(buf.getvalue().splitlines()) if "checks passed" in line), "?")
        print(f"{'PASS' if code == 0 else 'FAIL'}  {script:34} {summary.split(' (')[0]}")
        failed += code != 0
    print(f"\n{len(BLOWMOULDER_TESTS) - failed}/{len(BLOWMOULDER_TESTS)} scripts passed offline")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    _cli()
