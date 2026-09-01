"""Hash every data input/output for the national CA app and write cluster_results/ca/provenance.json."""

import glob
import hashlib
import json
import os
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CA_DIR = os.path.join(REPO_ROOT, "cluster_results", "ca")
OUT_PATH = os.path.join(CA_DIR, "provenance.json")
INDEX_PATH = os.path.join(CA_DIR, "index.json")

SOURCES = {
    "discover/env/urgency/travel": {
        "datasets": (
            "iNat density COG (Biodiversite Quebec STAC), CHELSA bioclimate, "
            "Hansen Global Forest Change, Weiss et al. 2018 travel-time"
        ),
        "builder": "build_fullgrid_ca.py",
    },
    "conservation": {
        "datasets": (
            "CAN-SAR (OSF DOI 10.17605/OSF.IO/E4A58, CC-BY) x GBIF Canadian occurrences"
        ),
        "builder": "build_atrisk_layer.py -> ca_atrisk_richness.csv, joined in fullgrid_fields.py",
    },
    "staleness": {
        "datasets": "iNaturalist open-data research-grade (AWS dump)",
        "builder": "cluster DuckDB -> ca_inat_metrics.csv",
    },
}

INPUT_PATTERNS = [
    "cluster_results/ca/ca_density_*.tif",
    "cluster_results/ca/ca_atrisk_richness.csv",
    "cluster_results/ca/ca_inat_metrics.csv",
    "cluster_results/ca/ca_staleness.csv",
    "cluster_results/ca/index.json",
]

OUTPUT_PATTERNS = [
    "cluster_results/ca/webapp_data_*.json",
    "cluster_results/ca/breaks.json",
    "cluster_results/ca/us_cells.json",
    # The Getting Even fill and its build record (#122). The PNG is lattice index space, so a
    # lattice change re-points every pixel instead of making a lookup miss; freezing its hash
    # here is how a reader can tell which build the shipped colours came from.
    "cluster_results/ca/gettingeven_grid_*.png",
    "cluster_results/ca/gettingeven_grid.json",
]


def read_grid() -> dict:
    """The lattice as the build itself declares it, from the shipped index.json (#87).

    Hardcoding this is how the manifest came to claim a 0.25 degree grid of 31,804 cells
    months after the app moved to the 25 km equal-area lattice.
    """
    with open(INDEX_PATH) as f:
        index = json.load(f)
    return {
        "res_m": index["res_m"],
        "n_cells": index["n_cells"],
        "lattice": index["lattice"],
        "crs": index["crs"],
    }


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(patterns: list[str]) -> list[dict]:
    seen: set[str] = set()
    records: list[dict] = []
    for pattern in patterns:
        full_pattern = os.path.join(REPO_ROOT, pattern)
        for abs_path in sorted(glob.glob(full_pattern)):
            if not os.path.isfile(abs_path):
                continue
            rel = os.path.relpath(abs_path, REPO_ROOT)
            if rel in seen:
                continue
            seen.add(rel)
            mtime = time.gmtime(os.path.getmtime(abs_path))
            records.append(
                {
                    "path": rel,
                    "sha256": sha256_file(abs_path),
                    "size_bytes": os.path.getsize(abs_path),
                    "modified_date": time.strftime("%Y-%m-%d", mtime),
                }
            )
    return records


def manifest_hash(files: list[dict]) -> str:
    lines = "\n".join(f"{r['path']}:{r['sha256']}" for r in sorted(files, key=lambda r: r["path"]))
    return hashlib.sha256(lines.encode()).hexdigest()


def main() -> None:
    files = collect_files(INPUT_PATTERNS + OUTPUT_PATTERNS)
    files.sort(key=lambda r: r["path"])

    doc = {
        "schema": "where-to-blitz/provenance@1",
        "grid": read_grid(),
        "sources": SOURCES,
        "files": files,
        "manifest_hash": manifest_hash(files),
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")

    print(f"Wrote {OUT_PATH}")
    print(f"manifest_hash: {doc['manifest_hash']}")
    print(f"files hashed:  {len(files)}")


if __name__ == "__main__":
    main()
