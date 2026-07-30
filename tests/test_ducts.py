"""Sanity check for duct auto-sizing — velocity-method arithmetic and a small hand-built
network, not an ASHRAE table lookup (there's no time-series or solar involved here, same
reasoning as test_heating.py: nothing to validate against a workbook).

Equipment-matching tests use a small SYNTHETIC fixture table (not the real
equipment_data/*.csv) injected straight into ducts._csv_cache, so they don't depend on the
real CSVs' exact current contents — those are maintained/cleaned up separately.

    python3 tests/test_ducts.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import ducts
from engine.ducts import (required_supply_cfm, round_duct_diameter, rect_duct_width, size_duct_network,
                           match_diffuser, match_air_handler, match_condensing_unit, duct_report_data)


def test_formulas():
    # 24,000 Btu/h at a 20F supply-air delta-T -> 24000/(1.1*20) = 1090.909...
    assert abs(required_supply_cfm(24000, 20) - 1090.9090909) < 1e-3
    # no load / no delta-T -> 0, not an error
    assert required_supply_cfm(0, 20) == 0.0
    assert required_supply_cfm(24000, 0) == 0.0
    assert required_supply_cfm(None, 20) == 0.0

    # 450 cfm at 900 fpm -> area=0.5sqft -> dia=sqrt(4*0.5/pi)*12=9.575in -> rounds up to 10
    assert round_duct_diameter(450, 900) == 10
    # no cfm / no velocity -> None, not an error
    assert round_duct_diameter(0, 900) is None
    assert round_duct_diameter(450, 0) is None

    # 1000 cfm at 900 fpm, 10in height -> area=1.1111sqft -> width=16.0in exactly -> 16
    assert rect_duct_width(1000, 900, 10) == 16
    # 1100 cfm, same velocity/height -> width=17.6in -> rounds up to the next 2" step, 18
    assert rect_duct_width(1100, 900, 10) == 18
    assert rect_duct_width(0, 900, 10) is None
    assert rect_duct_width(1000, 900, 0) is None
    print("PASS  duct sizing formulas")


def test_network():
    # One AH, a rect trunk, and two branches teeing off its middle into two rooms with
    # different sensible loads. Room 1: 24,000 Btu/h -> 24000/22 = 1090.909 cfm.
    # Room 2: 12,000 Btu/h -> 12000/22 = 545.455 cfm. Trunk should carry their sum.
    review = {
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

    t20, t21 = out["duct_terminals"][20]["cfm"], out["duct_terminals"][21]["cfm"]
    assert abs(t20 - 1090.9) < 0.1
    assert abs(t21 - 545.5) < 0.1
    assert abs(out["duct_runs"][10]["cfm"] - (t20 + t21)) < 0.1   # trunk = sum of both branches
    assert abs(out["duct_runs"][11]["cfm"] - t20) < 0.1
    assert abs(out["duct_runs"][12]["cfm"] - t21) < 0.1
    assert out["duct_equipment"][30]["cfm"] == out["duct_runs"][10]["cfm"]
    # the rootless return run has no way to know its flow direction -> left untouched
    assert 13 not in out["duct_runs"]

    # A manual override (auto: False) is skipped entirely, even though it's otherwise
    # perfectly reachable from the AH root.
    review["duct_runs"][1]["auto"] = False
    out2 = size_duct_network(review, result)
    assert 11 not in out2["duct_runs"]
    assert 10 in out2["duct_runs"]   # everything else still resolves normally
    print("PASS  duct network CFM propagation + sizing")


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
_FIXTURE_AHS = [
    {"manufacturer": "Trane", "model": "T1", "rated_supply_cfm": 1000.0},
    {"manufacturer": "Trane", "model": "T2", "rated_supply_cfm": 2000.0},
    {"manufacturer": "Trane", "model": "T3", "rated_supply_cfm": 3000.0},
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
        # 1500 cfm rounds up to the smallest covering model, T2 (2000 cfm)
        assert match_air_handler(1500) == {"manufacturer": "Trane", "model": "T2", "clamped": None}
        # above every model's rating -> largest available, flagged
        m_high = match_air_handler(5000)
        assert m_high["model"] == "T3" and m_high["clamped"] == "above_max"
        # well below the smallest model -> that smallest model still covers it, no clamp
        assert match_air_handler(100) == {"manufacturer": "Trane", "model": "T1", "clamped": None}
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
        # 1090.9 cfm falls in BB's 151-300 band? No — falls in CC's 301-500? Neither:
        # 1090.9 is above every band's max (500) -> clamped to CC, flagged above_max.
        assert term["matched_manufacturer"] == "Acme"
        assert term["matched_model_line"] == "CC"
        assert term["matched_clamped"] == "above_max"

        equip = {e["tag"]: e for e in out["equipment"]}
        # AH sees the same ~1090.9 cfm -> rounds up to T2 (2000 cfm)
        assert equip["AH-1"]["matched_manufacturer"] == "Trane"
        assert equip["AH-1"]["matched_model"] == "T2"
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


def main():
    test_formulas()
    test_network()
    test_match_diffuser()
    test_match_air_handler()
    test_match_condensing_unit()
    test_duct_report_data_matching()


if __name__ == "__main__":
    main()
