"""Canada-wide where-to-blitz grid — reproducible rebuild (issues #58, #87).

Rebuilds cluster_results/ca/webapp_data_<GROUP>.json + index.json from first principles,
streaming the per-taxon iNaturalist density directly from the public Biodiversite Quebec
bucket so the whole grid is reproducible from a clean checkout (no lost ad-hoc builder).

GEOMETRY (#87): cells are defined on a projected equal-area lattice in the density COGs' own
CRS (WGS84 Lambert Azimuthal Equal Area, lat_0=45 lon_0=-100) at GRID_RES metres -- see
grid_lattice.py. Every cell is square, equal-area and actually GRID_RES across, 1 km -> cell
aggregation is an exact integer block reduce, and the 25 km tier is an exact 5x5 aggregate of
the 5 km tier. The previous 0.25-deg definition made a "25 km" cell 21.0 km wide at Windsor
and 2.9 km wide at 84N.

Every layer is now a cell mean over the equal-area cell (reprojected with an area-weighted
average), replacing the old cell-centre point samples: resolution-independent, and it means a
25 km value is the mean of its 25 constituent 5 km values by construction.

RAMPS (#87): the 0..1 axes are mapped from their raw quantities through the national ramps in
breaks.json, fitted once on the 25 km tier -- see national_breaks.py. They used to be percentile
ranks and min-max normalizations over whichever grid was being built, which made a cell's value
depend on the size and composition of that grid, and so put the same place at two different
colours at two zooms.

Recipe:
  density   = exact 5x5 (or k x k) block mean of the 1 km LAEA iNat heatmap onto the lattice
  n_train   = round(cell-mean density x cell_km2)            (implied research-grade records)
  discover  = under-sampling score from density per km2 (fewer records = higher); zero-record
              cells on top, ordered among themselves by climate distinctiveness (env).
  env       = climate surprisal (CHELSA, density-weighted KDE) on the national ramp
  urgency   = Hansen forest-loss fraction on the national ramp
  travel    = cell-mean Weiss 2018 travel-time over the cell's land pixels
  conservation = COSEWIC/SARA at-risk richness   (joined from ca_atrisk_richness.csv)
  staleness    = iNat recent-vs-all-time density  (joined from ca_staleness.csv; else inverse-density proxy)

The conservation and staleness CSVs are keyed on the old 0.25-deg (gi, gj) lattice, so each
projected cell inherits the value of the 0.25-deg cell its centre falls in. That is a
nearest-neighbour resample of a coarser layer, recorded in index.json; rebuilding those two
layers natively on the lattice is separate work (they are network pulls from CAN-SAR/GBIF).

MASK: keep every cell inside the iNat density COG footprint that has Weiss land coverage OR
non-zero density. The data-bearing clause recovers water-edge urban cells whose centroid lands
on water (the #58 bug, e.g. Laval / Lac-Saint-Louis, 78k-153k obs).

Outputs:
  cluster_results/ca/grid_<RES>m/<GROUP>.tif   7-band float32 stack on the lattice (all RES)
  cluster_results/ca/webapp_data_<GROUP>.json  per-cell rows for the app  (25 km tier only)
  cluster_results/ca/index.json                                          (25 km tier only)
  cluster_results/ca/breaks.json               national 0..1 ramps       (25 km tier only)

Usage:  python build_fullgrid_ca.py            # 25 km tier: fits the ramps, must run first
        GRID_RES=5000 python build_fullgrid_ca.py
"""
import argparse
import os

import fullgrid_fields as ff
import fullgrid_outputs as fo

RES = int(os.environ.get("GRID_RES", "25000"))  # lattice cell size in metres; 25000 or 5000
JSON_RES = 25000                                # the tier the app's vector/popup layer ships from

# Accept an optional per-group build via --group or the GROUP env var. When provided,
# the script will build only that group's stack (and JSON if GRID_RES==JSON_RES). This
# keeps the default behaviour unchanged when no group is specified.
parser = argparse.ArgumentParser(description="Build per-group or full Canada grid")
parser.add_argument("--group", dest="group", default=os.environ.get("GROUP"),
                    help="Optional group name to build (must match one of GROUPS)")
args = parser.parse_args()
GROUP_ARG = args.group

# If a group was requested, validate it and restrict work to that single group.
if GROUP_ARG:
    if GROUP_ARG not in ff.GROUPS:
        raise SystemExit(f"Unknown group {GROUP_ARG!r}; must be one of: {ff.GROUPS}")
    groups_to_build = [GROUP_ARG]
else:
    groups_to_build = ff.GROUPS

# ----------------------------------------------------------------- stages
f = ff.shared_fields(RES, groups_to_build)   # lattice, mask, group-independent inputs, ramps

sizes = {}
for group in groups_to_build:
    cog = ff.GROUP_TO_COG[group]
    bands = ff.group_bands(f, group)
    fo.write_stack(f, group, bands)
    rec = int((bands["n_train"] > 0).sum())
    if RES == JSON_RES:
        sizes[group] = fo.write_webapp_json(f, group, bands)
        print(f"  {group:16s} (COG={cog:14s}): {f.n} cells ({rec} recorded, {f.n - rec} gaps), "
              f"{sizes[group]//1024} KB")
    else:
        print(f"  {group:16s} (COG={cog:14s}): {f.n} cells ({rec} recorded, {f.n - rec} gaps) -> stack only")

fo.write_indexes(f, GROUP_ARG, len(groups_to_build), sizes,
                 write_global=(RES == JSON_RES and GROUP_ARG is None))
