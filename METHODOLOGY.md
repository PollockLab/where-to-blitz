# Where to Blitz the Gap — methodology

A terse reference for how every number on the map is computed. For the narrated
version with figures and the validation backtest, see the
[full walkthrough](https://pollocklab.github.io/where-to-blitz/where-to-blitz-walkthrough.html).
Each priority axis is scored **0–1 per cell**, then your chosen weights are combined
into the **0–100 "impact"** reported at the foot of a cell's popup. The map itself carries colour, not
the number.

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
| **impact** | The 0–100 number at the foot of a cell's popup: the preset's blend of the five goals, expressed as a percentile rank across every cell in the country. It is national and stable: panning and zooming never change a cell's number. Because the fill *is* restretched to the view, a cell that reads bright locally can still report a low national number. |
| **preset** | A named weight mix over the five goals, each linked to a real Blitz the Gap iNaturalist sub-project (`goal_presets.py`). |
| **gap** | A cell that is under-recorded for the goal in play. "Blitz the gap" = go to one and record there. |
| **Getting Even** | A separate layer, not a goal: it colours each cell by the taxonomic group most under-represented *there*, birds excluded (`build_gettingeven.py`). It answers *what* to record in a cell, not *where* to go. |
| **VOI** | Value of information — how much a new observation would improve what we know. The intended end state for species suggestions; today's ranking is an interpretable stand-in (see "Which species to record in a cell" below). |
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
- **Source:** **CAN-SAR** (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY) → the species listed **Endangered / Threatened / Special Concern**, matched to **Catalogue of Life XR** species keys (release COL26.6 XR, the GBIF default for SQL downloads, which replaced the frozen GBIF Backbone; names go through the GBIF v2 species/match, synonyms and subspecies collapse to the accepted species key, orthographic duplicates of one species in COL are counted once, and unmatched or higher-rank-only names are listed in the crosswalk with no key and never used), × their Canadian occurrences from **one GBIF SQL download with a DOI** (see `cluster_results/ca/ca_atrisk_provenance.json`; the name-to-key crosswalk is `cluster_results/ca/atrisk_species_keys.csv`).
- **Occurrence filters:** Canadian records only, occurrence status present, year 1990 or later, no fossil or living (zoo, garden) specimens, no GBIF geospatial issues, coordinates present. There is deliberately no coordinate-uncertainty filter: iNaturalist publishes obscured at-risk records with large uncertainty, and a cutoff such as 10 km would drop many of them, while cells are 0.25° (~25 km) anyway.
- **Selected occurrence policy:** CAN-SAR lists subspecies and populations, while GBIF species keys include their parent species. The registered comparison in `research/GATE-range-clip.md` selected the simpler allowlist: its fixed test listings for SPILLS species and *Silene scouleri* are excluded, while COVERS species plus badger, shrike and bison retain species-level counting. The download-specific decision excludes 35 listings and is enforced by `cluster_results/ca/atrisk_shipping_policy.json`; `--candidate` explicitly builds a research range clip instead. Ambiguous *Lampetra richardsoni* and *Peltigera gowardii* matches, including the eastern *Peltigera hydrothyria* listing sharing the same key, retain polygon checks with a 10 km buffer and no infraspecific bypass. A missing polygon drops the ambiguous listing and is logged. Available ranges use DFO distribution polygons, the five fixed ecozone overrides within CAN-SAR provinces, then Natural Earth province polygons. The selected policy applies polygons only to these ambiguous matches. Per-listing counts and exclusions are in `cluster_results/ca/atrisk_listing_clip.csv`; raw candidate counts and hand reviews are retained separately in `research/range_clip_candidate.csv` and `research/range_clip_reviews.csv`.
- **Crosswalk gate:** when a committed crosswalk exists, a re-match that merges names of different status weights under one species key, splits a key, or loses a previously matched name stops the build unless `--accept-crosswalk-changes` is passed; the diff is printed either way.
- **Check:** before the layer is written, the per-species totals of a seeded sample of 6 species are re-counted through the GBIF occurrence search API (COL XR `checklistKey` and species key) under the same filters; any mismatch (or an empty download) stops the build.
- **Formula:** per cell, sum the status weights of the at-risk species recorded there —
  **Endangered = 3, Threatened = 2, Special Concern = 1** — then min–max scale to 0–1.
- **Status:** REAL (assessment statuses × occurrence records). **Caveat:** CAN-SAR is an assessment snapshot, not a current legal-status lookup, and under-assessed invertebrates, plants and fungi remain underrepresented. The allowlist deliberately discards genuine records for excluded listings; retained species-level listings can still combine populations. The comparison covers resolved infraspecific records in its fixed test set and does not establish accuracy for every species-level occurrence or population. Provincial polygons remain coarse for the ambiguous matches. A cell with no at-risk score means "not recorded under this policy", not "absent". The same all-taxa layer is applied to every group.

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
- **What it measures:** cumulative mapped forest loss, a reason to document changed habitat.
- **Source:** **Hansen Global Forest Change** forest-loss fraction for 2001–2023 (per-0.05° raster).
- **Formula:** forest-loss fraction, scaled 0–1 against fixed national bounds (0 to 0.85).
  Cells above the top bound sit at 1. High = greater cumulative forest loss (logging, fire, dieback).
- **Status:** REAL where the Canada loss raster is present.

---

## The composite score ("impact", 0–100)

1. You pick a preset. Each one fixes a weight 0–1 for each of the five axes. The weights are not
   user-editable.
2. Per cell: `raw_impact = Σ weight_i × axis_i`.
3. The **N/100** is the **percentile rank** of `raw_impact` across all cells nationally, not a
   min–max, so a few extreme Arctic super-gaps don't crush every reachable cell to ~0. It is computed
   once over the whole country, so a cell keeps its number as you pan and zoom. It sits in a muted
   footer line of the cell popup, below the under-sampled groups and the species worth recording,
   because in a well-recorded cell it legitimately reads 0 and that is a fact about the national
   ranking rather than a verdict on the outing.
4. The map colour starts from `raw_impact`, clipped to 0–1 and painted through viridis, baked per
   (group, goal) into a PNG at build time. At runtime the fill is then **restretched to the cells in
   view** (a quantile stretch, so a quiet region stops reading as flat purple). The legend says which
   scale is live, and the stretch falls back to the baked national ramp when the view holds fewer than
   24 cells or too little spread to rank. Score and colour are deliberately decoupled: the stretch
   never touches the number.

The three presets, straight from `goal_presets.PRESETS`: *Spatial Gap* = `discover` 1.0;
*Species discovery* ("Revisiting the Past") = `discover` 1.0 + `staleness` 0.6;
*Conservation* ("Canada's Most Wanted") = `conservation` 1.0 + `urgency` 0.4. `env` carries weight 0 in
all three, so no preset reads it. Each links to its iNaturalist project.

---

## Validation, retrospective record novelty

The backtest compares priority scores with **new taxon IDs in sampled project records**, not prospective field discoveries. Across eight BC and Eastern Canada taxon-region samples, the training-period scarcity proxy has rho 0.48 to 0.71. Its expected record novelty at K = 5 observations is 1.2x to 3.2x at upper versus lower score-tercile cutoffs. The published map score is evaluated separately; this proxy result does not establish that following the map improves a future trip.

### Grid and score parity

`backtest_appscore.py` assigns observations to the same declared **25 km WGS84 LAEA lattice** as the viewer, joins by projected row and column, excludes cells hidden by the published Canada eligibility mask, and blends the original normalized axes using `goal_presets.PRESETS`. It does not renormalize axes within a regional sample. The earlier 0.25-degree join could merge independent projected cells and the earlier sample rescaling could alter mixed-preset rankings; those results are superseded by the committed reruns below. The generic proxy analysis in `voi_backtest.py` retains its legacy default grid for compatibility.

The default Spatial Gap preset uses `discover` alone. The training-period alternative replaces that axis with scarcity derived from the sampled count of records dated before the split. Its legacy keys `app_leakfree` and `discover_leakfree` remain for compatibility, but they name **retrospective observed-date proxies with historical availability unknown**. They do not certify that records and IDs were available at the cutoff. The `opportunistic_density` score is the same sampled training-count ordering reversed, so its opposite correlation is not an independent negative control.

### Outcome and calculation

Records are split using `observed_on` at **2025-07-01**. Novelty means an iNaturalist **taxon_id** absent from that cell's sampled pre-split project records. The IDs include species, subspecies, variety, form and hybrid ranks; legacy fields containing `species` do not imply a biological-species crosswalk. Only cells with at least one pre-split record and at least K post-split records contribute. Previously unobserved cells are excluded.

For a cell with N post-split observations and n_j records of an unseen taxon j, expected novelty in K observations drawn without replacement is `sum_j [1 - C(N - n_j, K) / C(N, K)]`. The calculation is exact for this sampled finite population, following the individual-based rarefaction expectation in [Hurlbert (1971)](https://doi.org/10.2307/1934145). It removes the simulation noise of the former 200 draws. It does not remove sampling bias or uncertainty about a future visit. Equal **observation counts** are not equal visits, field hours or detectability.

Spearman correlations are compared with 2,000 unrestricted cell permutations. Probabilities use `(exceedances + 1) / (draws + 1)`, with minimum `1/2001`, following [Phipson and Smyth (2010)](https://doi.org/10.2202/1544-6115.1585). The correction prevents zero Monte Carlo probabilities. These are nominal exploratory probabilities: exchangeability is assumed, spatial dependence is not controlled by blocked permutations, and multiple comparisons are unadjusted.

### The numbers, per taxon

| Taxon | Region | Cells (K = 5) | Training proxy rho | Published Spatial Gap rho | Proxy novelty, upper/lower |
|---|---|---:|---:|---:|---:|
| Amphibians | BC | 108 | 0.63 | 0.31 | 2.7x |
| Birds | BC | 144 | 0.61 | 0.38 | 1.6x |
| Insects | BC | 123 | 0.57 | 0.39 | 1.5x |
| Mammals | BC | 173 | 0.65 | 0.50 | 2.7x |
| Reptiles | BC | 73 | 0.59 | 0.46 | 2.1x |
| Birds | East | 236 | 0.54 | 0.25 | 1.4x |
| Insects | East | 141 | 0.48 | 0.22 | 1.2x |
| Mammals | East | 228 | 0.71 | 0.52 | 3.2x |

**How to read the table:** rho is the rank correlation between the named score and expected novel taxon IDs among five later sampled observations. The last column divides mean expected novelty above the upper score tercile cutoff by the mean below the lower cutoff, including ties. It is the training proxy's sampled-record ratio, not a forecast of a visitor's yield. `n.s.` denotes a nominal permutation probability above 0.05. `test_methodology_table.py` checks the rows against the committed result files.

### Scope of inference

The CSVs are page-capped project samples pulled after the pilot. They do not reconstruct historical upload times, identifications or research-grade status, and the later published inventory does not reconstruct the historical footprint. The outcome is novelty relative to that sampled project history, not the complete local iNaturalist inventory or new-to-science species. Regional repetition is an additional retrospective check, not an independent prospective trial.

Published discover and staleness axes use all-time observations. The environmental axis density-weights CHELSA climate with all-time observations, conservation uses a later GBIF occurrence download, and a pre-split urgency source vintage is not established. These scores are therefore exploratory diagnostics. A record-novelty test also does not validate conservation status or habitat-change objectives. The sampling-history sensitivity below distinguishes finding gaps in existing records from ecological discovery skill beyond the sampled history.

Observer behaviour can alter spatial, temporal and taxonomic coverage, as documented by [Di Cecco et al. (2021)](https://doi.org/10.1093/biosci/biab093). Rarefying later observations fixes their count, not these biases. Prospective validation would require frozen inputs available before an outing, measured survey effort, and outcomes appropriate to each goal.

### Sampling-history sensitivity

A second outcome holds the sampled training history at **M = 20 records**, as well as the later sample at **K = 5**. For each test taxon, it multiplies the probability of absence from an independent M-record training draw by the probability of presence in the K-record test draw, then sums those contributions. This is a derived hypergeometric expectation for a different sampled-history estimand.

Both full-history and standardized-history outcomes are compared on the **same cells** with at least M training and K test records. No association is reported below ten qualifying cells. The paired cohort size and coordinate-key hash, outcome means, correlations, nominal probabilities and inference limits are recorded in each result.

| Taxon | Region | Paired cells | Scarcity rho, full history | Scarcity rho, M = 20 |
|---|---|---:|---:|---:|
| Amphibians | BC | 5 | unavailable | unavailable |
| Birds | BC | 48 | 0.62 | -0.02 |
| Insects | BC | 48 | 0.46 | 0.05 |
| Mammals | BC | 45 | 0.66 | 0.22 |
| Reptiles | BC | 8 | unavailable | unavailable |
| Birds | East | 59 | 0.37 | 0.09 |
| Insects | East | 44 | 0.38 | -0.13 |
| Mammals | East | 63 | 0.45 | 0.10 |

In these six qualifying cohorts, scarcity's full-history association weakens substantially after standardizing the sampled training count. The main result therefore cannot establish ecological discovery skill beyond differing sampled histories. Finding a gap in existing records remains a distinct recording objective. This sensitivity does not remove observer, seasonal, habitat or identification biases, and it leaves the published map weights unchanged.

### Reproduce

The cached input CSVs are gitignored. Download the archived files rather than treating a fresh API pull as the same historical dataset:

```bash
gh release download grid-inputs-v1 --repo PollockLab/where-to-blitz --dir cluster_results --pattern "inat_*.csv" --clobber
python backtest_appscore.py
python backtest_east.py
```

The two scripts regenerate `cluster_results/voi_appscore_results.json` and `cluster_results/voi_appscore_east_results.json`. Each run records its grid, Canada mask, normalized inventory and source hashes, input and observation-frame hashes, split, seeds, permutation count, outcome definition, inference limits and package versions. A later API pull may change the source records or identifications even with fixed observation-date bounds.

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

## Which species to record in a cell

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
Invertebrates (666). That is 18% of the coloured map decided by a tie-break.

The first version of this section read that tie-break as harmless. Of the 9,641 scored cells with
at least three scored neighbours, 54.9% carry a different priority group from their neighbour
majority, and that share does not rise as records fall (54.4% at one record, 60.8% at 11-30, 45.6%
at 301 and up); against a permutation null that keeps the national mix of groups and destroys the
spatial arrangement, 72.1% ± 0.5% over 50 draws, every band beat chance and the one-record band beat
it by the widest margin of all, 18.8 points. Three sharper tests say that margin was an artefact of
the null and of the comparison, not evidence that thin cells carry signal.

*The null was too generous.* It hands a one-record cell any of the six groups, but a one-record cell
can only answer the group its single record belongs to, and in practice that is Plants or
Invertebrates. Shuffling the labels inside each record band instead, so the null carries the band's
own mix, the one-record band's margin falls from 18.8 points to 4.0 and the two-record band's to 2.3,
while the well-sampled bands keep theirs (15.7 points at 31-100 records, 21.3 at 301 and up).

*The neighbours were thin too.* Judged against the majority of the neighbours holding 100 records or
more, the only vote worth trusting, 4,313 cells qualify and 53.5% differ. Split by the cell's own
records, the one-record cells differ on 73.1% (of 52) and the two-record cells on 84.4% (of 32),
against 43.3% for cells holding 301 or more.

*A thin cell mostly does not recover its own answer.* Take the 2,339 cells holding 300 records or
more, thin each to n records drawn from its own record set, and rescore. At one record the thinned
cell lands on the group the full record set chose 35.8% ± 0.5% of the time, against 28.0% for
guessing Plants every time. Five records reach 41.1% and twenty reach 51.4%.

So a one-record cell's colour is close to a coin weighted by the national mix, and the floor is a
data problem as well as a legend problem. Pooling a cell with its neighbours would restore what a census district did by its
size, but it is a different estimator from the published one and it rewrites well-sampled cells
too: on the 3,486 cells holding 100 records or more, a 3x3 pooled priority matches the unpooled one
on 58.8%, a 5x5 on 51.0%.

The floor bites hardest at the tier the map shows when zoomed in. At 5 km, 26.1% of the 96,275 scored cells hold one record and 59.0% hold fewer than ten, against 17.6% and 39.8% at 25 km; the median scored cell holds 5 records at 5 km and 22 at 25 km. The neighbour statistic reads the same way there, 52.1% of the 80,459
comparable 5 km cells differing from their majority against a 65.8% national-mix null, with the
one-record cells lowest of any band at 46.9%, and it deserves the same discount: the band-restricted
null has only been run at 25 km, where it removed most of that band's margin. `probe_gettingeven.py` produces all of these numbers,
recomputing the 25 km figures from the rasters as a check on the JSON path.

**The two tiers do not have to agree, and mostly they do not (#122).** Each tier standardises and
gates over its own cell population, so a 5 km cell's z score is its group's share relative to the
other 5 km cells, not to the 25 km cell it sits in. Of the 96,275 scored 5 km cells whose 25 km
parent is also scored, 60.0% name a different group. That is above the 29.4% agreement the 5 km mix
of groups would give by chance, but not far above it, and it is not an artefact of thin cells or of
the gate: the best-sampled 5 km cells, those holding 301 records or more, still differ from their
parent 45.8% of the time, and recomputing both tiers with the richness gate off moves the figure only
to 59.5% (the gate changes the group of 14.1% of cells, and it changes the same share at both tiers). Zooming past `TIER_SWITCH_Z` is therefore a change of question, not a sharpening of the
same one: it asks which group is under-recorded here relative to other 5 km cells. `probe_gettingeven.py`
prints the per-band table.

**The richness gate, on since 2026-09-01.** Eckert also drops a group from the running where its
modelled richness sits in the national bottom quartile — no point sending herpers where there are few
herps. Ryan supplied the three rasters on #59: `ar.richness.tif` and `mammal.richness.tif` are 1 km
Lambert conformal conic, band `sum`; `plant.richness.stacks.tif` is 5 km Lambert azimuthal equal
area, and the builder reads its first band, `modelled.richness`. `reproject_mean` warps each onto the
lattice from its own CRS, so nothing has to be pre-aligned.

The quartile is taken the way the R script takes it. A cell the richness raster does not reach
becomes 0 before the quantile rather than being left out of it (`richness[is.na(richness) &
!is.na(base.5k)] <- 0`), so those cells count in the denominator as well as falling below the limit.
That matters: 966 of the 10,679 scored cells sit outside the herp and mammal rasters, and excluding
them from the quantile lifts the limit enough to drop 3,394 cells instead of the R script's 2,670.

At 25 km the gate drops 2,670 cells per group, a quarter of the scored cells by construction, at
limits of 0.766 herp species, 15.5 mammal species and 91.6 modelled plant species. It moves 14.1% of
the scored cells to a different group:

| | Fishes | Fungi | Herps | Inverts | Mammals | Plants |
|---|---|---|---|---|---|---|
| gate off | 82 | 1,519 | 199 | 3,390 | 1,955 | 3,534 |
| gate on | 208 | 1,970 | 207 | 4,218 | 1,775 | 2,301 |

**The gate cannot be rebuilt in CI yet.** The rasters are on the lab SharePoint, not on the public
`bq-io/blitz-the-gap` bucket the `Rebuild grid` workflow reads, so a CI rebuild sees no richness and
produces a valid-looking ungated map. `_refuse_silent_ungating` in `build_gettingeven.py` turns that
silent revert into a failed build: an ungated run refuses to overwrite a shipped layer whose
`richness_gate` is non-empty, unless `GE_ALLOW_UNGATED=1` says to. Hosting the rasters is tracked
separately; until that lands, a rebuild of this layer needs the three env vars set by hand.

Turning the gate on skips `test_the_shipped_layer_is_the_published_metric` and
`test_methodology_quotes_the_real_thin_cell_counts`, because neither can re-derive a gated layer
without the rasters. Both gates on this layer are therefore off in a gated build. That is the cost of
shipping the gate before the rasters are hosted, and it goes away with them.

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
  or where within the cell. The underlying CAN-SAR x GBIF occurrences are aggregated to 0.25° cells inside the GBIF SQL query run by
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
| conservation | `build_atrisk_layer.py`, joined in `fullgrid_fields.py` | CAN-SAR (OSF) + GBIF SQL download on Catalogue of Life XR, release COL26.6 XR (DOI and taxonomy release in `ca_atrisk_provenance.json`) |
| staleness | iNat open-data (cluster DuckDB) → `cluster_results/ca/ca_inat_metrics.csv` | iNaturalist open-data (AWS) |
| Getting Even | `build_gettingeven.py` | per-group iNat density COGs, via the grid build; Eckert 2025 metric |
| composite + display | `build_webapp.py` (`impact`, `recolour`) | — |
