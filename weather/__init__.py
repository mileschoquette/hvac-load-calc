"""Local ASHRAE climatic design-conditions dataset + resolver.

No runtime API — the dataset ships as `stations.json` (honors the project's
no-external-service constraint). Values are PROVISIONAL until verified against
the published ASHRAE Fundamentals climatic-design tables (see stations.json meta).

Typical use (from the calc-input builder / engine):
    from weather import resolve_station, design_conditions
    ws = resolve_station(project["weather_station"])          # by name
    ws = resolve_station((project["latitude"], project["longitude"]))  # or nearest
    dc = design_conditions(ws, cooling="1%", heating="99%")
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_STATIONS_PATH = Path(__file__).with_name("stations.json")


@dataclass
class WeatherStation:
    name: str
    wmo_id: str
    lat: float
    lon: float
    elev_ft: float
    climate_zone: str
    htg_db_99: float
    htg_db_996: float
    clg_db_04: float
    clg_mcwb_04: float
    clg_db_1: float
    clg_mcwb_1: float
    clg_db_2: float
    clg_mcwb_2: float
    dehum_dp_1: float
    dehum_mcdb_1: float
    dehum_hr_1: float
    mean_daily_range_f: float
    tau_b: float
    tau_d: float


def load_stations(path: Path | None = None) -> dict[str, WeatherStation]:
    """WMO id -> WeatherStation. Fails loud on a bad file (load-bearing data)."""
    doc = json.loads((path or _STATIONS_PATH).read_text())
    fields = WeatherStation.__annotations__
    return {s["wmo_id"]: WeatherStation(**{k: s[k] for k in fields})
            for s in doc["stations"]}


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def resolve_station(query, stations: dict[str, WeatherStation] | None = None
                    ) -> Optional[WeatherStation]:
    """Resolve by WMO id, case-insensitive name substring, or nearest to a
    (lat, lon) tuple. Returns None if nothing matches. Address geocoding is
    intentionally NOT done (would need an external API)."""
    st = stations if stations is not None else load_stations()
    if isinstance(query, (tuple, list)) and len(query) == 2:
        return min(st.values(), key=lambda s: _haversine(tuple(query), (s.lat, s.lon)))
    q = str(query).strip()
    if q in st:
        return st[q]
    hits = [s for s in st.values() if q.lower() in s.name.lower()]
    return hits[0] if hits else None


def design_conditions(ws: WeatherStation, cooling: str = "1%",
                      heating: str = "99%") -> dict:
    """The design temperatures the RTS calc pulls, for the chosen percentiles."""
    htg = {"99%": ws.htg_db_99, "99.6%": ws.htg_db_996}[heating]
    clg_db, clg_wb = {"0.4%": (ws.clg_db_04, ws.clg_mcwb_04),
                      "1%": (ws.clg_db_1, ws.clg_mcwb_1),
                      "2%": (ws.clg_db_2, ws.clg_mcwb_2)}[cooling]
    return {
        "station": ws.name, "wmo_id": ws.wmo_id, "climate_zone": ws.climate_zone,
        "elevation_ft": ws.elev_ft, "latitude": ws.lat, "longitude": ws.lon,
        "heating_db_f": htg,
        "cooling_db_f": clg_db, "cooling_mcwb_f": clg_wb,
        "dehum_dewpoint_f": ws.dehum_dp_1, "dehum_mcdb_f": ws.dehum_mcdb_1,
        "dehum_humidity_ratio_gr": ws.dehum_hr_1,
        "daily_range_f": ws.mean_daily_range_f,
        "tau_b": ws.tau_b, "tau_d": ws.tau_d,
    }
