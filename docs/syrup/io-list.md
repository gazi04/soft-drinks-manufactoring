# Syrup room and carbonator – I/O list

Draft 2026-11-08, open questions answered 2026-11-09 (list at the end). Working names; the one point left open is marked **(?)**.
Design reference: `docs/project-report.md` sections 4.2 (machine), 5.1 (`Product_Available` handshake), 6.2 (register layout), 9 (modes/alarms).

## Address rules

Same as the filler (`docs/filler/io-list.md`, "Address rules"), so `address_map.py`, `plcs.py` and the FUXA generators can be reused:

- Booleans → `%MX` (coils from 8192), numbers → `%MW` (holding registers from 1024), physical outputs → `%QX` (coils from 0).
- Sensors are written by the simulator into `%MX` / `%MW`, not `%IX`.
- Project setting **`mx_bits` = 64** in *Device → Servers → RuntimeServer*.
- Own runtime container: Modbus host port 5022, editor at `127.0.0.2:8443` (report 6.1; Editor v4 hard-codes port 8443). The addresses below are this PLC's own memory and may overlap with the filler's.

Same byte group as the filler for the same job (`%MX0` sensors, `%MX1` wiring, `%MX2` manual, `%MX3` conditioned, `%MX5` alarms; `%MW30–39` setpoints, `%MW50–53` status).

## Analog sensors (written by the simulator)

All values are INT (16-bit, signed) with fixed-point units (L, 0.1 °C, 0.01 bar), no REAL (decision 2026-11-09): one Modbus register per value and plain INT compares in ladder, like the scaled integers a real analog input card delivers. The HMI and the simulator do the scaling.

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| SyrupLevel | INT (L) | %MW12 | HR 1036 | Syrup tank level (low → `Alm_LowSyrup`) |
| MixLevel | INT (L) | %MW13 | HR 1037 | Mixing tank level (dosing, agitator interlock, overflow) |
| ProductTemp | INT (0.1 °C) | %MW14 | HR 1038 | Product temperature in the mixing tank, e.g. 40 = 4.0 °C |
| CO2Pressure | INT (0.01 bar) | %MW15 | HR 1039 | Carbonator pressure, e.g. 350 = 3.50 bar |
| BufferLevel | INT (L) | %MW16 | HR 1040 | Buffer tank level (new batch when low; `Product_Available`) |
| – | – | %MW17–19 | HR 1041–1043 | Spare analog |

No water level: water comes from the mains (treated-water supply line), always available (decision 2026-11-09).

## Digital sensors (written by the simulator)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| MixHighHigh | BOOL | %MX0.0 | coil 8192 | High-high level switch on the mixing tank (TRUE = liquid at the switch, about 980 L, above `MixMax`). Hardware backup for the analog `MixLevel`: `Alm_Overflow` and the pump interlock still work if the level sensor is stuck |
| – | BOOL | %MX0.1–0.7 | coils 8193–8199 | Spare |

## Signals from other machines (written by the simulator)

| Name | Type | Address | Modbus | Meaning | Until the source exists |
| --- | --- | --- | --- | --- | --- |
| – | BOOL | %MX1.0 | coil 8200 | Not used (in the filler: `Product_Available` in) – kept free so the layout stays the same | – |
| E_Stop_OK | BOOL | %MX1.1 | coil 8201 | E-stop chain healthy (FALSE = stop) | Held TRUE by simulator |
| Heartbeat | BOOL | %MX1.2 | coil 8202 | Toggles every 1 s | Toggled by simulator |

## Signals to other machines (written by the PLC, copied by the simulator)

| Name | Type | Address | Modbus | Meaning | Copied to |
| --- | --- | --- | --- | --- | --- |
| Product_Available | BOOL | %MX6.0 | coil 8240 | Buffer holds product: hysteresis on `BufferLevel` (set at ≥ BufferMin + 20 L, reset at < BufferMin; `state-diagram.md` rule 2) | Filler `%MX1.0` (coil 8200) |

The first report draft had it in `%MW60`; the filler stores its booleans in `%MX`, so a coil fits better (decision 2026-11-08, `progress.md`; report 6.2 updated 2026-11-09). Only the buffer level counts: the buffer has no pressure sensor, and only carbonated product is ever transferred.

## Operator commands and setpoints (written by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Cmd | INT | %MW0 | HR 1024 | 1 = Start, 2 = Stop, 3 = Reset |
| ModeReq | INT | %MW1 | HR 1025 | 0 = Stop, 1 = Auto, 2 = Manual |
| WaterBatch | INT (L) | %MW30 | HR 1054 | Water per batch |
| SyrupRatio | INT (×10) | %MW31 | HR 1055 | Water : syrup ratio ×10, e.g. 55 = 5.5:1 (`SyrupTarget` = `WaterBatch` × 10 / `SyrupRatio`) |
| MixTime | INT (s) | %MW32 | HR 1056 | Agitator run time in MIX (s, not ms: INT ms tops out at 32.7 s) |
| CoolSetpoint | INT (0.1 °C) | %MW33 | HR 1057 | Target product temperature, e.g. 40 = 4.0 °C |
| TempHyst | INT (0.1 °C) | %MW34 | HR 1058 | Hysteresis band for cooling |
| PressSetpoint | INT (0.01 bar) | %MW35 | HR 1059 | Target CO2 pressure |
| PressHyst | INT (0.01 bar) | %MW36 | HR 1060 | Hysteresis band for the CO2 valve (`CO2_Demand`) |
| BufferLow | INT (L) | %MW37 | HR 1061 | Start a new batch when BufferLevel < BufferLow |
| BufferMin | INT (L) | %MW38 | HR 1062 | Minimum for `Product_Available` |
| CarbTime | INT (s) | %MW39 | HR 1063 | Pressure must hold this long without the CO2 valve = product saturated (`state-diagram.md` rule 3) |

Defaults copied in on the first scan (`Def_…` → setpoints, as in the filler).

Manual buttons (hold-to-run, Manual mode only, interlocks still apply, jog watchdog as in the filler):

| Name | Type | Address | Modbus | Drives |
| --- | --- | --- | --- | --- |
| Man_WaterPump | BOOL | %MX2.0 | coil 8208 | WaterPump |
| Man_SyrupPump | BOOL | %MX2.1 | coil 8209 | SyrupPump |
| Man_Agitator | BOOL | %MX2.2 | coil 8210 | Agitator |
| Man_Cooling | BOOL | %MX2.3 | coil 8211 | CoolingValve |
| Man_CO2 | BOOL | %MX2.4 | coil 8212 | CO2Valve |
| Man_Transfer | BOOL | %MX2.5 | coil 8213 | TransferPump |

## Actuators (PLC outputs)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| WaterPump | BOOL | %QX0.0 | coil 0 | Mains water → mixing tank |
| SyrupPump | BOOL | %QX0.1 | coil 1 | Syrup tank → mixing tank |
| Agitator | BOOL | %QX0.2 | coil 2 | Mixer motor (interlock: MixLevel above minimum) |
| CoolingValve | BOOL | %QX0.3 | coil 3 | Cooling jacket of the mixing tank |
| CO2Valve | BOOL | %QX0.4 | coil 4 | CO2 into the carbonator (interlock: ProductTemp below limit) |
| TransferPump | BOOL | %QX0.5 | coil 5 | Mixing tank → buffer tank |

## Status and alarms (written by the PLC, read by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| State | INT | %MW50 | HR 1074 | 0 Stopped, 1 Running, 2 Starved (ingredients missing), 3 Blocked (batch does not fit into the buffer), 9 Fault (`state-diagram.md` rule 5) |
| Step | INT | %MW51 | HR 1075 | 0 IDLE, 5 WAIT_DEMAND, 10 DOSE_WATER, 20 DOSE_SYRUP, 30 MIX, 40 COOL, 50 CARBONATE, 55 WAIT_BUFFER, 60 TRANSFER, 900 FAULT |
| BatchCount | INT | %MW53 | HR 1077 | Batches transferred to the buffer |
| Alm_Overflow | BOOL | %MX5.0 | coil 8232 | MixLevel or BufferLevel above max (→ FAULT) |
| Alm_OverPressure | BOOL | %MX5.1 | coil 8233 | CO2Pressure above max (→ FAULT) |
| Alm_CoolTimeout | BOOL | %MX5.2 | coil 8234 | CoolSetpoint not reached in time (→ FAULT?) |
| Alm_CommLost | BOOL | %MX5.3 | coil 8235 | Heartbeat not toggled for 3 s (→ outputs off via `Master_Enable`) |
| Alm_LowSyrup | BOOL | %MX5.4 | coil 8236 | SyrupLevel too low for the next batch: **warning only**, State Starved, clears itself after refill |
| Alm_DoseTimeout | BOOL | %MX5.5 | coil 8237 | DOSE_WATER or DOSE_SYRUP did not reach its level within `DoseTimeout` (pump failure) (→ FAULT) |

`Alm_CommLost` sits at `%MX5.3` in **every** PLC, the same bit as in the filler (decision 2026-11-09), so comm loss can be checked the same way on each machine. The other alarm bits are per machine (`plcs.py` needs an alarm list per machine anyway).

## Computed and conditioned values (written by the PLC)

Located so tests, the debugger and the HMI can read them.

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| SyrupTarget | INT (L) | %MW40 | HR 1064 | WaterBatch × 10 / SyrupRatio (ladder `MUL`, then `DIV`) – week VIII math |
| DoseTarget | INT (L) | %MW41 | HR 1065 | WaterBatch + SyrupTarget (`ADD`): level at the end of DOSE_SYRUP |
| Temp_OK | BOOL | %MX3.0 | coil 8216 | Hysteresis latch: set at ≤ CoolSetpoint, reset at > CoolSetpoint + TempHyst |
| CO2_Demand | BOOL | %MX3.1 | coil 8217 | Hysteresis latch: set at < PressSetpoint − PressHyst, reset at ≥ PressSetpoint (opens the CO2 valve) |
| Jog_Block | BOOL | %MX3.2 | coil 8218 | Jog watchdog, same as the filler |
| Batch_Dosed | BOOL | %MX3.3 | coil 8219 | Water and syrup of the batch in the tank are dosed: set at the end of DOSE_SYRUP, reset at the end of TRANSFER; decides where a restart continues (`state-diagram.md` rule 6) |

Internal only (no address): `Ingredients_OK`, `Buffer_Room`, step bits, timers, the tank-size constants (`state-diagram.md`, "Fixed constants").

## Animation data

Probably none: tank levels, temperature and pressure are real PLC values, so FUXA reads them straight from `%MW12–16`. `%MW70–79` stay free **(?)** – decided at the HMI step.

## Open questions

1. ~~Units / scaling~~ → INT fixed-point (L, 0.1 °C, 0.01 bar), no REAL (2026-11-09).
2. ~~SyrupRatio~~ → ×10 (55 = 5.5:1): `MUL` before the `DIV`, integer truncation as a teaching point (2026-11-09).
3. ~~One vessel or two?~~ → one vessel, mixing tank = carbonator (decision 2026-11-08, `progress.md`).
4. ~~Water supply~~ → mains water, `WaterLevel` removed, analog sensors now `%MW12–16` (2026-11-09).
5. ~~Starved / Blocked meaning~~ → Starved = ingredients missing, Blocked = WAIT_BUFFER, full buffer in WAIT_DEMAND = Running (`state-diagram.md` rule 5).
6. ~~`Product_Available` address~~ → `%MX6.0`; report 6.2 still to update.
7. ~~Alarm bit positions~~ → `Alm_CommLost` at `%MX5.3` in every PLC, other alarms per machine (2026-11-09).
8. ~~Digital level switches~~ → one high-high switch `MixHighHigh` (%MX0.0) on the mixing tank (2026-11-09).
9. ~~`PressHyst` doing two jobs~~ → only the CO2 valve band (`CO2_Demand`); `Product_Available` no longer uses pressure.
