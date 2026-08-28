"""Builds index.html — an interactive trip planner for the Blitz the Gap
"where should I go to record biodiversity?" map. MapLibre GL basemap (OpenStreetMap +
style switcher); a preset weight mix over the goals blended into an "impact" score; a
start point + flexible time budget (minutes / hours / days); real driving routes
(OSRM) with drive time, field time, and travel CO2; and a low-carbon ranking
option. Answers "from here, with this much time, where do I maximise my impact?"."""

import json
import os

# Canada-wide: fetch per-group at runtime (national grid fetched per-group at runtime is too big to inline).
# Inject only the group->filename map; the browser fetches each group's JSON on demand.
with open("cluster_results/ca/index.json") as _fh:
    CA_INDEX = json.load(_fh)
FILES = CA_INDEX["files"]
# rows: [lat, lon, discover, conservation, env, staleness, urgency, travel_min, n_train]
OBJ = [
    {
        "key": "discover",
        "name": "Discover the most species",
        "q": "go where few people have looked",
    },
    {
        "key": "conservation",
        "name": "Find species at risk",
        "q": "go where COSEWIC/SARA species at risk concentrate",
    },
    {
        "key": "env",
        "name": "Cover every habitat",
        "q": "go where the climate is under-sampled",
    },
    {
        "key": "staleness",
        "name": "Freshest gaps",
        "q": "go where lots was recorded long ago but little lately (iNaturalist recent vs all-time density)",
    },
    {
        "key": "urgency",
        "name": "Sample before it's lost",
        "q": "go where forest cover was recently lost (logging, fire, dieback)",
    },
]
# order matches OBJ: [discover, conservation, env, staleness, urgency]
# Issue #49: the Goal selector is reduced to four central goals (Katherine/Maho). Their intended
# inputs (Make a Splash, Missing Species in Canada, Too Hot to Handle, KBA-assessment lists) are
# not all wired yet; until the lab finalises the calculation, each goal is a TEMPORARY combination
# of the five real axes already computed [discover, conservation, env, staleness, urgency]:
#   Spatial Gap       = iNaturalist under-sampling only (inverse density; #89: no other factors)
#   Species discovery = under-sampling + recent-vs-all-time density ("Revisit the Past")
#   Conservation      = COSEWIC/SARA at risk + recently changed habitat ("Too Hot to Handle")
# Getting Even is the separate categorical layer, added as the 'ge' option in the dropdown.
# PRESETS/DEFAULT/TIER_SWITCH_Z live in goal_presets.py (shared with the tile builder, #87).
from goal_presets import DEFAULT, PRESETS, TIER_SWITCH_Z
from grid_lattice import HEIGHT_M, WIDTH_M, X0, Y1

# PMTiles ship in the Pages site under tiles/ (same-origin: browsers block cross-origin range
# reads from release assets - no CORS headers). The grid-outputs-v1 release is the durable
# archive; the deploy-site job assembles _site/ from the built artifacts (#87 P4).
PMTILES_BASE = "tiles"

# Issue #17: the "Plan a trip" view (start point, travel budget, OSRM routing) is hidden for now —
# the team wants a simple gap-visualisation tool, not a trip planner. The code stays in place and
# dormant (flag flips it back) so a future "help plan a blitz" tool can reuse it.
PLAN_ENABLED = False
COMPARE_ENABLED = False  # Issue #71: the "Compare goals" view is stashed (kept dormant). Flag flips it back.

with open("webapp/index.html") as _fh:
    HTML = _fh.read()

# CARTO's raster basemaps watermark every tile unless the request carries our key. It is a
# client-side key by nature (it ships in the page), so it is a repo secret rather than a
# committed string: CI passes CARTO_API_KEY in, a local build leaves it empty and watermarked.
CARTO_API_KEY = os.environ.get("CARTO_API_KEY", "")


out = (
    HTML.replace("__FILES__", json.dumps(FILES, separators=(",", ":")))
    .replace("__OBJ__", json.dumps(OBJ))
    .replace("__PRESETS__", json.dumps(PRESETS))
    .replace("__DEFAULT__", json.dumps(DEFAULT))
    .replace("__PMTILES_BASE__", PMTILES_BASE)
    .replace("__CARTO_KEY__", CARTO_API_KEY)
    .replace("__TIER_SWITCH_Z__", str(TIER_SWITCH_Z))
    # The app snaps clicks to the same lattice the tiles are cut on. These were hardcoded in
    # the template, so the clickable cells could drift off the raster without anything saying so.
    .replace("__LATTICE_X0__", str(X0))
    .replace("__LATTICE_Y1__", str(Y1))
    .replace("__LATTICE_W__", str(WIDTH_M))
    .replace("__LATTICE_H__", str(HEIGHT_M))
    .replace("__PLAN_ENABLED__", "true" if PLAN_ENABLED else "false")
    .replace("__COMPARE_ENABLED__", "true" if COMPARE_ENABLED else "false")
)
with open("index.html", "w") as _fh:
    _fh.write(out)
print(f"wrote index.html  ({len(out) / 1024:.0f} KB)")
