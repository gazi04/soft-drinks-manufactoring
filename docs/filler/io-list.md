# Filler machine – I/O list

Draft, 2026-10-28. Working names; final descriptive names are chosen at implementation.

## Address rules (OpenPLC v4, default segment sizes)

- **Booleans → `%MX` (Boolean memory).** One BOOL per signal, so ladder contacts and coils use them directly (no bits packed inside words). Read/write over Modbus as **coils**.
- **Numbers → `%MW` (16-bit memory).** Read/write over Modbus as **holding registers**.
- **Physical outputs → `%QX`.** Read over Modbus as coils (written only by the PLC program).
- Sensors are not `%IX`: external Modbus clients cannot write `%IX`, so the simulator writes `%MX` / `%MW` and the PLC reads them as its sensors.

Modbus numbers (0-based):

```
%QXa.b  → coil a×8 + b                      (%QX0.0 = coil 0)
%MXa.b  → coil 8192 + a×8 + b               (%MX0.0 = coil 8192)
%MWn    → holding register 1024 + n         (%MW0  = HR 1024)
%MDn    → holding registers 2048 + 2n (2)   (%MD0  = HR 2048–2049, 32 bit, DINT)
%MLn    → holding registers 4096 + 4n (4)   (%ML0  = HR 4096–4099, 64 bit, LINT)
```

32/64-bit values: the **first register is the high word** (`value = HR[n] × 65536 + HR[n+1]`). Verified 2026-10-30 with the FB tests.

Source: OpenPLC v4 documentation (`%QX` max 8192 bits, then `%MX`; `%QW` max 1024 registers, then `%MW`). **Verified 2026-10-28 with `smoke_test.py`** (all checks pass).

**Required project setting:** `%MX` is not exposed over Modbus by default. In the OpenPLC Editor, *Device → Servers → RuntimeServer*, set **`mx_bits` = 64** (covers `%MX0.0`–`%MX7.7`). Without it, writes to coil 8192+ are silently ignored. The setting is part of the project and is applied on every upload.

## Sensors (written by the simulator)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| S2 | BOOL | %MX0.0 | coil 8192 | Bottle waiting at the entry door |
| S3 | BOOL | %MX0.1 | coil 8193 | Bottle at the rinse station |
| S4 | BOOL | %MX0.2 | coil 8194 | Bottle at the fill station |
| S5 | BOOL | %MX0.3 | coil 8195 | Bottle at the cap station |
| S6 | BOOL | %MX0.4 | coil 8196 | Bottle discharged (counts finished bottles) |
| S7 | BOOL | %MX0.5 | coil 8197 | C2 (output conveyor) full |
| S8 | BOOL | %MX0.6 | coil 8198 | Index sensor: cam on the internal belt, ON once per station pitch (belt moved one station) |
| FillLevel | INT (ml) | %MW12 | HR 1036 | Liquid level in the bottle at the fill position |

S1 (C1 full) is **not** a filler input: it is wired to the blow moulder.

## Signals from other machines (written by the simulator)

| Name | Type | Address | Modbus | Meaning | Until the source exists |
| --- | --- | --- | --- | --- | --- |
| Product_Available | BOOL | %MX1.0 | coil 8200 | Syrup room has carbonated product ready | Held TRUE by simulator |
| E_Stop_OK | BOOL | %MX1.1 | coil 8201 | E-stop chain healthy (FALSE = stop) | Held TRUE by simulator |
| Heartbeat | BOOL | %MX1.2 | coil 8202 | Toggles every 1 s | Toggled by simulator |

## Operator commands and setpoints (written by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Cmd | INT | %MW0 | HR 1024 | 1 = Start, 2 = Stop, 3 = Reset |
| ModeReq | INT | %MW1 | HR 1025 | 0 = Stop, 1 = Auto, 2 = Manual |
| FillSetpoint | INT (ml) | %MW30 | HR 1054 | Target fill level, e.g. 500 |
| RinseTime | INT (ms) | %MW31 | HR 1055 | Rinse duration |
| CapTime | INT (ms) | %MW32 | HR 1056 | Capping duration |

Manual buttons (hold-to-run, Manual mode only, interlocks still apply):

| Name | Type | Address | Modbus | Drives |
| --- | --- | --- | --- | --- |
| Man_C1 | BOOL | %MX2.0 | coil 8208 | ConveyerMotor1 |
| Man_Door | BOOL | %MX2.1 | coil 8209 | DoorMotor |
| Man_Rinse | BOOL | %MX2.2 | coil 8210 | RinseValve |
| Man_Fill | BOOL | %MX2.3 | coil 8211 | FillValve |
| Man_Cap | BOOL | %MX2.4 | coil 8212 | CapMotor |
| Man_Index | BOOL | %MX2.5 | coil 8213 | IndexMotor |

Until FUXA exists, these are forced from the OpenPLC Editor debugger.

## Actuators (PLC outputs)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| ConveyerMotor1 | BOOL | %QX0.0 | coil 0 | C1 infeed conveyor: runs continuously, bottles queue at the closed door |
| DoorMotor | BOOL | %QX0.1 | coil 1 | Opens the entry door (TP pulse, admits one bottle) |
| RinseValve | BOOL | %QX0.2 | coil 2 | Rinse valve at S3 |
| FillValve | BOOL | %QX0.3 | coil 3 | Fill valve at S4 |
| CapMotor | BOOL | %QX0.4 | coil 4 | Capping head at S5 |
| IndexMotor | BOOL | %QX0.5 | coil 5 | Internal belt: moves all bottles one station forward per INDEX |

The output conveyor motor (C2) belongs to the labeler; until the labeler exists, the simulator keeps C2 running.

## Status and alarms (written by the PLC, read by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| State | INT | %MW50 | HR 1074 | 0 Stopped, 1 Running, 2 Starved, 3 Blocked, 9 Fault |
| Step | INT | %MW51 | HR 1075 | Current sequencer step (0, 10, 15, 20, 900) |
| GoodCount | INT | %MW53 | HR 1077 | Bottles discharged (S6) |
| Alm_IndexJam | BOOL | %MX5.0 | coil 8232 | No S8 pulse within 5 s in INDEX (→ FAULT) |
| Alm_FillTimeout | BOOL | %MX5.1 | coil 8233 | Fill setpoint not reached within 10 s of open valve (→ FAULT) |
| Alm_OutfeedBlocked | BOOL | %MX5.2 | coil 8234 | Blocked longer than 2 min (warning, no FAULT) |
| Alm_CommLost | BOOL | %MX5.3 | coil 8235 | Heartbeat not toggled for 3 s |

Alarm bits are latched by the PLC and cleared with Reset (`Cmd = 3`) once the cause is gone.

## Conditioned signals (written by the PLC, stage 3)

Cleaned-up versions of raw sensors (`ladder-roadmap.md` stage 3). The sequencer uses these, never the raw sensor.

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Outfeed_Full | BOOL | %MX3.0 | coil 8216 | C2 really full: S7 ON 2 s without a break; released after S7 OFF 3 s (`state-diagram.md` rule 5) |
| S6_Edge | BOOL | – | – | One scan per discharged bottle (rising edge of S6) |
| S8_Edge | BOOL | – | – | One scan per index step (rising edge of S8) |

`Outfeed_Full` has an address so the test script and the HMI ("C2 full") can read it. Helper timers (`Full_OnTimer`, `Full_OffTimer`) are internal.

## Test variables (TEST ONLY: all removed from the project in stage 5b, 2026-11-02)

Located only so the Modbus test scripts in `simulator/tests/` could reach them. `%MX7.x` and `%MD`/`%ML` stay reserved for tests. Kept here as history: `test_fb_motor.py` and `test_fb_valve.py` were removed on 2026-11-02 (in git history); the S6 edge check in `test_signal_conditioning.py` moves to `GoodCount` in stage 6.

| Name | Type | Address | Modbus | Test |
| --- | --- | --- | --- | --- |
| testrun / testenable / testout | BOOL | %MX7.0 / 7.1 / 7.2 | coils 8248–8250 | `test_fb_motor.py` |
| teststart / testruntime | DINT | %MD0 / %MD1 | HR 2048–2051 | `test_fb_motor.py` |
| testopen / testvalveenable / testvalveout | BOOL | %MX7.3 / 7.4 / 7.5 | coils 8251–8253 | `test_fb_valve.py` |
| testcycles | DINT | %MD2 | HR 2052–2053 | `test_fb_valve.py` |
| testopentime / testlastopen | LINT | %ML0 / %ML1 | HR 4096–4103 | `test_fb_valve.py` |
| test_edgecount | DINT | %MD3 | HR 2054–2055 | `test_signal_conditioning.py` (counts `S6_Edge`) |
