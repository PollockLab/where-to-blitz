#!/usr/bin/env python3
"""Build `where-to-blitz-walkthrough.ipynb` — a walkthrough of
where-to-blitz: what it is, the design choices behind it, and the data underneath.

Source generator: edit THIS file, never the .ipynb. Map figures read the grid snapshot selected by WTB_CA_DIR (default cluster_results/ca); archived backtest results stay in cluster_results/ and retain their original source and input snapshots. Grid inputs with available provenance entries must match their recorded hashes. One optional live-iNat cell is guarded. Reproduce:

    .venv/bin/python _build_walkthrough.py
    export WTB_CA_DIR=/path/to/validated/cluster_results/ca
    .venv/bin/python -m nbconvert --to notebook --execute --inplace where-to-blitz-walkthrough.ipynb
    .venv/bin/python render_walkthrough_html.py    # → where-to-blitz-walkthrough.html (with figure alt text)

The HTML render is what the in-app "Full methodology" link points at (served on GitHub Pages).
render_walkthrough_html.py wraps nbconvert and adds alt text to the figures for screen-reader users.
Re-render whenever the notebook changes.

Use --reuse-diagram only after rendering the current Mermaid source locally when kroki.io is unavailable.

NOTE: The §0 flowchart is rendered to a PNG file (where-to-blitz-zoomout.png) at build time via
the kroki.io API and referenced with a relative markdown image link. This is required because
GitHub's .ipynb viewer does not render ```mermaid``` fenced blocks in notebook markdown cells, and
also strips data: URIs from <img src> in notebook markdown. A committed relative-path PNG file
renders correctly on GitHub's notebook viewer and in the nbconvert HTML page (both at the repo/
Pages root). The Mermaid source is preserved in a <details> block for editability and traceability.
"""

import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

from notebook_core import NotebookBuilder

_ZOOMOUT_PNG = "where-to-blitz-zoomout.png"


def _render_mermaid_to_file(diagram: str, path: str) -> None:
    """Render a Mermaid diagram to a PNG file via the kroki.io public API.

    Writes raw PNG bytes to ``path``.  Raises RuntimeError on HTTP or network failure.
    """
    url = "https://kroki.io/mermaid/png"
    req = urllib.request.Request(
        url,
        data=diagram.encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "where-to-blitz/_build_walkthrough.py",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            png_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"kroki.io returned HTTP {exc.code}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"kroki.io request failed: {exc}") from exc
    with open(path, "wb") as f:
        f.write(png_bytes)
    print(f"  wrote {path} ({len(png_bytes) // 1024} KB)")


nb = NotebookBuilder(strip=True)
md, code = nb.md, nb.co

# ---------------------------------------------------------------- title
md(r"""
# Where to Blitz the Gap — a walkthrough

*What is in the tool, and why is it built the way it is.*

[Where to Blitz the Gap](https://pollocklab.github.io/where-to-blitz/) is a companion map for the Canada-wide [Blitz the Gap](https://blitzthegap.org) iNaturalist bioblitz. The default Explore view lets you choose a recording goal, inspect priorities and tap a cell for live recording evidence. Its scores describe gaps in recorded evidence, not a tested forecast of an outing's ecological benefit.

The methodology and the data underneath it, laid out so you can check every choice and
reproduce every number. For the terse formula reference and the glossary, see
[`METHODOLOGY.md`](https://github.com/PollockLab/where-to-blitz/blob/main/METHODOLOGY.md).

> A **work-in-progress prototype and a planning aid, not ground truth.** The responsible-use
> guardrails are in the last section.

Each section pairs a design decision with the real data it acts on. Map figures use the selected grid snapshot, identified by its manifest and input hashes below. Retrospective figures retain their archived build and record-input snapshots, identified separately in Section 8.
""")

# ---------------------------------------------------------------- zoom out
_ZOOM_OUT_MERMAID = r"""flowchart TB
  Q(["<b>Where should I record wildlife<br/>so it does the most for biodiversity?</b>"])
  subgraph DATA["① Free, public data"]
    direction LR
    D1["iNaturalist sightings"]
    D2["Climate"]
    D3["Forest loss"]
    D4["Roads & travel time"]
    D5["At-risk species lists"]
  end
  GOALS["② Split Canada into 25 km squares.<br/>Score each square on <b>5 axes</b> (0–1):<br/>sparse records · recorded species at risk ·<br/>climate coverage · sampling history · forest loss"]
  IMPACT["③ <b>You</b> choose a fixed preset.<br/>→ its blend's <b>Canada percentile</b><br/>among eligible 25 km cells"]
  APP(["④ <b>The map</b><br/>Explore priorities · inspect live records"])
  CHECK{{"Retrospective record check:<br/>expected new taxon IDs in sampled bioblitz records<br/>Prospective field performance remains untested"}}

  Q --> DATA --> GOALS --> IMPACT --> APP
  GOALS -. "discovery-related recording priorities" .-> CHECK

  style Q fill:#1b4965,color:#fff,stroke:#1b4965
  style APP fill:#2a9d4a,color:#fff,stroke:#2a9d4a
  style CHECK fill:#eaf4ea,stroke:#2a9d4a"""

if "--reuse-diagram" in sys.argv:
    if not Path(_ZOOMOUT_PNG).is_file():
        raise FileNotFoundError("--reuse-diagram requires a PNG rendered from the current Mermaid source")
    print("reusing §0 flowchart PNG rendered from the current Mermaid source")
else:
    print("rendering §0 flowchart via kroki.io …")
    _render_mermaid_to_file(_ZOOM_OUT_MERMAID, _ZOOMOUT_PNG)

md(f"""
## 0 · Zoom out — the whole tool in one picture

How a question becomes a map you can act on:

![Flowchart: question → public data → five axes per 25 km square → fixed preset → Canada percentile among eligible 25 km cells → map; retrospective record check, prospective field performance untested]({_ZOOMOUT_PNG})

The tool combines seven public sources into priority scores for 25 km cells. Its goals express different reasons to record biodiversity. The retrospective backtest compares those priorities with sampled bioblitz records; it does not establish prospective field performance. The rest of this notebook shows the inputs, formulas and evidence behind each part.

<details><summary>Mermaid source (diagram above)</summary>

<pre><code>{escape(_ZOOM_OUT_MERMAID)}</code></pre>

</details>

<details><summary><b>Under the hood</b> — the modules a developer would touch</summary>

| Module | Responsible for |
|---|---|
| `build_fullgrid_ca.py`, `fullgrid_fields.py` | Density, climate, forest loss and travel fields, plus `n_train`, on the equal-area lattice and its footprint/land mask. |
| `build_atrisk_layer.py`, joined in `fullgrid_fields.py` | CAN-SAR listings × GBIF occurrences under the selected policy → status-weighted richness on a 0.25° source grid, joined to each 5 km cell by its centre. |
| cluster DuckDB → `ca_inat_metrics.csv` → `ca_staleness.csv`; `fullgrid_fields.py` | All-time vs last-5-years iNaturalist history on a 0.25° source grid, joined to each 5 km cell by its centre. Source cells with 1 to 19 records score 0; cells absent from the history file use a group-specific inverse-density fallback. |
| `aggregate_25km_from_5km.py`, `export_popup_grid.py` | The 25 km tier: NaN-aware means of the 25 child 5 km cells (record counts are summed), then the final cell inventory and float32 axes that popups and national ranks read. |
| `build_gettingeven.py` | Getting Even: the taxonomic group most under-represented in each cell, birds excluded. |
| `build_discovery_layer.py` | Historical discovery evidence: supplied discovery points per 10,000 research-grade observations, per 5 km cell and summed to 25 km. |
| `conservation_challenge.py` | Conservation Challenge sightings: a snapshot of recent open iNaturalist observations of the challenge target taxa in Canada. |
| `goal_presets.py`, `build_grid_values.py`, `webapp/index.html`, `build_webapp.py` | Fixed blends and baked colours; runtime ranks, viewport colours, live popup queries and trip planning; template assembly into `index.html`. |
| `build_provenance.py` | Freezes the national build to a hashed manifest (`provenance.json`). |
| `voi_backtest.py`, `backtest_appscore.py` | The validation layer: retrospective associations with new taxon IDs in sampled project records |

</details>
""")

# ---------------------------------------------------------------- setup
code(r"""
# Setup: WTB_CA_DIR selects map data; archived backtests remain relative to the repo root.
import hashlib, json, os, warnings
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
warnings.filterwarnings("ignore")
plt.rcParams.update({"figure.dpi": 110, "font.size": 10, "axes.grid": False})

CA = Path(os.environ.get("WTB_CA_DIR", "cluster_results/ca")).expanduser()
RES = Path("cluster_results")

def checked_ca_sources(directory, names):
    provenance = json.loads((directory / "provenance.json").read_text())
    expected = {r["path"].removeprefix("cluster_results/ca/"): r["sha256"] for r in provenance["files"]}
    hashes = {}
    for name in names:
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Walkthrough grid input is missing: {path}; select a complete snapshot with WTB_CA_DIR")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in expected and digest != expected[name]:
            raise ValueError(f"Walkthrough grid input hash mismatch: {path}; select a coherent snapshot with WTB_CA_DIR")
        hashes[name] = digest
    return provenance, hashes

PROV, SOURCE_HASHES = checked_ca_sources(CA, [
    "index.json", "webapp_data_All_biodiversity.json", "us_cells.json",
    "ca_atrisk_richness.csv", "ca_inat_metrics.csv", "ca_atrisk_provenance.json",
    "atrisk_species_keys.csv", "atrisk_listings.csv",
])
INDEX = json.load(open(CA / "index.json"))

# row_format from the shipped index — the contract every webapp_data_*.json obeys.
COLS = INDEX["row_format"]            # [lat,lon,discover,conservation,env,staleness,urgency,travel_min,n_train]
from goal_presets import AXES     # the one definition, shared with the app build
print("row format :", COLS)
print("axes       :", AXES)

def load_group(group="All biodiversity"):
    d = json.load(open(CA / f"webapp_data_{group.replace(' ', '_')}.json"))
    rows = d[list(d)[0]]
    return pd.DataFrame(rows, columns=COLS)

ALL = load_group("All biodiversity")
print(f"loaded {len(ALL):,} cells × {len(COLS)} columns for 'All biodiversity'")

# A reusable Canada scatter-map used throughout. Cells are points on the 25 km lattice;
# small squares read as a surface. ASPECT ~ 1/cos(59°N) keeps Canada from looking stretched.
LON, LAT = ALL["lon"].to_numpy(), ALL["lat"].to_numpy()
ASPECT = 1.7
def canada_map(ax, values, title, cmap="magma", vmax=None):
    ax.scatter(LON, LAT, c=values, s=2.0, marker="s", cmap=cmap, vmin=0, vmax=vmax, linewidths=0)
    ax.set_title(title, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect(ASPECT)
    for s in ax.spines.values(): s.set_visible(False)

ALL.head(3)
""")

# ---------------------------------------------------------------- contents at a glance
md(r"""
## 1 · What's in it — contents at a glance

Everything is one fixed geometry reused across life groups. A *cell* is the atom: a 25 km
equal-area square of Canadian land carrying five priority axes plus travel cost. The app
also serves a 5 km tier, which is the same lattice split 5×5; this walkthrough reads the
25 km tier.
""")
code(r"""
lat = INDEX["lattice"]
print(f"Grid          : {INDEX['n_cells']:,} cells of {INDEX['res_m']//1000} km ({lat['cell_km2']:.0f} km² each)")
print(f"Lattice       : {lat['ncol']} × {lat['nrow']} equal-area cells (all of Canada + a US land sliver)")
print(f"Life groups   : {len(INDEX['groups'])} → {', '.join(INDEX['groups'])}")
print(f"Per cell      : 5 axes (0–1) + travel_min + n_train")
print(f"Grid manifest : {PROV['manifest_hash']}  ({len(PROV['files'])} recorded files)")
print("Walkthrough grid inputs, SHA256 (unlisted inputs are fingerprinted but not covered by the grid manifest):")
recorded = {r["path"].removeprefix("cluster_results/ca/") for r in PROV["files"]}
for name, digest in SOURCE_HASHES.items():
    print(f"  {name}: {digest}" + ("" if name in recorded else " (not listed in grid manifest)"))

# Inspect the shipped ranges. Nonzero values are not evidence of outcome validation.
print("\nAxis ranges (from the shipped data itself):")
for ax in AXES:
    col = ALL[ax].to_numpy(float)
    nz = (col != 0).mean()
    print(f"  {ax:13s} range [{col.min():.2f}, {col.max():.2f}]  nonzero in {nz*100:5.1f}% of cells")
stale_corr = np.corrcoef(ALL['discover'], ALL['staleness'])[0,1]
print(f"\n  corr(discover, shipped staleness) = {stale_corr:+.3f}")
print(f"  {(ALL['conservation']>0).sum():,} inventory cells have a nonzero conservation score")
""")
# ---------------------------------------------------------------- the data
md(r"""
## 2 · The data

Seven public sources (the conservation row combines CAN-SAR and GBIF), each chosen because it is **fetchable without credentials** and
**national in coverage**. For the five priority axes this is a hard constraint: they must be reproducible by
anyone and must not depend on the private McGill iNat-Canada parquet. The historical discovery layer (§5.1) is the exception: its raw shapefile and Parquet inputs are private, and only the derived rate artifact is published.

| Axis | Source | What it is | Resolution | Licence / access |
|---|---|---|---|---|
| discover | iNaturalist observation density (Biodiversité Québec STAC) | per-group "light up the map" record density — the same surface the official project uses | 1 km COG | public COG on Arbutus |
| conservation | **CAN-SAR** (COSEWIC/SARA) × **GBIF**, COL26.6 XR taxonomy | listed names × Canadian occurrences under a frozen allowlist | 0.01° policy checks → 0.25° source grid | CC-BY (OSF `10.17605/OSF.IO/E4A58`) + [GBIF download DOI](https://doi.org/10.15468/dl.6hqdyd) |
| env | **CHELSA** bioclimate | temperature, seasonality, precipitation | ~1 km | public (`/vsicurl`) |
| staleness | **iNaturalist open-data** dump; density fallback | all-time vs recent history where available, inverse density otherwise | 0.25° history source grid | public AWS dump and density COG |
| urgency | **Hansen** Global Forest Change | forest-loss fraction (logging / fire / dieback) | 0.05° | public |
| travel | **Weiss et al. 2018** accessibility | minutes to the nearest city | ~1 km | public (MalariaAtlas) |

**As of:** the GBIF at-risk download was created 2026-10-14, and the iNaturalist history in `ca_inat_metrics.csv` ends with last observation 2026-05-27.

A subtle but load-bearing data choice: the **land mask**. A cell is kept if it sits inside the
iNat density COG footprint *and* either the Weiss raster calls it land or it carries records.
Cells outside the COG are dropped rather than shown as false gaps. The grid is a *decision*,
not a given.
""")

# ---- why US
md(r"""
### 2.1 · Why the map spills into the United States

One thing you notice immediately: there is real data across Washington, Oregon, Idaho, Montana,
the Dakotas, Minnesota, Michigan, and Maine. That is **not a bug, but it is a known
consequence of two deliberate choices**, worth being explicit about:

1. **The lattice is a rectangle in projected space**, and its southern edge reaches ~41°N so
   that Canada's *southernmost* hotspots are covered — Point Pelee (41.9°N), the Carolinian
   zone, southern Vancouver Island. Reaching that far south scoops up a band of the northern US.
2. **The land mask is *geographic*, not *political*.** `build_fullgrid_ca.py` keeps a cell if
   the Weiss travel-time raster says it's land (ocean = nodata) — it does **not** clip to the
   Canada polygon. The Natural Earth refinement only removed ocean and Greenland, keeping
   Canada **and** US land.

The important honesty point is *what the US data is and isn't*: the density layer is the
**Canada-focused** `inat_canada_heatmaps` COG. Its footprint spills a little across the border,
but the overwhelming majority of US cells sit **outside** the Canadian recording layer — so
they read as "gaps" largely because *the Canadian density layer stops at the border*, not
because they are genuinely under-recorded. (Deep-US cells outside the COG are dropped by the
mask.) The figure quantifies it.

**The sharp horizontal seam along ~49°N is a data-footprint edge, not ecology.** The transect below inspects the western border band: Canadian cells north of the line have varied recording density, while many inventory cells to its south fall in the zero-density block, ordered only by climate. The shipped country mask hides foreign-border cells and excludes them from national ranks and trip candidates. These figures inspect the unmasked inventory to explain why that guard is necessary; they do not recommend its foreign-border cells.
""")
code(r"""
us_west = (ALL["lon"] < -95) & (ALL["lat"] < 49.0)         # clearly-US band (W of -95, S of the 49th-parallel border)
n_us = int(us_west.sum()); n_us_rec = int((us_west & (ALL["n_train"] > 0)).sum())

fig, ax = plt.subplots(figsize=(8.5, 4.6))
ax.scatter(ALL.lon[~us_west], ALL.lat[~us_west], s=2, marker="s", color="#cbd5e0", label="Canadian / border cells")
ax.scatter(ALL.lon[us_west],  ALL.lat[us_west],  s=2, marker="s", color="#c0392b", label="clearly-US cells")
ax.axhline(49, color="#2c3e50", lw=0.8, ls="--"); ax.text(-138, 49.4, "49°N border", fontsize=8, color="#2c3e50")
ax.set_aspect(ASPECT); ax.set_xticks([]); ax.set_yticks([])
for s in ax.spines.values(): s.set_visible(False)
ax.legend(loc="lower left", fontsize=9, markerscale=4)
ax.set_title(f"{n_us:,} cells (~{us_west.mean()*100:.0f}%) are clearly US — but only {n_us_rec} carry iNaturalist records")
plt.tight_layout(); plt.show()
print(f"Clearly-US cells: {n_us:,}  ·  of those with real iNat records: {n_us_rec}")
print("→ Most US cells are gaps only because the Canadian density COG stops at the border.\n")

# Transect across the western border (lon -125..-100): the seam at ~49°N is the COG edge.
band = (ALL.lon > -125) & (ALL.lon < -100)
print("Transect across the 49°N border (the visible seam):")
print(f"{'lat':>6} {'n_train':>9} {'%zero-obs':>10} {'discover':>9}")
edge = []
for lo in [47.5, 48.0, 48.5, 49.0, 49.5]:
    m = band & (ALL.lat >= lo) & (ALL.lat < lo + 0.5)
    side = "US " if lo < 49 else "CA "
    edge.append((ALL.n_train[m].mean(), (ALL.n_train[m]==0).mean()*100))
    print(f"{side}{lo:5.1f} {edge[-1][0]:9.0f} {edge[-1][1]:9.0f}% {ALL.discover[m].mean():9.2f}")
print(f"→ n_train {edge[0][0]:.0f}→{edge[-1][0]:.0f} and zero-obs {edge[0][1]:.0f}%→{edge[-1][1]:.0f}% from the southernmost to the northernmost band = a data-footprint edge, not ecology.")
""")

# ---------------------------------------------------------------- the five axes, visual
md(r"""
## 3 · The five axes, what each one looks like over Canada

Every square has five underlying **0–1 axes**. They summarize different kinds of recorded evidence, with the sampling-history fallback described below. The app's five Goal-menu choices are a separate set: three numeric presets plus Getting Even and Climate Gap (§5).

| Axis (code name) | The goal, in plain English | A high score means |
|---|---|---|
| `discover` | Go where few people have looked | Hardly any iNaturalist records nearby |
| `conservation` | Recorded conservation concern | More status-weighted species records under the shipped policy |
| `env` | Cover every habitat | The square's *climate type* is barely sampled anywhere |
| `staleness` | Revisit recorded gaps | Old records with few recent ones (0 below 20 historical records), or inverse density where the source cell is absent from the history file |
| `urgency` | Document changed forest habitat | Greater cumulative mapped forest loss, 2001–2023 |

Below they are side by side — read each map as *"where would this goal send me?"* They send you
to different places, which is exactly why you get to choose between them (§6). Each goal below
opens with a one-line plain-English version, then the exact method and the design choice behind it.
""")
code(r"""
titles = {
    "discover":     "Discover\n(under-sampled)",
    "conservation": "Find rare species\n(at-risk richness)",
    "env":          "Cover every habitat\n(climate surprisal)",
    "staleness":    "Freshest gaps\n(quiet lately)",
    "urgency":      "Sample before it's lost\n(forest loss)",
}
fig, axs = plt.subplots(1, 5, figsize=(15, 3.6))
for ax, k in zip(axs, AXES):
    canada_map(ax, ALL[k], titles[k], cmap="magma")
fig.suptitle(f"The five priority axes — 'All biodiversity', {len(ALL):,} real cells (darker = higher priority)", y=1.02)
plt.tight_layout(); plt.show()
""")

# ---- discover
md(r"""
### 3.1 · Discover — and the **de-saturation choice**

> **In plain terms:** *go where few people have looked.* A square scores high if hardly anyone
> has recorded there on iNaturalist.

**Measures:** how under-sampled a square is — fewer records, higher discover.

**The choice (why it's a *ramp*, not just "1 ÷ records").** The obvious formula is
`norm(1 / (density + ε))`. We *don't* ship it. On the
real national density it is violently bimodal: ~half the cells have zero research-grade
records and pin at 1.0, while every recorded cell collapses to near-zero — the map becomes a
binary "recorded / not", with no gradient to plan a trip on. The shipped axis is instead a
**smooth under-sampling ramp**: records per km² mapped through a fixed national quantile ramp, with zero-record cells ordered by climate distinctiveness
(`env`). Within one 5 km cell, recorded cells score below zero-record cells. A 25 km cell is the mean of its 25 children, so the two groups overlap there; the cell below prints by how much. The figure shows why the ramp is the better content.
""")
code(r"""
# n_train ≈ density × cell-area, so it's a faithful stand-in for the raw density the
# naive formula would have used. Compare the naive saturating transform to the shipped rank.
dens = ALL["n_train"].to_numpy(float)
naive = 1.0 / (dens + 1e-3)
naive = (naive - naive.min()) / (naive.max() - naive.min())   # min–max norm(1/density)
shipped = ALL["discover"].to_numpy(float)                      # the de-saturated rank

fig, axs = plt.subplots(1, 3, figsize=(14, 3.8))
axs[0].hist(naive, bins=60, color="#c0392b"); axs[0].set_title("Naive  norm(1/density)\nbimodal: a binary mask")
axs[0].set_xlabel("axis value"); axs[0].set_ylabel("cells")
axs[1].hist(shipped, bins=60, color="#2980b9"); axs[1].set_title("Shipped  under-sampling ramp\nsmooth: a plannable gradient")
axs[1].set_xlabel("axis value")
canada_map(axs[2], shipped, "Shipped discover over Canada", cmap="magma")
frac_pinned = (naive > 0.99).mean()
fig.suptitle(f"Why a ramp, not 1/density:  {frac_pinned*100:.0f}% of cells pin at the max under the naive transform", y=1.03)
plt.tight_layout(); plt.show()
zero = (ALL["n_train"] == 0).to_numpy()
above = (shipped[~zero] > shipped[zero].min()).mean()
print(f"25 km cells: {(~zero).sum():,} recorded, {zero.sum():,} zero-record; {above*100:.0f}% of recorded cells score above the lowest zero-record cell.")
""")

# ---- conservation
md(r"""
### 3.2 · Find rare species — and the **dual-use safety choice**

> **In plain terms:** prioritize cells with more recorded status-weighted conservation concern under the shipped assessment snapshot and occurrence policy. This is a coarse recording signal, not a map of exact locations or current legal status.

**Measures:** recorded conservation concern. **Formula:** in each source cell, sum the maximum applicable status weight once per retained species (**Endangered 3 · Threatened 2 · Special Concern 1**), then min–max to 0–1.

**The design choice:** expose a coarse all-taxa score. Pollock et al. 2025 (*Nat Rev Biodiversity*, Box 3) warns that fine-grained threatened-species maps can aid poaching or collection. The download aggregates records to 0.01° for policy checks; `build_atrisk_layer.py` then counts retained species in 0.25° source cells. Their normalized score is joined to each 5 km cell by its centre, and a 25 km cell is the mean of its 25 children. A 5 km square can therefore display a value inherited from the same coarse source cell as its neighbours, and a 25 km value can blend several neighbouring source cells, so the national maximum falls below 1.0 (printed below). Neither is a new inventory of species at risk.

**What the snapshot can establish:** its CAN-SAR names and statuses, COL XR matches, GBIF record filters, and selected allowlist are traceable below. It retains species-level counting for most listings, so separate populations can still be combined. Matching a name also does not settle a changed biological concept: the crosswalk's historical *Bartramia stricta* match remains ambiguous against the revised Canadian *Bartramia aprica* concept in the [COSEWIC 2023 report](https://www.canada.ca/en/environment-climate-change/services/species-risk-public-registry/cosewic-assessments-status-reports/rigid-apple-moss-2023.html). A zero score means no retained record under this policy, not absence. The exact filters, safeguards and build checks are in the conservation reference in `METHODOLOGY.md`.
""")
code(r"""
atrisk_prov = json.load(open(CA / "ca_atrisk_provenance.json"))
crosswalk = pd.read_csv(CA / "atrisk_species_keys.csv", keep_default_na=False)
listings = pd.read_csv(CA / "atrisk_listings.csv", keep_default_na=False)
matched_names = set(crosswalk.loc[crosswalk.col_key != "", "name"])
n_matched_listings = sum(any(name.strip() in matched_names for name in names.split(";")) for names in listings.names)
policy = atrisk_prov["shipping_policy"]
n_retained = len(policy["retained_listing_ids"])
n_guarded = len(policy["required_polygon_listing_ids"])
snapshot = pd.DataFrame([
    ("GBIF occurrence download DOI", atrisk_prov["doi"]),
    ("GBIF download created", atrisk_prov["created"][:10]),
    ("Catalogue of Life release", atrisk_prov["taxonomy"]["shortName"]),
    ("Catalogue of Life DOI", atrisk_prov["taxonomy"]["doi"]),
    ("Listed names in crosswalk", len(crosswalk)),
    ("Names with a primary key", len(matched_names)),
    ("Distinct primary species keys", crosswalk.loc[crosswalk.col_key != "", "col_key"].nunique()),
    ("Matched CAN-SAR listings", n_matched_listings),
    ("Selected occurrence policy", policy["method"]),
    ("Retained listings", n_retained),
    ("Retained species-level listings", n_retained - n_guarded),
    ("Retained polygon-guarded listings", n_guarded),
    ("Excluded matched listings", len(policy["dropped_matched_listing_ids"])),
], columns=["Frozen conservation snapshot", "Value"])
display(snapshot)

cons = pd.read_csv(CA / "ca_atrisk_richness.csv")
top = cons.nlargest(8, "score_raw").reset_index(drop=True)

def name_place(lat, lon):
    spots = [("Point Pelee / Carolinian SW Ontario", 42.0, -82.5),
             ("Southern Vancouver Island / Garry Oak", 48.5, -123.4),
             ("Okanagan", 49.9, -119.5), ("Lower Mainland BC", 49.2, -122.5),
             ("Niagara / Golden Horseshoe", 43.1, -79.4), ("Montréal / St. Lawrence", 45.5, -73.6)]
    return min(spots, key=lambda s: (s[1]-lat)**2 + (s[2]-lon)**2)[0]
top["nearest reference label"] = [name_place(r.lat, r.lon) for r in top.itertuples()]
# Region first, with explicit widths, so its long labels get the room and never clip the right edge.
top = top[["nearest reference label", "score_raw", "lat", "lon"]]

fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
canada_map(axs[0], ALL["conservation"], "Conservation axis (25 km, all-taxa pooled)", cmap="magma")
axs[1].axis("off")
axs[1].set_title("Highest recorded status-weighted 0.25° source cells", fontsize=10)
tbl = axs[1].table(cellText=top.round(2).values, colLabels=top.columns, loc="center",
                   cellLoc="left", colWidths=[0.52, 0.16, 0.16, 0.16])
tbl.auto_set_font_size(False); tbl.set_fontsize(8); tbl.scale(1, 1.5)
fig.suptitle("Recorded conservation concern, pooled across taxa on a coarse source grid", y=1.0)
plt.tight_layout(); plt.show()
print(f"Maximum 25 km conservation value: {ALL.conservation.max():.2f} (a mean of 25 child cells)")
print("Reference labels are the nearest of six named places, not independent hotspot validation.")
print("Caveat: CAN-SAR is an assessment snapshot; under-assessed groups and taxon-concept ambiguity remain.")
""")

# ---- env
md(r"""
### 3.3 · Cover every habitat — sampling Canada's rarest climates

> **In plain terms:** *go where the climate itself is barely recorded.* Some climate types —
> a particular cold, wet, coastal mix, say — are hardly sampled anywhere in Canada. A square
> with such a climate scores high **even if it sits next to a city.**

**Measures:** how under-sampled a square's *climate type* is (not its location).
**How:** we describe each square by three CHELSA numbers (temperature, how much it swings
through the year, and rainfall), then ask "how many *already-recorded* places have a climate
like this one?" Few similar recorded places → high score. (Technically this is *climate
surprisal*: a density estimate in 3-D climate space, mapped to 0–1 through a national ramp — a rarely-seen climate is
"surprising," so it scores high.)

**The choice:** a geographic gap is not the same as a *climate* gap. Scoring climate rarity
rather than distance keeps this goal genuinely different from "discover" — we check that in §6.
""")
code(r"""
fig, axs = plt.subplots(1, 2, figsize=(12, 3.8))
canada_map(axs[0], ALL["env"], "How rare each square's climate is", cmap="magma")
axs[1].hist(ALL["env"], bins=60, color="#16a085")
axs[1].set_title("Mapped through the national ramp → uses the full 0–1 range"); axs[1].set_xlabel("env"); axs[1].set_ylabel("cells")
plt.tight_layout(); plt.show()
""")

# ---- staleness
md(r"""
### 3.4 · Freshest gaps — and the **iNat-only data-integrity choice**

> **In plain terms:** the observed-history component highlights places recorded heavily in the past and rarely in the last five years. Where history is unavailable, the axis falls back to sparse recording density.

**Observed-history component:** a 0.25° source cell with ≥20 historical records uses `staleness = (1 − recent/all-time) · log(1 + all-time records)`, scaled 0–1. Lots of old records and few in the last five years give a high value. A source cell with 1 to 19 records scores 0 and does not fall back. Each 5 km cell takes its source value by its centre, and a 25 km cell is the mean of its 25 children. The history ends with last observation 2026-05-27.

**The source choice:** use iNaturalist history for an iNaturalist recording objective, rather than treating GBIF's mix of iNaturalist, eBird and museum dates as equivalent. **The missing-data choice:** where the source cell is absent from the history file, the builder substitutes the selected group's normalized inverse observation density. A high fallback value therefore means sparse records, not proven recent silence. The figure inspects the combined shipped axis and example observed-history counts; it does not independently validate both components or predict a revisit's outcome.
""")
code(r"""
met = pd.read_csv(CA / "ca_inat_metrics.csv")
# Show staleness is a genuinely distinct signal, and surface a concrete "quiet now" cell.
fig, axs = plt.subplots(1, 2, figsize=(12, 4.0))
s = ALL.sample(6000, random_state=0)
axs[0].scatter(s["discover"], s["staleness"], s=4, alpha=0.25, color="#8e44ad")
axs[0].set_xlabel("discover (under-sampled)"); axs[0].set_ylabel("staleness (quiet lately)")
r = np.corrcoef(ALL["discover"], ALL["staleness"])[0,1]
axs[0].set_title(f"Distinct, not a mirror — corr = {r:+.2f}\n(well-recorded ≠ recently-recorded)")
busy_then_quiet = met[(met.n_all >= 200)].assign(recent_frac=lambda d: d.n_recent/d.n_all).nsmallest(8, "recent_frac")
axs[1].axis("off"); axs[1].set_title("Recorded a lot all-time, almost nothing recently", fontsize=10)
# 0.25° source-cell centres from the gi/gj indices, so the coordinates stay in the table
show = busy_then_quiet.assign(lat=(busy_then_quiet.gi + 0.5) * 0.25, lon=(busy_then_quiet.gj + 0.5) * 0.25)[["lat","lon","n_all","n_recent"]].round(2)
t = axs[1].table(cellText=show.values, colLabels=show.columns, loc="center"); t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1,1.5)
plt.tight_layout(); plt.show()
""")

# ---- urgency
md(r"""
### 3.5 · Sample before it's lost, cumulative forest change

This axis measures the Hansen forest-loss fraction accumulated over **2001–2023**, normalized to 0–1. High values indicate more cumulative mapped forest loss from causes such as logging, fire or dieback. It is not a measurement of current loss or a forecast of imminent habitat change.

The Fort McMurray fire scar scores about 0.65, as printed below; saturated southern cities ~0.
""")
code(r"""
fig, ax = plt.subplots(figsize=(7.5, 4.2))
canada_map(ax, ALL["urgency"], "Urgency — Hansen forest-loss fraction", cmap="magma")
plt.tight_layout(); plt.show()
fm = ALL.iloc[((ALL.lat-56.7)**2 + (ALL.lon+111.4)**2).idxmin()]
print(f"Fort McMurray-area cell urgency = {fm.urgency:.2f}  (vs national median {ALL.urgency.median():.3f})")
""")

# ---------------------------------------------------------------- composite
md(r"""
## 4 · The score — "impact 0–100"

> **In plain terms:** pick a fixed numeric preset. Its popup percentile compares the enclosing **25 km cell** with Canada-eligible 25 km cells for your selected species group. At 5 km the finer colour and live records describe the selected cell, while this percentile still describes its 25 km parent. It is a relative recording priority, not a probability or forecast.

**How it's built:**

1. **Blend:** multiply each axis's 0–1 value by its fixed preset weight and add (`raw = w₁·discover + w₂·conservation + …`). The weights express the recording objective.
2. **Colour:** bake `clip(colour_blend / preset.scale, 0, 1)` through viridis, combining the colour stack's axes with the preset weights, per group, preset and tier. Species discovery uses scale **1.6**, the other numeric presets **1.0**. The release's 25 km colour stacks average 5 km axes with NaN-aware means. Popup components and national ranks now use those same final-stack inputs, retaining float32 precision to avoid rounding-induced rank ties or colour-bin changes. The viewer then stretches those quantized colours to cells on screen, so panning and zooming can change the fill. Too few cells or too little colour spread retain the national colour scale, as named by the legend.
3. **Rank:** order raw blends among Canada-eligible **25 km** cells of the selected group. Equal values share their average rank; 0 and 100 are reached only at untied endpoints. A constant or singleton population gets zero, and an unavailable country mask withholds ranks. The colour stretch never changes this number.

The raw blend and its percentile rank answer different questions. The raw blend expresses the chosen axis weights; the rank expresses relative position among eligible cells, with ties assigned their average position. The figure compares linear rescaling with national average ranks, both using the same aggregated 25 km inputs as the colour stacks. Neither scale estimates ecological benefit.

This illustration reproduces the shipped eligibility and average-tie ranking policy. It is separate from the raw preset-blend parity checked in Section 8.
""")
code(r"""
from goal_presets import DEFAULT              # the shipped default preset, not a copy of it
raw = sum(w * ALL[a].to_numpy(float) for a, w in zip(AXES, DEFAULT) if w)
hidden = set(json.load(open(CA / "us_cells.json"))["us_cells"])
eligible = np.array([f"{la:.3f},{lo:.3f}" not in hidden for la, lo in ALL[["lat", "lon"]].to_numpy()])
minmax = np.full_like(raw, np.nan)
minmax[eligible] = (raw[eligible] - raw[eligible].min()) / (raw[eligible].max() - raw[eligible].min()) * 100
pct = np.full_like(raw, np.nan)
pct[eligible] = (pd.Series(raw[eligible]).rank(method="average").to_numpy() - 1) * 100 / (eligible.sum() - 1)

fig, axs = plt.subplots(2, 2, figsize=(12, 7.4))
canada_map(axs[0,0], minmax, "Linear rescaling of the raw blend", cmap="magma", vmax=100)
canada_map(axs[0,1], pct,    "National average ranks, eligible cells", cmap="magma", vmax=100)
axs[1,0].hist(minmax[np.isfinite(minmax)], bins=60, color="#c0392b"); axs[1,0].set_title("Distribution of the linearly rescaled blend"); axs[1,0].set_xlabel("score 0–100"); axs[1,0].set_ylabel("squares")
axs[1,1].hist(pct[np.isfinite(pct)], bins=60, color="#27ae60"); axs[1,1].set_title("Distribution of national average ranks"); axs[1,1].set_xlabel("score 0–100")
crushed = (minmax[eligible] < 5).mean()
fig.suptitle(f"Same eligible cells, two scales; {crushed*100:.1f}% below 5/100 on the linear scale", y=1.0)
plt.tight_layout(); plt.show()
""")

# ---------------------------------------------------------------- presets
md(r"""
## 5 · The Goal menu, three numeric presets and two other views

A where-to-go map expresses a recording objective. **Spatial Gap**, **Species discovery**, and **Conservation** use the fixed blends below and a national priority percentile. **Getting Even** instead recommends a taxonomic group, and **Climate Gap** is a separate climate-coverage raster. Those two views have their own legends, and show no numeric preset percentile. The five menu choices are distinct from the five underlying axes shown in §3.

`env` has zero direct weight in all three presets, but climate coverage orders the zero-density cells inside `discover`. That order is set per 5 km cell; a 25 km cell averages 25 children, so its recorded and zero-record cells overlap in score. Species discovery's `staleness` term scores 0 for source cells with 1 to 19 records and includes the inverse-density fallback where a source cell is absent from the history file. Each of the three presets has a join link. The links resolve the current general Blitz the Gap iNaturalist project; the table shows their frozen fallback slug, not three independent campaign projects.
""")
code(r"""
# straight from goal_presets.py, the same list build_webapp.py injects into index.html
from goal_presets import PRESETS
pd.set_option("display.max_colwidth", 60)
pdf = pd.DataFrame([(p["name"], *p["w"], p["proj"]) for p in PRESETS],
                   columns=["preset",*AXES,"iNat project"])
display(pdf)

# Two presets, two genuinely different maps — same geometry, different weighting.
def impact_pct(weights):
    raw = sum(weights[i]*ALL[AXES[i]].to_numpy(float) for i in range(5))
    p = np.full_like(raw, np.nan)
    p[eligible] = (pd.Series(raw[eligible]).rank(method="average").to_numpy() - 1) * 100 / (eligible.sum() - 1)
    return p
fig, axs = plt.subplots(1, 3, figsize=(14, 3.8))
for ax,p in zip(axs, PRESETS):
    canada_map(ax, impact_pct(p["w"]), p["name"], cmap="magma", vmax=100)
fig.suptitle("Same cells, different values \u2192 different trips (" + " \u00b7 ".join(p["name"] for p in PRESETS) + ")", y=1.03)
plt.tight_layout(); plt.show()
""")
md(r"""
### 5.1 · Three more shipped layers

The Map display control also carries layers that are not numeric presets and show no priority percentile.

- **Getting Even count cutoffs.** The viewer can hide Getting Even recommendations in cells with fewer aggregate records than a cutoff of 1, 5, 10, 20, 30 or 100, without recalculating any group score. The default is 1. Counts belong to the displayed tier, include all taxa, and may be rounded density-derived proxies rather than verified observations.
- **Historical discovery evidence.** A separate measured layer: a lab-supplied 2025 point set (826 features) per 10,000 research-grade observations in each 5 km cell, summed to 25 km as a ratio of sums. It is not the Species discovery preset, and it is not a forecast.
- **Conservation Challenge sightings.** Recent open iNaturalist sightings of the 335 challenge target taxa (313 species, 10 genus, 9 variety and 3 subspecies, from `webapp/challenge_taxa.json`), plotted at their reported coordinates. The snapshot query (`conservation_challenge.py`, built by the `conservation-challenge.yml` workflow) uses the Canada place, the last 14 days, `geoprivacy=open` and `taxon_geoprivacy=open`. It applies no `threatened` filter.
""")

# ---------------------------------------------------------------- disagreement
md(r"""
## 6 · Do the underlying axes point to different places?

> **In plain terms:** the matrix compares the five underlying axes, separately from the five Goal-menu choices. It measures how strongly pairs of axes rank recorded cells in the same order: **+1** means the same order, **0** no rank correlation, and **−1** the reverse order. This describes the selected snapshot's score relationships, not outcomes from visits.

(Computed over squares that actually have records, `n_train > 0`.)

> The population matters: `discover` orders zero-density cells by `env` (per 5 km cell; 25 km averaging overlaps the recorded and zero-record groups), so their whole-grid association includes an intentional shared component. Restricting to recorded cells removes those zero-density cells and changes the comparison; it does not remove observer or habitat bias.
""")
code(r"""
rec = ALL[ALL["n_train"] > 0]
M = rec[AXES].corr(method="spearman")
fig, ax = plt.subplots(figsize=(5.8, 5))
im = ax.imshow(M, cmap="RdBu_r", vmin=-1, vmax=1)
ax.set_xticks(range(5)); ax.set_yticks(range(5))
ax.set_xticklabels(AXES, rotation=40, ha="right"); ax.set_yticklabels(AXES)
for i in range(5):
    for j in range(5):
        ax.text(j, i, f"{M.iloc[i,j]:+.2f}", ha="center", va="center",
                color="white" if abs(M.iloc[i,j])>0.5 else "black", fontsize=9)
ax.set_title(f"Rank correlation between pairs of underlying axes\n(+1 same order · 0 uncorrelated · −1 reverse; over {len(rec):,} recorded squares)")
fig.colorbar(im, fraction=0.046, pad=0.04); plt.tight_layout(); plt.show()
""")

# ---------------------------------------------------------------- trip planner
md(r"""
## 7 · Optional development view, trip planning

**Disabled by default:** `build_webapp.py` ships `PLAN_ENABLED = False`. The planner is an optional development view available only by opting in with `?plan=1`; the standard Explore view does not ask for a start or time budget or display trip routes.

> **When enabled:** the planner accepts a start and time budget, ranks reachable recording priorities and estimates time available in the field. It selects a walking, cycling or driving option and requests a route. These mechanics are planning estimates, not validated trip outcomes.

**The choices that keep it honest:**

- **Travel only ever *costs* you — it never raises a square's score.** A trip is ranked by
  *how good the square is × how long you'd get to record there*. Closer squares win partly
  because you spend less time travelling and more time recording — but being easy to reach
  never makes a square "better." That's deliberate: otherwise everyone gets sent to the same
  roadside spots and the gaps never get filled.
- **Greenest mode that fits.** It defaults to the greenest option — **walk, then bike, then
  drive** — that can still reach the square within your budget, decided from *your* start.
- **Real routes, honest fallback.** Walk/bike/drive routes come from real road data
  (OpenStreetMap routing). If a route can't be fetched, it falls back to a straight-line
  estimate and **says so**. Driving counts ≈ 0.18 kg CO₂ per km; walking and cycling, zero.
- **"Worth the drive."** By default a trip must let you record for at least *half* as long as
  you travel — so you don't get a 4-hour drive for 1 hour in the field.

(No figure here — this is interaction, not data — but it's the same discipline: every default
is a defensible choice, and any estimated route is labelled.)
""")

# ---------------------------------------------------------------- validation
md(r"""
## 8 · What the retrospective test measures

The archived backtest measures new **taxon IDs in a sampled record set**, not species that a future trip will discover. It used the declared 25 km equal-area lattice, original normalized grid inputs and preset weights recorded in each result's `run` metadata. These outcomes have not been recomputed for the rebuilt grid shown in this walkthrough; shared geometry and scoring code do not make the data snapshots identical. The original group-JSON and record-input hashes are listed below.

1. Split the cached project records on **observed_on = 2025-07-01**. Their historical upload, identification and research-grade availability are unknown.
2. For each cell with earlier records and at least five later records, calculate the expected number of distinct later taxon IDs absent from its sampled earlier records, in a draw of **K = 5 observations without replacement**.
3. Compare that expected record novelty with each score using Spearman correlation. Equal observation counts do not establish equal field hours, visits or detectability.

The earlier 0.25-degree join could merge independent projected cells and rescaled the axes within each sample. The archived corrected results below use exact projected cell assignments and preserve their original normalized axis values. The legacy key `app_leakfree` names a retrospective training-period proxy, with historical availability unknown.

The outcome includes species and infraspecific taxon IDs. The sample is page-capped and excludes previously unobserved cells. Published axes use later information, including all-time density and a later occurrence download. The permutation probabilities are nominal, assume exchangeable cells, and have no spatial blocking or multiple-comparison adjustment. These are exploratory associations, not prospective or causal estimates of trip yield. See [METHODOLOGY.md](https://github.com/PollockLab/where-to-blitz/blob/main/METHODOLOGY.md) for the full scope and reproduction commands.
""")
code(r"""
app = json.load(open(RES / "voi_appscore_results.json"))
labels = {
    "training-period scarcity proxy": "app_leakfree",
    "archived Spatial Gap snapshot": "app_shipped",
    "archived Species discovery snapshot": "species_discovery_shipped",
}
bt = pd.DataFrame([
    {"taxon": r["taxon"], **{label: r["scores"][key]["spearman"] for label, key in labels.items()}}
    for r in app
]).set_index("taxon")
fig, ax = plt.subplots(figsize=(9.5, 4.4))
bt.plot.bar(ax=ax, color=["#27ae60", "#2980b9", "#a26bba"], width=0.8)
ax.axhline(0, color="k", lw=0.8)
ax.set_ylabel("Spearman correlation with expected record novelty")
ax.set_title("Retrospective sampled-record association, BC")
ax.legend(loc="lower right", fontsize=9); ax.set_xlabel("")
plt.xticks(rotation=0); plt.tight_layout(); plt.show()
display(bt.round(2))
print("K = 5 observations, not visits or field hours. Historical record availability is unknown.")
""")
md(r"""
**The training proxy and the archived grid scores answer different questions.** The proxy uses the sampled count of records dated before the split. The archived Spatial Gap score uses a national density ramp built later. Neither comparison reevaluates or validates the refreshed grid snapshot shown above.

**A smaller sampled history makes novelty easier to obtain.** That is useful for finding record gaps, but it does not by itself establish ecological discovery skill beyond the coverage already recorded. The inverse training-count score is the same ordering reversed and is not an independent negative control.

**Each goal needs an outcome matching its purpose.** The Species discovery blend (`discover` plus `staleness`) has its own archived result, the third bar in the chart above and the third column in the Eastern table below. Its correlations with expected record novelty are about zero, from -0.07 to 0.13 across the five BC taxa and from -0.06 to 0.00 across the three Eastern taxa. In this sampled-record test it showed no association with record novelty. Conservation prioritizes status and habitat change; a novelty association does not validate or invalidate that objective.
""")
code(r"""
east = json.load(open(RES / "voi_appscore_east_results.json"))
er = pd.DataFrame([
    {"taxon": r["taxon"], **{label: r["scores"][key]["spearman"] for label, key in labels.items()}}
    for r in east
]).set_index("taxon")
print("Eastern Canada, a separate retrospective regional sample:")
display(er.round(2))
print("A positive association in another region does not establish prospective field performance.")

snapshots = pd.DataFrame([
    {"Archived sample": f"{region} / {r['taxon']}",
     "Original group JSON SHA256": r["run"]["grid"]["source_sha256"]["group"],
     "Original record input SHA256": r["run"]["input_sha256"]}
    for region, results in [("BC", app), ("East", east)] for r in results
])
with pd.option_context("display.max_colwidth", None):
    display(snapshots)
""")

md(r"""
### Sampling-history sensitivity

The paired check fixes the sampled training count at M = 20 and the later count at K = 5. It compares full-history novelty and standardized-history novelty on exactly the same eligible cells. Smaller histories make taxon IDs easier to call new; this check asks whether the association extends beyond that difference in recorded coverage. It changes the estimand and does not remove observer, habitat or seasonal bias.
""")
code(r"""
names = {"Aves": "Birds", "Insecta": "Insects", "Mammalia": "Mammals"}
paired = []
for region, records in [("BC", app), ("East", east)]:
    for r in records:
        h = r["training_history_sensitivity"]
        p = h["paired_scores"].get("discover_leakfree")
        if not p:
            print(f"{region} / {r['taxon']}: {h['n_cells']} paired cells, association unavailable")
            continue
        paired.append({"sample": f"{region} / {names.get(r['taxon'], r['taxon'])} ({h['n_cells']} cells)",
                       "full sampled history": p["full_training"]["spearman"],
                       "20-record training draw": p["standardized_training"]["spearman"]})
paired = pd.DataFrame(paired).set_index("sample")
fig, ax = plt.subplots(figsize=(9.5, 4.8))
paired.plot.bar(ax=ax, color=["#2980b9", "#a26bba"], width=0.75)
ax.axhline(0, color="k", lw=0.8)
ax.set_ylabel("Scarcity correlation with expected record novelty")
ax.set_title("Sampling-history sensitivity, identical paired cohorts")
ax.set_xlabel(""); ax.legend(fontsize=9)
plt.xticks(rotation=25, ha="right"); plt.tight_layout(); plt.show()
display(paired.round(2))
""")
md(r"""
### Spatial sensitivity

The archived spatial sensitivity report (`cluster_results/appscore_spatial_sensitivity.json`) checks whether that paired association change depends on one geographic cluster. It deletes each occupied projected block in turn, at widths of 50, 100 and 200 km and with zero and half-width row and column offsets, and recomputes both correlations on identical retained cells. Across every deletion the scarcity change stays negative in all six qualifying cohorts, from -0.698 to -0.217. These are descriptive envelopes, not confidence intervals. BC amphibians and reptiles stay unavailable with five and eight paired cells. The check is an influence diagnostic, not blocked cross-validation, and it does not remove spatial dependence.
""")

# ---------------------------------------------------------------- responsible use
md(r"""
## 9 · Responsible use

These constraints shaped the contents above; they are the reason some things are *coarse* or
*absent* on purpose:

- **Species-at-risk are gated out of the popup's suggestions.** Every live iNaturalist fetch carries
  `taxon_geoprivacy=open` and the "fill the gap" suggestions add `threatened=false`. The tool
  will not point a stranger at a rare orchid.
- **The conservation axis is coarse by design** (Section 3.2): an all-taxa score from a 0.25° source bin, joined to 5 km cells by centre and averaged to 25 km. It does not identify individual species or precise locations. Dual-use mitigation (Pollock et al. 2025, Box 3) remains a constraint on finer source layers.
- **Indigenous data sovereignty.** Obscure sensitive locations and respect
  [Indigenous data sovereignty](https://native-land.ca) before any public use; a territory
  acknowledgment is linked in-app.
- **Point layers are not coarse.** Conservation Challenge sightings are open observations plotted at their reported coordinates with no `threatened` filter, and the historical discovery markers sit at supplied reported positions, some of them source-obscured. Unlike the conservation axis, neither is aggregated to a coarse source cell.
- **It is a companion tool, not an official project page** (the in-app wording), and a *planning aid, not a census*. iNaturalist counts
  are sampling effort, not ground-truth abundance, and are labelled that way throughout.
""")

# ---------------------------------------------------------------- the stack
md(r"""
## 10 · How it's delivered — the stack

> **In plain terms:** the priority inputs are frozen files served by a static website. The popup's iNaturalist records are live external requests, as are routes in the optional planner when enabled, so those results can change independently of the frozen scores.

Python builds the lattice and fills it; `build_webapp.py` inlines the result into one self-contained `index.html`, byte-identical for the same inputs. The page is plain JavaScript with MapLibre GL JS and pmtiles.js, on GitHub Pages. The map layers have different spatial support and display rules:

| Layer | Served as | Built by |
|---|---|---|
| Base map | XYZ raster tiles (CARTO / ArcGIS / OpenTopoMap) | — |
| Cell geometry | GeoJSON polygons, in the LAEA lattice of Section 2 | `grid_lattice.py` |
| Cell colours | One PNG per (group, goal, tier), **one pixel per cell**, painted onto those polygons | `build_grid_values.py` |
| Density overlay | Raster PMTiles, magma baked in at build time | `build_density_pmtiles.py` |
| Climate Gap overlay | Separate climate-coverage raster PMTiles, with its own legend | Release asset, built outside this repository |
| Getting Even | One category PNG per tier, with count and richness-gate images | `build_gettingeven.py` |
| Historical discovery | Published rate artifact; at 5 km, a point-inspection derivative | `build_discovery_layer.py`, `historical_discovery_points.py` |
| Conservation Challenge | Sightings snapshot JSON, staged for the Pages build | `conservation_challenge.py`, `prepare_optional_layers.py` |

Colours are a PNG rather than tiles because warping rotated LAEA cells into Mercator pixels
left a staircase that never matched the lattice (#116). Both docstrings carry the full reasoning.
""")

# ---------------------------------------------------------------- reproduce
md(r"""
## 11 · Reproduce everything

```bash
# 1. environment (no credentials needed; all sources public)
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt

# 2. optional layers: stage the discovery rate and Conservation Challenge snapshot before the build
#    (reads DISCOVERY_RATE_URL/_SHA256 and CHALLENGE_TAXA_URL/_SHA256; no configuration means no activation)
.venv/bin/python prepare_optional_layers.py

# 3. regenerate the national build from cluster_results/ca/ (deterministic, byte-identical)
.venv/bin/python build_webapp.py          # → index.html

# 4. rebuild + execute THIS walkthrough, then render the HTML the in-app link points to
.venv/bin/python _build_walkthrough.py
export WTB_CA_DIR=/path/to/validated/cluster_results/ca
.venv/bin/python -m nbconvert --to notebook --execute --inplace where-to-blitz-walkthrough.ipynb
.venv/bin/python render_walkthrough_html.py    # → where-to-blitz-walkthrough.html (adds figure alt text)
```

Select a coherent grid directory with `WTB_CA_DIR`; leaving it unset uses `cluster_results/ca`. The render verifies consumed JSON/CSV inputs against available provenance entries before drawing figures and prints the manifest plus all consumed input hashes in Section 1. A checkout mixing older grid provenance with newer conservation CSVs fails clearly. The published figures were refreshed from the validated rebuild snapshot; `WTB_CA_DIR` does not change the archived backtest files in `cluster_results/`.

Section 8 displays the archived results without rerunning them and prints their original group-JSON and record-input hashes. `.venv/bin/python verify_appscore_results.py --download` fetches the archived record inputs from `grid-inputs-v1`, checks their hashes and replays the eight benchmarks without replacing the archived results; add `--inputs-dir` to keep the downloads. A fresh API pull or a newer grid is a new evaluation. The optional cell below queries today's iNaturalist API.
""")
code(r"""
# OPTIONAL — live iNaturalist "fill the gap" near a chosen cell. Guarded: skips cleanly offline.
import urllib.request
def fill_the_gap(lat, lon, radius_km=25):
    url = ("https://api.inaturalist.org/v1/observations/species_counts"
           f"?lat={lat}&lng={lon}&radius={radius_km}&quality_grade=research"
           "&geoprivacy=open&taxon_geoprivacy=open&threatened=false&per_page=5")
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.load(r)
try:
    # pick the highest-priority reachable-ish CANADIAN cell under the default preset
    # (exclude the clearly-US band from §2.1 so the demo stays on-mission)
    cand = ALL.assign(imp=pct, usw=us_west.to_numpy()).query("travel_min < 600 and not usw")
    cell = cand.nlargest(1, "imp").iloc[0]
    print(f"Top reachable gap under default preset: ({cell.lat:.2f}, {cell.lon:.2f})  impact≈{cell.imp:.0f}/100")
    res = fill_the_gap(cell.lat, cell.lon)
    print(f"iNaturalist shows {res['total_results']:,} research-grade species in the surrounding 25 km. Sample:")
    for t in res["results"][:5]:
        print(f"  • {t['taxon'].get('preferred_common_name', t['taxon']['name'])}  ({t['count']} obs)")
except Exception as e:
    print(f"(live iNaturalist call skipped — offline or rate-limited: {type(e).__name__})")
""")

md(r"""
---
*Built by `_build_walkthrough.py`: map figures trace to the selected `WTB_CA_DIR` snapshot and printed input hashes; retrospective figures trace to the archived result files in `cluster_results/`. The optional live demonstration uses today's iNaturalist API.*
""")

nb.write("where-to-blitz-walkthrough.ipynb")
