"""Regression coverage for engine/compute.py's compute_building() and its internal phase
functions (added alongside the module-level refactor that split one giant function into
_resolve_scales_and_types / _resolve_weather_and_safety_factors / _build_design_day_context /
_compute_room_loads / _rollup_building).

compute_building() consumes a `review` dict — rooms_edited.json's shape, everything
buildPayload() saves (rooms, wall/roof/glass/door/floor type libraries, openings, floors,
safety_factors) — not validation/golden_building_inputs.py's lower-level shape (that's
reconstruct()'s input, already covered by test_golden_room.py / test_golden_building.py).
So this file hand-builds a small multi-floor, multi-zone `review` fixture (mirroring how
loadcalc.py builds it) and checks structural properties (keys present, room count, positive/
sane tonnage) rather than an exact ASHRAE-table match — this is compute_building's own
"is the plumbing wired up right" test, not another physics calibration.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.compute import compute_building, _resolve_sf, _resolve_scales_and_types


def _review():
    return {
        "pxPerFt": 12.0,
        "floors": [
            {"level": 1, "pxPerFt": 12.0, "label": "First Floor"},
            {"level": 2, "pxPerFt": 24.0, "label": "Second Floor"},
        ],
        "wall_types": [{"name": "8in CMU", "u": 0.12, "itype": "heavy"}],
        "roof_types": [{"name": "Built-up", "u": 0.05, "itype": "medium"}],
        "glass_types": [{"name": "DblLowE", "u": 0.35, "shgc": 0.30}],
        "door_types": [{"name": "Hollow Metal", "u": 0.5}],
        "floor_types": [{"name": "Slab on grade", "u": 0.73, "description": "slab perimeter"}],
        "safety_factors": {"clg_room": 5.0, "clg_vent": 0.0, "htg_room": 10.0, "htg_vent": 0.0},
        "openings": [
            {"rid": 1, "edge": 0, "cat": "window", "type": "DblLowE", "width": 4.0, "height": 5.0},
            {"rid": 1, "edge": 1, "cat": "door", "type": "Hollow Metal", "width": 3.0, "height": 7.0},
        ],
        "rooms": [
            {"id": 1, "label": "Office 101", "type": "Office", "zone": "Zone A", "level": 1,
             "ceiling_ft": 9.0, "floor_to_floor_ft": 12.0, "carpet": True,
             "points": [[0, 0], [240, 0], [240, 180], [0, 180]],
             "walls": ["8in CMU", "8in CMU", "No wall load", "8in CMU"],
             "wall_orientation": ["S", "E", "N", "W"],
             "roof": "Built-up", "floor": "Slab on grade",
             "loads": {"htg": 70, "clg": 75, "lpd": 1.0, "epd": 1.2, "occ": 4,
                       "vent": 120, "inf": 0.2, "exh_cfm": 50, "makeup_pct": 50}},
            {"id": 2, "label": "Storage 201", "type": "Storage", "zone": "Zone B", "level": 2,
             "ceiling_ft": 10.0, "carpet": True,
             "points": [[0, 0], [150, 0], [150, 150], [0, 150]],
             "walls": ["8in CMU", "8in CMU", "8in CMU", "8in CMU"],
             "wall_orientation": ["S", "E", "N", "W"],
             "roof": "Built-up", "floor": "No floor load",
             "loads": {"vent": 0, "inf": 0.1}},
        ],
    }


_WEATHER = {"cooling_db": 94.0, "daily_range": 17.0, "lat": 27.9, "lon": -82.3,
            "std_meridian": -75.0, "tau_b": 0.51, "tau_d": 2.05, "indoor_db": 75.0,
            "indoor_grains": 65.0, "outdoor_grains": 118.0, "heating_db": 40.0,
            "station": "Tampa Intl", "weather_source": "bundled"}


def test_compute_building_shape_and_sane_ranges():
    result = compute_building(_review(), _WEATHER, month=8)

    for key in ("design", "rooms", "building", "envelope", "types",
               "air_balance", "ventilation_schedule"):
        assert key in result

    assert len(result["rooms"]) == 2
    for room in result["rooms"]:
        assert room["sensible_btuh"] >= 0
        assert 1 <= room["peak_hour"] <= 24

    b = result["building"]
    assert b["sensible_btuh"] > 0
    assert 0 < b["tons"] < 1000   # sane range for this tiny 2-room fixture

    # two zones / two floors -> both rollups present
    assert {z["name"] for z in result["zones"]} == {"Zone A", "Zone B"}
    assert {f["name"] for f in result["floors"]} == {"First Floor", "Second Floor"}
    for z in result["zones"]:
        assert z["tons"] >= 0
    for f in result["floors"]:
        assert f["tons"] >= 0


def test_compute_building_deterministic():
    """Same inputs -> same output (no hidden mutable/global state leaking between calls)."""
    review = _review()
    r1 = compute_building(review, _WEATHER, month=8)
    r2 = compute_building(review, _WEATHER, month=8)
    assert r1 == r2


def test_resolve_sf_default_fallback():
    assert _resolve_sf({}, "clg_room") == 5.0
    assert _resolve_sf({"clg_room": None}, "clg_room") == 5.0


def test_resolve_sf_respects_explicit_zero():
    # A deliberately-set 0 must NOT be overridden by the nonzero default — the falsy-zero
    # trap this codebase has been bitten by before (see engine/compute.py's module docs).
    assert _resolve_sf({"clg_vent": 0}, "clg_vent") == 0
    assert _resolve_sf({"htg_room": 0}, "htg_room") == 0


def test_resolve_scales_and_types_multi_floor_ppf():
    review = _review()
    ppf_by_level, default_ppf, wtypes, rtypes, gtypes, dtypes, ftypes, openings = \
        _resolve_scales_and_types(review)
    assert ppf_by_level == {1: 12.0, 2: 24.0}
    assert default_ppf == 12.0
    assert "8in CMU" in wtypes
    assert "Built-up" in rtypes
    assert "DblLowE" in gtypes
    assert "Hollow Metal" in dtypes
    assert "Slab on grade" in ftypes
    assert len(openings) == 2


def test_resolve_scales_and_types_falls_back_to_top_level_pxperft():
    # An old saved file with no `floors` key at all -> every level falls back to
    # review["pxPerFt"] (or the hardcoded 18.0 default if that's absent too).
    review = {"pxPerFt": 20.0, "rooms": [{"id": 1, "level": 1}]}
    ppf_by_level, default_ppf, *_ = _resolve_scales_and_types(review)
    assert ppf_by_level == {}
    assert default_ppf == 20.0
