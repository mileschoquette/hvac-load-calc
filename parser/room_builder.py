"""Phase 4 — wall network -> enclosed room polygons.

Pipeline: rejoin CAD-fragmented collinear wall pieces -> keep only true wall
pairs (drop furniture/fixture/dimension lines) -> close door/window gaps ->
NODE the network (split every segment at its intersections) -> polygonize into
faces -> area filter -> resolve nesting (closet/core punched out as a hole).

NOTE: real CAD walls cross and meet without shared vertices, so a raw half-edge
traversal (architecture-doc Phase 4) finds nothing. shapely's unary_union does the
planarization (noding) for free and polygonize builds the faces robustly; that's
what we use here."""
import math
from shapely import set_precision
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union, polygonize


# ── Geometry helpers ─────────────────────────────────────────────────────────
def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


def _dir(w):
    return _unit(w["x2"] - w["x1"], w["y2"] - w["y1"])


def _llen(w):
    return math.hypot(w["x2"] - w["x1"], w["y2"] - w["y1"])


def _eff_width(poly) -> float:
    """Effective width = 2*area/perimeter. Small for long thin slivers AND for
    wrapping rings (the cavity between double exterior wall lines), unlike a
    bounding-box short side. Used to reject non-room slivers."""
    return 2.0 * poly.area / poly.length if poly.length else 0.0


# ── Step 0a: rejoin CAD-fragmented wall pieces ───────────────────────────────
def merge_collinear(walls, px_per_ft, angle_tol_deg=2.0, offset_in=2.0,
                    gap_in=6.0):
    """Rejoin wall faces the CAD split into many short collinear pieces, so a
    'gap' in a line means a real opening (door/window) rather than fragmentation.
    Segments are grouped onto a shared supporting line (same direction, same
    perpendicular offset) and merged where they overlap or sit within a small
    gap (< gap_in). Real door-width gaps (>~2 ft) are preserved; the ~2 in offset
    tolerance keeps the two faces of a wall separate."""
    cos_tol = math.cos(math.radians(angle_tol_deg))
    offset_tol = (offset_in / 12) * px_per_ft
    gap = (gap_in / 12) * px_per_ft

    groups = []
    for w in walls:
        d = _dir(w)
        if d == (0.0, 0.0):
            continue
        placed = False
        for g in groups:
            gd, gn, rx, ry = g["dir"], g["n"], g["ref"][0], g["ref"][1]
            if abs(d[0] * gd[0] + d[1] * gd[1]) < cos_tol:           # parallel?
                continue
            if abs((w["x1"] - rx) * gn[0] + (w["y1"] - ry) * gn[1]) > offset_tol:
                continue                                            # same line?
            t1 = (w["x1"] - rx) * gd[0] + (w["y1"] - ry) * gd[1]
            t2 = (w["x2"] - rx) * gd[0] + (w["y2"] - ry) * gd[1]
            g["intervals"].append((min(t1, t2), max(t1, t2)))
            placed = True
            break
        if not placed:
            groups.append({"dir": d, "n": (-d[1], d[0]), "ref": (w["x1"], w["y1"]),
                           "src": w, "intervals": [(0.0, _llen(w))]})

    out, k = [], 0
    for g in groups:
        (rx, ry), d = g["ref"], g["dir"]
        ivs = sorted(g["intervals"])
        cl, ch = ivs[0]
        merged = []
        for lo, hi in ivs[1:]:
            if lo <= ch + gap:
                ch = max(ch, hi)
            else:
                merged.append((cl, ch)); cl, ch = lo, hi
        merged.append((cl, ch))
        for lo, hi in merged:
            out.append({**g["src"], "id": f"M{k}",
                        "x1": rx + d[0] * lo, "y1": ry + d[1] * lo,
                        "x2": rx + d[0] * hi, "y2": ry + d[1] * hi}); k += 1
    return out


def keep_wall_pairs(walls, px_per_ft, min_thick_ft=2 / 12, max_thick_ft=18 / 12,
                    angle_tol_deg=8.0):
    """Keep only segments that belong to a parallel pair a wall-thickness apart
    (the two faces of a real wall). Drops lone lines (furniture, fixtures,
    dimension/annotation) and far-apart pairs like casework/counters (~24 in),
    which would otherwise act as false room borders. Both faces are kept."""
    min_px, max_px = min_thick_ft * px_per_ft, max_thick_ft * px_per_ft
    cos_tol = math.cos(math.radians(angle_tol_deg))
    keep = set()
    for i, a in enumerate(walls):
        da = _dir(a)
        nx_, ny_ = -da[1], da[0]
        t = lambda px, py: (px - a["x1"]) * da[0] + (py - a["y1"]) * da[1]
        a0, a1 = sorted((0.0, t(a["x2"], a["y2"])))
        for j in range(len(walls)):
            if j == i:
                continue
            b = walls[j]; db = _dir(b)
            if abs(da[0] * db[0] + da[1] * db[1]) < cos_tol:        # parallel?
                continue
            mx, my = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
            gap = abs((mx - a["x1"]) * nx_ + (my - a["y1"]) * ny_)
            if not (min_px <= gap <= max_px):                       # wall-thick?
                continue
            b0, b1 = sorted((t(b["x1"], b["y1"]), t(b["x2"], b["y2"])))
            if min(a1, b1) - max(a0, b0) <= 0:                      # face-to-face?
                continue
            keep.add(i); keep.add(j)
            break
    return [walls[i] for i in sorted(keep)]


def close_wall_gaps(walls, px_per_ft, min_gap_ft=1.5, max_gap_ft=7.0,
                    angle_tol_deg=2.0, offset_in=2.0):
    """Close openings (doors/windows) found as door-width GAPS between collinear
    pieces of the same wall line. Operates on MERGED walls, so a gap is a real
    opening rather than fragmentation. Closing a door gap separates two rooms;
    closing a window/perimeter gap just completes the envelope (harmless). Wide
    cased openings (> max_gap_ft) are left open — an earlier version could gate
    those by a detected door swing-arc point, but every gating attempt made both
    real drawings worse (real doors often measure as wide gaps when the wall
    line stops short of the jamb, so the gate opened them) — removed rather than
    kept as an always-inert parameter."""
    cos_tol = math.cos(math.radians(angle_tol_deg))
    offset_tol = (offset_in / 12) * px_per_ft
    gmin, gmax = min_gap_ft * px_per_ft, max_gap_ft * px_per_ft

    groups = []
    for w in walls:
        d = _dir(w)
        if d == (0.0, 0.0):
            continue
        placed = False
        for g in groups:
            gd, gn, r = g["dir"], g["n"], g["ref"]
            if abs(d[0] * gd[0] + d[1] * gd[1]) < cos_tol:
                continue
            if abs((w["x1"] - r[0]) * gn[0] + (w["y1"] - r[1]) * gn[1]) > offset_tol:
                continue
            t1 = (w["x1"] - r[0]) * gd[0] + (w["y1"] - r[1]) * gd[1]
            t2 = (w["x2"] - r[0]) * gd[0] + (w["y2"] - r[1]) * gd[1]
            g["iv"].append((min(t1, t2), max(t1, t2))); placed = True; break
        if not placed:
            groups.append({"dir": d, "n": (-d[1], d[0]), "ref": (w["x1"], w["y1"]),
                           "iv": [(0.0, _llen(w))]})

    closures, k = [], 0
    for g in groups:
        iv = sorted(g["iv"]); r, d = g["ref"], g["dir"]
        m = []; cl, ch = iv[0]
        for lo, hi in iv[1:]:
            if lo <= ch + 1e-6:
                ch = max(ch, hi)
            else:
                m.append((cl, ch)); cl, ch = lo, hi
        m.append((cl, ch))
        for (l1, h1), (l2, h2) in zip(m, m[1:]):
            gap = l2 - h1
            if not (gmin <= gap <= gmax):
                continue
            closures.append({"id": f"GC{k}", "width": 0.0, "color": (0., 0., 0.),
                             "x1": r[0] + d[0] * h1, "y1": r[1] + d[1] * h1,
                             "x2": r[0] + d[0] * l2, "y2": r[1] + d[1] * l2}); k += 1
    return closures


# ── Rooms from the (noded) centerline network ────────────────────────────────
def build_room_polygons(walls: list[dict], px_per_ft: float,
                        min_room_sqft: float = 20.0,
                        min_width_ft: float = 1.5,
                        snap_in: float = 4.0,
                        extend_in: float = 4.0,
                        wall_pairs_only: bool = True,
                        recognize_doors: bool = True) -> list[Polygon]:
    # Rejoin CAD-fragmented wall pieces into continuous lines, so a gap in a line
    # is a real opening (door/window), not fragmentation noise.
    walls = merge_collinear(walls, px_per_ft)

    # Keep only the two-faces-of-a-wall lines; drop furniture/fixture/dim lines.
    if wall_pairs_only:
        walls = keep_wall_pairs(walls, px_per_ft)

    # Seal openings with the robust collinear-gap closer (closes every
    # opening-width gap incl. perimeter). The small residual over-split
    # (Sample A 22 vs 19) is review-UI cleanup.
    if recognize_doors:
        walls = walls + close_wall_gaps(walls, px_per_ft)

    # Extend each segment a few inches at both ends so partition walls that stop
    # just short of the wall they meet still cross it and node at the T-junction.
    ext = (extend_in / 12.0) * px_per_ft
    segs = []
    for w in walls:
        p1, p2 = (w["x1"], w["y1"]), (w["x2"], w["y2"])
        if p1 == p2:
            continue
        dx, dy = _unit(p2[0] - p1[0], p2[1] - p1[1])
        segs.append(LineString([(p1[0] - dx * ext, p1[1] - dy * ext),
                                 (p2[0] + dx * ext, p2[1] + dy * ext)]))
    if not segs:
        return []

    # NODE the network (split at every intersection); snap to a real-unit grid
    # (~4 in) so near-meeting wall corners close, then re-node.
    noded = unary_union(segs)
    grid = (snap_in / 12.0) * px_per_ft
    if grid > 0:
        noded = unary_union(set_precision(noded, grid))
    faces = [p for p in polygonize(noded)
             if p.is_valid and p.area / px_per_ft ** 2 > min_room_sqft
             and _eff_width(p) / px_per_ft > min_width_ft]

    # Resolve nesting: subtract any room fully inside another (closet/core).
    final = []
    for outer in faces:
        inner = [r for r in faces if r is not outer and outer.contains(r)]
        final.append(outer.difference(unary_union(inner)) if inner else outer)
    return final
