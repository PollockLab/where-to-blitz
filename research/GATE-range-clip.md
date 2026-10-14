# Gate: range-clipped at-risk layer

Pre-registered before the clipped layer is computed. The rules and pass criteria below are fixed; the results are reported against them as written, pass or fail.

## Problem

The live at-risk layer (`cluster_results/ca/ca_atrisk_richness.csv`, GBIF Backbone download) counts CAN-SAR listings per GBIF species key. Subspecies and population listings therefore take every record of the whole species: Ardea herodias fannini (Special Concern, coastal BC) scores every great blue heron cell in Canada, Falco peregrinus pealei every peregrine, Chrysemys picta bellii every painted turtle, and the caribou population listings every caribou record.

## Rule

1. A GBIF record counts for a listing only when its 0.01 deg cell centre lies inside the listing's range buffered by 10 km. Buffer: GBIF coordinates are rounded or generalised by a few km, records are binned to 0.01 deg (< 1.1 km), and the ranges are drawn at roughly 1:1M (DFO) or are 1:10M province edges, so a record on the true range edge can sit several km outside the drawn polygon. 10 km is under half a 0.25 deg layer cell (about 18-28 km wide in Canada).
2. A record whose GBIF accepted taxon is the listed infraspecific taxon (COL XR subspecies or variety key) also counts outside the range. This applies to whole-taxon listings only: a population listing does not take records of its subspecies from outside its range (caribou population listings would otherwise take R. t. caribou records everywhere).
3. A record accepted by several listings takes the max status weight among them. A species (with its alias keys) adds its best accepted record weight once per 0.25 deg cell, as before.
4. Listings with no range polygon keep species-level counting and are flagged (`range_source = none` in `atrisk_listings.csv`, listed in the provenance).

## Range sources

The ECCC "Range Map extents - Species at Risk - Canada" dataset (open.canada.ca d00f8e8c-40c4-435a-b790-980339ce3121) is withdrawn: CKAN returns an authorization error, GeoNetwork returns forbidden, and the datamart folder is gone. In its place, first available wins:

1. `dfo`: Fisheries and Oceans Canada Species at Risk Distribution (Range), open.canada.ca e0fabad5-9379-4077-87b9-5705f28c490b, `Distribution_SAR_EN_2026.gdb.zip` (sha256 857869c9...), joined to CAN-SAR by common name plus population.
2. `ecozone`: a hand-listed ecozone, intersected with the listing's CAN-SAR provinces, for listings that split within a province (`atrisk_range_overrides.csv`, each row citing the COSEWIC range): laingi, pealei, fannini and the painted turtle Pacific Coast population to Pacific Maritime (13); the painted turtle Intermountain-Rocky Mountain population to Montane Cordillera (14). AAFC Ecological Framework ecozones (sha256 ccfeaaca...).
3. `province`: the provinces and territories in the CAN-SAR `ranges` column (Natural Earth 10m admin-1 v5.1.2, sha256 22d0e3ad...).
4. `none`: no polygon (ocean-only ranges, or no province).

## Matching fixes in the same change

- Accepted species-rank VARIANT alternatives of a matched species with the same genus and authorship (orthographic duplicates) are alias keys of it, counted once per cell (Centaurium / Zeltnera muehlenbergii 5CZX8 + TVMH7). VARIANT alone is not enough: it also returns near-homonyms such as Ulus maritimus for Ursus maritimus.
- Lupinus densiflorus is matched as "Lupinus densiflorus Benth." (bare name is HIGHERRANK across homonyms).
- Lampetra richardsoni resolves to the river lamprey species key (KVYB5); it is clipped to the Morrison Creek DFO polygon. Peltigera gowardii resolves to P. hydrothyria (76F7Q); it is clipped to its CAN-SAR provinces. Either one without a polygon is dropped from the layer and logged.

## Pass criteria

Computed from a new GBIF SQL download with the live filters at 0.01 deg, grouped by species key and accepted taxon key.

- **C1 laingi.** Every 0.01 deg cell accepted for the laingi listing (121) lies inside Pacific Maritime within BC, buffered 10 km. Records identified to the subspecies count toward this check too: any accepted cell outside fails C1.
- **C2 fannini, pealei, bellii.** No 0.01 deg cell accepted for fannini (336), pealei (179), painted turtle Pacific Coast (500) or Intermountain-Rocky Mountain (501) lies outside that listing's range buffered 10 km. Subspecies-identified records count toward this check too.
- **C3 Long Point.** The live top cell, Long Point (gi 170, gj -322, centre 42.625, -80.375), ranks in the top 5 by score (rank = 1 + cells with a strictly higher score).
- **C4 flagship species.** Piping plover, spotted owl and greater sage-grouse each keep at least one listing with records kept > 0.
- **C5 losses.** Every listing that loses more than 90% of its species' records to the clip (records_kept / records_species < 0.1, records_species > 0) is hand-reviewed against its COSEWIC range and listed in the PR with the reason.

## Reported, not gated

Spearman rank correlation of cell scores vs the live layer (13,914 cells, top cell (170, -322) score 213), cells gained and lost, and the 20 listings with the largest record losses.

## Ship rule: range clip vs the allowlist

Range clipping is only worth its complexity if it beats the simpler rule on independent evidence. Added before any clipped result was computed.

**Test set.** `research/subspecies_test_set.csv`: the 57 subspecies, variety and population listing names (49 species) classified COVERS (19), SPILLS (33) or UNCLEAR (5), with their listed and unlisted Canadian subspecies. Test records are the records of those species in the new download whose GBIF accepted taxon is infraspecific. A record of a listed subspecies (`listed_ssp_counts`, pooled over all test names of the species) is true; a record of an unlisted Canadian subspecies (`unlisted_ssp_counts`, minus any listed one) is false; any other infraspecific record is ignored. Subspecies are matched by epithet of the accepted name. The classification uses CAN-SAR status as is (badger jeffersonii and shrike migrans are false), and the pooled result is also reported without the UNCLEAR species.

**No leakage.** Scoring range clipping ignores rule 2 (infraspecific records count outside the range): a test record counts only when it lies inside the buffered range of one of the species' test listings. The ecozone overrides were fixed from COSEWIC range text in `atrisk_range_overrides.csv` (committed with this gate), not from where the test records fall. Overrides are only for listings that split within a province; more than 10 stops the work for review.

**Methods**, each scored on the test listings only:

1. Live: species-level counting, every record of the species counts.
2. Allowlist: species of COVERS names plus badger, shrike and bison count everywhere; species of SPILLS names and Silene scouleri count nowhere.
3. Range clip, with the ecozone overrides (3a) and without them, the five override listings falling back to their CAN-SAR provinces (3b).

**Metrics**, pooled and per species with n >= 20 test records: recall (true records counted / true records) and false share (false records counted / false records). Also, per method, the number of (listing, 0.25 deg cell) pairs the method weights for a test listing where the cell holds at least one false test record and no true one (a listing weighted with only an unlisted subspecies in evidence). A method weights a cell for a listing when any record of the species in the cell counts under that method.

**Ship rule (revised before any result).** Incremental test records are those that range clipping (3a) counts and the allowlist does not; since the allowlist counts every record of the species it keeps, they all come from the species it drops. Range clipping ships if the true share of the incremental test records (true / (true + false), pooled) has a 95% bootstrap CI lower bound above 0.5, resampling species with incremental test records (2,000 resamples, seed 0): each record clipping adds over the allowlist is more likely right than wrong. Otherwise the allowlist ships with the matching fixes (alias keys, Lupinus override). If fewer than 5 species have incremental test records, the test is too thin to decide, the result says so, and the allowlist ships. The first version of this rule (no more false records than the allowlist) failed by construction: the allowlist counts 0 true and 0 false records for every species it drops, so it could never weigh the true records the allowlist loses against the false ones clipping adds.

**Per-species drop.** Every species with n >= 20 incremental test records whose own incremental true share is below 0.5 is flagged, hand-reviewed against its COSEWIC range, and, if clipping ships, its test listings are dropped from the clipped layer as in the allowlist. This mechanism is fixed now; the flagged species are listed after the results.

**Also reported:** the incremental true share and CI for 3b (no overrides) and for 3a without the UNCLEAR species; pooled recall and false share for all four methods; the per-species incremental true share for n >= 20.
