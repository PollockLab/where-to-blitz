"""Score live counting, the COVERS/SPILLS allowlist and the range clip on subspecies-identified records.

Pre-registered in research/GATE-range-clip.md ("Ship rule: range clip vs the allowlist"). A test record is a
record of a test species whose accepted taxon is a listed subspecies (true) or an unlisted Canadian one
(false), per research/subspecies_test_set.csv. The clip is scored without its infraspecific rule.

  .venv/bin/python research/allowlist_compare.py DOWNLOAD.zip
"""

import argparse
import csv
import re
import zipfile

import _rootpath  # puts the repo root on sys.path
import numpy as np

import atrisk_ranges as ar
import build_atrisk_layer as bal

TEST_SET = "research/subspecies_test_set.csv"
KEEP_UNCLEAR = ("Taxidea taxus", "Lanius ludovicianus", "Bos bison")  # Silene scouleri is dropped
METHODS = ("live", "allowlist", "clip", "clip_no_override")
MIN_N = 20
N_BOOT = 2000


def epithets(s):
    return {p.split(":")[0].strip() for p in (s or "").split(";") if p.strip()}


def load_test_set(path=TEST_SET):
    """-> {species name: {"names", "classes", "listed", "unlisted"}} pooled over the test names."""
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            s = out.setdefault(r["species"], {"names": set(), "classes": set(), "listed": set(), "unlisted": set()})
            s["names"].add(r["name"])
            s["classes"].add(r["class"])
            s["listed"] |= epithets(r["listed_ssp_counts"])
            s["unlisted"] |= epithets(r["unlisted_ssp_counts"])
    for s in out.values():
        s["unlisted"] -= s["listed"]
    return out


def resolve(species, eps, col_key):
    """Epithets -> accepted infraspecific COL XR keys under col_key (unresolved ones are reported)."""
    keys, miss = set(), []
    for ep in sorted(eps):
        k, _, _, infra = bal.species_match(f"{species} {ep}")
        (keys.add(infra) if infra and k == col_key else miss.append(ep))
    return keys, miss


def without_overrides(listings):
    out = []
    for r in listings:
        if r["range_source"] == "ecozone":
            r = dict(r, range_source="province",
                     range_ids=[f"prov:{p}" for p in sorted(re.split(r"[\s;,]+", r["provinces"].strip()))])  # fmt: skip
        out.append(r)
    return out


def counted(cols, entries, project, n_rows):
    """Rows (bool per download row) that some entry counts, with the infraspecific rule off."""
    hit = np.zeros(n_rows, dtype=bool)
    for e in entries:
        e = dict(e, infra=set())
        for _, _, acc, _ in ar.accepted_rows(cols, [e], project, bal.FINE):
            hit[acc] = True
    return hit


def summarize(label, per, species):
    t = sum(per[s]["true"] for s in species)
    f = sum(per[s]["false"] for s in species)
    print(f"{label}: {len(species)} species, true {t}, false {f}")
    for m in METHODS:
        tk = sum(per[s][m][0] for s in species)
        fk = sum(per[s][m][1] for s in species)
        print(f"  {m:17s} true kept {tk:6d} recall {tk / t if t else float('nan'):.3f}   "
              f"false counted {fk:6d} share {fk / f if f else float('nan'):.3f}")  # fmt: skip


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("download")
    a = ap.parse_args()
    with zipfile.ZipFile(a.download) as z, z.open(next(n for n in z.namelist() if not n.endswith("/"))) as f:
        cols = ar.read_columns(f)
    test = load_test_set()
    cw = bal.read_crosswalk()
    listings = ar.read_listings()
    geoms = ar.load_range_geoms()
    project = ar.transformer()
    sk, at, n = cols["specieskey"], cols["acceptedtaxonkey"], cols["n"]
    ratio = round(bal.RES / bal.FINE)
    cell = np.stack([cols["yi"] // ratio, cols["xi"] // ratio], axis=1)

    per, pairs = {}, {m: 0 for m in METHODS}
    for sp, t in sorted(test.items()):
        lst = [r for r in listings if t["names"] & set(r["names"])]
        keys = set()
        for nm in t["names"]:
            keys.update(cw.get(nm, ((), ""))[0])
        if not lst or not keys:
            print(f"skip {sp}: no matched test listing")
            continue
        col_key = cw[next(nm for nm in t["names"] if nm in cw and cw[nm][0])][0][0]
        tk, tmiss = resolve(sp, t["listed"], col_key)
        fk, fmiss = resolve(sp, t["unlisted"], col_key)
        if tmiss or fmiss:
            print(f"  {sp}: unresolved listed {tmiss} unlisted {fmiss}")
        rows = np.isin(sk, sorted(keys))
        is_t, is_f = rows & np.isin(at, sorted(tk)), rows & np.isin(at, sorted(fk))
        hits = {
            "live": rows,
            "allowlist": rows if ("COVERS" in t["classes"] or sp in KEEP_UNCLEAR) else np.zeros_like(rows),
            "clip": counted(cols, ar.listing_entries(lst, cw, geoms), project, len(sk)),
            "clip_no_override": counted(cols, ar.listing_entries(without_overrides(lst), cw, geoms), project, len(sk)),
        }  # fmt: skip
        per[sp] = {"true": int(n[is_t].sum()), "false": int(n[is_f].sum()), "classes": t["classes"]}
        per[sp].update({m: (int(n[h & is_t].sum()), int(n[h & is_f].sum())) for m, h in hits.items()})
        for m in ("clip", "clip_no_override"):
            inc = hits[m] & ~hits["allowlist"]
            per[sp][f"inc_{m}"] = (int(n[inc & is_t].sum()), int(n[inc & is_f].sum()))
        true_cells = {tuple(c) for c in cell[is_t]}
        false_only = {tuple(c) for c in cell[is_f]} - true_cells
        for m, h in hits.items():
            pairs[m] += len(lst) * len({tuple(c) for c in cell[h & rows]} & false_only)

    have = [s for s in per if per[s]["true"] + per[s]["false"] > 0]
    summarize("Pooled", per, have)
    summarize("Pooled without UNCLEAR", per, [s for s in have if "UNCLEAR" not in per[s]["classes"]])
    print(f"Per species with n >= {MIN_N} (true, false; then kept true/false per method)")
    for s in have:
        if per[s]["true"] + per[s]["false"] >= MIN_N:
            print(f"  {s} [{'/'.join(sorted(per[s]['classes']))}] {per[s]['true']}, {per[s]['false']}: "
                  + "  ".join(f"{m} {per[s][m][0]}/{per[s][m][1]}" for m in METHODS))  # fmt: skip
    print("(listing, cell) pairs weighted with only unlisted-subspecies evidence: "
          + ", ".join(f"{m} {v}" for m, v in pairs.items()))  # fmt: skip

    rng = np.random.default_rng(0)
    res = {}
    for label, m, keep in (
        ("3a clip", "clip", have),
        ("3b clip without overrides", "clip_no_override", have),
        ("3a clip without UNCLEAR", "clip", [s for s in have if "UNCLEAR" not in per[s]["classes"]]),
    ):
        res[label] = incremental(label, per, keep, f"inc_{m}", rng)
    print(f"Per species incremental true share (3a), n >= {MIN_N}; FLAG = below 0.5")
    for s in have:
        t, f = per[s]["inc_clip"]
        if t + f >= MIN_N:
            print(f"  {s} [{'/'.join(sorted(per[s]['classes']))}] {t}/{t + f} = {t / (t + f):.3f}"
                  f"{'  FLAG' if t / (t + f) < 0.5 else ''}")  # fmt: skip
    ok, thin = res["3a clip"]
    print("Ship rule: " + ("TOO THIN (< 5 species with incremental records), allowlist ships" if thin
                           else "range clip ships" if ok else "allowlist ships"))  # fmt: skip


def incremental(label, per, species, key, rng):
    """Pooled true share of the incremental records with a species bootstrap CI -> (ships, too thin)."""
    sp = [s for s in species if sum(per[s][key])]
    t = np.array([per[s][key][0] for s in sp], dtype=float)
    f = np.array([per[s][key][1] for s in sp], dtype=float)
    if not sp:
        print(f"{label}: no incremental test records")
        return False, True
    boot = rng.integers(0, len(sp), size=(N_BOOT, len(sp)))
    share = t[boot].sum(axis=1) / (t[boot].sum(axis=1) + f[boot].sum(axis=1))
    lo, hi = np.percentile(share, [2.5, 97.5])
    print(f"{label}: incremental true {int(t.sum())} false {int(f.sum())} share {t.sum() / (t.sum() + f.sum()):.3f} "
          f"CI95 [{lo:.3f}, {hi:.3f}] over {len(sp)} species")  # fmt: skip
    return lo > 0.5, len(sp) < 5

if __name__ == "__main__":
    main()
