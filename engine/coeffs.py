"""Coefficient library for the RTS engine — CTS (conduction time series) by wall/
roof construction weight, and RTS (radiant time series) by zone, extracted from
the ASHRAE RTS example workbook. Real projects rarely specify a full layer stack,
so we map a construction to the nearest representative set by thermal mass; the
engineer can override. The exact sets used in the single-room validation are the
'spandrel' (light wall), 'brick' (heavy wall), and the zone-8 RTS series.
"""
from __future__ import annotations

_RTS_NONSOLAR_ALL = {
    1: [0.47, 0.19, 0.11, 0.06, 0.04, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    2: [0.5, 0.18, 0.1, 0.06, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    3: [0.53, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    4: [0.41, 0.2, 0.12, 0.08, 0.05, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    5: [0.43, 0.19, 0.11, 0.07, 0.05, 0.03, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    6: [0.46, 0.19, 0.11, 0.07, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    7: [0.46, 0.18, 0.1, 0.06, 0.04, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    8: [0.49, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0],
    9: [0.52, 0.16, 0.08, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    10: [0.31, 0.17, 0.11, 0.08, 0.06, 0.04, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    11: [0.33, 0.16, 0.1, 0.07, 0.05, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0],
    12: [0.35, 0.15, 0.1, 0.07, 0.05, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0],
    13: [0.34, 0.09, 0.06, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    14: [0.38, 0.09, 0.06, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01],
    15: [0.42, 0.09, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
    16: [0.22, 0.1, 0.06, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
    17: [0.25, 0.09, 0.06, 0.05, 0.05, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
    18: [0.28, 0.09, 0.06, 0.05, 0.04, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01],
    19: [0.46, 0.19, 0.11, 0.06, 0.04, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    20: [0.4, 0.2, 0.12, 0.08, 0.05, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    21: [0.46, 0.18, 0.1, 0.06, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0],
    22: [0.31, 0.17, 0.11, 0.08, 0.06, 0.04, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    23: [0.33, 0.09, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    24: [0.21, 0.09, 0.06, 0.05, 0.05, 0.04, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
}
_RTS_SOLAR_ALL = {
    1: [0.53, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    2: [0.55, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    3: [0.56, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    4: [0.44, 0.19, 0.11, 0.07, 0.05, 0.03, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    5: [0.45, 0.2, 0.11, 0.07, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    6: [0.46, 0.2, 0.11, 0.07, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    7: [0.52, 0.16, 0.08, 0.05, 0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    8: [0.54, 0.16, 0.08, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    9: [0.55, 0.15, 0.08, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0],
    10: [0.28, 0.15, 0.1, 0.07, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0],
    11: [0.29, 0.15, 0.1, 0.07, 0.06, 0.05, 0.04, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0],
    12: [0.29, 0.15, 0.1, 0.07, 0.06, 0.05, 0.04, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0],
    13: [0.47, 0.11, 0.06, 0.04, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
    14: [0.49, 0.12, 0.06, 0.04, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
    15: [0.51, 0.12, 0.06, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
    16: [0.26, 0.12, 0.07, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
    17: [0.27, 0.13, 0.07, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    18: [0.28, 0.13, 0.07, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    19: [1.0] + [0.0] * 23,
    20: [1.0] + [0.0] * 23,
    21: [1.0] + [0.0] * 23,
    22: [1.0] + [0.0] * 23,
    23: [1.0] + [0.0] * 23,
    24: [1.0] + [0.0] * 23,
}
# name -> zone id, from ASHRAE Fundamentals Ch.18 Table 19/20 (2017), extracted
# from the RTS Components workbook's "RTS Data" sheet — the SAME source the
# golden calibration's zone-8 series came from, just the other 23 zones too.
_ZONE_NAME_TO_ID = {
    "LW w Carpet 10% glass": 1, "LW w Carpet 50% glass": 2, "LW w Carpet 90% glass": 3,
    "LW no Carpet 10% glass": 4, "LW no Carpet 50% glass": 5, "LW no Carpet 90% glass": 6,
    "MW w Carpet 10% glass": 7, "MW w Carpet 50% glass": 8, "MW w Carpet 90% glass": 9,
    "MW no Carpet 10% glass": 10, "MW no Carpet 50% glass": 11, "MW no Carpet 90% glass": 12,
    "HW w Carpet 10% glass": 13, "HW w Carpet 50% glass": 14, "HW w Carpet 90% glass": 15,
    "HW no Carpet 10% glass": 16, "HW no Carpet 50% glass": 17, "HW no Carpet 90% glass": 18,
    "Interior LW w Carpet": 19, "Interior LW no Carpet": 20,
    "Interior MW w Carpet": 21, "Interior MW no Carpet": 22,
    "Interior HW w Carpet": 23, "Interior HW no Carpet": 24,
}
_MASS_PREFIX = {"light": "LW", "medium": "MW", "heavy": "HW"}
ZONE_NAME = {zid: name for name, zid in _ZONE_NAME_TO_ID.items()}


def select_rts_zone(wall_mass: str, has_carpet: bool, glass_pct: float, is_interior: bool = False) -> int:
    """ASHRAE Ch.18 Table 19/20 zone selection: exterior wall mass (light/medium/
    heavy) x floor covering x glass area as a % of gross exterior wall area
    (bucketed to the nearest of 10/50/90%) — or one of the 6 interior-zone types
    for a room with no exterior wall/window exposure at all (glass_pct moot)."""
    prefix = _MASS_PREFIX.get(wall_mass, "MW")
    covering = "w Carpet" if has_carpet else "no Carpet"
    if is_interior:
        name = f"Interior {prefix} {covering}"
    else:
        bucket = 10 if glass_pct < 30 else (50 if glass_pct < 70 else 90)
        name = f"{prefix} {covering} {bucket}% glass"
    return _ZONE_NAME_TO_ID[name]


def rts_series(zone_id: int) -> tuple[list[float], list[float]]:
    """(nonsolar, solar) RTS series for a zone id from select_rts_zone()."""
    return _RTS_NONSOLAR_ALL[zone_id], _RTS_SOLAR_ALL[zone_id]

# ── Wall CTS (c_0..c_23), from Wall CTS Data ─────────────────────────────────
CTS_WALL = {
    # light / low-mass curtain wall (spandrel glass) — quick response
    "light": [0.18011, 0.57131, 0.19838, 0.04037, 0.00790, 0.00154, 0.00030,
              0.00006, 0.00001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
              0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    # heavy masonry (brick + insulation + gyp) — delayed, peak ~3 h lag
    "heavy": [0.00210, 0.04847, 0.13948, 0.16671, 0.14860, 0.11957, 0.09248,
              0.07036, 0.05316, 0.04004, 0.03012, 0.02264, 0.01702, 0.01279,
              0.00961, 0.00722, 0.00543, 0.00408, 0.00307, 0.00230, 0.00173,
              0.00130, 0.00098, 0.00073],
}
# medium = mean of light & heavy (reasonable interpolation for CMU + furring)
CTS_WALL["medium"] = [(a + b) / 2 for a, b in zip(CTS_WALL["light"], CTS_WALL["heavy"])]

# ── Roof CTS ─────────────────────────────────────────────────────────────────
# Light flat roof (metal deck, insulation board, low mass), Roof CTS Data #17 —
# the exact construction behind the single-room golden reconstruction.
CTS_ROOF = {
    "light": [0.18039, 0.60046, 0.18407, 0.02970, 0.00456, 0.00070, 0.00011,
              0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
              0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "medium": CTS_WALL["medium"], "heavy": CTS_WALL["heavy"],
}

# ── Radiant fractions (radiant portion of each gain type) ─────────────────────
# roof/equipment corrected against the single-room golden case (Table 35): roof
# convective/radiant is 0.4/0.6 (not the wall's 0.54/0.46), equipment is 0.7/0.3.
RADIANT_FRACTION = {
    "wall": 0.46, "roof": 0.60, "window_conduction": 0.46,
    "people": 0.60, "lighting": 0.48, "equipment": 0.30,
}


def wall_mass(u_value: float | None) -> str:
    """Guess construction weight from U-value when no explicit mass is given:
    well-insulated assemblies here are typically higher-mass CMU/masonry."""
    if u_value is None:
        return "medium"
    return "light" if u_value >= 0.2 else "medium"


# DM (Design Master) construction "type code" (iType) -> our CTS mass bucket.
# Confirmed against the real dm_hvac.dm construction catalog (see the Adicot
# Summer 26 app's `_MASS_CLASS_OPTIONS`) — these are the codes loadcalc_input.
# json's wall_types/roof_types actually carry as "itype" from the DM Setup site.
DM_ITYPE_MASS = {
    "wall": {2: "light", 5: "light", 10: "heavy"},   # Frame, Wood stud, Block/CMU
    "roof": {4: "light", 2: "light", 10: "heavy"},   # Wood deck/vented attic, Frame, Concrete/masonry
}


def mass_from_construction(category: str, itype, u_value: float | None) -> str:
    """The DM construction type code, when present, identifies the actual
    assembly (wood frame vs. CMU, etc.) — prefer it over guessing from U-value
    alone, since a well-insulated frame wall and an insulated CMU wall can land
    on a similar U despite very different thermal mass. Falls back to the
    U-value guess when itype is missing or not one of the confirmed codes
    (e.g. a construction the engineer added by hand)."""
    if itype is not None:
        try:
            mass = DM_ITYPE_MASS.get(category, {}).get(int(itype))
        except (TypeError, ValueError):
            mass = None
        if mass is not None:
            return mass
    return wall_mass(u_value)


def cts_wall(mass="medium"):
    return CTS_WALL.get(mass, CTS_WALL["medium"])


def cts_roof(mass="medium"):
    return CTS_ROOF.get(mass, CTS_ROOF["medium"])
