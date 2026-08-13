# research/

Experiments that shaped the app but do not run in it. The findings come
first; the file inventory and how to run them are at the bottom.

## Findings

**Embedding novelty versus geography depends on how species-rich the taxon is.**
The original Amphibia run said geography wins: discovery-AUC of random 22.1,
embedding novelty 23.2, spatial coverage 23.3, combined 24.1. That result
reproduced on separate hardware (Mila V100 and DRAC Fir H100) and is what
`run_fir.sh`'s header comment still claims. The 7-taxon sweep does not support
it as a general finding. Holding config fixed (1200 obs, budget 300, 200 seeds,
DINOv2):

| taxon | species | random | spatial | embedding | embed − spatial |
|---|---:|---:|---:|---:|---:|
| Amphibia | 36 | 22.1 | 23.3 | 23.2 | −0.2 |
| Reptilia | 53 | 27.7 | 32.8 | 32.6 | −0.2 |
| Mammalia | 79 | 38.4 | 45.4 | 48.7 | +3.4 |
| Arachnida | 160 | 50.7 | 58.2 | 52.1 | −6.1 |
| Aves | 246 | 80.6 | 85.0 | 91.1 | +6.1 |
| Fungi | 317 | 72.8 | 77.3 | 104.6 | +27.3 |
| Insecta | 392 | 97.5 | 96.7 | 103.2 | +6.5 |
| Plantae | 518 | 116.2 | 113.7 | 136.0 | +22.3 |

Across all 33 committed runs (4 backbones × 7 taxa, plus the online Amphibia
runs), embedding novelty beats spatial coverage in 22. It ties or loses only on
the species-poor taxa the original conclusion was drawn from, and Arachnida
breaks the richness trend. So the honest statement is that geography is enough
for vertebrate-scale richness and embeddings pull ahead on Fungi, Plantae, and
Insecta. This has not been re-tested since, and the shipped app scores cells
rather than images, which the sweep does not vindicate.

**Rarity is where weighting the objective changes the answer.** Under
observed-range rarity weights, the priority score's correlation with discovery
drops from ρ 0.49 to 0.14 on Amphibia and the top-priority cells hold *rarer*
species than the bottom ones (ρ −0.33 on rarity-versus-priority). Under IUCN
threat weights the same test flips positive (ρ 0.59, top-cell mean threat 0.94
against 0.47 at the bottom). The two rarity definitions disagree, and which one
the app should optimise is unsettled.

**Access shrinks the map more than it shrinks the yield.** Within 60 minutes of
travel on the Amphibia BC set, 18.4% of cells retain 29.5% of the new species;
within 120 minutes, 31.1% of cells retain 46.3%. A travel filter is cheaper than
it looks.

**The ID backlog is large; where it sits is unresolved.** Across five taxa, 78%
to 94% of needs-ID records had no identifier engagement at all, with median
waits of 148 to 327 days. That part is consistent. The spatial part is not:
backlog against the discover axis runs from ρ −0.23 (Amphibia, Reptilia) to
+0.08 (Insecta), and against observation density from −0.10 to +0.25. So the
backlog is real and worth a layer, but this run does not establish where it
concentrates. `exp_confusability.py` tested whether stuck records are simply
hard photos and got the opposite sign from the hypothesis on all five taxa:
coarser identifications correlate with *less* unengagement (ρ −0.11 to −0.38),
not more.

Numbers above are read from the committed JSONs. They are single-run results on
the legacy grid, most on one province or one taxon, and none carries a
multiple-comparison correction.

## Live options, not just history

Three of these are the only implementation of something the project has since
asked for again:

- `voi_iucn.py` is the only GBIF-to-IUCN threat-status lookup in the repo, with
  a cached `iucn_cache.json`. The shipped conservation axis uses a COSEWIC/SARA
  proxy instead. Issues #95 and #98 are this territory.
- `exp_idlatency.py` is the only per-cell identification-backlog measurement.
  That is the where-to-ID prioritisation the BC identifier team asked about.
- `exp_discovery_offline.py` is the embedding-versus-geography sweep. If the
  question returns with better embeddings, this is the harness.

## Why this directory exists

Nothing here is on the build path: no workflow, no test, and `rebuild_grid.sh`
never touches it. It is kept because every script below has committed results
elsewhere in the repo, and deleting the generators would leave that data with no
provenance.

Everything here works on the **legacy 0.25° lat/lon grid**, which predates the
5 km projected lattice the app ships. Note that the app has not fully left that
grid: `fullgrid_fields.py` still rounds cell centres onto the 0.25° parent to
inherit the conservation and staleness axes.

## What is not here

`voi_backtest.py`, `backtest_appscore.py`, and `backtest_east.py` stayed at the
repo root on purpose. METHODOLOGY.md quotes their output tables and gives
`python backtest_appscore.py` as the reproduce command, so they are documented
interface, not research. Three scripts here import them across the directory
boundary; `_rootpath.py` puts the repo root on `sys.path` so that works.

## Contents

| Script | Question | Committed output |
|---|---|---|
| `exp_discovery_acquisition.py` | Does embedding novelty beat spatial coverage at finding new species? | `cluster_results/{mila,fir}/exp_discovery_*.json` |
| `exp_discovery_offline.py` | Same, offline and swept across 7 taxa and 4 backbones | `cluster_results/generalization/<Taxon>/` |
| `exp_idlatency.py` | Where is the identification backlog, in cells? | `cluster_results/idlatency_results.json` |
| `pull_inat_idlatency.py` | Pulls the needs-ID records the above scores | `cluster_results/needsid_*.csv` |
| `exp_confusability.py` | Are stuck records stuck because the photo is hard? | `cluster_results/confusability_pilot.json` |
| `voi_conservation.py` | Does the priority score find *rare* species, not just many? | `cluster_results/voi_conservation_results.json`, `conservation_cells_*.csv` |
| `voi_iucn.py` | Same test against IUCN threat status instead of observed rarity | `cluster_results/voi_iucn_results.json` |
| `voi_accessibility.py` | How much discovery survives a travel-time budget? | `cluster_results/voi_accessibility_results.json` |
| `whereto.py` | Do the five objectives actually disagree about where to go? | `cluster_results/whereto_cells_*.csv`, `whereto_summary.json` |

## Running them

Run from the repo root, not from this directory, since output paths are relative
to it:

```bash
.venv/bin/python research/whereto.py
```

Several need inputs that are gitignored and must be fetched or re-pulled first:
`cluster_results/inat_*.csv` (from `pull_inat_backtest.py`) and
`cluster_results/bc_travel_time.tif` (Weiss travel-time raster, release asset).
`exp_discovery_acquisition.py` wants a GPU; `run_fir.sh` is the SLURM wrapper
that ran it on DRAC Fir, and it executes from a staged copy rather than from
this checkout.
