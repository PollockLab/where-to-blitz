"""Exercise release publication and fast restoration without contacting GitHub."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from goal_presets import PRESETS

ROOT = Path(__file__).parent
BUNDLE = "grid_site_data_v1.tar.gz"


@pytest.fixture
def release_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("publish_grid_release.sh", "build_provenance.py", "goal_presets.py"):
        shutil.copy(ROOT / name, repo / name)
    ca = repo / "cluster_results" / "ca"
    (ca / "values").mkdir(parents=True)
    index = {
        "groups": ["All biodiversity"],
        "files": {"All biodiversity": "webapp_data_All_biodiversity.json"},
        "res_m": 25000,
        "n_cells": 1,
        "lattice": {"nrow": 1, "ncol": 1},
        "crs": "test",
    }
    (ca / "index.json").write_text(json.dumps(index))
    for name in (
        "breaks.json",
        "us_cells.json",
        "gettingeven_grid.json",
        "webapp_data_gettingeven.json",
        "ca_atrisk_provenance.json",
        "atrisk_shipping_policy.json",
    ):
        (ca / name).write_text(json.dumps({"build": "new", "name": name}))
    (ca / "webapp_data_All_biodiversity.json").write_text(json.dumps({"All biodiversity": [[45, -75, 1]]}))
    (ca / "ca_atrisk_richness.csv").write_text("lat,lon,richness\n45,-75,1\n")
    for name in ("gettingeven_grid_25000m.png", "gettingeven_grid_5000m.png", "land_sea_5000m.png"):
        (ca / name).write_bytes(b"new-mask")
    for preset in PRESETS:
        for res in (25000, 5000):
            slug = f"All_biodiversity_{preset['name'].lower().replace(' ', '_')}_grid_{res}m.png"
            (ca / "values" / slug).write_bytes(b"new-colours")
    subprocess.run([sys.executable, "build_provenance.py"], cwd=repo, check=True, capture_output=True)
    (repo / "density").mkdir()
    (repo / "density" / "All.pmtiles").write_bytes(b"existing-density")

    release = tmp_path / "release"
    release.mkdir()
    (release / "climate_gap.pmtiles").write_bytes(b"existing-climate")
    tools = tmp_path / "bin"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['GH_CALLS'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "release = pathlib.Path(os.environ['FAKE_RELEASE'])\n"
        "if args[:2] == ['release', 'upload']:\n"
        "    asset = pathlib.Path(args[3]); shutil.copy(asset, release / asset.name)\n"
        "elif args[:2] == ['release', 'download']:\n"
        "    asset = release / args[args.index('--pattern') + 1]\n"
        "    if not asset.exists(): sys.exit(1)\n"
        "    target = pathlib.Path(args[args.index('--dir') + 1]); target.mkdir(parents=True, exist_ok=True)\n"
        "    shutil.copy(asset, target / asset.name)\n"
        "elif args[:2] != ['release', 'view']:\n"
        "    sys.exit('unexpected publication command')\n"
    )
    gh.chmod(0o755)
    calls = tmp_path / "gh-calls.jsonl"
    env = {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "PYTHON": sys.executable,
        "SKIP_BUILD": "1",
        "FAKE_RELEASE": str(release),
        "GH_CALLS": str(calls),
    }
    return repo, ca, release, calls, env


def _run(fixture, *args):
    repo, _, _, _, env = fixture
    return subprocess.run(
        ["bash", "publish_grid_release.sh", *args], cwd=repo, env=env, capture_output=True, text=True
    )


def _calls(fixture):
    path = fixture[3]
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_publish_and_restore_keep_data_and_colours_from_same_build(release_repo):
    repo, ca, release, calls, env = release_repo
    expected = {p.relative_to(ca): p.read_bytes() for p in ca.rglob("*") if p.is_file()}
    artifact = repo / "artifacts" / "cluster_results" / "ca"
    shutil.copytree(ca, artifact)
    # Manual publication must read the rebuilt artifact even when checkout is older.
    for path in expected:
        if path.suffix in (".json", ".png"):
            (ca / path).write_bytes(b"tracked-old")
    env["GRID_DATA_DIR"] = str(artifact)
    result = _run(release_repo)
    assert result.returncode == 0, result.stderr
    assert (release / BUNDLE).is_file()
    assert (release / "grid_values.tar.gz").is_file()  # existing consumers retain their asset
    assert (release / "density_All.pmtiles").read_bytes() == b"existing-density"
    assert (release / "climate_gap.pmtiles").read_bytes() == b"existing-climate"

    env.pop("GRID_DATA_DIR")
    (ca / "stale_only.json").write_text("{}")
    (ca / "values" / "stale_only.png").write_bytes(b"old-colours")
    calls.unlink()
    result = _run(release_repo, "--restore")
    assert result.returncode == 0, result.stderr
    for path, content in expected.items():
        assert (ca / path).read_bytes() == content, path
    assert not (ca / "stale_only.json").exists()
    assert not (ca / "values" / "stale_only.png").exists()
    assert [call[:2] for call in _calls(release_repo)] == [["release", "download"]]


def test_missing_bundle_fails_without_reusing_tracked_data(release_repo):
    _, ca, _, _, _ = release_repo
    before = (ca / "webapp_data_All_biodiversity.json").read_bytes()
    result = _run(release_repo, "--restore")
    assert result.returncode != 0
    assert BUNDLE in result.stderr
    assert "validated rebuilt-site artifact" in result.stderr
    assert (ca / "webapp_data_All_biodiversity.json").read_bytes() == before
    assert [call[:2] for call in _calls(release_repo)] == [["release", "download"]]


def test_package_only_has_no_publication_side_effects(release_repo, tmp_path):
    release_repo[4].pop("SKIP_BUILD")  # packaging also skips expensive rebuilds without this flag
    bundle = tmp_path / BUNDLE
    result = _run(release_repo, "--package-only", str(bundle))
    assert result.returncode == 0, result.stderr
    assert bundle.is_file()
    assert not _calls(release_repo)
    with tarfile.open(bundle) as archive:
        members = set(archive.getnames())
    assert {"provenance.json", "ca_atrisk_provenance.json", "atrisk_shipping_policy.json"} <= members
    assert "ca_atrisk_richness.csv" not in members  # bundle ships app data, not heavy build inputs


@pytest.mark.parametrize(
    "missing",
    [
        "index.json",
        "ca_atrisk_provenance.json",
        "atrisk_shipping_policy.json",
        "values/All_biodiversity_conservation_grid_5000m.png",
    ],
)
def test_incomplete_build_fails_before_publication(release_repo, missing):
    _, ca, _, _, _ = release_repo
    (ca / missing).unlink()
    result = _run(release_repo)
    assert result.returncode != 0
    assert not _calls(release_repo)


def test_restore_rejects_tampered_data_before_replacing_checkout(release_repo, tmp_path):
    _, ca, release, _, _ = release_repo
    assert _run(release_repo).returncode == 0
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(release / BUNDLE) as archive:
        archive.extractall(extracted, filter="data")
    (extracted / "webapp_data_All_biodiversity.json").write_text('{"old": true}')
    with tarfile.open(release / BUNDLE, "w:gz") as archive:
        for path in sorted(extracted.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(extracted)))
    before = (ca / "index.json").read_bytes()
    result = _run(release_repo, "--restore")
    assert result.returncode != 0
    assert "checksum" in result.stderr
    assert (ca / "index.json").read_bytes() == before


@pytest.mark.parametrize("invalid", ["missing_file", "missing_manifest", "unsafe_member"])
def test_restore_rejects_incomplete_or_unsafe_archive_without_side_effects(release_repo, tmp_path, invalid):
    _, ca, release, _, _ = release_repo
    bundle = tmp_path / BUNDLE
    assert _run(release_repo, "--package-only", str(bundle)).returncode == 0
    with tarfile.open(bundle) as archive, tarfile.open(release / BUNDLE, "w:gz") as modified:
        for member in archive:
            if member.name == {"missing_file": "index.json", "missing_manifest": "bundle_manifest.json"}.get(
                invalid
            ):
                continue
            modified.addfile(member, archive.extractfile(member))
        if invalid == "unsafe_member":
            member = tarfile.TarInfo("../escape.json")
            member.size = 2
            modified.addfile(member, io.BytesIO(b"{}"))
    before = {p.relative_to(ca): p.read_bytes() for p in ca.rglob("*") if p.is_file()}
    result = _run(release_repo, "--restore")
    assert result.returncode != 0
    assert "Invalid grid site data bundle" in result.stderr
    assert {p.relative_to(ca): p.read_bytes() for p in ca.rglob("*") if p.is_file()} == before
    assert not (ca.parent / "escape.json").exists()
    assert [call[:2] for call in _calls(release_repo)] == [["release", "download"]]


def test_provenance_binds_shipping_policy_and_atrisk_source_record(release_repo):
    _, ca, _, _, _ = release_repo
    records = {r["path"] for r in json.loads((ca / "provenance.json").read_text())["files"]}
    assert "cluster_results/ca/ca_atrisk_provenance.json" in records
    assert "cluster_results/ca/atrisk_shipping_policy.json" in records


def test_stale_provenance_prevents_publishing_changed_json(release_repo):
    _, ca, _, _, _ = release_repo
    (ca / "webapp_data_All_biodiversity.json").write_text('{"different_build": true}')
    result = _run(release_repo)
    assert result.returncode != 0
    assert "provenance checksum mismatch" in result.stderr
    assert not _calls(release_repo)


@pytest.mark.parametrize("invalid", [b"", b"null", b"[]", b"{bad json}"])
def test_malformed_index_fails_before_publication(release_repo, invalid):
    _, ca, _, _, _ = release_repo
    (ca / "index.json").write_bytes(invalid)
    result = _run(release_repo)
    assert result.returncode != 0
    assert "Invalid grid site data bundle" in result.stderr
    assert not _calls(release_repo)
