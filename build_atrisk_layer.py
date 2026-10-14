"""Per-cell conservation-concern layer for where-to-blitz ("Canada's Most Wanted", done properly).

Species at risk: CAN-SAR (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY), the
Canadian authority (IUCN diverges). Per cell, the conservation score is the STATUS-WEIGHTED sum of
CAN-SAR at-risk species with a Canadian GBIF record in the cell: Endangered=3, Threatened=2,
Special Concern=1. This is authoritative status x real occurrences, NOT the range-restriction proxy
(IUCN validation rho=0.054).

Listings, not species: CAN-SAR lists subspecies and populations, GBIF records carry a species key. A record
counts for a listing only inside the listing's range (plus a buffer) or when identified to the listed
infraspecific taxon, and takes the max weight among the listings that accept it (atrisk_ranges.py, which
also builds the range table cluster_results/ca/atrisk_listings.csv and its polygons).

Occurrences come from ONE citable GBIF SQL download (a DOI), not per-species REST paging, which was
capped and returned records in GBIF default order. The query counts records per species, accepted taxon
and 0.01 deg cell (fine enough to clip to a range, then summed to 0.25 deg cells) with these filters:
  - countrycode = 'CA' and specieskey in the matched CAN-SAR species. Taxonomy is Catalogue of Life XR
    (GBIF's default for SQL downloads, gbif/occurrence#538): names are matched with the v2 species/match
    against the COL XR checklist and collapsed to the COL species key; the crosswalk is
    cluster_results/ca/atrisk_species_keys.csv. Unmatched and HIGHERRANK matches get no key and are listed
    in the crosswalk (empty col_key), never used; VARIANT matches (spelling variants that resolve to a
    COL species) are used and printed for review. Accepted species-rank VARIANT alternatives of the
    matched species with the same genus and authorship (orthographic duplicates in COL, e.g. Zeltnera
    muhlenbergii / muehlenbergii) are alias keys of the same species, counted once per cell.
    NAME_OVERRIDES pins names whose bare form is ambiguous between homonyms.
  - hasgeospatialissues = FALSE, decimallatitude not null: usable coordinates only
  - occurrencestatus = 'PRESENT': absence records are not presence
  - year >= 1990: drops historical records of extirpated populations
  - basisofrecord not FOSSIL_SPECIMEN / LIVING_SPECIMEN: no fossils, no zoo or garden specimens
  - NO coordinateUncertainty filter, on purpose: iNaturalist publishes obscured at-risk records with
    large uncertainty, so a 10 km cutoff would drop many of them, and cells here are 0.25 deg anyway.

Honesty: reflects ASSESSED species (CAN-SAR snapshot ~2021); occurrence-based, so absence in a cell
= "not recorded there", not "absent". Ranges are coarse (provinces where no finer polygon exists), so a
listing can still take records of an unlisted population in the same province. Label in-app as COSEWIC
at-risk richness.

Crosswalk diff gate: when a committed crosswalk exists, a re-match that merges names of different status
weights under one species key, splits a key, or loses a previously matched name stops (non-zero exit)
unless --accept-crosswalk-changes is passed. The diff is printed either way.

Provenance: cluster_results/ca/ca_atrisk_provenance.json (doi, download_key, sql, created,
n_species_matched, cansar_source, taxonomy = COL XR dataset title/short name/version/modified/DOI, ranges =
sources, buffer and listings per range source). Per-listing records kept by the clip:
cluster_results/ca/atrisk_listing_clip.csv. Cite the DOI.

Output: cluster_results/ca/ca_atrisk_richness.csv (gi,gj,lat,lon,n_species,score_raw,conservation_norm)
Usage :
  .venv/bin/python build_atrisk_layer.py --validate     # match names, POST the query to GBIF's validator
      # add --accept-crosswalk-changes when the printed crosswalk diff is intended
  GBIF_USER=.. GBIF_PWD=.. GBIF_EMAIL=.. .venv/bin/python build_atrisk_layer.py --submit
      # macOS: GBIF_PWD="$(security find-generic-password -a "$GBIF_USER" -s api.gbif.org -w)"
  .venv/bin/python build_atrisk_layer.py --fetch KEY    # after the GBIF email: poll, read, write layer
"""

import argparse
import base64
import csv
import io
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

import atrisk_ranges
from atrisk_crosswalk import crosswalk_diff, diff_blocks, print_diff, read_crosswalk_rows

RES = 0.25
FINE = 0.01  # download grid: records are clipped to listing ranges at this resolution
MIN_YEAR = 1990
CANSAR = "/tmp/wtb/can-sar.csv"
CANSAR_URL = "https://osf.io/download/jv5w6/"
WT = {"Endangered": 3, "Threatened": 2, "Special Concern": 1}
STATUS = {v: k for k, v in WT.items()}
OUT_DIR = "cluster_results/ca"
CROSSWALK = f"{OUT_DIR}/atrisk_species_keys.csv"
PROVENANCE = f"{OUT_DIR}/ca_atrisk_provenance.json"
OUT_CSV = f"{OUT_DIR}/ca_atrisk_richness.csv"
GBIF = "https://api.gbif.org/v1"
GBIF_V2 = "https://api.gbif.org/v2"
COL_XR = "7ddf754f-d193-4cc9-b351-99906754a03b"  # Catalogue of Life XR checklist, GBIF's SQL default
UNUSABLE = ("NONE", "HIGHERRANK", "NOSPECIES", "ERROR")  # match types that yield no species key
CW_COLS = [
    "name",
    "match_name",
    "col_key",
    "alias_keys",
    "infra_key",
    "col_matchType",
    "col_accepted_name",
    "backbone_key",
    "status_weight",
]
# A bare name that matches several homonyms (HIGHERRANK) is pinned to the authorship of the listed taxon.
NAME_OVERRIDES = {"Lupinus densiflorus": "Lupinus densiflorus Benth."}  # COSEWIC: the BC lupine, Benth.
INFRA_RANKS = ("SUBSPECIES", "VARIETY", "FORM", "INFRASPECIFIC_NAME")
EXCLUDED_BASIS = ("FOSSIL_SPECIMEN", "LIVING_SPECIMEN")
N_CHECK = 6  # species whose download totals are re-counted through the occurrence search API


def gget(url, params):
    for a in range(4):
        try:
            with urllib.request.urlopen(url + urllib.parse.urlencode(params), timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(1.2 * (a + 1))
    return {}


def load_cansar(path=CANSAR):
    """CAN-SAR -> {species name: most-severe status weight}; downloads the CSV if absent."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            with urllib.request.urlopen(CANSAR_URL, timeout=120) as r, open(path, "wb") as f:
                f.write(r.read())
        except Exception as e:
            raise SystemExit(f"CAN-SAR CSV missing at {path} and download from {CANSAR_URL} failed: {e}")
    sev = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            s = (r.get("species") or "").strip()
            st = (r.get("cosewic_status") or "").strip()
            if s and st in WT:
                sev[s] = max(sev.get(s, 0), WT[st])
    return sev


def species_match(name):
    """(col_key, matchType, accepted species name, infra_key) from the v2 species/match against COL XR.
    col_key is the SPECIES-rank key of the accepted usage (a subspecies or synonym collapses to its
    species); None for unmatched, HIGHERRANK or rank-less matches. VARIANT (a spelling variant of an
    accepted name) still resolves to its species. infra_key is the accepted usage's key when that usage is
    below species rank (the listed subspecies or variety), else "". ERROR = no usable response."""
    m = gget(f"{GBIF_V2}/species/match?", {"scientificName": name, "checklistKey": COL_XR})
    if not m.get("diagnostics"):
        return None, "ERROR", "", ""
    mt = m["diagnostics"].get("matchType") or "NONE"
    if mt in UNUSABLE:
        return None, mt, "", ""
    acc = m.get("acceptedUsage") or m.get("usage") or {}
    cands = [acc] + list(m.get("classification") or [])
    sp = next((c for c in cands if c.get("rank") == "SPECIES" and c.get("key")), None)
    if sp is None:
        return None, "NOSPECIES", "", ""
    infra = str(acc["key"]) if acc.get("rank") in INFRA_RANKS and acc.get("key") else ""
    return str(sp["key"]), mt, sp.get("canonicalName") or sp.get("name") or "", infra


def edits(a, b):
    """Levenshtein distance."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def variant_aliases(m, key):
    """Accepted species-rank VARIANT alternatives in a verbose v2 match response that are orthographic
    duplicates of the matched species, which COL XR keeps as separate accepted keys (Zeltnera muhlenbergii /
    muehlenbergii): same genus, same authorship, and epithets at most two edits apart. VARIANT alone also
    returns unrelated near-homonyms (Ulus maritimus for Ursus maritimus, Viola pinnata L. for Viola pedata
    L.). -> sorted keys other than key."""
    top = m.get("usage") or {}
    alts = (m.get("diagnostics") or {}).get("alternatives") or []
    out = {
        str(u["key"])
        for a in alts
        if (a.get("diagnostics") or {}).get("matchType") == "VARIANT"
        and (u := a.get("usage") or {}).get("rank") == "SPECIES"
        and u.get("status", "ACCEPTED") == "ACCEPTED"
        and u.get("key")
        and u.get("genericName") == top.get("genericName")
        and u.get("authorship") == top.get("authorship")
        and edits(u.get("specificEpithet") or "", top.get("specificEpithet") or "") <= 2
    }
    return sorted(out - {key})


def species_aliases(acc_name, key):
    """Alias keys of a matched species: re-match its accepted name verbosely, keep variant_aliases."""
    m = gget(
        f"{GBIF_V2}/species/match?",
        {"scientificName": acc_name, "checklistKey": COL_XR, "verbose": "true"},
    )
    return variant_aliases(m, key)


def backbone_key(name):
    """GBIF Backbone speciesKey (v1 species/match), kept in the crosswalk for reference only."""
    m = gget(f"{GBIF}/species/match?", {"name": name})
    return m.get("speciesKey") if m.get("matchType") not in (None, "NONE", "HIGHERRANK") else None


def match_species(sev, accept_changes=False):
    """Match names against COL XR, aggregate max severity per species key, gate on the crosswalk diff,
    write the crosswalk. -> {col_key: weight}, alias keys included under their species' weight."""
    old, old_bb = read_crosswalk_rows(CROSSWALK)
    weights, rows, new = {}, [], {}
    for i, (name, w) in enumerate(sev.items()):
        match_name = NAME_OVERRIDES.get(name, name)
        key, mt, acc, infra = species_match(match_name)
        aliases = species_aliases(acc, key) if key else []
        bb = old_bb.get(name) or (backbone_key(name) if key else "")
        rows.append((name, match_name, key or "", " ".join(aliases), infra, mt, acc, bb or "", w))
        new[name] = (key or "", w)
        for k in [key] + aliases if key else []:
            weights[k] = max(weights.get(k, 0), w)
        if (i + 1) % 100 == 0:
            print(f"  matched {i + 1}/{len(sev)} names")
    errors = [r[0] for r in rows if r[5] == "ERROR"]
    if errors:
        # gget() swallows every GBIF error, so an outage looks like "nothing matched".
        raise SystemExit(f"no species/match response for {len(errors)} names (outage?): {errors[:5]}")
    skipped = [r for r in rows if not r[2]]
    print(
        f"CAN-SAR names: {len(sev)}; matched to a COL species: {len(sev) - len(skipped)}; "
        f"no key: {len(skipped)}; distinct COL keys: {len(weights)}"
    )
    for r in skipped:
        print(f"  no key: {r[0]} ({r[5]})")
    for r in rows:
        if r[2] and r[5] != "EXACT":
            print(f"  used {r[5]}: {r[0]} -> {r[6]} ({r[2]})")
        if r[3]:
            print(f"  alias keys: {r[0]} -> {r[6]} ({r[2]}) + {r[3]}")
        if r[1] != r[0]:
            print(f"  name override: {r[0]} matched as {r[1]} -> {r[6]} ({r[2]})")
    if not weights:
        raise SystemExit("no CAN-SAR names matched in GBIF species/match")
    if old:
        d = crosswalk_diff(old, new)
        print_diff(d)
        if diff_blocks(d) and not accept_changes:
            raise SystemExit(
                "crosswalk changed (see diff above); review it, then re-run with --accept-crosswalk-changes"
            )
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CROSSWALK, "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(CW_COLS)
        cw.writerows(rows)
    print(f"wrote {CROSSWALK}")
    return weights


def build_sql(keys):
    # Plain taxon columns hold Catalogue of Life XR keys (strings like '4RF6J') since GBIF made COL XR
    # the SQL default (gbif/occurrence#538). acceptedtaxonkey keeps records identified below species rank
    # apart, so a record of a listed subspecies counts for its listing outside the listing's range.
    sk = "specieskey"
    in_list = ", ".join(f"'{k}'" for k in sorted(keys))
    basis = ", ".join(f"'{b}'" for b in EXCLUDED_BASIS)
    yi, xi = f"FLOOR(decimallatitude / {FINE})", f"FLOOR(decimallongitude / {FINE})"
    return (
        f"SELECT {sk} AS specieskey, acceptedtaxonkey AS acceptedtaxonkey, {yi} AS yi, {xi} AS xi, "
        f"COUNT(*) AS n FROM occurrence WHERE countrycode = 'CA' AND {sk} IN ({in_list}) "
        f"AND hasgeospatialissues = FALSE AND occurrencestatus = 'PRESENT' AND \"year\" >= {MIN_YEAR} "
        f"AND basisofrecord NOT IN ({basis}) AND decimallatitude IS NOT NULL "
        f"GROUP BY {sk}, acceptedtaxonkey, {yi}, {xi}"
    )


def http_json(url, body=None, auth=None):
    """POST/GET JSON. -> (status, parsed-or-text); HTTP errors return their status instead of raising."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode(errors="replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def species_totals(cols):
    """Download columns -> {COL speciesKey: records in the download}."""
    tot = defaultdict(int)
    for k, n in zip(cols["specieskey"], cols["n"]):
        tot[k] += int(n)
    return tot


def count_mismatches(totals, api_counts, tol=0.05):
    """Keys whose download total is off the search-API count by more than tol (relative) and 2 records."""
    return {
        k: (totals.get(k, 0), c) for k, c in api_counts.items() if abs(totals.get(k, 0) - c) > max(2, tol * c)
    }


def search_count(key, basis):
    """Records of one COL XR speciesKey under the SQL filters, counted by the occurrence search API."""
    params = [
        ("checklistKey", COL_XR),
        ("speciesKey", key),
        ("country", "CA"),
        ("hasGeospatialIssue", "false"),
        ("hasCoordinate", "true"),
        ("occurrenceStatus", "PRESENT"),
        ("year", f"{MIN_YEAR},3000"),
        ("limit", 0),
    ] + [("basisOfRecord", b) for b in basis]
    return gget(f"{GBIF}/occurrence/search?", params).get("count")


def check_counts(cols, keys, n=N_CHECK, seed=0):
    """Guard against silent drift between the SQL table and the search index (e.g. a taxonomy switch
    that empties a key filter): re-count a seeded sample of crosswalk keys and refuse on mismatch."""
    basis = [b for b in gget(f"{GBIF}/enumeration/basic/BasisOfRecord", {}) or [] if b not in EXCLUDED_BASIS]
    if not basis:
        raise SystemExit("could not read the BasisOfRecord enumeration from GBIF")
    sample = random.Random(seed).sample(sorted(keys), min(n, len(keys)))
    api = {k: search_count(k, basis) for k in sample}
    if any(c is None for c in api.values()):
        raise SystemExit(f"occurrence search failed for some of {sample}")
    bad = count_mismatches(species_totals(cols), api)
    print(f"count check: {len(api) - len(bad)}/{len(api)} species match the search API")
    if bad:
        raise SystemExit(f"download vs search API mismatch (download, api): {bad}; not writing the layer")


def do_validate(sql):
    status, body = http_json(
        f"{GBIF}/occurrence/download/request/validate", {"format": "SQL_TSV_ZIP", "sql": sql}
    )
    print(f"validate HTTP {status}")
    print("normalized SQL:", body.get("sql", body) if isinstance(body, dict) else body)
    if not 200 <= status < 300:
        raise SystemExit(1)


def do_submit(sql):
    env = {k: os.environ.get(k) for k in ("GBIF_USER", "GBIF_PWD", "GBIF_EMAIL")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit(f"--submit needs env vars: {', '.join(missing)} (not set)")
    status, body = http_json(
        f"{GBIF}/occurrence/download/request",
        {
            "format": "SQL_TSV_ZIP",
            "sql": sql,
            "sendNotification": True,
            "notificationAddresses": [env["GBIF_EMAIL"]],
        },
        auth=(env["GBIF_USER"], env["GBIF_PWD"]),
    )
    if not 200 <= status < 300:
        hint = (
            " 403 means SQL downloads are not enabled on this account (contact helpdesk@gbif.org)."
            if status == 403
            else ""
        )
        raise SystemExit(f"GBIF download request failed: HTTP {status}.{hint} {body}")
    print(f"download key: {str(body).strip()}")
    print(f"later: build_atrisk_layer.py --fetch {str(body).strip()}")


def read_crosswalk():
    """Committed crosswalk -> {name: (species and alias keys, infra key)}; names without a COL species key
    (unmatched, HIGHERRANK) are left out."""
    if not os.path.exists(CROSSWALK):
        raise SystemExit(f"{CROSSWALK} missing: run --validate or --submit first (it writes the crosswalk)")
    with open(CROSSWALK) as f:
        return {
            r["name"]: (tuple([r["col_key"]] + r["alias_keys"].split()), r["infra_key"])
            for r in csv.DictReader(f)
            if r["col_key"]
        }


def col_release():
    """COL XR checklist dataset metadata, recorded so the taxonomy release is traceable."""
    status, d = http_json(f"{GBIF}/dataset/{COL_XR}")
    if status != 200 or not isinstance(d, dict):
        raise SystemExit(f"could not read COL XR dataset metadata: HTTP {status}")
    keys = ("title", "shortName", "version", "modified", "pubDate", "doi")
    return {"dataset_key": COL_XR, **{k: d.get(k) for k in keys}}


def do_fetch(key):
    crosswalk = read_crosswalk()
    keys = {k for ks, _ in crosswalk.values() for k in ks}
    while True:
        status, info = http_json(f"{GBIF}/occurrence/download/{key}")
        if status != 200 or not isinstance(info, dict):
            raise SystemExit(f"GBIF download lookup failed: HTTP {status} {info}")
        st = info.get("status")
        print(f"status: {st}")
        if st == "SUCCEEDED":
            break
        if st in ("KILLED", "FAILED", "CANCELLED"):
            raise SystemExit(f"GBIF download {key} ended as {st}")
        time.sleep(30)
    print(f"DOI: {info.get('doi')}")
    with urllib.request.urlopen(info["downloadLink"], timeout=600) as r:
        blob = r.read()
    with (
        zipfile.ZipFile(io.BytesIO(blob)) as z,
        z.open(next(n for n in z.namelist() if not n.endswith("/"))) as f,
    ):
        cols = atrisk_ranges.read_columns(f)
    listings = atrisk_ranges.read_listings()
    entries = atrisk_ranges.listing_entries(listings, crosswalk, atrisk_ranges.load_range_geoms())
    score, nsp, kept = atrisk_ranges.score_cells(cols, entries, atrisk_ranges.transformer(), FINE, RES)
    print(f"{len(cols['n'])} fine-cell rows; {len(score)} cells host >=1 at-risk listing")
    if not score:
        raise SystemExit("no at-risk cells in the download: refusing to overwrite ca_atrisk_richness.csv")
    check_counts(cols, keys)
    write_layer(score, nsp)
    atrisk_ranges.write_clip_table(listings, entries, kept)
    prov = {
        "doi": info.get("doi"),
        "download_key": key,
        "sql": (info.get("request") or {}).get("sql") or build_sql(keys),
        "created": info.get("created") or datetime.now(timezone.utc).isoformat(),
        "n_species_matched": len({ks[0] for ks, _ in crosswalk.values()}),
        "taxonomy": col_release(),
        "cansar_source": f"{CANSAR_URL} (CAN-SAR, OSF DOI 10.17605/OSF.IO/E4A58)",
        "ranges": atrisk_ranges.provenance(listings, entries),
    }
    with open(PROVENANCE, "w") as f:
        json.dump(prov, f, indent=2)
    print(f"wrote {PROVENANCE}")


def write_layer(score, nsp):
    """One row per 0.25-deg cell holding >=1 at-risk species; fullgrid_fields.py joins on (gi, gj) and
    treats absent cells as 0, so every lattice cell with records keeps its score."""
    hi = max(score.values())
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gi", "gj", "lat", "lon", "n_species", "score_raw", "conservation_norm"])
        for gi, gj in sorted(score):
            v = score[(gi, gj)]
            w.writerow(
                [gi, gj, (gi + 0.5) * RES, (gj + 0.5) * RES, nsp[(gi, gj)], f"{v:.2f}", f"{v / hi:.5f}"]
            )
    print(f"cells with at-risk score: {len(score)}; max score={hi:.1f}")
    print(f"wrote {OUT_CSV}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", action="store_true", help="match names, validate the SQL with GBIF")
    g.add_argument("--submit", action="store_true", help="submit the SQL download (needs GBIF_* env vars)")
    g.add_argument("--fetch", metavar="KEY", help="poll a finished download and build the layer")
    ap.add_argument(
        "--accept-crosswalk-changes",
        action="store_true",
        help="proceed when the re-match merges weights, splits a key or loses a name vs the committed crosswalk",
    )
    ap.add_argument("--cansar", default=CANSAR, help=f"CAN-SAR CSV path (default {CANSAR})")
    a = ap.parse_args()
    if a.fetch:
        return do_fetch(a.fetch)
    if a.submit and not all(os.environ.get(k) for k in ("GBIF_USER", "GBIF_PWD", "GBIF_EMAIL")):
        do_submit("")  # fail fast on missing vars before any network work
    sev = load_cansar(a.cansar)
    print(f"CAN-SAR COSEWIC at-risk species names: {len(sev)}")
    sql = build_sql(match_species(sev, a.accept_crosswalk_changes))
    (do_validate if a.validate else do_submit)(sql)


if __name__ == "__main__":
    main()
