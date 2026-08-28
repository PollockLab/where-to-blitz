"""Rebuilds the Getting Even layer on the projected equal-area lattice (#59).

The layer colours each cell by the taxonomic group most under-represented there, birds
excluded (eBird already covers them, as in the 2025 challenge map). The metric is the one
Lucas Eckert used for the 2025 county map, moved from census-district polygons onto the
25 km / 5 km cells this app draws:

  1. proportion  p_g = records of group g in the cell / all iNaturalist records in the cell
  2. z-score     z_g = (p_g - mean over cells) / sd over cells, standardised per group
  3. priority    the group with the lowest z: the one most under-recorded here *relative to
                 how often it is recorded everywhere else*, not in absolute terms. Without the
                 standardisation nearly every cell would come back fungi or fish.

Step 2 is what the previous builder did differently: it scored a group's local share against
its national share (`1 - local/national`), a ratio rather than a distributional rank, and so
answered a different question from the published challenge.

Richness gate (optional, off unless the rasters are supplied). Eckert dropped a group from the
running in units whose modelled richness for that group sits in the national bottom quartile --
a place with few herps to find is not a place to send herpers. The three source rasters live in
the lab SharePoint, not in any public bucket, so the step runs only when it is pointed at them:

    GE_RICHNESS_HERP=ar.richness.tif GE_RICHNESS_MAMMAL=mammal.richness.tif \
    GE_RICHNESS_PLANT=plant.richness.stacks.tif python build_gettingeven.py

Any CRS or resolution is accepted; each raster is averaged onto the lattice cell. Without them
the run prints and records the layer as ungated -- a missing input never passes silently for a
metric that has no such step.

Sources. The 25 km tier is built from the per-group `webapp_data_<GROUP>.json` files, so the
layer is keyed on exactly the cells the app draws and cannot drift from them; finer tiers are
built from the `n_train` band of the per-group lattice stacks. The pre-#87 file was hand-made
on the legacy 0.25-deg lattice with no producer, so the lattice migration left every lookup
missing and the whole layer rendered grey.

Outputs (per tier):
  cluster_results/ca/grid_<RES>m/gettingeven.tif    2 bands: priority index, priority z
  cluster_results/ca/webapp_data_gettingeven.json   the app's rows            (25 km tier only)

Usage:  python build_gettingeven.py                 # 25 km tier, after build_fullgrid_ca.py
        GRID_RES=5000 python build_gettingeven.py   # 5 km tier: raster only
"""
import json
import os

import numpy as np
import rasterio
import rasterio.warp

from grid_lattice import Lattice, reproject_mean

OUT_DIR = "cluster_results/ca"
RES = int(os.environ.get("GRID_RES", "25000"))   # lattice cell size in metres
JSON_RES = 25000                                 # the tier the app's vector layer ships from
TOTAL = "All biodiversity"                       # denominator: every record, all taxa

# A cell needs this many records before its composition means anything. At the default of 1 a
# single-record cell scores five groups at p = 0, and the argmin then falls to whichever group
# has the largest national mean/sd -- so ~1,900 near-empty cells come back Plants or
# Invertebrates on tie-break alone. That is the honest reading of "almost nothing has been
# recorded here", and it is what the county metric does too; raise it to score only cells whose
# composition is real, at the cost of more grey.
MIN_RECORDS = float(os.environ.get("GE_MIN_RECORDS", "1"))

# Display category -> the index.json groups that feed it. The order matches GE_PAL and ge_cats
# in webapp/index.html; changing it recolours the legend. Aves is deliberately absent.
GE_GROUPS = {
    "Fishes": ["Actinopterygii"],
    "Fungi": ["Fungi"],
    "Reptiles & Amphibians": ["Amphibia", "Reptilia"],
    "Invertebrates": ["Arachnida", "Insecta", "Mollusca"],
    "Mammals": ["Mammalia"],
    "Plants": ["Plantae"],
}
CATS = list(GE_GROUPS)

# Category -> env var holding its modelled-richness raster (Eckert's ar / mammal / plant stacks).
RICHNESS_ENV = {
    "Reptiles & Amphibians": "GE_RICHNESS_HERP",
    "Mammals": "GE_RICHNESS_MAMMAL",
    "Plants": "GE_RICHNESS_PLANT",
}
RICHNESS_PCT = 25   # a group is dropped below this percentile of its own richness


def _stack(group):
    return os.path.join(OUT_DIR, f"grid_{RES}m", group.replace(" ", "_") + ".tif")


def _lattice():
    with rasterio.open(_stack(TOTAL)) as ds:
        lat = Lattice(RES, ds.crs)
        if (lat.shape, lat.transform) != (ds.shape, ds.transform):
            raise SystemExit(f"{_stack(TOTAL)} is not on the {RES} m lattice; rerun build_fullgrid_ca.py")
    return lat


def cells_from_json(lat):
    """The app's own cells: (row, col), all-taxa records, per-category records.

    Read from the same files the app loads, so the layer is keyed on the cells it will be
    looked up by. Reading the lattice stacks instead would silently mix vintages whenever the
    rasters and the JSON come from different builds.
    """
    with open(os.path.join(OUT_DIR, "index.json")) as fh:
        index = json.load(fh)
    wanted = [TOTAL] + [g for cat in CATS for g in GE_GROUPS[cat]]
    missing = [g for g in wanted if g not in index["files"]]
    if missing:
        raise SystemExit(f"index.json has no file for: {', '.join(missing)}")
    ntr = index["row_format"].index("n_train")

    def column(group):
        with open(os.path.join(OUT_DIR, index["files"][group])) as fh:
            rows = json.load(fh)[group]
        if len(rows) != len(coords):
            raise SystemExit(f"{group}: {len(rows)} rows, expected {len(coords)}")
        return np.array([r[ntr] for r in rows], float)

    with open(os.path.join(OUT_DIR, index["files"][TOTAL])) as fh:
        coords = [(r[0], r[1]) for r in json.load(fh)[TOTAL]]
    total = column(TOTAL)
    counts = np.array([sum(column(g) for g in GE_GROUPS[cat]) for cat in CATS])

    x, y = rasterio.warp.transform("EPSG:4326", lat.crs,
                                   [c[1] for c in coords], [c[0] for c in coords])
    row, col = rasterio.transform.rowcol(lat.transform, x, y)
    row, col = np.asarray(row), np.asarray(col)
    if row.min() < 0 or col.min() < 0 or row.max() >= lat.nrow or col.max() >= lat.ncol:
        raise SystemExit("a webapp cell centre falls outside the lattice")
    return coords, (row, col), total, counts


def cells_from_stacks(lat):
    """Every on-mask cell of the lattice stacks: (row, col), all-taxa and per-category records."""
    def plane(group):
        with rasterio.open(_stack(group)) as ds:
            return ds.read(ds.descriptions.index("n_train") + 1).astype(float)

    total_plane = plane(TOTAL)
    row, col = np.where(np.isfinite(total_plane))
    total = total_plane[row, col]
    counts = np.array([sum(np.nan_to_num(plane(g))[row, col] for g in GE_GROUPS[cat])
                       for cat in CATS])
    return None, (row, col), total, counts


def zscores(total, counts):
    """Standardised local share per category, NaN where the cell is not scored.

    One standardisation per category over the scored cells: a value says where this cell sits
    in the national distribution *of that category's share*, which is what makes the six
    categories comparable to each other in the argmin below.
    """
    scored = np.isfinite(total) & (total >= MIN_RECORDS)
    prop = np.full(counts.shape, np.nan)
    np.divide(counts, total, out=prop, where=scored)
    mean, sd = np.nanmean(prop, axis=1), np.nanstd(prop, axis=1)
    if not np.all(sd > 0):
        flat = [CATS[i] for i in np.where(sd <= 0)[0]]
        raise SystemExit(f"no variation in {', '.join(flat)}: refusing to write a constant layer")
    return (prop - mean[:, None]) / sd[:, None], scored


def apply_richness_gate(z, lat, rowcol, scored):
    """Drop a category where its own modelled richness is in the national bottom quartile.

    Returns what was actually gated, so the run can report the step rather than let an absent
    raster look like a metric that never had one.
    """
    gated = []
    for i, cat in enumerate(CATS):
        path = os.environ.get(RICHNESS_ENV.get(cat, ""))
        if not path:
            continue
        if not os.path.exists(path):
            raise SystemExit(f"{RICHNESS_ENV[cat]}={path}: no such file")
        rich = reproject_mean(path, lat, nonneg=True)[0].astype(float)[rowcol]
        limit = np.nanpercentile(np.where(scored, rich, np.nan), RICHNESS_PCT)
        drop = scored & ~(rich >= limit)          # ~(>=) also drops cells with no richness value
        z[i][drop] = np.nan
        gated.append(f"{cat} (<{limit:.3g}, {int(drop.sum()):,} cells)")
    return gated


def priority(z, scored):
    """Per cell: the lowest-z category and that z. -1 / NaN where no category is scored."""
    scoreable = scored & np.isfinite(z).any(axis=0)
    idx = np.argmin(np.where(np.isfinite(z), z, np.inf), axis=0)
    best = np.take_along_axis(z, idx[None], axis=0)[0]
    return np.where(scoreable, idx, -1), np.where(scoreable, best, np.nan)


def write_raster(lat, rowcol, idx, best):
    path = os.path.join(OUT_DIR, f"grid_{RES}m", "gettingeven.tif")
    planes = np.full((2,) + lat.shape, np.nan, "float32")
    planes[0][rowcol] = idx
    planes[1][rowcol] = best
    with rasterio.open(path, "w", driver="GTiff", height=lat.nrow, width=lat.ncol, count=2,
                       dtype="float32", crs=lat.crs, transform=lat.transform,
                       nodata=float("nan"), compress="deflate") as ds:
        ds.write(planes)
        ds.descriptions = ("priority", "priority_z")
    return path


def write_json(coords, idx, best, gated):
    """The app's rows, in the app's order: [lat, lon, category index, z]. z is null when grey.

    `richness_gate` records which categories the optional gate dropped cells for, so a reader
    (and test_gettingeven.py) can tell a gated build from an ungated one from the file alone.
    """
    rows = [[lat, lon, int(c), None if c < 0 else round(float(z), 3)]
            for (lat, lon), c, z in zip(coords, idx, best)]
    path = os.path.join(OUT_DIR, "webapp_data_gettingeven.json")
    with open(path, "w") as fh:
        json.dump({"gettingeven": rows, "cats": CATS, "min_records": MIN_RECORDS,
                   "richness_gate": gated}, fh, separators=(",", ":"))
    return path, rows


def main():
    lat = _lattice()
    coords, rowcol, total, counts = (cells_from_json if RES == JSON_RES else cells_from_stacks)(lat)
    z, scored = zscores(total, counts)
    gated = apply_richness_gate(z, lat, rowcol, scored)
    idx, best = priority(z, scored)

    print(f"lattice: {lat.ncol} x {lat.nrow} cells of {RES} m; {len(total):,} on the mask, "
          f"{int(scored.sum()):,} with >= {MIN_RECORDS:g} records")
    print("richness gate: " + ("; ".join(gated) if gated else
                               f"NOT APPLIED (set {', '.join(RICHNESS_ENV.values())})"))
    named = [(CATS[c], int((idx == c).sum())) for c in range(len(CATS))]
    print("  " + ", ".join(f"{c} {n:,}" for c, n in named))
    if any(n == 0 for _, n in named):
        raise SystemExit("a category never wins: the legend would promise a colour nobody sees")
    print(f"wrote {write_raster(lat, rowcol, idx, best)}")

    if coords is not None:
        path, rows = write_json(coords, idx, best, gated)
        grey = sum(1 for r in rows if r[2] < 0)
        print(f"wrote {path}: {len(rows)} cells, {len(rows) - grey} with a named group, "
              f"{grey} too sparse to score")


if __name__ == "__main__":
    main()
