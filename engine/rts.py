"""Stage 6 — heat gain to cooling load via the Radiant Time Series (ASHRAE Ch.18).

Each hourly heat gain is split into a convective part (an instantaneous cooling
load) and a radiant part. The radiant part is absorbed by the room mass and
released over the following hours; the 24-term radiant time series (RTS) converts
the radiant gain history into the current radiant cooling load:

    load(theta) = convective(theta) + sum_n r_n * radiant(theta - n)

Two RTS sets exist: a SOLAR series for beam solar transmitted through glazing
(strikes the floor directly) and a NONSOLAR series for everything else
(conduction, internal gains, diffuse/absorbed solar). Coefficients depend on the
zone's construction and come from the workbook's RTS data.
"""
from __future__ import annotations


def split(gain_24, radiant_fraction):
    """Return (radiant_24, convective_24)."""
    rad = [g * radiant_fraction for g in gain_24]
    conv = [g * (1.0 - radiant_fraction) for g in gain_24]
    return rad, conv


def apply_rts(radiant_24, rts):
    """Radiant cooling load from a radiant-gain history via the 24-term RTS
    (the design day repeats, so the history wraps)."""
    return [sum(rts[n] * radiant_24[(theta - n) % 24] for n in range(24))
            for theta in range(24)]


def cooling_load(gain_24, radiant_fraction, rts):
    """Hourly cooling load (Btu/h) for one heat-gain component."""
    rad, conv = split(gain_24, radiant_fraction)
    rl = apply_rts(rad, rts)
    return [conv[theta] + rl[theta] for theta in range(24)]
