"""Builds index.html — an interactive trip planner for the Blitz the Gap
"where should I go to record biodiversity?" map. MapLibre GL basemap (OpenStreetMap +
style switcher); a preset weight mix over the goals blended into an "impact" score; a
start point + flexible time budget (minutes / hours / days); real driving routes
(OSRM) with drive time, field time, and travel CO2; and a low-carbon ranking
option. Answers "from here, with this much time, where do I maximise my impact?"."""

import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path

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
        "name": "Climate under-sampling",
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
#   Spatial Gap       = the recording-gap axis (density rank, climate ordering within zero-density cells)
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

# CARTO's browser key ships in the page. Without one the catalog selects a keyless
# OpenStreetMap source, so local previews display real tiles rather than error images.
CARTO_API_KEY = os.environ.get("CARTO_API_KEY", "")


def load_plant_pilot(path):
    """Missing pilot artifacts disable the feature; malformed artifacts fail the build."""
    path = Path(path)
    if not path.exists():
        return None
    pilot = json.loads(path.read_text())

    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def vector(value, length, positive=False):
        return isinstance(value, list) and len(value) == length and all(
            number(v) and (not positive or v > 0) for v in value
        )

    def iso_date(value):
        try:
            return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False

    valid = isinstance(pilot, dict) and type(pilot.get("schema_version")) is int and pilot["schema_version"] == 1 and pilot.get("method") == "bc-plants-habitat-season-v1"
    if valid:
        gate, scaler, names = pilot.get("release_gate"), pilot.get("scaler"), pilot.get("feature_names")
        features = ["bio1", "bio4", "bio12"] + [f"cover_{v}" for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)]
        valid = (isinstance(gate, dict) and isinstance(gate.get("passed"), bool)
                 and iso_date(pilot.get("training_before")) and iso_date(pilot.get("valid_until"))
                 and pilot["training_before"] < pilot["valid_until"]
                 and names == features
                 and (scaler is None and not gate["passed"] or isinstance(scaler, dict)
                      and vector(scaler.get("mean"), 14) and vector(scaler.get("scale"), 14, True))
                 and isinstance(pilot.get("species"), list) and isinstance(pilot.get("cells"), dict))
    if valid:
        ids = set()
        for model in pilot["species"]:
            if not isinstance(model, dict):
                valid = False
                break
            taxon = model.get("id")
            if (not isinstance(taxon, int) or isinstance(taxon, bool) or taxon <= 0 or taxon in ids
                or not vector(model.get("mean"), 14) or not vector(model.get("variance"), 14, True)
                or not vector(model.get("months"), 12) or any(v < 0 for v in model["months"])
                or not number(model.get("rarity")) or not 0 <= model["rarity"] <= 1
                or not isinstance(model.get("support_cells"), int) or isinstance(model["support_cells"], bool)
                or model["support_cells"] < 3):
                valid = False
                break
            ids.add(taxon)
        valid = valid and all(re.fullmatch(r"\d+,\d+", key) and vector(raw, 14)
                              for key, raw in pilot["cells"].items())
        valid = valid and (pilot["release_gate"]["passed"] or not pilot["species"] and not pilot["cells"])
    if not valid:
        raise ValueError("Malformed BC plant pilot artifact")
    return pilot


BC_PLANT_PILOT = load_plant_pilot("webapp/bc_plant_pilot.json")


def load_optional_layers(files, environ=None):
    """Only explicitly configured, validated authentic inputs advertise optional layers."""
    from build_discovery_layer import validate_artifact
    from conservation_challenge import validate_snapshot

    environ = os.environ if environ is None else environ
    result = {"discovery": None, "challenge": None}
    for name, variable, validator, filename in (
        ("discovery", "DISCOVERY_RATE_FILE", validate_artifact, "discovery_rate.json"),
        ("challenge", "CONSERVATION_CHALLENGE_FILE", validate_snapshot, "conservation_challenge.json"),
    ):
        path = environ.get(variable)
        if not path:
            continue
        payload = Path(path).read_bytes()
        data = validator(json.loads(payload))
        descriptor = {
            "schema_version": data["schema_version"],
            "path": "cluster_results/ca/" + filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if name == "discovery":
            group = data["scope"]["filters"].get("taxon_group")
            if not isinstance(group, str) or group not in files:
                raise ValueError(
                    "Discovery rate scope.filters.taxon_group must name an available species group"
                )
            descriptor.update(taxon_group=group, scope=data["scope"], metric=data["metric"])
        else:
            descriptor.update(
                as_of=data["as_of"], window=data["window"], source_version=data["source"]["version"]
            )
        result[name] = descriptor
    return result


OPTIONAL_LAYERS = load_optional_layers(FILES)
OPTIONAL_LAYER_SCRIPT = (Path(__file__).resolve().parent / "webapp/optional_layers.js").read_text() + "\ninstallOptionalLayers();\n"
BASEMAP_SCRIPT = (Path(__file__).resolve().parent / "webapp/basemaps.js").read_text()


out = (
    HTML.replace("__FILES__", json.dumps(FILES, separators=(",", ":")))
    .replace(
        "__OPTIONAL_LAYERS__",
        json.dumps(OPTIONAL_LAYERS, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c"),
    )
    .replace("__OPTIONAL_LAYER_SCRIPT__", OPTIONAL_LAYER_SCRIPT)
    .replace("__BASEMAP_SCRIPT__", BASEMAP_SCRIPT)
    .replace("__BC_PLANT_PILOT__", json.dumps(BC_PLANT_PILOT, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c"))
    .replace("__OBJ__", json.dumps(OBJ))
    .replace("__PRESETS__", json.dumps(PRESETS))
    .replace("__DEFAULT__", json.dumps(DEFAULT))
    .replace("__PMTILES_BASE__", PMTILES_BASE)
    .replace("__CARTO_KEY__", CARTO_API_KEY)
    .replace("__CARTO_KEY_JSON__", json.dumps(CARTO_API_KEY).replace("<", "\\u003c"))
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
