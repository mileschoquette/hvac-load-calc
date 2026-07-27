"""Orchestrator — composes the verified stages into full component cooling loads,
and a room total. Given only raw inputs (design-day temps, location + tau,
surface geometry/construction), it runs solar -> sol-air -> CTS -> RTS for opaque
surfaces, and solar(SHGC/shading) + conduction -> RTS for windows, then adds
internal gains and sums to the room's 24-hour sensible cooling load.
"""
from __future__ import annotations

from .solar import (declination, apparent_solar_time, solar_position,
                    clear_sky, cos_incidence, surface_irradiance)
from .conduction import sol_air_temp, conduction_gain
from .fenestration import shgc_at_angle, iac_at_profile_angle
from .shading import overhang_sunlit_fraction, profile_angle
from .rts import cooling_load


def _surface_irradiance_24(n, tau_b, tau_d, lat, lon, std_meridian,
                           azimuth, tilt):
    """Per-hour (Eb, Ed, cos_incidence, total incident) for one surface."""
    out = []
    for hr in range(1, 25):
        ast = apparent_solar_time(hr, n, lon, std_meridian)
        beta, phi = solar_position(lat, declination(n), ast)
        if beta <= 0:
            out.append((0.0, 0.0, 0.0, 0.0, beta, phi))
            continue
        Eb, Ed = clear_sky(n, beta, tau_b, tau_d)
        ct = cos_incidence(beta, phi, azimuth, tilt)
        tot = surface_irradiance(Eb, Ed, beta, ct, tilt)[0]
        out.append((Eb, Ed, ct, tot, beta, phi))
    return out


def opaque_cooling_load(design_db_24, n, tau_b, tau_d, lat, lon, std_meridian,
                        azimuth, tilt, u, area, cts, rts_nonsolar, radiant_frac,
                        absorptance=0.9, ho=3.0, lwr=0.0, t_indoor=75.0):
    """24-hour cooling load (Btu/h) for a wall or roof, from raw inputs."""
    irr = _surface_irradiance_24(n, tau_b, tau_d, lat, lon, std_meridian, azimuth, tilt)
    sol = [sol_air_temp(design_db_24[i], irr[i][3], absorptance, ho, lwr)
           for i in range(24)]
    gain = conduction_gain(sol, cts, u * area, t_indoor)
    return cooling_load(gain, radiant_frac, rts_nonsolar)


def window_cooling_load(design_db_24, n, tau_b, tau_d, lat, lon, std_meridian,
                        azimuth, tilt, u, area, shgc_curve, shgc_diffuse,
                        rts_solar, rts_nonsolar, iac=1.0,
                        overhang_proj=0.0, overhang_sep=0.0, win_height=None,
                        cond_radiant_frac=0.46, t_indoor=75.0,
                        iac_profile=None, shaded_radiant_frac=0.46):
    """24-hour cooling load (Btu/h) for a window.

    Unshaded (default, `iac_profile=None`): transmitted beam is 100% radiant
    through the SOLAR RTS; diffuse + conduction go through the NONSOLAR RTS
    (ASHRAE Table 31 method).

    With an interior shade (blinds — `iac_profile=(iac0, iac60, iac_diffuse)`):
    beam+diffuse+conduction are combined and passed through the NONSOLAR RTS
    with `shaded_radiant_frac` (ASHRAE Table 32/33 method) — a shaded window's
    beam no longer reaches the floor directly, so it no longer uses the solar
    RTS series. iac_beam varies with the vertical solar profile angle Omega,
    interpolated between iac0 (Omega=0) and iac60 (Omega>=60)."""
    import math
    irr = _surface_irradiance_24(n, tau_b, tau_d, lat, lon, std_meridian, azimuth, tilt)
    beam_gain, diff_gain, cond_gain = [], [], []
    for i in range(24):
        Eb, Ed, ct, tot, beta, phi = irr[i]
        beam_surf = Eb * max(ct, 0.0)
        theta = math.degrees(math.acos(max(-1.0, min(1.0, ct)))) if tot else 90.0
        sunlit = 1.0
        if overhang_proj > 0 and win_height:
            sunlit = overhang_sunlit_fraction(beta, phi if phi is not None else 0.0,
                                               azimuth, win_height, overhang_proj, overhang_sep)
        iac_beam, iac_diff = iac, iac
        if iac_profile is not None:
            iac0, iac60, iac_diff = iac_profile
            omega = profile_angle(beta, phi if phi is not None else 0.0, azimuth)
            iac_beam = iac_at_profile_angle(omega, iac0, iac60) if omega is not None else iac0
        beam_gain.append(area * beam_surf * shgc_at_angle(theta, shgc_curve) * iac_beam * sunlit)
        # diffuse (sky+ground) = total incident minus beam-on-surface
        diff_surf = max(tot - beam_surf, 0.0)
        diff_gain.append(area * diff_surf * shgc_diffuse * iac_diff)
        cond_gain.append(u * area * (design_db_24[i] - t_indoor))
    if iac_profile is not None:
        total_gain = [beam_gain[i] + diff_gain[i] + cond_gain[i] for i in range(24)]
        return cooling_load(total_gain, shaded_radiant_frac, rts_nonsolar)
    beam_load = cooling_load(beam_gain, 1.0, rts_solar)          # transmitted beam: all radiant, solar RTS
    diff_load = cooling_load(diff_gain, 1.0, rts_nonsolar)       # diffuse: radiant, nonsolar RTS
    cond_load = cooling_load(cond_gain, cond_radiant_frac, rts_nonsolar)
    return [beam_load[i] + diff_load[i] + cond_load[i] for i in range(24)]
