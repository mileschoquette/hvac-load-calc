"""Duct auto-sizing — velocity method (SMACNA/ASHRAE simplified sizing; not a full
equal-friction chart, which needs duct-roughness/fitting data this tool doesn't have).

Two independent halves: CFM propagation through the drawn duct network (pure geometry,
once each terminal's own CFM is known) and CFM -> dimensions (plain velocity-method
arithmetic). Terminal CFM itself comes from a room's `sensible_btuh`, which only exists
after the RTS engine (compute_building) has run — that's why `size_duct_network` takes
`result` (compute_building's output) as an argument rather than computing it itself.
"""
from __future__ import annotations
import csv
import math
from pathlib import Path

DEFAULT_SUPPLY_DELTA_T = 20.0
DEFAULT_TRUNK_VELOCITY_FPM = 900.0
DEFAULT_BRANCH_VELOCITY_FPM = 700.0
DEFAULT_TRUNK_HEIGHT_IN = 10.0

# SMACNA standard round sizes (1" steps to 10", then 2" steps) and the rect width increment.
STANDARD_ROUND_SIZES_IN = [4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36]
STANDARD_RECT_STEP_IN = 2

# Same connectivity tolerance as export_review.py's findTeePoint — same coordinate space
# (raw plan px), same drawing precision.
TEE_TOL_PX = 1.5

EQUIPMENT_DATA_DIR = Path(__file__).resolve().parent.parent / "equipment_data"

# Numeric columns per CSV that csv.DictReader hands back as strings.
_NUMERIC_COLS = {
    "diffuser_schedule.csv": ("cfm_min", "cfm_max"),
    "air_handlers.csv": ("nominal_tons", "cooling_capacity_btuh", "rated_supply_cfm"),
    "condensing_units.csv": ("nominal_tons", "cooling_capacity_btuh"),
}

_csv_cache = {}


def _load_csv(name):
    """Loads `equipment_data/<name>` as a list of dicts, cast per `_NUMERIC_COLS`, cached
    at module level (tiny static reference files — no reason to re-read per call)."""
    if name in _csv_cache:
        return _csv_cache[name]
    numeric_cols = _NUMERIC_COLS.get(name, ())
    rows = []
    with open(EQUIPMENT_DATA_DIR / name, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for col in numeric_cols:
                if row.get(col):
                    row[col] = float(row[col])
                else:
                    row[col] = None
            rows.append(row)
    _csv_cache[name] = rows
    return rows


def _match_by_band(rows, value, min_key, max_key):
    """Smallest row whose [min_key, max_key] band contains `value`; clamps to the nearest
    extreme (flagged) if `value` falls outside every row's band; None if `rows` is empty."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r[min_key])
    for row in rows:
        if row[min_key] <= value <= row[max_key]:
            return row, None
    if value > rows[-1][max_key]:
        return rows[-1], "above_max"
    return rows[0], "below_min"


def _match_ceiling(rows, value, key):
    """Smallest row whose `key` (a single rated capacity, not a band) is >= `value` — the
    'round up to the smallest model that covers it' rule. Clamps to the largest available
    row (flagged `above_max`) if `value` exceeds every row's capacity; None if `rows` is
    empty. There's no `below_min` case here (unlike the diffuser band match): rounding up
    naturally returns the smallest covering model even when `value` is far below it, and
    that's a normal match, not a clamp."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r[key])
    for row in rows:
        if row[key] >= value:
            return row, None
    return rows[-1], "above_max"


def match_diffuser(terminal_kind, cfm):
    """Smallest diffuser/grille of `terminal_kind` whose CFM band covers `cfm`. Clamps to
    the largest/smallest available model (flagged) if `cfm` is outside every band. None if
    `terminal_kind` has no rows at all."""
    rows = [r for r in _load_csv("diffuser_schedule.csv") if r["terminal_kind"] == terminal_kind]
    matched = _match_by_band(rows, cfm, "cfm_min", "cfm_max")
    if matched is None:
        return None
    row, clamped = matched
    return {"manufacturer": row["manufacturer"], "model_line": row["model_line"],
            "size": row["neck_or_face_size"], "clamped": clamped}


def match_air_handler(cfm):
    """Smallest air handler whose `rated_supply_cfm` covers `cfm`, rounding up. Clamps to
    the largest available model (flagged `above_max`) if `cfm` exceeds every model's rating;
    None if the table's empty. Includes the matched row's own `rated_supply_cfm` so callers
    can prorate a theoretical CFM total against the real unit's rated airflow."""
    rows = [r for r in _load_csv("air_handlers.csv") if r["rated_supply_cfm"] is not None]
    matched = _match_ceiling(rows, cfm, "rated_supply_cfm")
    if matched is None:
        return None
    row, clamped = matched
    return {"manufacturer": row["manufacturer"], "model": row["model"],
            "rated_supply_cfm": row["rated_supply_cfm"], "clamped": clamped}


def match_condensing_unit(tons):
    """Smallest condensing unit whose `nominal_tons` covers `tons`, rounding up. Clamps to
    the largest available model (flagged `above_max`) if `tons` exceeds every model's
    rating; None if the table's empty."""
    rows = _load_csv("condensing_units.csv")
    matched = _match_ceiling(rows, tons, "nominal_tons")
    if matched is None:
        return None
    row, clamped = matched
    return {"manufacturer": row["manufacturer"], "model": row["model"], "clamped": clamped}


def required_supply_cfm(sensible_btuh, delta_t=DEFAULT_SUPPLY_DELTA_T):
    """Supply air CFM needed to offset a sensible load at a given supply-air ΔT."""
    if not sensible_btuh or not delta_t:
        return 0.0
    return sensible_btuh / (1.1 * delta_t)


def round_duct_diameter(cfm, velocity_fpm):
    """Nearest standard round duct diameter (in), rounded UP, carrying `cfm` at `velocity_fpm`."""
    if not cfm or not velocity_fpm:
        return None
    area_sqft = cfm / velocity_fpm
    dia = math.sqrt(4 * area_sqft / math.pi) * 12
    for size in STANDARD_ROUND_SIZES_IN:
        if size >= dia:
            return size
    return STANDARD_ROUND_SIZES_IN[-1]


def rect_duct_width(cfm, velocity_fpm, height_in):
    """Width (in), rounded UP to the nearest 2", for a fixed-height rect duct carrying
    `cfm` at `velocity_fpm` — height is a design input here, not solved for."""
    if not cfm or not velocity_fpm or not height_in:
        return None
    area_sqft = cfm / velocity_fpm
    width = area_sqft * 144.0 / height_in
    steps = math.ceil(width / STANDARD_RECT_STEP_IN)
    return max(STANDARD_RECT_STEP_IN, steps * STANDARD_RECT_STEP_IN)


def _round_up_5(cfm):
    """Rounds a CFM value up to the nearest 5 (the increment diffuser/grille and air-handler
    airflow are conventionally specified in) — None in, None out."""
    if cfm is None:
        return None
    return math.ceil(cfm / 5.0) * 5.0


def _distribute_exact(values, target):
    """Rounds each of `values` (id -> ideal continuous cfm) to a multiple of 5 so they sum
    to EXACTLY `target` (itself rounded up to a multiple of 5) — largest-remainder method:
    scale every value to the target, floor each to the nearest 5, then hand out the
    leftover 5-cfm units to whichever ids were closest to rounding up. Ensures an air
    handler's diffusers always add up to its stated capacity instead of drifting off by
    whatever each terminal's independent round-up happened to add."""
    ids = list(values.keys())
    if not ids:
        return {}
    target = _round_up_5(target) if target else 0.0
    total = sum(values.values())
    scaled = {i: values[i] * (target / total) for i in ids} if total > 0 else {i: target / len(ids) for i in ids}
    floors = {i: math.floor(scaled[i] / 5.0) * 5.0 for i in ids}
    remainder_units = int(round((target - sum(floors.values())) / 5.0))
    order = sorted(ids, key=lambda i: scaled[i] - floors[i], reverse=True)
    result = dict(floors)
    for i in order[:max(0, remainder_units)]:
        result[i] += 5.0
    return result


def _closest_on_seg(p, a, b):
    """Closest point on segment a-b to p, and the distance to it."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    l2 = (dx * dx + dy * dy) or 1e-9
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    qx, qy = ax + t * dx, ay + t * dy
    return (qx, qy), math.hypot(px - qx, py - qy)


def _point_touches_run(pt, run_points):
    """True if `pt` lands anywhere along `run_points` (endpoint or mid-span) within tolerance."""
    for i in range(len(run_points) - 1):
        _, d = _closest_on_seg(pt, run_points[i], run_points[i + 1])
        if d <= TEE_TOL_PX:
            return True
    return False


def _runs_connect(run_a, run_b):
    """General graph connectivity (for CFM propagation) — true if the two runs touch
    ANYWHERE (shared/near endpoint, or one's endpoint landing mid-span on the other).
    Unlike `find_tee_point`, this doesn't care whether it's a mid-span tee or just an
    end-to-end continuation — both mean CFM flows between them."""
    a_pts, b_pts = run_a["points"], run_b["points"]
    for ep in (a_pts[0], a_pts[-1]):
        if _point_touches_run(ep, b_pts):
            return True
    for ep in (b_pts[0], b_pts[-1]):
        if _point_touches_run(ep, a_pts):
            return True
    return False


def find_tee_point(run, all_runs):
    """Python port of export_review.py's findTeePoint — does either endpoint of `run`
    land on the MIDDLE of some other run's segment (a real branch takeoff), as opposed to
    just meeting another run at ITS endpoint (that's trunk continuation, still rect)."""
    def tee_on(pt):
        best = None
        for other in all_runs:
            if other is run:
                continue
            for i in range(len(other["points"]) - 1):
                a, b = other["points"][i], other["points"][i + 1]
                q, d = _closest_on_seg(pt, a, b)
                if d > TEE_TOL_PX:
                    continue
                if (math.hypot(q[0] - a[0], q[1] - a[1]) < TEE_TOL_PX
                        or math.hypot(q[0] - b[0], q[1] - b[1]) < TEE_TOL_PX):
                    continue   # meets an endpoint, not a mid-span tee
                if best is None or d < best[1]:
                    best = (q, d, other)
        return best

    pts = run["points"]
    if len(pts) < 2:
        return None
    s = tee_on(pts[0])
    if s:
        return {"end": "start", "point": s[0], "on_run": s[2]}
    e = tee_on(pts[-1])
    if e:
        return {"end": "end", "point": e[0], "on_run": e[2]}
    return None


def effective_form(run, all_runs):
    """rect unless it tees off another run's middle — same rule as export_review.py's
    effectiveForm; a manual `form` override (anything but 'auto'/absent) always wins."""
    form = run.get("form")
    if form and form != "auto":
        return form
    return "flex" if find_tee_point(run, all_runs) else "rect"


def _point_in_poly(p, poly):
    """Ray-casting point-in-polygon — same algorithm as export_review.py's pip()."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[i - 1]
        if (yi > y) != (yj > y):
            x_int = xi + (xj - xi) * (y - yi) / ((yj - yi) or 1e-9)
            if x < x_int:
                inside = not inside
    return inside


def _run_key(run):
    return (run.get("level", 1), run.get("system"))


def _collect_subtree_runs(root_runs, children):
    """Every run in the subtree(s) rooted at `root_runs` (roots included), walking the
    already-built BFS `children` map (id(parent_run) -> [child_run, ...]). Since each
    non-root run has exactly one parent in that forest, starting from one AH's own root
    runs correctly isolates just that AH's subtree, even though `children` may have been
    built from a BFS that started at several different AHs' roots at once."""
    runs = list(root_runs)
    frontier = list(root_runs)
    while frontier:
        frontier = [c for r in frontier for c in children.get(id(r), [])]
        runs.extend(frontier)
    return runs


def size_duct_network(review, result, design=None):
    """Auto-sizes every duct run/terminal that isn't flagged `auto: False` (a deliberate
    manual override). Returns only the changed fields, keyed by id, so the caller can
    merge them into the live drawing without touching anything else:
        {"duct_runs": {id: {...}}, "duct_terminals": {id: {...}}, "duct_equipment": {id: {...}}}
    """
    design = design or {}
    delta_t = design.get("supply_delta_t") or DEFAULT_SUPPLY_DELTA_T
    trunk_v = design.get("trunk_velocity_fpm") or DEFAULT_TRUNK_VELOCITY_FPM
    branch_v = design.get("branch_velocity_fpm") or DEFAULT_BRANCH_VELOCITY_FPM
    trunk_h = design.get("trunk_height_in") or DEFAULT_TRUNK_HEIGHT_IN

    all_runs = review.get("duct_runs", [])
    all_terminals = review.get("duct_terminals", [])
    all_equipment = review.get("duct_equipment", [])
    rooms = review.get("rooms", [])
    sensible_by_room = {r["id"]: r.get("sensible_btuh", 0.0) for r in result.get("rooms", [])}

    # 1. Terminal CFM: containing room's required supply CFM, split across same-kind
    # (supply vs return) terminals in that room. A terminal outside every room polygon is
    # left alone (flagged by simply never appearing in `terminal_cfm`), not guessed at.
    room_terminal_groups = {}
    for t in all_terminals:
        room = next((r for r in rooms if _point_in_poly(t["point"], r["points"])), None)
        if room is None:
            continue
        is_supply = "supply" in t["kind"]
        room_terminal_groups.setdefault((room["id"], is_supply), []).append(t)

    terminal_cfm = {}
    for (room_id, _is_supply), group in room_terminal_groups.items():
        room_cfm = required_supply_cfm(sensible_by_room.get(room_id, 0.0), delta_t)
        share = room_cfm / len(group) if group else 0.0
        for t in group:
            terminal_cfm[t["id"]] = share

    # 2. Network CFM propagation: partition by (level, system) so floor 1 never connects
    # to floor 2 and a supply terminal never attaches to a return run, then BFS from
    # whichever run(s) touch an Air Handler (the only equipment kind treated as a network
    # root) and sum each terminal's CFM upward through the tree. A partition with no AH in
    # it is skipped entirely — without a root there's no way to tell which direction is
    # "downstream", so any existing manual `cfm` on those runs is left untouched.
    #
    # Supply groups (across every level/floor) are fully resolved — BFS sum, then AH-match
    # proration + round-up-5 — before any return group runs. That's what lets step 2c below
    # derive each room's return CFM from its now-FINAL (post-proration) supply total instead
    # of an independent sensible_btuh calc, so a room's return always equals its supply.
    run_cfm = {}
    equipment_cfm = {}
    by_key = {}
    for r in all_runs:
        by_key.setdefault(_run_key(r), []).append(r)

    def _process_group(level, system, group):
        roots = [eq for eq in all_equipment if eq.get("level", 1) == level and eq.get("kind") == "AH"]
        if not roots:
            return
        root_runs = [r for r in group if any(_point_touches_run(eq["point"], r["points"]) for eq in roots)]
        if not root_runs:
            return

        touching_terminals = {}
        for t in all_terminals:
            if t.get("level", 1) != level or ("supply" in t["kind"]) != (system == "supply"):
                continue
            for r in group:
                if _point_touches_run(t["point"], r["points"]):
                    touching_terminals.setdefault(id(r), []).append(t)

        adj = {id(r): [] for r in group}
        for i, ra in enumerate(group):
            for rb in group[i + 1:]:
                if _runs_connect(ra, rb):
                    adj[id(ra)].append(rb)
                    adj[id(rb)].append(ra)

        # BFS from the root run(s) gives each run a single parent and a set of children —
        # every child is strictly later in BFS order than its parent, so walking `order`
        # in reverse guarantees a run's children are all resolved before the run itself.
        parent = {id(r): None for r in root_runs}
        order = list(root_runs)
        frontier = list(root_runs)
        while frontier:
            nxt = []
            for r in frontier:
                for nb in adj[id(r)]:
                    if id(nb) not in parent:
                        parent[id(nb)] = r
                        order.append(nb)
                        nxt.append(nb)
            frontier = nxt

        children = {}
        for r in order:
            p = parent[id(r)]
            if p is not None:
                children.setdefault(id(p), []).append(r)

        subtree_cfm = {}
        for r in reversed(order):
            total = sum(terminal_cfm.get(t["id"], 0.0) for t in touching_terminals.get(id(r), []))
            for c in children.get(id(r), []):
                total += subtree_cfm[id(c)]
            subtree_cfm[id(r)] = total
            run_cfm[r["id"]] = total

        # 2b. Proration (supply only): the BFS pass above gives each terminal/run a
        # THEORETICAL cfm (pure room-load arithmetic). Real equipment only comes in
        # discrete rated sizes, so once an AH's supply subtree is matched to a real unit —
        # or the engineer types in their own `capacity_cfm` override on the equipment —
        # every terminal downstream of it is rescaled to that target and split across the
        # terminals with `_distribute_exact` (largest-remainder rounding), so they always
        # add up to EXACTLY the air handler's stated capacity rather than drifting off by
        # whatever each terminal's independent round-up happened to add. Return terminals
        # need no rescaling here — step 2c below already made each room's return total
        # exactly match that room's own finalized supply total, so this just totals them up
        # for the AH's own label. `subtree_cfm`/`run_cfm` are then re-summed for just this
        # subtree so duct sizing (step 3) picks up these final numbers instead of the
        # pre-prorate ones.
        for eq in roots:
            eq_root_runs = [r for r in group if _point_touches_run(eq["point"], r["points"])]
            if not eq_root_runs:
                continue   # some OTHER AH in this level roots this group; this one has nothing here
            subtree_runs = _collect_subtree_runs(eq_root_runs, children)
            seen_ids = set()
            my_terminals = []
            for r in subtree_runs:
                for t in touching_terminals.get(id(r), []):
                    if t["id"] not in seen_ids:
                        seen_ids.add(t["id"])
                        my_terminals.append(t)

            if system == "supply":
                theoretical_total = sum(terminal_cfm.get(t["id"], 0.0) for t in my_terminals)
                override = eq.get("capacity_cfm")
                if override:
                    target = float(override)
                else:
                    match = match_air_handler(theoretical_total) if theoretical_total > 0 else None
                    target = match["rated_supply_cfm"] if match else theoretical_total
                target = _round_up_5(target) if target else 0.0
                values = {t["id"]: terminal_cfm.get(t["id"], 0.0) for t in my_terminals}
                dist = _distribute_exact(values, target)
                for t in my_terminals:
                    terminal_cfm[t["id"]] = dist[t["id"]]
                equipment_cfm.setdefault(eq["id"], {})["supply"] = target
            else:
                equipment_cfm.setdefault(eq["id"], {})["return"] = sum(
                    terminal_cfm.get(t["id"], 0.0) for t in my_terminals)

            subtree_ids = {id(r) for r in subtree_runs}
            for r in reversed([o for o in order if id(o) in subtree_ids]):
                total = sum(terminal_cfm.get(t["id"], 0.0) for t in touching_terminals.get(id(r), []))
                for c in children.get(id(r), []):
                    if id(c) in subtree_ids:
                        total += subtree_cfm[id(c)]
                subtree_cfm[id(r)] = total
                run_cfm[r["id"]] = total

    for (level, system), group in by_key.items():
        if system == "supply":
            _process_group(level, system, group)

    # 2c. Return terminal CFM = that room's now-finalized (post-proration, post-round-up-5)
    # supply CFM total, distributed EXACTLY (via `_distribute_exact`, not an independent
    # round per terminal) across that room's return terminals — so a room's return total
    # always matches its supply total to the cfm, even after the AH-match proration above
    # rescaled supply away from its theoretical value, and even when a room has several
    # return terminals whose individual round-ups would otherwise drift the room's total off
    # by a few cfm (which is what let an AH's aggregate return total drift off its aggregate
    # supply total in the first place). Not an independent `required_supply_cfm` calc. A
    # room with no supply terminals gets 0 on its return terminals.
    room_supply_totals = {}
    for (room_id, is_supply), group in room_terminal_groups.items():
        if is_supply:
            room_supply_totals[room_id] = sum(terminal_cfm.get(t["id"], 0.0) for t in group)
    for (room_id, is_supply), group in room_terminal_groups.items():
        if is_supply:
            continue
        dist = _distribute_exact({t["id"]: 1.0 for t in group}, room_supply_totals.get(room_id, 0.0))
        for t in group:
            terminal_cfm[t["id"]] = dist[t["id"]]

    for (level, system), group in by_key.items():
        if system == "return":
            _process_group(level, system, group)

    # 3. Sizing: whatever CFM each run ended up with (skip anything a manual override
    # kept out of `run_cfm`, or that the engineer flagged `auto: False`).
    changed_runs = {}
    for r in all_runs:
        if not r.get("auto", True) or r["id"] not in run_cfm:
            continue
        cfm = run_cfm[r["id"]]
        form = effective_form(r, all_runs)
        if form == "rect":
            changed_runs[r["id"]] = {"cfm": round(cfm, 1), "width_in": rect_duct_width(cfm, trunk_v, trunk_h),
                                      "height_in": trunk_h, "diameter_in": None}
        else:
            velocity = trunk_v if form == "round" else branch_v
            changed_runs[r["id"]] = {"cfm": round(cfm, 1), "diameter_in": round_duct_diameter(cfm, velocity),
                                      "width_in": None, "height_in": None}

    changed_terminals = {}
    for t in all_terminals:
        if not (t.get("auto", True) and t["id"] in terminal_cfm):
            continue
        cfm = terminal_cfm[t["id"]]
        match = match_diffuser(t["kind"], cfm)
        changed_terminals[t["id"]] = {"cfm": round(cfm, 1), "size": match["size"] if match else None}

    changed_equipment = {eq_id: {"supply_cfm": round(v["supply"], 1) if "supply" in v else None,
                                  "return_cfm": round(v["return"], 1) if "return" in v else None}
                         for eq_id, v in equipment_cfm.items()}

    return {"duct_runs": changed_runs, "duct_terminals": changed_terminals, "duct_equipment": changed_equipment}


_TERMINAL_TAG_PREFIX = {"ceiling_supply": "CD", "ceiling_return": "CR",
                        "sidewall_supply": "SD", "sidewall_return": "SR"}


def duct_report_data(review, result, design=None):
    """Report-shaped duct summary for engine/report.py — two independent pieces:

    `supply_air`: each room's required CFM straight from its `sensible_btuh`, regardless
    of whether any ductwork has been drawn yet (this is what report.py's docstring used to
    flag as a gap; it needs nothing but the RTS result, so it always renders).

    `schedule`/`terminals`/`equipment`: whatever IS drawn, sized fresh via
    `size_duct_network` for every `auto` item (same "don't clobber a deliberate override"
    rule that function already applies) — so the report always reflects current CFM/sizes
    without the engineer having to remember to click Auto-Size in the browser first, the
    same way the cooling/heating tables are always freshly computed rather than cached.

    Terminal/equipment rows also carry a real-catalog match (`matched_*` fields) against
    `equipment_data/`, via `match_diffuser`/`match_air_handler`/`match_condensing_unit` —
    same "don't clobber a manual override" rule (`auto`), and DH equipment is deliberately
    skipped (no clean required-capacity number for it yet).
    """
    design = design or {}
    delta_t = design.get("supply_delta_t") or DEFAULT_SUPPLY_DELTA_T

    supply_air = [{"room": r["label"], "area_sqft": r["area_sqft"], "sensible_btuh": r["sensible_btuh"],
                   "delta_t": delta_t, "cfm": round(required_supply_cfm(r["sensible_btuh"], delta_t), 0)}
                  for r in result.get("rooms", [])]

    all_runs = review.get("duct_runs", [])
    all_terminals = review.get("duct_terminals", [])
    all_equipment = review.get("duct_equipment", [])
    sized = size_duct_network(review, result, design)

    def _merge(items, changed):
        return [{**it, **changed.get(it["id"], {})} for it in items]

    runs = _merge(all_runs, sized["duct_runs"])
    terminals = _merge(all_terminals, sized["duct_terminals"])
    equipment = _merge(all_equipment, sized["duct_equipment"])

    zones_by_name = {z["name"]: z for z in result.get("zones", [])}

    schedule = []
    for r in runs:
        form = effective_form(r, all_runs)
        if form == "rect" and r.get("width_in") and r.get("height_in"):
            size_label = f"{r['width_in']:g}x{r['height_in']:g}\""
        elif r.get("diameter_in"):
            size_label = f"{r['diameter_in']:g}\"Ø"
        else:
            size_label = "—"
        schedule.append({"kind": f"{form.capitalize()} Duct — {(r.get('system') or '').capitalize()}",
                          "size": size_label, "cfm": r.get("cfm"), "zone": r.get("zone") or "—"})

    tag_counts = {}
    terminal_rows = []
    for t in terminals:
        prefix = _TERMINAL_TAG_PREFIX.get(t["kind"], "T")
        tag_counts[prefix] = tag_counts.get(prefix, 0) + 1
        row = {"tag": f"{prefix}-{tag_counts[prefix]}", "kind": t["kind"].replace("_", " ").title(),
               "size": t.get("size") or "—", "cfm": t.get("cfm"), "zone": t.get("zone") or "—",
               "matched_manufacturer": None, "matched_model_line": None,
               "matched_size": None, "matched_clamped": None}
        if t.get("cfm") is not None and t.get("auto", True):
            match = match_diffuser(t["kind"], t["cfm"])
            if match:
                row["matched_manufacturer"] = match["manufacturer"]
                row["matched_model_line"] = match["model_line"]
                row["matched_size"] = match["size"]
                row["matched_clamped"] = match["clamped"]
        terminal_rows.append(row)

    equipment_rows = []
    for eq in equipment:
        row = {"tag": eq.get("label") or eq["kind"], "kind": eq["kind"],
               "supply_cfm": eq.get("supply_cfm"), "return_cfm": eq.get("return_cfm"),
               "matched_manufacturer": None, "matched_model": None, "matched_clamped": None}
        if eq["kind"] == "AH" and eq.get("supply_cfm") is not None:
            match = match_air_handler(eq["supply_cfm"])
        elif eq["kind"] == "CU":
            zone = zones_by_name.get(eq.get("zone"))
            tons = zone["tons"] if zone else result.get("building", {}).get("tons")
            match = match_condensing_unit(tons) if tons is not None else None
        else:
            match = None
        if match:
            row["matched_manufacturer"] = match["manufacturer"]
            row["matched_model"] = match["model"]
            row["matched_clamped"] = match["clamped"]
        equipment_rows.append(row)

    return {"supply_air": supply_air, "schedule": schedule, "terminals": terminal_rows, "equipment": equipment_rows}
