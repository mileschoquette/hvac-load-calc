"""Stage 4b — external shading (overhang) on a vertical window (ASHRAE Ch.18).

An overhang of projection P a distance S above the window casts a shadow whose
vertical drop across the glass is P*tan(Omega), where Omega is the vertical solar
profile angle:  tan(Omega) = tan(beta) / cos(gamma),  gamma = solar - surface
azimuth. Returns the sunlit fraction of the glass (1 = fully sunlit, applied to
the BEAM component only; diffuse is treated as unshaded here).
"""
from __future__ import annotations
import math


def profile_angle(alt_deg, sol_az_deg, surf_az_deg):
    """Vertical solar profile angle Omega (deg) on a surface of azimuth surf_az:
    tan(Omega) = tan(beta) / cos(gamma). Returns None when the sun is behind the
    wall (gamma beyond +/-90 deg) or below the horizon."""
    gamma = math.radians(sol_az_deg - surf_az_deg)
    if math.cos(gamma) <= 0 or alt_deg <= 0:
        return None
    return math.degrees(math.atan(math.tan(math.radians(alt_deg)) / math.cos(gamma)))


def overhang_sunlit_fraction(alt_deg, sol_az_deg, surf_az_deg,
                             win_height, projection, sep_above=0.0):
    """sep_above = gap from the top of the glass up to the overhang (ft)."""
    omega = profile_angle(alt_deg, sol_az_deg, surf_az_deg)
    if omega is None:
        return 0.0                                   # sun behind the wall
    shadow_drop = projection * math.tan(math.radians(omega))
    shaded = max(0.0, shadow_drop - sep_above)       # onto the glass, from the top
    sunlit = max(0.0, win_height - shaded)
    return min(1.0, sunlit / win_height)
