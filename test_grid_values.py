"""Gate the colour parity of the client-rendered grid (#116).

The webapp paints its LAEA cell polygons with colours read back from the values PNGs
(build_grid_values.py), replacing the warped raster PMTiles whose staircased edges could
never match the vector lattice. That makes the PNG the single carrier of the goal
colouring: if its bytes stop being viridis(clip(blend / preset scale, 0, 1)) with a
validity alpha,
or the PNG encode stops being lossless, every cell on the map silently changes colour
with nothing else failing. This holds the exported bytes to the blend definition.
"""

import matplotlib
import matplotlib.image
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from build_grid_values import render_goal_rgba
from goal_presets import AXES, PRESETS
from grid_schema import BAND_INDEX, BANDS

LAEA = "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
RES = 25000
# Deep inside Canada (~54 N, mid-country), so the border mask hides nothing and the
# expected alpha is driven purely by the data's own NaNs.
X0, Y1 = -500_000, 1_000_000
NCOLS, NROWS = 10, 10

_VIRIDIS = matplotlib.colormaps["viridis"]
_LUT = (np.asarray([_VIRIDIS(i / 255.0) for i in range(256)])[:, :3] * 255).round().astype(np.uint8)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """A small 7-band LAEA stack with a gradient in every axis and one NaN hole."""
    rng = np.random.default_rng(87)
    data = {name: rng.random((NROWS, NCOLS)) for name in BANDS}
    data["discover"][0, 0] = np.nan  # nodata cell -> alpha 0
    path = tmp_path_factory.mktemp("stack") / "Testae.tif"
    with rasterio.open(path, "w", driver="GTiff", width=NCOLS, height=NROWS,
                       count=len(BANDS), dtype="float32", crs=LAEA,
                       transform=from_origin(X0, Y1, RES, RES)) as dst:
        for name in BANDS:
            dst.write(data[name].astype(np.float32), BAND_INDEX[name])
    return path, data


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["name"])
def test_rgba_is_viridis_of_the_goal_blend(stack, preset):
    path, data = stack
    rgba = render_goal_rgba(path, preset, RES)

    blend = np.zeros((NROWS, NCOLS))
    for ax, w in zip(AXES, preset["w"], strict=True):
        if w:
            blend += w * np.nan_to_num(data[ax])
    idx = (np.clip(blend / preset["scale"], 0, 1) * 255).round().astype(np.uint8)

    valid = np.isfinite(data["discover"])
    assert np.array_equal(rgba[..., 3] == 255, valid), "alpha must be the validity mask"
    assert np.array_equal(rgba[valid][:, :3], _LUT[idx[valid]]), \
        "cell colours must be viridis(clip(blend / preset scale, 0, 1))"


def test_png_roundtrip_is_lossless(stack, tmp_path):
    """What the browser reads back through a canvas must be byte-identical to the render."""
    path, _ = stack
    rgba = render_goal_rgba(path, PRESETS[0], RES)
    out = tmp_path / "values.png"
    matplotlib.image.imsave(out, rgba)
    back = (matplotlib.image.imread(out) * 255).round().astype(np.uint8)
    assert np.array_equal(back, rgba)
