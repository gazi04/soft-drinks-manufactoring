# Packer – I/O list

Draft 2026-11-21 (second version: collation with a held gate and a CTU to 6; **film-slot wrapper** – film fed to length and cut first, the moving pack drives onto the sheet, a wrap bar lays it over the top; oven with heater + blower + oven conveyor; outfeed table). Answers Q1–Q6 confirmed 2026-11-21, wrap redesign and oven rules from the review of 2026-11-21 (`progress.md`, decisions).
Design reference: `docs/project-report.md` sections 4.5 (machine), 5.3 (conveyor full sensors), 6.2 (register layout), 9 (modes/alarms). Concept and decisions: `progress.md`. Steps and rules: `state-diagram.md`.

## Address rules

Same as the filler, the syrup room and the labeler (`docs/filler/io-list.md`, "Address rules"), so `address_map.py`, `plcs.py` and the FUXA generators can be reused:

- Booleans → `%MX` (coils from 8192), numbers → `%MW` (holding registers from 1024), physical outputs → `%QX` (coils from 0).
- Sensors are written by the simulator into `%MX` / `%MW`, not `%IX`.
- Project setting **`mx_bits` = 64** in *Device → Servers → RuntimeServer* (covers `%MX0.0`–`%MX7.7`).
- Own runtime container `plc-packer`: Modbus host port **5024**, editor at **`127.0.0.4:8443`** (report 6.1). The addresses below are this PLC's own memory and may overlap with the other PLCs'.

Same byte group as the other machines for the same job (`%MX0` + `%MX4` sensors, `%MX1` wiring, `%MX2` manual, `%MX3` conditioned, `%MX5` alarms; `%MW30–39` setpoints, `%MW40–49` computed, `%MW50–53` status). **New for this machine:**
- a **second step word** `WrapStep` in `%MW52` (the word between `Step` and the counter, unused in every PLC so far), because two sequencers run in parallel (Q1);
- **10 alarm bits**, two more than `%MX5` holds, so the ninth and tenth go into **`%MX7.0–7.1`** (free in the standard layout, inside `mx_bits` = 64);
- 14 digital sensors: `%MX0` full, six more in `%MX4`.

## Digital sensors (written by the simulator)

Descriptive names, as the labeler's.

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Bottle_At_Gate | BOOL | %MX0.0 | coil 8192 | Bottle waiting at the infeed gate (end of C3); used only for the Starved filter |
| Bottle_Counted | BOOL | %MX0.1 | coil 8193 | Count sensor right behind the gate: ON while a bottle passes, one pulse per bottle (the CTU counts the rising edge) |
| Pusher_Home | BOOL | %MX0.2 | coil 8194 | Group pusher retracted (home); the gate only opens with the pusher home |
| Pusher_Out | BOOL | %MX0.3 | coil 8195 | Group pusher fully out: the group is on the wrap conveyor (ends PUSH) |
| Pack_At_Film | BOOL | %MX0.4 | coil 8196 | Pack at the stop point right before the film slot: the wrap conveyor holds it here until sheet, oven and outfeed are ready |
| Film_In_Slot | BOOL | %MX0.5 | coil 8197 | Film sheet sticks out of the slot: "slot empty" before a new feed, "sheet there" before the pack drives on |
| Film_Pulse | BOOL | %MX0.6 | coil 8198 | Pulse sensor (encoder) on the film feed roller: one pulse per 50 mm of film (`FilmPulseMM`) |
| Wrap_Home | BOOL | %MX0.7 | coil 8199 | Wrap bar at its home position (below the conveyor, out of the way); one wrap = leave home, come back (one turn) |
| Film_Empty | BOOL | %MX4.0 | coil 8224 | Film roll empty: no new sheet is started (the roll's tail still finishes a started sheet) |
| Film_Low | BOOL | %MX4.1 | coil 8225 | Film roll low (diameter sensor): prepare the next roll – warning in FUXA only |
| Oven_Entry | BOOL | %MX4.2 | coil 8226 | Pack at the oven entry (pulse per pack): CTUD counts up, ends the wrap sequence |
| Oven_Exit | BOOL | %MX4.3 | coil 8227 | Pack at the oven exit (pulse per pack): CTUD counts down, `PackCount` counts up |
| Table_Full | BOOL | %MX4.4 | coil 8228 | Outfeed table full (sensor placed so that the table still has room for every pack the oven and the wrap conveyor can hold, Q3) |
| Airflow_OK | BOOL | %MX4.5 | coil 8229 | Air flow switch in the oven duct: the blower really blows; the heater interlock uses this feedback, not the blower command (Q6) |
| – | BOOL | %MX4.6–4.7 | coils 8230–8231 | Spare |

`C3_Full` is **not** a packer input: it is wired to the labeler (report 5.3), which already reads it. The packer only drives C3's motor.

## Analog sensors (written by the simulator)

INT with fixed-point units, no REAL (same decision as the syrup room and the labeler).

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| OvenTemp | INT (0.1 °C) | %MW12 | HR 1036 | Air temperature in the oven, e.g. 1900 = 190.0 °C |
| – | – | %MW13–19 | HR 1037–1043 | Spare analog |

## Signals from other machines (written by the simulator)

| Name | Type | Address | Modbus | Meaning | Until the source exists |
| --- | --- | --- | --- | --- | --- |
| – | BOOL | %MX1.0 | coil 8200 | Not used (filler: `Product_Available` in) – kept free so the layout stays the same | – |
| E_Stop_OK | BOOL | %MX1.1 | coil 8201 | E-stop chain healthy (FALSE = stop) | Held TRUE by simulator |
| Heartbeat | BOOL | %MX1.2 | coil 8202 | Toggles every 1 s | Toggled by simulator |

No signal to other machines. The packer's link to the labeler is C3 (`C3_Full`, wired to the labeler); downstream there is only the palletizer stand-in in the simulator, which takes packs off the outfeed table.

## Operator commands and setpoints (written by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Cmd | INT | %MW0 | HR 1024 | 1 = Start, 2 = Stop, 3 = Reset (alarms), 4 = Reset counter (`PackCount` → 0, as the labeler, Q4) |
| ModeReq | INT | %MW1 | HR 1025 | 0 = Stop, 1 = Auto, 2 = Manual |
| OvenSetpoint | INT (0.1 °C) | %MW30 | HR 1054 | Oven air temperature, default 1900 = 190 °C |
| OvenHyst | INT (0.1 °C) | %MW31 | HR 1055 | Heater hysteresis band, default 50 = 5 °C |
| OvenBand | INT (0.1 °C) | %MW32 | HR 1056 | `Oven_Ready` window around the setpoint, default 150 = ±15 °C (must be wider than `OvenHyst`) |
| FilmLength | INT (mm) | %MW33 | HR 1057 | Length of one film sheet, chosen by the operator per product (recipe value), default 850 mm; HMI limits 600–1200 mm |

Defaults copied in on the first scan (`Def_…` → setpoints, as in the other machines). The pack size (6) is a constant, not a setpoint (decision 2026-11-21). Further constants (pulse length, over-temperature limit, blower run-on, timeouts) are listed in `state-diagram.md`.

Manual buttons (hold-to-run, Manual mode only, interlocks still apply, jog watchdog as in the filler). Heater and blower have no button: the oven runs only in Auto (`state-diagram.md` rule 6).

| Name | Type | Address | Modbus | Drives |
| --- | --- | --- | --- | --- |
| Man_C3 | BOOL | %MX2.0 | coil 8208 | ConveyorMotor3 |
| Man_Gate | BOOL | %MX2.1 | coil 8209 | GateMotor |
| Man_GroupPush | BOOL | %MX2.2 | coil 8210 | GroupPusher |
| Man_WrapConveyor | BOOL | %MX2.3 | coil 8211 | WrapConveyor |
| Man_FilmFeed | BOOL | %MX2.4 | coil 8212 | FilmFeed |
| Man_Knife | BOOL | %MX2.5 | coil 8213 | Knife |
| Man_WrapBar | BOOL | %MX2.6 | coil 8214 | WrapBar |
| Man_OvenConveyor | BOOL | %MX2.7 | coil 8215 | OvenConveyor |

## Actuators (PLC outputs)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| ConveyorMotor3 | BOOL | %QX0.0 | coil 0 | C3 infeed conveyor: runs continuously, bottles queue at the closed gate (report 5.3 point 3; takes over from the simulator's `PackerStandIn`) |
| GateMotor | BOOL | %QX0.1 | coil 1 | Infeed gate open, **held** while the group is not complete (not a TP pulse as the labeler's): bottles run through and are counted; it closes when the 6th is counted |
| GroupPusher | BOOL | %QX0.2 | coil 2 | Pushes the complete group from the collation table onto the wrap conveyor; spring return when OFF |
| WrapConveyor | BOOL | %QX0.3 | coil 3 | Conveyor from the collation table over the film slot to the oven; stops to hold the pack at `Pack_At_Film` |
| FilmFeed | BOOL | %QX0.4 | coil 4 | Feed rollers: push film off the roll up through the slot, until `FilmLength` is counted |
| Knife | BOOL | %QX0.5 | coil 5 | Cuts the sheet off the roll: ON for the whole CUT step, which lasts `CutTime` (0.3 s, step timer; not a TP) |
| WrapBar | BOOL | %QX0.6 | coil 6 | Wrap bar motor, own drive: one turn per pack lifts the back of the sheet over the top of the moving pack |
| OvenHeater | BOOL | %QX0.7 | coil 7 | Oven heater (hysteresis control), **only in Auto** and **only while `Airflow_OK`** |
| OvenBlower | BOOL | %QX1.0 | coil 8 | Oven blower: runs while the oven is on (Auto) and **keeps running `BlowerRunOn` (30 s) after it is switched off (TOF)** – Manual, Mode Stop or FAULT – to cool the heater |
| OvenConveyor | BOOL | %QX1.1 | coil 9 | Conveyor through the oven onto the outfeed table: runs while the oven is on **or** packs are inside (`Oven_On OR Packs_Inside`), so it never stands in the heat and always empties the oven |

## Status and alarms (written by the PLC, read by the HMI)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| State | INT | %MW50 | HR 1074 | 0 Stopped, 1 Running (also WARM_UP), 2 Starved, 3 Blocked, 9 Fault – one state for the whole machine from both sequencers, priority Fault > Stopped > Blocked > Starved > Running (Q1; rule in `state-diagram.md`) |
| Step | INT | %MW51 | HR 1075 | **Collation** sequencer: 0 IDLE, 5 WARM_UP, 10 COUNT, 15 WAIT_WRAP, 20 PUSH, 25 RETRACT, 900 FAULT |
| WrapStep | INT | %MW52 | HR 1076 | **Wrap** sequencer: 0 IDLE, 10 WAIT_PACK, 20 FEED, 22 CUT, 24 READY, 30 RUN_ON, 32 WRAP, 34 RUN_OUT, 900 FAULT |
| PackCount | INT | %MW53 | HR 1077 | Finished packs leaving the oven (counted at `Oven_Exit`; main counter, report 6.2); zeroed by `Cmd = 4`; no separate shift total (Q4) |
| Alm_FilmFault | BOOL | %MX5.0 | coil 8232 | FEED: `FilmLength` not counted within 10 s (film torn, feed roller slipping), or READY: no sheet in the slot for 1 s (→ FAULT) |
| Alm_PackJam | BOOL | %MX5.1 | coil 8233 | A movement did not finish within 3 s: group pusher out / home again, pack off the film stop (RUN_ON), pack at the oven entry (RUN_OUT) (→ FAULT) |
| Alm_OutfeedBlocked | BOOL | %MX5.2 | coil 8234 | Blocked longer than 2 min (warning, no FAULT; same as the filler and the labeler) |
| Alm_CommLost | BOOL | %MX5.3 | coil 8235 | Heartbeat not toggled for 3 s (→ outputs off via `Master_Enable`) |
| Alm_FilmEmpty | BOOL | %MX5.4 | coil 8236 | Film roll empty (`Film_Empty`) and a pack waits for a sheet: **warning only**, State Starved, clears itself after the roll change (as the labeler's `Alm_LabelEmpty`) |
| Alm_OvenOverTemp | BOOL | %MX5.5 | coil 8237 | `OvenTemp` ≥ `OvenMax` (→ FAULT in every mode; heater off, **blower keeps running** on its run-on) |
| Alm_OvenHeatTimeout | BOOL | %MX5.6 | coil 8238 | WARM_UP: `Oven_Ready` not reached within 120 s (heater failed) (→ FAULT) |
| Alm_OvenJam | BOOL | %MX5.7 | coil 8239 | Packs in the oven (`PacksInOven` > 0) and no `Oven_Exit` for `OvenJamTime` (pack stuck in the oven) (→ FAULT) |
| Alm_AirflowLost | BOOL | %MX7.0 | coil 8248 | Blower on and no `Airflow_OK` within 3 s (blower failed, duct blocked) (→ FAULT; the heater is already off by its interlock) |
| Alm_WrapTimeout | BOOL | %MX7.1 | coil 8249 | WRAP: wrap bar not back at `Wrap_Home` within 5 s (bar stuck, film caught) (→ FAULT) |
| – | BOOL | %MX7.2–7.7 | coils 8250–8255 | Spare |

`Alm_CommLost` sits at `%MX5.3`, as in every PLC. The early warning `Film_Low` gets no alarm bit: FUXA raises it straight from the sensor bit (as the labeler's `Roll_Low`). An oven that drifts out of its window while running gets no alarm of its own: the pack waits in READY, a heater that stays off ends in over-temperature or (in WARM_UP) heat-up timeout.

## Conditioned values (written by the PLC)

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| Outfeed_Full | BOOL | %MX3.0 | coil 8216 | Outfeed table really full: `Table_Full` ON 2 s without a break; released after `Table_Full` OFF 3 s (same filter as the other machines' `Outfeed_Full`, report 5.3) |
| Group_Complete | BOOL | %MX3.1 | coil 8217 | Group counter reached 6 (`Group_Counter.Q`): the gate closes; the counter is reset in RETRACT, after the group arrived on the wrap conveyor |
| Jog_Block | BOOL | %MX3.2 | coil 8218 | Jog watchdog, same as the filler |
| Heat_Demand | BOOL | %MX3.3 | coil 8219 | Heater hysteresis: set at `OvenTemp < OvenSetpoint − OvenHyst`, reset at `OvenTemp ≥ OvenSetpoint` (Q2, as the labeler's glue) |
| Oven_Ready | BOOL | %MX3.4 | coil 8220 | `OvenTemp` within `OvenSetpoint ± OvenBand` (window compare): film shrinks properly; a pack only drives onto the film with it (Q2) |
| Starved | BOOL | %MX3.5 | coil 8221 | Wrap waits for a pack and (no bottle at the gate for 2 s, or film empty) (rule in `state-diagram.md`) |
| Wrap_Clear | BOOL | %MX3.6 | coil 8222 | The wrap conveyor has run empty long enough (`ClearTime`) that no earlier group is still on it: the collation may push the next group (`state-diagram.md` rule 2) |
| Oven_On | BOOL | %MX3.7 | coil 8223 | Oven switched on: `Master_Enable AND Auto AND no FAULT`; drives heater, blower (TOF) and oven conveyor (rules 6–8) |

| Name | Type | Address | Modbus | Meaning |
| --- | --- | --- | --- | --- |
| GroupCount | INT | %MW40 | HR 1064 | Bottles in the current group, 0–6 (`Group_Counter.CV`, CTU, PV = 6) – for the HMI |
| PacksInOven | INT | %MW41 | HR 1065 | Packs inside the oven (`Oven_Counter.CV`, **CTUD**: CU = `Oven_Entry`, CD = `Oven_Exit`) – for the HMI, the oven conveyor and `Alm_OvenJam` |
| FeedPulses | INT | %MW42 | HR 1066 | Pulses for one sheet: `LIMIT(600, FilmLength, 1200) / FilmPulseMM` (DIV, INT truncation: 850 / 50 = 17) – PV of `Feed_Counter` |
| FeedCount | INT | %MW43 | HR 1067 | Pulses fed for the current sheet (`Feed_Counter.CV`, CTU): kept through Stop and E-stop, so a restart feeds only the missing rest |

Internal only (no address): step bits of both sequencers, timers (`Gate_Empty_Timer`, `Cut_Timer`, `Clear_Timer`, `Settle_Timer`, `Blower_TOF`, …), `Group_Counter` (CTU), `Feed_Counter` (CTU), `Oven_Counter` (CTUD), `Pack_Counter` (CTU behind `PackCount`), edge bits, `Stop_Request`, the constants of `state-diagram.md`.

## Animation data

Decided with the HMI plan (part C), as for the labeler. Words `%MW70–79` stay free for it (written by the simulator, read by the HMI, ignored by the PLC).

## Answers to the open questions (confirmed 2026-11-21)

1. **Two step words (Q1):** collation in `Step` %MW51, wrap in `WrapStep` %MW52. One `State` for the machine from both, priority Fault > Stopped > Blocked > Starved > Running. Both sequencers go to 900 together on a FAULT.
2. **Oven control (Q2):** the labeler's glue pattern: hysteresis (`Heat_Demand`) plus a ready window (`Oven_Ready`), INT in 0.1 °C, three setpoints, `OvenMax` as a constant. Changed in the review: the oven heats **only in Auto** (below).
3. **Outfeed table full (Q3):** no new pack drives onto the film (the pack waits in READY); packs already past the film continue into the oven, the oven conveyor keeps running. The table-full sensor sits early enough that everything behind it still fits.
4. **Counters (Q4):** `PackCount` (%MW53, finished packs at the oven exit) with `Cmd = 4` reset. No shift total.
5. **Stop and resume (Q5):** started work is finished, the oven empties itself; details in `state-diagram.md` rules 9 and 11.
6. **Alarms (Q6):** FAULT = film fault, pack jam, wrap timeout, oven over-temperature (every mode), oven heat-up timeout, oven jam, airflow lost. Warnings = film empty (Starved, not latched), outfeed blocked > 2 min. HMI-only early warning = film low. Airflow switch `Airflow_OK` + `Alm_AirflowLost` kept.

## Review decisions (2026-11-21)

- **Film cut first, film-slot wrapper** (the machine the user saw): the sheet is fed to length and cut **before** the pack arrives; the moving pack drives onto the sheet, a wrap bar on its own motor lays the back of the sheet over the top. No wrap station, no transfer pusher, no `Pack_Wrapped` memory bit: the pack only moves forward, a restart can never wrap it twice.
- **Film length = operator setpoint** `FilmLength` (mm, recipe value), **measured** by counting `Film_Pulse` with a CTU (`FeedPulses = FilmLength / 50`); the count is kept through a stop, so a restart feeds only the rest.
- **Feed only when a pack is waiting** at `Pack_At_Film`, so a sheet never waits alone; **`Film_In_Slot`** checks "slot empty" before a feed and "sheet there" before the pack drives on.
- **Knife:** CUT is its own step, knife ON, ended by a 0.3 s TON (option A).
- **Oven heats only in Auto** (off in Manual and Mode Stop; Cmd Stop in Auto keeps it hot).
- **Oven conveyor** := `Oven_On OR Packs_Inside`: always running in a hot oven, and empties the oven after the switch-off.
