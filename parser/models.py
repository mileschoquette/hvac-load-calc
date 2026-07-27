"""Core data models. Geometry-phase subset — the THERMAL layer (Roof, Floor,
ProjectInfo, WeatherStation) comes in the thermal phase. See the architecture doc."""
from dataclasses import dataclass, field
from typing import Optional
from shapely.geometry import Polygon


@dataclass
class Window:
    id: str
    wall_id: str
    position_along_wall: float    # 0.0–1.0 along the wall
    width_ft: float
    height_ft: float = 0.0
    orientation: str = "UNKNOWN"
    source: str = "detected"


@dataclass
class Door:
    id: str
    wall_id: str
    position_along_wall: float
    width_ft: float
    height_ft: float = 6.83
    is_exterior: bool = False
    source: str = "detected"


@dataclass
class Wall:
    id: str
    x1: float
    y1: float
    x2: float
    y2: float
    length_ft: float
    orientation: str = "UNKNOWN"
    is_exterior: bool = False
    windows: list = field(default_factory=list)
    doors: list = field(default_factory=list)


@dataclass
class Room:
    id: str
    polygon: Polygon              # drawing coords (PDF points)
    area_sqft: float
    label: str = ""
    room_type: Optional[str] = None
    walls: list = field(default_factory=list)
    source: str = "detected"
    confidence: float = 1.0


@dataclass
class Drawing:
    filename: str
    page_number: int
    scale_px_per_ft: float
    rooms: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
