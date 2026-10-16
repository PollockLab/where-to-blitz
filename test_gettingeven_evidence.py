"""Exact source-count evidence is bound to the displayed Getting Even tier."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image

import gettingeven_evidence as evidence
from gettingeven_inputs import observation_hash
from grid_lattice import Lattice


@pytest.fixture
def source(tmp_path, monkeypatch):
    directory = tmp_path / "source"
    directory.mkdir()
    crs = rasterio.CRS.from_string("+proj=laea +lat_0=45 +lon_0=-100 +datum=WGS84 +units=m +no_defs")
    (directory / "index.json").write_text(json.dumps({"crs": crs.to_wkt()}))
    (directory / "webapp_data_gettingeven.json").write_text('{"All biodiversity":[]}')
    sidecar, inputs = {"tiers": {}}, {}
    for resolution in (25000, 5000):
        lattice = Lattice(resolution, crs)
        total = np.array([0, 65536, 458040 if resolution == 25000 else 108513, 16777215])
        counts = np.zeros((4, 6), dtype=int)
        rowcol = np.array([[0, 0], [1, 1], [2, 2], [3, 3]])
        payload = json.dumps(total.tolist()).encode()
        name = f"source-{resolution}.json"
        (directory / name).write_bytes(payload)
        used = {name: hashlib.sha256(payload).hexdigest()}
        inputs[resolution] = (total, counts, rowcol, used)
        rgba = np.zeros((*lattice.shape, 4), dtype=np.uint8)
        rgba[rowcol[:, 0], rowcol[:, 1], 3] = 255
        rgba[0, 0, 0] = 255  # A real unscored zero is distinct from missing support.
        rgba[2, 2, 3] = 0  # A foreign/off-border cell must not leak its source count.
        name = f"gettingeven_grid_{resolution}m.png"
        Image.fromarray(rgba).save(directory / name)
        sidecar["tiers"][str(resolution)] = {
            "png": name,
            "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
            "lattice": {
                "x0": lattice.x0,
                "y1": lattice.y1,
                "res_m": resolution,
                "nrow": lattice.nrow,
                "ncol": lattice.ncol,
                "crs": crs.to_wkt(),
            },
            "inputs": {
                "observations_sha256": observation_hash((rowcol[:, 0], rowcol[:, 1]), total, counts.T)
            },
        }
    (directory / "gettingeven_grid.json").write_text(json.dumps(sidecar))
    monkeypatch.setattr(evidence, "load_inputs", lambda _, resolution: inputs[resolution])
    return directory, inputs


def test_both_tiers_roundtrip_rgb24_zero_missing_and_maximum_without_changing_winners(source, tmp_path):
    directory, _ = source
    before = {path.name: path.read_bytes() for path in directory.glob("gettingeven_grid*")}
    output = tmp_path / "evidence"
    descriptor = evidence.build_evidence(directory, output)
    assert descriptor == evidence.validate_evidence(directory, output)
    assert descriptor == evidence.validate_public_evidence(directory, output)
    assert set(descriptor["tiers"]) == {"5000", "25000"}
    for resolution in (5000, 25000):
        tier = descriptor["tiers"][str(resolution)]
        image = np.asarray(Image.open(output / tier["png"]))
        assert image[0, 0].tolist() == [0, 0, 0, 255]
        assert image[1, 1].tolist() == [1, 0, 0, 255]
        assert image[2, 2].tolist() == [0, 0, 0, 0]
        assert image[3, 3].tolist() == [255, 255, 255, 255]
        assert image[4, 4].tolist() == [0, 0, 0, 0]
        assert tier["max_count"] == 16777215 and tier["valid_cells"] == 3
    assert before == {path.name: path.read_bytes() for path in directory.glob("gettingeven_grid*")}


@pytest.mark.parametrize(
    "change", ["overflow", "fraction", "nan", "infinity", "negative", "duplicate", "bounds", "boolean"]
)
def test_invalid_counts_or_indices_fail_before_replacing_any_evidence(source, tmp_path, change):
    directory, inputs = source
    total, counts, rowcol, used = inputs[5000]
    total = total.astype(float)
    if change in {"overflow", "fraction", "nan", "infinity", "negative"}:
        total[1] = {"overflow": 16777216, "fraction": 0.5, "nan": np.nan, "infinity": np.inf, "negative": -1}[
            change
        ]
    elif change == "duplicate":
        rowcol[1] = rowcol[0]
    elif change == "bounds":
        rowcol[1] = [905, 1100]
    else:
        total = total.astype(bool)
    inputs[5000] = (total, counts, rowcol, used)
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "gettingeven_evidence.json"
    sentinel.write_bytes(b"last good")
    with pytest.raises(ValueError):
        evidence.build_evidence(directory, output)
    assert list(output.iterdir()) == [sentinel] and sentinel.read_bytes() == b"last good"


@pytest.mark.parametrize("change", ["fingerprint", "category-hash", "dimensions", "lattice", "tier", "alpha"])
def test_source_and_published_category_mismatches_block_generation(source, tmp_path, change):
    directory, _ = source
    path = directory / "gettingeven_grid.json"
    sidecar = json.loads(path.read_bytes())
    tier = sidecar["tiers"]["5000"]
    png = directory / tier["png"]
    if change == "fingerprint":
        tier["inputs"]["observations_sha256"] = "0" * 64
    elif change == "category-hash":
        tier["sha256"] = "0" * 64
    elif change == "lattice":
        tier["lattice"]["x0"] += 5000
    elif change == "tier":
        tier["lattice"]["res_m"] = 25000
    else:
        pixels = np.asarray(Image.open(png)).copy()
        if change == "dimensions":
            pixels = pixels[:-1]
        else:
            pixels[0, 0, 3] = 128
        Image.fromarray(pixels).save(png)
        tier["sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
    path.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError):
        evidence.build_evidence(directory, tmp_path / "output")


@pytest.mark.parametrize(
    "change", ["count-hash", "category-json", "category-png", "lattice", "mask", "wrong-tier", "dimensions"]
)
def test_public_validation_rejects_corrupt_or_misbound_artifacts(source, tmp_path, change):
    directory, _ = source
    output = tmp_path / "output"
    evidence.build_evidence(directory, output)
    path = output / "gettingeven_evidence.json"
    manifest = json.loads(path.read_bytes())
    item = manifest["tiers"]["5000"]
    if change == "count-hash":
        item["sha256"] = "0" * 64
    elif change == "category-json":
        (directory / "webapp_data_gettingeven.json").write_text("different published winners")
    elif change == "category-png":
        item["category_sha256"] = "0" * 64
    elif change == "lattice":
        item["lattice"]["nrow"] += 1
    else:
        target = output / item["png"]
        pixels = np.asarray(Image.open(target)).copy()
        if change == "mask":
            pixels[0, 0, 3] = 0
        elif change == "dimensions":
            pixels = pixels[:-1]
        else:
            pixels = np.asarray(Image.open(output / manifest["tiers"]["25000"]["png"]))
        Image.fromarray(pixels).save(target)
        item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        evidence.validate_public_evidence(directory, output)


def test_strong_validation_checks_every_decoded_count_while_public_check_needs_no_source_files(
    source, tmp_path, monkeypatch
):
    directory, _ = source
    output = tmp_path / "output"
    evidence.build_evidence(directory, output)
    path = output / "gettingeven_evidence.json"
    manifest = json.loads(path.read_bytes())
    item = manifest["tiers"]["5000"]
    png = output / item["png"]
    pixels = np.asarray(Image.open(png)).copy()
    pixels[1, 1, 2] = 1  # 65537, without changing the maximum or support summary.
    Image.fromarray(pixels).save(png)
    item["sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="actual source"):
        evidence.validate_evidence(directory, output)
    for source_file in directory.glob("source-*.json"):
        source_file.unlink()
    monkeypatch.setattr(evidence, "load_inputs", lambda *_: pytest.fail("fast deploy read source counts"))
    # The public boundary relies on the caller's pinned producer manifest, not source TIFFs.
    assert evidence.validate_public_evidence(directory, output)["tiers"]["5000"]["sha256"] == item["sha256"]


def test_in_place_full_build_attaches_evidence_without_mutating_any_winner(source):
    directory, _ = source
    path = directory / "gettingeven_grid.json"
    old = json.loads(path.read_bytes())
    winners = {
        name: (directory / name).read_bytes()
        for name in (
            "gettingeven_grid_5000m.png",
            "gettingeven_grid_25000m.png",
            "webapp_data_gettingeven.json",
        )
    }
    result = evidence.build_evidence(directory, directory)
    updated = json.loads(path.read_bytes())
    for resolution, tier in updated["tiers"].items():
        attached = tier.pop("evidence")
        assert attached["sha256"] == result["tiers"][resolution]["sha256"]
    assert updated == old
    assert all((directory / name).read_bytes() == value for name, value in winners.items())


def frozen_reference(directory):
    reference = json.loads((Path(__file__).parent / "gettingeven_reference.json").read_bytes())
    for name in reference["files"]:
        target = directory / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(name.encode())
        reference["files"][name] = hashlib.sha256(target.read_bytes()).hexdigest()
    for name in reference["published_outputs_sha256"]:
        reference["published_outputs_sha256"][name] = hashlib.sha256(
            (directory / name).read_bytes()
        ).hexdigest()
    sidecar_path = directory / "gettingeven_grid.json"
    sidecar = json.loads(sidecar_path.read_bytes())
    for resolution, tier in sidecar["tiers"].items():
        reference["provenance"]["observations_sha256"][resolution] = tier.pop("inputs")["observations_sha256"]
    sidecar_path.write_text(json.dumps(sidecar))
    path = directory / "trusted-reference.json"
    path.write_text(json.dumps(reference))
    return path


@pytest.mark.parametrize("change", [None, "missing-trust", "count-band", "published-json", "population"])
def test_legacy_frozen_evidence_requires_explicit_trusted_output_and_count_pins(source, tmp_path, change):
    directory, _ = source
    reference = frozen_reference(directory)
    if change == "count-band":
        (directory / "grid_5000m/All_biodiversity.tif").write_bytes(b"different source population")
    elif change == "published-json":
        (directory / "webapp_data_gettingeven.json").write_bytes(b"different winners")
    elif change == "population":
        document = json.loads(reference.read_bytes())
        document["provenance"]["observations_sha256"]["5000"] = "0" * 64
        reference.write_text(json.dumps(document))
    output = tmp_path / "output"
    if change is not None:
        with pytest.raises(ValueError):
            evidence.build_evidence(
                directory, output, reference_manifest=None if change == "missing-trust" else reference
            )
    else:
        result = evidence.build_evidence(directory, output, reference_manifest=reference)
        assert result["tiers"]["5000"]["provenance"]["basis"] == "frozen-reference"
        assert evidence.validate_public_evidence(directory, output) == result
