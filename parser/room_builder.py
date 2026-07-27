"""Phase 4 — wall network -> enclosed room polygons.

Pipeline: collapse double-line walls to centerlines -> snap (real-unit tolerance)
-> NODE the network (split every segment at its intersections) -> polygonize into
faces -> area filter -> resolve nesting (closet/core punched out as a hole).

NOTE: real CAD walls cross and meet without shared vertices, so a raw half-edge
traversal (architecture-doc Phase 4) finds nothing. shapely's unary_union does the
planarization (noding) for free and polygonize builds the faces robustly; that's
what we use here. The double-line collapse and nesting logic from the doc are kept."""
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


def _pt_seg(px, py, w):
    x1, y1, x2, y2 = w["x1"], w["y1"], w["x2"], w["y2"]
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _pt_walls(p, walls):
    return min((_pt_seg(p[0], p[1], w) for w in walls), default=1e9)


def _arc_center(p1, p2, p3, p4):
    """Center of a Bezier arc = intersection of the two end-radii (normals to the
    end tangents). Works for any arc angle; None if the segment isn't an arc."""
    t1 = (p2[0] - p1[0], p2[1] - p1[1])
    t4 = (p3[0] - p4[0], p3[1] - p4[1])
    n1 = (-t1[1], t1[0])
    n4 = (-t4[1], t4[0])
    den = n1[0] * (-n4[1]) - n1[1] * (-n4[0])
    if abs(den) < 1e-9:
        return None
    bx, by = p4[0] - p1[0], p4[1] - p1[1]
    s = (bx * (-n4[1]) - by * (-n4[0])) / den
    return (p1[0] + s * n1[0], p1[1] + s * n1[1])


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


# ── Step 0: collapse double-line walls to centerlines ────────────────────────
def collapse_double_lines(walls, px_per_ft,
                          min_thick_ft=2 / 12, max_thick_ft=16 / 12,
                          angle_tol_deg=6.0):
    """Replace each pair of parallel wall faces with one centerline segment.
    Greedy: each segment pairs with its closest valid partner. Unpaired segments
    (true single-line walls, already-centerline drawings) pass through."""
    min_px, max_px = min_thick_ft * px_per_ft, max_thick_ft * px_per_ft
    cos_tol = math.cos(math.radians(angle_tol_deg))

    def perp_gap(a, b, da):                       # signed dist: b's midpoint -> a's line
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


def bridge_openings(walls, px_per_ft, max_gap_ft=3.6, angle_tol_deg=8.0):
    """Close door/window openings. A doorway is a gap in an otherwise-straight
    wall, so a room leaks through it and merges with its neighbor. Bridge two
    near-collinear segments whose facing endpoints sit up to a doorway-width
    apart — re-closing the wall the door interrupts. Perpendicular T-junctions
    are NOT bridged (the gap must run ALONG the wall direction)."""
    cos_tol = math.cos(math.radians(angle_tol_deg))
    max_gap = max_gap_ft * px_per_ft
    ends = []
    for idx, w in enumerate(walls):
        d = _dir(w)
        ends.append(((w["x1"], w["y1"]), d, idx))
        ends.append(((w["x2"], w["y2"]), d, idx))

    bridges, used = [], set()
    for i in range(len(ends)):
        (pi, di, si) = ends[i]
        for j in range(i + 1, len(ends)):
            (pj, dj, sj) = ends[j]
            if si == sj:
                continue
            vx, vy = pj[0] - pi[0], pj[1] - pi[1]
            gap = math.hypot(vx, vy)
            if gap < 1e-6 or gap > max_gap:
                continue
            uv = (vx / gap, vy / gap)
            if abs(di[0] * dj[0] + di[1] * dj[1]) < cos_tol:      # walls parallel?
                continue
            if abs(uv[0] * di[0] + uv[1] * di[1]) < cos_tol:      # gap along the wall?
                continue
            key = (min(si, sj), max(si, sj))
            if key in used:
                continue
            used.add(key)
            bridges.append({"id": f"BR{len(bridges)}", "x1": pi[0], "y1": pi[1],
                            "x2": pj[0], "y2": pj[1], "width": 0.0,
                            "color": (0.0, 0.0, 0.0)})
    return walls + bridges


def detect_door_closures(wall_faces, candidates, px_per_ft,
                         leaf_min_ft=2.0, leaf_max_ft=3.6, attach_in=7.0,
                         gap_in=12.0, perp_cos=0.34, par_cos=0.985,
                         max_thick_ft=18 / 12):
    """Find doors and return segments that seal their openings.

    A door is drawn as a 'leaf' — a short line (~door width) anchored at the
    jamb and sticking out perpendicular to the wall. The leaf is a single line
    (dropped by the pair filter), so we look for it in the pre-filter candidates:
    short, perpendicular to a nearby wall face, one end ON the wall and the other
    sticking out. We confirm it's a real door (not casework) by requiring the
    base to sit at a wall BREAK — near a wall-face endpoint. Then we seal the
    opening by laying a closing segment along each wall face (both sides of the
    wall) spanning the door width."""
    attach = (attach_in / 12) * px_per_ft
    gapd = (gap_in / 12) * px_per_ft
    max_perp = max_thick_ft * px_per_ft
    fpts = ([(f["x1"], f["y1"]) for f in wall_faces]
            + [(f["x2"], f["y2"]) for f in wall_faces])

    closures = []
    for c in candidates:
        L = _llen(c)
        if not (leaf_min_ft * px_per_ft <= L <= leaf_max_ft * px_per_ft):
            continue
        cd = _dir(c)
        for (bx, by), (ox, oy) in (((c["x1"], c["y1"]), (c["x2"], c["y2"])),
                                    ((c["x2"], c["y2"]), (c["x1"], c["y1"]))):
            # nearest wall face to this end (the base)
            best = None
            for f in wall_faces:
                d = _pt_seg(bx, by, f)
                if best is None or d < best[1]:
                    best = (f, d)
            if best is None or best[1] > attach:
                continue
            fd = _dir(best[0])
            if abs(cd[0] * fd[0] + cd[1] * fd[1]) > perp_cos:   # not perpendicular?
                continue
            if _pt_seg(ox, oy, best[0]) < attach:               # other end must stick out
                continue
            # confirm a real door: base sits at a wall break (near a face endpoint)
            if min(math.hypot(bx - px, by - py) for px, py in fpts) > gapd:
                continue
            # seal the opening on each wall face near the base (both wall sides)
            for g in wall_faces:
                gd = _dir(g)
                if abs(gd[0] * fd[0] + gd[1] * fd[1]) < par_cos:   # parallel to the wall?
                    continue
                ngx, ngy = -gd[1], gd[0]
                if abs((bx - g["x1"]) * ngx + (by - g["y1"]) * ngy) > max_perp:
                    continue
                t = (bx - g["x1"]) * gd[0] + (by - g["y1"]) * gd[1]
                px0, py0 = g["x1"] + gd[0] * t, g["y1"] + gd[1] * t
                closures.append({"id": f"DC{len(closures)}",
                                 "x1": px0 - gd[0] * L, "y1": py0 - gd[1] * L,
                                 "x2": px0 + gd[0] * L, "y2": py0 + gd[1] * L,
                                 "width": 0.0, "color": (0.0, 0.0, 0.0)})
            break   # one base per leaf is enough
    return closures


def close_wall_gaps(walls, px_per_ft, min_gap_ft=1.5, max_gap_ft=7.0,
                    angle_tol_deg=2.0, offset_in=2.0,
                    narrow_max_ft=3.6, door_pts=None):
    """Close openings (doors/windows) found as door-width GAPS between collinear
    pieces of the same wall line. Operates on MERGED walls, so a gap is a real
    opening rather than fragmentation. Closing a door gap separates two rooms;
    closing a window/perimeter gap just completes the envelope (harmless). Wide
    cased openings (> max_gap_ft) are left open."""
    cos_tol = math.cos(math.radians(angle_tol_deg))
    offset_tol = (offset_in / 12) * px_per_ft
    gmin, gmax = min_gap_ft * px_per_ft, max_gap_ft * px_per_ft
    narrow_max = narrow_max_ft * px_per_ft

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
        iv = sorted(g["iv"]); r, d, gn = g["ref"], g["dir"], g["n"]
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
            if door_pts is not None and gap > narrow_max:
                # WIDE gap: only a real door (swing arc inside the opening) closes
                # it; a cased opening with no arc stays open so the room isn't split.
                tol_t, tol_p = 0.5 * px_per_ft, 1.5 * px_per_ft
                hit = any(
                    h1 - tol_t <= (P[0]-r[0])*d[0] + (P[1]-r[1])*d[1] <= l2 + tol_t
                    and abs((P[0]-r[0])*gn[0] + (P[1]-r[1])*gn[1]) <= tol_p
                    for P in door_pts)
                if not hit:
                    continue
            closures.append({"id": f"GC{k}", "width": 0.0, "color": (0., 0., 0.),
                             "x1": r[0] + d[0] * h1, "y1": r[1] + d[1] * h1,
                             "x2": r[0] + d[0] * l2, "y2": r[1] + d[1] * l2}); k += 1
    return closures


def detect_door_arcs(curves, wall_faces, px_per_ft, rmin_ft=1.5, rmax_ft=4.0,
                     attach_in=6.0, par_cos=0.985, max_thick_ft=18 / 12):
    """Find doors from their SWING ARCS and return segments that seal the
    openings. An arc's center is the hinge and its radius is the door width;
    for a door both the hinge AND the closed-position endpoint lie ON the wall a
    door-width apart (fixtures' arcs float mid-room, so they're rejected). We
    seal that span on every wall face near the opening (both sides of the wall)."""
    attach = (attach_in / 12) * px_per_ft
    max_thick = max_thick_ft * px_per_ft
    closures = []
    for (p1, p2, p3, p4) in curves:
        c = _arc_center(p1, p2, p3, p4)
        if c is None:
            continue
        r1 = math.hypot(c[0] - p1[0], c[1] - p1[1])
        r4 = math.hypot(c[0] - p4[0], c[1] - p4[1])
        if abs(r1 - r4) > 0.15 * max(r1, r4):              # not a clean arc
            continue
        r = (r1 + r4) / 2
        if not (rmin_ft * px_per_ft <= r <= rmax_ft * px_per_ft):
            continue
        if _pt_walls(c, wall_faces) > attach:              # hinge must be on a wall
            continue
        d1, d4 = _pt_walls(p1, wall_faces), _pt_walls(p4, wall_faces)
        if min(d1, d4) > attach:                           # closed jamb must be on a wall
            continue
        closed = p1 if d1 < d4 else p4
        wd = _unit(closed[0] - c[0], closed[1] - c[1])     # along the wall
        L = math.hypot(closed[0] - c[0], closed[1] - c[1])  # = door width
        # seal the opening on each wall face parallel to the wall near the hinge
        for g in wall_faces:
            gd = _dir(g)
            if abs(gd[0] * wd[0] + gd[1] * wd[1]) < par_cos:
                continue
            ngx, ngy = -gd[1], gd[0]
            if abs((c[0] - g["x1"]) * ngx + (c[1] - g["y1"]) * ngy) > max_thick:
                continue
            t = (c[0] - g["x1"]) * gd[0] + (c[1] - g["y1"]) * gd[1]
            x0, y0 = g["x1"] + gd[0] * t, g["y1"] + gd[1] * t
            sgn = L if (gd[0] * wd[0] + gd[1] * wd[1]) > 0 else -L
            closures.append({"id": f"DA{len(closures)}", "x1": x0, "y1": y0,
                             "x2": x0 + gd[0] * sgn, "y2": y0 + gd[1] * sgn,
                             "width": 0.0, "color": (0.0, 0.0, 0.0)})
    return closures


def door_arc_points(curves, wall_faces, px_per_ft, rmin_ft=1.5, rmax_ft=4.0,
                    attach_in=6.0):
    """Opening-center points of valid door swing arcs (hinge + closed jamb both
    on a wall). Used to gate wide gaps in close_wall_gaps."""
    attach = (attach_in / 12) * px_per_ft
    pts = []
    for (p1, p2, p3, p4) in curves:
        c = _arc_center(p1, p2, p3, p4)
        if c is None:
            continue
        r1 = math.hypot(c[0] - p1[0], c[1] - p1[1])
        r4 = math.hypot(c[0] - p4[0], c[1] - p4[1])
        if abs(r1 - r4) > 0.15 * max(r1, r4):
            continue
        if not (rmin_ft * px_per_ft <= (r1 + r4) / 2 <= rmax_ft * px_per_ft):
            continue
        if _pt_walls(c, wall_faces) > attach:
            continue
        d1, d4 = _pt_walls(p1, wall_faces), _pt_walls(p4, wall_faces)
        if min(d1, d4) > attach:
            continue
        closed = p1 if d1 < d4 else p4
        pts.append(((c[0] + closed[0]) / 2, (c[1] + closed[1]) / 2))
    return pts


# ── Rooms from the (noded) centerline network ────────────────────────────────
def build_room_polygons(walls: list[dict], px_per_ft: float,
                        min_room_sqft: float = 20.0,
                        min_width_ft: float = 1.5,
                        snap_in: float = 4.0,
                        extend_in: float = 4.0,
                        wall_pairs_only: bool = True,
                        recognize_doors: bool = True,
                        curves: list | None = None,
                        collapse: bool = False) -> list[Polygon]:
    # collapse=False by default: polygonize returns the room interior as a face
    # and double-line wall cavities as slivers (dropped by the area filter), so
    # collapsing is unnecessary here and currently shatters the network.
    if collapse:
        walls = collapse_double_lines(walls, px_per_ft)

    # Rejoin CAD-fragmented wall pieces into continuous lines, so a gap in a line
    # is a real opening (door/window), not fragmentation noise.
    walls = merge_collinear(walls, px_per_ft)

    candidates = walls
    # Keep only the two-faces-of-a-wall lines; drop furniture/fixture/dim lines.
    if wall_pairs_only:
        walls = keep_wall_pairs(walls, px_per_ft)

    # Seal openings with the robust collinear-gap closer (closes every
    # opening-width gap incl. perimeter). The width+arc gate was tried and made
    # both drawings worse — real doors often measure as wide gaps (the wall line
    # stops short of the jamb), so the gate opened them. Keeping ungated; the
    # small residual over-split (Sample A 22 vs 19) is review-UI cleanup.
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
