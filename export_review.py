"""Generate a self-contained review UI (review.html) seeded with the parser's
rooms over the rendered floor plan. Open the file in a browser to add / delete /
move / resize / relabel rooms with live area readouts.

    python3 export_review.py "sample_drawings/Sample A plan (MECH).pdf" 18.0 [min_len] [page]
"""
import base64
import glob
import json
import re
import sys
from pathlib import Path
import fitz
from parser.pdf_extractor import extract_page
from parser.wall_detector import detect_walls
from parser.room_builder import build_room_polygons

_TYPE_KEYS = ("room_types", "wall_types", "roof_types", "floor_types",
              "door_types", "glass_types", "project_settings")


def _logo_html():
    """Adicot wordmark for the top bar, embedded as base64 (keeps review.html
    self-contained — no external file reference to break when the file moves).
    Falls back to plain text if the logo file isn't present in this checkout."""
    logo_path = Path(__file__).resolve().parent / "adicot logo.jpg"
    try:
        b64 = base64.b64encode(logo_path.read_bytes()).decode()
        return f'<img class="logo" src="data:image/jpeg;base64,{b64}" alt="Adicot">'
    except Exception:
        return "<b>Room Review</b>"


_LOGO_HTML = _logo_html()


def find_types_json():
    """Look in the script's folder for a load-calc types file dropped in from the
    Adicot site (loadcalc_input.json or any *loadcalc*.json). Returns (dict, name)
    or (None, None). This is what makes the room-type / construction palettes and
    the per-room load seeding come alive instead of using placeholder defaults."""
    here = Path(__file__).resolve().parent
    seen, cands = set(), []
    for p in [here / "loadcalc_input.json", *(Path(x) for x in glob.glob(str(here / "*loadcalc*.json")))]:
        if p.exists() and p not in seen:
            seen.add(p); cands.append(p)
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)   # newest drop wins
    for p in cands:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if any(k in d for k in _TYPE_KEYS):
            return d, p.name
    return None, None


def ext_coords(g, rm):
    # rm = page.rotation_matrix: the parser works in unrotated page space, but the
    # rendered image is upright (rotated), so map each vertex into image space.
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    g = g.simplify(2.0, preserve_topology=True)
    xs, ys = g.exterior.coords.xy
    pts = []
    for x, y in zip(xs, ys):
        q = fitz.Point(x, y) * rm
        pts.append([round(q.x, 1), round(q.y, 1)])
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def export(pdf, ppf, min_len=1.0, pages=(0,), out="review.html", labels=None):
    """pages = one page number per building floor (page 1 = floor 1, page 2 = floor
    2, etc — the common case of one multi-page plan set). A bare int is also
    accepted for the single-floor case. ppf is either one number (applied to every
    floor) or a list matching `pages`, since different sheets can be drawn at
    different scales. Each floor gets its own rooms/openings/scale/north in the
    review UI; `rooms`/`openings` in the saved JSON stay flat lists (each room
    tagged with its `level`) so engine/compute.py can sum across floors the same
    way it already does across zones."""
    if isinstance(pages, int):
        pages = (pages,)
    ppfs = list(ppf) if isinstance(ppf, (list, tuple)) else [ppf] * len(pages)
    if len(ppfs) != len(pages):
        raise ValueError(f"got {len(ppfs)} ppf values for {len(pages)} pages — "
                         "pass one ppf for all floors, or one per page")
    labels = list(labels) if labels else [f"Floor {i + 1}" for i in range(len(pages))]

    floors, rdata, next_id = [], [], 0
    doc = fitz.open(pdf)
    for i, (page_no, page_ppf, label) in enumerate(zip(pages, ppfs, labels)):
        level = i + 1
        raw = extract_page(pdf, page_no)
        walls = detect_walls(raw["lines"], page_ppf, min_len_ft=min_len)
        rooms = build_room_polygons(walls, page_ppf)

        page = doc[page_no]
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        img_uri = "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()
        rm = page.rotation_matrix

        for g in sorted(rooms, key=lambda r: -r.area):
            rdata.append({"id": next_id, "label": f"ROOM {next_id}", "type": "",
                          "level": level, "points": ext_coords(g, rm)})
            next_id += 1
        floors.append({"level": level, "label": label, "image": img_uri,
                       "pageW": page.rect.width, "pageH": page.rect.height,
                       "pxPerFt": page_ppf, "north_deg": 0})
    doc.close()

    # Carried through to buildPayload() -> the saved JSON's filename, so the site's
    # "Save JSON" / "Run Load Calc" output is named after the PDF instead of always
    # clobbering a fixed rooms_edited.json across different projects.
    data = {"floors": floors, "rooms": rdata, "pdf_stem": Path(pdf).stem}

    # Pull in a types file (room/wall/roof/... definitions) if the site's JSON
    # was dropped in the folder — this fills the dropdowns + seeds room loads.
    types, tname = find_types_json()
    if types:
        for k in _TYPE_KEYS:
            if k in types:
                data[k] = types[k]
        print(f"loaded types from {tname}: {len(types.get('room_types', []))} room, "
              f"{len(types.get('wall_types', []))} wall, {len(types.get('roof_types', []))} roof, "
              f"{len(types.get('glass_types', []))} glass, {len(types.get('door_types', []))} door")
    else:
        print("no *loadcalc*.json in folder — using seeded default types")

    html = HTML.replace("/*__DATA__*/", json.dumps(data)).replace("<!--__LOGO__-->", _LOGO_HTML)
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out}  rooms={len(rdata)}  size={len(html)//1024} KB")


def resume(rooms_edited_path, existing_html="review.html", out="review.html"):
    """Reopen a previously saved rooms_edited.json in the review UI, without
    re-parsing the PDF — reuses the plan image + page size already embedded in
    `existing_html` from the last `parse`, and overlays the saved rooms,
    openings, north orientation, and type libraries on top."""
    p = Path(existing_html)
    if not p.exists():
        raise FileNotFoundError(
            f"{existing_html} not found — run 'python3 loadcalc.py parse <plans.pdf>' "
            "at least once first, so there's a plan image to resume into.")
    m = re.search(r"const REVIEW_DATA = (.*?);</script>", p.read_text(), re.S)
    if not m:
        raise ValueError(f"couldn't find embedded REVIEW_DATA in {existing_html}")
    base = json.loads(m.group(1))

    saved = json.loads(Path(rooms_edited_path).read_text())

    # Floors: the plan image/page size only ever live in `base` (the last parse's
    # embedded review.html) — buildPayload() never saves images, to keep
    # rooms_edited.json lean. So merge per floor: image/pageW/pageH always from
    # base, but pxPerFt/label/north_deg prefer whatever the engineer saved (their
    # scale-match / North-arrow edits), matched up by `level`.
    base_floors = {f["level"]: f for f in base.get("floors", [])}
    if "floors" in saved:
        data = {"floors": [
            {"level": sf["level"],
             "label": sf.get("label", base_floors.get(sf["level"], {}).get("label", f"Floor {sf['level']}")),
             "image": base_floors.get(sf["level"], {}).get("image"),
             "pageW": base_floors.get(sf["level"], {}).get("pageW"),
             "pageH": base_floors.get(sf["level"], {}).get("pageH"),
             "pxPerFt": sf.get("pxPerFt"), "north_deg": sf.get("north_deg", 0)}
            for sf in saved["floors"]]}
    elif base.get("floors"):
        data = {"floors": base["floors"]}
    else:
        # Both sides predate multi-floor — synthesize a single Floor 1 from the old
        # flat image/pxPerFt/north_deg fields.
        data = {"floors": [{"level": 1, "label": "Floor 1",
                            "image": base.get("image"), "pageW": base.get("pageW"), "pageH": base.get("pageH"),
                            "pxPerFt": saved.get("pxPerFt", base.get("pxPerFt")),
                            "north_deg": saved.get("north_deg", base.get("north_deg", 0))}]}

    for k in ("rooms", "openings", "wall_types", "roof_types",
              "floor_types", "glass_types", "door_types", "room_types", "zones",
              "safety_factors", "project_settings", "pdf_stem",
              "duct_runs", "duct_terminals", "duct_equipment"):
        if k in saved:
            data[k] = saved[k]
        elif k in base:
            data[k] = base[k]

    # A room (or duct entity) saved before multi-floor existed has no `level` at
    # all — it belongs to (the only) Floor 1.
    if "rooms" in data:
        for r in data["rooms"]:
            r.setdefault("level", 1)
    for k in ("duct_runs", "duct_terminals", "duct_equipment"):
        if k in data:
            for d in data[k]:
                d.setdefault("level", 1)

    # room_types saved before the fix for the seedLoads/RT_REC bug are bare name
    # strings, not the full DM record (lighting/equipment/people/etc.) that loads
    # actually seed from — repair them here by matching against the freshly
    # parsed types file's records (still full), so an old save doesn't
    # permanently lose the ability to auto-seed loads on room-type assignment.
    if "room_types" in data:
        base_by_name = {t["name"]: t for t in (base.get("room_types") or []) if isinstance(t, dict)}
        data["room_types"] = [base_by_name.get(rt, {"name": rt}) if isinstance(rt, str) else rt
                              for rt in data["room_types"]]

    html = HTML.replace("/*__DATA__*/", json.dumps(data)).replace("<!--__LOGO__-->", _LOGO_HTML)
    with open(out, "w") as f:
        f.write(html)
    print(f"resumed {out}  rooms={len(data.get('rooms', []))}  size={len(html)//1024} KB")


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Load Calc — Room Review</title>
<style>
  :root {
    --ink:#111827; --ink-soft:#475569; --ink-muted:#6b7280; --ink-faint:#cbd5e1;
    --surface:#ffffff; --surface-alt:#f3f4f6; --surface-soft:#f8fafc;
    --surface-tint:#eff6ff; --surface-tint-2:#dbeafe; --surface-subtle:#f1f5f9;
    --border:#e5e7eb; --border-soft:#f1f5f9; --input-border:#d1d5db;
    --bar-bg:#1f2937; --bar-text:#ffffff; --btn-bg:#374151; --btn-bg-hover:#4b5563;
    --accent:#2563eb; --success:#059669; --success-hover:#047857;
    --danger-bg:#fee2e2; --danger-text:#b91c1c; --danger:#ef4444;
    --warn-bg:#fffbeb; --warn-text:#92400e; --warn-border:#fde68a;
    --tab-bg:#eef2f7;
  }
  html[data-theme="dark"] {
    --ink:#e5e7eb; --ink-soft:#cbd5e1; --ink-muted:#94a3b8; --ink-faint:#64748b;
    --surface:#111827; --surface-alt:#0b1220; --surface-soft:#1e293b;
    --surface-tint:#1e3a5f; --surface-tint-2:#1d4ed8; --surface-subtle:#1e293b;
    --border:#1f2937; --border-soft:#1e293b; --input-border:#334155;
    --bar-bg:#0b1220; --bar-text:#e5e7eb; --btn-bg:#1e293b; --btn-bg-hover:#334155;
    --accent:#3b82f6; --success:#10b981; --success-hover:#34d399;
    --danger-bg:#3f1d1d; --danger-text:#fca5a5; --danger:#f87171;
    --warn-bg:#3f2d0f; --warn-text:#fbbf24; --warn-border:#78350f;
    --tab-bg:#1e293b;
  }
  * { box-sizing: border-box; }
  html,body { margin:0; height:100%; font-family: -apple-system, system-ui, sans-serif; background:var(--surface); color:var(--ink); }
  #app { display:flex; flex-direction:column; height:100vh; }
  #bar { display:flex; align-items:center; gap:8px; padding:8px 12px; background:var(--bar-bg); color:var(--bar-text); }
  #bar b { font-size:15px; margin-right:10px; }
  #bar .logo { height:26px; margin-right:10px; display:block; }
  #bar button { background:var(--btn-bg); color:var(--bar-text); border:0; padding:7px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  #bar button:hover { background:var(--btn-bg-hover); }
  #bar button.on { background:var(--accent); }
  #bar button.primary { background:var(--success); }
  #bar button.primary:hover { background:var(--success-hover); }
  #bar button:disabled { opacity:.6; cursor:default; }
  #bar .sp { flex:1; }
  #bar .stat { font-variant-numeric:tabular-nums; opacity:.9; font-size:13px; }
  #bar .ruler-stat { font-variant-numeric:tabular-nums; font-size:13px; color:var(--warn-border); margin-right:4px; }
  #bar .theme-toggle { background:transparent; border:1px solid var(--btn-bg-hover); font-size:15px; padding:5px 9px; }
  #main { flex:1; display:flex; min-height:0; overflow:hidden; }
  #cv { flex:1 1 0; min-width:0; min-height:0; background:var(--surface-alt); cursor:grab; }
  #cv.draw { cursor:crosshair; }
  #cv.measure { cursor:crosshair; }
  #side { width:380px; flex:0 0 380px; background:var(--surface); color:var(--ink); border-left:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; }
  #side .selblock { padding:14px; flex:0 0 auto; overflow:auto; max-height:55%; }
  #side .listblock { padding:0 14px 14px; flex:1 1 auto; min-height:160px; overflow:auto; border-top:1px solid var(--border); }
  #side .listblock h3 { position:sticky; top:0; background:var(--surface); padding-top:12px; margin-top:0; }
  #side h3 { margin:0 0 8px; font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink-muted); }
  #side label { display:block; font-size:12px; color:var(--ink-muted); margin:10px 0 3px; }
  #side input, #side select { width:100%; padding:7px; border:1px solid var(--input-border); border-radius:6px; font-size:14px; background:var(--surface); color:var(--ink); }
  #side .area { font-size:26px; font-weight:600; margin:6px 0; }
  #side .danger { background:var(--danger-bg); color:var(--danger-text); border:0; padding:8px; width:100%; border-radius:6px; cursor:pointer; margin-top:12px; }
  #list { margin-top:8px; }
  #list .row { display:flex; justify-content:space-between; padding:8px; border-radius:6px; cursor:pointer; font-size:13px; }
  #list .row:hover { background:var(--surface-alt); }
  #list .row.sel { background:var(--surface-tint-2); }
  #hint { padding:8px 12px; background:var(--warn-bg); color:var(--warn-text); font-size:13px; border-top:1px solid var(--warn-border); display:none; }
  #hint.show { display:block; }
  #cv.paint { cursor:crosshair; }
  .tabs { display:flex; gap:6px; padding:10px 14px 0; flex:0 0 auto; }
  .tab { flex:1; padding:8px; border:0; background:var(--tab-bg); border-radius:7px 7px 0 0; cursor:pointer; font-size:13px; font-weight:600; color:var(--ink-soft); }
  .tab.on { background:var(--bar-bg); color:var(--bar-text); }
  .types { display:flex; flex-direction:column; gap:5px; margin:6px 0 10px; }
  .chip { display:flex; align-items:center; gap:8px; padding:7px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; font-size:12.5px; }
  .chip:hover { background:var(--surface-soft); }
  .chip.on { border-color:var(--accent); background:var(--surface-tint); box-shadow:inset 0 0 0 1px var(--accent); }
  .chip .sw { width:14px; height:14px; border-radius:4px; flex:0 0 auto; }
  .chip .nm { flex:1; }
  .chip .u { color:var(--ink-muted); font-variant-numeric:tabular-nums; }
  .chip .edit { color:var(--ink-faint); cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .edit:hover { color:var(--accent); }
  .chip .del { color:var(--ink-faint); cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .del:hover { color:var(--danger); }
  .chip .copy { color:var(--ink-faint); cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .copy:hover { color:var(--success); }
  .con-add { background:var(--surface-subtle); border:1px dashed var(--ink-faint); color:var(--ink-soft); padding:7px; width:100%; border-radius:8px; cursor:pointer; font-size:12.5px; margin-bottom:14px; }
  .type-form { border:1px solid var(--accent); border-radius:8px; padding:8px 10px; margin-bottom:14px; background:var(--surface-tint); }
  .type-form .loadgrid { margin:4px 0 8px; }
  .type-form .tf-row { display:flex; gap:8px; margin-top:4px; }
  .type-form .tf-save { flex:1; background:var(--accent); color:#fff; border:0; padding:8px; border-radius:6px; cursor:pointer; font-size:12.5px; }
  .type-form .tf-cancel { flex:1; background:var(--border); color:var(--ink-soft); border:0; padding:8px; border-radius:6px; cursor:pointer; font-size:12.5px; }
  .loadgrid { display:grid; grid-template-columns:1fr 92px; gap:5px 8px; align-items:center; margin:10px 0; }
  .loadgrid label { margin:0; font-size:11.5px; color:var(--ink-soft); }
  .loadgrid input { padding:5px 6px; }
  #bar .north { font-size:12px; color:var(--ink-faint); }
  #bar .north input { width:52px; padding:4px; border:0; border-radius:5px; margin-left:2px; background:var(--surface); color:var(--ink); }
  #bar .north select { padding:4px; border:0; border-radius:5px; margin-left:2px; background:var(--surface); color:var(--ink); }
  .walls-list { margin:10px 0; font-size:11.5px; }
  .walls-list .wl-h { color:var(--ink-muted); margin-bottom:3px; }
  .walls-list .wl-row { display:grid; grid-template-columns:36px 1fr 52px; gap:6px; padding:2px 0; border-top:1px solid var(--border-soft); }
  .filter-box { width:100%; padding:6px 8px; margin:4px 0 8px; border:1px solid var(--input-border); border-radius:6px; font-size:12.5px; background:var(--surface); color:var(--ink); }
  .legend { font-size:11px; color:var(--ink-muted); padding:8px 12px; border-top:1px solid var(--border); }
  .legend summary { cursor:pointer; color:var(--ink-soft); font-weight:600; }
  .legend .lg-row { display:flex; align-items:center; gap:6px; margin:4px 0 0 2px; }
  .legend .lg-sw { width:10px; height:10px; border-radius:3px; flex:0 0 auto; }
  .toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:var(--bar-bg); color:var(--bar-text);
    padding:10px 16px; border-radius:8px; font-size:13px; box-shadow:0 4px 16px rgba(0,0,0,.25); z-index:50;
    opacity:0; pointer-events:none; transition:opacity .15s; max-width:520px; text-align:center; white-space:pre-line; }
  .toast.show { opacity:1; pointer-events:auto; cursor:pointer; }
  .toast.err { background:var(--danger-text); }
</style></head>
<body><div id="app">
  <div id="bar">
    <!--__LOGO__-->
    <span class="north" title="Switch which floor's plan you're viewing/editing. Each floor keeps its own rooms, drawing scale, and North setting.">Floor <select id="bFloor"></select></span>
    <button id="bAdd">+ Add Room</button>
    <button id="bDel">Delete</button>
    <button id="bFit">Fit</button>
    <button id="bMeasure">📏 Measure</button>
    <span class="north">North° <input id="bNorth" type="number" step="1" value="0"></span>
    <span class="north" title="Recalibrates THIS FLOOR's drawing scale so its rooms drawn so far add up to this — enter this floor's actual total square footage, not a guess.">Total ft² <input id="bTotalSqft" type="number" step="1" placeholder="e.g. 12500"></span>
    <button id="bMatchSqft">Match</button>
    <button id="bSave">Save JSON</button>
    <button id="bRunCalc" class="primary">▶ Run Load Calc</button>
    <span class="sp"></span>
    <span class="ruler-stat" id="rulerStat"></span>
    <span class="stat" id="stat"></span>
    <button class="theme-toggle" id="bTheme" title="Toggle light/dark theme">🌙</button>
    <button class="theme-toggle" id="bDuctView" title="Toggle duct centerline markup: Engineer view shows supply(blue)/return(orange) centerlines; Client view hides them for a submittal-ready drawing">🔧 Engineer</button>
  </div>
  <div id="main">
    <canvas id="cv"></canvas>
    <div id="side"></div>
  </div>
  <div id="hint"></div>
  <details class="legend">
    <summary>What do the colors mean?</summary>
    <div class="lg-row">Room fill tint = its assigned <b>Roof type</b>'s color (matches that type's swatch in the Construction tab)</div>
    <div class="lg-row">Wall edge color = its assigned <b>Wall type</b>'s color; a thin faint line = "No wall load"</div>
    <div class="lg-row"><span class="lg-sw" style="background:#0284c7"></span> Window opening</div>
    <div class="lg-row"><span class="lg-sw" style="background:#a16207"></span> Door opening</div>
    <div class="lg-row"><span class="lg-sw" style="background:#7c3aed"></span> ◆ Zone label (below the room name)</div>
    <div class="lg-row">▲ Roof label &nbsp;·&nbsp; ▼ Floor label (shown when assigned, below the room name)</div>
    <div class="lg-row">Tip: every type's swatch color in the <b>Construction</b> tab is exactly what you'll see drawn on the plan.</div>
  </details>
  <details class="legend">
    <summary>Duct / HVAC Symbols</summary>
    <div class="lg-row"><b>Ductwork</b> (black — matches the plan's own line weight, same as a real submittal set)</div>
    <div class="lg-row">Double-line outline = Rigid trunk (rect, sized W×H) or a Round duct (sized by diameter) — both true to scale, labeled with size (e.g. "18X18" or "10Ø")</div>
    <div class="lg-row">Corrugated/coiled outline = Flexible insulated branch, labeled with diameter + CFM (e.g. "6Ø · 120 cfm")</div>
    <div class="lg-row">Rect/Round/Flex is figured out automatically from how runs connect — a run stays rect unless it tees off another run's middle, which makes it a flex branch with a plain round collar (no taper) at the takeoff, like a real spin-in fitting. Select a run — edit it exactly like a room: drag a dot to move it, alt/right-click a dot to delete it, click an orange + at a midpoint to add one, drag the body to move the whole run. Shift-click an interior dot to split the run into two independent pieces, then force either piece's Form (Auto/Rect/Round/Flex) in the side panel if the auto result isn't what you want.</div>
    <div class="lg-row">The dot you last clicked/dragged turns solid blue — that's the pivot for <b>Straighten</b> (like DM's Next/Previous tool): the <kbd>[</kbd>/<kbd>]</kbd> keys (or the buttons in the side panel) snap the previous/next segment to whichever is closer — running straight on (0°/180°) or turning a clean corner (±90°) — off the other one, pivoting at that dot. Pivoting at one of a run's two END dots (where it's otherwise got nothing of its own to compare against) instead straightens that run against whatever OTHER run it's connected to there — e.g. squaring a branch up to the trunk it tees off of.</div>
    <div class="lg-row"><b>Diffusers &amp; Grilles</b></div>
    <div class="lg-row">▢ Ceiling supply diffuser &nbsp;·&nbsp; ◯ Ceiling return &nbsp;·&nbsp; → Sidewall supply/return (blue outline = supply, green = return)</div>
    <div class="lg-row"><b>Equipment</b></div>
    <div class="lg-row">▢ Air Handler (AH-#) &nbsp;·&nbsp; ▢ Dehumidifier (DH-#) &nbsp;·&nbsp; ◯ Condensing Unit (CU-#)</div>
    <div class="lg-row"><b>Engineer markup (toggle with the 🔧/📄 button in the top bar)</b></div>
    <div class="lg-row"><span class="lg-sw" style="background:#1d4ed8"></span> Supply duct centerline &nbsp;·&nbsp; <span class="lg-sw" style="background:#f97316"></span> Return duct centerline — engineer-only, hidden in Client view for a clean submittal drawing</div>
    <div class="lg-row"><b>Keyboard shortcuts</b> (focus the canvas first)</div>
    <div class="lg-row">1-2 = draw a run (Supply/Return) &nbsp;·&nbsp; Shift+1-4 = place a terminal (Ceil Sup/Ret, Sidewall Sup/Ret) &nbsp;·&nbsp; Alt+1-3 = place equipment (AH/CU/DH)</div>
    <div class="lg-row">While drawing a run: Enter/Esc stops; double-click finishes a run and stays armed for the next one; points snap to nearby network points, otherwise to 45°</div>
  </details>
  <div id="toast" class="toast"></div>
</div>
<script>const REVIEW_DATA = /*__DATA__*/;</script>
<script>
const D = REVIEW_DATA;
const PDF_STEM = D.pdf_stem || null;   // the dropped PDF's filename (no extension) — names the saved JSON after it

// Theme: persisted in localStorage (per-browser, not saved into rooms_edited.json —
// a display preference, not project data). Applied on <html> so :root variables cascade
// everywhere, including the side-panel forms.
function applyTheme(t){ document.documentElement.setAttribute('data-theme', t);
  const b=document.getElementById('bTheme'); if(b) b.textContent = t==='dark' ? '☀️' : '🌙'; }
applyTheme(localStorage.getItem('loadcalc-theme') || 'light');

// Inline toast, replacing native alert() popups (which looked out of place next to the
// rest of this UI's inline forms). Errors stay up longer and are click-to-dismiss, since
// some of them are multi-line troubleshooting text the engineer needs time to read.
let toastTimer=null;
function showToast(msg, isErr){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show'+(isErr?' err':'');
  clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>{ t.className='toast'; }, isErr?8000:2200);
}
document.getElementById('toast').onclick=()=>{ clearTimeout(toastTimer); document.getElementById('toast').className='toast'; };

// ── Multi-floor state ────────────────────────────────────────────────────────
// Each floor keeps its own plan image/scale/north AND its own rooms/openings.
// `rooms`/`openings`/`PPF`/`northDeg` (below) are always THE ACTIVE FLOOR'S live,
// mutable working copies — every existing selection/paint/drag/measure/draw
// function in this file reads/writes those same names exactly as it did back when
// there was only ever one floor, completely unmodified. switchFloor() just stashes
// the outgoing floor's copies into floorRoomsMap/floorOpeningsMap and loads the
// incoming floor's — a "swap the working set" pattern instead of threading a floor
// filter through every interaction/hit-test in the file (which is where an invisible
// other-floor room could accidentally get clicked/painted if pixel coordinates from
// two different page scales happened to overlap).
let floorMeta = (D.floors && D.floors.length ? D.floors
  : [{level:1, label:'Floor 1', image:D.image, pageW:D.pageW, pageH:D.pageH, pxPerFt:D.pxPerFt, north_deg:D.north_deg||0}]
  ).map(f=>Object.assign({}, f));
let currentFloorIdx = 0;

const _allRooms = (D.rooms||[]).map(r => ({id:r.id, label:r.label, type:r.type||"", zone:r.zone||"",
  level:(r.level!=null?r.level:1), points:r.points.map(p=>[p[0],p[1]]),
  roof:r.roof||"No roof load", floor:r.floor||"No floor load", ceiling_ft:(r.ceiling_ft!=null?r.ceiling_ft:''),
  floor_to_floor_ft:(r.floor_to_floor_ft!=null?r.floor_to_floor_ft:''), carpet:(r.carpet!==false), loads:r.loads||{},
  walls:(r.walls && r.walls.length===r.points.length) ? r.walls.slice() : r.points.map(function(){return "No wall load";})}));
let nextId = _allRooms.reduce((m,r)=>Math.max(m,r.id),-1)+1;
const _allOpenings = (D.openings||[]).map(o=>Object.assign({},o));
let opId = _allOpenings.reduce((m,o)=>Math.max(m,o.id),-1)+1;

// Duct entities — same shape of per-floor bookkeeping as rooms/openings above, not a
// wall-attached thing like openings, so each carries its own `level` directly (no need
// to look it up via a parent room).
// A run saved before rect/round/flex became auto-classified has a flat `kind` string
// (rigid_supply/rigid_return/flex_supply/flex_return) instead of separate `system`/`form`
// fields — migrate once on load, preserving whatever was explicitly drawn before (never
// silently reclassifies old data as 'auto').
const _allDuctRuns = (D.duct_runs||[]).map(d=>{
  const r = Object.assign({level:(d.level!=null?d.level:1)}, d);
  if(r.system==null && r.kind!=null){ r.system = r.kind.indexOf('supply')>=0 ? 'supply' : 'return'; r.form = r.kind.indexOf('rigid')===0 ? 'rect' : 'flex'; delete r.kind; }
  if(r.form==null) r.form = 'auto';
  if(r.auto==null) r.auto = true;   // 📐 Auto-Size Ducts only overwrites cfm/size where this is true
  return r;
});
let nextDuctRunId = _allDuctRuns.reduce((m,d)=>Math.max(m,d.id),-1)+1;
const _allDuctTerminals = (D.duct_terminals||[]).map(t=>Object.assign({level:(t.level!=null?t.level:1), auto:true}, t));
let nextTerminalId = _allDuctTerminals.reduce((m,t)=>Math.max(m,t.id),-1)+1;
const _allDuctEquipment = (D.duct_equipment||[]).map(e=>Object.assign({level:(e.level!=null?e.level:1)}, e));
let nextEquipId = _allDuctEquipment.reduce((m,e)=>Math.max(m,e.id),-1)+1;

const floorRoomsMap = new Map(), floorOpeningsMap = new Map();
const floorDuctRunsMap = new Map(), floorTerminalsMap = new Map(), floorEquipmentMap = new Map();
floorMeta.forEach(f=>{ floorRoomsMap.set(f.level, []); floorOpeningsMap.set(f.level, []);
  floorDuctRunsMap.set(f.level, []); floorTerminalsMap.set(f.level, []); floorEquipmentMap.set(f.level, []); });
_allRooms.forEach(r=>{ if(!floorRoomsMap.has(r.level)) floorRoomsMap.set(r.level, []); floorRoomsMap.get(r.level).push(r); });
const _roomLevelById = new Map(_allRooms.map(r=>[r.id, r.level]));
_allOpenings.forEach(o=>{ const lvl=_roomLevelById.get(o.rid); if(lvl==null) return;
  if(!floorOpeningsMap.has(lvl)) floorOpeningsMap.set(lvl, []); floorOpeningsMap.get(lvl).push(o); });
_allDuctRuns.forEach(d=>{ if(!floorDuctRunsMap.has(d.level)) floorDuctRunsMap.set(d.level, []); floorDuctRunsMap.get(d.level).push(d); });
_allDuctTerminals.forEach(t=>{ if(!floorTerminalsMap.has(t.level)) floorTerminalsMap.set(t.level, []); floorTerminalsMap.get(t.level).push(t); });
_allDuctEquipment.forEach(e=>{ if(!floorEquipmentMap.has(e.level)) floorEquipmentMap.set(e.level, []); floorEquipmentMap.get(e.level).push(e); });

// Every duct run/terminal/equipment across ALL floors, live object references (not the
// current floor's working array) — 📐 Auto-Size Ducts' server response can touch any
// floor's entities, not just the one currently being viewed.
function allDuctRunsLive(){ return [].concat(...floorDuctRunsMap.values()); }
function allDuctTerminalsLive(){ return [].concat(...floorTerminalsMap.values()); }
function allDuctEquipmentLive(){ return [].concat(...floorEquipmentMap.values()); }

let rooms = floorRoomsMap.get(floorMeta[currentFloorIdx].level) || [];
let openings = floorOpeningsMap.get(floorMeta[currentFloorIdx].level) || [];
let ductRuns = floorDuctRunsMap.get(floorMeta[currentFloorIdx].level) || [];
let ductTerminals = floorTerminalsMap.get(floorMeta[currentFloorIdx].level) || [];
let ductEquipment = floorEquipmentMap.get(floorMeta[currentFloorIdx].level) || [];
let PPF = floorMeta[currentFloorIdx].pxPerFt;   // reassignable — "Match total ft²" recalibrates this after drawing

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const img = new Image();
let view = {zoom:1, panX:0, panY:0}, selected=null, mode='select', draft=[], drag=null, VW=0, VH=0;
let ruler=null;   // {a:[x,y], b:[x,y]} in world (px) coords — last/active measurement, or null
let lastSavedJSON=null;   // buildPayload() snapshot as of the last save — set once at load, below
let tab='rooms', paintCat=null, activeType=null, hoverEdge=null, northDeg=(floorMeta[currentFloorIdx].north_deg||0);   // Construction paint state; northDeg = ° CW from screen-up to true north (active floor's)
// Duct-drawing hover state: hoverDuctSnap = a nearby existing network point the next click
// would snap onto (terminal/equipment placement or a duct-draft point); ductPreviewPt = the
// next duct-draft point's would-be location (post point-snap/angle-snap), rubber-banded
// from the last placed point so the angle constraint is visible before clicking.
let hoverDuctSnap=null, ductPreviewPt=null;
// 'engineer' shows the supply/return centerline markup (see drawDuctCenterline); 'client'
// hides it — for a submittal-ready view, since those centerlines are engineer notation,
// not part of the actual duct drawing. Persisted like the theme (a display preference).
let ductViewMode = localStorage.getItem('loadcalc-ductview')==='client' ? 'client' : 'engineer';
// Inline type add/edit form state — {cat, name} of the type being edited (name=null means
// "adding a new one in this cat"), or null when no form is open. Replaces the old one-
// field-at-a-time prompt() chain for wall/roof/floor/window/door types with a single form
// showing every field at once (rtype/zone still use the simple name-only prompt below,
// since they have nothing else to edit here).
let editingType = null;
// Project-wide design-margin knobs (DM's own report has these; we had none). Room = envelope +
// internal gains, Vent = ventilation/infiltration/exhaust-makeup outdoor air — kept separate
// because DM applies them separately and compute.py itemizes them the same way. Defaults
// (5/0/10/0 — must match engine/compute.py's DEFAULT_SAFETY_FACTORS) are pre-filled, not
// zero; the report footnotes the rationale for whichever of these the engineer leaves as-is.
const SF = Object.assign({clg_room:5, clg_vent:0, htg_room:10, htg_vent:0}, D.safety_factors || {});
// Weather station / design-condition inputs (loadcalc.py's _weather() reads these keys
// straight off project_settings). Embedded into REVIEW_DATA on first parse from the
// DM-setup JSON, but until now nothing in this UI read, displayed, or saved them back —
// invisible and unrecoverable here after the first Save (the calc itself still worked,
// since loadcalc.py reads loadcalc_input.json directly, bypassing whatever the UI saved).
let PS = Object.assign({}, D.project_settings || {});
// Room-type library (editable in the Construction tab). Seeded from the DM-setup
// JSON (D.room_types, {name,...} objects) OR a resumed rooms_edited.json
// (D.room_types, flat name strings) when present, else a default commercial/dental list.
let ROOM_TYPE_LIB = (D.room_types&&D.room_types.length) ? D.room_types.map(rt=>typeof rt==='object'?rt.name:rt)
  : ["Operatory","Exam","Sterilization","Lab","Reception","Waiting","Office","Consult",
     "Break Room","Restroom","Corridor","Storage","Mechanical Room","Server Room",
     "Lobby","Conference Room","Retail"];
// Zone library (e.g. "AHU-1", "RTU-2") — for grouping rooms into per-system block
// loads at calc time. Empty by default: no universal default list makes sense
// (unlike room types), this is entirely project-specific naming.
let ZONE_LIB = (D.zones&&D.zones.length) ? D.zones.map(z=>typeof z==='object'?z.name:z) : [];
// Construction type libraries — seeded defaults (no-load first). Later replaced by
// the DM-setup JSON (same {name,u,dark} the .vbs uses), passed in via D.wall_types/D.roof_types.
const PALETTE = ['#ef4444','#f59e0b','#8b5cf6','#0ea5e9','#ec4899','#14b8a6','#84cc16','#f43f5e'];
// Duct vocabulary is a fixed set, not a user-extensible library like wall/roof types —
// there's no meaningful "add a new kind of terminal" the way there is "add a new wall
// construction". kind key -> label.
// Run kind is now just which air SYSTEM a run is on — its physical FORM (rect/round/flex)
// is auto-classified from topology (see effectiveForm) or manually forced after a split,
// not chosen upfront. See DUCT_FORM_OPTS below.
const DUCT_RUN_KINDS = [['supply','Supply Duct'],['return','Return Duct']];
const DUCT_FORM_OPTS = [['auto','Auto'],['rect','Rectangular'],['round','Round'],['flex','Flexible']];
const TERMINAL_KINDS = [['ceiling_supply','Ceiling Supply Diffuser'],['ceiling_return','Ceiling Return'],
  ['sidewall_supply','Sidewall Supply'],['sidewall_return','Sidewall Return']];
const EQUIPMENT_KINDS = [['AH','Air Handler'],['CU','Condensing Unit'],['DH','Dehumidifier']];
let selDuctKind=null, selDuctId=null;   // currently selected placed duct item (for its property form) — kind is 'run'|'terminal'|'equip'
let selDuctVertex=null;   // for kind==='run': index of the last-touched vertex on that run, used as the pivot for the Straighten Next/Previous tool below
function ductLabel(kindList, key){ const f=kindList.find(k=>k[0]===key); return f?f[1]:key; }
// Every project's wall/roof/floor palette must start with a "No X load" entry —
// a real DM-setup JSON's wall_types/roof_types/floor_types is just the site's
// actual construction(s), with no "none" placeholder of its own. Without this,
// index 0 (silently treated as the protected/undeletable "no load" sentinel
// elsewhere) would BE the project's real construction, with no way to mark a
// wall/roof/floor as not load-bearing at all.
function withNoLoadFirst(list, noLoadEntry, fallback){
  const base = (list && list.length) ? list : fallback;
  return [noLoadEntry, ...base.filter(t=>(typeof t==='object'?t.name:t)!==noLoadEntry.name)];
}
let WALL_TYPES = withNoLoadFirst(D.wall_types, {name:"No wall load", u:0, dark:false}, [
  {name:'8" CMU, uninsulated', u:0.51, dark:false},
  {name:'8" CMU + R-11', u:0.11, dark:false},
  {name:"Metal panel + R-19", u:0.062, dark:false}]);
let ROOF_TYPES = withNoLoadFirst(D.roof_types, {name:"No roof load", u:0, dark:false}, [
  {name:"Flat: built-up + R-30 c.i.", u:0.032, dark:true},
  {name:"Metal deck + R-20 c.i.", u:0.048, dark:false}]);
let GLASS_TYPES = (D.glass_types&&D.glass_types.length) ? D.glass_types.slice() : [{name:"Default window", u:0.5, shgc:0.4, width:3, height:4}];
let DOOR_TYPES = (D.door_types&&D.door_types.length) ? D.door_types.slice() : [{name:"Default door", u:0.6, dark:false, width:3, height:6.83}];
let FLOOR_TYPES = withNoLoadFirst(D.floor_types, {name:"No floor load", u:0, dark:false}, [
  {name:"Slab-on-grade", u:0.73, dark:false},
  {name:"Slab + R-10 edge", u:0.54, dark:false},
  {name:"Floor over unconditioned", u:0.033, dark:false}]);
// Full DM Room Type records (name -> record) for seeding a room's loads on assign.
const RT_REC = {}; (D.room_types||[]).forEach(rt=>{ RT_REC[rt.name]=rt; });
// Window/door INSTANCES placed on walls: {id,cat,type,rid,edge,t(0..1),width_ft}.
// (openings/opId themselves are set up above, per-floor, alongside rooms/nextId.)
let selOpening=null;
function libOf(cat){ return cat==='wall'?WALL_TYPES:cat==='roof'?ROOF_TYPES:cat==='floor'?FLOOR_TYPES:cat==='window'?GLASS_TYPES:cat==='door'?DOOR_TYPES:cat==='zone'?ZONE_LIB:ROOM_TYPE_LIB; }
function uvec(dx,dy){ const n=Math.hypot(dx,dy)||1; return [dx/n,dy/n]; }
// ASHRAE 62.1 / FBC Mechanical ventilation rate: Rp (cfm/person, type 1) x Pz (occupants) +
// Ra (cfm/ft2, type 2) x Az (area) = Vbz. Rp/Ra pulled out separately (not just the summed
// Vbz) so the Ventilation Schedule report section can show the actual code rates applied,
// same as a DM/FBC ventilation schedule does — shared by seedLoads() (initial seed) and the
// occupants-field cascade below, so the two never compute it differently.
function ventRatesFromRec(rec){
  let rp=0, ra=0;
  (rec && rec.ventilation_cooling || []).forEach(s=>{ if(s.type===1) rp+=s.value; else if(s.type===2) ra+=s.value; });
  return {rp, ra};
}
function ventCfmFromRec(rec, occ, A){
  if(!rec) return null;
  const {rp, ra} = ventRatesFromRec(rec);
  const v = rp*(occ||0) + ra*A;
  return v ? +v.toFixed(0) : null;
}
function seedLoads(r){   // pull loads from the assigned room-type record (best effort)
  const rec=RT_REC[r.type]; if(!rec) return; const A=area(r.points), L={};
  if(rec.lighting&&rec.lighting.type===1) L.lpd=rec.lighting.value;
  if(rec.equipment&&rec.equipment.sensible_type===1) L.epd=rec.equipment.sensible_value;
  if(rec.equipment&&rec.equipment.latent_btuh) L.equip_latent=rec.equipment.latent_btuh;
  if(rec.people) L.occ = rec.people.type===1 ? +(A/(rec.people.value||1e9)).toFixed(1) : rec.people.value;
  const {rp, ra}=ventRatesFromRec(rec); if(rp) L.vent_rp=rp; if(ra) L.vent_ra=ra;
  const v=ventCfmFromRec(rec, L.occ, A); if(v!=null) L.vent=v;
  if(rec.infiltration_cooling&&rec.infiltration_cooling.type===0) L.inf=rec.infiltration_cooling.value;
  if(rec.exhaust&&rec.exhaust.cfm_per_sqft) L.exh_cfm=+(rec.exhaust.cfm_per_sqft*A).toFixed(0);
  if(rec.heating_temp_f!=null) L.htg=rec.heating_temp_f;
  if(rec.cooling_temp_f!=null) L.clg=rec.cooling_temp_f;
  if(rec.relative_humidity!=null) L.rh=rec.relative_humidity;
  r.loads=L;
}
// A room typed BEFORE this session added exhaust seeding has a saved `loads`
// object with no `exh_cfm` at all — not because its type lacks exhaust data,
// but because seedLoads() never ran since that field started existing. Fixes
// it once on load without touching any other loads field (so a manual
// override on e.g. ventilation for that room survives) — re-running the full
// seedLoads() would silently clobber those overrides back to the type's
// defaults, which this avoids.
function backfillExhaust(){
  rooms.forEach(r=>{
    if(!r.type) return;
    const rec=RT_REC[r.type]; if(!rec||!rec.exhaust||!rec.exhaust.cfm_per_sqft) return;
    if(!r.loads) r.loads={};
    if(r.loads.exh_cfm==null) r.loads.exh_cfm=+(rec.exhaust.cfm_per_sqft*area(r.points)).toFixed(0);
  });
}
// Same pattern as backfillExhaust() — a room typed before equipment latent gain existed has a
// saved `loads` with no `equip_latent`, even though its assigned type's record has the data
// (equipment.latent_btuh, e.g. cooking/dishwashing gear in a kitchen — was captured on the DM
// side but never read here at all until now). Fills only that one field; leaves everything else,
// including any already-set equip_latent, untouched.
function backfillEquipLatent(){
  rooms.forEach(r=>{
    if(!r.type) return;
    const rec=RT_REC[r.type]; if(!rec||!rec.equipment||!rec.equipment.latent_btuh) return;
    if(!r.loads) r.loads={};
    if(r.loads.equip_latent==null) r.loads.equip_latent=rec.equipment.latent_btuh;
  });
}
// Same pattern again — a room typed before the Ventilation Schedule report section existed
// has its final Vbz (loads.vent) but never had the underlying Rp/Ra rates stashed
// separately, so the schedule would show blank rate columns for it even though the type's
// record has them. Backfills only vent_rp/vent_ra; leaves vent (and everything else) as-is.
function backfillVentRates(){
  rooms.forEach(r=>{
    if(!r.type) return;
    const rec=RT_REC[r.type]; if(!rec) return;
    const {rp, ra}=ventRatesFromRec(rec); if(!rp&&!ra) return;
    if(!r.loads) r.loads={};
    if(rp&&r.loads.vent_rp==null) r.loads.vent_rp=rp;
    if(ra&&r.loads.vent_ra==null) r.loads.vent_ra=ra;
  });
}
function edgeParam(r,i,wp){ const a=r.points[i], b=r.points[(i+1)%r.points.length]; const dx=b[0]-a[0],dy=b[1]-a[1]; const L2=dx*dx+dy*dy||1e-9; return Math.max(0,Math.min(1,((wp[0]-a[0])*dx+(wp[1]-a[1])*dy)/L2)); }
function typeColor(cat,name){ const lib=libOf(cat); const i=lib.findIndex(t=>t.name===name); if(i<=0) return null;
  const t=lib[i]; return (t&&t.color) || PALETTE[(i-1)%PALETTE.length]; }
function hexA(hex,a){ const n=parseInt(hex.slice(1),16); return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a+')'; }

function rel(e){ const r=cv.getBoundingClientRect(); return [e.clientX-r.left, e.clientY-r.top]; }
function w2s(p){ return [p[0]*view.zoom+view.panX, p[1]*view.zoom+view.panY]; }
function s2w(x,y){ return [(x-view.panX)/view.zoom, (y-view.panY)/view.zoom]; }
// Parameterized by ppf/northDeg (not the active floor's global PPF/northDeg) so
// buildPayload() can flatten every floor's rooms using THAT floor's own scale/north,
// not whichever floor happens to be on-screen when Save is clicked. area()/edgeOrient()
// below are the everyday shorthand every other call site in this file already uses,
// for the always-correct active floor.
function polyAreaFt(pts, ppf){ let s=0; for(let i=0;i<pts.length;i++){ const a=pts[i],b=pts[(i+1)%pts.length]; s+=a[0]*b[1]-b[0]*a[1]; } return Math.abs(s/2)/(ppf*ppf); }
function edgeOrientFor(r,i,nDeg){ const P=r.points,a=P[i],b=P[(i+1)%P.length]; const dx=b[0]-a[0],dy=b[1]-a[1];
  let cx=0,cy=0; P.forEach(p=>{cx+=p[0];cy+=p[1];}); cx/=P.length; cy/=P.length;
  const mx=(a[0]+b[0])/2,my=(a[1]+b[1])/2; let n=[-dy,dx]; if((mx-cx)*n[0]+(my-cy)*n[1]<0) n=[dy,-dx];  // outward (away from centroid)
  return CARD[Math.round(((bearing(n[0],n[1])-nDeg+360)%360)/45)%8]; }
function area(pts){ return polyAreaFt(pts, PPF); }
function total(){ return rooms.reduce((t,r)=>t+area(r.points),0); }
// Recalibrate the scale so the drawing's total area matches a known real-world
// total (the engineer's actual sqft, not a guessed drawing scale). Room points
// are stored in raw pixel space, and every derived measurement (area, wall
// length, the measure tool) divides by PPF live — so rescaling PPF alone
// corrects the whole drawing at once. Window/door sizes are stored in real
// feet already and are untouched (they don't depend on drawing scale).
function rescaleToTotalSqft(target){
  const cur = total();
  if(!target || target<=0 || !cur) return;
  PPF = PPF * Math.sqrt(cur/target);
  draw(); renderSide();
}
function fmtFtIn(ft){ const totalIn=Math.round(ft*12), f=Math.floor(totalIn/12), inch=totalIn%12; return f+"'-"+inch+'"'; }
function pip(pt,poly){ let c=false; for(let i=0,j=poly.length-1;i<poly.length;j=i++){ const xi=poly[i][0],yi=poly[i][1],xj=poly[j][0],yj=poly[j][1]; if(((yi>pt[1])!=(yj>pt[1])) && (pt[0]<(xj-xi)*(pt[1]-yi)/((yj-yi)||1e-9)+xi)) c=!c; } return c; }
function cen(sp){ let x=0,y=0; sp.forEach(p=>{x+=p[0];y+=p[1];}); return [x/sp.length, y/sp.length]; }
function segDist(p,a,b){ const dx=b[0]-a[0],dy=b[1]-a[1]; const L2=dx*dx+dy*dy||1e-9; let t=((p[0]-a[0])*dx+(p[1]-a[1])*dy)/L2; t=Math.max(0,Math.min(1,t)); return Math.hypot(p[0]-(a[0]+t*dx), p[1]-(a[1]+t*dy)); }

// ── Duct rendering — the whole point of this feature per the user: has to read as real
// ductwork (double-line rigid trunk, coiled flex branch), not a schematic single line.
// Sizes default to a placeholder until the Phase C sizing engine assigns real ones, so a
// freshly-drawn run still looks like a duct instead of a bare line.
const DUCT_DEFAULT_W_IN=12, DUCT_DEFAULT_H_IN=8, DUCT_DEFAULT_DIA_IN=8;
function inToPx(inches){ return (inches/12) * PPF * view.zoom; }
function ductSizeLabel(d){
  const form=effectiveForm(d);
  return form==='rect' ? (d.width_in&&d.height_in ? (d.width_in+'X'+d.height_in) : '')
                       : (d.diameter_in ? (d.diameter_in+'Ø') : '');
}
function drawDuctLabel(sp, label){
  if(!label) return;
  // Placed at the midpoint of the run's longest segment, centered ON the duct (not offset
  // above it) — textBaseline='middle' plus no y-offset lands it right on the centerline. A
  // white halo (stroked wide, behind the fill) keeps it legible over the duct lines it sits on.
  let best=0, bestLen=-1;
  for(let i=0;i<sp.length-1;i++){ const l=Math.hypot(sp[i+1][0]-sp[i][0],sp[i+1][1]-sp[i][1]); if(l>bestLen){bestLen=l;best=i;} }
  const a=sp[best], b=sp[best+1], mx=(a[0]+b[0])/2, my=(a[1]+b[1])/2;
  ctx.font='10px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.strokeStyle='rgba(255,255,255,0.85)'; ctx.lineWidth=3; ctx.strokeText(label, mx, my);
  ctx.fillStyle='#111827'; ctx.fillText(label, mx, my);
  ctx.textBaseline='alphabetic';
}
// One offset "rail" of a mitered double-line outline (like a real duct fitting's mitered
// elbow): endpoints offset by their single adjacent segment's normal, interior vertices
// offset along the two adjacent segments' averaged (miter) direction so the rail lands
// exactly on the intersection of both segments' offset lines — no per-segment seam.
// `side` is +1/-1 for the two rails. Miter length is capped (like canvas's miterLimit) so
// a near-180° reversal doesn't spike out.
function offsetPolyline(pts, halfW, side){
  const n = pts.length, dirs=[];
  for(let i=0;i<n-1;i++){ const dx=pts[i+1][0]-pts[i][0], dy=pts[i+1][1]-pts[i][1], len=Math.hypot(dx,dy)||1; dirs.push([dx/len,dy/len]); }
  const out=[];
  for(let i=0;i<n;i++){
    let nx,ny;
    if(i===0){ nx=-dirs[0][1]; ny=dirs[0][0]; }
    else if(i===n-1){ nx=-dirs[n-2][1]; ny=dirs[n-2][0]; }
    else {
      const n1x=-dirs[i-1][1], n1y=dirs[i-1][0], n2x=-dirs[i][1], n2y=dirs[i][0];
      let mx=n1x+n2x, my=n1y+n2y; const mlen=Math.hypot(mx,my);
      if(mlen<1e-6){ nx=n2x; ny=n2y; }
      else { mx/=mlen; my/=mlen; const cosHalf=mx*n1x+my*n1y, miterLen=1/Math.max(cosHalf,0.2); nx=mx*miterLen; ny=my*miterLen; }
    }
    out.push([pts[i][0]+nx*halfW*side, pts[i][1]+ny*halfW*side]);
  }
  return out;
}
// DM convention: the duct itself is black (selection highlights blue instead of the old
// supply/return color coding); a thin colored centerline additionally marks supply (blue)
// vs return (orange) runs — matches the reference DM drawings the user provided. These
// centerlines are engineer markup, not part of the drawing — gated by `ductViewMode` so
// they're hidden in the client/submittal view (see bDuctView toggle).
const DUCT_SUPPLY_COL = '#1d4ed8', DUCT_RETURN_COL = '#f97316';
function drawDuctCenterline(sp, system){
  if(ductViewMode!=='engineer') return;
  ctx.beginPath(); sp.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
  ctx.strokeStyle = system==='supply' ? DUCT_SUPPLY_COL : DUCT_RETURN_COL;
  ctx.lineWidth=1; ctx.stroke();
}
// Rect and round trunks render identically (a mitered double-line outline) — in 2D plan
// view they only differ in which size field applies (W×H vs diameter) and the label
// format (see ductSizeLabel); only `flex` gets the corrugated look, in drawFlexRun below.
function drawHardRun(d, on){
  const form = effectiveForm(d);
  const half = inToPx(form==='rect' ? (d.width_in || DUCT_DEFAULT_W_IN) : (d.diameter_in || DUCT_DEFAULT_DIA_IN))/2;
  const sp = d.points.map(w2s);
  ctx.strokeStyle = on ? '#2563eb' : '#111827'; ctx.lineWidth=1.4;
  if(sp.length>=2){
    const left=offsetPolyline(sp,half,1), right=offsetPolyline(sp,half,-1);
    ctx.beginPath();
    left.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
    for(let i=right.length-1;i>=0;i--) ctx.lineTo(right[i][0],right[i][1]);
    ctx.closePath(); ctx.stroke();
  }
  drawDuctCenterline(sp, d.system);
  drawDuctLabel(sp, ductSizeLabel(d));
}
// One "rail" of a corrugated flex-duct bellows: walks a single segment at a fixed
// REAL-WORLD step (so the corrugation reads the same density at any zoom level),
// necking the offset in by `bump` on every other step — both rails neck in on the same
// steps, so the tube visibly pinches at each "ring" the way a real insulated flex duct does.
// [smoothLo,smoothHi] (px along THIS segment, measured from point a) is a stretch that
// renders plain/smooth instead of corrugated — the collar stub at a branch takeoff (see
// drawFlexRun); both zero for an ordinary flex run (no effect — matches the old behavior).
// [visLo,visHi] (px along THIS segment, from point a) is the range that's drawn at all —
// points outside it are skipped entirely, not just left un-corrugated, so a branch's rail
// never renders back over the trunk it tees from (see drawFlexRun's `hideLen`). Within the
// visible range, [smoothLo,smoothHi] is the plain (non-corrugated) collar stub.
function flexRail(a,b,halfW,bump,stepPx,side,visLo,visHi,smoothLo,smoothHi){
  const dx=b[0]-a[0], dy=b[1]-a[1], len=Math.hypot(dx,dy)||1;
  const ux=dx/len, uy=dy/len, nx=-uy, ny=ux;
  const n=Math.max(2,Math.round(len/stepPx));
  const pts=[];
  for(let k=0;k<=n;k++){
    const t=k/n, distAlong=t*len;
    if(distAlong<visLo || distAlong>visHi) continue;
    const px=a[0]+dx*t, py=a[1]+dy*t;
    const smooth = distAlong>=smoothLo && distAlong<=smoothHi;
    const w=halfW-(smooth?0:((k%2===0)?0:bump));
    pts.push([px+nx*w*side, py+ny*w*side]);
  }
  return pts;
}
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
function drawFlexRun(d, on){
  // Corrugated/bellows outline (per segment — a slight kink at bends reads as natural for
  // a flexible duct, unlike the rigid trunk's true miter) instead of a thin line + ticks.
  // If this run is a branch (findTeePoint), nothing is drawn for the stretch that falls
  // INSIDE the trunk it tees from (`hideLen`, the trunk's own half-width/diameter there —
  // real ductwork doesn't render past the wall it penetrates), then a plain round collar —
  // a real spin-in fitting, same diameter, no taper — starts right at the trunk's outer
  // wall before the corrugation begins. Both budgets are spent walking segments in order
  // from whichever end ties into the trunk, so it's correct regardless of draw direction.
  const half = inToPx(d.diameter_in || DUCT_DEFAULT_DIA_IN)/2;
  const sp = d.points.map(w2s);
  const bump = half*0.35, step = inToPx(2);
  const tee = findTeePoint(d), collarLen = inToPx(8);
  const hideLen = tee ? inToPx(effectiveForm(tee.onRun)==='rect' ? (tee.onRun.width_in||DUCT_DEFAULT_W_IN) : (tee.onRun.diameter_in||DUCT_DEFAULT_DIA_IN))/2 : 0;
  const nSeg = sp.length-1;
  const visLo=new Array(nSeg).fill(0), visHi=new Array(nSeg).fill(Infinity), smoothLo=new Array(nSeg).fill(0), smoothHi=new Array(nSeg).fill(0);
  if(tee){
    const hideEnd=hideLen, collarEnd=hideLen+collarLen;
    let cursor=0;
    const order=[]; for(let i=0;i<nSeg;i++) order.push(i);
    if(tee.end==='end') order.reverse();
    order.forEach(i=>{
      const a=sp[i], b=sp[i+1], segLen=Math.hypot(b[0]-a[0],b[1]-a[1])||1;
      const segHideEnd=clamp(hideEnd-cursor,0,segLen), segCollarEnd=clamp(collarEnd-cursor,0,segLen);
      if(tee.end==='start'){ visLo[i]=segHideEnd; visHi[i]=segLen; smoothLo[i]=segHideEnd; smoothHi[i]=segCollarEnd; }
      else { visLo[i]=0; visHi[i]=segLen-segHideEnd; smoothLo[i]=segLen-segCollarEnd; smoothHi[i]=segLen-segHideEnd; }
      cursor+=segLen;
    });
  }
  ctx.strokeStyle = on ? '#2563eb' : '#111827'; ctx.lineWidth=1.2;
  for(let i=0;i<nSeg;i++){
    const a=sp[i], b=sp[i+1];
    const left=flexRail(a,b,half,bump,step,1,visLo[i],visHi[i],smoothLo[i],smoothHi[i]), right=flexRail(a,b,half,bump,step,-1,visLo[i],visHi[i],smoothLo[i],smoothHi[i]);
    if(left.length<2) continue;
    ctx.beginPath();
    left.forEach((p,j)=>j?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
    for(let j=right.length-1;j>=0;j--) ctx.lineTo(right[j][0],right[j][1]);
    ctx.closePath(); ctx.stroke();
  }
  drawDuctCenterline(sp, d.system);
  drawDuctLabel(sp, ductSizeLabel(d));
}
function drawTerminalIcon(t, on){
  // Square = ceiling supply, circle = ceiling return, arrow = sidewall supply/return —
  // matches the reference sheets' HVAC Symbols legend exactly. Fixed screen-space size
  // (not scaled by PPF) — real CAD terminal blocks are drawn at a constant symbol size
  // regardless of plan scale, same as a north-arrow or door-swing symbol would be.
  const s=w2s(t.point), sz=8;
  const col = on ? '#111827' : (t.kind.indexOf('supply')>=0 ? '#1d4ed8' : '#15803d');
  ctx.strokeStyle=col; ctx.fillStyle='rgba(255,255,255,0.92)'; ctx.lineWidth=1.5;
  if(t.kind==='ceiling_supply'){
    ctx.fillRect(s[0]-sz,s[1]-sz,sz*2,sz*2); ctx.strokeRect(s[0]-sz,s[1]-sz,sz*2,sz*2);
    ctx.beginPath(); ctx.moveTo(s[0]-sz,s[1]-sz); ctx.lineTo(s[0]+sz,s[1]+sz);
    ctx.moveTo(s[0]+sz,s[1]-sz); ctx.lineTo(s[0]-sz,s[1]+sz); ctx.stroke();
  } else if(t.kind==='ceiling_return'){
    ctx.beginPath(); ctx.arc(s[0],s[1],sz,0,7); ctx.fill(); ctx.stroke();
  } else {
    // Sidewall supply/return: an arrow. Direction is a fixed visual cue in v1 — terminals
    // are free points (not wall-attached like windows/doors), so there's no wall normal to
    // derive an actual facing direction from.
    const dir = t.kind==='sidewall_supply' ? 1 : -1;
    ctx.beginPath(); ctx.moveTo(s[0]-sz*dir,s[1]); ctx.lineTo(s[0]+sz*dir,s[1]);
    ctx.lineTo(s[0]+sz*0.4*dir,s[1]-sz*0.5); ctx.moveTo(s[0]+sz*dir,s[1]); ctx.lineTo(s[0]+sz*0.4*dir,s[1]+sz*0.5);
    ctx.stroke();
  }
  const label=[t.size,t.cfm!=null?t.cfm+' cfm':''].filter(Boolean).join(' · ');
  if(label){ ctx.fillStyle='#111827'; ctx.font='9px sans-serif'; ctx.textAlign='center'; ctx.fillText(label, s[0], s[1]+sz+11); }
}
function drawEquipmentIcon(eq, on){
  // Square = air handler / dehumidifier, circle = condensing unit — matches the reference.
  const s=w2s(eq.point), sz=13;
  ctx.strokeStyle = on ? '#111827' : '#5b21b6'; ctx.fillStyle='rgba(255,255,255,0.92)'; ctx.lineWidth=1.5;
  if(eq.kind==='CU'){ ctx.beginPath(); ctx.arc(s[0],s[1],sz,0,7); ctx.fill(); ctx.stroke(); }
  else { ctx.fillRect(s[0]-sz,s[1]-sz,sz*2,sz*2); ctx.strokeRect(s[0]-sz,s[1]-sz,sz*2,sz*2); }
  ctx.fillStyle='#111827'; ctx.font='bold 9px sans-serif'; ctx.textAlign='center';
  let ly=s[1]+sz+11;
  ctx.fillText(eq.label||eq.kind, s[0], ly);
  if(eq.kind==='AH'){
    if(eq.supply_cfm!=null){ ly+=11; ctx.fillText('Supply: '+eq.supply_cfm+' cfm', s[0], ly); }
    if(eq.return_cfm!=null){ ly+=11; ctx.fillText('Return: '+eq.return_cfm+' cfm', s[0], ly); }
  }
}
function nearestEdge(wp){ let best=null; rooms.forEach(r=>{ const P=r.points; for(let i=0;i<P.length;i++){ const d=segDist(wp,P[i],P[(i+1)%P.length]); if(!best||d<best.dist) best={room:r,i:i,dist:d}; } }); return best; }
// Nearest existing duct-network point — any run vertex, terminal, or equipment point, OR
// any point ALONG a run's segments (via segDist, same closest-point-on-segment math as
// nearestEdge) so a branch can tee off mid-span from a straight trunk, not just its
// endpoints — a real duct takeoff, and the fix for "can't attach the flex duct to the main
// trunk" (previously only exact vertices were snap targets). Caller applies its own pixel
// tolerance, same convention as nearestEdge.
function closestOnSeg(p,a,b){ const dx=b[0]-a[0], dy=b[1]-a[1], L2=dx*dx+dy*dy||1e-9; let t=((p[0]-a[0])*dx+(p[1]-a[1])*dy)/L2; t=Math.max(0,Math.min(1,t)); const q=[a[0]+t*dx,a[1]+t*dy]; return {point:q, dist:Math.hypot(p[0]-q[0],p[1]-q[1])}; }
function nearestDuctPoint(wp){
  let best=null;
  const consider=p=>{ const dist=Math.hypot(wp[0]-p[0],wp[1]-p[1]); if(!best||dist<best.dist) best={point:p,dist:dist}; };
  ductRuns.forEach(d=>{
    d.points.forEach(consider);
    for(let i=0;i<d.points.length-1;i++) consider(closestOnSeg(wp,d.points[i],d.points[i+1]).point);
  });
  ductTerminals.forEach(t=>consider(t.point));
  ductEquipment.forEach(eq=>consider(eq.point));
  return best;
}
// Does either of this run's endpoints tee off the MIDDLE of some other run's segment — a
// real branch takeoff — as opposed to just meeting another run at ITS endpoint (that's
// trunk continuation, not a branch: two runs sharing an exact tip-to-tip point, e.g. the
// two pieces of a split, stay rect). World-space tolerance (not zoom-scaled) since this
// drives classification, not a UI hit-test — snapping (nearestDuctPoint) already places
// genuinely-connected points at the exact same coordinate, so a small fixed epsilon is
// plenty. Returns {end:'start'|'end', point, dir} (dir = unit vector from the tee point
// INTO this run, for the collar stub) or null if this run isn't a branch.
function findTeePoint(d){
  const TOL=1.5;
  function teeOn(pt){
    let best=null;
    ductRuns.forEach(r=>{
      if(r.id===d.id) return;
      for(let i=0;i<r.points.length-1;i++){
        const a=r.points[i], b=r.points[i+1], cs=closestOnSeg(pt,a,b);
        if(cs.dist>TOL) continue;
        if(Math.hypot(cs.point[0]-a[0],cs.point[1]-a[1])<TOL || Math.hypot(cs.point[0]-b[0],cs.point[1]-b[1])<TOL) continue; // meets an endpoint, not a mid-span tee
        if(!best||cs.dist<best.dist) best={point:cs.point, run:r};
      }
    });
    return best;
  }
  if(!d.points || d.points.length<2) return null;
  const s=teeOn(d.points[0]);
  if(s){ const nxt=d.points[1], dx=nxt[0]-s.point[0], dy=nxt[1]-s.point[1], len=Math.hypot(dx,dy)||1; return {end:'start', point:s.point, dir:[dx/len,dy/len], onRun:s.run}; }
  const e=teeOn(d.points[d.points.length-1]);
  if(e){ const prv=d.points[d.points.length-2], dx=prv[0]-e.point[0], dy=prv[1]-e.point[1], len=Math.hypot(dx,dy)||1; return {end:'end', point:e.point, dir:[dx/len,dy/len], onRun:e.run}; }
  return null;
}
// A run's physical form: an explicit override (set by the user after splitting) always
// wins; otherwise 'auto' resolves from topology — trunk (rect) unless this run tees off
// another run's mid-span, in which case it's a branch (flex, with a collar at the tee —
// see drawFlexRun). 'round' never comes out of auto-resolution; it's reachable only as a
// manual override on a split piece.
function effectiveForm(d){
  if(d.form && d.form!=='auto') return d.form;
  return findTeePoint(d) ? 'flex' : 'rect';
}
// Constrains the direction from `prev` to `p` to the nearest 45° increment, preserving
// distance — "clean angle" drawing (ducts run orthogonal/45°, not arbitrary freehand).
function snapAngle(prev, p){
  const dx=p[0]-prev[0], dy=p[1]-prev[1], dist=Math.hypot(dx,dy);
  if(dist<1e-6) return p;
  const ang=Math.round(Math.atan2(dy,dx)/(Math.PI/4))*(Math.PI/4);
  return [prev[0]+Math.cos(ang)*dist, prev[1]+Math.sin(ang)*dist];
}
// Where the next duct-draft point would land if placed now: snap to an existing network
// point within tolerance (an intentional connection always wins), else angle-snap relative
// to the last draft point, else (first point of a run) the raw click.
function ductNextPoint(wp){
  const near=nearestDuctPoint(wp);
  if(near && near.dist*view.zoom<12) return near.point;
  if(draft.length) return snapAngle(draft[draft.length-1], wp);
  return wp;
}
const CARD=['N','NE','E','SE','S','SW','W','NW'];
function bearing(vx,vy){ let a=Math.atan2(vx,-vy)*180/Math.PI; return a<0?a+360:a; }  // screen vec (y down) -> ° CW from up
function edgeOrient(r,i){ return edgeOrientFor(r,i,northDeg); }

function resize(){
  const r=cv.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
  VW=r.width; VH=r.height;                     // logical (CSS) size — what clicks use
  cv.width=Math.round(VW*dpr); cv.height=Math.round(VH*dpr);   // buffer (physical) size
  ctx.setTransform(dpr,0,0,dpr,0,0);           // draw in CSS px, crisp on Retina
  draw();
}
function fit(){ const f=floorMeta[currentFloorIdx]; const z=Math.min(VW/f.pageW, VH/f.pageH)*0.96; view.zoom=z;
  view.panX=(VW-f.pageW*z)/2; view.panY=(VH-f.pageH*z)/2; }

function draw(printMode){
  ctx.clearRect(0,0,VW,VH);
  const _f=floorMeta[currentFloorIdx];
  if(img.complete && img.naturalWidth) ctx.drawImage(img, view.panX, view.panY, _f.pageW*view.zoom, _f.pageH*view.zoom);
  if(!printMode) rooms.forEach(r=>{
    const sp=r.points.map(w2s);
    ctx.beginPath(); sp.forEach((p,i)=> i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])); ctx.closePath();
    const rc=typeColor('roof', r.roof);
    ctx.fillStyle = r.id===selected ? 'rgba(37,99,235,0.28)' : (rc?hexA(rc,0.16):'rgba(16,185,129,0.12)');
    ctx.fill();
    // edges colored by wall type (No load = thin; assigned = colored; hovered in wall-paint = orange)
    for(let i=0;i<sp.length;i++){ const a=sp[i], b=sp[(i+1)%sp.length]; const wc=typeColor('wall', r.walls[i]);
      const hov=(paintCat==='wall' && hoverEdge && hoverEdge.room===r && hoverEdge.i===i);
      ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]);
      ctx.strokeStyle = hov?'#ea580c':(wc||(r.id===selected?'#1d4ed8':'#10b981')); ctx.lineWidth = hov?6:(wc?3.5:1.2); ctx.stroke();
      if(wc){ ctx.fillStyle='#1e3a8a'; ctx.font='bold 10px sans-serif'; ctx.textAlign='center'; ctx.fillText(edgeOrient(r,i), (a[0]+b[0])/2, (a[1]+b[1])/2-2); } }
    const c=cen(sp); ctx.textAlign='center';
    ctx.fillStyle='#064e3b'; ctx.font='bold 12px sans-serif'; ctx.fillText(r.label, c[0], c[1]-12);
    ctx.font='11px sans-serif'; ctx.fillText(area(r.points).toFixed(0)+' ft²', c[0], c[1]+1);
    let yy=c[1]+13;
    if(r.zone){ ctx.fillStyle='#7c3aed'; ctx.font='bold 10px sans-serif'; ctx.fillText('◆ '+r.zone, c[0], yy); yy+=11; }
    if(r.type){ ctx.fillStyle='#475569'; ctx.font='10px sans-serif'; ctx.fillText(r.type, c[0], yy); yy+=11; }
    if(rc){ ctx.fillStyle='#7c2d12'; ctx.font='10px sans-serif'; ctx.fillText('▲ '+r.roof, c[0], yy); yy+=11; }
    if(r.floor && r.floor!=='No floor load'){ ctx.fillStyle='#374151'; ctx.font='10px sans-serif'; ctx.fillText('▼ '+r.floor, c[0], yy); }
  });
  // window/door instances sitting on their walls
  if(!printMode) openings.forEach(o=>{ const r=rooms.find(x=>x.id===o.rid); if(!r) return; const P=r.points, a=P[o.edge], b=P[(o.edge+1)%P.length];
    const pt=[a[0]+(b[0]-a[0])*o.t, a[1]+(b[1]-a[1])*o.t]; const d=uvec(b[0]-a[0],b[1]-a[1]); const half=(o.width*PPF)/2;
    const s1=w2s([pt[0]-d[0]*half,pt[1]-d[1]*half]), s2=w2s([pt[0]+d[0]*half,pt[1]+d[1]*half]);
    const col=o.cat==='window'?'#0284c7':'#a16207', on=(selOpening===o.id);
    ctx.beginPath(); ctx.moveTo(s1[0],s1[1]); ctx.lineTo(s2[0],s2[1]); ctx.strokeStyle=on?'#111827':col; ctx.lineWidth=on?8:5.5; ctx.stroke();
    const sd=uvec(s2[0]-s1[0],s2[1]-s1[1]), sn=[-sd[1],sd[0]];
    [s1,s2].forEach(s=>{ ctx.beginPath(); ctx.moveTo(s[0]-sn[0]*4,s[1]-sn[1]*4); ctx.lineTo(s[0]+sn[0]*4,s[1]+sn[1]*4); ctx.strokeStyle=on?'#111827':col; ctx.lineWidth=2; ctx.stroke(); });
  });
  // Ducts — Phase B rendering: true-to-scale double-line rigid trunk, coiled/corrugated
  // flex branch, terminal/equipment symbols — matching the DM reference drawings'
  // conventions (see drawHardRun/drawFlexRun/drawTerminalIcon/drawEquipmentIcon below).
  ductRuns.forEach(d=>{
    const on=(selDuctKind==='run'&&selDuctId===d.id);
    if(effectiveForm(d)==='flex') drawFlexRun(d,on); else drawHardRun(d,on);
  });
  ductTerminals.forEach(t=>drawTerminalIcon(t, selDuctKind==='terminal'&&selDuctId===t.id));
  ductEquipment.forEach(eq=>drawEquipmentIcon(eq, selDuctKind==='equip'&&selDuctId===eq.id));
  // Vertex handles — every point of the selected run, same visual language AND same edit
  // gestures as a room's corners: drag to move, alt-click (or right-click) to delete,
  // click an edge-midpoint "+" to insert a new point there. Shift-click an INTERIOR vertex
  // splits the run into two independently-editable pieces (a duct-specific extra — rooms
  // have no equivalent) — see mousedown/contextmenu.
  if(tab==='ducts' && selDuctKind==='run'){
    const selRun=ductRuns.find(d=>d.id===selDuctId);
    if(selRun){
      for(let i=0;i<selRun.points.length-1;i++){ const a=selRun.points[i], b=selRun.points[i+1], m=w2s([(a[0]+b[0])/2,(a[1]+b[1])/2]);
        ctx.beginPath(); ctx.arc(m[0],m[1],4,0,7); ctx.fillStyle='rgba(234,88,12,0.9)'; ctx.fill();
        ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(m[0]-2.2,m[1]); ctx.lineTo(m[0]+2.2,m[1]); ctx.moveTo(m[0],m[1]-2.2); ctx.lineTo(m[0],m[1]+2.2); ctx.stroke(); }
      // The active vertex (last clicked/dragged — the Straighten Next/Previous pivot) fills
      // solid blue instead of white, so it's clear which joint those buttons will act on.
      selRun.points.forEach((p,i)=>{ const s=w2s(p), active=(i===selDuctVertex);
        ctx.beginPath(); ctx.arc(s[0],s[1],5,0,7); ctx.fillStyle=active?'#2563eb':'#fff'; ctx.fill(); ctx.lineWidth=2; ctx.strokeStyle='#ea580c'; ctx.stroke(); });
    }
  }
  const sel=rooms.find(r=>r.id===selected);
  if(sel && tab==='rooms' && !paintCat){
    // edge-midpoint "add corner" handles (orange +)
    sel.points.forEach((p,i)=>{ const q=sel.points[(i+1)%sel.points.length]; const m=w2s([(p[0]+q[0])/2,(p[1]+q[1])/2]);
      ctx.beginPath(); ctx.arc(m[0],m[1],4,0,7); ctx.fillStyle='rgba(234,88,12,0.9)'; ctx.fill();
      ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(m[0]-2.2,m[1]); ctx.lineTo(m[0]+2.2,m[1]); ctx.moveTo(m[0],m[1]-2.2); ctx.lineTo(m[0],m[1]+2.2); ctx.stroke(); });
    // corner handles (draggable = move, right/alt-click = delete)
    sel.points.forEach(p=>{ const s=w2s(p); ctx.beginPath(); ctx.arc(s[0],s[1],5,0,7); ctx.fillStyle='#fff'; ctx.fill(); ctx.lineWidth=2; ctx.strokeStyle='#1d4ed8'; ctx.stroke(); });
  }
  if(mode==='draw' && draft.length){ const sp=draft.map(w2s); ctx.beginPath(); sp.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])); ctx.strokeStyle='#ea580c'; ctx.lineWidth=2; ctx.stroke(); sp.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],4,0,7);ctx.fillStyle='#ea580c';ctx.fill();}); }
  if(mode==='draw_duct' && draft.length){ const sp=draft.map(w2s); ctx.beginPath(); sp.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])); ctx.strokeStyle='#2563eb'; ctx.lineWidth=2; ctx.stroke(); sp.forEach(p=>{ctx.beginPath();ctx.arc(p[0],p[1],4,0,7);ctx.fillStyle='#2563eb';ctx.fill();});
    // Rubber-band preview of the next point (post point-snap/angle-snap), so the 45° constraint
    // and any network-point snap are visible before the user actually clicks.
    if(ductPreviewPt){ const last=sp[sp.length-1], pp=w2s(ductPreviewPt);
      ctx.setLineDash([4,4]); ctx.beginPath(); ctx.moveTo(last[0],last[1]); ctx.lineTo(pp[0],pp[1]); ctx.strokeStyle='#93c5fd'; ctx.lineWidth=1.5; ctx.stroke(); ctx.setLineDash([]); } }
  if(hoverDuctSnap){ const hp=w2s(hoverDuctSnap); ctx.beginPath(); ctx.arc(hp[0],hp[1],7,0,7); ctx.strokeStyle='#f97316'; ctx.lineWidth=2; ctx.stroke(); }
  if(ruler){
    const distFt=Math.hypot(ruler.b[0]-ruler.a[0],ruler.b[1]-ruler.a[1])/PPF;
    const s1=w2s(ruler.a), s2=w2s(ruler.b);
    ctx.setLineDash([6,4]); ctx.beginPath(); ctx.moveTo(s1[0],s1[1]); ctx.lineTo(s2[0],s2[1]); ctx.strokeStyle='#dc2626'; ctx.lineWidth=2; ctx.stroke(); ctx.setLineDash([]);
    [s1,s2].forEach(p=>{ ctx.beginPath(); ctx.arc(p[0],p[1],4,0,7); ctx.fillStyle='#dc2626'; ctx.fill(); });
    const mid=[(s1[0]+s2[0])/2,(s1[1]+s2[1])/2], label=distFt.toFixed(2)+' ft ('+fmtFtIn(distFt)+')';
    ctx.font='bold 12px sans-serif'; const tw=ctx.measureText(label).width;
    ctx.fillStyle='rgba(255,255,255,0.9)'; ctx.fillRect(mid[0]-tw/2-5,mid[1]-19,tw+10,18);
    ctx.strokeStyle='#dc2626'; ctx.lineWidth=1; ctx.strokeRect(mid[0]-tw/2-5,mid[1]-19,tw+10,18);
    ctx.fillStyle='#dc2626'; ctx.textAlign='center'; ctx.fillText(label, mid[0], mid[1]-6);
    document.getElementById('rulerStat').textContent = '📏 '+label;
  } else { document.getElementById('rulerStat').textContent = ''; }
  // compass rose (screen-space overlay) — shows current north
  const R=26,rx=48,ry=56,nr=northDeg*Math.PI/180,nv=[Math.sin(nr),-Math.cos(nr)];
  ctx.beginPath(); ctx.arc(rx,ry,R,0,7); ctx.fillStyle='rgba(255,255,255,0.85)'; ctx.fill(); ctx.strokeStyle='#94a3b8'; ctx.lineWidth=1; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(rx-nv[0]*R*0.6,ry-nv[1]*R*0.6); ctx.lineTo(rx+nv[0]*R*0.8,ry+nv[1]*R*0.8); ctx.strokeStyle='#ef4444'; ctx.lineWidth=2.5; ctx.stroke();
  ctx.fillStyle='#ef4444'; ctx.font='bold 11px sans-serif'; ctx.textAlign='center'; ctx.fillText('N', rx+nv[0]*(R+8), ry+nv[1]*(R+8)+3);
  document.getElementById('stat').textContent = rooms.length+' rooms · '+total().toFixed(0)+' ft² total';
}

// Renders the current floor's duct layer only (no room fills/labels, no openings) into a
// hi-res PNG for the printable report, then restores the live on-screen view exactly as
// it was — this must be side-effect-free from the user's perspective.
function captureDuctPlanImage(){
  if(ductRuns.length===0) return null;
  const savedSelected=selected, savedSelOpening=selOpening, savedSelDuctKind=selDuctKind, savedSelDuctId=selDuctId;
  selected=null; selOpening=null; selDuctKind=null; selDuctId=null;
  const savedW=cv.width, savedH=cv.height, savedView=Object.assign({},view);
  fit();
  const scale=3;
  cv.width=Math.round(VW*scale); cv.height=Math.round(VH*scale);
  ctx.setTransform(scale,0,0,scale,0,0);
  draw(true);
  const dataUri=cv.toDataURL('image/png');
  cv.width=savedW; cv.height=savedH;
  const dpr=window.devicePixelRatio||1; ctx.setTransform(dpr,0,0,dpr,0,0);
  view=savedView;
  selected=savedSelected; selOpening=savedSelOpening; selDuctKind=savedSelDuctKind; selDuctId=savedSelDuctId;
  draw();
  return dataUri;
}

const HINTS = {draw:'Drawing a room: click to drop each corner, double-click to finish, Esc to cancel.',
  draw_duct:'Drawing a duct run: click to drop each point (snaps to nearby network points, otherwise to 45°), double-click to finish this run and start the next one, Enter/Esc to stop.',
  measure:'Measure: click and drag between two points to see the distance. Esc to clear.'};
function setMode(m){ mode=m; cv.classList.toggle('draw',m==='draw'||m==='draw_duct'); cv.classList.toggle('measure',m==='measure');
  document.getElementById('bAdd').classList.toggle('on',m==='draw');
  document.getElementById('bMeasure').classList.toggle('on',m==='measure');
  const hint=document.getElementById('hint'); hint.textContent=HINTS[m]||''; hint.classList.toggle('show',!!HINTS[m]);
  if(m!=='draw'&&m!=='draw_duct') draft=[];
  if(m!=='draw_duct'){ hoverDuctSnap=null; ductPreviewPt=null; } }

function renderSide(){
  const s=document.getElementById('side');
  let h='<div class="tabs"><button class="tab'+(tab==='rooms'?' on':'')+'" data-tab="rooms">Rooms</button>'
       +'<button class="tab'+(tab==='con'?' on':'')+'" data-tab="con">Construction</button>'
       +'<button class="tab'+(tab==='ducts'?' on':'')+'" data-tab="ducts">Ducts</button>'
       +'<button class="tab'+(tab==='proj'?' on':'')+'" data-tab="proj">Project / Weather</button></div>';
  h += (tab==='rooms') ? roomsPanel() : (tab==='con') ? conPanel() : (tab==='ducts') ? ductsPanel() : projectPanel();
  s.innerHTML=h;
  s.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{ tab=b.dataset.tab; paintCat=null; activeType=null; hoverEdge=null; cv.classList.remove('paint'); setMode('select'); renderSide(); draw(); });
  if(tab==='rooms') wireRooms(); else if(tab==='con') wireCon(); else if(tab==='ducts') wireDucts(); else wireProject();
}

// Same {key,label} generic-field pattern as LOAD_FIELDS — every one of these is a
// project_settings key loadcalc.py's _weather() reads by name (see loadcalc.py:_weather).
const PROJECT_FIELDS=[['project_name','Project Name'],['weather_station','Weather Station (name / WMO id)'],
  ['latitude','Latitude (°N)'],['longitude','Longitude (°, west negative)'],['elevation','Elevation (ft)'],
  ['standard_meridian','Standard Meridian (°, e.g. -75 Eastern)'],
  ['cooling_design_month','Cooling Design Month (1-12)'],
  ['cooling_design_db','Cooling Design DB (°F)'],['cooling_design_wb','Cooling Design MCWB (°F)'],
  ['cooling_design_percentile','Cooling Percentile (e.g. 1%)'],
  ['osa_low_dry','Heating Design DB (°F)'],['osa_daily_range','Daily Range (°F)'],
  ['heating_design_percentile','Heating Percentile (e.g. 99%)'],
  ['dehumid_humidity_ratio','Dehum. Humidity Ratio (grains/lb)'],
  ['clear_sky_taub','Clear-sky τb'],['clear_sky_taud','Clear-sky τd']];
const SF_FIELDS=[['clg_room','Cooling — Room %'],['clg_vent','Cooling — Ventilation %'],
  ['htg_room','Heating — Room %'],['htg_vent','Heating — Ventilation %']];
// Duct Design Criteria — plain project_settings fields (like the weather ones above, not
// their own top-level dict like Safety Factors), read by engine/ducts.py's size_duct_network
// via loadcalc.py's _duct_design(). Blank = the documented default in engine/ducts.py.
const DUCT_DESIGN_FIELDS=[['duct_trunk_velocity_fpm','Trunk/Round Velocity (fpm) — default 900'],
  ['duct_branch_velocity_fpm','Branch/Flex Velocity (fpm) — default 700'],
  ['duct_trunk_height_in','Trunk Height (in) — default 10'],
  ['duct_supply_delta_t','Supply Air ΔT (°F) — default 20']];
function projectPanel(){
  let h='<div class="selblock"><h3>Project / Weather</h3>';
  h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">Design conditions for this project\'s actual location — sourced from ASHRAE Fundamentals Ch.14 (or an equivalent regional table); there\'s no live weather lookup here. Manual values entered here take priority over any bundled station match and over the engine\'s Florida-representative defaults.</div>';
  h+='<div class="loadgrid">';
  PROJECT_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input class="pf" data-k="'+f[0]+'" value="'+(PS[f[0]]!=null?String(PS[f[0]]).replace(/"/g,'&quot;'):'')+'">'; });
  h+='</div>';
  h+='<h3 style="margin-top:18px">Safety Factors</h3>';
  h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">Design margin on top of the calculated loads, project-wide. Room = envelope + internal gains; Vent = ventilation/infiltration/exhaust-makeup outdoor air — tracked separately since DM\'s own report does. Defaults (5/0/10/0%) are pre-filled with a rationale footnoted on the results report; edit here to override for this project.</div>';
  h+='<div class="loadgrid">';
  SF_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input type="number" step="1" class="sf" data-k="'+f[0]+'" value="'+SF[f[0]]+'">'; });
  h+='</div>';
  h+='<h3 style="margin-top:18px">Duct Design Criteria</h3>';
  h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">Used by 📐 Auto-Size Ducts (Ducts tab) to turn each run\'s CFM into a size — velocity method, not equal-friction. Leave blank to use the default.</div>';
  h+='<div class="loadgrid">';
  DUCT_DESIGN_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input class="pf" data-k="'+f[0]+'" value="'+(PS[f[0]]!=null?String(PS[f[0]]).replace(/"/g,'&quot;'):'')+'">'; });
  h+='</div></div>';
  return h;
}
function wireProject(){
  document.querySelectorAll('#side .pf').forEach(el=>el.oninput=()=>{
    const v=el.value.trim();
    if(v==='') delete PS[el.dataset.k]; else PS[el.dataset.k]=v;
  });
  document.querySelectorAll('#side .sf').forEach(el=>el.oninput=()=>{ SF[el.dataset.k]=parseFloat(el.value)||0; });
}

// ── Ducts tab — Phase A: data model + manual drawing only, no rendering polish yet
// (plain lines/dots in draw() for now; the double-line/coiled-flex rendering matching
// the DM reference is Phase B, deliberately checked in as its own step).
function ductsPanel(){
  const kind=selDuctKind, id=selDuctId;
  let item=null;
  if(kind==='run') item=ductRuns.find(d=>d.id===id);
  else if(kind==='terminal') item=ductTerminals.find(t=>t.id===id);
  else if(kind==='equip') item=ductEquipment.find(e=>e.id===id);

  const zoneField=(v)=>'<label>Zone</label><select class="df" data-f="zone"><option value=""'+(!v?' selected':'')+'>— none —</option>'
    +ZONE_LIB.map(z=>'<option'+(z===v?' selected':'')+'>'+z+'</option>').join('')+'</select>';

  let h='<div class="selblock"><h3>Ducts</h3>';
  h+='<button class="con-add" id="bAutoSizeDucts" title="Derives each terminal\'s CFM from the room it sits in, sums that up through the drawn network, and sizes every run/terminal from the Duct Design Criteria (Project/Weather tab). Skips anything with its Auto-size checkbox off.">📐 Auto-Size Ducts</button>';
  if(item){
    if(kind==='run'){
      const ef=effectiveForm(item), isRect=(ef==='rect');
      h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">'+ductLabel(DUCT_RUN_KINDS,item.system)+' — '+ductLabel(DUCT_FORM_OPTS,ef)+(item.form==='auto'?' (auto)':' (forced)')
        +'<br><span style="font-size:11px">Edit it like a room: drag a dot to move it, alt/right-click to delete it (min 2 points), click an orange + to add one, drag the body to move the whole run. Shift-click an interior dot to split it into two independently-editable pieces. Straighten (below) pivots at whichever dot is solid blue — the one you last clicked or dragged.</span></div><div class="loadgrid">';
      h+='<label>Form</label><select class="df" data-f="form">'+DUCT_FORM_OPTS.map(([k,l])=>'<option value="'+k+'"'+(item.form===k?' selected':'')+'>'+l+'</option>').join('')+'</select>';
      h+='<label>Auto-size this</label><input type="checkbox" class="df" data-f="auto" style="width:auto;justify-self:start"'+(item.auto!==false?' checked':'')+'>';
      h+='<label>CFM</label><input type="number" step="any" class="df" data-f="cfm" value="'+(item.cfm!=null?item.cfm:'')+'">';
      if(isRect){
        h+='<label>Width (in)</label><input type="number" step="any" class="df" data-f="width_in" value="'+(item.width_in!=null?item.width_in:'')+'">';
        h+='<label>Height (in)</label><input type="number" step="any" class="df" data-f="height_in" value="'+(item.height_in!=null?item.height_in:'')+'">';
      } else {
        h+='<label>Diameter (in)</label><input type="number" step="any" class="df" data-f="diameter_in" value="'+(item.diameter_in!=null?item.diameter_in:'')+'">';
      }
      h+=zoneField(item.zone)+'</div>';
      if(selDuctVertex!=null && selDuctVertex>=0 && selDuctVertex<item.points.length){
        const isInterior=selDuctVertex>0 && selDuctVertex<item.points.length-1;
        const crossRun=!isInterior && connectionAt(item, item.points[selDuctVertex]);
        const canStraighten=isInterior || !!crossRun;
        h+='<div style="font-size:11px;color:var(--ink-muted);margin:10px 0 4px">Straighten (DM-style Next/Previous) — snaps '
          +(isInterior ? 'the segment on either side of the dot you last clicked/dragged'
                       : (crossRun ? 'this run\'s segment at that end-dot, against the OTHER run it connects to,'
                                   : 'the segment at that end-dot — but it isn\'t touching another run, so there\'s nothing to straighten against'))
          +' to whichever is closer to its current angle: running straight on, or turning a clean 90° corner, off the other segment.</div>';
        h+='<div class="tf-row"><button class="con-add" id="bStraightenPrev"'+(canStraighten?'':' disabled')+'>⌐ Previous [</button>'
          +'<button class="con-add" id="bStraightenNext"'+(canStraighten?'':' disabled')+'>Next ] ⌐</button></div>';
      }
    } else if(kind==='terminal'){
      h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">'+ductLabel(TERMINAL_KINDS,item.kind)+'</div><div class="loadgrid">';
      h+='<label>Auto-size CFM</label><input type="checkbox" class="df" data-f="auto" style="width:auto;justify-self:start"'+(item.auto!==false?' checked':'')+'>';
      h+='<label>CFM</label><input type="number" step="any" class="df" data-f="cfm" value="'+(item.cfm!=null?item.cfm:'')+'">';
      h+='<label>Size</label><input class="df" data-f="size" value="'+(item.size!=null?String(item.size).replace(/"/g,'&quot;'):'')+'">';
      h+=zoneField(item.zone)+'</div>';
    } else {
      h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">'+ductLabel(EQUIPMENT_KINDS,item.kind)
        +(item.kind==='AH' ? '<br><span style="font-size:11px">Supply: '+(item.supply_cfm!=null?item.supply_cfm+' cfm':'—')+' · Return: '+(item.return_cfm!=null?item.return_cfm+' cfm':'—')+'</span>' : '')
        +'</div><div class="loadgrid">';
      h+='<label>Label</label><input class="df" data-f="label" value="'+(item.label!=null?String(item.label).replace(/"/g,'&quot;'):'')+'">';
      if(item.kind==='AH'){
        h+='<label>Capacity override (cfm)</label><input type="number" step="any" class="df" data-f="capacity_cfm" value="'+(item.capacity_cfm!=null?item.capacity_cfm:'')+'" title="Leave blank to auto-match a catalog unit to the theoretical load. Set this to whatever this air handler actually outputs and every diffuser downstream of it will be sized to add up to exactly that number.">';
      }
      h+=zoneField(item.zone)+'</div>';
    }
    h+='<button class="danger" id="ductDel">Delete this item</button>';
  } else {
    h+='<div style="font-size:12px;color:var(--ink-muted);margin-bottom:8px">Pick Supply or Return below, then just draw — rect/round/flex is figured out automatically (trunk stays rect; anything that tees off an existing run becomes flex with a round collar at the takeoff). Select a run and edit it just like a room: drag a dot to move it, alt/right-click to delete it, click an orange + to add one, drag the body to move the whole run. Shift-click an interior dot to split it and force either piece to Rect/Round/Flex yourself. Straighten (DM-style Next/Previous) snaps the segment next to whichever dot you last touched (shown solid blue) to the closest clean angle — straight on or a 90° corner — off the other one — <kbd>[</kbd> Previous, <kbd>]</kbd> Next, or use the buttons that appear below once a dot is active. Terminals/equipment: one click places it.<br><b>Keyboard:</b> 1-2 draw a run (Supply/Return) · Shift+1-4 place a terminal (Ceil.Sup/Ret, Sidewall Sup/Ret) · Alt+1-3 place equipment (AH/CU/DH) · [ / ] straighten previous/next · press again to deselect.</div>';
    h+='<h3 style="margin-top:4px">Duct Runs</h3><div class="types">';
    DUCT_RUN_KINDS.forEach(([key,label])=>{ const on=(paintCat==='ductrun'&&activeType===key);
      h+='<div class="chip'+(on?' on':'')+'" data-dcat="ductrun" data-dkey="'+key+'"><span class="nm">'+label+'</span></div>'; });
    h+='</div>';
    if(paintCat==='ductrun') h+='<button class="con-add" id="bDrawDuct">'+(mode==='draw_duct'?'Drawing… double-click to finish':'✏️ Draw Run')+'</button>';
    h+='<h3 style="margin-top:14px">Terminals</h3><div class="types">';
    TERMINAL_KINDS.forEach(([key,label])=>{ const on=(paintCat==='terminal'&&activeType===key);
      h+='<div class="chip'+(on?' on':'')+'" data-dcat="terminal" data-dkey="'+key+'"><span class="nm">'+label+'</span></div>'; });
    h+='</div>';
    h+='<h3 style="margin-top:14px">Equipment</h3><div class="types">';
    EQUIPMENT_KINDS.forEach(([key,label])=>{ const on=(paintCat==='equip'&&activeType===key);
      h+='<div class="chip'+(on?' on':'')+'" data-dcat="equip" data-dkey="'+key+'"><span class="nm">'+label+'</span></div>'; });
    h+='</div>';
  }
  h+='</div>';
  const total=ductRuns.length+ductTerminals.length+ductEquipment.length;
  h+='<div class="listblock"><h3>Placed ('+total+')</h3><div id="ductList">';
  ductRuns.forEach(d=>{ h+='<div class="row'+(kind==='run'&&id===d.id?' sel':'')+'" data-dcat="run" data-did="'+d.id+'"><span>'+ductLabel(DUCT_RUN_KINDS,d.system)+' · '+ductLabel(DUCT_FORM_OPTS,effectiveForm(d))+'</span><span>'+(d.cfm!=null?d.cfm+' cfm':'—')+'</span></div>'; });
  ductTerminals.forEach(t=>{ h+='<div class="row'+(kind==='terminal'&&id===t.id?' sel':'')+'" data-dcat="terminal" data-did="'+t.id+'"><span>'+ductLabel(TERMINAL_KINDS,t.kind)+'</span><span>'+(t.cfm!=null?t.cfm+' cfm':'—')+'</span></div>'; });
  ductEquipment.forEach(e=>{ h+='<div class="row'+(kind==='equip'&&id===e.id?' sel':'')+'" data-dcat="equip" data-did="'+e.id+'"><span>'+(e.label||ductLabel(EQUIPMENT_KINDS,e.kind))+'</span><span>'+(e.kind==='AH'?('S:'+(e.supply_cfm!=null?e.supply_cfm:'—')+' R:'+(e.return_cfm!=null?e.return_cfm:'—')):'')+'</span></div>'; });
  h+='</div></div>';
  return h;
}
// Keyboard-driven duct tool selection — a second, faster control surface alongside the
// side-panel chips (requested: the mouse-driven chip+button flow felt clunky). Unmodified
// digits 1-4 arm a run kind (and go straight into draw_duct — no separate "Draw Run"
// click), Shift+1-4 arm a terminal kind, Alt+1-3 arm an equipment kind; either way the tool
// is immediately ready to place. Pressing the same key again toggles it off, mirroring the
// existing chip-click behavior in wireDucts() below.
function armDuctKeyTool(cat, key){
  if(paintCat===cat && activeType===key){ paintCat=null; activeType=null; setMode('select'); }
  else {
    paintCat=cat; activeType=key; selDuctKind=null; selDuctId=null; selDuctVertex=null; tab='ducts';
    if(cat==='ductrun') setMode('draw_duct'); else if(mode==='draw_duct') setMode('select');
  }
  cv.classList.toggle('paint', paintCat==='terminal'||paintCat==='equip');
  renderSide(); draw();
}
function wireDucts(){
  const sizeBtn=document.getElementById('bAutoSizeDucts');
  if(sizeBtn) sizeBtn.onclick=async()=>{
    const orig=sizeBtn.textContent;
    sizeBtn.disabled=true; sizeBtn.textContent='Sizing…';
    try{
      const res=await postJson('/api/size_ducts', buildPayload());
      const runs=allDuctRunsLive(), terms=allDuctTerminalsLive(), equips=allDuctEquipmentLive();
      let nRuns=0, nTerms=0, nEquip=0;
      Object.entries(res.duct_runs||{}).forEach(([id,patch])=>{ const r=runs.find(x=>String(x.id)===id); if(r){ Object.assign(r,patch); nRuns++; } });
      Object.entries(res.duct_terminals||{}).forEach(([id,patch])=>{ const t=terms.find(x=>String(x.id)===id); if(t){ Object.assign(t,patch); nTerms++; } });
      Object.entries(res.duct_equipment||{}).forEach(([id,patch])=>{ const e=equips.find(x=>String(x.id)===id); if(e){ Object.assign(e,patch); nEquip++; } });
      renderSide(); draw();
      showToast('Auto-sized '+nRuns+' run'+(nRuns===1?'':'s')+', '+nTerms+' terminal'+(nTerms===1?'':'s')
        +(nEquip?', '+nEquip+' air handler'+(nEquip===1?'':'s'):'')+'. Anything with "Auto-size" unchecked was left alone.');
    }catch(e){
      showToast('Could not reach the local server to auto-size ducts.\n'
        +'Make sure this was opened via "python3 loadcalc.py parse ..." and that terminal is still running.\n'+e.message, true);
    } finally { sizeBtn.disabled=false; sizeBtn.textContent=orig; }
  };
  document.querySelectorAll('#side [data-dcat][data-dkey]').forEach(el=>el.onclick=()=>{
    const cat=el.dataset.dcat, key=el.dataset.dkey;
    if(paintCat===cat && activeType===key){ paintCat=null; activeType=null; setMode('select'); }
    else { paintCat=cat; activeType=key; selDuctKind=null; selDuctId=null; selDuctVertex=null; if(mode==='draw_duct') setMode('select'); }
    cv.classList.toggle('paint', paintCat==='terminal'||paintCat==='equip');
    renderSide(); draw();
  });
  const drawBtn=document.getElementById('bDrawDuct');
  if(drawBtn) drawBtn.onclick=()=>{ setMode(mode==='draw_duct'?'select':'draw_duct'); renderSide(); };
  document.querySelectorAll('#ductList .row').forEach(el=>el.onclick=()=>{
    selDuctKind=el.dataset.dcat; selDuctId=+el.dataset.did; selDuctVertex=null; paintCat=null; activeType=null; cv.classList.remove('paint'); renderSide(); draw();
  });
  document.querySelectorAll('#side .df').forEach(el=>{
    const ev = el.tagName==='SELECT' ? 'onchange' : 'oninput';
    el[ev]=()=>{
      let item=null;
      if(selDuctKind==='run') item=ductRuns.find(d=>d.id===selDuctId);
      else if(selDuctKind==='terminal') item=ductTerminals.find(t=>t.id===selDuctId);
      else if(selDuctKind==='equip') item=ductEquipment.find(e=>e.id===selDuctId);
      if(!item) return;
      const f=el.dataset.f, v=el.value;
      if(f==='auto') item.auto=el.checked;
      else {
        item[f] = (el.tagName==='SELECT'||f==='label'||f==='size') ? v : (v===''?null:parseFloat(v));
        // A manual edit to anything 📐 Auto-Size Ducts would otherwise compute is a
        // deliberate override — stick with it across future Auto-Size runs until the
        // checkbox above is re-checked (same "seed, don't silently clobber" precedent as
        // seedLoads() elsewhere).
        if(['cfm','width_in','height_in','diameter_in','size'].includes(f)) item.auto=false;
      }
      if(f==='form'||f==='auto') renderSide(); // Rect vs Round/Flex (or the checkbox state) needs a re-render, not just a re-draw
      draw();
    };
  });
  const delBtn=document.getElementById('ductDel');
  if(delBtn) delBtn.onclick=()=>{
    if(selDuctKind==='run') ductRuns=ductRuns.filter(d=>d.id!==selDuctId);
    else if(selDuctKind==='terminal') ductTerminals=ductTerminals.filter(t=>t.id!==selDuctId);
    else if(selDuctKind==='equip') ductEquipment=ductEquipment.filter(e=>e.id!==selDuctId);
    selDuctKind=null; selDuctId=null; selDuctVertex=null; renderSide(); draw();
  };
  const prevBtn=document.getElementById('bStraightenPrev'), nextBtn=document.getElementById('bStraightenNext');
  if(prevBtn) prevBtn.onclick=()=>{ const run=ductRuns.find(d=>d.id===selDuctId); if(run) straightenDuctVertex(run, selDuctVertex, 'previous'); };
  if(nextBtn) nextBtn.onclick=()=>{ const run=ductRuns.find(d=>d.id===selDuctId); if(run) straightenDuctVertex(run, selDuctVertex, 'next'); };
}
function finishDuctDraw(){
  if(draft.length>=2){
    const d={id:nextDuctRunId++, system:activeType, form:'auto', points:draft.slice(), cfm:null,
      width_in:null, height_in:null, diameter_in:null, zone:null, auto:true};
    ductRuns.push(d); selDuctKind='run'; selDuctId=d.id; selDuctVertex=null;
  }
  // Stay armed in draw_duct mode ("pick the pen up, keep drawing") instead of dropping back
  // to select — lets a whole branching system (trunk + branches) be drawn in one continuous
  // session. Enter (or Esc) ends the session; re-clicking Draw Run/a duct-kind chip also does.
  draft=[]; ductPreviewPt=null; renderSide(); draw();
}
// Breaks a run into two independent pieces at interior point `i`, sharing that exact point
// (stays visually continuous — and since they meet endpoint-to-endpoint, findTeePoint
// correctly keeps treating that shared point as a continuation, not a new branch tee).
// Both pieces start as a copy of the parent (same system/form/cfm/size) — the point is to
// let the user then force either piece's Form independently, not to change anything yet.
function splitDuctRun(run, i){
  const twin = Object.assign({}, run, {id: nextDuctRunId++, points: run.points.slice(i)});
  run.points = run.points.slice(0, i+1);
  ductRuns.push(twin);
  selDuctKind='run'; selDuctId=run.id; selDuctVertex=null;
  renderSide(); draw();
}
// DM's "Next"/"Previous" straighten tool: pivot at vertex `i`, using the OTHER segment
// (the one not being adjusted) as the fixed reference direction. Rotates the far endpoint
// of the segment being straightened around the pivot, preserving that segment's own
// length, to whichever of the two perpendicular directions needs the least rotation from
// where it is now (so it never unexpectedly flips to the far side).
// Reference segment for straightening when the pivot is an ENDPOINT of `run` — there's no
// same-run neighbor to compare against there, so look at whatever OTHER run touches this
// point instead. Unlike findTeePoint, endpoint-to-endpoint touches count here too (not
// just a mid-span tee) — even a plain straight continuation into another run is a valid
// thing to straighten against. Returns the connected run's [a,b] segment, or null.
function connectionAt(run, pt){
  let best=null;
  ductRuns.forEach(r=>{
    if(r.id===run.id) return;
    for(let i=0;i<r.points.length-1;i++){
      const a=r.points[i], b=r.points[i+1], cs=closestOnSeg(pt,a,b);
      if(cs.dist>1.5) continue;
      if(!best||cs.dist<best.dist) best=[a,b];
    }
  });
  return best;
}
function straightenDuctVertex(run, i, dir){
  const pts=run.points;
  let pivot, refA, refB, targetIdx;
  if(i>0 && i<pts.length-1){
    // Interior pivot: reference = the OTHER segment at this same run's own vertex.
    pivot=pts[i];
    refA = (dir==='next') ? pts[i-1] : pts[i+1];
    refB = pivot;
    targetIdx = (dir==='next') ? i+1 : i-1;
  } else if(i===0 || i===pts.length-1){
    // Endpoint pivot: no same-run neighbor on the outside — reference = whatever other
    // run this endpoint connects to (a branch tee-in, or a plain end-to-end join).
    pivot=pts[i];
    const seg=connectionAt(run, pivot);
    if(!seg) return;
    [refA,refB]=seg;
    targetIdx = (i===0) ? 1 : pts.length-2;
  } else return;
  // "Closest 90° angle" means the closest of FOUR options relative to the reference —
  // continuing straight (0°/180°, i.e. parallel) or turning a clean corner (±90°) — not
  // always a forced right-angle turn. Picking only the two perpendiculars (as this used
  // to) would "straighten" a nearly-straight run into an unwanted right-angle kink instead
  // of actually straightening it.
  const curPt = pts[targetIdx];
  const rx=refB[0]-refA[0], ry=refB[1]-refA[1], rlen=Math.hypot(rx,ry)||1, refx=rx/rlen, refy=ry/rlen;
  const cx=curPt[0]-pivot[0], cy=curPt[1]-pivot[1], clen=Math.hypot(cx,cy)||1;
  const candidates=[[refx,refy],[-refx,-refy],[-refy,refx],[refy,-refx]];
  let chosen=candidates[0], bestDot=-Infinity;
  for(const c of candidates){ const d=c[0]*cx+c[1]*cy; if(d>bestDot){ bestDot=d; chosen=c; } }
  pts[targetIdx]=[pivot[0]+chosen[0]*clen, pivot[1]+chosen[1]*clen];
  draw();
}

const LOAD_FIELDS=[['lpd','Lighting (W/ft²)'],['epd','Equipment (W/ft²)'],['equip_latent','Equipment Latent (Btu/h) — cooking/dishwashing etc.'],['occ','Occupants'],
  ['vent','Ventilation (CFM) — recalculates when Occupants changes; edit here to override'],
  ['vent_rp','Vent. Rp (cfm/person) — for the Ventilation Schedule report'],
  ['vent_ra','Vent. Ra (cfm/ft²) — for the Ventilation Schedule report'],['inf','Infiltration (ACH)'],
  ['exh_cfm','Exhaust (CFM) — kitchen hood, restroom, general, etc.'],['makeup_pct','Exhaust Makeup, % as new OA (blank/0 = transfer air)'],
  ['htg','Heating SP °F'],['clg','Cooling SP °F'],['rh','Indoor RH %']];
function roomsPanel(){
  const op = selOpening!=null ? openings.find(o=>o.id===selOpening) : null;
  const sel=rooms.find(r=>r.id===selected);
  let h='';
  if(op){
    h+='<div class="selblock"><h3>'+(op.cat==='window'?'Window':'Door')+'</h3>';
    h+='<label>Type</label><select id="oType">'+libOf(op.cat).map(t=>'<option'+(t.name===op.type?' selected':'')+'>'+t.name+'</option>').join('')+'</select>';
    h+='<label>Width (ft)</label><input type="number" step="0.1" min="0" id="oWidth" value="'+op.width+'">';
    h+='<label>Height (ft)</label><input type="number" step="0.1" min="0" id="oHeight" value="'+(op.height!=null?op.height:'')+'">';
    if(op.cat==='window'){
      h+='<label>Sill height (ft)</label><input type="number" step="0.1" min="0" id="oSill" value="'+(op.sill!=null?op.sill:'')+'">';
      h+='<h3 style="margin-top:14px">Shading</h3>';
      h+='<label>Overhang depth (ft)</label><input type="number" step="0.1" min="0" id="oOhProj" value="'+(op.overhang_proj||0)+'">';
      h+='<label>Overhang height above window (ft)</label><input type="number" step="0.1" min="0" id="oOhSep" value="'+(op.overhang_sep||0)+'">';
      h+='<label style="display:flex;align-items:center;gap:6px;margin-top:8px;cursor:pointer"><input type="checkbox" id="oShaded" style="width:auto"'+(op.shaded?' checked':'')+'> Interior shade (blinds)</label>';
      if(op.shaded){
        h+='<div style="font-size:11.5px;color:var(--ink-muted);margin:2px 0 6px">Indoor Attenuation Coefficient — how much of the solar gain the shade blocks (1.0 = none, lower = more shading)</div>';
        h+='<label>IAC at 0° (beam, normal)</label><input type="number" step="0.01" min="0" max="1" id="oIac0" value="'+(op.iac0!=null?op.iac0:0.7)+'">';
        h+='<label>IAC at 60° (beam, grazing)</label><input type="number" step="0.01" min="0" max="1" id="oIac60" value="'+(op.iac60!=null?op.iac60:0.6)+'">';
        h+='<label>IAC diffuse</label><input type="number" step="0.01" min="0" max="1" id="oIacDiff" value="'+(op.iac_diffuse!=null?op.iac_diffuse:0.7)+'">';
      }
    }
    h+='<div style="font-size:12px;color:var(--ink-muted)">Drag it along the wall to reposition · Delete key to remove</div>';
    h+='<button class="danger" id="oDel">Delete this '+op.cat+'</button></div>';
  } else if(sel){
    h+='<div class="selblock"><h3>Selected room</h3>';
    h+='<label>Label</label><input id="fLabel" value="'+sel.label.replace(/"/g,'&quot;')+'">';
    h+='<label>Type</label><select id="fType"><option value=""'+(sel.type?'':' selected')+'></option>'+ROOM_TYPE_LIB.map(t=>'<option'+(t===sel.type?' selected':'')+'>'+t+'</option>').join('')+'</select>';
    h+='<label>Zone</label><select id="fZone"><option value=""'+(sel.zone?'':' selected')+'>— none —</option>'+ZONE_LIB.map(z=>'<option'+(z===sel.zone?' selected':'')+'>'+z+'</option>').join('')+'</select>';
    h+='<label>Roof</label><select id="fRoof">'+ROOF_TYPES.map(t=>'<option'+(t.name===sel.roof?' selected':'')+'>'+t.name+'</option>').join('')+'</select>';
    h+='<label>Floor</label><select id="fFloor">'+FLOOR_TYPES.map(t=>'<option'+(t.name===sel.floor?' selected':'')+'>'+t.name+'</option>').join('')+'</select>';
    h+='<label>Ceiling height (ft)</label><input type="number" step="0.1" id="fCeil" placeholder="e.g. 9" value="'+(sel.ceiling_ft!==''&&sel.ceiling_ft!=null?sel.ceiling_ft:'')+'">';
    h+='<label>Floor-to-floor height (ft)</label><input type="number" step="0.1" id="fFtoF" placeholder="defaults to ceiling ht." value="'+(sel.floor_to_floor_ft!==''&&sel.floor_to_floor_ft!=null?sel.floor_to_floor_ft:'')+'">';
    h+='<div style="font-size:11px;color:var(--ink-muted);margin-top:-6px">Exterior walls run to the structure above, not just the dropped ceiling — set this if there\'s a plenum (floor-to-floor taller than the ceiling height).</div>';
    h+='<label>Floor covering</label><select id="fCarpet"><option value="1"'+(sel.carpet!==false?' selected':'')+'>Carpet</option><option value="0"'+(sel.carpet===false?' selected':'')+'>No carpet (tile / vinyl / concrete)</option></select>';
    h+='<div style="font-size:11px;color:var(--ink-muted);margin-top:-6px">Feeds the RTS zone (ASHRAE Ch.18 Table 19/20) — changes how quickly a heat gain shows up as cooling load, not its size.</div>';
    h+='<div class="area">'+area(sel.points).toFixed(0)+' ft²</div>';
    const L=sel.loads||{}; h+='<div class="loadgrid">';
    LOAD_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input type="number" step="any" class="lf" data-k="'+f[0]+'" value="'+(L[f[0]]!=null?L[f[0]]:'')+'">'; });
    h+='</div>';
    h+='<div class="walls-list"><div class="wl-h">Walls — dir · type · length</div>';
    sel.points.forEach((p,i)=>{ const q=sel.points[(i+1)%sel.points.length]; const len=(Math.hypot(q[0]-p[0],q[1]-p[1])/PPF).toFixed(1); h+='<div class="wl-row"><span>'+edgeOrient(sel,i)+'</span><span>'+sel.walls[i]+'</span><span>'+len+' ft</span></div>'; });
    h+='</div>';
    h+='<div style="font-size:12px;color:var(--ink-muted)">Loads seed from the room type — edit to override. Set North (top bar) so wall directions are right. Corners: drag=move · ✚=add · right/Alt-click=delete.</div>';
    h+='<button class="danger" id="fDel">Delete this room</button></div>';
  } else {
    h+='<div class="selblock"><h3>No selection</h3><div style="font-size:13px;color:var(--ink-muted)">Click a room to edit it, a window/door to adjust it, or <b>+ Add Room</b>. Assign walls, roofs, windows &amp; doors in the <b>Construction</b> tab.</div></div>';
  }
  h+='<div class="listblock"><h3>All rooms ('+rooms.length+')</h3>';
  h+='<input class="filter-box" id="roomFilter" placeholder="Filter rooms…">';
  h+='<div id="list">';
  rooms.forEach(r=>{ h+='<div class="row'+(r.id===selected?' sel':'')+'" data-id="'+r.id+'"><span>'+r.label+'</span><span>'+area(r.points).toFixed(0)+' ft²</span></div>'; });
  h+='</div></div>';
  return h;
}
function wireRooms(){
  const op = selOpening!=null ? openings.find(o=>o.id===selOpening) : null;
  const sel=rooms.find(r=>r.id===selected);
  if(op){
    document.getElementById('oType').onchange=e=>{ op.type=e.target.value; };
    document.getElementById('oWidth').oninput=e=>{ op.width=parseFloat(e.target.value)||0; draw(); };
    document.getElementById('oHeight').oninput=e=>{ op.height=parseFloat(e.target.value)||0; };
    const _s=document.getElementById('oSill'); if(_s) _s.oninput=e=>{ op.sill=parseFloat(e.target.value)||0; };
    const _op=document.getElementById('oOhProj'); if(_op) _op.oninput=e=>{ op.overhang_proj=parseFloat(e.target.value)||0; };
    const _os=document.getElementById('oOhSep'); if(_os) _os.oninput=e=>{ op.overhang_sep=parseFloat(e.target.value)||0; };
    const _sh=document.getElementById('oShaded'); if(_sh) _sh.onchange=e=>{ op.shaded=e.target.checked;
      if(op.shaded){ if(op.iac0==null) op.iac0=0.7; if(op.iac60==null) op.iac60=0.6; if(op.iac_diffuse==null) op.iac_diffuse=0.7; }
      renderSide(); };
    const _i0=document.getElementById('oIac0'); if(_i0) _i0.oninput=e=>{ op.iac0=parseFloat(e.target.value); };
    const _i60=document.getElementById('oIac60'); if(_i60) _i60.oninput=e=>{ op.iac60=parseFloat(e.target.value); };
    const _idf=document.getElementById('oIacDiff'); if(_idf) _idf.oninput=e=>{ op.iac_diffuse=parseFloat(e.target.value); };
    document.getElementById('oDel').onclick=()=>{ openings=openings.filter(x=>x.id!==op.id); selOpening=null; renderSide(); draw(); };
  } else if(sel){
    document.getElementById('fLabel').oninput=e=>{ sel.label=e.target.value; draw(); const row=document.querySelector('#list .row[data-id="'+sel.id+'"] span'); if(row) row.textContent=sel.label; };
    document.getElementById('fType').onchange=e=>{ sel.type=e.target.value; seedLoads(sel); renderSide(); draw(); };
    document.getElementById('fZone').onchange=e=>{ sel.zone=e.target.value; draw(); };
    document.getElementById('fRoof').onchange=e=>{ sel.roof=e.target.value; draw(); };
    document.getElementById('fFloor').onchange=e=>{ sel.floor=e.target.value; draw(); };
    document.getElementById('fCeil').oninput=e=>{ const v=e.target.value.trim(); sel.ceiling_ft = v===''?'':parseFloat(v); };
    document.getElementById('fFtoF').oninput=e=>{ const v=e.target.value.trim(); sel.floor_to_floor_ft = v===''?'':parseFloat(v); };
    document.getElementById('fCarpet').onchange=e=>{ sel.carpet = e.target.value==='1'; };
    document.querySelectorAll('#side .lf').forEach(el=>el.oninput=()=>{
      if(!sel.loads) sel.loads={};
      const v=el.value.trim();
      if(v==='') delete sel.loads[el.dataset.k]; else sel.loads[el.dataset.k]=parseFloat(v);
      // Ventilation CFM is derived from occupant count (cfm/person x occupants + cfm/ft2 x
      // area, from the room type's own rate) — editing occupants without this would leave
      // ventilation (and the latent load it drives) stuck at whatever headcount it was last
      // seeded from, a real gap that inflated a real project's load noticeably.
      if(el.dataset.k==='occ'){
        const vent=ventCfmFromRec(RT_REC[sel.type], sel.loads.occ||0, area(sel.points));
        if(vent!=null){
          sel.loads.vent=vent;
          const ventEl=document.querySelector('#side .lf[data-k="vent"]');
          if(ventEl) ventEl.value=vent;
        }
      }
    });
    document.getElementById('fDel').onclick=delSel;
  }
  document.querySelectorAll('#list .row').forEach(el=>el.onclick=()=>{ selected=+el.dataset.id; selOpening=null; renderSide(); draw(); });
  // Direct DOM show/hide on input, not a renderSide() re-render — re-rendering the panel
  // on every keystroke would recreate this same input and steal its own focus, making it
  // impossible to type more than one character.
  const rf=document.getElementById('roomFilter');
  if(rf) rf.oninput=()=>{
    const q=rf.value.trim().toLowerCase();
    document.querySelectorAll('#list .row').forEach(el=>{
      el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  };
}

function conPanel(){
  let h='<div class="selblock"><h3>Types &amp; assignment</h3><div style="font-size:12px;color:var(--ink-muted)">Pick a type, then click on the plan: <b>walls</b> (edges) for a wall type, <b>rooms</b> for a roof / floor / room / zone type, or a <b>wall</b> to drop a window / door. ＋ adds a type; 📋 duplicates one (a quick start for a similar variant); ✎ edits every field on it at once; ✕ deletes one. Esc stops.</div></div>';
  h+='<div class="listblock">'+typeSection('wall','Wall types')+typeSection('window','Window types')+typeSection('door','Door types')+typeSection('roof','Roof types')+typeSection('floor','Floor types')+typeSection('rtype','Room types')+typeSection('zone','Zones (for per-system block loads)')+'</div>';
  return h;
}
// Field specs for the inline add/edit form — every wall/roof/floor/window/door field shown
// and editable at once, replacing the old one-field-at-a-time prompt() chain. rtype/zone
// keep the simple name-only prompt below (nothing else on them to edit here).
const TYPE_FIELD_SPECS = {
  wall:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark (higher solar absorptance)','checkbox'],['color','Custom plan color','color']],
  roof:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark (higher solar absorptance)','checkbox'],['color','Custom plan color','color']],
  floor: [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark','checkbox'],['color','Custom plan color','color']],
  window:[['name','Name','text'],['u','U-value','number'],['shgc','SHGC (0–1)','number'],['width','Width (ft)','number'],['height','Height (ft)','number'],['color','Custom plan color','color']],
  door:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['width','Width (ft)','number'],['height','Height (ft)','number'],['color','Custom plan color','color']],
};
const TYPE_FIELD_DEFAULTS = {
  wall:{u:0,dark:false}, roof:{u:0,dark:false}, floor:{u:0,dark:false},
  window:{u:0.5,shgc:0.4,width:3,height:4}, door:{u:0,width:3,height:6.83,dark:false},
};
function typeFieldForm(cat, cur, autoColor){
  const specs=TYPE_FIELD_SPECS[cat], defaults=TYPE_FIELD_DEFAULTS[cat]||{};
  let h='<div class="loadgrid">';
  specs.forEach(([key,label,kind])=>{
    const val = cur ? cur[key] : defaults[key];
    if(kind==='checkbox') h+='<label>'+label+'</label><input type="checkbox" class="tf" data-k="'+key+'"'+(val?' checked':'')+'>';
    else if(kind==='color'){
      // Unchecked = no override, falls back to the auto-assigned palette color (pre-filled
      // into the picker so it starts on the color actually showing, not an arbitrary one).
      const has = val!=null && val!=='';
      h+='<label>'+label+'</label><div style="display:flex;align-items:center;gap:6px">'
        +'<input type="checkbox" class="tf-color-on" data-k="'+key+'"'+(has?' checked':'')+' style="width:auto">'
        +'<input type="color" class="tf" data-k="'+key+'" value="'+(has?val:autoColor)+'"'+(has?'':' disabled')+'>'
        +'</div>';
    }
    else h+='<label>'+label+'</label><input class="tf" data-k="'+key+'" type="'+(kind==='number'?'number':'text')+'"'
           +(kind==='number'?' step="any"':'')+' value="'+String(val!=null?val:'').replace(/"/g,'&quot;')+'">';
  });
  h+='</div><div class="tf-row"><button class="tf-save" data-cat="'+cat+'" data-oldname="'
    +(cur?cur.name.replace(/"/g,'&quot;'):'')+'">Save</button><button class="tf-cancel">Cancel</button></div>';
  return h;
}
function readTypeForm(cat){
  const out={};
  TYPE_FIELD_SPECS[cat].forEach(([key,label,kind])=>{
    if(kind==='color'){
      const on=document.querySelector('#side .tf-color-on[data-k="'+key+'"]').checked;
      out[key] = on ? document.querySelector('#side .tf[data-k="'+key+'"]').value : null;
      return;
    }
    const el=document.querySelector('#side .tf[data-k="'+key+'"]');
    out[key] = kind==='checkbox' ? el.checked : kind==='number' ? (parseFloat(el.value)||0) : el.value.trim();
  });
  return out;
}
function typeSection(cat,title){
  const lib=libOf(cat); let h='<h3>'+title+'</h3>';
  if(lib.length>4) h+='<input class="filter-box type-filter" data-cat="'+cat+'" placeholder="Filter '+title.toLowerCase()+'…">';
  h+='<div class="types" data-cat="'+cat+'">';
  lib.forEach((t,i)=>{ const name=(typeof t==='string')?t:t.name; const isNo=((cat==='wall'||cat==='roof'||cat==='floor')&&i===0);
    // Auto-assigned color for this slot, BEFORE any custom override — this is what the color
    // picker in the edit form pre-fills with, so it starts on the color already showing rather
    // than an arbitrary default.
    const autoCol = cat==='window'?'#0284c7' : cat==='door'?'#a16207' : cat==='zone'?'#7c3aed' : cat==='rtype'?'#94a3b8' : (i===0?'#cbd5e1':PALETTE[(i-1)%PALETTE.length]);
    if(editingType && editingType.cat===cat && editingType.name===name){
      h+='<div class="type-form">'+typeFieldForm(cat, t, autoCol)+'</div>'; return;
    }
    // A custom color (set via the type's edit form) always wins over the category default/
    // auto-assigned palette slot — this is what the engineer sees on the plan too, via typeColor().
    const custom = (typeof t==='object' && t.color) ? t.color : null;
    const col = custom || autoCol;
    let info=''; if(typeof t==='object'){ if(t.u) info='U '+t.u; if(cat==='window'&&t.shgc!=null) info+=(info?' · ':'')+'SHGC '+t.shgc;
      if((cat==='window'||cat==='door')&&t.width&&t.height) info+=(info?' · ':'')+t.width+'×'+t.height+'ft'; }
    const on=(paintCat===cat&&activeType===name);
    h+='<div class="chip'+(on?' on':'')+'" data-cat="'+cat+'" data-name="'+name.replace(/"/g,'&quot;')+'">'
      +'<span class="sw" style="background:'+col+'"></span><span class="nm">'+name+'</span><span class="u">'+info+'</span>'
      +(isNo?'':'<span class="copy" data-cat="'+cat+'" data-copy="'+name.replace(/"/g,'&quot;')+'">📋</span>'
             +'<span class="edit" data-cat="'+cat+'" data-edit="'+name.replace(/"/g,'&quot;')+'">✎</span>'
             +'<span class="del" data-cat="'+cat+'" data-del="'+name.replace(/"/g,'&quot;')+'">✕</span>')+'</div>'; });
  h+='</div>';
  if(editingType && editingType.cat===cat && editingType.name===null) h+='<div class="type-form">'+typeFieldForm(cat, null, '#888888')+'</div>';
  else h+='<button class="con-add" data-cat="'+cat+'">＋ Add type</button>';
  return h;
}
function promptTypeFields(cat, cur){
  // rtype/zone only — a single name field, so a plain prompt is still simplest. Every
  // other category (wall/roof/floor/window/door) uses typeFieldForm/readTypeForm instead.
  if(cat==='rtype'){ const nm=prompt('Room type name:', cur||''); return nm ? {name:nm} : null; }
  const nm=prompt('Zone name (e.g. "AHU-1"):', cur||''); return nm ? {name:nm} : null;
}
function editType(cat, oldName){   // rtype/zone rename only (see promptTypeFields)
  const f = promptTypeFields(cat, oldName);
  if(!f) return;
  if(cat==='rtype'){
    const i=ROOM_TYPE_LIB.indexOf(oldName); if(i>=0) ROOM_TYPE_LIB[i]=f.name;
    if(f.name!==oldName){
      rooms.forEach(r=>{ if(r.type===oldName) r.type=f.name; });
      if(RT_REC[oldName]){ RT_REC[f.name]=Object.assign({},RT_REC[oldName],{name:f.name}); delete RT_REC[oldName]; }
    }
  } else {
    const i=ZONE_LIB.indexOf(oldName); if(i>=0) ZONE_LIB[i]=f.name;
    if(f.name!==oldName) rooms.forEach(r=>{ if(r.zone===oldName) r.zone=f.name; });
  }
  if(activeType===oldName) activeType=f.name;
  renderSide(); draw();
}
function startEditType(cat, name){
  if(cat==='rtype'||cat==='zone'){ editType(cat, name); return; }
  editingType={cat, name};
  renderSide();
}
function startAddType(cat){
  if(cat==='rtype'||cat==='zone'){
    const f=promptTypeFields(cat, null); if(!f) return;
    if(cat==='rtype'){ if(!ROOM_TYPE_LIB.includes(f.name)) ROOM_TYPE_LIB.push(f.name); }
    else if(!ZONE_LIB.includes(f.name)) ZONE_LIB.push(f.name);
    renderSide(); return;
  }
  editingType={cat, name:null};
  renderSide();
}
function cancelTypeEdit(){ editingType=null; renderSide(); }
function applyTypeSave(cat, oldName){   // oldName === '' means "adding new"
  const f = readTypeForm(cat);
  if(!f.name){ showToast('Name is required.', true); return; }
  if(!oldName){
    const lib=libOf(cat);
    if(lib.some(t=>t.name===f.name)){ showToast('A '+cat+' type named "'+f.name+'" already exists.', true); return; }
    lib.push(f);
  } else {
    const cur=libOf(cat).find(t=>t.name===oldName);
    Object.assign(cur, f);   // mutate in place — keeps the type's position, no re-paint needed
    if(f.name!==oldName){
      if(cat==='wall') rooms.forEach(r=>{ r.walls=r.walls.map(w=>w===oldName?f.name:w); });
      else if(cat==='roof') rooms.forEach(r=>{ if(r.roof===oldName) r.roof=f.name; });
      else if(cat==='floor') rooms.forEach(r=>{ if(r.floor===oldName) r.floor=f.name; });
      else openings.forEach(o=>{ if(o.cat===cat&&o.type===oldName) o.type=f.name; });   // window/door
      if(activeType===oldName) activeType=f.name;
    }
  }
  editingType=null;
  renderSide(); draw();
}
// Duplicate an existing type as a starting point for a similar variant (e.g. two window
// types that only differ by SHGC) instead of re-entering every field from scratch. Room
// types get their full DM record (lighting/equipment/people/ventilation/etc.) cloned too,
// not just the bare name, since that's the data seedLoads() actually needs.
function uniqueTypeName(base, existingNames){
  let n=base+' (copy)', i=2;
  while(existingNames.includes(n)){ n=base+' (copy '+i+')'; i++; }
  return n;
}
function duplicateType(cat, name){
  if(cat==='rtype'){
    const newName=uniqueTypeName(name, ROOM_TYPE_LIB);
    ROOM_TYPE_LIB.push(newName);
    if(RT_REC[name]) RT_REC[newName]=Object.assign({}, JSON.parse(JSON.stringify(RT_REC[name])), {name:newName});
    renderSide(); return;
  }
  if(cat==='zone'){
    ZONE_LIB.push(uniqueTypeName(name, ZONE_LIB));
    renderSide(); return;
  }
  const lib=libOf(cat), src=lib.find(t=>t.name===name); if(!src) return;
  lib.push(Object.assign({}, src, {name:uniqueTypeName(name, lib.map(t=>t.name))}));
  renderSide();
}
function wireCon(){
  document.querySelectorAll('#side .chip').forEach(el=>el.onclick=()=>{ const cat=el.dataset.cat, name=el.dataset.name;
    if(paintCat===cat&&activeType===name){ paintCat=null; activeType=null; } else { paintCat=cat; activeType=name; }
    hoverEdge=null; cv.classList.toggle('paint',!!paintCat); renderSide(); draw(); });
  document.querySelectorAll('#side .copy').forEach(el=>el.onclick=(ev)=>{ ev.stopPropagation(); duplicateType(el.dataset.cat, el.dataset.copy); });
  document.querySelectorAll('#side .edit').forEach(el=>el.onclick=(ev)=>{ ev.stopPropagation(); startEditType(el.dataset.cat, el.dataset.edit); });
  document.querySelectorAll('#side .del').forEach(el=>el.onclick=(ev)=>{ ev.stopPropagation(); delType(el.dataset.cat, el.dataset.del); });
  document.querySelectorAll('#side .con-add').forEach(el=>el.onclick=()=>{ startAddType(el.dataset.cat); });
  document.querySelectorAll('#side .tf-save').forEach(el=>el.onclick=(ev)=>{ ev.stopPropagation(); applyTypeSave(el.dataset.cat, el.dataset.oldname); });
  document.querySelectorAll('#side .tf-cancel').forEach(el=>el.onclick=(ev)=>{ ev.stopPropagation(); cancelTypeEdit(); });
  document.querySelectorAll('#side .tf-color-on').forEach(el=>el.onchange=()=>{
    const sw=document.querySelector('#side .tf[data-k="'+el.dataset.k+'"]'); if(sw) sw.disabled=!el.checked;
  });
  // Same direct-DOM show/hide as the room filter — avoids a renderSide() re-render
  // stealing focus from the input on every keystroke.
  document.querySelectorAll('#side .type-filter').forEach(el=>el.oninput=()=>{
    const q=el.value.trim().toLowerCase();
    const box=document.querySelector('#side .types[data-cat="'+el.dataset.cat+'"]');
    if(box) box.querySelectorAll('.chip').forEach(chip=>{
      chip.style.display = chip.querySelector('.nm').textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
}
function delType(cat,name){
  if(cat==='wall'){ WALL_TYPES=WALL_TYPES.filter(t=>t.name!==name); rooms.forEach(r=>{ r.walls=r.walls.map(w=>w===name?'No wall load':w); }); }
  else if(cat==='roof'){ ROOF_TYPES=ROOF_TYPES.filter(t=>t.name!==name); rooms.forEach(r=>{ if(r.roof===name) r.roof='No roof load'; }); }
  else if(cat==='window'){ GLASS_TYPES=GLASS_TYPES.filter(t=>t.name!==name); openings=openings.filter(o=>!(o.cat==='window'&&o.type===name)); }
  else if(cat==='door'){ DOOR_TYPES=DOOR_TYPES.filter(t=>t.name!==name); openings=openings.filter(o=>!(o.cat==='door'&&o.type===name)); }
  else if(cat==='floor'){ FLOOR_TYPES=FLOOR_TYPES.filter(t=>t.name!==name); rooms.forEach(r=>{ if(r.floor===name) r.floor='No floor load'; }); }
  else if(cat==='zone'){ ZONE_LIB=ZONE_LIB.filter(n=>n!==name); rooms.forEach(r=>{ if(r.zone===name) r.zone=''; }); }
  else { ROOM_TYPE_LIB=ROOM_TYPE_LIB.filter(n=>n!==name); rooms.forEach(r=>{ if(r.type===name) r.type=''; }); }
  if(activeType===name){ paintCat=null; activeType=null; cv.classList.remove('paint'); }
  renderSide(); draw();
}

function delSel(){ if(selected==null) return; rooms=rooms.filter(r=>r.id!==selected); selected=null; renderSide(); draw(); }
function finishDraw(){ if(draft.length>=3){ const r={id:nextId++, label:'ROOM '+nextId, type:'', zone:'', points:draft.slice(), roof:'No roof load', floor:'No floor load', ceiling_ft:'', floor_to_floor_ft:'', carpet:true, loads:{}, walls:draft.map(function(){return 'No wall load';})}; rooms.push(r); selected=r.id; } setMode('select'); renderSide(); draw(); }

cv.addEventListener('wheel', e=>{ e.preventDefault(); const f=e.deltaY<0?1.1:1/1.1; const [mx,my]=rel(e); const w=s2w(mx,my); view.zoom*=f; view.panX=mx-w[0]*view.zoom; view.panY=my-w[1]*view.zoom; draw(); }, {passive:false});

cv.addEventListener('mousedown', e=>{
  const [mx,my]=rel(e), wp=s2w(mx,my);
  if(mode==='draw'||mode==='draw_duct') return;
  if(mode==='measure'){ ruler={a:wp,b:wp}; drag={type:'measure'}; draw(); return; }
  if(paintCat){
    if(paintCat==='wall'){ const ne=nearestEdge(wp); if(ne && ne.dist*view.zoom<12){ ne.room.walls[ne.i]=activeType; draw(); return; } }
    else if(paintCat==='window'||paintCat==='door'){ const ne=nearestEdge(wp); if(ne && ne.dist*view.zoom<16){
      const ty=libOf(paintCat).find(t=>typeof t==='object'&&t.name===activeType);
      const w=(ty&&ty.width)||3, h=(ty&&ty.height)||(paintCat==='door'?6.83:4);
      const op={id:opId,cat:paintCat,type:activeType,rid:ne.room.id,edge:ne.i,t:edgeParam(ne.room,ne.i,wp),width:w,height:h,sill:(paintCat==='window'?2.5:0)};
      if(paintCat==='window'){ Object.assign(op,{overhang_proj:0,overhang_sep:0,shaded:false}); }
      openings.push(op); selOpening=opId; opId++; selected=null; draw(); renderSide(); return; } }
    else if(paintCat==='terminal'){
      const near=nearestDuctPoint(wp), pt=(near&&near.dist*view.zoom<12)?near.point:[wp[0],wp[1]];
      const t={id:nextTerminalId++, kind:activeType, point:pt, cfm:null, size:null, zone:null, auto:true};
      ductTerminals.push(t); selDuctKind='terminal'; selDuctId=t.id; selDuctVertex=null; draw(); renderSide(); return;
    }
    else if(paintCat==='equip'){
      const near=nearestDuctPoint(wp), pt=(near&&near.dist*view.zoom<12)?near.point:[wp[0],wp[1]];
      const n=ductEquipment.filter(x=>x.kind===activeType).length+1;
      const eq={id:nextEquipId++, kind:activeType, point:pt, label:activeType+'-'+n, zone:null};
      ductEquipment.push(eq); selDuctKind='equip'; selDuctId=eq.id; selDuctVertex=null; draw(); renderSide(); return;
    }
    else if(paintCat==='ductrun'){ /* placed via the "Draw Run" button + draw_duct mode below, not a direct click */ }
    else { for(let i=rooms.length-1;i>=0;i--){ if(pip(wp,rooms[i].points)){ if(paintCat==='roof') rooms[i].roof=activeType; else if(paintCat==='floor') rooms[i].floor=activeType; else if(paintCat==='zone') rooms[i].zone=activeType; else { rooms[i].type=activeType; seedLoads(rooms[i]); } draw(); return; } } }
    drag={type:'pan',last:[mx,my]}; return;   // missed a target → allow pan
  }
  // pick a window/door instance first (they sit on the walls)
  for(let k=openings.length-1;k>=0;k--){ const o=openings[k], r=rooms.find(x=>x.id===o.rid); if(!r) continue; const P=r.points,a=P[o.edge],b=P[(o.edge+1)%P.length]; const s=w2s([a[0]+(b[0]-a[0])*o.t,a[1]+(b[1]-a[1])*o.t]); if(Math.hypot(s[0]-mx,s[1]-my)<Math.max(9,(o.width*PPF*view.zoom)/2)){ selOpening=o.id; selected=null; drag={type:'opening',op:o}; renderSide(); draw(); return; } }
  // Duct terminals/equipment/runs only grab clicks while the Ducts tab is active — they
  // can visually sit right on top of a room, and must not steal clicks meant for room
  // vertices/edges on the Rooms tab.
  if(tab==='ducts'){
    // Vertex handles: only the currently-selected run shows these (see draw()), so only it
    // needs checking, and only before the general run/terminal/equipment hit-tests below.
    // Same gestures as room corners: plain click+drag moves the point, alt-click deletes it
    // (min 2 points left, same "don't go below a valid shape" guard room corners use).
    // Shift-click an INTERIOR point instead splits the run into two pieces there — a
    // duct-specific extra rooms don't need (see contextmenu for the right-click delete twin).
    if(selDuctKind==='run'){ const selRun=ductRuns.find(d=>d.id===selDuctId);
      if(selRun){
        for(let i=0;i<selRun.points.length;i++){ const s=w2s(selRun.points[i]);
          if(Math.hypot(s[0]-mx,s[1]-my)<9){
            if(e.altKey){ if(selRun.points.length>2) selRun.points.splice(i,1); selDuctVertex=null; renderSide(); draw(); }
            else if(e.shiftKey && i>0 && i<selRun.points.length-1) splitDuctRun(selRun,i);
            else { drag={type:'ductvertex',run:selRun,vi:i}; selDuctVertex=i; }
            return; } }
        // edge midpoint: insert a new point there (open polyline — no wraparound past the last point)
        for(let i=0;i<selRun.points.length-1;i++){ const a=selRun.points[i], b=selRun.points[i+1], m=w2s([(a[0]+b[0])/2,(a[1]+b[1])/2]);
          if(Math.hypot(m[0]-mx,m[1]-my)<8){ selRun.points.splice(i+1,0,[(a[0]+b[0])/2,(a[1]+b[1])/2]); drag={type:'ductvertex',run:selRun,vi:i+1}; selDuctVertex=i+1; renderSide(); draw(); return; } }
      }
    }
    for(let k=ductTerminals.length-1;k>=0;k--){ const t=ductTerminals[k]; const s=w2s(t.point); if(Math.hypot(s[0]-mx,s[1]-my)<10){ selDuctKind='terminal'; selDuctId=t.id; selDuctVertex=null; selected=null; selOpening=null; drag={type:'ductpoint',item:t}; renderSide(); draw(); return; } }
    for(let k=ductEquipment.length-1;k>=0;k--){ const eq=ductEquipment[k]; const s=w2s(eq.point); if(Math.hypot(s[0]-mx,s[1]-my)<10){ selDuctKind='equip'; selDuctId=eq.id; selDuctVertex=null; selected=null; selOpening=null; drag={type:'ductpoint',item:eq}; renderSide(); draw(); return; } }
    for(let k=ductRuns.length-1;k>=0;k--){ const d=ductRuns[k]; for(let i=0;i<d.points.length-1;i++){
      if(segDist(wp,d.points[i],d.points[i+1])*view.zoom<8){ selDuctKind='run'; selDuctId=d.id; selDuctVertex=null; selected=null; selOpening=null; drag={type:'ductrun',run:d,last:wp}; renderSide(); draw(); return; } } }
  }
  const sel=rooms.find(r=>r.id===selected);
  if(sel){
    // corner: alt-click deletes (min 3), otherwise start moving it
    for(let i=0;i<sel.points.length;i++){ const s=w2s(sel.points[i]); if(Math.hypot(s[0]-mx,s[1]-my)<9){
      if(e.altKey){ if(sel.points.length>3){ sel.points.splice(i,1); sel.walls.splice(i,1); renderSide(); draw(); } } else { drag={type:'vertex',room:sel,vi:i}; }
      return; } }
    // edge midpoint: insert a new corner there (its two halves inherit the edge's wall type)
    for(let i=0;i<sel.points.length;i++){ const a=sel.points[i], b=sel.points[(i+1)%sel.points.length]; const m=w2s([(a[0]+b[0])/2,(a[1]+b[1])/2]);
      if(Math.hypot(m[0]-mx,m[1]-my)<8){ sel.points.splice(i+1,0,[(a[0]+b[0])/2,(a[1]+b[1])/2]); sel.walls.splice(i+1,0,sel.walls[i]); drag={type:'vertex',room:sel,vi:i+1}; renderSide(); draw(); return; } }
  }
  for(let i=rooms.length-1;i>=0;i--){ if(pip(wp,rooms[i].points)){ selected=rooms[i].id; selOpening=null; drag={type:'room',room:rooms[i],last:wp}; renderSide(); draw(); return; } }
  selected=null; selOpening=null; drag={type:'pan',last:[mx,my]}; renderSide(); draw();
});
window.addEventListener('mousemove', e=>{
  const [mx,my]=rel(e), wp=s2w(mx,my);
  if(paintCat==='wall' && !drag){ const ne=nearestEdge(wp); const nh=(ne&&ne.dist*view.zoom<12)?ne:null;
    const changed=(nh&&(!hoverEdge||hoverEdge.room!==nh.room||hoverEdge.i!==nh.i))||(!nh&&hoverEdge); hoverEdge=nh; if(changed) draw(); }
  if((mode==='draw_duct'||paintCat==='terminal'||paintCat==='equip') && !drag){
    const near=nearestDuctPoint(wp); const nh=(near&&near.dist*view.zoom<12)?near.point:null;
    hoverDuctSnap=nh; ductPreviewPt=(mode==='draw_duct'&&draft.length)?ductNextPoint(wp):null; draw();
  }
  if(!drag) return;
  if(drag.type==='pan'){ view.panX+=mx-drag.last[0]; view.panY+=my-drag.last[1]; drag.last=[mx,my]; }
  else if(drag.type==='vertex'){ drag.room.points[drag.vi]=[wp[0],wp[1]]; if(tab==='rooms') renderSide(); }
  else if(drag.type==='room'){ const dx=wp[0]-drag.last[0], dy=wp[1]-drag.last[1]; drag.room.points=drag.room.points.map(p=>[p[0]+dx,p[1]+dy]); drag.last=wp; if(tab==='rooms') renderSide(); }
  else if(drag.type==='opening'){ const o=drag.op, r=rooms.find(x=>x.id===o.rid); if(r) o.t=edgeParam(r,o.edge,wp); }
  else if(drag.type==='ductpoint'){ drag.item.point=[wp[0],wp[1]]; }
  else if(drag.type==='ductvertex'){ drag.run.points[drag.vi]=[wp[0],wp[1]]; }
  else if(drag.type==='ductrun'){ const dx=wp[0]-drag.last[0], dy=wp[1]-drag.last[1]; drag.run.points=drag.run.points.map(p=>[p[0]+dx,p[1]+dy]); drag.last=wp; }
  else if(drag.type==='measure'){ ruler.b=wp; }
  draw();
});
window.addEventListener('mouseup', ()=>{ drag=null; });
cv.addEventListener('click', e=>{ if((mode!=='draw'&&mode!=='draw_duct')||e.detail>1) return; const [mx,my]=rel(e);
  const wp=s2w(mx,my); draft.push(mode==='draw_duct' ? ductNextPoint(wp) : wp); draw(); });
cv.addEventListener('dblclick', e=>{ if(mode==='draw'){ e.preventDefault(); finishDraw(); } else if(mode==='draw_duct'){ e.preventDefault(); finishDuctDraw(); } });
cv.addEventListener('contextmenu', e=>{ e.preventDefault();
  if(tab==='ducts' && selDuctKind==='run'){ const selRun=ductRuns.find(d=>d.id===selDuctId); if(!selRun||paintCat) return; const [mx,my]=rel(e);
    for(let i=0;i<selRun.points.length;i++){ const s=w2s(selRun.points[i]); if(Math.hypot(s[0]-mx,s[1]-my)<9){ if(selRun.points.length>2) selRun.points.splice(i,1); selDuctVertex=null; renderSide(); draw(); return; } } return; }
  const sel=rooms.find(r=>r.id===selected); if(!sel||paintCat) return; const [mx,my]=rel(e);
  for(let i=0;i<sel.points.length;i++){ const s=w2s(sel.points[i]); if(Math.hypot(s[0]-mx,s[1]-my)<9){ if(sel.points.length>3){ sel.points.splice(i,1); sel.walls.splice(i,1); renderSide(); draw(); } return; } } });
window.addEventListener('keydown', e=>{ if(e.key==='Escape'){ setMode('select'); paintCat=null; activeType=null; hoverEdge=null; ruler=null; editingType=null; cv.classList.remove('paint'); selected=null; selOpening=null; selDuctKind=null; selDuctId=null; selDuctVertex=null; renderSide(); draw(); }
  // "Lift the pen": stop the continuous duct-drawing session without deselecting the duct
  // kind (unlike Esc, which resets everything) — same effect as re-clicking Draw Run.
  if(e.key==='Enter' && mode==='draw_duct' && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){ setMode('select'); renderSide(); draw(); }
  if((e.key==='Delete'||e.key==='Backspace') && !paintCat && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){
    if(selOpening!=null){ openings=openings.filter(o=>o.id!==selOpening); selOpening=null; renderSide(); draw(); }
    else if(selected!=null){ delSel(); }
    else if(selDuctKind==='run'){ ductRuns=ductRuns.filter(d=>d.id!==selDuctId); selDuctKind=null; selDuctId=null; selDuctVertex=null; renderSide(); draw(); }
    else if(selDuctKind==='terminal'){ ductTerminals=ductTerminals.filter(t=>t.id!==selDuctId); selDuctKind=null; selDuctId=null; selDuctVertex=null; renderSide(); draw(); }
    else if(selDuctKind==='equip'){ ductEquipment=ductEquipment.filter(x=>x.id!==selDuctId); selDuctKind=null; selDuctId=null; selDuctVertex=null; renderSide(); draw(); } }
  const dm = !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName) && e.code.match(/^Digit([1-4])$/);
  if(dm){
    const idx=+dm[1]-1;
    let list=null, cat=null;
    if(e.altKey && !e.shiftKey){ list=EQUIPMENT_KINDS; cat='equip'; }
    else if(e.shiftKey && !e.altKey){ list=TERMINAL_KINDS; cat='terminal'; }
    else if(!e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey){ list=DUCT_RUN_KINDS; cat='ductrun'; }
    if(list && list[idx]){ e.preventDefault(); armDuctKeyTool(cat, list[idx][0]); }
  }
  // Straighten Next/Previous (DM's "Next"/"Previous" tool): pivots at the last-touched
  // vertex of the selected run, squaring up the next/previous segment to 90° off the other.
  if((e.key===']'||e.key==='[') && selDuctKind==='run' && selDuctVertex!=null && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){
    const run=ductRuns.find(d=>d.id===selDuctId);
    if(run){ e.preventDefault(); straightenDuctVertex(run, selDuctVertex, e.key===']' ? 'next' : 'previous'); }
  } });

document.getElementById('bTheme').onclick=()=>{
  const next = document.documentElement.getAttribute('data-theme')==='dark' ? 'light' : 'dark';
  localStorage.setItem('loadcalc-theme', next); applyTheme(next);
};
document.getElementById('bDuctView').onclick=()=>{
  ductViewMode = ductViewMode==='engineer' ? 'client' : 'engineer';
  localStorage.setItem('loadcalc-ductview', ductViewMode);
  document.getElementById('bDuctView').textContent = ductViewMode==='engineer' ? '🔧 Engineer' : '📄 Client';
  draw();
};
document.getElementById('bDuctView').textContent = ductViewMode==='engineer' ? '🔧 Engineer' : '📄 Client';
document.getElementById('bAdd').onclick=()=>setMode(mode==='draw'?'select':'draw');
document.getElementById('bMeasure').onclick=()=>setMode(mode==='measure'?'select':'measure');
document.getElementById('bDel').onclick=delSel;
document.getElementById('bFit').onclick=()=>{ fit(); draw(); };
// resize() reads the canvas's on-screen size via getBoundingClientRect(); right at initial
// page load (and occasionally right after a floor switch) the browser hasn't finished
// laying out #main/#cv yet and this comes back 0x0 — fit() against a 0-size canvas computes
// zoom=0, which draws nothing and looks like a blank/broken canvas even though every room
// loaded correctly. Retry on the next frame until there's a real size to fit to.
function fitWhenReady(){
  resize();
  if(VW===0 || VH===0){ requestAnimationFrame(fitWhenReady); return; }
  fit(); draw();
}
// Switch the active floor: stash this floor's live rooms/openings/scale/north back into
// its bucket, then load the target floor's — see the multi-floor state comment above for
// why nothing else in the file needs to change to support this.
function switchFloor(newIdx){
  if(newIdx===currentFloorIdx || newIdx<0 || newIdx>=floorMeta.length) return;
  const cur=floorMeta[currentFloorIdx];
  floorRoomsMap.set(cur.level, rooms);
  floorOpeningsMap.set(cur.level, openings);
  floorDuctRunsMap.set(cur.level, ductRuns);
  floorTerminalsMap.set(cur.level, ductTerminals);
  floorEquipmentMap.set(cur.level, ductEquipment);
  cur.pxPerFt=PPF; cur.north_deg=northDeg;

  currentFloorIdx=newIdx;
  const f=floorMeta[currentFloorIdx];
  rooms=floorRoomsMap.get(f.level)||[];
  openings=floorOpeningsMap.get(f.level)||[];
  ductRuns=floorDuctRunsMap.get(f.level)||[];
  ductTerminals=floorTerminalsMap.get(f.level)||[];
  ductEquipment=floorEquipmentMap.get(f.level)||[];
  PPF=f.pxPerFt; northDeg=f.north_deg||0;
  selected=null; selOpening=null;
  document.getElementById('bNorth').value=northDeg;
  document.getElementById('bFloor').value=String(currentFloorIdx);
  img.onload=()=>{ fitWhenReady(); };
  img.src=f.image;
  renderSide();
  fitWhenReady();   // in case the browser doesn't re-fire onload for a cached/identical src
}
function renderFloorSelect(){
  const sel=document.getElementById('bFloor');
  sel.innerHTML=floorMeta.map((f,i)=>'<option value="'+i+'"'+(i===currentFloorIdx?' selected':'')+'>'+f.label+'</option>').join('');
}
document.getElementById('bFloor').onchange=e=>switchFloor(+e.target.value);
document.getElementById('bNorth').oninput=e=>{ northDeg=parseFloat(e.target.value)||0; draw(); };
document.getElementById('bMatchSqft').onclick=()=>{
  const target=parseFloat(document.getElementById('bTotalSqft').value);
  if(!target || target<=0){ showToast('Enter the building\'s actual total square footage first.', true); return; }
  rescaleToTotalSqft(target);
};
function buildPayload(){
  // Stash the active floor's live edits back before flattening every floor — otherwise
  // whatever floor is on-screen right now would be saved from a stale snapshot.
  floorRoomsMap.set(floorMeta[currentFloorIdx].level, rooms);
  floorOpeningsMap.set(floorMeta[currentFloorIdx].level, openings);
  floorDuctRunsMap.set(floorMeta[currentFloorIdx].level, ductRuns);
  floorTerminalsMap.set(floorMeta[currentFloorIdx].level, ductTerminals);
  floorEquipmentMap.set(floorMeta[currentFloorIdx].level, ductEquipment);
  floorMeta[currentFloorIdx].pxPerFt=PPF;
  floorMeta[currentFloorIdx].north_deg=northDeg;

  const outRooms=[], outOpenings=[], outDuctRuns=[], outTerminals=[], outEquipment=[];
  floorMeta.forEach(f=>{
    (floorRoomsMap.get(f.level)||[]).forEach(r=>{
      outRooms.push({id:r.id,label:r.label,type:r.type,zone:r.zone,level:f.level,
        area_sqft:+polyAreaFt(r.points,f.pxPerFt).toFixed(1),
        ceiling_ft:r.ceiling_ft,floor_to_floor_ft:r.floor_to_floor_ft,carpet:r.carpet,roof:r.roof,floor:r.floor,
        loads:r.loads||{},walls:r.walls,
        wall_orientation:r.points.map((_,i)=>edgeOrientFor(r,i,f.north_deg||0)),
        points:r.points});
    });
    (floorOpeningsMap.get(f.level)||[]).forEach(o=>outOpenings.push(o));
    // level:f.level always wins here (not just a fallback default like the parse-time
    // Object.assign) — a run drawn on this floor is tagged with THIS floor's level
    // regardless of whether the in-memory object happened to carry one already.
    (floorDuctRunsMap.get(f.level)||[]).forEach(d=>outDuctRuns.push(Object.assign({}, d, {level:f.level})));
    (floorTerminalsMap.get(f.level)||[]).forEach(t=>outTerminals.push(Object.assign({}, t, {level:f.level})));
    (floorEquipmentMap.get(f.level)||[]).forEach(e=>outEquipment.push(Object.assign({}, e, {level:f.level})));
  });
  const totalSqft=floorMeta.reduce((t,f)=>t+(floorRoomsMap.get(f.level)||[]).reduce((tt,r)=>tt+polyAreaFt(r.points,f.pxPerFt),0),0);

  return {floors:floorMeta.map(f=>({level:f.level,label:f.label,pxPerFt:f.pxPerFt,north_deg:f.north_deg||0})),
    total_sqft:+totalSqft.toFixed(1),safety_factors:SF,project_settings:PS,pdf_stem:PDF_STEM,wall_types:WALL_TYPES,roof_types:ROOF_TYPES,floor_types:FLOOR_TYPES,glass_types:GLASS_TYPES,door_types:DOOR_TYPES,
    // save the FULL room-type record (lighting/equipment/people/etc.), not just the
    // name — otherwise seedLoads() breaks on every reload/resume (RT_REC[name] comes
    // back undefined, so changing a room's type stops updating its loads at all).
    room_types:ROOM_TYPE_LIB.map(function(n){ return RT_REC[n]||{name:n}; }),
    zones:ZONE_LIB,openings:outOpenings,rooms:outRooms,
    duct_runs:outDuctRuns,duct_terminals:outTerminals,duct_equipment:outEquipment};
}
function downloadJson(payload){
  const b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download=(payload.pdf_stem||'rooms_edited')+'.json'; a.click();
}
async function postJson(path,payload){
  const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await res.json().catch(()=>({}));
  if(!res.ok || data.ok===false) throw new Error(data.error||('server error '+res.status));
  return data;
}
document.getElementById('bSave').onclick=async()=>{
  const btn=document.getElementById('bSave'), payload=buildPayload(), orig=btn.textContent;
  try{ await postJson('/api/save',payload); btn.textContent='Saved ✓'; }
  catch(e){ downloadJson(payload); btn.textContent='Saved (download) ✓'; }
  lastSavedJSON=JSON.stringify(payload);
  setTimeout(()=>btn.textContent=orig,1200);
};
document.getElementById('bRunCalc').onclick=async()=>{
  const btn=document.getElementById('bRunCalc'), payload=buildPayload(), orig=btn.textContent;
  const ductPlanPng=captureDuctPlanImage();
  if(ductPlanPng) payload.duct_plan_png=ductPlanPng;
  btn.disabled=true; btn.textContent='Running…';
  try{
    await postJson('/api/calc',payload);
    lastSavedJSON=JSON.stringify(payload);
    btn.textContent='Done ✓'; setTimeout(()=>btn.textContent=orig,1500);
    window.open('/results.html','_blank');
  }catch(e){
    downloadJson(payload); lastSavedJSON=JSON.stringify(payload); btn.textContent=orig;
    showToast('Could not reach the local server to run the calc (saved the JSON to your downloads instead).\n'
          +'Make sure this was opened via "python3 loadcalc.py parse ..." and that terminal is still running, '
          +'then reload this page at its http://127.0.0.1:PORT address.\n'+e.message, true);
  } finally { btn.disabled=false; }
};

// Unsaved-changes guard: compare the current state against a snapshot taken at
// load and refreshed on every successful Save/Run Load Calc (including the
// download fallback — the user has the file at that point even if the server
// was unreachable). If they differ, warn before the tab closes/navigates away,
// since nothing here auto-saves.
window.addEventListener('beforeunload', e=>{
  if(JSON.stringify(buildPayload())!==lastSavedJSON){ e.preventDefault(); e.returnValue=''; }
});

renderFloorSelect();
document.getElementById('bNorth').value = northDeg;
img.onload=()=>{ fitWhenReady(); };
img.src=floorMeta[currentFloorIdx].image;
window.addEventListener('resize', resize);
new ResizeObserver(()=>resize()).observe(cv);   // keep buffer synced to display size
backfillExhaust();
backfillEquipLatent();
backfillVentRates();
renderSide();
fitWhenReady();
lastSavedJSON = JSON.stringify(buildPayload());   // baseline — resumed edits count as "saved"
</script></body></html>
"""


if __name__ == "__main__":
    pdf = sys.argv[1]
    ppf = float(sys.argv[2])
    mlf = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    page = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    export(pdf, ppf, mlf, page)
