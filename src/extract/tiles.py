"""Tiling geometry: root grid over the bbox and quadtree split."""
import math
from dataclasses import dataclass

from src.settings import BBox

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320


@dataclass(frozen=True)
class Tile:
    tile_id: str
    south: float
    west: float
    north: float
    east: float
    size_km: float


def _edges(lo: float, hi: float, n: int) -> list[float]:
    """n+1 equally spaced edges; the outer ones are exactly lo and hi."""
    return [lo] + [lo + (hi - lo) * i / n for i in range(1, n)] + [hi]


def root_tiles(bbox: BBox, tile_km: float) -> list[Tile]:
    lat_km = (bbox.north - bbox.south) * KM_PER_DEG_LAT
    mid = math.radians((bbox.north + bbox.south) / 2)
    lon_km = (bbox.east - bbox.west) * KM_PER_DEG_LON_EQ * math.cos(mid)
    rows = max(1, math.ceil(lat_km / tile_km))
    cols = max(1, math.ceil(lon_km / tile_km))
    lats = _edges(bbox.south, bbox.north, rows)
    lons = _edges(bbox.west, bbox.east, cols)
    return [
        Tile(f"r{r}c{c}", lats[r], lons[c], lats[r + 1], lons[c + 1], tile_km)
        for r in range(rows)
        for c in range(cols)
    ]


def split(t: Tile) -> list[Tile]:
    """Four children: SW, SE, NW, NE."""
    mlat = (t.south + t.north) / 2
    mlon = (t.west + t.east) / 2
    half = t.size_km / 2
    return [
        Tile(f"{t.tile_id}.0", t.south, t.west, mlat, mlon, half),
        Tile(f"{t.tile_id}.1", t.south, mlon, mlat, t.east, half),
        Tile(f"{t.tile_id}.2", mlat, t.west, t.north, mlon, half),
        Tile(f"{t.tile_id}.3", mlat, mlon, t.north, t.east, half),
    ]
