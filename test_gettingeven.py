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
    if ge.get("richness_gate"):
        pytest.skip(f"built with the richness gate on: {ge['richness_gate']}")
    groups = _per_group_records()
    total = groups["All biodiversity"]
    floor = ge.get("min_records", 1)
    scored = [i for i, t in enumerate(total) if t >= floor]

    z = []
    for cat in ge["cats"]:
        counts = [sum(groups[g][i] for g in GE_GROUPS[cat]) for i in range(len(total))]
        prop = [counts[i] / total[i] for i in scored]
        mean = sum(prop) / len(prop)
        sd = (sum((p - mean) ** 2 for p in prop) / len(prop)) ** 0.5
        z.append([(p - mean) / sd for p in prop])

    rows = ge["gettingeven"]
    for k, i in enumerate(scored):
        want = min(range(len(z)), key=lambda c: z[c][k])
        assert rows[i][2] == want, (
            f"cell {rows[i][0]},{rows[i][1]}: layer says {ge['cats'][rows[i][2]]}, the "
            f"published metric says {ge['cats'][want]}"
        )
        assert rows[i][3] == pytest.approx(z[want][k], abs=5e-4), (
            "the shipped z is not the winning category's z-score"
        )


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
    if ge.get("richness_gate"):
        pytest.skip(f"built with the richness gate on: {ge['richness_gate']}")
    total = _per_group_records()["All biodiversity"]
    floor = ge.get("min_records", 1)
    rows = ge["gettingeven"]

    scored = [i for i, t in enumerate(total) if t >= floor]
    thin = [i for i in scored if total[i] == 1]
    named = {}
    for i in thin:
        named[ge["cats"][rows[i][2]]] = named.get(ge["cats"][rows[i][2]], 0) + 1

    assert f"{len(thin):,} of the {len(scored):,} scored cells" in doc, (
        f"METHODOLOGY should say {len(thin):,} of the {len(scored):,} scored cells hold one record"
    )
    # The tie-break claim is that every one-record cell lands on the same two groups.
    assert set(named) == {"Plants", "Invertebrates"}, (
        f"one-record cells now resolve to {sorted(named)}, so the two names in the doc are wrong"
    )
    for cat, n in named.items():
        assert f"{cat} ({n:,})" in doc, f"METHODOLOGY should say {cat} ({n:,})"
