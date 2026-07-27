"""Stage-2a check: solar geometry constants (equation of time, declination, Eo)
must reproduce the workbook's 'Solar Table' (ASHRAE Table 2) for every month.

    python3 tests/test_solar.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.solar import REP_N, declination, equation_of_time, eo

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"


def main():
    df = pd.read_excel(WB, "Solar Table", header=None)
    rows = df.iloc[8:20]  # months 1..12: cols EQT=1, Decln=2, Eo(IP)=3
    we = wd = wo = 0.0
    for i, (_, r) in enumerate(rows.iterrows()):
        eqt_x, dec_x, eo_x = float(r[1]), float(r[2]), float(r[3])
        n = REP_N[i]
        we = max(we, abs(equation_of_time(n) - eqt_x))
        wd = max(wd, abs(declination(n) - dec_x))
        wo = max(wo, abs(eo(n) - eo_x))
    assert we < 0.6, f"equation of time off by {we:.2f} min"
    assert wd < 0.6, f"declination off by {wd:.2f} deg"
    assert wo < 1.5, f"Eo off by {wo:.2f} Btu/h.ft2"
    print(f"PASS  solar geometry vs Solar Table — worst: EQT {we:.2f} min, "
          f"decl {wd:.2f} deg, Eo {wo:.2f} Btu/h.ft2")


if __name__ == "__main__":
    main()
