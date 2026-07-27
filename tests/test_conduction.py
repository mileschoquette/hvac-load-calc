"""Stage-3 check: sol-air temperature and the CTS conductive heat gain must
reproduce the workbook's Table 29B (July, the Spandrel-Glass wall: U=0.077,
A=60 ft2, azimuth 60, vertical). Sol-air uses alpha=0.9, ho=3.0, no long-wave
term (vertical). CTS coefficients are taken from the sheet's own column.

    python3 tests/test_conduction.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.conduction import sol_air_temp, conduction_gain

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
UA = 0.077 * 60      # U * A for the example wall
T_INDOOR = 75.0


def main():
    df = pd.read_excel(WB, "Table 29B", header=None)
    rows = df.iloc[8:32]                       # hours 1..24
    Et = [float(r[2]) for _, r in rows.iterrows()]     # total surface irradiance
    To = [float(r[3]) for _, r in rows.iterrows()]     # outside air temp
    sol_x = [float(r[4]) for _, r in rows.iterrows()]  # sol-air temp (workbook)
    raw_x = [float(r[6]) for _, r in rows.iterrows()]  # raw U*A*(Te-Trc), pre-CTS
    cts = [float(r[7]) for _, r in rows.iterrows()]    # CTS coefficients c_0..c_23
    heat_x = [float(r[8]) for _, r in rows.iterrows()] # CTS-convolved heat gain

    # (1) sol-air temperature
    sol = [sol_air_temp(To[i], Et[i], absorptance=0.9, ho=3.0, lwr_corr=0.0)
           for i in range(24)]
    e_sol = max(abs(a - b) for a, b in zip(sol, sol_x))
    assert e_sol < 0.2, f"sol-air off by {e_sol:.3f} F"
    assert abs(sum(cts) - 1.0) < 1e-3, f"CTS coeffs sum to {sum(cts):.4f}, not 1"

    # (2) CTS conductive heat gain (use the workbook's sol-air so this isolates
    #     the convolution)
    q = conduction_gain(sol_x, cts, UA, T_INDOOR)
    e_q = max(abs(a - b) for a, b in zip(q, heat_x))
    assert e_q < 1.0, f"CTS heat gain off by {e_q:.2f} Btu/h"

    print(f"PASS  conduction vs Table 29B — sol-air worst {e_sol:.3f} F, "
          f"CTS heat gain worst {e_q:.2f} Btu/h (peak {max(heat_x):.0f})")


if __name__ == "__main__":
    main()
