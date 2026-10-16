"""Bind frozen aggregate counts to saved source provenance and published output bytes."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from prepare_gettingeven_reference import prepare

ROOT = Path(__file__).parent


def reference(tmp_path, change=None):
    doc = json.loads((ROOT / "gettingeven_reference.json").read_bytes())
    output, saved = tmp_path / "output", tmp_path / "saved"
    output.mkdir()
    saved.mkdir()
    source = json.loads((ROOT / "cluster_results/ca/gettingeven_grid.json").read_bytes())
    for name in doc["published_outputs_sha256"]:
        data = name.encode()
        (output / name).write_bytes(data)
        doc["published_outputs_sha256"][name] = hashlib.sha256(data).hexdigest()
    for resolution, tier in source["tiers"].items():
        tier["sha256"] = doc["published_outputs_sha256"][tier["png"]]
        mask = tier["gate_mask"]
        data = mask["png"].encode()
        (saved / mask["png"]).write_bytes(data)
        mask["sha256"] = hashlib.sha256(data).hexdigest()
    source_path = saved / "gettingeven_grid.json"
    source_path.write_text(json.dumps(source))
    files = {name: name.encode() for name in doc["files"]}
    doc["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if change == "wrong-role":
        files["grid_5000m/Plants.tif"] = files.pop("grid_5000m/Plantae.tif")
    elif change == "corrupt-band":
        files["grid_5000m/Plantae.tif"] = b"another group's counts"
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as stream:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    content = archive.getvalue()
    doc["sha256"] = hashlib.sha256(content).hexdigest()
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(doc))
    return path, content, files, source_path


def test_reference_provenance_matches_both_shipped_observation_populations():
    doc = json.loads((ROOT / "gettingeven_reference.json").read_bytes())
    sidecar = json.loads((ROOT / "cluster_results/ca/gettingeven_grid.json").read_bytes())
    assert doc["provenance"]["observations_sha256"] == {
        res: tier["inputs"]["observations_sha256"] for res, tier in sidecar["tiers"].items()
    }
    for name, checksum in doc["published_outputs_sha256"].items():
        assert hashlib.sha256((ROOT / "cluster_results/ca" / name).read_bytes()).hexdigest() == checksum
    assert doc["source"].endswith("-" + doc["sha256"] + ".tar.gz")


def test_verified_reference_restores_exact_count_bands(tmp_path):
    manifest, content, files, source = reference(tmp_path)
    output = tmp_path / "output"
    prepare(manifest, output, source_sidecar=source, fetch=lambda _: content)
    assert {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*.tif")} == files


@pytest.mark.parametrize("change", ["wrong-role", "corrupt-band", "corrupt-archive"])
def test_bad_reference_leaves_existing_counts_untouched(tmp_path, change):
    manifest, content, _, source = reference(tmp_path, change)
    output = tmp_path / "output"
    sentinel = output / "grid_5000m/All_biodiversity.tif"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"last good counts")
    if change == "corrupt-archive":
        content += b"changed"
    with pytest.raises(ValueError, match="checksum|exactly"):
        prepare(manifest, output, source_sidecar=source, fetch=lambda _: content)
    assert list(output.rglob("*.tif")) == [sentinel]
    assert sentinel.read_bytes() == b"last good counts"


@pytest.mark.parametrize("change", ["missing-role", "missing-provenance", "bad-fingerprint"])
def test_invalid_reference_manifest_abstains_before_network(tmp_path, change):
    path, _, _, source = reference(tmp_path)
    doc = json.loads(path.read_bytes())
    if change == "missing-role":
        doc["files"].pop("grid_5000m/Fungi.tif")
    elif change == "missing-provenance":
        doc["provenance"] = {}
    else:
        doc["provenance"]["observations_sha256"] = {"25000": "not a digest", "5000": "a" * 64}
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        prepare(path, tmp_path / "output", source_sidecar=source, fetch=lambda _: pytest.fail("invalid manifest fetched"))


@pytest.mark.parametrize("resolution", ["25000", "5000"])
def test_different_saved_observation_population_abstains_before_install(tmp_path, resolution):
    path, _, _, source = reference(tmp_path)
    doc = json.loads(source.read_bytes())
    doc["tiers"][resolution]["inputs"]["observations_sha256"] = "a" * 64
    source.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="observations differ"):
        prepare(path, tmp_path / "output", source_sidecar=source, fetch=lambda _: pytest.fail("mismatched provenance fetched"))
    assert not list((tmp_path / "output").rglob("*.tif"))


@pytest.mark.parametrize("name", ["webapp_data_gettingeven.json", "gettingeven_grid_25000m.png", "gettingeven_grid_5000m.png"])
def test_changed_public_baseline_abstains_before_install(tmp_path, name):
    path, _, _, source = reference(tmp_path)
    (tmp_path / "output" / name).write_bytes(b"new published grid")
    with pytest.raises(ValueError, match="published outputs differ"):
        prepare(path, tmp_path / "output", source_sidecar=source, fetch=lambda _: pytest.fail("changed baseline fetched"))
    assert not list((tmp_path / "output").rglob("*.tif"))


def test_corrupt_saved_gate_mask_abstains_before_install(tmp_path):
    path, _, _, source = reference(tmp_path)
    (source.parent / "gettingeven_gate_5000m.png").write_bytes(b"changed exclusions")
    with pytest.raises(ValueError, match="gate mask checksum"):
        prepare(path, tmp_path / "output", source_sidecar=source, fetch=lambda _: pytest.fail("changed gate fetched"))
    assert not list((tmp_path / "output").rglob("*.tif"))
