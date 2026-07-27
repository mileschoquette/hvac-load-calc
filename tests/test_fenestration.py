"""Stage-4 check: window solar (beam + diffuse) and conduction heat gain must
reproduce the workbook's Table 30 (July, no blinds/overhang). Example window:
A=40 ft2, U=0.56, glazing ID 5d (dbl BRZ/CLR) SHGC {0:.49,40:.46,50:.44,60:.39,
70:.31,80:.17}, hemispherical diffuse SHGC 0.41. Uses the sheet's own incident
irradiance (beam-surface, diffuse) so this isolates the fenestration model.

    python3 tests/test_fenestration.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.fenestration import shgc_at_angle, window_heat_gain

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
AREA, U = 40.0, 0.56
SHGC_CURVE = [0.49, 0.46, 0.44, 0.39, 0.31, 0.17]
SHGC_DIFF = 0.41
T_INDOOR = 75.0


def main():
    df = pd.read_excel(WB, "Table 30", header=None)
    w = {"shgc": 0.0, "beam": 0.0, "diff": 0.0, "cond": 0.0, "tot": 0.0}
    for _, r in df.iloc[8:32].iterrows():
        theta = float(r[3])              # incidence angle
        beam_surf = float(r[4])          # Eb*cos theta on surface
        shgc_x = float(r[5])             # workbook beam SHGC(theta)
        q_beam_x = float(r[7])           # beam solar heat gain
        diff_surf = float(r[12])         # sub-total diffuse on surface
        q_diff_x = float(r[14])          # diffuse solar heat gain
        t_out = float(r[15])
        q_cond_x = float(r[16])          # conduction heat gain
        tot_x = float(r[17])             # total window heat gain

        w["shgc"] = max(w["shgc"], abs(shgc_at_angle(theta, SHGC_CURVE) - shgc_x))
        tot, q_beam, q_diff, q_cond = window_heat_gain(
            AREA, U, SHGC_CURVE, SHGC_DIFF, beam_surf, diff_surf, t_out, T_INDOOR, theta)
        w["beam"] = max(w["beam"], abs(q_beam - q_beam_x))
        w["diff"] = max(w["diff"], abs(q_diff - q_diff_x))
        w["cond"] = max(w["cond"], abs(q_cond - q_cond_x))
        w["tot"] = max(w["tot"], abs(tot - tot_x))

    assert w["shgc"] < 0.005, w
    assert w["beam"] < 3.0, w
    assert w["diff"] < 2.0, w
    assert w["cond"] < 1.0, w
    assert w["tot"] < 4.0, w
    print(f"PASS  fenestration vs Table 30 — worst: SHGC(θ) {w['shgc']:.4f}, "
          f"beam {w['beam']:.2f}, diffuse {w['diff']:.2f}, cond {w['cond']:.2f}, "
          f"total {w['tot']:.2f} Btu/h")


if __name__ == "__main__":
    main()
