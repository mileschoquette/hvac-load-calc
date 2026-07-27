"""Golden engine calibration, part 2: the whole-building blocks (Tables 37, 38,
39 — AHU-1 first floor, AHU-2 second floor, and the combined building). Same
method as `test_golden_room.py`, just bigger geometry (a floor/building's
aggregated wall+window areas and people/lighting/equipment totals instead of
one room's), no return-air credit, plus a ventilation (outside-air) load on
top. See `validation/golden_building_inputs.py` for what's derived vs. given
and why.

Each block's own peak month/hour is taken as given (matching HANDOFF's/the
single-room test's scope) rather than searched across all 12 design days. One
wrinkle AHU-1 has that the single room didn't: its own room-sensible peak
(month 9 hour 12) doesn't coincide with the TOTAL BLOCK's peak (month 8 hour
15) — outside-air sensible follows the outdoor dry-bulb curve and shifts the
combined peak to a different month, so room-sensible is checked at its own
peak month/hour, and the total (the actual design figure) at the block's.

    python3 tests/test_golden_building.py
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
from validation import golden_building_inputs as B
from validation.reconstruct import reconstruct

MONTH_WEATHER = {
    7: (B.COOLING_DB_JUL, B.DAILY_RANGE_JUL, B.TAU_B_JUL, B.TAU_D_JUL),
    8: (G.COOLING_DB_AUG, G.DAILY_RANGE_AUG, G.TAU_B_AUG, G.TAU_D_AUG),
    9: (B.COOLING_DB_SEP, B.DAILY_RANGE_SEP, B.TAU_B_SEP, B.TAU_D_SEP),
}


def _run(block, month):
    """Reconstruct the block's 24-hour room/RA/latent series for one month."""
    cooling_db, daily_range, tau_b, tau_d = MONTH_WEATHER[month]
    db24 = design_day_db(cooling_db, daily_range)
    n = REP_N[month - 1]
    room24, ra24, latent24 = reconstruct(
        db24, n, tau_b, tau_d, G.LAT, G.LON, G.STD_MERIDIAN, G.T_INDOOR,
        block["walls"], block["roof"], block["windows"], block["people"],
        block["lighting"], block["equipment"], G.RTS_NONSOLAR_Z8, G.RTS_SOLAR_Z8)
    return db24, room24, ra24, latent24


def pct(actual, target):
    return abs(actual - target) / target * 100 if target else abs(actual)


def run_block(name, block):
    print(f"-- {name} --")

    db24, room24, ra24, latent24 = _run(block, block["month"])

    # Room-sensible alone, at ITS OWN published peak month/hour (may be a
    # different month than the block's own TOTAL peak — see AHU-1).
    rs_month, rs_hour = block["room_sensible_month"], block["room_sensible_hour"]
    rs_room24 = room24 if rs_month == block["month"] else _run(block, rs_month)[1]
    room_sens = rs_room24[rs_hour - 1]
    print(f"  room sensible (month {rs_month} hr {rs_hour}): {room_sens:12.2f}  "
          f"vs {block['target_room_sensible']:12.2f}  "
          f"({pct(room_sens, block['target_room_sensible']):.2f}%)")
    assert pct(room_sens, block["target_room_sensible"]) < 0.1, \
        f"{name}: room sensible off by >0.1%"

    # TOTAL BLOCK COOLING LOAD, at the block's own published peak month/hour.
    idx = block["peak_hour"] - 1
    fixed = block["oa_latent"] + block["hp_to_supply_air"] + block["hp_to_chw"]
    total24 = [room24[i] + ra24[i] + latent24[i]
               + 1.1 * block["oa_cfm"] * max(db24[i] - G.T_INDOOR, 0.0) + fixed
               for i in range(24)]
    peak_hr = max(range(24), key=lambda i: total24[i])
    total = total24[idx]

    print(f"  peak hour found: {peak_hr + 1} (published: {block['peak_hour']})")
    print(f"  TOTAL BLOCK:    {total:12.2f}  vs {block['target_total_block']:12.2f}  "
          f"({pct(total, block['target_total_block']):.2f}%)")

    assert peak_hr == idx, f"{name}: peak hour {peak_hr + 1} != published {block['peak_hour']}"
    err = pct(total, block["target_total_block"])
    assert err < 0.1, f"{name}: total block cooling load off by {err:.2f}% (>0.1%)"
    return err


def main():
    errs = [run_block(name, block) for name, block in B.BLOCKS.items()]
    print(f"PASS  golden building reconstruction (Tables 37/38/39) — worst {max(errs):.2f}%")


if __name__ == "__main__":
    main()
