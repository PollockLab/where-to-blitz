"""BTG species-discovery experiment — does embedding-novelty acquisition beat
simple spatial coverage for amphibian discovery? (design-04's no-free-lunch test,
on real iNaturalist 228908 data, GPU image embeddings.)

Runs on a GPU node. Self-contained: pulls obs+photos from iNat, extracts vision
embeddings on GPU, simulates active discovery under several acquisition strategies,
and reports discovery curves. Every claim is a measured number with seeds.

Output: exp_discovery_results.json + exp_discovery_curves.png in --out.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.request
from collections import defaultdict

import numpy as np

INAT = "https://api.inaturalist.org/v1/observations"
PROJECT = 228908


def pull_observations(n_target=1500, per_page=200):
    """Research-grade amphibians in Canada with a photo + species id."""
    params = {"project_id": PROJECT, "quality_grade": "research", "iconic_taxa": "Amphibia",
              "per_page": per_page, "order_by": "id", "order": "desc",
              "photos": "true", "identified": "true",
              "swlat": 41, "swlng": -141, "nelat": 84, "nelng": -52}
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


def fetch_image(url):
    from PIL import Image
    with urllib.request.urlopen(url, timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def _load_dinov2(device):
    import torch
    import torchvision.transforms as T
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(device).eval()
    tf = T.Compose([T.Resize(224), T.CenterCrop(224), T.ToTensor(),
                    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    return model, tf, "dinov2_vits14"

def _load_resnet50(device):
    import torch
    import torchvision
    from torchvision.models import ResNet50_Weights
    w = ResNet50_Weights.IMAGENET1K_V2
    m = torchvision.models.resnet50(weights=w); m.fc = torch.nn.Identity()
    return m.to(device).eval(), w.transforms(), "resnet50_imagenet"

def _load_clip(device):
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model = model.to(device).eval()
    class _Vis:
        def __call__(self, x):
            return model.encode_image(x)
    return _Vis(), preprocess, "clip_vit_b32"

def embed_images(records, device, batch=64, want="auto"):
    """Vision embeddings on GPU. Pick backbone; auto = DINOv2 → ResNet50 fallback."""
    import torch
    order = {"auto": ["dinov2", "resnet50"], "dinov2": ["dinov2"],
             "resnet50": ["resnet50"], "clip": ["clip", "resnet50"]}[want]
    loaders = {"dinov2": _load_dinov2, "resnet50": _load_resnet50, "clip": _load_clip}
    model = tf = backbone = None
    for name in order:
        try:
            model, tf, backbone = loaders[name](device); break
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
            img = fetch_image(rec["photo"])
            buf.append(tf(img)); idxs.append(i)
        except Exception:
            continue
        if len(buf) >= batch:
            flush()
    flush()
    E = np.concatenate(embs, 0) if embs else np.zeros((0, 1))
    E /= (np.linalg.norm(E, axis=1, keepdims=True) + 1e-8)
    return E, keep, backbone


# ---- acquisition strategies: pick next index given seen set ----
def discover(order_fn, recs, E, seed, budget):
    rng = np.random.default_rng(seed)
    n = len(recs)
    start = int(rng.integers(n))
    seen = [start]
    seen_species = {recs[start]["species"]}
    curve = [1]
    remaining = set(range(n)) - {start}
    while remaining and len(seen) < budget:
        nxt = order_fn(seen, remaining, recs, E, rng)
        remaining.discard(nxt)
        seen.append(nxt)
        seen_species.add(recs[nxt]["species"])
        curve.append(len(seen_species))
    return curve


def acq_random(seen, remaining, recs, E, rng):
    return int(rng.choice(list(remaining)))

def acq_embed_novelty(seen, remaining, recs, E, rng):
    rem = np.fromiter(remaining, int)
    sims = E[rem] @ E[seen].T            # cosine sim to seen set
    nearest = sims.max(axis=1)           # most-similar seen
    return int(rem[int(np.argmin(nearest))])   # pick the most-novel (farthest)

def acq_spatial_coverage(seen, remaining, recs, E, rng):
    rem = np.fromiter(remaining, int)
    sl = np.array([[recs[i]["lat"], recs[i]["lon"]] for i in seen])
    rl = np.array([[recs[i]["lat"], recs[i]["lon"]] for i in rem])
    d = np.sqrt(((rl[:, None, :] - sl[None, :, :]) ** 2).sum(-1))  # geo distance
    return int(rem[int(np.argmax(d.min(axis=1)))])   # farthest-point in space


def auc(curve, budget):
    c = np.array(curve, float)
    if len(c) < budget:
        c = np.concatenate([c, np.full(budget - len(c), c[-1])])
    return float(c[:budget].mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--budget", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--backbone", default="auto", choices=["auto", "dinov2", "resnet50", "clip"])
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    print(f"device={device} cuda={torch.cuda.is_available()} "
          f"name={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}")

    recs = pull_observations(args.n)
    print(f"pulled {len(recs)} amphibian obs w/ photo+species, "
          f"{len({r['species'] for r in recs})} distinct species")
    E, keep, backbone = embed_images(recs, device, want=args.backbone)
    recs = [recs[i] for i in keep]
    print(f"embedded {len(recs)} images with {backbone}, dim={E.shape[1]} in {time.time()-t0:.0f}s")
    n_species = len({r["species"] for r in recs})

    strategies = {"random": acq_random, "spatial_coverage": acq_spatial_coverage,
                  "embedding_novelty": acq_embed_novelty}
    budget = min(args.budget, len(recs))
    results = defaultdict(list)
    curves_mean = {}
    for name, fn in strategies.items():
        aucs, last = [], []
        all_curves = []
        for s in range(args.seeds):
            c = discover(fn, recs, E, seed=s, budget=budget)
            aucs.append(auc(c, budget)); last.append(c[-1])
            padded = np.array(c + [c[-1]] * (budget - len(c)))[:budget]
            all_curves.append(padded)
        results[name] = {"auc_mean": float(np.mean(aucs)), "auc_std": float(np.std(aucs)),
                         "species_at_budget_mean": float(np.mean(last)),
                         "species_at_budget_std": float(np.std(last))}
        curves_mean[name] = np.mean(all_curves, 0).tolist()
        print(f"  {name:18s} AUC={results[name]['auc_mean']:.1f}±{results[name]['auc_std']:.1f} "
              f"species@{budget}={results[name]['species_at_budget_mean']:.1f}")

    # the design-04 verdict: does the fancy method beat simple coverage?
    base = results["spatial_coverage"]["species_at_budget_mean"]
    fancy = results["embedding_novelty"]["species_at_budget_mean"]
    rng = results["random"]["species_at_budget_mean"]
    verdict = ("embedding_novelty BEATS coverage" if fancy > base + 1 else
               "embedding_novelty does NOT beat coverage" if fancy < base - 1 else
               "embedding_novelty TIES coverage")
    print(f"\nVERDICT @budget={budget}: {verdict} "
          f"(novelty {fancy:.1f} vs coverage {base:.1f} vs random {rng:.1f}; total species {n_species})")

    out = {"meta": {"n_obs": len(recs), "n_species": n_species, "budget": budget,
                    "seeds": args.seeds, "backbone": backbone, "device": device,
                    "runtime_s": round(time.time() - t0, 1)},
           "results": dict(results), "curves_mean": curves_mean, "verdict": verdict}
    import os
    os.makedirs(args.out, exist_ok=True)
    tag = f"_{backbone}" if args.backbone != "auto" else ""
    with open(os.path.join(args.out, f"exp_discovery_results{tag}.json"), "w") as f:
        json.dump(out, f, indent=2)
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.figure(figsize=(7, 5))
        for name, c in curves_mean.items():
            plt.plot(range(1, len(c) + 1), c, label=name, lw=2)
        plt.xlabel("observations sampled"); plt.ylabel("distinct species discovered")
        plt.title(f"BTG amphibian discovery curves ({backbone}, n={len(recs)}, {args.seeds} seeds)")
        plt.legend(); plt.tight_layout()
        plt.savefig(os.path.join(args.out, f"exp_discovery_curves{tag}.png"), dpi=120)
        print(f"wrote exp_discovery_curves{tag}.png")
    except Exception as e:
        print(f"[plot] skipped: {e}")
    print(f"DONE in {time.time()-t0:.0f}s -> exp_discovery_results.json")


if __name__ == "__main__":
    main()
