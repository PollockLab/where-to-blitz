"""Gate the key joins between the shipped artefacts.

Every artefact the app loads after the base grid is joined back onto it by cell key or by
row position: us_cells.json by `gekey(lat,lon)`, webapp_data_gettingeven.json the same way,
the per-group webapp_data files by row index, and the values PNGs by lattice column/row. A
join that goes stale does not raise anywhere - a missed key renders as an unhighlighted
grey cell, which is also what a legitimately unremarkable cell looks like. That is how the
Getting Even layer shipped a stale key join for three days. These tests fail the build
instead.

`test_laea_parity.py` already covers the key *format* and the JS/Python projection seam;
`test_grid_values.py` covers the PNG colour bytes. This file only covers whether the keys
and shapes line up with the lattice the build declares in index.json.
"""

import json
from pathlib import Path

import matplotlib.image
import pytest

from goal_presets import PRESETS

CA = Path(__file__).parent / "cluster_results" / "ca"
KEY_DECIMALS = 3

pytestmark = pytest.mark.skipif(not (CA / "index.json").exists(), reason="no shipped build present")


def _index():
    return json.loads((CA / "index.json").read_text())


def _rows(group):
    idx = _index()
    return json.loads((CA / idx["files"][group]).read_text())[group]


def _key(lat, lon):
    return f"{lat:.{KEY_DECIMALS}f},{lon:.{KEY_DECIMALS}f}"


def _lattice_keys():
    """The key set the app builds its markers from, i.e. what every join must hit."""
    return {_key(r[0], r[1]) for r in _rows(_index()["groups"][0])}


def test_row_format_still_leads_with_lat_lon():
    """Every join below assumes columns 0 and 1 are the key; hold the build to that."""
    assert _index()["row_format"][:2] == ["lat", "lon"]


def test_group_files_agree_on_geometry_row_for_row():
    """The app swaps groups by re-reading values at the same row index it already drew."""
    idx = _index()
    base = _rows(idx["groups"][0])
    assert len(base) == idx["n_cells"]
    width = len(idx["row_format"])
    for group in idx["groups"][1:]:
        rows = _rows(group)
        assert len(rows) == len(base), f"{group} has {len(rows)} rows, base has {len(base)}"
        off = [i for i, (a, b) in enumerate(zip(rows, base)) if a[:2] != b[:2]]
        assert not off, f"{group} disagrees on geometry at {len(off)} rows, e.g. {off[:3]}"
        assert all(len(r) == width for r in rows), f"{group} has rows off the declared format"


def test_cell_keys_are_unique():
    """A duplicated key makes both cells resolve to whichever the join wrote last."""
    rows = _rows(_index()["groups"][0])
    assert len(_lattice_keys()) == len(rows)


def test_us_cells_keys_exist_in_the_lattice():
    """The Canada-only filter hides cells by key; an unmatched key hides nothing."""
    cells = json.loads((CA / "us_cells.json").read_text())["us_cells"]
    orphans = sorted(set(cells) - _lattice_keys())
    assert not orphans, f"{len(orphans)} mask keys hit no cell, e.g. {orphans[:3]}"


def test_gettingeven_keys_exist_in_the_lattice_and_carry_known_categories():
    """A missed GE key falls back to the neutral colour, which reads as a valid answer."""
    ge = json.loads((CA / "webapp_data_gettingeven.json").read_text())
    keys = _lattice_keys()
    orphans = [e for e in ge["gettingeven"] if _key(e[0], e[1]) not in keys]
    assert not orphans, f"{len(orphans)} GE keys hit no cell, e.g. {orphans[:3]}"
    assert len({_key(e[0], e[1]) for e in ge["gettingeven"]}) == len(ge["gettingeven"])
    bad = [e for e in ge["gettingeven"] if not -1 <= e[2] < len(ge["cats"])]
    assert not bad, f"{len(bad)} GE rows name a category outside cats, e.g. {bad[:3]}"


def test_values_pngs_cover_every_group_and_goal_at_the_lattice_shape():
    """The app indexes the PNG by lattice column/row, so a reshaped PNG shifts colours."""
    idx = _index()
    lat = idx["lattice"]
    missing, misshapen = [], []
    for group in idx["groups"]:
        for preset in PRESETS:
            for res in (25000, 5000):
                slug = f"{group.replace(' ', '_')}_{preset['name'].lower().replace(' ', '_')}_grid_{res}m.png"
                png = CA / "values" / slug
                if not png.exists():
                    missing.append(slug)
                    continue
                factor = idx["res_m"] // res
                h, w = matplotlib.image.imread(png).shape[:2]
                if (w, h) != (lat["ncol"] * factor, lat["nrow"] * factor):
                    misshapen.append((slug, w, h))
    assert not missing, f"{len(missing)} values PNGs are absent, e.g. {missing[:3]}"
    assert not misshapen, f"{len(misshapen)} values PNGs are off-lattice, e.g. {misshapen[:3]}"


def test_provenance_grid_matches_the_shipped_index():
    """provenance.json is what the docs quote; index.json is what the app loads."""
    idx = _index()
    grid = json.loads((CA / "provenance.json").read_text())["grid"]
    assert grid["res_m"] == idx["res_m"]
    assert grid["n_cells"] == idx["n_cells"] == len(_rows(idx["groups"][0]))
    assert grid["lattice"] == idx["lattice"]
    assert grid["crs"] == idx["crs"]
