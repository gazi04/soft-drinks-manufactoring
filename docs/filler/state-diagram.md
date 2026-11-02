# Filler machine – State diagram

Draft, 2026-10-27. Signal names from `io-list.md` (working names; final descriptive names are chosen at implementation).

## Machine layout: an indexing machine

The machine has three stations (rinse, fill, cap) on one internal belt (`IndexMotor`). Several bottles are inside at the same time, one per station. Because all bottles sit on the same belt, they all move together, so the machine works in a fixed rhythm:

1. **WORK:** every station that has a bottle does its job, all at the same time.
2. **INDEX:** when all stations are done, the belt moves exactly one station forward. Every bottle moves one place, the bottle at the cap station leaves onto C2, and the door lets the next bottle in.

```
 C1 infeed (ConveyerMotor1,          Filler machine (IndexMotor belt)            C2 (driven by labeler)
 runs continuously)
 ─────────────────────► [DOOR] ─► [ RINSE ] ─► [ FILL ] ─► [  CAP  ] ─► out ─►────────────────────────►
                          S2         S3           S4          S5        S6                  S7 (full)
                                     S8 = index sensor (cam on the belt, ON once per station pitch)

 S1 (C1 full) is wired to the blow moulder, not to the filler.
```

C1 runs continuously (also in Stop mode), so bottles queue up against the closed door. It stops only on E-stop or FAULT.

## Diagram

Steps are numbered so a step can be inserted later without renumbering.

```
               Auto + Start, no alarm
   ┌────────┐ ─────────────────────────► ┌──────────────────────────────────────────┐
   │ 0 IDLE │                            │ 10 WORK  (station jobs run in parallel)  │◄──────────┐
   └────────┘ ◄───────────────────────── │   Rinse:  if S3 → RinseValve, TON        │           │
       ▲  ▲     Stop requested and       │   Fill:   if S4 → FillValve until level  │           │
       │  │     all jobs done            │           (only while Product_Available) │           │
       │  │                              │   Cap:    if S5 → CapMotor, TON          │           │
       │  │                              │ Nothing to move (no S2..S5) = Starved    │           │
       │  │                              └───────────────┬──────────────────────────┘           │
       │  │                                              │ all jobs done AND something to move  │
       │  │                       S5 AND Outfeed_Full    │                                      │
       │  │                  ┌───────────────────────────┤                                      │
       │  │                  ▼                           │ otherwise                            │
       │  │      ┌────────────────────────┐              │                                      │
       │  └──────│ 15 WAIT_OUTFEED        │              │                                      │
       │  Stop   │ all off (Blocked)      │              │                                      │
       │         └───────────┬────────────┘              │                                      │
       │                     │ Outfeed_Full released     │                                      │
       │                     ▼                           ▼                                      │
       │         ┌────────────────────────────────────────────────────┐                         │
       │         │ 20 INDEX                                           │                         │
       │         │   IndexMotor ON                                    │                         │
       │         │   DoorMotor: TP pulse at step start if S2          │                         │
       │         │   (admits exactly one bottle)                      │                         │
       │         │   S6 rising edge → good-bottle counter +1          │                         │
       │         └───────────────────────┬────────────────────────────┘                         │
       │                                 │ S8 rising edge (belt moved one station)              │
       │       Stop requested            │                                                      │
       └─────────────────────────────────┴──────────────────────────────────────────────────────┘
                                              otherwise

   From ANY step:  timeout (index jam / no level rise) ─► 900 FAULT (all off, alarm latched) ─► Reset ─► 0 IDLE
                   E_Stop_OK = FALSE                   ─► master enable off, all off          ─► Reset ─► 0 IDLE
```

## Step table

| Step | Name | Outputs ON | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – (C1 keeps running unless E-stop/FAULT) | Auto AND Start AND no alarm | – |
| 10 | WORK | Station jobs (table below) | All jobs done AND (S2 OR S3 OR S4 OR S5) → 15 if S5 AND `Outfeed_Full`, else 20 | Fill: 10 s, counted only while FillValve is ON |
| 15 | WAIT_OUTFEED | – | `Outfeed_Full` released → 20 | none (waiting = **Blocked**); alarm after 2 min |
| 20 | INDEX | IndexMotor; DoorMotor as TP pulse (e.g. 0.5 s) at step start if S2 | S8 rising edge → 10 | 5 s (index jam) |
| 900 | FAULT | – | Reset | – |

## Station jobs inside step 10 (run in parallel)

| Station | Job runs if | Output | Job done when |
| --- | --- | --- | --- |
| Rinse | S3 (bottle present) | RinseValve | TON rinse time elapsed, **or** no bottle (NOT S3) |
| Fill | S4 (bottle present) | FillValve, only while `Product_Available` | FillLevel ≥ fill setpoint, **or** no bottle (NOT S4) |
| Cap | S5 (bottle present) | CapMotor | TON cap time elapsed, **or** no bottle (NOT S5) |

An empty station counts as done immediately. In ladder, each job is a small combinational block, for example:

```
RinseValve := (Step = 10) AND S3 AND NOT Rinse_Done
Rinse_Done := NOT S3 OR TON_Rinse.Q          (TON_Rinse.IN := (Step = 10) AND S3)
```

## Rules

The ladder sketches below are for understanding only; the real rungs are written in the OpenPLC Editor.

### 1. How the program is built: steps decide the outputs

The variable `Step` (%MW51) always holds the number of the current step (0, 10, 15, 20 or 900). The ladder has two kinds of rungs:

- **Transition rungs** change the step. "If we are in step X and condition Y is true, move the new step number into `Step`."

  ```
    EQ Step,20      S8 rising edge                   MOVE 10 → Step
   ─┤    ├──────────┤P├──────────────────────────────[ MOVE ]──
  ```

- **Output rungs** switch an output by comparing the step. "The belt runs in step 20, and only there."

  ```
    EQ Step,20                                        IndexMotor
   ─┤    ├────────────────────────────────────────────( )──
  ```

So no output needs a long, complicated condition. Each output only asks "which step are we in?". IEC has no sequencer instruction (like Delta's SQO), so this step variable + compare **is** the sequencer (syllabus week XI).

The station jobs in WORK add a few contacts in series (e.g. `Step = 10 AND S3 AND NOT Rinse_Done`). That part is plain AND/OR logic, which is **combinational**; the step part is **sequential**. The course asks to show both, clearly separated.

### 2. Stop: remembered, then done at a safe point

The Stop button is only a short signal: the HMI writes `Cmd = 2` for a moment and then it is gone. But the machine may be busy at that moment, for example the fill valve is open over a half-full bottle. Stopping right there would leave a half-processed bottle.

So the PLC **remembers** the press in a memory bit, `Stop_Request`. This is a latch (seal-in), the same idea as a motor start/stop circuit:

```
Stop_Request := (Cmd = 2 OR Stop_Request) AND NOT (Step = 0)
```

- `Cmd = 2` sets the bit.
- The bit holds itself (the `OR Stop_Request` branch), even after the button signal is gone.
- It clears itself once the machine has reached IDLE (step 0).

The sequencer looks at `Stop_Request` only at **safe points**, where no station job is running and the belt is standing still:

| Safe point | What happens |
| --- | --- |
| End of INDEX (S8 reached) | Go to IDLE instead of WORK |
| WORK, all jobs done | Go to IDLE instead of INDEX |
| WAIT_OUTFEED | Go to IDLE instead of waiting further |

Bottles can stay inside the machine, but none is half-processed. On the next Start, the machine goes to WORK and continues where it stopped.

**Stop is not the E-stop.** Stop is a "polite" stop that waits for a safe point. The E-stop (`E_Stop_OK` = FALSE) switches everything off **immediately**, without waiting.

### 3. WAIT_OUTFEED: no room on C2, so wait

WAIT_OUTFEED (step 15) means "wait until the output conveyor has room".

When WORK is finished and a capped bottle stands at the cap station (S5), the next INDEX would push that bottle out onto C2. If C2 is full (`Outfeed_Full`), there is no room for it, so the machine does not index. It goes to step 15:

- all outputs off,
- state = **Blocked**,
- when C2 has room again → INDEX → normal work continues.

If there is **no** bottle at the cap station, nothing leaves the machine, so it may index even with C2 full. That is why the condition is `S5 AND Outfeed_Full`, not only `Outfeed_Full`.

### 4. Waiting is not a fault

A machine that waits for bottles, product or room is working correctly; it is not broken. So waiting only changes the **state** shown on the HMI. It does not go to FAULT and does not need a Reset.

| Situation | State | What the machine does |
| --- | --- | --- |
| WORK, no bottle at the door or in any station (no S2..S5) | **Starved** | Waits for bottles |
| WORK, bottle at the fill station but `Product_Available` = FALSE | **Starved** | Fill valve stays closed; the fill timeout does **not** count, so no false alarm |
| WAIT_OUTFEED | **Blocked** | Waits for room on C2 |

Only one waiting situation raises an alarm: Blocked for more than **2 minutes** gives the alarm *outfeed blocked too long*. It is a message for the operator ("look at the labeler"); the machine still keeps waiting and continues by itself when C2 has room.

### 5. Outfeed_Full: S7 with a delay, so it does not flicker

S7 sees bottles passing by on C2, so it switches ON and OFF all the time, even when C2 is not full. If the PLC used S7 directly, the machine would start and stop many times per second.

`Outfeed_Full` is a cleaned-up version of S7 (time-based hysteresis):

- it becomes TRUE only when S7 has been ON for **2 s** without a break (bottles really stand still),
- it becomes FALSE only when S7 has been OFF for **3 s** (real room on C2).

```
Full_OnTimer  := TON(IN := S7,     PT := 2 s)
Full_OffTimer := TON(IN := NOT S7, PT := 3 s)
Outfeed_Full  := (Full_OnTimer.Q OR Outfeed_Full) AND NOT Full_OffTimer.Q
```

The flicker is not electrical noise: S7 works correctly, every pulse really is a bottle passing. It is *process noise*, normal movement that answers the wrong question ("is a bottle in front of S7?" instead of "is C2 full?"). Only a bottle **standing still** keeps S7 ON for 2 s without a break. The seal-in branch (`Outfeed_Full` in parallel with `Full_OnTimer.Q`) is the memory: it keeps the signal TRUE through short gaps until the off-timer releases it.

See report section 5.3.

### 6. Door: opened by a short pulse (TP), one bottle per index

The door opens only when a bottle is waiting (S2), at the start of INDEX, when the belt brings an empty pocket to the entry.

The extra question is **how long** the door stays open. C1 runs all the time, so the bottles in the queue touch each other:

```
  [b4][b3][b2][b1] | DOOR
                  S2
```

If the door stayed open "while S2 is ON", b1 would go in and b2 would immediately take its place at S2. S2 would never switch off, the door would never close, and b2, b3 and b4 would all flow in. But there is only **one** pocket per index.

So the door gets a **TP** (pulse timer). A TP gives an output pulse of a fixed length (e.g. 0.5 s), even if its input stays ON. 0.5 s is long enough for one bottle to pass and too short for two.

```
  EQ Step,20    S2                 TP_Door (PT = 500 ms)          DoorMotor
 ─┤    ├────────┤ ├────────────────[ TP ]─────────────────────────( )──
```

- **When** the door opens: start of INDEX AND S2.
- **How long** it opens: the TP pulse.

### 7. Resume after a FAULT

A FAULT (index jam or fill timeout) switches everything off, but the bottles stay where they are. After the operator fixes the problem, presses Reset (→ IDLE) and then Start, the machine goes to WORK.

WORK does not assume anything: it looks at the sensors (S3, S4, S5) and does the job for every bottle it finds. A bottle that was half-filled is simply filled up until `FillLevel` reaches the setpoint.

### 8. Manual mode

The machine has three modes (`ModeReq`, %MW1):

| Mode | Who controls the outputs |
| --- | --- |
| Stop | Nobody, everything off (C1 keeps running) |
| Auto | The sequencer (the steps above) |
| **Manual** | **The operator, one actuator at a time, from HMI buttons** |

Manual mode is for maintenance, setup and testing, for example "open the rinse valve to check that water comes out", "move the belt a little to remove a stuck bottle" or "test the cap head".

- **The sequencer is frozen.** The transition rungs get an extra contact `Mode = Auto`, so in Manual the step never changes.
- **One button per actuator.** Each HMI button is its own `%MX` bit (`Man_Rinse`, `Man_Fill`, …, see `io-list.md`) and the PLC switches that output.
- **Hold-to-run (jog).** The output is ON only while the button is held. When the operator lets go, it stops. This is safer than a toggle button that could be forgotten ON.
- **Interlocks still apply.** The operator cannot do things that damage the machine or spill product:
  - `FillValve` only with a bottle at S4 (otherwise the product sprays onto the floor),
  - `IndexMotor` only with all valves closed and the cap head stopped (otherwise bottles break).

So every output rung has two branches, Auto OR Manual:

```
  Mode=Auto   EQ Step,10   S4   NOT Fill_Done   Product_Available        FillValve
 ─┤ ├─────────┤    ├───────┤ ├──┤/├─────────────┤ ├──────────────────┬───( )──
                                                                      │
  Mode=Manual   Man_Fill          S4 (interlock)                      │
 ─┤ ├───────────┤ ├───────────────┤ ├─────────────────────────────────┘
```

**Leaving Manual:** switching from Manual back to Auto puts the machine in **IDLE (step 0)**. The operator then presses Start. This is safe because WORK checks the sensors (rule 7), so bottles moved by hand are handled correctly.
