"""Listing-level range clipping for the at-risk layer (build_atrisk_layer.py).

CAN-SAR lists species, subspecies and populations, but a GBIF record carries a species key, so counting per
species puts a population's status weight on every record of the species (Ardea herodias fannini, Special
Concern, on every Canadian great blue heron). Here a record counts for a listing only when it lies inside the
listing's range plus BUFFER_M, or is identified to the listed infraspecific taxon (whole-taxon listings only:
a population of a subspecies, such as a caribou population, does not take records of the subspecies from
outside its range). Ranges, first available wins:
  - dfo:      Fisheries and Oceans Canada "Aquatic Species at Risk Distribution (Range)", population polygons
  - ecozone:  hand-listed ecozones (atrisk_range_overrides.csv), within the listing's provinces, for listings
              that split within a province
  - province: the provinces and territories CAN-SAR gives for the listing (Natural Earth admin-1 polygons)
  - none:     no polygon (ocean-only ranges, or no province): species-level counting, flagged in the table
When several listings share a species, a record takes the max weight among the listings that accept it, and
a species (with its alias keys) adds its best record weight once per cell.

Build the range inputs once (downloads are listed in RANGE_SOURCES):
  .venv/bin/python atrisk_ranges.py --dfo DIST.gdb --ecozones ecozones.shp --admin1 ne_admin1.geojson
which writes the listing table and the simplified range polygons that build_atrisk_layer.py reads.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import defaultdict

import numpy as np

OUT_DIR = "cluster_results/ca"
LISTINGS = f"{OUT_DIR}/atrisk_listings.csv"
RANGES = f"{OUT_DIR}/atrisk_ranges.geojson"
OVERRIDES = f"{OUT_DIR}/atrisk_range_overrides.csv"
CLIP_CSV = f"{OUT_DIR}/atrisk_listing_clip.csv"
WT = {"Endangered": 3, "Threatened": 2, "Special Concern": 1}
PROVINCES = ("BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "PE", "NL", "YT", "NT", "NU")
OCEANS = ("Pacific_Ocean", "Atlantic_Ocean", "Arctic_Ocean")
# Coordinate noise allowance: GBIF coordinates are rounded or generalised by a few km, records are binned
# to 0.01 deg (< 1.1 km), and the source ranges are drawn at roughly 1:1M (DFO) or are province edges at
# 1:10M (Natural Earth), so a point on the true range edge can sit several km outside the drawn polygon.
BUFFER_M = 10_000
SIMPLIFY_M = 500  # DFO polygon simplification in the source CRS (metres) before the dissolve
SIMPLIFY_DEG = 0.005  # simplification of the committed lon/lat polygons, ~0.5 km, well under BUFFER_M
CRS_M = "EPSG:3978"  # Canada Atlas Lambert: metres, for buffering and point tests
LISTING_COLS = [
    "listing_id",
    "common_name",
    "names",
    "status_weight",
    "provinces",
    "range_source",
    "range_ids",
]
# DFO population names that CAN-SAR words differently (same COSEWIC designatable unit): (common, pop) -> id
DFO_RENAMED = {
    ("Coastrange Sculpin", "Cultus population"): "123",  # coastrange sculpin cultus lake population
    ("Deepwater Sculpin", "Great Lakes-Western St. Lawrence"): "70",  # great lakes-upper st. lawrence
    ("Rocky Mountain Sculpin", "Westslope"): "292",  # pacific populations, renamed by COSEWIC in 2017
}
RANGE_SOURCES = {
    "dfo": {
        "title": "Fisheries and Oceans Canada Species at Risk Distribution (Range), Open Government Licence - Canada",
        "url": "https://open.canada.ca/data/en/dataset/e0fabad5-9379-4077-87b9-5705f28c490b",
        "file": "Distribution_SAR_EN_2026.gdb.zip",
        "sha256": "857869c93b42f48aa8f74503c3f74502fafdbddb71c940107b4cfbefc1dd5bde",
    },
    "ecozone": {
        "title": "Ecological Framework of Canada, ecozones (AAFC), Open Government Licence - Canada",
        "url": "https://sis.agr.gc.ca/cansis/nsdb/ecostrat/zone/ecozone_shp.zip",
        "sha256": "ccfeaacad154c922eb08e3012043eb05944b838c6f62e26f8ecfdf32336ce4f6",
    },
    "province": {
        "title": "Natural Earth 10m admin-1 states and provinces v5.1.2, public domain",
        "url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
        "ne_10m_admin_1_states_provinces.geojson",
        "sha256": "22d0e3ad85eb3e27f17cabf8ba2d50e554fbc27a87796ff891d958185da62fb5",
    },
}


def norm(s):
    """Lowercase ASCII words: accents, hyphens and punctuation dropped, for name joins across sources."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def is_population(common):
    """CAN-SAR names a population (or populations) listing in its common name."""
    return bool(re.search(r"\bpopulations?\b", norm(common)))


def load_listings(path):
    """CAN-SAR CSV -> {listing id: {common, names, weight, provinces, oceans}} for every listing with a COSEWIC
    at-risk status; weight is the most severe status across its rows, ranges the union of its rows'."""
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            lid = (r.get("speciesID") or "").strip()
            if not lid:
                continue
            d = out.setdefault(
                lid, {"common": set(), "names": set(), "weight": 0, "provinces": set(), "oceans": set()}
            )
            d["common"].add((r.get("common_name") or "").strip())
            if (r.get("species") or "").strip():
                d["names"].add(r["species"].strip())
            d["weight"] = max(d["weight"], WT.get((r.get("cosewic_status") or "").strip(), 0))
            toks = (r.get("ranges") or "").split()
            d["provinces"].update(t for t in toks if t in PROVINCES)
            d["oceans"].update(t for t in toks if t in OCEANS)
    for d in out.values():
        d["common"] = min(d["common"], key=lambda c: (c != norm(c), c))  # prefer a clean ASCII spelling
    return {k: d for k, d in out.items() if d["weight"] and d["names"]}


def dfo_join_key(common, pop=""):
    """Join key shared by CAN-SAR common names and DFO common + population names."""
    s = norm(f"{common} {pop}")
    return re.sub(r"\b(populations?|subspecies|designatable units?)\b", " ", s).split()


def match_dfo(listings, dfo_names):
    """{listing id: DFO (common, population)} where the normalised names agree word for word, plus
    DFO_RENAMED where the two sources name one population differently."""
    by_key = {tuple(dfo_join_key(c, p)): (c, p) for c, p in dfo_names}
    out = {lid: by_key[k] for lid, d in listings.items() if (k := tuple(dfo_join_key(d["common"]))) in by_key}
    out.update({lid: cp for cp, lid in DFO_RENAMED.items() if lid in listings and cp in set(dfo_names)})
    return out


def read_overrides(path=OVERRIDES):
    """{listing id: [ecozone ids]} from the hand-written override table (listing_id, ecozones, reason)."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {r["listing_id"]: r["ecozones"].split() for r in csv.DictReader(f)}


def assign_ranges(listings, dfo, overrides):
    """{listing id: (source, [range ids])} in preference order dfo, ecozone, province, none."""
    out = {}
    for lid, d in listings.items():
        if lid in dfo:
            out[lid] = ("dfo", [f"dfo:{lid}"])
        elif lid in overrides:
            out[lid] = ("ecozone", [f"eco:{z}:{p}" for z in overrides[lid] for p in sorted(d["provinces"])])
        elif d["provinces"]:
            out[lid] = ("province", [f"prov:{p}" for p in sorted(d["provinces"])])
        else:
            out[lid] = ("none", [])
    return out


def write_listings(listings, ranges, path=LISTINGS):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(LISTING_COLS)
        for lid in sorted(listings, key=int):
            d, (src, ids) = listings[lid], ranges[lid]
            w.writerow(
                [lid, d["common"], ";".join(sorted(d["names"])), d["weight"],
                 " ".join(sorted(d["provinces"] | d["oceans"])), src, " ".join(ids)]
            )  # fmt: skip


def read_listings(path=LISTINGS):
    """Committed listing table -> list of row dicts with names split and weight as int."""
    if not os.path.exists(path):
        raise SystemExit(f"{path} missing: build it with atrisk_ranges.py (see its docstring)")
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["names"] = r["names"].split(";")
        r["status_weight"] = int(r["status_weight"])
        r["range_ids"] = r["range_ids"].split()
    return rows


def transformer():
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", CRS_M, always_xy=True)
    return lambda lon, lat: t.transform(lon, lat)


def load_range_geoms(path=RANGES):
    """{range id: shapely geometry in CRS_M}, from the committed lon/lat GeoJSON."""
    import shapely
    from shapely.geometry import shape

    with open(path) as f:
        feats = json.load(f)["features"]
    project = transformer()
    return {
        ft["properties"]["range_id"]: shapely.make_valid(shapely.transform(
            shape(ft["geometry"]), lambda xy: np.column_stack(project(xy[:, 0], xy[:, 1]))))
        for ft in feats
    }  # fmt: skip


def buffered_union(geometries, buffer_m):
    """Buffer a range union, bounding overlay work for positive buffers of >1024 atomic components."""
    import shapely

    geometry = shapely.union_all(geometries)
    if buffer_m <= 0:
        return geometry.buffer(buffer_m)
    parts = np.asarray([geometry], dtype=object)
    while np.any(shapely.get_type_id(parts) >= 4):
        parts = shapely.get_parts(parts)
    parts = parts[~shapely.is_empty(parts)]
    if len(parts) <= 1024:
        return geometry.buffer(buffer_m)

    # Morton order keeps nearby components together so chunk unions remove local overlaps early.
    bounds = shapely.bounds(parts)
    centers = (bounds[:, :2] + bounds[:, 2:]) / 2
    minimum = centers.min(axis=0)
    extent = centers.max(axis=0) - minimum
    coords = ((centers - minimum) / np.maximum(extent, 1) * 65535).astype(np.uint64)

    def spread(v):
        v = (v | (v << 8)) & np.uint64(0x00FF00FF)
        v = (v | (v << 4)) & np.uint64(0x0F0F0F0F)
        v = (v | (v << 2)) & np.uint64(0x33333333)
        return (v | (v << 1)) & np.uint64(0x55555555)

    key = spread(coords[:, 0]) | (spread(coords[:, 1]) << 1)
    parts = parts[np.argsort(key, kind="stable")]
    chunk_size = 512
    chunks = [
        # BaseGeometry.buffer uses quad_segs=16; the vectorized default is 8.
        shapely.union_all(shapely.buffer(parts[lo : lo + chunk_size], buffer_m, quad_segs=16))
        for lo in range(0, len(parts), chunk_size)
    ]
    return shapely.union_all(chunks)


def listing_entries(listings, crosswalk, geoms, buffer_m=BUFFER_M):
    """Listing rows + crosswalk {name: (keys, infra key)} + {range id: geometry} -> entries for score_cells:
    dicts with id, weight, keys (species and alias keys), infra (keys of the listed infraspecific taxon, empty
    for population listings) and geom (buffered union of the listing's ranges, None = no clipping)."""
    import shapely

    out = []
    for r in listings:
        keys, infra = set(), set()
        for n in r["names"]:
            k, ik = crosswalk.get(n, ((), ""))
            keys.update(k)
            if ik:
                infra.add(ik)
        if not keys:
            continue
        geom = None
        if r["range_ids"]:
            missing = [i for i in r["range_ids"] if i not in geoms]
            if missing:
                raise SystemExit(f"listing {r['listing_id']}: range ids {missing} not in {RANGES}")
            geom = buffered_union([geoms[i] for i in r["range_ids"]], buffer_m)
            shapely.prepare(geom)
        if is_population(r["common_name"]):
            infra = set()
        out.append({"id": r["listing_id"], "weight": r["status_weight"], "keys": keys, "infra": infra,
                    "geom": geom})  # fmt: skip
    return out


def key_units(entries):
    """Union-find over the entries' key sets: {key: unit} so alias keys of one species count once."""
    parent = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for e in entries:
        ks = sorted(e["keys"])
        for k in ks:
            parent[find(k)] = find(ks[0])
    return {k: find(k) for k in parent}


def accepted_rows(cols, entries, project, fine):
    """Yield (entry, rows of its keys, rows it accepts, inside) per listing, as index arrays into cols. A
    listing accepts a row of one of its keys when it has no range, the row's accepted taxon is the listed
    infraspecific taxon, or the fine-cell centre lies in its buffered range (inside: per accepted row)."""
    import shapely

    sk, at = cols["specieskey"], cols["acceptedtaxonkey"]
    x, y = project((cols["xi"] + 0.5) * fine, (cols["yi"] + 0.5) * fine)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    for e in entries:
        idx = np.flatnonzero(np.isin(sk, sorted(e["keys"])))
        inside = np.ones(len(idx), dtype=bool)
        if e["geom"] is not None and len(idx):
            inside = shapely.contains_xy(e["geom"], x[idx], y[idx])
        ok = inside | np.isin(at[idx], sorted(e["infra"]))
        yield e, idx, idx[ok], inside[ok]


def record_weights(cols, entries, project, fine):
    """Per fine-cell row, the max weight among the listings that accept it (0 = none).
    -> (weights, {listing id: (records kept, records of its keys)})"""
    w = np.zeros(len(cols["specieskey"]), dtype=np.int8)
    kept = {}
    for e, idx, acc, _ in accepted_rows(cols, entries, project, fine):
        w[acc] = np.maximum(w[acc], e["weight"])
        kept[e["id"]] = (int(cols["n"][acc].sum()), int(cols["n"][idx].sum()))
    return w, kept


def score_cells(cols, entries, project, fine=0.01, res=0.25):
    """Column arrays of the fine-grid download (specieskey, acceptedtaxonkey, yi, xi, n) -> (score, nsp,
    kept) keyed by the res-degree cell (gi, gj): each species unit adds its best accepted record weight once
    per cell. kept = {listing id: (records kept, records of its keys)} for the provenance and review."""
    w, kept = record_weights(cols, entries, project, fine)
    ratio = round(res / fine)
    unit = key_units(entries)
    best = {}
    gi, gj = cols["yi"] // ratio, cols["xi"] // ratio
    for i in np.flatnonzero(w):
        c = (int(gi[i]), int(gj[i]), unit[cols["specieskey"][i]])
        best[c] = max(best.get(c, 0), int(w[i]))
    score, nsp = defaultdict(float), defaultdict(int)
    for (a, b, _), v in best.items():
        score[(a, b)] += v
        nsp[(a, b)] += 1
    return score, nsp, kept


def read_columns(f):
    """Download TSV (file object or path) -> column arrays for score_cells."""
    import pandas as pd

    df = pd.read_csv(f, sep="\t", dtype={"specieskey": str, "acceptedtaxonkey": str}, keep_default_na=False)
    return {
        "specieskey": df["specieskey"].to_numpy(dtype=object),
        "acceptedtaxonkey": df["acceptedtaxonkey"].to_numpy(dtype=object),
        **{k: df[k].astype(float).astype(np.int64).to_numpy() for k in ("yi", "xi", "n")},
    }


def write_clip_table(listings, entries, kept, path=CLIP_CSV):
    """Per listing: range source and records kept by the clip out of the records of its species keys
    (both empty for listings without a matched species key)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["listing_id", "common_name", "status_weight", "range_source", "records_kept", "records_species"]
        )
        for r in listings:
            k, n = kept.get(r["listing_id"], ("", ""))
            w.writerow([r["listing_id"], r["common_name"], r["status_weight"], r["range_source"], k, n])
    print(f"wrote {path}")


def provenance(listings, entries, buffer_m=BUFFER_M):
    """Range block of the layer provenance: sources, buffer, listings per range source, unclipped listings."""
    used = {e["id"] for e in entries}
    per = defaultdict(int)
    for r in listings:
        per[r["range_source"]] += r["listing_id"] in used
    return {
        "sources": RANGE_SOURCES,
        "buffer_m": buffer_m,
        "listings_in_layer_by_source": dict(per),
        "listings_without_range": sorted(r["common_name"] for r in listings
                                         if r["range_source"] == "none" and r["listing_id"] in used),
    }  # fmt: skip


def ogr_features(src, layer=None, sql=None, simplify=None, where=None):
    """Read a vector source through ogr2ogr as lon/lat GeoJSON features (avoids a GDAL Python binding)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.geojson")
        cmd = ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", "-lco", "COORDINATE_PRECISION=5", out, src]
        cmd += ["-dialect", "sqlite", "-sql", sql] if sql else ([layer] if layer else [])
        if simplify:
            cmd += ["-simplify", str(simplify)]
        if where:
            cmd += ["-where", where]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(out, encoding="utf-8", errors="replace") as f:  # the ecozone .dbf is not UTF-8
            return json.load(f)["features"]


def compact(g):
    """Lon/lat geometry simplified to SIMPLIFY_DEG on a 0.001 deg grid: far below BUFFER_M, small to commit."""
    import shapely

    g = shapely.make_valid(shapely.make_valid(g).simplify(SIMPLIFY_DEG))  # simplify can self-intersect
    return shapely.set_precision(g, 0.001)


def build_inputs(cansar, dfo_gdb, ecozones, admin1):
    """Assemble the listing table and range polygons from the downloaded sources (see RANGE_SOURCES)."""
    import shapely
    from shapely.geometry import mapping, shape

    listings = load_listings(cansar)
    dfo_layer = "DFO_SARA_Dist_EDH_EN"
    dfo_feats = ogr_features(
        dfo_gdb,
        sql=f"SELECT COMMON_E, POP_E, ST_Union(ST_Simplify(Shape, {SIMPLIFY_M})) AS geom FROM {dfo_layer} "
        "GROUP BY COMMON_E, POP_E",
    )
    dfo_geom = {
        (f["properties"]["COMMON_E"], f["properties"]["POP_E"] or ""): f["geometry"] for f in dfo_feats
    }
    for c, p in [cp for cp, g in dfo_geom.items() if g is None]:
        # the SQL dissolve returns null for some lake-sized ranges (Enos Lake sticklebacks): union them here
        q = "'" + c.replace("'", "''") + "'"
        where = f"COMMON_E = {q} AND " + (f"POP_E = '{p}'" if p else "POP_E IS NULL")
        parts = [
            shapely.make_valid(shape(f["geometry"])) for f in ogr_features(dfo_gdb, dfo_layer, where=where)
        ]
        dfo_geom[(c, p)] = mapping(shapely.union_all(parts))
    dfo = match_dfo(listings, dfo_geom)
    unmatched = sorted(set(dfo_geom) - set(dfo.values()))
    print(f"DFO listings: {len(dfo_geom)}; joined to CAN-SAR: {len(dfo)}; not joined: {len(unmatched)}")
    for c, p in unmatched:
        print(f"  DFO not joined: {c} | {p}")
    overrides = read_overrides()
    ranges = assign_ranges(listings, dfo, overrides)
    feats = {f"dfo:{lid}": shape(dfo_geom[cp]) for lid, cp in dfo.items()}
    for f in ogr_features(admin1):
        pr = f["properties"]
        if pr.get("adm0_a3") == "CAN" and pr.get("postal") in PROVINCES:
            feats[f"prov:{pr['postal']}"] = shape(f["geometry"])
    zones = defaultdict(list)
    for f in ogr_features(ecozones):
        zones[str(f["properties"]["ECOZONE"])].append(shape(f["geometry"]))
    used = {i for _, ids in ranges.values() for i in ids}
    for rid in used:
        if rid.startswith("eco:"):  # an ecozone within one of the listing's provinces
            _, z, p = rid.split(":")
            feats[rid] = shapely.intersection(shapely.union_all(zones[z]), feats[f"prov:{p}"])
    fc = {
        "type": "FeatureCollection",
        "sources": RANGE_SOURCES,
        "features": [
            {"type": "Feature", "properties": {"range_id": rid}, "geometry": mapping(compact(g))}
            for rid, g in sorted(feats.items())
            if rid in used
        ],
    }
    with open(RANGES, "w") as f:
        json.dump(fc, f, separators=(",", ":"))
    write_listings(listings, ranges, LISTINGS)
    counts = defaultdict(int)
    for src, _ in ranges.values():
        counts[src] += 1
    print(f"listings: {len(listings)}; range source counts: {dict(counts)}")
    print(f"wrote {LISTINGS} and {RANGES}")


def main():
    ap = argparse.ArgumentParser(description="Build the at-risk listing table and range polygons.")
    ap.add_argument("--cansar", default="/tmp/wtb/can-sar.csv")
    ap.add_argument("--dfo", required=True, help="Distribution_SAR_EN_2026.gdb")
    ap.add_argument("--ecozones", required=True, help="ecozones.shp")
    ap.add_argument("--admin1", required=True, help="ne_10m_admin_1_states_provinces.geojson")
    a = ap.parse_args()
    build_inputs(a.cansar, a.dfo, a.ecozones, a.admin1)


if __name__ == "__main__":
    main()
