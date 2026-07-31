"""Results report — renders a self-contained HTML load-calc report styled after
Design Master's HVAC Loads report (serif type, black-bordered tables, the same
mainHeader/otherHeader/otherData section structure) so it reads like the
document engineers already know, instead of a modern dashboard. Built entirely
from `compute_building`'s result — nothing here is computed, only formatted.

Sections DM has that this doesn't: Psychrometrics — that needs an actual
psychrometric process-point calc this engine doesn't do. Supply Air
Requirements and a Duct/Diffuser Schedule ARE included (see `_supply_air`/
`_duct_schedule`), built from `engine.ducts`'s auto-sizing.
"""
from __future__ import annotations

import re

from .coeffs import mass_from_construction
from .compute import DEFAULT_SAFETY_FACTORS

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def _slug(text):
    """Anchor id for a section heading, derived from its title text."""
    return "sec-" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _ft_in(ft):
    if ft is None:
        return "—"
    total_in = round(ft * 12)
    f, inch = divmod(total_in, 12)
    return f"{f}'-{inch}\""


def _num(v, decimals=0):
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"


def _pct(part, whole):
    return f"{(part / whole * 100):.0f}%" if whole else "0%"


def _hour12(hour_1_24):
    h = ((hour_1_24 - 1) % 24) + 1
    ampm = "a.m." if h < 12 or h == 24 else "p.m."
    h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    return f"{h12}:00 {ampm}"


def _table(main_header, col_count, subtitle=None, col_headers=None, rows=""):
    title = main_header if not subtitle else f"{main_header}<br><span class='regularSize'>{subtitle}</span>"
    head = f"<th colspan='{col_count}' class='mainHeader' id='{_slug(main_header)}'>{title}</th>"
    cols = ("<tr>" + "".join(f"<th class='otherHeader'>{c}</th>" for c in col_headers) + "</tr>") if col_headers else ""
    body = rows if rows.strip() else f"<tr><td class='otherData' colspan='{col_count}'><i>none</i></td></tr>"
    return f"<table><thead><tr>{head}</tr>{cols}</thead>{body}</table>"


def _td(v, right=True):
    inner = v if not right else f"<div class='right'>{v}</div>"
    return f"<td class='otherData'>{inner}</td>"


def _project_info(design, project_name, generated, building):
    sf_clg = f"{design['sf_clg_room_pct']:.0f}% / {design['sf_clg_vent_pct']:.0f}%"
    sf_htg = f"{design['sf_htg_room_pct']:.0f}% / {design['sf_htg_vent_pct']:.0f}%"
    clg_pctl = design.get("cooling_percentile") or "1%"
    htg_pctl = design.get("heating_percentile") or "99.6%"
    source = design.get("weather_source", "unknown")
    source_html = (f"<b class='flag-warn'>⚠ {source}</b>" if source.startswith("UNVERIFIED")
                   else source)
    elev_row = ""
    if design.get("elevation_ft") is not None:
        elev_str = f"{design['elevation_ft']:,.0f} ft"
        elev_row = (f"<tr><th class='project'>Elevation:</th>{_td(elev_str)}"
                    f"<th class='project'></th><td class='otherData'></td></tr>")
    lon_warning_row = ""
    if design.get("lon_warning"):
        lon_warning_row = (
            f"<tr><th class='project flag-warn' style='background:#fdecea'>⚠ Longitude Sign Error:</th>"
            f"<td class='otherData flag-warn' colspan='3' style='background:#fdecea'>"
            f"<b>{design['lon_warning']}</b></td></tr>")
    rows = "".join([
        f"<tr><th class='project'>Project Name:</th><td class='otherData' colspan='3'>{project_name or '—'}</td></tr>",
        f"<tr><th class='project'>Weather Station:</th><td class='otherData' colspan='3'>{design['station']}</td></tr>",
        f"<tr><th class='project'>Design Source:</th><td class='otherData' colspan='3'>{source_html}</td></tr>",
        lon_warning_row,
        f"<tr><th class='project'>Calculation Date:</th><td class='otherData' colspan='3'>{generated or '—'}</td></tr>",
        f"<tr><th class='project'>Default Cooling Temperature:</th>{_td(str(design['indoor_db']) + '°F')}"
        f"<th class='project'>Method:</th><td class='otherData'>{design['method']}</td></tr>",
        "<tr><th colspan='4' class='mainHeader' style='font-size:14px'>Design Conditions</th></tr>",
        f"<tr><th class='project'>OSA Design Dry Bulb ({clg_pctl}):</th>{_td(str(design['cooling_db']) + '°F')}"
        f"<th class='project'>Latitude:</th>{_td(str(design['latitude']) + '° N')}</tr>",
        f"<tr><th class='project'>OSA Daily Range:</th>{_td(str(design['daily_range']) + '°F')}"
        f"<th class='project'>Clear-sky τb / τd:</th>{_td(str(design['tau_b']) + ' / ' + str(design['tau_d']))}</tr>",
        f"<tr><th class='project'>Design Month:</th>{_td(MONTHS[design['month']])}"
        f"<th class='project'>Design Peak Hour:</th>{_td(_hour12(design['peak_hour']))}</tr>",
        f"<tr><th class='project'>OSA Heating Design Dry Bulb ({htg_pctl}):</th>{_td(str(design['heating_db']) + '°F')}"
        f"<th class='project'></th><td class='otherData'></td></tr>",
        elev_row,
        f"<tr><th class='project'>Cooling Safety Factor (Room / Vent):</th>{_td(sf_clg)}"
        f"<th class='project'>Heating Safety Factor (Room / Vent):</th>{_td(sf_htg)}</tr>",
        f"<tr><th class='project'>Building Peak:</th><td class='otherData' colspan='3'>"
        f"{MONTHS[building['peak_month']]}, {_hour12(building['peak_hour'])} — {building['tons']:,.1f} tons "
        f"({building['total_btuh']:,.0f} Btu/h, {building['area_sqft']:,.0f} ft²)</td></tr>",
    ])
    return (f"<table><thead><tr><th colspan='4' class='mainHeader' id='{_slug('Project Information')}'>"
            f"Project Information</th></tr></thead>{rows}</table>")


def _types_table(title, types, category):
    cols = ["Type", "U-Value", "Mass Class", "Color", "Description"]
    rows = ""
    for t in types:
        if not isinstance(t, dict) or not t.get("name") or t["name"].lower().startswith("no "):
            continue   # the editor's "No wall/roof/... load" placeholder isn't a real material
        mass = mass_from_construction(category, t.get("itype"), t.get("u")) if category in ("wall", "roof") else "—"
        color = "Dark" if t.get("dark") else "Light"
        rows += ("<tr>" + _td(t["name"], False) + _td(_num(t.get("u"), 3)) + _td(mass.title() if mass != "—" else "—")
                 + _td(color, False) + _td(t.get("description", "—"), False) + "</tr>")
    return _table(title, len(cols), col_headers=cols, rows=rows)


def _usage_table(title, envelope, category, extra_cols=()):
    """extra_cols: list of (column label, envelope dict key) pairs, e.g. [("Length","length_ft")]."""
    base_cols = ["Room", "Area"] + [label for label, _ in extra_cols] + ["Type", "Facing"]
    rows = ""
    for e in envelope:
        if e["category"] != category:
            continue
        extras = "".join(_td(f"{e[key]:,.1f} ft" if key in e else "—") for _, key in extra_cols)
        rows += ("<tr>" + _td(e["room"], False) + _td(f"{e['area_sqft']:,.0f} ft²") + extras
                 + _td(e["type"], False) + _td(e.get("orientation", "—"), False)
                 + (_td("Yes" if e.get("shaded") else "No", False) if category == "window" else "") + "</tr>")
    cols = base_cols + (["Shaded"] if category == "window" else [])
    return _table(title, len(cols), col_headers=cols, rows=rows)


def _ventilation_schedule(vs):
    # Styled after a Florida Building Code, Mechanical / ASHRAE 62.1 ventilation schedule
    # (Rp/Pz/Ra/Az/Vbz columns, occupant + total-OA footer row) — the same layout as DM's.
    cols = ["Room", "Room Type", "Rp (cfm/person)", "Pz (people)", "Ra (cfm/ft²)", "Az (ft²)", "Vbz (CFM)"]
    rows = ""
    for v in vs["rows"]:
        rows += ("<tr>" + _td(v["room"], False) + _td(v["room_type"], False)
                  + _td(_num(v["rp"], 2)) + _td(_num(v["pz"], 1))
                  + _td(_num(v["ra"], 2)) + _td(f"{v['az']:,.0f}")
                  + _td(f"{v['vbz']:,.0f}") + "</tr>")
    rows += (f"<tr><td class='otherData' colspan='5'><div class='right'><b>{_num(vs['total_occupants'], 1)} Occupants</b></div></td>"
             f"<td class='otherData' colspan='2'><div class='right'><b>Total Min. OA {vs['total_oa_cfm']:,.0f} CFM</b></div></td></tr>")
    subtitle = "ASHRAE 62.1 Ventilation Rate Procedure: Rp x Pz + Ra x Az = Vbz — matches the FBC Mechanical ventilation schedule format"
    return _table("Ventilation Schedule", len(cols), subtitle, cols, rows)


def _room_info(rooms):
    show_floor = any(r.get("floor_label") for r in rooms)
    cols = (["Number"] + (["Floor"] if show_floor else [])
            + ["Type", "Area", "Ceiling Ht.", "Floor-to-Floor", "Occupants",
               "Lighting", "Equipment", "Equip. Latent", "Ventilation", "Infiltration", "Exhaust",
               "Cooling / Heating SP", "RH"])
    rows = ""
    for r in rooms:
        L = r["loads"]
        exh = f"{_num(L.get('exh_cfm'))} CFM ({_num(L.get('makeup_pct')) if L.get('exh_cfm') else '—'}% OA)" \
              if L.get("exh_cfm") else "—"
        rows += ("<tr>" + _td(r["label"], False) + (_td(r.get("floor_label") or "—", False) if show_floor else "")
                  + _td(r["type"] or "—", False)
                  + _td(f"{r['area_sqft']:,.0f} ft²") + _td(_ft_in(r["ceiling_ft"]))
                  + _td(_ft_in(r.get("floor_to_floor_ft")) if r.get("floor_to_floor_ft") else "same")
                  + _td(_num(L.get("occupants"))) + _td(f"{_num(L.get('lpd'), 2)} W/ft²")
                  + _td(f"{_num(L.get('epd'), 2)} W/ft²")
                  + _td(f"{_num(L.get('equip_latent'))} Btu/h" if L.get("equip_latent") else "—")
                  + _td(f"{_num(L.get('vent_cfm'))} CFM")
                  + _td(f"{_num(L.get('inf_ach'), 2)} AC/hr") + _td(exh)
                  + _td(f"{_num(L.get('clg'), 0)}°F / {_num(L.get('htg'), 0)}°F")
                  + _td(f"{_num(L.get('rh'))}%") + "</tr>")
    return _table("Room Information", len(cols), col_headers=cols, rows=rows)


def _cooling_detail(rooms, design_month):
    cols = ["Room", "RTS Zone", "Peak Month", "Peak Hour", "Sensible", "Latent", "Total",
            "Sensible Breakdown (Btu/h / % of room total)",
            "Latent Breakdown (Btu/h / % of room latent)"]
    rows = ""
    for r in rooms:
        comp_str = " · ".join(f"{c['name']}: {c['btuh']:,.0f} ({_pct(c['btuh'], r['total_btuh'])})"
                               for c in r["components"]) or "no loads assigned"
        latent_str = " · ".join(f"{c['name']}: {c['btuh']:,.0f} ({_pct(c['btuh'], r['latent_btuh'])})"
                                for c in r.get("latent_components", [])) or "no latent load"
        rows += ("<tr>" + _td(r["label"], False) + _td(r.get("rts_zone", "—"), False)
                  + _td(MONTHS[design_month], False) + _td(_hour12(r["peak_hour"]))
                  + _td(f"{r['sensible_btuh']:,.0f}") + _td(f"{r['latent_btuh']:,.0f}")
                  + _td(f"{r['total_btuh']:,.0f}") + _td(comp_str, False) + _td(latent_str, False) + "</tr>")
    subtitle = ("(Btu/h / % of room total or room latent) — RTS Zone selected per ASHRAE Ch.18 Table 19/20 "
                "from exterior wall mass x floor covering x glass % of exterior wall area")
    return _table("Cooling Load Details — Room", len(cols), subtitle, cols, rows)


def _heating_detail(rooms, heating_db):
    cols = ["Room", "Heating Btu/h", "Component Breakdown (Btu/h / % of room total)"]
    rows = ""
    for r in rooms:
        comp_str = " · ".join(f"{c['name']}: {c['btuh']:,.0f} ({_pct(c['btuh'], r['heating_btuh'])})"
                               for c in r["heating_components"]) or "no loads assigned"
        rows += ("<tr>" + _td(r["label"], False) + _td(f"{r['heating_btuh']:,.0f}")
                  + _td(comp_str, False) + "</tr>")
    subtitle = (f"Design outdoor {heating_db:.0f}°F, steady-state — no solar, thermal-mass delay, "
                "or internal-gains credit (worst-case sizing)")
    return _table("Heating Load Details — Room", len(cols), subtitle, cols, rows)


def _supply_air(supply_air):
    cols = ["Room", "Area", "Sensible Btu/h", "Supply ΔT", "Required CFM"]
    rows = ""
    for s in supply_air:
        rows += ("<tr>" + _td(s["room"], False) + _td(f"{s['area_sqft']:,.0f} ft²")
                  + _td(f"{s['sensible_btuh']:,.0f}") + _td(f"{s['delta_t']:.0f}°F")
                  + _td(f"{s['cfm']:,.0f}") + "</tr>")
    total_cfm = sum(s["cfm"] for s in supply_air)
    rows += (f"<tr><td class='otherData' colspan='4'><div class='right'><b>Total Required Supply CFM</b></div></td>"
             f"<td class='otherData'><div class='right'><b>{total_cfm:,.0f}</b></div></td></tr>")
    subtitle = ("CFM = Sensible Btu/h / (1.1 x Supply ΔT) — the standard sensible-air-quantity formula; "
                "not a full psychrometric process")
    return _table("Supply Air Requirements", len(cols), subtitle, cols, rows)


def _suggested_model(row, model_key="matched_model"):
    """"Manufacturer Model" (equipment) or "Manufacturer ModelLine Size" (diffuser) when a
    catalog match exists, "—" when it doesn't (no match found, or a kind — DH — that never
    gets one). `clamped` isn't shown here; it's informational, not load-bearing for the tag."""
    manufacturer = row.get("matched_manufacturer")
    if not manufacturer:
        return "—"
    if model_key == "matched_model":
        return f"{manufacturer} {row.get('matched_model')}"
    return f"{manufacturer} {row.get('matched_model_line')} {row.get('matched_size')}"


def _static_pressure_cell(e):
    """Formats an AH row's computed static pressure, bold-red-flagged (same convention as
    `_project_info`'s UNVERIFIED-weather-source flag) when it exceeds the matched unit's
    rated ESP — "—" when no static pressure could be computed at all (e.g. missing floor
    scale for real-length calc, or no ductwork drawn downstream of this AH)."""
    sp = e.get("static_pressure_in_wg")
    if sp is None:
        return "—"
    text = f"{sp:.2f}\" wg"
    return f"<b class='flag-warn'>⚠ {text} (exceeds rated ESP)</b>" if e.get("esp_exceeded") else text


def _duct_schedule(ducts):
    out = ""
    if ducts.get("equipment"):
        cols = ["Tag", "Kind", "Supply CFM", "Return CFM", "Static Pressure", "Suggested Model"]
        rows = "".join("<tr>" + _td(e["tag"], False) + _td(e["kind"], False)
                        + _td(f"{e['supply_cfm']:,.0f}" if e.get("supply_cfm") is not None else "—")
                        + _td(f"{e['return_cfm']:,.0f}" if e.get("return_cfm") is not None else "—")
                        + _td(_static_pressure_cell(e))
                        + _td(_suggested_model(e), False) + "</tr>"
                        for e in ducts["equipment"])
        out += _table("Air Handling Equipment", len(cols), col_headers=cols, rows=rows)
    cols = ["Duct", "Size", "CFM", "Zone"]
    rows = "".join("<tr>" + _td(r["kind"], False) + _td(r["size"], False)
                    + _td(f"{r['cfm']:,.0f}" if r.get("cfm") is not None else "—") + _td(r["zone"], False) + "</tr>"
                    for r in ducts.get("schedule", []))
    out += _table("Duct Schedule", len(cols), col_headers=cols, rows=rows)
    cols = ["Tag", "Type", "Size", "CFM", "Zone", "Suggested Model"]
    rows = "".join("<tr>" + _td(t["tag"], False) + _td(t["kind"], False) + _td(t["size"], False)
                    + _td(f"{t['cfm']:,.0f}" if t.get("cfm") is not None else "—") + _td(t["zone"], False)
                    + _td(_suggested_model(t, "matched_model_line"), False) + "</tr>"
                    for t in ducts.get("terminals", []))
    out += _table("Diffuser / Grille Schedule", len(cols), col_headers=cols, rows=rows)
    return out


def _air_balance(ab):
    net = ab["net_cfm"]
    status = ("✓ Positive — building is not under-pressured" if net >= 0 else
              "<span class='flag-warn'>⚠ NEGATIVE — more air is being exhausted than brought "
              "in as outdoor air</span>")
    summary_rows = (
        "<tr><th class='project'>Ventilation OA In:</th>" + _td(f"{ab['vent_cfm']:,.0f} CFM") +
        "<th class='project'>Exhaust Out:</th>" + _td(f"{ab['exh_cfm']:,.0f} CFM") + "</tr>"
        "<tr><th class='project'>Exhaust Makeup OA In:</th>" + _td(f"{ab['makeup_oa_cfm']:,.0f} CFM") +
        "<th class='project'>Total OA In:</th>" + _td(f"{ab['oa_in_cfm']:,.0f} CFM") + "</tr>"
        "<tr><th class='project'>Net Building Pressurization:</th>"
        f"<td class='otherData' colspan='3'><b>{net:,.0f} CFM — {status}</b></td></tr>"
    )
    summary = (f"<table><thead><tr><th colspan='4' class='mainHeader' "
               f"id='{_slug('Air Balance — Building Pressurization')}'>"
               f"Air Balance — Building Pressurization</th></tr></thead>{summary_rows}</table>")

    zones_table = ""
    if ab.get("zones"):
        zcols = ["Zone / System", "Ventilation OA", "Exhaust Makeup OA", "Total OA In", "Exhaust Out", "Net CFM"]
        zrows = ""
        for z in ab["zones"]:
            flag = "" if z["net_cfm"] >= 0 else " ⚠"
            zrows += ("<tr>" + _td(z["name"], False) + _td(f"{z['vent_cfm']:,.0f}")
                      + _td(f"{z['makeup_oa_cfm']:,.0f}") + _td(f"{z['oa_in_cfm']:,.0f}")
                      + _td(f"{z['exh_cfm']:,.0f}") + _td(f"{z['net_cfm']:,.0f}{flag}") + "</tr>")
        zones_table = _table("Air Balance — By Zone / System", len(zcols), col_headers=zcols, rows=zrows)

    cols = ["Room", "Zone", "Ventilation OA", "Exhaust", "Makeup %", "Makeup OA", "Net CFM"]
    rows = ""
    for a in ab["rooms"]:
        if not (a["vent_cfm"] or a["exh_cfm"]):
            continue
        rows += ("<tr>" + _td(a["room"], False) + _td(a["zone"] or "—", False)
                  + _td(f"{a['vent_cfm']:,.0f}") + _td(f"{a['exh_cfm']:,.0f}")
                  + _td(f"{a['makeup_pct']:,.0f}%") + _td(f"{a['makeup_oa_cfm']:,.0f}")
                  + _td(f"{a['net_cfm']:,.0f}") + "</tr>")
    note = ("A negative room total is often intentional (e.g. a kitchen held slightly negative relative to dining "
            "via transfer air) — what matters is the BUILDING total above, not any one room. Infiltration is "
            "excluded here; it's a symptom of imbalance, not a designed, reliable air source. Only exhaust entered "
            "per room is counted — any exhaust system not entered (fume hoods, dryer vents, etc.) won't appear.")
    rooms_table = (_table("Air Balance — By Room", len(cols), col_headers=cols, rows=rows)
                  + f"<p class='note'>{note}</p>")

    return summary + zones_table + rooms_table


# Rationale text for each safety-factor dimension's DEFAULT value (engine/compute.py's
# DEFAULT_SAFETY_FACTORS) — shown as a report footnote, but only for whichever dimension(s)
# a project is still using unedited, so an engineer who overrides one doesn't get told a
# rationale for a number they already rejected.
_SF_RATIONALE = {
    "clg_room": ("Cooling — Room", "Standard commercial-practice range (5–10%) covering as-built vs. "
                 "as-designed construction tolerance, occupancy/plug-load growth over the building's "
                 "life, and this tool's own documented parser-accuracy uncertainty."),
    "clg_vent": ("Cooling — Ventilation", "OA quantity is a fixed, code-derived design value (ASHRAE "
                 "62.1) — not an “uncertain” load the way envelope/gains are. In Florida "
                 "specifically, oversized OA equipment running at part-load hurts dehumidification and "
                 "raises indoor RH — a moisture-intrusion risk this tool's own air-balance check exists "
                 "to catch. Padding the one input most likely to make that worse isn't defensible here; "
                 "leaving it at 0% is."),
    "htg_room": ("Heating — Room", "Same tolerance/growth logic as cooling, at the higher end of the "
                 "common range because the heating method here (steady-state, ASHRAE Ch.18) already "
                 "excludes any internal-gains credit and has no thermal-mass/time-of-day cushion the way "
                 "RTS cooling does — less margin is already “baked in” to the method itself."),
    "htg_vent": ("Heating — Ventilation", "Same reasoning as cooling vent."),
}
_SF_PCT_KEY = {"clg_room": "sf_clg_room_pct", "clg_vent": "sf_clg_vent_pct",
               "htg_room": "sf_htg_room_pct", "htg_vent": "sf_htg_vent_pct"}


def _safety_factor_footnote(design):
    items = ""
    for key, (label, why) in _SF_RATIONALE.items():
        pct = design.get(_SF_PCT_KEY[key])
        default = DEFAULT_SAFETY_FACTORS[key]
        if pct is not None and abs(pct - default) < 1e-6:
            items += f"<li><b>{label} ({default:.0f}%, default):</b> {why}</li>"
    if not items:
        return ""
    return (f"<div class='note' style='margin:8px 0 0'>"
            f"<b>Safety factor defaults still in use (unedited by the engineer):</b>"
            f"<ul style='margin:4px 0 0 18px;padding:0'>{items}</ul></div>")


def _load_summary(title, rows_data, note=""):
    cols = ["Location", "Rooms", "Area", "Sensible", "Latent", "Total Btu/h", "Tons", "ft²/ton", "Peak", "Heating Btu/h"]
    rows = ""
    for d in rows_data:
        sqft_per_ton = d.get("sqft_per_ton") or (round(d["area_sqft"] / d["tons"]) if d["tons"] else None)
        rows += ("<tr>" + _td(d["name"], False) + _td(_num(d.get("room_count"))) + _td(f"{d['area_sqft']:,.0f} ft²")
                  + _td(f"{d['sensible_btuh']:,.0f}") + _td(f"{d['latent_btuh']:,.0f}")
                  + _td(f"{d['total_btuh']:,.0f}") + _td(f"{d['tons']:,.1f}")
                  + _td(_num(sqft_per_ton)) + _td(_hour12(d["peak_hour"]))
                  + _td(f"{d.get('heating_btuh', 0):,.0f}") + "</tr>")
    table = _table(title, len(cols), col_headers=cols, rows=rows)
    return table + (f"<p class='note'>{note}</p>" if note else "")


def render_report(result, project_name="", plan_datauri=None, duct_plan_datauri=None, generated=""):
    d, b = result["design"], result["building"]
    rooms = result["rooms"]
    envelope = result.get("envelope", [])
    types = result.get("types", {})

    building_row = [{"name": "Building Total", "room_count": len(rooms), "area_sqft": b["area_sqft"],
                      "sensible_btuh": b["sensible_btuh"], "latent_btuh": b["latent_btuh"],
                      "total_btuh": b["total_btuh"], "tons": b["tons"], "sqft_per_ton": b["sqft_per_ton"],
                      "peak_hour": b["peak_hour"], "heating_btuh": b["heating_btuh"]}]
    zones_section = ""
    if result.get("zones"):
        zones_section = _load_summary(
            "Load Total Summary — Zones", result["zones"],
            "Each zone is its own block load — rooms summed hour-by-hour, so a zone's peak hour (and its "
            "tons) reflects when THAT zone's rooms collectively peak, which may differ from any single "
            "room's peak or the whole building's. Use this for per-AHU/per-RTU equipment sizing.")

    floors_section = ""
    if result.get("floors"):
        floors_section = _load_summary(
            "Load Total Summary — Floors", result["floors"],
            "Each floor is its own block load, same reasoning as a zone (and the same pattern the golden-"
            "building calibration validated against ASHRAE's own AHU-1/AHU-2 first-floor/second-floor "
            "example) — a floor's peak hour may differ from any single room's or the whole building's.")

    plan = (f"<table><thead><tr><th class='mainHeader' id='{_slug('Reviewed Floor Plan')}'>"
            f"Reviewed Floor Plan</th></tr></thead>"
            f"<tr><td class='otherData' style='text-align:center'>"
            f"<img class='report-img' src='{plan_datauri}' style='max-width:100%'></td></tr></table>") if plan_datauri else ""

    duct_plan = (f"<table><thead><tr><th class='mainHeader' id='{_slug('Duct Plan')}'>Duct Plan</th></tr></thead>"
            f"<tr><td class='otherData' style='text-align:center'>"
            f"<img class='report-img' src='{duct_plan_datauri}' style='max-width:100%'></td></tr></table>"
            f"<p class='note'>Double-line outline = rigid trunk (rectangular or round), sized to true width. "
            f"Corrugated/coiled outline = flexible branch. Blue centerline = supply duct, orange = return duct "
            f"(engineer markup only). Square icon = ceiling supply diffuser, circle = ceiling return, arrow = "
            f"sidewall grille. Square/circle equipment icons = air handler / dehumidifier / condensing unit "
            f"(AH/DH/CU).</p>") if duct_plan_datauri else ""

    air_balance_section = _air_balance(result["air_balance"]) if result.get("air_balance") else ""
    ventilation_schedule_section = (_ventilation_schedule(result["ventilation_schedule"])
                                    if result.get("ventilation_schedule") else "")
    supply_air_section = _supply_air(result["ducts"]["supply_air"]) if result.get("ducts") else ""
    duct_schedule_section = _duct_schedule(result["ducts"]) if result.get("ducts") else ""
    sf_footnote = _safety_factor_footnote(d)

    sections = [
        ("Project Information", _project_info(d, project_name, generated, b)),
        ("Load Total Summary — Room (Building)", _load_summary("Load Total Summary — Room (Building)", building_row)),
        ("Roof Types", _types_table("Roof Types", types.get("roof", []), "roof")),
        ("Roofs", _usage_table("Roofs", envelope, "roof")),
        ("Wall Types", _types_table("Wall Types", types.get("wall", []), "wall")),
        ("Walls", _usage_table("Walls", envelope, "wall", extra_cols=[("Length", "length_ft"), ("Height", "height_ft")])),
        ("Door Types", _types_table("Door Types", types.get("door", []), "door")),
        ("Doors", _usage_table("Doors", envelope, "door")),
        ("Glass Types", _types_table("Glass Types", types.get("glass", []), "window")),
        ("Glass", _usage_table("Glass", envelope, "window")),
        ("Room Information", _room_info(rooms)),
        ("Ventilation Schedule", ventilation_schedule_section),
        ("Cooling Load Details — Room", _cooling_detail(rooms, d["month"])),
        ("Heating Load Details — Room", _heating_detail(rooms, d["heating_db"])),
        ("Supply Air Requirements", supply_air_section),
        ("Load Total Summary — Zones", zones_section),
        ("Load Total Summary — Floors", floors_section),
        ("Air Balance — Building Pressurization", air_balance_section),
        ("Duct Plan", duct_plan),
        ("Duct Schedule", duct_schedule_section),
        ("Reviewed Floor Plan", plan),
    ]
    sections = [(title, html) for title, html in sections if html]

    # substantial sections worth starting a fresh printed page for (short header-only
    # sections aren't) — see plan item 6
    major_break = {"Room Information", "Cooling Load Details — Room", "Heating Load Details — Room",
                   "Load Total Summary — Room (Building)", "Duct Plan"}
    sections_html = "<hr class='pagebreak'>".join(
        f"<div class=\"{'section-break' if title in major_break else 'section'}\">{html}</div>"
        for title, html in sections)

    ducts = result.get("ducts") or {}
    # (TOC label, present?, section title used to derive the matching #anchor)
    toc_items = [
        ("Project Information", True, "Project Information"),
        ("Load Total Summary — Building", True, "Load Total Summary — Room (Building)"),
        ("Roof Types", True, "Roof Types"),
        ("Roofs", True, "Roofs"),
        ("Wall Types", True, "Wall Types"),
        ("Walls", True, "Walls"),
        ("Door Types", True, "Door Types"),
        ("Doors", True, "Doors"),
        ("Glass Types", True, "Glass Types"),
        ("Glass", True, "Glass"),
        ("Room Information", True, "Room Information"),
        ("Ventilation Schedule", bool(ventilation_schedule_section), "Ventilation Schedule"),
        ("Cooling Load Details", True, "Cooling Load Details — Room"),
        ("Heating Load Details", True, "Heating Load Details — Room"),
        ("Supply Air Requirements", bool(supply_air_section), "Supply Air Requirements"),
        ("Load Total Summary — Zones", bool(zones_section), "Load Total Summary — Zones"),
        ("Load Total Summary — Floors", bool(floors_section), "Load Total Summary — Floors"),
        ("Air Balance", bool(air_balance_section), "Air Balance — Building Pressurization"),
        ("Duct Plan", bool(duct_plan), "Duct Plan"),
        ("Air Handling Equipment", bool(ducts.get("equipment")), "Air Handling Equipment"),
        ("Duct Schedule", bool(duct_schedule_section), "Duct Schedule"),
        ("Diffuser / Grille Schedule", bool(duct_schedule_section), "Diffuser / Grille Schedule"),
        ("Reviewed Floor Plan", bool(plan), "Reviewed Floor Plan"),
    ]
    toc_html = " &middot; ".join(f"<a href='#{_slug(src)}' style='color:#000'>{label}</a>"
                                  for label, present, src in toc_items if present)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{project_name or 'Load Calc'} — Cooling &amp; Heating Loads</title>
<style>
  body {{ margin: 24px; background: #fff; font-family: Georgia, 'Times New Roman', serif; color: #000; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #444; margin: 0 0 18px; font-size: 13px; }}
  table {{ border: 4px solid #000; margin: 0 0 20px 0; border-collapse: collapse; font-size: 12px; width: 100%; }}
  thead {{ display: table-header-group; }}
  th {{ font-weight: bold; }}
  .regularSize {{ font-size: 12px; font-weight: normal; }}
  th.mainHeader {{ text-align: center; font-size: 18px; font-weight: bold; border-width: 4px 2px 2px 2px;
    border-style: solid; border-color: #000; padding: 6px; background: #f2f2ee; }}
  th.project {{ text-align: left; font-size: 12px; border: 1px solid #ccc; padding: 3px 8px; background: #fafafa; }}
  th.otherHeader {{ text-align: center; border: 1px solid #000; padding: 4px 6px; background: #eaeaea; }}
  td.otherData {{ border: 1px solid #ccc; padding: 3px 8px; }}
  .right {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .note {{ color: #444; font-size: 11.5px; margin: -12px 0 20px; font-style: italic; }}
  .flag-warn {{ color: #b00020; }}
  .pagebreak {{ border: 0; border-top: 2px dashed #999; margin: 24px 0; }}
  .print-btn {{ font: 13px Georgia, serif; padding: 6px 14px; margin: 0 0 14px; cursor: pointer; }}
  @media print {{
    .no-print {{ display: none; }}
    tr {{ page-break-inside: avoid; }}
    table {{ page-break-inside: avoid; }}
    img.report-img {{ max-width: 100%; height: auto; }}
    .section-break {{ page-break-before: always; break-before: page; }}
  }}
</style></head><body>
  <h1>Cooling &amp; Heating Load Results{(' — ' + project_name) if project_name else ''}</h1>
  <button class="print-btn no-print" onclick="window.print()">&#128424; Print / Save as PDF</button>
  <p class="sub">ASHRAE Handbook-Fundamentals Ch.18 · cooling: Radiant Time Series, peak {MONTHS[b['peak_month']]} {_hour12(b['peak_hour'])} · heating: steady-state, {d['heating_db']:.0f}°F design{(' · generated ' + generated) if generated else ''}</p>
  <p class="sub">{toc_html}</p>
  {sections_html}
  {sf_footnote}
</body></html>"""
