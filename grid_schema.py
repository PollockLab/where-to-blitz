"""The band contract of the lattice stacks (#87): one module owns it.

Every per-group stack (cluster_results/ca/grid_<RES>m/<GROUP>.tif) is a 7-band
float32 GeoTIFF with the bands below, in this order, 1-based. The webapp JSON row
format is [lat, lon] + BANDS. Consumed by build_fullgrid_ca (writer),
build_grid_pmtiles (tiler), aggregate_25km_from_5km (nesting), and
validate_artifacts (nesting gate) - previously each kept its own copy.
"""

BANDS = ["discover", "conservation", "env", "staleness", "urgency", "travel_min", "n_train"]

# Extensive bands: a parent cell is the SUM of its children, not the mean.
SUM_BANDS = {"n_train"}  # counts add, they don't average

# 1-based raster band numbers, for rasterio reads and the validator's index set.
BAND_INDEX = {name: i for i, name in enumerate(BANDS, start=1)}
SUM_BAND_INDICES = {BAND_INDEX[name] for name in SUM_BANDS}
