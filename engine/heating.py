"""Heating load — steady-state, not RTS. ASHRAE's heating design method (Ch.18)
is intentionally simpler than cooling: worst case is a still, sunless night, so
there's no solar, no thermal-mass time delay, and — being conservative — no
credit for internal gains (people/lights/equipment), which might not be present
yet during a cold-morning warm-up. Every component is just steady-state
conduction or sensible ventilation/infiltration at the design ΔT.
"""
from __future__ import annotations


def conduction_loss(u, area, t_indoor, t_outdoor):
    """Steady-state heat loss through a wall/roof/window/door/floor (Btu/h)."""
    if not u or area <= 0:
        return 0.0
    return u * area * max(t_indoor - t_outdoor, 0.0)


def air_loss(cfm, t_indoor, t_outdoor):
    """Sensible heat loss from infiltration/ventilation air (Btu/h)."""
    if not cfm:
        return 0.0
    return 1.1 * cfm * max(t_indoor - t_outdoor, 0.0)
