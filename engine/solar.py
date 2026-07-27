"""Stage 2 — solar geometry + ASHRAE clear-sky (tau) irradiance (Ch.14/18).

Two layers, each checkable against the workbook:
  (a) geometry constants per month — equation of time, declination, Eo
      (vs the 'Solar Table' sheet, ASHRAE Table 2);
  (b) clear-sky beam/diffuse and the irradiance incident on a tilted surface
      (vs the wall/window component sheets, Stage 2b / 3 / 4).

Angles: solar & surface azimuth are measured from SOUTH, positive toward WEST
(ASHRAE convention). Tilt Sigma: 90 deg = vertical wall, 0 deg = horizontal roof.
"""
from __future__ import annotations
import math

ESC = 433.4          # solar constant, Btu/h.ft2 (IP)
RHO_GROUND = 0.2     # default ground reflectance

# Representative day-of-year: the 21st of each month (ASHRAE basis).
REP_N = [21, 52, 80, 111, 141, 172, 202, 233, 264, 294, 325, 355]

# Surface azimuth (deg from south, + west) for the cardinal faces.
SURFACE_AZIMUTH = {"S": 0.0, "W": 90.0, "N": 180.0, "E": -90.0,
                   "SW": 45.0, "NW": 135.0, "NE": -135.0, "SE": -45.0}
TILT_WALL, TILT_ROOF = 90.0, 0.0


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


# ── (a) monthly geometry constants ───────────────────────────────────────────
def declination(n: int) -> float:
    """Solar declination (deg) for day-of-year n."""
    return 23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0))


def equation_of_time(n: int) -> float:
    """Equation of time (minutes) for day-of-year n."""
    g = math.radians(360.0 * (n - 1) / 365.0)
    return 2.2918 * (0.0075 + 0.1868 * math.cos(g) - 3.2077 * math.sin(g)
                     - 1.4615 * math.cos(2 * g) - 4.089 * math.sin(2 * g))


def eo(n: int) -> float:
    """Extraterrestrial normal irradiance (Btu/h.ft2) for day-of-year n."""
    return ESC * (1 + 0.033 * math.cos(math.radians(360.0 * (n - 3) / 365.0)))


# ── solar position ────────────────────────────────────────────────────────────
def apparent_solar_time(lst_hour: float, n: int, longitude: float,
                        std_meridian: float) -> float:
    """Apparent (true) solar time, hours. longitude/std_meridian in deg
    (west negative, as in the workbook: Atlanta -84.43, Eastern -75)."""
    return lst_hour + equation_of_time(n) / 60.0 + (longitude - std_meridian) / 15.0


def hour_angle(ast_hour: float) -> float:
    return 15.0 * (ast_hour - 12.0)          # deg, negative am / positive pm


def solar_position(lat_deg: float, decl_deg: float, ast_hour: float):
    """Return (altitude_deg, azimuth_deg). azimuth from south, + west.
    altitude <= 0 means the sun is down (azimuth returned as None)."""
    L, d, H = map(math.radians, (lat_deg, decl_deg, hour_angle(ast_hour)))
    sin_b = math.cos(L) * math.cos(d) * math.cos(H) + math.sin(L) * math.sin(d)
    beta = math.asin(_clamp(sin_b))
    if beta <= 0:
        return math.degrees(beta), None
    cos_phi = (math.sin(beta) * math.sin(L) - math.sin(d)) / (math.cos(beta) * math.cos(L))
    phi = math.copysign(math.acos(_clamp(cos_phi)), H)
    return math.degrees(beta), math.degrees(phi)


# ── (b) clear-sky irradiance + surface incidence ──────────────────────────────
def clear_sky(n: int, alt_deg: float, tau_b: float, tau_d: float):
    """Beam-normal and diffuse-horizontal irradiance (Btu/h.ft2) via the ASHRAE
    2013+ tau model. Returns (Eb, Ed); (0,0) when the sun is down."""
    if alt_deg <= 0:
        return 0.0, 0.0
    m = 1.0 / (math.sin(math.radians(alt_deg))
               + 0.50572 * (6.07995 + alt_deg) ** -1.6364)     # air mass
    ab = 1.454 - 0.406 * tau_b - 0.268 * tau_d + 0.021 * tau_b * tau_d
    ad = 0.507 + 0.205 * tau_b - 0.080 * tau_d - 0.190 * tau_b * tau_d
    e0 = eo(n)
    return e0 * math.exp(-tau_b * m ** ab), e0 * math.exp(-tau_d * m ** ad)


def cos_incidence(alt_deg, sol_az_deg, surf_az_deg, tilt_deg=TILT_WALL) -> float:
    b = math.radians(alt_deg)
    gamma = math.radians(sol_az_deg - surf_az_deg)
    S = math.radians(tilt_deg)
    return math.cos(b) * math.cos(gamma) * math.sin(S) + math.sin(b) * math.cos(S)


def surface_irradiance(Eb, Ed, alt_deg, cos_theta, tilt_deg=TILT_WALL,
                       rho=RHO_GROUND):
    """Total irradiance on a surface (Btu/h.ft2) and its (beam, diffuse, ground)
    parts. Diffuse uses the ASHRAE vertical-surface Y ratio, blended by tilt."""
    S = math.radians(tilt_deg)
    beam = Eb * max(cos_theta, 0.0)
    Y = max(0.45, 0.55 + 0.437 * cos_theta + 0.313 * cos_theta ** 2)
    diffuse = Ed * (Y * math.sin(S) + math.cos(S))
    ground = rho * (Eb * max(math.sin(math.radians(alt_deg)), 0.0) + Ed) * (1 - math.cos(S)) / 2
    return beam + diffuse + ground, beam, diffuse, ground
