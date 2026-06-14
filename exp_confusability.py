"""Does visual confusability predict identification latency? (BioCLIP -> bottleneck)

Hypothesis (the in-app "easy win vs needs-a-specialist" flag): a record whose
subject is visually confusable with other species takes longer to verify. If
true, BioCLIP embeddings can pre-sort a cell's gap candidates by how hard they
will be to ID, relieving the expert-identifier bottleneck (exp_idlatency.py).

Bar: a confusability score has Spearman > 0 with wait time / unengaged-ness on
held-out needs-ID records. Refute if not.

Two parts:
  PILOT (runs now, no embeddings): rank-coarseness proxy. A needs-ID record whose
    community taxon is stuck above species (genus/family/order) is one the crowd
    could not pin down — a cheap, embedding-free hardness proxy. Test whether
    coarser records wait longer and stay more unengaged. This is a PULSE CHECK,
    explicitly NOT the BioCLIP claim (rank-coarseness != visual confusability),
    but if even this is null the embedding run is unpromising.
  SCAFFOLD (cluster/GPU): the real test. Embed needs-ID record photos with
    BioCLIP (reuse exp_discovery_offline.embed_images), score each stuck record's
    confusability = 1 - cosine distance to its nearest already-research-grade
    neighbour of a DIFFERENT species in the same neighbourhood, and correlate
    with wait_days. Gated behind open_clip availability; prints how to run on the
    cluster when deps are absent (torch/open_clip not installed locally).
"""
import sys, glob, json
import numpy as np
import pandas as pd

REF_DATE = pd.Timestamp("2026-06-14", tz="UTC")
# coarser community taxon == harder to pin down. depth: species/finer = high.
RANK_DEPTH = {"subspecies": 7, "variety": 7, "form": 7, "hybrid": 6, "species": 6,
              "subgenus": 5, "genus": 5, "subfamily": 4, "family": 4, "superfamily": 4,
              "suborder": 3, "order": 3, "subclass": 2, "class": 2, "phylum": 1, "kingdom": 0}


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 12:
        return float("nan"), int(m.sum())
    ra = pd.Series(a[m]).rank().values
    rb = pd.Series(b[m]).rank().values
    if ra.std() == 0 or rb.std() == 0:
        return float("nan"), int(m.sum())
    return float(np.corrcoef(ra, rb)[0, 1]), int(m.sum())


def pilot(name):
    """Rank-coarseness hardness proxy vs wait/engagement on needs-ID records."""
    df = pd.read_csv(f"cluster_results/needsid_{name}.csv")
    df["created"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df["wait_days"] = (REF_DATE - df["created"]).dt.total_seconds() / 86400.0
    df["depth"] = df["rank"].map(RANK_DEPTH)
    df["coarseness"] = -df["depth"]                       # higher = coarser = harder
    df["unengaged"] = (df["ident_count"].fillna(0) <= 1).astype(float)
    df = df.dropna(subset=["coarseness", "wait_days"])
    rho_wait, n1 = spearman(df.coarseness, df.wait_days)
    rho_uneng, n2 = spearman(df.coarseness, df.unengaged)
    # fraction of the needs-ID pile stuck ABOVE species (could not be pinned down)
    above_species = float((df.depth < 6).mean())
    return dict(taxon=name, n=int(len(df)),
                frac_above_species=above_species,
                rho_coarseness_wait=rho_wait,
                rho_coarseness_unengaged=rho_uneng)


def embeddings_available():
    try:
        import open_clip  # noqa: F401
        import torch      # noqa: F401
        return True
    except Exception:
        return False


SCAFFOLD_MSG = """\
[SCAFFOLD] BioCLIP confusability test is CLUSTER-GATED (open_clip/torch not
installed locally). To run the real test on a GPU box:
  1. stage photos:  python exp_discovery_offline.py --stage-only --taxon <t> \\
       --project 228908 --image-cache /scratch/wtb/imgs --obs-cache obs_<t>.json
  2. embed + score: python exp_confusability.py --embed --taxon <t> \\
       --image-cache /scratch/wtb/imgs --obs-cache obs_<t>.json
The --embed path reuses exp_discovery_offline.embed_images(backbone='bioclip'),
computes per-record nearest-different-species cosine distance, and correlates
1-distance with wait_days from the needs-ID pull. Ground truth (wait_days,
unengaged) is already committed in cluster_results/needsid_*.csv."""


if __name__ == "__main__":
    if "--embed" in sys.argv and not embeddings_available():
        print(SCAFFOLD_MSG); sys.exit(0)
    taxa = [a for a in sys.argv[1:] if not a.startswith("--")] or \
        [f.split("needsid_")[-1].replace(".csv", "")
         for f in sorted(glob.glob("cluster_results/needsid_*.csv"))]
    results = []
    print("PILOT — rank-coarseness hardness proxy (NOT the BioCLIP claim):")
    for name in taxa:
        r = pilot(name)
        results.append(r)
        print(f"\n=== {name} === n={r['n']}  stuck-above-species={r['frac_above_species']*100:.0f}%")
        print(f"  rho(coarseness, wait_days)  ={r['rho_coarseness_wait']:+.3f}")
        print(f"  rho(coarseness, unengaged)  ={r['rho_coarseness_unengaged']:+.3f}  (>0 supports the hypothesis)")
    json.dump(results, open("cluster_results/confusability_pilot.json", "w"), indent=2)
    if not embeddings_available():
        print("\n" + SCAFFOLD_MSG)
    print(f"\nwrote cluster_results/confusability_pilot.json ({len(results)} taxa)")
