"""Sanity check for the steady-state heating loss formulas — plain conduction
and sensible-air arithmetic, not an ASHRAE table lookup (there's no time-series
or solar involved in the heating design method, so there's nothing to validate
against a workbook the way the cooling stages are).

    python3 tests/test_heating.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.heating import air_loss, conduction_loss


def main():
    # U=0.1, 100 sf, 70F indoor, 30F outdoor -> 0.1*100*40 = 400
    assert abs(conduction_loss(0.1, 100, 70, 30) - 400.0) < 1e-9
    # negative delta (outdoor warmer than indoor) -> no loss, not negative
    assert conduction_loss(0.1, 100, 70, 80) == 0.0
    # no U-value / no area -> 0, not an error
    assert conduction_loss(None, 100, 70, 30) == 0.0
    assert conduction_loss(0.1, 0, 70, 30) == 0.0
    # 500 cfm, 70F indoor, 20F outdoor -> 1.1*500*50 = 27,500
    assert abs(air_loss(500, 70, 20) - 27500.0) < 1e-9
    assert air_loss(0, 70, 20) == 0.0
    print("PASS  heating steady-state conduction + air-loss formulas")


if __name__ == "__main__":
    main()
