"""Projected equal-area lattice for the where-to-blitz cell grid (#87).

The previous grid was defined in degrees (`RES = 0.25`, plate carree). A 0.25-deg cell is
27.8 km tall everywhere and 27.8*cos(lat) wide, so a cell labelled "25 km" was 21.0 km across
at Windsor and 2.9 km across at 84N, and its area varied by the same factor across the country.

The iNaturalist density COGs this app is built from are already in WGS84 Lambert Azimuthal
Equal Area (lat_0=45, lon_0=-100) at 1000 m. Defining the cells in that CRS at an integer
multiple of 1000 m makes every cell square, equal-area and actually the size it is labelled,
turns 1 km -> cell aggregation into an exact integer block reduce, and makes the 25 km tier an
exact 5x5 aggregate of the 5 km tier so the two zoom levels cannot disagree.

Lattice: the source COG extent snapped outward to a 25 km multiple, so both tiers share an
origin and 25 km cells nest 5x5 over 5 km cells.

    x [-2150000, 3350000]   5,500,000 m    25 km:  220 x 181 =    39,820 cells
    y [ -225000, 4300000]   4,525,000 m     5 km: 1100 x 905 =   995,500 cells
"""
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.warp import transform as warp_transform

# Snapped outward from the density COG extent (x [-2145000, 3336000], y [-218000, 4276000])
# to a 25 km multiple. Both tiers are exact integer divisions of this box.
X0, Y1 = -2150000, 4300000
WIDTH_M, HEIGHT_M = 5_500_000, 4_525_000
SRC_RES = 1000  # native resolution of the density COGs, in the lattice CRS


class Lattice:
    """A square, equal-area cell grid at `res` metres in the density COG's CRS."""

    def __init__(self, res, crs):
        if WIDTH_M % res or HEIGHT_M % res or res % SRC_RES:
            raise ValueError(f"GRID_RES={res} must divide the lattice and be a multiple of {SRC_RES} m")
        self.res = int(res)
        self.crs = crs
        self.x0, self.y1 = X0, Y1
        self.ncol = WIDTH_M // self.res
        self.nrow = HEIGHT_M // self.res
        self.transform = from_origin(X0, Y1, self.res, self.res)
        self.cell_km2 = (self.res / 1000.0) ** 2

    @property
    def shape(self):
        return (self.nrow, self.ncol)

    def centres(self, rows, cols):
        """Projected (x, y) centre of the given cell indices."""
        return (self.x0 + (np.asarray(cols) + 0.5) * self.res,
                self.y1 - (np.asarray(rows) + 0.5) * self.res)

    def centres_lonlat(self, rows, cols):
        """WGS84 (lon, lat) of the given cell centres."""
        x, y = self.centres(rows, cols)
        lon, lat = warp_transform(self.crs, "EPSG:4326", x.tolist(), y.tolist())
        return np.asarray(lon), np.asarray(lat)


def read_crs(url):
    with rasterio.open(url) as src:
        return src.crs


def _valid_mask(a, nodata):
    ok = np.isfinite(a)
    if nodata is not None:
        ok &= a != nodata
    return ok


def block_mean(url, lat):
    """Aggregate a 1 km raster that already shares the lattice CRS onto the lattice.

    Exact integer k x k block mean over the cells the source covers -- no reprojection, no
    resampling kernel. Cells the source does not cover come back NaN.
    """
    k = lat.res // SRC_RES
    with rasterio.open(url) as src:
        if src.crs != lat.crs or src.res != (float(SRC_RES), float(SRC_RES)):
            raise ValueError(f"{url}: expected {SRC_RES} m in {lat.crs}, got {src.res} in {src.crs}")
        ox = (src.transform.c - lat.x0) / SRC_RES
        oy = (lat.y1 - src.transform.f) / SRC_RES
        if ox != int(ox) or oy != int(oy) or ox < 0 or oy < 0:
            raise ValueError(f"{url}: origin {src.transform.c, src.transform.f} is off the lattice")
        ox, oy = int(ox), int(oy)
        a = src.read(1)
        ok = _valid_mask(a, src.nodata)

    tot = np.zeros((lat.nrow * k, lat.ncol * k), np.float32)
    cnt = np.zeros_like(tot)
    sl = (slice(oy, oy + a.shape[0]), slice(ox, ox + a.shape[1]))
    if sl[0].stop > tot.shape[0] or sl[1].stop > tot.shape[1]:
        raise ValueError(f"{url}: {a.shape} at ({oy},{ox}) overflows the {tot.shape} lattice")
    tot[sl] = np.where(ok, a, 0.0)
    cnt[sl] = ok
    s = tot.reshape(lat.nrow, k, lat.ncol, k).sum((1, 3))
    c = cnt.reshape(lat.nrow, k, lat.ncol, k).sum((1, 3))
    out = np.full(lat.shape, np.nan, np.float32)
    np.divide(s, c, out=out, where=c > 0, casting="unsafe")
    return out


def reproject_mean(path, lat, band=1, nonneg=False):
    """Cell-mean of a raster in any CRS onto the lattice, plus the fraction of the cell covered.

    Averages the value and a validity indicator through the same warp and divides, so the result
    is the mean over *valid* source pixels regardless of how the driver treats nodata. Returns
    (mean, coverage) where coverage is 0..1 and mean is NaN where coverage is 0.
    """
    with rasterio.open(path) as ds:
        a = ds.read(band).astype(np.float32)
        ok = _valid_mask(a, ds.nodata)
        if nonneg:
            ok &= a >= 0
        kw = {"src_transform": ds.transform, "src_crs": ds.crs, "dst_transform": lat.transform,
              "dst_crs": lat.crs, "resampling": Resampling.average,
              "src_nodata": None, "dst_nodata": None}
        num = np.zeros(lat.shape, np.float32)
        den = np.zeros(lat.shape, np.float32)
        reproject(np.where(ok, a, 0.0).astype(np.float32), num, **kw)
        reproject(ok.astype(np.float32), den, **kw)
    out = np.full(lat.shape, np.nan, np.float32)
    np.divide(num, den, out=out, where=den > 0)
    return out, den
