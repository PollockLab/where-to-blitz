# Where to Blitz the Gap — methodology

A terse reference for how every number on the map is computed. For the narrated
version with figures and the validation backtest, see the
[full walkthrough](https://pollocklab.github.io/where-to-blitz/where-to-blitz-walkthrough.html).
Each priority axis is scored **0–1 per cell**. A numeric preset fixes the weights, and the popup reports the blend's **0–100 national priority percentile**. This relative rank describes the frozen recording priorities, not the probability of finding a species or the benefit of a visit. The map colour and the live popup records use separate scales and inputs.

- **Grid:** an equal-area lattice (WGS84 Lambert Azimuthal Equal Area, centred 45°N 100°W).
  Every cell is a square of the same size. Two tiers: **25 km, 23,214 cells** (the default view)
  and **5 km, 536,164 cells** (from zoom 9). A cell is kept if it is inside the iNaturalist
  density footprint and either holds land (Weiss travel-time raster) or holds records.
- **Per group:** the same geometry is reused for all 11 life-group layers (All biodiversity,
  Plants, Insects, Birds, Fungi, Mammals, Fishes, Reptiles, Amphibians, Arachnids, Molluscs).
- **Shared normalization:** the raster-derived axes use national ramps fitted on the 25 km tier (`breaks.json`) and reused at 5 km; conservation and observed staleness join from their normalized 0.25° source grids. A finer cell can have different inputs and colour. The display then stretches colours to the visible cells, while the popup percentile remains a rank of the enclosing 25 km cell.
- **Three spatial supports:** the drawn cells are 25 km or 5 km equal-area squares. Conservation and observed staleness are source values on a 0.25° latitude-longitude grid, joined using the drawn cell's centre (`fullgrid_fields.PARENT_RES_DEG`); a 5 km display does not add conservation or historical evidence. Live iNaturalist queries use the selected drawn cell's geographic bounding box, which can include space outside its projected square.

---

## Glossary

| Term | Means |
|---|---|
| **cell** | One square of the grid. The unit everything is scored on: 25 km by default, 5 km from zoom 9. |
| **lattice** | The projected equal-area grid the cells sit on (`grid_lattice.py`). WGS84 Lambert Azimuthal Equal Area, 45°N 100°W, snapped to a 25 km multiple so the 25 km tier is an exact 5×5 aggregate of the 5 km tier. |
| **axis** | One underlying priority signal, scored 0–1 per cell. The five keys are `discover`, `conservation`, `env`, `staleness`, `urgency` (`goal_presets.py`). |
| **Goal menu** | Five display choices: the three numeric presets, Getting Even's group recommendation, and the separate Climate Gap overlay. These choices are distinct from the five axes. |
| **impact / priority percentile** | The preset's raw blend ranked from 0–100 among Canada-eligible 25 km cells for the selected species group, with equal values assigned their average rank. Panning and zooming preserve this number; at 5 km it belongs to the enclosing 25 km cell. A locally bright cell can have a low national percentile because the colour is stretched separately to the view. |
| **preset** | A named, fixed weight mix over the five axes (`goal_presets.py`). The three numeric presets share the current general Blitz the Gap iNaturalist project, resolved live with a frozen fallback slug. |
| **gap** | A cell that is under-recorded for the goal in play. "Blitz the gap" = go to one and record there. |
| **Getting Even** | A separate Goal-menu layer, not a numeric axis: it colours each cell by the taxonomic group most under-represented there, birds excluded (`build_gettingeven.py`). It answers what group to record in a cell. |
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
- **What it measures:** recorded status-weighted conservation concern under the shipped assessment snapshot and occurrence policy.
- **Source:** **CAN-SAR** (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY) → the species listed **Endangered / Threatened / Special Concern**, matched to **Catalogue of Life XR** species keys (release COL26.6 XR, the GBIF default for SQL downloads, which replaced the frozen GBIF Backbone; names go through the GBIF v2 species/match, synonyms and subspecies collapse to the accepted species key, orthographic duplicates of one species in COL are counted once, and unmatched or higher-rank-only names are listed in the crosswalk with no key and never used), × their Canadian occurrences from **one GBIF SQL download with a DOI** (see `cluster_results/ca/ca_atrisk_provenance.json`; the name-to-key crosswalk is `cluster_results/ca/atrisk_species_keys.csv`).
- **Shipped snapshot:** GBIF download [10.15468/dl.6hqdyd](https://doi.org/10.15468/dl.6hqdyd), using Catalogue of Life **COL26.6 XR**, taxonomy DOI [10.48580/dgyy9](https://doi.org/10.48580/dgyy9). The crosswalk has **521 listed names**, of which **507 names match 496 distinct primary species keys**; these are name and key counts, not 521 independently resolved biological species. Of **549 matched CAN-SAR listings**, the selected allowlist retains **514**, comprising **511 species-level listings and 3 polygon-guarded listings**, and excludes **35**. Several listings can share a species key; an eligible species contributes its maximum applicable status weight once per source cell. The provenance, crosswalk and listing-policy files provide the exact identifiers.
- **Occurrence filters:** Canadian records only, occurrence status present, year 1990 or later, no fossil or living (zoo, garden) specimens, no GBIF geospatial issues, coordinates present. There is deliberately no coordinate-uncertainty filter: iNaturalist publishes obscured at-risk records with large uncertainty, and a cutoff such as 10 km would drop many of them. The SQL download aggregates records at 0.01° for policy checks, then the builder aggregates retained species to 0.25° source cells; their physical width varies with latitude.
- **Selected occurrence policy:** CAN-SAR lists subspecies and populations, while GBIF species keys include their parent species. The registered comparison in `research/GATE-range-clip.md` selected the simpler allowlist: its fixed test listings for SPILLS species and *Silene scouleri* are excluded, while COVERS species plus badger, shrike and bison retain species-level counting. The download-specific decision excludes 35 listings and is enforced by `cluster_results/ca/atrisk_shipping_policy.json`; `--candidate` explicitly builds a research range clip instead. Ambiguous *Lampetra richardsoni* and *Peltigera gowardii* matches, including the eastern *Peltigera hydrothyria* listing sharing the same key, retain polygon checks with a 10 km buffer and no infraspecific bypass. A missing polygon drops the ambiguous listing and is logged. Available ranges use DFO distribution polygons, the five fixed ecozone overrides within CAN-SAR provinces, then Natural Earth province polygons. The selected policy applies polygons only to these ambiguous matches. Per-listing counts and exclusions are in `cluster_results/ca/atrisk_listing_clip.csv`; raw candidate counts and hand reviews are retained separately in `research/range_clip_candidate.csv` and `research/range_clip_reviews.csv`.
- **Crosswalk gate:** when a committed crosswalk exists, a re-match that merges names of different status weights under one species key, splits a key, or loses a previously matched name stops the build unless `--accept-crosswalk-changes` is passed; the diff is printed either way.
- **Check:** before the layer is written, the per-species totals of a seeded sample of 6 species are re-counted through the GBIF occurrence search API (COL XR `checklistKey` and species key) under the same filters; any mismatch (or an empty download) stops the build.
- **Formula:** per cell, sum the status weights of the at-risk species recorded there —
  **Endangered = 3, Threatened = 2, Special Concern = 1** — then min–max scale to 0–1.
- **Status:** REAL (assessment statuses × occurrence records). **Caveat:** CAN-SAR is an assessment snapshot, not a current legal-status lookup, and under-assessed invertebrates, plants and fungi remain underrepresented. The allowlist deliberately discards genuine records for excluded listings; retained species-level listings can still combine populations. The comparison covers resolved infraspecific records in its fixed test set and does not establish accuracy for every species-level occurrence or population. Provincial polygons remain coarse for the ambiguous matches. A cell with no at-risk score means "not recorded under this policy", not "absent". The same all-taxa layer is applied to every group.
- **Taxon-concept limit:** the shipped crosswalk matches the historical name *Bartramia stricta* to COL's literal species key. The [COSEWIC 2023 Rigid Apple Moss report](https://www.canada.ca/en/environment-climate-change/services/species-risk-public-registry/cosewic-assessments-status-reports/rigid-apple-moss-2023.html) uses *Bartramia aprica* for the revised Canadian concept. A name match and aggregate occurrence count do not establish that the shipped records represent that concept; this remains an unresolved biological identity ambiguity.

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
- **Formula and fallback:** where the 0.25° source CSV supplies a value, cells with ≥ 20 historical records use `staleness = (1 − n_recent/n_all) · log(1 + n_all)`, min–max scaled to 0–1. Lots of old records + few recent records give a high value. When the selected source parent has no CSV value, `fullgrid_fields.group_bands` substitutes `1 / (density + 0.001)`, scaled through the selected group's national `staleness_proxy` bounds. That fallback measures sparse recording, not a demonstrated decline in recent activity; the shipped staleness axis combines both kinds of evidence.
- **Scope:** observed history is iNaturalist-specific, matching the recording objective; GBIF's mix of iNaturalist, eBird and museum dates is not interchangeable with it. The inverse-density fallback does not establish recent silence. Neither component is a prospective estimate of what a revisit will find.

### 5. Sample before it's lost — `urgency`
- **What it measures:** cumulative mapped forest loss, a reason to document changed habitat.
- **Source:** **Hansen Global Forest Change** forest-loss fraction for 2001–2023 (per-0.05° raster).
- **Formula:** forest-loss fraction, scaled 0–1 against fixed national bounds (0 to 0.85).
  Cells above the top bound sit at 1. High = greater cumulative forest loss (logging, fire, dieback).
- **Status:** REAL where the Canada loss raster is present.

---

## The composite score ("impact", 0–100)

1. You pick one of the three numeric presets. Each fixes a weight 0–1 for each of the five axes. The weights are not user-editable.
2. Per cell: `raw_impact = Σ weight_i × axis_i`.
3. The **N/100** is the **percentile rank** of `raw_impact` among the selected group's **Canada-eligible 25 km cells**, using the same published country mask as map visibility and trip candidates. For a population of N cells, an equal-value block at zero-based sorted positions lo through hi receives `100 × (lo + hi) / (2 × (N − 1))`. The lowest and highest values reach 0 and 100 only when untied; an empty, singleton or constant population returns zero. If the Canada mask is unavailable, the app withholds national ranks. Panning and zooming preserve the rank, and a 5 km popup reports its enclosing 25 km cell's percentile.
4. The baked map colour uses `clip(colour_blend / preset.scale, 0, 1)` through viridis, where `colour_blend` combines the axes in the colour stack with the preset's weights, per (group, preset, tier) PNG in `build_grid_values.render_goal_rgba`. The fixed scales are **1.0 for Spatial Gap, 1.6 for Species discovery, and 1.0 for Conservation**. At runtime the fill is **restretched to the cells in view** using the ordered green-channel levels of those quantized colours. The legend identifies the current scale. Views with fewer than **24** valid cells or a green-channel span below **4** retain the baked national colour scale. This display stretch never changes the popup percentile.

The release's **25 km colour stacks are NaN-aware means of 5 km axes**, produced by `aggregate_25km_from_5km.py`. The popup components and national ranks instead use the **native 25 km webapp JSON** from the `fit-25km` build. Their different spatial aggregation can therefore make the 25 km fill differ from the native ranked blend even before colour quantisation and viewport stretching.

The three presets, straight from `goal_presets.PRESETS`: *Spatial Gap* = `discover` 1.0; *Species discovery* = `discover` 1.0 + `staleness` 0.6; *Conservation* = `conservation` 1.0 + `urgency` 0.4. `env` carries weight 0 in all three, but `national_breaks.discover` uses it to order zero-density cells, so the discovery presets retain an indirect climate contribution. Their join links resolve the current general Blitz the Gap iNaturalist project. The other two Goal-menu choices, Getting Even and Climate Gap, have separate legends and do not display these presets' national priority percentile.

---

## Historical discovery evidence

The Map display control exposes historical discovery evidence separately from the five recording goals requested in [#49](https://github.com/PollockLab/where-to-blitz/issues/49): Spatial Gap (the default), Species discovery, Conservation, Getting Even and Climate Gap. The **Species discovery** recording preset remains the documented `discover` plus `staleness` blend; it is not the historical rate. The initial rasterized-point calculation requested in [#98](https://github.com/PollockLab/where-to-blitz/issues/98) is implemented at both 5 km and 25 km. Interim recording formulas and later value-of-information work remain separate from this measured layer.

### Inputs and calculation

The numerator is the lab-supplied 2025 point set: 826 source features. `prepare_discovery_inputs.py` checks the supplied shapefile components, resolves their coordinates against distinct compatible research-grade rows in the frozen lab Canada Parquet export, and requires the declared 2025 observation year. It does not recover original observation IDs. The denominator counts research-grade observation rows from that same export and calendar year, using finite reported coordinates within the existing national LAEA lattice. Source-obscured reported coordinates are retained; private coordinates are not recovered and source obscuration is not reversed. Both inputs declare the same annual period, filters and spatial support in checksum-bearing metadata.

For each supported cell, `rate = 10,000 × supplied_point_count / research_grade_observation_count`. At 25 km, counts are summed over the 25 child cells before division; the parent rate is a ratio of sums, not an average of child rates. These are observed counts and rates, without interpolation, smoothing, an invented minimum denominator or conversion to the recording presets' percentile scale.

A zero rate requires positive observation exposure and complete declared support: it means no point from the supplied list, not absence of undiscovered species. Zero exposure or incomplete support yields an unavailable rate, never zero. The published artifact has 285 positive and 5,712 zero-point cells at 25 km, and 458 positive and 44,054 zero-point cells at 5 km; another 105,413 declared fine cells have zero exposure and no rate. Neutral fills identify exposed zeros and blank areas identify unavailable rates. Positive colours use a logarithmic scale while popups retain the underlying counts and rate. A high ratio can arise from very few observations.

### Point inspection and limits

At the 5 km tier, the optional teal markers group only exact reported coordinates: 826 features occupy 737 positions, with up to 27 features at one position. The marker count is a feature count, and its popup retains every supplied scientific name and observation year alongside the cell's count/rate evidence. A paged keyboard-accessible location list exposes the same positions. The source's original discovery-selection rule is unknown, scientific names are supplied assertions, and a source-feature identity is a shapefile-bundle checksum plus feature index, not a verified iNaturalist observation ID. Annual matching does not establish a unique daily date or original observation link. Some reported positions are obscured. These data describe the supplied historical point set, not every new-to-iNaturalist taxon, exhaustive new species, ecological absence or expected discoveries on a future visit. Orange recent Conservation Challenge sightings use a different source and time window.

### Provenance and reproduction

The [published rate artifact](https://github.com/PollockLab/where-to-blitz/releases/download/grid-outputs-v1/discovery-rate-2025-d9b304b801ee58d2.json) has SHA256 `d9b304b801ee58d21e3006272342470e099f8430be2349c047308699b11ee107`. Its `sources` metadata records the original shapefile-component hashes, frozen Parquet hash, prepared point/count hashes and matching scope. The [approved point-inspection derivative](https://github.com/PollockLab/where-to-blitz/blob/97341625f22f4ba595a149079c9ed069cd3efca0/webapp/historical_discovery_points.json) has SHA256 `467e305252655293103449350f4988b33e175f9b4d093739c0a682322e7736e0`. Raw shapefile and Parquet inputs remain private; the published derivative contains only synthetic source-feature identity, supplied scientific name, year and reported position.

`build_discovery_layer.py` verifies source checksums, compatible scope, count integrity, exact lattice boundaries and aggregation before producing JSON and optional count/rate GeoTIFFs. `prepare_optional_layers.py` verifies the configured public artifact against `DISCOVERY_RATE_SHA256`, validates its contents and stages it before the viewer build. `historical_discovery_points.py` reconstructs the annual point-source bytes from the ordered derivative, checks their checksum and reconciles every numerator cell at both tiers. A missing or different-vintage point derivative does not activate markers; malformed matching data fail the build.

To validate and build the published layer without accessing private sources, run from the repository root:

```bash
wtb_discovery_dir="$(mktemp -d)"
gh release download grid-outputs-v1 --repo PollockLab/where-to-blitz --dir "$wtb_discovery_dir" --pattern discovery-rate-2025-d9b304b801ee58d2.json
printf '%s  %s\n' d9b304b801ee58d21e3006272342470e099f8430be2349c047308699b11ee107 "$wtb_discovery_dir/discovery-rate-2025-d9b304b801ee58d2.json" | shasum -a 256 -c -
.venv/bin/python -c 'import json, sys; from build_discovery_layer import validate_artifact; validate_artifact(json.load(open(sys.argv[1]))); print("Discovery artifact valid")' "$wtb_discovery_dir/discovery-rate-2025-d9b304b801ee58d2.json"
DISCOVERY_RATE_FILE="$wtb_discovery_dir/discovery-rate-2025-d9b304b801ee58d2.json" .venv/bin/python build_webapp.py
```

The build writes the generated root `index.html`; it is not a source file to commit. For automated deployment, configure `DISCOVERY_RATE_URL` and its exact `DISCOVERY_RATE_SHA256`; both deploy paths run `prepare_optional_layers.py --env-file "$GITHUB_ENV"` before building. Without configured discovery input, the optional historical layer is not advertised.

For authorized holders of the frozen private sources, the producer and standalone rasterizer use these exact CLI flags. The private producer additionally requires `ogr2ogr` and `pyarrow`. Replace the example paths with private input/output locations:

```bash
.venv/bin/python prepare_discovery_inputs.py --points /path/to/supplied-points.shp --observations /path/to/lab-canada.parquet --year 2025 --out-dir /path/to/private-prepared
.venv/bin/python build_discovery_layer.py --discoveries /path/to/private-prepared/discovery-events.geojson --observations /path/to/private-prepared/observation-counts.csv --discoveries-metadata /path/to/private-prepared/discovery-events.metadata.json --observations-metadata /path/to/private-prepared/observation-counts.metadata.json --out /path/to/private-prepared/discovery_grid.json --raster-dir /path/to/private-prepared/rasters
```

The producer writes `preparation_audit.json` with source-row matching, boundary accounting and checksums. Existing regression gates exercise preparation, rate aggregation, source-byte binding and viewer behavior:

```bash
.venv/bin/python -m pytest -q test_prepare_discovery_inputs.py test_discovery_layer.py test_historical_discovery_points.py test_optional_layer_viewer.py
```

---

## Validation, retrospective record novelty

The archived backtest compares priority scores with **new taxon IDs in sampled project records**, not prospective field discoveries. Across eight BC and Eastern Canada taxon-region samples, the training-period scarcity proxy has rho 0.48 to 0.71. Its expected record novelty at K = 5 observations is 1.2x to 3.2x at upper versus lower score-tercile cutoffs. The grid-score comparisons below retain their original build and record-input snapshots, identified by each result's `run.grid.source_sha256` and `run.input_sha256`. They have not been rerun for the latest rebuilt grid or the refreshed walkthrough figures and do not establish future trip yield.

### Grid and score parity

`backtest_appscore.py` assigns observations to the same declared **25 km WGS84 LAEA lattice** as the viewer, joins by projected row and column, excludes cells hidden by the published Canada eligibility mask, and blends the original normalized axes using `goal_presets.PRESETS`. It does not renormalize axes within a regional sample. The earlier 0.25-degree join could merge independent projected cells and the earlier sample rescaling could alter mixed-preset rankings; those results are superseded by the committed reruns below. The generic proxy analysis in `voi_backtest.py` retains its legacy default grid for compatibility.

The default Spatial Gap preset uses `discover` alone. The training-period alternative replaces that axis with scarcity derived from the sampled count of records dated before the split. Its legacy keys `app_leakfree` and `discover_leakfree` remain for compatibility, but they name **retrospective observed-date proxies with historical availability unknown**. They do not certify that records and IDs were available at the cutoff. The `opportunistic_density` score is the same sampled training-count ordering reversed, so its opposite correlation is not an independent negative control.

### Outcome and calculation

Records are split using `observed_on` at **2025-07-01**. Novelty means an iNaturalist **taxon_id** absent from that cell's sampled pre-split project records. The IDs include species, subspecies, variety, form and hybrid ranks; legacy fields containing `species` do not imply a biological-species crosswalk. Only cells with at least one pre-split record and at least K post-split records contribute. Previously unobserved cells are excluded.

For a cell with N post-split observations and n_j records of an unseen taxon j, expected novelty in K observations drawn without replacement is `sum_j [1 - C(N - n_j, K) / C(N, K)]`. The calculation is exact for this sampled finite population, following the individual-based rarefaction expectation in [Hurlbert (1971)](https://doi.org/10.2307/1934145). It removes the simulation noise of the former 200 draws. It does not remove sampling bias or uncertainty about a future visit. Equal **observation counts** are not equal visits, field hours or detectability.

Spearman correlations are compared with 2,000 unrestricted cell permutations. Probabilities use `(exceedances + 1) / (draws + 1)`, with minimum `1/2001`, following [Phipson and Smyth (2010)](https://doi.org/10.2202/1544-6115.1585). The correction prevents zero Monte Carlo probabilities. These are nominal exploratory probabilities: exchangeability is assumed, spatial dependence is not controlled by blocked permutations, and multiple comparisons are unadjusted.

### The numbers, per taxon

| Taxon | Region | Cells (K = 5) | Training proxy rho | Archived Spatial Gap rho | Proxy novelty, upper/lower |
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

### Spatial sensitivity

The archived [spatial sensitivity report](cluster_results/appscore_spatial_sensitivity.json) checks whether the paired M20 association change depends on one geographic cluster. It deletes each occupied projected LAEA block in turn, at all three widths of 50, 100 and 200 km and all four combinations of zero and half-width row/column offsets. Each deletion uses identical retained cells for both outcomes, preserves the original score definitions, and re-ranks the scores and outcomes for Spearman correlation. Every deletion is reported, with fewer than ten retained cells or constant scores/outcomes marked unavailable.

Across all reported deletions, the M20-minus-full-training scarcity correlation change remains negative in all six qualifying cohorts. The ranges below combine every predefined partition; they are descriptive sensitivity envelopes, not confidence intervals, calibrated probabilities or estimates of performance in an unobserved location.

| Region | Taxon | Paired cells | Scarcity correlation change, all deletions |
|---|---|---|---|
| BC | Birds | 48 | -0.698 to -0.556 |
| BC | Insects | 48 | -0.460 to -0.312 |
| BC | Mammals | 45 | -0.522 to -0.389 |
| East | Birds | 59 | -0.303 to -0.233 |
| East | Insects | 44 | -0.540 to -0.428 |
| East | Mammals | 63 | -0.417 to -0.217 |

BC amphibians and reptiles remain unavailable with five and eight paired cells. The result is specific to the scarcity proxy: East insects' published-map correlation change spans -0.188 to +0.019. This diagnostic does not remove spatial dependence, establish causation or validate future field yield. [Roberts et al.](https://doi.org/10.1111/ecog.02881) describe why spatial structure matters for predictive evaluation; the deletion analysis here is an influence check, not their blocked cross-validation procedure.

Regenerate the report from the same hash-verified raw archives. Derived per-cell CSVs are not a numerical replay boundary because serialization can alter exact rank ties:

```bash
.venv/bin/python verify_appscore_results.py --download --inputs-dir /tmp/wtb-record-benchmarks
.venv/bin/python backtest_spatial_sensitivity.py --input-dir /tmp/wtb-record-benchmarks --output /tmp/wtb-spatial-sensitivity.json
```

The report verifies all eight input, canonical observation-frame, inventory, source and paired-cohort hashes and original statistics before writing. It records the diagnostic's own source hash and runtime versions, and preserves any existing output if verification fails. The published primary results and weights remain unchanged.

### Reproduce

Replay all eight frozen benchmarks against the committed result JSONs without replacing them. The cached input CSVs are gitignored; the command checks their archived hashes and the original grid inputs before analysis:

```bash
.venv/bin/python verify_appscore_results.py --download
```

The verifier downloads the eight public `grid-inputs-v1` CSV assets into a private temporary directory and removes it afterwards. For existing archived assets, use `.venv/bin/python verify_appscore_results.py --inputs-dir PATH`; relative input paths resolve from the script's directory. Add `--download` to download missing files into that directory. A cached file with the wrong SHA-256 fails rather than being replaced, and a failed download checksum leaves no installed file. No observation rows or coordinates are printed.

Before any analysis, all eight cases must pass schema and fixed-configuration checks, source and input SHA-256 checks, and grid index, Canada mask, group-file and normalized-inventory checks, including cell counts. The replay uses the original analysis defaults, adds the archived region label in memory, and compares every scientific and provenance field, including missing or extra keys, with exact discrete values and 1e-12 absolute and relative float tolerance. Runtime package versions are reported separately; numerical agreement does not establish an identical environment. Mismatches identify the JSON path and return a nonzero exit status.

These are frozen retrospective sampled-record benchmarks on the committed 25 km inventory. They do not validate later rebuilt live Conservation data, the 5 km inventory or prospective trip yield. A fresh API pull can change source records or identifications even with fixed observation-date bounds, so it is not the archived input.

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

Tapping a cell suggests species from recorded observations. The default nearby-count method prioritizes recording gaps; its ordering has not been shown to maximize field success. A separate BC plant pilot tests seasonal and environmental ranking before any replacement is enabled.

Earlier the suggestions were sorted by a species' **global iNaturalist observation count** ("globally
rarest first"). That conflates *photographic popularity* with *recording value*: a species can have
few records worldwide simply because it is hard to photograph or unpopular, while a species that is
common globally can still be genuinely under-recorded in one place. So the list now ranks by recording
value computed from records actually around the cell:

- Each live query uses the **selected 25 km or 5 km cell's geographic bounding box** and a **40 km neighbourhood**. These are current iNaturalist research-grade records, separate from the frozen priority axes. The bounding box encloses the projected square rather than reproducing its exact outline. If the neighbourhood query fails, the list falls back to within-cell ordering.
- Candidates are ordered **lexicographically**: (1) species recorded nearby with **no matching filtered record in the cell** first; (2) local÷neighbourhood record share ascending; (3) fewer neighbourhood records as the tie-break. A complete cell list establishes the first condition, or, when the unfiltered result is capped at 500 taxa, a complete candidate-specific request must confirm it. A capped, failed or taxonomically ambiguous check cannot support a no-record claim. This is evidence about the filtered query, not biological absence or a promise of discovery. Species at risk and obscured taxa are excluded upstream (`threatened=false`, `taxon_geoprivacy=open`) per the dual-use guard below.

Caveats: counts are iNaturalist research-grade records, a sampling proxy, not a census; species tied on
regional scarcity keep iNaturalist's own order; and the metric measures recording-gap value, not
ecological importance — that awaits the SDM/VOI score.

The optional “Why these species?” disclosure explains why missing-local candidates can tie at the three-record minimum. On opening it, a bounded public-record query can show distinct observers, observation dates and the approximate distance from the cell centre to the nearest record. These are recording-support summaries, not independent surveys or encounter probabilities. Details require a complete response whose counts match the displayed regional totals; missing, changed or incomplete evidence leaves the suggestions unchanged. The optional query covers at most four species with at most 50 nearby records each, so it does not add a request to the initial popup load or paginate an unbounded record set.

The **BC plants pilot** uses CHELSA v2.1 BIO1/BIO4/BIO12 climate and [ESA WorldCover 2021 v200](https://doi.org/10.5281/zenodo.7254221) land-cover composition, sampled at 225 fixed equal-area locations per 25 km cell using categorical overviews. At least 80% of samples must be valid for each input. WorldCover attribution: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium, CC-BY-4.0. Environmental profiles count each occupied species-cell once, standardize from training cells only, and shrink diagonal variance halfway toward the background. Profiles require three occupied training cells. Seasonal indices use complete prior-year iNaturalist monthly species-count pages for the actual BC province, normalize against overall plant recording seasonality, and add 30 background-proportional pseudo-records. These are broad habitat and seasonal recording indices, without calibrated occurrence or encounter probabilities.

The combined order excludes environmental similarity below 0.2 and seasonal indices below 0.5. It sorts by `floor(environmental_similarity × min(seasonal_index, 1) × 5)`, then by the same unbinned similarity with a maximum 25% training-incidence rarity bonus, then taxon ID. An enabled pilot shows up to four verified missing-local candidates for the current month, without filling weak slots. It applies only to 25 km BC plant cells included in the model artifact. Complete 40 km candidate enumeration is bounded to twelve pages; incomplete local lists or unresolved infraspecific ancestry require candidate-specific verification. Expired, missing, malformed or failed-gate artifacts retain the labelled nearby-count method.

`evaluate_plant_pilot.py` compares rarest-first, common-nearby, a seasonal baseline and the combined model on identical pre-forecast candidate pools at July, August and September 2025 origins. Environmental fitting withholds each target's 100 km block and a 40 km buffer; the fixed prior-year provincial seasonal summaries include those regions. Outcomes are exact expected distinct suggested taxa in five following-month records, reported overall and for the lowest quartile of training-cell incidence. Repeated cell-months stay together in paired spatial resampling. The historical cache cannot establish API availability, identification, privacy or research-grade state at each forecast date, and recorded observations are not equal field effort. Results remain exploratory. The public artifact stays disabled unless both outcomes improve against every baseline with sufficient contributing blocks and historical feature provenance is verified. The present data cannot support the rare objective because its Q1 taxa fall below the environmental model's minimum cell support.

Reproduce the dated pilot with the original observation cache supplied explicitly; its checksum is recorded in the result. The acquired aggregate seasonal and habitat inputs are committed. Each command refuses existing outputs.

```bash
python evaluate_plant_pilot.py --observations /path/to/inat_bcall_Plantae.csv --habitat research/data/bc_plant_habitat.json --season research/data/bc_plant_season.json --boundary research/data/bc_boundary.geojson --output /tmp/bc-plant-evaluation.json --artifact /tmp/bc-plant-pilot.json
```

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
versus its 40 km neighbourhood (see "Which species to record in a cell" above). The colours come
from this offline layer; the ranking in the panel does not.

## Honesty notes

- This is a **work-in-progress prototype, not an official Blitz the Gap tool** (so flagged in-app).
- The map is a **planning aid, not ground truth.** Obscure sensitive-species locations and
  respect Indigenous data sovereignty before any public use.
- **Dual-use guard (Pollock et al. 2025, *Nat Rev Biodiversity*, Box 3, [10.1038/s44358-025-00022-3](https://doi.org/10.1038/s44358-025-00022-3)).** That review warns that fine-grained prediction of where threatened species occur can inadvertently aid poaching or collection. The `conservation` axis is exposed as an **all-taxa sum of status weights over a 0.25° source bin**, joined to the drawn cell by its centre. The GBIF SQL download aggregates to 0.01° bins for policy checks; `build_atrisk_layer.py` then aggregates retained species to 0.25° bins. Only the normalized score reaches the app. Drawing a 5 km square does not reveal a finer species-at-risk inventory, and coarse binning does not establish precise locations for obscured records.
- **Evidence is axis-specific.** The retrospective novelty analysis tests discovery-related recording priorities in sampled projects. The conservation build checks taxonomy, occurrence counts and its selected policy; staleness includes observed history and a sparse-recording fallback. These checks do not constitute independent validation of all five axes or prospective ecological benefit.

---

## Provenance (where each layer is built)

| Axis | Builder / source file | External source |
|------|----------------------|-----------------|
| discover, env, urgency, travel | `build_fullgrid_ca.py` | iNat density COG (Biodiversité Québec STAC), CHELSA, Hansen, Weiss 2018 |
| conservation | `build_atrisk_layer.py`, joined in `fullgrid_fields.py` | CAN-SAR (OSF) + GBIF SQL download on Catalogue of Life XR, release COL26.6 XR (DOI and taxonomy release in `ca_atrisk_provenance.json`) |
| staleness | iNat open-data (cluster DuckDB) → `cluster_results/ca/ca_inat_metrics.csv` | iNaturalist open-data (AWS) |
| Getting Even | `build_gettingeven.py` | per-group iNat density COGs, via the grid build; Eckert 2025 metric |
| composite + display | `goal_presets.py`, `build_grid_values.py`, `webapp/index.html` (`impact`, `recolour`), assembled by `build_webapp.py` | — |
