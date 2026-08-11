"""Goal presets: the fixed axis blends behind the criteria dropdown (#20).

Single source of truth shared by build_webapp.py (which injects them into index.html)
and build_grid_values.py (which bakes the same blends into per-(taxon, goal) cell-colour
grids, #87 P2/P3). Weight order matches build_fullgrid_ca.BANDS:
[discover, conservation, env, staleness, urgency].
"""

AXES = ["discover", "conservation", "env", "staleness", "urgency"]

PRESETS = [
    {"name": "Spatial Gap",       "w": [1.0, 0, 0, 0, 0],   "proj": "blitz-the-gap-2026-general",         "blurb": "Under-recorded places: where few have logged on iNaturalist (inverse observation density)."},
    {"name": "Species discovery", "w": [1.0, 0, 0, 0.6, 0], "proj": "blitz-the-gap-revisiting-the-past",  "blurb": "Where new-to-the-record species are likeliest: under-sampling plus cells recorded long ago but quiet lately."},
    {"name": "Conservation",      "w": [0, 1.0, 0, 0, 0.4], "proj": "blitz-the-gap-canada-s-most-wanted", "blurb": "Where species at risk concentrate, weighted toward recently changed habitat (COSEWIC/SARA via CAN-SAR + GBIF)."},
]
DEFAULT = PRESETS[0]["w"]

# TIER_SWITCH_Z: 25 km vector tier at z <= this, 5 km raster tier above it.
# Emitted as both the 25 km source's maxzoom and the 5 km source's minzoom so the
# tiers are guaranteed adjacent. Ryan tunes by eye (#87 D5) - one edit, here.
TIER_SWITCH_Z = 9
