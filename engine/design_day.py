"""Stage 1 — design-day dry-bulb temperature profile (ASHRAE Ch.14/18).

Given a month's design dry-bulb (the peak, reached ~hour 15) and its mean daily
range, the 24-hour design-day temperature is

    t(hr) = peak_db - daily_range * f(hr)

where f is the ASHRAE fraction-of-daily-range: 1.0 at the pre-dawn minimum
(~hour 5) and 0.0 at the afternoon peak (hours 14-15). These multipliers are the
ones implied by the RTS example workbook's Table 27 (derived from it, consistent
across every month to within rounding).
"""
from __future__ import annotations

# ASHRAE fraction of daily range, hours 1..24 (local standard time).
DAILY_RANGE_FRACTION = [
    0.88, 0.92, 0.95, 0.98, 1.00, 0.98, 0.91, 0.74, 0.55, 0.38, 0.23, 0.13,
    0.05, 0.00, 0.00, 0.06, 0.14, 0.24, 0.39, 0.50, 0.59, 0.68, 0.75, 0.82,
]


def design_day_db(peak_db: float, daily_range: float) -> list[float]:
    """24 hourly design dry-bulb temps (deg F), hour 1..24, for one design day.

    peak_db      the month's design dry-bulb (e.g. the 1% cooling DB) = the max.
    daily_range  the mean coincident daily range for that month (deg F).
    """
    return [peak_db - daily_range * f for f in DAILY_RANGE_FRACTION]
