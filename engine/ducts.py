"""Duct auto-sizing — equal-friction method (ASHRAE Fundamentals Ch.21/32 Huebscher
curve-fit + ASHRAE Duct Fitting Database; see equipment_data/duct_friction_reference.md
for the full derivation/citations of every constant used below).

Three independent halves: CFM propagation through the drawn duct network (pure geometry,
once each terminal's own CFM is known), CFM -> dimensions (every segment sized to hit ONE
system-wide target friction rate, in.wg/100ft, rather than a fixed velocity), and pressure
drop (straight friction + fitting losses along each air handler's worst-case root-to-leaf
path, once every run's final size is known). Terminal CFM itself comes from a room's
`sensible_btuh`, which only exists after the RTS engine (compute_building) has run —
that's why `size_duct_network` takes `result` (compute_building's output) as an argument
rather than computing it itself.
"""
from __future__ import annotations
import csv
import math
from pathlib import Path

DEFAULT_SUPPLY_DELTA_T = 20.0
DEFAULT_FRICTION_RATE_IN_WG_PER_100FT = 0.10
DEFAULT_TRUNK_HEIGHT_IN = 10.0

# SMACNA standard round sizes (1" steps to 10", then 2" steps) and the rect width increment.
STANDARD_ROUND_SIZES_IN = [4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36]
STANDARD_RECT_STEP_IN = 2

# Same connectivity tolerance as export_review.py's findTeePoint — same coordinate space
# (raw plan px), same drawing precision.
TEE_TOL_PX = 1.5

# Fitting-loss coefficients (DP = C * VP, VP referenced per the notes below) — see
# equipment_data/duct_friction_reference.md section 3 for the ASHRAE Duct Fitting Database
# citations behind each value.
ELBOW_C_ROUND = 0.22    # die-stamped smooth-radius, r/D=1.0; referenced to its own VP
ELBOW_C_RECT = 0.15     # mitered w/ turning vanes; referenced to its own VP
TRANSITION_C = 0.20     # straight reducer/transition; referenced to the upstream (larger) side's VP
AH_ENTRANCE_C = 0.50    # sharp-edged duct entrance at an AH discharge collar; referenced to its own VP
# Branch tee C, keyed by V_branch/V_main, interpolated linearly between the nearest two keys
# (clamped to the table's ends outside [0.2, 1.6]) — referenced to the BRANCH's own VP.
BRANCH_TEE_C_TABLE = {0.2: 0.85, 0.4: 0.74, 0.6: 0.62, 0.8: 0.52,
                      1.0: 0.42, 1.2: 0.36, 1.4: 0.32, 1.6: 0.32}
# A run's own drawn geometry counts as an elbow only where an interior vertex bends by more
# than this many degrees — a deliberate simplification for inferring real sheet-metal
# fittings from 2D drawn centerlines (see the pressure-drop model note in size_duct_network).
ELBOW_ANGLE_THRESHOLD_DEG = 30.0

EQUIPMENT_DATA_DIR = Path(__file__).resolve().parent.parent / "equipment_data"

# Numeric columns per CSV that csv.DictReader hands back as strings.
_NUMERIC_COLS = {
    "diffuser_schedule.csv": ("cfm_min", "cfm_max"),
    "air_handlers.csv": ("nominal_tons", "cooling_capacity_btuh", "rated_supply_cfm", "rated_esp_in_wg"),
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
            "rated_supply_cfm": row["rated_supply_cfm"], "rated_esp_in_wg": row.get("rated_esp_in_wg"),
            "clamped": clamped}


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


def friction_rate(cfm, diameter_in):
    """Forward Huebscher equal-friction equation: the friction rate (in.wg/100ft) a round
    duct of `diameter_in` delivers carrying `cfm`. Use this AFTER a duct's ideal diameter
    has been rounded up to a standard size, to report the actual (always <= target) rate
    that size delivers."""
    if not cfm or not diameter_in:
        return None
    return 0.109136 * cfm ** 1.9 / diameter_in ** 5.02


def diameter_for_friction(cfm, friction_rate_in_wg_per_100ft):
    """Algebraic inverse of `friction_rate`: the ideal (unrounded) round duct diameter (in)
    needed to hit a target friction rate at this cfm — the equal-friction design direction."""
    if not cfm or not friction_rate_in_wg_per_100ft:
        return None
    return (0.109136 * cfm ** 1.9 / friction_rate_in_wg_per_100ft) ** (1 / 5.02)


def rect_equivalent_diameter(width_in, height_in):
    """ASHRAE Fundamentals Ch.32 Eq.(25): the round diameter (in) with the same friction
    loss, at the same cfm and length, as a `width_in` x `height_in` rectangular duct."""
    return 1.30 * (width_in * height_in) ** 0.625 / (width_in + height_in) ** 0.25


def _solve_rect_width_for_de(target_de, height_in, tol=1e-4, max_iter=100):
    """Bisects for the rectangular width `a` (in) such that `rect_equivalent_diameter(a,
    height_in) == target_de` — there's no closed form for `a`, but `rect_equivalent_diameter`
    is monotonically increasing in `a` for a fixed height, so bisection is safe."""
    lo, hi = 0.1, 200.0
    while rect_equivalent_diameter(hi, height_in) < target_de and hi < 1e5:
        hi *= 2
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if rect_equivalent_diameter(mid, height_in) < target_de:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def _round_area_sqft(diameter_in):
    return math.pi / 4 * (diameter_in / 12) ** 2


def _rect_area_sqft(width_in, height_in):
    return (width_in / 12) * (height_in / 12)


def _velocity(cfm, area_sqft):
    if not cfm or not area_sqft:
        return None
    return cfm / area_sqft


def _velocity_pressure(v_fpm):
    """VP (in.wg) = (V/4005)^2 — ASHRAE Fundamentals Eq.(9) for standard air."""
    if not v_fpm:
        return 0.0
    return (v_fpm / 4005) ** 2


def _branch_tee_c(velocity_ratio):
    """Interpolates BRANCH_TEE_C_TABLE by V_branch/V_main, clamped to the table's ends."""
    keys = sorted(BRANCH_TEE_C_TABLE)
    if velocity_ratio <= keys[0]:
        return BRANCH_TEE_C_TABLE[keys[0]]
    if velocity_ratio >= keys[-1]:
        return BRANCH_TEE_C_TABLE[keys[-1]]
    for k0, k1 in zip(keys, keys[1:]):
        if k0 <= velocity_ratio <= k1:
            c0, c1 = BRANCH_TEE_C_TABLE[k0], BRANCH_TEE_C_TABLE[k1]
            return c0 + (velocity_ratio - k0) / (k1 - k0) * (c1 - c0)


def round_duct_diameter(cfm, friction_rate_in_wg_per_100ft):
    """Nearest standard round duct diameter (in), rounded UP, sized to hit a target friction
    rate at this cfm (equal-friction method)."""
    ideal = diameter_for_friction(cfm, friction_rate_in_wg_per_100ft)
    if ideal is None:
        return None
    for size in STANDARD_ROUND_SIZES_IN:
        if size >= ideal:
            return size
    return STANDARD_ROUND_SIZES_IN[-1]


def rect_duct_width(cfm, friction_rate_in_wg_per_100ft, height_in):
    """Width (in), rounded UP to the nearest 2", for a fixed-height rect duct sized to hit a
    target friction rate at this cfm (equal-friction method) — height is a design input
    here, not solved for; width is found by bisecting the equivalent-diameter equation
    since there's no closed form for it."""
    if not cfm or not friction_rate_in_wg_per_100ft or not height_in:
        return None
    target_de = diameter_for_friction(cfm, friction_rate_in_wg_per_100ft)
    width = _solve_rect_width_for_de(target_de, height_in)
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
    friction_target = design.get("friction_rate_in_wg_per_100ft") or DEFAULT_FRICTION_RATE_IN_WG_PER_100FT
    trunk_h = design.get("trunk_height_in") or DEFAULT_TRUNK_HEIGHT_IN

    all_runs = review.get("duct_runs", [])
    all_terminals = review.get("duct_terminals", [])
    all_equipment = review.get("duct_equipment", [])
    rooms = review.get("rooms", [])
    sensible_by_room = {r["id"]: r.get("sensible_btuh", 0.0) for r in result.get("rooms", [])}

    # Real drawn length (ft) per run, for straight-friction loss — segment lengths in the
    # plan's raw px, converted via that run's OWN floor's pxPerFt (matched by `level`). A
    # run whose level has no matching floor entry is left out of `run_length_ft` entirely;
    # its pressure-drop calc is skipped later (no way to know its real length, don't guess).
    px_per_ft_by_level = {f.get("level", 1): f["pxPerFt"] for f in review.get("floors", []) if f.get("pxPerFt")}

    def _run_length_ft(run):
        px_per_ft = px_per_ft_by_level.get(run.get("level", 1))
        if not px_per_ft:
            return None
        pts = run["points"]
        length_px = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                         for i in range(len(pts) - 1))
        return length_px / px_per_ft

    run_length_ft = {r["id"]: _run_length_ft(r) for r in all_runs}

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
    # run id -> its BFS parent run's id (None for a root run touching an AH directly) and the
    # set of root run ids, both needed later to classify each run's own upstream connection
    # fitting (AH entrance / tee / transition / none). ah_subtrees: eq id -> {"supply"/"return":
    # {"root_runs": [...], "children": {...}}} — each AH's own root runs + the (per-group) BFS
    # children map, so the static-pressure pass can walk that AH's own subtree independently
    # of whatever other AH shared the same (level, system) partition.
    run_parent_id = {}
    run_is_root = set()
    ah_subtrees = {}
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

        for r in order:
            run_parent_id[r["id"]] = parent[id(r)]["id"] if parent[id(r)] is not None else None
        run_is_root.update(r["id"] for r in root_runs)

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
            ah_subtrees.setdefault(eq["id"], {})[system] = {"root_runs": eq_root_runs, "children": children}
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
    # kept out of `run_cfm`, or that the engineer flagged `auto: False`). Every run — rect,
    # round, or flex — sizes to hit the SAME system-wide target friction rate (equal-friction
    # method), not a fixed velocity. `friction_rate_actual`/`velocity_fpm` are the as-built
    # results after rounding up to a standard size, reported for the engineer to sanity-check,
    # not sizing inputs.
    changed_runs = {}
    for r in all_runs:
        if not r.get("auto", True) or r["id"] not in run_cfm:
            continue
        cfm = run_cfm[r["id"]]
        form = effective_form(r, all_runs)
        if form == "rect":
            width = rect_duct_width(cfm, friction_target, trunk_h)
            final_de = rect_equivalent_diameter(width, trunk_h) if width else None
            area = _rect_area_sqft(width, trunk_h) if width else None
            changed_runs[r["id"]] = {"cfm": round(cfm, 1), "width_in": width, "height_in": trunk_h,
                                      "diameter_in": None}
        else:
            diameter = round_duct_diameter(cfm, friction_target)
            final_de = diameter
            area = _round_area_sqft(diameter) if diameter else None
            changed_runs[r["id"]] = {"cfm": round(cfm, 1), "diameter_in": diameter,
                                      "width_in": None, "height_in": None}
        v = _velocity(cfm, area)
        fr_actual = friction_rate(cfm, final_de)
        changed_runs[r["id"]]["velocity_fpm"] = round(v, 1) if v is not None else None
        changed_runs[r["id"]]["friction_rate_actual"] = round(fr_actual, 4) if fr_actual is not None else None

    # 4. Pressure drop: each run's own straight friction loss (its final rounded size's actual
    # friction rate x its real drawn length) plus exactly ONE "connection" fitting loss at its
    # own upstream end — AH entrance if it's a root run, else a branch tee if `find_tee_point`
    # finds one, else a transition/reducer if its final size differs from its parent's, else
    # nothing for a plain same-size continuation — plus one elbow loss for every interior
    # vertex in its OWN drawn points where the direction changes by more than
    # ELBOW_ANGLE_THRESHOLD_DEG (30 degrees). This "one connection fitting + N interior
    # elbows, inferred from 2D drawn centerlines" model is a deliberate simplification: a plan
    # view can't show every physical coupling a duct shop actually installs, so it's the best
    # approximation available without the engineer tagging each fitting by hand.
    for r in all_runs:
        entry = changed_runs.get(r["id"])
        if entry is None:
            continue
        length_ft = run_length_ft.get(r["id"])
        if length_ft is None:
            continue   # this run's level has no matching floor entry — no way to know, don't guess
        own_v = entry["velocity_fpm"]
        own_vp = _velocity_pressure(own_v)
        straight_loss = (entry["friction_rate_actual"] or 0.0) / 100.0 * length_ft

        if r["id"] in run_is_root:
            fitting_loss = AH_ENTRANCE_C * own_vp
        else:
            tee = find_tee_point(r, all_runs)
            if tee:
                trunk_entry = changed_runs.get(tee["on_run"]["id"])
                trunk_v = trunk_entry["velocity_fpm"] if trunk_entry else None
                fitting_loss = _branch_tee_c(own_v / trunk_v) * own_vp if (trunk_v and own_v) else 0.0
            else:
                parent_id = run_parent_id.get(r["id"])
                parent_entry = changed_runs.get(parent_id) if parent_id is not None else None
                own_size = (entry["width_in"], entry["height_in"], entry["diameter_in"])
                parent_size = ((parent_entry["width_in"], parent_entry["height_in"], parent_entry["diameter_in"])
                                if parent_entry else None)
                if parent_entry and parent_size != own_size:
                    fitting_loss = TRANSITION_C * _velocity_pressure(parent_entry["velocity_fpm"])
                else:
                    fitting_loss = 0.0   # same size as parent (or parent unresolved) -> no fitting

        # Elbow C by this run's own cross-section (round/flex duct is always round; only a
        # rect-form run uses the rectangular-miter-with-vanes coefficient).
        elbow_c = ELBOW_C_RECT if entry["width_in"] else ELBOW_C_ROUND
        elbow_loss = 0.0
        pts = r["points"]
        for i in range(1, len(pts) - 1):
            v1 = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
            v2 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            len1, len2 = math.hypot(*v1), math.hypot(*v2)
            if len1 < 1e-6 or len2 < 1e-6:
                continue
            cos_ang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (len1 * len2)))
            if math.degrees(math.acos(cos_ang)) > ELBOW_ANGLE_THRESHOLD_DEG:
                elbow_loss += elbow_c * own_vp

        entry["pressure_drop_in_wg"] = round(straight_loss + fitting_loss + elbow_loss, 5)

    # 5. Static pressure per AH: walk each AH's own supply subtree and its own return subtree
    # (via the per-eq root_runs/children saved in `ah_subtrees` above) from root(s) to every
    # leaf, summing `pressure_drop_in_wg` along each root-to-leaf path, and take the MAX over
    # all leaf paths on each side — the worst-case ("index") run for that side. The fan has to
    # overcome both sides, so static_pressure_in_wg = supply's worst path + return's worst path.
    def _max_path_pressure(subtree):
        root_runs, children = subtree["root_runs"], subtree["children"]
        if not root_runs:
            return None
        best = 0.0
        stack = [(r, changed_runs.get(r["id"], {}).get("pressure_drop_in_wg") or 0.0) for r in root_runs]
        while stack:
            run, total = stack.pop()
            kids = children.get(id(run), [])
            if not kids:
                best = max(best, total)
            for c in kids:
                stack.append((c, total + (changed_runs.get(c["id"], {}).get("pressure_drop_in_wg") or 0.0)))
        return best

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

    for eq_id, subtrees in ah_subtrees.items():
        supply_max = _max_path_pressure(subtrees["supply"]) if "supply" in subtrees else None
        return_max = _max_path_pressure(subtrees["return"]) if "return" in subtrees else None
        if supply_max is not None and return_max is not None:
            changed_equipment.setdefault(eq_id, {"supply_cfm": None, "return_cfm": None})[
                "static_pressure_in_wg"] = round(supply_max + return_max, 4)

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
               "static_pressure_in_wg": eq.get("static_pressure_in_wg"),
               "matched_manufacturer": None, "matched_model": None, "matched_clamped": None,
               "esp_exceeded": None}
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
            # esp_exceeded only when BOTH the computed static pressure and the matched unit's
            # rated ESP are known — most rows have no rated_esp_in_wg (see air_handlers.csv
            # notes: most catalog units size the fan/drive package per job instead of
            # publishing one nameplate ESP), and that's correct, not a bug, so it's left None.
            rated_esp = match.get("rated_esp_in_wg")
            if row["static_pressure_in_wg"] is not None and rated_esp is not None:
                row["esp_exceeded"] = row["static_pressure_in_wg"] > rated_esp
        equipment_rows.append(row)

    return {"supply_air": supply_air, "schedule": schedule, "terminals": terminal_rows, "equipment": equipment_rows}
