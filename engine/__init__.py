"""Adicot RTS load engine — ASHRAE Handbook-Fundamentals Ch.18 (Radiant Time
Series). Built stage by stage, each verified against the bundled ASHRAE example
workbooks (validation/ashrae_golden.json + the .xls). Acceptance: reproduce the
single-room example (4,121.6 Btu/h, Aug @ hour 14) then the building (65.9 ton).

Stages (build order):
  1. design-day temperatures         design_day.py        <- done
  2. solar position + clear-sky irradiance per surface
  3. opaque conduction: sol-air temp -> CTS -> gain
  4. fenestration: SHGC(incidence) x IAC x shading + conduction
  5. internal gains: people / lighting / equipment (sensible + latent)
  6. gain -> cooling load via RTS (radiant split, 24-term); sum 24h; peak
  7. latent + ventilation/infiltration; aggregate room -> block -> building
"""
from .design_day import design_day_db, DAILY_RANGE_FRACTION

__all__ = ["design_day_db", "DAILY_RANGE_FRACTION"]
