"""The frozen replay must fail before analysis on provenance drift and never replace results."""

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import verify_appscore_results as replay


@pytest.fixture
def benchmark(tmp_path, monkeypatch):
    """Complete eight-case manifests with small inputs and a stubbed expensive analysis."""
    root = tmp_path / "repo"
    data = root / "cluster_results" / "ca"
    inputs = tmp_path / "inputs"
    data.mkdir(parents=True)
    inputs.mkdir()
    for name in replay.SOURCE_FILES:
        (root / name).write_text(f"frozen source: {name}\n")
    taxa = set(taxon for group in replay.CASES.values() for taxon in group)
    (data / "index.json").write_text(json.dumps({"files": {taxon: f"{taxon}.json" for taxon in taxa}}))
    (data / "us_cells.json").write_text('{"us_cells": []}')
    for taxon in taxa:
        (data / f"{taxon}.json").write_text(json.dumps({"group": taxon}))
    records, inventories = {}, {}
    for region, filename in replay.RESULT_FILES.items():
        expected = json.loads((replay.ROOT / "cluster_results" / filename).read_text())
        for record in expected:
            case = replay.BenchmarkCase(region, record["taxon"], record)
            path = inputs / case.filename
            path.write_text("taxon_id\n1\n")
            record["run"]["input_sha256"] = replay.file_sha256(path)
            record["run"]["source_sha256"] = {
                name: replay.file_sha256(root / name) for name in replay.SOURCE_FILES
            }
            record["run"]["grid"]["source_sha256"] = {
                name: replay.file_sha256(data / asset)
                for name, asset in (
                    ("index", "index.json"),
                    ("canada_mask", "us_cells.json"),
                    ("group", f"{case.taxon}.json"),
                )
            }
            records[case.filename] = record
            inventories[case.taxon] = record["run"]["grid"]
        (root / "cluster_results" / filename).write_text(json.dumps(expected))
    calls, reports, downloads = [], [], []

    def analyse(taxon, frame, data_dir, input_path):
        assert data_dir == data
        assert list(frame.columns) == ["taxon_id"]
        calls.append(input_path.name)
        result = copy.deepcopy(records[input_path.name])
        assert result["taxon"] == taxon
        result.pop("region")
        return result, None

    def urlopen(url, timeout):
        assert timeout == 60
        downloads.append(url)
        return io.BytesIO(b"taxon_id\n1\n")

    monkeypatch.setattr(
        replay.bas,
        "AppGrid",
        lambda taxon, directory: SimpleNamespace(metadata=copy.deepcopy(inventories[taxon])),
    )
    monkeypatch.setattr(replay.bas, "analyse", analyse)
    monkeypatch.setattr(replay.urllib.request, "urlopen", urlopen)
    return SimpleNamespace(
        root=root,
        data=data,
        inputs=inputs,
        records=records,
        inventories=inventories,
        calls=calls,
        reports=reports,
        downloads=downloads,
    )


def write_manifest(benchmark, region, transform):
    path = benchmark.root / "cluster_results" / replay.RESULT_FILES[region]
    records = json.loads(path.read_text())
    transform(records)
    path.write_text(json.dumps(records))


def verify(benchmark, **options):
    return replay.verify(
        root=benchmark.root, inputs_dir=benchmark.inputs, report=benchmark.reports.append, **options
    )


def test_complete_replay_compares_all_cases_and_preserves_every_file(benchmark):
    before = {path: path.read_bytes() for path in benchmark.root.rglob("*") if path.is_file()}
    assert verify(benchmark) == 8
    assert len(benchmark.calls) == 8
    assert benchmark.downloads == []
    assert all(path.read_bytes() == content for path, content in before.items())
    assert "all 8 frozen retrospective sampled-record benchmarks" in benchmark.reports[-1]
    assert "5 km inventory or prospective trip yield" in benchmark.reports[-1]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda records: records.pop(), "expected exactly eight cases"),
        (lambda records: records.append(copy.deepcopy(records[0])), "duplicate taxon-region"),
        (lambda records: records[0].update(region="East"), "unexpected taxon-region"),
        (lambda records: records[0].update(taxon="Fungi"), "unexpected taxon-region"),
    ],
)
def test_case_set_fails_before_download_or_analysis(benchmark, mutation, message):
    write_manifest(benchmark, "BC", mutation)
    with pytest.raises(replay.VerificationError, match=message):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize(
    "section, field, value",
    [
        ("run", "split", "2025-07-02"),
        ("run", "seed", True),
        ("run", "permutation_seed", 8),
        ("run", "n_permutations", 2001),
        ("run", "K", 6),
        ("run", "rarefaction_method", "Monte Carlo"),
        ("run", "rarefaction_reps", 200),
        ("run", "rarefaction_seed", 0),
        ("run", "input_sha256", None),
        ("training_history_sensitivity", "M", 21),
        ("training_history_sensitivity", "K", 6),
        ("training_history_sensitivity", "permutation_seed", 8),
        ("training_history_sensitivity", "n_permutations", 2001),
    ],
)
def test_fixed_configuration_and_hash_schema_fail_before_analysis(benchmark, section, field, value):
    write_manifest(benchmark, "BC", lambda records: records[0][section].update({field: value}))
    with pytest.raises(replay.VerificationError, match=field):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("spearman", 1.0001),
        ("perm_p", 0.0),
        ("new_at_K_top", 5.0001),
        ("new_at_K_bottom", -0.01),
        ("eff_ratio_top_bottom", float("inf")),
        ("spearman", True),
    ],
)
def test_scientific_schema_bounds_fail_before_analysis(benchmark, field, value):
    write_manifest(
        benchmark, "BC", lambda records: records[0]["scores"]["app_shipped"].update({field: value})
    )
    with pytest.raises(replay.VerificationError):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda records: records[0].pop("scores"),
        lambda records: records[0].update(unexpected="value"),
        lambda records: records[0]["run"]["source_sha256"].pop("grid_schema.py"),
        lambda records: records[0]["run"]["versions"].update(pandas=None),
        lambda records: records[0]["training_history_sensitivity"].pop("unavailable_reason"),
    ],
)
def test_incomplete_or_extra_manifest_schema_is_rejected(benchmark, mutation):
    write_manifest(benchmark, "BC", mutation)
    with pytest.raises(replay.VerificationError):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize("source", replay.SOURCE_FILES)
def test_each_analysis_source_hash_is_checked_before_analysis(benchmark, source):
    (benchmark.root / source).write_text("changed")
    with pytest.raises(replay.VerificationError, match="source_sha256.*SHA-256 mismatch"):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize(
    "asset, field",
    [
        ("index.json", "index"),
        ("us_cells.json", "canada_mask"),
        ("Mammalia.json", "group"),
    ],
)
def test_each_grid_asset_hash_is_checked_before_analysis(benchmark, asset, field):
    (benchmark.data / asset).write_text("changed")
    with pytest.raises(replay.VerificationError, match=f"source_sha256.{field}.*SHA-256 mismatch"):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("inventory_sha256", "0" * 64),
        ("n_inventory_cells", 23215),
        ("n_eligible_cells", 18300),
    ],
)
def test_normalized_inventory_and_counts_are_checked_before_analysis(benchmark, monkeypatch, field, value):
    metadata = copy.deepcopy(benchmark.inventories["Reptilia"])
    metadata[field] = value
    monkeypatch.setattr(
        replay.bas,
        "AppGrid",
        lambda taxon, directory: SimpleNamespace(
            metadata=metadata if taxon == "Reptilia" else copy.deepcopy(benchmark.inventories[taxon])
        ),
    )
    with pytest.raises(replay.VerificationError, match=field):
        verify(benchmark, download=True)
    assert benchmark.calls == benchmark.downloads == []


def test_corrupt_last_cached_input_fails_before_any_analysis_or_download(benchmark):
    path = benchmark.inputs / "inat_east_Mammalia.csv"
    path.write_bytes(b"corrupt")
    (benchmark.inputs / "inat_Amphibia.csv").unlink()
    with pytest.raises(replay.VerificationError, match="East/Mammalia.*input_sha256.*SHA-256 mismatch"):
        verify(benchmark, download=True)
    assert path.read_bytes() == b"corrupt"
    assert benchmark.calls == benchmark.downloads == []


def test_missing_local_input_does_not_authorize_network(benchmark):
    (benchmark.inputs / "inat_Amphibia.csv").unlink()
    with pytest.raises(replay.VerificationError, match="missing inat_Amphibia.csv.*--download"):
        verify(benchmark)
    assert benchmark.calls == benchmark.downloads == []


def test_download_only_missing_asset_and_never_replaces_cached_inputs(benchmark):
    path = benchmark.inputs / "inat_Amphibia.csv"
    path.unlink()
    existing = benchmark.inputs / "inat_Aves.csv"
    existing_inode = existing.stat().st_ino
    assert verify(benchmark, download=True) == 8
    assert benchmark.downloads == [f"{replay.RELEASE}/inat_Amphibia.csv"]
    assert existing.stat().st_ino == existing_inode
    assert path.read_bytes() == b"taxon_id\n1\n"
    assert list(benchmark.inputs.glob("*.part")) == []


def test_download_checksum_failure_installs_nothing_and_cleans_temporary_file(benchmark, monkeypatch):
    path = benchmark.inputs / "inat_Amphibia.csv"
    path.unlink()
    monkeypatch.setattr(replay.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong input"))
    with pytest.raises(replay.VerificationError, match="download SHA-256 mismatch"):
        verify(benchmark, download=True)
    assert not path.exists()
    assert list(benchmark.inputs.glob("*.part")) == []
    assert benchmark.calls == []


def test_atomic_install_refuses_a_racing_destination_and_cleans_temporary_file(benchmark, monkeypatch):
    path = benchmark.inputs / "inat_Amphibia.csv"
    path.unlink()
    original_link = replay.os.link

    def racing_link(source, destination):
        destination.write_bytes(b"another writer")
        original_link(source, destination)

    monkeypatch.setattr(replay.os, "link", racing_link)
    with pytest.raises(replay.VerificationError, match="existing files are never overwritten"):
        verify(benchmark, download=True)
    assert path.read_bytes() == b"another writer"
    assert list(benchmark.inputs.glob("*.part")) == []
    assert benchmark.calls == []


def test_default_download_cache_is_temporary_and_does_not_touch_repo(benchmark, monkeypatch):
    temporary_directories = []
    original = replay.tempfile.TemporaryDirectory

    def temporary_directory(**options):
        directory = original(**options)
        temporary_directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(replay.tempfile, "TemporaryDirectory", temporary_directory)
    assert replay.verify(root=benchmark.root, download=True, report=benchmark.reports.append) == 8
    assert len(benchmark.downloads) == 8
    assert not temporary_directories[0].exists()
    assert list((benchmark.root / "cluster_results").glob("inat*.csv")) == []


def test_changed_scientific_field_reports_its_full_json_path(benchmark, monkeypatch):
    original = replay.bas.analyse

    def changed(*args, **kwargs):
        actual, cells = original(*args, **kwargs)
        actual["scores"]["app_shipped"]["spearman"] += 0.01
        return actual, cells

    monkeypatch.setattr(replay.bas, "analyse", changed)
    with pytest.raises(replay.VerificationError, match=r"\$\[BC/Amphibia\].scores.app_shipped.spearman"):
        verify(benchmark)


def test_runtime_versions_report_separately_without_claiming_identical_environment(benchmark, monkeypatch):
    original = replay.bas.analyse

    def changed(*args, **kwargs):
        actual, cells = original(*args, **kwargs)
        actual["run"]["versions"]["pandas"] = "different-version"
        return actual, cells

    monkeypatch.setattr(replay.bas, "analyse", changed)
    assert verify(benchmark) == 8
    assert any("archived:" in line for line in benchmark.reports)
    assert any("different-version" in line for line in benchmark.reports)
    assert any("does not establish an identical environment" in line for line in benchmark.reports)


@pytest.mark.parametrize(
    "expected, actual, path",
    [
        ({"a": None}, {"a": 0.0}, "$.a"),
        ({"a": 1}, {"a": True}, "$.a"),
        ({"a": 1}, {"a": 1.0}, "$.a"),
        ({"a": "hash"}, {"a": "other"}, "$.a"),
        ({"a": [1]}, {"a": [2]}, "$.a[0]"),
        ({"a": 1}, {}, "$"),
        ({"a": 1}, {"a": 1, "extra": 2}, "$"),
        ({"a": [1]}, {"a": []}, "$.a"),
        ({"a": 1.0}, {"a": 1.0 + 1e-8}, "$.a"),
        ({"a": 1.0}, {"a": float("nan")}, "$.a"),
    ],
)
def test_comparison_rejects_null_type_key_value_and_array_changes(expected, actual, path):
    with pytest.raises(replay.VerificationError) as error:
        replay.compare_json(expected, actual)
    assert str(error.value).startswith(path + ":")


def test_comparison_accepts_only_float_rounding_within_tolerance():
    replay.compare_json(
        {"float": 1.0, "null": None, "count": 0}, {"float": 1.0 + 5e-13, "null": None, "count": 0}
    )


def test_cli_returns_nonzero_with_clear_json_path(benchmark, monkeypatch, capsys):
    monkeypatch.setattr(
        replay,
        "verify",
        lambda **options: (_ for _ in ()).throw(
            replay.VerificationError("$.scores.app_shipped.spearman: value differs")
        ),
    )
    assert replay.main(["--inputs-dir", str(benchmark.inputs)]) == 1
    assert "$.scores.app_shipped.spearman" in capsys.readouterr().err
