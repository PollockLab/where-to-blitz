"""Which lattice cells fall outside Canada, resolved at each tier's own resolution.

The app hides foreign cells so the cross-border drop-off reads as a data edge rather than a real
gap (#5). Doing that classification once at 25 km and inheriting the verdict to the 5 km children
punched diagonal holes through the Canadian side of the border: a 25 km cell whose centre sits just
south of the line took its Canadian fifth-cells down with it. So each tier is clipped on its own
grid instead, and a 25 km cell is hidden only when every one of its 5 km children is — the "buffer"
that keeps border cells clickable.

Land is decided by containment in the Natural Earth 1:50m polygons, exact at the tier's resolution.
Open water belongs to no polygon; those cells fall back to the nearest-country test on the coarse
25 km grid (#72, #86 — it keeps Canadian coastal water and drops the Alaska panhandle and western
Greenland). Water has no visible coastline to stair-step against, so the coarse fallback costs
nothing on screen and saves a scipy dependency.
"""
import json
from pathlib import Path

import numpy as np
import shapely
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape

HERE = Path(__file__).resolve().parent
BOUNDARIES = HERE / "cluster_results" / "ca" / "na_boundaries.geojson"
FOREIGN = ("US", "GL")
SEGMENT_DEG = 0.02          # ~2 km, well inside a 5 km cell
FINE_RES = 5000
COARSE_RES = 25000
FACTOR = COARSE_RES // FINE_RES


def _geoms(dst_crs):
    """Boundary polygons in dst_crs, densified first.

    The 49th parallel is a straight line in lon/lat and a curve in LAEA, and transform_geom only
    moves vertices — so the long vertex-free runs across the prairies reprojected to chords that
    bowed north of the true border and swallowed a strip of southern Canada (Osoyoos, Emerson).
    Segmentizing to well under a cell width before projecting keeps the edge where it belongs.
    """
    with BOUNDARIES.open() as fh:
        bd = json.load(fh)
    out = {}
    for f in bd["features"]:
        dense = shapely.segmentize(shape(f["geometry"]), SEGMENT_DEG)
        out[f["properties"]["country"]] = transform_geom("EPSG:4326", dst_crs, mapping(dense))
    return out


def _cover(geom, height, width, transform, all_touched=False):
    return rasterize([(geom, 1)], out_shape=(height, width), transform=transform,
                     fill=0, all_touched=all_touched, dtype="uint8").astype(bool)


def _nearest_country_water(undecided, transform, crs, geoms):
    """Nearest-country verdict for cells inside no polygon, evaluated on the coarse grid."""
    rows, cols = np.nonzero(undecided)
    if not len(rows):
        return np.zeros(undecided.shape, dtype=bool)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    lons, lats = warp_transform(crs, "EPSG:4326", xs.tolist(), ys.tolist())
    pts = shapely.points(lons, lats)
    d_ca = shapely.distance(pts, shape(geoms["CA"]))
    hide = np.zeros(len(rows), dtype=bool)
    for name in FOREIGN:
        hide |= shapely.distance(pts, shape(geoms[name])) < d_ca
    out = np.zeros(undecided.shape, dtype=bool)
    out[rows, cols] = hide
    return out


def hidden_fine(crs, origin_x, origin_y, height, width):
    """Boolean (height, width) mask of 5 km cells to hide, True = outside Canada."""
    fine_tr = Affine(FINE_RES, 0, origin_x, 0, -FINE_RES, origin_y)
    proj = _geoms(crs)
    # all_touched: a 5 km cell holding any Canadian ground is kept, not just one centred on it.
    # The 25 km rule below ("hidden only if every child is") is then the same rule one tier up.
    ca = _cover(proj["CA"], height, width, fine_tr, all_touched=True)
    foreign = np.zeros_like(ca)
    for name in FOREIGN:
        foreign |= _cover(proj[name], height, width, fine_tr)

    # Water: decide once on the 25 km grid in lon/lat, then broadcast back to the 5 km children.
    ch, cw = height // FACTOR, width // FACTOR
    coarse_tr = Affine(COARSE_RES, 0, origin_x, 0, -COARSE_RES, origin_y)
    undecided = np.ones((ch, cw), dtype=bool)
    latlon = _geoms("EPSG:4326")
    water_hidden = _nearest_country_water(undecided, coarse_tr, crs, latlon)
    water_hidden = np.repeat(np.repeat(water_hidden, FACTOR, 0), FACTOR, 1)[:height, :width]

    return np.where(ca, False, np.where(foreign, True, water_hidden))


def hidden_coarse(fine_hidden):
    """A 25 km cell is hidden only when all 25 of its 5 km children are (the border buffer)."""
    h, w = fine_hidden.shape
    ch, cw = h // FACTOR, w // FACTOR
    return fine_hidden[:ch * FACTOR, :cw * FACTOR].reshape(ch, FACTOR, cw, FACTOR).all(axis=(1, 3))


_TIER_CACHE = {}


def hidden_for_tier(height, width, res_m, crs, origin_x, origin_y):
    """Cells outside Canada at this tier's own resolution, cached per lattice.

    The 25 km verdict is inherited from its 5 km children rather than recomputed on the coarse
    grid, which is the whole point of hidden_coarse above: classifying at 25 km directly punches
    diagonal holes through the Canadian side of the border.
    """
    key = (str(crs), origin_x, origin_y, height, width, res_m)
    if key not in _TIER_CACHE:
        if res_m == COARSE_RES:
            fine = hidden_fine(crs, origin_x, origin_y, height * FACTOR, width * FACTOR)
            _TIER_CACHE[key] = hidden_coarse(fine)
        else:
            _TIER_CACHE[key] = hidden_fine(crs, origin_x, origin_y, height, width)
    return _TIER_CACHE[key]
