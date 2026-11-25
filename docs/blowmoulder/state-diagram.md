# Blow moulder – State diagram

Draft 2026-11-25, reviewed the same day (all five open questions answered as proposed, section "Answers to the open questions"). Two stations as two sequencers in parallel: the **oven station** (`Step`) and the **mould station** (`MouldStep`); the IR lamps run outside both as a continuous P controller. Signal names from `io-list.md`; concept and answers in `progress.md` and at the end of `io-list.md`. Same building rules as the other machines: step variable + compare, Stop at safe points, waiting is not a fault, Auto OR Manual output rungs.

## Machine layout: two stations, one hand-over

```
 HOPPER        LOADER          OVEN POCKET                 TRANSFER ARM                MOULD                         C1
 Hopper_Empty  Preform_At_     Preform_In_Oven             Arm_At_Oven / Arm_At_Mould  Mould_Closed / Mould_Open     (filler drives)
 Preform_Low   Loader          OvenTemp, LampPower %QW0    ArmToMould, Gripper         Rod_Up / Rod_Down             C1_Full (S1)
   │ HopperFeed  │ Loader        │                                │                    BlowPressure_Raw              Bottle_Ejected
   ▼             ▼               ▼                                ▼                    Bottle_In_Mould
 [▼▼▼] ──────► [▼] ──────────► [ ▼ ≈≈≈ ] ═══ hand-over ═══► [ grip · swing · release ] ─► [ close·stretch·blow·exhaust·open ] ─Ejector─► [B][B][B]
       ◄────────── oven station: Step ──────────►             ◄─────────────────────── mould station: MouldStep ───────────────────────►
                     lamps: P controller, no sequencer
```

How one bottle is made:

1. **Feed and load:** the hopper feed brings a preform to the loader; when the oven pocket is empty the loader pushes it in.
2. **Heat:** the lamps heat the preform; the PLC waits until its surface is in the ready window, then a soak time so the heat gets through the wall (`Preform_Hot`).
3. **Hand-over:** the hot preform waits until the mould is open and empty. The **mould station** takes it: the arm grips it, swings it into the mould, releases it, swings back. The oven pocket is free and loads the next preform at once.
4. **Blow:** close the mould → rod down with pre-blow → high blow up to `BlowSetpoint` → hold → exhaust until the pressure is safe → rod up → open.
5. **Eject:** the ejector pushes the bottle onto C1; `Bottle_Ejected` counts it.

Why two sequencers (as the packer): a step word can be in one step only. While the mould blows bottle N, the oven heats preform N+1; both must make progress. They talk to each other **only at the hand-over** (rule 2), and the arm belongs to the mould station alone, so no output has two owners.

## Diagram: oven station (`Step`, %MW51)

```
            Auto + Start, no FAULT alarm, Step = 0 AND MouldStep = 0   (also sets MouldStep, rule 2)
  ┌────────┐ ──────────────────────────────────────────────► 10, or 20 if Preform_In_Oven (restart, rule 9)
  │ 0 IDLE │
  └────────┘
      ▲   Stop requested                  ┌──────────────────────────────────────────────────┐
      ├───────────────────────────────────│ 10 FEED  HopperFeed while NOT Preform_At_Loader  │◄──────────────────┐
      │                                   └────────────────────────┬─────────────────────────┘                   │
      │                                                            │ Preform_At_Loader AND NOT Preform_In_Oven   │
      │                                                            │ AND NOT Outfeed_Full AND NOT Stop_Request   │
      │                                                            ▼                                             │
      │                                   ┌──────────────────────────────────────────────────┐                   │
      │                                   │ 15 LOAD  Loader                                  │                   │
      │                                   └────────────────────────┬─────────────────────────┘                   │
      │                                                            │ Preform_In_Oven                             │
      │                                                            ▼                                             │
      │                                   ┌──────────────────────────────────────────────────┐                   │
      │                                   │ 20 HEAT  (lamps on: Lamps_On, rule 5)            │                   │
      │                                   │   Soak_Timer while Oven_Ready                    │                   │
      │                                   └────────────────────────┬─────────────────────────┘                   │
      │                                                            │ Preform_Hot                                 │
      │                                                            ▼                                             │
      │                                   ┌──────────────────────────────────────────────────┐                   │
      │   Stop: never here, the preform   │ 25 HOT  (waits for the mould; lamps hold it)     │                   │
      │   is finished first (Q5)          └────────────────────────┬─────────────────────────┘                   │
      │                                                            │ NOT Preform_In_Oven (the arm took it)       │
      │                                                            └─────────────────────────────────────────────┘
```

## Diagram: mould station (`MouldStep`, %MW52)

```
            set by the Start: 10, 60 if Mould_Closed, 80 if Mould_Open AND Bottle_In_Mould (rule 9)
  ┌────────┐ ─────────────────────► ┌────────────────────────────────────────────────────────┐
  │ 0 IDLE │                        │ 10 WAIT_PREFORM  (mould open and empty, arm home)      │◄──────────────────────┐
  └────────┘                        └───────────────────────────┬────────────────────────────┘                       │
      ▲   Stop requested AND Step = 0                           │ Step = 25 AND Mould_Open AND NOT Bottle_In_Mould   │
      ├─────────────────────────────────────────────────────────┤ AND Arm_At_Oven AND Rod_Up                         │
      │                                                         ▼                                                    │
      │                     20 GRIP      Gripper                         ── Grip_Timer.Q (GripTime) ──►               │
      │                     22 SWING     Gripper, ArmToMould             ── Arm_At_Mould ──►                          │
      │                     24 RELEASE   ArmToMould                      ── Grip_Timer.Q AND Bottle_In_Mould ──►      │
      │                     26 ARM_BACK  –                               ── Arm_At_Oven ──►                           │
      │                     30 CLOSE     MouldClose                      ── Mould_Closed ──►                          │
      │                     40 STRETCH   MouldClose, StretchRod, PreBlowValve        ── Rod_Down ──►                  │
      │                     50 BLOW      MouldClose, StretchRod, HighBlowValve       ── Pressure_Reached ──►          │
      │                     52 HOLD      MouldClose, StretchRod, HighBlowValve       ── Hold_Timer.Q (HoldTime) ──►   │
      │                     60 EXHAUST   MouldClose, StretchRod, ExhaustValve        ── Pressure_Safe ──►             │
      │                     62 ROD_UP    MouldClose, ExhaustValve        ── Rod_Up ──►                                │
      │                     70 OPEN      ExhaustValve                    ── Mould_Open ──►                            │
      │                     80 EJECT     Ejector while NOT Outfeed_Full  ── NOT Bottle_In_Mould ──────────────────────┘
      │
      └── Stop: the cycle is always finished to 10; IDLE from 10 when the oven station is IDLE too (rule 7)

  From ANY step of EITHER station:  handling jam / mould fault / heat-up timeout / blow fault / exhaust fault /
                                    over-pressure / pressure sensor / oven over-temperature
                       ─► Step := 900 AND MouldStep := 900 (alarm latched) ─► Auto + Reset (cause gone) ─► both 0 IDLE
  E_Stop_OK = FALSE or Mode Stop ─► both steps → 0 IDLE; exhaust open; a closed mould stays closed (rule 4)
```

## Step tables

**Oven station (`Step`)**

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | Auto AND Start AND no FAULT alarm AND `MouldStep` = 0 → 10, or 20 if `Preform_In_Oven` (and `MouldStep` := …, rule 2) | – |
| 10 | FEED | HopperFeed while `NOT Preform_At_Loader` | `Preform_At_Loader AND NOT Preform_In_Oven AND NOT Outfeed_Full AND NOT Stop_Request` → 15 | none (no preform = **Starved**, C1 full = **Blocked**) |
| 15 | LOAD | Loader | `Preform_In_Oven` → 20 | 3 s (`Alm_HandlingJam`) |
| 20 | HEAT | – (lamps by rule 5) | `Preform_Hot` → 25 | `HeatTimeout` 15 s (`Alm_OvenHeatTimeout`) |
| 25 | HOT | – (lamps hold the temperature, Q4) | `NOT Preform_In_Oven` → 10 | none (waiting for the own mould is **Running**) |
| 900 | FAULT | – | Auto + Reset (cause gone) → 0 | – |

**Mould station (`MouldStep`)**

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | set by the Start (rule 2) | – |
| 10 | WAIT_PREFORM | – | `Step = 25 AND Mould_Open AND NOT Bottle_In_Mould AND Arm_At_Oven AND Rod_Up` → 20 | none |
| 20 | GRIP | Gripper | `Grip_Timer.Q` (`GripTime` 0.3 s) → 22 | – |
| 22 | SWING | Gripper, ArmToMould | `Arm_At_Mould` → 24 | 3 s (`Alm_HandlingJam`) |
| 24 | RELEASE | ArmToMould | `Grip_Timer.Q AND Bottle_In_Mould` → 26 | 3 s (`Alm_HandlingJam`, preform not in the mould) |
| 26 | ARM_BACK | – (arm returns when `ArmToMould` drops) | `Arm_At_Oven` → 30 | 3 s (`Alm_HandlingJam`) |
| 30 | CLOSE | MouldClose | `Mould_Closed` → 40 | 3 s (`Alm_MouldFault`) |
| 40 | STRETCH | MouldClose, StretchRod, PreBlowValve | `Rod_Down` → 50 | 3 s (`Alm_MouldFault`) |
| 50 | BLOW | MouldClose, StretchRod, HighBlowValve | `Pressure_Reached` → 52 | `BlowTimeout` 2 s (`Alm_BlowFault`) |
| 52 | HOLD | MouldClose, StretchRod, HighBlowValve | `Hold_Timer.Q` (`HoldTime` 1.0 s) → 60 | – |
| 60 | EXHAUST | MouldClose, StretchRod, ExhaustValve | `Pressure_Safe` → 62 | `ExhaustTimeout` 3 s (`Alm_ExhaustFault`) |
| 62 | ROD_UP | MouldClose, ExhaustValve | `Rod_Up` → 70 | 3 s (`Alm_MouldFault`) |
| 70 | OPEN | ExhaustValve | `Mould_Open` → 80 | 3 s (`Alm_MouldFault`) |
| 80 | EJECT | Ejector while `NOT Outfeed_Full` | `NOT Bottle_In_Mould` → 10 | 3 s while the ejector is ON (`Alm_HandlingJam`); `Outfeed_Full` = **Blocked** |
| 900 | FAULT | – (ExhaustValve open, closed mould held, rule 4) | Auto + Reset (cause gone) → 0 | – |

`GripTime` is one timer for both steps (`Grip_Timer := TON(IN := Mould_Grip OR Mould_Release, PT := GripTime)`): SWING lies between GRIP and RELEASE, so the timer is reset there and starts again from 0 in RELEASE.

**Computed values** (every scan):

| Name | Formula | Syllabus |
| --- | --- | --- |
| `OvenError` | `OvenSetpoint − OvenTemp` (SUB) | VIII math |
| `LampPower` | rule 5: `LIMIT(0, Bias + Kp · LIMIT(−ErrMax, OvenError, ErrMax) / 10, 100)` while `Lamps_On`, else 0 | VIII math (MUL, DIV, ADD, LIMIT), **analog output** |
| `BlowPressure` | rule 6: `DINT_TO_INT(INT_TO_DINT(BlowPressure_Raw) · PressFull / RawFull)` | VIII math, X data types (INT ↔ DINT) |
| `Oven_Ready` | `OvenTemp ≥ OvenSetpoint − OvenBand AND OvenTemp ≤ OvenSetpoint + OvenBand` | VIII window compare |
| `Pressure_Reached` | `BlowPressure ≥ BlowSetpoint` (GE) | VIII compare |
| `Pressure_Safe` | `BlowPressure < ExhaustLimit AND NOT Alm_PressureSensor` | VIII compare |
| `Preform_Hot` | `Soak_Timer := TON(IN := Step_Heat AND Oven_Ready, PT := SoakTime)` | VII timers |

**Fixed constants** (internal variables with initial values, tuned with the simulator):

| Name | Value | Meaning |
| --- | --- | --- |
| `OvenMax` | 1300 (130.0 °C) | Over-temperature: PET starts to crystallise (turns white) |
| `ErrMax` | 1000 (100 °C) | `LIMIT` on `OvenError` before the MUL, so `Kp · error` stays inside INT (30 · 1000 = 30000 < 32767) |
| `RawFull`, `PressFull` | 27648, 400 | Scaling: 27648 counts = 40.0 bar |
| `RawMin`, `RawMax` | −1000, 32511 | Outside = `Alm_PressureSensor` (wire break / over range) |
| `ExhaustLimit` | 5 (0.5 bar) | Below this the mould may open |
| `MaxPressure` | 400 (40.0 bar) | Over-pressure |
| `SoakTime` | 1 s | Preform inside `Oven_Ready` this long = heated through |
| `GripTime` | 0.3 s | Gripper closes / opens |
| `HoldTime` | 1.0 s | High pressure held: the PET cools against the mould walls |
| `MoveTimeout` | 3 s | Loader, arm, mould, rod, ejector must reach their sensor within this time |
| `HeatTimeout` | 15 s | HEAT must reach `Preform_Hot` within this time (lamps failed) |
| `BlowTimeout` | 2 s | BLOW must reach `BlowSetpoint` |
| `ExhaustTimeout` | 3 s | EXHAUST must reach `Pressure_Safe` |
| `StarvedDelay` | 2 s | No preform at the loader this long = Starved |
| `BlockedWarn` | 2 min | `Alm_OutfeedBlocked` (same as every machine) |

Setpoint defaults: `OvenSetpoint` 1050 (105 °C), `OvenBand` 50 (ready window 100–110 °C), `Kp` 10 %/°C, `Bias` 0 %, `BlowSetpoint` 350 (35 bar). Cycle (simulator target): GRIP 0.3 + SWING 0.5 + RELEASE 0.3 + ARM_BACK 0.5 + CLOSE 0.5 + STRETCH 0.4 + BLOW 0.3 + HOLD 1.0 + EXHAUST 0.5 + ROD_UP 0.3 + OPEN 0.5 + EJECT 0.3 ≈ **5.4 s per bottle**; the oven has ~5 s for load + heat + soak meanwhile.

## Rules

The ladder sketches below are for understanding only; the real rungs are written in the OpenPLC Editor.

### 1. Steps decide the outputs (same as the other machines)

Two step words with their own transition rungs (`EQ Step,x AND … → MOVE y → Step`, `EQ MouldStep,x AND … → MOVE y → MouldStep`) and step bits (`Oven_Feed`, `Oven_Load`, `Step_Heat`, `Oven_Hot`, `Mould_WaitPreform`, `Mould_Grip`, …). Output requests:

```
HopperFeed     := Oven_Feed AND NOT Preform_At_Loader
Loader         := Oven_Load
Gripper        := Mould_Grip OR Mould_Swing
ArmToMould     := Mould_Swing OR Mould_Release
StretchRod     := Mould_Stretch OR Mould_Blow OR Mould_Hold OR Mould_Exhaust
PreBlowValve   := Mould_Stretch
HighBlowValve  := Mould_Blow OR Mould_Hold
Ejector        := Mould_Eject AND NOT Outfeed_Full
MouldClose     := rule 4 (seal-in, not a plain request)
ExhaustValve   := rule 3 (normally open)
```

Loader, arm, rod and ejector are **spring-return** (single-acting cylinders): they go home when their output drops, as the packer's pusher.

### 2. Two stations, one machine

- **Start** (Auto, no FAULT alarm, both steps 0) sets both: `Step` := 10 (20 with a preform in the pocket), `MouldStep` := 10 / 60 / 80 by rule 9.
- **Hand-over:** the mould station starts a transfer (10 → 20) only while the oven station holds a hot preform (`Step = 25`) and the mould is open, empty, rod up, arm home. The oven station sees the preform leave its pocket (`NOT Preform_In_Oven`, in SWING) and goes back to FEED. No extra bit is passed: each station reads the other's step word and its own sensors (the packer's pattern).
- **FAULT** of either moves **both** to 900; Reset moves both to 0.

### 3. Exhaust: open unless blowing (fail-safe)

```
ExhaustValve := NOT ((Mould_Stretch OR Mould_Blow OR Mould_Hold) AND Master_Enable)
```

The exhaust is closed only in the three steps that build pressure; in **every other case** – IDLE, Manual, Mode Stop, FAULT, E-stop, comm lost – it is open (answer 2). This is how a real machine uses a **normally-open** exhaust valve: lose the PLC, lose the air, and the bottle vents by itself. Because this rung does **not** go through `Master_Enable` like every other output, it is the one output ON in a safe state. High blow and exhaust are never open together (interlock, rule 10).

### 4. The mould never opens under pressure: a seal-in on the output

```
Close_Cmd  := Master_Enable AND ((Mode_Auto AND Mould_CloseSteps) OR (Mode_Manual AND Man_Mould_Closed AND Arm_At_Oven AND NOT Ejector))
Open_Cmd   := Master_Enable AND ((Mode_Auto AND NOT Mould_CloseSteps AND NOT Step_Fault AND NOT Step_Idle) OR (Mode_Manual AND NOT Man_Mould_Closed))
Open_OK    := Open_Cmd AND Pressure_Safe AND Rod_Up
MouldClose := (Close_Cmd OR (MouldClose AND Mould_Closed)) AND NOT Open_OK        (seal-in, week IV)
```

`Mould_CloseSteps` = CLOSE … ROD_UP (30–62). The output **holds itself** once the mould is fully closed and only lets go when someone asks to open **and** it is safe. Consequences:
- **Auto:** opens in OPEN (70), and only with the pressure gone and the rod up.
- **FAULT, Mode Stop, E-stop, comm lost:** nobody asks to open → a closed mould **stays closed** (no motion in a safe state, pressure may be inside; the exhaust is open, rule 3). A mould that was still **closing** (not yet `Mould_Closed`) opens again: back to the safe position, it cannot hold pressure yet.
- **Manual:** the toggle (rule 11) asks; the same `Open_OK` applies.

Like the exhaust (rule 3), the holding part of this rung does **not** go through `Master_Enable` (or the motor/valve FB's `Enable`): only `Close_Cmd` and `Open_Cmd` contain it, so an E-stop can neither close nor open the mould.

### 5. Oven lamps: P controller on an analog output (only in Auto, only with a preform)

```
Lamps_On  := Master_Enable AND Mode_Auto AND NOT Step_Fault AND Preform_In_Oven
OvenError := OvenSetpoint − OvenTemp
P_Term    := Kp · LIMIT(−ErrMax, OvenError, ErrMax) / 10          (Kp in % per °C, error in 0.1 °C)
LampPower := LIMIT(0, Bias + P_Term, 100)   if Lamps_On AND OvenTemp < OvenMax,   else 0      → %QW0
```

- **Proportional:** cold preform (error 85 °C) → 850 % → clamped to 100 %; near the setpoint the power falls with the error. A too-hot preform gives a negative term → 0 %.
- **Steady offset** (Q2): to hold 105 °C the lamps need some power `P_eq`; with `Bias` = 0 that power can only come from an error, so the temperature settles `(P_eq − Bias) · 10 / Kp` below the setpoint (`P_eq` = 30 %, `Kp` = 10 → 3 °C). The trend of `OvenError` shows it; `Bias` ≈ `P_eq` removes it. The offset must stay inside `OvenBand`, else HEAT never ends (`Alm_OvenHeatTimeout`): a teaching point, not a bug.
- **Why `LIMIT` before the MUL:** INT holds ±32767; `Kp` 30 · error 2000 would overflow. Clamping the error to ±100 °C first keeps the product ≤ 30000.
- **Only with a preform** in the pocket and **only in Auto** (as the packer's oven): an empty pocket needs no heat; a preform left in Manual cools and is heated again after the next Start (rule 9).
- **`OvenMax`** cuts the power in every mode and latches `Alm_OvenOverTemp`.

### 6. Blow pressure: raw counts scaled in DINT

```
BlowPressure := DINT_TO_INT( INT_TO_DINT(BlowPressure_Raw) * 400 / 27648 )      (0.1 bar)
```

- An analog input card delivers counts, not bar: 0 counts = 4 mA = 0 bar, 27648 = 20 mA = 40 bar (the S7 convention).
- `27648 · 400 = 11 059 200` does not fit an INT: the value is converted to **DINT**, multiplied, divided, converted back. Dividing first (`Raw / 27648 · 400`) would give 0 for every pressure below 40 bar (INT truncation) – the order matters.
- **Wire break:** a 4–20 mA signal never reaches 0 mA when healthy ("live zero"), so a broken wire gives negative counts. `Raw < RawMin` or `> RawMax` → `Alm_PressureSensor` → FAULT; `Pressure_Safe` is FALSE while it is set, so a broken sensor can never open the mould (answer 3).

### 7. Stop: finish what was started, then IDLE (Q5)

`Stop_Request` (seal-in, cleared when both steps are 0), as the other machines.

| Station | Safe point → IDLE | Finished first |
| --- | --- | --- |
| Oven | FEED (no new preform loaded: `NOT Stop_Request` in 10 → 15) | LOAD → HEAT → HOT → handed to the mould → FEED → IDLE |
| Mould | WAIT_PREFORM, **only when the oven station is already IDLE** (no hot preform left for it) | every step of the cycle up to EJECT |

So Stop always ends with an **empty oven and an empty mould**: the last preform is blown and ejected. **Mode Stop** and **E-stop** abort both at once (steps → 0); the mould keeps its position (rule 4).

### 8. Waiting is not a fault; one State for two stations

| Priority | Condition | State |
| --- | --- | --- |
| 1 | `Step = 900` | **9 Fault** |
| 2 | `Step = 0 AND MouldStep = 0` | **0 Stopped** |
| 3 | `Outfeed_Full AND (Mould_Eject OR Oven_Feed)` | **3 Blocked** (`Alm_OutfeedBlocked` after 2 min) |
| 4 | `Starved` | **2 Starved** |
| 5 | otherwise | **1 Running** |

```
Feed_Empty_Timer := TON(IN := Oven_Feed AND NOT Preform_At_Loader, PT := StarvedDelay)
Starved          := Feed_Empty_Timer.Q AND Mould_WaitPreform
Alm_NoPreforms   := Oven_Feed AND Hopper_Empty AND NOT Preform_At_Loader          (warning, not latched, answer 1)
```

- **C1 full (report 5.3):** the oven loads no new preform; the preform already in the oven is heated and handed over, its bottle is blown, and **waits in the open mould** (EJECT, ejector held off) until C1 has room. The machine is Blocked with one bottle in the mould and the oven empty; lamps off (no preform). When `Outfeed_Full` releases, the ejector pushes and the cycle runs on by itself.
- **Starved** only when the mould has nothing to do either: a missing preform while a bottle is still being blown is not Starved.

### 9. Resume after Stop, FAULT or E-stop: sensors tell where we are

| Situation at the restart | Start sets | Known by |
| --- | --- | --- |
| preform in the oven pocket (cold after Manual, or warm) | `Step` := 20 HEAT (heated again to `Preform_Hot`) | `Preform_In_Oven` |
| mould closed (FAULT or E-stop during a blow) | `MouldStep` := 60 EXHAUST → rod up → open → eject | `Mould_Closed` |
| mould open, something in it | `MouldStep` := 80 EJECT | `Mould_Open AND Bottle_In_Mould` |
| otherwise | `MouldStep` := 10 | – |

**Known limitations:** a half-blown bottle or an unblown preform found in the mould is ejected onto C1 (no reject flap, decision Q4); the simulator flags it as a bad bottle. A preform the gripper dropped during an E-stop must be taken out by hand (`Bottle_In_Mould` or `Preform_In_Oven` show where it is, if it fell into either).

### 10. Interlocks (in Auto **and** Manual)

| Output | Allowed only when | Why |
| --- | --- | --- |
| HopperFeed | – | Always safe (chute) |
| Loader | `NOT Preform_In_Oven AND NOT Gripper` | Never two preforms in the pocket; never against the closed gripper |
| ArmToMould | `Mould_Open AND Rod_Up AND NOT Ejector` | The arm only enters an open mould with the rod and ejector out of the way |
| Gripper | – | – |
| MouldClose (close) | `Arm_At_Oven AND NOT Ejector` | Never close on the arm or the ejector |
| MouldClose (open) | `Pressure_Safe AND Rod_Up` (rule 4) | **Never open under pressure** or onto the rod |
| StretchRod | `Mould_Closed` | The rod only moves inside a closed mould |
| PreBlowValve | `Mould_Closed` | No blowing into an open mould |
| HighBlowValve | `Mould_Closed AND NOT ExhaustValve AND BlowPressure < MaxPressure` | Closed mould, exhaust shut, never above the limit |
| ExhaustValve | – (always allowed) | Venting is always safe (rule 3) |
| Ejector | `Mould_Open AND Arm_At_Oven AND NOT Outfeed_Full` | Only out of an open mould, only onto a C1 with room |
| LampPower | `Lamps_On AND OvenTemp < OvenMax` (rule 5) | No over-heating, only in Auto, only with a preform |

### 11. Manual mode

Both sequencers frozen, hold-to-run buttons (`Man_*`), jog watchdog (`Jog_Block`), interlocks still apply. Lamps off (Auto only), **no blowing** (no button for the blow valves), exhaust open.

**Mould = toggle** (answer 4, as the labeler's bell): a click on *Mould* (rising edge of `Man_MouldClose`) flips the latch `Man_Mould_Closed`, the next click asks to open. On **entering Manual** the latch is loaded with `Mould_Closed`, so changing the mode never moves the mould. The jog watchdog does not touch the latch (the mould is a position, not a motion). The rod (hold-to-run) needs the mould closed, which only a toggle makes possible with one mouse.

### 12. E-stop and FAULT

- **E-stop** (`E_Stop_OK` = FALSE): `Master_Enable` off, every output off at once, **except**: the exhaust opens (rule 3) and a fully closed mould stays closed (rule 4). Both steps → IDLE; release + Start resumes by rule 9.
- **FAULT** (handling jam, mould fault, heat-up timeout, blow fault, exhaust fault, over-pressure, pressure sensor, oven over-temperature in every mode): both steps → 900, all off except the exhaust (open) and a closed mould (held), alarm latched. Auto + Reset clears it only when the cause is gone: `Alm_OvenOverTemp` below `OvenMax`, `Alm_OverPressure` below `MaxPressure`, `Alm_PressureSensor` with the raw value back in range, `Alm_ExhaustFault` with `Pressure_Safe`; the others can always be reset. After Reset the mould opens only through the restart (rule 9: EXHAUST → ROD_UP → OPEN) or by hand in Manual.
- `BottleCount` (CTU `Bottle_Counter`, CU := `Bottle_Ejected`, RESET := `Count_Reset_Cmd`) counts bottles put onto C1; `Cmd = 4` zeroes it, Reset (`Cmd = 3`) never does.

## Answers to the open questions (2026-11-25, all as proposed)

1. **C1 full:** the last blown bottle waits in the open mould (EJECT, ejector held off), never pushed onto a full C1.
2. **Exhaust normally open:** ON in IDLE and Manual too; closed only in STRETCH, BLOW, HOLD.
3. **Lamps only with a preform in the pocket:** no WARM_UP step; the first preform heats from cold in HEAT (≤ 15 s).
4. **Restart with something in the mould:** ejected onto C1 (no reject); the simulator flags it.
5. **E-stop while the mould closes:** a closing mould opens again, a fully closed one stays closed.
