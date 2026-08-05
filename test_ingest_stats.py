"""Acceptance tests for ingest_stats — runs against the 4 real richness COGs.
Needs internet (live Arbutus COGs + TiTiler). Run: .venv/bin/python -m pytest -q
"""
import urllib.parse

import pytest
import requests
from ingest_stats import compute_render_stats, stac_item

BUCKET = "https://object-arbutus.cloud.computecanada.ca/bq-io/blitz-the-gap"
TILER = "https://tiler.biodiversite-quebec.ca"
LAYERS = {
    "SR_allSDMs": 1200, "SR_verts": 700, "SR_plants": 700, "SR_butterflies": 100,
}


@pytest.fixture(scope="module", params=list(LAYERS))
def layer(request):
    name = request.param
    url = f"{BUCKET}/{name}.tif"
    return name, url, LAYERS[name], compute_render_stats(url)


def test_vmax_within_data_range(layer):
    _, _, _, s = layer
    assert 0 < s["render_vmax"] <= s["maximum"], "ceiling must sit inside the data"


def test_vmax_improves_colormap_use(layer):
    """The metadata ceiling must use at least as much of the colourmap as the
    hardcoded one — the whole point of the design."""
    _, _, hardcoded, s = layer
    span = s["p98"] - s["p2"]
    util_hard = min(span / hardcoded, 1.0)
    util_new = min(span / s["render_vmax"], 1.0)
    assert util_new >= util_hard - 1e-9


def test_cross_check_against_live_titiler(layer):
    """Independent implementation must agree — the honesty gate."""
    _, url, _, s = layer
    u = f"{TILER}/cog/statistics?url={urllib.parse.quote(url, safe='')}"
    band = next(iter(requests.get(u, timeout=40).json().values()))
    assert abs(band["max"] - s["maximum"]) < 1.0
    assert abs(band["percentile_98"] - s["p98"]) < 1.0


def test_stac_item_is_well_formed(layer):
    name, url, _, s = layer
    item = stac_item(url, s, title=name)
    assert item["type"] == "Feature"
    r = item["properties"]["renders"]["default"]
    assert r["colormap_name"] == "viridis"
    assert r["rescale"][0][0] == 0 and r["rescale"][0][1] > 0
    assert item["properties"]["license"]
    assert item["assets"]["cog"]["href"] == url


def test_metadata_drives_a_real_tile(layer):
    """End to end: the Item's rescale must render a valid PNG from the tiler."""
    name, url, _, s = layer
    item = stac_item(url, s, title=name)
    lo, hi = item["properties"]["renders"]["default"]["rescale"][0]
    tile = (f"{TILER}/cog/tiles/WebMercatorQuad/3/2/2@1x.png"
            f"?url={urllib.parse.quote(url, safe='')}&rescale={lo:g},{hi:g}&colormap_name=viridis")
    resp = requests.get(tile, timeout=40)
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"


def test_sparse_branch_on_synthetic_sdm(tmp_path):
    """The "95%-zeros" SDM branch is exercised by no public COG today (verified:
    the only public per-species layers are nodata-masked habitat-area rasters,
    not zero-encoded [0,1] probabilities). Synthesise one so the branch is tested:
    a 200x200 probability raster, 98.5% exact zeros, the rest in (0,1]. So even
    the whole-array p98 is 0; the ceiling must come from the *occupied* pixels,
    not be dragged to 0 by the zeros (which would render the layer invisible)."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds
    rng = np.random.default_rng(0)
    arr = np.zeros((200, 200), dtype="float32")
    occupied = rng.random(arr.shape) < 0.015           # ~1.5% occupied -> p98 of full array is 0
    arr[occupied] = rng.uniform(0.2, 1.0, occupied.sum()).astype("float32")
    path = tmp_path / "synthetic_sdm.tif"
    with rasterio.open(path, "w", driver="GTiff", height=200, width=200, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_bounds(-1, -1, 1, 1, 200, 200)) as dst:
        dst.write(arr, 1)

    s = compute_render_stats(str(path))
    assert s["sparse_branch"] is True, "should detect >50% zeros and take sparse branch"
    assert s["zero_frac"] > 0.9
    # dense (whole-array) p98 would be 0 here (96% zeros); the occupied-pixel p98
    # must be a real probability, so the layer renders visibly.
    dense_p98 = float(np.percentile(arr, 98))
    assert dense_p98 == 0.0
    assert 0.2 <= s["render_vmax"] <= 1.0
    item = stac_item(str(path), s, title="synthetic_sdm")
    assert item["properties"]["renders"]["default"]["rescale"][0][1] == round(s["render_vmax"], 2)


def test_empty_branch_is_safe():
    """Boundary: degenerate stats must not crash Item building."""
    from ingest_stats import _empty_stats
    s = _empty_stats("EPSG:4326", [0, 0, 1, 1], 10, 10, "no valid pixels")
    item = stac_item("http://x/y.tif", s)
    assert item["properties"]["renders"]["default"]["rescale"][0][1] == 1.0
    assert item["properties"]["openbiodiversity:flag"] == "hide_empty"
