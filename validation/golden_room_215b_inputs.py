"""Raw inputs for ASHRAE's single-room golden example (Table 35 Single Room
Example, Room 215B, 130 sf office — "RTS Cooling and Heating Loads Examples").

Every value here is transcribed directly from the workbook (Master Input Data
sheet unless noted) — nothing is derived or guessed. This is the one room in
the example building where every input (geometry, construction, CTS, RTS,
schedules, window shading/overhang) is laid out explicitly enough to
reconstruct from scratch, which is why it's the engine's calibration target
(see HANDOFF.md, "Golden engine calibration").

    python3 tests/test_golden_room.py
"""
from __future__ import annotations

# ── Location & design day (Master Input Data; Weather Data - OA, August col) ──
LAT, LON, STD_MERIDIAN = 33.64, -84.43, -75.0
MONTH = 8                       # peak block load month (Table 35 row 23)
PEAK_HOUR = 14                  # 1-indexed (2 pm)
COOLING_DB_AUG = 90.9           # Monthly Design DB, August
DAILY_RANGE_AUG = 19.5
TAU_B_AUG, TAU_D_AUG = 0.515, 2.052
T_INDOOR = 75.0

# ── Room geometry ──────────────────────────────────────────────────────────
FLOOR_AREA_SF = 130.0           # 13 ft x 10 ft
CEILING_HT_FT = 9.0

# Nominal exposure azimuths rotated -30 deg (the example building's actual
# orientation): actual = nominal - 30. South = -30, West = 60.
AZ_SOUTH, AZ_WEST = -30.0, 60.0
TILT_WALL, TILT_ROOF = 90.0, 0.0

# ── Roof: "Metal deck", Roof CTS Data #17 ──────────────────────────────────
ROOF = dict(
    area_sf=130.0, u=0.032, absorptance=0.45, ho=3.0,
    lwr_corr=20.0 / 3.0,          # epsilon=1, delta-R=20 (horizontal), ho=3
    radiant_frac=0.60,            # convective 0.4 / radiant 0.6
    ra_fraction=0.30,             # 30% of the roof's cooling load bypasses the
                                   # room straight to the return-air plenum
    cts=[0.1803888676949315, 0.6004599285725137, 0.18406948519022692,
         0.029696127254213785, 0.0045611356218978015, 0.0006982402674335326,
         0.00010686721020921031, 1.6357547410513338e-05, 2.5052924694214184e-06,
         3.8524558636547176e-07, 6.077867299187138e-08, 1.1119970581795108e-08,
         3.5198529043726423e-09, 2.5042358749139632e-09, 2.5042358749139632e-09,
         2.5042358749139632e-09, 2.146487892783397e-09, 2.146487892783397e-09,
         2.146487892783397e-09, 2.146487892783397e-09, 2.146487892783397e-09,
         2.146487892783397e-09, 2.146487892783397e-09, 2.146487892783397e-09],
)

# ── Walls: two constructions, each on south + west ─────────────────────────
# Type 1 "Brick pilasters" — similar CTS to Wall CTS Data #21 (brick/insulation/
# sheathing/gyp), but this room's *actual* U-factor (0.08) overrides the
# library U (the CTS shape is "similar", the U is measured for the pilasters).
WALL_TYPE1 = dict(
    u=0.08, absorptance=0.45, ho=3.0, lwr_corr=0.0, radiant_frac=0.46,
    cts=[0.0020979830570513514, 0.0484666650946192, 0.13947948473823477,
         0.16671067818801738, 0.14859740780709063, 0.1195735818898983,
         0.09247939598966479, 0.07036168451716523, 0.05316011579275225,
         0.04004202249102873, 0.03012101478975462, 0.022644908260716294,
         0.017020043912263656, 0.012790929752244273, 0.00961218621737603,
         0.007223253397566441, 0.005427994389449501, 0.004078910083124158,
         0.0030651242349940245, 0.002303306263840173, 0.0017308329323846813,
         0.0013006443371343574, 0.0009773766147326236, 0.0007344552488964264],
    areas={"south": 60.0, "west": 40.0},
)
# Type 2 "Spandrel panels" — Wall CTS Data #1 (Spandrel Glass, R-10 Insulation
# Board, Gyp Board) — this is the same wall test_room_endtoend.py validates
# against Table 29B (west, 60 sf, U=0.077).
WALL_TYPE2 = dict(
    u=0.077, absorptance=0.9, ho=3.0, lwr_corr=0.0, radiant_frac=0.46,
    cts=[0.18011381326965886, 0.571312050211333, 0.1983841107202557,
         0.040371868811090436, 0.007901093564475303, 0.0015426289322169708,
         0.00030115271445095755, 5.879954617772677e-05, 1.1489377001965148e-05,
         2.2538850554768853e-06, 4.510103273170753e-07, 9.906835202580143e-08,
         3.036521252229502e-08, 1.695356597545702e-08, 1.5983802965047787e-08,
         1.5983802965047787e-08, 1.3700402541469532e-08, 1.3700402541469532e-08,
         1.3700402541469532e-08, 1.3700402541469532e-08, 1.3700402541469532e-08,
         1.3700402541469532e-08, 1.3700402541469532e-08, 1.3700402541469532e-08],
    areas={"south": 60.0, "west": 60.0},
)

# ── Windows: "Dbl glz, low-E, brnz 10' ohng" (Type 2 only — Type 1 has 0 sf in
# this room) — Components workbook "Window w Insde Shd and overhang" sheet
# validates this exact window (west, 40 sf) against Table 33 ────────────────
WINDOW = dict(
    width_ft=6.25, height_ft=6.4, area_sf=40.0, u=0.56,
    shgc_curve=[0.49, 0.46, 0.44, 0.39, 0.31, 0.17],   # at 0/40/50/60/70/80 deg
    shgc_diffuse=0.41,                                  # "Hemis"
    iac0=0.74, iac60=0.65, iac_diffuse=0.79,
    shaded_radiant_frac=0.46,        # shaded/diffuse split: 0.54 conv / 0.46 rad
    overhang_depth_ft=10.0, overhang_above_ft=0.0,
    areas={"south": 40.0, "west": 40.0},
)

# ── RTS zone (8 — "MW w Carpet 50% glass") — same zone used everywhere in
# this room (roof, walls, windows, internal gains) ─────────────────────────
RTS_NONSOLAR_Z8 = [0.49, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02] + [0.01] * 13 + [0.0] * 4
RTS_SOLAR_Z8 = [0.54, 0.16, 0.08, 0.04, 0.03, 0.02] + [0.01] * 13 + [0.0] * 5

# ── Internal gains: schedule = 1.0 hours 7-18 (7 am - 6 pm), else 0 ────────
OCCUPIED_SCHEDULE = [0.0] * 6 + [1.0] * 12 + [0.0] * 6

PEOPLE = dict(n=1, sensible_per_person=250.0, latent_per_person=200.0,
              radiant_frac=0.60, schedule=OCCUPIED_SCHEDULE)
LIGHTING = dict(watts=110.0, radiant_frac=0.48, ra_fraction=0.26,
                schedule=OCCUPIED_SCHEDULE)
EQUIPMENT = dict(watts=130.0, radiant_frac=0.30, schedule=OCCUPIED_SCHEDULE)

# ── Targets (Table 35 ROOM LOAD TOTALS / TOTAL BLOCK COOLING LOAD, Aug hr 14)
TARGET_ROOM_SENSIBLE = 3777.0193728594077
TARGET_RETURN_AIR_SENSIBLE = 144.60925995569187
TARGET_ROOM_LATENT = 200.0
TARGET_TOTAL_BLOCK_COOLING = 4121.6286328151
