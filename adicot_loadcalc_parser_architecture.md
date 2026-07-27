# Adicot Load Calc Parser — Full Architecture Document
> For use with Claude Code. Build in order. Each phase is independently testable.

---

## Project Overview

A web application that accepts a CAD-exported PDF of architectural drawings, parses the vector geometry to extract rooms, dimensions, windows, and doors, and pre-populates the Adicot **RTS load-calculation engine** — which we build ourselves (Radiant Time Series, ASHRAE Fundamentals Ch. 18), validated against the bundled ASHRAE example workbooks (Phase 11). Anything the parser cannot determine is surfaced to the engineer in a review UI for manual correction before the calc runs.

**Core constraint:** No external AI/ML APIs. All parsing is deterministic geometry + OCR on local/open-source tools. The stack must be fully self-owned.

**Primary target: commercial buildings in Florida.** Residential is still supported
(the `room_type` map and `building_type` flag handle both), but defaults, the
space-type load tables, the code references, and the review UX are tuned for
**Florida commercial** first. Practical implications carried through this doc:
load methodology follows the ASHRAE Handbook / ACCA Manual N path (RTS-style)
rather than residential Manual J; the calc must produce a **sensible + latent
split** because Florida is cooling- and humidity-driven, not heating-driven;
commercial energy compliance is **ASHRAE 90.1-2019** via the Florida Building Code
– Energy Conservation (2023, 8th ed.) and ventilation is **ASHRAE 62.1**; envelope
defaults assume **CMU walls, flat roofs, slab-on-grade**; internal gains and
ventilation frequently dominate the envelope and are driven by space type. See
[Florida specifics](#florida-specifics-primary-market) in Phase 7.5 for the climate
zones, design conditions, and defaults. Where a single default can't serve both
markets, the commercial/Florida value wins and the other case is flagged.

---

## Integration Target — the Adicot app app

**Built standalone, destined to become a tab in the existing Adicot app Flask
app.** That app already owns everything around the load calc — Wix CMS job data,
Google Drive, QuickBooks, HTTP auth, per-job JSON storage, and the ReportLab
deliverables. We build and prove the parser + RTS engine on their own (fast, no
entanglement), then fold them in as pure-logic modules behind a thin route, exactly
how `hvac_pipeline.py` lives there today.

### Where it plugs in (real routes/templates)

The app is a per-job tab strip (`templates/_tabs.html`). The flow is:

```
/  index (Jobs-in-CMS landing)  →  select job  (job_id = Wix item id)
   └─ /job/<id>/star  job_star  → ★ W/O page (work order from Wix snapshot)
        └─ NEW: ★ W/O → [ Load Calc ] → PDF → Duct → Charts → Specs → Equipment → QC
```

- **New tab "Load Calc"**, placed immediately after **★ W/O** in `_tabs.html`.
- **New route** `GET /job/<job_id>/loadcalc` → `job_loadcalc` → `job_loadcalc.html`,
  plus POSTs `/loadcalc/upload`, `/loadcalc/save`, `/loadcalc/compute`. The tab is the
  "program that runs the load calc": upload the CAD PDF → review extracted rooms &
  thermal inputs (the intermediate page) → RTS engine computes the loads.
- It **inherits the job context for free** — `job_id`, `meta.json` (project address,
  engineer info, Wix work-order snapshot), the `jobs/<job_id>/` workspace, and auth.
  No re-collecting project identity; reuse `_job_dir(job_id)`.

### What it replaces, and what it reuses

Today the **Load deliverable** (`<project>-Load.pdf`) is built by `hvac_pipeline`
from a **Design Master HTML export**. The RTS engine **replaces Design Master as the
source of computed loads** — it produces the same load data structure (the
`report.json` equivalent) from the CAD takeoff, so the existing Load PDF and every
load-derived downstream (tonnage → supply CFM → air balance, equipment selection,
duct sizing, cooling-breakdown charts) read from *our* numbers instead of DM's. The
Load PDF is the first replacement target; the load-derived tabs follow.

Reused unchanged: ReportLab rendering + `pdf_combine`, Drive push, auth, storage.

### Provider seams (so standalone → integrated is a config swap)

Anything Adicot already owns is reached through a small interface, never hardcoded.
Standalone returns a local seed; integrated, the same interface points at Adicot.

```python
class AssemblyProvider(Protocol):
    def wall(self, name: str) -> dict: ...   # standalone: WALL_ASSEMBLIES seed
    def roof(self, name: str) -> dict: ...   # integrated: Adicot's loaded materials
    def floor(self, name: str) -> dict: ...  #             (Wix CMS / materials store)

class ProjectProvider(Protocol):
    def info(self, job_id: str) -> ProjectInfo: ...  # standalone: upload form
                                                     # integrated: meta.json + Wix snapshot
```

> **Building materials are Adicot's, not ours.** The `WALL/ROOF/FLOOR_ASSEMBLIES`
> dicts in Phase 7.5 are a dev stand-in only — at integration the materials are
> already loaded on the Adicot side, and the `AssemblyProvider` reads them there.
> Keep the RTS engine pure (geometry + thermal in → loads out, no Flask/Wix/storage)
> — that purity is what makes both the ASHRAE validation and this drop-in clean.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend framework | Flask (existing) |
| PDF vector extraction | `pymupdf` (fitz) |
| Geometry engine | `shapely` |
| Graph / polygon detection | `networkx` |
| OCR (fallback for raster text) | `pytesseract` + Tesseract |
| Dimension text parsing | Python `re` (regex) |
| Frontend review UI | Vanilla JS + HTML Canvas (or lightweight React) |
| Data format between layers | JSON |
| Deployment | Render (existing) |

**Install additions to `requirements.txt`:**
```
pymupdf
shapely
networkx
pytesseract
Pillow
numpy
```

---

## Repository Structure

```
adicot-Load-calc-doc/
│
├── app.py                        # Existing Flask app — add new routes here
├── requirements.txt              # Add new dependencies
│
├── parser/                       # All PDF parsing logic lives here
│   ├── __init__.py
│   ├── pdf_extractor.py          # Step 1: Raw vector/text extraction from PDF
│   ├── wall_detector.py          # Step 2: Identify wall line segments
│   ├── room_builder.py           # Step 3: Build enclosed room polygons
│   ├── opening_detector.py       # Step 4: Detect windows and doors
│   ├── text_parser.py            # Step 5: Parse room labels and dimensions
│   ├── scale_calibrator.py       # Step 6: Establish px-to-ft scale
│   ├── north_arrow.py            # Step 7: Detect north arrow / orientation
│   └── models.py                 # Dataclasses: Room, Wall, Window, Door, Drawing
│
├── static/
│   ├── js/
│   │   ├── review_ui.js          # Main review interface logic
│   │   ├── canvas_renderer.js    # Draws floor plan + overlays on HTML canvas
│   │   └── room_editor.js        # Room/window edit panel logic
│   └── css/
│       └── review.css
│
├── templates/
│   ├── upload.html               # PDF upload page
│   ├── review.html               # Engineer review/correction UI
│   └── (existing templates)
│
├── tests/
│   ├── test_pdf_extractor.py
│   ├── test_wall_detector.py
│   ├── test_room_builder.py
│   ├── test_opening_detector.py
│   ├── test_scale_calibrator.py
│   └── sample_drawings/          # Put real Adicot CAD exports here for testing
│
└── uploads/                      # Temp storage for uploaded PDFs (gitignored)
```

---

## Data Models (`parser/models.py`)

Define these first. Everything else produces or consumes them.

> **Reading note:** Fields tagged `# GEOMETRY` are extracted from the PDF by the
> parser. Fields tagged `# THERMAL` cannot be read from a floor plan — they come
> from elevations, schedules, the title block, or project defaults, and must be
> set in the review UI (see Phase 11). Every `# THERMAL` field is required to run
> the actual load calc; the parser pre-fills sensible defaults and flags them.

```python
from dataclasses import dataclass, field
from typing import Optional
from shapely.geometry import Polygon, LineString, Point

@dataclass
class Window:
    id: str
    wall_id: str                  # GEOMETRY — which wall segment this window is on
    position_along_wall: float    # GEOMETRY — 0.0–1.0, fraction along the wall
    width_ft: float               # GEOMETRY
    height_ft: float              # GEOMETRY (often from elevation/schedule, not plan)
    sill_height_ft: float         # THERMAL — default 2.5 ft, engineer can override
    orientation: str              # GEOMETRY — N/S/E/W from wall orientation
    u_value: Optional[float]      # THERMAL — from window schedule if found; else None
    shgc: Optional[float]         # THERMAL — solar heat gain coefficient
    operable: bool = False        # THERMAL — affects infiltration
    interior_shading: Optional[str] = None   # THERMAL — 'none'|'blinds'|'drapes'
    overhang_depth_ft: float = 0.0           # THERMAL — external shading for solar gain
    source: str = "detected"      # 'detected' | 'manual'

@dataclass
class Door:
    id: str
    wall_id: str                  # GEOMETRY
    position_along_wall: float    # GEOMETRY
    width_ft: float               # GEOMETRY
    height_ft: float = 6.83       # GEOMETRY/THERMAL — default 6'-10"
    is_exterior: bool = False     # GEOMETRY
    u_value: Optional[float] = None  # THERMAL — required if exterior
    is_glazed: bool = False       # THERMAL — glass door needs SHGC like a window
    shgc: Optional[float] = None  # THERMAL — only if glazed

@dataclass
class Wall:
    id: str
    x1: float; y1: float          # GEOMETRY — start point (drawing coordinates)
    x2: float; y2: float          # GEOMETRY — end point
    length_ft: float              # GEOMETRY
    height_ft: Optional[float] = None  # THERMAL — usually = room ceiling height
    gross_area_sqft: float = 0.0  # length_ft * height_ft
    net_area_sqft: float = 0.0    # gross minus window + door areas (use this for load)
    orientation: str = "UNKNOWN"  # GEOMETRY — 'N','S','E','W','NE', etc.
    is_exterior: bool = False     # GEOMETRY
    construction: Optional[str] = None   # THERMAL — assembly name, e.g. "2x6 R-19"
    r_value: Optional[float] = None      # THERMAL — or supply u_value
    u_value: Optional[float] = None      # THERMAL — 1 / r_value if not given directly
    adjacent_condition: str = "ambient"  # THERMAL — 'ambient'|'conditioned'|
                                         #   'unconditioned'|'ground'|'buffer'
                                         # 'conditioned' partitions carry no load
    windows: list[Window] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)

@dataclass
class Roof:
    """Ceiling/roof assembly above a room. THERMAL — not on the floor plan."""
    roof_type: str                # 'flat'|'pitched'|'cathedral'|'attic'|
                                  #   'conditioned_above'  (= no roof load)
    r_value: Optional[float] = None
    u_value: Optional[float] = None
    pitch: Optional[str] = None   # e.g. "4:12" — affects sloped area vs floor area
    color: str = "medium"         # 'light'|'medium'|'dark' — solar absorptance
    radiant_barrier: bool = False
    area_sqft: Optional[float] = None  # defaults to room area; larger if pitched

@dataclass
class Floor:
    """Floor assembly below a room. THERMAL — not on the floor plan."""
    floor_type: str               # 'slab_on_grade'|'crawlspace'|'basement'|
                                  #   'over_unconditioned'|'over_ambient'|
                                  #   'conditioned_below'  (= no floor load)
    r_value: Optional[float] = None
    exposed_perimeter_ft: float = 0.0  # for slab F-factor edge loss
    f_factor: Optional[float] = None   # slab-edge loss coefficient

@dataclass
class Room:
    id: str
    label: str                    # GEOMETRY — raw label e.g. "OFFICE 101"
    room_type: Optional[str]      # GEOMETRY — mapped type; None if unknown
    area_sqft: float              # GEOMETRY
    polygon: Polygon              # GEOMETRY — shapely polygon in drawing coords
    ceiling_height_ft: Optional[float] = None  # THERMAL — REQUIRED; blocks wall area
    volume_cuft: Optional[float] = None        # area * ceiling height (for infiltration)
    is_conditioned: bool = True   # THERMAL — unconditioned rooms get no load
    roof: Optional[Roof] = None   # THERMAL — only if room is under the roof
    floor: Optional[Floor] = None # THERMAL
    occupants: Optional[float] = None        # THERMAL — internal sensible+latent gain
    lighting_w_per_sqft: Optional[float] = None   # THERMAL — internal gain
    equipment_w_per_sqft: Optional[float] = None  # THERMAL — plug/appliance gain
    ventilation_cfm: Optional[float] = None  # THERMAL — mechanical OA (ASHRAE 62.1)
    infiltration_ach: Optional[float] = None # THERMAL — or inherit project default
    walls: list[Wall] = field(default_factory=list)
    floor_number: int = 1
    source: str = "detected"      # 'detected' | 'manual'
    confidence: float = 1.0       # 0.0–1.0 parser confidence

@dataclass
class WeatherStation:
    """ASHRAE climatic design data for one weather station. Bundled as a local
    dataset (no runtime API call — honors the no-external-service constraint).
    These are *design conditions* (statistical peaks) for a peak load calc, NOT
    hourly TMY data (that would only be needed for 8760 energy modeling)."""
    name: str                  # "Orlando Intl AP, FL"
    wmo_id: str                # "722050" — the ASHRAE/WMO station identifier
    lat: float
    lon: float
    elev_ft: float
    climate_zone: str          # IECC/ASHRAE zone for this station
    # Heating design dry-bulb (°F) — pick per project risk tolerance
    htg_db_99: float           # 99% annual
    htg_db_996: float          # 99.6% annual (more conservative)
    # Cooling design dry-bulb + mean coincident wet-bulb (°F)
    clg_db_04: float; clg_mcwb_04: float   # 0.4% annual
    clg_db_1:  float; clg_mcwb_1:  float   # 1% annual
    clg_db_2:  float; clg_mcwb_2:  float   # 2% annual
    # Dehumidification design — drives the LATENT load (critical in Florida)
    dehum_dp_1: float          # 1% design dew point (°F)
    dehum_mcdb_1: float        # mean coincident dry-bulb at that dew point
    dehum_hr_1: float          # humidity ratio (grains/lb) — for grains-difference
    mean_daily_range_f: float  # cooling MDR — builds the design-day hourly profile

@dataclass
class ProjectInfo:
    """Project-wide load-calc inputs. THERMAL — none of this is on the floor plan."""
    project_name: str = ""
    address: str = ""
    building_type: str = "commercial"    # 'commercial' | 'residential' (commercial-first)
    # ── Weather: the engineer picks a station; design temps DERIVE from it ──
    weather_station_id: Optional[str] = None   # WMO id of the selected station
    weather: Optional[WeatherStation] = None   # resolved station record
    cooling_percentile: str = "1%"             # '0.4%' | '1%' | '2%'  (which clg column)
    heating_percentile: str = "99%"            # '99%'  | '99.6%'      (which htg column)
    # These four are CACHED from `weather` per the percentile choice — see
    # apply_weather(). Never hand-edit independently of the station.
    climate_zone: Optional[str] = None   # IECC/ASHRAE zone (from station)
    elevation_ft: float = 0.0            # altitude correction (from station)
    latitude: Optional[float] = None     # for solar gain (from station)
    heating_design_db_f: Optional[float] = None   # winter dry-bulb
    cooling_design_db_f: Optional[float] = None   # summer dry-bulb
    cooling_design_wb_f: Optional[float] = None   # coincident wet-bulb (latent)
    design_dewpoint_f: Optional[float] = None     # dehumidification DP (latent)
    daily_range_f: Optional[float] = None         # cooling MDR (°F)
    indoor_heating_setpoint_f: float = 70.0
    indoor_cooling_setpoint_f: float = 75.0
    indoor_rh_pct: float = 50.0
    # Defaults applied to any room that doesn't override
    default_infiltration_ach: Optional[float] = None  # or ACH50 tightness category
    number_of_floors: int = 1

def apply_weather(project: ProjectInfo) -> None:
    """Copy the selected station's values into the project's design fields,
    honoring the chosen percentiles. Call whenever the station or percentile
    changes (on upload and on any review-UI edit)."""
    w = project.weather
    if w is None:
        return
    project.climate_zone = w.climate_zone
    project.elevation_ft = w.elev_ft
    project.latitude = w.lat
    project.heating_design_db_f = {"99%": w.htg_db_99, "99.6%": w.htg_db_996}[project.heating_percentile]
    clg = {"0.4%": (w.clg_db_04, w.clg_mcwb_04),
           "1%":   (w.clg_db_1,  w.clg_mcwb_1),
           "2%":   (w.clg_db_2,  w.clg_mcwb_2)}[project.cooling_percentile]
    project.cooling_design_db_f, project.cooling_design_wb_f = clg
    project.design_dewpoint_f = w.dehum_dp_1
    project.daily_range_f = w.mean_daily_range_f

@dataclass
class Drawing:
    filename: str
    page_number: int
    scale_px_per_ft: float        # Calibrated scale
    north_rotation_deg: float     # Degrees clockwise from up = north
    project: ProjectInfo = field(default_factory=ProjectInfo)
    rooms: list[Room] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # Parser warnings for UI
```

---

## Phase 1 — PDF Vector Extraction (`parser/pdf_extractor.py`)

**Goal:** Pull all raw geometric and text entities from the PDF. No interpretation yet.

### What to extract per page

```python
import fitz  # pymupdf

def extract_page(pdf_path: str, page_num: int) -> dict:
    doc = fitz.open(pdf_path)
    page = doc[page_num]

    return {
        "lines": extract_lines(page),       # All line segments
        "paths": extract_paths(page),       # Polylines / rectangles
        "text_blocks": extract_text(page),  # All text with position + content
        "page_rect": page.rect,             # Page bounding box
        "page_num": page_num
    }
```

### Line extraction

For each path in `page.get_drawings()`:
- Extract individual line segments as `(x1, y1, x2, y2, line_width, color)`
- Store `line_width` — this is critical for wall detection
- Store `color` — walls are typically black (0,0,0)
- Discard lines shorter than a minimum threshold (e.g., < 2px — annotation ticks)

### Text extraction

For each block in `page.get_text("dict")`:
- Store `(x, y, text_content, font_size, font_name)`
- Separate large text (room labels, titles) from small text (dimensions, notes)
- Flag text matching dimension patterns: `r"\d+['\"]\s*-?\s*\d*\s*['\"]?"` 

### Output format

```python
{
  "lines": [
    {"x1": 100.0, "y1": 200.0, "x2": 350.0, "y2": 200.0, "width": 2.5, "color": [0,0,0]},
    ...
  ],
  "text_blocks": [
    {"x": 225.0, "y": 180.0, "text": "OFFICE 101", "font_size": 10.0},
    {"x": 210.0, "y": 165.0, "text": "12'-6\"", "font_size": 7.0},
    ...
  ],
  "page_rect": {"x0": 0, "y0": 0, "x1": 2448, "y1": 3168}
}
```

---

## Phase 2 — Scale Calibration (`parser/scale_calibrator.py`)

**Goal:** Establish the scale factor (pixels per foot) before any geometry interpretation.

**Do this before wall detection** — you need scale to filter noise by real-world size.

### Strategy 1 — Dimension callout matching (preferred)

1. Find all text blocks matching a dimension pattern (e.g., `"12'-6\""`)
2. For each dimension text, find the nearest horizontal or vertical line segment
3. The line segment length in px divided by the parsed dimension in feet = `px_per_ft`
4. Average across multiple matched pairs for robustness

```python
import re

DIMENSION_PATTERN = re.compile(
    r"(\d+)'\s*[-–]?\s*(\d+)?\s*(?:\"|\s*in)?"
)

def parse_dimension_to_feet(text: str) -> Optional[float]:
    m = DIMENSION_PATTERN.search(text)
    if m:
        feet = int(m.group(1))
        inches = int(m.group(2)) if m.group(2) else 0
        return feet + inches / 12.0
    return None
```

### Strategy 2 — Title block scale (fallback)

1. Scan text for patterns like `"SCALE: 1/4\" = 1'-0\""` or `"1:50"`
2. Parse the ratio and convert to px_per_ft using page DPI

### Strategy 3 — Manual fallback

If neither strategy succeeds, flag a warning and prompt engineer to input scale in the review UI. Store as `Drawing.warnings`.

---

## Phase 3 — Wall Detection (`parser/wall_detector.py`)

**Goal:** Filter all extracted lines down to just wall segments.

### Wall identification heuristics for CAD exports

CAD drawings have consistent layer conventions. Walls are identifiable by:

1. **Line width** — walls are the thickest lines on the drawing. Compute the histogram of all line widths; walls cluster at the top 10–20% of widths.
2. **Color** — walls are black or near-black (RGB all < 30)
3. **Length** — walls are typically > 1 ft. Filter out anything shorter after scale is applied.
4. **Connectivity** — walls form a connected network. Isolated lines are annotation.

```python
def detect_walls(lines: list[dict], px_per_ft: float) -> list[dict]:
    # 1. Filter by color — keep near-black lines
    black_lines = [l for l in lines if is_near_black(l["color"])]
    
    # 2. Find wall line width threshold — use 70th percentile of widths
    widths = [l["width"] for l in black_lines]
    threshold = np.percentile(widths, 70)
    thick_lines = [l for l in black_lines if l["width"] >= threshold * 0.8]
    
    # 3. Filter by minimum real-world length
    min_px = px_per_ft * 0.5  # walls must be at least 6 inches
    long_lines = [l for l in thick_lines if line_length(l) >= min_px]
    
    # 4. Snap endpoints — merge lines within 2px of each other
    snapped = snap_endpoints(long_lines, tolerance=2.0)
    
    return snapped
```

### Endpoint snapping

This is critical. CAD exports often have tiny gaps between wall segments at corners. Snap endpoints within a tolerance to form a clean connected graph.

```python
def snap_endpoints(lines, tolerance=2.0):
    # Collect all endpoints
    # For each endpoint, find all endpoints within tolerance
    # Replace all with their centroid
    # Return lines with updated coordinates
    ...
```

---

## Phase 4 — Room Building (`parser/room_builder.py`)

**Goal:** Convert the wall line network into enclosed room polygons with areas.

This is the most algorithmically complex step.

### Why not `minimum_cycle_basis`

The intuitive approach — `nx.minimum_cycle_basis(G)` — is wrong here for two
concrete reasons, and both produce silently bad rooms:

1. **A cycle basis is not the set of faces.** It returns a *basis* of the cycle
   space (|E| − |V| + 1 cycles). For anything past a simple ring those basis cycles
   don't correspond one-to-one with the rooms you see on the plan.
2. **The returned nodes are an unordered set.** `Polygon(cycle)` then connects them
   in arbitrary order, producing self-intersecting garbage whose `.area` is
   meaningless — so the "drop the largest" step is dropping a random polygon.

The correct primitive is **planar face traversal** over a geometric (angle-sorted)
embedding. It yields exactly one polygon per face, with vertices already in order.

### First, the real problem: walls are *two* lines, not one

A floor plan never draws a wall as a centerline — it draws both **faces**: two
near-parallel lines a wall-thickness apart, usually with poché/hatch between them.
Feed those raw lines straight into face traversal and every wall contributes a thin
**cavity face** of its own. You get a sliver "room" for each wall segment, the real
rooms come out bounded by inner faces, and wall↔room association falls apart. This
is the single biggest correctness trap in the parser, and the 20-sqft filter does
**not** reliably catch it — a long, thick wall cavity clears 20 sqft.

So the pipeline runs a mandatory pre-pass: **collapse double-line walls to a single
centerline** before building the graph. Pair up segments that are (a) near-parallel,
(b) separated by a plausible wall thickness (≈2–16 in in real units), and (c)
overlapping along their shared direction; replace each pair with the midline over
the overlap. Segments with no partner — a genuine single-line wall, or an
already-centerline drawing — pass through untouched. Collapsing also makes room
**area** center-to-center, the conventional takeoff basis, instead of inside-clear.
(This step may equally live at the tail of `wall_detector`; it is shown here because
room correctness depends on it.)

### Algorithm: planar half-edge face traversal

```
0. Collapse double-line walls to centerlines (above).
1. Build a graph; snap near-coincident endpoints to shared nodes. The snap
   tolerance is in *real units* (≈1.5 in) scaled by px_per_ft — never a fixed
   pixel count, which breaks across DPIs and drawing scales.
2. Prune dangling edges (degree-1 nodes) — annotation spurs can't bound a room.
3. Process each connected component independently. A plan is rarely one network:
   detached wings, free-standing columns, and a closet that doesn't touch the
   room around it are separate components, each with its OWN exterior face.
4. Per component: split each edge into two directed half-edges; at each node sort
   incident edges by angle (the geometric embedding).
5. Trace a face: from half-edge u→v, the next half-edge is the edge leaving v
   that is immediately clockwise from the reverse edge v→u. Walk until you
   return to the start. Every half-edge belongs to exactly one face.
6. Per component, drop the exterior face by minority signed-area sign — NOT by
   largest area (an L-shaped plan breaks "largest = exterior"). PDF y is DOWN, so
   the sign is flipped from the math convention; a minority vote sidesteps that.
7. Resolve nesting across components: if room A fully contains room B (a closet
   in a bedroom, a service core in a floorplate), punch B out of A as a hole so
   A's area excludes it — B survives as its own room. Done globally, after every
   component is traced.
```

### Implementation

```python
import math
import networkx as nx
from shapely.geometry import Polygon
from shapely.ops import unary_union

# ── Geometry helpers ─────────────────────────────────────────────────────────
def _angle(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])

def _signed_area(pts):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2.0

def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)

def _dir(w):
    return _unit(w["x2"] - w["x1"], w["y2"] - w["y1"])

# ── Step 0: collapse double-line walls to centerlines ────────────────────────
def collapse_double_lines(walls, px_per_ft,
                          min_thick_ft=2/12, max_thick_ft=16/12, angle_tol_deg=6.0):
    """Replace each pair of parallel wall faces with one centerline segment.
    Greedy: each segment pairs with its closest valid partner. Unpaired segments
    (true single-line walls, already-centerline drawings) pass through unchanged."""
    min_px, max_px = min_thick_ft * px_per_ft, max_thick_ft * px_per_ft
    cos_tol = math.cos(math.radians(angle_tol_deg))

    def perp_gap(a, b, da):                       # signed dist: b's midpoint → a's line
        nx_, ny_ = -da[1], da[0]
        mx, my = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        return (mx - a["x1"]) * nx_ + (my - a["y1"]) * ny_

    def overlap(a, b, da):                        # shared span projected onto da
        t = lambda px, py: (px - a["x1"]) * da[0] + (py - a["y1"]) * da[1]
        a0, a1 = sorted((0.0, t(a["x2"], a["y2"])))
        b0, b1 = sorted((t(b["x1"], b["y1"]), t(b["x2"], b["y2"])))
        lo, hi = max(a0, b0), min(a1, b1)
        return (lo, hi) if hi - lo > 1e-6 else None

    used, out = set(), []
    for i, a in enumerate(walls):
        if i in used:
            continue
        da = _dir(a)
        best = None                               # (j, |gap|, signed_gap, span)
        for j in range(i + 1, len(walls)):
            if j in used:
                continue
            b = walls[j]; db = _dir(b)
            if abs(da[0] * db[0] + da[1] * db[1]) < cos_tol:   # not parallel
                continue
            g = perp_gap(a, b, da)
            if not (min_px <= abs(g) <= max_px):               # wrong thickness
                continue
            span = overlap(a, b, da)
            if span is None:                                   # don't co-extend
                continue
            if best is None or abs(g) < best[1]:
                best = (j, abs(g), g, span)
        if best is None:
            out.append(a)                         # genuine single-line wall
            continue
        j, _, g, (lo, hi) = best
        used.add(i); used.add(j)
        nx_, ny_ = -da[1], da[0]
        off = g / 2.0                             # midline between the two faces
        out.append({**a,
            "x1": a["x1"] + da[0] * lo + nx_ * off,
            "y1": a["y1"] + da[1] * lo + ny_ * off,
            "x2": a["x1"] + da[0] * hi + nx_ * off,
            "y2": a["y1"] + da[1] * hi + ny_ * off})
    return out

# ── Steps 1–7: rooms from the centerline network ─────────────────────────────
def build_room_polygons(walls: list[dict], px_per_ft: float) -> list[Polygon]:
    walls = collapse_double_lines(walls, px_per_ft)             # 0

    # 1. Build graph; snap endpoints in REAL units (≈1.5 in), not a fixed px count.
    snap_px = max((1.5 / 12) * px_per_ft, 1e-6)
    snap = lambda v: round(v / snap_px) * snap_px
    G = nx.Graph()
    for w in walls:
        p1, p2 = (snap(w["x1"]), snap(w["y1"])), (snap(w["x2"]), snap(w["y2"]))
        if p1 != p2:
            G.add_edge(p1, p2, wall_id=w.get("id"))

    # 2. Iteratively drop degree-1 nodes (dangling spurs).
    changed = True
    while changed:
        changed = False
        for n in [n for n in G.nodes if G.degree(n) == 1]:
            G.remove_node(n); changed = True

    # 3. One face set per connected component — each has its own exterior.
    rooms = []
    for nodes in nx.connected_components(G):
        sub = G.subgraph(nodes)
        if sub.number_of_edges() < 3:
            continue

        # 4. Angle-sorted embedding.
        order = {n: sorted(sub.neighbors(n), key=lambda m: _angle(n, m)) for n in sub.nodes}
        def next_he(u, v, order=order):
            nbrs = order[v]
            return (v, nbrs[(nbrs.index(u) - 1) % len(nbrs)])   # CW from v→u

        # 5. Trace every face in this component.
        half = {(u, v) for u, v in sub.edges} | {(v, u) for u, v in sub.edges}
        seen, faces = set(), []
        for he in half:
            if he in seen:
                continue
            face, cur = [], he
            while cur not in seen:
                seen.add(cur); face.append(cur[0]); cur = next_he(*cur)
            if len(face) >= 3:
                faces.append(face)

        # 6. Drop this component's exterior by MINORITY winding sign.
        signs = [1 if _signed_area(f) > 0 else -1 for f in faces]
        ext = 1 if sum(signs) < 0 else -1
        for face, sign in zip(faces, signs):
            if sign == ext:
                continue
            poly = Polygon(face)
            if poly.is_valid and poly.area / (px_per_ft ** 2) > 20:   # ≥ 20 sqft
                rooms.append(poly)

    # 7. Resolve nesting: subtract any room fully inside another (closet/core).
    final = []
    for outer in rooms:
        inner = [r for r in rooms if r is not outer and outer.contains(r)]
        final.append(outer.difference(unary_union(inner)) if inner else outer)
    return final
```

> Two things make or break this, both scale-derived — so calibrate scale (Phase 2)
> *first*, or a wrong px_per_ft breaks both: the **double-line collapse** (a missed
> pair leaves a thin cavity face masquerading as a room) and the **snap tolerance**
> (every gap left unsnapped is a face that fails to close and silently merges into
> its neighbor). If room counts come out *high* with thin slivers, suspect the
> collapse; if they come out *low*, suspect snapping.

### Edge cases to handle

- **Open rooms** — rooms missing one wall (e.g., open-plan areas). The unclosed face merges into its neighbor; flag low room counts and let the engineer close walls manually.
- **Nested rooms** — handled automatically in step 7 (a closet inside a bedroom is punched out as a hole and kept as its own room). Verify the containing room's area excludes the inner one.
- **Courtyards / light wells** — an interior void traces as a face exactly like a room; geometry alone can't tell them apart. Flag enclosed faces with no label or door for the engineer to mark unconditioned.
- **Corridors** — long narrow polygons. Flag if aspect ratio > 10:1.
- **Merged spaces** — two rooms with a shared open wall. No automatic fix; flag for engineer.

---

## Phase 5 — Opening Detection (`parser/opening_detector.py`)

**Goal:** Detect windows and doors on wall segments.

### Door detection — arc method

In CAD floor plans, doors are drawn as an arc (the door swing) plus a perpendicular line. The arc is the most reliable signal.

```python
def detect_doors(paths: list, walls: list, px_per_ft: float) -> list[Door]:
    # Extract arc segments from paths (fitz returns them as curve segments)
    arcs = [p for p in paths if is_arc(p)]
    
    for arc in arcs:
        # Find the nearest wall to this arc's origin point
        # The door is at the arc origin, width = arc radius
        door_width_ft = arc["radius"] / px_per_ft
        nearest_wall = find_nearest_wall(arc["center"], walls)
        position = project_point_onto_wall(arc["center"], nearest_wall)
        
        yield Door(
            wall_id=nearest_wall["id"],
            position_along_wall=position,
            width_ft=door_width_ft,
            is_exterior=is_exterior_wall(nearest_wall)
        )
```

### Window detection — symbol method

Standard CAD window symbol: three parallel lines perpendicular to and intersecting a wall segment (two lines = glazing, one line = frame).

```python
def detect_windows(lines: list, walls: list, px_per_ft: float) -> list[Window]:
    # Find groups of 2-3 short parallel lines that:
    # 1. Are perpendicular to a wall segment
    # 2. Are closely spaced (< 6 inches apart in real units)
    # 3. Span across the wall (endpoints on both sides of wall line)
    
    for wall in walls:
        perpendicular_groups = find_perpendicular_line_groups(lines, wall)
        for group in perpendicular_groups:
            window_width_ft = group["span_length"] / px_per_ft
            position = group["midpoint_along_wall"]
            
            yield Window(
                wall_id=wall["id"],
                position_along_wall=position,
                width_ft=window_width_ft,
                orientation=wall["orientation"],
                source="detected"
            )
```

### Window schedule parsing (bonus)

If the drawing has a window schedule table (common in commercial drawings):
- Find rectangular table structures in the PDF text
- Match rows with window IDs (W1, W2...) to parsed Window objects
- Populate `u_value` and `shgc` from schedule

---

## Phase 6 — Text Assignment (`parser/text_parser.py`)

**Goal:** Match room label text to room polygons, and classify room types.

### Label-to-room assignment

```python
def assign_labels_to_rooms(rooms: list[Room], text_blocks: list[dict]) -> list[Room]:
    for room in rooms:
        # Find all text blocks whose position falls inside the room polygon
        candidates = [
            t for t in text_blocks 
            if room.polygon.contains(Point(t["x"], t["y"]))
        ]
        
        if candidates:
            # Pick the largest font size text as the primary label
            label_block = max(candidates, key=lambda t: t["font_size"])
            room.label = label_block["text"].strip()
            room.room_type = classify_room_type(room.label)
        else:
            room.label = f"ROOM {room.id}"
            room.room_type = None  # Engineer must set
            room.confidence = 0.0
    
    return rooms
```

### Room type classification

Map common label strings to load calc room types. This is a simple lookup — no ML needed.

```python
ROOM_TYPE_MAP = {
    # Residential
    "bedroom": "Bedroom",
    "master bedroom": "Bedroom",
    "living": "Living Room",
    "living room": "Living Room",
    "kitchen": "Kitchen",
    "dining": "Dining Room",
    "bathroom": "Bathroom",
    "bath": "Bathroom",
    "laundry": "Laundry",
    "garage": "Garage",
    "closet": "Closet",

    # Commercial
    "office": "Office",
    "conference": "Conference Room",
    "conf": "Conference Room",
    "lobby": "Lobby",
    "reception": "Lobby",
    "break room": "Break Room",
    "breakroom": "Break Room",
    "server": "Server Room",
    "electrical": "Mechanical Room",
    "mechanical": "Mechanical Room",
    "mech": "Mechanical Room",
    "restroom": "Restroom",
    "corridor": "Corridor",
    "hallway": "Corridor",
    "storage": "Storage",
    "stair": "Stairwell",
    "elevator": "Elevator Shaft",
    "retail": "Retail",
    "gym": "Gym",
    "fitness": "Gym",
}

def classify_room_type(label: str) -> Optional[str]:
    label_lower = label.lower()
    for key, room_type in ROOM_TYPE_MAP.items():
        if key in label_lower:
            return room_type
    return None  # Unknown — engineer decides
```

---

## Phase 7 — North Arrow Detection (`parser/north_arrow.py`)

**Goal:** Determine drawing orientation so wall N/S/E/W assignments are correct.

### Approach

1. Scan text blocks for "N", "NORTH", or a north arrow symbol
2. If a north arrow symbol exists (typically an arrow/circle near a compass rose), detect its rotation
3. The angle from vertical = `north_rotation_deg` stored on `Drawing`
4. Apply this rotation when computing wall orientations

### Wall orientation assignment

```python
def assign_wall_orientations(walls: list[Wall], north_deg: float) -> list[Wall]:
    for wall in walls:
        dx = wall.x2 - wall.x1
        dy = wall.y2 - wall.y1
        angle_deg = math.degrees(math.atan2(dy, dx))
        
        # Adjust for north rotation
        adjusted = (angle_deg - north_deg) % 360
        
        # Classify into cardinal direction
        wall.orientation = angle_to_cardinal(adjusted)
    
    return walls
```

If north arrow detection fails, default all walls to `orientation = "UNKNOWN"` and require engineer to set the project north in the review UI.

---

## Phase 7.5 — Thermal & Project Inputs (the load-calc layer)

**Goal:** Capture everything a heating/cooling load calc needs that does **not**
live in the floor-plan geometry. The parser pre-fills these from any available
source and flags the rest; the engineer confirms them in the review UI.

A floor plan tells you *shape*. A load calc runs on *thermal properties + climate*.
Phases 1–7 deliver shape. This phase delivers the rest. **Do not skip it** — without
these fields the calc cannot run, only the drawing can be displayed.

### Why ceiling height is the linchpin

Walls are extracted with `length_ft` but a load calc needs wall **area**
(`length_ft × ceiling_height_ft`), then **net area** (gross minus window and door
areas). With no height, every conductive wall load is undefined. Ceiling height is
almost never on the floor plan — pull it from a section/elevation, the title block,
or a project default (e.g. 8 ft residential, 9–10 ft commercial), and require the
engineer to confirm it per floor.

```python
def compute_wall_areas(room: Room) -> None:
    h = room.ceiling_height_ft
    for wall in room.walls:
        wall.height_ft = wall.height_ft or h
        wall.gross_area_sqft = wall.length_ft * wall.height_ft
        opening_area = (
            sum(w.width_ft * w.height_ft for w in wall.windows)
            + sum(d.width_ft * d.height_ft for d in wall.doors)
        )
        wall.net_area_sqft = max(wall.gross_area_sqft - opening_area, 0.0)
    room.volume_cuft = room.area_sqft * h
```

### Roof type (`Room.roof`)

The roof/ceiling assembly above each top-floor room is a primary load surface and
must be set explicitly. The floor plan rarely shows it — derive the *type* from
context where possible, default the rest, and flag for confirmation.

| `roof_type` | When it applies | Load behavior |
|---|---|---|
| `attic` | Vented attic above ceiling | Ceiling load uses ceiling R-value + attic temp |
| `flat` | Flat/low-slope roof-ceiling | Roof load, full sun exposure |
| `cathedral` | Sloped exposed ceiling, no attic | Roof load; sloped area > floor area |
| `pitched` | Pitched roof over the room | Use `pitch` to scale sloped area |
| `conditioned_above` | Another conditioned floor above | **No roof load** (interior surface) |

```python
def assign_roof(room: Room, project: ProjectInfo) -> None:
    # Only the top floor sees the roof; rooms below get conditioned_above.
    if room.floor_number < project.number_of_floors:
        room.roof = Roof(roof_type="conditioned_above")
        return
    # Top floor: default to attic (most common); engineer confirms type + R-value.
    room.roof = Roof(roof_type="attic", color="medium")
    # Pitched/cathedral roofs cover more area than the floor footprint:
    if room.roof.roof_type in ("pitched", "cathedral") and room.roof.pitch:
        room.roof.area_sqft = room.area_sqft * pitch_area_factor(room.roof.pitch)
    else:
        room.roof.area_sqft = room.area_sqft
```

### Floor type (`Room.floor`)

Same logic downward: only the lowest floor sees the ground/exterior; rooms above a
conditioned floor get `conditioned_below` (no floor load). Slab-on-grade uses an
F-factor on the **exposed perimeter** (compute from the room polygon edges that lie
on the building exterior), not an area R-value.

### Project & climate inputs (`Drawing.project`)

These are the highest-leverage inputs in the whole calc and none of them are on the
plan. Collect them once per project, on the upload screen or the first review step:

- **Weather station → design conditions.** The engineer selects an ASHRAE weather
  station; every design temperature in the calc is read from that station's record
  (see "Weather stations" below). This is an explicit, auditable choice — the calc
  must be able to state *which station* it used.
- **Indoor design conditions.** Heating/cooling setpoints and indoor RH.
- **Infiltration.** Air-tightness category or ACH50/ACH; apply a project default,
  allow per-room override (volume-based, which is why you need ceiling height).
- **Ventilation.** Mechanical outdoor air per room — critical for commercial
  (ASHRAE 62.1, by room type + occupancy/area).
- **Internal gains.** Occupant count, lighting W/ft², equipment/plug W/ft² per room.
  Seed defaults from `room_type` (e.g. office vs. server room differ enormously),
  engineer adjusts.

### Lookup tables (`parser/thermal_defaults.py`)

All three of the following are plain dicts — the same lookup-table pattern as
`ROOM_TYPE_MAP`, no ML. They are the data layer that turns a parsed shape into a
runnable calc.

> ⚠️ **Edition matters — store the source edition with every table.** Lighting
> below uses **ASHRAE 90.1-2019**, the commercial standard Florida adopts through
> FBC-EC 2023 (8th ed.); ventilation uses **ASHRAE 62.1**. Both are from the
> published standards (verified 2026-06-19). A project permitted elsewhere, or
> under a newer FBC, may differ. The *envelope assembly U-values are textbook-
> typical placeholders*, and `DESIGN_CONDITIONS` is a Florida starter set that must
> grow into the full ASHRAE/ACCA design-day dataset. Always let the engineer
> override any value in the review UI; never silently ship a value the project's
> code didn't sanction.

**1. Assembly library** — construction name → thermal properties:

```python
WALL_ASSEMBLIES = {
    # name                         u_value   notes
    "2x6 R-19 batt":              {"u": 0.057},
    "2x4 R-13 batt":              {"u": 0.082},
    "8\" CMU, uninsulated":       {"u": 0.51},
    "8\" CMU + R-11":             {"u": 0.11},
    "metal panel + R-19":         {"u": 0.062},
    "curtain wall (spandrel)":    {"u": 0.077},   # common in commercial
}
ROOF_ASSEMBLIES = {
    "attic + R-38":               {"u": 0.026},
    "flat: built-up + R-30 c.i.": {"u": 0.032},   # c.i. = continuous insulation
    "metal deck + R-20 c.i.":     {"u": 0.048},
}
FLOOR_ASSEMBLIES = {
    "slab-on-grade, unheated":    {"f_factor": 0.73},  # per-ft of exposed perimeter
    "slab + R-10 edge":           {"f_factor": 0.54},
    "floor over unconditioned":   {"u": 0.033},
}
```

**2. Weather stations** — the project's design temperatures come from a chosen
ASHRAE station, not a typed-in number. Bundle the dataset as a local file (CSV/JSON
shipped with the app) and load it into `WeatherStation` records — no runtime API,
consistent with the project's no-external-service constraint. The dataset is the
**ASHRAE Handbook of Fundamentals climatic design conditions** (same data ACCA
Manual J/N pulls from); seed Florida first, then expand.

```python
# Florida-first seed. Representative ASHRAE-basis values — VERIFY each row against
# the ASHRAE Fundamentals climatic design tables before stamping. clg_db/clg_mcwb
# are the 1% cooling pair; dehum_* is the 1% dehumidification design point — the
# latter drives Florida's latent load and is the row engineers most often omit.
WEATHER_STATIONS = {
    # wmo_id : WeatherStation(...)
    "722020": WeatherStation("Miami Intl AP, FL",        "722020", 25.79, -80.32,  11, "1A",
                  htg_db_99=50, htg_db_996=47, clg_db_04=92, clg_mcwb_04=77, clg_db_1=91, clg_mcwb_1=77,
                  clg_db_2=90, clg_mcwb_2=77, dehum_dp_1=77, dehum_mcdb_1=82, dehum_hr_1=144, mean_daily_range_f=12),
    "722025": WeatherStation("Ft Lauderdale Exec, FL",   "722025", 26.20, -80.17,   9, "1A",
                  htg_db_99=51, htg_db_996=48, clg_db_04=92, clg_mcwb_04=78, clg_db_1=91, clg_mcwb_1=78,
                  clg_db_2=90, clg_mcwb_2=78, dehum_dp_1=78, dehum_mcdb_1=83, dehum_hr_1=149, mean_daily_range_f=13),
    "722030": WeatherStation("W Palm Beach Intl, FL",    "722030", 26.69, -80.10,  19, "1A",
                  htg_db_99=50, htg_db_996=46, clg_db_04=92, clg_mcwb_04=78, clg_db_1=91, clg_mcwb_1=78,
                  clg_db_2=90, clg_mcwb_2=78, dehum_dp_1=78, dehum_mcdb_1=83, dehum_hr_1=149, mean_daily_range_f=14),
    "722010": WeatherStation("Key West Intl, FL",        "722010", 24.55, -81.75,   4, "1A",
                  htg_db_99=57, htg_db_996=55, clg_db_04=91, clg_mcwb_04=79, clg_db_1=90, clg_mcwb_1=79,
                  clg_db_2=89, clg_mcwb_2=79, dehum_dp_1=79, dehum_mcdb_1=84, dehum_hr_1=154, mean_daily_range_f=9),
    "722108": WeatherStation("Ft Myers SW FL Intl, FL",  "722108", 26.59, -81.86,  15, "1A",
                  htg_db_99=50, htg_db_996=46, clg_db_04=93, clg_mcwb_04=77, clg_db_1=92, clg_mcwb_1=77,
                  clg_db_2=91, clg_mcwb_2=76, dehum_dp_1=77, dehum_mcdb_1=82, dehum_hr_1=144, mean_daily_range_f=16),
    "722110": WeatherStation("Tampa Intl AP, FL",        "722110", 27.96, -82.54,  19, "2A",
                  htg_db_99=40, htg_db_996=36, clg_db_04=92, clg_mcwb_04=77, clg_db_1=92, clg_mcwb_1=77,
                  clg_db_2=91, clg_mcwb_2=76, dehum_dp_1=77, dehum_mcdb_1=82, dehum_hr_1=144, mean_daily_range_f=17),
    "722050": WeatherStation("Orlando Intl AP, FL",      "722050", 28.43, -81.32,  96, "2A",
                  htg_db_99=42, htg_db_996=38, clg_db_04=94, clg_mcwb_04=76, clg_db_1=93, clg_mcwb_1=76,
                  clg_db_2=92, clg_mcwb_2=76, dehum_dp_1=76, dehum_mcdb_1=81, dehum_hr_1=140, mean_daily_range_f=17),
    "722060": WeatherStation("Jacksonville Intl, FL",    "722060", 30.49, -81.69,  26, "2A",
                  htg_db_99=32, htg_db_996=28, clg_db_04=95, clg_mcwb_04=77, clg_db_1=94, clg_mcwb_1=76,
                  clg_db_2=92, clg_mcwb_2=76, dehum_dp_1=76, dehum_mcdb_1=81, dehum_hr_1=140, mean_daily_range_f=18),
    "722140": WeatherStation("Tallahassee Rgnl, FL",     "722140", 30.39, -84.35,  53, "2A",
                  htg_db_99=30, htg_db_996=26, clg_db_04=95, clg_mcwb_04=76, clg_db_1=94, clg_mcwb_1=76,
                  clg_db_2=93, clg_mcwb_2=76, dehum_dp_1=75, dehum_mcdb_1=80, dehum_hr_1=136, mean_daily_range_f=19),
    "722223": WeatherStation("Pensacola Rgnl, FL",       "722223", 30.48, -87.19,  26, "2A",
                  htg_db_99=32, htg_db_996=28, clg_db_04=94, clg_mcwb_04=78, clg_db_1=93, clg_mcwb_1=77,
                  clg_db_2=92, clg_mcwb_2=77, dehum_dp_1=77, dehum_mcdb_1=82, dehum_hr_1=144, mean_daily_range_f=16),
    # → expand to the full ASHRAE station list (ships as data, not code).
}

def resolve_station(query: str, stations=WEATHER_STATIONS) -> Optional[WeatherStation]:
    """Find a station by WMO id, by name match, or by nearest to a ZIP centroid.
    Address geocoding is intentionally NOT done (would need an external API) — the
    UI lets the engineer search by name/WMO or pick the nearest from a ZIP."""
    if query in stations:                       # 1. exact WMO id
        return stations[query]
    hits = [s for s in stations.values() if query.lower() in s.name.lower()]
    if hits:                                    # 2. name contains
        return hits[0]
    latlon = ZIP_CENTROIDS.get(query)           # 3. ZIP → nearest by great-circle
    if latlon:
        return min(stations.values(), key=lambda s: _haversine(latlon, (s.lat, s.lon)))
    return None                                 # 4. engineer picks from the list
```

**Design-day temperature profile.** A peak load calc (RTS / Manual N) needs an
*hourly* design-day curve, not just the peak. Build it from the station's peak
cooling DB and mean daily range using the standard ASHRAE fraction-of-daily-range
multipliers — no extra weather data required:

```python
# ASHRAE fraction of daily range, hour 1..24 (peak ~hour 15). Ships as a constant.
def design_day_db_profile(p: ProjectInfo) -> list[float]:
    peak, mdr = p.cooling_design_db_f, p.daily_range_f
    return [peak - mdr * (1 - f) for f in DAILY_RANGE_FRACTION]  # 24 values
# Solar for each hour comes from the station lat/lon + design month via the ASHRAE
# clear-sky (tau) model — deterministic, no external data.
```

**3. Commercial space-type defaults** — the highest-value table for your target.
In commercial buildings the internal and ventilation loads often dominate the
envelope, and they are driven entirely by space type. Key off `room_type`:

```python
# Sources (verified 2026-06-19):
#   occ, rp, ra = ASHRAE 62.1 Ventilation Rate Procedure, Table 6.2.2.1
#   lpd         = ASHRAE 90.1-2019 space-by-space (Florida's adopted commercial
#                 standard, per FBC-EC 2023 / 8th ed.). Cross-checked vs 90.1-2022:
#                 identical for these common space types within rounding. (W/ft²)
#   plug        = typical modeling defaults (COMNET / ASHRAE 90.1 App. C), W/ft²
# occ  = default occupant density, people / 1000 ft²
# rp   = outdoor air per person, cfm/person
# ra   = outdoor air per area,   cfm/ft²
# † = value NOT confirmed against the published table — approximate, edition-
#     sensitive, confirm before relying on it. All others are as published.
SPACE_TYPE_DEFAULTS = {
    "Office":          {"occ": 5,  "rp": 5,   "ra": 0.06, "lpd": 0.74,  "plug": 0.75},
    "Conference Room": {"occ": 50, "rp": 5,   "ra": 0.06, "lpd": 0.97,  "plug": 0.50},
    "Lobby":           {"occ": 10, "rp": 5,   "ra": 0.06, "lpd": 0.84,  "plug": 0.20},  # lpd †
    "Corridor":        {"occ": 0,  "rp": 0,   "ra": 0.06, "lpd": 0.41,  "plug": 0.10},
    "Break Room":      {"occ": 25, "rp": 5,   "ra": 0.12, "lpd": 0.59,  "plug": 2.00},  # lpd †
    "Restroom":        {"occ": 0,  "rp": 0,   "ra": 0.00, "lpd": 0.62,  "plug": 0.10},  # exhaust-based (below)
    "Retail":          {"occ": 15, "rp": 7.5, "ra": 0.12, "lpd": 0.84,  "plug": 0.50},
    "Server Room":     {"occ": 0,  "rp": 0,   "ra": 0.06, "lpd": 1.00,  "plug": 50.0},  # lpd †; plug = placeholder, use schedule
    "Mechanical Room": {"occ": 0,  "rp": 0,   "ra": 0.06, "lpd": 0.43,  "plug": 0.50},  # lpd †
    "Storage":         {"occ": 0,  "rp": 0,   "ra": 0.06, "lpd": 0.43,  "plug": 0.20},  # 62.1 warehouse basis
    "Gym":             {"occ": 10, "rp": 20,  "ra": 0.06, "lpd": 0.72,  "plug": 0.50},  # health-club/weight-room basis; lpd †
}

# Restrooms are exhaust-driven, not supply-OA via the VRP: design to the minimum
# exhaust rate (50 cfm/fixture intermittent, 70 cfm/fixture continuous — IMC/62.1),
# which is why ra = 0 above. seed_ventilation() should branch on this.

def seed_internal_gains(room: Room) -> None:
    d = SPACE_TYPE_DEFAULTS.get(room.room_type)
    if not d or room.area_sqft is None:
        return  # unknown type → engineer must enter gains; leave None + warn
    room.occupants = room.occupants or round(d["occ"] * room.area_sqft / 1000, 1)
    room.lighting_w_per_sqft = room.lighting_w_per_sqft or d["lpd"]
    room.equipment_w_per_sqft = room.equipment_w_per_sqft or d["plug"]

def seed_ventilation(room: Room, project: ProjectInfo) -> None:
    d = SPACE_TYPE_DEFAULTS.get(room.room_type)
    if not d or room.area_sqft is None:
        return
    # ASHRAE 62.1 Ventilation Rate Procedure: people + area components
    occ = room.occupants or (d["occ"] * room.area_sqft / 1000)
    room.ventilation_cfm = room.ventilation_cfm or round(
        d["rp"] * occ + d["ra"] * room.area_sqft, 1)
```

> **Server Room is the classic commercial trap:** near-zero envelope load but a
> huge, year-round equipment load that drives a dedicated cooling zone. The
> plug-load default above is a placeholder — these should come from the actual
> equipment schedule, so flag every server/IT/electrical room for manual entry.

### Florida specifics (primary market)

Florida is the design target, and it changes the emphasis of the whole calc:

- **Cooling- and latent-dominated.** Heating loads are small (99% design DB ~50 °F
  in the south, ~30 °F in the panhandle); the sizing driver is the **cooling load,
  and especially the latent portion**. The mean-coincident wet-bulb (76–79 °F) and
  the moisture brought in by ventilation/infiltration matter more than any
  conductive load. Make sure the engine computes a **sensible + latent split** and
  reports SHR — a Florida calc that only reports sensible Btu/h is wrong.
- **Ventilation is a latent penalty.** Outdoor air at ~78 °F WB carries a large
  latent load; in humid-climate commercial design it often dominates. The 62.1
  CFM from `seed_ventilation()` feeds straight into the latent calc, so get
  occupancy right.
- **Code basis:** commercial → **ASHRAE 90.1-2019** (FBC-EC 2023, 8th ed.); HVAC
  ventilation → ASHRAE 62.1; envelope/insulation minimums per FBC-EC for the
  building's zone. South Florida (Miami-Dade, Broward) also triggers **HVHZ**
  detailing, which doesn't change loads but does change products/notes.
- **Construction reality:** most Florida commercial walls are **CMU** (often
  uninsulated or with furring + R-bd), and roofs are **flat/low-slope** — so the
  CMU wall and flat-roof assemblies, not wood-frame, should be the defaults.
- **Slab-on-grade** is near-universal (no basements); use the F-factor path.

**Climate zones — what's solid vs. what to confirm.** Florida is predominantly
**Zone 2A (hot-humid)**, with the **southern counties Zone 1A** (Miami-Dade,
Broward, Monroe, and — added in the 2021 IECC — Palm Beach). Some sources also
place far-northwest **panhandle** counties in **Zone 3A**. Do **not** hardcode a
county→zone map from memory: ship the official **FBC-EC Table/Figure C301.1**
mapping as a data file and key off it. The per-city `DESIGN_CONDITIONS` zones above
follow the predominant 1A/2A split; confirm panhandle rows against C301.1.

```python
# Florida-first project defaults — engineer can change on the upload screen.
FL_PROJECT_DEFAULTS = ProjectInfo(
    building_type="commercial",
    climate_zone="2A",                 # most of the state; 1A in the south
    indoor_cooling_setpoint_f=75.0,
    indoor_heating_setpoint_f=70.0,
    indoor_rh_pct=50.0,                 # latent target — drives dehumidification
)
```

---

## Phase 8 — Flask Routes (`app.py` additions)

```python
# ── Upload ──────────────────────────────────────────────────────────────────
@app.route("/parse/upload", methods=["GET", "POST"])
def upload_drawing():
    """
    GET  — render upload.html
    POST — accept PDF, run full parser pipeline, redirect to review UI
    """
    if request.method == "POST":
        pdf_file = request.files["drawing"]
        session_id = str(uuid.uuid4())
        pdf_path = save_upload(pdf_file, session_id)
        
        try:
            drawing = run_parser_pipeline(pdf_path)
            session_data = drawing_to_dict(drawing)
            save_session(session_id, session_data)
            return redirect(url_for("review_drawing", session_id=session_id))
        except ParserException as e:
            return render_template("upload.html", error=str(e))


# ── Review UI ────────────────────────────────────────────────────────────────
@app.route("/parse/review/<session_id>")
def review_drawing(session_id):
    """
    Render the review UI with extracted drawing data.
    """
    data = load_session(session_id)
    return render_template("review.html", session_id=session_id, drawing=data)


# ── AJAX — get drawing data ──────────────────────────────────────────────────
@app.route("/parse/api/drawing/<session_id>")
def get_drawing_data(session_id):
    """
    Returns the full drawing JSON for the canvas renderer.
    """
    return jsonify(load_session(session_id))


# ── AJAX — save corrections ──────────────────────────────────────────────────
@app.route("/parse/api/drawing/<session_id>", methods=["PUT"])
def update_drawing_data(session_id):
    """
    Engineer saves corrections from the review UI.
    Accepts partial updates — only rooms/windows that changed.
    """
    updates = request.json
    data = load_session(session_id)
    apply_updates(data, updates)
    save_session(session_id, data)
    return jsonify({"status": "ok"})


# ── Submit to load calc ──────────────────────────────────────────────────────
@app.route("/parse/submit/<session_id>", methods=["POST"])
def submit_to_load_calc(session_id):
    """
    Takes reviewed drawing data, converts to load calc input format,
    and redirects to the existing load calc results flow.
    """
    data = load_session(session_id)
    load_calc_input = convert_to_load_calc_format(data)
    # Hand off to existing load calc engine
    return run_load_calc(load_calc_input)
```

### Full parser pipeline orchestrator

```python
def run_parser_pipeline(pdf_path: str) -> Drawing:
    # Step 1: Extract raw vectors and text
    raw = pdf_extractor.extract_page(pdf_path, page_num=0)
    
    # Step 2: Calibrate scale
    scale, warnings = scale_calibrator.calibrate(raw["text_blocks"], raw["lines"])
    
    # Step 3: Detect walls
    walls = wall_detector.detect_walls(raw["lines"], scale)
    
    # Step 4: Build room polygons
    polygons = room_builder.build_room_polygons(walls, scale)
    rooms = [Room(id=str(i), polygon=p, area_sqft=p.area / scale**2) 
             for i, p in enumerate(polygons)]
    
    # Step 5: Detect openings
    windows = opening_detector.detect_windows(raw["lines"], walls, scale)
    doors = opening_detector.detect_doors(raw["paths"], walls, scale)
    
    # Step 6: Assign text labels
    rooms = text_parser.assign_labels_to_rooms(rooms, raw["text_blocks"])
    
    # Step 7: Determine north / wall orientations
    north_deg = north_arrow.detect(raw["text_blocks"], raw["paths"])
    walls = north_arrow.assign_wall_orientations(walls, north_deg)
    
    # Step 8: Link windows/doors to rooms
    link_openings_to_rooms(rooms, windows, doors)
    
    # Step 9: Apply thermal/project defaults (Phase 7.5).
    #   These are pre-fills the engineer confirms in the review UI — the parser
    #   cannot read them from the plan, but the calc cannot run without them.
    project = ProjectInfo()                       # from the upload form
    if station_query:                             # engineer's station pick / ZIP
        project.weather = resolve_station(station_query)
        project.weather_station_id = project.weather.wmo_id if project.weather else None
        apply_weather(project)                    # fills design temps from station
    for room in rooms:
        room.ceiling_height_ft = room.ceiling_height_ft or default_ceiling_height(project)
        compute_wall_areas(room)
        assign_roof(room, project)
        assign_floor(room, project)
        seed_internal_gains(room)        # occupants, lighting, equipment from room_type
        seed_ventilation(room, project)  # ASHRAE 62.1 default by room type
    warnings += missing_thermal_warnings(rooms, project)  # flag every unconfirmed field
    
    drawing = Drawing(
        filename=os.path.basename(pdf_path),
        page_number=0,
        scale_px_per_ft=scale,
        north_rotation_deg=north_deg,
        project=project,
        rooms=rooms,
        warnings=warnings
    )
    
    return drawing
```

---

## Phase 9 — Review UI

This is the engineer-facing correction interface. It is the most important UX piece.

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  TOOLBAR: [Save] [Submit to Load Calc] [Add Room] [Set Scale]   │
│  WEATHER: [Orlando Intl AP (722050) ▼] 1%▼/99%▼  Zone 2A         │
│           Htg 42°F · Clg 93°/76°wb · DP 76°F (latent)    [Change] │
├──────────────────────────┬───────────────────────────────────────┤
│                          │  SELECTED ROOM PANEL                  │
│   FLOOR PLAN CANVAS      │  ─────────────────────────────────── │
│                          │  Label:    [OFFICE 101      ]         │
│   (Rendered from         │  Type:     [Office          ▼]        │
│    extracted geometry)   │  Area:     [285 sqft        ] (auto)  │
│                          │  Ceiling:  [9.0 ft          ] ⚠ confirm│
│                          │  Roof:     [Attic R-38      ▼] ⚠      │
│                          │  Floor:    [Slab-on-grade   ▼]        │
│                          │  Occupants/Ltg/Equip: [auto from type]│
│                          │  Vent (CFM) / Infiltration: [defaults]│
│                          │                                        │
│   Rooms highlighted      │  WALLS  (R-value + net area required) │
│   with confidence color: │  ┌──────┬──────┬──────┬─────────┐   │
│   Green = high           │  │ Wall │ Dir  │ Len  │ Windows │   │
│   Yellow = medium        │  ├──────┼──────┼──────┼─────────┤   │
│   Red = low / unknown    │  │  N   │  N   │ 15ft │  2      │   │
│                          │  │  E   │  E   │ 19ft │  1      │   │
│   Click room to select   │  │  S   │  S   │ 15ft │  0      │   │
│   Click window to edit   │  │  W   │  W   │ 19ft │  0      │   │
│   Drag to move           │  └──────┴──────┴──────┴─────────┘   │
│                          │                                        │
│                          │  WINDOWS (click to edit)              │
│                          │  W1: N wall, 3ft wide, U=? SHGC=?    │
│                          │  W2: N wall, 3ft wide, U=? SHGC=?    │
│                          │  W3: E wall, 4ft wide, U=? SHGC=?    │
│                          │  [+ Add Window]                       │
│                          │                                        │
│                          │  ⚠ WARNINGS                           │
│                          │  Room type unknown — please set       │
│                          │  2 windows missing U-value            │
└──────────────────────────┴───────────────────────────────────────┘
```

### Canvas renderer (`static/js/canvas_renderer.js`)

```javascript
class FloorPlanRenderer {
    constructor(canvasId, drawingData) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.drawing = drawingData;
        this.selectedRoomId = null;
        this.transform = { scale: 1, offsetX: 0, offsetY: 0 };
    }

    render() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.drawRooms();
        this.drawWalls();
        this.drawWindows();
        this.drawDoors();
        this.drawLabels();
    }

    drawRooms() {
        for (const room of this.drawing.rooms) {
            const color = this.getRoomColor(room);
            this.ctx.fillStyle = color;
            this.drawPolygon(room.polygon);
            if (room.id === this.selectedRoomId) {
                this.ctx.strokeStyle = '#0057b7';
                this.ctx.lineWidth = 3;
                this.drawPolygon(room.polygon, stroke=true);
            }
        }
    }

    getRoomColor(room) {
        if (room.id === this.selectedRoomId) return 'rgba(0, 87, 183, 0.2)';
        if (room.confidence >= 0.8) return 'rgba(0, 200, 100, 0.15)';
        if (room.confidence >= 0.5) return 'rgba(255, 200, 0, 0.2)';
        return 'rgba(255, 80, 80, 0.2)';  // Red = needs attention
    }
}
```

### Engineer workflow

1. **Upload PDF** → parser runs → redirected to review UI
2. **Canvas shows** floor plan with all detected rooms color-coded by confidence
3. **Red rooms** (low confidence or unknown type) are shown first in sidebar
4. **Click a room** → right panel populates with all editable fields
5. **Warnings panel** shows all issues that must be resolved before submission
6. **"Submit to Load Calc"** button is disabled until all warnings are cleared
7. On submit → data converts to existing load calc input format → calc runs normally

---

## Phase 10 — Load Calc Integration

### Conversion function

```python
def convert_to_load_calc_format(drawing_data: dict) -> dict:
    """
    Converts reviewed drawing JSON to the format your existing
    load calc engine expects. Adapt field names to match your
    current manual input structure.
    """
    rooms_out = []
    
    for room in drawing_data["rooms"]:
        room_out = {
            "name": room["label"],
            "type": room["room_type"],
            "area_sqft": room["area_sqft"],
            "ceiling_height_ft": room["ceiling_height_ft"],
            "volume_cuft": room["volume_cuft"],
            "is_conditioned": room["is_conditioned"],
            "roof": room.get("roof"),     # type, r/u-value, pitch, color, area
            "floor": room.get("floor"),   # type, r-value, exposed perimeter, f-factor
            "occupants": room["occupants"],
            "lighting_w_per_sqft": room["lighting_w_per_sqft"],
            "equipment_w_per_sqft": room["equipment_w_per_sqft"],
            "ventilation_cfm": room["ventilation_cfm"],
            "infiltration_ach": room["infiltration_ach"],
            "walls": []
        }
        
        for wall in room["walls"]:
            wall_out = {
                "orientation": wall["orientation"],
                "length_ft": wall["length_ft"],
                "height_ft": wall["height_ft"],
                "net_area_sqft": wall["net_area_sqft"],   # <- load uses net, not length
                "is_exterior": wall["is_exterior"],
                "adjacent_condition": wall["adjacent_condition"],
                "construction": wall["construction"],
                "u_value": wall["u_value"],
                "windows": [
                    {
                        "width_ft": w["width_ft"],
                        "height_ft": w["height_ft"],
                        "sill_height_ft": w["sill_height_ft"],
                        "u_value": w["u_value"],
                        "shgc": w["shgc"],
                        "overhang_depth_ft": w["overhang_depth_ft"]
                    }
                    for w in wall["windows"]
                ],
                "doors": [
                    {
                        "width_ft": d["width_ft"],
                        "height_ft": d["height_ft"],
                        "is_exterior": d["is_exterior"],
                        "u_value": d["u_value"],
                        "is_glazed": d["is_glazed"],
                        "shgc": d["shgc"]
                    }
                    for d in wall["doors"]
                ]
            }
            room_out["walls"].append(wall_out)
        
        rooms_out.append(room_out)
    
    return {"rooms": rooms_out, "project": drawing_data.get("project", {})}
```

---

## Phase 11 — RTS Load Engine & ASHRAE Validation

We compute loads ourselves with the **Radiant Time Series (RTS)** method (ASHRAE
Handbook–Fundamentals Ch. 18) — the standard commercial peak-load method, and the
right fit for the Florida cooling/latent-dominated work. The engine is correct *by
definition* when it reproduces the bundled ASHRAE example workbooks. Don't invent
acceptance numbers — these are them.

### Engine pipeline (each stage independently checkable)

```
1. Design temps   — monthly/hourly design DB/WB from the design day
                    (selected weather station + daily-range profile)
2. Solar          — sun position + clear-sky beam/diffuse irradiance per surface
                    (station lat/lon + date/hour)
3. Opaque cond.   — sol-air temp → CTS (conduction time series) → wall/roof gain
4. Fenestration   — window solar via SHGC(incidence) × IAC(blinds) × overhang/fin
                    shading geometry, + conduction
5. Internal gains — people / lighting / equipment, sensible + latent
6. Gain→load      — split each gain radiant/convective; run the radiant part
                    through the RTS; sum 24 h; take the peak hour
7. Latent + OA    — room latent, ventilation (62.1) + infiltration loads
8. Aggregation    — room → zone/AHU block → building; peak month/hour per level
```

### Validation ladder — match bottom-up, components before totals

If a component matches but the room total doesn't, the bug is in the RTS
aggregation (step 6/8), not the heat gain. That ordering is the whole point.

**Component unit tests** (`RTS Components Examples …xls`):

| Test | Sheet(s) | What it pins down |
|---|---|---|
| Design temps | Table 27 | hourly design-DB profile from the design day |
| Wall conduction | Table 29A → 29B, `Wall CTS Data` | irradiance → sol-air temp → CTS → cooling load |
| Roof conduction | `Roof`, `Roof CTS Data` | same path for the roof |
| Window, clear | Table 30 → 31, `Window SHGC`, `Solar Table` | beam + diffuse + conduction → cooling load |
| Window + blinds | Table 32 | interior shade attenuation (IAC) |
| Window + overhang | Table 33 | overhang/fin shading geometry |
| Lighting | Table 28 | gain → radiant/convective split → RTS → load |
| People / equipment | `People`, `Equipment` | sensible/latent split + usage profile |

**Integration tests** (`RTS Cooling and Heating Loads Examples …xls`) — golden peaks
extracted to `validation/ashrae_golden.json` (regenerate with
`validation/extract_golden.py`):

| Case | Sheet | Peak | Total cooling | Heating |
|---|---|---|---|---|
| Single room (215B) | Table 35 | Aug, 2 pm | 4,122 Btu/h (0.34 ton) | 4,391 Btu/h |
| AHU-1, 1st floor | Table 37 | Aug, 3 pm | 417,766 Btu/h (34.8 ton) | 229,135 Btu/h |
| AHU-2, 2nd floor | Table 38 | Jul, 2 pm | 361,223 Btu/h (30.1 ton) | 271,190 Btu/h |
| Whole building | Table 39 | Jul, 3 pm | 790,953 Btu/h (65.9 ton) | 547,885 Btu/h |

The single-room case is the first end-to-end milestone: it already exercises wall
conduction (two types), a window with a 10 ft overhang, roof, and all three
internal-gain types. Get **4,122 Btu/h** and the core engine is sound.

### Tolerances

Match components to **≤ 1 %** and room/block/building totals to **≤ 2 %**. The
workbooks carry full floating-point precision (`ashrae_golden.json` keeps it), so
larger gaps are real method bugs, not rounding — common culprits: interpolating the
SHGC-vs-incidence or solar tables differently, an off-by-one in the RTS/CTS hour
indexing, or applying the daily-range profile in local vs solar time.

```python
# tests/test_ashrae_rts.py — shape of the harness
import json, pytest
GOLD = json.load(open("validation/ashrae_golden.json"))["cases"]

@pytest.mark.parametrize("case", GOLD)
def test_total_cooling(case):
    expected = GOLD[case]["total_block_cooling_btuh"]
    actual = run_engine(case).total_block_cooling_btuh   # our RTS engine
    assert abs(actual - expected) / expected <= 0.02
```

> The peak **month and hour** are part of the answer, not just the magnitude. An
> engine that lands the right Btu/h on the wrong hour has a broken time series and
> got lucky — assert `peak_month`/`peak_hour` too.

---

## Build Order for Claude Code

Build strictly in this order. Each phase is independently testable.

```
Phase 1  →  parser/models.py
Phase 2  →  parser/pdf_extractor.py  +  test on a real Adicot PDF
Phase 3  →  parser/scale_calibrator.py  +  verify scale against known dimensions
Phase 4  →  parser/wall_detector.py  +  visualize detected walls (debug render)
Phase 5  →  parser/room_builder.py  +  verify polygon areas match real rooms
Phase 6  →  parser/opening_detector.py  +  verify windows/doors located correctly
Phase 7  →  parser/text_parser.py  +  verify labels assigned to correct rooms
Phase 8  →  parser/north_arrow.py  +  verify wall orientations
Phase 8.5→  parser/thermal_defaults.py  +  lookup tables (R-values, design temps)
           — ceiling height, wall areas, roof/floor type, gains, ventilation
Phase 9  →  Flask routes in app.py  +  session management
Phase 10 →  review.html + canvas_renderer.js + room_editor.js  (+ project/thermal fields)
Phase 11 →  convert_to_load_calc_format()  +  end-to-end test
```

---

## Debug Visualization (Build Early)

Before the full review UI exists, build a simple debug renderer to verify parser output visually. This saves enormous debugging time.

```python
# debug_render.py — run from command line
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from parser.pdf_extractor import extract_page
from parser.wall_detector import detect_walls
from parser.room_builder import build_room_polygons

def debug_render(pdf_path):
    raw = extract_page(pdf_path, 0)
    scale = 10  # hardcode for debug
    walls = detect_walls(raw["lines"], scale)
    rooms = build_room_polygons(walls, scale)
    
    fig, ax = plt.subplots(1, 1, figsize=(20, 16))
    
    # Draw raw lines in gray
    for line in raw["lines"]:
        ax.plot([line["x1"], line["x2"]], [line["y1"], line["y2"]], 
                'lightgray', linewidth=0.5)
    
    # Draw detected walls in black
    for wall in walls:
        ax.plot([wall["x1"], wall["x2"]], [wall["y1"], wall["y2"]], 
                'black', linewidth=2)
    
    # Draw detected rooms in color
    colors = plt.cm.Set3.colors
    for i, room in enumerate(rooms):
        x, y = room.exterior.xy
        ax.fill(x, y, alpha=0.3, color=colors[i % len(colors)])
        ax.plot(x, y, color=colors[i % len(colors)], linewidth=1)
    
    plt.savefig("debug_output.png", dpi=150, bbox_inches='tight')
    print("Saved debug_output.png")

if __name__ == "__main__":
    import sys
    debug_render(sys.argv[1])
```

Run after each phase: `python debug_render.py sample.pdf`

---

## Known Hard Cases — Flag for Later

These are real issues you will hit on some drawings. Don't solve them in V1 — detect and surface them as warnings.

| Case | Detection | Response |
|---|---|---|
| Multi-page drawing | Page count > 1 | Let engineer select which page is floor plan |
| Scale only in title block | No dimension callouts | Prompt engineer to input scale manually |
| Open plan (no interior walls) | Single large polygon | Flag, engineer defines zones manually |
| Reflected ceiling plan on same page | Two overlapping wall networks | Flag, engineer selects correct layer |
| North arrow missing | No compass text found | Default north = up, show warning |
| Window schedule on separate page | No schedule text on floor plan page | Flag missing U-values for all windows |
| Curved walls | Arc segments in wall layer | Approximate as chord for now, flag |
| Ceiling height unknown | Not on floor plan | Apply project default, flag every room to confirm |
| Roof type unknown | Not on floor plan | Default top floor to attic, flag; rooms below → conditioned_above |
| Multi-story building | `number_of_floors > 1` | Only top floor gets roof load, only bottom gets floor load |
| Vaulted/cathedral ceiling | Sloped ceiling in section | Sloped roof area > floor area; scale by pitch, flag |
| No weather station selected | `project.weather is None` | Block submit — every design temp derives from the station |

---

## Session Storage

For Render deployment, use file-based session storage (no Redis needed for V1).

```python
import json, os, uuid

SESSION_DIR = "/tmp/adicot_sessions"

def save_session(session_id: str, data: dict):
    os.makedirs(SESSION_DIR, exist_ok=True)
    path = os.path.join(SESSION_DIR, f"{session_id}.json")
    with open(path, "w") as f:
        json.dump(data, f)

def load_session(session_id: str) -> dict:
    path = os.path.join(SESSION_DIR, f"{session_id}.json")
    with open(path) as f:
        return json.load(f)
```

Note: `/tmp` on Render is ephemeral. Sessions clear on dyno restart. Acceptable for V1 — engineer uploads and completes in one session. For V2, persist to your existing storage solution.

---

## Summary Checklist for Claude Code

- [ ] `parser/models.py` — all dataclasses defined (incl. Roof, Floor, ProjectInfo)
- [ ] `parser/pdf_extractor.py` — lines, paths, text extracted from PDF
- [ ] `parser/scale_calibrator.py` — px_per_ft from dimension callouts
- [ ] `parser/wall_detector.py` — walls filtered by width/color/length
- [ ] `parser/room_builder.py` — enclosed polygons from wall network
- [ ] `parser/opening_detector.py` — windows (parallel line groups) + doors (arcs)
- [ ] `parser/text_parser.py` — labels assigned to rooms, room types mapped
- [ ] `parser/north_arrow.py` — north detected, wall orientations assigned
- [ ] `parser/thermal_defaults.py` — ceiling height, wall areas, roof/floor type, internal gains, ventilation seeded + flagged (Phase 7.5)
- [ ] weather-station dataset (bundled ASHRAE design data) + `resolve_station()` + `apply_weather()` + station picker in the review UI
- [ ] assembly lookup table (R-values by construction); design-day profile + clear-sky solar from the selected station
- [ ] `app.py` — upload, review, API, submit routes added
- [ ] `templates/upload.html` — PDF upload form
- [ ] `templates/review.html` — review UI shell
- [ ] `static/js/canvas_renderer.js` — floor plan drawn on canvas
- [ ] `static/js/room_editor.js` — right panel edit logic
- [ ] `debug_render.py` — matplotlib debug visualization
- [ ] `convert_to_load_calc_format()` — bridge into the RTS engine input format
- [ ] RTS load engine — solar, CTS conduction, fenestration, internal gains, RTS aggregation (Phase 11)
- [ ] `validation/ashrae_golden.json` + `extract_golden.py` — golden fixtures from the ASHRAE workbooks
- [ ] `tests/test_ashrae_rts.py` — component tests (Tables 28–33) then integration (single room → building), within tolerance
- [ ] End-to-end test on one real Adicot drawing
