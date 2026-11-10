# Syrup room (mixer) – State diagram

Draft, 2026-11-08. Signal names from `io-list.md` (working names). Same building rules as the filler (`docs/filler/state-diagram.md`): step variable + compare, Stop at safe points, waiting is not a fault, Auto OR Manual output rungs.

## Machine layout: a batch machine with a buffer

The filler is an **indexing** machine (many bottles inside, all stations work in parallel). The syrup room is a **batch** machine: one batch is made in the mixing tank, step after step, and only one step is active at a time. The buffer tank decouples the batch rhythm from the filler, which draws product all the time.

```
   WATER        SYRUP          CO2
  (mains)
  ┌─────┐      ┌─────┐       ┌─────┐
  └──┬──┘      └──┬──┘       └──┬──┘
     P            P              X
     └──────┐     │     ┌───────┘
            ▼     ▼     ▼
          ┌──────(M)──────┐
cooling ─X│    MIXING     │  MixLevel, ProductTemp, CO2Pressure
          │     TANK      │
          └───────┬───────┘
                  P
          ┌───────▼───────┐
          │    BUFFER     │  BufferLevel
          └───────┬───────┘
                  ▼
                FILLER         (draws product while its FillValve is open)
```

`P` = pump · `X` = valve · `M` = agitator. The mixing tank is also the carbonator (one vessel). The chiller and the CO2 supply are outside the machine and assumed always available (stand-ins).

## Diagram

Steps are numbered in tens so a step can be inserted later without renumbering.

```
             Auto + Start, no FAULT alarm
  ┌────────┐ ──────────────────────────► ┌──────────────────────────────────────┐
  │ 0 IDLE │                             │ 5 WAIT_DEMAND  (all off)             │◄─────────────────┐
  └────────┘ ◄────────────────────────── │   buffer above BufferLow = Running   │                  │
      ▲         Stop requested           │   batch needed, no syrup = Starved   │                  │
      │                                  └───┬──────────────────────────┬───────┘                  │
      │      (BufferLevel < BufferLow AND    │                          │ Batch_Dosed              │
      │       Ingredients_OK) OR unfinished  │                          │ (resume a dosed batch)   │
      │       batch in the tank              ▼                          │                          │
      │                         ┌──────────────────────────┐            │                          │
      │                         │ 10 DOSE_WATER  WaterPump │            │                          │
      │                         └────────────┬─────────────┘            │                          │
      │                                      │ MixLevel ≥ WaterBatch    │                          │
      │                                      ▼                          │                          │
      │                         ┌──────────────────────────┐            │                          │
      │                         │ 20 DOSE_SYRUP  SyrupPump │            │                          │
      │                         └────────────┬─────────────┘            │                          │
      │                                      │ MixLevel ≥ DoseTarget    │                          │
      │                                      │ → (S) Batch_Dosed        │                          │
      │                                      ▼                          │                          │
      │                         ┌──────────────────────────┐            │                          │
      │                         │ 30 MIX  Agitator         │◄───────────┘                          │
      │                         └────────────┬─────────────┘                                       │
      │                                      │ Mix_Timer done (MixTime)                            │
      │                                      ▼                                                     │
      │                         ┌──────────────────────────────────┐                               │
      │                         │ 40 COOL  CoolingValve, Agitator  │                               │
      │                         └────────────┬─────────────────────┘                               │
      │                                      │ Temp_OK                                             │
      │                                      ▼                                                     │
      │                         ┌───────────────────────────────────────────┐                      │
      │                         │ 50 CARBONATE  CO2Valve (hysteresis),      │                      │
      │                         │   Agitator, cooling held                  │                      │
      │                         └──────┬─────────────────────────┬──────────┘                      │
      │      Carb_Timer done AND       │                         │ Carb_Timer done AND             │
      │      NOT Buffer_Room           ▼                         │ Buffer_Room                     │
      │                ┌─────────────────────────────────┐       │                                 │
      │   Stop         │ 55 WAIT_BUFFER  (Blocked)       │       │                                 │
      ├────────────────│   cooling + CO2 + agitator held │       │                                 │
      │                └────────────────┬────────────────┘       │                                 │
      │                                 │ Buffer_Room            │                                 │
      │                                 ▼                        ▼                                 │
      │                         ┌──────────────────────────────────────────┐                       │
      │                         │ 60 TRANSFER  TransferPump                │                       │
      │                         └────────────────────┬─────────────────────┘                       │
      │                                              │ MixLevel ≤ MixEmpty                         │
      │                                              │ → BatchCount +1, (R) Batch_Dosed            │
      │  Stop requested (at the end of any step)     │                                             │
      └──────────────────────────────────────────────┴─────────────────────────────────────────────┘
                                                        otherwise

  From ANY step:  overflow / over-pressure / cooling or dosing timeout ─► 900 FAULT (all off, alarm latched) ─► Reset ─► 0 IDLE
                  E_Stop_OK = FALSE                          ─► master enable off, all off, step → 0 IDLE
```

## Step table

| Step | Name | Outputs ON (before interlocks) | Go to next when | Timeout → FAULT |
| --- | --- | --- | --- | --- |
| 0 | IDLE | – | Auto AND Start AND no FAULT alarm → 5 | – |
| 5 | WAIT_DEMAND | – | `Batch_Dosed` → 30; else (`BufferLevel < BufferLow` AND `Ingredients_OK`) OR `MixLevel > MixEmpty` → 10 | – |
| 10 | DOSE_WATER | WaterPump | `MixLevel ≥ WaterBatch` → 20 | `DoseTimeout` (pump failure) |
| 20 | DOSE_SYRUP | SyrupPump | `MixLevel ≥ DoseTarget` → 30, set `Batch_Dosed` | `DoseTimeout` (pump failure) |
| 30 | MIX | Agitator | `Mix_Timer.Q` (MixTime) → 40 | – |
| 40 | COOL | CoolingValve while `NOT Temp_OK`, Agitator | `Temp_OK` → 50 | `CoolTimeout` (e.g. 60 s in the simulator) |
| 50 | CARBONATE | CO2Valve while `CO2_Demand`, CoolingValve while `NOT Temp_OK`, Agitator | `Carb_Timer.Q` → 60 if `Buffer_Room`, else 55 | – (over-pressure alarm is separate) |
| 55 | WAIT_BUFFER | same as 50 (hold the product cold and carbonated) | `Buffer_Room` → 60 | none (waiting = **Blocked**) |
| 60 | TRANSFER | TransferPump | `MixLevel ≤ MixEmpty` → 5, `BatchCount` +1, reset `Batch_Dosed` | – |
| 900 | FAULT | – | Reset (cause gone) → 0 | – |

**Computed values** (section 2 of the ladder, every scan):

| Name | Formula | Syllabus |
| --- | --- | --- |
| `SyrupTarget` | `WaterBatch × 10 / SyrupRatio` (ratio ×10; only if `SyrupRatio ≥ 10`, i.e. at least 1:1, otherwise keep the old value – no division by zero). INT division truncates: 500 × 10 / 55 = 90 (not 90.9) | VIII math |
| `DoseTarget` | `WaterBatch + SyrupTarget` | VIII math |
| `Ingredients_OK` | `SyrupLevel ≥ SyrupTarget` (water from the mains is always there) | VIII compare |
| `Buffer_Room` | `BufferLevel + MixLevel ≤ BufferMax` | VIII math + compare |

**Fixed constants** (tank sizes, not operator setpoints; internal variables with initial values, values to tune with the simulator):

| Name | Value | Meaning |
| --- | --- | --- |
| `MixMax` | 950 L | Mixing tank high limit (overflow alarm, pump interlock) – tank 1000 L |
| `MixEmpty` | 5 L | Below this the mixing tank counts as empty (transfer done, pump runs dry below) |
| `AgitatorMin` | 100 L | The propeller is covered from this level on |
| `BufferMax` | 1950 L | Buffer tank high limit – tank 2000 L |
| `PressMax` | 600 (6.00 bar) | Over-pressure limit |
| `AvailBand` | 20 L | Hysteresis band of `Product_Available` |
| `CoolTimeout` | 60 s | COOL must reach `Temp_OK` within this time (simulator time) |
| `DoseTimeout` | 60 s | DOSE_WATER / DOSE_SYRUP must reach their level within this time (simulator time) |

## Rules

The ladder sketches below are for understanding only; the real rungs are written in the OpenPLC Editor.

### 1. Steps decide the outputs (same as the filler)

`Step` (%MW51) holds the current step. **Transition rungs** change it (`EQ Step,x AND condition → MOVE y → Step`), **output rungs** switch an output by comparing the step. Only one step is active at a time, so each output asks "which step are we in?" plus, for cooling and CO2, a hysteresis bit:

```
CoolingValve request := (Step_Cool OR Step_Carbonate OR Step_WaitBuffer) AND NOT Temp_OK
CO2Valve request     := (Step_Carbonate OR Step_WaitBuffer) AND CO2_Demand
Agitator request     := Step_Mix OR Step_Cool OR Step_Carbonate OR Step_WaitBuffer
```

Steps 40–55 keep cooling, steps 50–55 keep the CO2 pressure: heat leaks in from the room, and warm product gives its CO2 back.

### 2. Hysteresis: two thresholds instead of one

A single threshold makes a valve chatter when the value hovers at the setpoint. Each analog control uses a **set** threshold and a **reset** threshold; between them the bit keeps its last value (seal-in):

| Bit | Set when | Reset when | Used for |
| --- | --- | --- | --- |
| `Temp_OK` | `ProductTemp ≤ CoolSetpoint` | `ProductTemp > CoolSetpoint + TempHyst` | Cooling valve, COOL done, CO2 interlock |
| `CO2_Demand` | `CO2Pressure < PressSetpoint − PressHyst` | `CO2Pressure ≥ PressSetpoint` | CO2 valve |
| `Product_Available` | `BufferLevel ≥ BufferMin + AvailBand` | `BufferLevel < BufferMin` | Handshake to the filler |

```
   ProductTemp <= CoolSetpoint                                  Temp_OK
──┤ LE ├───────────────────────────────────────────┬──────────( )──
   Temp_OK     ProductTemp <= CoolSetpoint+TempHyst │
──┤ ├────────┤ LE ├─────────────────────────────────┘
```

Same idea as the filler's `Outfeed_Full`, which is a hysteresis on **time** instead of value.

### 3. When is carbonation done?

CO2 dissolves into the cold product, so the pressure drops after each valve opening and `CO2_Demand` opens the valve again. At first this happens often; once the product is **saturated**, the pressure stays up. So:

```
Carb_Timer := TON(IN := Step_Carbonate AND NOT CO2_Demand, PT := CarbTime)
```

The timer restarts every time the valve has to open again. It only finishes when the pressure held for `CarbTime` without help: that is "saturated", measured with one timer.

### 4. Stop: finish the current step, then IDLE

The operator's Stop (`Cmd = 2`) is remembered in `Stop_Request` (seal-in, cleared in IDLE), as in the filler. A batch takes minutes, so waiting for the whole batch would be too slow; instead **every step end is a safe point**: no pump is half-way through a dose, no timer is half-way.

| Safe point | What happens |
| --- | --- |
| End of any step 10–60 | Go to IDLE instead of the next step |
| WAIT_DEMAND, WAIT_BUFFER | Go to IDLE at once (nothing is running) |

The product stays in the mixing tank. Rule 6 says how the next Start continues.

### 5. Waiting is not a fault

| Situation | State | What the machine does |
| --- | --- | --- |
| WAIT_DEMAND, buffer above `BufferLow` | **Running** (green) | Nothing to do: the buffer is full enough (report 5.5: "Running, then idle") |
| WAIT_DEMAND, batch needed, `NOT Ingredients_OK` | **Starved** | Waits for syrup; `Alm_LowSyrup` as a warning (no FAULT), clears itself after the refill |
| WAIT_BUFFER | **Blocked** | Finished batch does not fit into the buffer; waits for the filler to use product |

WAIT_BUFFER normally does not happen (a batch starts only when the buffer is low). It protects the buffer from overflowing when the setpoints do not fit (`BufferLow + batch > BufferMax`) or the filler stopped during the batch. It is also a good demo of **Blocked**.

### 6. Resume after Stop, FAULT or E-stop: the process values tell where we are

After Reset → IDLE → Start, the machine goes through WAIT_DEMAND, which looks at the tank instead of remembering the old step:

- **Dosing steps are level-based**, so repeating them is harmless: if `MixLevel` already reached the target, the step is done at once (like an empty filler station).
- **But:** a partly transferred, finished batch also has a low level, and dosing water into it would dilute the drink. So the PLC remembers one fact: `Batch_Dosed` (%MX3.3), set at the end of DOSE_SYRUP, reset at the end of TRANSFER. If it is TRUE, the batch restarts at MIX (re-mix, re-cool, re-carbonate, transfer the rest).

`Batch_Dosed` is material tracking like the filler's `Entry_Load`, with the same limit: it is not retained, so after a PLC restart with product in the tank the PLC does not know whether the syrup was dosed.

### 7. Interlocks (in Auto **and** Manual)

Interlocks stop damage. They sit after the Auto-OR-Manual branch, so they also protect against the operator:

| Output | Allowed only when | Why |
| --- | --- | --- |
| WaterPump, SyrupPump | `MixLevel < MixMax AND NOT MixHighHigh` | No overflow of the mixing tank (switch = backup if the level sensor sticks) |
| Agitator | `MixLevel ≥ AgitatorMin` | Propeller must be under liquid (report 4.2) |
| CO2Valve | `Temp_OK AND CO2Pressure < PressMax` | CO2 only into cold product (report 4.2); never above the pressure limit |
| TransferPump | `MixLevel > MixEmpty AND BufferLevel < BufferMax` | Pump must not run dry; buffer must not overflow |
| CoolingValve | – | Always safe |

### 8. Manual mode (same as the filler)

Sequencer frozen (transitions need `Mode_Auto`), one hold-to-run button per actuator (`Man_*`), **jog watchdog** from the start (`Jog_Block`: a button held > 10 s is ignored until all are released), interlocks still apply. Manual → Auto goes to IDLE; Start then resumes by rule 6.

### 9. E-stop and FAULT

- **E-stop** (`E_Stop_OK` = FALSE): `Master_Enable` off, every output off at once, step → IDLE (same as the filler; no Reset needed, release + Start). The CO2 valve closing on E-stop is the safe direction.
- **FAULT** (overflow, over-pressure, cooling timeout, dosing timeout): step → 900, all off, alarm latched; Reset clears it only when the cause is gone.
- `Product_Available` is **not** switched off by FAULT or E-stop: the product already in the buffer is good, and the filler has its own E-stop.
