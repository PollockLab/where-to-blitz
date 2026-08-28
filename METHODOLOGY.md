# Where to Blitz the Gap — methodology

A terse reference for how every number on the map is computed. For the narrated
version with figures and the validation backtest, see the
[full walkthrough](https://pollocklab.github.io/where-to-blitz/where-to-blitz-walkthrough.html).
Each priority axis is scored **0–1 per cell**, then your chosen weights are combined
into the **0–100 "impact"** shown on the map and in popups.

- **Grid:** an equal-area lattice (WGS84 Lambert Azimuthal Equal Area, centred 45°N 100°W).
  Every cell is a square of the same size. Two tiers: **25 km, 23,214 cells** (the default view)
  and **5 km, 536,164 cells** (from zoom 9). A cell is kept if it is inside the iNaturalist
  density footprint and either holds land (Weiss travel-time raster) or holds records.
- **Per group:** the same geometry is reused for all 11 life-group layers (All biodiversity,
  Plants, Insects, Birds, Fungi, Mammals, Fishes, Reptiles, Amphibians, Arachnids, Molluscs).
- **Same colour at both zooms:** each axis maps raw values to 0–1 through one fixed national
  ramp, fitted once on the 25 km tier (`breaks.json`). A cell's score depends on its own data,
  never on which tier it was built in.

---

## Glossary

| Term | Means |
|---|---|
| **cell** | One square of the grid. The unit everything is scored on: 25 km by default, 5 km from zoom 9. |
| **lattice** | The projected equal-area grid the cells sit on (`grid_lattice.py`). WGS84 Lambert Azimuthal Equal Area, 45°N 100°W, snapped to a 25 km multiple so the 25 km tier is an exact 5×5 aggregate of the 5 km tier. |
| **goal** (= **axis**) | One reason to go somewhere, scored 0–1 per cell. The five keys are `discover`, `conservation`, `env`, `staleness`, `urgency` (`goal_presets.py`). |
| **impact** | The 0–100 number shown on the map and in popups: your weighted blend of the five goals, expressed as a percentile rank across the cells shown. |
| **preset** | A named weight mix over the five goals, each linked to a real Blitz the Gap iNaturalist sub-project (`goal_presets.py`). |
| **gap** | A cell that is under-recorded for the goal in play. "Blitz the gap" = go to one and record there. |
| **Getting Even** | A separate layer, not a goal: it colours each cell by the taxonomic group most under-represented *there*, birds excluded (`build_gettingeven.py`). It answers *what* to record in a cell, not *where* to go. |
| **VOI** | Value of information — how much a new observation would improve what we know. The intended end state for species suggestions; today's ranking is an interpretable stand-in (see #48 below). |
| **appscore** | The composite the app actually ships, read straight from `goal_presets.PRESETS`, as distinct from the generic proxy `voi_backtest.py` tests. `backtest_appscore.py` scores it so the backtest cannot drift from the dropdown. |

---

## The five priority axes

### 1. Discover the most species — `discover`
- **What it measures:** how under-sampled a cell is. High = few people have recorded there.
- **Source:** per-group **iNaturalist observation-density** raster (Biodiversité Québec STAC,
  1 km, averaged over the 1 km pixels inside each cell) — the same "light up the map" density the
  official project uses. (Groups without their own density layer fall back to all-biodiversity
  density.)
- **Formula:** records per km², mapped through the national ramp. Cells with records take the
  lower part of the scale, densest first; cells with no records take the top, ordered by `env`.
- **Status:** REAL. 54% of cells have zero research-grade records for All biodiversity (62–87%
  for the single groups); these are true gaps and score highest.

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
  weighted climate density)`, then mapped to 0–1 through the national ramp. High = a
  climate/habitat type that is rarely recorded.
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
- **Formula:** forest-loss fraction, scaled 0–1 against fixed national bounds (0 to 0.85).
  Cells above the top bound sit at 1. High = recent forest loss (logging, fire, dieback).
- **Status:** REAL where the Canada loss raster is present.

---

## The composite score ("impact", 0–100)

1. You set a weight 0–1 for each of the five axes (or pick a challenge preset).
2. Per cell: `raw_impact = Σ weight_i × axis_i`.
3. The **N/100** in popups is the **percentile rank** of `raw_impact` across all cells, not a
   min–max, so a few extreme Arctic super-gaps don't crush every reachable cell to ~0.
4. The map colour is `raw_impact` itself, clipped to 0–1 and painted through viridis. It is
   baked per (group, goal) into a PNG at build time, so panning and zooming never change it.

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
- **Chasing the crowds is the same finding, read backwards.** The `discover` axis is by construction
  an inverse of observation density, so all-time density (the opportunistic "light up the map" signal,
  used as a negative control) lands at the *exact mirror* value, **ρ ≈ −0.47 to −0.69**. It is not a
  second, independent test — it is the one result stated the other way: steering toward busy cells is
  precisely the wrong move. That sign flip is the "blitz the gap" result.
- **The leak-free composite validates** at **ρ ≈ 0.21–0.52** (all p ≤ 0.03): the blended impact
  score points the same way, once its `discover` axis is rebuilt from only what was known at T.
- **Read it per-effort, not by raw count.** Because more people visit busy cells, those cells
  still accumulate *more* new species in absolute terms — raw count anti-correlates with priority.
  The validated, decision-relevant claim is the one about *your* trip: **a given amount of effort
  discovers more in a gap cell.**

### The numbers, per taxon

| Taxon | Region | Cells (rarefied) | `discover` ρ | Composite ρ | Shipped ρ | Yield, top vs bottom |
|---|---|---:|---:|---:|---:|---:|
| Amphibians | BC | 91 | 0.48 | 0.42 | 0.09 n.s. | 2.0× |
| Birds | BC | 126 | 0.61 | 0.24 | 0.10 n.s. | 1.8× |
| Insects | BC | 106 | 0.53 | 0.21 | 0.02 n.s. | 1.3× |
| Mammals | BC | 141 | 0.61 | 0.30 | 0.26 | 2.7× |
| Reptiles | BC | 59 | 0.65 | 0.52 | 0.27 | 2.5× |
| Birds | East | 178 | 0.58 | 0.37 | 0.09 n.s. | 1.4× |
| Insects | East | 136 | 0.47 | 0.30 | −0.11 n.s. | 1.1× |
| Mammals | East | 213 | 0.69 | 0.52 | 0.28 | 3.0× |

*How to read a row:* take **mammals in the East** — rank its 213 cells by the `discover` axis, send the
same five observations to each, and the top-ranked cells turn up **3× as many new species** as the
bottom-ranked ones. **ρ** is the rank correlation between priority and discovery (1.0 = perfect, 0 =
none); **Yield** is that effect in plain terms — new species found per equal effort, best cells over
worst. Every `discover` correlation clears permutation **p < 0.001**; the composite clears **p ≤ 0.03**
(weakest: BC insects, 0.029). `n.s.` = not statistically significant (p > 0.05). The `discover`,
`Shipped`, and `Yield` columns are single-axis and preset-independent; the `Composite` column scores the
backtest's blend (`discover` 0.8 + `env` 0.7 + `urgency` 0.3), close to but not identical to the live
*Spatial Gap* default (`discover` 1.0 + `env` 0.5) — refreshing it to the current preset is part of
[issue #80](https://github.com/PollockLab/where-to-blitz/issues/80).

**What the live map shows today is weaker than what validates.** The `Shipped ρ` column scores the
all-time-density blend the map currently ranks by. It validates for **mammals** (ρ 0.26–0.28, p ≤ 0.002)
and marginally for BC reptiles, but for **birds, insects, and amphibians it is statistically
indistinguishable from random** (p > 0.2). The reason is mechanical: the shipped `discover` axis is
`1/(all-time density)`, so a just-sampled cell instantly looks "covered" and sheds priority — defensible
prospectively, but it means the *shown* score is not the one the backtest validates. The strong columns
above (`discover`, composite) rebuild that axis from only what was known before the cutoff T. Closing the
gap — anchoring the shipped axis to a fixed snapshot or window — is tracked in
[issue #80](https://github.com/PollockLab/where-to-blitz/issues/80).

*Reproduce:* both scripts read `cluster_results/inat_*.csv`, which is gitignored and so absent from a
clean clone; `python pull_inat_backtest.py` (BC) and `python pull_east.py` (East) fetch it first. Then
`python backtest_appscore.py` (BC) and `python backtest_east.py` (East) regenerate
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

## The Getting Even layer (#59)

A separate layer, not one of the five axes: it answers *what* to record in a cell, not *where* to
go. It is the calculation Lucas Eckert ran for the 2025 *Getting Even* challenge, moved from
census districts onto this app's equal-area cells (`build_gettingeven.py`):

1. **Proportion** — each group's share of the cell's iNaturalist records, over records of all taxa.
2. **z-score** — that share standardised against the same group's share across every other cell,
   one standardisation per group.
3. **Priority** — the cell takes the group with the lowest z: the group most under-recorded *here
   relative to how often it is recorded elsewhere*. The standardisation is the point; on raw shares
   almost every cell comes back fungi or fish.

Birds are excluded, as in the published 2025 map — eBird already covers them. Cells with no records
are grey rather than scored; `GE_MIN_RECORDS` raises that floor, at the cost of more grey. The
metric itself is re-derived independently and asserted cell-by-cell in `test_gettingeven.py`.

**What a thin cell's colour means.** At 25 km, 1,876 of the 10,679 scored cells hold exactly one
record. Five groups then sit at a share of zero and the lowest z comes from the national mean and
standard deviation rather than from the cell, so all 1,876 come back Plants (1,210) or
Invertebrates (666). That is 18% of the coloured map decided by a tie-break. It is not, however,
what makes the map look busy: of the 9,641 scored cells with at least three scored neighbours,
54.9% carry a different priority group from their neighbour majority, and that share does not rise
as records fall (54.4% at one record, 60.8% at 11-30, 45.6% at 301 and up). Against a permutation
null that keeps the national mix of groups and destroys the spatial arrangement, 72.1% ± 0.5% over
50 draws, every record-count band agrees with its neighbours more than chance, the one-record band
by 18.8 points. Pooling a cell with its neighbours would restore what a census district did by its
size, but it is a different estimator from the published one and it rewrites well-sampled cells
too: on the 3,486 cells holding 100 records or more, a 3x3 pooled priority matches the unpooled one
on 58.8%, a 5x5 on 51.0%.

The floor bites hardest at the tier the map shows when zoomed in. At 5 km, 26.1% of the 96,275 scored cells hold one record and 59.0% hold fewer than ten, against 17.6% and 39.8% at 25 km; the median scored cell holds 5 records at 5 km and 22 at 25 km. `probe_gettingeven.py` produces all of these numbers.

**Not yet transferred:** Eckert also drops a group from the running where its modelled richness sits
in the national bottom quartile — no point sending herpers where there are few herps. Those three
richness rasters (`ar.richness.tif`, `mammal.richness.tif`, `plant.richness.stacks.tif`) are in the
lab SharePoint, not in any public bucket, so the step is implemented but off: point
`GE_RICHNESS_HERP` / `GE_RICHNESS_MAMMAL` / `GE_RICHNESS_PLANT` at them and it runs. Until then the
shipped layer records `"richness_gate": []` and the build prints `NOT APPLIED`.

The tap panel is a *different* calculation — a live iNaturalist query about the species in the cell
versus its ~50 km neighbourhood (see "Which species to record in a cell" above). The colours come
from this offline layer; the ranking in the panel does not.

## Honesty notes

- This is a **work-in-progress prototype, not an official Blitz the Gap tool** (so flagged in-app).
- The map is a **planning aid, not ground truth.** Obscure sensitive-species locations and
  respect Indigenous data sovereignty before any public use.
- **Dual-use guard (Pollock et al. 2025, *Nat Rev Biodiversity*, Box 3,
  [10.1038/s44358-025-00022-3](https://doi.org/10.1038/s44358-025-00022-3)).** That review warns that
  fine-grained prediction of where threatened species occur can inadvertently aid poaching or
  collection. The `conservation` axis is therefore exposed only as a **per-cell sum of status weights
  over a 0.25° (~25 km) bin**: it shows that a cell is rich in at-risk species, never which species
  or where within the cell. The underlying CAN-SAR x GBIF point occurrences are aggregated away in
  `build_atrisk_layer.py`; only the per-cell score reaches the public app. Coarse 25 km binning plus
  all-taxa pooling are the mitigation, and remain in force for any future finer-resolution layer.
- All five axes are now **real** (no placeholders) and were **cross-validated against the raw
  iNaturalist record dump at cluster scale**, which caught and fixed the staleness sourcing
  error noted above.

---

## Provenance (where each layer is built)

| Axis | Builder / source file | External source |
|------|----------------------|-----------------|
| discover, env, urgency, travel | `build_fullgrid_ca.py` | iNat density COG (Biodiversité Québec STAC), CHELSA, Hansen, Weiss 2018 |
| conservation | `build_atrisk_layer.py`, joined in `fullgrid_fields.py` | CAN-SAR (OSF) + GBIF occurrences |
| staleness | iNat open-data (cluster DuckDB) → `cluster_results/ca/ca_inat_metrics.csv` | iNaturalist open-data (AWS) |
| Getting Even | `build_gettingeven.py` | per-group iNat density COGs, via the grid build; Eckert 2025 metric |
| composite + display | `build_webapp.py` (`impact`, `recolour`) | — |
