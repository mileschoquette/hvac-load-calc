"""Raw inputs for ASHRAE's whole-building example blocks (Tables 37, 38, 39 —
"RTS Cooling and Heating Loads Examples"). Same building, same Atlanta location,
same wall/roof/window constructions as the single-room example (Room 215B, see
`golden_room_215b_inputs.py`) — each block is just a much bigger "room" (an
entire floor, or the whole building) with aggregated wall/window areas and
people/lighting/equipment totals. Confirmed straight off the workbook: AHU-1 +
AHU-2's wall/window areas and internal-load magnitudes sum exactly to the
Building block's — Table 39 is not independently derived, it's the two floors'
inputs added together, evaluated at the *building's own* peak (which differs
from either floor's peak hour, since the floors don't peak simultaneously).

Two things these blocks need that the single room didn't:
  - Return-air credit is OFF here (0% for both roof and lighting) — Table 35's
    30%/26% RA credit was that example's own choice, not a fixed constant.
  - A separate ventilation (outside air) load, on top of the room+RA+latent
    total: OA sensible (derived below, straight physics: 1.1*cfm*(Tout-Tin) —
    confirmed to ~0.02% against the published figure) plus OA latent and two
    "heat pump" terms (HP to supply air, HP to chilled water) that are outside
    the RTS envelope/gains method being calibrated here (a specific mechanical-
    system add-on in this example) — those two are taken as given, transcribed
    inputs, same as tau/design-day values are elsewhere.

    TOTAL BLOCK COOLING LOAD = room_sensible + ra_sensible + room_latent
                                + oa_sensible + oa_latent
                                + hp_to_supply_air + hp_to_chw
"""
from __future__ import annotations

from .golden_room_215b_inputs import (
    LAT, LON, STD_MERIDIAN, T_INDOOR, WALL_TYPE1, WALL_TYPE2, ROOF, WINDOW,
    RTS_NONSOLAR_Z8, RTS_SOLAR_Z8, AZ_SOUTH, AZ_WEST, OCCUPIED_SCHEDULE,
)

AZ_NORTH, AZ_EAST = -210.0, -120.0   # actual azimuths (rotated -30 deg), N/E faces

# Window Type 1 = same glazing/IAC as WINDOW (Type 2) but with NO overhang.
WINDOW_TYPE1 = dict(WINDOW)
WINDOW_TYPE1["overhang_depth_ft"] = 0.0
WINDOW_TYPE1["overhang_above_ft"] = 0.0

# Weather Data - OA sheet, monthly columns (Jul = index 7, Aug = index 8 already
# in golden_room_215b_inputs as *_AUG).
COOLING_DB_JUL, DAILY_RANGE_JUL = 91.6, 20.2
TAU_B_JUL, TAU_D_JUL = 0.515, 2.066
COOLING_DB_SEP, DAILY_RANGE_SEP = 86.4, 19.1
TAU_B_SEP, TAU_D_SEP = 0.417, 2.312


def _walls(w_type1_areas, w_type2_areas):
    """Build the (construction, azimuth, area) list for all 4 exposures x 2
    wall types from a block's per-exposure area tables (order: N, S, E, W)."""
    exposures = [AZ_NORTH, AZ_SOUTH, AZ_EAST, AZ_WEST]
    out = []
    for az, a1, a2 in zip(exposures, w_type1_areas, w_type2_areas):
        if a1:
            out.append((WALL_TYPE1, az, a1))
        if a2:
            out.append((WALL_TYPE2, az, a2))
    return out


def _windows(win1_areas, win2_areas):
    exposures = [AZ_NORTH, AZ_SOUTH, AZ_EAST, AZ_WEST]
    out = []
    for az, a1, a2 in zip(exposures, win1_areas, win2_areas):
        if a1:
            out.append((WINDOW_TYPE1, az, a1))
        if a2:
            out.append((WINDOW, az, a2))
    return out


# ── AHU-1, First Floor Block (Table 37) — no roof exposure, Window Type 1 only.
# NOTE: unlike the other two blocks, AHU-1's own room-sensible peak (month 9,
# hour 12 — "Peak Rm.Sens. Occurs" on the sheet) does NOT coincide with the
# TOTAL BLOCK COOLING LOAD's peak (month 8, hour 15): outside-air sensible
# (which follows the outdoor dry-bulb curve) shifts the combined peak to a
# different month entirely. `target_room_sensible` is at the room's own peak
# (Sep/12), not at the block's (Aug/15) — the two are not comparable hour-for-
# hour, only the TOTAL is meaningfully checked at the block's peak.
AHU1 = dict(
    month=8, peak_hour=15,
    room_sensible_month=9, room_sensible_hour=12,
    walls=_walls([680, 560, 400, 400], [1400, 1350, 1040, 360]),
    roof=None,
    windows=_windows([600, 1000, 120, 360], [0, 0, 0, 0]),
    people=dict(n=133, sensible_per_person=250.0, latent_per_person=200.0,
                radiant_frac=0.60, schedule=OCCUPIED_SCHEDULE),
    lighting=dict(watts=20900.0, radiant_frac=0.48, ra_fraction=0.0,
                  schedule=OCCUPIED_SCHEDULE),
    equipment=dict(watts=19000.0, radiant_frac=0.30, schedule=OCCUPIED_SCHEDULE),
    oa_cfm=2660.0, oa_latent=65569.91729408878, hp_to_supply_air=38191.47,
    hp_to_chw=0.0,
    target_room_sensible=250608.84053845864, target_ra_sensible=0.0,
    target_latent=26600.0, target_total_block=417766.34067092533,
)

# ── AHU-2, Second Floor Block (Table 38) — has the roof, Window Type 2 (10 ft
# overhang) only, no RA credit for roof or lighting
AHU2 = dict(
    month=7, peak_hour=14,
    room_sensible_month=7, room_sensible_hour=14,
    walls=_walls([510, 390, 300, 300], [1040, 920, 540, 540]),
    roof=(ROOF, 15700.0, 0.0),
    windows=_windows([0, 0, 0, 0], [560, 840, 360, 360]),
    people=dict(n=110, sensible_per_person=250.0, latent_per_person=200.0,
                radiant_frac=0.60, schedule=OCCUPIED_SCHEDULE),
    lighting=dict(watts=17270.0, radiant_frac=0.48, ra_fraction=0.0,
                  schedule=OCCUPIED_SCHEDULE),
    equipment=dict(watts=15700.0, radiant_frac=0.30, schedule=OCCUPIED_SCHEDULE),
    oa_cfm=2200.0, oa_latent=53409.04941962306, hp_to_supply_air=25460.98,
    hp_to_chw=0.0,
    target_room_sensible=220180.6730190903, target_ra_sensible=0.0,
    target_latent=22000.0, target_total_block=361222.7024387133,
)

# ── Building (Table 39) — AHU-1 + AHU-2's inputs summed; its own peak (Jul 15)
# differs from either floor's own peak, since they don't peak simultaneously.
BUILDING = dict(
    month=7, peak_hour=15,
    room_sensible_month=7, room_sensible_hour=15,
    walls=_walls([1190, 950, 700, 700], [2440, 2270, 1580, 900]),
    roof=(ROOF, 15700.0, 0.0),
    windows=_windows([600, 1000, 120, 360], [560, 840, 360, 360]),
    people=dict(n=243, sensible_per_person=250.0, latent_per_person=200.0,
                radiant_frac=0.60, schedule=OCCUPIED_SCHEDULE),
    lighting=dict(watts=38170.0, radiant_frac=0.48, ra_fraction=0.0,
                  schedule=OCCUPIED_SCHEDULE),
    equipment=dict(watts=34700.0, radiant_frac=0.30, schedule=OCCUPIED_SCHEDULE),
    oa_cfm=4860.0, oa_latent=117985.4455360764, hp_to_supply_air=63652.45,
    hp_to_chw=12730.49,
    target_room_sensible=459241.03437130386, target_ra_sensible=0.0,
    target_latent=48600.0, target_total_block=790953.0199073801,
)

BLOCKS = {"ahu1_first_floor": AHU1, "ahu2_second_floor": AHU2, "building": BUILDING}
