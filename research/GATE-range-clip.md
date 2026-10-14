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
