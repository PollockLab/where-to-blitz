"""Count evidence publication preserves authentic bindings and the last good files."""

import hashlib
import json
import shutil

import numpy as np
import pytest
from PIL import Image

from gettingeven_evidence import build_evidence
from prepare_gettingeven_evidence import load_installed, prepare
from test_gettingeven_evidence import source as source


@pytest.fixture
def publication(source, tmp_path):
    directory, _ = source
    output = tmp_path / "published"
    manifest = build_evidence(directory, output)
    base = "https://github.com/PollockLab/where-to-blitz/releases/download/grid-inputs-v1/"
    values = {}
    config = {"schema_version": 1, "counts": {}, "published_outputs_sha256": {}}
    content = (output / "gettingeven_evidence.json").read_bytes()
    config["manifest"] = {"url": base + "evidence.json", "sha256": hashlib.sha256(content).hexdigest()}
    values[config["manifest"]["url"]] = content
    for res, tier in manifest["tiers"].items():
        config["counts"][res] = {"url": base + tier["png"], "sha256": tier["sha256"]}
        values[config["counts"][res]["url"]] = (output / tier["png"]).read_bytes()
        config["published_outputs_sha256"][tier["category_png"]] = tier["category_sha256"]
    config["published_outputs_sha256"]["webapp_data_gettingeven.json"] = manifest["category_json"]["sha256"]
    path = tmp_path / "source.json"
    path.write_text(json.dumps(config))
    return directory, output, path, config, values


def test_pinned_publication_installs_both_tiers_and_reuses_only_the_pinned_manifest(publication):
    directory, _, path, _, values = publication
    expected = prepare(directory, path, fetch=values.__getitem__)
    assert expected["tiers"]["25000"]["max_count"] == 16777215
    assert (
        prepare(directory, path, fetch=lambda _: pytest.fail("verified installed evidence fetched"))
        == expected
    )
    assert load_installed(directory, path) == expected


@pytest.mark.parametrize("change", ["manifest", "image", "support", "missing_download"])
def test_invalid_publication_does_not_replace_existing_unadvertised_images(publication, change):
    directory, _, path, config, values = publication
    sentinel = directory / "gettingeven_counts_25000m.png"
    sentinel.write_bytes(b"last good unadvertised image")
    if change == "manifest":
        values[config["manifest"]["url"]] += b"changed"
    elif change == "image":
        values[config["counts"]["5000"]["url"]] += b"changed"
    elif change == "missing_download":
        values.pop(config["counts"]["5000"]["url"])
    else:
        import io

        url = config["counts"]["5000"]["url"]
        pixels = np.array(Image.open(io.BytesIO(values[url])))
        pixels[4, 4] = [0, 0, 1, 255]
        image = io.BytesIO()
        Image.fromarray(pixels).save(image, format="PNG")
        values[url] = image.getvalue()
        checksum = hashlib.sha256(values[url]).hexdigest()
        config["counts"]["5000"]["sha256"] = checksum
        manifest = json.loads(values[config["manifest"]["url"]])
        manifest["tiers"]["5000"]["sha256"] = checksum
        values[config["manifest"]["url"]] = json.dumps(manifest).encode()
        config["manifest"]["sha256"] = hashlib.sha256(values[config["manifest"]["url"]]).hexdigest()
        path.write_text(json.dumps(config))
    with pytest.raises((ValueError, KeyError)):
        prepare(directory, path, fetch=values.__getitem__)
    assert sentinel.read_bytes() == b"last good unadvertised image"
    assert not (directory / "gettingeven_evidence.json").exists()
    assert not list(directory.glob(".ge-evidence-*"))


def test_different_map_abstains_before_download_or_install(publication):
    directory, _, path, _, _ = publication
    (directory / "webapp_data_gettingeven.json").write_text('{"different":"vintage"}')
    assert prepare(directory, path, fetch=lambda _: pytest.fail("different map fetched counts")) is None
    assert not (directory / "gettingeven_evidence.json").exists()


def test_installed_tampering_cannot_bypass_config_pin_through_internal_consistency(publication):
    directory, _, path, _, values = publication
    prepare(directory, path, fetch=values.__getitem__)
    image_path = directory / "gettingeven_counts_5000m.png"
    pixels = np.array(Image.open(image_path))
    pixels[1, 1, 2] = 1  # 65536 -> 65537, below the unchanged maximum.
    Image.fromarray(pixels).save(image_path)
    manifest_path = directory / "gettingeven_evidence.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["tiers"]["5000"]["sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    for check in (load_installed, prepare):
        with pytest.raises(ValueError, match="pinned producer"):
            check(directory, path)


def test_native_producer_uses_current_sidecar_attachments_without_a_frozen_config(source, tmp_path):
    directory, _ = source
    expected = build_evidence(directory, directory)
    assert load_installed(directory, tmp_path / "absent-source.json") == expected
    assert prepare(directory, tmp_path / "absent-source.json") == expected


def test_legacy_installed_evidence_requires_a_publisher_pin(publication, tmp_path):
    directory, output, _, _, _ = publication
    for name in (
        "gettingeven_evidence.json",
        "gettingeven_counts_25000m.png",
        "gettingeven_counts_5000m.png",
    ):
        shutil.copyfile(output / name, directory / name)
    with pytest.raises(ValueError, match="pinned producer manifest"):
        load_installed(directory, tmp_path / "absent-source.json")
