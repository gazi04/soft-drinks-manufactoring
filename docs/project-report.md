# Soft Drink Production Line – PLC Project Report

2026-10-27 · Gazi

## 1. Project overview

This project automates a complete soft drink production line, from plastic preforms to finished multipacks. Each machine on the line runs on its own PLC. A Python simulator plays the role of the physical plant, and a FUXA web HMI acts as the line's SCADA system.

It is the semester project for the master's course *PLC Programming* (Prof. Dr. Sc. Pajtim Vela, Public University "Kadri Zeka" Gjilan). The professor asked for a real industry scenario, designed from raw material to finished product, with the PLC program that controls it. The professor approved OpenPLC in place of the Delta PLCs used in the lab.

Goals of the project:

- Model a realistic line of five machines, each with its own sequence, interlocks, alarms and operating modes.
- Write each machine's control logic in **Ladder Diagram**, with Structured Text only where ladder is clearly unsuitable.
- Connect the machines into one coordinated line, as on a real factory floor where machines from different suppliers each have their own controller.
- Close the control loop with a simulated plant, so the programs can be tested without hardware.
- Give the operator one SCADA screen for the whole line, plus a detail screen per machine, with animated conveyors.
- Cover every topic of the course syllabus somewhere in the project (section 10).

The whole system runs on Linux with Docker: **OpenPLC Runtime v4** (one container per machine), the **Python simulator** (pymodbus) and **FUXA**. All parts talk over **Modbus TCP**.

## 2. Production process

The line makes carbonated soft drink in PET bottles and packs them into 6-bottle multipacks (the "drink set"). Two flows meet at the filler: the **bottle flow** (solid, discrete) and the **product flow** (liquid, continuous).

**Bottle flow**

1. **Blow moulding:** PET preforms are heated in an infrared oven, then stretched and blown into bottles inside a mould.
2. **Transport:** empty bottles travel on conveyor C1 to the filler.
3. **Rinsing, filling, capping:** each bottle is rinsed, filled with carbonated drink to the correct level, and capped.
4. **Transport:** full bottles travel on conveyor C2 to the labeler.
5. **Labeling:** a label is cut from a roll, glued with hot melt and wrapped around the spinning bottle.
6. **Transport:** labelled bottles travel on conveyor C3 to the packer.
7. **Packing:** bottles are grouped in sixes, wrapped in film and shrunk in a heat tunnel. Each finished pack is counted.

**Product flow**

1. **Water treatment:** treated water is collected in a water tank.
2. **Syrup dosing:** water and sugar syrup are dosed into the mixing tank in a fixed ratio from the recipe.
3. **Mixing:** an agitator mixes the batch for a set time.
4. **Cooling:** the drink is cooled to about 4 °C, because cold liquid absorbs CO2 much better.
5. **Carbonation:** CO2 is injected until the carbonator reaches its pressure setpoint.
6. **Supply to filler:** the carbonated drink is held in a pressurised buffer tank that feeds the filler.

This gives the project both kinds of control the course asks for: **discrete sequential logic** (bottles, conveyors, counters) and **analog process values** (temperature, level, pressure).

## 3. System architecture

The system has three layers: a virtual plant at the top, five independent machine PLCs in the middle, and one SCADA screen at the bottom. Every arrow is a Modbus TCP connection, and the PLCs sit in the middle of every one.

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                Python simulator (the plant)                                │
│            Physics per machine · conveyors C1–C3 · copies handshakes and E-stop            │
└────────┬──────────────────┬──────────────────┬──────────────────┬──────────────────┬───────┘
         │ Modbus TCP       │                  │                  │                  │
┌────────┴───────┐ ┌────────┴───────┐ ┌────────┴───────┐ ┌────────┴───────┐ ┌────────┴───────┐
│  Blow moulder  │ │  Syrup / CO2   │ │Filler / capper │ │    Labeler     │ │     Packer     │
│ plc-blowmolder │ │ plc-syrup-carb │ │   plc-filler   │ │  plc-labeler   │ │   plc-packer   │
│     PLC 1      │ │     PLC 2      │ │     PLC 3      │ │     PLC 4      │ │     PLC 5      │
└────────┬───────┘ └────────┬───────┘ └────────┬───────┘ └────────┬───────┘ └────────┬───────┘
         │ Modbus TCP       │                  │                  │                  │
┌────────┴──────────────────┴──────────────────┴──────────────────┴──────────────────┴───────┐
│                                 FUXA HMI / SCADA (browser)                                 │
│           Line overview · one screen per machine · alarms · trends · Start/Stop            │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

The simulator and FUXA never talk to each other directly. Everything goes through the PLCs, so the simulator could later be replaced by real machines without changing the HMI.

Key design rules:

- **The PLCs make every control decision.** The simulator only models physics and wiring; the HMI only displays values and passes on operator commands.
- **One PLC project per machine.** Each machine can be developed, tested and demonstrated on its own, as on a real line.
- **Same interface on every PLC.** All five use the same register layout (section 6.2), so the simulator and HMI code is written once and reused.
- **One command starts everything.** `docker compose up` launches the five runtimes, the simulator and FUXA.

## 4. The machines: one PLC project per machine

The line has five machines, and each one is a separate OpenPLC project running in its own runtime. This mirrors real plants, where every machine arrives from its supplier with its own PLC, and the line is integrated afterwards.

Every machine project uses the same internal structure, so they all look alike to the simulator and the HMI:

- **Mode and safety section** (combinational): E-stop, Auto/Manual/Stop, master enable for the whole program (the IEC replacement for MCR).
- **Interlocks and permissives** (combinational): AND/OR/NOT conditions that allow each output.
- **Sequencer** (sequential): a step variable plus compare instructions, one step per machine state.
- **Function blocks** (subroutines): `FB_Motor` (start/stop, seal-in, run-hours) and `FB_Valve`, reused in every project.
- **Alarms:** timeouts and limit checks, latched into an alarm word until reset.
- **Standard machine interface:** the same register layout in every PLC (section 6).

Each machine owns and drives its **infeed conveyor** motor. The conveyor's **full sensor**, however, is wired to the **upstream** machine, because that is the machine that has to stop when the conveyor is full (section 5.3).

| # | PLC project | Machine | Infeed | Main analog value |
| --- | --- | --- | --- | --- |
| 1 | `plc-blowmolder` | Stretch blow moulder | Preform hopper | Oven temperature (°C) |
| 2 | `plc-syrup-carb` | Syrup room, mixer, carbonator | Mains water, syrup tank | Tank levels (L), product temperature (0.1 °C), CO2 pressure (0.01 bar) |
| 3 | `plc-filler` | Rinser, filler, capper (monobloc) | C1 | Fill level (ml) |
| 4 | `plc-labeler` | Labeler | C2 | Hot-melt glue temperature (0.1 °C) |
| 5 | `plc-packer` | Multipack shrink-wrapper | C3 | Shrink tunnel temperature (°C) |

### 4.1 Blow moulder (`plc-blowmolder`)

Turns PET preforms into empty bottles.

- **Inputs:** preform present, oven temperature, mould closed/open, stretch rod up/down, blow pressure reached, bottle ejected, C1 full sensor (S1, on the filler's infeed conveyor).
- **Outputs:** preform feeder, oven heaters, mould close, stretch rod, blow valve, exhaust valve, eject.
- **Sequence:** Idle → Load preform → Heat (until oven at setpoint) → Close mould → Stretch → Blow (TON hold) → Exhaust → Open mould → Eject → Idle.
- **Interlocks:** no blow unless mould closed; no new preform is loaded while C1 is full (S1, filtered and latched as in section 5.3).
- **Blocked behaviour:** the bottle already in the mould is finished and ejected, then the preform feed stops. The **oven heaters stay ON** while blocked, because reheating the oven takes minutes; the machine can then restart immediately when C1 has room again.
- **Alarms:** oven over-temperature, mould close timeout, no preforms.

### 4.2 Syrup room and carbonator (`plc-syrup-carb`)

Makes the carbonated drink in batches and keeps the filler supplied. This is the process-control machine of the line: a **batch** machine (one step at a time, analog values), next to the filler's indexing machine. Details: `docs/syrup/` (I/O list, state diagram, ladder roadmap, progress).

- **Vessels:** one mixing tank that is also the carbonator (mixing, cooling and carbonating in one vessel), and a **buffer tank** between it and the filler. The buffer decouples the batch rhythm from the filler, which draws product all the time; without it the filler would be Starved during every batch.
- **Inputs:** syrup tank level, mixing tank level, product temperature, carbonator pressure, buffer tank level (INT with fixed-point units: L, 0.1 °C, 0.01 bar), plus a high-high level switch on the mixing tank as a hardware overflow backup. Water comes from the mains and is always available.
- **Outputs:** water pump, syrup pump, agitator, cooling valve, CO2 valve, transfer pump.
- **Sequence:** Idle → Wait for demand → Dose water → Dose syrup (ratio from recipe ×10, e.g. 55 = 5.5:1) → Mix (TON) → Cool to 4 °C → Carbonate (pressure held without the CO2 valve for a set time = saturated) → (Wait for room in the buffer) → Transfer to buffer tank → Wait for demand (new batch when the buffer is low).
- **Math and compare:** syrup amount = water amount × 10 / ratio (multiply before dividing, because INT division truncates); hysteresis (two thresholds + seal-in) on temperature, pressure and the buffer level.
- **Interlocks:** dosing pumps off at the high limit or the high-high switch; agitator only above minimum level; no CO2 unless the product is cold and the pressure is below the limit; transfer pump not dry and not into a full buffer.
- **Alarms:** tank overflow, over-pressure, cooling timeout and dosing timeout (pump failure) → Fault; low syrup → warning only (state Starved, clears itself after the refill).
- **Stop and resume:** Stop takes effect at the end of the current step. On restart the tank levels show where the batch is; one memory bit (`Batch_Dosed`) prevents water from being added to a finished batch.
- **Output to the line:** `Product_Available` for the filler = buffer level above minimum, with hysteresis so the handshake does not chatter. It stays TRUE during a Fault or E-stop of the syrup room, because the product already in the buffer is good.

### 4.3 Rinser, filler and capper (`plc-filler`)

Fills and caps bottles. It is the first machine to build end to end, because it combines a sequence with an analog value.

It is an **indexing machine**: three stations (rinse, fill, cap) on one internal belt, one bottle per station. All stations work at the same time; when all are done, the belt moves every bottle one station forward and the next bottle enters.

- **Inputs:** bottle at entry door (S2), bottle at rinse (S3), fill (S4) and cap (S5) stations, discharge counter (S6), C2 full sensor (S7, on the labeler's infeed conveyor), index sensor (S8, belt moved one station), fill level (analog, ml), *Product available* from the syrup room.
- **Outputs:** C1 infeed motor (runs continuously), entry door, internal index belt, rinse valve, fill valve, cap head.
- **Sequence:** Idle → Work (rinse, fill and cap in parallel, each only if its station has a bottle) → Index (belt one station forward, door admits one bottle with a TP pulse, only if the entry pocket is empty, discharged bottle counted) → Work … Detailed state diagram: `docs/filler/state-diagram.md`.
- **Interlocks:** door only if the entry pocket is empty (the pocket has no sensor, so the PLC tracks it with a set/reset bit: set by the door, reset when the belt has moved; found by the simulator's jam scenario); filling only if *Product available*; index only when all station jobs are done; index with a bottle at the cap station only if C2 is not full (S7, filtered and latched as in section 5.3).
- **Alarms:** fill timeout (valve open but level not rising), index jam, outfeed blocked too long.

### 4.4 Labeler (`plc-labeler`)

Labels bottles. A **roll-fed hot-melt labeler** with one pocket on a turntable, one bottle at a time: the operator loads a label roll and glue blocks; the machine cuts one label off the web at each printed registration mark, puts melted glue on it, and **spins the bottle** so the label wraps around it. Every labelled bottle goes to C3: there is no inspection and no reject (decision 2026-11-15; a fill level check with a reject station is a future enhancement of the filler). Details: `docs/labeler/` (I/O list, state diagram, ladder roadmap, progress).

- **Inputs:** bottle at the gate, bottle in the pocket, centering bell down, label present (used on a restart), registration mark, spin pulse (one per turn), label roll empty / low, glue low, C3 full sensor (on the packer's infeed conveyor), glue temperature (INT, 0.1 °C).
- **Outputs:** C2 motor (runs continuously, bottles queue at the gate), infeed gate (TP pulse), centering bell, turntable, label feed, knife (TP pulse), glue roller, glue heater, discharge to C3.
- **Sequence:** Idle → Warm up (glue at temperature) → Wait for bottle → Clamp → Wrap (spin + feed + glue, until the registration mark) → Press (knife cuts, bottle turns `PressTurns` more times, counted with a CTU) → Unclamp → Discharge to C3 (or wait while C3 is full) → Wait for bottle.
- **Glue heater:** hysteresis control on the glue temperature, separate from the sequence; it stays ON in Stop and Manual (melting takes minutes), off only on E-stop, FAULT or over-temperature. Labelling only inside a temperature window (`Glue_Ready`).
- **Interlocks:** never two bottles in the pocket; spin, feed and knife only with the bell down; feed and glue only with hot glue; discharge only with the bell up and never into a full C3.
- **Counter:** labelled bottles (CTU); the operator zeroes it with `Cmd = 4`.
- **Stop and resume:** a started label is always finished; on restart the pocket sensors show where the bottle is (a labelled bottle is never labelled twice). Limitation without a reject: a label broken off by an E-stop leaves on C3, and the operator removes that bottle.
- **Alarms:** label timeout (web torn, turntable stuck), pocket jam, glue over-temperature, glue heat-up timeout → Fault; label roll empty → warning (state Starved); outfeed blocked too long → warning. Label roll low and glue low are early warnings in the HMI.

### 4.5 Multipack packer (`plc-packer`)

Groups bottles into 6-packs (the "drink set") and shrink-wraps them.

- **Inputs:** bottle counted, group complete, film present, tunnel temperature, pack at outfeed.
- **Outputs:** C3 motor, group stop gate, pusher, film wrapper, tunnel heaters, tunnel conveyor.
- **Sequence:** Idle → Count bottles (CTU to 6) → Push group → Wrap film → Shrink in tunnel (TON at temperature) → Pack out → Idle.
- **Counters:** bottles in current group (CTU, reset at 6), finished packs, shift total.
- **Alarms:** film empty, tunnel temperature low or high, pack jam at outfeed.

## 5. Line integration: how the machines work together

The five PLCs never read each other's memory. They coordinate through **conveyor full sensors** and one **handshake signal**, exactly like machines from different suppliers on a real line.

### 5.1 Conveyor sensors and handshake signal

- **Bottle flow (conveyors C1–C3):** no handshake is needed. Each conveyor's full sensor is wired directly to the upstream machine, which stops by itself when the conveyor is full (section 5.3).
- **Liquid flow (syrup room → filler):** the filler cannot see the syrup room's tank, so one handshake is used: `Product_Available`, sent by the syrup room = buffer tank level above minimum (with hysteresis). The buffer has no pressure sensor; only finished, carbonated product is ever transferred into it.

The simulator copies the syrup room PLC's `Product_Available` (%MX6.0) to the filler PLC's input (%MX1.0) every tick, like a wire. Only when the simulator runs with `--filler-only` (no syrup room PLC) does a stand-in hold it TRUE.

Each machine reacts to these signals with two extra states:

- **Starved:** waiting because nothing comes from upstream.
- **Blocked:** waiting because downstream cannot take more.

A machine that is starved or blocked is not in fault. It resumes on its own as soon as the signal returns.

### 5.2 How the signals travel: simulated hardwiring

In a real plant, handshake signals are often **hardwired**: a 24 V output of one PLC is wired to an input of the next PLC. This project reproduces that wiring in software:

1. The upstream PLC sets its handshake output in its *interface out* register.
2. The Python simulator reads it, like a wire carrying the voltage.
3. The simulator writes it into the *interface in* register of the downstream PLC.

The simulator only copies these bits; it makes no decision. All control decisions stay inside the PLCs. If the line were built for real, the simulator's wiring table would become real cables, and no PLC program would change.

**Fail-safe signals.** Signals between machines are **active-high**: `Product_Available` and the E-stop chain (`E_Stop_OK`) are TRUE only when everything is OK. A broken wire, a stopped PLC or a lost connection therefore reads as FALSE, and the machine stops safely instead of, for example, opening the fill valve with no product.

**Heartbeat.** If the simulator crashes, the registers keep their last value, so a stuck TRUE could still look like "product available". To detect this, the simulator toggles a **heartbeat bit** in every PLC's *interface in* register once per second. Each PLC watches the bit with a TON: if it has not changed for 3 s, the PLC raises a *communication lost* alarm and treats all handshake inputs as FALSE.

A PLC-to-PLC network link (one OpenPLC polling another over Modbus) would be an alternative. It is not the main plan, because Modbus master support in OpenPLC v4 still needs to be verified.

### 5.3 Buffers between machines

Conveyors C1, C2 and C3 act as **accumulation buffers**. A short stop on one machine fills its infeed buffer first, so the upstream machine keeps running for a while instead of stopping at once.

Each conveyor has a **full sensor** near its entry. The downstream machine drives the conveyor motor, but the full sensor is wired directly to the **upstream** machine, the one that must stop when the conveyor is full:

```
Upstream machine ──► [B][B][B][B][B][B][B][B] ──► Downstream machine
                      ▲                            (drives the conveyor motor)
                   Full sensor ──── wired to the upstream machine
```

| Conveyor | Motor driven by | Full sensor wired to |
| --- | --- | --- |
| C1 | Filler | Blow moulder (S1) |
| C2 | Labeler | Filler (S7) |
| C3 | Packer | Labeler |

**1. Filter and latch the full sensor.** Moving bottles pass the sensor with small gaps, so its raw signal flickers ON and OFF. The conveyor only counts as full when the sensor is covered **continuously** for 2 s, and counts as free again only when the sensor has been free for 3 s. This time-based hysteresis stops the upstream machine from stopping and restarting at every single bottle. In ladder it is two timers plus a seal-in circuit:

```
Full_OnTimer  := TON(IN := S7,     PT := 2 s)
Full_OffTimer := TON(IN := NOT S7, PT := 3 s)
Outfeed_Full  := (Full_OnTimer.Q OR Outfeed_Full) AND NOT Full_OffTimer.Q
```

**2. A full conveyor means Blocked, not an alarm.** A full outfeed conveyor is normal: the next machine is stopped or slower. The upstream machine finishes the product it is working on, waits, and shows the state **Blocked**. An alarm (*outfeed blocked too long*) is raised only if it stays blocked for more than 2 minutes (TON). Otherwise the operator would get an alarm every time the next machine pauses, and would soon stop reading alarms.

**3. Infeed conveyor during a machine stop.** When the operator stops a machine (Stop mode), its infeed conveyor **keeps running**, so bottles pack tightly and the buffer uses its full capacity. This is how real accumulation conveyors work. The infeed conveyor stops only on E-stop, on a fault, or when the conveyor itself has a jam alarm.

### 5.4 Line start, stop and E-stop

- **Start:** the operator starts the line from the HMI, which sends Start to every PLC. No start order is needed: a machine whose outfeed conveyor is full simply waits (Blocked), and a machine with nothing arriving waits (Starved). The line settles by itself without a separate line controller.
- **Normal stop:** Stop is sent to every PLC. Each machine finishes its current cycle and stops, so no bottle is left half-filled.
- **Emergency stop:** the E-stop is a **safety chain** that reaches every machine. It is wired (simulated) to every PLC, and each PLC drops all outputs at once through its master enable. After the E-stop is released, each machine must be reset before it restarts.

### 5.5 Example: the operator stops the filler

This example shows how one machine affects the others through the conveyor sensors alone, with no PLC reading another PLC's memory.

1. The operator presses Stop on the filler. The filler finishes the bottle it is working on and stops taking new ones. C1 keeps running (section 5.3).
2. Bottles from the blow moulder keep arriving and pile up on C1, starting at the filler's entry door.
3. The queue grows back to the C1 full sensor (S1). The simulator writes S1 into the blow moulder's sensor word, like any other sensor.
4. After 2 s of continuous cover, the blow moulder PLC latches *C1 full*.
5. The blow moulder finishes the bottle in the mould, stops loading preforms and goes to **Blocked**, with the oven still hot.
6. Downstream, C2 runs empty: the labeler becomes **Starved**, and shortly after, the packer too.
7. The operator restarts the filler. C1 drains, S1 is free for 3 s, the latch releases, and the blow moulder continues on its own. The labeler and packer resume as soon as bottles reach them.

What the operator sees on the line overview during the stop:

| Machine | State | Colour | Reason |
| --- | --- | --- | --- |
| Blow moulder | Blocked | Orange | C1 full |
| Syrup / CO2 | Running, then idle | Green | Buffer tank fills up, no new batch needed |
| Filler | Stopped | Grey | Stopped by operator |
| Labeler | Starved | Yellow | C2 empty |
| Packer | Starved | Yellow | C3 empty |

The cause is visible at a glance: the only grey block, with orange to its left and yellow to its right. This scenario is also one of the integration tests in section 11.

## 6. Network and address map

All components talk over **Modbus TCP**. Each OpenPLC runtime is a Modbus server (slave). The simulator and FUXA are Modbus clients (masters) that connect to all five PLCs. This is the "PLC networks in manufacturing" part of the course.

### 6.1 Network layout

All containers run on one Docker network and are started with one `docker compose up`. Inside the network every PLC listens on port 502 under its own service name. Different host ports are mapped only for debugging from the laptop.

**Editor connection:** OpenPLC Editor v4 always connects to port **8443** (hard-coded, verified 2026-11-09 in Editor 4.3.3); only the device IP can be set in the project. So every PLC gets its own **loopback IP** on port 8443, like real PLCs on one network with the same port and different addresses. Loopback IP and host Modbus port follow the **order in which the machines are built** (decision 2026-11-16): the filler came first and keeps `127.0.0.1` (= `localhost`) and port 502, every later PLC gets the next `127.0.0.n` and port `502n`. The filler service may still be renamed from `openplc` to `plc-filler`; its addresses stay.

| Service | Role | Modbus inside network | Host Modbus port | Editor address (port 8443) |
| --- | --- | --- | --- | --- |
| `plc-blowmolder` | PLC 1 | `plc-blowmolder:502` | 5025 | 127.0.0.5 |
| `plc-syrup-carb` | PLC 2 | `plc-syrup-carb:502` | 5022 | 127.0.0.2 |
| `plc-filler` | PLC 3 | `plc-filler:502` | 502 | 127.0.0.1 (service today: `openplc`) |
| `plc-labeler` | PLC 4 | `plc-labeler:502` | 5023 | 127.0.0.3 |
| `plc-packer` | PLC 5 | `plc-packer:502` | 5024 | 127.0.0.4 |
| `simulator` | Plant model | client only | – | – |
| `fuxa` | HMI / SCADA | client only | – | 1881 (web) |

### 6.2 Standard machine interface

Every PLC uses the **same register layout**. The simulator and the HMI can then treat all machines the same way, and one HMI template fits every machine. Only the meaning of individual sensor bits differs per machine and is listed in that machine's I/O list.

| Address | Content | Written by | Read by |
| --- | --- | --- | --- |
| %MW0 | Command: 1 = Start, 2 = Stop, 3 = Reset; 4 = Reset counters (labeler) | HMI | PLC |
| %MW1 | Mode request: 0 = Stop, 1 = Auto, 2 = Manual | HMI | PLC |
| %MX0.0–%MX0.7 | Digital sensors (one BOOL each) | Simulator | PLC |
| %MX1.0–%MX1.2 | Interface in: `Product_Available` (filler only), `E_Stop_OK`, heartbeat bit | Simulator (wiring) | PLC |
| %MX2.0–%MX2.7 | Manual hold-to-run buttons (one BOOL per actuator) | HMI | PLC |
| %MX3.0–%MX3.7 | Conditioned signals (filtered sensors, hysteresis bits, jog watchdog) | PLC | Tests, HMI |
| %MX4.0–%MX4.7 | More digital sensors, for a machine with more than 8 (labeler: 12) | Simulator | PLC |
| %MX5.0–%MX5.7 | Alarms, one BOOL each; **`Alm_CommLost` is `%MX5.3` in every PLC**, the other bits are per machine | PLC | HMI |
| %MX6.0 | Interface out: `Product_Available` (syrup room only) | PLC | Simulator (wiring) |
| %MW12–%MW19 | Analog sensors (level, temperature, pressure), INT with fixed-point units | Simulator | PLC, HMI |
| %MW30–%MW39 | Setpoints and recipe values | HMI | PLC |
| %MW40–%MW49 | Computed values (e.g. syrup target) | PLC | Tests, HMI |
| %MW50 | Machine state: 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault | PLC | HMI |
| %MW51 | Current sequencer step | PLC | HMI |
| %MW53 | Main counter (filler: good bottles, syrup room: batches, labeler: good bottles) | PLC | HMI |
| %MW70–%MW79 | Animation data (conveyor slots, filler pocket states), ignored by the PLC; filler layout in `docs/filler/io-list.md` | Simulator | HMI |
| %QX0.0–%QX1.7 | Actuator outputs (motors, valves, heaters) | PLC | Simulator, HMI |

Sensors are written to memory (%MX bits, %MW words), not to physical inputs (%IX), because external Modbus clients cannot write the PLC's input image. The PLC program reads this memory as its sensors. Booleans are single `%MX` bits instead of bits packed inside a word, so ladder contacts and coils can use them directly.

**Verified in OpenPLC v4** (2026-10-28, filler `smoke_test.py`): `%MWn` = holding register 1024 + n, `%MXa.b` = coil 8192 + 8a + b, `%QXa.b` = coil 8a + b. `%MX` is only reachable over Modbus with the project setting `mx_bits` = 64 (*Device → Servers → RuntimeServer*). Each machine's exact addresses are in its I/O list (`docs/filler/io-list.md`, `docs/syrup/io-list.md`, `docs/labeler/io-list.md`).

## 7. Python simulator: the virtual plant

The simulator replaces the physical factory. It is **one Python program with one class per machine**, so all machines share one clock and bottles can pass physically from one machine to the next. It makes no control decisions: it only answers "the valve is open, so the liquid flows".

It has an optional **control panel** in the terminal (section 7.5) for the engineer who tests the line. FUXA remains the only operator screen. With `--headless` the simulator runs without the panel, for example inside Docker.

**Status (2026-11-03):** built for the filler with stand-ins for its neighbours: physics, Modbus link, tick loop, command queue with fault scenarios, and the Textual control panel (`uv run simulator`, `--headless` without panel); 20 bottles run through the real filler PLC with 0 audit warnings, and 9 fault scenarios (gaps, no product, labeler stopped, index jam, slow fill, E-stop, heartbeat lost, Stop during WORK, Manual) pass against it. It writes the HMI animation data (%MW70–75) and shows the PLC's mode in the panel. The simulator is complete for the filler (`docs/simulator/roadmap.md`, stages 0–9); the structure below is the full-line target, the built version is marked.

**Status (2026-11-13), syrup room:** built (roadmap section "Syrup room simulator", stages 0–8). One simulator process drives **both real PLCs** (filler on port 502, syrup room on 5022): tank volumes, temperature and CO2 pressure of the mixing tank, the `Product_Available` wire to the filler and a buffer tank emptied by the filler's fill valve. The buffer loses 30 times what the filler fills (a 500 ml bottle takes 15 L): a time compression, because a real 590 L batch would last about 1180 bottles, i.e. hours, and the syrup room would never start a batch during a demo; the PLCs never see the factor, and the *Syrup room* screen states it under the buffer tank (`config.BUFFER_DRAW_SCALE`, design note in `docs/simulator/design-notes.md`). One real batch on the syrup PLC takes about 151 s (dose water 25 s, syrup 18 s, mix 20 s, cool 29 s, carbonate 32 s, transfer 23 s); 20 bottles were filled from the real `Product_Available` without the signal ever being lost, and 6 syrup fault scenarios (low syrup, chiller failed, CO2 valve stuck, pump failed, level sensor stuck, buffer full) plus `buffer_starved` (both PLCs) pass against the real PLCs. `uv run simulator --filler-only` keeps the filler-alone behaviour.

### 7.1 What it does

- **Simulates sensors:** levels, temperatures, pressures, bottle-present signals, counts.
- **Simulates the physical reaction to PLC outputs:** pump ON → level rises; cooling ON → temperature falls; CO2 valve ON → pressure rises; conveyor motor ON → bottles move.
- **Simulates the hardwiring between PLCs:** copies handshake bits and the E-stop chain, and toggles the heartbeat bit once per second (section 5.2).
- **Feeds the HMI animation:** writes the conveyor slots (one bit per slot) and the state of the bottle in each filler pocket (empty, bottle, rinsed, filled, capped) into the spare animation registers every tick.
- **Injects faults for testing:** a jammed bottle, an empty label roll, a stuck valve, slow cooling. This shows that alarms and timeouts work.
- **Audits the PLC:** every bottle is an object that carries its own state (fill level, rinsed, capped). The simulator logs a warning when the physics shows a control error: product spilled with no bottle under the valve, two bottles in one pocket, a bottle pushed onto a full conveyor, a bottle leaving not rinsed, uncapped or underfilled. Setpoints are read from the PLC, so the underfill limit follows the HMI.

**One writer per address.** The simulator writes only its own sensor and interface addresses. It never writes commands, modes or setpoints, which belong to the HMI. The filler's Modbus test scripts also write the sensors, so they must never run at the same time as the simulator.

### 7.2 Structure

```
simulator/src/simulator/
├── __init__.py        ← entry point main(): panel or --headless          (built)
├── loop.py            ← tick loop                                       (built)
├── plant.py           ← composes conveyors, machines and stand-ins: one step() for the whole world   (built)
├── standins.py        ← neighbours that do not exist yet (bottle supply, labeler, heartbeat)          (built)
├── plcs.py            ← one Modbus client per PLC (5 connections), the only file that knows Modbus     (built: PlcLink base, FillerPlc, SyrupPlc)
├── address_map.py     ← standard machine interface (section 6.2)                                      (built: filler)
├── address_map_syrup.py ← the same layout for the syrup room PLC                                     (built)
├── plant_lock.py      ← one sensor writer per PLC (OS file lock per host:port)                         (built)
├── wiring.py          ← handshake and E-stop wiring table                                             (later: the plant holds the wires)
├── conveyor.py        ← Conveyor class: slots, belt motion, full sensor                               (built)
├── bottle.py          ← Bottle objects carrying their state                                           (built)
├── events.py          ← event log (bottle events, PLC state changes, audit warnings)                  (built)
├── config.py          ← all parameters with the PLC time each one is checked against                  (built)
├── commands.py        ← command queue: panel / scenario → plant (supply, faults, stand-ins)           (built)
├── scenarios.py       ← fault injection: fixed command sequences, time or condition steps             (built, replaces faults.py)
├── ui/                ← Textual control panel (section 7.5): view.py text, app.py screen              (built)
└── machines/
    ├── blow_molder.py                                                                                 (later)
    ├── syrup.py       ← syrup room + carbonator: tanks, temperature, CO2 pressure                      (built)
    ├── filler.py                                                                                      (built)
    ├── labeler.py                                                                                     (later)
    └── packer.py                                                                                      (later)
```

Every machine class has the same small interface: it receives that machine's PLC outputs, advances its physics by one time step, and returns the sensor values to write back plus a list of events. Machine and conveyor classes are **pure Python**, with no Modbus and no UI, so they can be unit-tested without a PLC.

### 7.3 The tick loop

Every cycle (about 100 ms) the simulator:

1. Applies the commands queued by the control panel (e.g. "jam the belt", "add 10 bottles").
2. Reads the outputs of all five PLCs (one bulk read per PLC).
3. Updates every machine's physics with the measured time step `dt` and moves bottles along C1, C2 and C3.
4. Copies the handshake bits between PLCs according to the wiring table and toggles the heartbeat.
5. Writes the sensor values, interface bits and animation data back to each PLC.
6. Passes the new events to the log (panel and log file).

Because each machine is its own class, each can be tested alone with unit tests, and a machine is added to the line by adding its class to the plant list.

The PLC (20 ms scan) and the simulator (100 ms tick) are not synchronised. Every sensor pulse therefore stays ON for at least one full tick, so the PLC cannot miss its edge. The physics also tolerates the PLC's reaction delay; for example, the filler's belt overruns a little after S8 before the motor stops.

The real tick is never exactly 100 ms, which the first version of the physics did not tolerate (false audit warnings, bottles skipping a station). As built:

- The loop sleeps until fixed 100 ms deadlines (measured 98–102 ms) instead of "work, then sleep".
- A late tick is split into physics sub-steps of at most 0.1 s, so events keep their order and the simulator's clock stays equal to the PLC's wall-clock timers (simply limiting `dt` was tried and rejected for that reason).
- Pulse sensors (S6, S8) are written ON if they were ON at any point of the tick; presence sensors are not stretched.
- A tick longer than 0.3 s is logged as "slow tick", so a warning caused by the simulator is never blamed on the PLC program.
- If the PLC cannot be reached, the physics continues with all outputs OFF, like a stopped PLC, and the link reconnects by itself. Every 10 ticks the simulator reads its sensor values back to detect a debugger force or a second writer.

With two PLCs (filler + syrup room, as built) every PLC has its own link and its own error handling inside the same tick, so one PLC being down never stops the other. A PLC that accepts the TCP connection but never answers is retried after 2, 4, 8, 16 s instead of every tick, otherwise its 1 s timeout would delay the other PLC's heartbeat. Step 4 (the wiring) as built: the syrup room's `Product_Available` goes to the filler, the E-stop chain is shared, and each PLC gets its own heartbeat bit.

### 7.4 Physics models

The models are simple first-order behaviour, enough to make the control logic meaningful:

- **Levels:** level changes by inflow minus outflow each tick.
- **Temperatures:** move toward the cooling or heating target with a time constant, with slow drift back to ambient when off.
- **Pressure:** rises while the CO2 valve is open; falls while the liquid takes CO2 up and when the tank is vented.

As built for the syrup room (`machines/syrup.py`; every parameter in `config.py` with the PLC value it must fit):

- **Tanks:** syrup tank, mixing tank and buffer tank as volumes in litres. Water 20 L/s, syrup 5 L/s, transfer 25 L/s. The PLC doses by absolute level and sees one level per tick, so a pump runs up to one tick too long; the small flows keep this overshoot at 2 L water and 0.5 L syrup, so the 5.5:1 ratio holds. Each dose (25 s, 18 s) stays far below the PLC's 60 s `DoseTimeout`. The high-high switch `MixHighHigh` (980 L) always sees the true level, also when a fault freezes the level sensor.
- **Temperature:** first-order lag, solved exactly for each step. The cooling jacket pulls the tank toward the chiller (0 °C, time constant 20 s), the room always pulls it slowly toward 20 °C (600 s), and water (15 °C) and syrup (20 °C) mix in by volume. COOL takes about 29 s against the PLC's 60 s `CoolTimeout`.
- **CO2 pressure:** the valve raises the headspace pressure by 0.5 bar/s. The liquid takes CO2 up (fast with the agitator, ten times slower without), and the headspace loses pressure for what the liquid gains. The pressure therefore falls quickly while the liquid is not saturated and holds when it is, which is exactly what the PLC's `Carb_Timer` measures: CARBONATE needs about three valve openings and 30 s. The transfer pump vents the headspace into the buffer (time constant 5 s); a relief valve at 8 bar limits a stuck valve, long after the PLC's 6 bar over-pressure alarm.
- **Buffer and filler:** the filler's fill valve empties the buffer, scaled 30 L per litre filled so that one batch of about 590 L lasts a few minutes of filling instead of hours. When the buffer is empty, no product flows into the bottles, whatever the PLC says.
- **Faults** change the world, never the PLC program: chiller failed, water or syrup pump failed (output ON, no flow), CO2 valve stuck open, level sensor frozen.
- **Conveyors:** a list of slots. While the motor is ON, bottles shift one slot per step, and they stop and accumulate at the end when the next machine does not take them. A full sensor sees one slot, so it flickers naturally while bottles pass and stays ON when they stand still.
- **Indexing belt (filler):** a continuous position that grows while the motor is ON. Crossing a station pitch moves every bottle one pocket forward. The index sensor S8 is a cam that is ON in a small window after each pitch (15 %), so it stays ON when the belt stops and absorbs the overrun. The station sensors see their bottle in a wider window (40 %) and are OFF while the belt moves between stations.
- **Door (filler):** a bottle needs a fixed pass time to go through the open door. The queue behind it stands still, so the next bottle starts from rest and needs a full slot step to reach the door. A correct door pulse admits exactly one bottle; a door held open too long lets the next one in as well, which the audit reports.

### 7.5 Control panel

The control panel is a terminal UI built with **Textual**. It is the engineer's test bench, not a second HMI:

| Simulator panel | FUXA HMI |
| --- | --- |
| Shows the physical truth: where each bottle is, its fill level, rinsed or capped | Shows what the PLC knows: state, step, alarms, counters |
| Changes the world: bottle supply, jams, labeler stopped, product available, E-stop | Sends operator commands: Start, Stop, Reset, mode, setpoints |

The panel never writes commands, modes or setpoints, so the rule "the simulator and FUXA never talk to each other" still holds.

It has three areas:

- **World view:** conveyor slots, the bottle in each station with its fill level, sensor lamps, and the PLC's step and state read for reference.
- **Controls:** supply rate (bottles/min), "add N bottles", pause supply, fill flow rate, and switches for every fault and stand-in signal (product available, E-stop, belt jam, labeler stopped, heartbeat off).
- **Event log:** one line per event, e.g. `Bottle 192: entered filler`, `Bottle 191: filled 500 ml`, `PLC: step 10 → 20`, audit warnings in red. It can be filtered by bottle, PLC or warnings, and it is also written to a file as evidence for the tests.

Panel buttons put commands on a queue; the tick loop applies them at the start of the next tick (section 7.3), so the world changes in one place only. Each machine gets its own tab with the same layout (keys `1` filler, `2` syrup room). The syrup room tab shows the tank levels as bars, temperature, CO2 pressure, the syrup PLC's output lamps and a red marker for every injected fault; its controls are switches for the faults and the syrup heartbeat, value rows (deliver or drain syrup, draw or fill the buffer, buffer draw scale) and the buttons "Vent tank" and "Drain mix tank". The scenario row under the tabs serves both machines, because a scenario such as `buffer_starved` involves both.

## 8. HMI / SCADA in FUXA

FUXA is the operator's single window onto the whole line. It connects to all five PLCs as five Modbus devices and combines them on one set of screens, which makes it a small SCADA system. FUXA never talks to the simulator; everything it shows comes from the PLCs, as in a real plant.

### 8.1 Screens

| Screen | Content |
| --- | --- |
| Header (on every screen) | Line state, mode, E-stop status, active alarm count, navigation buttons |
| Line overview | All five machines as blocks, conveyors C1–C3 animated, liquid link from the syrup room to the filler, line counters, line Start / Stop / Reset |
| Blow moulder | Oven temperature, mould and stretch rod status, current step, manual controls |
| Syrup room and carbonator | Tanks with live levels, pipes with flow animation, pumps and valves, temperature and CO2 pressure, recipe ratio |
| Filler | Bottle at station, fill level, cap status, current step, manual controls |
| Labeler | Pocket and current step, glue temperature, label roll and glue warnings, labelled bottles counter and its reset |
| Packer | Bottles in current group (0–6), tunnel temperature, finished packs |
| Alarms | Active alarms and history from all PLCs, with acknowledge |
| Trends | Tank levels, product temperature, CO2 pressure, oven and tunnel temperature over time |

Clicking a machine block on the overview opens that machine's detail screen. Because every PLC uses the same interface (section 6.2), all detail screens share the same layout for state, step, alarms and mode.

**As built (filler, 2026-11-04):** five screens, generated by `simulator/tests/fuxa_views.py` and checked by `simulator/tests/test_hmi_tags.py` (build steps and tests: `docs/hmi/roadmap.md`):

- **Line** (start page): C1, the filler and C2, with grey stand-in blocks for the machines not built yet.
- **Filler**: the detail screen.
- **Alarms**: the active list with Ack and Reset; the history is in FUXA's alarm panel.
- **Trends**: three charts.
  - Fill level against its setpoint, last 5 min.
  - Good bottles, last 30 min.
  - Machine state, last 30 min.
  - FUXA logs these four tags into its own history store: every change, and at least once a minute. The store is on the Docker volume `fuxa-daq`, so a re-created container keeps it.
  - The lines are drawn as steps, because the history stores a value only when it changes. A straight line between two stored points would show a slope that never happened.
  - A normal run shows one step-shaped ramp per bottle up to the setpoint.
  - A blocked outfeed shows as State 3 with a flat count.
- **Tags**: a test page with every tag.

**Syrup room (built, 2026-11-14):** FUXA has the syrup room PLC as a second Modbus device (`plc-syrup-carb:502`, 48 tags generated from `address_map_syrup.py`) and a test page *Syrup tags*. The header shows both machines on every screen: a chip per machine coloured by its state (click = its screen), the shared E-stop and both PLC links; its second row operates the machine of that screen (Start / Stop / Reset, mode, alarm chips). Line, Alarms and Trends exist once per machine, and a picker in the header switches between them. The *Syrup room* screen shows the three tanks as level bars, pipes that colour while their pump or valve runs, temperature in °C and CO2 pressure in bar (FUXA scales the PLC's fixed-point INTs and rounds when it writes), the ten recipe setpoints with their limits and the hold-to-run buttons. On the Line overview the syrup room is a block coloured by its state, with its step and buffer level (click = its screen), and the product pipe into the filler follows the syrup PLC's own *Product available* output, with a lamp for the filler's input at the other end of the wire. Its six alarm bits are FUXA alarms too (group *Syrup room*; FAULT bits red, comm lost orange, low syrup a yellow warning), and the Alarms screen is one list for the whole line with a *Machine* column, while Reset acts on the machine picked in the header. Its *Trends* screen shows the tank levels, the product temperature and the CO2 pressure (each with its setpoint) over the last 15 minutes and the machine state over 30 minutes; FUXA logs these values in °C and bar, because it scales a value before it stores it (`docs/hmi/roadmap.md`, section "Syrup room screens").

Screens of the whole HMI after the syrup room (generated by `fuxa_views.py`, checked by `test_hmi_tags.py`):

- **Line** (start page): blow moulder (stand-in) → C1 → filler → C2 → labeler (stand-in), with the syrup room as a real block above the filler and the product pipe between them.
- **Filler** and **Syrup room**: the two detail screens.
- **Alarms**: one list for the whole line (10 alarms, *Machine* column), Ack, Reset for the machine picked in the header; history in FUXA's alarm panel.
- **Trends**: per machine – filler 3 charts, syrup room 4 (levels, temperature, pressure, state).
- **Tags** / **Syrup tags**: test pages with every tag of each PLC.

Line, Alarms and Trends exist once per machine (the header picker switches); a line-wide Start / Stop / Reset (one click for both PLCs) is not built: FUXA 1.3.5 drops its PLC link when one click sends several writes (HMI issue I13). The pre-demo check `demo_check.py` checks both PLCs and FUXA's link to each; the demo script is `docs/hmi/demo-script.md`. Still open: the live run of the syrup room's Line block, alarms and trends with PLC data.

### 8.2 Machine state colours

Each machine block is coloured from its state register (%MW50):

| State | Colour |
| --- | --- |
| Stopped | Grey |
| Running | Green |
| Starved | Yellow |
| Blocked | Orange |
| Fault | Red |

The operator can then see at a glance which machine is holding up the line.

### 8.3 Conveyor animation

FUXA updates its values by polling, every 200–500 ms, which is too slow for smooth pixel motion. Conveyors are therefore animated with **slots**:

- Each conveyor is divided into a fixed number of slots (e.g. 8).
- The simulator writes one bit per slot (bottle present or not) into the animation registers.
- In FUXA, a bottle icon in each slot is shown or hidden by its bit.
- The belt rollers rotate (or a dashed belt line moves) only while the conveyor's motor output is ON.

The result looks clear at any refresh rate: bottles step along the belt, and they visibly pile up when a machine downstream is blocked. Tanks use FUXA's level fill, and pipes use flow animation bound to pump and valve outputs.

### 8.4 Operator actions

- Line Start, Stop and Reset are sent to all five PLCs.
- Mode (Auto / Manual / Stop) is selected per machine.
- Manual buttons (jog conveyor, open valve) are enabled only in Manual mode and work hold-to-run. The PLC still checks every manual command against its interlocks, and a jog watchdog ignores a button held longer than 10 s (for example after the HMI page was closed while pressing it) until all buttons are released.
- Setpoints and recipe values (fill level, mixing time, syrup ratio, temperature limits) are entered on the detail screens.

### 8.5 Design guideline

The screens follow **ISA-101 high-performance HMI** principles: grey background, muted equipment drawings, and strong colour only for abnormal states. Every value is shown with its unit. This keeps the operator's attention on problems instead of decoration.

## 9. Operating modes, safety and alarms

Every machine PLC implements the same three modes and the same alarm handling, so the operator works with all machines in the same way.

### 9.1 Operating modes

| Mode | Behaviour |
| --- | --- |
| Auto | The sequencer runs cycles on its own, following the handshake signals |
| Manual | The sequencer is paused; the operator moves single actuators from the HMI, still protected by interlocks |
| Stop | The current cycle finishes, then all outputs are off, except the infeed conveyor, which keeps accumulating bottles (section 5.3) |

Mode changes are only accepted in a safe state, for example Auto → Manual only at the end of a cycle.

### 9.2 Emergency stop

- The E-stop chain reaches all five PLCs (section 5.4).
- E-stop removes the **master enable**, which forces all outputs off in that program. This is the IEC equivalent of the MCR instruction.
- After release, the machine stays stopped until the operator presses Reset and then Start. It never restarts on its own.

### 9.3 Alarms

Alarms are BOOLs in `%MX5.0–%MX5.7` (section 6.2), shown in FUXA's alarm list. Fault alarms are latched until the cause is gone and the operator presses Reset; warnings (e.g. low syrup, outfeed blocked) do not stop the machine.

| Alarm type | How it is detected | Example |
| --- | --- | --- |
| Timeout / jam | TON runs while a step waits too long | Bottle does not reach the filler within 5 s |
| Overflow | Level above high limit | Mixing tank above 95 % |
| Over-temperature | Temperature above limit | Oven or tunnel too hot |
| Over-pressure | Pressure above limit | Carbonator above maximum |
| Material empty | Missing-material sensor or count | No preforms, no caps, no labels, no film |
| Communication lost | TON on the heartbeat bit (section 5.2) | Heartbeat unchanged for 3 s |
| Outfeed blocked too long | TON on the Blocked state (section 5.3) | Filler blocked by full C2 for more than 2 min |

### 9.4 Maintenance counters

Each `FB_Motor` instance counts **run hours** and **start cycles**. They are shown on the HMI and can trigger a "maintenance due" message, which supports the preventive-maintenance part of the course.

## 10. Course syllabus coverage

Every weekly topic of the course appears in at least one machine.

| Week | Topic | Where it appears |
| --- | --- | --- |
| III | I/O devices and motor controls | Conveyor motors, pumps, agitator via `FB_Motor` |
| IV | Relay logic diagrams | Start/stop seal-in circuits, interlocks between valves, conveyor-full latch |
| VI | Logic gate functions | Permissives: fill only if bottle present AND product available AND NOT fault |
| VII | Timers and counters | TON for mixing, rinsing, blowing, jam timeouts, sensor filtering and heartbeat watchdog; TP for the filler's door pulse and the labeler's gate and knife; CTU for batches, labelled bottles, the labeler's bottle turns, 6-packs and pack totals |
| VIII | Math, compare, jump, MCR | Syrup ratio calculation (`MUL` before `DIV`, INT truncation), batch and buffer sums (`ADD`), level, temperature and pressure compares with hysteresis; master enable as MCR equivalent |
| IX | Subroutines | `FB_Motor` and `FB_Valve`, reused in all five projects |
| X | Data handling | MOVE of recipe values and setpoints, copying sensor words |
| XI | Sequencer | Step variable plus compare in every machine (the IEC equivalent of SQO) |
| XIII | PLC networks | Modbus TCP between five PLCs, the simulator and FUXA; `Product_Available` handshake, E-stop chain, heartbeat; FUXA as a small SCADA with alarm history and trends (data logging) |

The project also shows the difference between **combinational control** (interlocks and permissives, labelled as such in every program) and **sequential control** (the step sequencers). Grounding of sensors and motors and preventive maintenance are covered in the documentation, supported by the run-hour counters.

## 11. Development plan and risks

The line is built **one machine at a time, end to end**: paper design, PLC project, simulator class, HMI screen. After each machine, the project is a working demo, even if later machines are not finished.

**Committed scope (decided 2026-11-09, `docs/scope-options.md` option B): filler + syrup room and carbonator.** These two machines cover every syllabus topic of section 10, including math/analog control (week VIII) and a real handshake between two PLCs (week XIII). The labeler, packer and blow moulder (steps 4–6) are **stretch goals**; until they exist, the simulator replaces them with stand-ins (held signals, an empty-bottle supply, a free outfeed), so neither PLC program changes when one is added later.

1. **Foundation:** process description, I/O lists, standard machine interface, Docker Compose with five runtimes.
2. **Filler** (done): first complete machine, since it has a sequence and an analog value. Includes `FB_Motor`, `FB_Valve` and the mode and alarm template.
3. **Syrup room and carbonator** (done): the process-control machine; links to the filler through `Product_Available`.
4. *(stretch, in progress since 2026-11-15)* **Labeler:** adds a heated glue tank, a turn counter and the first real conveyor link (C2).
5. *(stretch)* **Packer:** adds the 6-pack counter and shrink tunnel.
6. *(stretch)* **Blow moulder:** completes the start of the line.
7. **Line overview and integration tests:** full line run, starved and blocked situations (e.g. the filler-stop scenario in section 5.5), E-stop, communication loss (simulator stopped), fault injection.
8. **Documentation and presentation.**

### Risks and fallbacks

- **Scope:** five machines is a large amount of work for one semester (the filler alone took about a week of design, ladder and simulator work). Therefore only the filler and the syrup room are committed (see above); the other three are added only if time remains, the blow moulder last.
- **OpenPLC v4 Modbus mapping:** the %MW offsets must be confirmed before the address map is fixed.
- **Five runtimes:** five OpenPLC containers use more memory and must each be programmed separately from the editor. Each project is stored in its own folder under `plcs/` in Git.
- **HMI refresh rate:** polling five devices may slow FUXA; the polling interval is tuned per device, and animation uses slots instead of continuous motion.
