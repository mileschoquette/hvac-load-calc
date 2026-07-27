"""Stage-2b check: the clear-sky model + surface incidence must reproduce the
workbook's Table 29A hour-by-hour solar trace (July, a vertical wall of the
Atlanta example building): altitude, azimuth, air mass, beam-normal Eb, diffuse
Ed, incidence angle theta, and total irradiance incident on the surface.

The wall azimuth is 60 deg W of S (the example building is rotated 30 deg from
cardinal) -- confirmed by reproducing the sheet's own theta column across every
daylight hour, so this validates cos_incidence, not just fits it.

    python3 tests/test_solar_irradiance.py
"""
import math
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.solar import (declination, apparent_solar_time, solar_position,
                          clear_sky, cos_incidence, surface_irradiance)

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
LAT, LON, SM = 33.64, -84.43, -75.0
N_JULY = 202
TAU_B, TAU_D = 0.515, 2.066
SURF_AZ, TILT = 60.0, 90.0


def main():
    df = pd.read_excel(WB, "Table 29A", header=None)
    w = {"beta": 0.0, "phi": 0.0, "Eb": 0.0, "Ed": 0.0, "theta": 0.0, "tot": 0.0}
    n_day = 0
    for _, r in df.iloc[11:35].iterrows():
        try:
            lst = float(r[1])
        except (TypeError, ValueError):
            continue
        beta_x, phi_x, Eb_x, th_x, Ed_x, tot_x = (
            float(r[4]), float(r[5]), float(r[7]), float(r[8]), float(r[10]), float(r[15]))
        ast = apparent_solar_time(lst, N_JULY, LON, SM)
        beta, phi = solar_position(LAT, declination(N_JULY), ast)
        w["beta"] = max(w["beta"], abs(beta - beta_x))
        if beta <= 0:
            continue
        n_day += 1
        Eb, Ed = clear_sky(N_JULY, beta, TAU_B, TAU_D)
        ct = cos_incidence(beta, phi, SURF_AZ, TILT)
        theta = math.degrees(math.acos(max(-1.0, min(1.0, ct))))
        tot = surface_irradiance(Eb, Ed, beta, ct, TILT)[0]
        w["phi"] = max(w["phi"], abs(phi - phi_x))
        w["Eb"] = max(w["Eb"], abs(Eb - Eb_x))
        w["Ed"] = max(w["Ed"], abs(Ed - Ed_x))
        w["theta"] = max(w["theta"], abs(theta - th_x))
        w["tot"] = max(w["tot"], abs(tot - tot_x))

    assert w["beta"] < 0.3, w
    assert w["phi"] < 0.4, w
    assert w["Eb"] < 1.0, w
    assert w["Ed"] < 1.0, w
    assert w["theta"] < 0.5, w
    assert w["tot"] < 1.5, w
    print("PASS  clear-sky + surface irradiance vs Table 29A "
          f"({n_day} daylight hrs) -- worst: beta {w['beta']:.2f}, phi {w['phi']:.2f}, "
          f"Eb {w['Eb']:.2f}, Ed {w['Ed']:.2f}, theta {w['theta']:.2f}, "
          f"total {w['tot']:.2f} Btu/h.ft2")


if __name__ == "__main__":
    main()
