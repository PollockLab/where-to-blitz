"""Builds the "Complete a checklist" layer: Chao2 inventory completeness per cell and group.

A cell is a complete checklist when enough of its species have been recorded that a species
missing from its list is probably absent, not merely unrecorded. That is what turns
presence-only records into usable zeros, and the layer shows how close each cell is.

Method (incidence-based, Chao 1987; per cell as in KnowBR, Lobo et al. 2018):

  1. sampling unit   one observer on one day in the cell: a distinct (observer_id, observed_on)
  2. incidence       q_s = number of sampling units that recorded species s
  3. Chao2           S_est = S_obs + ((m - 1) / m) * Q1^2 / (2 Q2), or Q1 (Q1 - 1) / 2 in place of
                     Q1^2 / (2 Q2) when Q2 = 0, with m sampling units, Q1 species in exactly one
                     unit and Q2 in exactly two; the form iNEXT's ChaoRichness uses
  4. completeness    S_obs / S_est, in (0, 1]
  5. interval        95% log-transformed interval on S_est from the Chao (1987) variance
                     (Chao & Colwell 2017, eq. 3c), so completeness gets [S_obs/upper, S_obs/lower]
  6. coverage        sample coverage, 1 - (Q1 / U) (m - 1) Q1 / ((m - 1) Q1 + 2 Q2), with U the
                     total incidence count (Chao & Jost 2012; Chao et al. 2014, eq. C.7)

Records: iNaturalist research grade, identified to species (subspecies roll up), with an
observation date, positional accuracy unknown or no coarser than the cell. Source is the
public iNaturalist open-data export, not a sample:

    curl -O https://inaturalist-open-data.s3.amazonaws.com/observations.csv.gz   # ~13 GB
    curl -O https://inaturalist-open-data.s3.amazonaws.com/taxa.csv.gz
    python build_checklist.py --observations observations.csv.gz --taxa taxa.csv.gz

The status bands and the minimum effort live in the sidecar, not in this file's pixels, so the
app can show them and a threshold change is not a rebuild. Chao2 is a lower bound on richness
and is unstable on a handful of units, so cells under MIN_UNITS are drawn as "too few".

Outputs in cluster_results/checklist/ (outside cluster_results/ca/, which a deploy replaces
with the release bundle): checklist_<res>m_<cog>.png per tier and group, one pixel per lattice
cell, and checklist.json describing them. Pixel encoding:

    R  round(completeness * 250), 0..250
    G  observer-days // 256      (capped at 65535 in total)
    B  observer-days % 256
    A  255 for a Canadian cell with at least one record of the group, 64 for a Canadian cell
       with none (drawn as "too few"), 0 outside Canada

checklist_<res>m_<cog>_ci.png holds what only the popup reads, A=255 where the group has records:

    R  round(completeness lower 95% bound * 250)
    G  round(completeness upper 95% bound * 250)
    B  round(sample coverage * 250)
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from statistics import NormalDist

import matplotlib.image
import numpy as np
import pandas as pd
from pyproj import CRS, Transformer

import border_mask
import grid_lattice
from fullgrid_fields import GROUP_TO_COG

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cluster_results", "checklist")
GRID_SIDECAR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cluster_results", "ca", "gettingeven_grid.json"
)
TIERS = (25000, 5000)
MIN_UNITS = 10
BANDS = {"complete": 0.9, "close": 0.7}
RULE_OF_THUMB_UNITS = 30
# iNaturalist iconic taxon ids for the app's groups; "All biodiversity" takes every species.
ICONIC = {
    "Plantae": 47126,
    "Insecta": 47158,
    "Aves": 3,
    "Fungi": 47170,
    "Mammalia": 40151,
    "Actinopterygii": 47178,
    "Reptilia": 26036,
    "Amphibia": 20978,
    "Arachnida": 47119,
    "Mollusca": 47115,
}
GROUP_CODE = {g: i + 1 for i, g in enumerate(ICONIC)}  # 0 = species outside the named groups
BBOX = (-142.0, 40.0, -50.0, 84.0)  # lon/lat prefilter; the lattice and border mask cut exactly
SPECIES_RANK = 10
NO_RECORDS_ALPHA = 64  # a Canadian cell with no record of the group; the app reads >= 32 as Canadian
CHUNK = 5_000_000
Z95 = NormalDist().inv_cdf(0.975)  # iNEXT's qnorm(0.975), not 1.96


def _arrays(*a):
    return (np.asarray(x, float) for x in a)


def chao2(s_obs, q1, q2, m):
    """Chao2 richness estimate as iNEXT computes it, vectorised. m >= 1 wherever s_obs > 0."""
    s_obs, q1, q2, m = _arrays(s_obs, q1, q2, m)
    a = np.where(m > 0, (m - 1) / np.maximum(m, 1), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        f0 = np.where(q2 > 0, a * q1**2 / (2 * q2), a * q1 * (q1 - 1) / 2)
    return s_obs + f0


def chao2_interval(s_obs, q1, q2, m, e1, e2, qe1):
    """95% interval on Chao2 as iNEXT 3.0.2 computes it, vectorised.

    Where Chao2 adds species (Q1 > 0 and Q2 > 0, or Q1 > 1), the log-transformed interval from the
    Chao (1987) variance (Chao & Colwell 2017, eq. 3c). Elsewhere iNEXT's Poisson fallback, which
    needs per-cell sums over species of e^-q (e1), e^-2q (e2) and q e^-q (qe1), q the incidence.
    """
    s_obs, q1, q2, m, e1, e2, qe1 = _arrays(s_obs, q1, q2, m, e1, e2, qe1)
    est = chao2(s_obs, q1, q2, m)
    a = np.where(m > 0, (m - 1) / np.maximum(m, 1), 0.0)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        r = q1 / q2
        var = np.where(
            q2 > 0,
            q2 * (a / 2 * r**2 + a**2 * r**3 + a**2 / 4 * r**4),
            a * q1 * (q1 - 1) / 2 + a**2 * q1 * (2 * q1 - 1) ** 2 / 4 - a**2 * q1**4 / 4 / est,
        )
        d = est - s_obs
        k = np.exp(Z95 * np.sqrt(np.log1p(var / d**2)))
        p = e1 / s_obs
        sd = np.sqrt(np.maximum(e1 - e2 - qe1**2 / m, 0))
        lo_fb = np.maximum(s_obs, (s_obs - Z95 * sd) / (1 - p))
        hi_fb = (s_obs + Z95 * sd) / (1 - p)
        log_form = d > 0  # iNEXT's first two branches; m = 1 (NaN there) falls back instead
        lo, hi = np.where(log_form, s_obs + d / k, lo_fb), np.where(log_form, s_obs + d * k, hi_fb)
    # The fallback can exclude its own estimate on a single unit; such cells are "too few" anyway.
    return np.minimum(lo, est), np.maximum(hi, est)


def coverage(q1, q2, u, m):
    """Incidence-based sample coverage of the reference sample (Chao et al. 2014, eq. C.7)."""
    q1, q2, u, m = _arrays(q1, q2, u, m)
    with np.errstate(divide="ignore", invalid="ignore"):
        tail = np.where(
            q2 > 0,
            (m - 1) * q1 / ((m - 1) * q1 + 2 * q2),
            (m - 1) * (q1 - 1) / ((m - 1) * (q1 - 1) + 2),
        )
        c = 1 - q1 / u * tail
    return np.where((q1 > 0) & (u > 0), c, 1.0)


def species_lookup(taxa):
    """taxon_id -> (species taxon id, group code) for taxa at or below species rank.

    `taxa` has the export's columns taxon_id, ancestry ("48460/1/2/..."), rank_level. Taxa above
    species, or below it with no species ancestor, get species id 0 and are dropped.
    """
    rank = dict(zip(taxa.taxon_id, taxa.rank_level))
    code_of = {v: GROUP_CODE[g] for g, v in ICONIC.items()}
    size = int(taxa.taxon_id.max()) + 1
    sp, grp = np.zeros(size, np.int32), np.zeros(size, np.int8)
    for tid, anc, rl in zip(taxa.taxon_id, taxa.ancestry, taxa.rank_level):
        if not rl <= SPECIES_RANK:
            continue
        path = [int(a) for a in anc.split("/")] if isinstance(anc, str) and anc else []
        path.append(int(tid))
        species = next((a for a in reversed(path) if rank.get(a) == SPECIES_RANK), 0)
        sp[tid] = species
        grp[tid] = next((code_of[a] for a in path if a in code_of), 0)
    return sp, grp


def read_records(obs_path, sp, grp, lat5):
    """Stream the export into compact per-record arrays on the 5 km lattice.

    Returns a dict of equal-length arrays: species, group, row/col (5 km), observer, day and
    acc (positional accuracy in metres, NaN when unknown).
    """
    to_lattice = Transformer.from_crs("EPSG:4326", lat5.crs, always_xy=True)
    cols = [
        "observer_id",
        "latitude",
        "longitude",
        "positional_accuracy",
        "taxon_id",
        "quality_grade",
        "observed_on",
    ]
    parts = []
    reader = pd.read_csv(
        obs_path,
        sep="\t",
        usecols=cols,
        chunksize=CHUNK,
        dtype={"quality_grade": "category", "observed_on": "string"},
    )
    for i, df in enumerate(reader):
        ok = (
            (df.quality_grade == "research")
            & df.observed_on.notna()
            & df.taxon_id.notna()
            & df.longitude.between(BBOX[0], BBOX[2])
            & df.latitude.between(BBOX[1], BBOX[3])
        )
        df = df[ok]
        tid = df.taxon_id.to_numpy(np.int64)
        tid = np.where(tid < len(sp), tid, 0)
        species = sp[tid]
        keep = species > 0
        df, species, tid = df[keep], species[keep], tid[keep]
        x, y = to_lattice.transform(df.longitude.to_numpy(), df.latitude.to_numpy())
        col = np.floor((x - lat5.x0) / lat5.res).astype(np.int64)
        row = np.floor((lat5.y1 - y) / lat5.res).astype(np.int64)
        inside = (row >= 0) & (row < lat5.nrow) & (col >= 0) & (col < lat5.ncol)
        day = pd.to_datetime(df.observed_on, errors="coerce", format="%Y-%m-%d")
        inside &= day.notna().to_numpy()
        parts.append(
            {
                "species": species[inside].astype(np.int32),
                "group": grp[tid][inside],
                "row": row[inside].astype(np.int32),
                "col": col[inside].astype(np.int32),
                "observer": df.observer_id.to_numpy(np.int64)[inside].astype(np.int32),
                "day": (day[inside].to_numpy().astype("datetime64[D]").astype(np.int64)).astype(np.int32),
                "acc": df.positional_accuracy.to_numpy(float)[inside].astype(np.float32),
            }
        )
        print(f"chunk {i}: kept {inside.sum():,} records", flush=True)
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def completeness_by_cell(cell, observer, day, species):
    """Per-cell m, S_obs, Q1, Q2 from one row per record. Duplicate records collapse.

    Returns (cells, stats): stats maps m, s_obs, q1, q2, u (total incidences) and the iNEXT fallback
    sums e1, e2, qe1 to arrays with one entry per cell that has any record.
    """
    unit = pd.DataFrame({"cell": cell, "observer": observer, "day": day, "species": species})
    unit = unit.drop_duplicates()
    m = unit.drop_duplicates(["cell", "observer", "day"]).groupby("cell").size()
    q = unit.groupby(["cell", "species"]).size()  # units per species in the cell
    eq = np.exp(-q.astype(float))
    per = pd.DataFrame(
        {
            "s_obs": q.groupby(level="cell").size(),
            "q1": (q == 1).groupby(level="cell").sum(),
            "q2": (q == 2).groupby(level="cell").sum(),
            "u": q.groupby(level="cell").sum(),
            "e1": eq.groupby(level="cell").sum(),
            "e2": (eq**2).groupby(level="cell").sum(),
            "qe1": (q * eq).groupby(level="cell").sum(),
        }
    )
    per["m"] = m
    return per.index.to_numpy(), {k: per[k].to_numpy() for k in per}


def encode(lat, cells, completeness, m, hidden):
    """RGBA pixels in lattice index space, as described in the module docstring."""
    rgba = np.zeros((lat.nrow * lat.ncol, 4), np.uint8)
    rgba[:, 3] = NO_RECORDS_ALPHA
    units = np.minimum(m, 65535).astype(np.int64)
    rgba[cells, 0] = _q250(completeness)
    rgba[cells, 1] = units // 256
    rgba[cells, 2] = units % 256
    rgba[cells, 3] = 255
    rgba = rgba.reshape(lat.nrow, lat.ncol, 4)
    rgba[hidden] = 0
    return rgba


def encode_ci(lat, cells, comp_lo, comp_hi, cov, hidden):
    """The popup's _ci pixels: completeness interval and sample coverage, see the module docstring."""
    rgba = np.zeros((lat.nrow * lat.ncol, 4), np.uint8)
    for ch, v in enumerate((comp_lo, comp_hi, cov)):
        rgba[cells, ch] = _q250(v)
    rgba[cells, 3] = 255
    rgba = rgba.reshape(lat.nrow, lat.ncol, 4)
    rgba[hidden] = 0
    return rgba


def _q250(v):
    return np.round(np.clip(np.nan_to_num(v, nan=0.0), 0, 1) * 250).astype(np.uint8)


def decode(rgba):
    """Inverse of encode for a pixel array: (completeness, observer-days, present, canadian)."""
    rgba = np.asarray(rgba, np.int64)
    return rgba[..., 0] / 250, rgba[..., 1] * 256 + rgba[..., 2], rgba[..., 3] == 255, rgba[..., 3] > 0


def status_counts(completeness, m):
    scored = m >= MIN_UNITS
    return {
        "complete": int((scored & (completeness >= BANDS["complete"])).sum()),
        "close": int((scored & (completeness >= BANDS["close"]) & (completeness < BANDS["complete"])).sum()),
        "far": int((scored & (completeness < BANDS["close"])).sum()),
        "too_few": int((~scored).sum()),
    }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build(rec, crs, source, out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    tiers = {}
    for res in TIERS:
        lat = grid_lattice.Lattice(res, crs)
        k = res // 5000
        cell = (rec["row"] // k).astype(np.int64) * lat.ncol + rec["col"] // k
        precise = ~(rec["acc"] > res)  # NaN (unknown) passes
        hidden = border_mask.hidden_for_tier(lat.nrow, lat.ncol, lat.res, lat.crs, lat.x0, lat.y1)
        groups = {}
        for name, cog in GROUP_TO_COG.items():
            sel = precise if name not in ICONIC else precise & (rec["group"] == GROUP_CODE[name])
            cells, st = completeness_by_cell(
                cell[sel], rec["observer"][sel], rec["day"][sel], rec["species"][sel]
            )
            m, s_obs, q1, q2 = st["m"], st["s_obs"], st["q1"], st["q2"]
            # Quantised as in the pixel, so these counts and the app's statuses agree at band edges.
            comp = np.round(s_obs / chao2(s_obs, q1, q2, m) * 250) / 250
            lo, hi = chao2_interval(s_obs, q1, q2, m, st["e1"], st["e2"], st["qe1"])
            canadian = ~hidden.reshape(-1)[cells]
            png, ci_png = f"checklist_{res}m_{cog}.png", f"checklist_{res}m_{cog}_ci.png"
            matplotlib.image.imsave(os.path.join(out_dir, png), encode(lat, cells, comp, m, hidden))
            matplotlib.image.imsave(
                os.path.join(out_dir, ci_png),
                encode_ci(lat, cells, s_obs / hi, s_obs / lo, coverage(q1, q2, st["u"], m), hidden),
            )
            counts = status_counts(comp[canadian], m[canadian])
            counts["no_records"] = int((~hidden).sum()) - int(canadian.sum())
            groups[name] = {
                "png": png,
                "sha256": sha256_file(os.path.join(out_dir, png)),
                "ci_png": ci_png,
                "ci_sha256": sha256_file(os.path.join(out_dir, ci_png)),
                "cells": counts,
            }
            print(f"{res} m {name}: {groups[name]['cells']}", flush=True)
        tiers[str(res)] = {
            "lattice": {"x0": lat.x0, "y1": lat.y1, "res_m": lat.res, "ncol": lat.ncol, "nrow": lat.nrow},
            "max_accuracy_m": res,
            "groups": groups,
        }
    doc = {
        "schema_version": 2,
        "kind": "checklist-completeness",
        "method": "Chao2 incidence estimate per cell as in iNEXT 3.0.2, with its 95% interval; "
        "sampling unit = one observer on one day; completeness = S_obs / S_est; sample coverage "
        "per Chao & Jost 2012",
        "min_units": MIN_UNITS,
        "bands": BANDS,
        "rule_of_thumb_units": RULE_OF_THUMB_UNITS,
        "encoding": "R=round(completeness*250); G*256+B=observer-days (cap 65535); "
        "A=255 Canadian cell with records, 64 Canadian cell without, 0 outside Canada",
        "ci_encoding": "R=round(completeness lower 95% bound*250); G=upper bound; "
        "B=round(sample coverage*250); A=255 where the group has records",
        "crs": crs.to_wkt(),
        "source": source,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tiers": tiers,
    }
    with open(os.path.join(out_dir, "checklist.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--observations", required=True, help="iNaturalist open-data observations.csv.gz")
    ap.add_argument("--taxa", required=True, help="iNaturalist open-data taxa.csv.gz")
    ap.add_argument(
        "--export-date",
        required=True,
        help="Last-Modified date of the export, YYYY-MM-DD, recorded as its vintage",
    )
    args = ap.parse_args()
    for p in (args.observations, args.taxa):
        if not os.path.exists(p):
            raise SystemExit(f"missing input {p}")
    with open(GRID_SIDECAR) as fh:
        crs = CRS.from_wkt(json.load(fh)["tiers"]["5000"]["lattice"]["crs"])
    taxa = pd.read_csv(args.taxa, sep="\t", usecols=["taxon_id", "ancestry", "rank_level"])
    sp, grp = species_lookup(taxa)
    rec = read_records(args.observations, sp, grp, grid_lattice.Lattice(5000, crs))
    source = {
        "name": "iNaturalist open data (inaturalist-open-data S3 export)",
        "url": "https://inaturalist-open-data.s3.amazonaws.com/observations.csv.gz",
        "export_date": args.export_date,
        "records_used": int(len(rec["species"])),
        "filters": "research grade; species rank or below, rolled up to species; dated; "
        "positional accuracy unknown or <= cell size",
    }
    build(rec, crs, source)


if __name__ == "__main__":
    main()
