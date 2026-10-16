"""The Getting Even layer must be keyed on the lattice the app actually draws.

The layer is looked up by `gekey(lat,lon) = lat.toFixed(3)+','+lon.toFixed(3)` against the
cells the app already has. When the grid moved to the projected equal-area lattice (#87),
this file stayed on the legacy 0.25-deg one: not one key matched, every lookup fell through
to the grey "all groups under-sampled" branch, and the map went uniformly grey under a
legend of six colours that could no longer appear. A join can fail completely and still
look like data, so the join is what gets asserted here.
"""

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pytest
import rasterio
import rasterio.warp
from PIL import Image

import border_mask
from gettingeven_inputs import observation_hash
from gettingeven_oracle import metric
from grid_lattice import Lattice

HERE = Path(__file__).resolve().parent
CA = HERE / "cluster_results" / "ca"
TEMPLATE = HERE / "webapp" / "index.html"
GE_FILE = CA / "webapp_data_gettingeven.json"
SIDECAR = CA / "gettingeven_grid.json"

# Restated, not imported, for the same reason as GE_GROUPS below: the red-channel value the app
# reads as "a cell, but too few records to name a group". Alpha 0 is "no cell here".
PNG_UNSCORED = 255

# Restated, not imported: a test that reads the builder's own grouping cannot catch the builder
# regrouping taxa (Mollusca quietly leaving Invertebrates, say).
GE_GROUPS = {
    "Fishes": ["Actinopterygii"],
    "Fungi": ["Fungi"],
    "Reptiles & Amphibians": ["Amphibia", "Reptilia"],
    "Invertebrates": ["Arachnida", "Insecta", "Mollusca"],
    "Mammals": ["Mammalia"],
    "Plants": ["Plantae"],
}


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


def _per_group_records():
    """Every group's per-cell record count, straight from the files the app loads."""
    index = json.loads((CA / "index.json").read_text())
    ntr = index["row_format"].index("n_train")

    def column(group):
        rows = json.loads((CA / index["files"][group]).read_text())[group]
        return [r[ntr] for r in rows]

    return {g: column(g) for g in index["files"]}


def test_the_shipped_layer_is_the_published_metric():
    """Re-derive Eckert's metric independently and demand the shipped file agrees, cell by cell.

    The layer's whole claim is that it is the 2025 Getting Even calculation on this year's
    cells. Nothing else here can tell a z-score of local share from the share-ratio heuristic
    it replaced: both produce a plausible six-colour map. So the metric itself is asserted --
    proportion, standardise per group, take the argmin -- computed here from the per-group
    files rather than by calling the builder.
    """
    ge = _ge()
    groups = _per_group_records()
    total = groups["All biodiversity"]
    floor = ge.get("min_records", 1)
    scored = [i for i, t in enumerate(total) if t >= floor]
    excluded = np.zeros(len(total), dtype=np.uint8)
    if ge.get("richness_gate"):
        tier = _sidecar()["tiers"]["25000"]
        mask = tier["gate_mask"]
        path = CA / mask["png"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == mask["sha256"]
        rowcol = _rowcol(ge["gettingeven"], _lattice(tier))
        excluded = np.asarray(Image.open(path))[*rowcol, 0]
        counts = np.asarray([np.sum([groups[g] for g in GE_GROUPS[cat]], axis=0) for cat in ge["cats"]])
        assert observation_hash(rowcol, total, counts) == tier["inputs"]["observations_sha256"]

    z = []
    for cat in ge["cats"]:
        counts = [sum(groups[g][i] for g in GE_GROUPS[cat]) for i in range(len(total))]
        prop = [counts[i] / total[i] for i in scored]
        mean = sum(prop) / len(prop)
        sd = (sum((p - mean) ** 2 for p in prop) / len(prop)) ** 0.5
        z.append([(p - mean) / sd for p in prop])

    rows = ge["gettingeven"]
    for k, i in enumerate(scored):
        eligible = [c for c in range(len(z)) if not (int(excluded[i]) & (1 << c))]
        want = min(eligible, key=lambda c: z[c][k]) if eligible else -1
        assert rows[i][2] == want, (
            f"cell {rows[i][0]},{rows[i][1]}: layer says {ge['cats'][rows[i][2]]}, the "
            f"published metric says {ge['cats'][want]}"
        )
        assert rows[i][3] == pytest.approx(z[want][k], abs=5e-4) if want >= 0 else rows[i][3] is None, (
            "the shipped z is not the winning category's z-score"
        )


def _verify_fine_tier(res, tier):
    """Check the shipped PNG even when preserving a tier produced no local output TIFF."""
    stack_dir = CA / f"grid_{res}m"
    lattice = _lattice(tier)

    def plane(group):
        with rasterio.open(stack_dir / (group.replace(" ", "_") + ".tif")) as ds:
            assert ds.shape == lattice.shape and ds.crs == lattice.crs and ds.transform == lattice.transform
            return ds.read(ds.descriptions.index("n_train") + 1).astype(float)

    total_plane = plane("All biodiversity")
    rowcol = np.where(np.isfinite(total_plane))
    total = total_plane[rowcol]
    counts = np.asarray(
        [sum(np.nan_to_num(plane(g))[rowcol] for g in GE_GROUPS[cat]) for cat in _ge()["cats"]]
    )
    assert observation_hash(rowcol, total, counts) == tier["inputs"]["observations_sha256"]
    mask = tier["gate_mask"]
    path = CA / mask["png"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == mask["sha256"]
    excluded = np.asarray(Image.open(path))[*rowcol, 0]
    want, best = metric(total, counts, tier["min_records"], excluded)
    path = CA / tier["png"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == tier["sha256"]
    image = np.asarray(Image.open(path))
    assert image.shape == (*lattice.shape, 4)
    categories = np.full(lattice.shape, PNG_UNSCORED, np.uint8)
    categories[rowcol] = np.where(want < 0, PNG_UNSCORED, want)
    assert np.array_equal(image[:, :, 0], categories), "fine-tier PNG disagrees with gated metric"
    present = np.zeros(lattice.shape, bool)
    present[rowcol] = True
    hidden = border_mask.hidden_for_tier(
        lattice.nrow, lattice.ncol, lattice.res, lattice.crs, lattice.x0, lattice.y1
    )
    assert np.array_equal(image[:, :, 3], np.where(present & ~hidden, 255, 0)), "fine-tier PNG alpha disagrees with mask"
    output = stack_dir / "gettingeven.tif"
    if output.exists():
        with rasterio.open(output) as ds:
            assert ds.shape == lattice.shape and ds.crs == lattice.crs and ds.transform == lattice.transform
            assert np.array_equal(ds.read(1)[rowcol], want)
            assert np.allclose(ds.read(2)[rowcol], best, atol=1e-6, equal_nan=True)


def test_gated_oracle_covers_both_tiers_when_stacks_are_available():
    """Full rebuilds check actual fine-tier counts too, not just the public coarse rows."""
    for res, tier in _sidecar()["tiers"].items():
        if int(res) != 25000 and (CA / f"grid_{res}m" / "All_biodiversity.tif").exists():
            _verify_fine_tier(res, tier)


def test_only_the_too_sparse_cells_are_grey():
    """Grey must mean "too few records to judge", not "the builder had nothing to say"."""
    ge = _ge()
    total = _per_group_records()["All biodiversity"]
    floor = ge.get("min_records", 1)
    for t, r in zip(total, ge["gettingeven"]):
        if t >= floor and not ge.get("richness_gate"):
            assert r[2] >= 0 and r[3] is not None, f"cell with {t} records has no group"
        elif t < floor:
            assert r[2] == -1 and r[3] is None, f"cell with {t} records was scored anyway"


def test_methodology_quotes_the_real_thin_cell_counts():
    """METHODOLOGY prices the record floor in exact cell counts, so a rebuild must move them.

    The claim it supports is that 18% of the coloured map is decided by the national mean and
    sd rather than by the cell. If a rebuild changes the lattice or the density vintage and
    the prose keeps the old counts, the doc understates or overstates how much of the map is a
    tie-break, and nothing else here reads those sentences.
    """
    doc = (HERE / "METHODOLOGY.md").read_text()
    if "thin cell's colour means" not in doc:
        pytest.skip("METHODOLOGY does not price the floor")
    ge = _ge()
    groups = _per_group_records()
    total = groups["All biodiversity"]
    floor = ge.get("min_records", 1)
    # The prose explicitly describes the ungated counterfactual. Recompute it
    # from source counts even when the production layer has the richness gate on.
    assert "With the richness gate off" in doc
    counts = np.asarray([np.sum([groups[g] for g in GE_GROUPS[cat]], axis=0) for cat in ge["cats"]])
    priorities, _ = metric(np.asarray(total), counts, floor, np.zeros(len(total), dtype=np.uint8))

    scored = [i for i, t in enumerate(total) if t >= floor]
    thin = [i for i in scored if total[i] == 1]
    named = {}
    for i in thin:
        named[ge["cats"][priorities[i]]] = named.get(ge["cats"][priorities[i]], 0) + 1

    assert f"{len(thin):,} of the {len(scored):,} scored cells" in doc, (
        f"METHODOLOGY should say {len(thin):,} of the {len(scored):,} scored cells hold one record"
    )
    # The tie-break claim is that every one-record cell lands on the same two groups.
    assert set(named) == {"Plants", "Invertebrates"}, (
        f"one-record cells now resolve to {sorted(named)}, so the two names in the doc are wrong"
    )
    for cat, n in named.items():
        assert f"{cat} ({n:,})" in doc, f"METHODOLOGY should say {cat} ({n:,})"


def _sidecar():
    if not SIDECAR.exists():
        pytest.skip("run build_gettingeven.py first")
    return json.loads(SIDECAR.read_text())


def _png(res_m):
    doc = _sidecar()
    tier = doc["tiers"].get(str(res_m))
    if not tier:
        pytest.skip(f"no {res_m} m tier built")
    path = CA / tier["png"]
    if not path.exists():
        pytest.skip(f"{tier['png']} not built")
    return tier, path, np.asarray(Image.open(path))


def _lattice(tier):
    """The lattice the PNG records, from the sidecar rather than from a raster.

    The stacks live in the grid release, not in the repo, so reopening one here made these two
    tests fail in the light CI job that has only the tracked files. The sidecar carries the CRS
    the builder used, and test_the_png_is_on_the_lattice_the_app_draws holds it to index.json.
    """
    lat = tier["lattice"]
    return Lattice(lat["res_m"], rasterio.crs.CRS.from_wkt(lat["crs"]))


def _rowcol(rows, lattice):
    """Where the app's cells land on the lattice, by the same projection the app uses."""
    x, y = rasterio.warp.transform("EPSG:4326", lattice.crs,
                                   [r[1] for r in rows], [r[0] for r in rows])
    row, col = rasterio.transform.rowcol(lattice.transform, x, y)
    return np.asarray(row), np.asarray(col)


def test_the_png_says_what_the_shipped_json_says():
    """The two 25 km products are one build or they are two maps.

    The PNG is the fill at both tiers and the JSON is the fill at 25 km until it is retired, so
    for as long as both ship they have to agree cell for cell. Nothing else here can catch a
    PNG written from a different run: it would still be a plausible six-colour map.
    """
    ge = _ge()
    tier, _, img = _png(25000)
    rows = ge["gettingeven"]
    row, col = _rowcol(rows, _lattice(tier))
    cat, alpha = img[row, col, 0], img[row, col, 3]
    want = np.array([PNG_UNSCORED if r[2] < 0 else r[2] for r in rows])
    wrong = np.flatnonzero((cat != want) & (alpha > 0))
    assert not len(wrong), (
        f"{len(wrong)} of {len(rows)} cells disagree between the PNG and the JSON "
        f"(e.g. cell {rows[wrong[0]][0]},{rows[wrong[0]][1]}): they are not one build"
    )
    assert tier["richness_gate"] == ge["richness_gate"], "the two products carry different gates"
    assert tier["min_records"] == ge["min_records"], "the two products carry different floors"


def test_the_png_hides_exactly_the_cells_the_vector_mask_hides():
    """Alpha 0 is the same "not Canada" verdict us_cells.json carries, or the border flickers.

    The app hides foreign cells through us_cells.json and the PNG through its alpha, and the two
    are drawn on top of each other at the zoom switch. Both come from border_mask.py, which is
    exactly the arrangement that let them drift apart before (see the Canada-mask step in
    rebuild-grid.yml).
    """
    ge = _ge()
    tier, _, img = _png(25000)
    rows = ge["gettingeven"]
    row, col = _rowcol(rows, _lattice(tier))
    hidden = {_gekey(r[0], r[1]) for r, a in zip(rows, img[row, col, 3]) if not a}
    us = set(json.loads((CA / "us_cells.json").read_text())["us_cells"])
    cells = {_gekey(r[0], r[1]) for r in rows}
    assert hidden == (us & cells), (
        f"{len(hidden ^ (us & cells))} cells are hidden by one mask and not the other"
    )


def test_the_png_is_on_the_lattice_the_app_draws():
    """A lattice-index PNG cannot fail loudly, so the lattice it was built on is asserted here.

    A coordinate-keyed file goes grey when the lattice moves, because every key misses; that is
    how the pre-#87 layer failed and it is the failure this whole file exists to catch. The PNG
    has no keys to miss. A lattice change silently re-points every pixel at a different place
    and the map still looks like a map, so the only warning available is this comparison.
    """
    doc = _sidecar()
    index = json.loads((CA / "index.json").read_text())
    for res, tier in doc["tiers"].items():
        got, res_m = tier["lattice"], int(res)
        assert got["res_m"] == res_m
        assert (got["x0"], got["y1"]) == (index["lattice"]["x0"], index["lattice"]["y1"]), (
            f"the {res} m PNG was built on a lattice with a different origin from index.json"
        )
        if res_m == index["res_m"]:
            assert (got["ncol"], got["nrow"]) == (index["lattice"]["ncol"], index["lattice"]["nrow"])
        path = CA / tier["png"]
        if not path.exists():
            continue
        h, w = np.asarray(Image.open(path)).shape[:2]
        assert (w, h) == (got["ncol"], got["nrow"]), (
            f"{tier['png']} is {w}x{h}, the lattice it records is {got['ncol']}x{got['nrow']}"
        )


def test_the_sidecar_hashes_the_png_beside_it():
    """A PNG rebuilt without its record, or a record kept past its PNG, is the same silent drift."""
    doc = _sidecar()
    for res, tier in doc["tiers"].items():
        path = CA / tier["png"]
        if not path.exists():
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == tier["sha256"], (
            f"{tier['png']} is not the file the {res} m record describes; rerun the builder "
            f"for that tier so the two ship together"
        )


def test_the_tiers_agree_on_the_gate_and_the_floor():
    """One map, one metric: the colours cannot change meaning at the zoom switch.

    Each tier is built by its own command from rasters CI cannot see, so nothing but this stops a
    gated 5 km tier shipping under an ungated 25 km one. The user crossing TIER_SWITCH_Z would
    see cells change group for a reason that is not in the data.
    """
    tiers = _sidecar()["tiers"]
    if len(tiers) < 2:
        pytest.skip("only one tier built")
    # The categories, not the lines: each tier's limit and cell count are that tier's own, and a
    # 5 km bottom quartile is not a 25 km one. What cannot differ is which groups were gated.
    gates = {res: t["gated_categories"] for res, t in tiers.items()}
    floors = {res: t["min_records"] for res, t in tiers.items()}
    assert len({json.dumps(sorted(g)) for g in gates.values()}) == 1, (
        f"the tiers gate different categories: {gates}"
    )
    assert len(set(floors.values())) == 1, f"the tiers carry different record floors: {floors}"


@pytest.mark.parametrize("corrupt", [None, "category", "alpha"])
def test_preserved_fine_png_is_verified_without_a_local_gettingeven_tiff(tmp_path, monkeypatch, corrupt):
    """A clean CI rebuild has source stacks and preserved PNGs, with no Getting Even TIFF."""
    from types import SimpleNamespace

    lattice = SimpleNamespace(
        shape=(2, 3), nrow=2, ncol=3, res=5000, x0=0, y1=2,
        crs=rasterio.crs.CRS.from_epsg(4326), transform=rasterio.transform.from_origin(0, 2, 1, 1),
    )
    monkeypatch.setattr(f"{__name__}.CA", tmp_path)
    monkeypatch.setattr(f"{__name__}._lattice", lambda _: lattice)
    monkeypatch.setattr(f"{__name__}._ge", lambda: {"cats": list(GE_GROUPS)})
    hidden = np.array([[False, True, False], [False, False, False]])
    monkeypatch.setattr(border_mask, "hidden_for_tier", lambda *args: hidden)
    stack_dir = tmp_path / "grid_5000m"
    stack_dir.mkdir()
    total = np.array([10, 20, 30, 0.])
    counts = np.array([[1, 3, 2, 0], [4, 2, 7, 0], [2, 6, 2, 0],
                       [1, 4, 5, 0], [2, 1, 4, 0], [0, 4, 10, 0.]])
    rowcol = (np.array([0, 0, 0, 1]), np.array([0, 1, 2, 0]))
    groups = {"All biodiversity": total}
    for category, values in zip(GE_GROUPS.values(), counts):
        for index, group in enumerate(category):
            groups[group] = values if index == 0 else np.zeros_like(values)
    for group, values in groups.items():
        data = np.full(lattice.shape, np.nan, np.float32)
        data[rowcol] = values
        with rasterio.open(stack_dir / (group.replace(" ", "_") + ".tif"), "w", driver="GTiff",
                           height=2, width=3, count=1, dtype="float32", crs=lattice.crs,
                           transform=lattice.transform, nodata=np.nan) as ds:
            ds.write(data, 1)
            ds.descriptions = ("n_train",)
    excluded = np.array([0, 4, 0, 0], np.uint8)
    gate = np.zeros(lattice.shape, np.uint8)
    gate[rowcol] = excluded
    gate_path = tmp_path / "gate.png"
    Image.fromarray(np.dstack([gate, gate, gate])).save(gate_path)
    want, _ = metric(total, counts, 1, excluded)
    categories = np.full(lattice.shape, PNG_UNSCORED, np.uint8)
    categories[rowcol] = np.where(want < 0, PNG_UNSCORED, want)
    alpha = np.zeros(lattice.shape, np.uint8)
    alpha[rowcol] = 255
    alpha[hidden] = 0
    if corrupt == "category":
        categories[0, 0] = (categories[0, 0] + 1) % len(GE_GROUPS)
    if corrupt == "alpha":
        alpha[0, 1] = 255
    image_path = tmp_path / "priority.png"
    Image.fromarray(np.dstack([categories, np.zeros_like(gate), np.zeros_like(gate), alpha])).save(image_path)
    tier = {
        "inputs": {"observations_sha256": observation_hash(rowcol, total, counts)}, "min_records": 1,
        "gate_mask": {"png": gate_path.name, "sha256": hashlib.sha256(gate_path.read_bytes()).hexdigest()},
        "png": image_path.name, "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }
    assert not (stack_dir / "gettingeven.tif").exists()
    if corrupt:
        with pytest.raises(AssertionError, match="fine-tier PNG"):
            _verify_fine_tier("5000", tier)
    else:
        _verify_fine_tier("5000", tier)
