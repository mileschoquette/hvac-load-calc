"""Sanity check for duct auto-sizing — equal-friction-method arithmetic and small hand-built
networks, not an ASHRAE table lookup (there's no time-series or solar involved here, same
reasoning as test_heating.py: nothing to validate against a workbook).

Equipment-matching tests use a small SYNTHETIC fixture table (not the real
equipment_data/*.csv) injected straight into ducts._csv_cache, so they don't depend on the
real CSVs' exact current contents — those are maintained/cleaned up separately.

    python3 tests/test_ducts.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import ducts
from engine.ducts import (required_supply_cfm, round_duct_diameter, rect_duct_width, size_duct_network,
                           match_diffuser, match_air_handler, match_condensing_unit, duct_report_data,
                           friction_rate, diameter_for_friction, rect_equivalent_diameter, _round_up_5)


def test_formulas():
    # 24,000 Btu/h at a 20F supply-air delta-T -> 24000/(1.1*20) = 1090.909...
    assert abs(required_supply_cfm(24000, 20) - 1090.9090909) < 1e-3
    # no load / no delta-T -> 0, not an error
    assert required_supply_cfm(0, 20) == 0.0
    assert required_supply_cfm(24000, 0) == 0.0
    assert required_supply_cfm(None, 20) == 0.0

    # Round-trip check against the reference doc's own numeric sanity check (equipment_data/
    # duct_friction_reference.md section 1): 800 cfm, target fr=0.10 in.wg/100ft -> ideal
    # diameter ~12.77in; feeding that back through the forward formula returns ~0.10
    # in.wg/100ft (the design/report directions are consistent inverses of each other).
    ideal = diameter_for_friction(800, 0.10)
    assert abs(ideal - 12.77) < 0.02
    assert abs(friction_rate(800, ideal) - 0.10) < 1e-6
    # no cfm / no target rate -> None, not an error
    assert diameter_for_friction(0, 0.10) is None
    assert diameter_for_friction(800, 0) is None
    assert friction_rate(0, 12.77) is None
    assert friction_rate(800, 0) is None

    # Rounding the ideal diameter UP to the nearest standard size (14in, the first standard
    # size >= 12.77) can only LOWER the actual friction rate below the target — a bigger duct
    # at the same cfm has less friction, never more.
    assert round_duct_diameter(800, 0.10) == 14
    assert friction_rate(800, 14) < 0.10
    assert round_duct_diameter(0, 0.10) is None
    assert round_duct_diameter(800, 0) is None

    # Rectangular equivalent-diameter round trip (ASHRAE Ch.32 Eq. 25): the width solved by
    # bisection for a fixed height reproduces the SAME target equivalent diameter when fed
    # back through the forward equivalent-diameter formula.
    target_de = diameter_for_friction(1000, 0.10)
    width = ducts._solve_rect_width_for_de(target_de, 10)
    assert abs(rect_equivalent_diameter(width, 10) - target_de) < 1e-3

    assert rect_duct_width(1000, 0.10, 10) % 2 == 0
    assert rect_duct_width(0, 0.10, 10) is None
    assert rect_duct_width(1000, 0.10, 0) is None
    print("PASS  duct sizing formulas")


def test_network():
    # One AH, a rect trunk, and two branches teeing off its middle into two rooms with
    # different sensible loads. Room 1: 24,000 Btu/h -> 24000/22 = 1090.909 cfm.
    # Room 2: 12,000 Btu/h -> 12000/22 = 545.455 cfm. Theoretical trunk total is their sum,
    # 1636.364 cfm, but the real AH (from the actual equipment_data/air_handlers.csv, not a
    # fixture here) only comes in discrete sizes, so the final terminal/trunk cfm is that
    # theoretical split PRORATED to whatever real unit covers it, then rounded up to 5.
    review = {
        "floors": [{"level": 1, "pxPerFt": 12.0}],
        "rooms": [
            {"id": 1, "points": [[0, 0], [100, 0], [100, 200], [0, 200]]},
            {"id": 2, "points": [[200, 0], [300, 0], [300, 200], [200, 200]]},
        ],
        "duct_runs": [
            {"id": 10, "system": "supply", "form": "auto", "points": [[0, 50], [300, 50]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 11, "system": "supply", "form": "auto", "points": [[50, 50], [50, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 12, "system": "supply", "form": "auto", "points": [[250, 50], [250, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            # A disconnected run with no AH in its partition — must be left alone.
            {"id": 13, "system": "return", "form": "auto", "points": [[500, 0], [600, 0]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 20, "kind": "ceiling_supply", "point": [50, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 21, "kind": "ceiling_supply", "point": [250, 150], "level": 1, "auto": True, "cfm": None},
        ],
        "duct_equipment": [{"id": 30, "kind": "AH", "point": [0, 50], "level": 1}],
    }
    result = {"rooms": [{"id": 1, "sensible_btuh": 24000.0}, {"id": 2, "sensible_btuh": 12000.0}]}
    out = size_duct_network(review, result)

    theoretical1, theoretical2 = 24000.0 / 22.0, 12000.0 / 22.0
    theoretical_total = theoretical1 + theoretical2
    match = match_air_handler(theoretical_total)
    target = _round_up_5(match["rated_supply_cfm"])

    t20, t21 = out["duct_terminals"][20]["cfm"], out["duct_terminals"][21]["cfm"]
    # Both terminals land on a multiple of 5, and — the actual point of this rewrite —
    # they add up to EXACTLY the AH's target, not just approximately (independent
    # per-terminal round-up used to let the sum drift a few cfm past the AH's real capacity).
    assert t20 % 5 == 0 and t21 % 5 == 0
    assert t20 + t21 == target
    assert abs(t20 / theoretical1 - t21 / theoretical2) < 0.01   # still proportional to load
    assert out["duct_runs"][10]["cfm"] == t20 + t21   # trunk = sum of both branches
    assert out["duct_runs"][11]["cfm"] == t20
    assert out["duct_runs"][12]["cfm"] == t21
    assert out["duct_equipment"][30]["supply_cfm"] == target
    # the rootless return run has no way to know its flow direction -> left untouched
    assert 13 not in out["duct_runs"]

    # A manual override (auto: False) is skipped entirely, even though it's otherwise
    # perfectly reachable from the AH root.
    review["duct_runs"][1]["auto"] = False
    out2 = size_duct_network(review, result)
    assert 11 not in out2["duct_runs"]
    assert 10 in out2["duct_runs"]   # everything else still resolves normally
    print("PASS  duct network CFM propagation + sizing")


def test_pressure_network():
    # One AH root (run 1, a straight rect trunk -> gets the AH-entrance fitting only), one
    # branch (run 2, teeing off run 1's middle at [60,0] -> gets the branch-tee fitting, PLUS
    # a 90-degree bend at [60,120] -> gets one elbow loss), and a straight return root (run 3,
    # also off the same AH -> AH-entrance fitting only, no tee/elbow). Floor pxPerFt=12.0 so
    # every segment is a clean number of feet (120px = 10ft, matching the reference doc's
    # "pick a clean round pxPerFt" guidance).
    review = {
        "floors": [{"level": 1, "pxPerFt": 12.0}],
        "rooms": [{"id": 1, "points": [[-500, -500], [500, -500], [500, 500], [-500, 500]]}],
        "duct_runs": [
            {"id": 1, "system": "supply", "form": "auto", "points": [[0, 0], [120, 0]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 2, "system": "supply", "form": "auto", "points": [[60, 0], [60, 120], [180, 120]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 3, "system": "return", "form": "auto", "points": [[0, 0], [0, -120]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 10, "kind": "ceiling_supply", "point": [180, 120], "level": 1, "auto": True, "cfm": None},
            {"id": 11, "kind": "ceiling_return", "point": [0, -120], "level": 1, "auto": True, "cfm": None},
        ],
        "duct_equipment": [{"id": 20, "kind": "AH", "point": [0, 0], "level": 1}],
    }
    # 2,200 Btu/h at the default 20F supply delta-T -> exactly 100.0 cfm — and the fixture AH
    # below is rated for exactly 100.0 cfm too, so AH-match proration is a no-op (target ==
    # theoretical, no rescaling), keeping every downstream number a clean, hand-checkable value.
    result = {"rooms": [{"id": 1, "sensible_btuh": 2200.0}]}

    ducts._csv_cache["air_handlers.csv"] = [
        {"manufacturer": "Acme", "model": "AH1", "rated_supply_cfm": 100.0, "rated_esp_in_wg": None}]
    try:
        out = size_duct_network(review, result)
    finally:
        del ducts._csv_cache["air_handlers.csv"]

    run1, run2, run3 = out["duct_runs"][1], out["duct_runs"][2], out["duct_runs"][3]
    assert run1["cfm"] == 100.0 and run2["cfm"] == 100.0 and run3["cfm"] == 100.0
    # run1/run3 are plain (no tee at either endpoint) -> default "rect"; run2's own endpoint
    # lands mid-span on run1 -> "flex" (round cross-section, sized by diameter).
    assert run1["width_in"] and run3["width_in"]
    assert run2["diameter_in"]

    # run1: straight rect trunk off the AH root -> AH-entrance fitting (C=0.50) only, no elbow
    # (dead straight, no interior vertex at all).
    v1 = run1["velocity_fpm"]
    vp1 = (v1 / 4005) ** 2
    expected_dp1 = run1["friction_rate_actual"] / 100.0 * 10.0 + 0.50 * vp1
    assert abs(run1["pressure_drop_in_wg"] - expected_dp1) < 1e-4

    # run2: branch-tee fitting (ratio = its own V / run1's V, interpolated off
    # BRANCH_TEE_C_TABLE) PLUS one elbow (its own >30-degree bend) — both referenced to run2's
    # OWN velocity pressure, round C since it's round/flex cross-section, length 20ft (10+10).
    v2 = run2["velocity_fpm"]
    vp2 = (v2 / 4005) ** 2
    tee_c = ducts._branch_tee_c(v2 / v1)
    expected_dp2 = (run2["friction_rate_actual"] / 100.0 * 20.0 + tee_c * vp2 + ducts.ELBOW_C_ROUND * vp2)
    assert abs(run2["pressure_drop_in_wg"] - expected_dp2) < 1e-4

    # run3: straight rect return root off the same AH -> AH-entrance fitting only.
    v3 = run3["velocity_fpm"]
    vp3 = (v3 / 4005) ** 2
    expected_dp3 = run3["friction_rate_actual"] / 100.0 * 10.0 + 0.50 * vp3
    assert abs(run3["pressure_drop_in_wg"] - expected_dp3) < 1e-4

    # static_pressure_in_wg = supply side's one worst-case path (run1 -> run2, the only
    # supply leaf) + return side's one worst-case path (run3 alone, the only return leaf).
    expected_static = expected_dp1 + expected_dp2 + expected_dp3
    assert abs(out["duct_equipment"][20]["static_pressure_in_wg"] - expected_static) < 1e-4
    print("PASS  duct pressure-drop network (AH entrance + branch tee + elbow + static pressure)")


# Synthetic diffuser fixture — 3 non-overlapping ceiling_supply bands (50-150, 151-300,
# 301-500), nothing in sidewall_return. Deliberately NOT the real diffuser_schedule.csv,
# whose exact bands are being cleaned up (de-overlapped/capped) elsewhere.
_FIXTURE_DIFFUSERS = [
    {"manufacturer": "Acme", "model_line": "AA", "terminal_kind": "ceiling_supply",
     "neck_or_face_size": "6in", "cfm_min": 50.0, "cfm_max": 150.0},
    {"manufacturer": "Acme", "model_line": "BB", "terminal_kind": "ceiling_supply",
     "neck_or_face_size": "8in", "cfm_min": 151.0, "cfm_max": 300.0},
    {"manufacturer": "Acme", "model_line": "CC", "terminal_kind": "ceiling_supply",
     "neck_or_face_size": "10in", "cfm_min": 301.0, "cfm_max": 500.0},
]
# rated_esp_in_wg included per air_handlers.csv's real schema (blank/None for most rows, a
# real value for a few) — only T3 carries one here, matching that "most rows have none" shape.
_FIXTURE_AHS = [
    {"manufacturer": "Trane", "model": "T1", "rated_supply_cfm": 1000.0, "rated_esp_in_wg": None},
    {"manufacturer": "Trane", "model": "T2", "rated_supply_cfm": 2000.0, "rated_esp_in_wg": None},
    {"manufacturer": "Trane", "model": "T3", "rated_supply_cfm": 3000.0, "rated_esp_in_wg": 0.5},
]
_FIXTURE_CUS = [
    {"manufacturer": "Carrier", "model": "C1", "nominal_tons": 2.0},
    {"manufacturer": "Carrier", "model": "C2", "nominal_tons": 4.0},
    {"manufacturer": "Carrier", "model": "C3", "nominal_tons": 6.0},
]


def test_match_diffuser():
    ducts._csv_cache["diffuser_schedule.csv"] = _FIXTURE_DIFFUSERS
    try:
        # in-range -> the band that actually contains it
        assert match_diffuser("ceiling_supply", 200) == {
            "manufacturer": "Acme", "model_line": "BB", "size": "8in", "clamped": None}
        # above every band's max -> largest available, flagged
        m_high = match_diffuser("ceiling_supply", 9999)
        assert m_high["model_line"] == "CC" and m_high["clamped"] == "above_max"
        # below every band's min -> smallest available, flagged
        m_low = match_diffuser("ceiling_supply", 10)
        assert m_low["model_line"] == "AA" and m_low["clamped"] == "below_min"
        # no rows at all for this terminal_kind -> None
        assert match_diffuser("sidewall_return", 200) is None
    finally:
        del ducts._csv_cache["diffuser_schedule.csv"]
    print("PASS  match_diffuser (synthetic fixture)")


def test_match_air_handler():
    ducts._csv_cache["air_handlers.csv"] = _FIXTURE_AHS
    try:
        # 1500 cfm rounds up to the smallest covering model, T2 (2000 cfm, no rated ESP)
        assert match_air_handler(1500) == {"manufacturer": "Trane", "model": "T2",
                                            "rated_supply_cfm": 2000.0, "rated_esp_in_wg": None,
                                            "clamped": None}
        # above every model's rating -> largest available, flagged (T3, which DOES carry a
        # rated ESP — proves match_air_handler passes it through when present, too)
        m_high = match_air_handler(5000)
        assert m_high["model"] == "T3" and m_high["clamped"] == "above_max"
        assert m_high["rated_esp_in_wg"] == 0.5
        # well below the smallest model -> that smallest model still covers it, no clamp
        assert match_air_handler(100) == {"manufacturer": "Trane", "model": "T1",
                                           "rated_supply_cfm": 1000.0, "rated_esp_in_wg": None,
                                           "clamped": None}
    finally:
        del ducts._csv_cache["air_handlers.csv"]
    # empty table -> None
    ducts._csv_cache["air_handlers.csv"] = []
    try:
        assert match_air_handler(1000) is None
    finally:
        del ducts._csv_cache["air_handlers.csv"]
    print("PASS  match_air_handler (synthetic fixture)")


def test_match_condensing_unit():
    ducts._csv_cache["condensing_units.csv"] = _FIXTURE_CUS
    try:
        assert match_condensing_unit(4.0) == {"manufacturer": "Carrier", "model": "C2", "clamped": None}
        m_high = match_condensing_unit(100)
        assert m_high["model"] == "C3" and m_high["clamped"] == "above_max"
        assert match_condensing_unit(0.5) == {"manufacturer": "Carrier", "model": "C1", "clamped": None}
    finally:
        del ducts._csv_cache["condensing_units.csv"]
    print("PASS  match_condensing_unit (synthetic fixture)")


def test_duct_report_data_matching():
    # One room/terminal (ceiling_supply, 24,000 Btu/h -> 1090.9 cfm), an AH feeding it, a
    # CU on the same zone (tons taken from the zone, not the building), and a DH — which
    # must come back with no matched_* fields at all (deliberately not auto-matched).
    review = {
        "rooms": [{"id": 1, "points": [[0, 0], [100, 0], [100, 100], [0, 100]]}],
        "duct_runs": [
            {"id": 100, "system": "supply", "form": "auto", "points": [[0, 50], [50, 50]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 200, "kind": "ceiling_supply", "point": [50, 50], "level": 1, "auto": True, "cfm": None},
        ],
        "duct_equipment": [
            {"id": 300, "kind": "AH", "point": [0, 50], "level": 1, "label": "AH-1"},
            {"id": 301, "kind": "CU", "level": 1, "label": "CU-1", "zone": "Zone A"},
            {"id": 302, "kind": "DH", "level": 1, "label": "DH-1"},
        ],
    }
    result = {"rooms": [{"id": 1, "label": "Room 1", "area_sqft": 100.0, "sensible_btuh": 24000.0}],
              "zones": [{"name": "Zone A", "tons": 3.0}],
              "building": {"tons": 5.0}}

    ducts._csv_cache["diffuser_schedule.csv"] = _FIXTURE_DIFFUSERS
    ducts._csv_cache["air_handlers.csv"] = _FIXTURE_AHS
    ducts._csv_cache["condensing_units.csv"] = _FIXTURE_CUS
    try:
        out = duct_report_data(review, result)

        term = out["terminals"][0]
        # Theoretical 1090.9 cfm matches AH fixture T2 (2000 cfm, the only one that covers
        # it) and prorates 1:1 up to exactly 2000 (single terminal, single AH) — still way
        # above every diffuser band's max (500) -> clamped to CC, flagged above_max either way.
        assert term["matched_manufacturer"] == "Acme"
        assert term["matched_model_line"] == "CC"
        assert term["matched_clamped"] == "above_max"

        equip = {e["tag"]: e for e in out["equipment"]}
        # AH sees the same ~1090.9 cfm -> rounds up to T2 (2000 cfm)
        assert equip["AH-1"]["matched_manufacturer"] == "Trane"
        assert equip["AH-1"]["matched_model"] == "T2"
        # No floor scale in this fixture -> no real-length calc -> no static pressure -> no
        # rated-ESP comparison possible, regardless of T2's own (blank) rated ESP.
        assert equip["AH-1"]["static_pressure_in_wg"] is None
        assert equip["AH-1"]["esp_exceeded"] is None
        # CU tons come from the matching zone (3.0), not the building (5.0) -> C2 (4 tons)
        assert equip["CU-1"]["matched_manufacturer"] == "Carrier"
        assert equip["CU-1"]["matched_model"] == "C2"
        # DH is deliberately never matched
        assert equip["DH-1"]["matched_manufacturer"] is None
        assert equip["DH-1"].get("matched_model") is None
    finally:
        del ducts._csv_cache["diffuser_schedule.csv"]
        del ducts._csv_cache["air_handlers.csv"]
        del ducts._csv_cache["condensing_units.csv"]
    print("PASS  duct_report_data equipment matching (synthetic fixture)")


def test_esp_exceeded_flag():
    # Reuse test_pressure_network's hand-built network purely to get a known, non-trivial
    # static_pressure_in_wg out of the engine, then flex `rated_esp_in_wg` above and below
    # that number to prove `esp_exceeded` flips correctly — and is left None when the matched
    # unit has no rated ESP at all (true for most real air_handlers.csv rows, per that file's
    # own notes: most catalog units size their fan/drive package per job rather than
    # publishing one nameplate ESP, so this is a correct "unknown", not a bug).
    review = {
        "floors": [{"level": 1, "pxPerFt": 12.0}],
        "rooms": [{"id": 1, "points": [[-500, -500], [500, -500], [500, 500], [-500, 500]]}],
        "duct_runs": [
            {"id": 1, "system": "supply", "form": "auto", "points": [[0, 0], [120, 0]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 2, "system": "supply", "form": "auto", "points": [[60, 0], [60, 120], [180, 120]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 3, "system": "return", "form": "auto", "points": [[0, 0], [0, -120]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 10, "kind": "ceiling_supply", "point": [180, 120], "level": 1, "auto": True, "cfm": None},
            {"id": 11, "kind": "ceiling_return", "point": [0, -120], "level": 1, "auto": True, "cfm": None},
        ],
        "duct_equipment": [{"id": 20, "kind": "AH", "point": [0, 0], "level": 1, "label": "AH-1"}],
    }
    result = {"rooms": [{"id": 1, "label": "Room 1", "area_sqft": 100.0, "sensible_btuh": 2200.0}]}

    ducts._csv_cache["air_handlers.csv"] = [
        {"manufacturer": "Acme", "model": "AH1", "rated_supply_cfm": 100.0, "rated_esp_in_wg": None}]
    try:
        sized = size_duct_network(review, result)
    finally:
        del ducts._csv_cache["air_handlers.csv"]
    static = sized["duct_equipment"][20]["static_pressure_in_wg"]
    assert static is not None and static > 0

    for rated_esp, expected in [(static * 0.5, True), (static * 2.0, False), (None, None)]:
        ducts._csv_cache["air_handlers.csv"] = [
            {"manufacturer": "Acme", "model": "AH1", "rated_supply_cfm": 100.0, "rated_esp_in_wg": rated_esp}]
        try:
            out = duct_report_data(review, result)
        finally:
            del ducts._csv_cache["air_handlers.csv"]
        row = next(e for e in out["equipment"] if e["kind"] == "AH")
        assert row["static_pressure_in_wg"] == static
        assert row["esp_exceeded"] is expected
    print("PASS  ESP-exceeded flag (exceeded / within range / no rated ESP)")


def test_supply_return_proration():
    # Same AH is root for BOTH a supply subtree and a separate return subtree — exactly the
    # scenario the old flat `equipment_cfm[eq["id"]] = ...` bug clobbered (the return
    # group's iteration silently overwrote the supply group's). Two supply branches off a
    # trunk feed two rooms with known loads; their theoretical sum (1300 cfm) sits strictly
    # BETWEEN two fixture air handlers (1000/2000 cfm), so the AH match forces a real,
    # non-1.0 proration scale (2000/1300 = 1.5385) instead of an accidental no-op.
    #
    # Room 1 ALSO has a return terminal — this is what proves return-follows-supply: before
    # the fix, room 1's return terminal would have been sized off room 1's own sensible_btuh
    # independently (16,500/22 = 750 cfm -> 750 rounded to 5), completely ignoring that room
    # 1's supply got rescaled by the AH-match proration above. After the fix, the return
    # terminal must equal room 1's FINAL (post-proration, post-round-5) supply total instead
    # — a different, larger number — so this assertion would have failed before the change.
    #
    # Room 3 has a return terminal but NO supply terminal at all -> must come back as 0 cfm,
    # not an independent sensible_btuh-based value (there's no supply in that room to match).
    review = {
        "rooms": [
            {"id": 1, "points": [[0, 0], [100, 0], [100, 200], [0, 200]]},
            {"id": 2, "points": [[200, 0], [300, 0], [300, 200], [200, 200]]},
            # Kept well clear of room 1/2's edges (x=0/100/200/300) so point-in-polygon
            # boundary handling can't accidentally fold the return terminal into room 1.
            {"id": 3, "points": [[-100, 100], [-10, 100], [-10, 200], [-100, 200]]},
        ],
        "duct_runs": [
            {"id": 40, "system": "supply", "form": "auto", "points": [[0, 50], [300, 50]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 41, "system": "supply", "form": "auto", "points": [[50, 50], [50, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 42, "system": "supply", "form": "auto", "points": [[250, 50], [250, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            # Return run into room 1 (same room as supply terminal 50).
            {"id": 43, "system": "return", "form": "auto", "points": [[0, 50], [10, 50], [10, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            # Separate return run into room 3, which has no supply terminal at all.
            {"id": 44, "system": "return", "form": "auto", "points": [[0, 50], [-20, 50], [-20, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 50, "kind": "ceiling_supply", "point": [50, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 51, "kind": "ceiling_supply", "point": [250, 150], "level": 1, "auto": True, "cfm": None},
            # Room 1's return terminal — sits in the same room as supply terminal 50.
            {"id": 52, "kind": "ceiling_return", "point": [10, 150], "level": 1, "auto": True, "cfm": None},
            # Room 3's return terminal — room 3 has no supply terminals.
            {"id": 53, "kind": "ceiling_return", "point": [-20, 150], "level": 1, "auto": True, "cfm": None},
        ],
        "duct_equipment": [{"id": 60, "kind": "AH", "point": [0, 50], "level": 1}],
    }
    # Room 1: 16,500 Btu/h -> 16500/22 = 750.0 cfm. Room 2: 12,100 Btu/h -> 12100/22 = 550.0
    # cfm (theoretical supply sum 1300.0). Room 3 has no supply, so its sensible_btuh is
    # irrelevant to its return terminal now — included just to prove that.
    result = {"rooms": [{"id": 1, "sensible_btuh": 16500.0}, {"id": 2, "sensible_btuh": 12100.0},
                        {"id": 3, "sensible_btuh": 10100.0}]}

    diffuser_fixture = _FIXTURE_DIFFUSERS + [
        {"manufacturer": "Acme", "model_line": "RR", "terminal_kind": "ceiling_return",
         "neck_or_face_size": "10in", "cfm_min": 50.0, "cfm_max": 5000.0},
    ]
    ducts._csv_cache["diffuser_schedule.csv"] = diffuser_fixture
    ducts._csv_cache["air_handlers.csv"] = _FIXTURE_AHS
    try:
        out = size_duct_network(review, result)

        # Theoretical supply total 750 + 550 = 1300 cfm -> smallest fixture AH covering it
        # is T2 (2000 cfm, since T1's 1000 doesn't).
        theoretical1, theoretical2 = 750.0, 550.0

        t50, t51 = out["duct_terminals"][50]["cfm"], out["duct_terminals"][51]["cfm"]
        t52, t53 = out["duct_terminals"][52]["cfm"], out["duct_terminals"][53]["cfm"]

        # (a) the AH's supply_cfm is the matched fixture unit's RATED cfm (2000), not the
        # raw theoretical sum (1300) — and, since this is the same AH id, its return_cfm is
        # ALSO present at the same time (the bug this whole change fixes).
        assert out["duct_equipment"][60]["supply_cfm"] == 2000.0
        assert out["duct_equipment"][60]["return_cfm"] is not None

        # (b) both supply terminals land on a multiple of 5, are proportional to the load
        # they serve, and — the core of this fix — add up to EXACTLY the AH's 2000 cfm,
        # not just approximately (independent per-terminal round-up used to let the sum
        # drift a few cfm past the AH's real capacity, e.g. 1155+850=2005 here).
        assert t50 % 5 == 0 and t51 % 5 == 0
        assert t50 + t51 == 2000.0
        assert abs(t50 / theoretical1 - t51 / theoretical2) < 0.01

        # (c) room 1's return terminal equals room 1's FINAL (post-proration, post-round-5)
        # supply total, t50 — NOT the independent theoretical value 750 (which is what the
        # pre-fix code would have produced: ceil(750/5)*5 = 750, a different number from
        # t50 since t50 is scaled by ~1.538x). This is the core assertion of this test.
        assert t50 != 750.0   # sanity: proration actually moved supply away from theoretical
        assert t52 == t50

        # (d) room 3's return terminal has no matching supply terminal in its room -> 0 cfm,
        # not an independent sensible_btuh-derived value (10100/22 = 459.09... rounded to 460
        # is what the pre-fix code would have produced here).
        assert t53 == 0.0

        # (e) the trunk's cfm is the sum of the two branches' FINAL (post-prorate,
        # post-round) values, not their pre-prorate theoretical sum (1300).
        assert out["duct_runs"][40]["cfm"] == t50 + t51
        assert out["duct_runs"][41]["cfm"] == t50
        assert out["duct_runs"][42]["cfm"] == t51

        # (f) every supply terminal's `size` is populated from the fixture diffuser table;
        # room 1's return terminal matches too (t52 == t50 > 0), room 3's zero-cfm return
        # terminal has no diffuser match (0 cfm falls outside every band, `match_diffuser`
        # still clamps it, so just confirm it's the room-1-mirroring terminal that's sized).
        assert out["duct_terminals"][50]["size"] is not None
        assert out["duct_terminals"][51]["size"] is not None
        assert out["duct_terminals"][52]["size"] == "10in"
    finally:
        del ducts._csv_cache["diffuser_schedule.csv"]
        del ducts._csv_cache["air_handlers.csv"]
    print("PASS  duct network supply/return proration + return-follows-supply + rounding")


def test_capacity_override_and_exact_room_sums():
    # Per the user: an engineer should be able to type in what an air handler actually
    # outputs (instead of trusting the catalog match), and every diffuser downstream of it
    # must add up to EXACTLY that number. Room 1 also gets THREE return terminals — the
    # screenshot bug this whole thing is fixing was an aggregate drift (1205 vs 1200) that
    # only shows up once a room has more than one return terminal splitting the same total,
    # since each terminal's own independent round-up can overshoot.
    review = {
        "rooms": [
            {"id": 1, "points": [[0, 0], [100, 0], [100, 200], [0, 200]]},
            {"id": 2, "points": [[200, 0], [300, 0], [300, 200], [200, 200]]},
        ],
        "duct_runs": [
            {"id": 100, "system": "supply", "form": "auto", "points": [[0, 50], [300, 50]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 101, "system": "supply", "form": "auto", "points": [[50, 50], [50, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 102, "system": "supply", "form": "auto", "points": [[250, 50], [250, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 110, "system": "return", "form": "auto", "points": [[0, 50], [80, 50]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 111, "system": "return", "form": "auto", "points": [[20, 50], [20, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 112, "system": "return", "form": "auto", "points": [[40, 50], [40, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
            {"id": 113, "system": "return", "form": "auto", "points": [[60, 50], [60, 150]],
             "level": 1, "auto": True, "width_in": None, "height_in": None, "diameter_in": None, "cfm": None},
        ],
        "duct_terminals": [
            {"id": 200, "kind": "ceiling_supply", "point": [50, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 201, "kind": "ceiling_supply", "point": [250, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 210, "kind": "ceiling_return", "point": [20, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 211, "kind": "ceiling_return", "point": [40, 150], "level": 1, "auto": True, "cfm": None},
            {"id": 212, "kind": "ceiling_return", "point": [60, 150], "level": 1, "auto": True, "cfm": None},
        ],
        # Deliberately not a multiple of 5, and deliberately not near any real catalog
        # model's rated cfm — proves the override bypasses match_air_handler entirely.
        "duct_equipment": [{"id": 300, "kind": "AH", "point": [0, 50], "level": 1, "capacity_cfm": 983}],
    }
    result = {"rooms": [{"id": 1, "sensible_btuh": 30000.0}, {"id": 2, "sensible_btuh": 15000.0}]}
    out = size_duct_network(review, result)

    # (a) the override (rounded up to the nearest 5) is what the AH reports, and it's what
    # the two supply terminals add up to EXACTLY — not whatever a catalog match would give.
    assert out["duct_equipment"][300]["supply_cfm"] == 985.0
    t200, t201 = out["duct_terminals"][200]["cfm"], out["duct_terminals"][201]["cfm"]
    assert t200 % 5 == 0 and t201 % 5 == 0
    assert t200 + t201 == 985.0

    # (b) the three return terminals in room 1 (which is what makes the independent-rounding
    # drift show up) add up to EXACTLY room 1's own finalized supply total, t200 — not
    # merely close to it.
    t210, t211, t212 = (out["duct_terminals"][210]["cfm"], out["duct_terminals"][211]["cfm"],
                         out["duct_terminals"][212]["cfm"])
    assert t210 % 5 == 0 and t211 % 5 == 0 and t212 % 5 == 0
    assert t210 + t211 + t212 == t200
    print("PASS  manual AH capacity override + exact per-room supply/return sums")


def main():
    test_formulas()
    test_network()
    test_pressure_network()
    test_match_diffuser()
    test_match_air_handler()
    test_match_condensing_unit()
    test_duct_report_data_matching()
    test_esp_exceeded_flag()
    test_supply_return_proration()
    test_capacity_override_and_exact_room_sums()


if __name__ == "__main__":
    main()
