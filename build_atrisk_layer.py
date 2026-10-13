"""Per-cell conservation-concern layer for where-to-blitz ("Canada's Most Wanted", done properly).

Species at risk: CAN-SAR (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY), the
Canadian authority (IUCN diverges). Per cell, the conservation score is the STATUS-WEIGHTED sum of
CAN-SAR at-risk species with a Canadian GBIF record in the cell: Endangered=3, Threatened=2,
Special Concern=1 (a species listed several times keeps its most severe status). This is
authoritative status x real occurrences, NOT the range-restriction proxy (IUCN validation rho=0.054).

Occurrences come from ONE citable GBIF SQL download (a DOI), not per-species REST paging, which was
capped and returned records in GBIF default order. The query counts records per species per 0.25 deg
cell with these filters:
  - countrycode = 'CA' and specieskey in the matched CAN-SAR species (names matched with GBIF
    species/match, collapsed to species level; the crosswalk is cluster_results/ca/atrisk_species_keys.csv)
  - hasgeospatialissues = FALSE, decimallatitude not null: usable coordinates only
  - occurrencestatus = 'PRESENT': absence records are not presence
  - year >= 1990: drops historical records of extirpated populations
  - basisofrecord not FOSSIL_SPECIMEN / LIVING_SPECIMEN: no fossils, no zoo or garden specimens
  - NO coordinateUncertainty filter, on purpose: iNaturalist publishes obscured at-risk records with
    large uncertainty, so a 10 km cutoff would drop many of them, and cells here are 0.25 deg anyway.

Honesty: reflects ASSESSED species (CAN-SAR snapshot ~2021); occurrence-based, so absence in a cell
= "not recorded there", not "absent". Species-level matching applies the most severe status of any
listed population to every record of that species. Label in-app as COSEWIC at-risk richness.

Provenance: cluster_results/ca/ca_atrisk_provenance.json (doi, download_key, sql, created,
n_species_matched, cansar_source). Cite the DOI.

Output: cluster_results/ca/ca_atrisk_richness.csv (gi,gj,lat,lon,n_species,score_raw,conservation_norm)
Usage :
  .venv/bin/python build_atrisk_layer.py --validate     # match names, POST the query to GBIF's validator
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
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

RES = 0.25
MIN_YEAR = 1990
CANSAR = "/tmp/wtb/can-sar.csv"
CANSAR_URL = "https://osf.io/download/jv5w6/"
WT = {"Endangered": 3, "Threatened": 2, "Special Concern": 1}
OUT_DIR = "cluster_results/ca"
CROSSWALK = f"{OUT_DIR}/atrisk_species_keys.csv"
PROVENANCE = f"{OUT_DIR}/ca_atrisk_provenance.json"
OUT_CSV = f"{OUT_DIR}/ca_atrisk_richness.csv"
GBIF = "https://api.gbif.org/v1"


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
    """(speciesKey, matchType) from GBIF species/match; key is None when unusable."""
    m = gget(f"{GBIF}/species/match?", {"name": name})
    mt = m.get("matchType") or "NONE"
    if mt in ("NONE", "HIGHERRANK"):
        return None, mt
    return m.get("speciesKey"), mt


def match_species(sev):
    """Match names, aggregate max severity per speciesKey, write the crosswalk. -> {speciesKey: weight}."""
    weights, rows = {}, []
    matched = unmatched = fuzzy = 0
    for i, (name, w) in enumerate(sev.items()):
        key, mt = species_match(name)
        if key is None:
            unmatched += 1
            continue
        matched += 1
        fuzzy += mt == "FUZZY"
        weights[key] = max(weights.get(key, 0), w)
        rows.append((name, key, mt, w))
        if (i + 1) % 100 == 0:
            print(f"  matched {i + 1}/{len(sev)} names")
    print(
        f"CAN-SAR names: {len(sev)}; matched: {matched}; unmatched: {unmatched}; fuzzy: {fuzzy}; "
        f"distinct speciesKeys: {len(weights)}"
    )
    if not weights:
        # gget() swallows every GBIF error, so an outage looks like "nothing matched".
        raise SystemExit("no CAN-SAR names matched in GBIF species/match (outage?)")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CROSSWALK, "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["name", "speciesKey", "matchType", "status_weight"])
        cw.writerows(rows)
    print(f"wrote {CROSSWALK}")
    return weights


def build_sql(keys):
    # Plain taxon columns hold Catalogue of Life keys since GBIF made COL XR the SQL default
    # (gbif/occurrence#538); backbone speciesKeys from species/match live under gbif_classification.
    sk = "occurrence.gbif_classification.specieskey"
    in_list = ", ".join(f"'{k}'" for k in sorted(keys))
    return (
        f"SELECT {sk} AS specieskey, FLOOR(decimallatitude / {RES}) AS gi, "
        f"FLOOR(decimallongitude / {RES}) AS gj, COUNT(*) AS n FROM occurrence WHERE countrycode = 'CA' "
        f"AND {sk} IN ({in_list}) "
        f"AND hasgeospatialissues = FALSE AND occurrencestatus = 'PRESENT' AND \"year\" >= {MIN_YEAR} "
        f"AND basisofrecord NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN') AND decimallatitude IS NOT NULL "
        f"GROUP BY {sk}, FLOOR(decimallatitude / {RES}), FLOOR(decimallongitude / {RES})"
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


def aggregate(rows, weights):
    """rows: dicts with specieskey, gi, gj, n (TSV rows). Each species adds its weight once per cell.
    Species without a weight are ignored. -> (score, nsp) keyed by (gi, gj)."""
    score, nsp = defaultdict(float), defaultdict(int)
    for r in rows:
        w = weights.get(int(r["specieskey"]))
        if not w:
            continue
        c = (int(float(r["gi"])), int(float(r["gj"])))
        score[c] += w
        nsp[c] += 1
    return score, nsp


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
    if not os.path.exists(CROSSWALK):
        raise SystemExit(f"{CROSSWALK} missing: run --validate or --submit first (it writes the crosswalk)")
    w = {}
    with open(CROSSWALK) as f:
        for r in csv.DictReader(f):
            k = int(r["speciesKey"])
            w[k] = max(w.get(k, 0), int(r["status_weight"]))
    return w


def do_fetch(key):
    weights = read_crosswalk()
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
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        member = next(n for n in z.namelist() if not n.endswith("/"))
        with z.open(member) as f:
            rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t"))
    score, nsp = aggregate(rows, weights)
    print(f"{len(rows)} species-cell rows; {len(score)} cells host >=1 at-risk species")
    if not score:
        raise SystemExit("no at-risk cells in the download: refusing to overwrite ca_atrisk_richness.csv")
    write_layer(score, nsp)
    prov = {
        "doi": info.get("doi"),
        "download_key": key,
        "sql": (info.get("request") or {}).get("sql") or build_sql(weights),
        "created": info.get("created") or datetime.now(timezone.utc).isoformat(),
        "n_species_matched": len(weights),
        "cansar_source": f"{CANSAR_URL} (CAN-SAR, OSF DOI 10.17605/OSF.IO/E4A58)",
    }
    with open(PROVENANCE, "w") as f:
        json.dump(prov, f, indent=2)
    print(f"wrote {PROVENANCE}")


def write_layer(score, nsp):
    d = json.load(open(f"{OUT_DIR}/webapp_data_All_biodiversity.json"))
    rows = next(v for v in d.values() if isinstance(v, list))
    raw = [score.get((int(r[0] // RES), int(r[1] // RES)), 0.0) for r in rows]
    hi = max(raw) or 1.0
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gi", "gj", "lat", "lon", "n_species", "score_raw", "conservation_norm"])
        for r, v in zip(rows, raw):
            key = (int(r[0] // RES), int(r[1] // RES))
            w.writerow([key[0], key[1], r[0], r[1], nsp.get(key, 0), f"{v:.2f}", f"{v / hi:.5f}"])
    nz = sum(1 for v in raw if v)
    print(
        f"webapp cells with at-risk score: {nz}/{len(rows)} ({100 * nz / len(rows):.0f}%); max score={hi:.1f}"
    )
    print(f"wrote {OUT_CSV}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", action="store_true", help="match names, validate the SQL with GBIF")
    g.add_argument("--submit", action="store_true", help="submit the SQL download (needs GBIF_* env vars)")
    g.add_argument("--fetch", metavar="KEY", help="poll a finished download and build the layer")
    ap.add_argument("--cansar", default=CANSAR, help=f"CAN-SAR CSV path (default {CANSAR})")
    a = ap.parse_args()
    if a.fetch:
        return do_fetch(a.fetch)
    if a.submit and not all(os.environ.get(k) for k in ("GBIF_USER", "GBIF_PWD", "GBIF_EMAIL")):
        do_submit("")  # fail fast on missing vars before any network work
    sev = load_cansar(a.cansar)
    print(f"CAN-SAR COSEWIC at-risk species names: {len(sev)}")
    sql = build_sql(match_species(sev))
    (do_validate if a.validate else do_submit)(sql)


if __name__ == "__main__":
    main()
