"""Gate the LAEA-to-Mercator rotation seam in build_grid_pmtiles._write_mercator (#116).

A LAEA lattice cell is a square that renders as a *rotated* quadrilateral once warped into
Mercator -- the vector lattice draws this shape faithfully (cellPoly() in webapp/index.html).
A GeoTIFF pixel grid cannot itself be rotated, so _write_mercator can only depict that shape as
a staircase of small axis-aligned pixels; if the intermediate resolution is too coarse (close to
one pixel per source cell), the staircase collapses to a single flat, axis-aligned rectangle per
cell and the raster tiers silently stop matching the vector lattice -- the exact bug this map
was reported for. Nothing checked that the "pinned" resolution was fine enough; it only checked
that the warp ran and reached the Arctic.
"""

import numpy as np
import rasterio
from rasterio.transform import from_origin

from build_grid_pmtiles import _write_mercator

LAEA = "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
RES = 25000
# Far east of the LAEA centre (lon_0=-100) and at a mid-Canada latitude, where a cell's true
# Mercator rotation is large and unambiguous -- Saint-Georges, QC is the region the bug was
# first spotted in.
X0, Y1 = 2_050_000, 700_000
NCOLS, NROWS = 10, 10


def _stripe_geotiff(tmp_path):
    """A LAEA raster with vertical (alternating-column) stripes, like a real band's data."""
    transform = from_origin(X0, Y1, RES, RES)
    data = np.zeros((NROWS, NCOLS), dtype=np.float64)
    data[:, ::2] = 1.0
    valid = np.ones_like(data, dtype=np.uint8) * 255
    bands = np.stack([(data * 255).astype(np.uint8)] * 3 + [valid])
    _write_mercator(str(tmp_path), bands, LAEA, transform, NCOLS, NROWS)
    return transform


def test_pinned_resolution_supersamples_the_cell(tmp_path):
    """The intermediate raster must carry several pixels per source cell, not ~1.

    _write_mercator's pin already divides by cos(south) (Mercator's own latitude stretch), so
    the actual px/cell ratio is somewhat below SUPERSAMPLE even when everything is healthy (~2.8
    at this test's latitude, matching the ~2.6 measured for the real full-country grid). The
    floor below is an absolute pixel count, not scaled by SUPERSAMPLE itself -- comparing the
    ratio to the very knob that produces it would be circular and could never catch a regression
    (e.g. SUPERSAMPLE reverted to 1, which still trivially clears any SUPERSAMPLE-relative bound).
    A single pixel cannot show a cell's rotated boundary at all, so the floor is picked well
    above 1 px/cell and below the ~2.8 healthy value.
    """
    out = tmp_path / "merc.tif"
    _stripe_geotiff(out)
    with rasterio.open(out) as dst:
        px_per_cell = RES / abs(dst.transform.a)
    assert px_per_cell >= 2.0, (
        f"only {px_per_cell:.1f} output pixels per {RES} m cell -- "
        "too coarse to depict a rotated LAEA cell in Mercator"
    )


def test_warped_cell_boundary_is_actually_rotated(tmp_path):
    """The warp must reproduce the true shear, not flatten it into an axis-aligned grid.

    Compares the column position of one stripe boundary at the top vs. the bottom of the output
    raster. In true Mercator space a LAEA cell edge this far from the projection centre shears by
    several pixels top-to-bottom; an unrotated (bugged) warp holds that column fixed.
    """
    out = tmp_path / "merc.tif"
    _stripe_geotiff(out)
    with rasterio.open(out) as dst:
        band = dst.read(1)  # 0/255 stripes; alpha (band 4) marks valid pixels
        alpha = dst.read(4)

    def boundary_col(row):
        vals = band[row]
        ok = alpha[row] > 0
        if ok.sum() < 2:
            return None
        idx = np.flatnonzero(ok)
        # first transition from low to high within the valid run
        run = vals[idx]
        transitions = np.flatnonzero(np.diff(run.astype(int)) != 0)
        return None if len(transitions) == 0 else idx[transitions[0]]

    h = band.shape[0]
    top = boundary_col(int(h * 0.1))
    bottom = boundary_col(int(h * 0.9))
    assert top is not None and bottom is not None, "warp produced no valid stripe boundary to compare"

    shift = abs(top - bottom)
    assert shift >= 3, (
        f"stripe boundary only shifts {shift} px top-to-bottom -- "
        "the LAEA cell's true Mercator rotation was lost in the warp"
    )
