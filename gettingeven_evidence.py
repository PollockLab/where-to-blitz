"""Exact all-taxa source-count evidence for the displayed Getting Even tiers.

These aggregate n_train values may be rounded density-derived proxies. They do
not estimate accuracy or calibrated confidence and never alter category winners.
"""

import argparse
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

from evaluate_gettingeven import load_inputs, validate_counts
from gettingeven_inputs import observation_hash
from grid_lattice import Lattice

MAX_COUNT = 16777215
SOURCE = "aggregate n_train, may be rounded density-derived proxies"
MANIFEST = "gettingeven_evidence.json"
RESOLUTIONS = (25000, 5000)
SCORE_JSON = "webapp_data_gettingeven.json"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("evidence requires SHA256 content hashes")
    return value


def _lattice(directory, resolution, tier):
    index = json.loads((directory / "index.json").read_bytes())
    lattice = Lattice(resolution, rasterio.CRS.from_wkt(index["crs"]))
    expected = {
        "x0": lattice.x0,
        "y1": lattice.y1,
        "res_m": resolution,
        "ncol": lattice.ncol,
        "nrow": lattice.nrow,
        "crs": lattice.crs.to_wkt(),
    }
    actual = tier["lattice"]
    if (
        set(actual) != set(expected)
        or any(actual[key] != expected[key] for key in expected if key != "crs")
        or rasterio.CRS.from_wkt(actual["crs"]) != lattice.crs
    ):
        raise ValueError("Getting Even evidence belongs to another lattice or tier")
    return lattice, expected


def _image(path, shape):
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode != "RGBA" or image.size != (shape[1], shape[0]):
            raise ValueError("evidence/category PNG must be RGBA on the declared lattice")
        rgba = np.array(image)
    if not np.isin(rgba[:, :, 3], [0, 255]).all():
        raise ValueError("evidence/category alpha must be exactly 0 or 255")
    return rgba


def _published(directory):
    sidecar = json.loads((directory / "gettingeven_grid.json").read_bytes())
    result = {}
    for resolution in RESOLUTIONS:
        tier = sidecar["tiers"][str(resolution)]
        name = f"gettingeven_grid_{resolution}m.png"
        if tier.get("png") != name or _sha(directory / name) != _digest(tier.get("sha256")):
            raise ValueError("published category PNG checksum differs from its sidecar")
        lattice, record = _lattice(directory, resolution, tier)
        pixels = _image(directory / name, lattice.shape)
        valid = pixels[:, :, 3] == 255
        if not np.isin(pixels[:, :, 0][valid], [0, 1, 2, 3, 4, 5, 255]).all() or pixels[:, :, 1:3].any():
            raise ValueError("published PNG is not a Getting Even category image")
        result[str(resolution)] = (tier, lattice, record, pixels)
    return sidecar, result


def _source(directory, published, reference_manifest):
    reference = None
    if reference_manifest is not None:
        reference = json.loads(Path(reference_manifest).read_bytes())
        if reference.get("schema") != "gettingeven-reference-counts@1":
            raise ValueError("unsupported trusted frozen reference")
        expected_outputs = {
            SCORE_JSON,
            *(f"gettingeven_grid_{resolution}m.png" for resolution in RESOLUTIONS),
        }
        if set(reference.get("published_outputs_sha256", {})) != expected_outputs:
            raise ValueError("frozen reference must pin both category tiers and coarse JSON")
        for name, checksum in reference["published_outputs_sha256"].items():
            if _sha(directory / name) != _digest(checksum):
                raise ValueError("published output differs from the trusted frozen reference")
        required = {"grid_25000m/All_biodiversity.tif"} | {
            f"grid_5000m/{group}.tif"
            for group in (
                "All_biodiversity",
                "Actinopterygii",
                "Fungi",
                "Amphibia",
                "Reptilia",
                "Arachnida",
                "Insecta",
                "Mollusca",
                "Mammalia",
                "Plantae",
            )
        }
        if set(reference.get("files", {})) != required:
            raise ValueError("frozen reference must pin all aggregate count bands")
        for name, checksum in reference["files"].items():
            if _sha(directory / name) != _digest(checksum):
                raise ValueError("aggregate counts differ from the trusted frozen reference")
    result = {}
    for resolution in RESOLUTIONS:
        tier, lattice, record, category = published[str(resolution)]
        total, counts, rowcol, used = load_inputs(directory, resolution)
        # Reject before int64 conversion, including unsigned values outside its range.
        for values in (total, counts, rowcol):
            values = np.asarray(values)
            if values.dtype.kind not in "iuf" or not np.isfinite(values).all() or (values > MAX_COUNT).any():
                raise ValueError("counts/indices must be finite RGB24 integers without overflow")
        total, counts, rowcol = validate_counts(total, counts, rowcol)
        if ((rowcol[:, 0] >= lattice.nrow) | (rowcol[:, 1] >= lattice.ncol)).any():
            raise ValueError("source count cell falls outside the lattice")
        fingerprint = observation_hash((rowcol[:, 0], rowcol[:, 1]), total, counts.T)
        supplied = (tier.get("inputs") or {}).get("observations_sha256")
        provenance = {"basis": "enriched-sidecar"}
        if reference is not None:
            frozen = reference["provenance"]["observations_sha256"][str(resolution)]
            if fingerprint != _digest(frozen):
                raise ValueError("source observation fingerprint differs from the trusted frozen reference")
            provenance = {"basis": "frozen-reference", "reference_manifest_sha256": _sha(reference_manifest)}
        if supplied is None:
            if reference is None:
                raise ValueError(
                    "source counts require enriched input provenance or an explicit trusted frozen reference"
                )
            supplied = reference["provenance"]["observations_sha256"][str(resolution)]
        if fingerprint != _digest(supplied):
            raise ValueError("source observation fingerprint differs from the displayed tier")
        plane = np.zeros(lattice.shape, dtype=np.uint32)
        present = np.zeros(lattice.shape, dtype=bool)
        plane[rowcol[:, 0], rowcol[:, 1]] = total
        present[rowcol[:, 0], rowcol[:, 1]] = True
        mask = category[:, :, 3] == 255
        if (mask & ~present).any():
            raise ValueError("published category support lacks source counts")
        rgba = np.zeros((*lattice.shape, 4), dtype=np.uint8)
        rgba[:, :, 0][mask] = plane[mask] >> 16
        rgba[:, :, 1][mask] = (plane[mask] >> 8) & 255
        rgba[:, :, 2][mask] = plane[mask] & 255
        rgba[:, :, 3] = category[:, :, 3]
        result[str(resolution)] = (
            rgba,
            {
                "png": f"gettingeven_counts_{resolution}m.png",
                "category_png": tier["png"],
                "category_sha256": tier["sha256"],
                "lattice": record,
                "observations_sha256": fingerprint,
                "input_hashes": used,
                "provenance": provenance,
                "valid_cells": int(mask.sum()),
                "max_count": int(plane[mask].max()) if mask.any() else 0,
            },
        )
    return result


def _validate_public_evidence(data_dir, evidence_dir):
    """Verify pinned public artifacts without needing the source count TIFFs."""
    directory, output = Path(data_dir), Path(evidence_dir)
    payload = (output / MANIFEST).read_bytes()
    document = json.loads(payload)
    if (
        document.get("schema_version") != 1
        or document.get("kind") != "gettingeven-source-counts"
        or document.get("encoding") != "rgb24"
        or document.get("source") != SOURCE
        or set(document.get("tiers", {})) != {"25000", "5000"}
    ):
        raise ValueError("unsupported Getting Even evidence manifest")
    _, published = _published(directory)
    score = document.get("category_json", {})
    if score != {"path": SCORE_JSON, "sha256": _sha(directory / SCORE_JSON)}:
        raise ValueError("coarse category JSON differs from the evidence publisher")
    for resolution in RESOLUTIONS:
        key = str(resolution)
        tier, lattice, record, category = published[key]
        item = document["tiers"][key]
        name = f"gettingeven_counts_{resolution}m.png"
        if (
            item["png"] != name
            or item["category_png"] != tier["png"]
            or item["category_sha256"] != tier["sha256"]
            or item["lattice"] != record
            or _sha(output / name) != _digest(item["sha256"])
        ):
            raise ValueError("evidence differs from its category tier, lattice or PNG checksum")
        fingerprint = _digest(item["observations_sha256"])
        current = (tier.get("inputs") or {}).get("observations_sha256")
        provenance = item.get("provenance", {})
        if current is not None and fingerprint != current:
            raise ValueError("evidence input fingerprint differs from the published tier")
        if provenance.get("basis") == "frozen-reference":
            _digest(provenance.get("reference_manifest_sha256"))
        elif provenance != {"basis": "enriched-sidecar"} or current is None:
            raise ValueError("evidence lacks verified input provenance")
        if not isinstance(item.get("input_hashes"), dict) or not item["input_hashes"]:
            raise ValueError("evidence lacks source input hashes")
        for checksum in item["input_hashes"].values():
            _digest(checksum)
        pixels = _image(output / name, lattice.shape)
        mask = pixels[:, :, 3] == 255
        decoded = (
            pixels[:, :, 0].astype(np.uint32) * 65536
            + pixels[:, :, 1].astype(np.uint32) * 256
            + pixels[:, :, 2]
        )
        if not np.array_equal(pixels[:, :, 3], category[:, :, 3]) or pixels[:, :, :3][~mask].any():
            raise ValueError("evidence support differs from the category visibility mask")
        if item.get("valid_cells") != int(mask.sum()) or item.get("max_count") != (
            int(decoded[mask].max()) if mask.any() else 0
        ):
            raise ValueError("evidence count summary differs from decoded pixels")
        attached = tier.get("evidence")
        if attached is not None and attached != {
            "png": name,
            "sha256": item["sha256"],
            "observations_sha256": fingerprint,
        }:
            raise ValueError("evidence differs from the published sidecar attachment")
    return {**document, "manifest_sha256": hashlib.sha256(payload).hexdigest()}


def validate_public_evidence(data_dir, evidence_dir):
    """Verify a pinned producer manifest and public bytes without source TIFF reads."""
    try:
        return _validate_public_evidence(data_dir, evidence_dir)
    except (KeyError, TypeError, AttributeError, OSError) as exc:
        raise ValueError("malformed or missing Getting Even evidence artifacts") from exc


def validate_evidence(data_dir, evidence_dir, *, reference_manifest=None):
    """Additionally rederive every visible count from source inputs and provenance."""
    result = validate_public_evidence(data_dir, evidence_dir)
    _, published = _published(Path(data_dir))
    expected = _source(Path(data_dir), published, reference_manifest)
    for resolution, (pixels, item) in expected.items():
        recorded = result["tiers"][resolution]
        if any(recorded[key] != value for key, value in item.items()) or not np.array_equal(
            pixels, _image(Path(evidence_dir) / item["png"], pixels.shape[:2])
        ):
            raise ValueError("evidence decoded counts differ from the actual source inputs")
    return result


def build_evidence(data_dir, output_dir, *, reference_manifest=None):
    """Build both tiers only after every source/category binding has been checked."""
    directory, output = Path(data_dir), Path(output_dir)
    sidecar, published = _published(directory)
    expected = _source(directory, published, reference_manifest)
    files, tiers = {}, {}
    for resolution, (pixels, item) in expected.items():
        stream = io.BytesIO()
        Image.fromarray(pixels).save(stream, format="PNG")
        files[item["png"]] = stream.getvalue()
        tiers[resolution] = {**item, "sha256": hashlib.sha256(files[item["png"]]).hexdigest()}
    document = {
        "schema_version": 1,
        "kind": "gettingeven-source-counts",
        "encoding": "rgb24",
        "source": SOURCE,
        "category_json": {"path": SCORE_JSON, "sha256": _sha(directory / SCORE_JSON)},
        "tiers": tiers,
    }
    files[MANIFEST] = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as staging:
        for name, payload in files.items():
            (Path(staging) / name).write_bytes(payload)
        validate_evidence(directory, staging, reference_manifest=reference_manifest)
        for name in files:  # Manifest is installed last after both PNGs.
            os.replace(Path(staging) / name, output / name)
    if output.resolve() == directory.resolve():
        for resolution, item in tiers.items():
            sidecar["tiers"][resolution]["evidence"] = {
                key: item[key] for key in ("png", "sha256", "observations_sha256")
            }
        (directory / "gettingeven_grid.json").write_text(json.dumps(sidecar, sort_keys=True, indent=1) + "\n")
    return validate_public_evidence(directory, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path)
    args = parser.parse_args()
    build_evidence(args.data_dir, args.output_dir, reference_manifest=args.reference_manifest)
