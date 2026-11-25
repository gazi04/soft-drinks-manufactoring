# Blow moulder – I/O list

Draft 2026-11-25, review answers of the same day built in (section "Answers to the open questions"). Two stations in parallel: the **oven station** (hopper, loader, IR oven with a P controller on an analog output) and the **mould station** (transfer arm, mould, stretch rod, pre-blow, high blow, exhaust, ejector onto C1). Concept and answers Q1–Q6: `progress.md`.
Design reference: `docs/project-report.md` sections 4.1 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms). Steps and rules: `state-diagram.md` (next).

## Address rules

Same as the other four machines (`docs/packer/io-list.md`, "Address rules"), so `address_map*.py`, `plcs.py` and the FUXA generators can be reused:

- Booleans → `%MX` (coils from 8192), numbers → `%MW` (holding registers from 1024), physical outputs → `%QX` (coils from 0).
- Sensors are written by the simulator into `%MX` / `%MW`, not `%IX`.
- Project setting **`mx_bits` = 64** in *Device → Servers → RuntimeServer* (covers `%MX0.0`–`%MX7.7`).
- Own runtime container `plc-blowmolder`: Modbus host port **5025**, editor at **`127.0.0.5:8443`** (report 6.1). The addresses below are this PLC's own memory and may overlap with the other PLCs'.

Same byte group as the other machines for the same job (`%MX0` + `%MX4` sensors, `%MX1` wiring, `%MX2` manual, `%MX3` conditioned, `%MX5` + `%MX7` alarms; `%MW12–19` analog, `%MW30–39` setpoints, `%MW40–49` computed, `%MW50–53` status). **New for this machine:**
- the first **analog output**, `LampPower` at **`%QW0`** (Q1) = **holding register 0, verified in OpenPLC v4** (stage 0, 2026-11-25: a MOVE 42 into `LampPower` read back as HR 0). `%QWn` = HR n;
- the first **raw analog input**, `BlowPressure_Raw` in counts 0–27648, scaled by the PLC into `BlowPressure` (0.1 bar);
- two step words as the packer: `Step` = oven station, `MouldStep` = mould station;
- 13 digital sensors (`%MX0` full, five in `%MX4`), 11 alarm bits (`%MX5` full, three in `%MX7`).

## Digital sensors (written by the simulator)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Preform_At_Loader | BOOL | %MX0.0 | coil 8192 | A preform waits at the loader (end of the hopper chute) |
| Preform_In_Oven | BOOL | %MX0.1 | coil 8193 | A preform sits in the oven pocket: ends LOAD, "pocket empty" before the next load |
| Arm_At_Oven | BOOL | %MX0.2 | coil 8194 | Transfer arm at its home position above the oven pocket, **out of the mould** (the mould only closes with it) |
| Arm_At_Mould | BOOL | %MX0.3 | coil 8195 | Transfer arm inside the open mould: the preform can be released |
| Mould_Closed | BOOL | %MX0.4 | coil 8196 | Mould halves closed and locked (blow only with it) |
| Mould_Open | BOOL | %MX0.5 | coil 8197 | Mould halves fully open (arm and ejector only with it) |
| Rod_Up | BOOL | %MX0.6 | coil 8198 | Stretch rod up, out of the bottle (mould opens only with it) |
| Rod_Down | BOOL | %MX0.7 | coil 8199 | Stretch rod at the bottom of the mould: stretch finished, ends pre-blow |
| Bottle_In_Mould | BOOL | %MX4.0 | coil 8224 | Photo eye across the open mould: a preform or bottle is in it ("mould empty" before a transfer, "ejected" after the ejector) |
| Bottle_Ejected | BOOL | %MX4.1 | coil 8225 | Eye at the entry of C1: one pulse per bottle put onto C1 (`BottleCount` counts the rising edge) |
| Hopper_Empty | BOOL | %MX4.2 | coil 8226 | No preforms left in the hopper |
| Preform_Low | BOOL | %MX4.3 | coil 8227 | Hopper low: refill soon – warning in FUXA only (as `Roll_Low` / `Film_Low`) |
| C1_Full | BOOL | %MX4.4 | coil 8228 | **S1**: C1 full sensor, on the filler's infeed conveyor, wired to this PLC (report 5.3); filtered into `Outfeed_Full` |
| – | BOOL | %MX4.5–4.7 | coils 8229–8231 | Spare |

The filler drives C1's motor; the blow moulder only reads S1.

## Analog sensors (written by the simulator)

INT with fixed-point units, no REAL (same decision as every machine).

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| OvenTemp | INT (0.1 °C) | %MW12 | HR 1036 | Preform surface temperature in the oven pocket (IR pyrometer, Q3), e.g. 1050 = 105.0 °C; drops when a cold preform comes in |
| BlowPressure_Raw | INT (counts) | %MW13 | HR 1037 | Blow pressure transducer as an analog card delivers it: **0–27648 = 0–40.0 bar** (4–20 mA). Below 0 = wire break (live zero), above 27648 = over range |
| – | – | %MW14–19 | HR 1038–1043 | Spare analog |

## Signals from other machines (written by the simulator)

| Name | Type | Address | Modbus | Meaning | Until the source exists |
| --- | --- | --- | --- | --- | --- |
| – | BOOL | %MX1.0 | coil 8200 | Not used (filler: `Product_Available` in) – kept free so the layout stays the same | – |
| E_Stop_OK | BOOL | %MX1.1 | coil 8201 | E-stop chain healthy (FALSE = stop) | Held TRUE by simulator |
| Heartbeat | BOOL | %MX1.2 | coil 8202 | Toggles every 1 s | Toggled by simulator |

No signal to other machines: the link to the filler is C1 (S1 wired here, motor driven by the filler).

## Operator commands and setpoints (written by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Cmd | INT | %MW0 | HR 1024 | 1 = Start, 2 = Stop, 3 = Reset (alarms), 4 = Reset counter (`BottleCount` → 0, Q6) |
| ModeReq | INT | %MW1 | HR 1025 | 0 = Stop, 1 = Auto, 2 = Manual |
| OvenSetpoint | INT (0.1 °C) | %MW30 | HR 1054 | Preform temperature, default 1050 = 105 °C; HMI limits 950–1200 (below `OvenMax`) |
| OvenBand | INT (0.1 °C) | %MW31 | HR 1055 | `Oven_Ready` window around the setpoint, default 50 = ±5 °C; HMI limits 20–100 |
| Kp | INT (% per °C) | %MW32 | HR 1056 | P gain of the lamp controller, default 10; HMI limits 1–30 (keeps `Kp · error` inside INT) |
| Bias | INT (%) | %MW33 | HR 1057 | Lamp power added to the P term, default 0 (shows the P offset); HMI limits 0–100 |
| BlowSetpoint | INT (0.1 bar) | %MW34 | HR 1058 | High-blow pressure that must be reached, default 350 = 35 bar; HMI limits 250–380 (below `MaxPressure`) |

Defaults copied in on the first scan (`Def_…` → setpoints, as in the other machines). Values that protect people or the product are **constants, not setpoints** (listed in `state-diagram.md`): `OvenMax` 1300 (130 °C, PET crystallises), `ExhaustLimit` 5 (0.5 bar, mould may open), `MaxPressure` 400 (40 bar), soak / blow hold / grip times and the timeouts.

Manual buttons (hold-to-run, Manual mode only, interlocks still apply, jog watchdog as in the filler). Exception: the mould is a **toggle** (answer 4), as the labeler's bell: the HMI still writes 1 on press and 0 on release, the PLC turns each click into close / open. Reason: the rod and the blow need `Mould_Closed`, and one mouse can hold only one button. **No button for the blow valves** (no blowing by hand) and none for the lamps (the oven heats only in Auto, as the packer's).

| Name | Type | Address | Modbus | Drives |
| --- | --- | --- | --- | --- |
| Man_HopperFeed | BOOL | %MX2.0 | coil 8208 | HopperFeed |
| Man_Loader | BOOL | %MX2.1 | coil 8209 | Loader |
| Man_Arm | BOOL | %MX2.2 | coil 8210 | ArmToMould (held = to the mould, released = back home) |
| Man_Gripper | BOOL | %MX2.3 | coil 8211 | Gripper (held = closed) |
| Man_MouldClose | BOOL | %MX2.4 | coil 8212 | MouldClose, **toggle**: each click (rising edge) flips the internal latch `Man_Mould_Closed`; opening only with `Pressure_Safe` and `Rod_Up`; the latch drops when Manual is left |
| Man_Rod | BOOL | %MX2.5 | coil 8213 | StretchRod (held = down; only with `Mould_Closed`) |
| Man_Ejector | BOOL | %MX2.6 | coil 8214 | Ejector |
| – | BOOL | %MX2.7 | coil 8215 | Spare |

## Actuators (PLC outputs)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| HopperFeed | BOOL | %QX0.0 | coil 0 | Hopper elevator / chute vibrator: runs while no preform waits at the loader |
| Loader | BOOL | %QX0.1 | coil 1 | Pushes the waiting preform into the oven pocket; spring return when OFF; only with the pocket empty |
| ArmToMould | BOOL | %QX0.2 | coil 2 | Transfer arm: ON = swing into the mould, OFF = back home above the oven; into the mould only with `Mould_Open`, rod up and the mould empty |
| Gripper | BOOL | %QX0.3 | coil 3 | Gripper on the arm: ON = closed (holds the preform by its neck) |
| MouldClose | BOOL | %QX0.4 | coil 4 | Mould halves: ON = close and lock, OFF = open; closes only with `Arm_At_Oven`, **opens only with `Pressure_Safe` and `Rod_Up`** (an OFF command is held back by the PLC, never by the operator); in FAULT, E-stop and comm lost a closed mould **stays closed** (no motion in a safe state, pressure may still be inside; exact rule in `state-diagram.md`) |
| StretchRod | BOOL | %QX0.5 | coil 5 | ON = rod down into the preform, OFF = up; only with `Mould_Closed` |
| PreBlowValve | BOOL | %QX0.6 | coil 6 | Low-pressure air (~8 bar, fixed regulator) during the stretch; only with `Mould_Closed` |
| HighBlowValve | BOOL | %QX0.7 | coil 7 | High-pressure air until `BlowSetpoint`, then held; only with `Mould_Closed`, never together with `ExhaustValve` |
| ExhaustValve | BOOL | %QX1.0 | coil 8 | Vents the bottle to atmosphere; **normally open**: ON in every case except STRETCH, BLOW and HOLD, also in FAULT, E-stop and comm lost (the one output ON in a safe state, `state-diagram.md` rule 3) |
| Ejector | BOOL | %QX1.1 | coil 9 | Pushes the bottle out of the open mould onto C1; only with `Mould_Open`, arm home and no `Outfeed_Full` |
| LampPower | INT (%) | **%QW0** | HR 0 (verified 2026-11-25) | IR lamp power 0–100 %: `LIMIT(0, Bias + Kp · (OvenSetpoint − OvenTemp) / 10, 100)` while `Lamps_On`, else 0; 0 at `OvenTemp ≥ OvenMax` in every mode |

## Status and alarms (written by the PLC, read by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| State | INT | %MW50 | HR 1074 | 0 Stopped, 1 Running (also warm-up), 2 Starved, 3 Blocked, 9 Fault – one state from both stations, priority Fault > Stopped > Blocked > Starved > Running (as the packer) |
| Step | INT | %MW51 | HR 1075 | **Oven station**: steps decided in `state-diagram.md` (idle, warm-up, load, heat / soak, wait for transfer, fault) |
| MouldStep | INT | %MW52 | HR 1076 | **Mould station**: steps decided in `state-diagram.md` (idle, transfer, close, stretch + pre-blow, high blow, hold, exhaust, rod up, open, eject, fault) |
| BottleCount | INT | %MW53 | HR 1077 | Bottles put onto C1 (rising edge of `Bottle_Ejected`); zeroed by `Cmd = 4` (Q6) |
| Alm_NoPreforms | BOOL | %MX5.0 | coil 8232 | Hopper empty and the oven waits for a preform: **warning only**, State Starved, clears itself after a refill (as `Alm_FilmEmpty`) |
| Alm_HandlingJam | BOOL | %MX5.1 | coil 8233 | Loader, transfer arm or ejector did not reach its sensor in time (preform stuck, bottle not out) (→ FAULT) |
| Alm_OutfeedBlocked | BOOL | %MX5.2 | coil 8234 | Blocked longer than 2 min (warning, no FAULT; as every machine) |
| Alm_CommLost | BOOL | %MX5.3 | coil 8235 | Heartbeat not toggled for 3 s (→ outputs off via `Master_Enable`, exhaust open) |
| Alm_MouldFault | BOOL | %MX5.4 | coil 8236 | Mould did not close / open, or the stretch rod did not reach down / up, in time (→ FAULT) |
| Alm_OvenOverTemp | BOOL | %MX5.5 | coil 8237 | `OvenTemp` ≥ `OvenMax` (→ FAULT in every mode; lamp power 0) |
| Alm_OvenHeatTimeout | BOOL | %MX5.6 | coil 8238 | Warm-up / heat: `Oven_Ready` not reached in time (lamps failed) (→ FAULT) |
| Alm_BlowFault | BOOL | %MX5.7 | coil 8239 | High blow: `BlowSetpoint` not reached in time (air supply low, leaking mould, burst preform) (→ FAULT) |
| Alm_ExhaustFault | BOOL | %MX7.0 | coil 8248 | Exhaust: pressure not below `ExhaustLimit` in time (exhaust blocked) (→ FAULT; **the mould stays closed**) |
| Alm_OverPressure | BOOL | %MX7.1 | coil 8249 | `BlowPressure` ≥ `MaxPressure` (regulator failed) (→ FAULT, high blow off, exhaust open) |
| Alm_PressureSensor | BOOL | %MX7.2 | coil 8250 | `BlowPressure_Raw` < −1000 (wire break, live zero lost) or > 32511 (over range) (→ FAULT; the mould stays closed, a broken sensor can not prove the mould is empty of pressure) |
| – | BOOL | %MX7.3–7.7 | coils 8251–8255 | Spare |

`Alm_CommLost` sits at `%MX5.3`, as in every PLC. The early warning `Preform_Low` gets no alarm bit: FUXA raises it straight from the sensor bit.

## Conditioned values (written by the PLC)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Outfeed_Full | BOOL | %MX3.0 | coil 8216 | C1 really full: `C1_Full` ON 2 s without a break; released after OFF 3 s (same filter as every machine, report 5.3) |
| Oven_Ready | BOOL | %MX3.1 | coil 8217 | `OvenTemp` within `OvenSetpoint ± OvenBand` (window compare) |
| Jog_Block | BOOL | %MX3.2 | coil 8218 | Jog watchdog, same as the filler |
| Preform_Hot | BOOL | %MX3.3 | coil 8219 | The preform in the pocket was inside `Oven_Ready` for `SoakTime` without a break (TON): heated through, not only on the surface; the transfer waits for it |
| Pressure_Reached | BOOL | %MX3.4 | coil 8220 | `BlowPressure ≥ BlowSetpoint`: starts the blow hold timer |
| Pressure_Safe | BOOL | %MX3.5 | coil 8221 | `BlowPressure < ExhaustLimit` **and** no `Alm_PressureSensor`: the mould may open |
| Starved | BOOL | %MX3.6 | coil 8222 | Oven station waits for a preform and none came for 2 s (hopper empty or chute stuck) **and** the mould waits for a preform too (`state-diagram.md` rule 8) |
| Lamps_On | BOOL | %MX3.7 | coil 8223 | Oven switched on: `Master_Enable AND Auto AND no FAULT AND Preform_In_Oven`; enables `LampPower` (`state-diagram.md` rule 5) |

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Lamp_Calc | INT (%) | %MW40 | HR 1064 | Lamp power the P controller asks for: `LIMIT(0, Bias + Kp · LIMIT(ErrMin, OvenError, ErrMax) / 10, 100)`, every scan; `LampPower` follows it only while `Lamps_On` (stage 6). Located (decision 2026-11-25, ladder stage 2) so the P controller is testable over Modbus and the HMI can show demand vs. output; was the spare fallback for `LampPower` |
| BlowPressure | INT (0.1 bar) | %MW41 | HR 1065 | Scaled pressure: `BlowPressure_Raw · 400 / 27648`, e.g. 24192 counts = 350 = 35.0 bar. **Computed in DINT** (`INT_TO_DINT`, `MUL`, `DIV`, `DINT_TO_INT`): 27648 · 400 overflows an INT (max 32767). Negative raw values give a negative pressure (only for the sensor alarm) |
| OvenError | INT (0.1 °C) | %MW42 | HR 1066 | `OvenSetpoint − OvenTemp` (SUB), the P controller's input – for the HMI trend, shows the steady offset |

Internal only (no address): step bits of both stations, timers (`Soak_Timer`, `Hold_Timer`, `Grip_Timer`, `Release_Timer`, the timeout TONs, filter TONs), `Bottle_Counter` (CTU behind `BottleCount`), edge bits, `Stop_Request`, the constants of `state-diagram.md`.

## Animation data

Decided with the HMI plan (part C), as for the labeler and the packer. `%MW70–79` stay free until then.

## Answers to the open questions (2026-11-25)

1. **`Alm_NoPreforms` = warning**, not FAULT: State Starved, clears itself after a refill (as `Alm_FilmEmpty`).
2. **Exhaust open in the safe state:** FAULT, E-stop and comm lost set `ExhaustValve` ON (vent the bottle), the only output ON when everything else is off.
3. **`Alm_PressureSensor` kept:** live-zero wire-break and over-range check on `BlowPressure_Raw`.
4. **Manual mould = toggle** (`Man_Mould_Closed` latch), as the labeler's bell.
5. **`%QW0` first, `%MW40` as the fallback** if stage 0 finds `%QW0` not served over Modbus in v4. **Result (2026-11-25): `%QW0` = HR 0, no fallback.**
