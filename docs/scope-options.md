# Scope options – how many machines?

> **Decided 2026-11-09: option B** (filler + syrup room committed, the other machines stretch goals). Recorded in `project-report.md` section 11 and `CLAUDE.md`. The page below is the decision aid as written.

Decision aid, 2026-11-02. The report (section 11) plans five machines, built one at a time, end to end, with "the blow moulder replaced by a simple supply" as the fallback. This page compares the realistic options so the decision can be made **before simulator stage 7**, because it decides how much time the panel, FUXA and the other machines get.

## What one machine really cost

The filler (2026-10-27 → 2026-11-02): paper design, ladder stages 0–8 (157 checks), simulator stages 0–6 (88 unit tests + closed loop with the real PLC). **Still open for the filler:** FUXA screens (section 3 of `filler/progress.md`, nothing done), simulator stages 7–9 (commands/faults, Textual panel, HMI animation words), the remaining test scenarios of section 4.

The next machines are cheaper: `FB_Motor`, `FB_Valve`, the mode/alarm template, the register layout, the simulator framework (loop, link, plant, events, tests) are reused. What stays per machine: I/O list + state diagram, ladder, one physics class + tests, one PLC client, one FUXA screen, one container.

## What the filler alone already shows (syllabus, report section 10)

| Week | Topic | Filler alone |
| --- | --- | --- |
| III | I/O, motor control | ✅ C1, door, cap, index motors via `FB_Motor` |
| IV | Relay logic | ✅ seal-ins (`Stop_Request`, `Outfeed_Full`), interlocks |
| VI | Logic gates | ✅ permissives (fill only with bottle AND product …) |
| VII | Timers, counters | ✅ TON, TP (door), CTU (`GoodCount`) |
| VIII | Math, compare, MCR | ⚠️ compare (`FillLevel ≥ setpoint`) and master enable yes; **math** (e.g. syrup ratio) and analog process control missing |
| IX | Subroutines | ✅ `FB_Motor`, `FB_Valve` |
| X | Data handling | ✅ MOVE of setpoints, defaults |
| XI | Sequencer | ✅ step variable + compare |
| XIII | PLC networks | ⚠️ Modbus PLC ↔ simulator ↔ HMI yes; **PLC ↔ PLC** (handshake between machines) missing |

## Options

| | A. Filler only | **B. Filler + syrup room** | C. + labeler | D. All five (report plan) |
| --- | --- | --- | --- | --- |
| New machines | – | syrup room / carbonator | + labeler | + labeler, packer, blow moulder |
| Fills syllabus gaps | no (VIII math, XIII PLC↔PLC) | **yes**: ratio math, level/pressure compares, real `Product_Available` handshake between two PLCs | + CTU rejects, inspection, real C2 link | all of report section 10 |
| "Whole industry scenario" | one machine + stand-ins | process + packaging core | most of the line | complete line |
| Work (rough) | smallest | + 1 machine | + 2 machines | + 4 machines, 5 containers |
| Risk | lowest | low | medium | high (report already lists scope as risk no. 1) |
| Demo after each step | yes | yes | yes | yes (one machine at a time) |

The filler itself has to be finished in every option (FUXA screens, stages 7–9): the HMI and the fault scenarios are required, and they are the most visible part of a presentation.

## Recommendation

**Commit to B, treat C and D as stretch goals.**
- B closes the two syllabus gaps the filler leaves (math/analog in week VIII, PLC-to-PLC networking in week XIII) and turns `Product_Available` from a stand-in into a real handshake between two PLCs – the strongest demo of "PLC networks in manufacturing".
- It follows the report's own order (step 3 after the filler) and its rule "after each machine the project is a working demo", so stopping after B is not a failure, and C/D can be added if time remains.
- The other machines stay simulator stand-ins, which the architecture already supports (they replace a stand-in without changing the filler).

## Needed from you to decide

1. The **deadline** (presentation date) and how many weeks remain.
2. Whether the professor expects the **whole line** or accepts a deep subset with stand-ins (worth asking directly – the course context only says "a scenario from a real industry").
3. Order of work for the filler leftovers: FUXA first (visible, needed for the demo) or simulator stages 7–9 first.

After the decision: update report section 11 (and section 4 if machines are dropped), and the "Planned target" note in `CLAUDE.md`.
