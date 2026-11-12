# Complete checklists: light-up view and climate-band tally (amphibians, mammals, Canada)

## What was built

A map that starts empty and lights a cell once that cell's list for a group counts as complete, in the form of the sketch: a Canada outline, hollow squares per group (green amphibians, brown mammals), a "Complete checklists" legend with a checkbox per group, a "Latest checklists" panel, and switches for the rule and the cell size (5, 25 or 100 km). Page: `research/checklist_lightup.html`. Static figure: `research/results/checklist_lightup.png` (100 km, observer-days rule). Full tables: `research/results/checklist_lightup.md`. Records: the iNaturalist open-data export of 2026-09-27, research grade, species level, 31,644,832 records in the Canada box, the same input as the live layer. A sampling unit is one observer on one day in the cell (an observer-day).

## The two rules

- **Chao2 rule:** at least 10 observer-days and Chao2 completeness (species seen over species estimated) of at least 0.9. This is the live layer's "complete" status.
- **Observer-days rule:** at least 30 observer-days, the rule of thumb that a complete checklist takes about 30 observer-days. A cell lit this way gets a date, the day of its 30th observer-day, which feeds the "Latest checklists" panel.

The panel shows dates for the observer-days rule only. Chao2 completeness can fall as well as rise when new records add singletons, so a Chao2 cell has no single date it lit without rebuilding the estimate at every date.

## Why Chao2 alone is not labelled "complete"

A holdout test froze each cell's records at 2025-01-01 and counted the new species found from 2025-01-01 to 2026-09-27. For amphibians and mammals, plain observer-days ranked which cells would gain new species better than Chao2 completeness (AUC 0.70 vs 0.57 for amphibians and 0.75 vs 0.57 for mammals at 5 km; 0.74 vs 0.68 and 0.81 vs 0.58 at 25 km). Cells that Chao2 called complete gained 3.3 to 5.4 times more new species in their next 10 observer-days than the estimate predicted. So "complete" by Chao2 means "few unseen species implied by the current list", not "nothing left to find".

## How often the two rules agree

Assessed cells are Canadian cells with at least one record of the group.

| group | tier | assessed | lit Chao2 | lit 30 days | both | Chao2 only | 30 days only |
|---|---|---:|---:|---:|---:|---:|---:|
| Amphibians | 5 km | 18,483 | 1,587 (8.6%) | 939 (5.1%) | 631 | 956 | 308 |
| Amphibians | 25 km | 3,735 | 874 (23.4%) | 722 (19.3%) | 542 | 332 | 180 |
| Amphibians | 100 km | 603 | 294 (48.8%) | 267 (44.3%) | 235 | 59 | 32 |
| Mammals | 5 km | 28,929 | 866 (3.0%) | 1,879 (6.5%) | 412 | 454 | 1,467 |
| Mammals | 25 km | 6,057 | 346 (5.7%) | 1,147 (18.9%) | 231 | 115 | 916 |
| Mammals | 100 km | 1,002 | 101 (10.1%) | 405 (40.4%) | 77 | 24 | 328 |

For amphibians the rules light similar numbers of cells. For mammals the 30-day rule lights two to four times more cells than Chao2.

## Climate-band tally against 25 and 50 checklists

Climate is CHELSA V2.1, 1981-2010. Degree-days below 0 C are summed from the 12 monthly mean temperatures; climate moisture deficit is the sum of monthly shortfalls of precipitation below Penman evaporation (CHELSA's monthly climate moisture index). Each variable is cut into terciles over Canadian 5 km cells: degree-days below 0 at 1,924 and 3,578 (mild, cool, cold), moisture deficit at 91 and 162 mm (moist, medium, dry), giving nine strata. 25 checklists spread over nine strata is about 3 per stratum, 50 is about 6. "Strata covered" counts strata with at least 3 (or 6) lit cells; "usable" counts lit cells after capping each stratum at 3 (or 6).

| group | tier | rule | strata with >= 3 | usable toward 25 | strata with >= 6 | usable toward 50 |
|---|---|---|---:|---:|---:|---:|
| Amphibians | 5 km | Chao2 | 4 | 15 | 3 | 26 |
| Amphibians | 5 km | 30 days | 3 | 9 | 3 | 18 |
| Amphibians | 25 km | Chao2 | 6 | 18 | 6 | 36 |
| Amphibians | 25 km | 30 days | 3 | 10 | 3 | 19 |
| Amphibians | 100 km | Chao2 | 6 | 19 | 5 | 35 |
| Amphibians | 100 km | 30 days | 4 | 15 | 4 | 27 |
| Mammals | 5 km | Chao2 | 7 | 22 | 6 | 40 |
| Mammals | 5 km | 30 days | 6 | 21 | 6 | 39 |
| Mammals | 25 km | Chao2 | 9 | 27 | 7 | 50 |
| Mammals | 25 km | 30 days | 8 | 25 | 6 | 44 |
| Mammals | 100 km | Chao2 | 8 | 26 | 7 | 48 |
| Mammals | 100 km | 30 days | 9 | 27 | 7 | 51 |

Lit cells pile up in the mild third: of 939 amphibian cells lit by 30 days at 5 km, all 939 are mild. Mammals reach the 25-checklist design at 25 km and 100 km and come close to 50. Amphibians have at most one lit cell in any cold stratum under either rule, so a stratified amphibian design may need strata drawn within the amphibian records' range rather than over all of Canada.

The same agreement and tally tables for all eleven groups (including plants, birds, insects, fungi and "All biodiversity") are in `research/results/checklist_lightup.md`, and the page shows every group behind its checkbox.

## Limits

- Degree-days from monthly means miss frost days in months whose mean is above 0 C; the moisture deficit uses Penman evaporation, not the Hargreaves form ClimateNA uses. Both are approximations to the named variables.
- Climate per cell is the mean of CHELSA samples read every third pixel (about 3 km). Lit cells with no sample (coast, open water) are counted under "no climate": 37 mammal cells at 5 km under Chao2, 13 under 30 days, 1 at 100 km, none for amphibians.
- The 100 km tier pads the lattice's southern edge because 100 km does not divide it; cells there follow the same Canada mask rule as 25 km.
- Records are iNaturalist only, so a lit cell means well observed on iNaturalist, not surveyed.
- The 30-day rule's dates mark when effort reached 30 observer-days, not when the list stopped growing.

## Decision needed

Which rule lights a cell on the map and counts toward the 25 and 50: Chao2 completeness >= 0.9, at least 30 observer-days, or both together (the "both" column above). A second choice is the cell size the tally uses (5, 25 or 100 km), and whether the climate strata should be drawn over all of Canada or within each group's range.
