# Labeler – State diagram

Draft, 2026-11-15 (fourth version: roll-fed hot-melt labeler with clamp and spin; descriptive sensor names; **no inspection, no reject**). Signal names from `io-list.md`. Same building rules as the filler and the syrup room: step variable + compare, Stop at safe points, waiting is not a fault, Auto OR Manual output rungs.

## Machine layout: one station, one bottle at a time

The filler is an **indexing** machine (four pockets, all stations in parallel), the syrup room a **batch** machine. The labeler has **one pocket** on a turntable, one bottle at a time (decision 2026-11-15, `progress.md`). Every bottle that is labelled goes to C3; there is no inspection and no reject (decision 2026-11-15: reject and fill check are future enhancements of the filler).

```
                       label roll (Roll_Empty, Roll_Low)
                            ( O )
                              │ web
                       feed rollers (LabelFeed)
                              │
               Label_Mark ────┤ knife (Cutter)
                              │ one label
           melt tank ──► glue roller (GlueRoller)
       (GlueHeater, GlueTemp,  │ glue on both edges
        Glue_Low)              ▼
       Bottle_At_Gate     ┌──────────────────────┐  centering bell (ClampValve, Bell_Down)
  C2 ──► [b][b][b] |GATE| │ POCKET on turntable  │
  (ConveyorMotor2)  (TP)  │ bottle spins (SpinMotor, Spin_Pulse once per turn)
                          │ Bottle_In_Pocket, Label_Present
                          └──────────┬───────────┘
                                     │ DischargeMotor
                                     ▼
                                    C3 ──► packer (stand-in)    C3_Full
```

How a real roll-fed hot-melt labeler puts on one label:

1. **Gate:** one bottle enters the pocket on the turntable.
2. **Clamp:** the centering bell comes down and holds the bottle, so it cannot fall over while it spins.
3. **Wrap:** the turntable spins the bottle. At the same time the feed rollers pull the label web off the roll, and the glue roller puts hot melt on the label's leading edge. The glued edge touches the spinning bottle, sticks, and the bottle "winds" the label around itself.
4. **Cut:** a printed **registration mark** on the web (one per label) passes `Label_Mark`: one label length has been fed, the knife cuts it off.
5. **Press:** the bottle turns a few more times (`PressTurns`), so the glued tail is pressed onto the label.
6. **Unclamp:** the bell goes up, the bottle is pushed onto C3.

The hot melt is solid at room temperature. The operator puts glue blocks into the **melt tank**; the heater keeps them at about 170 °C. Too cold, the glue does not stick; too hot, it burns. That is why labelling is only allowed inside a temperature window (rule 4).

## Diagram

```
             Auto + Start, no FAULT alarm
  ┌────────┐ ─────────────────────► ┌──────────────────────────┐
  │ 0 IDLE │                        │ 5 WARM_UP  (heater only) │◄─────── NOT Glue_Ready AND NOT Bottle_In_Pocket ─┐
  └────────┘                        └────────────┬─────────────┘                                                  │
      ▲                                          │ Glue_Ready                                                     │
      │                                          ▼                                                                │
      │                     ┌──────────────────────────────────────────────────┐                                  │
      │   Stop requested    │ 10 WAIT_BOTTLE                                   │──────────────────────────────────┘
      ├─────────────────────│   GateMotor: TP pulse if Bottle_At_Gate AND      │◄─────────────────────────────────────┐
      │                     │   NOT Bottle_In_Pocket AND NOT Bottle_Entering   │                                      │
      │                     │   AND NOT Roll_Empty AND Glue_Ready              │                                      │
      │                     └──┬───────────────────────────────────┬───────────┘                                      │
      │  Bottle_In_Pocket AND  │                                   │ Bottle_In_Pocket AND Label_Present            │
      │  NOT Label_Present AND │                                   │ (already labelled: restart)                   │
      │  Glue_Ready            ▼                                   │                                               │
      │          ┌───────────────────────────┐                     │                                               │
      │          │ 20 CLAMP  ClampValve      │                     │                                               │
      │          └────────────┬──────────────┘                     │                                               │
      │                       │ Bell_Down                          │                                               │
      │                       ▼                                    │                                               │
      │          ┌───────────────────────────┐                     │                                               │
      │          │ 22 WRAP  ClampValve,      │                     │                                               │
      │          │  SpinMotor, LabelFeed,    │                     │                                               │
      │          │  GlueRoller               │                     │                                               │
      │          └────────────┬──────────────┘                     │                                               │
      │                       │ Label_Mark rising edge             │                                               │
      │                       ▼                                    │                                               │
      │          ┌───────────────────────────┐                     │                                               │
      │          │ 24 PRESS  ClampValve,     │                     │                                               │
      │          │  SpinMotor; Cutter TP at  │                     │                                               │
      │          │  step start; count turns  │                     │                                               │
      │          └────────────┬──────────────┘                     │                                               │
      │                       │ Spin_Counter ≥ PressTurns          │                                               │
      │                       ▼                                    │                                               │
      │          ┌───────────────────────────┐                     │                                               │
      │          │ 26 UNCLAMP  (bell up)     │                     │                                               │
      │          └────────────┬──────────────┘                     │                                               │
      │                       │ NOT Bell_Down                      │                                               │
      │                       ├────────────────────────────────────┘                                               │
      │                       │ Outfeed_Full                                                                       │
      │                       ├────────────────► ┌──────────────────────┐                                          │
      │                       │                  │ 35 WAIT_OUTFEED      │                                          │
      │                       │ NOT Outfeed_Full │   (Blocked)          │                                          │
      │                       ▼                  └──────────┬───────────┘                                          │
      │          ┌──────────────────────────────┐           │ NOT Outfeed_Full                                     │
      │          │ 40 DISCHARGE  DischargeMotor │◄──────────┘                                                      │
      │          └──────────────┬───────────────┘                                                                  │
      │                         │ NOT Bottle_In_Pocket → GoodCount +1                                              │
      │  Stop requested (at a safe point, rule 6)                                                                  │
      └─────────────────────────┴──────────────────────────────────────────────────────────────────────────────────┘
                                  otherwise

  From ANY step:  label timeout / pocket jam / glue over-temperature / heat-up timeout
                                    ─► 900 FAULT (all off, heater too, alarm latched) ─► Reset ─► 0 IDLE
                  E_Stop_OK = FALSE ─► master enable off, all off (heater too), step → 0 IDLE
```

## Step table

C2 (`ConveyorMotor2`) runs in every step except FAULT and E-stop, also in IDLE (report 5.3 point 3), and is left out of the "Outputs" column. The glue heater is controlled outside the sequencer (rule 4).

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | Auto AND Start AND no FAULT alarm → 5 | – |
| 5 | WARM_UP | – | `Glue_Ready` → 10 | 120 s (`Alm_GlueHeatTimeout`) |
| 10 | WAIT_BOTTLE | GateMotor as TP pulse (0.5 s) if `Bottle_At_Gate AND NOT Bottle_In_Pocket AND NOT Bottle_Entering AND NOT Roll_Empty AND Glue_Ready` (rule 2) | `Bottle_In_Pocket AND NOT Label_Present AND Glue_Ready` → 20 (cold glue: the bottle waits in 10); `Bottle_In_Pocket AND Label_Present` → 40 (or 35 if `Outfeed_Full`); `NOT Glue_Ready AND NOT Bottle_In_Pocket` → 5 | none (waiting = **Starved**) |
| 20 | CLAMP | ClampValve | `Bell_Down` → 22 | 2 s (`Alm_PocketJam`) |
| 22 | WRAP | ClampValve, SpinMotor, LabelFeed, GlueRoller | `Label_Mark` rising edge → 24 | 5 s (`Alm_LabelTimeout`) |
| 24 | PRESS | ClampValve, SpinMotor; Cutter as TP pulse (0.2 s) at step start | `Spin_Counter.Q` (CV ≥ `PressTurns`) → 26 | 5 s (`Alm_LabelTimeout`) |
| 26 | UNCLAMP | – (bell goes up when ClampValve drops) | `NOT Bell_Down`: `NOT Outfeed_Full` → 40, `Outfeed_Full` → 35 | 2 s (`Alm_PocketJam`) |
| 35 | WAIT_OUTFEED | – | `NOT Outfeed_Full` → 40 | none (waiting = **Blocked**; `Alm_OutfeedBlocked` warning after 2 min) |
| 40 | DISCHARGE | DischargeMotor | `NOT Bottle_In_Pocket` → 10, `GoodCount` +1 | 3 s (`Alm_PocketJam`) |
| 900 | FAULT | – (heater off too) | Reset (cause gone) → 0 | – |

**Computed values** (every scan):

| Name | Formula | Syllabus |
| --- | --- | --- |
| `Glue_Ready` | `GlueTemp ≥ GlueSetpoint − GlueBand AND GlueTemp ≤ GlueSetpoint + GlueBand` | VIII math (`SUB`, `ADD`) + window compare |
| `Heat_Demand` | hysteresis, rule 4 | VIII compare, IV seal-in |

**Fixed constants** (internal variables with initial values, tuned with the simulator):

| Name | Value | Meaning |
| --- | --- | --- |
| `GatePulse` | 0.5 s | TP length of the gate (one bottle, as the filler's door) |
| `CutPulse` | 0.2 s | TP length of the knife |
| `StarvedDelay` | 2 s | No bottle at the gate this long = Starved (rule 8) |
| `ClampTimeout` | 2 s | CLAMP / UNCLAMP must see `Bell_Down` change, a gated bottle must reach the pocket (`Bottle_Entering`) within this time |
| `LabelTimeout` | 5 s | WRAP must see `Label_Mark`, PRESS must count `PressTurns` within this time |
| `OutTimeout` | 3 s | DISCHARGE must empty the pocket within this time |
| `HeatTimeout` | 120 s | WARM_UP must reach `Glue_Ready` within this time (simulator time; a real melter needs 20–30 min) |
| `GlueMax` | 2000 (200.0 °C) | Over-temperature limit |
| `BlockedWarn` | 2 min | `Alm_OutfeedBlocked` (same as the filler) |

## Rules

The ladder sketches below are for understanding only; the real rungs are written in the OpenPLC Editor.

### 1. Steps decide the outputs (same as the filler)

`Step` (%MW51) holds the current step; transition rungs change it (`EQ Step,x AND condition → MOVE y → Step`), output rungs compare it. Steps that share an output are grouped, as in the syrup room (`CO2_Steps`):

```
ClampValve request     := Step_Clamp OR Step_Wrap OR Step_Press
SpinMotor request      := Step_Wrap OR Step_Press
LabelFeed request      := Step_Wrap
GlueRoller request     := Step_Wrap
Cutter                 := TP(IN := Step_Press, PT := CutPulse)
GateMotor              := TP(IN := Step_Wait AND Bottle_At_Gate AND NOT Bottle_In_Pocket AND NOT Bottle_Entering
                                   AND NOT Roll_Empty AND Glue_Ready, PT := GatePulse)
Bottle_Entering        := (S) by GateMotor, (R) by Bottle_In_Pocket
DischargeMotor request := Step_Discharge
```

UNCLAMP has no output: the bell is spring-return, it goes up as soon as `ClampValve` drops.

### 2. Two TP pulses: gate and knife

- **Gate:** copy of the filler's door (`docs/filler/state-diagram.md` rule 6), including its `Entry_Load` idea. The pocket has a sensor, but a bottle needs a moment to travel from the gate to it. In that moment `Bottle_At_Gate` drops (bottle 1 left the gate) and rises again (bottle 2 moves up), while `Bottle_In_Pocket` is still FALSE: the TP sees a new rising edge and would admit bottle 2 onto bottle 1. So the PLC remembers "a bottle is on its way": `Bottle_Entering` (%MX3.1) is set by the gate pulse and reset when `Bottle_In_Pocket` turns TRUE; the gate pulse needs `NOT Bottle_Entering`. If the bottle never arrives (stuck in the gate), `Bottle_Entering` stays set for longer than `ClampTimeout` (2 s) → `Alm_PocketJam`.
- **Knife:** the cut must be short and happen **once** per label. `Step_Press` is TRUE for the whole step, but a TP gives one fixed pulse at its rising edge, so entering PRESS fires the knife once. Same trick as the gate: the step change is the trigger.

### 3. Counting turns: CTU on the spin pulse

After the cut, the label tail still hangs loose; the bottle must turn a few more times to press it on. The turntable has a cam (`Spin_Pulse`), one pulse per turn:

```
Spin_Counter := CTU(CU := Spin_Pulse, RESET := NOT Step_Press, PV := PressTurns)
PRESS done   := Spin_Counter.Q            (CV ≥ PressTurns)
```

`RESET := NOT Step_Press` keeps the counter at 0 in every other step, so each bottle starts counting from zero. This counts **turns**, not time: it works at any spin speed (week VII; a timer would depend on the motor speed).

### 4. Glue temperature: hysteresis, outside the sequencer

The heater is not part of the bottle sequence: melting glue takes minutes, so it must not wait for Start. It runs in **every mode** (Stop, Auto, Manual) as long as `Master_Enable` is on and the machine is not in FAULT (decision 2026-11-15, like the blow moulder's oven, report 4.1):

```
Heat_Demand := set at GlueTemp < GlueSetpoint − GlueHyst, reset at GlueTemp ≥ GlueSetpoint (seal-in)
GlueHeater  := Heat_Demand AND NOT Step_Fault AND GlueTemp < GlueMax      (interlock)
```

Same two-threshold seal-in as the syrup room's `Temp_OK` (`docs/syrup/state-diagram.md` rule 2), here for heating. `Glue_Ready` is a separate, wider window: the heater switches inside a 5 °C band, labelling is allowed inside ±10 °C, so the heater's normal swing never stops labelling.

When the operator adds cold glue blocks, `GlueTemp` drops. If it leaves the window, no new bottle is admitted and the machine goes back to WARM_UP after the current bottle (never in the middle of a label). A bottle that reached the pocket while the glue left the window waits in WAIT_BOTTLE (10 → 20 needs `Glue_Ready`, decision 2026-11-17): otherwise WRAP would start with feed and glue roller blocked by their interlock and end in a label timeout FAULT.

### 5. Counter: labelled bottles

`Good_Counter` (CTU, week VII) → `GoodCount`: counts at the end of DISCHARGE (`Step_Discharge AND NOT Bottle_In_Pocket`); RESET by `Cmd = 4` (Reset counter). `Cmd = 4` is separate from Reset (`Cmd = 3`): Reset is pressed after every alarm, and that must never lose the count.

### 6. Stop: finish the label, then IDLE

`Stop_Request` (seal-in, cleared in IDLE) as in the other machines. A label that has started (glue on the bottle) must be finished, so **CLAMP → WRAP → PRESS → UNCLAMP run through without a stop**:

| Safe point | What happens |
| --- | --- |
| WARM_UP, WAIT_BOTTLE, WAIT_OUTFEED | Go to IDLE at once |
| End of UNCLAMP, DISCHARGE | Go to IDLE instead of the next step |
| CLAMP, WRAP, PRESS | No stop: continue to UNCLAMP first |

### 7. Resume after Stop, FAULT or E-stop: the sensors tell where we are

After Reset → IDLE → Start, the machine passes WARM_UP and WAIT_BOTTLE, which look at the pocket instead of remembering the old step:

| Pocket | Next step | Why |
| --- | --- | --- |
| empty (`NOT Bottle_In_Pocket`) | gate admits the next bottle | normal start |
| bottle, no label (`NOT Label_Present`) | CLAMP (once `Glue_Ready`) | label not started |
| bottle with label | DISCHARGE (or WAIT_OUTFEED) | finished label: **never labelled twice** |

**Known limitation** (no reject, decision 2026-11-15): an E-stop or FAULT in WRAP / PRESS leaves a half-wrapped label; `Label_Present` sees it, so the bottle is discharged onto C3 as it is. The operator takes it off by hand. With a reject station (future enhancement) the PLC would remember the broken-off label and reject the bottle instead.

### 8. Waiting is not a fault; Starved with a filter

| Situation | State | What the machine does |
| --- | --- | --- |
| WARM_UP | **Running** | Heating the glue; the HMI shows the step |
| WAIT_BOTTLE, no bottle at the gate for 2 s | **Starved** | C2 is empty: the filler is stopped or slow (report 5.5) |
| WAIT_BOTTLE, label roll empty | **Starved** | No bottle admitted; `Alm_LabelEmpty` warning, clears itself after the roll change |
| WAIT_OUTFEED | **Blocked** | Labelled bottle in the pocket, C3 full; `Alm_OutfeedBlocked` after 2 min |
| all other steps, or WAIT_BOTTLE with a short gap at the gate | **Running** | |

**Starved filter** (decision 2026-11-15): between two bottles `Bottle_At_Gate` is FALSE for a moment (the gate let one in, the next one on C2 has not moved up yet). Without a filter the HMI block would blink green / yellow at every bottle. So "no bottle" must last `StarvedDelay` (2 s):

```
Gate_Empty_Timer := TON(IN := NOT Bottle_At_Gate, PT := StarvedDelay)
Starved          := Step_Wait AND (Gate_Empty_Timer.Q OR Roll_Empty)
```

A normal gap is far shorter than 2 s, so the timer never finishes and the state stays green; when C2 really runs empty, it turns yellow after 2 s. Same idea as `Outfeed_Full` (a sensor must hold its state for a while before the PLC believes it), with one timer instead of two.

### 9. Interlocks (in Auto **and** Manual)

| Output | Allowed only when | Why |
| --- | --- | --- |
| GateMotor | `NOT Bottle_In_Pocket` (and `NOT Bottle_Entering` in the TP's input, rule 2: as an interlock it would cut the running pulse, which sets it) | Never two bottles in the pocket |
| ClampValve | `Bottle_In_Pocket` | Bell down only onto a bottle |
| SpinMotor | `Bell_Down` | A bottle that is not clamped falls over |
| LabelFeed, GlueRoller | `Bell_Down AND Glue_Ready` | Label only onto a clamped bottle, glue only when it sticks; both always together |
| Cutter | `Bell_Down` | Knife only while a label is on its way to a bottle |
| GlueHeater | `GlueTemp < GlueMax AND NOT Step_Fault` | No burning glue |
| DischargeMotor | `NOT Bell_Down AND NOT Outfeed_Full` | Bell up, room on C3 |
| ConveyorMotor2 | – | Always safe (accumulation conveyor) |

### 10. Manual mode (same as the filler)

Sequencer frozen, one hold-to-run button per actuator (`Man_*`), jog watchdog (`Jog_Block`), interlocks still apply (spin only with the bell down). The heater keeps working by itself (rule 4). Manual → Auto goes to IDLE; Start resumes by rule 7.

### 11. E-stop and FAULT

- **E-stop** (`E_Stop_OK` = FALSE): `Master_Enable` off, every output off at once (C2 and the heater too), step → IDLE; release + Start.
- **FAULT** (label timeout, pocket jam, glue over-temperature, heat-up timeout): step → 900, all off (heater too), alarm latched; Reset clears it only when the cause is gone (`Alm_GlueOverTemp`: only below `GlueMax`).
