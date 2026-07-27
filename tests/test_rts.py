"""Stage-6 check: the radiant/convective split + RTS must turn the wall's
conductive heat GAIN into its cooling LOAD, reproducing Table 29B (July wall):
gain (col8) -> radiant 0.46 / convective 0.54 -> RTS zone type 8 (col11) ->
radiant cooling load (col12) -> + convective = total cooling load (col13).

    python3 tests/test_rts.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.rts import split, apply_rts, cooling_load

WB = ROOT / "RTS Components Examples - IP Units rev 2017-04-06.xls"
RADIANT_FRAC = 0.46


def main():
    df = pd.read_excel(WB, "Table 29B", header=None)
    rows = df.iloc[8:32]
    gain = [float(r[8]) for _, r in rows.iterrows()]       # heat gain
    rts = [float(r[11]) for _, r in rows.iterrows()]       # RTS zone type 8
    radload_x = [float(r[12]) for _, r in rows.iterrows()] # radiant cooling load
    total_x = [float(r[13]) for _, r in rows.iterrows()]   # total cooling load

    assert abs(sum(rts) - 1.0) < 1e-3, f"RTS sums to {sum(rts):.4f}"
    rad, _ = split(gain, RADIANT_FRAC)
    radload = apply_rts(rad, rts)
    total = cooling_load(gain, RADIANT_FRAC, rts)
    e_rad = max(abs(a - b) for a, b in zip(radload, radload_x))
    e_tot = max(abs(a - b) for a, b in zip(total, total_x))
    assert e_rad < 1.0, f"radiant cooling load off by {e_rad:.2f}"
    assert e_tot < 1.0, f"total cooling load off by {e_tot:.2f}"
    print(f"PASS  RTS aggregation vs Table 29B — radiant load worst {e_rad:.2f}, "
          f"total cooling load worst {e_tot:.2f} Btu/h (peak {max(total_x):.0f})")


if __name__ == "__main__":
    main()
