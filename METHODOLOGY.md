# Where to Blitz the Gap — methodology

A terse reference for how every number on the map is computed. For the narrated
version with figures and the validation backtest, see the
[full walkthrough](https://pollocklab.github.io/where-to-blitz/where-to-blitz-walkthrough.html).
Each priority axis is scored **0–1 per cell**, then your chosen weights are combined
into the **0–100 "impact"** shown on the map and in popups.

- **Grid:** Canada on a **0.25° (~25 km) grid**, 31,804 land cells. Land vs. ocean is
  decided by the Weiss travel-time raster (ocean = nodata).
- **Per group:** the same geometry is reused for all 11 life-group layers (All biodiversity,
  Plants, Insects, Birds, Fungi, Mammals, Fishes, Reptiles, Amphibians, Arachnids, Molluscs).

---

## The five priority axes

### 1. Discover the most species — `discover`
- **What it measures:** how under-sampled a cell is. High = few people have recorded there.
- **Source:** per-group **iNaturalist observation-density** raster (Biodiversité Québec STAC,
  1 km, sampled at each cell centre) — the same "light up the map" density the official
  project uses. (Groups without their own density layer fall back to all-biodiversity density.)
- **Formula:** `discover = normalize( 1 / (density + 0.001) )`, min–max scaled to 0–1.
  So lower density → higher discover.
- **Status:** REAL. Note: ~53% of cells have zero research-grade records (true gaps) and
  correctly score highest.

### 2. Find species at risk — `conservation`
- **What it measures:** how many of Canada's at-risk species occur in a cell — "Canada's Most Wanted."
- **Source:** **CAN-SAR** (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY) →
  521 species listed **Endangered / Threatened / Special Concern** × their **public GBIF**
  Canadian occurrences.
- **Formula:** per cell, sum the status weights of the at-risk species recorded there —
  **Endangered = 3, Threatened = 2, Special Concern = 1** — then min–max scale to 0–1.
- **Status:** REAL (authoritative status × real occurrences). **Caveat:** reflects *assessed*
  species only (CAN-SAR ~2021 snapshot; IUCN/COSEWIC under-assess invertebrates, plants, fungi).
  The same all-taxa layer is applied to every group (a cell rich in at-risk species is a
  priority regardless of what you record). Validated: top cells are Canada's real hotspots
  (Point Pelee / Carolinian SW Ontario, southern Vancouver Island / Garry Oak, Okanagan).

### 3. Cover every habitat — `env`
- **What it measures:** how under-sampled a cell's *climate type* is — climate "surprisal."
- **Source:** **CHELSA** bioclimate (3 bands: temperature, seasonality, precipitation) +
  the iNaturalist density (as the sampling weight).
- **Formula:** in 3-D climate space, estimate how densely *recorded* places resemble this
  cell's climate (a density-weighted Gaussian kernel, bandwidth `H`); `env = −log(that
  weighted climate density)`, then **percentile-ranked 0–1**. High = a climate/habitat type
  that is rarely recorded.
- **Status:** REAL.

### 4. Freshest gaps / Revisit the Past — `staleness`
- **What it measures:** cells well-recorded **historically** on iNaturalist but **quiet recently** — worth revisiting.
- **Source:** **iNaturalist open-data** research-grade observations (the public AWS dump),
  per cell: `n_all` (all-time) and `n_recent` (last 5 years), computed over the 0.25° grid.
- **Formula:** for cells with ≥ 20 historical records, `staleness = (1 − n_recent/n_all) ·
  log(1 + n_all)`, min–max scaled to 0–1. So lots of old records + few recent → high.
- **Status:** REAL, **iNaturalist-specific**. *Why iNat-only and not GBIF:* an earlier version
  used GBIF density, but GBIF blends iNaturalist + eBird + museum records — eBird's recent bird
  volume and museums' old specimens distort "where iNaturalist users have gone quiet." A
  cluster cross-validation against the raw iNat dump caught this; staleness was re-sourced to
  iNaturalist only. Top cells now correctly flag iNat-quiet areas (e.g. James Bay: 1,319 historical, 2 recent).

### 5. Sample before it's lost — `urgency`
- **What it measures:** recent habitat change — record before it's gone.
- **Source:** **Hansen Global Forest Change** forest-loss fraction (per-0.05° raster).
- **Formula:** `urgency = normalize( forest-loss fraction )`, min–max 0–1. High = recent
  forest loss (logging, fire, dieback).
- **Status:** REAL where the Canada loss raster is present.

---

## The composite score ("impact", 0–100)

1. You set a weight 0–1 for each of the five axes (or pick a challenge preset).
2. Per cell: `raw_impact = Σ weight_i × axis_i`.
3. The map colour and the **N/100** in popups are the **percentile rank** of `raw_impact`
   across all cells — not a min–max — so a few extreme Arctic super-gaps don't crush every
   reachable cell to ~0. Darker = higher rank.

Challenge presets are just preset weight mixes, e.g. *Canada's Most Wanted* = conservation 1.0
(+ minor discover/urgency); *Revisit the Past* = staleness 1.0. Each links to its iNaturalist project.

---

## Validation — does priority actually predict discovery?

**Tested, not asserted.** At equal effort, the cells this tool ranks highest discover **up to 3× more
new species** than the cells it ranks lowest — Spearman **ρ ≈ 0.47–0.69**, permutation **p < 0.001**,
and it holds out-of-sample in Eastern Canada. The per-taxon numbers are in the table below; every one
reads straight from a committed result file.

The premise — *a record in an under-sampled cell adds more than one where people already crowd* —
is tested, not asserted, on a **leakage-free temporal split** of the BC 2025 pilot (iNaturalist
project 228908): score each cell from observations **up to** a cutoff T, then measure
**new-to-cell species recorded after T**, rarefied to **equal effort** (K = 5 observations per
cell) so busy cells get no free credit for sheer volume. Significance is a permutation null, and
the whole thing is re-run **out-of-sample on Eastern Canada** (disjoint from BC). Scripts:
`voi_backtest.py`, `backtest_appscore.py` (BC), `backtest_east.py` (East).

- **Under-sampling predicts discovery.** At equal effort, the train-only `discover` axis ranks
  cells by new-species yield at Spearman **ρ ≈ 0.47–0.69** across five taxa (amphibians, birds,
  insects, mammals, reptiles), all permutation **p < 0.01**, and holds out-of-sample in the East.
- **Going where it's already busy anti-predicts it.** All-time observation density (the
  opportunistic "light up the map" signal, used as a negative control) lands at **ρ ≈ −0.47 to
  −0.69** — the opposite sign. This is the core "blitz the gap" result.
- **The leak-free composite validates** at **ρ ≈ 0.21–0.52** (all p ≤ 0.03): the blended impact
  score points the same way, once its `discover` axis is rebuilt from only what was known at T.
- **Read it per-effort, not by raw count.** Because more people visit busy cells, those cells
  still accumulate *more* new species in absolute terms — raw count anti-correlates with priority.
  The validated, decision-relevant claim is the one about *your* trip: **a given amount of effort
  discovers more in a gap cell.**

### The numbers, per taxon

| Taxon | Region | Cells (rarefied) | `discover` ρ | Composite ρ | Yield, top vs bottom |
|---|---|---:|---:|---:|---:|
| Amphibians | BC | 91 | 0.48 | 0.42 | 2.0× |
| Birds | BC | 126 | 0.61 | 0.24 | 1.8× |
| Insects | BC | 106 | 0.53 | 0.21 | 1.3× |
| Mammals | BC | 141 | 0.61 | 0.30 | 2.7× |
| Reptiles | BC | 59 | 0.65 | 0.52 | 2.5× |
| Birds | East | 178 | 0.58 | 0.37 | 1.4× |
| Insects | East | 136 | 0.47 | 0.30 | 1.1× |
| Mammals | East | 213 | 0.69 | 0.52 | 3.0× |

*How to read a row:* take **mammals in the East** — rank its 213 cells by the `discover` axis, send the
same five observations to each, and the top-ranked cells turn up **3× as many new species** as the
bottom-ranked ones. **ρ** is the rank correlation between priority and discovery (1.0 = perfect, 0 =
none); **Yield** is that effect in plain terms — new species found per equal effort, best cells over
worst. Every `discover` correlation clears permutation **p < 0.001**; the composite clears **p ≤ 0.03**
(weakest: BC insects, 0.029).

*Reproduce:* `python backtest_appscore.py` (BC) and `python backtest_east.py` (East) regenerate
`cluster_results/voi_appscore_results.json` and `…_east_results.json`; the table reads straight from
those two files.

*Scope (honest):* retrospective over *collected* iNaturalist observations — it inherits observer
bias (controlled via rate-per-observation and effort rarefaction, not eliminated); "new species"
means new to that cell's iNaturalist record, not new to science. Method grounded in Di Cecco et
al. 2021 (effort/observer-bias confound), Chao 1984 and Colwell & Coddington 1994 (richness
extrapolation).

---

## Trip planning

- **Travel time** per cell: mean of **Weiss et al. 2018** "accessibility" (minutes to the
  nearest city) over land sub-points in the cell.
- **Routing:** real Walk / Cycle / Drive routes from **OSRM** (FOSSGIS public server); when a
  route can't be fetched it falls back to a straight-line estimate (speeds: Walk 5, Cycle 14,
  Drive 60 km/h; ×1.35 road factor), and the trip is flagged as estimated.
- **Adaptive mode:** the default travel mode is the **greenest** (Walk > Cycle > Drive) that
  can actually reach a gap within your time budget — chosen from your start, not assumed.
- **CO₂:** driving ≈ 0.18 kg/km; cycling/walking zero.

## Which species to record in a cell (#48)

Tapping a cell suggests *what to record there*. The axes above rank **where** to go; this ranks
**which species** add the most once you are there. It is an **intermediate, interpretable metric** —
the intended end state is a model-based score (the lab's SDM predictions in the cell plus a
value-of-information score for how much an observation would improve those models), which is future
work, not v1.

Earlier the suggestions were sorted by a species' **global iNaturalist observation count** ("globally
rarest first"). That conflates *photographic popularity* with *recording value*: a species can have
few records worldwide simply because it is hard to photograph or unpopular, while a species that is
common globally can still be genuinely under-recorded in one place. So the list now ranks by recording
value computed from records actually around the cell:

- For each candidate species we pull its research-grade count **in the cell** (~14 km box) and **in the
  ~40 km neighbourhood** (one extra `species_counts` call, best-effort — if it fails the rank degrades
  to a within-cell order).
- Candidates are ordered **lexicographically**: (1) **new-to-cell** species — present in the
  neighbourhood but not yet recorded in this cell — first, because recording one adds a species the
  cell's record is missing; (2) then by **local coverage gap**, the local÷neighbourhood share ascending,
  so species under-recorded *here* relative to nearby rank above ones already well covered here;
  (3) tie-broken by **regional scarcity** (fewer neighbourhood records first), so each record is more
  informative. Species at risk and obscured taxa are excluded upstream (`threatened=false`,
  `taxon_geoprivacy=open`) per the dual-use guard below.

Caveats: counts are iNaturalist research-grade records, a sampling proxy, not a census; species tied on
regional scarcity keep iNaturalist's own order; and the metric measures recording-gap value, not
ecological importance — that awaits the SDM/VOI score.

---

## Honesty notes

- This is a **work-in-progress prototype, not an official Blitz the Gap tool** (so flagged in-app).
- The map is a **planning aid, not ground truth.** Obscure sensitive-species locations and
  respect Indigenous data sovereignty before any public use.
- **Dual-use guard (Pollock et al. 2025, *Nat Rev Biodiversity*, Box 3,
  [10.1038/s44358-025-00022-3](https://doi.org/10.1038/s44358-025-00022-3)).** That review warns that
  fine-grained prediction of where threatened species occur can inadvertently aid poaching or
  collection. The `conservation` axis is therefore exposed only as a **per-cell sum of status weights
  over the 0.25° (~25 km) grid**: it shows that a cell is rich in at-risk species, never which species
  or where within the cell. The underlying CAN-SAR x GBIF point occurrences are aggregated away in
  `join_conservation.py`; only the per-cell score reaches the public app. Coarse 25 km binning plus
  all-taxa pooling are the mitigation, and remain in force for any future finer-resolution layer.
- All five axes are now **real** (no placeholders) and were **cross-validated against the raw
  iNaturalist record dump at cluster scale**, which caught and fixed the staleness sourcing
  error noted above.

---

## Provenance (where each layer is built)

| Axis | Builder / source file | External source |
|------|----------------------|-----------------|
| discover, env, urgency, travel | `build_fullgrid_ca.py` | iNat density COG (Biodiversité Québec STAC), CHELSA, Hansen, Weiss 2018 |
| conservation | `build_atrisk_layer.py` + `join_conservation.py` | CAN-SAR (OSF) + GBIF occurrences |
| staleness | iNat open-data (cluster DuckDB) → `cluster_results/ca/ca_inat_metrics.csv` | iNaturalist open-data (AWS) |
| composite + display | `build_webapp.py` (`impact`, `recolour`) | — |
