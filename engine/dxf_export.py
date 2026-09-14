"""Exports drawn ductwork (review.html's duct_runs/duct_terminals/duct_equipment, edited in
the browser review UI) to DXF, one file per floor, for the engineer to xref onto the real
architectural drawing in AutoCAD. Room/wall geometry is explicitly out of scope. This is a
straight port of review.html's DM-accurate canvas rendering (offsetPolyline/flexRail/
drawTerminalIcon/drawEquipmentIcon around lines 495-680) into real-feet DXF geometry — not a
simplified re-derivation, since a plain single-line/box style was already tried and rejected.
"""
from __future__ import annotations
import math
import pathlib
import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import LineString

from engine.ducts import find_tee_point, effective_form, ELBOW_ANGLE_THRESHOLD_DEG

DUCT_DEFAULT_W_IN = 12
DUCT_DEFAULT_H_IN = 8
DUCT_DEFAULT_DIA_IN = 8
FLEX_STEP_FT = 2 / 12
FLEX_COLLAR_FT = 8 / 12
TERMINAL_SZ_FT = 0.4
EQUIP_SZ_FT = 0.75
LABEL_TEXT_H = 0.15
ARROW_LEN_FT = 0.3
ARROW_HALF_W_FT = 0.12
ELBOW_BEND_RADIUS_MULT = 1.5
LINEAR_DIFF_WIDTH_IN = 4
LINEAR_DIFF_BLADE_SPACING_IN = 3


def px_to_ft(point, px_per_ft):
    """Stored pixel point -> real feet; stored px is image-space Y-down, DXF is Y-up."""
    x, y = point
    return (x / px_per_ft, -y / px_per_ft)


def half_width_ft(run, form):
    """Duct half-width (ft) for outline offset — rect uses width_in, round/flex use
    diameter_in, both falling back to review.html's DUCT_DEFAULT_*_IN when missing."""
    if form == "rect":
        return (run.get("width_in") or DUCT_DEFAULT_W_IN) / 24
    return (run.get("diameter_in") or DUCT_DEFAULT_DIA_IN) / 24


def duct_size_label(run, form):
    """Matches review.html's ductSizeLabel; None (not drawn) if the relevant size field is missing."""
    if form == "rect":
        if run.get("width_in") and run.get("height_in"):
            return f"{run['width_in']:g}x{run['height_in']:g}"
        return None
    if run.get("diameter_in"):
        return f"{run['diameter_in']:g}Ø"
    return None


def _stitch_rails(left, right):
    """Forward `left` rail + reversed `right` rail -> a single closed ring."""
    return list(left) + list(reversed(right))


def rigid_outline(points_ft, half_w_ft):
    """Mitered double-line outline (closed ring) for a rect/round duct centerline —
    shapely's offset_curve replaces the JS's hand-rolled offsetPolyline miter math."""
    ls = LineString(points_ft)
    left = list(ls.offset_curve(half_w_ft, join_style="mitre").coords)
    right = list(ls.offset_curve(-half_w_ft, join_style="mitre").coords)
    return _stitch_rails(left, right)


def is_round_shape(run, form):
    """Round-for-drawing purposes only (real Design Master elbow arcs vs a rect miter
    break line) — explicit Form=Round, or an auto/rect run whose only size field is a
    diameter (no width/height). Does not touch effective_form()'s rect/flex topology
    semantics used elsewhere (pressure-drop, etc)."""
    if form == "round":
        return True
    return form == "rect" and bool(run.get("diameter_in")) and not run.get("width_in") and not run.get("height_in")


def _seg_dirs(points_ft):
    """Unit direction vector of each of a run's straight segments."""
    dirs = []
    for i in range(len(points_ft) - 1):
        dx = points_ft[i + 1][0] - points_ft[i][0]
        dy = points_ft[i + 1][1] - points_ft[i][1]
        length = math.hypot(dx, dy) or 1.0
        dirs.append((dx / length, dy / length))
    return dirs


def _miter_normal(dirs, i, n):
    """Unit (miter-lengthened at interior vertices) rail normal at vertex i — port of
    review.html's offsetPolyline per-vertex math."""
    if i == 0:
        dx, dy = dirs[0]
        return (-dy, dx)
    if i == n - 1:
        dx, dy = dirs[-1]
        return (-dy, dx)
    n1x, n1y = -dirs[i - 1][1], dirs[i - 1][0]
    n2x, n2y = -dirs[i][1], dirs[i][0]
    mx, my = n1x + n2x, n1y + n2y
    mlen = math.hypot(mx, my)
    if mlen < 1e-6:
        return (n2x, n2y)
    mx, my = mx / mlen, my / mlen
    cos_half = mx * n1x + my * n1y
    miter_len = 1 / max(cos_half, 0.2)
    return (mx * miter_len, my * miter_len)


def _turn_angle_deg(dirs, i):
    """Angle (0-180) the path bends at interior vertex i — same formula engine/ducts.py's
    _apply_pressure_drop already uses to decide "is this an elbow"."""
    v1, v2 = dirs[i - 1], dirs[i]
    cos_ang = max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1]))
    return math.degrees(math.acos(cos_ang))


def rect_break_lines(points_ft, half_w_ft):
    """Mitered-seam break line at each >ELBOW_ANGLE_THRESHOLD_DEG interior vertex of a
    rect duct run — confirmed against a real Design Master DWG as a plain same-layer
    LINE at each rectangular elbow corner."""
    dirs = _seg_dirs(points_ft)
    n = len(points_ft)
    lines = []
    for i in range(1, n - 1):
        if _turn_angle_deg(dirs, i) <= ELBOW_ANGLE_THRESHOLD_DEG:
            continue
        nx, ny = _miter_normal(dirs, i, n)
        p = points_ft[i]
        outer = (p[0] + nx * half_w_ft, p[1] + ny * half_w_ft)
        inner = (p[0] - nx * half_w_ft, p[1] - ny * half_w_ft)
        lines.append((outer, inner))
    return lines


def _elbow_fits(points_ft, dirs, half_w_ft, bend_radius_ft):
    """For each interior vertex that qualifies for a real radius elbow (turn angle beyond
    threshold, standard bend radius geometrically fits both the duct width and the
    adjacent segment lengths), compute the concentric-arc fillet: a shared center, and
    per-rail (left/right, i.e. +/-half_w normal side) radius + tangent points + arc sweep.
    Vertices that don't qualify map to None (plain sharp pass-through, same as today)."""
    n = len(points_ft)
    seg_lens = [math.hypot(points_ft[i + 1][0] - points_ft[i][0], points_ft[i + 1][1] - points_ft[i][1])
                for i in range(n - 1)]
    fits = {}
    for i in range(1, n - 1):
        d1, d2 = dirs[i - 1], dirs[i]
        theta_deg = _turn_angle_deg(dirs, i)
        if theta_deg <= ELBOW_ANGLE_THRESHOLD_DEG:
            fits[i] = None
            continue
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        s = 1.0 if cross >= 0 else -1.0
        radius_left = bend_radius_ft - s * half_w_ft
        radius_right = bend_radius_ft + s * half_w_ft
        theta_rad = math.radians(theta_deg)
        t = bend_radius_ft * math.tan(theta_rad / 2)
        if radius_left <= 1e-6 or radius_right <= 1e-6 or \
           t > 0.45 * seg_lens[i - 1] or t > 0.45 * seg_lens[i]:
            fits[i] = None
            continue
        bx, by = -d1[0] + d2[0], -d1[1] + d2[1]
        blen = math.hypot(bx, by)
        cos_half = math.cos(theta_rad / 2)
        if blen < 1e-9 or cos_half < 1e-6:
            fits[i] = None
            continue
        bx, by = bx / blen, by / blen
        cdist = bend_radius_ft / cos_half
        p = points_ft[i]
        center = (p[0] + cdist * bx, p[1] + cdist * by)
        t1c = (p[0] - t * d1[0], p[1] - t * d1[1])
        t2c = (p[0] + t * d2[0], p[1] + t * d2[1])
        n1 = (-d1[1], d1[0])
        n2 = (-d2[1], d2[0])
        tangent1_left = (t1c[0] + half_w_ft * n1[0], t1c[1] + half_w_ft * n1[1])
        tangent2_left = (t2c[0] + half_w_ft * n2[0], t2c[1] + half_w_ft * n2[1])
        tangent1_right = (t1c[0] - half_w_ft * n1[0], t1c[1] - half_w_ft * n1[1])
        tangent2_right = (t2c[0] - half_w_ft * n2[0], t2c[1] - half_w_ft * n2[1])

        def arc_start_end(c, p1, p2, expected_sweep_deg):
            a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0])) % 360
            a2 = math.degrees(math.atan2(p2[1] - c[1], p2[0] - c[0])) % 360
            sweep_12 = (a2 - a1) % 360
            sweep_21 = (a1 - a2) % 360
            if abs(sweep_12 - expected_sweep_deg) <= abs(sweep_21 - expected_sweep_deg):
                return a1, a2
            return a2, a1

        start_left, end_left = arc_start_end(center, tangent1_left, tangent2_left, theta_deg)
        start_right, end_right = arc_start_end(center, tangent1_right, tangent2_right, theta_deg)
        fits[i] = dict(center=center, radius_left=radius_left, radius_right=radius_right,
                        tangent1_left=tangent1_left, tangent2_left=tangent2_left,
                        tangent1_right=tangent1_right, tangent2_right=tangent2_right,
                        start_left=start_left, end_left=end_left,
                        start_right=start_right, end_right=end_right)
    return fits


def _rail_pieces(points_ft, dirs, half_w_ft, side, fits):
    """One rail's LINE/ARC pieces (side=+1 left-normal rail, -1 right-normal rail),
    straight through plain-miter vertices, an ARC at each fitted vertex."""
    n = len(points_ft)

    def plain_pt(i):
        nx, ny = _miter_normal(dirs, i, n)
        p = points_ft[i]
        return (p[0] + side * half_w_ft * nx, p[1] + side * half_w_ft * ny)

    lines, arcs = [], []
    prev = plain_pt(0)
    for i in range(1, n - 1):
        fit = fits.get(i)
        if fit is None:
            cur = plain_pt(i)
            lines.append((prev, cur))
            prev = cur
        else:
            if side == 1:
                t1, t2, radius, start, end = (fit["tangent1_left"], fit["tangent2_left"],
                                               fit["radius_left"], fit["start_left"], fit["end_left"])
            else:
                t1, t2, radius, start, end = (fit["tangent1_right"], fit["tangent2_right"],
                                               fit["radius_right"], fit["start_right"], fit["end_right"])
            lines.append((prev, t1))
            arcs.append((fit["center"], radius, start, end))
            prev = t2
    lines.append((prev, plain_pt(n - 1)))
    return lines, arcs


def round_outline_pieces(points_ft, half_w_ft, bend_radius_ft):
    """Round duct outline as separate LINE/ARC pieces per rail (not one polyline) — matches
    a real Design Master DWG's actual entity structure: individual straight rail LINEs plus
    concentric-arc elbows (radius = bend_radius +/- half_w, SMACNA "1.5D" bend_radius by
    default) at each turn beyond ELBOW_ANGLE_THRESHOLD_DEG. Falls back to a plain sharp
    pass-through (see _elbow_fits) if the standard bend radius doesn't geometrically fit."""
    n = len(points_ft)
    dirs = _seg_dirs(points_ft)
    fits = _elbow_fits(points_ft, dirs, half_w_ft, bend_radius_ft)
    lines_l, arcs_l = _rail_pieces(points_ft, dirs, half_w_ft, 1, fits)
    lines_r, arcs_r = _rail_pieces(points_ft, dirs, half_w_ft, -1, fits)

    def plain_pt(i, side):
        nx, ny = _miter_normal(dirs, i, n)
        p = points_ft[i]
        return (p[0] + side * half_w_ft * nx, p[1] + side * half_w_ft * ny)

    lines = lines_l + lines_r
    lines.append((plain_pt(0, 1), plain_pt(0, -1)))
    lines.append((plain_pt(n - 1, 1), plain_pt(n - 1, -1)))
    return lines, arcs_l + arcs_r


def _flex_rail_seg(a, b, half_w, bump, step, side, vis_lo, vis_hi, smooth_lo, smooth_hi):
    """Port of review.html's flexRail — one rail of one segment's corrugated bellows."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1e-9
    nx, ny = -dy / length, dx / length
    n = max(2, round(length / step))
    pts = []
    for k in range(n + 1):
        t = k / n
        dist_along = t * length
        if dist_along < vis_lo or dist_along > vis_hi:
            continue
        px, py = a[0] + dx * t, a[1] + dy * t
        smooth = smooth_lo <= dist_along <= smooth_hi
        w = half_w - (0 if smooth else (0 if k % 2 == 0 else bump))
        pts.append((px + nx * w * side, py + ny * w * side))
    return pts


def flex_outline(points_ft, half_w_ft, run, all_runs):
    """Corrugated flex-duct outline, ported from review.html's drawFlexRun: one closed ring
    per drawn segment (not a single continuous offset — a branch's collar/hidden-in-trunk
    stretch is trimmed per-segment exactly as the JS does it)."""
    bump = half_w_ft * 0.35
    n_seg = len(points_ft) - 1
    vis_lo = [0.0] * n_seg
    vis_hi = [math.inf] * n_seg
    smooth_lo = [0.0] * n_seg
    smooth_hi = [0.0] * n_seg

    tee = find_tee_point(run, all_runs)
    if tee:
        on_run, on_form = tee["on_run"], effective_form(tee["on_run"], all_runs)
        hide_len_ft = half_width_ft(on_run, on_form)
        hide_end, collar_end = hide_len_ft, hide_len_ft + FLEX_COLLAR_FT
        order = list(range(n_seg))
        if tee["end"] == "end":
            order.reverse()
        cursor = 0.0
        for i in order:
            a, b = points_ft[i], points_ft[i + 1]
            seg_len = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-9
            seg_hide_end = min(max(hide_end - cursor, 0.0), seg_len)
            seg_collar_end = min(max(collar_end - cursor, 0.0), seg_len)
            if tee["end"] == "start":
                vis_lo[i], vis_hi[i] = seg_hide_end, seg_len
                smooth_lo[i], smooth_hi[i] = seg_hide_end, seg_collar_end
            else:
                vis_lo[i], vis_hi[i] = 0.0, seg_len - seg_hide_end
                smooth_lo[i], smooth_hi[i] = seg_len - seg_collar_end, seg_len - seg_hide_end
            cursor += seg_len

    rings = []
    for i in range(n_seg):
        a, b = points_ft[i], points_ft[i + 1]
        left = _flex_rail_seg(a, b, half_w_ft, bump, FLEX_STEP_FT, 1,
                               vis_lo[i], vis_hi[i], smooth_lo[i], smooth_hi[i])
        right = _flex_rail_seg(a, b, half_w_ft, bump, FLEX_STEP_FT, -1,
                                vis_lo[i], vis_hi[i], smooth_lo[i], smooth_hi[i])
        if len(left) < 2:
            continue
        rings.append(_stitch_rails(left, right))
    return rings


def _longest_seg_midpoint(points_ft):
    """Midpoint of the run's longest segment — where review.html's drawDuctLabel places the size label."""
    best_i, best_len = 0, -1.0
    for i in range(len(points_ft) - 1):
        a, b = points_ft[i], points_ft[i + 1]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > best_len:
            best_len, best_i = length, i
    a, b = points_ft[best_i], points_ft[best_i + 1]
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def _add_text(msp, text, point, layer, height=LABEL_TEXT_H):
    t = msp.add_text(text, dxfattribs={"layer": layer, "height": height})
    t.set_placement(point, align=TextEntityAlignment.MIDDLE_CENTER)


def _add_terminal_icon(msp, point, kind, layer):
    """Port of review.html's drawTerminalIcon shapes, at a fixed real-world symbol size."""
    x, y = point
    sz = TERMINAL_SZ_FT
    if kind == "ceiling_supply":
        msp.add_lwpolyline([(x - sz, y - sz), (x + sz, y - sz), (x + sz, y + sz), (x - sz, y + sz)],
                            close=True, dxfattribs={"layer": layer})
        msp.add_line((x - sz, y - sz), (x + sz, y + sz), dxfattribs={"layer": layer})
        msp.add_line((x + sz, y - sz), (x - sz, y + sz), dxfattribs={"layer": layer})
    elif kind == "ceiling_return":
        msp.add_circle((x, y), sz, dxfattribs={"layer": layer})
    else:
        dir_ = 1 if kind == "sidewall_supply" else -1
        msp.add_line((x - sz * dir_, y), (x + sz * dir_, y), dxfattribs={"layer": layer})
        msp.add_line((x + sz * dir_, y), (x + sz * 0.4 * dir_, y - sz * 0.5), dxfattribs={"layer": layer})
        msp.add_line((x + sz * dir_, y), (x + sz * 0.4 * dir_, y + sz * 0.5), dxfattribs={"layer": layer})


def _add_linear_diffuser_icon(msp, p1_ft, p2_ft, layer):
    """Real to-scale linear slot diffuser: a rectangular frame the diffuser's actual drawn
    length/width, with evenly-spaced perpendicular blade ticks — a generic standard linear-
    diffuser CAD symbol (confirmed real Design Master output uses a to-scale multi-blade
    symbol here, not a fixed-size point icon; this doesn't clone that one product's specific
    skewed-blade styling, just the general rectangle+ticks convention)."""
    dx, dy = p2_ft[0] - p1_ft[0], p2_ft[1] - p1_ft[1]
    length_ft = math.hypot(dx, dy) or 1e-9
    nx, ny = -dy / length_ft, dx / length_ft
    half_w_ft = (LINEAR_DIFF_WIDTH_IN / 12) / 2

    left = [(p1_ft[0] + nx * half_w_ft, p1_ft[1] + ny * half_w_ft),
            (p2_ft[0] + nx * half_w_ft, p2_ft[1] + ny * half_w_ft)]
    right = [(p1_ft[0] - nx * half_w_ft, p1_ft[1] - ny * half_w_ft),
             (p2_ft[0] - nx * half_w_ft, p2_ft[1] - ny * half_w_ft)]
    msp.add_lwpolyline(_stitch_rails(left, right), close=True, dxfattribs={"layer": layer})

    length_in = length_ft * 12
    n_blades = max(3, round(length_in / LINEAR_DIFF_BLADE_SPACING_IN) + 1)
    for i in range(n_blades):
        t = i / (n_blades - 1)
        px, py = p1_ft[0] + dx * t, p1_ft[1] + dy * t
        msp.add_line((px + nx * half_w_ft, py + ny * half_w_ft),
                     (px - nx * half_w_ft, py - ny * half_w_ft), dxfattribs={"layer": layer})


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def export_ducts_dxf(review: dict, out_dir: pathlib.Path) -> list[pathlib.Path]:
    """Writes one DXF per floor with drawn duct content; returns the list of paths written."""
    px_per_ft_by_level = {f["level"]: f["pxPerFt"] for f in review.get("floors", []) if f.get("pxPerFt")}
    all_runs = review.get("duct_runs", [])
    all_terminals = review.get("duct_terminals", [])
    all_equipment = review.get("duct_equipment", [])

    levels = {r.get("level", 1) for r in all_runs} | {t.get("level", 1) for t in all_terminals} \
        | {e.get("level", 1) for e in all_equipment}

    stem = pathlib.Path(review["pdf_stem"]).name if review.get("pdf_stem") else "ducts"
    paths = []

    for level in sorted(levels):
        px_per_ft = px_per_ft_by_level.get(level)
        if not px_per_ft:
            continue
        runs = [r for r in all_runs if r.get("level", 1) == level]
        terminals = [t for t in all_terminals if t.get("level", 1) == level]
        equipment = [e for e in all_equipment if e.get("level", 1) == level]
        if not (runs or terminals or equipment):
            continue

        doc = ezdxf.new(dxfversion="R2010")
        doc.header["$INSUNITS"] = 2
        doc.header["$MEASUREMENT"] = 0  # imperial, so predefined hatch patterns scale correctly
        msp = doc.modelspace()

        layer_colors = {"MN-SUP-DUCT": 7, "MN-SUP-DUCT-FLEX": 7, "MN-SUP-DUCT-ANNO": 4,
                        "MN-RET-DUCT": 2, "MN-RET-DUCT-FLEX": 2, "MN-RET-DUCT-ANNO": 4,
                        "MN-SUP-DIFF": 7, "MN-SUP-DIFF-ANNO": 4, "MN-RET-DIFF": 2, "MN-RET-DIFF-ANNO": 4,
                        "MN-EQU": 7, "MN-SUP-CENTERLINE": 140, "MN-RET-CENTERLINE": 30,
                        "MN-SUP-DUCT-AROW": 1, "MN-RET-DUCT-AROW": 1}
        for name, color in layer_colors.items():
            layer = doc.layers.add(name, color=color)
            if name.endswith("-CENTERLINE"):
                layer.off()

        equ_square_blk = doc.blocks.new("MN-EQU-SQUARE")
        square_pts = [(-EQUIP_SZ_FT, -EQUIP_SZ_FT), (EQUIP_SZ_FT, -EQUIP_SZ_FT),
                      (EQUIP_SZ_FT, EQUIP_SZ_FT), (-EQUIP_SZ_FT, EQUIP_SZ_FT)]
        equ_square_blk.add_lwpolyline(square_pts, close=True)
        square_hatch = equ_square_blk.add_hatch()
        square_hatch.set_pattern_fill("ANSI37", scale=0.5)
        square_hatch.paths.add_polyline_path(square_pts, is_closed=True)

        equ_cu_blk = doc.blocks.new("MN-EQU-CU")
        equ_cu_blk.add_circle((0, 0), EQUIP_SZ_FT)
        cu_hatch = equ_cu_blk.add_hatch()
        cu_hatch.set_pattern_fill("AR-SAND", scale=0.5)
        cu_edge_path = cu_hatch.paths.add_edge_path()
        cu_edge_path.add_arc((0, 0), EQUIP_SZ_FT, 0, 360, ccw=True)

        arrow_blk = doc.blocks.new("MN-DUCT-AROW")
        arrow_blk.add_lwpolyline(
            [(ARROW_LEN_FT, 0), (0, ARROW_HALF_W_FT), (0, -ARROW_HALF_W_FT)], close=True)

        for run in runs:
            points_ft = [px_to_ft(p, px_per_ft) for p in run["points"]]
            form = effective_form(run, runs)
            round_shaped = is_round_shape(run, form)
            half_w = half_width_ft(run, "round" if round_shaped else form)
            is_supply = run["system"] == "supply"
            anno_layer = "MN-SUP-DUCT-ANNO" if is_supply else "MN-RET-DUCT-ANNO"
            cl_layer = "MN-SUP-CENTERLINE" if is_supply else "MN-RET-CENTERLINE"
            arow_layer = "MN-SUP-DUCT-AROW" if is_supply else "MN-RET-DUCT-AROW"
            if form == "flex":
                duct_layer = "MN-SUP-DUCT-FLEX" if is_supply else "MN-RET-DUCT-FLEX"
            else:
                duct_layer = "MN-SUP-DUCT" if is_supply else "MN-RET-DUCT"

            if form == "flex":
                rings = flex_outline(points_ft, half_w, run, runs)
                for ring in rings:
                    msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": duct_layer})
            elif round_shaped:
                bend_radius_ft = ELBOW_BEND_RADIUS_MULT * (half_w * 2)
                lines, arcs = round_outline_pieces(points_ft, half_w, bend_radius_ft)
                for p1, p2 in lines:
                    msp.add_line(p1, p2, dxfattribs={"layer": duct_layer})
                for center, radius, start_deg, end_deg in arcs:
                    msp.add_arc(center, radius, start_deg, end_deg, dxfattribs={"layer": duct_layer})
            else:
                ring = rigid_outline(points_ft, half_w)
                msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": duct_layer})
                for outer, inner in rect_break_lines(points_ft, half_w):
                    msp.add_line(outer, inner, dxfattribs={"layer": duct_layer})

            msp.add_lwpolyline(points_ft, close=False, dxfattribs={"layer": cl_layer})

            for a, b in zip(points_ft, points_ft[1:]):
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                msp.add_blockref("MN-DUCT-AROW", mid, dxfattribs={"layer": arow_layer, "rotation": angle})

            label = duct_size_label(run, form)
            if label:
                _add_text(msp, label, _longest_seg_midpoint(points_ft), anno_layer)

        for t in terminals:
            x, y = px_to_ft(t["point"], px_per_ft)
            is_supply = "supply" in t["kind"]
            layer = "MN-SUP-DIFF" if is_supply else "MN-RET-DIFF"
            anno_layer = "MN-SUP-DIFF-ANNO" if is_supply else "MN-RET-DIFF-ANNO"
            if t.get("points"):
                p1_ft = px_to_ft(t["points"][0], px_per_ft)
                p2_ft = px_to_ft(t["points"][1], px_per_ft)
                _add_linear_diffuser_icon(msp, p1_ft, p2_ft, layer)
            else:
                _add_terminal_icon(msp, (x, y), t["kind"], layer)
            label = " · ".join(s for s in (t.get("size"), f"{_fmt(t['cfm'])} cfm" if t.get("cfm") is not None else None) if s)
            if label:
                _add_text(msp, label, (x, y - TERMINAL_SZ_FT - 0.2), anno_layer)

        for eq in equipment:
            x, y = px_to_ft(eq["point"], px_per_ft)
            sz = EQUIP_SZ_FT
            block_name = "MN-EQU-CU" if eq["kind"] == "CU" else "MN-EQU-SQUARE"
            msp.add_blockref(block_name, (x, y), dxfattribs={"layer": "MN-EQU"})
            ly = y - sz - 0.25
            _add_text(msp, eq.get("label") or eq["kind"], (x, ly), "MN-EQU")
            if eq["kind"] == "AH":
                for label_prefix, value, unit in (("Supply", eq.get("supply_cfm"), "cfm"),
                                                   ("Return", eq.get("return_cfm"), "cfm"),
                                                   ("ESP", eq.get("static_pressure_in_wg"), "in.wg")):
                    if value is not None:
                        ly -= 0.3
                        _add_text(msp, f"{label_prefix}: {_fmt(value)} {unit}", (x, ly), "MN-EQU")

        out_path = out_dir / f"{stem}_L{level}_ducts.dxf"
        doc.saveas(out_path)
        paths.append(out_path)

    return paths
