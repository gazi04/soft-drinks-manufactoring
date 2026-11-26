# Ladder helper (`bm_ladder.py`)

A helper script for the OpenPLC Editor v4 projects in `plcs/`. It was used to build the blow moulder's
ladder (stages 0–9, 2026-11-25). It does two jobs:

1. **Writes rungs into `main.ld`.** It copies proven rung shapes from the packer and syrup room
   projects and gives them fresh ids and this project's variables, so the Editor's JSON is never typed by
   hand.
2. **Runs a fake PLC offline.** It runs `main.ld` scan by scan on a simulated clock, behind a fake Modbus
   client, so the **real test scripts** in `simulator/tests/` run against a program **before** it is
   uploaded. No runtime and no Editor are needed.

The real PLC always has the last word: a stage counts as done only when its test passes on the uploaded
program.

## Offline check

```bash
cd simulator
uv run python ../tools/ladder/bm_ladder.py all                                  # all blow moulder scripts (~1 min)
uv run python ../tools/ladder/bm_ladder.py test test_blowmoulder_outputs        # one script
uv run python ../tools/ladder/bm_ladder.py test test_blowmoulder_alarms --quick # extra args go to the script
uv run python ../tools/ladder/bm_ladder.py all --file /tmp/main.ld.stage9       # a dry-run output instead of the project
```

Run it from `simulator/` with `uv run`, because the test scripts import `pymodbus`.

`test_blowmoulder_stage0.py` is not in `all`: it checks the other four PLCs, which the fake PLC doesn't
model.

## Writing rungs

```python
import bm_ladder as L
head, body = L.split(L.BM)                          # declarations text + rung JSON of main.ld
B = L.Builder(head, body, [("My_Bit", "BOOL", "FALSE", "comment")])   # new internal variables
r1 = B.series("comment", ["Seq_Enable", "!Oven_Fault", "^Man_X"], ("coil", "My_Bit"))     # NO / NC / P contact
r2 = B.rung("comment", [["A", "B"], ["Mode_Manual", "C"]], ["!Jog_Block"], ("coil", "Req_X")) # (A AND B OR ...) AND ...
r3 = B.series("comment", ["Mould_Hold"], ("ton", "Hold_Timer", "HoldTime"))                # TON, optional coil after Q
r4 = B.series("comment", ["Start_Accepted"], ("move", [("10", "Step"), ("10", "MouldStep")]))  # MOVE chain
r5 = B.chain("comment", [("SUB", {"IN1": "A", "IN2": "B", "OUT": "C"}), ("LIMIT", {...})])     # function blocks in series
r6 = B.fb_chain("comment", [("FB_Valve", "Vlv_Rod", "Req_Rod", "StretchRod")])                 # FB instances, Enable = Master_Enable
r7 = B.clone(B.P[89], "comment", {"Oven_Exit": "Bottle_Ejected"})                           # copy a packer rung and rename
B.check([r1, r2])                                   # every name declared, no dangling edge, ids unique
L.BM.write_text(L.join(B.head, body["rungs"] + [r1, r2]))   # or insert at the right index
```

- Coil variants: `("coil", name, "set" | "reset")`; after a TON: `("ton", inst, PT, coil, "set")`.
- Blocks with templates: SUB, ADD, MUL, DIV, LIMIT, EQ, GE, LE, LT, MOVE, TON, TO_DINT, TO_INT, FB_Motor,
  FB_Valve; CTU via `clone` of the packer's counter rung.

## Rules learned while building the blow moulder

- **The Editor must be closed** while `main.ld` is written. An open Editor overwrites the file when it saves.
- **Dry run first:** each generator writes `main.ld.stageN` into the temp folder; `--write` writes the
  project only after the offline check passed.
- **Planted bugs:** break one contact on purpose and check that a test fails. That shows the test really
  covers the rung. Two kinds of rule can't be seen over Modbus: anything that only lasts one scan (20 ms),
  such as a limit alarm reset without its cause-gone contact.
- **Outputs follow a step change one scan later:** the transition in section 6 writes the new step, but
  section 7 still decodes the old one. Tests wait 0.1 s after a step change before reading outputs.
- **Write the sensor that blocks a transition first**, or there is a moment where all conditions are TRUE
  and the PLC moves on.
- **Last write wins** within one scan, so "default, then override" rungs replace OR branches and long NC
  chains (lamps, Start, State). Modbus only sees the value at the end of the scan.

## What the fake PLC models

- **Modelled:** contacts (NO, NC, P), coils (plain, set, reset), parallel branches, EQ / GE / LE / LT / GT,
  ADD / SUB / MUL / DIV (INT wrap, division truncating toward 0), LIMIT, MOVE, TO_DINT / TO_INT, XOR, TON,
  CTU, FB_Motor / FB_Valve (Out = request AND Enable).
- **Timing:** a 20 ms task. A Modbus request between two scan boundaries runs no scan, as on the runtime.
- **Not modelled:** TOF, TP, CTUD, located `%IX` inputs, retain. Add them in `OfflinePlc.scan` before
  using them.

## `stages/`

`gen_bm_stage0.py` … `gen_bm_stage9.py` are the generators that built each stage. They are kept as worked
examples. Each one checks that `main.ld` is at the stage before (number of rungs, last rung), so they
don't run again on the finished program.
