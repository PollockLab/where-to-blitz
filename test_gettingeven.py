"""The Getting Even layer must be keyed on the lattice the app actually draws.

The layer is looked up by `gekey(lat,lon) = lat.toFixed(3)+','+lon.toFixed(3)` against the
cells the app already has. When the grid moved to the projected equal-area lattice (#87),
this file stayed on the legacy 0.25-deg one: not one key matched, every lookup fell through
to the grey "all groups under-sampled" branch, and the map went uniformly grey under a
legend of six colours that could no longer appear. A join can fail completely and still
look like data, so the join is what gets asserted here.
"""
import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CA = HERE / "cluster_results" / "ca"
TEMPLATE = HERE / "webapp" / "index.html"
GE_FILE = CA / "webapp_data_gettingeven.json"


def _gekey(lat, lon):
    """The JS key, reproduced: Number.toFixed(3) on both coordinates."""
    return f"{lat:.3f},{lon:.3f}"


def _ge():
    if not GE_FILE.exists():
        pytest.skip("run build_gettingeven.py first")
    return json.loads(GE_FILE.read_text())


def _lattice_keys():
    index = json.loads((CA / "index.json").read_text())
    group = index["groups"][0]
    rows = json.loads((CA / index["files"][group]).read_text())[group]
    return {_gekey(r[0], r[1]) for r in rows}


def test_every_cell_the_app_draws_has_a_getting_even_row():
    """Total-miss is the failure mode, so nothing less than full coverage passes."""
    keys = {_gekey(r[0], r[1]) for r in _ge()["gettingeven"]}
    lattice = _lattice_keys()
    missing = lattice - keys
    assert not missing, (
        f"{len(missing)} of {len(lattice)} lattice cells have no Getting Even row "
        f"(e.g. {sorted(missing)[:3]}); those cells render grey as if under-sampled"
    )


def test_the_layer_is_not_uniformly_grey():
    """Every category index must be reachable, or the legend promises colours nobody sees."""
    rows = _ge()["gettingeven"]
    named = {r[2] for r in rows if r[2] >= 0}
    assert named == set(range(len(_ge()["cats"]))), (
        f"categories {sorted(set(range(len(_ge()['cats']))) - named)} never appear on the map"
    )


def test_categories_match_the_palette_and_both_languages():
    """The legend indexes GE_PAL by ge_cats position, so all three lists must line up."""
    src = TEMPLATE.read_text()
    cats = _ge()["cats"]

    pal = re.search(r"GE_PAL=\[([^\]]*)\]", src)
    assert pal, "GE_PAL is gone from the template"
    assert len(pal.group(1).split(",")) == len(cats), (
        "GE_PAL and the data file's categories are different lengths: the legend would "
        "colour a category with another category's swatch"
    )

    labels = re.findall(r"ge_cats:\[([^\]]*)\]", src)
    assert len(labels) == 2, f"expected an EN and a FR ge_cats, found {len(labels)}"
    for group in labels:
        assert len(group.split('","')) == len(cats)

    assert [c.strip().strip('"') for c in labels[0].split(",")] == cats, (
        "the English ge_cats no longer matches the order the data file was built in"
    )
