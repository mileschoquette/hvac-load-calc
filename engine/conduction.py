"""Stage 3 — opaque conduction (walls & roofs): sol-air temperature -> CTS ->
conductive heat gain (ASHRAE Ch.18).

Sol-air temperature rolls the incident solar and long-wave exchange into an
equivalent outdoor temperature:

    Te = To + alpha * Et / ho - epsilon*dR/ho

The conductive heat gain at the interior is the 24-term conduction time series
(CTS) applied to the sol-air-vs-room temperature difference; the CTS coefficients
(one set per construction, summing to 1) come from the workbook's CTS data.
"""
from __future__ import annotations

OUTSIDE_CONDUCTANCE = 3.0     # ho, Btu/h.ft2.F (summer)
ABSORPTANCE_DARK = 0.9        # alpha for a dark surface
LWR_ROOF = 20.0 / 3.0          # epsilon*dR/ho, dR=20F (ASHRAE horizontal-surface default), ho=3
LWR_WALL = 0.0                # vertical surfaces


def sol_air_temp(t_out, e_surface, absorptance=ABSORPTANCE_DARK,
                 ho=OUTSIDE_CONDUCTANCE, lwr_corr=LWR_WALL):
    """Sol-air temperature (deg F) for one hour/surface."""
    return t_out + absorptance * e_surface / ho - lwr_corr


def conduction_gain(sol_air_24, cts, ua, t_indoor):
    """Hourly conductive heat gain (Btu/h) at the interior via the CTS.

    sol_air_24  24 sol-air temps, hour 1..24 (the design day repeats).
    cts         24 coefficients c_0..c_23 (c_0 = current hour, c_1 = 1 h ago, ...).
    ua          U * A (Btu/h.F).
    t_indoor    room air temperature (deg F).
    """
    return [ua * sum(cts[n] * (sol_air_24[(theta - n) % 24] - t_indoor)
                     for n in range(24))
            for theta in range(24)]
