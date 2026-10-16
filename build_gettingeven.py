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
a place with few herps to find is not a place to send herpers. The three lab-supplied rasters are
checksum-pinned in gettingeven_richness.json, using content-addressed GitHub release assets:

    GE_RICHNESS_MANIFEST=gettingeven_richness.json python build_gettingeven.py

Local copies remain supported:

    GE_RICHNESS_HERP=ar.richness.tif GE_RICHNESS_MAMMAL=mammal.richness.tif \
    GE_RICHNESS_PLANT=plant.richness.stacks.tif python build_gettingeven.py

For checksum-pinned local or HTTPS inputs, set GE_RICHNESS_MANIFEST to the manifest described
in gettingeven_inputs.py. Hosted manifests also require GE_RICHNESS_MANIFEST_SHA256.

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
  cluster_results/ca/gettingeven_grid_<RES>m.png    the app's fill, one pixel per cell
  cluster_results/ca/gettingeven_grid.json          per-tier build record for the PNGs
  cluster_results/ca/webapp_data_gettingeven.json   the app's rows            (25 km tier only)

The PNG is what the app fills cells from at both tiers (#122). The JSON only ever carried the
25 km tier: at 5 km the same format is ~250k rows keyed on coordinate strings, some 14 MB, and
the app already reads a lattice-index PNG through a canvas for the priority grid. The two tiers
are standardised and gated over their own cells, so a 5 km cell can name a different group from
the 25 km cell containing it; that is the metric being computed on the units being scored, not
a defect, and probe_gettingeven.py measures how often it happens.

Usage:  python build_gettingeven.py                 # 25 km tier, after build_fullgrid_ca.py
        GRID_RES=5000 python build_gettingeven.py   # 5 km tier: raster + PNG, no JSON
"""

import json
import os

import matplotlib.image
import numpy as np
import rasterio
import rasterio.warp

import border_mask
from build_provenance import sha256_file
from gettingeven_inputs import RICHNESS_ENV, load_richness_inputs, observation_hash
from gettingeven_oracle import gate_bits, metric
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
RICHNESS_PCT = 25   # a group is dropped below this percentile of its own richness

# Red-channel value for a cell that is on the map but holds too few records to score. Kept
# out of 0..len(CATS)-1 so the app can tell "no answer" from a category without a second
# channel; alpha 0 means there is no cell here at all.
PNG_UNSCORED = 255
SIDECAR = os.path.join(OUT_DIR, "gettingeven_grid.json")


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
        if [(r[0], r[1]) for r in rows] != coords:
            raise SystemExit(f"{group}: cell ordering differs from {TOTAL}")
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
            if ds.crs != lat.crs or ds.shape != lat.shape or ds.transform != lat.transform:
                raise SystemExit(f"{group}: observation stack is not on the {RES} m lattice")
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
    if not np.isfinite(MIN_RECORDS) or MIN_RECORDS <= 0:
        raise SystemExit("GE_MIN_RECORDS must be finite and greater than zero")
    if np.any(~np.isfinite(total)) or np.any(total < 0) or np.any(~np.isfinite(counts)) or np.any(counts < 0):
        raise SystemExit("observation counts must be finite and nonnegative")
    scored = np.isfinite(total) & (total >= MIN_RECORDS)
    if not scored.any():
        raise SystemExit("no cells meet GE_MIN_RECORDS")
    prop = np.full(counts.shape, np.nan)
    np.divide(counts, total, out=prop, where=scored)
    mean, sd = np.nanmean(prop, axis=1), np.nanstd(prop, axis=1)
    if not np.all(np.isfinite(sd) & (sd > 0)):
        flat = [CATS[i] for i in np.where(~np.isfinite(sd) | (sd <= 0))[0]]
        raise SystemExit(f"no variation in {', '.join(flat)}: refusing to write a constant layer")
    return (prop - mean[:, None]) / sd[:, None], scored


def apply_richness_gate(z, lat, rowcol, scored, paths=None, richness=None):
    """Drop a category where its own modelled richness is in the national bottom quartile.

    Returns what was actually gated, so the run can report the step rather than let an absent
    raster look like a metric that never had one: the human line, which carries the tier's own
    limit and cell count, and the bare category names, which are the part that has to be the
    same at every tier (see test_the_tiers_agree_on_the_gate_and_the_floor).
    """
    gated, cats = [], []
    if paths is None:
        paths, _ = load_richness_inputs()
    for i, cat in enumerate(CATS):
        path = paths.get(cat)
        if not path:
            continue
        if not os.path.exists(path):
            raise SystemExit(f"{RICHNESS_ENV[cat]}={path}: no such file")
        rich = reproject_mean(path, lat, nonneg=True)[0].astype(float)[rowcol]
        # The R script fills a cell its richness raster does not reach with 0 before taking the
        # quartile (`richness[is.na(richness) & !is.na(base.5k)] <- 0`), so those cells sit in the
        # denominator as well as below the limit. Excluding them from the quantile instead lifts
        # the limit and over-drops: 3,394 cells against the R script's 2,670 at 25 km.
        rich = np.where(np.isfinite(rich), rich, 0.0)
        if richness is not None:
            richness[i] = rich
        limit = np.percentile(rich[scored], RICHNESS_PCT)
        drop = scored & (rich < limit)
        z[i][drop] = np.nan
        gated.append(f"{cat} (<{limit:.3g}, {int(drop.sum()):,} cells)")
        cats.append(cat)
    return gated, cats


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


def write_png(lat, rowcol, idx):
    """The app's fill: one pixel per lattice cell, category index in the red channel.

    Lattice index space, not a georeferenced raster, which is the point: the app reads it back
    through a canvas and paints the cell polygons it already draws, so the colours and the
    clickable lattice are the same geometry by construction (the same trick build_grid_values.py
    uses for the priority grid). The index is carried rather than the colour so GE_PAL stays in
    webapp/index.html alone; baking the palette here would make a legend change a rebuild.

    Alpha 0 is "no cell": off the lattice stacks, or hidden as foreign by border_mask, which is
    how the priority grid hides the cross-border band too. Alpha 255 with PNG_UNSCORED is a real
    cell with too few records to judge, and the app decides per tier how to draw that.
    """
    hidden = border_mask.hidden_for_tier(lat.nrow, lat.ncol, lat.res, lat.crs, lat.x0, lat.y1)
    cat = np.full(lat.shape, PNG_UNSCORED, np.uint8)
    cat[rowcol] = np.where(idx < 0, PNG_UNSCORED, idx).astype(np.uint8)
    present = np.zeros(lat.shape, bool)
    present[rowcol] = True
    present &= ~hidden
    zero = np.zeros(lat.shape, np.uint8)
    rgba = np.dstack([cat, zero, zero, np.where(present, 255, 0).astype(np.uint8)])
    path = os.path.join(OUT_DIR, f"gettingeven_grid_{RES}m.png")
    matplotlib.image.imsave(path, rgba)
    return path


def _sidecar():
    if not os.path.exists(SIDECAR):
        return {}
    with open(SIDECAR) as fh:
        return json.load(fh)


def write_sidecar(lat, gated, gated_cats, png_path, inputs=None, mask_path=None):
    """Per-tier build record for the PNGs: which lattice, which gate, which floor.

    A coordinate-keyed file fails loudly when the lattice moves, because every key misses; that
    is how the pre-#87 layer went grey. A lattice-index PNG fails silently instead, because a
    lattice change re-points every pixel at a different place and the map still looks plausible.
    Nothing inside the image can catch that, so the lattice it was built on is recorded next to
    it and asserted by test_gettingeven.py. index.json would be the natural home, but
    build_fullgrid_ca.py rewrites that file whole, so a Getting Even field there would not
    survive the next full grid rebuild.

    Each tier is built by its own command, so this merges rather than overwrites, and recording
    both tiers' `richness_gate` is what lets a test catch a map that is gated above the zoom
    switch and ungated below it.
    """
    doc = _sidecar()
    doc["cats"] = CATS
    doc["unscored_index"] = PNG_UNSCORED
    doc.setdefault("tiers", {})[str(RES)] = {
        "png": os.path.basename(png_path),
        "sha256": sha256_file(png_path),
        "lattice": {
            "x0": lat.x0,
            "y1": lat.y1,
            "res_m": lat.res,
            "ncol": lat.ncol,
            "nrow": lat.nrow,
            "crs": lat.crs.to_wkt(),
        },
        "min_records": MIN_RECORDS,
        "richness_gate": gated,
        "gated_categories": gated_cats,
        "inputs": inputs,
        "build_status": "rebuilt-and-verified",
    }
    if mask_path:
        doc["tiers"][str(RES)]["gate_mask"] = {
            "png": os.path.basename(mask_path),
            "sha256": sha256_file(mask_path),
        }
    with open(SIDECAR, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    return SIDECAR


def _refuse_silent_ungating(path, shipped, gated):
    """Fail loudly when an ungated build would overwrite a gated shipped layer.

    The richness rasters live on the lab's SharePoint, not on the public bucket CI reads, so a
    rebuild that cannot see them produces a valid-looking ungated map and reverts the shipped one
    without a word. That is how the layer went grey once before, see the "Rebuild the Getting Even
    layer" step in rebuild-grid.yml. Until the rasters are hosted (issue below), turn the silent
    revert into a failed build. `shipped` is the gate the file on disk carries: the JSON's own
    field for the 25 km vector layer, the sidecar's tier entry for the PNGs. The 5 km tier has no
    JSON, so the sidecar is the only thing standing between it and a silent ungating.
    """
    if gated or os.environ.get("GE_ALLOW_UNGATED") == "1" or not shipped:
        return
    raise SystemExit(
        f"{path} was built with the richness gate on ({'; '.join(shipped)}) and this build "
        f"has no richness rasters, so writing it would silently revert the layer. Set "
        f"{', '.join(RICHNESS_ENV.values())}, pass GE_KEEP_SHIPPED=1 to keep the shipped layer, "
        f"or GE_ALLOW_UNGATED=1 to overwrite it on purpose.")


def _keep_shipped(lat, coords, shipped, gated, inputs=None):
    """With GE_KEEP_SHIPPED=1, leave a gated shipped tier in place instead of failing the build.

    CI cannot see the richness rasters, so every full rebuild there would otherwise stop at
    `_refuse_silent_ungating`, and a rebuild for an unrelated layer could never deploy. The shipped
    tier is kept only while it still fits this build: the lattice it records, the PNG hash beside
    it and, at 25 km, a JSON row for every cell the app draws. A moved lattice is how the layer
    went grey before, so that still fails. Returns True when the tier was kept.
    """
    if gated or not shipped.get("richness_gate") or os.environ.get("GE_KEEP_SHIPPED") != "1":
        return False
    built = dict(shipped.get("lattice") or {})
    if not built or rasterio.crs.CRS.from_wkt(built.pop("crs")) != lat.crs or built != {
            "x0": lat.x0, "y1": lat.y1, "res_m": lat.res, "ncol": lat.ncol, "nrow": lat.nrow}:
        raise SystemExit(f"GE_KEEP_SHIPPED: the shipped {RES} m tier is on another lattice; "
                         f"rebuild it with the richness rasters")
    png = os.path.join(OUT_DIR, shipped["png"])
    if not os.path.exists(png) or sha256_file(png) != shipped["sha256"]:
        raise SystemExit(f"GE_KEEP_SHIPPED: {png} does not match the hash in {SIDECAR}")
    if coords is not None:
        with open(os.path.join(OUT_DIR, "webapp_data_gettingeven.json")) as fh:
            rows = json.load(fh)["gettingeven"]
        if {f"{r[0]:.3f},{r[1]:.3f}" for r in rows} != {f"{a:.3f},{b:.3f}" for a, b in coords}:
            raise SystemExit("GE_KEEP_SHIPPED: webapp_data_gettingeven.json is not keyed on the "
                             "cells the app draws; rebuild it with the richness rasters")
    previous = shipped.get("inputs")
    if shipped.get("min_records") != MIN_RECORDS:
        raise SystemExit("GE_KEEP_SHIPPED: record floor changed; rebuild with pinned richness inputs")
    if previous:
        if not inputs or previous["observations_sha256"] != inputs["observations_sha256"]:
            raise SystemExit(
                "GE_KEEP_SHIPPED: observation counts changed; rebuild with pinned richness inputs"
            )
        if previous.get("richness_percentile") != RICHNESS_PCT:
            raise SystemExit(
                "GE_KEEP_SHIPPED: richness percentile changed; rebuild with pinned richness inputs"
            )
        status = "preserved-observations-verified"
    else:
        print(
            f"::warning::legacy-preserved {RES} m Getting Even: input hashes unavailable; "
            "this is not a reproduced build"
        )
        status = "legacy-preserved-inputs-unverified"
    doc = _sidecar()
    doc["tiers"][str(RES)]["build_status"] = status
    with open(SIDECAR, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    print(f"::notice::kept the shipped gated {RES} m Getting Even tier "
          f"({'; '.join(shipped['richness_gate'])}): no richness rasters in this build")
    return True


def _shipped_json_gate(path):
    """The gate the shipped 25 km vector layer carries, empty when there is no file yet."""
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return json.load(fh).get("richness_gate") or []


def write_json(coords, idx, best, gated):
    """The app's rows, in the app's order: [lat, lon, category index, z]. z is null when grey.

    `richness_gate` records which categories the optional gate dropped cells for, so a reader
    (and test_gettingeven.py) can tell a gated build from an ungated one from the file alone.
    """
    rows = [[lat, lon, int(c), None if c < 0 else round(float(z), 3)]
            for (lat, lon), c, z in zip(coords, idx, best)]
    path = os.path.join(OUT_DIR, "webapp_data_gettingeven.json")
    _refuse_silent_ungating(path, _shipped_json_gate(path), gated)
    with open(path, "w") as fh:
        json.dump({"gettingeven": rows, "cats": CATS, "min_records": MIN_RECORDS,
                   "richness_gate": gated}, fh, separators=(",", ":"))
    return path, rows


def main():
    lat = _lattice()
    coords, rowcol, total, counts = (cells_from_json if RES == JSON_RES else cells_from_stacks)(lat)
    paths, records = load_richness_inputs()
    inputs = {
        "observations_sha256": observation_hash(rowcol, total, counts),
        "richness": records,
        "richness_percentile": RICHNESS_PCT,
    }
    z, scored = zscores(total, counts)
    richness = {}
    gated, gated_cats = apply_richness_gate(z, lat, rowcol, scored, paths, richness)
    idx, best = priority(z, scored)
    excluded = gate_bits(richness, scored)
    expected, expected_z = metric(total, counts, MIN_RECORDS, excluded)
    if not np.array_equal(idx, expected) or not np.allclose(best, expected_z, atol=1e-10, equal_nan=True):
        raise SystemExit("independent gated metric oracle disagrees with the build")

    print(f"lattice: {lat.ncol} x {lat.nrow} cells of {RES} m; {len(total):,} on the mask, "
          f"{int(scored.sum()):,} with >= {MIN_RECORDS:g} records")
    print("richness gate: " + ("; ".join(gated) if gated else
                               f"NOT APPLIED (set {', '.join(RICHNESS_ENV.values())})"))
    named = [(CATS[c], int((idx == c).sum())) for c in range(len(CATS))]
    print("  " + ", ".join(f"{c} {n:,}" for c, n in named))
    if any(n == 0 for _, n in named):
        raise SystemExit("a category never wins: the legend would promise a colour nobody sees")
    shipped_tier = _sidecar().get("tiers", {}).get(str(RES), {})
    if _keep_shipped(lat, coords, shipped_tier, gated, inputs):
        return
    _refuse_silent_ungating(SIDECAR, shipped_tier.get("richness_gate") or [], gated)
    print(f"wrote {write_raster(lat, rowcol, idx, best)}")
    png = write_png(lat, rowcol, idx)
    mask_path = os.path.join(OUT_DIR, f"gettingeven_gate_{RES}m.png")
    mask = np.zeros(lat.shape, dtype=np.uint8)
    mask[rowcol] = excluded
    matplotlib.image.imsave(mask_path, np.dstack([mask, mask, mask]))
    print(f"wrote {png}: {lat.ncol} x {lat.nrow} px, {os.path.getsize(png) / 1e3:.0f} kB")
    print(f"wrote {write_sidecar(lat, gated, gated_cats, png, inputs, mask_path)}")

    if coords is not None:
        path, rows = write_json(coords, idx, best, gated)
        grey = sum(1 for r in rows if r[2] < 0)
        print(f"wrote {path}: {len(rows)} cells, {len(rows) - grey} with a named group, "
              f"{grey} too sparse to score")


if __name__ == "__main__":
    main()
