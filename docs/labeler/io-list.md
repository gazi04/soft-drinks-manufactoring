# Labeler – I/O list

Draft 2026-11-15 (fourth version: roll-fed hot-melt labeler with clamp and spin; descriptive sensor names; **no inspection, no reject**).
Design reference: `docs/project-report.md` sections 4.4 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms). Steps and rules: `state-diagram.md`.

## Address rules

Same as the filler and the syrup room (`docs/filler/io-list.md`, "Address rules"), so `address_map.py`, `plcs.py` and the FUXA generators can be reused:

- Booleans → `%MX` (coils from 8192), numbers → `%MW` (holding registers from 1024), physical outputs → `%QX` (coils from 0).
- Sensors are written by the simulator into `%MX` / `%MW`, not `%IX`.
- Project setting **`mx_bits` = 64** in *Device → Servers → RuntimeServer* (covers `%MX0.0`–`%MX7.7`).
- Own runtime container `plc-labeler`: Modbus host port **5023**, editor at **`127.0.0.3:8443`** (report 6.1). The addresses below are this PLC's own memory and may overlap with the other PLCs'.

Same byte group as the other machines for the same job (`%MX0` sensors, `%MX1` wiring, `%MX2` manual, `%MX3` conditioned, `%MX5` alarms; `%MW30–39` setpoints, `%MW50–53` status). **New for this machine:** 10 digital sensors, more than the 8 bits of `%MX0`, so the two extra ones go into **`%MX4`** (free in the standard layout, report 6.2).

## Digital sensors (written by the simulator)

Descriptive names (decision 2026-11-15), not line-wide numbers like the filler's S2–S8: on a spinning machine `Bell_Down` reads better in a rung than `S11`.

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Bottle_At_Gate | BOOL | %MX0.0 | coil 8192 | Bottle waiting at the infeed gate (end of C2) |
| Bottle_In_Pocket | BOOL | %MX0.1 | coil 8193 | Bottle in the station pocket (on the turntable) |
| Bell_Down | BOOL | %MX0.2 | coil 8194 | Centering bell down (bottle clamped); "bell up" = `NOT Bell_Down` (one sensor, decision 2026-11-15) |
| Label_Present | BOOL | %MX0.3 | coil 8195 | Label on the bottle in the pocket; used only on a restart, so a labelled bottle is not labelled twice (`state-diagram.md` rule 7) |
| C3_Full | BOOL | %MX0.4 | coil 8196 | C3 (outfeed conveyor to the packer) full |
| Roll_Empty | BOOL | %MX0.5 | coil 8197 | Label roll empty (end of the web) |
| Label_Mark | BOOL | %MX0.6 | coil 8198 | Registration mark on the label web: one label length fed → cut here |
| Spin_Pulse | BOOL | %MX0.7 | coil 8199 | One pulse per turn of the turntable (cam) |
| Roll_Low | BOOL | %MX4.0 | coil 8224 | Label roll low (diameter sensor): prepare the next roll – warning in FUXA only |
| Glue_Low | BOOL | %MX4.1 | coil 8225 | Glue level low in the melt tank: add glue blocks – warning in FUXA only |
| – | BOOL | %MX4.2–4.7 | coils 8226–8231 | Spare |

S7 (C2 full) is **not** a labeler input: it is wired to the filler (report 5.3).

## Analog sensors (written by the simulator)

INT with fixed-point units, no REAL (same decision as the syrup room).

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| GlueTemp | INT (0.1 °C) | %MW12 | HR 1036 | Hot-melt temperature in the melt tank, e.g. 1700 = 170.0 °C |
| – | – | %MW13–19 | HR 1037–1043 | Spare analog |

## Signals from other machines (written by the simulator)

| Name | Type | Address | Modbus | Meaning | Until the source exists |
| --- | --- | --- | --- | --- | --- |
| – | BOOL | %MX1.0 | coil 8200 | Not used (filler: `Product_Available` in) – kept free so the layout stays the same | – |
| E_Stop_OK | BOOL | %MX1.1 | coil 8201 | E-stop chain healthy (FALSE = stop) | Held TRUE by simulator |
| Heartbeat | BOOL | %MX1.2 | coil 8202 | Toggles every 1 s | Toggled by simulator |

No signal to other machines: the bottle flow needs none (report 5.1). The labeler's link to the filler is C2 (S7, wired to the filler), its link to the packer is C3 (`C3_Full`, wired to the labeler).

## Operator commands and setpoints (written by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Cmd | INT | %MW0 | HR 1024 | 1 = Start, 2 = Stop, 3 = Reset (alarms), **4 = Reset counter** (`GoodCount` → 0; new for this machine) |
| ModeReq | INT | %MW1 | HR 1025 | 0 = Stop, 1 = Auto, 2 = Manual |
| GlueSetpoint | INT (0.1 °C) | %MW30 | HR 1054 | Melt temperature, e.g. 1700 = 170 °C |
| GlueHyst | INT (0.1 °C) | %MW31 | HR 1055 | Heater hysteresis band, e.g. 50 = 5 °C |
| GlueBand | INT (0.1 °C) | %MW32 | HR 1056 | `Glue_Ready` window around the setpoint, e.g. 100 = ±10 °C (must be wider than `GlueHyst`) |
| PressTurns | INT | %MW33 | HR 1057 | Bottle turns after the cut (label tail pressed on), e.g. 2 |

Defaults copied in on the first scan (`Def_…` → setpoints, as in the other machines).

Manual buttons (hold-to-run, Manual mode only, interlocks still apply, jog watchdog as in the filler). The heater has no button: it is controlled automatically in every mode (`state-diagram.md` rule 4).

| Name | Type | Address | Modbus | Drives |
| --- | --- | --- | --- | --- |
| Man_C2 | BOOL | %MX2.0 | coil 8208 | ConveyorMotor2 |
| Man_Gate | BOOL | %MX2.1 | coil 8209 | GateMotor |
| Man_Clamp | BOOL | %MX2.2 | coil 8210 | ClampValve |
| Man_Spin | BOOL | %MX2.3 | coil 8211 | SpinMotor |
| Man_Feed | BOOL | %MX2.4 | coil 8212 | LabelFeed + GlueRoller (always together) |
| Man_Cut | BOOL | %MX2.5 | coil 8213 | Cutter |
| Man_Discharge | BOOL | %MX2.6 | coil 8214 | DischargeMotor |

## Actuators (PLC outputs)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| ConveyorMotor2 | BOOL | %QX0.0 | coil 0 | C2 infeed conveyor: runs continuously, bottles queue at the closed gate (same rule as the filler's C1, report 5.3 point 3) |
| GateMotor | BOOL | %QX0.1 | coil 1 | Opens the infeed gate (TP pulse, admits one bottle) |
| ClampValve | BOOL | %QX0.2 | coil 2 | Centering bell down (pneumatic): holds the bottle on the turntable |
| SpinMotor | BOOL | %QX0.3 | coil 3 | Turntable: spins the bottle around its axis so the label wraps around it |
| LabelFeed | BOOL | %QX0.4 | coil 4 | Feed rollers: pull the label web off the roll at the bottle's surface speed |
| Cutter | BOOL | %QX0.5 | coil 5 | Knife: cuts one label off the web (TP pulse) |
| GlueRoller | BOOL | %QX0.6 | coil 6 | Glue roller: hot melt on the leading and trailing edge of the label |
| GlueHeater | BOOL | %QX0.7 | coil 7 | Heater of the melt tank (hysteresis control) |
| DischargeMotor | BOOL | %QX1.0 | coil 8 | Pushes the labelled bottle out of the pocket onto C3 |

The C3 motor belongs to the packer; until the packer exists, the simulator's packer stand-in keeps C3 running.

## Status and alarms (written by the PLC, read by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| State | INT | %MW50 | HR 1074 | 0 Stopped, 1 Running (also WARM_UP), 2 Starved (no bottle at the gate for 2 s, or label roll empty), 3 Blocked (labelled bottle, C3 full), 9 Fault (`state-diagram.md` rule 8) |
| Step | INT | %MW51 | HR 1075 | 0 IDLE, 5 WARM_UP, 10 WAIT_BOTTLE, 20 CLAMP, 22 WRAP, 24 PRESS, 26 UNCLAMP, 35 WAIT_OUTFEED, 40 DISCHARGE, 900 FAULT |
| GoodCount | INT | %MW53 | HR 1077 | Labelled bottles discharged onto C3 (CTU; main counter, report 6.2); zeroed by `Cmd = 4` |
| Alm_LabelTimeout | BOOL | %MX5.0 | coil 8232 | WRAP: no `Label_Mark` within 5 s (web torn, feed jammed), or PRESS: `PressTurns` not reached within 5 s (turntable stuck) (→ FAULT) |
| Alm_PocketJam | BOOL | %MX5.1 | coil 8233 | A pocket movement did not finish: gated bottle not in the pocket (`Bottle_Entering`), CLAMP / UNCLAMP within 2 s, DISCHARGE (bottle out) within 3 s (→ FAULT) |
| Alm_OutfeedBlocked | BOOL | %MX5.2 | coil 8234 | Blocked longer than 2 min (warning, no FAULT; same as the filler) |
| Alm_CommLost | BOOL | %MX5.3 | coil 8235 | Heartbeat not toggled for 3 s (→ outputs off via `Master_Enable`) |
| Alm_LabelEmpty | BOOL | %MX5.4 | coil 8236 | Label roll empty (`Roll_Empty`): **warning only**, no new bottle admitted, State Starved, clears itself after the roll change |
| Alm_GlueOverTemp | BOOL | %MX5.5 | coil 8237 | `GlueTemp` ≥ `GlueMax` (glue burns, fire risk) (→ FAULT, heater off by interlock) |
| Alm_GlueHeatTimeout | BOOL | %MX5.6 | coil 8238 | WARM_UP: `Glue_Ready` not reached within 120 s (heater failed) (→ FAULT) |
| – | BOOL | %MX5.7 | coil 8239 | Spare |

`Alm_CommLost` sits at `%MX5.3`, as in every PLC. The two early warnings `Roll_Low` and `Glue_Low` get no alarm bit: they are not latched and change nothing in the program, so FUXA raises them straight from the sensor bits (decision 2026-11-15).

## Conditioned values (written by the PLC)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Outfeed_Full | BOOL | %MX3.0 | coil 8216 | C3 really full: `C3_Full` ON 2 s without a break; released after `C3_Full` OFF 3 s (copy of the filler's `Outfeed_Full`, report 5.3) |
| Bottle_Entering | BOOL | %MX3.1 | coil 8217 | A bottle passed the gate but has not reached the pocket sensor yet: set by the gate pulse, reset by `Bottle_In_Pocket`; the gate needs it FALSE (`state-diagram.md` rule 2, same idea as the filler's `Entry_Load`, also at `%MX3.1`) |
| Jog_Block | BOOL | %MX3.2 | coil 8218 | Jog watchdog, same as the filler |
| Heat_Demand | BOOL | %MX3.3 | coil 8219 | Heater hysteresis: set at `GlueTemp < GlueSetpoint − GlueHyst`, reset at `GlueTemp ≥ GlueSetpoint` |
| Glue_Ready | BOOL | %MX3.4 | coil 8220 | `GlueTemp` within `GlueSetpoint ± GlueBand` (window compare): glue liquid enough to stick, not burnt |
| Starved | BOOL | %MX3.5 | coil 8221 | WAIT_BOTTLE and (no bottle at the gate for 2 s, or `Roll_Empty`) (`state-diagram.md` rule 8); located so tests can read the filter |

Internal only (no address): step bits, timers (`Gate_Empty_Timer`, …), `Spin_Counter` (CTU), edge bits, `Stop_Request`, the constants of `state-diagram.md`.

## Animation data

Decided at the HMI step (decision 2026-11-15): probably `Anim_C3` (C3 slot bits, as the filler's `Anim_C1`/`Anim_C2`) and `Anim_Station` (pocket state code) in `%MW70–71`, written by the simulator.

## Decisions on the open questions (2026-11-15)

1. Sensor names → descriptive (`Bottle_At_Gate`, `Bell_Down`, …).
2. Fill level window → **removed** from the labeler; future enhancement of the filler (`docs/filler/progress.md`).
3. Reject (pusher, chute, reject bin) → **removed** from the labeler; future enhancement of the filler. With no reject path the inspection is removed too (`Cap_Present`, `Bottle_Good`, `RejectCount`, `RejectRate`): every bottle goes to C3.
4. Rejects in a row → **removed**.
5. Bell sensor → one sensor, up = `NOT Bell_Down`.
6. Roll low / glue low → warnings in FUXA straight from the sensor bits, no PLC alarm bit.
7. Counter → the operator can zero `GoodCount` with `Cmd = 4` (separate from Reset, so acknowledging an alarm never loses the count).
8. Starved → filtered with a TON (2 s without a bottle at the gate), so the state does not flicker between bottles.
9. Animation words → decided at the HMI step.
