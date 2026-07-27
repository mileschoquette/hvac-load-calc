"""Stage-1 acceptance check: design_day_db() must reproduce the workbook's
Table 27 design-day temperature profile for every month.

    python3 tests/test_design_day.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.design_day import design_day_db

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
# db column index per month in the Table 27 sheet (wb column is the next one)
MONTH_COL = {"Jan": 1, "Feb": 3, "Mar": 5, "Apr": 7, "May": 9, "Jun": 11,
             "Jul": 13, "Aug": 15, "Sep": 17, "Oct": 19, "Nov": 21, "Dec": 23}
TOL = 0.4  # deg F — the fractions are 2-decimal, so ~0.01*DR of slack


def main():
    df = pd.read_excel(WB, "Table 27", header=None)
    rows = df.iloc[4:28]  # hours 1..24
    worst = 0.0
    for m, c in MONTH_COL.items():
        db = [float(v) for v in rows[c].tolist()]
        tmax = max(db)
        dr = tmax - min(db)
        calc = design_day_db(tmax, dr)
        err = max(abs(a - b) for a, b in zip(calc, db))
        worst = max(worst, err)
        assert err < TOL, f"{m}: max err {err:.2f} F exceeds {TOL}"
    print(f"PASS  design-day profile reproduces Table 27 (all 12 months), "
          f"worst error {worst:.2f} F < {TOL}")


if __name__ == "__main__":
    main()
