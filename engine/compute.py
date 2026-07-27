"""Building load computation — adapts the review UI's packet (rooms_edited.json)
+ the DM-setup types/weather into the verified RTS engine, and returns per-room
and whole-building cooling AND heating loads with a full per-component breakdown
(the audit trail attached to the results).

Cooling uses the RTS method (time-series, solar, thermal mass — see engine/room.py).
Heating uses ASHRAE's simpler steady-state method (engine/heating.py): worst-case
design ΔT, straight conduction, no solar, no thermal-mass delay, and — being
conservative — no credit for internal gains, since they might not be present yet
during a cold-morning warm-up.

Approximations for arbitrary projects (documented, engineer-overridable):
  - CTS by construction weight (from the DM itype code when present, else
    U-value); RTS zone selected per-room from ASHRAE Ch.18 Table 19/20
    (exterior wall mass x floor covering x glass % of exterior wall area, or
    an interior zone with no exterior exposure) — all 24 zones, not just one.
  - clear-sky tau comes from the weather station (single representative
    summer-design pair per climate zone — see weather/stations.json) with a
    hot-humid default when the station lacks it.
  - internal gains assumed present at the design hour (flat schedule).
  - floor heat loss (heating only): a "slab"-named floor type uses the true
    ASHRAE F-factor x perimeter method (perimeter approximated from this
    room's exterior wall edges, since true slab-edge perimeter isn't
    separately tracked); other floor types use area x U.
The physics (solar / sol-air / CTS / fenestration / RTS) is the ASHRAE-verified
core; these choices only set which coefficients feed it.
"""
from __future__ import annotations
import math

from .solar import REP_N, SURFACE_AZIMUTH
from .design_day import design_day_db
from .room import opaque_cooling_load, window_cooling_load
from .rts import cooling_load
from . import coeffs
from . import gains as gain_mod
from .heating import air_loss, conduction_loss

FLAT = [1.0] * 24
DEFAULT_TAU_B, DEFAULT_TAU_D = 0.5, 2.1     # hot-humid summer default
DEFAULT_HEATING_DB = 35.0                   # generic FL-representative 99.6% design DB when no station resolved
DEFAULT_HEATING_INDOOR = 70.0

# Safety-factor (design margin) defaults, applied whenever the engineer hasn't overridden
# them — single source of truth shared with engine/report.py's footnote (which explains
# these to the engineer whenever a project is still using them unedited).
#   clg_room 5%: standard commercial-practice range (5-10%) covering as-built vs. as-designed
#     tolerance, occupancy/plug-load growth over the building's life, and this tool's own
#     documented parser-accuracy uncertainty.
#   clg_vent 0%: OA quantity is a fixed, code-derived design value (ASHRAE 62.1), not an
#     uncertain load like envelope/gains — and in FL, oversized OA equipment running at
#     part-load hurts dehumidification and raises indoor RH (the moisture-intrusion risk
#     the air-balance check exists to catch), so padding it isn't defensible.
#   htg_room 10%: same tolerance/growth logic as cooling, higher because the steady-state
#     heating method already excludes internal-gains credit and has no RTS thermal-mass
#     cushion — less margin is already "baked in" to the method itself.
#   htg_vent 0%: same reasoning as cooling vent.
DEFAULT_SAFETY_FACTORS = {"clg_room": 5.0, "clg_vent": 0.0, "htg_room": 10.0, "htg_vent": 0.0}


def _edge_len_ft(p, q, px_per_ft):
    return math.hypot(q[0] - p[0], q[1] - p[1]) / px_per_ft


def _poly_area_sqft(pts, px_per_ft):
    s = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        s += a[0] * b[1] - b[0] * a[1]
    return abs(s / 2.0) / (px_per_ft ** 2)


def _type_map(lst):
    return {t["name"]: t for t in (lst or []) if isinstance(t, dict) and "name" in t}


def compute_building(review, weather=None, month=8):
    """review = parsed rooms_edited.json dict — rooms plus wall/roof/floor/glass/
    door type libraries, safety_factors, and project_settings, everything
    buildPayload() saves. weather = {cooling_db, daily_range, lat, lon,
    std_meridian, tau_b, tau_d, indoor_db, indoor_grains, outdoor_grains}.
    Returns a results dict."""
    # Scale (px/ft) is now per-floor (different sheets in a multi-page plan set can be
    # drawn at different scales) — buildPayload() stores it in `floors`, keyed by the
    # `level` each room is tagged with. review.get("pxPerFt") is the pre-multi-floor
    # fallback for an old saved file that predates the `floors` key entirely.
    ppf_by_level = {f["level"]: f.get("pxPerFt", 18.0) for f in review.get("floors", [])}
    default_ppf = review.get("pxPerFt", 18.0)
    wtypes = _type_map(review.get("wall_types"))
    rtypes = _type_map(review.get("roof_types"))
    gtypes = _type_map(review.get("glass_types"))
    dtypes = _type_map(review.get("door_types"))
    ftypes = _type_map(review.get("floor_types"))
    openings = review.get("openings", [])

    w = weather or {}
    cdb = w.get("cooling_db", 95.0)
    dr = w.get("daily_range", 18.0)
    lat = w.get("lat", 28.5)
    lon = w.get("lon", -81.4)
    sm = w.get("std_meridian", -75.0)
    tau_b = w.get("tau_b", DEFAULT_TAU_B)
    tau_d = w.get("tau_d", DEFAULT_TAU_D)
    ti = w.get("indoor_db", 75.0)
    w_out = w.get("outdoor_grains", 120.0)
    w_in = w.get("indoor_grains", 65.0)
    to_htg = w.get("heating_db", DEFAULT_HEATING_DB)

    # Safety factors (design margin) — DM's own report has explicit, separately-editable
    # Room and Ventilation safety factors for both heating and cooling; we had no equivalent
    # knob at all. Room = envelope conduction/solar + internal gains; Vent = ventilation/
    # infiltration/exhaust-makeup outdoor air — kept separate because DM applies them
    # separately (a duct-leakage/OA-uncertainty margin is a different concern from an
    # envelope/occupancy-load margin). Falls back to DEFAULT_SAFETY_FACTORS (not 0) when a
    # project has never set these — `or 0` would be the falsy-zero trap this codebase has
    # been bitten by before: an engineer who explicitly sets e.g. clg_vent to 0 (a real,
    # deliberate override, and in fact the recommended value) must not get silently bumped
    # back up to a nonzero default just because 0 is falsy.
    sf = review.get("safety_factors") or {}

    def _sf(key):
        v = sf.get(key)
        return DEFAULT_SAFETY_FACTORS[key] if v is None else v

    sf_clg_room = 1.0 + _sf("clg_room") / 100.0
    sf_clg_vent = 1.0 + _sf("clg_vent") / 100.0
    sf_htg_room = 1.0 + _sf("htg_room") / 100.0
    sf_htg_vent = 1.0 + _sf("htg_vent") / 100.0

    db24 = design_day_db(cdb, dr)
    n = REP_N[month - 1]

    def opaque(area, u, azimuth, tilt, mass, lwr, t_indoor, rts_nonsolar):
        if area <= 0 or not u:
            return [0.0] * 24
        cts = coeffs.cts_roof(mass) if tilt == 0 else coeffs.cts_wall(mass)
        return opaque_cooling_load(db24, n, tau_b, tau_d, lat, lon, sm, azimuth,
                                   tilt, u, area, cts, rts_nonsolar,
                                   coeffs.RADIANT_FRACTION["wall"], 0.9, 3.0, lwr, t_indoor)

    any_zoned = any(r.get("zone") for r in review.get("rooms", []))
    zone_acc = {}   # zone name -> {room24, latent, area, count} — only populated if any_zoned
    # Per-floor subtotals — the exact AHU-1/AHU-2 first-floor/second-floor pattern the
    # golden-building calibration already validated (a floor is its own block load, with
    # its own peak hour, same reasoning as a zone). Only surfaced when there's more than
    # one floor, same as zones only surfacing when a room actually has one assigned.
    floor_labels = {f["level"]: f.get("label", f"Floor {f['level']}") for f in review.get("floors", [])}
    any_multifloor = len(floor_labels) > 1
    floor_acc = {}   # level -> {room24, latent, area, count, htg} — only populated if any_multifloor
    envelope = []   # every wall/roof/window/door instance — for a DM-style "Types"/"Usage" report
    air_rooms = []  # per-room air-balance rows (ventilation/exhaust/makeup CFM) — see result["air_balance"]
    vent_rows = []  # per-room ASHRAE 62.1 / FBC Mechanical ventilation-rate rows — see result["ventilation_schedule"]

    rooms_out, bldg24 = [], [0.0] * 24
    bldg_latent = 0.0
    bldg_htg = 0.0
    for r in review.get("rooms", []):
        pts = r["points"]
        ppf = ppf_by_level.get(r.get("level", 1), default_ppf)
        h = r.get("ceiling_ft") or 9.0
        wall_h = r.get("floor_to_floor_ft") or h   # exterior walls run to the structure
        # above, not just the dropped ceiling — falls back to ceiling height when
        # there's no plenum (or it isn't specified).
        area = _poly_area_sqft(pts, ppf)
        comps, room24 = [], [0.0] * 24
        htg_comps, room_htg = [], 0.0   # steady-state heating loss — no RTS/solar/gains credit
        # Ventilation/infiltration/exhaust-makeup tracked in their own accumulators (vent24,
        # vent_htg) rather than folded into room24/room_htg — the safety factor for outdoor-
        # air loads is applied separately from the envelope/internal-gains safety factor,
        # same split DM's own report uses.
        vent24 = [0.0] * 24
        vent_htg = 0.0
        L = r.get("loads", {}) or {}
        ti_htg = L.get("htg") or DEFAULT_HEATING_INDOOR
        ti_room = L.get("clg") or ti   # each room's own cooling setpoint, not one global indoor temp

        # openings on this room's edges, grouped by edge
        rops = [o for o in openings if o.get("rid") == r["id"]]

        # ---- pre-pass: gather wall specs + pick this room's RTS zone (ASHRAE
        # Ch.18 Table 19/20 — exterior wall mass x floor covering x glass % of
        # gross exterior wall area, or an interior zone with no exterior
        # exposure at all). Needs wall/window areas before any load is computed,
        # so it has to happen before the loop that builds them.
        wall_names = r.get("walls", [])
        orients = r.get("wall_orientation", [])
        wall_specs = []
        mass_area = {}
        total_ext_gross = 0.0
        for i in range(len(pts)):
            wt = wall_names[i] if i < len(wall_names) else "No wall load"
            if wt == "No wall load" or wt not in wtypes:
                continue
            u_i = wtypes[wt].get("u")
            mass_i = coeffs.mass_from_construction("wall", wtypes[wt].get("itype"), u_i)
            length_i = _edge_len_ft(pts[i], pts[(i + 1) % len(pts)], ppf)
            gross_i = length_i * wall_h
            card_i = orients[i] if i < len(orients) else "S"
            wall_specs.append({"i": i, "wt": wt, "u": u_i, "mass": mass_i,
                               "length": length_i, "gross": gross_i, "card": card_i})
            mass_area[mass_i] = mass_area.get(mass_i, 0.0) + gross_i
            total_ext_gross += gross_i
        total_window_area = sum(o.get("width", 0) * o.get("height", 0)
                                for o in rops if o.get("cat") == "window")
        dominant_mass = max(mass_area, key=mass_area.get) if mass_area else "medium"
        is_interior = total_ext_gross <= 0 and total_window_area <= 0
        if total_ext_gross > 0:
            glass_pct = total_window_area / total_ext_gross * 100
        else:
            glass_pct = 90.0 if total_window_area > 0 else 0.0
        has_carpet = r.get("carpet", True) is not False   # new per-room input; default True
        zone_id = coeffs.select_rts_zone(dominant_mass, has_carpet, glass_pct, is_interior)
        rts_nonsolar, rts_solar = coeffs.rts_series(zone_id)

        # walls (each exterior edge with a load-bearing type)
        for spec in wall_specs:
            i, wt, u, mass, length, gross, card = (spec["i"], spec["wt"], spec["u"], spec["mass"],
                                                    spec["length"], spec["gross"], spec["card"])
            opening_area = sum((o.get("width", 0) * o.get("height", 0))
                               for o in rops if o.get("edge") == i)
            net = max(gross - opening_area, 0.0)
            az = SURFACE_AZIMUTH.get(card, 0.0)
            load = opaque(net, u, az, 90.0, mass, 0.0, ti_room, rts_nonsolar)
            comps.append({"name": f"Wall {card} ({wt})", "peak": max(load), "series": load})
            room24 = [room24[k] + load[k] for k in range(24)]
            envelope.append({"category": "wall", "room": r.get("label", ""), "type": wt,
                             "orientation": card, "length_ft": round(length, 1),
                             "height_ft": round(wall_h, 1), "area_sqft": round(net, 1)})
            htg = conduction_loss(u, net, ti_htg, to_htg)
            htg_comps.append({"name": f"Wall {card} ({wt})", "btuh": round(htg, 0)})
            room_htg += htg

        # roof
        roof = r.get("roof", "No roof load")
        if roof != "No roof load" and roof in rtypes and rtypes[roof].get("u"):
            mass = coeffs.mass_from_construction("roof", rtypes[roof].get("itype"), rtypes[roof]["u"])
            load = opaque(area, rtypes[roof]["u"], 0.0, 0.0, mass, 7.0, ti_room, rts_nonsolar)
            comps.append({"name": f"Roof ({roof})", "peak": max(load), "series": load})
            room24 = [room24[k] + load[k] for k in range(24)]
            envelope.append({"category": "roof", "room": r.get("label", ""), "type": roof,
                             "area_sqft": round(area, 1)})
            htg = conduction_loss(rtypes[roof]["u"], area, ti_htg, to_htg)
            htg_comps.append({"name": f"Roof ({roof})", "btuh": round(htg, 0)})
            room_htg += htg

        # floor (not a cooling load here — ground/slab is close to indoor temp in
        # summer — but a real heating loss path that was never wired to anything
        # before, despite floor types being fully editable in the review UI).
        # A slab-on-grade loses heat mainly at its exposed edge, so ASHRAE uses
        # F-factor x perimeter, not U x area — a "slab"-named floor type's u
        # field is (and was already, in the seeded defaults: 0.73/0.54 is
        # implausibly high for an area-based U but exactly right as an
        # F-factor) treated as F-factor x this room's exterior perimeter,
        # approximated from its exterior wall edges (already computed above
        # for RTS zone selection) since true slab-edge perimeter isn't
        # separately tracked. Non-slab floor types (e.g. over unconditioned
        # space) keep the area-based method.
        floor = r.get("floor", "No floor load")
        if floor != "No floor load" and floor in ftypes and ftypes[floor].get("u"):
            ft = ftypes[floor]
            is_slab = "slab" in floor.lower() or "slab" in (ft.get("description") or "").lower()
            perimeter_ft = sum(spec["length"] for spec in wall_specs)
            if is_slab and perimeter_ft > 0:
                htg = conduction_loss(ft["u"], perimeter_ft, ti_htg, to_htg)
                htg_comps.append({"name": f"Floor ({floor}, F-factor x {perimeter_ft:.0f} ft perimeter)",
                                  "btuh": round(htg, 0)})
            else:
                htg = conduction_loss(ft["u"], area, ti_htg, to_htg)
                htg_comps.append({"name": f"Floor ({floor})", "btuh": round(htg, 0)})
            room_htg += htg

        # windows & doors
        for o in rops:
            i = o.get("edge", 0)
            card = orients[i] if i < len(orients) else "S"
            az = SURFACE_AZIMUTH.get(card, 0.0)
            a = o.get("width", 0) * o.get("height", 0)
            if o.get("cat") == "window":
                g = gtypes.get(o.get("type"), {})
                u = g.get("u", 0.5)
                shgc = g.get("shgc", 0.4)
                curve = [shgc, shgc * 0.94, shgc * 0.90, shgc * 0.80, shgc * 0.63, shgc * 0.35]
                iac_profile = (o.get("iac0", 0.7), o.get("iac60", 0.6),
                               o.get("iac_diffuse", 0.7)) if o.get("shaded") else None
                load = window_cooling_load(db24, n, tau_b, tau_d, lat, lon, sm, az, 90.0,
                                           u, a, curve, shgc * 0.84, rts_solar,
                                           rts_nonsolar, iac=1.0,
                                           overhang_proj=o.get("overhang_proj") or 0.0,
                                           overhang_sep=o.get("overhang_sep") or 0.0,
                                           win_height=o.get("height"), t_indoor=ti_room,
                                           iac_profile=iac_profile)
                comps.append({"name": f"Window {card}", "peak": max(load), "series": load})
                room24 = [room24[k] + load[k] for k in range(24)]
                envelope.append({"category": "window", "room": r.get("label", ""),
                                 "type": o.get("type", ""), "orientation": card,
                                 "area_sqft": round(a, 1), "shaded": bool(o.get("shaded"))})
                htg = conduction_loss(u, a, ti_htg, to_htg)   # conduction only — no solar credit for heating
                htg_comps.append({"name": f"Window {card}", "btuh": round(htg, 0)})
                room_htg += htg
            else:  # door — conduction only
                u = dtypes.get(o.get("type"), {}).get("u", 0.6)
                gain = [u * a * (db24[k] - ti_room) for k in range(24)]
                load = cooling_load(gain, coeffs.RADIANT_FRACTION["wall"], rts_nonsolar)
                comps.append({"name": f"Door {card}", "peak": max(load), "series": load})
                room24 = [room24[k] + load[k] for k in range(24)]
                envelope.append({"category": "door", "room": r.get("label", ""),
                                 "type": o.get("type", ""), "orientation": card,
                                 "area_sqft": round(a, 1)})
                htg = conduction_loss(u, a, ti_htg, to_htg)
                htg_comps.append({"name": f"Door {card}", "btuh": round(htg, 0)})
                room_htg += htg

        # internal gains
        lpd, epd, occ = L.get("lpd", 0) or 0, L.get("epd", 0) or 0, L.get("occ", 0) or 0
        equip_latent = L.get("equip_latent", 0) or 0   # DM's equipment.latent_btuh — cooking/
        # dishwashing/etc. gear captured on the DM side but never read anywhere until now;
        # latent was previously just occupants + outdoor-air moisture.
        if lpd:
            g = gain_mod.lighting(lpd * area, FLAT)
            load = cooling_load(g, coeffs.RADIANT_FRACTION["lighting"], rts_nonsolar)
            comps.append({"name": "Lighting", "peak": max(load), "series": load})
            room24 = [room24[k] + load[k] for k in range(24)]
        if epd:
            g = gain_mod.equipment(epd * area, FLAT)
            load = cooling_load(g, coeffs.RADIANT_FRACTION["equipment"], rts_nonsolar)
            comps.append({"name": "Equipment", "peak": max(load), "series": load})
            room24 = [room24[k] + load[k] for k in range(24)]
        if occ:
            g = gain_mod.people_sensible(occ, 250.0, FLAT)
            load = cooling_load(g, coeffs.RADIANT_FRACTION["people"], rts_nonsolar)
            comps.append({"name": "People (sensible)", "peak": max(load), "series": load})
            room24 = [room24[k] + load[k] for k in range(24)]

        # ventilation + infiltration (sensible added to the series at outdoor DB)
        vent = L.get("vent", 0) or 0
        inf_ach = L.get("inf", 0) or 0
        inf_cfm = inf_ach * area * h / 60.0
        vent_inf_cfm = vent + inf_cfm

        # ASHRAE 62.1 / FBC Mechanical ventilation schedule row — Rp x Pz + Ra x Az = Vbz.
        # Rp/Ra are the room type's own rates (seeded into loads.vent_rp/vent_ra alongside
        # the summed loads.vent); Vbz shown is the room's actual vent CFM (which may have
        # been manually overridden), not a recompute from Rp/Ra, so this matches what the
        # calc actually uses rather than silently diverging from it. Every room appears,
        # including zero-OA-rate ones (e.g. restrooms handled by exhaust, not an OA rate) —
        # matches a real DM/FBC ventilation schedule, which lists every space.
        vent_rows.append({"room": r.get("label", ""), "room_type": r.get("type", "") or "—",
                          "rp": L.get("vent_rp", 0) or 0, "pz": occ,
                          "ra": L.get("vent_ra", 0) or 0, "az": round(area, 0),
                          "vbz": round(vent, 0)})
        if vent_inf_cfm:
            sens = [1.1 * vent_inf_cfm * max(db24[k] - ti_room, 0.0) for k in range(24)]
            comps.append({"name": "Ventilation/Infiltration (sensible)", "peak": max(sens), "series": sens})
            vent24 = [vent24[k] + sens[k] for k in range(24)]
            htg = air_loss(vent_inf_cfm, ti_htg, to_htg)
            htg_comps.append({"name": "Ventilation/Infiltration", "btuh": round(htg, 0)})
            vent_htg += htg

        # exhaust makeup air (kitchen hood, restroom, general, etc.) — only the
        # portion assumed replaced with OUTDOOR air adds a new load; transfer
        # air (this engine's default, and the user's stated usual design) is
        # presumed already-conditioned air moved from elsewhere in the
        # building, so it's excluded unless the engineer sets a nonzero
        # makeup_pct for a real dedicated-MAU design.
        exh_cfm = L.get("exh_cfm", 0) or 0
        makeup_pct = L.get("makeup_pct", 0) or 0
        exh_oa_cfm = exh_cfm * makeup_pct / 100.0
        if exh_oa_cfm:
            sens = [1.1 * exh_oa_cfm * max(db24[k] - ti_room, 0.0) for k in range(24)]
            comps.append({"name": f"Exhaust Makeup ({makeup_pct:.0f}% of {exh_cfm:.0f} CFM)",
                         "peak": max(sens), "series": sens})
            vent24 = [vent24[k] + sens[k] for k in range(24)]
            htg = air_loss(exh_oa_cfm, ti_htg, to_htg)
            htg_comps.append({"name": "Exhaust Makeup", "btuh": round(htg, 0)})
            vent_htg += htg

        oa = vent_inf_cfm + exh_oa_cfm

        # air balance row — designed (not uncontrolled) airflows only:
        # ventilation + exhaust-makeup are outdoor air IN; exhaust is the FULL
        # exhausted CFM OUT regardless of how much of it is made up (the fan
        # pulls all of it; only the makeup portion is NEW outdoor air replacing
        # it — the rest is transfer air, a wash at the building level since it
        # moves within the building rather than crossing the envelope).
        # Infiltration is deliberately excluded: it's a symptom of imbalance,
        # not a designed, reliable air source.
        air_rooms.append({"room": r.get("label", ""), "zone": r.get("zone", ""),
                          "vent_cfm": round(vent, 0), "exh_cfm": round(exh_cfm, 0),
                          "makeup_pct": makeup_pct, "makeup_oa_cfm": round(exh_oa_cfm, 0),
                          "oa_in_cfm": round(vent + exh_oa_cfm, 0),
                          "net_cfm": round(vent + exh_oa_cfm - exh_cfm, 0)})

        # latent components — itemized the same way sensible is, instead of one opaque
        # number. Ventilation/infiltration and exhaust-makeup latent were already being
        # calculated and folded into the total before this change (0.68 x outdoor-air CFM x
        # grains difference) — they just had no visible line item, indistinguishable from
        # people/equipment latent in the report.
        latent_comps = []
        people_latent = occ * 200.0
        if people_latent:
            latent_comps.append({"name": "People (latent)", "btuh": people_latent})
        if equip_latent:
            latent_comps.append({"name": "Equipment (latent)", "btuh": equip_latent})
        vent_inf_latent = 0.68 * vent_inf_cfm * max(w_out - w_in, 0.0)
        if vent_inf_latent:
            latent_comps.append({"name": "Ventilation/Infiltration (latent)", "btuh": vent_inf_latent})
        exh_latent = 0.68 * exh_oa_cfm * max(w_out - w_in, 0.0)
        if exh_latent:
            latent_comps.append({"name": f"Exhaust Makeup (latent, {makeup_pct:.0f}% of {exh_cfm:.0f} CFM)",
                                 "btuh": exh_latent})
        # room (people + equipment) vs outdoor-air (ventilation/infiltration/exhaust-makeup) —
        # same room/vent split as sensible, so each gets its own safety factor below.
        latent_room = people_latent + equip_latent
        latent_vent = vent_inf_latent + exh_latent

        # Safety factor — DM applies a separate design margin to the Room load (envelope +
        # internal gains) and the Ventilation load (outdoor air); shown as its own line item,
        # same as every other add-on load in this engine, rather than silently baked in.
        if sf_clg_room != 1.0:
            extra = [room24[k] * (sf_clg_room - 1.0) for k in range(24)]
            comps.append({"name": f"Safety Factor - Room ({(sf_clg_room - 1.0) * 100:.0f}%)",
                         "peak": max(extra), "series": extra})
            if latent_room:
                latent_comps.append({"name": f"Safety Factor - Room, Latent ({(sf_clg_room - 1.0) * 100:.0f}%)",
                                     "btuh": latent_room * (sf_clg_room - 1.0)})
        if sf_clg_vent != 1.0:
            extra = [vent24[k] * (sf_clg_vent - 1.0) for k in range(24)]
            comps.append({"name": f"Safety Factor - Ventilation ({(sf_clg_vent - 1.0) * 100:.0f}%)",
                         "peak": max(extra), "series": extra})
            if latent_vent:
                latent_comps.append({"name": f"Safety Factor - Ventilation, Latent ({(sf_clg_vent - 1.0) * 100:.0f}%)",
                                     "btuh": latent_vent * (sf_clg_vent - 1.0)})
        room24 = [room24[k] * sf_clg_room + vent24[k] * sf_clg_vent for k in range(24)]
        latent = latent_room * sf_clg_room + latent_vent * sf_clg_vent
        bldg_latent += latent

        if sf_htg_room != 1.0:
            htg_comps.append({"name": f"Safety Factor - Room ({(sf_htg_room - 1.0) * 100:.0f}%)",
                              "btuh": round(room_htg * (sf_htg_room - 1.0), 0)})
        if sf_htg_vent != 1.0:
            htg_comps.append({"name": f"Safety Factor - Ventilation ({(sf_htg_vent - 1.0) * 100:.0f}%)",
                              "btuh": round(vent_htg * (sf_htg_vent - 1.0), 0)})
        room_htg = room_htg * sf_htg_room + vent_htg * sf_htg_vent

        peak_hr = max(range(24), key=lambda k: room24[k])
        rooms_out.append({
            "id": r["id"], "label": r.get("label", ""), "type": r.get("type", ""),
            "zone": r.get("zone", ""),
            "floor_label": floor_labels.get(r.get("level", 1), "") if any_multifloor else "",
            "area_sqft": round(area, 1), "ceiling_ft": h,
            "floor_to_floor_ft": r.get("floor_to_floor_ft") or None,
            "sensible_btuh": round(room24[peak_hr], 0),
            "latent_btuh": round(latent, 0),
            "total_btuh": round(room24[peak_hr] + latent, 0),
            "peak_hour": peak_hr + 1,
            "components": [{"name": c["name"], "btuh": round(c["peak"], 0)} for c in comps],
            "latent_components": [{"name": c["name"], "btuh": round(c["btuh"], 0)} for c in latent_comps],
            "rts_zone": f"{zone_id} ({coeffs.ZONE_NAME[zone_id]})",
            "heating_btuh": round(room_htg, 0),
            "heating_components": htg_comps,
            "loads": {"occupants": occ, "lpd": lpd, "epd": epd, "equip_latent": equip_latent or None,
                      "vent_cfm": vent, "inf_ach": inf_ach, "exh_cfm": exh_cfm or None,
                      "makeup_pct": makeup_pct, "htg": L.get("htg"), "clg": L.get("clg"), "rh": L.get("rh")},
        })
        bldg24 = [bldg24[k] + room24[k] for k in range(24)]
        bldg_htg += room_htg

        if any_zoned:
            # A zone's own peak hour doesn't necessarily match any single room's
            # (or the whole building's) — same diversity effect the golden
            # building-block calibration confirmed, just one level down: sum the
            # zone's rooms hour-by-hour and let ITS OWN peak fall out.
            z = zone_acc.setdefault(r.get("zone") or "Unassigned",
                                    {"room24": [0.0] * 24, "latent": 0.0, "area": 0.0, "count": 0, "htg": 0.0})
            z["room24"] = [z["room24"][k] + room24[k] for k in range(24)]
            z["latent"] += latent
            z["area"] += area
            z["count"] += 1
            z["htg"] += room_htg

        if any_multifloor:
            fl = floor_acc.setdefault(r.get("level", 1),
                                      {"room24": [0.0] * 24, "latent": 0.0, "area": 0.0, "count": 0, "htg": 0.0})
            fl["room24"] = [fl["room24"][k] + room24[k] for k in range(24)]
            fl["latent"] += latent
            fl["area"] += area
            fl["count"] += 1
            fl["htg"] += room_htg

    peak_hr = max(range(24), key=lambda k: bldg24[k])
    sens = bldg24[peak_hr]
    total = sens + bldg_latent
    total_sqft = sum(rm["area_sqft"] for rm in rooms_out) or 1

    zones_out = None
    if any_zoned:
        zones_out = []
        for name, z in zone_acc.items():
            zpk = max(range(24), key=lambda k: z["room24"][k])
            zsens = z["room24"][zpk]
            ztotal = zsens + z["latent"]
            zones_out.append({
                "name": name, "room_count": z["count"], "area_sqft": round(z["area"], 0),
                "sensible_btuh": round(zsens, 0), "latent_btuh": round(z["latent"], 0),
                "total_btuh": round(ztotal, 0), "tons": round(ztotal / 12000.0, 1),
                "peak_hour": zpk + 1, "heating_btuh": round(z["htg"], 0),
            })

    floors_out = None
    if any_multifloor:
        floors_out = []
        for level, fl in sorted(floor_acc.items()):
            fpk = max(range(24), key=lambda k: fl["room24"][k])
            fsens = fl["room24"][fpk]
            ftotal = fsens + fl["latent"]
            floors_out.append({
                "name": floor_labels.get(level, f"Floor {level}"), "room_count": fl["count"],
                "area_sqft": round(fl["area"], 0),
                "sensible_btuh": round(fsens, 0), "latent_btuh": round(fl["latent"], 0),
                "total_btuh": round(ftotal, 0), "tons": round(ftotal / 12000.0, 1),
                "peak_hour": fpk + 1, "heating_btuh": round(fl["htg"], 0),
            })

    # Air balance — building must never take in less outdoor air than it
    # exhausts, or it runs net-negative pressure (in FL's climate, that means
    # drawing in unconditioned humid air through every crack/gap instead of
    # keeping it out — moisture intrusion risk, not just comfort). Only
    # designed airflows count (ventilation + exhaust-makeup in, exhaust out);
    # infiltration is excluded since it's a symptom of imbalance, not a
    # reliable source. Zone subtotals (same zones as the load blocks) help
    # find which system is actually driving a building-level deficit.
    bldg_vent = sum(a["vent_cfm"] for a in air_rooms)
    bldg_makeup_oa = sum(a["makeup_oa_cfm"] for a in air_rooms)
    bldg_exhaust = sum(a["exh_cfm"] for a in air_rooms)
    air_zones_out = None
    if any_zoned:
        az_acc = {}
        for a in air_rooms:
            z = az_acc.setdefault(a["zone"] or "Unassigned",
                                  {"vent": 0.0, "makeup_oa": 0.0, "exh": 0.0})
            z["vent"] += a["vent_cfm"]; z["makeup_oa"] += a["makeup_oa_cfm"]; z["exh"] += a["exh_cfm"]
        air_zones_out = [{"name": name, "vent_cfm": round(z["vent"], 0),
                          "makeup_oa_cfm": round(z["makeup_oa"], 0), "exh_cfm": round(z["exh"], 0),
                          "oa_in_cfm": round(z["vent"] + z["makeup_oa"], 0),
                          "net_cfm": round(z["vent"] + z["makeup_oa"] - z["exh"], 0)}
                         for name, z in az_acc.items()]
    air_balance = {
        "rooms": air_rooms, "zones": air_zones_out,
        "vent_cfm": round(bldg_vent, 0), "makeup_oa_cfm": round(bldg_makeup_oa, 0),
        "exh_cfm": round(bldg_exhaust, 0), "oa_in_cfm": round(bldg_vent + bldg_makeup_oa, 0),
        "net_cfm": round(bldg_vent + bldg_makeup_oa - bldg_exhaust, 0),
    }

    ventilation_schedule = {
        "rows": vent_rows,
        "total_occupants": round(sum(v["pz"] for v in vent_rows), 1),
        "total_oa_cfm": round(sum(v["vbz"] for v in vent_rows), 0),
    }

    result = {
        "design": {"station": w.get("station", "—"), "month": month, "peak_hour": peak_hr + 1,
                   "cooling_db": cdb, "daily_range": dr, "indoor_db": ti,
                   "heating_db": to_htg, "tau_b": tau_b, "tau_d": tau_d, "latitude": lat,
                   "method": "ASHRAE Handbook-Fundamentals Ch.18, Radiant Time Series (RTS) cooling; "
                             "steady-state heating",
                   "sf_clg_room_pct": (sf_clg_room - 1.0) * 100, "sf_clg_vent_pct": (sf_clg_vent - 1.0) * 100,
                   "sf_htg_room_pct": (sf_htg_room - 1.0) * 100, "sf_htg_vent_pct": (sf_htg_vent - 1.0) * 100,
                   # Where these design conditions actually came from — a resolved bundled
                   # station, manual per-project values, or (flagged loudly, not silently)
                   # this tool's Florida-representative fallback. See loadcalc._weather().
                   "weather_source": w.get("weather_source", "unknown"),
                   "cooling_percentile": w.get("cooling_percentile"),
                   "heating_percentile": w.get("heating_percentile"),
                   "elevation_ft": w.get("elevation_ft"),
                   "lon_warning": w.get("lon_warning")},
        "rooms": rooms_out,
        "building": {
            "sensible_btuh": round(sens, 0), "latent_btuh": round(bldg_latent, 0),
            "total_btuh": round(total, 0), "tons": round(total / 12000.0, 1),
            "sqft_per_ton": round(total_sqft / (total / 12000.0), 0) if total else 0,
            "peak_month": month, "peak_hour": peak_hr + 1, "area_sqft": round(total_sqft, 0),
            "heating_btuh": round(bldg_htg, 0),
        },
        "envelope": envelope,
        "types": {"wall": review.get("wall_types", []), "roof": review.get("roof_types", []),
                  "door": review.get("door_types", []), "glass": review.get("glass_types", [])},
        "air_balance": air_balance,
        "ventilation_schedule": ventilation_schedule,
    }
    if zones_out is not None:
        result["zones"] = zones_out
    if floors_out is not None:
        result["floors"] = floors_out
    return result
