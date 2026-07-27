# Adicot Load Calc — Project Handoff

_Last updated at the end of the build session below. Read this top-to-bottom to continue in a new chat._

## 1. What this is

A tool that turns a **CAD-exported PDF of architectural plans** into an **ASHRAE RTS
cooling-load calculation**, replacing Design Master. Commercial-first, Florida primary.

**End-to-end data flow:**

```
plans.pdf ─┐
           ├─▶ parser ─▶ rooms ─▶ REVIEW UI (engineer edits) ─▶ rooms_edited.json ─┐
DM Setup ──┘   (geometry)        (assign types, loads, walls,                       ├─▶ RTS ENGINE ─▶ results.html
loadcalc_input.json              windows, ceiling h, orientation)                   │   (per-room + building     (+ supporting
(room/wall/roof/window types,        ▲ auto-loads the types JSON                    │    loads, peak)              evidence)
 weather, from Adicot site)          └──────────────────────────────────────────────┘
```

One source drives both Design Master and our calc: the **DM Setup tab** selection is
rendered as a `.vbs` (for DM) **and** `loadcalc_input.json` (for us).

## 2. State at a glance

| Piece | Status |
|---|---|
| Parser (PDF → rooms) | ✅ works on 2 real FL drawings; Sample A 22 rooms vs 19 target (minor over-split) |
| Review UI (`review.html`) | ✅ full editor: rooms, nodes, types, loads, windows/doors, ceiling, floor, orientation |
| DM generator → `loadcalc_input.json` | ✅ built in `../adicot-app` (needs deploy) |
| RTS engine physics (Stages 1–6) | ✅ 7 tests, all match ASHRAE workbook to ~hundredths |
| Engine end-to-end (a full wall) | ✅ raw inputs → cooling load = 304.6 vs 304.7 |
| Bundle CLI (`loadcalc.py`) | ✅ parse → review → calc → results, runs end-to-end |
| **Golden reconstruction (single room 4,121.6 Btu/h)** | ✅ done — `tests/test_golden_room.py`, within 0.01% |
| **Golden reconstruction (building blocks 35/30/65.9 ton)** | ✅ done — `tests/test_golden_building.py`, within 0.02% |
| Whole-building accuracy (arbitrary/real projects) | ✅ CTS mass, all 24 RTS zones, τ per station, and per-room cooling setpoint all now selected/used per-project (item #2) — remaining accuracy ceiling is data verification (item #3, provisional weather values) not coefficient selection |
| Weather dataset | ⚠️ 10 FL stations, PROVISIONAL values |

## 3. What's built

### Parser — `parser/`
- `pdf_extractor.py` — pulls line segments + Bézier curves from the PDF (pymupdf).
- `wall_detector.py` — wall candidates = dark, near-grayscale, length ≥ min, **orthogonal** (user-added filter). NOT the doc's line-width heuristic.
- `room_builder.py` — `unary_union` + `polygonize` (NOT the doc's half-edge traversal — that found 0 rooms on real data). `close_wall_gaps` closes door/window gaps between collinear wall pieces. `collapse_double_lines` and the arc-based door code (`detect_door_arcs`) exist but are **unused** (see gotchas).
- `models.py` — dataclasses.
- Dev/triage scripts: `triage_pdf.py`, `probe2.py`, `probe_arcs.py`, `diag.py`, `debug_render.py`, `run_page6.py`.
- Samples in `sample_drawings/` (Sample A DDS). Sample B PDF in `~/Downloads/Sample B.pdf`.

### Review UI — `export_review.py` → generates self-contained `review.html`
- Canvas floor-plan editor: select/move/add/delete rooms; add/delete polygon **nodes**; edit labels.
- **Construction tab**: wall / window / door / roof / floor / room-type / **zone** palettes — add (＋), edit (✎, renaming cascades to every room/wall/opening/zone assignment using that type), delete (✕), and **paint** onto walls/rooms. Wall/roof/floor palettes always get a "No X load" entry prepended first, real project constructions after — a real bug until this session: a DM-setup JSON's wall_types/roof_types/floor_types is just the site's actual construction(s) with no "none" placeholder, so index 0 (silently treated elsewhere as the protected/undeletable "no load" sentinel) WAS the project's real construction — no way to mark something not load-bearing, and the real construction couldn't be edited or deleted.
- **Per-room**: type, **zone** (for splitting a project into per-system block loads — see below), roof, floor, **ceiling height**, **floor-to-floor height** (optional — defaults to ceiling height when blank; exterior wall conduction area uses this, not ceiling height, since the wall runs to the structure above, not just a dropped ceiling — a real gap until this session, walls above a plenum were under-counted), **floor covering** (Carpet / No carpet, defaults Carpet — feeds RTS zone selection, see item #2), and a **loads grid** (lighting W/ft², equipment W/ft², occupants, ventilation CFM, infiltration ACH, heating/cooling setpoints, RH) — auto-seeded from the room type's record when one is assigned; the cooling setpoint now actually drives the cooling calc (item #2), not just display.
- **Zones**: assign rooms to a zone (dropdown per-room, or paint like room type/roof/floor) to get per-system block loads instead of just one whole-building total — mirrors the golden-building calibration's AHU-1/AHU-2 finding that a group of rooms has its OWN peak hour (`engine/compute.py` sums each zone's rooms hour-by-hour and finds that zone's own argmax, same as the whole-building total does — not just adding up each room's individual peak). Unassigned rooms land in an implicit "Unassigned" zone so the zone totals still tie back to the building total. If no room has a zone, the `zones` key is omitted entirely from `compute_building`'s result and the report — existing projects are unaffected. Report gets a new **Zones** table (rooms/area/sensible/latent/total/tons/peak) between the whole-building tiles and the per-room table, shown only when zones are in use.
- **Windows/doors**: placed on wall edges, with width/height/sill.
- **Orientation**: set North (top bar) + compass rose → each load-bearing wall shows its cardinal direction; saved as `wall_orientation`.
- **Auto-loads** any `*loadcalc*.json` dropped in its folder to populate palettes + seed loads.
- **Save** → `rooms_edited.json` (the full input packet).
- **Unsaved-changes guard**: a snapshot of `buildPayload()` is taken at load and refreshed on every successful Save/Run Load Calc; `beforeunload` compares against it and warns before closing/navigating away if they differ — nothing here auto-saves, so this is the only thing preventing silent loss of in-progress edits (this bit someone this session: redraw/relabel work was never saved before the server was stopped).

### DM integration — `../adicot-app/`
- `dm_setup_generator.py`: added `build_loadcalc_payload()` / `render_setup_json()` — same inputs as `render_setup_vbs`, emits JSON with **full room-type records** + wall/roof/glass/door types + project settings.
- `app.py` route (`generate_dm_setup`, ~line 2486): now **zips** the `.vbs` + `<name>-loadcalc.json` for download AND writes `loadcalc_input.json` into the job workspace. **Needs commit + deploy to Render** to go live.

### Weather — `weather/`
- `stations.json` (10 FL stations) + `__init__.py` (`resolve_station`, `design_conditions`). ⚠️ values PROVISIONAL, `"verified": false`. No τ (tau) values — engine defaults them.

### RTS Engine — `engine/` (ASHRAE Handbook-Fundamentals Ch.18)
| File | Stage | Verified against |
|---|---|---|
| `design_day.py` | 1 design-day temps | Table 27 (±0.05 °F) |
| `solar.py` | 2 solar geom + clear-sky (τ model) + incidence | Solar Table + Table 29A (≤0.14) |
| `conduction.py` | 3 sol-air + CTS | Table 29B (exact) |
| `fenestration.py` | 4 window solar + conduction | Table 30 (exact) |
| `shading.py` | 4b overhang shadow + profile angle | Table 33 (via golden room, ≤0.1%) |
| `gains.py` | 5 people/lighting/equipment | Table 35 schedules (exact) |
| `rts.py` | 6 radiant split + RTS | Table 29B col13 (exact) |
| `room.py` | orchestrator (composes stages incl. shaded-window Table 32/33 method) | end-to-end wall 304.6 vs 304.7; **golden room 4,122.2 vs 4,121.6 (0.01%)** |
| `coeffs.py` | CTS/RTS coefficient library | extracted from workbook; roof CTS + roof/equipment radiant fractions corrected against golden room |
| `heating.py` | steady-state heating loss (conduction + air, no RTS/solar/gains) | `tests/test_heating.py` (arithmetic, no ASHRAE table — heating has no time-series to validate) |
| `compute.py` | building compute (from review packet) — cooling + heating | smoke-tested, cooling calibrated per item #2, heating hand-verified component-by-component |
| `report.py` | results HTML — styled after Design Master's HVAC Loads report | — |

**Golden room reconstruction** (`tests/test_golden_room.py`, `validation/golden_room_215b_inputs.py`):
all 10 components of ASHRAE Table 35's Room 215B (roof, brick-pilaster + spandrel
walls x south/west, one window type x south/west with a 10 ft overhang and
venetian blinds, people/lighting/equipment) reconstructed from raw inputs only.
Composed result: room sensible 3777.58 vs 3777.02, return-air sensible 144.65 vs
144.61, total block cooling 4122.23 vs 4121.63 (0.01%). Two real bugs found and
fixed along the way: `coeffs.RADIANT_FRACTION["roof"]` was 0.46 (should be 0.60)
and `["equipment"]` was 0.50 (should be 0.30); `coeffs.CTS_ROOF["light"]` was
reusing the wall's CTS array instead of the roof's own. Also added: shaded-window
composition (`room.window_cooling_load(iac_profile=...)`) — a window with an
interior shade (blinds) routes beam+diffuse+conduction through the NONSOLAR RTS
with a single convective/radiant split, per ASHRAE Table 32/33, rather than the
unshaded Table 31 method (100%-radiant beam through the SOLAR RTS) that `room.py`
only supported before. IAC (indoor shading) varies with the vertical solar
profile angle Omega, interpolated between IAC(0) and IAC(60), flat beyond 60 deg
(`fenestration.iac_at_profile_angle`, `shading.profile_angle`).

**Golden building reconstruction** (`tests/test_golden_building.py`, `validation/golden_building_inputs.py`):
Tables 37/38/39 (AHU-1 first floor, AHU-2 second floor, building) reconstructed
the same way — each block is just a much bigger "room" using the *same* wall/
roof/window constructions and Atlanta location as Room 215B, with aggregated
wall/window areas and people/lighting/equipment totals (confirmed: AHU-1 +
AHU-2's inputs sum exactly to the Building block's). Two things these blocks
needed that the single room didn't: return-air credit is OFF (0% roof/lighting,
vs. Table 35's own 30%/26% choice), and a ventilation (outside-air) load on top
of room+RA+latent — OA sensible is derived (`1.1*cfm*(Tout-Tin)`, confirmed to
~0.02%), OA latent + two heat-pump terms (supply-air, chilled-water) are taken
as given (a specific mechanical-system add-on outside the RTS envelope method).
Also surfaced: AHU-1's own room-sensible peak (month 9 hour 12) does NOT
coincide with its TOTAL BLOCK peak (month 8 hour 15) — outside-air sensible
follows the outdoor dry-bulb curve and can shift the combined peak to a
different month than the envelope alone would. Result: room sensible within
0.01-0.04%, TOTAL BLOCK COOLING LOAD within 0.01-0.02% on all three blocks
(target tons 34.81/30.10/65.91). Composition logic shared between both golden
tests via `validation/reconstruct.py`.

Tests: `tests/test_*.py` (design_day, solar, solar_irradiance, conduction, fenestration, rts, room_endtoend, golden_room, golden_building) — **all 9 green**. Golden targets: `validation/ashrae_golden.json` + `extract_golden.py`. ASHRAE workbooks: the two `RTS *.xls` files in the project root.

### Bundle — `app.py` / `loadcalc.py`
- **`python3 app.py`** — the easy way in. Opens a browser landing page with two drop zones: the plans PDF, and a JSON file (the DM-setup types JSON the first time on a project, or a saved `rooms_edited.json` to resume — detected automatically from the JSON's shape, no mode toggle needed). Also reachable as `python3 loadcalc.py app`.
- `python3 loadcalc.py parse "<plans.pdf>" [types.json] [px_per_ft] [min_len_ft] [page]` — CLI equivalent of the "first time" path. Also starts a local server (port 8765+) so the review UI's "Save JSON" / "Run Load Calc" buttons write files + run the engine directly. Blocks (Ctrl-C to stop) — the server has to stay alive for those buttons to work.
- `python3 loadcalc.py resume rooms_edited.json [types.json]` — CLI equivalent of the "resume" path. Reuses the plan image already embedded in the last `parse`'s `review.html` (no PDF re-parse). Also blocks.
- `python3 loadcalc.py calc rooms_edited.json [types.json]` — only needed if you didn't use "Run Load Calc" in the browser.

## 4. How to run it

```bash
cd "/Users/mileschoquette/Documents/Load Calc Software"

# Easiest: opens a browser page — drop in your plans PDF + a JSON file
python3 app.py

#   First time on a project: JSON = the DM-setup types JSON from the Adicot site.
#   Picking back up: JSON = your saved rooms_edited.json instead — same drop
#   zone, the app tells them apart by the JSON's contents.
#   Set the drawing scale (px/ft) before clicking Start — 18 for Sample A.

#   In the browser: edit rooms, then either
#     "Save JSON"      → writes rooms_edited.json into the project folder
#     "Run Load Calc"  → saves + runs the engine + opens results.html
#   Ctrl-C the terminal when done (stops the local server).

# --- CLI equivalent, if you'd rather skip the browser landing page ---
python3 loadcalc.py parse "sample_drawings/Sample A plan (MECH).pdf" loadcalc_input.json 18

#   Didn't finish? Resume later without re-parsing the PDF:
python3 loadcalc.py resume rooms_edited.json loadcalc_input.json

# Only needed if you skipped "Run Load Calc" in the browser:
python3 loadcalc.py calc rooms_edited.json loadcalc_input.json

# run the engine test suite
for t in design_day solar solar_irradiance conduction fenestration rts room_endtoend golden_room golden_building heating; do python3 tests/test_$t.py; done
```
Scale note: Sample A = 18 px/ft (¼"=1'); Sample B page 6 = 13.5 px/ft (3/16"=1') with `min_len 2.5`. The initial px/ft guess only needs to be roughly right (it affects wall-detection quality during parsing) — once all rooms are drawn, the review UI's **Total ft²** field (top bar) recalibrates the scale exactly: enter the building's actual total square footage and click **Match**, and every room's real-world size rescales proportionally (room points are stored in raw pixel space; area/wall-length/the measure tool all divide by the scale factor live, so correcting it once corrects everything — window/door sizes are stored in real feet already and are unaffected).
Deps: `pymupdf shapely networkx matplotlib numpy xlrd pandas` (+ `esprima` for the review-UI JS syntax check).

## 5. Outstanding work (priority order)

1. **Golden engine calibration — ✅ DONE (both single room and whole building).** Single room (215B, `tests/test_golden_room.py`) within 0.01%; AHU-1/AHU-2/building blocks (**34.81 / 30.10 / 65.91 ton**, `tests/test_golden_building.py`, `Table 37/38/39`) within 0.02%. This item is closed — the engine's physics and composition are certified against every level of ASHRAE's own worked example. Item #2 (teaching `compute.py` to pick the *right* coefficients for an arbitrary real project, since real buildings won't hand us a "Table 37"-style pre-aggregated block with known CTS/RTS IDs the way this textbook example does) is now also done.
2. **CTS mass + RTS zone from real constructions — ✅ FULLY DONE.** `coeffs.mass_from_construction(category, itype, u_value)` maps DM's `iType` construction code — confirmed against the real `dm_hvac.dm` catalog via the Adicot app app's `_MASS_CLASS_OPTIONS` (wall: 2=Frame/5=Wood stud→light, 10=Block/CMU→heavy; roof: 4=Wood deck-attic/2=Frame→light, 10=Concrete/masonry→heavy) — to the right CTS set, for both walls and roofs. **RTS zone is no longer hard-coded to zone 8** — `coeffs.py` now has all **24 zones** (not just 8) extracted from the same ASHRAE workbook's "RTS Data" sheet (Ch.18 Table 19/20: 18 exterior zones by wall mass [light/medium/heavy] x floor covering [carpet/no carpet] x glass % of gross exterior wall area [10/50/90%], + 6 interior zones for rooms with no exterior exposure). `coeffs.select_rts_zone(wall_mass, has_carpet, glass_pct, is_interior)` picks the right one per room — wall mass from the room's dominant exterior wall construction, glass % computed from actual window/wall areas already being tracked, interior-exposure auto-detected (no exterior walls or windows). The one new input this needed: a per-room **Floor covering** (Carpet / No carpet) dropdown in the Rooms tab — defaults to Carpet (matches the old zone-8-always assumption, so existing projects don't silently change). Every room's report row now shows which zone was selected (e.g. "13 (HW w Carpet 10% glass)") for traceability. τ (tau) is threaded from the weather station (`stations.json` now has `tau_b`/`tau_d` — see item #3) and each room's own cooling setpoint (`loads.clg`) now actually drives its cooling calc instead of one global indoor temp for every room (a real bug: the field was captured and displayed but silently ignored by the physics).
3. **Weather dataset — τb/τd added, station verification still open.** `stations.json` now has `tau_b`/`tau_d` per station (was the one missing field keeping tau "inert" per item #2's old note) — currently a single representative pair per climate zone (1A humid-coastal ≈0.44/2.30, 2A ≈0.47/2.20), not station-differentiated, and — like the rest of the dataset — clearly flagged provisional. **Still open:** verify all values (including these new tau figures) against the published ASHRAE Fundamentals climatic-design tables; expand beyond FL.
4. **Parser accuracy.** Sample A 22 vs 19 (cased openings/pass-throughs over-split). Consider door-evidence gating (tried; needs the leaf/arc signal done right). Multi-page/multi-floor sheet handling. OCR for outlined-text PDFs (Sample A) to auto-read scale + room labels (currently manual).
5. **Review UI gaps for the engine.** ✅ Window shading (overhang depth/height + interior-blind IAC at 0°/60°/diffuse) is now editable per window instance in the Rooms tab, and `compute.py` passes it through to `room.window_cooling_load`. Construction types (wall/roof/floor/window/door/room) are now fully editable in place (✎ icon per chip) — renaming cascades to every room/wall/opening using that type; previously you could only add or delete. **Floors→F-factor — ✅ DONE** (see item #6's heating entry: a "slab"-named floor type now uses true F-factor x perimeter, not area x U). Still open: fins (only overhangs are captured).
6. **Heating load — ✅ DONE, including the F-factor slab path.** `engine/heating.py` — steady-state, not RTS: ASHRAE's heating design method is deliberately simpler than cooling (worst case = a still, sunless night), so it's just `conduction_loss(u, area, ti, to) = U·A·ΔT` and `air_loss(cfm, ti, to) = 1.1·cfm·ΔT`, no solar, no thermal-mass time delay, and **no credit for internal gains** (conservative — people/lights/equipment might not be running yet during a cold-morning warm-up). Wired into `compute_building`'s existing per-room envelope loop (reuses the same wall/roof/window/door/opening-area math already there for cooling, so no second traversal) plus a **new floor heat-loss path** — floor types were fully editable in the review UI but never consumed by the calc until now. A "slab"-named floor type (matched against the type's name or description, e.g. the seeded "Slab-on-grade"/"Slab + R-10 edge") uses the true ASHRAE **F-factor × perimeter** method instead of area × U — the seeded defaults' `u` values (0.73/0.54) were already implausible as area-based U (way too high) but exactly right as F-factors, so this was a latent mislabeling waiting to be used correctly, not new data. Perimeter is approximated from the room's exterior wall edges (already computed for RTS zone selection), since true slab-edge perimeter isn't separately tracked — falls back to area×U if a room has zero exterior wall length. Non-slab floor types (e.g. "Floor over unconditioned") keep the area×U method, which is correct for them. Outdoor design temp comes from the weather station's `htg_db_996` (99.6% annual, already in `stations.json`, just never threaded through `loadcalc._weather()` until now) with a generic 35°F fallback; indoor design temp is the room's existing heating-setpoint field (`loads.htg`, defaults 70°F). Each room gets `heating_btuh` + a `heating_components` breakdown (mirrors the cooling `components` list); building and zone totals sum directly (no peak-hour search needed — heating has no 24-hour profile, just one steady-state number per room). Report gets a new **Heating Load Details — Room** table (component breakdown, DM has this section, it was explicitly NOT replicated when the DM-style report first shipped — now it is) and a **Heating Btu/h** column on both Load Total Summary tables. Verified: hand-calculated a full room's heating loss (wall/roof/floor/window/door/ventilation, including the slab F-factor case) against the engine's output component-by-component — exact match. `tests/test_heating.py` covers the two formulas (plain arithmetic, not an ASHRAE table lookup — there's no time-series or solar in heating to validate against a workbook).
7. **Integration.** Fold parser + engine into the Adicot app Flask app as the "Load Calc" tab (currently standalone CLI + self-contained HTML).
8. **Report format — ✅ DONE, styled after Design Master's HVAC Loads report.** `engine/report.py` rewritten from a modern dashboard to DM's table-based look (serif type, black-bordered tables, mainHeader/otherHeader/otherData sections) built from a real DM export (`dm_hvac-loads1.html`/`.css`) the user provided. Sections: Project Information; Roof/Wall/Door/Glass Types + their per-room usage tables (room, area, orientation — the "No X load" editor placeholder is filtered out, it isn't a real material); Room Information (area, ceiling + floor-to-floor height, loads, setpoints); Cooling Load Details and **Heating Load Details** - Room (component breakdown with % of room total — one row per room rather than DM's fixed roof/wall/glass/... columns, since our component list length varies); Load Total Summary for the building and, when zones are in use, per zone, now with a Heating Btu/h column. `compute_building` now also returns `envelope` (every wall/roof/window/door instance: room, type, orientation, area — new) and `types` (the raw wall/roof/door/glass type arrays passed through) so the report has real data for the Types/Usage tables instead of only the flattened per-room component list it had before. **Still not replicated** (DM has, we don't compute): Supply Air Requirements, Psychrometrics — would need an air-side system design this engine doesn't do. "ASHRAE Type"/"Color" columns intentionally renamed "Mass Class"/dark-light — DM's own internal group codes aren't something we compute; showing our actual CTS mass selection (item #2) is more honest than mislabeling it.
9. **Exhaust makeup air — ✅ DONE.** DM room-type records carry an `exhaust.cfm_per_sqft` field (e.g. FBC-required commercial cooking hood exhaust) that was captured in `loadcalc_input.json` but never read anywhere — a real gap, since exhaust CFM is often much larger than a space's general ventilation (e.g. 0.7 vs 0.12 CFM/ft² in a real project's kitchen type) and, if made up with outdoor air, can be one of the largest loads in the building. `seedLoads()` now seeds `loads.exh_cfm` from it; a new **`loads.makeup_pct`** field (0-100%, editable in the Rooms tab loads grid) decides how much of that exhaust CFM counts as a *new* outdoor-air load — defaults to **0% (transfer air)**, per the user's stated usual design practice (kitchens are typically pressure-balanced by transfer from an adjacent space, e.g. dining, not a fully dedicated makeup air unit), not the conservative 100% first proposed. Only `exh_cfm x makeup_pct/100` becomes new outdoor air in `compute.py`, kept as its own traceable sensible/heating component ("Exhaust Makeup") rather than folded into the general Ventilation/Infiltration line, and included in the room's latent calc. Labels generalized from "Kitchen Exhaust" to "Exhaust" — the same field covers restroom/general exhaust too (every commercial building has that, not just restaurants), which is what makes item #10's building-wide check actually comprehensive. Verified both ends: `makeup_pct=0` adds nothing (matches "we usually design assuming transfer air"); a nonzero value adds a real, substantial sensible+latent load, shown as its own line in both the Cooling and Heating Load Details tables and as a column in Room Information.
10. **Air balance / building pressurization report — ✅ DONE.** New report section: checks that the building is never net-negative pressure (outdoor humid air being drawn in through every crack instead of kept out — a real moisture-intrusion risk in FL, not just a comfort issue). The insight that made this tractable: at the BUILDING level, only designed airflows that actually cross the envelope matter — ventilation OA + exhaust-makeup OA (in) vs. total exhaust (out, the *full* exhaust CFM regardless of how much is made up — the fan pulls all of it). Transfer air (the non-makeup portion of exhaust) is a wash at the building level since it moves *within* the building rather than crossing the envelope, so it doesn't need to be modeled as an explicit path between rooms — no new data beyond what item #9 already added. `compute_building` returns a new `air_balance` dict: building totals (Ventilation OA / Exhaust Makeup OA / Total OA In / Exhaust Out / **Net CFM**, negative = under-pressured), the same breakdown per zone (reuses the existing zone grouping), and per room. Infiltration is deliberately excluded from the balance — it's a *symptom* of imbalance, not a reliable designed air source. A room can and often should show net-negative on its own (e.g. a kitchen counting on transfer air from dining) — that's flagged as expected/informational, not a problem; only the BUILDING total is the actual pass/fail check. Verified with a kitchen (30% dedicated makeup, -196 CFM alone) + dining (300 CFM ventilation) pair: building nets to +104 CFM (correctly not flagged) because dining's ventilation air covers both its own need and the kitchen's transfer requirement; a second case with too little dining ventilation (50 CFM) correctly nets to -146 CFM and flags **⚠ NEGATIVE**. **Known gap, disclosed in the report itself:** only exhaust the engineer actually enters counts — a fume hood, dryer vent, or other exhaust source not entered as `exh_cfm` on some room won't be caught by this check.

## 6. Key decisions & gotchas (don't re-learn these)

- **Folder was cleaned up (2026-07-23).** One-off dev/probe scripts, debug renders, and the last Sample A run's working files (`review.html`, `results.html`, `rooms_edited.json`, `loadcalc_input.json`) were moved to `dev_archive/` (not deleted — no git here, so nothing was thrown away). The root now holds only what's needed to run + test the tool; see §4 for the run steps.
- **PDFs vary by exporter.** Sample A = text converted to **outlines** → `get_text` returns ~nothing → OCR needed for scale/labels. Sample B = **live text** (scale + labels readable). Don't assume.
- **Sample A page is rotated 270°.** `get_pixmap` renders upright but `get_drawings` coords are unrotated — map with `page.rotation_matrix` (done in `export_review.ext_coords`).
- **ASHRAE example building is rotated 30° off cardinal** — its "west" wall azimuth is 60° W of S. Azimuths are measured **from south, + west**.
- **Room builder:** `polygonize` + noding works; the doc's half-edge traversal did **not**. Walls are double lines; `polygonize` handles it (room interior = a face, wall cavity = a thin sliver dropped by the area filter). Doors are gaps → `close_wall_gaps` bridges collinear wall pieces. Arc-based door closing was built but every gating attempt made results worse — left unused.
- **`x or default` falsy-zero trap** bit the existing Adicot app; use `is None` checks where 0 is valid (e.g., occupants).
- **Room-type seeding (`seedLoads`/`RT_REC`) broke on save+resume — fixed (2026-07-23), two parts.** `RT_REC` (name → full DM room-type record: lighting/equipment/people/ventilation/etc., built from `D.room_types`) needs each entry to be an object with a `.name`. But `buildPayload()` was saving `room_types` as bare name strings (`ROOM_TYPE_LIB` is just names, for the dropdown) — so after any Save/Run Load Calc + reload/resume, `D.room_types` came back as strings, `rt.name` was `undefined` for all of them, and `RT_REC` ended up as one bogus `{undefined: <last name>}` entry instead of a real lookup table. Every room's loads stopped updating on type change, silently, with no error. Part 1: `buildPayload()` now saves `RT_REC[name]||{name}` (the full record when one exists), and `editType`'s room-type rename migrates `RT_REC`'s entry to the new name instead of orphaning it. **Part 2 (the one that actually mattered for anyone who'd already saved before part 1 existed):** a `rooms_edited.json` saved by the OLD broken code has room_types permanently flattened to bare strings — part 1 alone can't fix a file that already lost the data, and `export_review.resume()` was unconditionally preferring the saved (degraded) `room_types` over the freshly-parsed one even though the fresh parse still had full records from `loadcalc_input.json`. Fixed: `resume()` now repairs bare-string entries by matching names against the freshly parsed types file's records before handing off to the browser. If loads ever silently stop seeding again, check both halves: (a) is `buildPayload()` still saving full records, not just names, and (b) is whatever reads a saved file back in still enriching bare names from a fresher source rather than trusting the saved file blindly.
- **A `loads` field added to `seedLoads()` after a room was already typed doesn't retroactively appear — general pattern, hit again by exhaust (2026-07-24).** `seedLoads()` only runs when a room's type is (re)assigned via the dropdown; a room typed *before* some new derived field existed in that function has a saved `loads` object that will never contain it, even though the assigned type's record clearly has the data (confirmed on a real project: a "Prep" room assigned "Kitchens (cooking)... *0.7 Exh (FBC)" showed correct ventilation CFM — proving it *was* seeded from that record at some point — but blank Exhaust, because `exh_cfm` seeding didn't exist yet when that happened). Re-running full `seedLoads()` on load isn't safe as a blanket fix — it would silently clobber any manual override the engineer made to other fields back to the type's defaults. Fixed with `backfillExhaust()`: runs once at page load, fills in *only* `loads.exh_cfm` when it's missing and the assigned type has `rec.exhaust`, leaves every other loads field (and any already-set `exh_cfm`) untouched. **If a new seedable field is added in the future, it needs the same treatment** — a targeted backfill for that one field, not a blanket re-seed — or existing typed rooms will silently miss it the same way.
- Engine sol-air: α=0.9, ho=3.0, wall long-wave=0 / roof≈7. RTS zone 8 (MW w/ carpet): nonsolar `[.49,.17,.09,.05,.03,.02,.02,.01×13]`, solar `[.54,.16,.08,.04,.03,.02,.01×13]`.

## 7. Memory (auto-loaded each session)
`~/.claude/projects/.../memory/` — see `loadcalc-build-state.md` (fullest), `sample-a-sample-ground-truth.md` (19 rooms), `ashrae-rts-validation.md` (golden targets), `cad-pdf-text-outlined.md` (PDF quirks), `loadcalc-doc-decisions.md`, `adicot-app-existing.md`. Architecture doc: `adicot_loadcalc_parser_architecture.md`.
