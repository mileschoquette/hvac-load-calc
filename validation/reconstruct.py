"""Shared "build a room/block from raw inputs" composition, factored out of the
single-room golden test so the whole-building block reconstructions (AHU-1,
AHU-2, Building — Tables 37/38/39) can reuse it without re-deriving the same
wall/roof/window/gains wiring three more times.
"""
from __future__ import annotations

from engine.room import opaque_cooling_load, window_cooling_load
from engine.rts import cooling_load
from engine.gains import BTU_PER_W


def reconstruct(design_db24, n, tau_b, tau_d, lat, lon, std_meridian, t_indoor,
                walls, roof, windows, people, lighting, equipment, rts_nonsolar,
                rts_solar):
    """walls: list of (construction_dict, azimuth, area).
    roof: (construction_dict, area, ra_fraction) or None.
    windows: list of (window_dict, azimuth, area).
    people/lighting/equipment: dicts with magnitude + schedule + radiant_frac
    (+ ra_fraction for lighting).

    Returns (room24, ra24, latent24) — room and return-air sensible cooling
    load series, and the (unlagged) latent gain series.
    """
    room24, ra24 = [0.0] * 24, [0.0] * 24

    for comp, azimuth, area in walls:
        load = opaque_cooling_load(
            design_db24, n, tau_b, tau_d, lat, lon, std_meridian, azimuth, 90.0,
            comp["u"], area, comp["cts"], rts_nonsolar, comp["radiant_frac"],
            comp["absorptance"], comp["ho"], comp["lwr_corr"], t_indoor)
        room24 = [room24[i] + load[i] for i in range(24)]

    if roof is not None:
        comp, area, ra_fraction = roof
        load = opaque_cooling_load(
            design_db24, n, tau_b, tau_d, lat, lon, std_meridian, 0.0, 0.0,
            comp["u"], area, comp["cts"], rts_nonsolar, comp["radiant_frac"],
            comp["absorptance"], comp["ho"], comp["lwr_corr"], t_indoor)
        room24 = [room24[i] + load[i] * (1 - ra_fraction) for i in range(24)]
        ra24 = [ra24[i] + load[i] * ra_fraction for i in range(24)]

    for win, azimuth, area in windows:
        load = window_cooling_load(
            design_db24, n, tau_b, tau_d, lat, lon, std_meridian, azimuth, 90.0,
            win["u"], area, win["shgc_curve"], win["shgc_diffuse"], rts_solar,
            rts_nonsolar, overhang_proj=win["overhang_depth_ft"],
            overhang_sep=win["overhang_above_ft"], win_height=win["height_ft"],
            t_indoor=t_indoor,
            iac_profile=(win["iac0"], win["iac60"], win["iac_diffuse"]),
            shaded_radiant_frac=win["shaded_radiant_frac"])
        room24 = [room24[i] + load[i] for i in range(24)]

    P = people
    people_gain = [P["n"] * P["sensible_per_person"] * s for s in P["schedule"]]
    people_load = cooling_load(people_gain, P["radiant_frac"], rts_nonsolar)
    room24 = [room24[i] + people_load[i] for i in range(24)]
    latent24 = [P["n"] * P["latent_per_person"] * s for s in P["schedule"]]

    L = lighting
    light_gain = [L["watts"] * BTU_PER_W * s for s in L["schedule"]]
    light_load = cooling_load(light_gain, L["radiant_frac"], rts_nonsolar)
    ra_fraction = L.get("ra_fraction", 0.0)
    room24 = [room24[i] + light_load[i] * (1 - ra_fraction) for i in range(24)]
    ra24 = [ra24[i] + light_load[i] * ra_fraction for i in range(24)]

    E = equipment
    equip_gain = [E["watts"] * BTU_PER_W * s for s in E["schedule"]]
    equip_load = cooling_load(equip_gain, E["radiant_frac"], rts_nonsolar)
    room24 = [room24[i] + equip_load[i] for i in range(24)]

    return room24, ra24, latent24
