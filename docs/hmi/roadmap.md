# FUXA HMI – Roadmap

How to build the operator screens in FUXA, in small stages. Each stage builds on the one before. Design: `docs/project-report.md` section 8. Addresses: `docs/filler/io-list.md`. Machine behaviour: `docs/filler/state-diagram.md`. The stage checklist is ticked here and in `docs/filler/progress.md` section 3.

**Status:** filler stages 0–6 done, 7 partly (7.4 optional, 7.6 open); issues closed except I16 (≈1–2 % of FUXA's Modbus writes time out, older than stage 6). Syrup room (section "Syrup room screens"): S0–S7 built (device, 48 tags, test page, header for two machines, syrup room screen, syrup room block on the Line overview, syrup room alarms, syrup room trends, wrap-up: `demo_check.py` D4 per PLC, demo script for both machines), live checks of S4–S6 passed (2026-11-14, see "Live verification" below); demo script rehearsed (2026-11-15). Labeler (section "Labeler screens"): planned 2026-11-19 (L0–L7), L0–L5 done (animation words; device + 53 tags + test page; header for three machines; labeler screen; Line with the labeler block and C3; labeler alarms, live 2026-11-19), I17 fixed (E-stop segment per PLC); L6 done (labeler trends, live 2026-11-19); L7 done (wrap-up, demo script for three machines rehearsed 2026-11-19): **labeler HMI complete**. Packer (section "Packer screens"): planned 2026-11-23 (P0–P7), P0–P7 done (animation words, device + 71 tags + test page, header for four machines, Packer screen, packer block on the Line view, packer alarms, packer trends, wrap-up: `demo_check.py` with the packer's FUXA link, demo script for four machines rehearsed 2026-11-24): **packer HMI complete**. Open: I16 (write timeouts), I18 (a downstream FAULT jams the machine upstream).

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

## Syrup room screens (second machine)

Second PLC on the same screens (report 8.1: one HMI for the whole line). Same working rule as above: plan → OK → build (generated), test per stage, export, tick here and in `docs/syrup/progress.md` section 3.

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| S0 | Refactor | View functions that can draw any machine take `m: ft.Machine` (`header`, `lamp`, `machine_block`, `value_id`, `tags_view`) | export byte-identical, `test_hmi_tags.py` |
| S1 | Device + tags | `fuxa_tags.SYRUP` (`plc-syrup-carb:502`), 48 tags from `address_map_syrup.py`, test page `Syrup tags` | H1 both devices, live H2 + H4 on the syrup PLC |
| S2 | Header for two machines | Row 1 shared on every view: both machines' state chips (click = their screen), E-stop, both PLC links. Row 2 = the machine of that screen: Start/Stop/Reset, mode, its alarm chips. Line / Alarms / Trends: a machine picker in row 2 (decided 2026-11-13) | H5 + browser: Auto / Reset / Start of the syrup room from FUXA |
| S3 | Syrup room screen | 3 tanks as level bars (L), pumps / valves as lamps, temperature (°C) + `Temp_OK`, CO2 (bar) + `CO2_Demand`, step, `BatchCount`, 10 recipe setpoints with min/max, 6 hold-to-run buttons (Manual only) | export check + `cooling_failure`, `over_pressure` followed on screen |
| S4 | Line overview | The syrup stand-in becomes a block coloured by its `State` (click → syrup screen); product pipe from the syrup PLC's `Product_Available` | H7 + `buffer_starved` |
| S5 | Alarms | 6 alarm definitions `syrup_Alm_*` (group Syrup), Reset per machine | H8 + `level_sensor_stuck` |
| S6 | Trends | DAQ on temperature, pressure, buffer / mixing level, syrup `State`; charts | H9 / H10 + one batch as a curve |
| S7 | Wrap-up | `demo_check.py` D4 for the syrup device, demo script, report 8.1 | `demo_check.py` 0 FAIL |

Open: a line-wide Start / Stop / Reset (report 8.4) writes to two PLCs in one click – spike it against I13 before promising it; until then Start / Stop / Reset stay per machine.

**S0 + S1 as built (2026-11-13):**
- **S0:** `header`, `lamp`, `machine_block`, `value_id` and `tags_view` take `m: ft.Machine = ft.FILLER`; inside, `T = m.tag_id` shadows the filler helper, so the drawing code is unchanged. Proved by an offline regeneration from the committed export (no FUXA needed): byte-identical, H1 + H5–H9 pass.
- **S1:**
  - `fuxa_tags.SYRUP_TAGS`: 48 tags (status 3, alarms 6, outputs 7 incl. `Product_Available` %MX6.0, conditioned / computed 6, sensors + wiring 8, commands + setpoints 12, manual 6). Descriptions from `docs/syrup/io-list.md`, with the fixed-point unit (L, 0.1 °C, 0.01 bar).
  - Device `Syrup room PLC` (`d_syrup`) at **`plc-syrup-carb:502`**: the Docker service name and the container port, not the host port 5022 (FUXA is inside the Docker network).
  - `Machine.trend_tags`: DAQ per machine. Before, DAQ was chosen by tag name only, so the syrup `State` would have been logged just because the filler logs a `State`.
  - Test page `Syrup tags` (`v_syrup_tags`), in the side menu only (the header nav has no room). It carries the filler header until S2: a syrup header with the filler's alarm chips would bind to tags that do not exist.
  - The export only gained lines (0 removed); import round trip identical.
- **Tests:** `test_hmi_tags.py` H1 per device (syrup addresses from `address_map_syrup.py`, area from the number range), `syrup_live()` = H4 + H2 on port 5022, read only. Live: **H4 syrup connected, H2 48/48** with the machine idle; 22 of 48 values non-zero, among them all 10 setpoints with distinct values, so an address shift would show. Outputs are 0 while idle: checked again with the simulator running in S3. Filler live part not run: the filler runtime did not answer on Modbus (D3, probably SAFE MODE after a suspend; upload from the Editor). Unit suite 304.

**S2 as built (2026-11-13):**
- **Layout** (1280 × 96, `HEADER_H` unchanged, so no view body moved):
  - Row 1, the same on every view: a **machine chip** per machine (coloured by its `State`, with state, step and mode; click = its screen: Filler → `Filler`, Syrup room → `Syrup tags` until S3), the **E-stop chip**, a **PLC link chip** per device, and **Line / Alarms / Trends**.
  - Row 2, the machine of the view: a machine box, Start / Stop / Reset, Auto / Manual / Stop, its alarm chips (filler 4, syrup room 6; click = its Alarms view).
- **The picker = sibling views.** Line, Alarms and Trends exist once per machine (`v_filler_line` / `v_syrup_line`, `…_alarms`, `…_trends`; names "Line (syrup room)" …), same body. On those views the row-2 machine box is two buttons; the other one opens the sibling. On a machine's own screen it is a plain label. Rejected: one view + a FUXA server tag "selected machine" with show/hide on row 2. That tag would be shared by every browser (two operators would switch each other's controls), and every row-2 shape would be drawn twice. The same shape id in two views was already proven (`lamp_S2` on Line and Filler).
- **Mode hint (I10) in the chip:** the mode word is "Stop – press Auto" in Stop mode and "Manual (paused)" in Manual, for BOTH machines at once. The yellow hint box and the raw step number are gone (the Tags pages show `Step`).
- **E-stop chip for two PLCs** (one chain, report 9.2), drawing order: grey chip → "E-stop OK" per PLC → a red cover per PLC (range colour `none` while it reads 1) → "E-STOP PRESSED" per PLC. Red when either PLC sees it pressed (a wiring fault shows too), and a PLC with a lost link leaves the decision to the other one.
- **Nav:** the header has Line / Alarms / Trends of the view's machine; the machine screens open from the chips; `Tags` moved to the side menu only (test page, like `Syrup tags`).
- **Alarms view:** Reset writes `Cmd` = 3 of its machine; the help text is machine-neutral. The syrup Trends view says that its charts are the filler's until S6.
- **Code:** `fuxa_views.MachineUi` (machine, title, step names, alarm bits, screen), `FILLER_UI` / `SYRUP_UI`, `SHARED` / `KIND` / `CONTEXT` (the machine of every view), `header()` → `machine_chip`, `estop_chip`, `link_chip`, `row2`. `line_view` / `alarms_view` / `trends_view` take a `view_id`. `demo_check.py` D7 now points the syrup room to the FUXA header.
- **Tests:** `test_hmi_tags.py` H5 rewritten per view with its machine: both chips (State, a text per state / step / mode value, target), E-stop per PLC + drawing order, both link chips, row 2 on the right machine, picker → sibling of the same kind, nav → same machine, and a **layout check** (header boxes inside 1280 × 96, no overlap in a row). It caught its own first bug: `rx="4"` was read as `x`. H7 / H9 also on the syrup views, H8 Reset + chips per machine. Six mutations of the export (syrup Start on the filler `Cmd`, a missing chip, a chip on the mode buttons, a wrong picker target, the E-stop on one PLC, the syrup Reset on the filler) each fail. Offline 7/7; import round trip identical; unit suite 304.
- **Browser** (Playwright/Firefox, simulator running, filler runtime down): 10/10. Header navigation over all views; the picker; the chips; **Auto → Reset → Start of the syrup room from its header: MIX, Running; Stop → IDLE at the end of the step**; Stop mode from `Line (syrup room)` reaches the syrup PLC and the chip shows "Stop – press Auto"; E-stop written on the syrup PLC → chip red "E-STOP PRESSED", released → "E-stop OK" (with the filler PLC down, so the syrup PLC alone decided). Live H4 + H2 syrup 48/48.
- **Not verified, filler runtime down** (no Modbus answer, SAFE MODE after a suspend; upload from the Editor): the filler row 2 live, filler H2–H4 / H10. The Trends charts stayed empty with `loadWatch` console errors, the same with the pre-S2 project, so this is not an S2 regression: check it again with the filler running.

**S3 as built (2026-11-13):**
- **View `Syrup room`** (`v_syrup_main`, `fuxa_views.syrup_view()`), the syrup chip in the header and the side menu open it. Same layout as the Filler screen:
  - **Process** (top): syrup tank, mixing tank / carbonator and buffer tank as level bars (0 to the tank size from `simulator.config`, live value in L, red tick at the PLC limits MixMax 950 / BufferMax 1950); pipes for mains water, syrup, CO2, cooling jacket, transfer and product to the filler, grey while their output is OFF and coloured while it is ON (report 8.3, pipes bound to pump and valve outputs); a lamp per output plus `MixHighHigh` and `Product_Available`.
  - **Batch** (right): step name, `BatchCount`, temperature (°C) + `Temp_OK`, CO2 pressure (bar) + `CO2_Demand`, `SyrupTarget`, `DoseTarget`, `Batch_Dosed`.
  - **Recipe setpoints** (bottom left): all 10, with the limits shown next to each input. The limits are chosen so that a legal recipe never trips an alarm: the largest batch 700 + 700 × 10 / 30 = 933 L < MixMax 950; its syrup 233 L takes 47 s < `DoseTimeout` 60 s; cooling to 2.0 °C takes about 41 s < `CoolTimeout` 60 s; the pressure setpoint stays at least 1 bar under `PressMax`; `BufferMin` ≥ 50 (at 0 `Product_Available` never drops).
  - **Manual** (bottom right): 6 hold-to-run buttons, visible only in the syrup room's Manual mode, the jog-watchdog text and the interlocks in words (`docs/syrup/state-diagram.md` section 7).
- **Scaling in FUXA, rounded.** The PLC keeps 0.1 °C / 0.01 bar INTs, the operator sees and types °C and bar. FUXA's Modbus `divisor` was rejected: on writing it passes value × 100 to Node's `writeInt16BE`, which cuts the fraction off, so 0.29 bar became 28 (checked in the container). The six tags (`ProductTemp`, `CoolSetpoint`, `TempHyst`, `CO2Pressure`, `PressSetpoint`, `PressHyst`) use FUXA's **expression scale** instead: read `this / 10`, write `Math.round(this * 10)`, `format` = decimals (`fuxa_tags.scale()`, `Machine.scales`). It is in FUXA's tag dialog (Scale → Expression), and the trends of S6 get °C and bar for free. The decimal inputs have `step="any"` (`fs.number_input(step=)`), otherwise the browser accepts only whole numbers.
- **Round trip:** FUXA stores the project through JavaScript, so `2000.0` came back as `2000`; the generator writes whole numbers as ints.
- **Tests:** `test_hmi_tags.py` **H11** (syrup screen: hold buttons on the syrup tags and its Manual mode, the 10 inputs with their limits and step, lamps, tank bars with the tank sizes, values, step texts, pipes on their outputs). **H1** checks the scale of every tag, **H2** compares scaled values (FUXA × factor), and **H3 syrup** writes `PressHyst` 0.29 bar through FUXA → PLC 29, then restores it. The live part no longer stops when the filler does not answer (one FAIL, then the syrup checks). Seven mutations (a hold button on a filler tag, min > max, a write expression without rounding, a tank bar on the wrong level, a missing `step`, a scale on a litre tag, a pipe on the wrong output) each fail. Unit tests +5 (input `step`, rounding), suite 309. Offline 8/8, import round trip identical, live syrup H4 / H2 48/48 / H3 0.29 → 29.
- **Browser** (Playwright, simulator running, filler runtime still down):
  - Screen: values in °C / bar; `CoolSetpoint` typed 4.5 → PLC 45, 4.0 → 40; Manual: holding *Cooling* switches `CoolingValve` on, releasing switches it off; holding *Agitator* with an empty tank keeps it off (interlock).
  - **`cooling_failure` followed on the screen** (10/10): a batch of 590 L reached COOL, the chiller died, and the temperature climbed 16.1 → 19.8 °C with `Temp_OK` off. `Alm_CoolTimeout` came, the header chip turned red (FAULT), and Reset + Start from the header resumed at MIX.
  - **`over_pressure`** (4/5; the one FAIL was the test's own threshold): in CARBONATE the pressure rose to 5.92 bar with the CO2 valve lamp OFF (the stuck valve is visible as "valve closed, pressure rising"). `Alm_OverPressure` came at 6.00 bar (the PLC alarms at ≥ `PressMax`), the scenario vented, and Reset + Start from the header continued.
- **Found while testing: the PLC remembers a dosed batch, the simulator does not.** A new simulator run starts with an empty mixing tank. If the PLC still has `Batch_Dosed` from an earlier run, Start resumes at MIX with 0 L: COOL never reaches `Temp_OK`, and the simulator vents an empty tank, so CARBONATE cannot finish either. The way out is an operator action on the new screen: Manual, the water pump in pulses under 10 s (jog watchdog) to about 590 L, then Auto + Start. The batch then finishes and clears `Batch_Dosed`. Before restarting the simulator, let the batch end and press Stop (IDLE).

**S4 as built (2026-11-14):**
- **Line view** (both `v_filler_line` and `v_syrup_line`, same body): the grey "Syrup room / simulated" stand-in is replaced by the real syrup PLC as a **machine block** (`machine_block(…, ft.SYRUP)`), above the filler block with the same x and width (464, y 108, 236 × 118), so the product pipe runs straight down between the two centres (y 226–270).
  - Coloured by the syrup `State` (report 8.2: grey Stopped, green Running, yellow Starved = low syrup, orange Blocked = WAIT_BUFFER, red FAULT); a click opens `Syrup room`.
  - Inside: name, state, **step** (`SYRUP_STEP_NAMES`) and the **buffer level** in L. The buffer is shown because it is the reason the product signal comes and goes. Everything on top lets the click through: texts with `click_through=True`, the value group with the new `click_through()` (SVG children inherit `pointer-events`).
- **Product pipe from the source:** `product_pipe` and "no product" are bound to the syrup PLC's output `Product_Available` (%MX6.0), no longer to the filler's input (%MX1.0, the copy the simulator makes).
- **"filler input" lamp** next to the pipe, on the filler's `Product_Available`: the other end of the wire. Pipe brown + lamp off = the signal does not reach the filler (simulator stopped, it emulates the hardwiring).
- **Tests:** `test_hmi_tags.py` H7 + `h7_syrup()`: block on the syrup `State` with one range per state colour, opens `Syrup room`, one state / step text per value on the syrup tags, buffer on `BufferLevel`, click-through of everything on the block, pipe + "no product" on the syrup tag, lamp on the filler tag, layout header < block < pipe < filler block. Eight mutations of the Line view (pipe and "no product" on the filler tag, block → Filler screen, block on the filler `State`, lamp on the syrup tag, a missing step text, the buffer value without click-through, block moved over the pipe) each fail. Offline 8/8, unit suite 309, only the two Line views changed in the export, import round trip identical.
- **Browser** (Playwright/Firefox, both PLCs without data): layout as planned; a click on the step text and on the buffer value opens `Syrup room`. Console: only `invalid language tag: "undefined"`, the same with the pre-S4 project (not S4).
- **Not verified live yet:** after `docker compose up -d` both runtimes had the Modbus server OFF (D2, `plugins.conf` reset), so no PLC data. Open: upload both programs from the Editor, then `test_hmi_tags.py` (H2–H4 live) and `buffer_starved` on the Line view (syrup block green while batching; buffer runs dry → pipe grey + "no product", filler yellow Starved; next batch → pipe brown again).

**S5 as built (2026-11-14):**
- **6 FUXA alarm definitions for the syrup room** (`fuxa_views.SYRUP_ALARMS`), names `syrup_<bit>`, group **"Syrup room"** (the machine's title, as in the header chip; the table above said "Syrup"). Same priority scheme as the filler: highhigh (red) = the bit puts the machine into FAULT, high (orange) = comm lost, low (yellow) = warning.

  | Bit | Priority | Text |
  | --- | --- | --- |
  | `Alm_Overflow` | highhigh | Overflow: mixing tank ≥ 950 L or high-high switch, or buffer ≥ 1950 L (FAULT) |
  | `Alm_OverPressure` | highhigh | Over-pressure: CO2 ≥ 6.00 bar in the mixing tank (FAULT) |
  | `Alm_CoolTimeout` | highhigh | Cooling timeout: cool setpoint not reached within 60 s in COOL (FAULT) |
  | `Alm_DoseTimeout` | highhigh | Dosing timeout: water or syrup level not reached within 60 s (pump failure, FAULT) |
  | `Alm_CommLost` | high | Plant heartbeat lost for 3 s (simulator link) |
  | `Alm_LowSyrup` | low | Low syrup: not enough for the next batch (warning, Starved; clears after refill) |

  - **Names per machine:** `alarm_name(bit, m)` = `<machine key>_<bit>`. Both PLCs have `Alm_CommLost`, so the prefix keeps them apart; the filler names (`filler_<bit>`) are unchanged, so its history stays. `alarm_definitions()` loops over the machines in the order of `ALARM_TEXTS` (machine → its alarms): the 4 filler definitions are byte-identical in the export.
- **One alarm list for the whole line** (report 8.1 "from all PLCs"): both Alarms views keep the same **unfiltered** active table, with a new **Machine** column (FUXA's `group` field). Columns: Time, Machine, Priority, Alarm, Status, Ack. FUXA could filter the table (its code: `alarmFilter.filterB[1]` = a group substring), but then a filler FAULT would be missing on the syrup room's Alarms view.
- **Reset stays per machine** (`Cmd` = 3 of the view's machine, S2). A new help line says so: "The list shows the alarms of both machines (column Machine); Reset acts on the <machine> only (other machine: picker in the header)".
- **Tests:** `test_hmi_tags.py` H8 per machine: one definition per alarm bit (`ALARM_TEXTS` keys = the machine's alarm bits), on that machine's tag, exactly one priority (the one in `ALARM_TEXTS`), group = machine title, active at 1, ackactive, unique names; each Alarms view has an unfiltered table with Ack and Machine columns. Eight mutations of the export (a syrup alarm on a filler tag, a missing one, two priorities, a wrong priority, group "Filler", a group filter on the table, no Machine column, a duplicate name) each fail. Offline 8/8, unit suite 309; in the export only the `alarms` list (+6) and the two Alarms views changed; import round trip identical.
- **Browser** (Playwright/Firefox, PLCs without data): both Alarms views show Time, Machine, Priority, Alarm, Status, Ack and the help line with the right machine; the picker switches. Console: only the known `invalid language tag` (see S4).
- **Not verified live yet** (both runtimes: Modbus server OFF, D2 – upload needed): `level_sensor_stuck` with `hmi.py --syrup auto reset start` → `syrup_Alm_Overflow` (Syrup room, highhigh) in the table and `/api/alarms`; Ack; after the drain, Reset (syrup) clears the bit and the acknowledged alarm leaves the list; `/api/alarmsHistory` has on, off and ack time.

**S6 as built (2026-11-14):**
- **DAQ for the syrup room** (`fuxa_tags.SYRUP_TREND_TAGS`, the syrup `Machine`'s `trend_tags`): `MixLevel`, `BufferLevel`, `ProductTemp`, `CoolSetpoint`, `CO2Pressure`, `PressSetpoint`, `State` – every change + once a minute, like the filler. The setpoints draw the target lines (as `FillSetpoint` does).
- **The history is in °C and bar:** FUXA's Modbus driver applies the tag's expression scale before it stores a value (`runtime/devices/modbus/index.js`: `tagValueCompose`, then `addDaq`), so the S3 scaling carries over to the DAQ and the charts.
- **4 charts** (`fuxa_views.SYRUP_CHARTS`, ids fixed): *Tank levels (L)* (mixing tank brown, buffer dark blue), *Product temperature (°C)* (+ setpoint grey), *CO2 pressure (bar)* (+ setpoint grey), each the last 15 min (a batch takes ~151 s, so about six batches and a whole buffer cycle), and *Syrup room state*, 30 min. Lines stepAfter (`fs.chart_line`); legend decimals 1 for °C, 2 for bar (`chart_options(decimalsPrecision=…)`).
- **Charts per machine:** `MACHINE_CHARTS` (machine → its charts); `charts()` binds every line to its machine's tag id and device name; `apply_charts()` replaces the 7 generated charts and keeps hand-made ones. The filler's charts and its Trends view are byte-identical in the export.
- **View `Trends (syrup room)`:** a 2 × 2 grid (levels, temperature / pressure, state), the state key and a short "normal batch" text; the old note "Syrup room charts are not built yet" is gone.
- **Tests:** `test_hmi_tags.py` **H9 per machine**: DAQ on exactly that machine's trend tags (every change), every chart line bound to its device + tag, stepAfter, **and logged** (a line on a tag without DAQ has no history to load), its Trends view shows exactly its charts (realtime + history). **H10 syrup** (live, in `syrup_live()`): points of every syrup trend tag in the last 10 min, and the stored setpoints in display units (CoolSetpoint ≤ 10 °C, PressSetpoint ≤ 5 bar; raw INTs would be 20–100 / 200–500). Eight mutations (a syrup line on a filler tag, on the filler device, linear, on an unlogged tag; DAQ off on MixLevel, DAQ on an extra tag; a filler chart on the syrup view; no history loading) each fail. Offline 8/8, unit suite 309; in the export only the 7 syrup DAQ flags, the `charts` list (+4) and `v_syrup_trends` changed; import round trip identical.
- **Browser** (Playwright/Firefox, PLCs without data): both Trends views draw their charts with titles, axes and legends, **0 console errors**.
- **Solved: the empty charts and `loadWatch` errors** seen since S2 were a test-browser artefact, not FUXA or the project. Playwright's headless Firefox started without a locale; FUXA's script bundle then threw `invalid language tag: "undefined"` before it defined `uPlot`, and every chart widget failed with `uPlot is undefined`. With `new_page(locale="en-US")` the bundle loads and the charts draw; a normal browser always has a locale. The same error in the S4 / S5 notes has the same cause.
- **Not verified live yet** (both runtimes: Modbus server OFF, D2 – upload needed): one batch with the simulator as a curve (mixing level up in two slopes, temperature down to the setpoint, pressure up to the setpoint, transfer into the buffer), and H10 syrup.

**S7 as built (2026-11-14):**
- **`demo_check.py` D4 per PLC:** one `/api/getTagValue` call with the connection status tag of every runtime (`t_<label>_connection_status`, the label = the FUXA machine key), one PASS / FAIL line per PLC (`D4 FUXA -> syrup PLC`). Before, D4 read only the filler's link, so a FUXA that had lost the syrup PLC passed. The hint "install the Modbus plugin" comes only when **every** link is down (a missing plugin breaks all devices; one link down = that PLC, see D3). Checked: the real run (FUXA up, both PLCs without Modbus server) gives two FAIL lines; with FUXA's answer replaced in a scratch script: filler 5 / syrup 0 → PASS + FAIL, 5 / 5 → 2 PASS, 0 / 0 → 2 FAIL with the plugin hint, syrup missing → FAIL, FUXA down → one FAIL and no D5.
- **Demo script for the whole line** (`docs/hmi/demo-script.md`, ~15 min, 12 steps): preparation for two PLCs (upload hint from D2, Ack + Reset per machine, Auto + Start for both, one batch before the demo so the trends have curves); new steps *Syrup room* screen, syrup *Trends*, `buffer_starved` (the `Product_Available` handshake: filler Starved without an alarm, continues by itself) and `level_sensor_stuck` (the high-high switch as the hardware backup, the *Machine* column in the alarm list), run in that order so the stuck sensor comes at the next batch at once; `e_stop` now stops both machines (one chain, `plant.py`), so both need Start. Spares, things to avoid (no simulator restart mid-batch; syrup scenarios wait for their step) and troubleshooting for both machines. Facts checked against `scenarios.py` and the setpoint defaults (`BufferLow` 800 L). **To rehearse once with live PLCs** – the syrup screens of S4–S6 have not seen live data yet.
- **ISA-101 review** of the syrup screens (screenshots without PLC data, `locale="en-US"`): grey page, colour only for the state block, alarm chips, flowing pipes and the red limit ticks; units next to every value; no overlap. No change. (Without data the Manual buttons show, because FUXA has no `ModeReq` value to hide them; live they appear only in Manual – checked in S3.)
- **Report 8.1** updated: the syrup room is built, with the list of screens for both machines.
- **Done live:** `demo_check.py` 0 FAIL and the live checks of S4–S6 (2026-11-14), one rehearsal of the demo script (2026-11-15).

**Live verification S4–S6 (2026-11-14):** both PLCs running, one simulator loop in a scratch driver (the HMI over Modbus), screens checked in a headless browser (`locale="en-US"`) and through FUXA's API; 24/26 checks, both FAILs were the driver's own conditions (log in `docs/syrup/progress.md`).
- `test_hmi_tags.py` live **16/16** with the machines idle. With the line running, H2 found the filler's `FillLevel` one FUXA poll behind (290 / 310 ml): H2 is meant for an idle line.
- **S4:** the syrup block is green with the step text during a batch and grey when stopped; the pipe is brown, or grey with "no product" and a yellow filler while the buffer is below `Product min`; `buffer_starved` recovers by itself. The agitator turns during MIX.
- **S5:** `level_sensor_stuck` → `Alm_Overflow`, FAULT; FUXA lists `syrup_Alm_Overflow` (Syrup room, highhigh) in the table with the Machine column; Ack → status NA; Reset clears the bit and the acknowledged alarm leaves the list; the history has on, off and ack time.
- **S6:** one batch in FUXA's history in display units (mixing level 590 L, temperature down to 4.0 °C, pressure up to 3.53 bar); H10 syrup passes; the syrup Trends screen opens without errors.

## Labeler screens (third machine)

Third PLC on the same screens (planned 2026-11-19). Same working rule as above: plan → OK → build (generated by `fuxa_tags.py` / `fuxa_views.py`), test per stage, export, tick here and in `docs/labeler/progress.md` section 3.

**Decisions (2026-11-19):**
- **Header for three machines:** three machine chips (~250 px each) in row 1. Each chip carries its **PLC link strip** (grey OK / yellow slow / red LOST); the separate link chips go away, so E-stop and Line / Alarms / Trends keep their places. Rejected: narrower chips with three 76 px link chips (step and mode words cramped), a taller header (every view body of filler and syrup room moves).
- **Line view in one row, shrunk:** slot pitch 38 → 30 px (conveyor 240 px), narrower blocks, C3 after the labeler, packer stand-in under the end of C3. Rejected: C3 on a second row running right to left (a bend in the line, reversed slot bits).
- **Animation words** (deferred from simulator stage 8): `Anim_C3` %MW70 (C3 slot bits, bit 0 = entry … bit 7 = packer end) and `Anim_Station` %MW71 (pocket code 0 empty, 1 bottle unlabelled, 2 labelling, 3 labelled), written by the simulator, ignored by the PLC. No ladder change: Modbus serves %MW70–71 without a declared variable (the filler's %MW70–75 are not declared either).
- **Roll low / glue low:** FUXA alarms (low) straight on the sensor bits `Roll_Low` / `Glue_Low` (labeler decision 2026-11-15, no PLC bit).
- **Counter reset:** a "Reset counter" button on the labeler screen writes `Cmd` = 4 (one write per click, I13).

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| L0 | Animation words (simulator) | `address_map_labeler.py`: `ANIM_C3`, `ANIM_STATION` in `SIM_WRITE_REGISTERS`; `plcs.LabelerPlc` writes both per tick (as `encode_animation` for the filler); `docs/labeler/io-list.md`, design notes | unit: encoding + address-map guard; live `test_link_labeler.py` reads both back; `test_closed_loop_labeler.py`: C3 bits move, code 1 → 2 → 3 → 0 |
| L1 | Device + tags | `fuxa_tags.LABELER_TAGS` (~53 tags from `address_map_labeler.py`), `Machine("labeler", "Labeler PLC", "plc-labeler:502", …)`, `LABELER_SCALES` (`GlueTemp`, `GlueSetpoint`, `GlueHyst`, `GlueBand` ÷ 10 → °C, rounded on write), test page `Labeler tags` (side menu) | H1 per device; live H4 + H2 labeler |
| L2 | Header for three machines | Row 1: 3 chips with link strip (16–782), E-stop 790–930 (red when any of the 3 PLCs reads 0), nav unchanged. Row 2: picker 3 × 70 px, commands and mode shifted, alarm chips 78 px (7 labeler chips). `LABELER_UI`, `SHARED` / `CONTEXT`, views `Line / Alarms / Trends (labeler)` | H5 for 3 machines + layout check (1280 × 96, no overlap) + mutations; browser: Auto / Reset / Start of the labeler from its header |
| L3 | Labeler screen | Process (gate, pocket from `Anim_Station`, bell, turntable, label roll + web + knife, glue roller, melt tank as a temperature bar with a red tick at `GlueMax` 200 °C and the window band, heater, pusher → C3), values (step, `GoodCount` + Reset counter, °C, `Glue_Ready`, `Heat_Demand`, `Bottle_Entering`), 4 setpoints with limits (`GlueSetpoint` 165–180 °C, `GlueHyst` 2–5 °C, `GlueBand` 6–15 °C, `PressTurns` 1–4; checked against the PLC constants at build time), 7 hold-to-run buttons (Manual only), interlocks in words, "heater automatic in every mode" | new H12 + mutations; live H3 labeler (`GlueHyst` 3.5 °C → 35); browser: `heater_failed`, `web_torn` |
| L4 | Line overview | Re-laid out: blow moulder, C1, filler (syrup block above), C2 (bottles capped, 2026-11-20), **labeler block** (State colour, step, glue °C, click → screen), **C3** from `Anim_C3`, packer stand-in. C2 rollers on the labeler's `ConveyorMotor2`; C3 rollers static (stand-in). `C3_Full` / `Outfeed_Full` lamps, both `GoodCount`s | H7 updated + mutations; browser: `packer_stopped` (C3 fills, labeler orange Blocked) |
| L5 | Alarms | `LABELER_ALARMS`: 4 FAULT bits highhigh, `Alm_CommLost` high, `Alm_OutfeedBlocked` / `Alm_LabelEmpty` + `Roll_Low` / `Glue_Low` low; names `labeler_<bit>`, group "Labeler"; one list, Reset per machine | H8 per machine; browser: `roll_empty` |
| L6 | Trends | DAQ `GlueTemp`, `GlueSetpoint`, `GoodCount`, `State`; charts glue temperature (°C, 15 min), labelled bottles (30 min), state (30 min); view `Trends (labeler)` | H9 / H10 labeler (history in °C); browser: one warm-up as a curve |
| L7 | Wrap-up | `demo_check.py` labeler `Runtime.fuxa = True`; demo script for three machines; report 8.1, `CLAUDE.md`, progress; ISA-101 review | `demo_check.py` 0 FAIL with 3 PLCs; one rehearsal |

Order: L0 first (the screen and the Line view need the animation words), L2 before L3 / L4 (every view draws the header). Risks: I13 (one write per click), I16 (write timeouts), overlap after the header re-layout (H5 layout check), Modbus server OFF after a container re-creation (D2 → upload).

**L0 as built (2026-11-19):**
- `address_map_labeler.py`: `ANIM_C3` / `ANIM_STATION` (HR 1094–1095), `ANIM_START` / `ANIM_COUNT`, codes `POCKET_EMPTY` … `POCKET_LABELLED` (0–3), both words in `SIM_WRITE_REGISTERS`. `Labeler.pocket_code()` ("labelled" = the audit's whole label, ≥ 90 %); `LabelerWrites.anim` = (C3 slot bits, pocket code) from `plant.labeler_writes()`; `LabelerPlc.write()` sends them as a fourth request (`encode_labeler_animation`), not read back by `verify_writes()` (design notes).
- `docs/labeler/io-list.md` "Animation data" table; the address-map test keeps them in their own `ANIMATION` dict (checked against io-list, and NOT allowed in `main.ld`, which stays at 51 located variables).
- Unit tests +9, suite **443 passed**: pocket code thresholds, encoding (a word ≥ 2¹⁵ refused), the link writes HR 1094–1095 and leaves the status words alone, the plant's words over one bottle: codes 0 → 1 → 2 → 3 → 0, C3 bits = `c3.slot_bits()` every tick, slots 1–6 seen (0 and 7 not at a tick end: design notes).
- **Live (2026-11-19, after uploading the three programs; `demo_check.py` 0 FAIL):** `test_link_labeler.py` **10/10**: L6b read [165, 3] back as written, the status words unchanged. `test_closed_loop_labeler.py` (filler + labeler) **6/6**: K6 saw pocket codes 0, 1, 2, 3 and 7 different non-zero C3 words in the PLC. The first run had 4/6: K6 passed, but `Alm_PocketJam` came 1.8 s after the run. My K6 reader was awaited BETWEEN the two loop runs, the plant froze while the PLC scanned on, and the gate pulse fired into it (the trap in the design notes). Fixed: the reader stops after the settle run. `run_labeler.py --quick` **179/179** (9 scripts, 3.6 min).

**L1 as built (2026-11-19):**
- `fuxa_tags.LABELER_TAGS`: **53 tags** from `address_map_labeler.py` (status 3, alarms 7, outputs 9, conditioned 6, sensors 10 + `GlueTemp`, wiring 2, `Cmd` / `ModeReq` + 4 setpoints, manual 7, animation 2), descriptions from `io-list.md` with the same group prefixes. `Machine("labeler", "Labeler PLC", "plc-labeler:502", …)`: the service name and the container port, as the other two. `LABELER_SCALES`: `GlueTemp`, `GlueSetpoint`, `GlueHyst`, `GlueBand` ÷ 10 (FUXA's expression scale, rounded on write: the S3 solution). No DAQ yet (L6). `MACHINES` = filler, syrup room, labeler.
- Test page **`Labeler tags`** (`v_labeler_tags`, side menu only). It carries the filler's header until L2, as `Syrup tags` did in S1: a labeler row 2 needs the three-machine header.
- **Export:** a large git diff (+18k / −14k lines) that is only the sorted-key order shifting (`d_labeler` sorts between `d_filler` and `d_syrup`). Checked: the new export minus the labeler device, its status tag, its test page and its menu entry is **identical** to the old export. Import round trip identical.
- **Tests:** `test_hmi_tags.py` H1 per device also for the labeler (`expected_labeler_addresses()`: every address constant except the block anchors, `UNUSED_MX1_0` and the pocket code values), new `labeler_live()` = H4 + H2 + **H3 labeler** (`GlueHyst` 3.5 °C through FUXA → PLC 35, restored). Offline 9/9; **live 20/20**: labeler connected, **53/53** values equal (setpoints 170.0 / 5.0 / 5.0 °C / 2 turns are different values, so an address shift would show), 3.5 °C → 35. Three mutations of a copy of the export (`GlueTemp` one address off, `GlueSetpoint` without its scale, `Anim_Station` missing) each fail H1. Unit suite 443 passed.
- `demo_check.py` still has the labeler `Runtime.fuxa = False` (D4 skips it): switched in L7 with the screens, as planned.

**L2 as built (2026-11-19):**
- **Row 1:** three machine chips at 250 px (16–782), E-stop 790–930, Line / Alarms / Trends unchanged at 1004. The separate link chips are gone (decision above). Each chip carries a **link cover** over its right part (112 px, `link_cover()`), on FUXA's connection status of that device: `none` while connected (the chip shows state and mode as before), yellow "PLC slow", red "PLC LOST". It is drawn after the state and mode words, so a lost link hides those stale values. It lets the click through to the chip (`no_click`), so the machine's screen still opens.
- **Row 2:** a three-button picker (76 px, short labels Filler / Syrup / Labeler: "Syrup room" does not fit at 14 px bold, `MachineUi.short`), Start / Stop / Reset at 262, mode at 478, alarm chips 78 px from 694 (the labeler's 7 end at 1264; chip text 10 px). On a machine's own screen the row-2 label spans the width of the three buttons.
- **`LABELER_UI`** (steps and alarm bits from `plcs.py`; its chip opens `Labeler tags` until its screen exists in L3). New sibling views **`Line (labeler)`, `Alarms (labeler)`, `Trends (labeler)`**: Line and Alarms with the same body; Trends with a note "No labeler charts yet (L6)" (`MACHINE_CHARTS["labeler"] = ()`). The Alarms help text says "every machine". 5 labeler alarm-chip labels in `ALARM_LABELS`. `alarm_definitions()` skips a machine without alarm texts (labeler until L5).
- **Tests:** `test_hmi_tags.py` H5 for three machines: a link cover per chip on its device's status, with a "PLC LOST" text, drawn after the mode words and click-through; the layout check now allows exactly one overlap per machine (chip + its own cover) and requires the cover to lie **inside** that chip. H7 and H8 loop over `fv.SHARED`; H8 prints the labeler's alarm definitions as **pending (L5)**, not silently skipped. Offline 9/9; six mutations each fail H5 (cover moved out of its chip, cover catching clicks, cover on another PLC's status, a Trends picker opening a Line view, the labeler's Reset writing the filler's `Cmd`, the E-stop without the labeler). Import round trip identical; live 20/20; unit suite 443.
- **Browser** (Playwright 1.64.0 via `uv run --with`, the version that matches the cached Firefox build 1555; headless simulator running for the heartbeat): 10/10. The picker on Line opens `Line (labeler)`; **Auto → Reset → Start from the labeler's header: WARM_UP**, the chip shows the step, Stop → IDLE; the filler's mode untouched; the labeler chip opens `Labeler tags`; no page errors; a screenshot shows nothing cut off. **Labeler container stopped for a moment:** red "PLC LOST" on the labeler chip only. Started again: program and Modbus server kept (stop / start, not a re-creation).
- **Found: I17** (below). The same screenshot showed "E-STOP PRESSED" while only the link was lost.

**L3 as built (2026-11-19):**
- **View `Labeler`** (`v_labeler_main`, `fuxa_views.labeler_view()`); the labeler chip and the side menu open it (the chip opened `Labeler tags` until now). Same four areas as the syrup room's screen:
  - **Process:** C2's end with the gate (a bar, white while `GateMotor` pulses), the pocket as a bottle from `Anim_Station` with its cap while not empty (empty / brown bottle / grey label band while labelling / white label when labelled, with the word under it), the bell above it (dark while `Bell_Down`), the turntable below it (turns with `SpinMotor`, the agitator shape of S3), the label roll (a disc with a core and three spokes that turns while `LabelFeed` runs, the conveyor-roller action, added 2026-11-20) and its web (grey, white while `LabelFeed` runs), the 8 C3 slots from `Anim_C3`, each bottle with its cap and a white label band on the same slot bit (every bottle on C3 is labelled: no reject; also on the Line view's C3, 2026-11-20), and a lamp for every sensor, output and conditioned bit next to where it acts.
  - **Melt tank:** `GlueTemp` as a bar 0–220 °C with a red tick at `GlueMax` 200 °C, the setpoint as a thin grey bar beside it, the value in °C, lamps heater / heat wanted / glue ready / glue low.
  - **Labeling:** step name, labelled bottles with a **"Reset counter"** button (one write: `Cmd` = 4), glue temperature and setpoint in °C, `Starved`, `Jog_Block`, a note that Reset in the header keeps the count.
  - **Setpoints:** 4 inputs with limits (`GlueSetpoint` 165–180 °C, `GlueHyst` 2–5 °C, `GlueBand` 6–15 °C, `PressTurns` 1–4; decimals allowed for °C), checked against the PLC and the simulator: lowest ready glue 165 − 15 = 150 °C = `GLUE_STICK_MIN_C`; highest window edge 195 °C < `GlueMax`; band ≤ 5 < 6 ≤ window, so `Glue_Ready` holds while the heater cycles; slowest warm-up (to 174 °C) about 51 s < `HeatTimeout` 120 s; 4 turns = 4 s < `LabelTimeout` 5 s. The defaults (170 / 5 / 10 °C, 2) lie inside.
  - **Manual:** 7 hold-to-run buttons (4 per row, only in the labeler's Manual mode; since 2026-11-24 the PLC turns clicks on *Bell down/up* into a toggle and *Feed + glue* also turns the table, `docs/labeler/ladder-roadmap.md` "Change: bell as a toggle in Manual"), the jog-watchdog line, the interlocks of `state-diagram.md` rule 9 in words, "heater: automatic in every mode".
- **Code:** `pipe()` and `decimals()` take a machine (default the syrup room: its screen and the filler's are byte-identical to L2, checked without the header). `POCKET_*` codes come from `address_map_labeler.py`.
- **Tests:** new **H12** (`test_hmi_tags.py`): hold buttons on the labeler tags and its Manual mode, one per `Man_*`; inputs with limits and `step="any"`; **the limit rules themselves** (stick limit, `GlueMax`, band < window, turns < timeout), so a later change of a range cannot break them silently; a lamp per signal; both bars with the 220 °C scale and the GlueMax tick; values; Reset counter = exactly one write of labeler `Cmd` 4; pocket body / label / word on every `Anim_Station` code; C3 slots on their bits; turntable on `SpinMotor`; web on `LabelFeed`; step texts; `Jog_Block` text. Offline 10/10; ten mutations each fail H12 (a hold button on a filler tag, Reset counter writing 3 or writing twice, a C3 slot on the wrong bit, the turntable on `GlueRoller`, a °C input without decimals, a missing pocket code, and three limit changes: window 4 below band 5, setpoint up to 190 °C, 5 turns – each named by its rule). Import round trip identical; live 21/21; unit suite 443.
- **Browser** (Playwright 1.64.0, the real line: filler + syrup room + labeler started from FUXA's headers, simulator with **`web_torn`**): 12/12. The labeler chip opens the screen; Auto / Reset / Start from its header → WARM_UP → WAIT_BOTTLE with 160.7 °C on the screen; the torn web → FAULT, the step text and `Alm_LabelTimeout`; Reset + Start from the screen's header → labelling again (`GoodCount` +1); `GlueSetpoint` typed 175 → PLC 1750, restored; 190 refused; Reset counter 2 → 0; Manual: Turntable held with the bell up → `Man_Spin` 1 but `SpinMotor` off (interlock), released → 0; buttons hidden outside Manual; no page errors. One layout fix after the screenshot ("gate open (pulse)" ran into the next lamp).
- **Side effect of the test, repaired:** a first browser run was stopped mid-batch, so the syrup room fell into the known trap (demo script: `Batch_Dosed` remembered, new simulator with an empty mixing tank → `Alm_CoolTimeout`). Recovered as the demo script says (Manual, water pump in 7 s pulses to 702 L, Auto + Start: one batch to the end, Stop → IDLE).

**I17 as built (2026-11-19, option B chosen by the user before L4):**
- **One E-stop segment per PLC** (`fuxa_views.estop_chip`), 3 × 66 px in 790–996 (the space up to the nav buttons). Per segment, drawing order: the segment on that PLC's `E_Stop_OK` (red at 0, grey at 1) → "E-stop OK" / "E-STOP" (white) → a **link cover over the whole segment** on that PLC's FUXA connection status (transparent while connected, light grey `#dedede` with a border in the same colour otherwise) → the PLC's short name and "no link" on top. A real E-stop turns all three red; one red segment alone is a wiring fault; a lost PLC is grey "no link" and leaves the decision to the others, which is the rule S2 wanted.
- **First version wrong, found in the browser:** the cover only covered the bottom line. The top half of a lost PLC's segment stayed red, because FUXA colours the segment from `null` too. Now the cover is the whole segment, with the name drawn after it; a 1 px border in the cover's colour hides the red anti-aliased edge.
- **Tests:** H5 per PLC: segment ranges (0 red, 1 grey), OK / E-STOP texts, cover on that PLC's own status with the "no link" text, drawing order segment → texts → cover → name → "no link", **cover box = segment box**, segments in machine order; the layout check allows only a cover inside its own box. Mutations caught: cover on the bottom half only (the first version), cover on another PLC's status, name drawn under the cover, cover missing, a segment red at 1, segments swapped, a cover wider than its segment. Offline 10/10, live 21/21, unit 443, `demo_check.py` 0 FAIL.
- **Browser** (Playwright 1.64.0, no simulator): 4/4. All connected → three "E-stop OK"; `E_Stop_OK` = 0 written into the filler → only its segment red "E-STOP"; labeler container stopped → **the topmost element at its segment is the grey cover** (`elementFromPoint`), "no link", chip "PLC LOST", filler and syrup still OK; labeler started again (program and Modbus server kept) → three "E-stop OK". Screenshots `simulator/logs/i17_*.png` (git-ignored).
- Demo script step 9 now says "each PLC's E-stop segment turns red".

**L4 as built (2026-11-19):**
- **Line view in one row** (all three Line views, same body; decision above): blow moulder stand-in 16–96, C1 108–348, filler 364–564 with the syrup room block above it (same x and width, product pipe unchanged), C2 580–820, **labeler block 836–996**, **C3 1012–1252**, packer stand-in (1140–1264, y 450) under the end of C3. Slots 30 px (`SLOT_PITCH`), rollers every 105 px; filler mini pockets and index rollers moved inside the narrower block. The x positions are constants (`C1_X`, `FILLER_X`, `BLOCK_W`, `C2_X`, `LABELER_X`, `LABELER_W`, `C3_LINE_X`, `PACKER_X`).
- **Labeler block** (`machine_block(…, ft.LABELER)`): coloured by the labeler's `State`, click → `Labeler`; step name and glue temperature (°C) on top, click-through.
- **`conveyor()` takes two machines:** the PLC holding the slot word and the PLC driving the motor. C2: slots in the filler PLC (`Anim_C2`), rollers on the **labeler's** `ConveyorMotor2` (until now static, because a stand-in drove C2). C3: slots in the labeler PLC (`Anim_C3`, L0), rollers static (packer stand-in, no PLC output). With `uv run simulator --no-labeler` the C2 rollers stand still while bottles move (the stand-in drives C2 then; the screen follows the PLC).
- Lamps `C3_Full` / `Outfeed_Full` of the labeler under C3; counters "Good bottles" (filler) and "Labelled" (labeler).
- **Bug found in the browser, fixed:** both "outfeed full" lamps (filler at C2, labeler at C3) got the element id `lamp_Outfeed_Full` (`lamp()` built the id from the tag name). One id = one FUXA binding, so the labeler's bit drove the C2 lamp and the C3 lamp stayed off while the labeler was Blocked. `lamp()` now takes an `element_id`; the labeler's C3 lamps are `lamp_labeler_C3_Full` / `lamp_labeler_Outfeed_Full`. New in **H5, for every view: no element id twice** (none elsewhere); H7 checks both lamps' bindings. The old duplicate, put back into a copy, fails H5 and H7.
- **Tests:** H7 for every Line view: C1 / C2 slots on the filler's words, C3 slots on the labeler's `Anim_C3` bits; C2 rollers on the labeler's `ConveyorMotor2`, C3 rollers static; labeler block on its `State` (a range per state colour), opens `Labeler`, step texts and glue value click-through; the labelled counter on the labeler; **the row left to right without overlap** (C1 < filler < C2 < labeler < C3, inside 1280) and the packer stand-in below the end of C3. Eight mutations each fail H7 (C2 rollers static again or on the filler's C1 motor, a C3 slot on the filler's `Anim_C2`, the labeler block opening `Labeler tags` or coloured by the filler's State, the block moved over C2, the glue value catching the click, a C3 roller turning). Offline 10/10, import round trip identical, live 21/21, unit 443.
- **Browser** (Playwright 1.64.0; filler + labeler started from FUXA's headers, syrup room left in Stop – its `Product_Available` does not depend on the mode, and no batch means no `Batch_Dosed` trap; simulator with **`packer_stopped`**): 5/5. Labeler block green while running; packer stopped → **C3 fills slot by slot on the screen (7 bottles, `Anim_C3` 11111110), block orange "Blocked", WAIT_OUTFEED**; each outfeed lamp shows its own PLC; after `Alm_OutfeedBlocked` (2 min) the packer runs again, Blocked ends, C3 empties; no page errors. Screenshots `simulator/logs/l4_*.png`.

**L5 as built (2026-11-19):**
- **9 FUXA alarm definitions for the labeler** (`fuxa_views.LABELER_ALARMS`), names `labeler_<bit>`, group **"Labeler"**, same priority scheme as the other machines:

  | Tag | Priority | Text |
  | --- | --- | --- |
  | `Alm_LabelTimeout` | highhigh | Label timeout: no label mark within 5 s in WRAP (web torn) or turns not reached within 5 s in PRESS (FAULT) |
  | `Alm_PocketJam` | highhigh | Pocket jam: bottle not in the pocket, bell not moved within 2 s, or not discharged within 3 s (FAULT) |
  | `Alm_GlueOverTemp` | highhigh | Glue over-temperature: 200 °C reached, heater off (FAULT; Reset below 200 °C) |
  | `Alm_GlueHeatTimeout` | highhigh | Glue heat-up timeout: not ready within 120 s of WARM_UP (heater failed, FAULT) |
  | `Alm_CommLost` | high | Plant heartbeat lost for 3 s (simulator link) |
  | `Alm_OutfeedBlocked` | low | Outfeed blocked longer than 2 min: C3 full (warning, no FAULT) |
  | `Alm_LabelEmpty` | low | Label roll empty: no new bottle (warning, Starved; clears after the roll change) |
  | `Roll_Low` (sensor) | low | Label roll low: prepare the next roll (warning) |
  | `Glue_Low` (sensor) | low | Glue low in the melt tank: add glue blocks (warning) |

  - **The two early warnings sit on the sensor bits** (`SENSOR_WARNINGS`), as decided 2026-11-15: no PLC alarm bit, nothing latched, the PLC program does not change. They have no header chip (row 2 is full with the 7 PLC alarm bits); they show in the alarm list and as lamps on the Labeler screen.
  - The list stays one table for the whole line (19 definitions: filler 4, syrup room 6, labeler 9); the filler's and the syrup room's definitions are unchanged. `alarm_definitions()` again requires alarm texts for every machine (the L2 fallback `.get(…, {})` is gone).
- **Tests:** H8 for every machine: texts = alarm bits + its sensor warnings; a sensor warning must be priority low and on an existing tag of that machine; the rest as before (one priority, group = machine title, ackactive, the Alarms view's Reset on the machine's `Cmd`). The "pending (L5)" note is gone. Six mutations each fail H8 (a labeler alarm on a filler bit, `Roll_Low` as high, `Glue_Low` missing, group "Filler", the label timeout as a warning, the labeler Alarms view resetting the syrup room). Offline 10/10, round trip identical, live 21/21, unit 443.
- **Browser** (Playwright 1.64.0; filler + labeler from FUXA's headers, syrup room in Stop; simulator with **`roll_empty`**): 5/5. A roll with 2 labels → **"Label roll low"** in the list (Labeler, low); after the last label → **"Label roll empty"** listed, the labeler chip yellow (Starved), its header chip "Roll empty" red; new roll → it continues by itself, no Reset, both warnings off (FUXA status `NF`: still listed until acknowledged); Ack in the table → both leave the list; no page errors.
- **Test findings (the screens were right, the first checks were not):** FUXA's alarm table draws a new row a few seconds after `/api/alarms` has it, so the checks wait up to 6 s for the row. A cleared but unacknowledged alarm keeps `value` 1 in `/api/alarms` and gets an `offtime` (status `NF`), so "off" is checked by `offtime`.

**L6 as built (2026-11-19):**
- **DAQ on 4 labeler tags** (`fuxa_tags.LABELER_TREND_TAGS`: `GlueTemp`, `GlueSetpoint`, `GoodCount`, `State`; every change + once a minute, as the other machines). FUXA scales before it stores, so the glue history is in °C (the syrup room's S6 finding).
- **3 charts** (`fuxa_views.LABELER_CHARTS`): "Glue temperature (°C)" (glue + setpoint, 15 min, 1 decimal), "Labelled bottles" (30 min), "Labeler state" (30 min); stepAfter lines like every chart. View **`Trends (labeler)`** (`labeler_trends()`) in the filler's layout: glue across the top, count left and state right below; a "Normal" line (warm-up in under a minute, then the heater's saw tooth in its band), the state key, the window lengths. The note "No labeler charts yet" is gone.
- **Tests:** H9 needed no code change (it already loops over every machine and `MACHINE_CHARTS`). New **H10 labeler** (`h10_daq_labeler()`, in `labeler_live()`): points of every labeler trend tag in the last 10 min, and the stored values in °C (`GlueSetpoint` ≤ 200, `GlueTemp` ≤ 250; raw INTs would be 1650–1800 / up to 2000). Offline 10/10; **live 22/22**; unit 443. Seven H9 mutations each fail (glue line on the filler device, DAQ off on `GlueTemp`, DAQ on `GlueBand`, setpoint line linear, a chart without history loading, a filler chart on the labeler view, the count line on the filler's `GoodCount`); two H10 mutations (history ×10 = unscaled, no `GoodCount` history) each fail.
- **Export:** only the 4 labeler DAQ flags, the `charts` list (+3) and `v_labeler_trends` changed (checked with a key-by-key diff against the export before); import round trip identical.
- **Browser** (Playwright 1.64.0; headless simulator with all three PLCs, the labeler alone started with `hmi.py --labeler auto reset start`): 4/4. **One warm-up as a curve:** FUXA's history 20.0 → 170.1 °C (WARM_UP 6.6 s → WAIT_BOTTLE 50.0 s, 43 s), the state line 0 → 1 → 2 (Running, then Starved without bottles); `Trends (labeler)` opens from the header (picker Labeler → Trends) with 3 drawn charts; no page errors. Screenshot `simulator/logs/l6_trends.png`, script `simulator/logs/l6_browser.py` (git-ignored).
- **Seen in the curve, not a fault:** the glue line starts at 170 °C and drops to 20 °C when the simulator starts. The PLC keeps the last `GlueTemp` the previous simulator wrote; a new simulator starts with a cold tank (`ROOM_C`). Also seen: `GlueTemp` changes at 0.1 °C, so it stores many points (≈ 380 in 10 min while heating or cycling); the filler's `FillLevel` and the syrup room's `ProductTemp` are logged the same way.

**L7 as built (2026-11-19):**
- **`demo_check.py`:** the labeler runtime has a FUXA device now (default `fuxa=True`), so D4 prints one line per PLC for all three; its operator hint points at the FUXA header (row 2: Labeler) instead of `hmi.py --labeler`. The `fuxa` flag stays for the packer and blow moulder. Live: **11 PASS, 4 WARN, 0 FAIL** (WARN = no simulator / `Alm_CommLost` latched). With FUXA's answer replaced in a scratch run: labeler at 0 → only its line FAIL without the plugin hint; all at 0 → three FAIL with the hint.
- **Demo script for three machines** (`docs/hmi/demo-script.md`, ~20 min, 14 steps): preparation for three PLCs (upload `plcs/labeler`, one Reset per machine, panel tab 3 defaults, Auto + Start per machine with the picker, the labeler warms up first); new steps *Labeler* screen and *Trends* with the picker; **`labeler_stopped` replaced by `packer_stopped`**: with the real labeler (the CLI default) the panel does not offer `labeler_stopped` (it needs the stand-in), and the old script ran it in parallel with `buffer_starved`. The panel has **one scenario slot**: Run replaces a running scenario, which stops where it was (the stand-in would have stayed stopped). The new script says "one scenario at a time" and lists the fix (switch *Packer running* ON). New step `web_torn` (labeler FAULT, Reset + Start on its screen); `e_stop` now three segments and three Starts; labeler spares and troubleshooting rows (C2 full with the labeler grey, heat timeout). Checked against `state-diagram.md`: in FAULT the heater is off too (first draft said it keeps the glue warm – corrected).
- **Rehearsal** (one run of the whole script, FUXA as the only operator: Playwright clicks the header, Ack in the table; a scratch driver runs the panel's loop with one scenario slot fed from a file; Modbus only read to measure; scripts `simulator/logs/l7_driver.py` / `l7_rehearsal.py`, screenshots `l7_*.png`, git-ignored): all demo steps passed, **0 page errors**, no lost bottle or broken label in the simulator log, only the scenarios' own alarms.
  - Preparation: one Reset per machine cleared `Alm_CommLost` (simulator log 28.3 / 31.4 / 34.5 s); three labels 39 s after Start; one syrup batch 148 s.
  - Labeler screen: pocket codes 0, 1, 2, 3 within 15 s; Trends (labeler) 3 charts.
  - **`packer_stopped`**: labeler Blocked 49 s after Run, **the filler Blocked 43 s later** (the blockage travels upstream), the labeler's warning at 2 min Blocked, both running again 7 s after it; the filler's own warning did not come.
  - `buffer_starved`: filler Starved 100 s after Run, no alarm, product back 30 s later. `level_sensor_stuck`: FAULT 66 s after Run (it waited for DOSE_WATER), Ack + Reset + Start. `web_torn`: FAULT 7 s after Run, next label 3 s after Reset + Start. `index_jam`: FAULT 5.5 s. `e_stop`: all three Stopped, three red E-STOP segments, running after three Starts.
  - Two checks of the rehearsal script were wrong, not the line: P1 read FUXA's alarm list right after the third Reset (before its next poll; the simulator log shows all three cleared), and the end condition waited for the syrup room to go below DOSE_WATER while batches ran back to back (TRANSFER → DOSE_WATER with a low buffer). The syrup room was then stopped by hand **in TRANSFER** (Stop at the end of the step → IDLE with the batch complete, `BatchCount` 6), so no `Batch_Dosed` was left for the next simulator.
- **ISA-101 review** with live data (screenshots of Line during `packer_stopped`, Labeler, Trends): grey page; strong colour only for the state blocks / chips, the red 200 °C tick and the alarm chips; the glue fill in the product colour as the syrup tanks; a unit next to every value; nothing cut off or overlapping. No change.
- **Report 8.1** (labeler built L0–L7, its trends, screen list with the labeler charts, `demo_check.py` for three PLCs; the old "still open: live run of the syrup room" removed – done 2026-11-14), `CLAUDE.md`, `docs/labeler/progress.md`.

## Packer screens (fourth machine)

Fourth PLC on the same screens (planned 2026-11-23, packer part C). Same working rule: plan → OK → build (generated by `fuxa_tags.py` / `fuxa_views.py`), test per stage, export, tick here and in `docs/packer/progress.md` section C.

**Decisions (user, 2026-11-23):**
- **Header row 1 for four machines:** four chips at **176 px** (16–744), E-stop **4 segments × 52 px** (752–972), nav 3 × 92 px (980–1264). Same height (96 px), so no view body moves. Chip text: name + state on line 1, step + mode on line 2, 12–13 px; the packer chip shows the collation `Step` (its `WrapStep` lives on the Packer screen and the Line block). Link cover narrows with the chip (`LINK_W` ≈ 80 px). Rejected: a taller header (every body moves), chips without the mode word.
- **Header row 2 shrinks for 10 alarm chips:** picker 4 × 60 px (16–268, short labels Filler / Syrup / Labeler / Packer), Start / Stop / Reset 3 × 56 px (276–452), mode 3 × 56 px (460–636), alarm chips **58 px** from 644 (the packer's 10 end at 1264; 10 px labels, e.g. "Film fault", "Pack jam", "Airflow", "Wrap timeout"). The same layout for every machine. Rejected: one shared "Warnings" chip, two thin chip rows.
- **Line view:** the **packer block** (State colour, `Step` + `WrapStep` names, oven °C, packs in oven, click → Packer screen) replaces the grey packer stand-in **under the end of C3**; a small grey **palletizer stand-in** beside it. C3 rollers turn on the packer's `ConveyorMotor3` (until now static). Rejected: a second row running right to left (a bend in the line).
- **Animation words** (the full set, written by the simulator into the packer PLC, ignored by the PLC, no ladder change, as the labeler's %MW70–71):

  | Word | Address | Content |
  | --- | --- | --- |
  | `Anim_Wrap` | %MW70 | wrap conveyor cells, 11 × 100 mm from the push line (0) to the oven entry (1100 mm): bit i = a pack covers cell i |
  | `Anim_Oven` | %MW71 | oven cells, 10 × 100 mm from the entry eye to the exit eye, + bit 10 = a pack leaving over the exit |
  | `Anim_Table` | %MW72 | packs on the outfeed table, 0–6 (`TABLE_PACKS`) |
  | `Anim_Film` | %MW73 | sheet code: 0 none, 1 feeding, 2 sheet in the slot (ready), 3 under a pack (wrapping) |

  Bottles on the collation table come from the PLC's `GroupCount` (6 places lit by value ranges), no word needed.
- **Film low:** FUXA alarm (low) straight on the sensor bit `Film_Low` (packer decision 2026-11-21), as the labeler's `Roll_Low`.
- **Counter reset:** "Reset counter" on the Packer screen writes `Cmd` = 4 (one write per click, I13).

| # | Stage | Content | Test |
| --- | --- | --- | --- |
| P0 | Animation words (simulator) | `address_map_packer.py`: `ANIM_WRAP` … `ANIM_FILM` (%MW70–73) in `SIM_WRITE_REGISTERS`, codes `SHEET_*`; `Wrapper` / `Oven` / `OutfeedTable` / `Film` give their cells / count / code; `PackerWrites.anim`; `PackerPlc.write()` sends them as an extra request (not read back, as the labeler); `docs/packer/io-list.md` "Animation data", design notes | unit: encoding + address-map guard (not in `main.ld`); live `test_link_packer.py` reads them back; `test_closed_loop_packer.py`: wrap / oven bits move, table count, sheet codes 0 → 1 → 2 → 3 |
| P1 | Device + tags | `fuxa_tags.PACKER_TAGS` (~73 tags from `address_map_packer.py`), `Machine("packer", "Packer PLC", "plc-packer:502", …)`, `PACKER_SCALES` (`OvenTemp`, `OvenSetpoint`, `OvenHyst`, `OvenBand` ÷ 10 → °C, rounded on write), test page `Packer tags` | H1 per device; live H2 + H4 packer, H3 packer (`OvenHyst` 4.5 °C → 45) |
| P2 | Header for four machines | Row 1 and row 2 as decided above for **every** view; `PACKER_UI` (steps, 10 alarm bits incl. %MX7), views `Line / Alarms / Trends (packer)`; `demo_check.py` D4 note stays until P7 | H5 for 4 machines + layout check (1280 × 96, no overlap, chip text inside the chip) + mutations; browser: Auto / Reset / Start of the packer from its header, labeler chip still readable, PLC LOST on one chip |
| P3 | Packer screen | Process left to right: C3 end + gate (open while `GateMotor`), collation table (6 places from `GroupCount`), pusher, wrap conveyor (11 cells from `Anim_Wrap`, rollers on `WrapConveyor`), film roll + feed (turns while `FilmFeed`) + slot with the sheet (`Anim_Film`) + knife, wrap bar (turns while `WrapBar`), oven (10 cells from `Anim_Oven`, heater / blower / airflow lamps, oven conveyor rollers), outfeed table (`Anim_Table`) + palletizer stand-in; a lamp per sensor, output and conditioned bit. Oven panel: `OvenTemp` bar 0–260 °C with a red tick at `OvenMax` 230 °C and the window band, setpoint bar. Values: both step names, `GroupCount`, `FeedCount` / `FeedPulses`, `PacksInOven`, `PackCount` + Reset counter, `Starved`, `Jog_Block`. Setpoints with limits checked against the PLC constants (`OvenSetpoint`, `OvenHyst`, `OvenBand`, `FilmLength` 600–1200 mm; band > hyst, window edge < `OvenMax`, warm-up < `HeatTimeout`). Manual: 8 hold-to-run buttons (Manual only), jog-watchdog line, interlocks in words, "oven only in Auto, blower run-on 30 s" | new H13 + mutations (limit rules by name); live H3 packer; browser: `film_torn`, Reset counter, a Manual interlock |
| P4 | Line overview | Packer block under the end of C3 (decision above), palletizer stand-in, C3 rollers on `ConveyorMotor3`, `PackCount` next to the other counters, legend unchanged | H7 updated + mutations (block on the packer's State, opens Packer, C3 rollers on the packer motor, no overlap); browser: `palletizer_stopped` (table fills, packer Blocked, labeler Blocked after it) |
| P5 | Alarms | `PACKER_ALARMS`: 7 FAULT bits highhigh (`FilmFault`, `PackJam`, `OvenOverTemp`, `OvenHeatTimeout`, `OvenJam`, `AirflowLost`, `WrapTimeout`), `CommLost` high, `OutfeedBlocked` / `FilmEmpty` + sensor `Film_Low` low; names `packer_<bit>`, group "Packer" | H8 per machine (also the %MX7 bits); browser: `film_empty` |
| P6 | Trends | DAQ `OvenTemp`, `OvenSetpoint`, `PackCount`, `State`; charts oven temperature (°C, 15 min), packs (30 min), state (30 min); view `Trends (packer)` | H9 / H10 packer (history in °C); browser: one oven warm-up as a curve |
| P7 | Wrap-up | `demo_check.py` packer `fuxa = True`; demo script for four machines (`palletizer_stopped` replaces `packer_stopped`), report 8.1, `CLAUDE.md`, progress; ISA-101 review | `demo_check.py` 0 FAIL with 4 PLCs; one rehearsal |

Order: P0 first (screen and Line need the words), P2 before P3 / P4 (every view draws the header). P2 touches every existing view (header re-layout): the filler, syrup room and labeler bodies must stay byte-identical (checked without the header). Risks: I13, I16, overlap after the re-layout (H5), text that no longer fits a 176 px chip or a 58 px alarm chip (checked in the browser screenshot), Modbus server OFF after a container re-creation or SAFE MODE after a suspend (D2 / D3 → upload).

**P0 as built (2026-11-23):**
- `address_map_packer.py`: `ANIM_WRAP` … `ANIM_FILM` (HR 1094–1097), `ANIM_START` / `ANIM_COUNT`, cell layout `ANIM_CELL_MM` 100, `WRAP_CELLS` 11, `OVEN_CELLS` 11, codes `SHEET_NONE` … `SHEET_UNDER_PACK` (0–3); all four words in `SIM_WRITE_REGISTERS`. `Packer.anim_words()`: a pack lights every 100 mm cell it covers; a pack just handed to the oven still lights the last wrap cells (its body is behind the entry eye), so it never vanishes at the hand-over. Sheet code priority feeding > in the slot > under a pack; "feeding" = film really moved (a torn film does not show as feeding). `PackerWrites.anim`, `PackerPlc.write()` sends them as a fourth request, not read back (as the labeler). `docs/packer/io-list.md` "Animation data", design notes.
- Unit +4, suite **624 passed**: cell bits at fixed pack positions (push line, slot, entry, hand-over, half inside, exit, waiting at the exit), table count and every sheet code, the encoding (2¹⁵ refused), the link writes HR 1094–1097 and leaves %MW74 alone; the plant over 2 packs: the words = `anim_words()` every tick, **no tick where a moving pack lights no cell**, every wrap and oven cell seen, codes 0–3, 2 packs on the table at the end. The address-map test keeps the words in their own `ANIMATION` dict (checked against io-list, NOT allowed in `main.ld`).
- **Live (2026-11-23):** `test_link_packer.py` **11/11** (new P6b: [1539, 1025, 5, 3] read back as written, %MW40–53 unchanged); `test_closed_loop_packer.py` **7/7** (new C7, read by a second client at 200 ms while the loop runs: all 11 wrap cells and all 11 oven cells, sheet codes 0, 1, 2, 3; table count only 0 – the palletizer takes a pack in the tick it lands, so the table shows packs only when the palletizer is stopped).

**P1 as built (2026-11-23):**
- `fuxa_tags.PACKER_TAGS`: **71 tags** from `address_map_packer.py` (status 4 + computed 4 – listed as "status", so every column of the test page stays within 20 rows –, alarms 10, outputs 10, conditioned 8, sensors 14 + `OvenTemp`, wiring 2, `Cmd` / `ModeReq` + 4 setpoints, manual 8, animation 4). `Machine("packer", "Packer PLC", "plc-packer:502", …)`, `PACKER_SCALES` (`OvenTemp`, `OvenSetpoint`, `OvenHyst`, `OvenBand` ÷ 10, rounded on write). No DAQ yet (P6). `MACHINES` = four machines; the header (`UIS`), alarms and charts still draw three until P2 / P5 / P6.
- Test page **`Packer tags`** (`v_packer_tags`, side menu only), with the filler's header row 2 until P2 (as `Labeler tags` in L1).
- **Export:** the new export minus the packer device, its status tag, its test page and its menu entry is **identical** to the committed one; import round trip identical.
- **Tests:** `test_hmi_tags.py` H1 also for the packer (`expected_packer_addresses()`: every address constant except the block anchors / counts, the cell layout, `UNUSED_MX1_0` and the sheet codes), new `packer_live()` = H4 + H2 + **H3 packer** (`OvenHyst` 4.5 °C through FUXA → PLC 45, restored). **Live 26/26**: packer connected, **71/71** values equal. Three mutations of a copy of the export (`OvenTemp` one address off, `OvenSetpoint` without its scale, `Anim_Film` missing) each fail H1. Unit suite 624.

**P2 as built (2026-11-23):**
- **Row 1:** four chips at 176 px (16–744), E-stop 4 × 52 px (752–972), nav 3 × 92 px (980–1264). Text sizes from widths **measured in Firefox** (sans-serif): name 14 bold (≤ 79 px) + state 13 bold (≤ 53 px), step 11 (≤ 85 px) + mode 11 bold – all inside the 156 px of a chip. Two changes to make it fit: the chip says **"Manual"** instead of "Manual (paused)" (87 px, would collide with WAIT_OUTFEED); "Stop – press Auto" (94 px, I10) stays, it only comes with IDLE / FAULT (Mode Stop sends every sequencer to IDLE). E-stop texts 10 px ("E-stop OK" 50 px at 11 px in a 52 px segment).
- **Link cover = the whole chip** (was the right 112 px): at 176 px a partial cover would leave the step stale. The machine's **name is drawn after the cover** (`<chip>_title`), so a lost chip reads "Packer … PLC LOST"; the cover stays click-through.
- **Row 2:** picker 4 × 60 px (16–268), Start / Stop / Reset 276–452, mode 460–636, alarm chips 58 px from 644 (the packer's 10 end at 1260). Labels on **two lines** (`alarm_label_lines()`: at the first space, or after a hyphen: "Over-" / "pressure"); 8 packer labels in `ALARM_LABELS`.
- **`PACKER_UI`** (the chip shows the collation `Step`; it opens `Packer tags` until P3), views **`Line / Alarms / Trends (packer)`**; Trends with a note "No packer charts yet (P6)" (`MACHINE_CHARTS["packer"] = ()`); `alarm_definitions()` skips the packer until P5 (`.get(…, {})`, as in L2); `Packer tags` now has the packer's row 2.
- **Bodies unchanged:** every one of the 15 existing views generated with the header switched off is **byte-identical** to the committed version (old `fuxa_views.py` from git vs new).
- **Tests:** H5 for four machines (loops over `UIS`) + two new rules: the link cover box = the chip box, and the name drawn after the cover; every alarm chip has a label of 1–2 lines. H7: the stand-in check now takes only a "Packer" text below the header (the header chip has the same word). H8 prints "Packer alarm definitions pending (HMI P5)" instead of failing. Offline 11/11; six H5 mutations each fail (a cover over the right part only, the name under the cover, the packer's Reset writing the filler's `Cmd`, the Alarms picker opening a Line view, the packer E-stop segment missing, an alarm chip outside the header). Import round trip identical; live 26/26; unit 624.
- **Browser** (Playwright 1.64.0, headless simulator with all four PLCs; `simulator/logs/p2_browser.py`, `p2_lost.py`): 12/12. Every chip's visible texts inside its chip without overlap (Line, and Line (packer) with the packer running); the 10 packer alarm labels inside their chips; picker → `Line (packer)`; **Auto → Reset → Start from the packer's header: WARM_UP** on its chip; Stop + mode Stop; filler untouched; the chip opens `Packer tags`; no page errors. **Packer container stopped:** red "PLC LOST" on the packer chip only, name readable, its E-stop segment grey "no link"; started again, program and Modbus server kept (`demo_check.py` D2 / D3). Screenshots `simulator/logs/p2_*.png` (git-ignored).
- Found while checking: `elementFromPoint` never returns the cover (it is `pointer-events="none"` on purpose), so the lost check reads the cover's fill instead.

**P3 as built (2026-11-23):**
- **View `Packer`** (`v_packer_main`, `fuxa_views.packer_view()`); the packer chip and the side menu open it (the chip opened `Packer tags` until now). Five areas:
  - **Process** (left to right, one cell = 100 mm of the simulator): the last 3 C3 slots from the **labeler's** `Anim_C3` (capped and labelled, rollers on the packer's `ConveyorMotor3`), the gate (white while held open), the collation table (2 × 3 places, place *i* shows a bottle from `GroupCount` *i* + 1 on – the PLC's own counter, no animation word), the group pusher (white while its valve is ON), the wrap conveyor (11 cells on `Anim_Wrap`, rollers on `WrapConveyor`), the film slot with the sheet standing up out of it (`Anim_Film` 1 feeding / 2 ready) or lying on the belt under the pack (3), a word under the slot per code, the knife above it, the film roll under the belt (turns with `FilmFeed`), the wrap bar (a two-bladed bar that turns with `WrapBar`), the oven (housing over 10 cells on `Anim_Oven` + the exit cell, heater strip orange while `OvenHeater`, blower turning with `OvenBlower`, rollers on `OvenConveyor`), the outfeed table (6 places from `Anim_Table`) and a grey palletizer stand-in. Under it **8 lamp columns**, one per part: every output, sensor and conditioned bit (`PACKER_LAMP_COLUMNS`, + `Starved` / `Jog_Block` in the values).
  - **Oven temperature:** `OvenTemp` as a bar 0–260 °C with a red tick at `OvenMax` 230 °C, the setpoint as a thin grey bar, the value in °C, the oven rules in words (Auto only, only with air flow, blower run-on 30 s).
  - **Packing:** both step names (collation, wrap), bottles in the group (/ 6), film pulses fed / per sheet, packs in the oven, finished packs with **"Reset counter"** (one write: `Cmd` = 4), lamps `Starved` / `Jog_Block`.
  - **Setpoints** with limits, checked against the PLC constants and the simulator (H13, each rule by name): `OvenSetpoint` 185–210 °C, `OvenHyst` 2–8 °C, `OvenBand` 10–15 °C, `FilmLength` 600–1200 mm (the PLC's `LIMIT`, io-list). Lowest ready oven 185 − 15 = 170 °C = `SHRINK_C` (a pack always shrinks); setpoint ≤ 210 < `OvenMax` 230; band ≥ 10 > heater band ≤ 8 (`Oven_Ready` holds while the heater cycles); slowest warm-up to 200 °C ≈ 74 s < `HeatTimeout` 120 s (computed from the simulator's oven model); 1200 mm at 150 mm/s = 8 s < `FeedTimeout` 10 s. Ranges shown as whole numbers (the panel is narrow). Defaults 190 / 5 / 15 °C / 850 mm inside.
  - **Manual:** 8 hold-to-run buttons (4 per row, only in the packer's Manual mode), the jog-watchdog line, the interlocks of `state-diagram.md` rule 12 in words, "heater + blower: Auto only, no button".
- **Layout found in the first screenshot and fixed:** the setpoint notes ran into the last input row, "185.0–210.0" ran out of the panel, "Reset counter" was wider than its button.
- **Tests:** new **H13** (`test_hmi_tags.py`): hold buttons on the packer tags and its Manual mode, one per `Man_*`; inputs with limits and `step="any"` for °C; the five limit rules (shrink, OvenMax, band, warm-up, film); lamps = exactly the outputs + conditioned + sensor tags; both bars with the 260 °C scale and the OvenMax tick; values; Reset counter = exactly one write of packer `Cmd` 4; wrap / oven cells on their bits; collation and outfeed places on their counts; the C3 end on the labeler's bits; the sheet shapes per `Anim_Film` code and a word per code; everything that turns on its motor; step texts of both words; the `Jog_Block` text. Offline 12/12; **13 mutations** each fail H13 (a hold button on a labeler tag, Reset counter writing 3 or twice, a wrap cell on the wrong bit, the wrap bar on `WrapConveyor`, a film word missing, the sheet still up while under the pack, the airflow lamp missing, a collation place one bottle early, and four limit changes – setpoint down to 180, up to 230, window from 8, film up to 1500 mm – each named by its rule). Import round trip identical; live **27/27**; unit 624.
- **Browser** (Playwright 1.64.0; headless simulator with all four PLCs and **`film_torn`**; filler + labeler started with `hmi.py`, the packer **only from FUXA**, syrup room left in Stop – a fresh simulator's buffer is enough for ~4 packs, and no batch means no `Batch_Dosed` trap; `simulator/logs/p3_browser.py`): **14/14**. The chip opens the screen; manual buttons hidden outside Manual; Auto / Reset / Start from the screen's header → WARM_UP → COUNT at 175 °C (screen 174.7, PLC 175.1); collation places lit = `GroupCount`; the torn film → `Alm_FilmFault`, both step texts FAULT, the "Film fault" chip red; Reset + Start from the screen → FEED, film fed; animation seen on the screen (computed fill): the sheet up out of the slot, the sheet under the pack, pack cells on the wrap belt and in the oven; Reset counter → `PackCount` 0 (4 → 0 in the first run); `OvenHyst` 6 °C → PLC 60, 9 refused, back to 5; **Manual interlock:** wrap bar held until it left home, then Film feed held → `Man_FilmFeed` 1, `FilmFeed` 0 (`Wrap_Home` interlock), bar turned home again; mode Stop; no page errors. Screenshots `simulator/logs/p3_*.png` (git-ignored).
- **Found while testing (setup, not the screen):** the first run reached `film_torn` during two aborted script starts (my Reset cleared the fault), then the line ran dry after 25 bottles (syrup room in Stop, the buffer empty), so S6–S8 waited in vain. Rerun with a fresh simulator: 14/14. Also: FUXA's Value shapes are SVG text, read with `text_content()` (`inner_text()` fails on SVG).

**P4 as built (2026-11-23):**
- **Line view** (all four Line views, same body): the grey packer stand-in is gone. **Packer block** under the end of C3 (`PACKER_X/Y/W/H` 1084, 446, 180 × 180, below the C3 lamps), coloured by the packer's `State`, click → `Packer`; on top, click-through: the collation step, the wrap step, the oven temperature (°C) and the packs in the oven. A grey **chute** (`c3_chute`) from C3's end down to the block, drawn before C3 so its last roller stays on top (found in the first screenshot: the chute covered it). The **palletizer stand-in** (grey) left of the block. **C3 rollers on the packer's `ConveyorMotor3`** (static until now: the stand-in had no PLC output). New counter **"Packs"** (`PackCount`) beside "Good bottles" and "Labelled". The other parts of the row are unchanged.
- **Tests:** H7 – C3 rollers on the packer motor; new `h7_packer()`: block on the packer `State` (a range per state colour), opens `Packer`, step / wrap step / oven / packs-in-oven texts click-through, pack counter on `PackCount`, block under the end of C3 below its lamps and inside the view, chute from C3's end down to the block's top, palletizer left of the block, the old "Packer simulated" stand-in gone (replaces the stand-in check of L4 / P2). Offline 12/12; **10 mutations** each fail H7 (block on the labeler's `State`, opening `Packer tags`, a C3 roller static or on `ConveyorMotor2`, a wrap step text catching the click, the oven value on `GlueTemp`, the block moved up over C3, no chute, no palletizer, the pack counter on the labeler's `GoodCount`). Import round trip identical; live 27/27; unit 624.
- **Browser** (Playwright 1.64.0; headless simulator with all four PLCs and **`palletizer_stopped`**; filler, syrup room and labeler started with `hmi.py`, the packer from FUXA's `Line (packer)` header; `simulator/logs/p4_browser.py`): **8/8**. Packer block green while running with its step texts; **C3 rollers turn** while the packer's `ConveyorMotor3` is ON (FUXA's rotate transform changes); palletizer stopped → **packer block orange "Blocked"** (table 3 packs, the next pack in READY); the blockage travels up: **labeler block orange** (C3 word 11111110); after the packer's 2 min warning the palletizer runs again and **both blocks leave Blocked**; the block opens the Packer screen; no page errors. The syrup room ran for product (a fresh simulator's 500 L buffer lasts only ~25 bottles) and was stopped between batches (WAIT_DEMAND, `BatchCount` 2 → IDLE, Mode Stop): no `Batch_Dosed` left. Screenshots `simulator/logs/p4_*.png`.

**P5 as built (2026-11-24):**
- **11 FUXA alarm definitions for the packer** (`fuxa_views.PACKER_ALARMS`), names `packer_<bit>`, group **"Packer"**, same priority scheme as the other machines; texts from `docs/packer/io-list.md`, numbers from the constants in `state-diagram.md`:

  | Tag | Priority | Text |
  | --- | --- | --- |
  | `Alm_FilmFault` | highhigh | Film fault: sheet not fed within 10 s in FEED (film torn) or no sheet in the slot for 1 s in READY (FAULT) (shortened in P7) |
  | `Alm_PackJam` | highhigh | Pack jam: pusher out / home, pack off the film stop or at the oven entry not reached within 3 s (FAULT) |
  | `Alm_WrapTimeout` (%MX7.1) | highhigh | Wrap timeout: wrap bar not back home within 5 s in WRAP (bar stuck, film caught) (FAULT) |
  | `Alm_OvenOverTemp` | highhigh | Oven over-temperature: 230 °C reached, heater off, blower runs on (FAULT in every mode; Reset below 230 °C) (shortened in P7) |
  | `Alm_OvenHeatTimeout` | highhigh | Oven heat-up timeout: not ready within 120 s of WARM_UP (heater failed, FAULT) |
  | `Alm_OvenJam` | highhigh | Oven jam: packs in the oven and none out for 20 s (FAULT; Reset zeroes the packs in the oven) |
  | `Alm_AirflowLost` (%MX7.0) | highhigh | Airflow lost: blower on and no airflow within 3 s (blower failed, duct blocked) (FAULT) |
  | `Alm_CommLost` | high | Plant heartbeat lost for 3 s (simulator link) |
  | `Alm_OutfeedBlocked` | low | Outfeed blocked longer than 2 min: outfeed table full (warning, no FAULT) |
  | `Alm_FilmEmpty` | low | Film roll empty: the next pack waits (warning, Starved; clears after the roll change) |
  | `Film_Low` (sensor) | low | Film roll low: prepare the next roll (warning) |

  - **`Film_Low` sits on the sensor bit** (`SENSOR_WARNINGS["packer"]`, decision 2026-11-21, as the labeler's `Roll_Low`): no PLC bit, nothing latched, no header chip (row 2 is full with the 10 PLC bits); it shows in the list and as a lamp on the Packer screen.
  - The packer entry is appended to `ALARM_TEXTS`, so the 19 older definitions keep their place: the export diff is exactly 11 new alarm definitions (30 in all: filler 4, syrup room 6, labeler 9, packer 11), no view changed. `alarm_definitions()` requires alarm texts for every machine again (the P2 fallback `.get(…, {})` is gone).
- **Tests:** H8 without the "pending (HMI P5)" branch: its rules now cover the packer (texts = the 10 alarm bits incl. the two %MX7 bits + `Film_Low`; the warning low on an existing tag; one level, ackactive, group "Packer"; the `Alarms (packer)` Reset writes the packer `Cmd` = 3). Offline 12/12, import round trip identical, unit 624. **Seven mutations** each fail H8 (a packer alarm on a labeler tag, `Film_Low` as highhigh, `Film_Low` missing, `Alm_AirflowLost` missing, `Alm_OvenOverTemp` as low, group "Labeler", the `Alarms (packer)` Reset on the labeler's `Cmd`).
- **Browser** (`simulator/logs/p5_browser.py`, two runs; filler + labeler started with `hmi.py`, syrup room in Stop, the packer only from FUXA's `Alarms (packer)` header): run **a** with `film_empty` – F1 `Film_Low` listed (Packer, low), F2 `Alm_FilmEmpty` listed, packer chip yellow (Starved), its header chip red, F3 the new roll continues without Reset, both warnings get an `offtime`, F4 Ack removes both rows; run **b** with `sheet_dropped` – F5 `Alm_FilmFault` listed as highhigh, Machine "Packer", F6 **Reset from the `Alarms (packer)` view** clears it and the packer leaves FAULT, the filler and labeler untouched; F7 no page errors. Run b was added in the plan review: `film_empty` alone shows only low alarms, and the Alarms view's Reset was checked only statically until now.
- **Live + browser (2026-11-24, after one upload per PLC):** live `test_hmi_tags.py` **28/28**. Run **a** (`film_empty`) **5/5**: `Film_Low` listed at once (Packer, low), `Alm_FilmEmpty` listed with the packer chip yellow and its header chip red, the new roll continues without Reset (`PackCount` 4 → 5), both warnings get an `offtime`, Ack removes both rows. The first attempt failed F3 on the check itself (it allowed only Running / Blocked after the roll change; the packer was rightly Starved, no bottle at the gate): fixed to "not FAULT". Run **b** (`sheet_dropped`): F5 `Alm_FilmFault` listed as highhigh, Machine Packer, chip red; **F6 Reset from `Alarms (packer)` clears it**, the packer leaves FAULT; F6b failed and found **issue I18** (the labeler jammed on the stopped C3 because this script pressed Start ~20 s after the FAULT; 13 filler bottles fell off C2 while the labeler stood in FAULT). No page errors.

**P6 as built (2026-11-24):**
- **DAQ on 4 packer tags** (`fuxa_tags.PACKER_TREND_TAGS`: `OvenTemp`, `OvenSetpoint`, `PackCount`, `State`; every change + once a minute, as the other machines). FUXA scales before it stores, so the oven history is in °C.
- **3 charts** (`fuxa_views.PACKER_CHARTS`): "Oven temperature (°C)" (oven + setpoint, 15 min, 1 decimal), "Packs" (30 min), "Packer state" (30 min); stepAfter lines like every chart. View **`Trends (packer)`** (`packer_trends()`, parallel to `labeler_trends()`, so the labeler's view stays byte-identical) in the filler's layout: oven across the top, packs left and state right below; a "Normal" line (warm-up to 175 °C in about a minute – the simulator's oven model: 20 → 175 °C in 55 s –, then the heater's saw tooth just under the setpoint, a cycle about every 13 s), the state key, the windows, "heats only in Auto; 230 °C = over-temperature". The placeholder "No packer charts yet" is gone.
- **Not added (plan review):** a 230 °C limit line (FUXA draws chart lines only from tags; `OvenMax` is a PLC constant without a register, so it is in the text); `PacksInOven` / `WrapStep` lines (not in the P6 row; the Packer screen shows them live).
- **Export:** only the 4 packer DAQ flags, the `charts` list (10 → 13, the old ones unchanged) and `v_packer_trends` changed (key-by-key diff against the export before); import round trip identical.
- **Tests:** H9 needed no code change (it loops over every machine and `MACHINE_CHARTS`). New **H10 packer** (`h10_daq_packer()`, in `packer_live()`): points of every packer trend tag in the last 10 min, and the stored values in °C (`OvenSetpoint` ≤ 230, `OvenTemp` ≤ 300; raw INTs would be 1850–2100 / up to 2530). Offline 12/12; unit 624. **Seven H9 mutations** each fail (oven line on the labeler device, DAQ off on `OvenTemp`, DAQ on `OvenBand` too, setpoint line linear, oven chart without history loading, a labeler chart on the packer view, the packs line on the labeler's `GoodCount`); **two H10 mutations** with a stubbed history (×10 = unscaled, no `PackCount` history) each fail, a good history passes.
- **Browser** (`simulator/logs/p6_browser.py`, Playwright 1.64.0 via `uv run --with playwright==1.64.0`): layout part done – **T2** `Trends (packer)` opens from the header (picker Packer → Trends) with 3 drawn charts, **T3** help text and state key, help lines end before the state key, **T4** no page errors (3/3, screenshot `simulator/logs/p6_trends.png`). **T1** (2026-11-24): one oven warm-up in FUXA's history, 366 points 0.0 → 190.0 °C (0 = the freshly uploaded PLC, then 20 °C from the simulator, WARM_UP → COUNT after 55 s as the model says), states 0 → 1 → 2: **4/4**; live H10 packer passes (489 `OvenTemp` points in 10 min). Screenshot `simulator/logs/p6_trends.png`.

**P7 as built (2026-11-24):**
- **`demo_check.py`:** the packer runtime has its FUXA device (default `fuxa=True`), so D4 prints one line per PLC for all four; its operator hint points at the FUXA header (row 2: Packer). The `fuxa` flag stays for the blow moulder. Unit test: four runtimes, all with a FUXA device. Live (after one upload per PLC): **14 PASS, 5 WARN, 0 FAIL** (WARN = no simulator, machines in Stop). `simulator/README.md`: the packer can be started from FUXA's header.
- **Demo script for four machines** (`docs/hmi/demo-script.md`, ~25 min, 16 steps): preparation with `plcs/packer`, one Reset per machine ×4, panel tab 4 defaults (*Palletizer running* ON), **start order packer → labeler → syrup room → filler** (both warm-ups are over before bottles arrive), run until the first pack has left the oven; new step **Packer screen** (two sequencers, film slot, wrap bar, oven with TOF run-on, interlock order); Trends with the packer; **`palletizer_stopped` replaces `packer_stopped`** (the blockage travels up through all four machines; `packer_stopped` needs `--no-packer`); new step **`sheet_dropped`** (packer FAULT, Reset + Start at once – I18); `e_stop` with four segments and four Starts; packer spares, avoid rows (palletizer left stopped, oven only in Auto, slow Reset after a downstream FAULT = I18, short first pack after a simulator restart) and troubleshooting rows (C3 full with the packer grey, oven heat-up timeout).
- **Rehearsal** (one run of the whole script, FUXA as the only operator: Playwright clicks the header, Ack in the table; `simulator/logs/p7_driver.py` = the panel's loop with one scenario slot fed from a file, four PLCs; `p7_rehearsal.py`; Modbus only read to measure; screenshots `p7_*.png`, git-ignored): **15/16**, 0 page errors, **no bottle lost** in the simulator log, only the scenarios' own alarms, all four machines Stopped at the end with the syrup room in IDLE (stopped in TRANSFER, no `Batch_Dosed` left).
  - Preparation: one Reset per machine cleared `Alm_CommLost` on all four; packer oven ready 33 s after Start (it had kept some heat), labeler at the same time; first new pack 23 s later.
  - Labeler screen: pocket codes 0–3; **Packer screen**: sheet codes 0–3 and wrap steps 10 → 20 → 22 → 24 → 30 → 32 within 16 s; Trends (packer) 3 charts.
  - **`palletizer_stopped`**: packer Blocked 105 s after Run, **the labeler 78 s later, the filler 42 s later** (the blockage travels upstream through four machines), the packer's warning 2 min after its Blocked, all running again 17 s after it.
  - `buffer_starved`: filler Starved 134 s after Run, no alarm, back 34 s later. `level_sensor_stuck`: FAULT 67 s after Run, Ack + Reset + Start. `web_torn`: FAULT 7 s, next label 3 s after Start. **`sheet_dropped`**: FAULT 21 s after Run, Reset + Start right after the scenario said so → RUN_ON at once, the next pack 11 s later, **no upstream jam**. `index_jam`: FAULT 9.5 s. `e_stop`: all four Stopped, four red E-STOP segments, all running after four Starts.
  - The one FAIL is in the check, not the line (the L7 finding again): P4 waited for the syrup room to go below DOSE_WATER after TRANSFER, but with a low buffer the batches ran back to back (TRANSFER → DOSE_WATER, driver log 208.3 s).
  - **First attempt aborted:** the filler never left Stop. FUXA's write of its *Auto* timed out (`TransactionTimedOutError`, I16) and FUXA then dropped the filler link for a few seconds, so *Reset* and *Start* right after it were lost too; reproduced by hand (two clicks on the filler's Alarms view: the second lost, same log line). The rehearsal now does what the demo script tells the operator (check the header, press again): every button is checked over Modbus and pressed again after 4 s (at most 3 tries). In the full run no press had to be repeated.
- **ISA-101 review** (screenshots of Line during `palletizer_stopped`, Packer, Alarms with the packer FAULT, Trends packer): grey page, strong colour only for the state blocks / chips, the red 230 °C tick and the alarm chips; units next to every value; nothing cut off. **One defect fixed:** in the Alarms list the long texts (9 of 30, 7 of them the packer's from P5) wrapped onto a second line that ran over the next row. The *Alarm* column is now 680 px (Time 160, Machine 110, Priority 90, Status 100: 1220 of 1248 px), the two longest packer texts are shorter (123 → 106 and 113 → 107 characters), and H8 checks `ALARM_TEXT_MAX` = 110 (about 113 fit, measured 5.8 px per character); one mutation (a 130-character text) fails H8. Export diff: the four Alarms views + the two texts; round trip identical; offline 12/12; rows 30 px in the browser.
- **Report 8.1** (the packer paragraph, the screen list, `demo_check.py` for four PLCs), `CLAUDE.md`, `docs/packer/progress.md`.

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
| I11 | Later machines: one Modbus device per PLC (ports 5021–5025). | Screens should be copyable for the next machine. | Same tag names under each device, header from the standard layout (report 6.2), stage 4 | ready: `fuxa_tags.Machine` (key, name, PLC address, tag table → device `d_<key>`, tags `t_<key>_*`, status tag), `MACHINES`; views by `conveyor()` / `machine_block()`. Refactor checked byte-identical. A second machine = address map + tag table + `Machine(…)` + view calls | in use: the syrup room is the second `Machine` (S0 + S1, 2026-11-13); the views take a machine parameter |
| I12 | FUXA's Modbus driver is an optional plugin (`modbus-serial`), installed into `server/_pkg` inside the container. A new container (here: after `docker compose up` in stage 0) has no plugin. FUXA then logs "plugin is missing", and the device never connects. The same thing happens to the OpenPLC runtime: the compiled program is in the container, not in its volume. | Every tag stays empty; it looks like an address problem. | `_pkg` is now on the named volume `fuxa-plugins`; plugin installed once in the editor (Settings → Plugins). Since I14 the OpenPLC program also survives `down/up` (volume `openplc-build`) | closed: plugin installed, on the volume |
| I13 | FUXA 1.3.5 sends all events of one click at the same time. Several writes per click (7 tried) time out and drop the PLC link for seconds. | Every write in that time is lost: a button seems to do nothing. | Rule: one write per event; H5 fails if a shape has two events of the same kind (stage 3) | closed: rule + check |
| I14 | The compiled PLC program (`/workdir/build/libplc_*.so`) lived only in the OpenPLC container: re-creating it (`down`/`up`, a service change, a newer `:latest`) left the runtime without a program – every HMI value empty. (`stop`/`start` were always fine: the runtime starts an existing program at boot, `plc_boot_autostart`.) | The demo shows nothing. | Named volume `openplc-build:/workdir/build` (filled from the image on creation) + runtime image pinned by digest (v4.3.1); `tests/demo_check.py` D2/D3 detect a missing or stopped program | closed (needs one upload after the change) |
| I15 | Two plant writers at once (two simulators, or a test script next to the simulator) write different sensor values into the same addresses: bottles were lost in a test. | Wrong behaviour that looks like a PLC fault. | `simulator/src/simulator/plant_lock.py`: OS file lock per PLC host:port, taken by `loop.run`, the CLI and every sensor-writing test script; the second one stops with a message naming the first. Released by the OS when a process ends (no stale lock) | closed |
| I16 | About 1–2 % of FUXA's Modbus writes time out (`TransactionTimedOutError` in the FUXA log, `POST /api/setTagValue` → 400 after 2 s). Measured 2026-11-04 with 1 write/s, simulator not running: 2/140 without DAQ (stage 5 project), 3/140 with DAQ, so it is older than the trends and not caused by them. | An operator click (Start, a setpoint, a mode) can be lost now and then; a test that writes can fail. | Test: H3 tries up to 3 times. Operator: the header shows the result (state, mode, value), press again if nothing changed. Possible fixes, to check in stage 7: device `timeout` / `delay` (now 2000 / 10 ms), polling 200 ms → 250 ms | open; 2026-11-24 (P7): seen twice on the filler within ~20 writes while the simulator drove four PLCs (each time the next two clicks were lost while FUXA reconnected); the P7 rehearsal presses again after a check, as the demo script says |
| I17 | A PLC whose link is lost while FUXA was connected to it showed **"E-STOP PRESSED"** on the E-stop chip. FUXA sends `null` for that device's tags (checked: `/api/getTagValue` up → 0/1, down → `null`), and its show / hide and colour ranges treat `null` as 0. The S2 note ("a PLC with a lost link leaves the decision to the others") had been checked with a filler that had never connected. Not new in L2: the filler and the syrup room had it too | The operator saw a wrong cause (an E-stop instead of a lost link); likely in a demo after a laptop suspend (SAFE MODE, link lost) | Found in the L2 browser check (labeler container stopped, 2026-11-19). Decision (user, option B): one E-stop segment per PLC with a link cover; see "I17 as built" | closed: browser 4/4 (lost labeler → grey "no link", filler E-stop → only its segment red) |
| I18 | **A downstream FAULT jams the machine upstream of it.** A FAULT stops that machine's conveyor motor at once (the packer's `ConveyorMotor3`, the labeler's `ConveyorMotor2`). The full sensors watch the **second** slot of their conveyor (`config.S7_SLOT` / `C3_FULL_SLOT` = 1, chosen for the 2 s `Outfeed_Full` filter), so the bottle just put on the stopped belt stays in slot 0, slot 1 never fills and `Outfeed_Full` never comes. The upstream machine discharges again onto the standing bottle: the labeler ends in `Alm_PocketJam` (DISCHARGE 3 s), the filler pushes bottles off C2 (simulator: "crash: C2 entry occupied, fell off"). Not new in P7: the filler + `web_torn` had it since L7 (the L7 rehearsal passed because Reset + Start came within one cycle). The blow moulder has it too (simulator stage 3, 2026-11-26, `test_plant_blowmoulder.py`): a filler FAULT stops C1, its dead plate takes one more bottle, the next ejection stops halfway → `Alm_HandlingJam` 3 s later, i.e. within two to three bottle cycles (5.8 s each) of the filler FAULT | A packer or labeler FAULT in the demo cascades if the operator is slow: P5 browser run b (`sheet_dropped`, Reset ~9 s after the FAULT, Start ~10 s later): labeler `Alm_PocketJam` 8.8 s after the packer FAULT, then **13 filler bottles lost** while the labeler stood in FAULT | Found in the P5 browser check, 2026-11-24 (simulator logs of runs a / b). Demo rule until fixed: after a downstream FAULT press Reset + Start at once (the operator has about one cycle: labeler ~5 s, filler ~7 s). Fix options (your decision, a design change outside the HMI): (a) simulator – the full sensor on the entry slot, or a bottle that stands > 2 s in slot 0 also counts as "full" (no ladder change; check the 2 s filter reason of S7_SLOT again); (b) ladder – the upstream machine discharges only when its own "room on the conveyor" check is fresh (e.g. a discharge-point sensor), as real lines do | open |

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
