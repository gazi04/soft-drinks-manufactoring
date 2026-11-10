# FUXA HMI – Roadmap

How to build the operator screens in FUXA, in small stages. Each stage builds on the one before. Design: `docs/project-report.md` section 8. Addresses: `docs/filler/io-list.md`. Machine behaviour: `docs/filler/state-diagram.md`. The stage checklist is ticked here and in `docs/filler/progress.md` section 3.

**Status:** stages 0–6 done; issues closed except I16 (≈1–2 % of FUXA's Modbus writes time out, older than stage 6). **Next:** 7 – Demo and wrap-up.

## Goal

The filler can be run from the browser alone, without `tests/hmi.py` and without the OpenPLC Editor debugger:

- **Screens** (report 8.1, filler part only): a header on every screen, a line overview with C1, the filler and C2, a filler detail screen, an alarm screen, and trends (optional).
- **Operator actions** (report 8.4): Start, Stop, Reset, mode Auto/Manual/Stop, setpoints (fill level, rinse time, cap time), hold-to-run manual buttons.
- **Animation** (report 8.3): bottles step along C1 and C2 from the slot bits, the filler pockets show their bottle state, and the belt moves only while its motor is ON.
- **Alarms** (report 9.3): the four filler alarms in FUXA's alarm list with acknowledge and history, plus Reset.
- **Look** (report 8.5): ISA-101. Grey background and muted drawings; strong colour only for abnormal states; a unit on every value.

Done means: every stage below is ticked, every fault scenario of the simulator can be followed on the screens, and the project export is committed.

The other four machines are not part of this roadmap. They are drawn as grey stand-in blocks on the overview. Stage 4 keeps the layout ready for them (see issue I11).

## Rules

- **FUXA only talks to the PLC.** Everything it shows comes from PLC memory. It never talks to the simulator (report section 3).
- **One writer per address.** FUXA owns `Cmd`, `ModeReq`, `FillSetpoint`, `RinseTime`, `CapTime` and `Man_*`. FUXA only reads everything else: sensors, outputs, state, alarms and animation words. Do not use `tests/hmi.py` or the PLC test scripts while FUXA is controlling the machine (issue I4).
- **Commands are short writes.** A button writes `Cmd` = 1 (Start), 2 (Stop) or 3 (Reset). The PLC sets `Cmd` back to 0 after reading it, so each press acts once.
- **Names.** Tag names are the IEC variable names from `io-list.md` (`State`, `Alm_IndexJam`, `Anim_C1`, …), so a tag can always be found in the ladder.
- **The PLC decides.** A disabled button in FUXA is only a convenience. The PLC still checks the mode and the interlocks for every manual command (state diagram section 8).

## Working rule for every stage

1. **Explain first:** what the stage shows or sends and why, and which tags it uses.
2. **You build it** in the FUXA editor (http://localhost:1881). I show the steps and check the result. I don't build the screens for you.
3. **Test against the real PLC with the simulator running** (`uv run simulator`). Use the scenario named in the stage.
4. **Export the project** to the committed export file (stage 0), then tick the stage here and in `progress.md`, and commit.

Never start the next stage with a failing test.

---

## Stage 0 – Setup and versioning

- Pin the FUXA image to a fixed version in `docker-compose.yml` instead of `latest`, and write the version here (issue I6).
- Replace the old test project (`docs/fuxa-project-v2.json`, one view `MainView`, two test tags from 2026-10-25) with a fresh, empty project.
- Version control (issue I5):
  - Commit only a project export, `docs/hmi/fuxa-project.json`. It is not inside `hmi/`, because `hmi/` belongs to root and you can't write there without sudo.
  - Put the live folder `hmi/` into `.gitignore`.
  - Delete the old `docs/fuxa-project-v2.json` once the new export exists.
- Settings: language, no Node-RED, and keep the DAQ store at SQLite.

**Test:** FUXA opens at :1881 after `docker compose up -d`. Exporting and re-importing gives the same project.

**As built (2026-11-03):**
- **Image:** `frangoteam/fuxa:1.3.5` (server build 1.3.5-2968). It's the same build that had been running as `latest` and that created the existing data. `latest` on Docker Hub is a newer, untested build.
- **Fresh project:**
  - Built from FUXA's own project JSON. Kept: `version`, `server`, the FUXA server device (its tags emptied) and `hmi.layout`. Removed: the old test view, its start-view reference, and the old OpenPLC device.
  - Loaded with `POST /api/project`.
  - The old DBs are backed up outside the repo, and the old export is still in git history.
- **Export and import** use FUXA's project API. There is no login, so no token is needed:

  ```bash
  # export (do this after every change in the editor, before committing)
  curl -s http://localhost:1881/api/project | python3 -m json.tool --sort-keys > docs/hmi/fuxa-project.json
  # import (replaces the whole project in FUXA)
  curl -X POST -H 'Content-Type: application/json' --data @docs/hmi/fuxa-project.json http://localhost:1881/api/project
  ```

  The editor can do the same with Save project / Open project in its project menu. That file has the same content. The curl version is used here because `--sort-keys` keeps the git diffs readable.
- **`.gitignore`:** `/hmi/`, anchored to the repo root. The first try was `hmi/`, which also hid `docs/hmi/`.
- **Settings** (`hmi/mysettings.json`, not versioned): the defaults are fine. Language en, Node-RED off, DAQ store SQLite.
- **Test:**
  - Export → import → export: identical.
  - After `docker compose restart fuxa`: still identical.
  - `/editor/` and `/` return 200, and the FUXA log has no errors.

## Stage 1 – Modbus device and tags

- **Device:**
  - Modbus TCP `openplc:502`, the Docker service name (FUXA runs in its own container, so it cannot use `127.0.0.1`).
  - Slave id 1.
  - Polling about 250 ms (issue I7).
- **Probe first (issue I1):**
  - Create one coil tag (`S2`, coil 8192) and one register tag (`FillSetpoint`, HR 1054).
  - Find out whether FUXA counts addresses from 0 or from 1. The value must match `uv run python tests/hmi.py status`.
- **Then one tag per HMI address**, grouped as in `io-list.md`:
  - **Status (read):** `State`, `Step`, `GoodCount`, `ModeReq` (also written).
  - **Alarms (read):** `Alm_IndexJam`, `Alm_FillTimeout`, `Alm_OutfeedBlocked`, `Alm_CommLost`.
  - **Sensors (read):** S2–S8, `FillLevel`, `Product_Available`, `E_Stop_OK`, `Outfeed_Full`.
  - **Outputs (read):** `ConveyerMotor1`, `DoorMotor`, `RinseValve`, `FillValve`, `CapMotor`, `IndexMotor`.
  - **Commands and setpoints (write):** `Cmd`, `ModeReq`, `FillSetpoint`, `RinseTime`, `CapTime`.
  - **Manual (write):** `Man_C1`, `Man_Door`, `Man_Rinse`, `Man_Fill`, `Man_Cap`, `Man_Index`.
  - **Animation (read):** `Anim_C1`, `Anim_C2`, `Anim_Entry`, `Anim_Rinse`, `Anim_Fill`, `Anim_Cap`. Find out here how to use single bits of `Anim_C1/C2` (issue I2).
- A test page (not part of the final screens) that shows every tag as a plain value.

**Test:**
- With the simulator running, every tag on the test page shows the same value as `tests/hmi.py status` and the simulator panel.
- A write to `FillSetpoint` from FUXA is seen by the PLC: the panel's fill level stops at the new value.
- Optional: a script `simulator/tests/test_hmi_tags.py` reads the export file and checks every tag address against `simulator/src/simulator/address_map.py`. It catches a typo in an address before it confuses a screen.

**As built (2026-11-03):**
- **I1 answered from FUXA's code** (1.3.5, `runtime/devices/modbus/index.js`): `offset = address - 1`, "settings address from 1 to 65536 but communication start from 0". So **FUXA address = Modbus number + 1**: S2 (coil 8192) is FUXA 8193, FillSetpoint (HR 1054) is FUXA 1055. `memaddress` picks the area: `000000` coils, `400000` holding registers.
- **I2 answered from FUXA's code:** a shape property has `variableId` + `bitmask` (the editor has a bitmask dialog for 32 bits). One `Int16` tag per animation word is enough; the bit is chosen on the shape in stage 4.
- **Polling 200 ms:** the editor offers 50/100/200/350/500 ms (no 250).
- **Tags generated, not typed:**
  - `simulator/tests/fuxa_tags.py` builds the device `Filler PLC` (`d_filler`, `openplc:502`, slave 1, 200 ms) with **43 tags** from `address_map.py` (44 since the jog watchdog tag `Jog_Block`, issue I3). The tag names are the IEC names, and each description says who writes the tag and what it means.
  - It also builds the FUXA server tag `Filler PLC Connection Status` (0 off, 3 warning, 5 connected).
  - The tag ids are fixed (`t_filler_<name>`), because the screens bind by id. A second run gives the same file.
  - It starts from FUXA's live project, so views made in the editor are kept, and replaces only this device.
  - Order: **export → `uv run python tests/fuxa_tags.py` → import** (the curl in the script and in `CLAUDE.md`).
  - Why generated: the I/O list would otherwise exist three times (io-list, address_map, FUXA), and a typo in an editor field shows a wrong value without any error.
- **Read only vs write:** a FUXA Modbus tag has no access flag. Which tags a screen may write is decided on the shape (stages 2–3). Only `Cmd`, `ModeReq`, the setpoints and `Man_*` get input shapes.
- `address_map.py` got `OUTFEED_FULL` (%MX3.0) and `ENTRY_LOAD` (%MX3.1), the conditioned PLC signals the HMI shows.
- **`simulator/tests/test_hmi_tags.py`:**
  - H1 (export: every `address_map` signal has exactly one tag, address + 1, area, type, status tag) passes. It works without a PLC (`--offline`).
  - Mutation check: an S4 tag moved by one and a deleted `Anim_Cap` are both reported.
  - **H2–H4 live, 4/4.** H2: FUXA value = direct Modbus value for all 43 tags. H3: FillSetpoint written through FUXA (480) arrives in the PLC and is restored. H4: connection status = 5. All three were run twice: once idle, and once **with bottles moving**. With the machine idle almost every value is 0, so a tag shifted by one address would also pass; with bottles moving, the sensors, outputs and animation words are non-zero.
  - First live run: 0/43, because FUXA's Modbus plugin was missing in the new container (issue I12).
- **Polling load (I7):** a 90 s headless run with FUXA polling every 200 ms gave `dt` 98–102 ms, the same as without FUXA. GoodCount +10, nothing lost, no foreign-writer warning. The only warning was `Alm_CommLost` at 0 s from the freshly started PLC, cleared by Reset.
- **Test page `Tags`** (view `v_filler_tags`, the viewer's start page), generated by the same script from the same table:
  - Seven columns: status + alarms, outputs, sensors, wiring, commands, manual, animation. One name + live value per tag; read only (no input shapes).
  - **How a FUXA Value shape is built** (format from FUXA's demo project, the same as what the editor makes when you drag a Value onto a view): two parts.
    - In the view's SVG: `<g id="VAL_filler_State" type="svg-ext-value"><text>##.##</text></g>`.
    - In the view's `items`: `{"id": "VAL_filler_State", "type": "svg-ext-value", "property": {"variableId": "t_filler_State"}}`.
    - FUXA writes the tag value into the `<text>`. Open the view in the editor and double-click a value to see its binding.
  - The empty `MainView` that the editor had added was removed.
  - `test_hmi_tags.py` H1 also checks that every tag has a drawn and bound Value shape.
  - **Render check:** a Playwright/Firefox screenshot of http://localhost:1881 (scratchpad, not in the repo) with bottles running showed all 43 values live (State 1, Step 10, FillValve 1, FillLevel 460). The first try with plain `firefox --screenshot` showed only the grey page, because it captures before FUXA draws. One column title overlapped the next one and was shortened.

**Your own Value shape (to learn the editor):** add a view `Playground` (Editor → Views → +), Controls → **Value** → drop it on the page → double-click it → device `Filler PLC` → a tag → close, then open the viewer and pick `Playground` from the menu. Use your own view for this: `fuxa_tags.py` regenerates the whole `Tags` view, so a shape added to `Tags` by hand would be lost. Export afterwards.

## Stage 2 – Header and line commands

The header is shown on every screen (report 8.1):
- Machine state as a text and a colour from `State`: Stopped grey, Running green, Starved yellow, Blocked orange, Fault red (report 8.2).
- Step name from `Step`: 0 IDLE, 10 WORK, 15 WAIT_OUTFEED, 20 INDEX, 900 FAULT.
- Mode from `ModeReq`. **Stop mode is shown clearly**: after a PLC restart the mode is 0 and Start is ignored (issue I10).
- E-stop status from `E_Stop_OK`, number of active alarms, and the PLC link status (FUXA's device status).
- Buttons: Start, Stop, Reset (`Cmd`), and a mode selector Auto / Manual / Stop (`ModeReq`).
- Navigation buttons to the other screens.

**Test:**
- With the simulator running, Auto → Reset → Start from FUXA. Bottles run, and the header shows WORK/INDEX and Running.
- Stop → the machine finishes its jobs and goes to IDLE.
- Restart the PLC → the header shows Stop mode.
- From here on, `tests/hmi.py` is no longer needed for normal operation.

**As built (2026-11-03):**
- **Header** = an SVG band (1280 × 96) drawn at the top of **every generated view** by `fuxa_views.header()`. FUXA's own header bar (`layout.header.items`) was not used: it only holds buttons, labels and images, with no colour ranges or show/hide.
  - Row 1:
    - Machine name.
    - **State block** coloured from `State` (report 8.2: Stopped grey, Running green, Starved yellow, Blocked orange, Fault red), with the state name.
    - **Step name**, plus the raw number small, so an unknown step still shows something.
    - **Mode.**
    - **Mode hint:** yellow "Stop mode: Start is ignored – press Auto" in Stop mode (I10), "Manual: sequencer paused" in Manual.
    - **E-stop chip:** red "E-STOP PRESSED".
    - **PLC link chip:** FUXA's connection status. Grey OK, yellow slow, red LOST.
  - Row 2:
    - **Start / Stop / Reset** write `Cmd` 1/2/3.
    - **Auto / Manual / Stop** write `ModeReq` 1/2/0. Each mode button is also bound to `ModeReq`, so the active mode is dark blue.
    - **4 alarm chips**, red while the bit is 1. The alarm list comes in stage 5.
    - **Navigation** Filler / Tags. The current view's button is dark blue.
- **Views:**
  - `Filler` (start page, the detail screen comes in stage 3).
  - `Tags`, now with the header; the grid moved down.
- **How the FUXA pieces work** (read from FUXA 1.3.5's client code, proved by a spike before building):
  - Colour: shape item `svg-ext-shapes-rect` with `property.ranges` `[{min, max, color, stroke}]`.
  - Number → word (step/state/mode names): FUXA's Value shape can't do it. So there is one `<text>` per name with `actions` show [v, v] and hide for all other values; each starts with `style="display:none"`.
  - Buttons: `svg-ext-html_button` with `events: [{type: click, action: onSetValue, actparam: "1", actoptions: {variableId}}]`. **The group id must start with `HXB_`**: FUXA finds the inner `<BUTTON id="B-HXB_…">` by that prefix. Without it the click still works, but the label and the range colours don't (found in the spike).
  - Navigation: event `onpage` with the view id.
- **Code:**
  - `simulator/tests/fuxa_shapes.py`: pure shape builders, explained in its docstring.
  - `simulator/tests/fuxa_views.py`: header and views.
  - `fuxa_tags.py` keeps the device and tags and builds everything in one run.
  - The names come from `simulator.plcs` (`STATE_NAMES`, `STEP_NAMES`, `MODE_NAMES`, `ALARM_NAMES`), so the header and the simulator can't disagree.
- **No confirm dialog** on Start/Reset: the PLC checks the mode and the alarms itself, and nothing here is dangerous. Revisit if the professor expects one.
- **Tests:**
  - **Unit:** `tests/unit/test_fuxa_shapes.py` 8 (show/hide ranges cover INT16 with no overlap, exactly one name visible per known value and none for an unknown one, button id prefix + write event). `tests/unit/conftest.py` puts `tests/` on the path. Unit tests 139/139.
  - **`test_hmi_tags.py` H5:** checks the header in the export (both views, Cmd/ModeReq values, one text per state/step/mode value, alarm chips, nav targets, every item has its SVG element) → 5/5.
  - H4 now waits up to 10 s, because right after an import FUXA reconnects and the status is 0 for a few seconds.
  - **Browser e2e** (Playwright/Firefox in the scratchpad, simulator running with 8 bottles), line driven **only by the header buttons**: 8/8.
    - Stop mode: hint visible.
    - Start ignored in Stop mode.
    - Auto + Reset clears `Alm_CommLost`.
    - Start → Running and green.
    - Stop → IDLE.
    - Nav to Tags and back.
    - No browser errors.


## Stage 3 – Filler detail screen

- **Four pockets** (ENTRY, RINSE, FILL, CAP), drawn from `Anim_Entry` … `Anim_Cap`: 0 empty (nothing drawn), 1 empty bottle, 2 rinsed, 3 filled, 4 capped. Each code gets its own symbol or fill, not a bright colour: a normal bottle is not an alarm (ISA-101).
- **Fill level bar** for `FillLevel` in ml, with a mark at `FillSetpoint`.
- **Lamps** for sensors S2–S8, and for the outputs: door, rinse valve, fill valve, cap motor, index motor.
- **GoodCount.**
- **Setpoints:** inputs for `FillSetpoint` (ml), `RinseTime` (ms) and `CapTime` (ms), with min/max in FUXA (issue I9). Each one shows its unit.
- **Manual buttons, hold-to-run:**
  - Six buttons, `Man_C1` … `Man_Index`. Each sets its bit ON while pressed and OFF on release.
  - The buttons are enabled only when `ModeReq` = 2 (Manual).
  - Check what happens when the release is lost (issue I3).

**Test:**
- Run the simulator scenarios `manual` and `e_stop` from the simulator panel and follow them on this screen: the pockets change code, the fill bar rises to the setpoint, and E-stop shows in the header.
- Jog the index belt by hand in Manual: it moves only while the button is held.

**As built (2026-11-03):** the `Filler` view (start page) under the header.
- **Machine row:**
  - **C1** with lamps C1 motor, S2 waiting, door open.
  - **4 pockets** (Entry, Rinse, Fill, Cap) on the index belt. Each bottle rect is coloured from its `Anim_*` code: empty = page colour, bottle = white, rinsed = light blue, filled = brown, capped = brown + a dark cap. The state word is under it.
  - The station lamps (Entry_Load; S3 + rinse valve; S4 + fill valve; S5 + cap head), plus index belt and S8.
  - **C2** with lamps S6, S7, outfeed full.
  - **Lamps** are white = ON, grey = OFF (ISA-101: no colour for normal equipment), with a legend.
- **Fill station:**
  - Two bars on the same 0–600 ml scale: level (`FillLevel`) and setpoint (`FillSetpoint`). FUXA's progress bar has a fixed max, so the setpoint is its own bar instead of a moving mark.
  - Values for fill level, setpoint and good bottles.
- **Setpoints** (I9):
  - Fill 100–600 ml: 600 ml = 6 s at 100 ml/s, below the 10 s fill timeout.
  - Rinse 1500–5000 ms and Cap 1000–5000 ms: the lower limits come from the simulator's physical minimum (`config.RINSE_MIN_S`, `CAP_MIN_S`).
  - FUXA writes only on Enter, and refuses values outside the range with a message.
- **Manual (hold to run):**
  - Six buttons C1, Door, Rinse, Fill, Cap, Index → `Man_*`.
  - Each writes 1 on mouse down and 0 on mouse up **and when the pointer leaves the button**.
  - The buttons are visible only in Manual (show/hide on `ModeReq`; a hidden HTML button can't be clicked). Outside Manual the box says how to get them.
  - The interlocks are explained as text; the PLC enforces them.
- **New FUXA shapes** (`fuxa_shapes.py`, formats from the client code, proved by a spike first):
  - `progress` (gauge_progress: exactly three children `A-GXP_`/`B-GXP_`/`H-GXP_`),
  - `number_input` (html_input: `I-HXI_`, `options {type number, min, max, updated}`),
  - `hold_button` (mousedown/mouseup/mouseout + show/hide actions),
  - `circle_ranges` (lamp).
- **Finding: one write per click.** The first version also released all six `Man_*` from every mode button (7 writes per click, against I3). FUXA 1.3.5 sends all events of a click at the same time; that gave `TransactionTimedOutError`, "working overload" and a dropped PLC link, so every write in the next seconds was lost (the setpoint test failed too). It was removed. **H5 now fails if a shape has two events of the same kind.**
- **I3 (lost release):**
  - Covered: mouse-up, and sliding off the button (mouseout).
  - **Residual risk, measured:** closing the tab while a button is held leaves `Man_*` = 1. Pressing and releasing the same button clears it. In Auto/Stop the PLC ignores `Man_*`.
  - Real fix: a ladder jog watchdog (e.g. `Man_*` longer than 10 s → ignored until released). That's a PLC change, proposed to you, not done.
- **Tests:**
  - **Unit:** `test_fuxa_shapes.py` +3 (hold events, progress children, input limits), unit tests 142/142.
  - **`test_hmi_tags.py` H6** (Filler screen in the export: hold buttons, setpoint limits, 15 lamps, pockets + words, bars) → 6/6 with H1–H5.
  - **Browser e2e with the simulator,** 8/8:
    - setpoint 450 written, 900 refused, 500 written;
    - pocket colours = `Anim_*` codes in 31 samples;
    - fill bar follows FillLevel;
    - manual buttons only in Manual;
    - holding C1 runs C1 and releasing stops it;
    - sliding off releases;
    - closed tab leaves `Man_C1` = 1 and press/release clears it;
    - no browser errors.
  - **Simulator:** `dt` 98–102 ms, 0 lost bottles, no foreign writes; FUXA link errors 0.
  - **Test lesson:** one e2e run first showed "values changed by someone else" and 2 lost bottles. Cause: an older simulator run was still running (two writers for 22 s). Rerun with one simulator: clean.



## Stage 4 – Line overview and conveyor animation

- **C1 and C2** as belts with 8 slots each. A bottle icon in slot *i* is visible when bit *i* of `Anim_C1` / `Anim_C2` is 1 (bit 0 = entry, bit 7 = exit; report 8.3).
- **Belt movement:**
  - The C1 belt line moves only while `ConveyerMotor1` is ON.
  - The filler's internal belt moves only while `IndexMotor` is ON.
  - C2 has no PLC output (the labeler drives it), so it is always shown as running while the labeler stand-in runs.
- **The filler as a block** coloured by `State`. A click opens the detail screen.
- **Stand-in blocks** for the blow moulder (supply) and the labeler, in grey, with the text "simulated".
- `Product_Available` drawn as the liquid link from the (stand-in) syrup room.

**Test:**
- The simulator scenario `gaps`: the gaps in the bottle flow are visible on C1.
- The simulator scenario `labeler_stopped`: C2 fills up slot by slot, and the filler block turns orange (Blocked).

**As built (2026-11-03):** new view `Line` (`v_filler_line`), **now the viewer's start page** (report 8.1: the overview is the main screen). Header navigation: Line | Filler | Tags.
- **Layout:**
  - Blow moulder (grey stand-in) → **C1** → **Filler block** → **C2** → Labeler (grey stand-in).
  - Syrup room (grey stand-in) above the filler, with the product pipe: brown while `Product_Available` = 1, grey + red "no product" when 0.
  - Good bottles counter and a legend.
- **Conveyors:**
  - 8 slot shapes each. Every slot is bound to **one bit** of `Anim_C1` / `Anim_C2`, using FUXA's `bitmask`: FUXA compares `(value & mask) ? 1 : 0` with the ranges. **This closes I2.**
  - C1 bottles are white (empty), C2 bottles brown (filled and capped).
  - 3 rollers per conveyor. A roller is a circle with a cross, because a plain circle shows no rotation.
  - The C1 rollers turn while `ConveyerMotor1` = 1 (FUXA actions `clockwise` / `stop`).
  - C2 has no PLC output (the labeler stand-in drives it), so its rollers are static and labelled so.
- **Filler block:**
  - Coloured from `State`, with the state name and 4 mini pockets from `Anim_*`.
  - 2 index rollers turning with `IndexMotor`.
  - **A click opens the Filler screen.** The texts and pockets on top have `pointer-events="none"`, so the whole block is clickable.
- **I11 (later machines):** `conveyor()`, `machine_block()` and `standin_block()` are one call per machine/conveyor. A second PLC = one more device + tag prefix + calls.
- **Code:**
  - `fuxa_shapes.py`: `rect_ranges(bitmask=…, events=…)`, `roller()`, `text(click_through=…)`.
  - `fuxa_views.py`: `line_view()` and the functions above; `HEADER_KEY` per view; the alarm chips narrowed to fit three nav buttons.
- **Spike first:**
  - Bitmask slots matched the bits.
  - The roller's transform changes while the motor is ON.
  - A click on text over a clickable rect reached the rect (Playwright refuses to click a `pointer-events: none` element itself; the e2e clicks by position or on the rect).
- **Tests:**
  - **Unit:** +2 (bitmask stored, roller actions only with a motor tag), unit tests 144/144.
  - **`test_hmi_tags.py` H7** (Line view: 16 slots on the right bit, roller actions, static C2 rollers, block → Filler, product pipe, start page) → 7/7.
  - **Browser e2e:** the script starts **one simulator per part itself** and refuses to start if one is already running. 6/6:
    - **L1:** slots = bits for C1 and C2 in 36 stable samples, 0 mismatches.
    - **L2:** the rollers turn only while the motor is ON (checked in Manual with C1 off).
    - **L3:** the filler block opens the Filler screen, and Line nav returns.
    - **L4:** `labeler_stopped`: C2 full and the filler orange (Blocked, WAIT_OUTFEED).
    - **L5:** `gaps`: a partly empty C1 is shown.
    - **L6:** no browser errors.
  - Simulator `dt` 97–102 ms, 0 lost bottles. FUXA link errors 0.
- **Test lessons:**
  - **C2 "full" is `11111110`, not 255.** S7 is on C2 slot 1 (`docs/simulator/design-notes.md`), so the filler blocks with slot 0 still free.
  - **A slot sample is only valid if the PLC value didn't change** while the screen was read. FUXA polls every 200 ms, so the screen can be one poll behind.



## Stage 5 – Alarms

- **One FUXA alarm** for each alarm bit:

  | Alarm bit | Priority | Text |
  | --- | --- | --- |
  | `Alm_IndexJam` | high | "Index belt jam: no S8 within 5 s" |
  | `Alm_FillTimeout` | high | "Fill timeout: setpoint not reached in 10 s" |
  | `Alm_OutfeedBlocked` | warning | "Outfeed blocked longer than 2 min" |
  | `Alm_CommLost` | high | "Simulator heartbeat lost" |

- **Alarm screen:** active alarms with acknowledge, and the history.
- **Reset button** on the alarm screen (`Cmd` = 3).
- **Explain on the screen** the difference between acknowledging and resetting:
  - Acknowledging in FUXA only means "the operator has seen it".
  - Reset clears an alarm in the PLC, and only once its cause is gone.

**Test:**
- The simulator scenarios `index_jam`, `no_product` and `heartbeat`: each alarm appears, can be acknowledged, and disappears after Reset once the cause is gone.
- The history keeps all of them.

**As built (2026-11-03):**
- **4 FUXA alarm definitions**, generated (`fuxa_views.ALARMS`, names `filler_<bit>`, group Filler):

  | Bit | Priority | Text |
  | --- | --- | --- |
  | `Alm_IndexJam` | highhigh (red) | Index belt jam: no S8 pulse within 5 s in INDEX (FAULT) |
  | `Alm_FillTimeout` | highhigh (red) | Fill timeout: setpoint not reached within 10 s (FAULT) |
  | `Alm_CommLost` | high (orange) | Plant heartbeat lost for 3 s (simulator link) |
  | `Alm_OutfeedBlocked` | low (yellow) | Outfeed blocked longer than 2 min (warning) |

  - Active at value 1, checked every second, `ackmode ackactive`. The operator can acknowledge while the alarm is active, and an alarm that came and went **stays in the list until it is acknowledged** (nothing is missed).
  - The names are fixed because FUXA keys the history by name.
  - They go into the project's top-level `alarms` list; FUXA stores them on import, and `GET /api/project` returns them, so the round trip is identical.
- **View `Alarms`** (`v_filler_alarms`):
  - **Active alarms table** (FUXA table widget, type `alarms`): time, priority, text, status, and an **Ack** button per row. Row colour = priority.
  - **Reset button** (`Cmd` = 3), with the explanation **Ack = "seen" in FUXA, the PLC doesn't change; Reset = the PLC clears the bits whose cause is gone; after a FAULT also Start.**
  - A note on how to open the history.
- **History:** FUXA's own **alarm panel**, from the side menu ☰ → *Alarm history* → ⋮ → *History Alarms* → dates → Apply. It shows on, off and ack time per alarm.
  - The plan had a history table on the view, but **FUXA 1.3.5 never loads data into a table of type `alarmsHistory`**: only `alarms` polls (`/api/alarms` every 2.5 s). Found in the spike.
  - The side menu item is `view: "[alarms]"`, which the viewer's `onGoToPage` turns into the expanded alarm panel. A shape event can't open it (`onpage` loads views only).
- **Header:**
  - Navigation Line | Filler | Alarms | Tags.
  - **Clicking an alarm chip opens Alarms.** One `onpage` event, no write; the chip text is click-through.
  - The side menu lists the 4 views + Alarm history (generated; a hand-made side menu would be replaced).
- **Code:** `fs.alarm()`, `fs.alarm_table()` (`OXC_` prefix, columns with width); `fv.ALARMS`, `alarms_view()`, `apply_alarms()`, `side_menu()`.
- **Tests:**
  - **Unit:** +2, unit tests 146/146.
  - **`test_hmi_tags.py` H8:** 4 definitions with one priority each, active at 1, ackactive; the active table with an Ack column; Reset = `Cmd` 3; every chip → Alarms; the side menu has the alarm panel. **8/8.**
  - **Browser e2e:** one simulator per part, started by the script, 11/11:
    - **Navigation:** a chip opens Alarms.
    - **index jam:** listed in FUXA and in the table; Ack in the table (status NA); after the jam is gone, **Reset** clears the PLC bit and the acknowledged alarm leaves the list; Start runs again.
    - **comm lost:** priority high; **not acknowledged, it stays (status NF) after Reset**, and Ack removes it.
    - **History:** `/api/alarmsHistory` has on/off/ack times for both, and both appear in the alarm panel.
    - No browser errors.
  - Simulator `dt` 97–103 ms; FUXA link errors 0.
- **Test lesson:** a guard `pgrep -f bin/simulator` also matches the shell that writes the test script with a heredoc. Write the script first, then run it in a separate command.



## Stage 6 – Trends (optional)

- FUXA DAQ (history) for `FillLevel`, `GoodCount` and `State`.
- A trend screen showing the fill curve of each bottle and the count over time.

**Test:** a 5 min run shows one fill curve per bottle, each flat at the setpoint, and a rising count.

**As built:** (2026-11-04)
- **What it shows and why:** a *trend* is a value drawn over time. The other screens show only *now*; the Trends view shows the last minutes, so a fault can be explained afterwards ("why did the fill time out?") and the throughput can be seen at a glance. Part of the syllabus topic data logging / SCADA.
- **Logging (DAQ):** `fuxa_tags.py` turns FUXA's DAQ on for `TREND_TAGS` = `FillLevel`, `FillSetpoint`, `GoodCount`, `State` (`daq_on()`: every change + at least once a minute, `interval` 60 s); all other tags stay off. FUXA stores each value with its time in SQLite under `server/_db`, kept 1 year (`hmi/mysettings.json` → `daqstore.retention`). FillLevel is stored at the polling rate (≈ every 200 ms while it changes).
- **History survives `down/up`:** `server/_db` is on the named volume `fuxa-daq` (`docker-compose.yml`), like `_pkg` (I12). Without it a re-created container started with an empty history.
- **Charts** (`project["charts"]`, ids `ch_filler_fill/count/state`, built by `fuxa_views.CHARTS` + `apply_charts`, charts made in the editor are kept):
  - *Fill level (ml)*: `FillLevel` (dark blue) + `FillSetpoint` (grey), last 5 min;
  - *Good bottles*: `GoodCount`, last 30 min;
  - *Machine state*: `State`, last 30 min, with a key "0 Stopped · 1 Running · 2 Starved · 3 Blocked · 9 Fault" under it (the axis shows numbers only).
  - Every line is **stepAfter**: the DAQ stores a value only when it changes, so a straight line between two samples would draw a ramp that never happened; stepAfter holds a value until the next one, like the PLC.
- **View `Trends`** (`v_filler_trends`): header + the three chart widgets (`fs.chart`, type `realtime1`, `loadOldValues`: when the view opens the chart fills its window from the DAQ store, so the curves are still there after a page change or a reload). Header nav got a 5th button (row 2 re-laid out: buttons 80 px, chips 96 px, nav 60 px), side menu entry `Trends`.
- **FUXA facts used** (1.3.5, read in the container's code): chart = `{id, name, lines: [{id: tag id, device: device name, label, color, yaxis, lineInterpolation 0 linear/1 stepAfter/2 stepBefore/3 spline/4 none, …}]}`; widget `svg-ext-html_chart` with id prefix `HXC_` and the div `D-HXC_…`, `property = {id: chart id, type, options}`; the chart's tags come from its lines, its title from the chart name; DAQ query `GET /api/daq?query={"sids":[…],"from":ms,"to":ms}`.
- **Seen, not bugs:**
  - after a reload the history curve is drawn coarser than live (FUXA's history loader); the stored data is complete (checked with `/api/daq`);
  - a State blip of 0.2 s (Starved at start) is stored, but is below one pixel in a 30 min window;
  - the setpoint line starts at its first stored point (stored once a minute while it does not change);
  - the legend reads "Time: 01/01/1970" until the mouse is over the chart (FUXA).
  - FUXA's round menu button covers the bottom-left corner, so the help texts start at x = 64.
- **Tests:**
  - unit (`test_fuxa_shapes.py`, chart markup/options/line): 152/152;
  - `fuxa_tags.py` twice and import + export round trip: identical;
  - `test_hmi_tags.py` **10/10**: new **H9** (export: DAQ on exactly the trend tags, chart lines bound + stepAfter, one realtime widget with history per chart, nav + side menu) and **H10** (live: DAQ has values of every trend tag from the last 10 min, no stored fill level above setpoint + 20 ml). H9 was shown to fail on a line bound to the wrong tag, DAQ on an extra tag and a linear line;
  - H3 now tries a write up to 3 times: about 1–2 % of FUXA's Modbus writes time out (`TransactionTimedOutError` → HTTP 400, issue I16). Once both H3 writes failed, which left the probe value 480 as the fill setpoint (put back to 500 through FUXA);
  - **browser e2e** (Playwright/Firefox in the scratchpad, one simulator started by the script, line run only from the FUXA header), checked on the stored history, two 5 min runs, both: **45 fill peaks, all 500 ml (setpoint), GoodCount +44** (the 45th bottle still inside), count never falls, Running 100 %; curves back after a page reload; no browser errors;
  - `labeler_stopped`: Blocked from 61.0 s to 184.7 s, GoodCount flat during it, `Alm_OutfeedBlocked` after 2 min, count rises again 1.4 s after Running; screenshots of the header on Line/Filler/Alarms: no overlap after the re-layout;
  - `docker compose restart fuxa`: history still there (run 1 analysis passes again); `demo_check.py` D1–D5 PASS.

## Stage 7 – Demo and wrap-up

- **Pre-demo check:** `uv run python tests/demo_check.py` (in `simulator/`) – all PASS before the demo (containers, PLC program present and running, FUXA connected and current, one simulator, Auto without alarms).
- **ISA-101 check** over all screens: grey background, colour only for abnormal states, units everywhere, readable from a distance.
- **One full run** of all fault scenarios, started from the simulator panel while FUXA is the HMI, with a screenshot per screen for the report. Don't use `tests/test_scenarios.py` here: it acts as the HMI itself (issue I4).
- **Docs:**
  - Report section 8: what was built.
  - `CLAUDE.md`: the FUXA part.
  - `progress.md`: section 3 ticked.
  - The final export committed.

**As built:**
- [x] 7.1 Pre-demo check (2026-11-04): `demo_check.py` 7 PASS, after Ack + Reset of two leftover latched alarms (`Alm_OutfeedBlocked`, `Alm_CommLost`; their causes were already gone).
- [x] 7.2 ISA-101 check (2026-11-04): all screens reviewed by hand, no changes needed.
- [x] 7.3 Full fault-scenario run with FUXA as the HMI (2026-11-04): 9/9 PASS, 2 findings for the ladder (manual interlocks, open). Screenshots not taken yet (`docs/hmi/screenshots/`); header colour during `Alm_CommLost` not noted.
  - 1 normal run: PASS
  - 2 `gaps`: PASS (3 + 2 + 3 bottles). Needs an empty line first (Supply /min 0, wait for Starved) and the panel switch *Supply paused* OFF: while paused, `add_bottles` is queued but never placed (first try failed that way). The scenario leaves the supply at 0.
  - 3 `no_product`: PASS (Starved, no alarm, bottle topped up to 500 ml)
  - 4 `labeler_stopped`: PASS (Blocked, `Alm_OutfeedBlocked` after 2 min, continues by itself; warning cleared with Ack + Reset)
  - 5 `index_jam`: PASS (FAULT 5 s after the jam, Ack + Reset + Start from FUXA, all bottles 500 ml, no double load)
  - 6 `e_stop`: PASS (all off, Stopped not Fault, Start only, bottle topped up to 500 ml)
  - 7 `heartbeat_off`: PASS (`Alm_CommLost` ~3 s after the freeze, outputs off, Reset + Start, bottle topped up)
  - 8 `slow_fill`: PASS (`Alm_FillTimeout` 10 s after the fill started, FAULT, Reset + Start, bottle topped up to 500 ml)
  - 9 Manual by hand: PASS with 2 findings. Buttons only in Manual, hold-to-run, `Man_Fill` without bottle does nothing, jog watchdog after 10 s, Auto + Start continues. Index-while-fill interlock not testable with one mouse (covered by `test_scenarios.py manual`). **Findings (open, ladder change):** (a) `Man_Door` has no interlock: the door opened onto a loaded entry pocket, two bottles in one pocket (fix: `AND NOT Entry_Load` in the Manual branch of `Req_Door`); (b) `Man_Fill` has no upper limit: 811 ml (fix: `AND FillLevel < FillSetpoint` in the Manual branch of `Req_Fill`).
- [ ] 7.4 (optional) I16 write timeouts: device timeout / polling
- [x] 7.5 Demo script `docs/hmi/demo-script.md` (2026-11-04): preparation, 10 demo steps (~10 min: normal run, `labeler_stopped`, Trends, `index_jam`, `e_stop`, Manual), what to avoid (manual door/fill gaps, `gaps` on a full line, supply paused), troubleshooting table. To rehearse once.
- [ ] 7.6 Docs, final export

---

## Issues and open questions

| ID | Issue | Why it matters | Checked how / when | Status |
| --- | --- | --- | --- | --- |
| I1 | Does FUXA count Modbus addresses from 0 or from 1, for coils and for holding registers? | Off by one = every tag shows its neighbour's value. | Probe with S2 and FillSetpoint, stage 1 | closed: address = Modbus number + 1 (FUXA code), confirmed live by H2 43/43 |
| I2 | Can a shape be bound to one bit of a holding register (`Anim_C1` bit *i*)? | The slot animation needs 8 bits per conveyor. | Try a bit/bitmask option in the tag or the shape property, stage 1. Fallbacks: a FUXA script splits the word, or the simulator writes the slots as coils instead | closed: one slot shape per bit with `bitmask` (stage 4, e2e L1 0 mismatches) |
| I3 | Hold-to-run: if the release is lost (tab closed, link lost while a button is held), `Man_*` stays TRUE. | An actuator keeps running with nobody watching. | Test mouse-up, mouse-leave and closing the tab, stage 3. Fallback: a jog timeout in the ladder (your ladder change, explained then) | fix prepared: PLC jog watchdog (`Jog_Block` %MX3.2, 10 s, re-arm after all released; guide in `docs/filler/ladder-roadmap.md`), HMI side done (tag, lamp, red line on the Filler screen), test `simulator/tests/test_jog_watchdog.py` | closed: ladder change uploaded 2026-11-03, `test_jog_watchdog.py` 9/9, `test_outputs.py` 36/36 |
| I4 | Several HMI writers: `tests/hmi.py` and the PLC test scripts write the same addresses as FUXA. | Two writers fight; a test could be blamed on the PLC. | Rule: use them only while FUXA is not controlling the machine | closed: rule + automatic reminder (`simulator/tests/hmi_guard.py` prints a note when FUXA runs, called by `hmi.py`, `test_outputs.prepare()` – used by every PLC test –, `test_closed_loop.py`, `test_scenarios.py`) |
| I5 | The live files in `hmi/` are SQLite DBs owned by root that change while FUXA runs. | Not diffable; a commit can catch a half-written DB. | Commit only the export; ignore the DBs, stage 0 | closed: `docs/hmi/fuxa-project.json` + `/hmi/` ignored |
| I6 | The `frangoteam/fuxa:latest` image is not reproducible. | A FUXA update can break the project before the presentation. | Pin the version, stage 0 | closed: pinned to `1.3.5` |
| I7 | Polling rate vs animation: the belt moves one slot every 0.5 s, the default polling is 1000 ms. | Bottles would jump two slots at a time. | 250 ms polling; check that the simulator `dt` stays at 98–102 ms, stage 1 | closed: 200 ms polling, simulator `dt` stays 98–102 ms |
| I8 | `GoodCount` is an INT and wraps at 32767 (assumption, proved wrong: the CTU stops at 32767). | A long demo could show a negative count. | Accept for the demo (about 64 h of running); note in the report | closed, not a bug: `GoodCount` is the CV of `Good_Counter : CTU` with PV 32767; the runtime counts only while `CV < PV` (generated `configuration.cpp`), so it **stops at 32767** (≈ 65 h at 7.2 s/bottle), never negative. Option (your call): counter reset from the HMI (`Cmd` = 4 → CTU `R`) |
| I9 | The PLC does not limit the setpoints (e.g. `FillSetpoint` = 5000 never finishes). | One typo leads to a fill timeout. | FUXA input min/max, stage 3. Decision later: a `LIMIT` block in the ladder as well | closed: FUXA input min/max (fill 100–600 ml, rinse 1500–5000 ms, cap 1000–5000 ms); ladder `LIMIT` still possible |
| I10 | After a PLC restart the mode is Stop (0) and Start is ignored. | Confusing during the demo ("Start does nothing"). | Shown clearly in the header, stage 2 | closed: yellow hint in the header, mode name, Stop button lit (stage 2 e2e E1/E2) |
| I11 | Later machines: one Modbus device per PLC (ports 5021–5025). | Screens should be copyable for the next machine. | Same tag names under each device, header from the standard layout (report 6.2), stage 4 | ready: `fuxa_tags.Machine` (key, name, PLC address, tag table → device `d_<key>`, tags `t_<key>_*`, status tag), `MACHINES`; views by `conveyor()` / `machine_block()`. Refactor checked byte-identical. A second machine = address map + tag table + `Machine(…)` + view calls; waits for the scope decision (`docs/scope-options.md`) |
| I12 | FUXA's Modbus driver is an optional plugin (`modbus-serial`), installed into `server/_pkg` inside the container. A new container (here: after `docker compose up` in stage 0) has no plugin. FUXA then logs "plugin is missing", and the device never connects. The same thing happens to the OpenPLC runtime: the compiled program is in the container, not in its volume. | Every tag stays empty; it looks like an address problem. | `_pkg` is now on the named volume `fuxa-plugins`; plugin installed once in the editor (Settings → Plugins). Since I14 the OpenPLC program also survives `down/up` (volume `openplc-build`) | closed: plugin installed, on the volume |
| I13 | FUXA 1.3.5 sends all events of one click at the same time. Several writes per click (7 tried) time out and drop the PLC link for seconds. | Every write in that time is lost: a button seems to do nothing. | Rule: one write per event; H5 fails if a shape has two events of the same kind (stage 3) | closed: rule + check |
| I14 | The compiled PLC program (`/workdir/build/libplc_*.so`) lived only in the OpenPLC container: re-creating it (`down`/`up`, a service change, a newer `:latest`) left the runtime without a program – every HMI value empty. (`stop`/`start` were always fine: the runtime starts an existing program at boot, `plc_boot_autostart`.) | The demo shows nothing. | Named volume `openplc-build:/workdir/build` (filled from the image on creation) + runtime image pinned by digest (v4.3.1); `tests/demo_check.py` D2/D3 detect a missing or stopped program | closed (needs one upload after the change) |
| I15 | Two plant writers at once (two simulators, or a test script next to the simulator) write different sensor values into the same addresses: bottles were lost in a test. | Wrong behaviour that looks like a PLC fault. | `simulator/src/simulator/plant_lock.py`: OS file lock per PLC host:port, taken by `loop.run`, the CLI and every sensor-writing test script; the second one stops with a message naming the first. Released by the OS when a process ends (no stale lock) | closed |
| I16 | About 1–2 % of FUXA's Modbus writes time out (`TransactionTimedOutError` in the FUXA log, `POST /api/setTagValue` → 400 after 2 s). Measured 2026-11-04 with 1 write/s, simulator not running: 2/140 without DAQ (stage 5 project), 3/140 with DAQ, so it is older than the trends and not caused by them. | An operator click (Start, a setpoint, a mode) can be lost now and then; a test that writes can fail. | Test: H3 tries up to 3 times. Operator: the header shows the result (state, mode, value), press again if nothing changed. Possible fixes, to check in stage 7: device `timeout` / `delay` (now 2000 / 10 ms), polling 200 ms → 250 ms | open |

## Decisions

| Date | Decision |
| --- | --- |
| 2026-11-03 | The HMI is built in stages (this file), like the simulator. The user builds the screens in the FUXA editor; each stage is checked against the real PLC with the simulator running. |
| 2026-11-03 | Lost mouse-up (I3) is fixed in the PLC, not in FUXA: a jog watchdog ignores manual buttons held > 10 s until all are released; the PLC never writes `Man_*`. |
| 2026-11-03 | Alarms: FUXA alarm definitions per PLC bit (ackactive), active list + Ack + Reset on the Alarms view, history in FUXA's own alarm panel (side menu): FUXA 1.3.5 tables of type alarmsHistory never load. Ack is FUXA-only; Reset is the PLC's. |
| 2026-11-03 | Start page = Line overview (report 8.1). Conveyor slots one shape per bit (bitmask); C2 rollers static because the labeler (stand-in) drives C2. |
| 2026-11-03 | One write per click/event (FUXA drops the PLC link when one click writes several tags, I13). A stuck manual bit is cleared by press/release of the same button; the real fix (jog watchdog) belongs in the ladder. |
| 2026-11-03 | Header drawn into every view by the generator (not FUXA's header bar: no colour ranges / show-hide there). Step/state/mode names shown with show/hide texts (FUXA's Value can't map numbers to words). |
| 2026-11-03 | The project is versioned as one FUXA export file, `docs/hmi/fuxa-project.json` (sorted keys, from `GET /api/project`); the live DBs in `hmi/` are not committed. |
| 2026-11-03 | FUXA pinned to 1.3.5, the build that created the existing data, not the newer `latest`. |
| 2026-11-04 | Trends: FUXA DAQ only for `FillLevel`, `FillSetpoint`, `GoodCount`, `State` (every change + once a minute), stepAfter lines, realtime charts that reload their window from the DAQ; history on the volume `fuxa-daq`. |

## Log

| Date | Entry |
| --- | --- |
| 2026-11-03 | Roadmap written. Starting point: FUXA container in `docker-compose.yml` (`latest`, port 1881, `./hmi` as `_appdata`), old test project `docs/fuxa-project-v2.json` (device `openplc:502`, 2 test tags, polling 1000 ms), animation words %MW70–75 ready (simulator stage 9). |
| 2026-11-03 | Stage 0 done: image pinned to 1.3.5, old test project replaced by an empty one (backup outside the repo), export/import via `/api/project`, round trip and restart identical, `/hmi/` ignored, `docs/fuxa-project-v2.json` removed. Found: `hmi/` in `.gitignore` also matched `docs/hmi/` → anchored as `/hmi/`. |
| 2026-11-03 | Stage 1 started: I1 (address + 1) and I2 (shape bitmask) answered from FUXA 1.3.5's code. Device `Filler PLC` with 43 tags generated from `address_map.py` (`tests/fuxa_tags.py`, fixed ids), imported (round trip identical). `test_hmi_tags.py` H1 passes, mutation check reported both changes. Live checks wait for the PLC program to be started. |
| 2026-11-03 | Live checks first failed: FUXA logged "try to create Filler PLC but plugin is missing". The Modbus plugin lived in the old container. `_pkg` moved to the volume `fuxa-plugins` (I12). The OpenPLC program also had to be uploaded again (new container). |
| 2026-11-03 | Plugin `modbus-serial` installed (on the volume `fuxa-plugins`), device connected. `test_hmi_tags.py` 4/4, idle and with bottles moving; FUXA polling leaves the simulator `dt` at 98–102 ms. Stage 1 waits only for the test page `Tags`. |
| 2026-11-03 | Stage 1 done: test page `Tags` generated (`fuxa_tags.py`, 43 Value shapes bound by tag id, viewer start page), round trip and second run identical, H1 checks the bindings, screenshot with bottles running shows live values. |
| 2026-11-03 | Stage 2 done: header on every view (state colour, step, mode + Stop-mode hint, E-stop, PLC link, alarm chips, Start/Stop/Reset, mode selector, navigation), views Filler (start) + Tags. Spike first: rect ranges, show/hide texts and buttons proven; found that FUXA needs the `HXB_` id prefix for button label/colours. Unit 139, `test_hmi_tags.py` 5/5, browser e2e 8/8 (line run only from FUXA). |
| 2026-11-03 | Stage 3 done: Filler detail screen (pockets from Anim codes, lamps, fill + setpoint bars, setpoint inputs with limits, hold-to-run manual buttons only in Manual). Spike first (progress, input, hold button, lamp). Found I13 (several writes per click drop FUXA's PLC link) → one write per event + H5 rule. Unit 142, `test_hmi_tags.py` 6/6, browser e2e 8/8, simulator clean. |
| 2026-11-03 | Stage 4 done: Line overview (stand-ins, C1/C2 slot animation by bitmask, turning rollers, filler block coloured + clickable, product pipe), start page. Spike first. Unit 144, `test_hmi_tags.py` 7/7, browser e2e 6/6 (normal run, `labeler_stopped`, `gaps`). I2 closed, I11 partly. |
| 2026-11-03 | Stage 5 done: 4 alarm definitions, Alarms view (active table with Ack, Reset, explanation), chips open it, side menu with views + Alarm history (FUXA panel). Spike found: history table type doesn't load in 1.3.5 → alarm panel. Unit 146, `test_hmi_tags.py` 8/8, browser e2e 11/11. |
| 2026-11-03 | Open issues worked: I8 closed (counter saturates at 32767, runtime source), I11 ready (`Machine` per PLC, export byte-identical), I4 closed (reminder `hmi_guard.py`), I3 prepared (ladder guide, `Jog_Block` tag 44, lamp + red line, `test_jog_watchdog.py`) – the ladder change is yours. |
| 2026-11-03 | Demo risks fixed: I14 (PLC program on volume `openplc-build`, runtime pinned by digest), I15 (plant lock), plus `tests/demo_check.py` (read-only pre-demo check with fixes). The runtime was re-created once for the volume: the program must be uploaded again (together with the I3 ladder change). |
| 2026-11-03 | I3 closed: jog watchdog in the ladder and uploaded (PLC program back in the runtime, I14). `test_jog_watchdog.py` 9/9, `test_outputs.py` 36/36. |
| 2026-11-04 | Stage 6 done: DAQ on 4 tags, 3 charts, view Trends (+ nav/side menu, header row 2 re-laid out), DAQ history on volume `fuxa-daq`. Spike first (chart format read from FUXA's code). Unit 152, `test_hmi_tags.py` 10/10 (H9, H10 new; H3 up to 3 tries, I16 opened: 1–2 % of FUXA's writes time out, with or without DAQ), e2e twice: 45 fill peaks at 500 ml = GoodCount +44 (+1 in the machine), `labeler_stopped` Blocked with a flat count. |
