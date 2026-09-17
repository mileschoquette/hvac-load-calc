"""Autosave endpoint: POST /api/autosave must snapshot the project WITHOUT ever
touching the file the Save JSON button owns.

That separation is the whole point of the feature. A five-minute snapshot of
half-finished work must not be able to replace a save the engineer chose to
make, so it is what this pins hardest. Also covered: duct_plan_png is stripped
(it is what once left a 1.6 MB rooms_edited.json on disk), /api/save still
writes the real file, and _write_json leaves no .tmp behind.

Runs offline against the real http.server on a real port.

    python3 tests/test_autosave.py
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import loadcalc

STEM = "__autosave_test__"
AUTOSAVE = ROOT / f"{STEM}.autosave.json"
PROJECT = ROOT / f"{STEM}.json"

PAYLOAD = {
    "pdf_stem": STEM,
    "total_sqft": 1440,
    "floors": [{"level": 1, "label": "L1", "pxPerFt": 18.0, "north_deg": 0}],
    "project_settings": {"project_name": "autosave test"},
    "rooms": [{"id": "r1", "label": "Office", "area_sqft": 120,
               "points": [[0, 0], [10, 0], [10, 12], [0, 12]]}],
    "duct_runs": [], "duct_terminals": [], "duct_equipment": [], "openings": [],
}


def _post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def check_autosave_writes_its_own_file(port):
    res = _post(port, "/api/autosave", PAYLOAD)
    assert res["ok"] is True, res
    assert res["filename"] == f"{STEM}.autosave.json", res
    assert AUTOSAVE.exists(), "autosave file was not written"
    got = json.loads(AUTOSAVE.read_text())
    assert got["rooms"][0]["label"] == "Office", got["rooms"]
    assert got["total_sqft"] == 1440, got["total_sqft"]
    print("PASS  /api/autosave writes <stem>.autosave.json")


def check_autosave_never_touches_the_project_file(port):
    """The guarantee the whole design rests on."""
    assert not PROJECT.exists(), (
        f"autosave wrote {PROJECT.name}; it must only ever write .autosave.json")
    # And an autosave over an existing project file must leave it byte-identical.
    PROJECT.write_text('{"sentinel": true}')
    before = PROJECT.read_bytes()
    _post(port, "/api/autosave", {**PAYLOAD, "total_sqft": 9999})
    assert PROJECT.read_bytes() == before, "autosave overwrote the real project file"
    assert json.loads(AUTOSAVE.read_text())["total_sqft"] == 9999
    print("PASS  autosave leaves <stem>.json untouched")


def check_duct_plan_png_is_stripped(port):
    _post(port, "/api/autosave", {**PAYLOAD, "duct_plan_png": "data:image/png;base64,AAAA"})
    got = json.loads(AUTOSAVE.read_text())
    assert "duct_plan_png" not in got, "duct_plan_png was persisted into the autosave"
    print("PASS  duct_plan_png is dropped from autosaves")


def check_manual_save_still_writes_the_project_file(port):
    PROJECT.unlink(missing_ok=True)
    res = _post(port, "/api/save", PAYLOAD)
    assert res["ok"] is True and res["filename"] == f"{STEM}.json", res
    assert json.loads(PROJECT.read_text())["rooms"][0]["label"] == "Office"
    print("PASS  /api/save still writes <stem>.json")


def check_write_json_is_atomic():
    target = ROOT / f"{STEM}.atomic.json"
    try:
        target.write_text('{"old": true}')
        loadcalc._write_json(target, {"new": True})
        assert json.loads(target.read_text()) == {"new": True}
        leftovers = list(ROOT.glob(f"{STEM}.atomic.json.tmp"))
        assert not leftovers, f"temp file left behind: {leftovers}"
        print("PASS  _write_json replaces in place and leaves no .tmp")
    finally:
        target.unlink(missing_ok=True)
        (ROOT / f"{STEM}.atomic.json.tmp").unlink(missing_ok=True)


def check_unknown_endpoint_still_404s(port):
    try:
        _post(port, "/api/nope", PAYLOAD)
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code
        print("PASS  unknown endpoint still 404s")
        return
    raise AssertionError("expected a 404 for an unknown endpoint")


def main():
    server, port = loadcalc._start_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        check_autosave_writes_its_own_file(port)
        check_autosave_never_touches_the_project_file(port)
        check_duct_plan_png_is_stripped(port)
        check_manual_save_still_writes_the_project_file(port)
        check_write_json_is_atomic()
        check_unknown_endpoint_still_404s(port)
    finally:
        server.shutdown()
        server.server_close()
        for f in (AUTOSAVE, PROJECT):
            f.unlink(missing_ok=True)
        for f in ROOT.glob(f"{STEM}*"):
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
