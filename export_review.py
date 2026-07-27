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

    html = HTML.replace("/*__DATA__*/", json.dumps(data))
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
              "safety_factors", "project_settings", "pdf_stem"):
        if k in saved:
            data[k] = saved[k]
        elif k in base:
            data[k] = base[k]

    # A room saved before multi-floor existed has no `level` at all — it belongs
    # to (the only) Floor 1.
    if "rooms" in data:
        for r in data["rooms"]:
            r.setdefault("level", 1)

    # room_types saved before the fix for the seedLoads/RT_REC bug are bare name
    # strings, not the full DM record (lighting/equipment/people/etc.) that loads
    # actually seed from — repair them here by matching against the freshly
    # parsed types file's records (still full), so an old save doesn't
    # permanently lose the ability to auto-seed loads on room-type assignment.
    if "room_types" in data:
        base_by_name = {t["name"]: t for t in (base.get("room_types") or []) if isinstance(t, dict)}
        data["room_types"] = [base_by_name.get(rt, {"name": rt}) if isinstance(rt, str) else rt
                              for rt in data["room_types"]]

    html = HTML.replace("/*__DATA__*/", json.dumps(data))
    with open(out, "w") as f:
        f.write(html)
    print(f"resumed {out}  rooms={len(data.get('rooms', []))}  size={len(html)//1024} KB")


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Load Calc — Room Review</title>
<style>
  * { box-sizing: border-box; }
  html,body { margin:0; height:100%; font-family: -apple-system, system-ui, sans-serif; }
  #app { display:flex; flex-direction:column; height:100vh; }
  #bar { display:flex; align-items:center; gap:8px; padding:8px 12px; background:#1f2937; color:#fff; }
  #bar b { font-size:15px; margin-right:10px; }
  #bar button { background:#374151; color:#fff; border:0; padding:7px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  #bar button:hover { background:#4b5563; }
  #bar button.on { background:#2563eb; }
  #bar button.primary { background:#059669; }
  #bar button.primary:hover { background:#047857; }
  #bar button:disabled { opacity:.6; cursor:default; }
  #bar .sp { flex:1; }
  #bar .stat { font-variant-numeric:tabular-nums; opacity:.9; font-size:13px; }
  #bar .ruler-stat { font-variant-numeric:tabular-nums; font-size:13px; color:#fde68a; margin-right:4px; }
  #main { flex:1; display:flex; min-height:0; overflow:hidden; }
  #cv { flex:1 1 0; min-width:0; min-height:0; background:#f3f4f6; cursor:grab; }
  #cv.draw { cursor:crosshair; }
  #cv.measure { cursor:crosshair; }
  #side { width:380px; flex:0 0 380px; background:#fff; border-left:1px solid #e5e7eb; display:flex; flex-direction:column; overflow:hidden; }
  #side .selblock { padding:14px; flex:0 0 auto; overflow:auto; max-height:55%; }
  #side .listblock { padding:0 14px 14px; flex:1 1 auto; min-height:160px; overflow:auto; border-top:1px solid #eee; }
  #side .listblock h3 { position:sticky; top:0; background:#fff; padding-top:12px; margin-top:0; }
  #side h3 { margin:0 0 8px; font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:#6b7280; }
  #side label { display:block; font-size:12px; color:#6b7280; margin:10px 0 3px; }
  #side input, #side select { width:100%; padding:7px; border:1px solid #d1d5db; border-radius:6px; font-size:14px; }
  #side .area { font-size:26px; font-weight:600; margin:6px 0; }
  #side .danger { background:#fee2e2; color:#b91c1c; border:0; padding:8px; width:100%; border-radius:6px; cursor:pointer; margin-top:12px; }
  #list { margin-top:8px; }
  #list .row { display:flex; justify-content:space-between; padding:8px; border-radius:6px; cursor:pointer; font-size:13px; }
  #list .row:hover { background:#f3f4f6; }
  #list .row.sel { background:#dbeafe; }
  #hint { padding:8px 12px; background:#fffbeb; color:#92400e; font-size:13px; border-top:1px solid #fde68a; display:none; }
  #hint.show { display:block; }
  #cv.paint { cursor:crosshair; }
  .tabs { display:flex; gap:6px; padding:10px 14px 0; flex:0 0 auto; }
  .tab { flex:1; padding:8px; border:0; background:#eef2f7; border-radius:7px 7px 0 0; cursor:pointer; font-size:13px; font-weight:600; color:#475569; }
  .tab.on { background:#1f2937; color:#fff; }
  .types { display:flex; flex-direction:column; gap:5px; margin:6px 0 10px; }
  .chip { display:flex; align-items:center; gap:8px; padding:7px 8px; border:1px solid #e5e7eb; border-radius:8px; cursor:pointer; font-size:12.5px; }
  .chip:hover { background:#f8fafc; }
  .chip.on { border-color:#2563eb; background:#eff6ff; box-shadow:inset 0 0 0 1px #2563eb; }
  .chip .sw { width:14px; height:14px; border-radius:4px; flex:0 0 auto; }
  .chip .nm { flex:1; }
  .chip .u { color:#6b7280; font-variant-numeric:tabular-nums; }
  .chip .edit { color:#cbd5e1; cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .edit:hover { color:#2563eb; }
  .chip .del { color:#cbd5e1; cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .del:hover { color:#ef4444; }
  .chip .copy { color:#cbd5e1; cursor:pointer; padding:0 2px; font-size:11px; }
  .chip .copy:hover { color:#059669; }
  .con-add { background:#f1f5f9; border:1px dashed #cbd5e1; color:#475569; padding:7px; width:100%; border-radius:8px; cursor:pointer; font-size:12.5px; margin-bottom:14px; }
  .type-form { border:1px solid #2563eb; border-radius:8px; padding:8px 10px; margin-bottom:14px; background:#eff6ff; }
  .type-form .loadgrid { margin:4px 0 8px; }
  .type-form .tf-row { display:flex; gap:8px; margin-top:4px; }
  .type-form .tf-save { flex:1; background:#2563eb; color:#fff; border:0; padding:8px; border-radius:6px; cursor:pointer; font-size:12.5px; }
  .type-form .tf-cancel { flex:1; background:#e5e7eb; color:#374151; border:0; padding:8px; border-radius:6px; cursor:pointer; font-size:12.5px; }
  .loadgrid { display:grid; grid-template-columns:1fr 92px; gap:5px 8px; align-items:center; margin:10px 0; }
  .loadgrid label { margin:0; font-size:11.5px; color:#475569; }
  .loadgrid input { padding:5px 6px; }
  #bar .north { font-size:12px; color:#cbd5e1; }
  #bar .north input { width:52px; padding:4px; border:0; border-radius:5px; margin-left:2px; }
  #bar .north select { padding:4px; border:0; border-radius:5px; margin-left:2px; }
  .walls-list { margin:10px 0; font-size:11.5px; }
  .walls-list .wl-h { color:#6b7280; margin-bottom:3px; }
  .walls-list .wl-row { display:grid; grid-template-columns:36px 1fr 52px; gap:6px; padding:2px 0; border-top:1px solid #f1f5f9; }
</style></head>
<body><div id="app">
  <div id="bar">
    <b>Room Review</b>
    <span class="north" title="Switch which floor's plan you're viewing/editing. Each floor keeps its own rooms, drawing scale, and North setting.">Floor <select id="bFloor"></select></span>
    <button id="bAdd">+ Add Room</button>
    <button id="bDel">Delete</button>
    <button id="bFit">Fit</button>
    <button id="bMeasure">📏 Measure</button>
    <span class="north">North° <input id="bNorth" type="number" step="1" value="0"></span>
    <span class="north" title="Recalibrates THIS FLOOR's drawing scale so its rooms drawn so far add up to this — enter this floor's actual total square footage, not a guess.">Total ft² <input id="bTotalSqft" type="number" step="1" placeholder="e.g. 12500"></span>
    <button id="bMatchSqft">Match</button>
    <span class="north" title="Design margin on top of the calculated loads, project-wide. Room = envelope + internal gains; Vent = ventilation/infiltration/exhaust-makeup outdoor air — tracked separately since DM's own report does. Defaults (5/0/10/0%) are pre-filled with a rationale footnoted on the results report; edit here to override for this project.">SF% Clg Room <input id="bSfClgRoom" type="number" step="1" value="5"> Clg Vent <input id="bSfClgVent" type="number" step="1" value="0"> Htg Room <input id="bSfHtgRoom" type="number" step="1" value="10"> Htg Vent <input id="bSfHtgVent" type="number" step="1" value="0"></span>
    <button id="bSave">Save JSON</button>
    <button id="bRunCalc" class="primary">▶ Run Load Calc</button>
    <span class="sp"></span>
    <span class="ruler-stat" id="rulerStat"></span>
    <span class="stat" id="stat"></span>
  </div>
  <div id="main">
    <canvas id="cv"></canvas>
    <div id="side"></div>
  </div>
  <div id="hint"></div>
</div>
<script>const REVIEW_DATA = /*__DATA__*/;</script>
<script>
const D = REVIEW_DATA;
const PDF_STEM = D.pdf_stem || null;   // the dropped PDF's filename (no extension) — names the saved JSON after it

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

const floorRoomsMap = new Map(), floorOpeningsMap = new Map();
floorMeta.forEach(f=>{ floorRoomsMap.set(f.level, []); floorOpeningsMap.set(f.level, []); });
_allRooms.forEach(r=>{ if(!floorRoomsMap.has(r.level)) floorRoomsMap.set(r.level, []); floorRoomsMap.get(r.level).push(r); });
const _roomLevelById = new Map(_allRooms.map(r=>[r.id, r.level]));
_allOpenings.forEach(o=>{ const lvl=_roomLevelById.get(o.rid); if(lvl==null) return;
  if(!floorOpeningsMap.has(lvl)) floorOpeningsMap.set(lvl, []); floorOpeningsMap.get(lvl).push(o); });

let rooms = floorRoomsMap.get(floorMeta[currentFloorIdx].level) || [];
let openings = floorOpeningsMap.get(floorMeta[currentFloorIdx].level) || [];
let PPF = floorMeta[currentFloorIdx].pxPerFt;   // reassignable — "Match total ft²" recalibrates this after drawing

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const img = new Image();
let view = {zoom:1, panX:0, panY:0}, selected=null, mode='select', draft=[], drag=null, VW=0, VH=0;
let ruler=null;   // {a:[x,y], b:[x,y]} in world (px) coords — last/active measurement, or null
let lastSavedJSON=null;   // buildPayload() snapshot as of the last save — set once at load, below
let tab='rooms', paintCat=null, activeType=null, hoverEdge=null, northDeg=(floorMeta[currentFloorIdx].north_deg||0);   // Construction paint state; northDeg = ° CW from screen-up to true north (active floor's)
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
function typeColor(cat,name){ const lib=libOf(cat); const i=lib.findIndex(t=>t.name===name); return i<=0?null:PALETTE[(i-1)%PALETTE.length]; }
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
function nearestEdge(wp){ let best=null; rooms.forEach(r=>{ const P=r.points; for(let i=0;i<P.length;i++){ const d=segDist(wp,P[i],P[(i+1)%P.length]); if(!best||d<best.dist) best={room:r,i:i,dist:d}; } }); return best; }
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

function draw(){
  ctx.clearRect(0,0,VW,VH);
  const _f=floorMeta[currentFloorIdx];
  if(img.complete && img.naturalWidth) ctx.drawImage(img, view.panX, view.panY, _f.pageW*view.zoom, _f.pageH*view.zoom);
  rooms.forEach(r=>{
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
  openings.forEach(o=>{ const r=rooms.find(x=>x.id===o.rid); if(!r) return; const P=r.points, a=P[o.edge], b=P[(o.edge+1)%P.length];
    const pt=[a[0]+(b[0]-a[0])*o.t, a[1]+(b[1]-a[1])*o.t]; const d=uvec(b[0]-a[0],b[1]-a[1]); const half=(o.width*PPF)/2;
    const s1=w2s([pt[0]-d[0]*half,pt[1]-d[1]*half]), s2=w2s([pt[0]+d[0]*half,pt[1]+d[1]*half]);
    const col=o.cat==='window'?'#0284c7':'#a16207', on=(selOpening===o.id);
    ctx.beginPath(); ctx.moveTo(s1[0],s1[1]); ctx.lineTo(s2[0],s2[1]); ctx.strokeStyle=on?'#111827':col; ctx.lineWidth=on?8:5.5; ctx.stroke();
    const sd=uvec(s2[0]-s1[0],s2[1]-s1[1]), sn=[-sd[1],sd[0]];
    [s1,s2].forEach(s=>{ ctx.beginPath(); ctx.moveTo(s[0]-sn[0]*4,s[1]-sn[1]*4); ctx.lineTo(s[0]+sn[0]*4,s[1]+sn[1]*4); ctx.strokeStyle=on?'#111827':col; ctx.lineWidth=2; ctx.stroke(); });
  });
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

const HINTS = {draw:'Drawing a room: click to drop each corner, double-click to finish, Esc to cancel.',
  measure:'Measure: click and drag between two points to see the distance. Esc to clear.'};
function setMode(m){ mode=m; cv.classList.toggle('draw',m==='draw'); cv.classList.toggle('measure',m==='measure');
  document.getElementById('bAdd').classList.toggle('on',m==='draw');
  document.getElementById('bMeasure').classList.toggle('on',m==='measure');
  const hint=document.getElementById('hint'); hint.textContent=HINTS[m]||''; hint.classList.toggle('show',!!HINTS[m]);
  if(m!=='draw') draft=[]; }

function renderSide(){
  const s=document.getElementById('side');
  let h='<div class="tabs"><button class="tab'+(tab==='rooms'?' on':'')+'" data-tab="rooms">Rooms</button>'
       +'<button class="tab'+(tab==='con'?' on':'')+'" data-tab="con">Construction</button>'
       +'<button class="tab'+(tab==='proj'?' on':'')+'" data-tab="proj">Project / Weather</button></div>';
  h += (tab==='rooms') ? roomsPanel() : (tab==='con') ? conPanel() : projectPanel();
  s.innerHTML=h;
  s.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{ tab=b.dataset.tab; paintCat=null; activeType=null; hoverEdge=null; cv.classList.remove('paint'); renderSide(); draw(); });
  if(tab==='rooms') wireRooms(); else if(tab==='con') wireCon(); else wireProject();
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
function projectPanel(){
  let h='<div class="selblock"><h3>Project / Weather</h3>';
  h+='<div style="font-size:12px;color:#6b7280;margin-bottom:8px">Design conditions for this project\'s actual location — sourced from ASHRAE Fundamentals Ch.14 (or an equivalent regional table); there\'s no live weather lookup here. Manual values entered here take priority over any bundled station match and over the engine\'s Florida-representative defaults.</div>';
  h+='<div class="loadgrid">';
  PROJECT_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input class="pf" data-k="'+f[0]+'" value="'+(PS[f[0]]!=null?String(PS[f[0]]).replace(/"/g,'&quot;'):'')+'">'; });
  h+='</div></div>';
  return h;
}
function wireProject(){
  document.querySelectorAll('#side .pf').forEach(el=>el.oninput=()=>{
    const v=el.value.trim();
    if(v==='') delete PS[el.dataset.k]; else PS[el.dataset.k]=v;
  });
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
        h+='<div style="font-size:11.5px;color:#6b7280;margin:2px 0 6px">Indoor Attenuation Coefficient — how much of the solar gain the shade blocks (1.0 = none, lower = more shading)</div>';
        h+='<label>IAC at 0° (beam, normal)</label><input type="number" step="0.01" min="0" max="1" id="oIac0" value="'+(op.iac0!=null?op.iac0:0.7)+'">';
        h+='<label>IAC at 60° (beam, grazing)</label><input type="number" step="0.01" min="0" max="1" id="oIac60" value="'+(op.iac60!=null?op.iac60:0.6)+'">';
        h+='<label>IAC diffuse</label><input type="number" step="0.01" min="0" max="1" id="oIacDiff" value="'+(op.iac_diffuse!=null?op.iac_diffuse:0.7)+'">';
      }
    }
    h+='<div style="font-size:12px;color:#6b7280">Drag it along the wall to reposition · Delete key to remove</div>';
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
    h+='<div style="font-size:11px;color:#6b7280;margin-top:-6px">Exterior walls run to the structure above, not just the dropped ceiling — set this if there\'s a plenum (floor-to-floor taller than the ceiling height).</div>';
    h+='<label>Floor covering</label><select id="fCarpet"><option value="1"'+(sel.carpet!==false?' selected':'')+'>Carpet</option><option value="0"'+(sel.carpet===false?' selected':'')+'>No carpet (tile / vinyl / concrete)</option></select>';
    h+='<div style="font-size:11px;color:#6b7280;margin-top:-6px">Feeds the RTS zone (ASHRAE Ch.18 Table 19/20) — changes how quickly a heat gain shows up as cooling load, not its size.</div>';
    h+='<div class="area">'+area(sel.points).toFixed(0)+' ft²</div>';
    const L=sel.loads||{}; h+='<div class="loadgrid">';
    LOAD_FIELDS.forEach(f=>{ h+='<label>'+f[1]+'</label><input type="number" step="any" class="lf" data-k="'+f[0]+'" value="'+(L[f[0]]!=null?L[f[0]]:'')+'">'; });
    h+='</div>';
    h+='<div class="walls-list"><div class="wl-h">Walls — dir · type · length</div>';
    sel.points.forEach((p,i)=>{ const q=sel.points[(i+1)%sel.points.length]; const len=(Math.hypot(q[0]-p[0],q[1]-p[1])/PPF).toFixed(1); h+='<div class="wl-row"><span>'+edgeOrient(sel,i)+'</span><span>'+sel.walls[i]+'</span><span>'+len+' ft</span></div>'; });
    h+='</div>';
    h+='<div style="font-size:12px;color:#6b7280">Loads seed from the room type — edit to override. Set North (top bar) so wall directions are right. Corners: drag=move · ✚=add · right/Alt-click=delete.</div>';
    h+='<button class="danger" id="fDel">Delete this room</button></div>';
  } else {
    h+='<div class="selblock"><h3>No selection</h3><div style="font-size:13px;color:#6b7280">Click a room to edit it, a window/door to adjust it, or <b>+ Add Room</b>. Assign walls, roofs, windows &amp; doors in the <b>Construction</b> tab.</div></div>';
  }
  h+='<div class="listblock"><h3>All rooms ('+rooms.length+')</h3><div id="list">';
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
}

function conPanel(){
  let h='<div class="selblock"><h3>Types &amp; assignment</h3><div style="font-size:12px;color:#6b7280">Pick a type, then click on the plan: <b>walls</b> (edges) for a wall type, <b>rooms</b> for a roof / floor / room / zone type, or a <b>wall</b> to drop a window / door. ＋ adds a type; 📋 duplicates one (a quick start for a similar variant); ✎ edits every field on it at once; ✕ deletes one. Esc stops.</div></div>';
  h+='<div class="listblock">'+typeSection('wall','Wall types')+typeSection('window','Window types')+typeSection('door','Door types')+typeSection('roof','Roof types')+typeSection('floor','Floor types')+typeSection('rtype','Room types')+typeSection('zone','Zones (for per-system block loads)')+'</div>';
  return h;
}
// Field specs for the inline add/edit form — every wall/roof/floor/window/door field shown
// and editable at once, replacing the old one-field-at-a-time prompt() chain. rtype/zone
// keep the simple name-only prompt below (nothing else on them to edit here).
const TYPE_FIELD_SPECS = {
  wall:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark (higher solar absorptance)','checkbox']],
  roof:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark (higher solar absorptance)','checkbox']],
  floor: [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['dark','Dark','checkbox']],
  window:[['name','Name','text'],['u','U-value','number'],['shgc','SHGC (0–1)','number'],['width','Width (ft)','number'],['height','Height (ft)','number']],
  door:  [['name','Name','text'],['u','U-value (Btu/h·ft²·°F), 0 = none','number'],['width','Width (ft)','number'],['height','Height (ft)','number']],
};
const TYPE_FIELD_DEFAULTS = {
  wall:{u:0,dark:false}, roof:{u:0,dark:false}, floor:{u:0,dark:false},
  window:{u:0.5,shgc:0.4,width:3,height:4}, door:{u:0,width:3,height:6.83,dark:false},
};
function typeFieldForm(cat, cur){
  const specs=TYPE_FIELD_SPECS[cat], defaults=TYPE_FIELD_DEFAULTS[cat]||{};
  let h='<div class="loadgrid">';
  specs.forEach(([key,label,kind])=>{
    const val = cur ? cur[key] : defaults[key];
    if(kind==='checkbox') h+='<label>'+label+'</label><input type="checkbox" class="tf" data-k="'+key+'"'+(val?' checked':'')+'>';
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
    const el=document.querySelector('#side .tf[data-k="'+key+'"]');
    out[key] = kind==='checkbox' ? el.checked : kind==='number' ? (parseFloat(el.value)||0) : el.value.trim();
  });
  return out;
}
function typeSection(cat,title){
  const lib=libOf(cat); let h='<h3>'+title+'</h3><div class="types">';
  lib.forEach((t,i)=>{ const name=(typeof t==='string')?t:t.name; const isNo=((cat==='wall'||cat==='roof'||cat==='floor')&&i===0);
    if(editingType && editingType.cat===cat && editingType.name===name){
      h+='<div class="type-form">'+typeFieldForm(cat, t)+'</div>'; return;
    }
    const col = cat==='window'?'#0284c7' : cat==='door'?'#a16207' : cat==='zone'?'#7c3aed' : cat==='rtype'?'#94a3b8' : (i===0?'#cbd5e1':PALETTE[(i-1)%PALETTE.length]);
    let info=''; if(typeof t==='object'){ if(t.u) info='U '+t.u; if(cat==='window'&&t.shgc!=null) info+=(info?' · ':'')+'SHGC '+t.shgc;
      if((cat==='window'||cat==='door')&&t.width&&t.height) info+=(info?' · ':'')+t.width+'×'+t.height+'ft'; }
    const on=(paintCat===cat&&activeType===name);
    h+='<div class="chip'+(on?' on':'')+'" data-cat="'+cat+'" data-name="'+name.replace(/"/g,'&quot;')+'">'
      +'<span class="sw" style="background:'+col+'"></span><span class="nm">'+name+'</span><span class="u">'+info+'</span>'
      +(isNo?'':'<span class="copy" data-cat="'+cat+'" data-copy="'+name.replace(/"/g,'&quot;')+'">📋</span>'
             +'<span class="edit" data-cat="'+cat+'" data-edit="'+name.replace(/"/g,'&quot;')+'">✎</span>'
             +'<span class="del" data-cat="'+cat+'" data-del="'+name.replace(/"/g,'&quot;')+'">✕</span>')+'</div>'; });
  h+='</div>';
  if(editingType && editingType.cat===cat && editingType.name===null) h+='<div class="type-form">'+typeFieldForm(cat, null)+'</div>';
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
  if(!f.name){ alert('Name is required.'); return; }
  if(!oldName){
    const lib=libOf(cat);
    if(lib.some(t=>t.name===f.name)){ alert('A '+cat+' type named "'+f.name+'" already exists.'); return; }
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
  if(mode==='draw') return;
  if(mode==='measure'){ ruler={a:wp,b:wp}; drag={type:'measure'}; draw(); return; }
  if(paintCat){
    if(paintCat==='wall'){ const ne=nearestEdge(wp); if(ne && ne.dist*view.zoom<12){ ne.room.walls[ne.i]=activeType; draw(); return; } }
    else if(paintCat==='window'||paintCat==='door'){ const ne=nearestEdge(wp); if(ne && ne.dist*view.zoom<16){
      const ty=libOf(paintCat).find(t=>typeof t==='object'&&t.name===activeType);
      const w=(ty&&ty.width)||3, h=(ty&&ty.height)||(paintCat==='door'?6.83:4);
      const op={id:opId,cat:paintCat,type:activeType,rid:ne.room.id,edge:ne.i,t:edgeParam(ne.room,ne.i,wp),width:w,height:h,sill:(paintCat==='window'?2.5:0)};
      if(paintCat==='window'){ Object.assign(op,{overhang_proj:0,overhang_sep:0,shaded:false}); }
      openings.push(op); selOpening=opId; opId++; selected=null; draw(); renderSide(); return; } }
    else { for(let i=rooms.length-1;i>=0;i--){ if(pip(wp,rooms[i].points)){ if(paintCat==='roof') rooms[i].roof=activeType; else if(paintCat==='floor') rooms[i].floor=activeType; else if(paintCat==='zone') rooms[i].zone=activeType; else { rooms[i].type=activeType; seedLoads(rooms[i]); } draw(); return; } } }
    drag={type:'pan',last:[mx,my]}; return;   // missed a target → allow pan
  }
  // pick a window/door instance first (they sit on the walls)
  for(let k=openings.length-1;k>=0;k--){ const o=openings[k], r=rooms.find(x=>x.id===o.rid); if(!r) continue; const P=r.points,a=P[o.edge],b=P[(o.edge+1)%P.length]; const s=w2s([a[0]+(b[0]-a[0])*o.t,a[1]+(b[1]-a[1])*o.t]); if(Math.hypot(s[0]-mx,s[1]-my)<Math.max(9,(o.width*PPF*view.zoom)/2)){ selOpening=o.id; selected=null; drag={type:'opening',op:o}; renderSide(); draw(); return; } }
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
  if(!drag) return;
  if(drag.type==='pan'){ view.panX+=mx-drag.last[0]; view.panY+=my-drag.last[1]; drag.last=[mx,my]; }
  else if(drag.type==='vertex'){ drag.room.points[drag.vi]=[wp[0],wp[1]]; if(tab==='rooms') renderSide(); }
  else if(drag.type==='room'){ const dx=wp[0]-drag.last[0], dy=wp[1]-drag.last[1]; drag.room.points=drag.room.points.map(p=>[p[0]+dx,p[1]+dy]); drag.last=wp; if(tab==='rooms') renderSide(); }
  else if(drag.type==='opening'){ const o=drag.op, r=rooms.find(x=>x.id===o.rid); if(r) o.t=edgeParam(r,o.edge,wp); }
  else if(drag.type==='measure'){ ruler.b=wp; }
  draw();
});
window.addEventListener('mouseup', ()=>{ drag=null; });
cv.addEventListener('click', e=>{ if(mode!=='draw'||e.detail>1) return; const [mx,my]=rel(e); draft.push(s2w(mx,my)); draw(); });
cv.addEventListener('dblclick', e=>{ if(mode==='draw'){ e.preventDefault(); finishDraw(); } });
cv.addEventListener('contextmenu', e=>{ e.preventDefault(); const sel=rooms.find(r=>r.id===selected); if(!sel||paintCat) return; const [mx,my]=rel(e);
  for(let i=0;i<sel.points.length;i++){ const s=w2s(sel.points[i]); if(Math.hypot(s[0]-mx,s[1]-my)<9){ if(sel.points.length>3){ sel.points.splice(i,1); sel.walls.splice(i,1); renderSide(); draw(); } return; } } });
window.addEventListener('keydown', e=>{ if(e.key==='Escape'){ setMode('select'); paintCat=null; activeType=null; hoverEdge=null; ruler=null; editingType=null; cv.classList.remove('paint'); selected=null; selOpening=null; renderSide(); draw(); }
  if((e.key==='Delete'||e.key==='Backspace') && !paintCat && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){
    if(selOpening!=null){ openings=openings.filter(o=>o.id!==selOpening); selOpening=null; renderSide(); draw(); }
    else if(selected!=null){ delSel(); } } });

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
  cur.pxPerFt=PPF; cur.north_deg=northDeg;

  currentFloorIdx=newIdx;
  const f=floorMeta[currentFloorIdx];
  rooms=floorRoomsMap.get(f.level)||[];
  openings=floorOpeningsMap.get(f.level)||[];
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
document.getElementById('bSfClgRoom').oninput=e=>{ SF.clg_room=parseFloat(e.target.value)||0; };
document.getElementById('bSfClgVent').oninput=e=>{ SF.clg_vent=parseFloat(e.target.value)||0; };
document.getElementById('bSfHtgRoom').oninput=e=>{ SF.htg_room=parseFloat(e.target.value)||0; };
document.getElementById('bSfHtgVent').oninput=e=>{ SF.htg_vent=parseFloat(e.target.value)||0; };
document.getElementById('bMatchSqft').onclick=()=>{
  const target=parseFloat(document.getElementById('bTotalSqft').value);
  if(!target || target<=0){ alert('Enter the building\'s actual total square footage first.'); return; }
  rescaleToTotalSqft(target);
};
function buildPayload(){
  // Stash the active floor's live edits back before flattening every floor — otherwise
  // whatever floor is on-screen right now would be saved from a stale snapshot.
  floorRoomsMap.set(floorMeta[currentFloorIdx].level, rooms);
  floorOpeningsMap.set(floorMeta[currentFloorIdx].level, openings);
  floorMeta[currentFloorIdx].pxPerFt=PPF;
  floorMeta[currentFloorIdx].north_deg=northDeg;

  const outRooms=[], outOpenings=[];
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
  });
  const totalSqft=floorMeta.reduce((t,f)=>t+(floorRoomsMap.get(f.level)||[]).reduce((tt,r)=>tt+polyAreaFt(r.points,f.pxPerFt),0),0);

  return {floors:floorMeta.map(f=>({level:f.level,label:f.label,pxPerFt:f.pxPerFt,north_deg:f.north_deg||0})),
    total_sqft:+totalSqft.toFixed(1),safety_factors:SF,project_settings:PS,pdf_stem:PDF_STEM,wall_types:WALL_TYPES,roof_types:ROOF_TYPES,floor_types:FLOOR_TYPES,glass_types:GLASS_TYPES,door_types:DOOR_TYPES,
    // save the FULL room-type record (lighting/equipment/people/etc.), not just the
    // name — otherwise seedLoads() breaks on every reload/resume (RT_REC[name] comes
    // back undefined, so changing a room's type stops updating its loads at all).
    room_types:ROOM_TYPE_LIB.map(function(n){ return RT_REC[n]||{name:n}; }),
    zones:ZONE_LIB,openings:outOpenings,rooms:outRooms};
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
  btn.disabled=true; btn.textContent='Running…';
  try{
    await postJson('/api/calc',payload);
    lastSavedJSON=JSON.stringify(payload);
    btn.textContent='Done ✓'; setTimeout(()=>btn.textContent=orig,1500);
    window.open('/results.html','_blank');
  }catch(e){
    downloadJson(payload); lastSavedJSON=JSON.stringify(payload); btn.textContent=orig;
    alert('Could not reach the local server to run the calc (saved the JSON to your downloads instead).\n\n'
          +'Make sure this was opened via "python3 loadcalc.py parse ..." and that terminal is still running, '
          +'then reload this page at its http://127.0.0.1:PORT address.\n\n'+e.message);
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
document.getElementById('bSfClgRoom').value = SF.clg_room;
document.getElementById('bSfClgVent').value = SF.clg_vent;
document.getElementById('bSfHtgRoom').value = SF.htg_room;
document.getElementById('bSfHtgVent').value = SF.htg_vent;
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
