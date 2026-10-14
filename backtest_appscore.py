"""Retrospective associations of app scores with novelty in sampled project records.

Observations are grouped on the published 25 km equal-area inventory. Shipped
0-1 axes are blended with the app preset weights without sample renormalization.

The split uses observed_on, not historical record availability or identification.
The cached research-grade records were pulled later; records and IDs at the split
cannot be reconstructed from these CSVs. A training-period scarcity proxy uses
only observations dated before the split; historical availability remains unknown.
Legacy result keys ending in _leakfree name that proxy for compatibility.

Discover/staleness use all-time density, env density-weights CHELSA climate with
all-time observations, and conservation uses a later GBIF occurrence download.
Urgency's pre-split source vintage is not established by the inventory metadata.
These axes are shipped diagnostics, and no frozen training source is invented.

Novelty counts taxon IDs, including infraspecific ranks, absent from the sampled
pre-split project observations in that cell. It is not biological-species discovery
or novelty relative to the cell's complete iNaturalist inventory. Equal observation
counts do not establish equal field hours. Run metadata records these boundaries.
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer

import voi_backtest as vb
from goal_presets import AXES, DEFAULT, PRESETS
from grid_lattice import Lattice
from grid_schema import BANDS

CA = "cluster_results/ca"
TRAIN_ONLY_AXES = {"discover"}
# backtest taxon (iconic) -> app group file stem
GROUP = {
    "Amphibia": "Amphibia",
    "Aves": "Aves",
    "Insecta": "Insecta",
    "Mammalia": "Mammalia",
    "Reptilia": "Reptilia",
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AppGrid:
    """The viewer's declared LAEA lattice and its eligible, coordinate-keyed inventory."""

    def __init__(self, group, data_dir=CA):
        self.group = group
        data_dir = Path(data_dir)
        index_path = data_dir / "index.json"
        index = json.loads(index_path.read_text())
        expected_crs = CRS.from_proj4(
            "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
        )
        crs = CRS.from_user_input(index.get("crs"))
        if crs != expected_crs or index.get("res_m") != 25000:
            raise ValueError("app backtest requires the viewer's WGS84 LAEA 25000 m grid")
        self.lattice = Lattice(index["res_m"], crs)
        expected = {
            "x0": self.lattice.x0,
            "y1": self.lattice.y1,
            "ncol": self.lattice.ncol,
            "nrow": self.lattice.nrow,
            "cell_km2": self.lattice.cell_km2,
        }
        if index.get("lattice") != expected:
            raise ValueError("app inventory lattice does not match the viewer lattice")
        columns = index.get("row_format")
        if columns != ["lat", "lon"] + BANDS:
            raise ValueError("app inventory row_format does not match the viewer's axis positions")
        if group not in index.get("files", {}):
            raise ValueError(f"app inventory has no group {group!r}")
        source_path = data_dir / index["files"][group]
        rows = json.loads(source_path.read_text()).get(group)
        if not isinstance(rows, list) or not rows or len(rows) != index.get("n_cells"):
            raise ValueError("app inventory group rows do not match n_cells")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError("app inventory contains a malformed data row")
        self.forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self.inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        values = np.asarray(rows, dtype=float)
        rr, cc = self._project(values[:, 0], values[:, 1])
        lat, lon = self.centres(rr, cc)
        if (
            (rr < 0).any()
            or (cc < 0).any()
            or not np.allclose(values[:, :2], np.column_stack([lat, lon]), atol=0.000501, rtol=0)
        ):
            raise ValueError("app inventory row coordinates are not rounded lattice centres")
        mask_path = data_dir / "us_cells.json"
        hidden = json.loads(mask_path.read_text()).get("us_cells")
        if not isinstance(hidden, list) or any(not isinstance(key, str) for key in hidden):
            raise ValueError("app inventory has an invalid Canada eligibility mask")
        hidden = set(hidden)
        self.axes, keys, geographic_keys = {}, set(), set()
        for row, gi, gj in zip(values, rr, cc, strict=True):
            key = (int(gi), int(gj))
            if key in keys:
                raise ValueError(f"duplicate app inventory cell {key}")
            keys.add(key)
            geographic = f"{row[0]:.3f},{row[1]:.3f}"
            geographic_keys.add(geographic)
            axes = {ax: float(row[columns.index(ax)]) for ax in AXES}
            if any(not np.isfinite(value) or not 0 <= value <= 1 for value in axes.values()):
                raise ValueError(f"app inventory cell {key} has an invalid normalized axis")
            if geographic not in hidden:
                self.axes[key] = axes
        if not hidden <= geographic_keys:
            raise ValueError("Canada eligibility mask names cells absent from the app inventory")
        inventory = [[list(key), axes] for key, axes in sorted(self.axes.items())]
        self.metadata = {
            "res_m": self.lattice.res,
            "crs": index["crs"],
            "lattice": expected,
            "cell_keys": ["row", "col"],
            "n_inventory_cells": len(rows),
            "n_eligible_cells": len(self.axes),
            "inventory_sha256": hashlib.sha256(
                json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_sha256": {
                "index": sha256(index_path),
                "group": sha256(source_path),
                "canada_mask": sha256(mask_path),
            },
        }

    def _project(self, lat, lon):
        lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
        if lat.shape != lon.shape or lat.ndim != 1:
            raise ValueError("grid coordinates must be matching one-dimensional lat/lon arrays")
        valid = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
        rows, cols = np.full(lat.shape, -1, dtype=int), np.full(lat.shape, -1, dtype=int)
        positions = np.flatnonzero(valid)
        x, y = self.forward.transform(lon[valid], lat[valid])
        x, y = np.asarray(x), np.asarray(y)
        finite = np.isfinite(x) & np.isfinite(y)
        rr = np.floor((self.lattice.y1 - y[finite]) / self.lattice.res).astype(int)
        cc = np.floor((x[finite] - self.lattice.x0) / self.lattice.res).astype(int)
        inside = (rr >= 0) & (rr < self.lattice.nrow) & (cc >= 0) & (cc < self.lattice.ncol)
        positions = positions[finite][inside]
        rows[positions], cols[positions] = rr[inside], cc[inside]
        return rows, cols

    def locate(self, lat, lon):
        """Eligible (row, col), or (-1, -1) for missing, foreign or unpublished cells."""
        rows, cols = self._project(lat, lon)
        absent = np.array(
            [(int(r), int(c)) not in self.axes for r, c in zip(rows, cols, strict=True)], dtype=bool
        )
        rows[absent], cols[absent] = -1, -1
        return rows, cols

    def centres(self, rows, cols):
        x, y = self.lattice.centres(rows, cols)
        lon, lat = self.inverse.transform(x, y)
        return np.asarray(lat), np.asarray(lon)


def load_app_axes(group, grid):
    """(row, col) -> shipped normalized axes, requiring an explicit compatible AppGrid."""
    if grid.group != group:
        raise ValueError("app axes group does not match the supplied grid inventory")
    return grid.axes


def join_axes(cells, app):
    """Attach shipped axes by the exact projected (row, col), carried as gi/gj."""
    if cells.attrs.get("grid", {}).get("cell_keys") != ["row", "col"]:
        raise ValueError("app axis joins require cells built on the explicit app grid")
    keep, env, urg, disc, cons, stal = [], [], [], [], [], []
    for _, row in cells.iterrows():
        a = app.get((int(row.gi), int(row.gj)))
        keep.append(a is not None)
        env.append(a["env"] if a else np.nan)
        urg.append(a["urgency"] if a else np.nan)
        disc.append(a["discover"] if a else np.nan)
        cons.append(a["conservation"] if a else np.nan)
        stal.append(a["staleness"] if a else np.nan)
    cells = cells.assign(
        app_env=env, app_urgency=urg, app_discover=disc, app_conservation=cons, app_staleness=stal
    )
    return cells[pd.Series(keep, index=cells.index)].copy()


def composite(cells, weights, discover_col):
    """One preset's blend over the app's own axes, with a choice of discover source."""
    col = {
        "discover": cells[discover_col],
        "conservation": cells.app_conservation,
        "env": cells.app_env,
        "staleness": cells.app_staleness,
        "urgency": cells.app_urgency,
    }
    total = pd.Series(0.0, index=cells.index)
    for ax, w in zip(AXES, weights, strict=True):
        if w:
            total = total + w * col[ax]
    return total


def eff_ratio(rk, score):
    """Effort-equalized efficiency: mean new@K in top vs bottom score-tercile."""
    if len(rk) < 6:
        return None, None, None
    q = score.quantile([1/3, 2/3])
    top = rk.rare_newK[score >= q.iloc[1]]
    bot = rk.rare_newK[score <= q.iloc[0]]
    if not len(bot) or bot.mean() <= 0:
        return None, float(top.mean()) if len(top) else None, None
    return float(top.mean() / bot.mean()), float(top.mean()), float(bot.mean())


def analyse(name, df, K=5, data_dir=CA, input_path=None):
    grid = AppGrid(GROUP[name], data_dir)
    cells = vb.build_cells(df, grid=grid)
    if cells is None or len(cells) < 10:
        return None
    aux = cells.attrs["aux"]
    app = load_app_axes(GROUP[name], grid)
    cells = join_axes(cells, app)
    if len(cells) < 10:
        return None
    # Novel taxon IDs relative to sampled project training records, at K test observations.
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(vb.SEED), K=K)
    rk = cells.dropna(subset=["rare_newK", "app_env", "app_urgency"]).copy()
    if len(rk) < 10:
        return None

    # The train-only alternative is a proxy, not the shipped national density ramp.
    rk["app_shipped"] = composite(rk, DEFAULT, "app_discover")  # all-time discover (LEAKY)

    out = {
        "taxon": name,
        "n_cells": len(cells),
        "n_rarefied": len(rk),
        "K": K,
        "perm_p_floor": 1.0 / (vb.N_PERM + 1),
        "total_new_species": int(cells.new_species.sum()),
    }

    # Density is the training-count rank inverse of scarcity, not an independent control.
    scores = {
        "app_shipped": rk.app_shipped,
        "discover_leakfree": rk.scarcity,
        "env": rk.app_env,
        "urgency": rk.app_urgency,
        "opportunistic_density": rk.density,
    }
    default_axes = {ax for ax, w in zip(AXES, DEFAULT, strict=True) if w}
    if default_axes <= TRAIN_ONLY_AXES:
        rk["app_leakfree"] = composite(rk, DEFAULT, "scarcity")
        scores["app_leakfree"] = rk.app_leakfree
    # ...plus every other preset the dropdown offers, under its own slug
    for p in PRESETS:
        if p["w"] == DEFAULT:
            continue
        slug = p["name"].lower().replace(" ", "_")
        weighted = {ax for ax, w in zip(AXES, p["w"], strict=True) if w}
        scores[f"{slug}_shipped"] = composite(rk, p["w"], "app_discover")
        if weighted <= TRAIN_ONLY_AXES:
            scores[f"{slug}_leakfree"] = composite(rk, p["w"], "scarcity")
    res = {}
    for key, sc in scores.items():
        rho, p, _mu, _sd = vb.perm_test(sc.values, rk.rare_newK.values,
                                     np.random.default_rng(vb.SEED + 7), n=vb.N_PERM)
        ratio, topm, botm = eff_ratio(rk, sc)
        res[key] = {
            "spearman": rho,
            "perm_p": p,
            "eff_ratio_top_bottom": ratio,
            "new_at_K_top": topm,
            "new_at_K_bottom": botm,
        }
    out["scores"] = res
    canonical = df.sort_values(list(df.columns)).to_csv(index=False).encode()
    out["run"] = {
        "grid": grid.metadata,
        "split": vb.SPLIT.date().isoformat(),
        "seed": vb.SEED,
        "permutation_seed": vb.SEED + 7,
        "n_permutations": vb.N_PERM,
        "permutation_probability": "(exceedances + 1) / (draws + 1)",
        "permutation_probability_source": "https://doi.org/10.2202/1544-6115.1585",
        "rarefaction_reps": 200,
        "K": K,
        "train_only_axes": sorted(TRAIN_ONLY_AXES),
        "score_scope": {
            key: (
                "training-period observed_on proxy; historical availability unknown"
                if key.endswith("leakfree")
                else "training-period count rank inverse; historical availability unknown"
                if key == "opportunistic_density"
                else "as-shipped exploratory association; historical availability unknown"
            )
            for key in scores
        },
        "outcome": {
            "unit": "iNaturalist taxon_id",
            "baseline": "sampled project observations with observed_on before split",
            "ranks": ["species", "subspecies", "variety", "form", "hybrid"],
            "definition": "distinct post-split taxon IDs absent from that cell's sampled training records",
            "legacy_field_names": "new_species/new_at_K fields count taxon IDs, not exclusively biological species",
        },
        "limitations": {
            "split_field": "observed_on only",
            "historical_availability": "unknown",
            "identifications": "research-grade IDs at the later pull, not a historical ID snapshot",
            "inventory": "later published footprint, not a historical footprint snapshot",
            "record_scope": "page-capped project sample, not the complete cell inventory",
            "previously_unobserved_cells": "excluded",
            "effort_unit": "observation count, not field hours",
            "exchangeability": "unrestricted cell permutations assume exchangeability, not established",
            "spatial_dependence": "not controlled by spatial blocking",
            "multiple_comparisons": "unadjusted",
            "inference": "retrospective association, not a prospective or causal trip-yield estimate",
        },
        "observation_rows": len(df),
        "observation_frame_sha256": hashlib.sha256(canonical).hexdigest(),
        "input_sha256": sha256(input_path) if input_path else None,
        "source_sha256": {
            "backtest_appscore.py": sha256(__file__),
            "voi_backtest.py": sha256(vb.__file__),
            "goal_presets.py": sha256(Path(__file__).with_name("goal_presets.py")),
        },
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__},
    }
    return out, rk


if __name__ == "__main__":
    files = sys.argv[1:] or [f"cluster_results/inat_{t}.csv" for t in GROUP]
    results = []
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        if name not in GROUP:
            print(f"{name}: no app group mapping, skip"); continue
        df = pd.read_csv(f)
        r = analyse(name, df, input_path=f)
        if r is None:
            print(f"{name}: insufficient data"); continue
        out, _ = r
        results.append(out)
        pf = out["perm_p_floor"]
        fp = lambda p: f"<{pf:.4f}" if p is not None and p < pf else (f"{p:.4f}" if p is not None else "nan")
        s = out["scores"]
        print(f"\n=== {name} === cells={out['n_cells']} rarefied={out['n_rarefied']} new_sp={out['total_new_species']}")
        for key, d in s.items():
            er = d["eff_ratio_top_bottom"]
            print(f"  {key:22s} rho={d['spearman']:+.3f} perm_p={fp(d['perm_p']):>8} "
                  f"eff_top/bot={(f'{er:.2f}x') if er else '  n/a'}")
    json.dump(results, open("cluster_results/voi_appscore_results.json", "w"), indent=2)
    print(f"\nwrote cluster_results/voi_appscore_results.json ({len(results)} taxa)")
