from src.extract.tiles import Tile, root_tiles, split
from src.settings import BBox

VILNIUS = BBox(south=54.57, west=25.02, north=54.83, east=25.48)


def test_root_tiles_cover_bbox():
    tiles = root_tiles(VILNIUS, 8.0)
    assert len(tiles) == 16
    assert [t.tile_id for t in tiles] == [f"r{r}c{c}" for r in range(4) for c in range(4)]
    assert min(t.south for t in tiles) == VILNIUS.south
    assert max(t.north for t in tiles) == VILNIUS.north
    assert min(t.west for t in tiles) == VILNIUS.west
    assert max(t.east for t in tiles) == VILNIUS.east
    by = {t.tile_id: t for t in tiles}
    assert by["r0c0"].north == by["r1c0"].south
    assert by["r0c0"].east == by["r0c1"].west
    assert all(t.size_km == 8.0 for t in tiles)


def test_root_tiles_single_cell_keeps_exact_bounds():
    (t,) = root_tiles(BBox(south=54.679, west=25.276, north=54.688, east=25.291), 1.0)
    assert (t.south, t.west, t.north, t.east) == (54.679, 25.276, 54.688, 25.291)


def test_split_quadrants():
    t = Tile("r0c0", 54.0, 25.0, 54.2, 25.4, 8.0)
    kids = split(t)
    assert [k.tile_id for k in kids] == ["r0c0.0", "r0c0.1", "r0c0.2", "r0c0.3"]
    assert all(k.size_km == 4.0 for k in kids)
    sw, se, nw, ne = kids
    assert sw.east == se.west and sw.north == nw.south
    assert (sw.south, sw.west) == (t.south, t.west)
    assert (ne.north, ne.east) == (t.north, t.east)
    assert se.east == t.east and nw.north == t.north
