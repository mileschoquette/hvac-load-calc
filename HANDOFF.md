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
| `ducts.py` | duct auto-sizing — equal-friction method + network CFM propagation + fitting-loss static pressure | `tests/test_ducts.py` (arithmetic + hand-built AH/trunk/branch networks — no ASHRAE table involved) |
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
for t in design_day solar solar_irradiance conduction fenestration rts room_endtoend golden_room golden_building heating ducts; do python3 tests/test_$t.py; done
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
8. **Report format — ✅ DONE, styled after Design Master's HVAC Loads report.** `engine/report.py` rewritten from a modern dashboard to DM's table-based look (serif type, black-bordered tables, mainHeader/otherHeader/otherData sections) built from a real DM export (`dm_hvac-loads1.html`/`.css`) the user provided. Sections: Project Information; Roof/Wall/Door/Glass Types + their per-room usage tables (room, area, orientation — the "No X load" editor placeholder is filtered out, it isn't a real material); Room Information (area, ceiling + floor-to-floor height, loads, setpoints); Cooling Load Details and **Heating Load Details** - Room (component breakdown with % of room total — one row per room rather than DM's fixed roof/wall/glass/... columns, since our component list length varies); Load Total Summary for the building and, when zones are in use, per zone, now with a Heating Btu/h column. `compute_building` now also returns `envelope` (every wall/roof/window/door instance: room, type, orientation, area — new) and `types` (the raw wall/roof/door/glass type arrays passed through) so the report has real data for the Types/Usage tables instead of only the flattened per-room component list it had before. **Still not replicated** (DM has, we don't compute): Psychrometrics — would need an actual psychrometric process-point calc. **Supply Air Requirements — ✅ DONE, 2026-07-30** (see item #11's duct-sizing entry): a per-room required-CFM table (always renders, independent of any duct drawing) plus a Duct Schedule / Diffuser-Grille Schedule / Air Handling Equipment table pulling live from `engine.ducts.duct_report_data` — closes this exact gap. "ASHRAE Type"/"Color" columns intentionally renamed "Mass Class"/dark-light — DM's own internal group codes aren't something we compute; showing our actual CTS mass selection (item #2) is more honest than mislabeling it.
9. **Exhaust makeup air — ✅ DONE.** DM room-type records carry an `exhaust.cfm_per_sqft` field (e.g. FBC-required commercial cooking hood exhaust) that was captured in `loadcalc_input.json` but never read anywhere — a real gap, since exhaust CFM is often much larger than a space's general ventilation (e.g. 0.7 vs 0.12 CFM/ft² in a real project's kitchen type) and, if made up with outdoor air, can be one of the largest loads in the building. `seedLoads()` now seeds `loads.exh_cfm` from it; a new **`loads.makeup_pct`** field (0-100%, editable in the Rooms tab loads grid) decides how much of that exhaust CFM counts as a *new* outdoor-air load — defaults to **0% (transfer air)**, per the user's stated usual design practice (kitchens are typically pressure-balanced by transfer from an adjacent space, e.g. dining, not a fully dedicated makeup air unit), not the conservative 100% first proposed. Only `exh_cfm x makeup_pct/100` becomes new outdoor air in `compute.py`, kept as its own traceable sensible/heating component ("Exhaust Makeup") rather than folded into the general Ventilation/Infiltration line, and included in the room's latent calc. Labels generalized from "Kitchen Exhaust" to "Exhaust" — the same field covers restroom/general exhaust too (every commercial building has that, not just restaurants), which is what makes item #12's building-wide check actually comprehensive. Verified both ends: `makeup_pct=0` adds nothing (matches "we usually design assuming transfer air"); a nonzero value adds a real, substantial sensible+latent load, shown as its own line in both the Cooling and Heating Load Details tables and as a column in Room Information.
11. **Duct sizing / ductwork drawing — ✅ DONE through the Supply Air Requirements report section, 2026-07-30.** Phase A+B (data model, drawing tool, rendering); drawing-tool UX refinement + a DM-reference visual pass; automatic rect/round/flex classification + split-to-edit + straighten (2026-07-27); auto-sizing (2026-07-27); and now a report section (2026-07-30). Plans at `~/.claude/plans/warm-splashing-sunset.md` (Phase A/B + UX refinement) and `~/.claude/plans/golden-rolling-cascade.md` (auto-classification + split, then auto-sizing). Decisions locked in before any code existed:
    - **Routing is manual, sizing is automatic.** The engineer draws duct runs in the review UI; the tool does not auto-route (real routing depends on ceiling/structural constraints it can't see). It sizes and labels each drawn segment from the CFM it carries (velocity method — see Phase D).
    - **Rendering must be double-line, true-to-scale duct outlines — not a single-line schematic.** Explicit hard requirement: DM's ductwork "looks different from the rest and you can tell the quality," and matching that bar is a condition of the feature being worth building at all. Reference drawings received and reviewed: `Invision-MAC-Mech.pdf` (commercial) and `VDG-Taylor Residence-Mech 2026 04 27.pdf` (residential, has an explicit HVAC Symbols legend that spelled out the exact convention — square=ceiling supply diffuser, circle=ceiling return, arrow=sidewall, AH/DH=square, CU=circle, rigid=double-line true-width, flex=coiled/corrugated centerline).
    - **v1 system scope: one packaged unit (RTU/AHU) per zone** — simple supply trunk-and-branch plus a return path. Matches the existing Zones concept and this firm's actual projects (dental/retail/small commercial).
    - **Future upgrade path to keep open (not needed for v1):** VAV-with-reheat. Don't hardcode a "one CFM value per zone" assumption that a VAV box's variable flow would break.

    **Phase A — data model + manual drawing tool: ✅ DONE.** New "Ducts" tab in `export_review.py`'s review UI, same conventions as the rest of the file: `duct_runs`/`duct_terminals`/`duct_equipment` are per-floor working arrays (mirrors the `rooms`/`openings` floor-swap pattern exactly, including `switchFloor()`/`buildPayload()` stash-and-flatten), saved into the same `rooms_edited.json` blob, round-trips through `resume()`. Duct runs draw as open polylines reusing the room-drawing `draft`/`finishDraw` pattern (new `mode='draw_duct'` + `finishDuctDraw()`, min 2 points, no auto-close). Terminals/equipment are single-click-to-place free points (not wall-attached like windows/doors — a ceiling diffuser isn't on a wall). Verified end-to-end: drawing, editing (CFM/size/zone via a property form), deleting, and a full save→resume round-trip all confirmed working via direct browser testing.

    **Phase B — rendering: ✅ DONE, checkpointed with user.** `drawRigidRun`/`drawFlexRun`/`drawTerminalIcon`/`drawEquipmentIcon` in `export_review.py`. Rigid = double-line outline (`inToPx()` converts real inches through `PPF`/`view.zoom`), stroked not filled. Flex = thin centerline + short angled ticks at a fixed **real-world** spacing (`inToPx(3)`) so the corrugated look is scale-invariant, not screen-pixel-invariant. Both label with size+CFM at their longest segment's midpoint. A second `<details class="legend">` block ("Duct / HVAC Symbols") matches the reference's 4-column categorization. Verified by actually drawing a sized rigid trunk + flex branch + terminals + equipment in the browser and zooming into the rendered canvas (not just a screenshot at a glance) to inspect the double-line and coil texture up close.

    **Corner-joint limitation — ✅ FIXED.** Rigid duct corners originally drew as independent per-segment offset rectangles, leaving an unmitered gap/notch at inside bends (flagged as a v1 tradeoff at the Phase B checkpoint). Replaced with `offsetPolyline()`: one mitered outline for the whole run (interior vertices offset along the two adjacent segments' averaged/miter direction, capped like canvas's `miterLimit` to avoid spikes on sharp reversals), stroked once as a single closed path instead of N independent rectangles. Verified by drawing a multi-bend run and zooming into each corner in the browser — clean single outline, no notch.

    **User feedback after the Phase A/B checkpoint (2026-07-27) — ✅ FIXED, all three items:**
    1. **Continuous multi-branch drawing session — done.** `finishDuctDraw()` no longer drops back to `select` after finishing a run; it stays in `draw_duct` mode ("pick the pen up, keep drawing"), so the next click immediately starts a new run of the same kind. `Enter` (new binding — nothing previously used it) ends the continuous session without clearing the selected duct kind, mirroring what re-clicking "Draw Run" already did; `Esc` still fully resets everything. Verified by drawing several connected/disconnected runs in one session without touching any button between them.
    2. **Snapping — done.** New `nearestDuctPoint(wp)` (same pattern as wall-edge snapping in `nearestEdge()`) scans every duct-run vertex, terminal point, and equipment point; wired into duct-draft point placement and terminal/equipment placement with the same ~12px screen tolerance used elsewhere. A new `hoverDuctSnap` ring (mirrors the existing `hoverEdge` wall-hover pattern) previews the snap target before the click lands. Verified: clicking near an existing run's endpoint/vertex or a placed terminal/equipment icon lands exactly on that point, confirmed both visually and by inspecting the resulting stored coordinates.
    3. **Straighten / clean angles — done.** New `snapAngle(prev, p)` constrains each new point to the nearest 45° increment from the previous point, live while drawing; combined with item #2's point-snap via `ductNextPoint()` (an explicit connection to an existing network point always wins over the angle constraint, so branching isn't fought by the ortho-snap). A dashed rubber-band preview (`ductPreviewPt`) shows where the point will land before the click. Verified: an arbitrary-angle click during a run was confirmed to land at exactly 45° in the stored data, and a non-45°-multiple click near an existing point still snapped to that exact point (priority order confirmed correct).

    **Visual pass against a real DM drawing the user provided (2026-07-27) — ✅ DONE.** The DM reference showed the duct ITSELF as black with a colored centerline denoting system, not the old per-supply/return outline coloring — `drawRigidRun`/`drawFlexRun` now stroke the outline black always (`#2563eb` only on selection) and draw a separate thin centerline (`drawDuctCenterline`, blue for supply/`DUCT_SUPPLY_COL`, orange for return/`DUCT_RETURN_COL`) on top. Those centerlines are engineer notation, not part of a real drawing, so a new **Engineer/Client view toggle** (`ductViewMode`, 🔧/📄 button in the top bar, persisted in `localStorage`) hides them entirely for a submittal-ready screenshot. Flex duct's old "centerline + perpendicular ticks" look ("looks terrible" — direct user feedback on a screenshot) was replaced with a proper corrugated bellows outline (`flexRail`, two rails necking in on alternating steps — see Phase C below for how this got a smooth-collar option added). Duct size/CFM labels now center directly on the duct (`textBaseline='middle'`, no y-offset) with a white halo (`ctx.strokeText`) for legibility over the duct lines they sit on, instead of floating above it. Controls also went fully keyboard-driven (a separate, faster control surface alongside the existing side-panel chips): number-key shortcuts arm a duct/terminal/equipment tool and go straight into placement mode — see Phase C below for the current (trimmed) key layout after run kinds stopped needing 4 choices.

    **Phase C — automatic rect/round/flex classification + split-to-edit (2026-07-27) — ✅ DONE.** Looking at a second DM reference (rect/round trunk feeding straight into coiled flex branches, no upfront kind choice) the user asked for the tool to classify a run's physical form automatically instead of making the engineer pick "Rigid Trunk" vs "Flex Branch" before drawing. `kind` (`rigid_supply`/`rigid_return`/`flex_supply`/`flex_return`) split into two independent fields: `system` (`supply`/`return`, still an explicit choice — `DUCT_RUN_KINDS` now just 2 chips, keybindings `1`/`2`) and `form` (`auto`/`rect`/`round`/`flex`, defaulted to `'auto'` on every new run). `effectiveForm(d)` resolves `'auto'` via topology (`findTeePoint(d)`, generalizing the old rigid-only `ductJunctionAt`): a run stays **rect** (trunk) unless one of its endpoints tees off the *middle* of another run's segment (a real branch takeoff) — meeting another run at *its* endpoint is just trunk continuation and stays rect, so multi-segment trunks drawn across several continuous-drawing sessions don't get misclassified. A genuine mid-span tee auto-becomes **flex**, with a short (~8", `inToPx(8)`) plain same-diameter round collar at the takeoff (no taper — a real spin-in fitting isn't a funnel, unlike the tapered reducer this replaced) before the corrugation begins; `flexRail` grew a `[smoothLo,smoothHi]` arc-length window (per segment, walked from whichever end the tee is on) that renders smooth instead of corrugated inside that window. `'round'` never comes out of auto-resolution — it's only reachable as a manual override. **Split-to-edit**: a selected run shows a small orange dot at each interior vertex (not its two endpoints); clicking one (`splitDuctRun`) breaks it into two independent runs sharing that exact point (so they still read as connected, and `findTeePoint` correctly treats the shared point as a continuation, not a new tee) — each piece then gets its own **Form** dropdown (Auto/Rectangular/Round/Flexible) in the property panel to force it if the auto result isn't wanted. Rect and round trunks render identically (`drawHardRun`, unified from the old rigid-only renderer) since in 2D plan view they only differ in which size field applies (W×H vs diameter). Old saved runs with a flat `kind` string migrate once on load (preserves whatever was explicitly drawn — old rigid/flex choices become forced `rect`/`flex`, never silently reclassified as `'auto'`). Verified: a mid-span tee auto-classifies flex with the collar landing exactly at the tee (confirmed via `findTeePoint`/`flexRail`'s width pattern directly, both tee directions); an end-to-end continuation off a trunk's tip stays rect; splitting a run via a real dispatched `mousedown` on its vertex handle produces two independent, independently-overridable pieces without misclassifying either; the old-`kind` migration round-trips correctly through `resume()`.

    **Phase D — auto-sizing (2026-07-27) — ✅ DONE.** New `engine/ducts.py` (velocity method — SMACNA/ASHRAE simplified sizing, not a full equal-friction chart, which needs duct-roughness/fitting data this tool doesn't have): `required_supply_cfm(sensible_btuh, delta_t)` derives a terminal's CFM from its containing room's `sensible_btuh` (Python-only — the RTS engine's output, never replicated in the browser, which is why this is the one piece of the duct feature that round-trips through the local server instead of being 100% client-side JS like everything else above); `round_duct_diameter`/`rect_duct_width` are plain velocity-method arithmetic against SMACNA standard sizes. The actual "auto" part is network CFM propagation: `find_tee_point`/`effective_form`/`_point_in_poly` are direct Python ports of the JS functions of the same purpose (`findTeePoint`/`effectiveForm`/`pip`) so classification is identical on both sides; runs are treated as whole graph nodes (not per-point — a run needing split CFM on two halves is exactly what the split tool is for), partitioned by `(level, system)`, then BFS'd from whichever run(s) touch an **AH** equipment point (the only kind treated as a network root), summing each reachable terminal's CFM upward through the tree via `size_duct_network()`. A partition with no AH is left alone entirely — no root means no way to know which direction is downstream, so any existing `cfm` there stays whatever it was. New **📐 Auto-Size Ducts** button (Ducts tab) POSTs the live payload to a new `/api/size_ducts` endpoint (`loadcalc.py`'s `ReviewHandler`, sibling to `/api/calc` but skips the report-writing side effect) which runs `compute_building` + `size_duct_network` and returns only the changed fields, merged into the live `ductRuns`/`ductTerminals`/`ductEquipment` by id. A new `auto` flag (default `true`) on runs/terminals means a manual edit to `cfm`/any size field flips it to `false` and the auto-sizer skips that item on every future click until its "Auto-size" checkbox is re-checked — same seed-then-override precedent as `seedLoads()` elsewhere, needed here specifically because re-running Auto-Size after adding a new branch must refresh the trunk's now-stale CFM without also stomping a deliberate manual override elsewhere. Four new project-wide **Duct Design Criteria** settings (trunk/branch target velocity, trunk height, supply-air ΔT — defaults 900/700 fpm, 10", 20°F) live in the Project/Weather tab as plain `project_settings` fields, resolved in `loadcalc.py` via `_duct_design()` the same `_f()`-based way weather values already are. **Explicitly out of scope:** equal-friction sizing, fittings/elbow pressure drop, duct leakage. Verified: `tests/test_ducts.py` (formulas + a hand-built AH/trunk/2-branch/2-room network confirming the trunk's CFM equals the sum of both branches, a rootless partition is left untouched, and a manual `auto:false` override is skipped even though otherwise reachable); in the browser, a real dispatched click on the Auto-Size button (with `fetch` stubbed to a known response) correctly merged CFM/size into runs/terminals/equipment by id, and a real dispatched `input` event on a run's CFM field flipped its `auto` flag to `false` (and back, via the checkbox).

    **Phase E — Supply Air Requirements report section (2026-07-30) — ✅ DONE.** New `engine.ducts.duct_report_data(review, result, design)`: a per-room **Supply Air Requirements** table (`required_supply_cfm` off each room's `sensible_btuh` — always renders, independent of whether any ductwork's been drawn, closing the exact gap `engine/report.py`'s docstring used to flag) plus, when ductwork exists, an **Air Handling Equipment** table, a **Duct Schedule** (form/system, size, CFM, zone per run), and a **Diffuser / Grille Schedule** (auto-tagged CD-#/CR-#/SD-#/SR-# per terminal, mirroring the equipment entities' existing AH-#/CU-#/DH-# labeling convention). Calls `size_duct_network` itself and merges its output over whatever's saved in `review` (same auto-vs-manual-override rule) so the report always reflects fresh CFM/sizes on every "Run Load Calc," without the engineer needing to remember to click Auto-Size first — the same "never cache, always recompute from inputs" precedent the rest of this report already follows. Wired into `loadcalc.py::_run_calc()` (not `compute_building` — keeps the calibrated RTS engine untouched, matching Phase C's own reasoning) via `result["ducts"]`, rendered by `engine/report.py`'s new `_supply_air`/`_duct_schedule`. Verified: ran `_run_calc()` against the real `rooms_edited.json` (6 rooms, 3 runs, 11 terminals, 2 AHs already drawn from prior testing) end-to-end and confirmed all four new tables render in `results.html` with sane numbers (e.g. a 1,293 CFM flex branch at 700 fpm rounding up to the correct 20" standard diameter); full `tests/test_*.py` suite still green.

    **Phase F — real-equipment auto-matching (2026-07-30) — ✅ DONE.** Four reference CSVs
    in `equipment_data/` (`diffuser_schedule.csv`, `air_handlers.csv`,
    `condensing_units.csv`, `dehumidifiers.csv` — built by research subagents from real
    manufacturer spec sheets, each row citing its source URL) are now wired into the
    engine: `diffuser_schedule.csv` was cleaned first (was multiple overlapping
    manufacturers per terminal_kind — standardized on Titus only, capped at 2400 CFM,
    de-overlapped into a clean non-overlapping size ladder per kind). New
    `match_diffuser(terminal_kind, cfm)` / `match_air_handler(cfm)` /
    `match_condensing_unit(tons)` in `engine/ducts.py` (smallest catalog entry whose
    rated capacity covers the requirement, clamped-and-flagged at either extreme if
    nothing covers it) feed into `duct_report_data()`, which now attaches
    `matched_manufacturer`/`matched_model`(`_line`/`_size`) to every auto-sized terminal
    and AH/CU equipment row — DH is **deliberately not auto-matched** (no clean
    "required pints/day" number exists yet; that needs a psychrometric calc this tool
    doesn't do — stays a manual pick from its reference CSV). `engine/report.py`'s
    Diffuser/Grille Schedule and Air Handling Equipment tables gained a "Suggested Model"
    column. Built via two parallel worktree-isolated subagents (CSV cleanup + engine
    wiring, disjoint files, no dependency between them) per the plan at
    `~/.claude/plans/serialized-sleeping-meerkat.md`; both merged cleanly and the full
    `tests/test_*.py` suite (including the ASHRAE golden-building calibration) stayed green.

    **Phase G — printable Duct Plan + PDF (2026-07-30) — ✅ DONE.** `draw()` gained an
    optional `printMode` flag that skips the room-fill/label and window/door layers
    (this tool's own load-calc analysis overlay) while keeping the background
    architectural plan and the full duct layer — real mechanical sheets show the
    architectural background, not this tool's colored room analysis. New
    `captureDuctPlanImage()`: clears selection state, calls the existing `fit()`, bumps
    canvas resolution 3x for print quality (mutating `cv.width/height`/`ctx.setTransform`
    directly — no refactor needed, `const` only locks the binding), captures
    `toDataURL('image/png')`, then restores everything so the live view is untouched.
    Wired into `bRunCalc` → `duct_plan_png` in the payload → `loadcalc.py::_run_calc` →
    `render_report(duct_plan_datauri=...)` → a new "Duct Plan" report section (same
    image-in-table style as the existing Reviewed Floor Plan section) with a static
    plain-language legend paragraph underneath (the on-screen HVAC-symbols legend is page
    chrome, not drawn onto the canvas, so it wouldn't otherwise appear in the capture). A
    "🖨 Print / Save as PDF" button (`window.print()`) plus `@media print` CSS
    (page-break-inside:avoid per row/table, images scaled to page width) makes the whole
    report — loads, schedules, and now the duct plan — a clean PDF via the browser's
    native print dialog, no new dependency. Built directly in the main checkout (not an
    isolated worktree — too much relevant uncommitted code a fresh worktree wouldn't
    have); verified in the browser (capture returns a valid PNG, canvas/view state
    byte-identical before/after) and via `_run_calc()` producing a real `results.html`
    with the section present/absent correctly depending on whether a duct plan was
    supplied; full test suite still green.

    **Phase H — drawing labels + real air-handler proration (2026-07-30) — ✅ DONE.** Two
    labeling fixes plus a real behavior change, per the user reviewing an actual printed
    duct drawing: (1) duct run labels no longer show CFM, size only — CFM belongs on the
    diffuser/equipment labels and in the schedules, not cluttering the trunk/branch lines.
    (2) **Fixed a real bug**: `size_duct_network` assigned `equipment_cfm[eq_id]` once per
    `(level, system)` loop iteration, and the same AH roots both its supply group and its
    return group — the second assignment silently overwrote the first, so an AH's number
    was always whichever system got computed last, never both, never labeled which one.
    Now `equipment_cfm` is `{eq_id: {"supply":.., "return":..}}`, and the AH icon/panel/
    list row all show both, labeled. (3) Diffuser `size` (always in the data model and
    property panel, never auto-filled) now gets populated from `match_diffuser`'s real
    catalog match. (4) New behavior: once an AH's theoretical supply total is matched to a
    real unit, every diffuser downstream of it is rescaled ("prorated") to that unit's
    actual rated CFM rather than the raw room-load sum (`scale = matched.rated_supply_cfm
    / theoretical_total`), and every diffuser's final CFM (supply or return) rounds up to
    the nearest 5 via new `_round_up_5()` — the increment diffuser/grille catalogs are
    conventionally specified in. New `_collect_subtree_runs()` isolates one AH's own
    subtree from the shared multi-root BFS `children` map so proration and the post-scale
    re-sum of `run_cfm`/`subtree_cfm` only touch that AH's own terminals/runs. A terminal
    never connected to any AH through the drawn network keeps its raw, unrounded per-room
    share — expected, same "no root, no way to know direction" honesty already used
    elsewhere in this feature. Built via two parallel worktree-isolated subagents (engine:
    `engine/ducts.py`/`engine/report.py`/`tests/test_ducts.py`; rendering:
    `export_review.py` only) against an agreed field contract (`supply_cfm`/`return_cfm`
    replacing equipment's old single `cfm`; terminals' existing `size` field populated) so
    neither needed the other's actual code. Both merged cleanly; full `tests/test_*.py`
    green (including golden_room/golden_building); verified against the real
    `rooms_edited.json` end-to-end (e.g. AH-1: supply 1,200 / return 1,155 cfm, distinct
    and both present; diffuser CFMs landing on multiples of 5 with real matched sizes like
    `14" dia neck`).

    **Phase I — return CFM derived from supply, not computed independently (2026-07-30) —
    ✅ DONE.** Phase H's AH-proration made supply and return silently diverge per room:
    both started out equal (same room `required_supply_cfm`, split across each system's
    terminals), but only supply got rescaled to the matched real AH's rated CFM — return
    kept its independent, un-prorated value. Per the user: return should just equal
    supply, always. `size_duct_network` now processes every **supply** group (across all
    floors) fully — theoretical BFS, AH-match proration, round-up-5 — before touching any
    **return** group; a new step in between sums each room's now-finalized supply total
    (`room_supply_totals`, reusing the existing `room_terminal_groups` structure) and
    overwrites that room's return terminals' CFM with an even split of it — replacing the
    old independent `required_supply_cfm` calc for return entirely (a room with no supply
    terminals gets 0 on its return terminals). The return group then runs through the same
    network-propagation + round-up-5 pass as before, just fed these room-derived leaf
    values instead. Verified: `tests/test_ducts.py`'s proration test now asserts the return
    terminal equals the *prorated* supply value (not the raw theoretical one the old code
    would have produced — the failing case this fix targets); full suite green; ran
    against the real `rooms_edited.json` and confirmed every room with both supply and
    return terminals now matches exactly (e.g. Dining 1,080/1,080, Prep 175.1/175.1).

    **Phase J — engineer-editable AH capacity + exact-sum rounding (2026-07-30) — ✅ DONE.**
    A real duct drawing showed AH-1 supply/return as 1,200/1,205 — a room whose supply
    total is 1,300 (say) but has 2+ return terminals could see 750/550 round independently
    to 750/555 instead of splitting to add up to exactly 1,300, and that drift compounds
    across every room an AH serves. Per the user: (1) the engineer should be able to type in
    what an air handler actually outputs (not just trust the catalog-match guess), and (2)
    whatever that number is, every diffuser downstream must add up to EXACTLY it, never
    approximately. New `duct_equipment` field `capacity_cfm` (blank by default) — when set,
    `size_duct_network` uses it directly as the AH's supply target instead of calling
    `match_air_handler`. New `_distribute_exact(values, target)`: scales a set of ideal
    per-terminal cfm values to a target total, floors each to the nearest 5, then hands out
    the leftover 5-cfm units to whichever terminals were closest to rounding up
    (largest-remainder method) — so the terminals always sum to EXACTLY `target` (itself
    rounded up to 5) instead of drifting by whatever each terminal's independent round-up
    added. Applied in two places: the AH supply subtree (target = override or matched
    catalog cfm), and — the actual fix for the screenshot bug — step 2c's per-ROOM return
    distribution (target = that room's own finalized supply total), since the drift was a
    same-room rounding problem, not an AH-wide one; giving the whole AH subtree a single
    blanket return target instead would have broken Phase I's "return matches supply" rule
    for rooms without their own return terminal. New editable "Capacity override (cfm)"
    number field in the AH property panel (`export_review.py`'s `ductsPanel()`), wired
    through the existing generic `.df` input handler — no dedicated JS needed. Verified: new
    `test_capacity_override_and_exact_room_sums` (override bypasses catalog match, 2 supply
    terminals sum to exactly the override, 3 return terminals in one room sum to exactly
    that room's supply) plus rewritten `test_network`/`test_supply_return_proration`
    assertions (exact-sum instead of independent-ceil magic numbers); full suite green; ran
    live in the browser against the real project — AH-1 went from 1,200/1,155 (stale saved
    state) to 1,200/1,200 after Auto-Size, then typing `1000` into the new field and
    re-running Auto-Size rescaled its two diffusers from 900/100 (previously 1080/120) to
    still sum to exactly 1,000.

    **Phase K — equal-friction sizing + fitting-loss static pressure + AH ESP matching
    (2026-07-31) — ✅ DONE.** Per the user: calculate static pressure drop through the
    ductwork and use it in sizing, not just report a CFM/size. Replaced the fixed-velocity
    sizing method entirely with the industry-standard **equal-friction** method (matches
    Design Master, the CAD tool this project's source drawings come from): one target
    friction rate (`design.friction_rate_in_wg_per_100ft`, default 0.10 in.wg/100ft, project
    setting `duct_friction_rate`) drives every duct's size instead of a fixed velocity;
    velocity (`velocity_fpm`) becomes a computed OUTPUT for the engineer to sanity-check,
    not a sizing input. New Huebscher-equation formulas in `engine/ducts.py`
    (`friction_rate`/`diameter_for_friction` forward/inverse, `rect_equivalent_diameter` +
    bisection for rectangular width) — all cited from ASHRAE Fundamentals Ch.21/32 in the
    new `equipment_data/duct_friction_reference.md` (built by a dedicated research
    subagent, independently numerically verified: 800 cfm @ target 0.10 → ~12.77in,
    round-trips back to 0.10; cross-checked separately against known ductulator convention,
    1000 cfm @ 900 fpm → 14.3in → 0.086 in.wg/100ft, right in the normal commercial range).
    New fitting-loss model, also cited (ASHRAE Duct Fitting Database): every run gets
    exactly ONE "connection" fitting at its own upstream end — AH entrance (C=0.50) if it's
    a root run, else a branch tee (`find_tee_point`, C interpolated 0.32–0.85 by
    branch/main velocity ratio) if it tees off another run, else a transition/reducer
    (C=0.20) if its final size differs from its parent's, else nothing — PLUS one elbow
    loss (C=0.22 round / 0.15 rect) for every interior vertex in its own drawn points that
    bends more than `ELBOW_ANGLE_THRESHOLD_DEG` (30°, a deliberate simplification for
    inferring real fittings from 2D centerlines). Real run length now comes from each
    floor's own `pxPerFt` (`review["floors"]`, the same scale already used for room square
    footage) instead of being unavailable to `ducts.py` at all. Per-AH
    `static_pressure_in_wg` = the worst root-to-leaf path ("index run") on the supply side
    plus the worst path on the return side, reusing the existing BFS parent/children
    structure rather than a new traversal.

    Also added ESP (external static pressure) matching: `equipment_data/air_handlers.csv`
    gained a `rated_esp_in_wg` column, researched by 5 parallel subagents (one per
    manufacturer spec sheet, split so no one agent re-fetches a PDF another already has).
    Result was a genuine finding, not just missing data: **20 of 21 units have no single
    nameplate ESP at all** — commercial packaged RTUs either ship with a field-selected
    drive/sheave package sized to the job's actual duct static, or (Trane Precedent) hold a
    duct-static SETPOINT via a VFD controller rather than being rated at one point. Only
    Rheem's 3 models yielded even a derived (not spec-sheet-stated) number, flagged
    low-confidence in the CSV's own notes. `duct_report_data`'s `esp_exceeded` flag
    (`engine/report.py`'s new Static Pressure column, bold-red when true) only ever fires
    where a real rated ESP exists — `None`/no-flag for the rest is correct, not a gap, per
    that research. **Documented scope boundary:** diffuser/coil/filter pressure drop are
    NOT modeled (no pressure-drop data in `diffuser_schedule.csv`, and equipment-internal
    drops are a manufacturer spec) — the computed number is duct-and-fitting-only, same
    "known gap, not silently wrong" pattern as item #12's air-balance report below.

    Built via one research subagent (formulas/coefficients) + 5 parallel ESP-lookup workers
    + 2 parallel build subagents (engine: `engine/ducts.py`/`engine/report.py`/
    `tests/test_ducts.py`; rendering: `export_review.py` only), run directly in the main
    checkout rather than isolated worktrees since the two build subagents' files are
    disjoint and Phase 0's uncommitted CSV/reference-doc output needed to be visible to
    them. `loadcalc.py`'s `_duct_design()` (the one shared touchpoint between the two
    subagents) was wired by hand at merge time. Verified: full `tests/test_*.py` green
    (golden_room/golden_building untouched, 0.01–0.02%); ran end-to-end against the real
    `rooms_edited.json` (AH-1: 0.1354 in.wg static pressure, matched to Carrier 48TC*A04,
    no `esp_exceeded` since that model has no rated ESP); hand-recomputed BOTH a real run's
    pressure drop (run 6: straight friction + AH-entrance + two elbows = 0.05393 in.wg,
    matched exactly) and the full AH-1 critical-path sum (supply worst-path 0.0744 +
    return worst-path 0.0611 = 0.1354, matched exactly) independently from the documented
    formulas, not just by trusting the subagents' own test suite.

    **Phase L — drags carry attachments; Shift detaches; bulk-attach diffusers
    (2026-07-31) — ✅ DONE.** Connectivity in the duct tool has always been purely
    geometric (`findTeePoint`/`nearestDuctPoint` just check point coincidence within
    1.5px/12-screen-px tolerance) and none of the three drag handlers (`ductrun` whole-body,
    `ductvertex` single-dot, `ductpoint` terminal/equipment) knew about anything else
    touching the thing being dragged — any drag silently broke whatever it used to touch.
    Per the user: dragging a run or a diffuser should carry along whatever's currently
    attached to it, no matter how far; an explicit gesture should detach on purpose.
    New `runAttachments(run)` (everything currently coincident with any point along a
    run — terminals, equipment, other runs' endpoints, broader than `findTeePoint`'s
    mid-span-only check, matching `engine/ducts.py`'s `_runs_connect`) and `vertexUnder(pt)`
    (the inverse — does a point sit on an existing run vertex). Whole-run body drags now
    translate every attached entry by the same (dx,dy) each frame; terminal/equipment point
    drags now also move whichever run vertex they're sitting on to track them. Holding
    **Shift** at drag start skips this entirely for that drag, so the dragged item moves
    alone and — since attachment is purely geometric — immediately detaches in both
    directions (nothing follows it, and if it was itself touching something upstream, that
    breaks too). Deliberately caps at one level deep: dragging a trunk moves an attached
    branch's near end, but the branch isn't rigidly translating, so nothing attached to
    THAT branch follows — by design, not a gap. Also new: multi-select diffusers (now
    Ctrl/Cmd-click, not Shift-click — a subagent-caught conflict, since Shift-click on a
    terminal would otherwise swallow the shift-drag-to-detach gesture for that one entity
    type; Ctrl/Cmd is also the more conventional toggle-multi-select modifier anyway) then
    click a target run to auto-draw a straight stub from the nearest point on that run to
    each selected diffuser (`bulkAttachTo`), skipping and toast-reporting any supply/return
    system mismatch. New stubs use `form:'auto'`, so `findTeePoint`/`effectiveForm` classify
    them as real flex branches with zero extra engine-side work. `export_review.py` only —
    no engine/report/test changes, since coincidence-based attachment means the Python side
    needs nothing new as long as drags preserve or break coincidence exactly. Built via one
    implementation subagent working directly in the main checkout (uncommitted Phase K work
    already in this same file); verified myself afterward with real dispatched
    `MouseEvent`s (mousedown/mousemove/mouseup) against the actual page functions, not
    screenshot-pixel clicking (unreliable on this small embedded canvas) or reimplemented
    logic: trunk drag carries a branch's near end + terminal but doesn't cascade to a
    grandchild; branch-body drag carries its own terminal while detaching from the trunk it
    was teeing off; Shift+drag on a run/terminal/equipment each correctly detach; Ctrl-click
    three diffusers (two supply, one return) + click a supply run correctly created two
    stubs, correctly classified `flex` via `findTeePoint`, and correctly skipped/toasted the
    return one ("Attached 2 diffusers. 1 skipped..."). No console errors at any step.
12. **Air balance / building pressurization report — ✅ DONE.** New report section: checks that the building is never net-negative pressure (outdoor humid air being drawn in through every crack instead of kept out — a real moisture-intrusion risk in FL, not just a comfort issue). The insight that made this tractable: at the BUILDING level, only designed airflows that actually cross the envelope matter — ventilation OA + exhaust-makeup OA (in) vs. total exhaust (out, the *full* exhaust CFM regardless of how much is made up — the fan pulls all of it). Transfer air (the non-makeup portion of exhaust) is a wash at the building level since it moves *within* the building rather than crossing the envelope, so it doesn't need to be modeled as an explicit path between rooms — no new data beyond what item #9 already added. `compute_building` returns a new `air_balance` dict: building totals (Ventilation OA / Exhaust Makeup OA / Total OA In / Exhaust Out / **Net CFM**, negative = under-pressured), the same breakdown per zone (reuses the existing zone grouping), and per room. Infiltration is deliberately excluded from the balance — it's a *symptom* of imbalance, not a reliable designed air source. A room can and often should show net-negative on its own (e.g. a kitchen counting on transfer air from dining) — that's flagged as expected/informational, not a problem; only the BUILDING total is the actual pass/fail check. Verified with a kitchen (30% dedicated makeup, -196 CFM alone) + dining (300 CFM ventilation) pair: building nets to +104 CFM (correctly not flagged) because dining's ventilation air covers both its own need and the kitchen's transfer requirement; a second case with too little dining ventilation (50 CFM) correctly nets to -146 CFM and flags **⚠ NEGATIVE**. **Known gap, disclosed in the report itself:** only exhaust the engineer actually enters counts — a fume hood, dryer vent, or other exhaust source not entered as `exh_cfm` on some room won't be caught by this check.

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
