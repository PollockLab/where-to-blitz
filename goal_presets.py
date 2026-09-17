"""Goal presets: the fixed axis blends behind the criteria dropdown (#20).

Single source of truth shared by build_webapp.py (which injects them into index.html)
and build_grid_values.py (which bakes the same blends into per-(taxon, goal) cell-colour
grids, #87 P2/P3). Weight order matches build_fullgrid_ca.BANDS:
[discover, conservation, env, staleness, urgency].

"scale" is the blend value that maps to the top of the viridis ramp. The blend is clipped
at 1.0, so any preset whose axes can sum past 1.0 paints a plateau of identical colour:
Species discovery does this on 55% of the cells you can see. Each scale is the largest
blend that preset actually attains, measured across every group and both tiers on the
shipped stacks, then rounded to a constant that will not drift on the next rebuild. It is
deliberately not sum(w): the two Conservation axes never co-occur high enough to approach
their 1.4, so dividing by the weight sum would push that preset down into the bottom half
of the ramp rather than fix anything.
"""

AXES = ["discover", "conservation", "env", "staleness", "urgency"]

# "proj" is the iNaturalist sub-project each preset's "join" link points at. Season
# challenges (Revisiting the past, Canada's Most Wanted) are date-boxed to their launch
# year and stop accepting new observations once it ends, so they go stale every summer.
# All three presets point at the general project instead, which the Pollock Lab rolls
# forward each year and which stays open across the whole observation season: re-check
# this each spring and bump the year if the umbrella project's slug changes.
PRESETS = [
    {
        "name": "Spatial Gap",
        "w": [1.0, 0, 0, 0, 0],
        "scale": 1.0,
        "proj": "blitz-the-gap-2026-general",
        "blurb": "Under-recorded places: where few have logged on iNaturalist (inverse observation density).",
    },
    {
        "name": "Species discovery",
        "w": [1.0, 0, 0, 0.6, 0],
        "scale": 1.6,
        "proj": "blitz-the-gap-2026-general",
        "blurb": "Where new-to-the-record species are likeliest: under-sampling plus cells recorded long ago but quiet lately.",
    },
    {
        "name": "Conservation",
        "w": [0, 1.0, 0, 0, 0.4],
        "scale": 1.0,
        "proj": "blitz-the-gap-2026-general",
        "blurb": "Where species at risk concentrate, weighted toward recently changed habitat (COSEWIC/SARA via CAN-SAR + GBIF).",
    },
]
DEFAULT = PRESETS[0]["w"]

# TIER_SWITCH_Z: 25 km vector tier at z <= this, 5 km raster tier above it.
# Emitted as both the 25 km source's maxzoom and the 5 km source's minzoom so the
# tiers are guaranteed adjacent. Ryan tunes by eye (#87 D5) - one edit, here.
TIER_SWITCH_Z = 9
