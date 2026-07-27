"""Stage 4 — fenestration heat gain (ASHRAE Ch.18).

Window heat gain has three parts:
  beam solar    = A * (Eb*cos theta) * SHGC(theta) * IAC_beam
  diffuse solar = A * E_diffuse_surface * SHGC_diffuse * IAC_diffuse
  conduction    = U * A * (t_out - t_indoor)              (windows: ~instantaneous)

SHGC(theta) is the angular solar heat gain coefficient, piecewise-linear over the
ASHRAE breakpoints 0/40/50/60/70/80 deg (0 at 90). SHGC_diffuse is the glazing's
hemispherical value. IAC (interior attenuation coefficient) = 1 with no inside
shade; overhang/fin external shading is applied to the beam separately (Stage 4b).
"""
from __future__ import annotations
import math

SHGC_ANGLES = [0.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]


def shgc_at_angle(theta_deg: float, shgc_curve) -> float:
    """SHGC at incidence angle theta. shgc_curve = SHGC at [0,40,50,60,70,80];
    SHGC(90)=0. Linear interpolation between breakpoints."""
    pts = list(shgc_curve) + [0.0]
    if theta_deg <= 0:
        return pts[0]
    if theta_deg >= 90:
        return 0.0
    for i in range(len(SHGC_ANGLES) - 1):
        a, b = SHGC_ANGLES[i], SHGC_ANGLES[i + 1]
        if a <= theta_deg <= b:
            return pts[i] + (theta_deg - a) / (b - a) * (pts[i + 1] - pts[i])
    return 0.0


def iac_at_profile_angle(omega_deg, iac0, iac60):
    """Interior attenuation coefficient (indoor shade, e.g. venetian blinds) at
    vertical profile angle Omega: linear between IAC(0) and IAC(60), held flat
    beyond 60 deg (ASHRAE Ch.15 Table 14 convention — IAC is tabulated only at
    profile angle 0 and 60)."""
    if omega_deg <= 0:
        return iac0
    if omega_deg >= 60:
        return iac60
    return iac0 + (omega_deg / 60.0) * (iac60 - iac0)


def window_heat_gain(area, u, shgc_curve, shgc_diffuse, beam_surface,
                     diffuse_surface, t_out, t_indoor, theta_deg,
                     iac_beam=1.0, iac_diffuse=1.0):
    """Return (total, beam, diffuse, conduction) window heat gain, Btu/h.

    beam_surface     beam irradiance on the surface, Eb*cos theta (Btu/h.ft2)
    diffuse_surface  diffuse (sky + ground) on the surface (Btu/h.ft2)
    """
    q_beam = area * max(beam_surface, 0.0) * shgc_at_angle(theta_deg, shgc_curve) * iac_beam
    q_diff = area * diffuse_surface * shgc_diffuse * iac_diffuse
    q_cond = u * area * (t_out - t_indoor)
    return q_beam + q_diff + q_cond, q_beam, q_diff, q_cond
