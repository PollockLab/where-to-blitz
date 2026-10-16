"""Reproducible richness loading and stale-build rejection at the input boundary."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import build_gettingeven as builder
import gettingeven_inputs as inputs
from gettingeven_oracle import gate_bits, metric


@pytest.fixture(autouse=True)
def clean_richness_environment(monkeypatch):
    for name in (*inputs.RICHNESS_ENV.values(), "GE_RICHNESS_MANIFEST", "GE_RICHNESS_MANIFEST_SHA256"):
        monkeypatch.delenv(name, raising=False)


def manifest(tmp_path, monkeypatch):
    source = tmp_path / "rich.tif"
    source.write_bytes(b"controlled fixture, loader need not parse TIFF")
    item = {"source": source.name, "sha256": inputs.sha256_file(source), "provenance": "fixture"}
    doc = {"schema": "gettingeven-richness@1", "sources": {cat: dict(item) for cat in inputs.RICHNESS_ENV}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv("GE_RICHNESS_MANIFEST", str(path))
    return source, path, doc


def test_local_manifest_is_pinned_and_does_not_publish_private_paths(tmp_path, monkeypatch):
    source, _, _ = manifest(tmp_path, monkeypatch)
    paths, records = inputs.load_richness_inputs()
    assert set(paths) == set(inputs.RICHNESS_ENV)
    assert all(Path(path) == source for path in paths.values())
    assert str(tmp_path) not in json.dumps(records)
    source.write_bytes(b"different vintage")
    with pytest.raises(ValueError, match="checksum mismatch"):
        inputs.load_richness_inputs()


@pytest.mark.parametrize("change", ["missing-category", "bad-hash", "missing-provenance", "wrong-schema"])
def test_invalid_manifest_fails_before_building(tmp_path, monkeypatch, change):
    _, path, doc = manifest(tmp_path, monkeypatch)
    if change == "missing-category":
        del doc["sources"]["Plants"]
    elif change == "bad-hash":
        doc["sources"]["Plants"]["sha256"] = "not a checksum"
    elif change == "missing-provenance":
        del doc["sources"]["Plants"]["provenance"]
    else:
        doc["schema"] = "wrong"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        inputs.load_richness_inputs()


def test_hosted_manifest_requires_pin_before_any_network_request(monkeypatch):
    monkeypatch.setenv("GE_RICHNESS_MANIFEST", "https://example.org/manifest.json")
    monkeypatch.setattr(inputs, "_read", lambda _: pytest.fail("unpinned request"))
    with pytest.raises(ValueError, match="requires GE_RICHNESS_MANIFEST_SHA256"):
        inputs.load_richness_inputs()


def test_hosted_manifest_fetches_verified_content_and_repairs_corrupt_cache(tmp_path, monkeypatch):
    data = b"raster content"
    digest = hashlib.sha256(data).hexdigest()
    doc = {
        "schema": "gettingeven-richness@1",
        "sources": {
            cat: {"source": "rich.tif", "sha256": digest, "provenance": "fixture"}
            for cat in inputs.RICHNESS_ENV
        },
    }
    content = json.dumps(doc).encode()
    requests = []

    def read(url):
        requests.append(url)
        return content if url.endswith("manifest.json") else data

    monkeypatch.setenv("GE_RICHNESS_MANIFEST", "https://example.org/private/manifest.json")
    monkeypatch.setenv("GE_RICHNESS_MANIFEST_SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setenv("GE_RICHNESS_CACHE", str(tmp_path))
    monkeypatch.setattr(inputs, "_read", read)
    paths, _ = inputs.load_richness_inputs()
    assert requests == ["https://example.org/private/manifest.json", "https://example.org/private/rich.tif"]
    Path(paths["Plants"]).write_bytes(b"corrupted")
    inputs.load_richness_inputs()
    assert requests.count("https://example.org/private/rich.tif") == 2


def test_hosted_checksum_mismatch_is_never_cached(tmp_path, monkeypatch):
    data = b"expected"
    doc = {
        "schema": "gettingeven-richness@1",
        "sources": {
            cat: {
                "source": "https://example.org/rich.tif",
                "sha256": hashlib.sha256(data).hexdigest(),
                "provenance": "fixture",
            }
            for cat in inputs.RICHNESS_ENV
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv("GE_RICHNESS_MANIFEST", str(path))
    monkeypatch.setenv("GE_RICHNESS_CACHE", str(tmp_path))
    monkeypatch.setattr(inputs, "_read", lambda url: path.read_bytes() if url == str(path) else b"wrong")
    with pytest.raises(ValueError, match="richness checksum mismatch"):
        inputs.load_richness_inputs()
    assert not list(tmp_path.glob("gettingeven-richness/*.tif"))


def test_legacy_paths_must_supply_complete_gate(tmp_path, monkeypatch):
    source = tmp_path / "rich.tif"
    source.write_bytes(b"fixture")
    monkeypatch.setenv("GE_RICHNESS_HERP", str(source))
    with pytest.raises(ValueError, match="all three"):
        inputs.load_richness_inputs()


@pytest.mark.parametrize("source", ["http://example.org/a", "file:///private/a", "https://u:p@example.org/a"])
def test_hosted_loader_rejects_untrusted_schemes_and_embedded_credentials(source):
    with pytest.raises(ValueError, match="HTTPS"):
        inputs._https(source)


def test_observation_binding_changes_with_counts_cells_order_and_grouping():
    rows, cols = np.array([1, 2, 3]), np.array([4, 5, 6])
    total = np.array([5, 8, 12])
    counts = np.array([[2, 3, 4], [1, 2, 3]])
    original = inputs.observation_hash((rows, cols), total, counts)
    assert original == inputs.observation_hash((rows.astype(float), cols), total, counts)
    assert original != inputs.observation_hash((rows, cols + 1), total, counts)
    assert original != inputs.observation_hash((rows, cols), total + 1, counts)
    assert original != inputs.observation_hash((rows, cols), total, counts[::-1])


def test_preserving_bound_layer_rejects_changed_observations(tmp_path, monkeypatch):
    lattice = SimpleNamespace(
        crs=builder.rasterio.crs.CRS.from_epsg(4326), x0=1, y1=2, res=25000, ncol=3, nrow=4
    )
    png = tmp_path / "priority.png"
    png.write_bytes(b"unchanged output")
    shipped = {
        "richness_gate": ["Plants"],
        "png": png.name,
        "sha256": inputs.sha256_file(png),
        "lattice": {"crs": lattice.crs.to_wkt(), "x0": 1, "y1": 2, "res_m": 25000, "ncol": 3, "nrow": 4},
        "min_records": 1,
        "inputs": {"observations_sha256": "old", "richness_percentile": 25},
    }
    monkeypatch.setattr(builder, "OUT_DIR", str(tmp_path))
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"tiers": {str(builder.RES): shipped}}))
    monkeypatch.setattr(builder, "SIDECAR", str(sidecar))
    monkeypatch.setenv("GE_KEEP_SHIPPED", "1")
    assert builder._keep_shipped(lattice, None, shipped, [], {"observations_sha256": "old"})
    assert (
        json.loads(sidecar.read_text())["tiers"][str(builder.RES)]["build_status"]
        == "preserved-observations-verified"
    )
    with pytest.raises(SystemExit, match="observation counts changed"):
        builder._keep_shipped(lattice, None, shipped, [], {"observations_sha256": "new"})
    shipped["inputs"]["richness_percentile"] = 30
    with pytest.raises(SystemExit, match="richness percentile changed"):
        builder._keep_shipped(lattice, None, shipped, [], {"observations_sha256": "old"})
    del shipped["inputs"]
    sidecar.write_text(json.dumps({"tiers": {str(builder.RES): shipped}}))
    assert builder._keep_shipped(lattice, None, shipped, [], {"observations_sha256": "new"})
    preserved = json.loads(sidecar.read_text())["tiers"][str(builder.RES)]
    assert preserved["build_status"] == "legacy-preserved-inputs-unverified"
    assert "inputs" not in preserved


@pytest.mark.parametrize(
    "total, counts",
    [
        ([0, 0], [[0, 0]] * 6),
        ([1, np.nan], [[0, 0]] * 6),
        ([1, 2], [[-1, 0]] * 6),
        ([1, 2], [[np.nan, 0]] * 6),
    ],
)
def test_invalid_or_unscored_observations_fail_clearly(total, counts):
    with pytest.raises(SystemExit, match="observation counts|no cells meet"):
        builder.zscores(np.asarray(total), np.asarray(counts))


def test_gate_oracle_uses_linear_quartile_missing_zero_and_strict_less_than():
    scored = np.array([True, True, True, True, False])
    richness = {2: np.array([np.nan, 1, 2, 3, 100]), 4: np.array([1, 1, 1, 9, 0])}
    # Quartile is .75 for category2, exactly1 for category4. Equal-to-limit remains eligible.
    assert gate_bits(richness, scored).tolist() == [4, 0, 0, 0, 0]


def test_independent_metric_matches_builder_with_real_gate_and_sparse_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "MIN_RECORDS", 1)
    total = np.array([10, 20, 30, 40, 0.0])
    counts = np.array(
        [
            [1, 3, 2, 1, 0],
            [4, 2, 7, 4, 0],
            [2, 6, 2, 4, 0],
            [1, 4, 5, 8, 0],
            [2, 1, 4, 9, 0],
            [0, 4, 10, 14, 0],
        ]
    )
    z, scored = builder.zscores(total, counts)
    richness = {2: np.array([0, 1, 2, 3, 100]), 4: np.array([5, 8, 3, 2, 0]), 5: np.array([1, 2, 4, 3, 0])}
    paths = {builder.CATS[i]: str(tmp_path / str(i)) for i in richness}
    for path in paths.values():
        Path(path).touch()
    monkeypatch.setattr(
        builder, "reproject_mean", lambda path, *_a, **_k: [richness[int(Path(path).name)][None]]
    )
    builder.apply_richness_gate(z, None, (np.zeros(5, int), np.arange(5)), scored, paths)
    actual, actual_z = builder.priority(z, scored)
    expected, expected_z = metric(total, counts, 1, gate_bits(richness, scored))
    assert np.array_equal(actual, expected)
    assert np.allclose(actual_z, expected_z, equal_nan=True)
