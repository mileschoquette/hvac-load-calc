"""Extract ASHRAE RTS golden answers from the Fundamentals Ch.18 example workbooks.

These workbooks are the acceptance-test suite for our RTS load-calc engine. Rather
than hand-transcribe numbers (error-prone), this regenerates the golden fixture
straight from the .xls so the targets always match the published source.

    pip install "xlrd>=2.0.1" pandas
    python validation/extract_golden.py        # writes validation/ashrae_golden.json
"""
from __future__ import annotations
import json, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOADS = ROOT / "RTS Cooling and Heating Loads Examples - IP Units rev 2017-04-06.xls"

# Integration cases: sheet name -> friendly id. Each sheet shares the same layout.
INTEGRATION = {
    "Table 35 Single Room Example":   "single_room",
    "Table 37 AHU-1 1st Floor Block ": "ahu1_first_floor",
    "Table 38 AHU-2 2nd Floor Block":  "ahu2_second_floor",
    "Table 39 Building Block Load":    "building",
}


def _nums(row) -> list[float]:
    """Numeric cells in a row, in order."""
    return [v for v in row.tolist() if isinstance(v, (int, float)) and pd.notna(v)]


def _find(df, needle: str):
    """First row whose joined text contains `needle`; returns (idx, row) or None."""
    for i, row in df.iterrows():
        text = " ".join(str(v) for v in row.tolist() if pd.notna(v))
        if needle in text:
            return i, row
    return None


def extract_case(df) -> dict:
    out: dict = {}
    # ROOM LOAD TOTALS = [room_sens, ret_air_sens, room_latent, room_heating]
    hit = _find(df, "ROOM LOAD TOTALS")
    if hit:
        n = _nums(hit[1])
        if len(n) >= 4:
            out["room_sensible_btuh"], out["return_air_sensible_btuh"], \
                out["room_latent_btuh"], out["room_heating_btuh"] = n[:4]
    # TOTAL BLOCK COOLING LOAD = [btuh, tons, sf/ton]
    hit = _find(df, "TOTAL BLOCK COOLING LOAD")
    if hit:
        n = _nums(hit[1])
        if n:
            out["total_block_cooling_btuh"] = n[0]
            if len(n) >= 2:
                out["total_block_cooling_tons"] = round(n[1], 2)
            if len(n) >= 3:
                out["sf_per_ton"] = round(n[2])
    # TOT HEATING,btuh =
    hit = _find(df, "TOT HEATING")
    if hit:
        n = _nums(hit[1])
        if n:
            out["total_heating_btuh"] = n[-1]
    # Block peak month/hour (the rows under "Peak Block Load Occurs:")
    pk = _find(df, "Peak Block Load Occurs")
    if pk:
        i = pk[0]
        for j in range(i, min(i + 4, len(df))):
            text = " ".join(str(v) for v in df.iloc[j].tolist() if pd.notna(v))
            n = _nums(df.iloc[j])
            if text.strip().startswith("Month:") and n:
                out["peak_month"] = int(n[0])
            if text.strip().startswith("Hour:") and n:
                out["peak_hour"] = int(n[0])
    return out


def main() -> None:
    if not LOADS.exists():
        raise SystemExit(f"Workbook not found: {LOADS}")
    xl = pd.ExcelFile(LOADS)
    cases = {}
    for sheet, key in INTEGRATION.items():
        cases[key] = extract_case(xl.parse(sheet, header=None))
    fixture = {
        "source": LOADS.name,
        "standard": "ASHRAE Handbook-Fundamentals Ch.18 (RTS), IP units, rev 2017-04-06",
        "building": "ASHRAE Example Office Building, Atlanta GA (Burdell Engineering)",
        "note": "Integration targets. Component-level targets (Tables 28-33) extracted per-test.",
        "cases": cases,
    }
    out = HERE / "ashrae_golden.json"
    out.write_text(json.dumps(fixture, indent=2))
    print(f"wrote {out}")
    print(json.dumps(fixture["cases"], indent=2))


if __name__ == "__main__":
    main()
