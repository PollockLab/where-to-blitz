"""Hash every data input/output for the national CA app and write cluster_results/ca/provenance.json."""

import argparse
import glob
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from goal_presets import PRESETS

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
    "cluster_results/ca/ca_atrisk_provenance.json",
    "cluster_results/ca/atrisk_shipping_policy.json",
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
    "cluster_results/ca/land_sea_5000m.png",
    "cluster_results/ca/values/*.png",
]

BUNDLE_SCHEMA = "where-to-blitz/grid-site-data@1"
BUNDLE_MANIFEST = "bundle_manifest.json"


def validate_release_data(data: dict[str, bytes]) -> None:
    """Require the complete app payload and check the build's recorded output hashes."""
    required = {
        "index.json",
        "breaks.json",
        "provenance.json",
        "us_cells.json",
        "ca_atrisk_provenance.json",
        "atrisk_shipping_policy.json",
        "gettingeven_grid.json",
        "webapp_data_gettingeven.json",
        "gettingeven_grid_25000m.png",
        "gettingeven_grid_5000m.png",
        "land_sea_5000m.png",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"missing app files: {', '.join(sorted(missing))}")
    index = json.loads(data["index.json"])
    if not index["groups"] or set(index["groups"]) != set(index["files"]):
        raise ValueError("index.json must declare matching nonempty groups and files")
    required.update(index["files"].values())
    for group in index["groups"]:
        for preset in PRESETS:
            for res in (25000, 5000):
                slug = f"{group.replace(' ', '_')}_{preset['name'].lower().replace(' ', '_')}_grid_{res}m.png"
                required.add(f"values/{slug}")
    missing = required - data.keys()
    if missing:
        raise ValueError(f"missing app files: {', '.join(sorted(missing))}")
    for name, content in data.items():
        if not content:
            raise ValueError(f"empty app file: {name}")
        if name.endswith(".json"):
            json.loads(content)
    provenance = json.loads(data["provenance.json"])
    grid = {key: index[key] for key in ("res_m", "n_cells", "lattice", "crs")}
    if provenance["grid"] != grid:
        raise ValueError("provenance.json grid disagrees with index.json")
    for record in provenance["files"]:
        prefix = "cluster_results/ca/"
        name = record["path"].removeprefix(prefix)
        if record["path"].startswith(prefix) and name in data:
            if hashlib.sha256(data[name]).hexdigest() != record["sha256"]:
                raise ValueError(f"provenance checksum mismatch: {name}")


def package_release_bundle(ca_dir: Path, bundle_path: Path) -> None:
    """Ship app JSON, masks and both colour tiers as one checksummed release asset."""
    paths = sorted({*ca_dir.glob("*.json"), *ca_dir.glob("*.png"), *ca_dir.glob("values/*.png")})
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("app bundle inputs must be regular files")
    data = {path.relative_to(ca_dir).as_posix(): path.read_bytes() for path in paths}
    validate_release_data(data)
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in data.items()},
    }
    data[BUNDLE_MANIFEST] = json.dumps(manifest, sort_keys=True).encode()
    with tarfile.open(bundle_path, "w:gz") as archive:
        for name, content in data.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    print(f"Packaged {bundle_path}")


def restore_release_bundle(bundle_path: Path, ca_dir: Path) -> None:
    """Verify the complete bundle before replacing tracked app data or old colour PNGs."""
    data = {}
    with tarfile.open(bundle_path, "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or member.name in data
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != member.name
                or not (
                    len(path.parts) == 1
                    and path.suffix in (".json", ".png")
                    or len(path.parts) == 2
                    and path.parts[0] == "values"
                    and path.suffix == ".png"
                )
            ):
                raise ValueError(f"invalid bundle member: {member.name}")
            data[member.name] = archive.extractfile(member).read()
    manifest = json.loads(data.pop(BUNDLE_MANIFEST))
    if manifest["schema"] != BUNDLE_SCHEMA or set(manifest["files"]) != set(data):
        raise ValueError("bundle manifest schema or file set disagrees with payload")
    for name, content in data.items():
        if hashlib.sha256(content).hexdigest() != manifest["files"][name]:
            raise ValueError(f"bundle checksum mismatch: {name}")
    validate_release_data(data)
    ca_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ca_dir.parent) as temp:
        staged = Path(temp)
        for name, content in data.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for path in [*ca_dir.glob("*.json"), *ca_dir.glob("*.png")]:
            path.unlink()
        if (ca_dir / "values").exists():
            shutil.rmtree(ca_dir / "values")
        for path in staged.iterdir():
            shutil.move(str(path), ca_dir / path.name)
    print(f"Restored {bundle_path} into {ca_dir}")


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
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--package-release", type=Path)
    modes.add_argument("--restore-release", type=Path)
    parser.add_argument("--ca-dir", type=Path, default=Path(CA_DIR))
    args = parser.parse_args()
    if args.package_release or args.restore_release:
        try:
            if args.package_release:
                package_release_bundle(args.ca_dir, args.package_release)
            else:
                restore_release_bundle(args.restore_release, args.ca_dir)
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            raise SystemExit(f"Invalid grid site data bundle: {exc}") from exc
    else:
        main()
