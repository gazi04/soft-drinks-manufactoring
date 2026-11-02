# PLC Course Project – Context & Decisions

This document summarizes the planning discussion for a university PLC project.
It is meant as a starting brief for Claude Code. Read it fully before proposing
code or structure.

---

## 1. Background

- **Student:** Gazi – master's student in _Control Systems and AI_, background in
  Computer Science / backend development (comfortable with Python, Docker, Git).
- **Course:** PLC Programming (see section 2a for the syllabus). The lab
  exercises use **Delta PLCs** (ISPSoft – Windows-only), but the project is
  approved to be built with OpenPLC.
- **Development OS:** **Arch Linux** (daily OS). Everything should run natively
  on Linux.

## 2. What the professor requires

- Take a **scenario from a real industry** (examples given: meat industry,
  soft drinks).
- Design the **whole production process from beginning to end** – how the
  product is made – and write the **PLC program** that controls it.

### Expected parts of the project

1. Process description (industry, every stage from raw material to finished product)
2. Process / flow diagram (tanks, pumps, valves, conveyors, sensors)
3. I/O list (every input and output with its address)
4. Sequence description – ideally a state diagram / SFC before writing ladder
5. PLC program (Ladder, possibly Structured Text), organized per station
6. Operating modes: Auto, Manual, Stop
7. Safety & alarms: emergency stop, overflow, over-temperature, jam detection (timeouts)
8. Testing / simulation
9. Documentation and presentation

### Tool approval

- **The professor approved using OpenPLC** for this project. The program does
  not need to be delivered in Delta ISPSoft, so no Windows VM or porting step
  is required. (Use IEC addresses like %IX0.0, %QX0.0, %MW0 – not Delta's
  X/Y/M/T/C/D.)

## 2a. Course syllabus (official) – what the project must demonstrate

**Course:** PLC Programming – Master, Year I / Sem I, mandatory, 6 ECTS,
2 h lectures + 2 h lab exercises per week. Professor: Prof. Dr. Sc. Pajtim Vela,
Public University "Kadri Zeka" Gjilan.

### Learning outcomes (the project should visibly cover these)

- Write a working PLC program **using ladder logic** → **Ladder Diagram is the
  primary language.** Use Structured Text only where ladder is clearly
  unsuitable (e.g. complex math), and keep the core control logic in ladder.
- Install and troubleshoot the program
- Integrate PLCs into electro-mechanical systems
- Compare **sequential** and **combinational** control processes → the project
  should contain both, clearly labeled (e.g. interlocks/permissives =
  combinational; station steps = sequential)
- Describe proper grounding practices and preventive maintenance → cover
  briefly in the documentation (grounding of sensors/motors, maintenance
  counters such as motor run-hours or cycle counts)
- Define and identify the functions of PLC instructions → document which
  instruction is used where and why

### Weekly topics – try to use each one somewhere in the project

| Week | Topic                         | How it can appear in the project                                                                                                                                                                                                  |
| ---- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| III  | I/O devices & motor controls  | Start/stop motor circuits, contactors, sensors                                                                                                                                                                                    |
| IV   | Relay logic diagrams          | Seal-in / latching circuits, interlocks                                                                                                                                                                                           |
| VI   | Logic gate functions          | AND/OR/NOT permissive conditions                                                                                                                                                                                                  |
| VII  | Timers & counters             | TON/TOF/TP (mixing, holding times, jam timeouts), CTU/CTD (packages per crate, batch count)                                                                                                                                       |
| VIII | Math, compare, jump, MCR      | Weight/temperature calculations and comparisons; **MCR** → in IEC/OpenPLC there is no MCR instruction, implement the equivalent with a master enable condition (e.g. E-stop / Auto mode gating a whole section) and document that |
| IX   | Subroutines                   | Reusable function blocks (e.g. one FB per motor or per station)                                                                                                                                                                   |
| X    | Data handling                 | MOVE, recipe/setpoint values, copying sensor values                                                                                                                                                                               |
| XI   | Sequencer                     | Station step sequences; IEC has no SQO-style sequencer instruction, so implement it with a step counter/state variable + compare (or SFC if available) and document it as the sequencer                                           |
| XIII | PLC networks in manufacturing | **Modbus TCP** between OpenPLC, the Python simulator and FUXA – explicitly present this as the PLC network part                                                                                                                   |

### Teaching methodology & grading (relevant to how the project is delivered)

- The project is divided into **homework assignments**; deliverables are the
  realized project, **project documentation**, and a **project presentation**.
- Grading: attendance/activity 10, test 30, homework 20, final exam 40 (total 100).
- Plagiarism/copying is punished under university rules → the student must
  understand and be able to explain every part of the code and logic.

## 3. Industry scenario

- **Not finally decided yet.** The meat industry was used as the working
  example in discussion (scale/load cell with e.g. 200 kg of meat → grinder →
  temperature monitoring + cooling → packaging conveyor → sealing).
- Other candidates discussed: soft drink / juice bottling line (rinse → fill →
  cap → label → inspect → crate), juice/milk with pasteurization (PID
  temperature control), bakery, batch mixing.
- Whichever is chosen should include both **discrete sequence logic**
  (conveyors, stations, counters) and at least one **analog/process value**
  (weight, level, temperature) to show the "control systems" side.

## 4. Chosen tool stack (final decision)

**OpenPLC v4 + Python process simulator + FUXA HMI**, all on Linux.

| Component              | Role                                             | Notes                                                                                                                                  |
| ---------------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| **OpenPLC Editor v4**  | Write, compile, upload and debug the PLC program | Installed as AppImage on Arch. Supports LD, ST, FBD, IL. SFC support unclear – verify. Has debugger with forcing and trends.           |
| **OpenPLC Runtime v4** | The soft PLC that executes the program           | Run via Docker (`ghcr.io/autonomy-logic/openplc-runtime:latest`). Port 8443 = editor connection, port 502 = Modbus TCP. No time limit. |
| **Python simulator**   | Simulates the physical plant ("fake world")      | Uses `pymodbus` as Modbus TCP client. Runs headless (no window).                                                                       |
| **FUXA**               | Web-based HMI / SCADA                            | Runs in Docker or Node.js, opened in browser. Shows animated plant and provides operator buttons.                                      |

**Important:** use OpenPLC **v4**, not v3. v3 is end-of-life (archived April 2026)
and has an unpatched critical vulnerability. Many online tutorials still show v3.

### Alternatives considered and rejected (for reference)

- **Delta ISPSoft / DOPSoft / Factory I/O:** Windows-only.
- **CODESYS 3:** most complete tool (all languages, simulation, forcing, built-in
  visualization) but Windows-only (runs under Wine unofficially, or in a VM).
- **CODESYS 4 (v1.0, Sept 2026):** native Linux, but too limited for now –
  Ladder editor only v0.1, no SFC/FBD, no visualization, no forcing/breakpoints;
  Linux soft PLC runs in 2-hour demo mode.

## 5. System architecture

Three separate programs that form **one connected system** at runtime.

```
   Python "factory"                FUXA (HMI)
   (Modbus client)               (Modbus client)
         │                              │
         │ reads PLC outputs            │ reads values to display
         │ writes sensor values         │ writes operator commands
         ▼                              ▼
   ┌───────────────────────────────────────────┐
   │           OpenPLC Runtime v4              │
   │      (Modbus TCP server, port 502)        │
   └───────────────────────────────────────────┘
```

### Key design rules

- **OpenPLC is the single source of truth.** It is the only component that
  makes control decisions.
- **Python and FUXA never talk to each other directly.** Everything goes through
  OpenPLC's memory – exactly like a real plant, where the HMI reads sensor
  values from the PLC, never from the machine.
- This means the Python simulator could later be replaced by real hardware
  without changing FUXA.

### Role of each part

- **OpenPLC = the brain.** Reads inputs, runs logic, sets outputs. Knows nothing
  about the "factory" except its I/O values.
- **Python = the factory.** It does two things:
  1. Simulates **sensor readings** (e.g. scale = 200 kg, temperature, bottle detected)
  2. Simulates the **physical reaction** to PLC outputs (e.g. grinder ON → weight
     decreases each second and temperature rises; cooling ON → temperature falls)
- **FUXA = the operator window.** Animated tanks/conveyors/lamps plus
  Start / Stop / E-Stop buttons, counters and alarms.

### Closed loop (meat example)

1. Python: scale = 200 kg → writes to PLC
2. PLC: enough meat → grinder = ON
3. Python: sees grinder ON → weight 200 → 195 → 190 … and temperature rises
4. PLC: temperature > 4 °C → cooling = ON
5. Python: cooling ON → temperature falls
6. Scale = 0 kg → PLC stops grinder, starts packaging conveyor → Python simulates
   packages reaching the sealing sensor, etc.

### Chosen visualization approach: "Option A"

- Python runs **headless** (no GUI). It only computes the physics.
- **FUXA is the only screen**: it shows the animated plant AND the controls.
- Values needed only for animation (e.g. exact bottle/package position on a
  conveyor) are also routed through **spare OpenPLC memory registers**: Python
  writes them, the PLC logic ignores them, FUXA reads them.
- Possible later bonus: a pygame window in Python showing the plant floor
  ("Option B"), only if time allows.

## 6. Modbus addressing caveat (plan for this from the start)

External Modbus clients **cannot write to the PLC's physical input addresses
(%IX…)** – those are read-only from outside. Therefore:

- Sensor values written by Python must go into **memory variables /
  writable registers** (e.g. %MW… holding registers, or writable coils).
- The PLC program reads these memory variables as its "sensors".
- Design one shared **I/O + Modbus address map** used by all three components
  (PLC variables ↔ Modbus addresses ↔ Python simulator ↔ FUXA tags).
- Verify the exact Modbus mapping in OpenPLC v4 docs/settings before coding.

## 7. Planned repository structure

One Git repository, everything started with one `docker compose up`:

```
plc-<industry>-project/
├── docker-compose.yml     ← starts OpenPLC runtime, simulator, FUXA
├── plc/                   ← OpenPLC Editor project
├── simulator/             ← Python plant simulator (pymodbus), Dockerfile
├── hmi/                   ← FUXA project export
└── docs/                  ← process description, I/O list, address map, diagrams
```

## 8. Current setup status (Arch Linux)

- OpenPLC Editor v4 downloaded as **AppImage**.
  - `chmod +x`, may need `sudo pacman -S fuse2`, possibly `--no-sandbox`.
  - Optional desktop entry in `~/.local/share/applications/`.
- OpenPLC Runtime planned via Docker:
  ```bash
  docker run -d --name openplc-runtime \
    -p 8443:8443 -p 502:502 \
    --cap-add=SYS_NICE --cap-add=SYS_RESOURCE \
    -v openplc-runtime-data:/var/run/runtime \
    ghcr.io/autonomy-logic/openplc-runtime:latest
  ```
  - Confirm the Modbus TCP server is enabled in the runtime/editor settings.
- FUXA and the Python simulator: not set up yet.

## 9. Next steps

1. Decide the industry scenario (meat processing is the current working example).
2. ~~Ask the professor whether OpenPLC is acceptable~~ → **Done: the professor approved OpenPLC.** No Delta/ISPSoft port needed.
3. Write the process description and stage breakdown.
4. Create the **I/O list and shared Modbus address map**.
5. Draw the sequence / state diagram per station.
6. Write the PLC program in OpenPLC (one state machine per station, linked with
   start/done bits; plus Auto/Manual/Stop modes and alarms).
7. Build the Python simulator (pymodbus client, simulation loop, physics per station).
8. Build the FUXA HMI screen.
9. Wire everything in `docker-compose.yml` and test the full loop.
10. Documentation + presentation.
