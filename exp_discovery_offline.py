"""BTG species-discovery experiment — offline version (design-04's no-free-lunch test).

Question: does embedding-based acquisition beat simple spatial coverage at
discovering distinct amphibian species (iNaturalist 228908, real Canada data)?

Modes
-----
- Staging (--stage-only): pull observations + download photos to --image-cache,
  save obs JSON, then exit. Run on a login node (needs internet).
- Run (--obs-cache <json> [--image-cache <dir>]): load saved obs + cached images,
  embed (GPU or CPU), simulate active discovery under several acquisition
  strategies, report discovery curves with paired significance tests.

Embeddings are cached to <image-cache>/emb_<backbone>.npz, so re-running the
strategy sweep (the cheap part) needs neither a GPU nor the images — only numpy.

Strategies
----------
- random              : uniform sampling (the floor).
- spatial_coverage    : greedy k-center in geographic space (haversine distance).
- embedding_novelty   : greedy k-center in embedding space (= CoreSet; outlier-prone).
- embedding_kmeanspp  : D^2-weighted probabilistic coverage (robust to outliers).
- combined            : z-scored spatial + embedding min-distance (the multi-axis app).

Every claim is a measured number with seeds and a paired permutation test.
"""
from __future__ import annotations
import argparse, io, json, os, time, urllib.request
from collections import defaultdict
from pathlib import Path
import numpy as np

INAT = "https://api.inaturalist.org/v1/observations"
PROJECT = 228908


def pull_observations(n_target=1500, per_page=200, taxon="Amphibia", project=PROJECT):
    params = {"quality_grade": "research", "iconic_taxa": taxon,
              "per_page": per_page, "order_by": "id", "order": "desc",
              "photos": "true", "identified": "true",
              "swlat": 41, "swlng": -141, "nelat": 84, "nelng": -52}
    if project:  # project 0/None => Canada-bbox only (for cross-taxon generalization)
        params["project_id"] = project
    import requests
    out, id_below = [], None
    while len(out) < n_target:
        p = dict(params)
        if id_below:
            p["id_below"] = id_below
        d = requests.get(INAT, params=p, timeout=60).json()
        res = d.get("results", [])
        if not res:
            break
        for o in res:
            taxon = o.get("taxon") or {}
            photos = o.get("photos") or []
            g = o.get("geojson") or {}
            if taxon.get("rank") == "species" and photos and g.get("coordinates"):
                url = photos[0].get("url", "").replace("/square.", "/small.")
                if url:
                    out.append({"id": o["id"], "species": taxon["name"],
                                "lat": g["coordinates"][1], "lon": g["coordinates"][0],
                                "photo": url})
        id_below = res[-1]["id"]
        if len(res) < per_page:
            break
    return out[:n_target]


def stage_images(records, cache_dir: Path):
    """Download all photos to cache_dir. Returns records with 'local_path' set."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for i, rec in enumerate(records):
        local = cache_dir / f"{rec['id']}.jpg"
        rec["local_path"] = str(local)
        if local.exists():
            ok += 1
            continue
        try:
            with urllib.request.urlopen(rec["photo"], timeout=30) as r:
                data = r.read()
            local.write_bytes(data)
            ok += 1
        except Exception as e:
            print(f"  [stage] SKIP {rec['id']}: {e}")
        if (i + 1) % 100 == 0:
            print(f"  staged {i+1}/{len(records)} ({ok} ok)")
    print(f"[stage] done: {ok}/{len(records)} images cached to {cache_dir}")
    return records


def load_image_from_disk(path: str):
    from PIL import Image
    return Image.open(path).convert("RGB")


def fetch_image_from_url(url: str):
    from PIL import Image
    with urllib.request.urlopen(url, timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


# ---- backbones -------------------------------------------------------------
def _load_dinov2(device):
    import torch, torchvision.transforms as T
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(device).eval()
    tf = T.Compose([T.Resize(224), T.CenterCrop(224), T.ToTensor(),
                    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    return model, tf, "dinov2_vits14"

def _load_resnet50(device):
    import torch, torchvision
    from torchvision.models import ResNet50_Weights
    w = ResNet50_Weights.IMAGENET1K_V2
    m = torchvision.models.resnet50(weights=w); m.fc = torch.nn.Identity()
    return m.to(device).eval(), w.transforms(), "resnet50_imagenet"

def _load_clip(device):
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k")
    model = model.to(device).eval()
    class _Vis:
        def __call__(self, x):
            return model.encode_image(x)
    return _Vis(), preprocess, "clip_vit_b32"

def _load_bioclip(device):
    # Domain-specific embedding: BioCLIP, trained on the TreeOfLife-10M species images.
    # Tests the No-Free-Lunch prediction that a taxon-aware embedding helps more.
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms("hf-hub:imageomics/bioclip")
    model = model.to(device).eval()
    class _Vis:
        def __call__(self, x):
            return model.encode_image(x)
    return _Vis(), preprocess, "bioclip"


def embed_images(records, device, want="auto", batch=64, use_cache=True):
    """Vision embeddings. Loads images from local_path if present, else fetches URL."""
    import torch
    order = {"auto": ["dinov2", "resnet50"], "dinov2": ["dinov2"],
             "resnet50": ["resnet50"], "clip": ["clip", "resnet50"],
             "bioclip": ["bioclip"]}[want]
    loaders = {"dinov2": _load_dinov2, "resnet50": _load_resnet50,
               "clip": _load_clip, "bioclip": _load_bioclip}
    model = tf = backbone = None
    for name in order:
        try:
            model, tf, backbone = loaders[name](device)
            break
        except Exception as e:
            print(f"[embed] {name} unavailable ({e}); trying next")
    if model is None:
        raise RuntimeError("no usable backbone")

    def encode(batch_t):
        return model(batch_t)

    embs, keep = [], []
    buf, idxs = [], []

    def flush():
        if not buf:
            return
        with torch.no_grad():
            x = torch.stack(buf).to(device)
            z = encode(x).float().cpu().numpy()
        embs.append(z); keep.extend(idxs)
        buf.clear(); idxs.clear()

    for i, rec in enumerate(records):
        try:
            local = rec.get("local_path", "")
            if use_cache and local and os.path.exists(local):
                img = load_image_from_disk(local)
            else:
                img = fetch_image_from_url(rec["photo"])
            buf.append(tf(img)); idxs.append(i)
        except Exception as exc:
            print(f"  [embed] skip {i}: {exc}")
            continue
        if len(buf) >= batch:
            flush()
        if (i + 1) % 200 == 0:
            print(f"  embedded {i+1}/{len(records)}...")
    flush()
    E = np.concatenate(embs, 0) if embs else np.zeros((0, 1))
    E /= (np.linalg.norm(E, axis=1, keepdims=True) + 1e-8)
    return E, keep, backbone


# resolved backbone name per --backbone choice (cache files are keyed by this).
ARG_TO_BACKBONE = {"dinov2": "dinov2_vits14", "resnet50": "resnet50_imagenet",
                   "clip": "clip_vit_b32", "bioclip": "bioclip"}

# ---- discovery simulation --------------------------------------------------
STRATEGIES = ("random", "spatial_coverage", "spatial_coverage_raw",
              "embedding_novelty", "embedding_kmeanspp", "combined", "combined_raw")


def _haversine_to(lat_rad, lon_rad, j):
    """Great-circle angular distance from point j to all points (radians ×R=1)."""
    dlat = lat_rad - lat_rad[j]
    dlon = lon_rad - lon_rad[j]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat_rad) * np.cos(lat_rad[j]) * np.sin(dlon / 2) ** 2
    return 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _zscore_masked(x, mask):
    """z-score values of x over the masked (candidate) subset; non-candidates -> -inf."""
    vals = x[mask]
    mu = vals.mean()
    sd = vals.std()
    z = (x - mu) / sd if sd > 1e-12 else np.zeros_like(x)
    out = np.full_like(x, -np.inf)
    out[mask] = z[mask]
    return out


def _raw_latlon_to(lat, lon, j):
    """OLD (distorted) baseline: raw lat/lon Euclidean in degrees. Kept only as a
    control to isolate the effect of the geographic-distance fix on identical data."""
    return np.sqrt((lat - lat[j]) ** 2 + (lon - lon[j]) ** 2)


def run_discovery(kind, lat, lon, E, species_ids, seed, budget):
    """One discovery run. Returns the species-accumulation curve (len = #sampled).

    ``lat``/``lon`` are degrees. Incremental O(budget*n): maintain running min-distance
    to the seen set rather than recomputing the full pairwise matrix at every step. For a
    given seed the start point is the first RNG draw -> identical across strategies, so the
    per-seed species@budget values are *paired* across strategies (valid paired tests).
    """
    lat_rad = np.radians(lat); lon_rad = np.radians(lon)
    rng = np.random.default_rng(seed)
    n = len(species_ids)
    NEG = -np.inf
    start = int(rng.integers(n))
    seen = np.zeros(n, dtype=bool); seen[start] = True
    seen_species = {species_ids[start]}
    curve = [1]

    need_geo = kind in ("spatial_coverage", "combined")
    need_geo_raw = kind in ("spatial_coverage_raw", "combined_raw")
    need_emb = kind in ("embedding_novelty", "embedding_kmeanspp", "combined", "combined_raw")
    mind_geo = _haversine_to(lat_rad, lon_rad, start) if need_geo else None
    mind_geo_raw = _raw_latlon_to(lat, lon, start) if need_geo_raw else None
    mind_emb = (1.0 - E @ E[start]) if need_emb else None  # cosine distance to seen

    budget = min(budget, n)
    while len(seen_species) < budget and seen.sum() < n and len(curve) < budget:
        cand = ~seen
        if kind == "random":
            nxt = int(rng.choice(np.flatnonzero(cand)))
        elif kind == "spatial_coverage":
            score = np.where(cand, mind_geo, NEG); nxt = int(np.argmax(score))
        elif kind == "spatial_coverage_raw":       # control: distorted lat/lon metric
            score = np.where(cand, mind_geo_raw, NEG); nxt = int(np.argmax(score))
        elif kind == "embedding_novelty":          # greedy k-center / CoreSet
            score = np.where(cand, mind_emb, NEG); nxt = int(np.argmax(score))
        elif kind == "embedding_kmeanspp":         # D^2-weighted probabilistic coverage
            w = np.where(cand, mind_emb ** 2, 0.0)
            s = w.sum()
            nxt = (int(rng.choice(n, p=w / s)) if s > 0
                   else int(rng.choice(np.flatnonzero(cand))))
        elif kind == "combined":                   # z-scored haversine spatial + embedding
            score = _zscore_masked(mind_geo, cand) + _zscore_masked(mind_emb, cand)
            nxt = int(np.argmax(score))
        elif kind == "combined_raw":                # z-scored raw-lat/lon spatial + embedding
            score = _zscore_masked(mind_geo_raw, cand) + _zscore_masked(mind_emb, cand)
            nxt = int(np.argmax(score))
        else:
            raise ValueError(kind)

        seen[nxt] = True
        seen_species.add(species_ids[nxt])
        curve.append(len(seen_species))
        if need_geo:
            mind_geo = np.minimum(mind_geo, _haversine_to(lat_rad, lon_rad, nxt))
        if need_geo_raw:
            mind_geo_raw = np.minimum(mind_geo_raw, _raw_latlon_to(lat, lon, nxt))
        if need_emb:
            mind_emb = np.minimum(mind_emb, 1.0 - E @ E[nxt])
    return curve


def auc(curve, budget):
    c = np.array(curve, float)
    if len(c) < budget:
        c = np.concatenate([c, np.full(budget - len(c), c[-1])])
    return float(c[:budget].mean())


def paired_permutation_p(a, b, n_perm=20000, seed=0):
    """Two-sided paired sign-flip permutation test on mean(a-b). Pure numpy."""
    d = np.asarray(a, float) - np.asarray(b, float)
    obs = abs(d.mean())
    if obs == 0 or np.allclose(d, 0):
        return 1.0
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(d)))
    perm = np.abs((signs * d).mean(axis=1))
    return float((perm >= obs - 1e-12).mean())


def bootstrap_ci(a, b, n_boot=20000, seed=0):
    """95% bootstrap CI for mean(a-b)."""
    d = np.asarray(a, float) - np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    means = d[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def contrast(name_a, name_b, per_seed):
    """Paired comparison a vs b on species@budget across seeds."""
    a, b = per_seed[name_a], per_seed[name_b]
    d = np.asarray(a, float) - np.asarray(b, float)
    lo, hi = bootstrap_ci(a, b)
    out = {"a": name_a, "b": name_b, "mean_diff": float(d.mean()),
           "ci95": [lo, hi], "p_perm": paired_permutation_p(a, b),
           "wins": int((d > 0).sum()), "ties": int((d == 0).sum()),
           "losses": int((d < 0).sum()), "n_seeds": len(d)}
    try:
        from scipy.stats import wilcoxon
        if np.any(d != 0):
            out["p_wilcoxon"] = float(wilcoxon(a, b).pvalue)
    except Exception:
        pass
    return out


def emb_cache_path(cache_dir, backbone, taxon="Amphibia"):
    if not cache_dir:
        return None
    tag = "" if taxon == "Amphibia" else f"{taxon}_"  # keep amphibian caches back-compatible
    return Path(cache_dir) / f"emb_{tag}{backbone}.npz"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--budget", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--backbone", default="auto",
                    choices=["auto", "dinov2", "resnet50", "clip", "bioclip"])
    ap.add_argument("--taxon", default="Amphibia",
                    help="iconic_taxa filter (e.g. Amphibia, Reptilia, Aves) for generalization.")
    ap.add_argument("--project", type=int, default=PROJECT,
                    help="iNat project_id; 0 = Canada-bbox only (cross-taxon generalization).")
    ap.add_argument("--out", default=".")
    ap.add_argument("--image-cache", default=None,
                    help="Dir to read/write cached images + embeddings (offline mode).")
    ap.add_argument("--obs-cache", default=None,
                    help="Path to pre-saved observations JSON (from --stage-only run).")
    ap.add_argument("--stage-only", action="store_true",
                    help="Download obs+photos to --image-cache, save JSON, then exit.")
    ap.add_argument("--no-emb-cache", action="store_true",
                    help="Recompute embeddings even if a cache exists.")
    args = ap.parse_args()

    cache_dir = Path(args.image_cache) if args.image_cache else None
    obs_json = Path(args.obs_cache) if args.obs_cache else None

    # STAGE MODE: login node, needs internet
    if args.stage_only:
        print("[stage] pulling observations from iNaturalist...")
        recs = pull_observations(args.n, taxon=args.taxon, project=args.project)
        print(f"[stage] got {len(recs)} obs ({len(set(r['species'] for r in recs))} species)")
        assert cache_dir, "--image-cache required for --stage-only"
        recs = stage_images(recs, cache_dir)
        out_json = cache_dir / "observations.json"
        with open(out_json, "w") as f:
            json.dump(recs, f)
        print(f"[stage] saved {out_json}")
        return

    t0 = time.time()

    # ---- obtain embeddings (from cache, or compute) ----
    # cache files are keyed by the *resolved* backbone name (e.g. dinov2_vits14),
    # so map the --backbone arg to that name for the lookup.
    resolved = ARG_TO_BACKBONE.get(args.backbone) if args.backbone != "auto" else None
    cache_npz = emb_cache_path(cache_dir, resolved, args.taxon) if resolved else None
    device = "cpu"
    gpu_name = "cpu"
    emb_device = "cpu"

    if cache_npz and cache_npz.exists() and not args.no_emb_cache:
        print(f"[run] loading cached embeddings from {cache_npz}")
        z = np.load(cache_npz)  # self-written cache; plain arrays only, no pickle
        E = z["E"]; species = list(z["species"]); lat = z["lat"]; lon = z["lon"]
        backbone = str(z["backbone"])
        emb_device = str(z["emb_device"]) if "emb_device" in z.files else "cached"
    else:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        emb_device = gpu_name  # where embeddings were actually computed
        print(f"device={device} cuda={torch.cuda.is_available()} name={gpu_name}")
        if obs_json and obs_json.exists():
            print(f"[run] loading obs from {obs_json}")
            with open(obs_json) as f:
                recs = json.load(f)
        else:
            print(f"[run] no obs cache, pulling {args.taxon} from iNat (need internet)...")
            recs = pull_observations(args.n, taxon=args.taxon, project=args.project)
        print(f"pulled {len(recs)} {args.taxon} obs w/ photo+species, "
              f"{len(set(r['species'] for r in recs))} distinct species")
        use_cache = cache_dir is not None
        E, keep, backbone = embed_images(recs, device, want=args.backbone, use_cache=use_cache)
        recs = [recs[i] for i in keep]
        species = [r["species"] for r in recs]
        lat = np.array([r["lat"] for r in recs], float)
        lon = np.array([r["lon"] for r in recs], float)
        print(f"embedded {len(recs)} images with {backbone}, dim={E.shape[1]} "
              f"in {time.time()-t0:.0f}s")
        out_npz = emb_cache_path(cache_dir, backbone, args.taxon)
        if out_npz is not None:
            out_npz.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(out_npz, E=E.astype(np.float32), species=np.array(species),
                                lat=lat, lon=lon, backbone=backbone, emb_device=gpu_name)
            print(f"[cache] wrote embeddings -> {out_npz}")

    species = list(species)
    lat = np.asarray(lat, float); lon = np.asarray(lon, float)
    n_obs = len(species)
    n_species = len(set(species))
    budget = min(args.budget, n_obs)
    print(f"sweep: n_obs={n_obs} n_species={n_species} budget={budget} "
          f"seeds={args.seeds} backbone={backbone}")

    # ---- strategy sweep (pure numpy) ----
    results = {}
    curves_mean = {}
    per_seed_last = {}   # species@budget per seed, paired across strategies by seed
    for name in STRATEGIES:
        aucs, last, all_curves = [], [], []
        for s in range(args.seeds):
            c = run_discovery(name, lat, lon, E, species, seed=s, budget=budget)
            aucs.append(auc(c, budget)); last.append(c[-1])
            padded = np.array(c + [c[-1]] * (budget - len(c)))[:budget]
            all_curves.append(padded)
        per_seed_last[name] = last
        results[name] = {"auc_mean": float(np.mean(aucs)), "auc_std": float(np.std(aucs)),
                         "species_at_budget_mean": float(np.mean(last)),
                         "species_at_budget_std": float(np.std(last)),
                         "species_at_budget_per_seed": [int(x) for x in last]}
        curves_mean[name] = np.mean(all_curves, 0).tolist()
        print(f"  {name:18s} AUC={results[name]['auc_mean']:.1f}±{results[name]['auc_std']:.1f} "
              f"species@{budget}={results[name]['species_at_budget_mean']:.2f}"
              f"±{results[name]['species_at_budget_std']:.2f}")

    # ---- paired significance: the design-04 no-free-lunch verdict ----
    emb_strats = ["embedding_novelty", "embedding_kmeanspp"]
    best_emb = max(emb_strats, key=lambda k: results[k]["species_at_budget_mean"])
    # The honest baseline is the *best* simple spatial strategy, not whichever metric
    # flatters the embedding. On a wide-longitude region the distorted raw lat/lon often
    # out-discovers haversine (it over-weights longitude, capturing E-W species turnover),
    # so compare embedding/combined against whichever spatial metric scores higher.
    spatial_strats = ["spatial_coverage", "spatial_coverage_raw"]
    best_spatial = max(spatial_strats, key=lambda k: results[k]["species_at_budget_mean"])
    contrasts = {
        "coverage_vs_random": contrast("spatial_coverage", "random", per_seed_last),
        "best_embedding_vs_coverage": contrast(best_emb, "spatial_coverage", per_seed_last),
        "combined_vs_coverage": contrast("combined", "spatial_coverage", per_seed_last),
        "kmeanspp_vs_kcenter": contrast("embedding_kmeanspp", "embedding_novelty", per_seed_last),
        # control on IDENTICAL data: does fixing the distorted lat/lon metric change coverage?
        "haversine_vs_raw_spatial": contrast("spatial_coverage", "spatial_coverage_raw", per_seed_last),
        # the load-bearing comparison: best embedding / combined vs the BEST simple spatial baseline
        "best_embedding_vs_best_spatial": contrast(best_emb, best_spatial, per_seed_last),
        "combined_vs_best_spatial": contrast("combined", best_spatial, per_seed_last),
        # does combining the embedding with the BETTER (raw/longitude-weighted) geo metric win overall?
        "combined_raw_vs_best_spatial": contrast("combined_raw", best_spatial, per_seed_last),
        "combined_raw_vs_combined": contrast("combined_raw", "combined", per_seed_last),
    }

    def verdict_line(label, c):
        sig = "SIGNIFICANT" if c["p_perm"] < 0.05 else "n.s."
        sign = "BEATS" if c["mean_diff"] > 0 else "trails" if c["mean_diff"] < 0 else "ties"
        return (f"{label}: {c['a']} {sign} {c['b']} by {c['mean_diff']:+.2f} sp "
                f"(95% CI [{c['ci95'][0]:+.2f},{c['ci95'][1]:+.2f}], p_perm={c['p_perm']:.3f} {sig}; "
                f"W/T/L {c['wins']}/{c['ties']}/{c['losses']})")

    bs = results[best_spatial]["species_at_budget_mean"]
    def beats(key):
        c = contrasts[key]
        return c["mean_diff"] > 0 and c["p_perm"] < 0.05
    emb_wins = beats("best_embedding_vs_best_spatial")
    comb_wins = beats("combined_vs_best_spatial")
    headline = (
        f"vs the BEST simple spatial baseline ({best_spatial}, {bs:.2f} sp): "
        f"pure embedding {'BEATS' if emb_wins else 'does NOT beat'} it; "
        f"combined (spatial+embedding) {'BEATS' if comb_wins else 'does NOT beat'} it")
    print("\n=== VERDICT (paired across seeds) ===")
    for k, c in contrasts.items():
        print("  " + verdict_line(k, c))
    print(f"  HEADLINE: {headline} (total species {n_species})")

    out_data = {"meta": {"n_obs": n_obs, "n_species": n_species, "budget": budget,
                         "seeds": args.seeds, "backbone": backbone, "device": device,
                         "gpu_name": gpu_name, "emb_device": emb_device,
                         "geo_distance": "haversine",
                         "taxon": args.taxon, "project": args.project,
                         "runtime_s": round(time.time() - t0, 1)},
                "results": results, "curves_mean": curves_mean,
                "contrasts": contrasts, "headline": headline}
    os.makedirs(args.out, exist_ok=True)
    tag = f"_{backbone}"
    with open(os.path.join(args.out, f"exp_discovery_results{tag}.json"), "w") as f:
        json.dump(out_data, f, indent=2)
    print(f"wrote exp_discovery_results{tag}.json")
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(7, 5))
        for name, c in curves_mean.items():
            plt.plot(range(1, len(c) + 1), c, label=name, lw=2)
        plt.xlabel("observations sampled"); plt.ylabel("distinct species discovered")
        plt.title(f"BTG amphibian discovery ({backbone}, n={n_obs}, {args.seeds} seeds)")
        plt.legend(); plt.tight_layout()
        plt.savefig(os.path.join(args.out, f"exp_discovery_curves{tag}.png"), dpi=120)
        print(f"wrote exp_discovery_curves{tag}.png")
    except Exception as e:
        print(f"[plot] skipped: {e}")
    print(f"DONE in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
