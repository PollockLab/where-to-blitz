"""Gate the JS/Python projection seam.

The app snaps a click by running its own LAEA implementation, then looks the resulting
"lat,lon" key up in artefacts Python wrote with pyproj. If the two projections disagree by
more than half a key digit, the lookup silently misses and cells go dead - which looks
exactly like the grid-misalignment bug this map was reported for. Nothing re-checked the
port's accuracy; the template only claimed it in a comment.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import grid_lattice

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
KEY_DECIMALS = 3
# A key is a rounded value, so anything under half an ulp of the last digit can never
# change it. Half of 1e-3 degrees is ~55 m; we hold the port far tighter than that.
TOL_DEG = 1e-7

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _js_laea():
    src = TEMPLATE.read_text()
    block = re.search(r"const LAEA=\(\(\)=>\{.*?\n\}\)\(\);", src, re.DOTALL)
    assert block, "the LAEA implementation is no longer where the gate expects it"
    return block.group(0)


def _sample_centres(n=400):
    """Cell centres spread over the lattice, in the projected CRS."""
    rng = np.random.default_rng(0)
    xs = grid_lattice.X0 + (rng.integers(0, grid_lattice.WIDTH_M // 25000, n) + 0.5) * 25000
    ys = grid_lattice.Y1 - (rng.integers(0, grid_lattice.HEIGHT_M // 25000, n) + 0.5) * 25000
    return xs, ys


def test_js_and_pyproj_agree_on_cell_centres():
    from pyproj import CRS, Transformer

    crs = CRS.from_proj4(
        "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
    xs, ys = _sample_centres()
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)

    script = _js_laea() + (
        "\nconst pts=JSON.parse(process.argv[1]);"
        "\nconsole.log(JSON.stringify(pts.map(p=>LAEA.inv(p[0],p[1]))));"
    )
    pts = json.dumps([[float(x), float(y)] for x, y in zip(xs, ys)])
    out = subprocess.run(["node", "-e", script, "--", pts],
                         capture_output=True, text=True, check=True).stdout
    js = np.array(json.loads(out))

    assert np.abs(js[:, 0] - lat).max() < TOL_DEG
    assert np.abs(js[:, 1] - lon).max() < TOL_DEG


def test_keys_round_trip_identically_in_both_languages():
    """Python's round-half-even and JS's toFixed must produce the same key string."""
    from pyproj import CRS, Transformer

    crs = CRS.from_proj4(
        "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
    xs, ys = _sample_centres()
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    py_keys = [f"{a:.{KEY_DECIMALS}f},{b:.{KEY_DECIMALS}f}" for a, b in zip(lat, lon)]

    script = _js_laea() + (
        f"\nconst D={KEY_DECIMALS};"
        "\nconst pts=JSON.parse(process.argv[1]);"
        "\nconsole.log(JSON.stringify(pts.map(p=>{const q=LAEA.inv(p[0],p[1]);"
        "return q[0].toFixed(D)+','+q[1].toFixed(D);})));"
    )
    pts = json.dumps([[float(x), float(y)] for x, y in zip(xs, ys)])
    out = subprocess.run(["node", "-e", script, "--", pts],
                         capture_output=True, text=True, check=True).stdout
    js_keys = json.loads(out)

    mismatched = [(p, j) for p, j in zip(py_keys, js_keys) if p != j]
    assert not mismatched, f"{len(mismatched)} keys differ, e.g. {mismatched[:3]}"


def test_mask_keys_use_the_agreed_format():
    """The shipped mask must be keyed the way the app looks it up."""
    mask = Path("cluster_results/ca/us_cells.json")
    if not mask.exists():
        pytest.skip("no mask artefact present")
    cells = json.loads(mask.read_text())["us_cells"]
    pattern = re.compile(rf"^-?\d+\.\d{{{KEY_DECIMALS}}},-?\d+\.\d{{{KEY_DECIMALS}}}$")
    bad = [c for c in cells if not pattern.match(c)]
    assert not bad, f"{len(bad)} mask keys are malformed, e.g. {bad[:3]}"

    src = TEMPLATE.read_text()
    assert f"toFixed({KEY_DECIMALS})" in src, "the app no longer keys cells to 3 decimals"
