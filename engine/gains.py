"""Stage 5 — internal gains: people, lighting, equipment (ASHRAE Ch.18).

Each returns a 24-hour sensible heat-gain profile (magnitude x hourly schedule
fraction) that Stage 6 (rts.cooling_load) turns into a cooling load using the
gain's radiant fraction. People/equipment latent gain is NOT computed here —
it's an instantaneous load (no RTS, no 24-hour profile), so compute.py adds it
directly as a scalar (occupants x 200 Btu/h/person, plus equipment.latent_btuh).
"""
from __future__ import annotations

BTU_PER_W = 3.412


def _profile(magnitude, schedule):
    return [magnitude * s for s in schedule]


def people_sensible(n, sensible_per_person, schedule):
    return _profile(n * sensible_per_person, schedule)


def lighting(watts, schedule, space_fraction=1.0):
    """Lighting sensible gain to the space (Btu/h). space_fraction excludes any
    heat picked up directly by a return-air plenum."""
    return _profile(watts * BTU_PER_W * space_fraction, schedule)


def equipment(watts, schedule):
    return _profile(watts * BTU_PER_W, schedule)
