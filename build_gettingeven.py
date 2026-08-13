"""Rebuilds cluster_results/ca/webapp_data_gettingeven.json from the shipped per-group grid.

The Getting Even layer colours each cell by the taxonomic group most under-represented
there, birds excluded (eBird already covers them). "Under-represented" is a relative
deficit: the group's local share of records against its own national share, so the
categories balance instead of the most-prevalent group winning everywhere.

Input is `n_train` (implied record count per cell) out of the per-group
`webapp_data_<GROUP>.json` files, which means the output is keyed on whatever lattice
those files were just built on. The previous file was hand-made on the legacy 0.25-deg
lattice and had no producer, so the #87 lattice migration left it keyed on cells that no
longer exist: every lookup missed and the whole layer rendered grey.

Run after build_fullgrid_ca.py, from the repo root.
"""
import json
import os

OUT_DIR = "cluster_results/ca"

# Display category -> the index.json groups that feed it. Aves is deliberately absent.
GE_GROUPS = {
    "Fishes": ["Actinopterygii"],
    "Fungi": ["Fungi"],
    "Reptiles & Amphibians": ["Amphibia", "Reptilia"],
    "Invertebrates": ["Arachnida", "Insecta", "Mollusca"],
    "Mammals": ["Mammalia"],
    "Plants": ["Plantae"],
}
CATS = list(GE_GROUPS)


def _load(index):
    """Per-category record counts per cell, plus the cell centres they are keyed on."""
    n_train = index["row_format"].index("n_train")
    coords, counts = None, []
    for cat in CATS:
        total = None
        for group in GE_GROUPS[cat]:
            with open(os.path.join(OUT_DIR, index["files"][group])) as fh:
                rows = json.load(fh)[group]
            if coords is None:
                coords = [(r[0], r[1]) for r in rows]
            elif len(rows) != len(coords):
                raise SystemExit(f"{group}: {len(rows)} rows, expected {len(coords)}")
            col = [r[n_train] for r in rows]
            total = col if total is None else [a + b for a, b in zip(total, col)]
        counts.append(total)
    return coords, counts


def build(coords, counts):
    """One row per cell: [lat, lon, category index, deficit]. -1 where nothing was recorded.

    deficit = 1 - (local share / national share), clipped to [0, 1]. A category with no
    local records scores 1.0; one at or above its national share scores 0.

    Cells where several categories are wholly absent all score 1.0, so ties go to the
    category with the larger national share: of two groups nobody has recorded here, the
    commoner one is the one a visitor can realistically go and find.
    """
    national = [sum(c) for c in counts]
    grand = sum(national)
    if grand == 0:
        raise SystemExit("no records in any group: refusing to write an all-grey layer")
    share = [n / grand for n in national]

    rows = []
    for i, (lat, lon) in enumerate(coords):
        local = [counts[c][i] for c in range(len(CATS))]
        cell = sum(local)
        if cell == 0:
            rows.append([lat, lon, -1, 1.0])
            continue
        best, best_key = -1, (-1.0, -1.0)
        for c in range(len(CATS)):
            if share[c] == 0:
                continue
            d = min(1.0, max(0.0, 1.0 - (local[c] / cell) / share[c]))
            if (d, share[c]) > best_key:
                best, best_key = c, (d, share[c])
        rows.append([lat, lon, best, round(best_key[0], 3)])
    return rows


def main():
    with open(os.path.join(OUT_DIR, "index.json")) as fh:
        index = json.load(fh)
    missing = [g for cat in CATS for g in GE_GROUPS[cat] if g not in index["files"]]
    if missing:
        raise SystemExit(f"index.json has no file for: {', '.join(missing)}")

    coords, counts = _load(index)
    rows = build(coords, counts)

    path = os.path.join(OUT_DIR, "webapp_data_gettingeven.json")
    with open(path, "w") as fh:
        json.dump({"gettingeven": rows, "cats": CATS}, fh, separators=(",", ":"))

    named = sum(1 for r in rows if r[2] >= 0)
    print(f"{path}: {len(rows)} cells, {named} with a named group, "
          f"{len(rows) - named} all under-sampled")


if __name__ == "__main__":
    main()
