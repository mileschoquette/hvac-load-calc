"""End-to-end check: the composed orchestrator must turn RAW INPUTS
(design-day DB + location/tau + geometry + U + CTS) into a wall's 24-hour
cooling load, reproducing Table 29B's final column (col13) — the July west
spandrel wall — WITHOUT using any of the workbook's intermediate values.

This proves solar -> sol-air -> CTS -> RTS all compose correctly.

    python3 tests/test_room_endtoend.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.room import opaque_cooling_load

COMP = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
LAT, LON, SM = 33.64, -84.43, -75.0
N_JULY = 202
TAU_B, TAU_D = 0.515, 2.066
AZ, TILT, U, AREA = 60.0, 90.0, 0.077, 60.0
RTS_NS_ZONE8 = [0.49, 0.17, 0.09, 0.05, 0.03, 0.02, 0.02] + [0.01] * 13 + [0.0] * 4
RADIANT_FRAC = 0.46


def main():
    b29 = pd.read_excel(COMP, "Table 29B", header=None).iloc[8:32]
    design_db = [float(r[3]) for _, r in b29.iterrows()]   # outside temp = design-day DB
    cts = [float(r[7]) for _, r in b29.iterrows()]         # spandrel wall CTS
    load_x = [float(r[13]) for _, r in b29.iterrows()]     # total cooling load (workbook)

    assert abs(sum(RTS_NS_ZONE8) - 1.0) < 1e-6, sum(RTS_NS_ZONE8)
    load = opaque_cooling_load(design_db, N_JULY, TAU_B, TAU_D, LAT, LON, SM,
                               AZ, TILT, U, AREA, cts, RTS_NS_ZONE8, RADIANT_FRAC,
                               absorptance=0.9, ho=3.0, lwr=0.0, t_indoor=75.0)
    err = max(abs(a - b) for a, b in zip(load, load_x))
    peak = max(load)
    peak_x = max(load_x)
    assert err < 1.5, f"end-to-end wall load off by {err:.2f} Btu/h"
    print(f"PASS  end-to-end wall (raw inputs -> cooling load) vs Table 29B — "
          f"worst {err:.2f} Btu/h; peak {peak:.1f} vs {peak_x:.1f}")


if __name__ == "__main__":
    main()
