"""Generate a self-contained review UI (review.html) seeded with the parser's
rooms over the rendered floor plan. Open the file in a browser to add / delete /
move / resize / relabel rooms with live area readouts.

    python3 export_review.py "sample_drawings/your-plan.pdf" 18.0 [min_len] [page]
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


HTML = (Path(__file__).parent / "templates" / "review.html").read_text()


if __name__ == "__main__":
    pdf = sys.argv[1]
    ppf = float(sys.argv[2])
    mlf = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    page = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    export(pdf, ppf, mlf, page)
