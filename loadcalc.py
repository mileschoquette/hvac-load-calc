#!/usr/bin/env python3
"""Adicot Load Calc — end-to-end bundler.

  0) app:    the easy way in — python3 app.py (or `loadcalc.py app`) opens a
             browser page where you just drop in your plans PDF and a JSON
             file (the DM-setup types JSON the first time on a project, or
             your saved rooms_edited.json to pick back up — detected
             automatically) and it takes care of parse/resume for you.
       python3 app.py

  1) parse:  drop in a plans PDF (+ the DM-setup types JSON) -> the parser draws
             the rooms and opens the review UI to edit them. This also starts a
             small local server, so the review UI's "Save JSON" and "Run Load
             Calc" buttons can write files and trigger the calc directly.
       python3 loadcalc.py parse "plans.pdf" [types.json] [px_per_ft] [min_len_ft] [page]

             Multi-floor plan set (one page per floor, page 1 = floor 1, etc):
             pass a comma-separated page list, e.g. page="0,1,2" for a 3-floor
             set. px_per_ft can likewise be a comma list matching the pages (one
             scale per floor) or a single value applied to all of them.
       python3 loadcalc.py parse "plans.pdf" [types.json] "18,18" 1.0 "0,1"

  2) resume: reopen a previously saved rooms_edited.json in the review UI to
             keep editing where you left off — reuses the plan image from the
             last parse, no need to re-parse the PDF.
       python3 loadcalc.py resume rooms_edited.json [types.json]

  3) calc:   (only needed if you didn't use "Run Load Calc" in the browser)
             run the RTS engine on the reviewed rooms -> a results report with
             the supporting evidence attached.
       python3 loadcalc.py calc rooms_edited.json [types.json]

The types JSON (loadcalc_input.json from the DM Setup tab) supplies room types,
wall/roof/window/door types, and the weather station.
"""
import base64
import json
import shutil
import subprocess
import sys
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import export_review
from engine.compute import compute_building
from engine.ducts import size_duct_network, duct_report_data
from engine.dxf_export import export_ducts_dxf
from engine.report import render_report


def _open(target):
    try:
        subprocess.run(["open", str(target)], check=False)
    except Exception:
        pass


def _f(ps, key):
    """A project_settings value as a float, or None if blank/absent/unparseable — these
    come in as strings from the DM-setup JSON editor."""
    v = ps.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _weather(types):
    """Design conditions for compute_building, in priority order:
      1. Manual per-project design values in project_settings (cooling_design_db/wb,
         osa_low_dry/daily_range, latitude/longitude, standard_meridian,
         clear_sky_taub/taud, dehumid_humidity_ratio) — the actual values for THIS
         project's location, entered from ASHRAE Fundamentals Ch.14 (or an equivalent
         regional table) since this tool has no live weather API and the bundled
         stations.json only covers 10 FL airports. Takes priority over #2 below because
         it's project-specific, not a generic same-climate-zone stand-in.
      2. A bundled stations.json match by name/WMO id or lat-lon proximity.
      3. compute.py's Florida-representative defaults, as an explicit last resort —
         flagged via `weather_source` rather than silently applied. This exact gap bit a
         real project: `weather_station` was "Greenville Downtown, SC" (no bundled match,
         and no manual values existed yet), so the report displayed that real station name
         and WMO id over Florida's 95F/18F/tau-0.5,2.1 numbers with no indication they
         weren't Greenville's actual design conditions.
    """
    ps = (types or {}).get("project_settings", {}) or {}
    w = {"indoor_db": 75.0, "indoor_grains": 65.0,
         "station": ps.get("weather_station") or "—"}

    resolved = None
    try:
        import weather
        lat, lon = _f(ps, "latitude"), _f(ps, "longitude")
        q = ps.get("weather_station") or (lat is not None and lon is not None and (lat, lon))
        resolved = weather.resolve_station(q) if q else None
        if resolved:
            w.update(station=getattr(resolved, "name", w["station"]),
                     cooling_db=getattr(resolved, "clg_db_1", None),
                     heating_db=getattr(resolved, "htg_db_996", None),
                     daily_range=getattr(resolved, "mean_daily_range_f", None),
                     lat=getattr(resolved, "lat", None), lon=getattr(resolved, "lon", None),
                     outdoor_grains=getattr(resolved, "dehum_hr_1", None),
                     tau_b=getattr(resolved, "tau_b", None), tau_d=getattr(resolved, "tau_d", None))
    except (ImportError, FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"  (weather station lookup unavailable: {e}; using design defaults)")

    manual = {k: v for k, v in {
        "cooling_db": _f(ps, "cooling_design_db"),
        "daily_range": _f(ps, "osa_daily_range"),
        "heating_db": _f(ps, "osa_low_dry"),
        "outdoor_grains": _f(ps, "dehumid_humidity_ratio"),
        "lat": _f(ps, "latitude"), "lon": _f(ps, "longitude"),
        "std_meridian": _f(ps, "standard_meridian"),
        "tau_b": _f(ps, "clear_sky_taub"), "tau_d": _f(ps, "clear_sky_taud"),
    }.items() if v is not None}
    w.update(manual)
    w.setdefault("std_meridian", -75.0)

    if manual:
        w["weather_source"] = "Manual project design values"
    elif resolved:
        w["weather_source"] = f"Bundled ASHRAE station match ({resolved.name})"
    else:
        w["weather_source"] = ("UNVERIFIED DEFAULT — Florida-representative values, "
                               "NOT this project's actual design conditions")

    if ps.get("cooling_design_percentile"):
        w["cooling_percentile"] = ps["cooling_design_percentile"]
    if ps.get("heating_design_percentile"):
        w["heating_percentile"] = ps["heating_design_percentile"]
    elev = _f(ps, "elevation")
    if elev is not None:
        w["elevation_ft"] = elev

    month = _f(ps, "cooling_design_month")
    w["month"] = int(month) if month else 8

    # Longitude sign guard — engine/solar.py's apparent_solar_time is west-negative
    # (Atlanta -84.43, Eastern std meridian -75), but ASHRAE Fundamentals Ch.14 tables
    # commonly list longitude as an unsigned "degrees West" figure, which is the natural
    # way to transcribe it into a manual-entry JSON — silently wrong-signed longitude
    # doesn't error, it just corrupts every solar gain in the report (a 92-degree apparent-
    # solar-time error turns midday into a wrong-side-of-the-planet solar position). A
    # legitimate timezone rarely puts a station more than ~30-40 deg from its meridian, so
    # anything past that is almost certainly this sign mistake, not a real project.
    if w.get("lon") is not None and w.get("std_meridian") is not None:
        offset = w["lon"] - w["std_meridian"]
        if abs(offset) > 40:
            warn = (f"Longitude ({w['lon']:g}°) and standard meridian ({w['std_meridian']:g}°) "
                    f"are {abs(offset):.0f}° apart — a real timezone is rarely more than "
                    f"~30-40° from its meridian. This usually means longitude was entered as an "
                    f"unsigned 'degrees West' value instead of this engine's negative-west "
                    f"convention (e.g. Atlanta -84.43, not 84.43). Every solar gain below is wrong "
                    f"until this is fixed.")
            print(f"  WARNING: {warn}")
            w["lon_warning"] = warn

    return {k: v for k, v in w.items() if v is not None}


def _duct_design(ps):
    """Duct sizing design criteria from project_settings — same _f()-based resolution as
    _weather() (a dict of possibly-None values; the consumer, size_duct_network, applies
    the documented defaults), not the None-safe safety-factor-style helper, since none of
    these have a legitimate deliberate zero the way a safety factor does."""
    return {"supply_delta_t": _f(ps, "duct_supply_delta_t"),
            "friction_rate_in_wg_per_100ft": _f(ps, "duct_friction_rate"),
            "trunk_height_in": _f(ps, "duct_trunk_height_in")}


def _merged_types(review):
    """loadcalc_input.json (`tj`), with the review UI's Project/Weather tab edits (saved
    into rooms_edited.json / `review`) overlaid onto its project_settings — those edits
    must win over the original baseline, or they silently never reach the calc. This was a
    real bug: editing e.g. longitude in that panel had no effect on results.html, because
    the calc only ever read loadcalc_input.json's project_settings, never the saved
    review's. Shared by every entry point that needs project_settings (`_run_calc`, duct
    sizing) — reads loadcalc_input.json fresh off disk each time, so edits to it (e.g.
    re-exported DM types) are picked up without restarting anything."""
    types_path = ROOT / "loadcalc_input.json"
    tj = json.loads(types_path.read_text()) if types_path.exists() else {}
    merged_ps = {**(tj.get("project_settings") or {}), **(review.get("project_settings") or {})}
    return {**tj, "project_settings": merged_ps}


def _run_calc(review):
    """Shared by `loadcalc.py calc` and the review UI's "Run Load Calc" button:
    review packet -> compute_building -> results.html."""
    tj = _merged_types(review)
    merged_ps = tj["project_settings"]
    w = _weather(tj)
    month = w.pop("month", 8)
    result = compute_building(review, w, month=month)
    result["ducts"] = duct_report_data(review, result, _duct_design(merged_ps))
    plan_uri = None
    p = ROOT / "plan.png"
    if p.exists():
        plan_uri = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    proj = merged_ps.get("project_name", "") or tj.get("job", "")
    html = render_report(result, project_name=proj, plan_datauri=plan_uri,
                         duct_plan_datauri=review.get("duct_plan_png"),
                         generated=datetime.now().strftime("%Y-%m-%d %H:%M"))
    (ROOT / "results.html").write_text(html)
    return result


def _start_project(pdf_bytes, pdf_name, json_text, ppf, min_len, page):
    """Behind the app's landing-page drop zones: saves the dropped PDF, figures
    out from the dropped JSON's shape whether this is a first-time project
    (DM-setup types JSON) or resuming one (a saved rooms_edited.json — it has
    a "rooms" key, a types JSON doesn't), and parses/resumes accordingly.
    Returns (resumed, weather_warning): resumed is True if resuming, False if freshly
    parsed; weather_warning is _weather()'s fallback message (surfaced here, before the
    user ever runs a calc, instead of only deep inside the final report) when this
    project's weather station will fall back to the Florida-representative defaults,
    else None."""
    name = Path(pdf_name or "plans.pdf").name or "plans.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    pdf_path = ROOT / name
    pdf_path.write_bytes(pdf_bytes)

    resumed = False
    if json_text:
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            raise ValueError("That JSON file isn't valid. Check that it's the "
                             "DM-setup types file or a saved rooms_edited.json.") from None
        if "rooms" in data:
            (ROOT / "rooms_edited.json").write_text(json_text)
            resumed = True
        else:
            (ROOT / "loadcalc_input.json").write_text(json_text)

    # Same _weather() resolution used at report time, just run now instead of only
    # after a full calc — so a project whose weather_station won't resolve (no
    # stations.json match, no manual values) gets flagged before the user does any
    # more work on it, not after reading the generated report.
    review = {}
    re_path = ROOT / "rooms_edited.json"
    if re_path.exists():
        try:
            review = json.loads(re_path.read_text())
        except json.JSONDecodeError:
            review = {}
    weather_source = _weather(_merged_types(review)).get("weather_source", "")
    weather_warning = weather_source if weather_source.startswith("UNVERIFIED") else None

    pages = _parse_pages_arg(page)
    ppfs = _parse_ppf_arg(ppf, len(pages))

    # Known failure modes on the dropped PDF itself, translated to plain English
    # before export_review.export gets anywhere near them (it would otherwise
    # surface a raw fitz/pymupdf exception, or an IndexError, straight to the user).
    import fitz
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        raise ValueError("That PDF couldn't be read. Make sure it's not "
                         "password-protected or corrupted.") from None
    if doc.needs_pass:
        doc.close()
        raise ValueError("That PDF is password-protected. Remove the password "
                         "and try again.")
    n_pages = doc.page_count
    doc.close()
    for pg_no in pages:
        if pg_no < 0 or pg_no >= n_pages:
            raise ValueError(
                f"Page {pg_no} doesn't exist in this PDF (it has {n_pages} "
                f"page{'s' if n_pages != 1 else ''}).")

    export_review.export(str(pdf_path), ppfs, min_len, pages)
    if resumed:
        export_review.resume(str(ROOT / "rooms_edited.json"))

    try:
        import fitz
        pg = fitz.open(str(pdf_path))[pages[0]]
        pg.get_pixmap(matrix=fitz.Matrix(0.7, 0.7)).save(str(ROOT / "plan.png"))
    except Exception:
        pass
    return resumed, weather_warning


APP_HTML = (Path(__file__).parent / "templates" / "landing.html").read_text()


class ReviewHandler(SimpleHTTPRequestHandler):
    """Serves the project folder (review.html, plan.png, results.html) and
    handles the review UI's Save/Run-calc buttons over POST, so both can write
    straight into the project folder instead of going through the browser's
    Downloads folder."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        pass   # keep the terminal quiet — errors still print via send_error

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = APP_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path == "/api/start":
            try:
                req = json.loads(raw)
                resumed, weather_warning = _start_project(
                    base64.b64decode(req["pdf_b64"]), req.get("pdf_name"),
                    req.get("json_text"), req.get("ppf") or "18.0",
                    float(req.get("min_len") or 1.0), req.get("page") or "0")
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "resumed": resumed, "weather_warning": weather_warning})
            return
        try:
            review = json.loads(raw)
        except Exception as e:
            self._json({"ok": False, "error": f"bad request body: {e}"}, 400)
            return
        # Named after the dropped PDF (embedded as pdf_stem by export_review.export/resume
        # and carried through every buildPayload()) instead of a fixed rooms_edited.json —
        # so different projects in this folder don't keep clobbering each other's save.
        stem = Path(review["pdf_stem"]).name if review.get("pdf_stem") else None
        out_name = f"{stem}.json" if stem else "rooms_edited.json"
        (ROOT / out_name).write_text(json.dumps(review, indent=2))
        if self.path == "/api/save":
            self._json({"ok": True, "filename": out_name})
        elif self.path == "/api/calc":
            try:
                result = _run_calc(review)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            b = result["building"]
            print(f"ran load calc from the browser -> {b['tons']:.1f} tons  "
                  f"({b['total_btuh']:,.0f} Btu/h, {len(result['rooms'])} rooms)")
            self._json({"ok": True, "tons": b["tons"], "btuh": b["total_btuh"],
                        "rooms": len(result["rooms"])})
        elif self.path == "/api/size_ducts":
            # Lighter-weight than /api/calc — just the RTS sensible loads (for terminal
            # CFM) and the duct network math, no report.html write. Returns only the
            # fields that changed, keyed by id, so the browser can merge them into the
            # live drawing without disturbing anything the engineer already edited.
            try:
                tj = _merged_types(review)
                w = _weather(tj)
                month = w.pop("month", 8)
                result = compute_building(review, w, month=month)
                sized = size_duct_network(review, result, _duct_design(tj["project_settings"]))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            print(f"sized ducts from the browser -> {len(sized['duct_runs'])} runs, "
                  f"{len(sized['duct_terminals'])} terminals, {len(sized['duct_equipment'])} air handlers")
            self._json({"ok": True, **sized})
        elif self.path == "/api/export_dxf":
            try:
                paths = export_ducts_dxf(review, ROOT)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "files": [p.name for p in paths]})
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)


def _start_server():
    for port in range(8765, 8785):
        try:
            return HTTPServer(("127.0.0.1", port), ReviewHandler), port
        except OSError:
            continue
    raise RuntimeError("no free port found in 8765-8784")


def _serve_and_wait(path="review.html", hint=None):
    server, port = _start_server()
    url = f"http://127.0.0.1:{port}/{path}"
    print(f"Open: {url}")
    print(hint or ("Edit the rooms, then click 'Save JSON' (writes rooms_edited.json) or "
                   "'Run Load Calc' (saves + runs the engine, opens results.html) in the browser."))
    print("Ctrl-C here to stop the server when you're done.")
    _open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


def app_cmd():
    """The easy way in: a browser landing page with drop zones for the plans
    PDF + a JSON file, instead of remembering CLI flags."""
    _serve_and_wait(path="", hint="Drop in your plans PDF + a JSON file (DM-setup "
                    "types the first time, or a saved rooms_edited.json to resume).")


def _load_types(types):
    """Copy a types JSON into the project's loadcalc_input.json, unless it's
    already that exact file — shutil.copy raises SameFileError otherwise (a
    real crash if someone re-passes the file already sitting in this folder)."""
    dest = ROOT / "loadcalc_input.json"
    if Path(types).resolve() != dest.resolve():
        shutil.copy(types, dest)


def _parse_pages_arg(page_arg):
    """CLI/browser page argument: a single int, or comma-separated page numbers —
    one per building floor (e.g. "0,1,2" for a 3-floor plan set, page 1 = floor 1,
    page 2 = floor 2, etc — the common case of one multi-page plan set export)."""
    if isinstance(page_arg, int):
        return (page_arg,)
    return tuple(int(x) for x in str(page_arg).split(","))


def _parse_ppf_arg(ppf_arg, n_pages):
    """CLI/browser ppf argument: a single number (applied to every floor), or
    comma-separated numbers matching the page count — different sheets are
    sometimes drawn at different scales."""
    if isinstance(ppf_arg, (int, float)):
        return [float(ppf_arg)] * n_pages
    parts = [float(x) for x in str(ppf_arg).split(",")]
    return parts if len(parts) > 1 else parts * n_pages


def parse_cmd(pdf, types=None, ppf=18.0, min_len=1.0, page=0):
    if types:
        _load_types(types)   # review UI auto-loads this
        print(f"loaded types from {types}")
    pages = _parse_pages_arg(page)
    ppfs = _parse_ppf_arg(ppf, len(pages))
    export_review.export(pdf, ppfs, min_len, pages)         # writes review.html (auto-loads types)
    # save floor 1's plan image for the results report's evidence
    try:
        import fitz
        pg = fitz.open(pdf)[pages[0]]
        pg.get_pixmap(matrix=fitz.Matrix(0.7, 0.7)).save(str(ROOT / "plan.png"))
    except Exception:
        pass
    _serve_and_wait()


def resume_cmd(rooms_edited, types=None):
    if types:
        _load_types(types)
        print(f"loaded types from {types}")
    export_review.resume(rooms_edited)   # reuses review.html's plan image, overlays saved rooms/openings/types
    _serve_and_wait()


def calc_cmd(rooms_edited, types=None):
    review = json.loads(Path(rooms_edited).read_text())
    if types:
        _load_types(types)
    result = _run_calc(review)
    b = result["building"]
    print(f"wrote results.html  ->  {b['tons']:.1f} tons  ({b['total_btuh']:,.0f} Btu/h, "
          f"{len(result['rooms'])} rooms, {b['area_sqft']:,.0f} ft²)")
    _open(ROOT / "results.html")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["app"]:
        app_cmd()
    elif not a or a[0] not in ("parse", "resume", "calc"):
        print(__doc__)
        sys.exit(0)
    elif a[0] == "parse":
        # ppf/page are left as raw strings (not pre-cast to float/int) so a
        # comma-separated multi-floor list ("18,18" / "0,1") can pass through —
        # parse_cmd's _parse_ppf_arg/_parse_pages_arg handle both single and
        # comma-list forms.
        parse_cmd(a[1], a[2] if len(a) > 2 else None,
                  a[3] if len(a) > 3 else 18.0,
                  float(a[4]) if len(a) > 4 else 1.0,
                  a[5] if len(a) > 5 else 0)
    elif a[0] == "resume":
        resume_cmd(a[1], a[2] if len(a) > 2 else None)
    else:
        calc_cmd(a[1], a[2] if len(a) > 2 else None)
