"""Golden engine calibration: reconstruct ASHRAE's single-room example (Table 35,
Room 215B) end-to-end from RAW INPUTS ONLY (geometry, U-values, CTS, RTS,
schedules, window shading/overhang) and reproduce the published room totals.

This is the outstanding validation flagged in HANDOFF.md — the per-stage physics
(design-day, solar, sol-air/CTS, fenestration, RTS) and a single wall are already
verified; this wires all 10 of the room's components (roof, 2 wall types x 2
exposures, 1 window type x 2 exposures, people/lighting/equipment) together and
checks the composed result against the workbook's published answer.

    python3 tests/test_golden_room.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.design_day import design_day_db
from engine.solar import REP_N
from validation import golden_room_215b_inputs as G
from validation.reconstruct import reconstruct


def main():
    db24 = design_day_db(G.COOLING_DB_AUG, G.DAILY_RANGE_AUG)
    n = REP_N[G.MONTH - 1]

    walls = [(wtype, az, wtype["areas"][exposure])
             for wtype in (G.WALL_TYPE1, G.WALL_TYPE2)
             for exposure, az in (("south", G.AZ_SOUTH), ("west", G.AZ_WEST))]
    roof = (G.ROOF, G.ROOF["area_sf"], G.ROOF["ra_fraction"])
    windows = [(G.WINDOW, az, G.WINDOW["areas"][exposure])
               for exposure, az in (("south", G.AZ_SOUTH), ("west", G.AZ_WEST))]

    room24, ra24, latent24 = reconstruct(
        db24, n, G.TAU_B_AUG, G.TAU_D_AUG, G.LAT, G.LON, G.STD_MERIDIAN,
        G.T_INDOOR, walls, roof, windows, G.PEOPLE, G.LIGHTING, G.EQUIPMENT,
        G.RTS_NONSOLAR_Z8, G.RTS_SOLAR_Z8)

    peak_hr = max(range(24), key=lambda i: room24[i])
    idx = G.PEAK_HOUR - 1

    room_sens = room24[idx]
    ra_sens = ra24[idx]
    latent = latent24[idx]
    total = room_sens + ra_sens + latent

    def pct(actual, target):
        return abs(actual - target) / target * 100

    print(f"peak hour found: {peak_hr + 1} (published: {G.PEAK_HOUR})")
    print(f"room sensible:   {room_sens:10.2f}  vs {G.TARGET_ROOM_SENSIBLE:10.2f}  "
          f"({pct(room_sens, G.TARGET_ROOM_SENSIBLE):.2f}%)")
    print(f"return air sens: {ra_sens:10.2f}  vs {G.TARGET_RETURN_AIR_SENSIBLE:10.2f}  "
          f"({pct(ra_sens, G.TARGET_RETURN_AIR_SENSIBLE):.2f}%)")
    print(f"room latent:     {latent:10.2f}  vs {G.TARGET_ROOM_LATENT:10.2f}")
    print(f"TOTAL BLOCK:     {total:10.2f}  vs {G.TARGET_TOTAL_BLOCK_COOLING:10.2f}  "
          f"({pct(total, G.TARGET_TOTAL_BLOCK_COOLING):.2f}%)")

    assert peak_hr == idx, f"peak hour {peak_hr + 1} != published {G.PEAK_HOUR}"
    assert pct(total, G.TARGET_TOTAL_BLOCK_COOLING) < 0.1, "total block cooling load off by >0.1%"
    print("PASS  golden single-room reconstruction (Table 35, Room 215B)")


if __name__ == "__main__":
    main()
