"""Phase 3 — filter raw lines down to wall candidates.

NOTE (from triage of real Adicot PDFs): on these drawings walls are NOT reliably
the thickest lines, and black linework is dominated by fixtures/dimensions/symbols.
So we filter by (a) dark, near-grayscale color and (b) real-world length, then let
the room-builder's connectivity + double-line collapse do the structural work.
Width is recorded but intentionally not used as a primary discriminator here."""
import math


def line_len(l) -> float:
    return math.hypot(l["x2"] - l["x1"], l["y2"] - l["y1"])


def is_structural_color(color) -> bool:
    """Keep dark, near-grayscale lines (black 0,0,0 and dark grey ~0.25). Drop
    light grey (~0.9) and colored mech linework (blue ducts/piping)."""
    r, g, b = (tuple(color) + (0.0, 0.0, 0.0))[:3]
    return max(r, g, b) < 0.45


def detect_walls(lines: list[dict], px_per_ft: float,
                 min_len_ft: float = 1.0) -> list[dict]:
    min_px = px_per_ft * min_len_ft
    return [l for l in lines
            if is_structural_color(l["color"])
            and line_len(l) >= min_px
            and is_orthogonal(l)]

def is_orthogonal(l, tol_deg=5.0):
    dx = l["x2"] - l["x1"]
    dy = l["y2"] - l["y1"]
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    return angle < tol_deg or angle > (90 - tol_deg)
