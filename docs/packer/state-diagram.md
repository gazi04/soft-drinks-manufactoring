# Packer – State diagram

Draft, 2026-11-21 (second version: collation and wrap as two sequencers in parallel, **film-slot wrapper** – film cut first, the moving pack drives onto the sheet; oven outside both sequencers). Signal names from `io-list.md`; answers Q1–Q6 and the review decisions of 2026-11-21 are listed at the end of `io-list.md`. Same building rules as the other machines: step variable + compare, Stop at safe points, waiting is not a fault, Auto OR Manual output rungs.

## Machine layout: two sequencers in parallel, one continuous oven

The filler is an **indexing** machine, the syrup room a **batch** machine, the labeler **one station, one bottle at a time**. The packer has **two sequencers that work at the same time** (decision 2026-11-21, pipelining) and a **continuous** oven behind them:

```
  Bottle_At_Gate   Bottle_Counted                    Pack_At_Film    film slot     wrap bar ↻ (WrapBar, Wrap_Home)
 C3 ─► [b][b][b] |GATE| ─► ┌───────────┐ GroupPusher      ▼          ▼                ▼
 (ConveyorMotor3) (held    │ COLLATION │ ───────► ══════ [P] ═══════╗ ║ ╔═══════════════════ ─► ═══ OVEN ═══ ─► [P][P] ─► palletizer
                   open)   │ TABLE 2×3 │  (Pusher_Home,  WrapConveyor ║ ║ ║                     Oven_Entry  Oven_Exit  ▲ Table_Full
                           └───────────┘   Pusher_Out)                ║▲║  Film_In_Slot          OvenHeater, OvenBlower,
                                                                       ║ Knife                   Airflow_OK, OvenTemp,
                                                          film roll ─► FilmFeed (Film_Pulse)     OvenConveyor
                                                       (Film_Empty, Film_Low)
       ◄─── collation sequencer: Step ───►◄──────────────── wrap sequencer: WrapStep ────────────────►◄── oven: no sequencer ──►
```

How one 6-pack is made:

1. **Count:** the gate is open; bottles run from C3 onto the collation table, a sensor behind the gate counts each one. Lane dividers form the 2×3. At the 6th bottle the gate closes.
2. **Push:** when the wrap conveyor is clear, the group pusher pushes the six bottles onto it and goes home. The gate opens again: the **next group is counted while this pack is wrapped** (pipelining).
3. **Stop before the film:** the wrap conveyor carries the pack to `Pack_At_Film` and stops there.
4. **Feed and cut:** with the pack waiting, the feed rollers push film up through a narrow slot in the conveyor until `FilmLength` is counted; the knife cuts the sheet off the roll. The cut sheet now sticks out of the slot (`Film_In_Slot`).
5. **Drive on:** when the oven is hot and the outfeed has room, the conveyor starts again: the pack rides onto the front edge of the sheet; its weight holds the film.
6. **Wrap:** the wrap bar comes up behind the pack, lifts the back of the sheet over the top and lays it down in front; the pack drives over that end too. The film is around the pack.
7. **Shrink and out:** the pack runs into the oven, the hot air shrinks the film tight, the pack leaves onto the outfeed table, where the palletizer (stand-in) takes it.

Why two sequencers: a step variable can only be in **one** step at a time. While the collation counts bottles, the wrap sequencer feeds film and wraps the previous pack, and both must make progress. One sequencer per job, each with its own step word, is the IEC way to run two things in parallel (in SFC it would be a parallel branch). They talk to each other only at the hand-over of a group (rule 2).

The **oven** has no sequence at all: it is a continuous process (temperature control, blower, conveyor), like the labeler's glue heater (rules 6–8).

## Diagram: collation sequencer (`Step`, %MW51)

```
            Auto + Start, no FAULT alarm, Step = 0 AND WrapStep = 0  (also sets WrapStep, rule 2)
  ┌────────┐ ───────────────────────────────────────► ┌──────────────────────────┐
  │ 0 IDLE │                                          │ 5 WARM_UP  (oven only)   │
  └────────┘                                          └────────────┬─────────────┘
      ▲                                                            │ Oven_Ready
      │                                                            ▼
      │   Stop requested           ┌────────────────────────────────────────────────────┐
      ├────────────────────────────│ 10 COUNT                                           │◄──────────────┐
      │                            │   GateMotor while NOT Group_Complete               │               │
      │                            │   Group_Counter counts Bottle_Counted (CTU, PV 6)  │               │
      │                            └───────────────────────┬────────────────────────────┘               │
      │                                                    │ Group_Complete                             │
      │                                                    ▼                                            │
      │   Stop requested           ┌────────────────────────────────────────────────────┐               │
      ├────────────────────────────│ 15 WAIT_WRAP  (gate shut, group waits)             │               │
      │                            └───────────────────────┬────────────────────────────┘               │
      │                                                    │ WrapStep = 10 AND Wrap_Clear               │
      │                                                    ▼                                            │
      │                            ┌────────────────────────────────────────────────────┐               │
      │                            │ 20 PUSH  GroupPusher                               │               │
      │                            └───────────────────────┬────────────────────────────┘               │
      │                                                    │ Pusher_Out                                 │
      │                                                    ▼                                            │
      │                            ┌────────────────────────────────────────────────────┐               │
      │                            │ 25 RETRACT  (pusher springs home;                  │               │
      │                            │    Group_Counter reset → 0)                        │               │
      │                            └───────────────────────┬────────────────────────────┘               │
      │   Stop requested (end of RETRACT)                  │ Pusher_Home                                │
      └────────────────────────────────────────────────────┴────────────────────────────────────────────┘
                                                              otherwise
```

## Diagram: wrap sequencer (`WrapStep`, %MW52)

```
            set by the Start: 10, or 32 if the wrap bar is not home (rule 2)
  ┌────────┐ ─────────────────────► ┌───────────────────────────────────────────────────────────┐
  │ 0 IDLE │                        │ 10 WAIT_PACK  WrapConveyor while NOT Pack_At_Film         │◄─────────────────────┐
  └────────┘                        └──────┬───────────────────────────────────────┬────────────┘                      │
      ▲   Stop requested                   │ Pack_At_Film AND NOT Sheet_Waiting    │ Pack_At_Film AND Sheet_Waiting    │
      ├────────────────────────────────────┤ AND NOT Film_Empty                    │ (restart: a cut sheet is there)   │
      │                                    ▼                                       │                                   │
      │   Stop requested    ┌───────────────────────────────────┐                  │                                   │
      ├─────────────────────│ 20 FEED  FilmFeed until           │                  │                                   │
      │                     │   Feed_Counter.Q (FeedPulses)     │                  │                                   │
      │                     └──────────────┬────────────────────┘                  │                                   │
      │                                    │ Feed_Counter.Q                        │                                   │
      │                                    ▼                                       │                                   │
      │                     ┌───────────────────────────────────┐                  │                                   │
      │                     │ 22 CUT  Knife (CutTime 0.3 s)     │                  │                                   │
      │                     └──────────────┬────────────────────┘                  │                                   │
      │                                    │ Cut_Timer.Q                           │                                   │
      │                                    ▼                                       │                                   │
      │   Stop requested    ┌───────────────────────────────────┐                  │                                   │
      ├─────────────────────│ 24 READY  (pack waits on the stop;│◄─────────────────┘                                   │
      │                     │   Feed_Counter reset → 0)         │                                                      │
      │                     └──────────────┬────────────────────┘                                                      │
      │                                    │ Film_In_Slot AND Oven_Ready AND NOT Outfeed_Full AND Wrap_Home            │
      │                                    ▼                                                                           │
      │                     ┌───────────────────────────────────┐                                                      │
      │                     │ 30 RUN_ON  WrapConveyor           │  pack drives onto the sheet                          │
      │                     └──────────────┬────────────────────┘                                                      │
      │                                    │ NOT Pack_At_Film                                                          │
      │                                    ▼                                                                           │
      │                     ┌───────────────────────────────────┐                                                      │
      │                     │ 32 WRAP  WrapConveyor, WrapBar    │  bar lays the sheet over the top                     │
      │                     └──────────────┬────────────────────┘                                                      │
      │                                    │ Wrap_Home rising edge (bar back)                                          │
      │                                    ▼                                                                           │
      │                     ┌───────────────────────────────────┐                                                      │
      │                     │ 34 RUN_OUT  WrapConveyor          │                                                      │
      │                     └──────────────┬────────────────────┘                                                      │
      │   Stop requested (end of RUN_OUT)  │ Oven_Entry                                                                │
      └────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────┘
                                             otherwise

  From ANY step of EITHER sequencer:  film fault / pack jam / wrap timeout / oven over-temperature / heat-up timeout / oven jam / airflow lost
                       ─► Step := 900 AND WrapStep := 900 (alarm latched) ─► Auto + Reset (cause gone) ─► both 0 IDLE
  E_Stop_OK = FALSE or Mode Stop ─► both steps → 0 IDLE (E-stop: master enable off, every output off, blower too)
```

`Sheet_Waiting := Film_In_Slot AND FeedCount = 0`: a sheet is in the slot and no feed is under way, so it is a **cut** sheet (rule 4).

## Step tables

C3 (`ConveyorMotor3`) runs in every step except FAULT and E-stop, also in IDLE (report 5.3 point 3), and is left out of the "Outputs" column. Heater, blower and oven conveyor are controlled outside the sequencers (rules 6–8).

**Collation (`Step`)**

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | Auto AND Start AND no FAULT alarm AND `WrapStep` = 0 → 5 (and `WrapStep` := 10 or 32, rule 2) | – |
| 5 | WARM_UP | – | `Oven_Ready` → 10 | 120 s (`Alm_OvenHeatTimeout`) |
| 10 | COUNT | GateMotor while `NOT Group_Complete` | `Group_Complete` → 15 | none (waiting = **Starved**, rule 10) |
| 15 | WAIT_WRAP | – | `WrapStep = 10 AND Wrap_Clear AND Group_Settled` → 20 | none (waiting for the own wrap conveyor is **Running**) |
| 20 | PUSH | GroupPusher | `Pusher_Out` → 25 | 3 s (`Alm_PackJam`) |
| 25 | RETRACT | – (spring return); `Group_Counter` held at 0 | `Pusher_Home` → 10 | 3 s (`Alm_PackJam`) |
| 900 | FAULT | – | Auto + Reset (cause gone) → 0 | – |

**Wrap (`WrapStep`)**

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | set by the Start (rule 2) | – |
| 10 | WAIT_PACK | WrapConveyor while `NOT Pack_At_Film` | `Pack_At_Film AND Sheet_Waiting` → 24; `Pack_At_Film AND NOT Sheet_Waiting AND NOT Film_Empty` → 20 | none (film empty = **Starved**) |
| 20 | FEED | FilmFeed while `NOT Feed_Counter.Q` | `Feed_Counter.Q` → 22 | 10 s (`Alm_FilmFault`) |
| 22 | CUT | Knife | `Cut_Timer.Q` (`CutTime` 0.3 s) → 24 | – |
| 24 | READY | – (pack waits on the stop); `Feed_Counter` held at 0 | `Film_In_Slot AND Oven_Ready AND NOT Outfeed_Full AND Wrap_Home` → 30 | no sheet for 1 s (`Alm_FilmFault`); `Outfeed_Full` = **Blocked** (`Alm_OutfeedBlocked` after 2 min); oven not ready = Running |
| 30 | RUN_ON | WrapConveyor | `NOT Pack_At_Film` → 32 | 3 s (`Alm_PackJam`) |
| 32 | WRAP | WrapConveyor, WrapBar | `Wrap_Home` rising edge → 34 | 5 s (`Alm_WrapTimeout`) |
| 34 | RUN_OUT | WrapConveyor | `Oven_Entry` → 10 | 3 s (`Alm_PackJam`) |
| 900 | FAULT | – | Auto + Reset (cause gone) → 0 | – |

**Computed values** (every scan):

| Name | Formula | Syllabus |
| --- | --- | --- |
| `Oven_Ready` | `OvenTemp ≥ OvenSetpoint − OvenBand AND OvenTemp ≤ OvenSetpoint + OvenBand` | VIII math (`SUB`, `ADD`) + window compare |
| `Heat_Demand` | hysteresis, rule 6 | VIII compare, IV seal-in |
| `FeedPulses` | `LIMIT(FilmMin, FilmLength, FilmMax) / FilmPulseMM` (LIMIT keeps a wrong entry in 600–1200 mm, DIV with INT truncation) | VIII math |
| `Sheet_Waiting` | `Film_In_Slot AND FeedCount = 0` (EQ) | VI logic, VIII compare |
| `GroupCount`, `FeedCount`, `PacksInOven` | `CV` of the three counters (MOVE) | VII counters, X data handling |
| `Packs_Inside` | `PacksInOven > 0` (GT) | VIII compare |

**Fixed constants** (internal variables with initial values, tuned with the simulator):

| Name | Value | Meaning |
| --- | --- | --- |
| `PackSize` | 6 | Bottles per pack, PV of `Group_Counter` (decision 2026-11-21: constant, not a setpoint) |
| `FilmMin`, `FilmMax` | 600, 1200 (mm) | `LIMIT` on `FilmLength`, so `FeedPulses` is always 12–24 (same range as the HMI limits) |
| `FilmPulseMM` | 50 | Film per `Film_Pulse` (mm); the simulator's feed speed keeps the pulses ≥ 0.3 s apart (100 ms tick) |
| `StarvedDelay` | 2 s | No bottle at the gate this long = Starved (rule 10) |
| `ClearTime` | 3 s | The wrap conveyor must run empty this long before the next push (rule 2); longer than a group's travel from the pusher to `Pack_At_Film` |
| `SettleTime` | 0.5 s | Wait in WAIT_WRAP before PUSH: the count sensor sits in front of the table, the 6th bottle needs about 0.4 s to land after its count (added 2026-11-23, found by the simulator) |
| `PushTimeout` | 3 s | PUSH, RETRACT, RUN_ON and RUN_OUT must finish within this time |
| `FeedTimeout` | 10 s | FEED must count `FeedPulses` within this time (longest sheet 1200 mm) |
| `CutTime` | 0.3 s | Length of CUT (knife ON) |
| `SheetCheck` | 1 s | READY without `Film_In_Slot` this long = sheet missing |
| `WrapTimeout` | 5 s | WRAP must see the wrap bar back home within this time |
| `HeatTimeout` | 120 s | WARM_UP must reach `Oven_Ready` within this time (simulator time; a real shrink tunnel needs 10–20 min) |
| `OvenMax` | 2300 (230.0 °C) | Over-temperature limit |
| `BlowerRunOn` | 30 s | TOF: blower keeps running this long after the oven is switched off (rule 7) |
| `AirflowTimeout` | 3 s | Blower on, no `Airflow_OK` this long = `Alm_AirflowLost` |
| `OvenJamTime` | 20 s | Packs inside and no pack out for this long = `Alm_OvenJam` (about twice the oven transit time, tuned with the simulator) |
| `BlockedWarn` | 2 min | `Alm_OutfeedBlocked` (same as the filler and the labeler) |

Setpoint defaults: `OvenSetpoint` 1900 (190.0 °C), `OvenHyst` 50 (5 °C), `OvenBand` 150 (±15 °C): ready window 175–205 °C, heater band 185–190 °C, limit 230 °C. `FilmLength` 850 mm → 17 pulses.

## Rules

The ladder sketches below are for understanding only; the real rungs are written in the OpenPLC Editor.

### 1. Steps decide the outputs (same as the other machines)

Two step words, each with its own transition rungs (`EQ Step,x AND condition → MOVE y → Step`, `EQ WrapStep,x AND … → MOVE y → WrapStep`) and step bits (`Step_Count`, `Step_Push`, …, `Wrap_WaitPack`, `Wrap_Feed`, …). Output requests:

```
GateMotor request     := Step_Count AND NOT Group_Complete
GroupPusher request   := Step_Push
WrapConveyor request  := (Wrap_WaitPack AND NOT Pack_At_Film) OR Wrap_RunOn OR Wrap_Wrap OR Wrap_RunOut
FilmFeed request      := Wrap_Feed AND NOT Feed_Counter.Q
Knife request         := Wrap_Cut
WrapBar request       := Wrap_Wrap
```

RETRACT has no output: the group pusher is spring-return (single-acting cylinder), it goes home as soon as its valve drops. The **film stop** is not a mechanical gate: the wrap conveyor simply stops while the pack is at `Pack_At_Film` (WAIT_PACK, FEED, CUT, READY).

### 2. Two sequencers, one machine

The two step words are separate, but the machine has **one** Start, Stop, FAULT and State:

- **Start** (Auto, no FAULT alarm, both steps 0) moves both at once: `Step` := 5 and `WrapStep` := 10 – or `WrapStep` := 32 when `NOT Wrap_Home`: the bar was stopped in the middle of a turn (E-stop), so the wrap is finished first (rule 11). The wrap sequencer has no WARM_UP of its own: a pack may get its sheet while the oven heats, only READY → RUN_ON needs `Oven_Ready`.
- **Hand-over** of a group: the collation pushes (15 → 20) only while the wrap sequencer waits for a pack (`WrapStep = 10`) **and the wrap conveyor is clear**:

  ```
  Clear_Timer := TON(IN := Wrap_WaitPack AND Pusher_Home AND NOT Pack_At_Film, PT := ClearTime)
  Wrap_Clear  := Clear_Timer.Q
  ```

  The conveyor runs in WAIT_PACK; once it has run **empty** for `ClearTime` (longer than a group's travel from the pusher to the film stop), nothing can still be on it. A push drops `Pusher_Home` and restarts the timer, so a second group can never be pushed behind the first one. No extra sensor and no memory bit: a "clear belt" timer, as real conveyors use. It also covers the restart: a group left on the conveyor by an E-stop reaches `Pack_At_Film` before the timer can finish.
- **Settle time** before the push (added 2026-11-23): the count sensor sits in front of the table, so the 6th bottle is counted about 0.4 s before it lies on the table. WAIT_WRAP waits `SettleTime` before PUSH:

  ```
  Settle_Timer  := TON(IN := Step_WaitWrap, PT := SettleTime)
  Group_Settled := Settle_Timer.Q
  ```

  The timer starts with WAIT_WRAP, so it adds no wait when the wrap conveyor clears later than 0.5 s; leaving WAIT_WRAP (PUSH, Stop, FAULT) resets it. A restart with a complete group (pushed halfway) waits it again: harmless. No extra sensor, like `Wrap_Clear`.
- **FAULT** of either side moves **both** to 900 (one alarm stops the machine); Reset moves both to 0.
- **Stop / Mode Stop / E-stop** act on both (rule 9).

### 3. Counting the group: CTU with a preset, reset per group

```
Group_Counter  := CTU(CU := Bottle_Counted, RESET := Step_Retract, PV := PackSize)
Group_Complete := Group_Counter.Q        (CV ≥ 6) → closes the gate
GroupCount     := Group_Counter.CV       (0–6, for the HMI)
```

The CTU counts **rising edges** by itself (an IEC counter has the edge detection inside), so a bottle that stays in front of the sensor is counted once. `RESET := Step_Retract` sets the counter back to 0 when the group has arrived on the wrap conveyor (`Pusher_Out`, 20 → 25) – not earlier, so a group stopped halfway by an E-stop is still "complete" after the restart and is pushed again (rule 11). The gate output is cut by `Group_Complete` in the **same scan** in which the 6th bottle is counted; the count sensor sits right behind the gate, so the 7th bottle is still in front of it (the simulator models this).

### 4. The sheet: length set by the operator, measured by a counter, checked by a sensor

```
FeedPulses   := LIMIT(FilmMin, FilmLength, FilmMax) / FilmPulseMM   (LIMIT 600–1200 mm, then DIV: 850 / 50 = 17)
Feed_Counter := CTU(CU := Film_Pulse, RESET := Wrap_Ready, PV := FeedPulses)
FEED done    := Feed_Counter.Q
FeedCount    := Feed_Counter.CV
```

- **The operator chooses** the length per product (`FilmLength`, recipe value); **the PLC measures** it by counting pulses of the feed roller. Counting works at any feed speed (the same argument as the labeler's turn counter; a feed timer would depend on the motor). INT division truncates: 870 mm gives 17 pulses = 850 mm (week VIII, as the syrup room's ratio).
- **Feed only when a pack waits** at `Pack_At_Film` (WAIT_PACK → FEED): a sheet never lies in the slot without its pack.
- **The counter is reset in READY**, not at the start of FEED: after a Stop or E-stop in the middle of FEED it still holds the pulses already fed, and the restart **feeds only the missing rest**. An E-stop in CUT leaves `Feed_Counter.Q` set: the restart goes FEED → CUT at once and cuts again (harmless).
- **`Film_In_Slot`** answers two questions with the help of the counter:

  | `Film_In_Slot` | `FeedCount` | Meaning | WAIT_PACK goes to |
  | --- | --- | --- | --- |
  | FALSE | 0 | slot empty | FEED (a whole sheet) |
  | TRUE | > 0 | a feed was interrupted, film still on the roll | FEED (the rest), then CUT |
  | TRUE | 0 | a **cut** sheet waits (stop in READY) | READY – never a second sheet on top |

  In READY the sheet must be there before the pack drives on: no `Film_In_Slot` for `SheetCheck` (1 s; sheet fell, torn) → `Alm_FilmFault`.
- **Knife:** CUT is its own step, the knife is ON for the whole step, a TON (`Cut_Timer`, `CutTime` 0.3 s) ends it (review decision, option A). The labeler needed a TP because its cut happens inside a longer step; here nothing else happens during the cut.

### 5. The wrap: one turn of the bar while the pack moves

The pack only drives on (READY → RUN_ON) with the sheet in the slot, the oven hot, room on the outfeed and the bar home. RUN_ON ends when the pack has left the film stop (`NOT Pack_At_Film`): it is on the sheet. WRAP runs the bar **one turn** while the conveyor keeps moving: the step ends at the **rising edge** of `Wrap_Home` (edge bit as the labeler's `Mark_Edge`), not on `Wrap_Home` itself, which is already TRUE when the step starts. RUN_OUT carries the wrapped pack to `Oven_Entry`.

The pack only ever moves **forward**: there is no wrap station and no memory of "wrapped or not". A restart can never wrap a pack twice (review decision: film-slot wrapper).

### 6. Oven temperature: hysteresis, only in Auto

Same pattern as the labeler's glue (Q2), but the oven is on **only in Auto** (review decision):

```
Oven_On     := Master_Enable AND Mode_Auto AND NOT Step_Fault
Heat_Demand := set at OvenTemp < OvenSetpoint − OvenHyst, reset at OvenTemp ≥ OvenSetpoint (seal-in)
OvenHeater  := Heat_Demand AND Oven_On AND Airflow_OK AND OvenTemp < OvenMax   (interlocks)
```

- `Oven_Ready` is the wider window (±15 °C) around the 5 °C heater band, so the heater's normal swing never holds a pack.
- **Auto only:** Manual means someone works at the machine, a hot oven is not needed for jogging, and leaving Auto shows the blower's cool-down (rule 7). Cmd Stop (Stop button, mode stays Auto) keeps the oven hot for short pauses. Back from Manual or Mode Stop, WARM_UP heats again (≤ 120 s in the simulator).

### 7. Blower with a run-on: TOF

The heater coils get very hot. If the air stops while they are hot, they burn out, so the blower must (a) run **before** the heater may switch on and (b) keep running for a while **after** the oven is switched off:

```
Blower_TOF := TOF(IN := Oven_On, PT := BlowerRunOn)
OvenBlower := Blower_TOF.Q
```

A TOF's output follows its input ON at once, and goes OFF only `PT` after the input went OFF. So: Auto → blower on at once; Manual, Mode Stop or a FAULT → heater off at once (`Oven_On` in its rung), blower **30 s later**. The blower is the only output that may still run in FAULT (an over-temperature FAULT is exactly when cooling matters). E-stop drops `Master_Enable` and with it every output, the blower too (the FB's `Enable`): safety first.

(a) is the **airflow interlock**: the heater needs `Airflow_OK`, the feedback that air really flows, not just the blower command (a broken belt would leave the command ON and the air still). Blower ON and no `Airflow_OK` for `AirflowTimeout` (3 s) → `Alm_AirflowLost` → FAULT (Q6).

### 8. Oven conveyor and the packs inside: CTUD

```
Oven_Counter := CTUD(CU := Oven_Entry, CD := Oven_Exit, RESET := Reset_Cmd AND Alm_OvenJam, PV := 32767)
PacksInOven  := Oven_Counter.CV
Packs_Inside := PacksInOven > 0
OvenConveyor request := Oven_On OR Packs_Inside
```

- Up at every pack in, down at every pack out: `PacksInOven` is the number of packs between the two sensors (week VII, up/down counter).
- **The belt runs for one of two reasons** (review decision): the oven is on (it never stands still in the heat, and a pack the counter missed still comes out), **or** packs are still inside (after Manual, Mode Stop or Cmd Stop the oven empties itself, then the belt stops). Off on E-stop and FAULT. In Manual `Man_OvenConveyor` can also run it.
- **Oven jam** (Q6): packs inside and no pack out for `OvenJamTime`:

  ```
  Jam_Timer := TON(IN := Packs_Inside AND OvenConveyor AND NOT Exit_Edge, PT := OvenJamTime)
  ```

  Every pack out (`Exit_Edge`, one scan) restarts the timer, so it measures "time since the last pack out" while packs are inside. → `Alm_OvenJam` → FAULT.
- **Counter reset:** the operator empties the oven by hand after a jam; then the counter must be 0, so Reset of `Alm_OvenJam` also resets the CTUD. No other reset: a Reset after any other alarm keeps the packs that are really inside.
- `PackCount` (CTU `Pack_Counter`, CU := `Oven_Exit`, RESET := `Count_Reset_Cmd`) counts finished packs at the oven exit; `Cmd = 4` zeroes it, Reset (`Cmd = 3`) never does (as the labeler).

### 9. Stop: finish what was started, then IDLE

`Stop_Request` (seal-in, cleared when both steps are 0) as in the other machines. A push or a pack on its way over the film is finished:

| Sequencer | Safe point → IDLE at once | Finished first |
| --- | --- | --- |
| Collation | WARM_UP, COUNT (gate shuts, counted bottles stay, the CTU keeps its count), WAIT_WRAP | PUSH → RETRACT, IDLE at the end of RETRACT |
| Wrap | WAIT_PACK, FEED (the counter keeps the fed length), READY (the cut sheet stays in the slot) | CUT → READY; RUN_ON → WRAP → RUN_OUT, IDLE when the pack reaches `Oven_Entry` |

The oven conveyor keeps running until the oven is empty (rule 8). **Mode Stop** and **E-stop** abort both sequencers at once (step → 0), as in the other machines.

### 10. Waiting is not a fault; one State for two sequencers

One `State` for the machine (Q1), first match wins:

| Priority | Condition | State |
| --- | --- | --- |
| 1 | `Step = 900` (both are 900 together) | **9 Fault** |
| 2 | `Step = 0 AND WrapStep = 0` | **0 Stopped** |
| 3 | `Wrap_Ready AND Outfeed_Full` | **3 Blocked** (outfeed table full; `Alm_OutfeedBlocked` after 2 min) |
| 4 | `Starved` (below) | **2 Starved** |
| 5 | otherwise (also WARM_UP, a pack waiting for a cold oven) | **1 Running** |

```
Gate_Empty_Timer := TON(IN := NOT Bottle_At_Gate, PT := StarvedDelay)
Starved          := Wrap_WaitPack AND ((Step_Count AND Gate_Empty_Timer.Q AND NOT Pack_At_Film) OR Film_Empty)
```

- **No bottles:** Starved only when the **wrap side has nothing to do** too (no pack at the film): while a pack is still being wrapped the machine is working, even if C3 is empty. The 2 s filter is the labeler's (a gap between two bottles is not "starved").
- **No film:** a pack waits at the film stop and the roll is empty → Starved with the `Alm_FilmEmpty` warning, which clears itself after the roll change (Q6).

### 11. Resume after Stop, FAULT or E-stop: sensors and counters tell where we are

After Reset → IDLE → Start the sequencers continue from what the machine **knows**:

| Situation at the restart | What happens | Known by |
| --- | --- | --- |
| 1–5 bottles on the collation table | COUNT counts on from there | `Group_Counter.CV` (kept) |
| 6 bottles, group not pushed (or pushed halfway) | WAIT_WRAP → PUSH (again) | `Group_Complete` (reset only after the group arrived, rule 3) |
| group on the wrap conveyor before the film | WAIT_PACK carries it to the film stop | `Pack_At_Film` |
| pack at the film stop, feed interrupted | FEED the rest, CUT | `FeedCount` > 0 |
| pack at the film stop, cut sheet in the slot | READY, **no second sheet** | `Film_In_Slot AND FeedCount = 0` |
| wrap bar stopped in the middle of a turn | WRAP finishes the turn, RUN_OUT | `NOT Wrap_Home` → Start sets `WrapStep` := 32 |
| pack between the film and the oven | WAIT_PACK's conveyor carries it into the oven | (no sensor needed: the conveyor runs while no pack is at the film) |
| packs in the oven | they leave (`Oven_On OR Packs_Inside`) | `PacksInOven` |

**Known limitations:** after an E-stop in the middle of a wrap the loose film may slip; the PLC finishes the turn anyway, and the operator checks that pack. Bottles taken off the collation table by hand are not known to the counter: the next group is short (no group sensor, the CTU is the only count). A half-fed sheet torn off by hand leaves `FeedCount` > 0: the next sheet is short by that much.

### 12. Interlocks (in Auto **and** Manual)

| Output | Allowed only when | Why |
| --- | --- | --- |
| GateMotor | `Pusher_Home AND NOT Group_Complete` | Never a 7th bottle; no bottle into a pusher that is out |
| GroupPusher | – | Pushes onto the conveyor's infeed end, away from film and bar; the hand-over (rule 2) keeps the belt clear |
| WrapConveyor | `NOT FilmFeed AND NOT Knife` | The pack never moves while film is fed or cut |
| FilmFeed | `Wrap_Home AND NOT WrapBar` | Film never fed into the bar's path |
| Knife | `NOT FilmFeed` | Never cut while feeding (the film would tear) |
| WrapBar | `NOT FilmFeed AND NOT Knife` | Bar and film feed lock each other out, like a motor's forward / reverse contactors (week IV) |
| OvenHeater | `Airflow_OK AND OvenTemp < OvenMax AND Oven_On` | No heater without air, no over-temperature, only in Auto, not in FAULT |
| OvenBlower | – (only `Master_Enable`) | Cooling is always allowed, also in FAULT (rule 7) |
| OvenConveyor | `NOT Step_Fault` | Stops on a FAULT (a jam must not be pushed on) |
| ConveyorMotor3 | – | Always safe (accumulation conveyor) |

### 13. Manual mode (same as the other machines)

Both sequencers frozen, one hold-to-run button per actuator (`Man_*`), jog watchdog (`Jog_Block`), interlocks still apply. The oven is **off** in Manual (rule 6): the blower runs on for 30 s, the oven conveyor empties the oven by itself (rule 8). Manual → Auto goes to IDLE; Start resumes by rule 11 (after WARM_UP).

### 14. E-stop and FAULT

- **E-stop** (`E_Stop_OK` = FALSE): `Master_Enable` off, every output off at once (C3, heater, **blower** and oven conveyor too), both steps → IDLE; release + Start.
- **FAULT** (film fault, pack jam, wrap timeout, oven over-temperature in every mode, heat-up timeout, oven jam, airflow lost): both steps → 900, all off except the **blower on its run-on**, alarm latched; Auto + Reset clears it only when the cause is gone (`Alm_OvenOverTemp`: only below `OvenMax`; `Alm_OvenJam`: also zeroes `PacksInOven`, rule 8). `Alm_AirflowLost` can always be reset: in FAULT the blower stops after its run-on, so `Airflow_OK` could never return; a still broken blower trips the alarm again 3 s after the next blower start.
