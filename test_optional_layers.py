"""Offline publication boundaries for optional overlays, with no fixture activation."""

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import build_discovery_layer as discovery
import build_provenance as provenance
import conservation_challenge as challenge
import prepare_optional_layers as optional
from test_conservation_challenge import AS_OF, CONFIG, FixtureAPI, infraspecies_config, observation
from test_discovery_layer import fixture_product
from test_release_bundle import release_repo as release_repo

REPOSITORY = "PollockLab/where-to-blitz"
TAXA_URL = "https://example.org/approved-combined.json"
DISCOVERY_URL = "https://example.org/discovery.json"
NOW = AS_OF + timedelta(days=1)


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def checksum(content):
    return hashlib.sha256(content).hexdigest()


def asset(identity, content, created="2026-10-16T03:00:00Z"):
    name = f"conservation-challenge-{identity}-1.json"
    return {
        "id": identity,
        "name": name,
        "state": "uploaded",
        "created_at": created,
        "size": len(content),
        "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/conservation-challenge-v1/{name}",
        "digest": "sha256:" + checksum(content),
    }


@pytest.fixture
def candidate_data(tmp_path):
    product = fixture_product(tmp_path)
    product["scope"]["filters"]["taxon_group"] = "All biodiversity"
    for source in product["sources"].values():
        source["scope"]["filters"]["taxon_group"] = "All biodiversity"
    discovery.validate_artifact(product)
    feed = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    product_bytes, config_bytes, feed_bytes = map(encoded, (product, CONFIG, feed))
    metadata = asset(1, feed_bytes)
    data = {
        DISCOVERY_URL: product_bytes,
        TAXA_URL: config_bytes,
        metadata["browser_download_url"]: feed_bytes,
    }
    kwargs = {
        "discovery_url": DISCOVERY_URL,
        "discovery_sha": checksum(product_bytes),
        "taxa_url": TAXA_URL,
        "taxa_sha": checksum(config_bytes),
        "fetch": data.__getitem__,
        "assets": lambda _: [metadata],
        "now": NOW,
    }
    return data, kwargs, feed, metadata


def test_both_layers_stage_after_all_validation_and_export_same_origin_paths(tmp_path, candidate_data):
    data, kwargs, _, metadata = candidate_data
    directory = tmp_path / "public"
    paths = optional.prepare(directory, **kwargs)
    assert paths == {
        "DISCOVERY_RATE_FILE": str(directory / "discovery_rate.json"),
        "CONSERVATION_CHALLENGE_FILE": str(directory / "conservation_challenge.json"),
    }
    assert (directory / "discovery_rate.json").read_bytes() == data[DISCOVERY_URL]
    assert (directory / "conservation_challenge.json").read_bytes() == data[metadata["browser_download_url"]]
    assert sorted(p.name for p in directory.iterdir()) == [
        "conservation_challenge.json",
        "discovery_rate.json",
    ]


def test_disabled_configuration_never_fetches_and_removes_old_optional_public_data(tmp_path):
    for name in ("discovery_rate.json", "conservation_challenge.json"):
        (tmp_path / name).write_bytes(b"stale fixture must not be republished")
    (tmp_path / "index.json").write_bytes(b"grid unchanged")
    assert optional.prepare(tmp_path, fetch=lambda _: pytest.fail("disabled request")) == {}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.json"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"discovery_url": DISCOVERY_URL},
        {"discovery_sha": "a" * 64},
        {"taxa_url": TAXA_URL},
        {"taxa_sha": "a" * 64},
    ],
)
def test_half_configured_inputs_fail_without_fetch_or_mutation(tmp_path, kwargs):
    path = tmp_path / "discovery_rate.json"
    path.write_text("last good")
    with pytest.raises(ValueError, match="both"):
        optional.prepare(tmp_path, fetch=lambda _: pytest.fail("half-configured request"), **kwargs)
    assert path.read_text() == "last good"


@pytest.mark.parametrize(
    "failure",
    ["checksum", "config", "filters", "source", "names", "future", "asset-digest", "size", "missing"],
)
def test_any_invalid_candidate_preserves_every_last_good_file(tmp_path, candidate_data, failure):
    data, kwargs, feed, metadata = candidate_data
    directory = tmp_path / "public"
    directory.mkdir()
    for name in ("discovery_rate.json", "conservation_challenge.json"):
        (directory / name).write_text("last good " + name)
    if failure == "checksum":
        kwargs["discovery_sha"] = "a" * 64
    elif failure == "config":
        feed["source"]["config_sha256"] = "b" * 64
    elif failure == "filters":
        feed["filters"]["taxon_id"] = "999"
    elif failure == "source":
        feed["source"]["version"] = "different-source-vintage"
    elif failure == "names":
        feed["observations"][0]["name"] = "Unapproved species"
    elif failure == "future":
        kwargs["now"] = AS_OF - timedelta(minutes=1)
        metadata["created_at"] = "2026-10-15T01:00:00Z"
    elif failure == "asset-digest":
        metadata["digest"] = "sha256:" + "b" * 64
    elif failure == "missing":
        kwargs["assets"] = lambda _: []
    if failure in ("config", "filters", "source", "names"):
        content = encoded(feed)
        data[metadata["browser_download_url"]] = content
        metadata.update(size=len(content), digest="sha256:" + checksum(content))
    if failure == "size":
        metadata["size"] += 1
    with pytest.raises(ValueError):
        optional.prepare(directory, **kwargs)
    assert {p.name: p.read_text() for p in directory.iterdir()} == {
        "discovery_rate.json": "last good discovery_rate.json",
        "conservation_challenge.json": "last good conservation_challenge.json",
    }


def test_latest_bad_asset_fails_closed_instead_of_falling_back_to_older_valid_feed(tmp_path, candidate_data):
    data, kwargs, _, older = candidate_data
    newer = asset(2, b"not JSON", created="2026-10-16T04:00:00Z")
    data[newer["browser_download_url"]] = b"not JSON"
    kwargs["assets"] = lambda _: [newer, older]
    with pytest.raises(ValueError):
        optional.prepare(tmp_path / "public", **kwargs)
    assert not (tmp_path / "public").exists()


def test_latest_snapshot_wins_by_time_then_asset_id_independent_of_api_order(tmp_path, candidate_data):
    data, kwargs, feed, older = candidate_data
    newer_feed = challenge.acquire(CONFIG, FixtureAPI([]), AS_OF)
    newest_bytes = encoded(newer_feed)
    newer = asset(2, newest_bytes)
    data[newer["browser_download_url"]] = newest_bytes
    kwargs["assets"] = lambda _: [newer, older]
    optional.prepare(tmp_path / "public", **kwargs)
    assert json.loads((tmp_path / "public" / "conservation_challenge.json").read_text())["observations"] == []
    assert feed["observations"]  # A complete genuinely empty window replaces older nonempty data.


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/file",
        "https://u:p@example.org/file",
        "https://example.org/#token",
        "https://example.org/ white",
        "https://example.org/" + "x" * 8192,
    ],
)
def test_insecure_or_unbounded_urls_rejected_before_fetch(url):
    with pytest.raises(ValueError):
        optional.pinned_json(url, "a" * 64, lambda _: pytest.fail("untrusted request"))


def test_asset_download_must_belong_to_exact_repository_release(tmp_path, candidate_data):
    _, kwargs, _, metadata = candidate_data
    metadata["browser_download_url"] = "https://github.com/somebody/other/releases/download/fake/file.json"
    with pytest.raises(ValueError, match="configured release"):
        optional.prepare(tmp_path / "public", **kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_at", "2027-01-01T00:00:00Z"),
        ("created_at", "2026-10-16T03:00:00"),
        ("size", 0),
        ("size", optional.MAX_BYTES + 1),
        ("id", True),
    ],
)
def test_invalid_release_metadata_is_rejected(tmp_path, candidate_data, field, value):
    _, kwargs, _, metadata = candidate_data
    metadata[field] = value
    with pytest.raises(ValueError):
        optional.prepare(tmp_path / "public", **kwargs)


def test_release_listing_uses_complete_pagination_and_finite_subprocess_timeout(monkeypatch):
    calls = []

    def gh(command, **kwargs):
        calls.append((command, kwargs))
        return encoded([[{"id": 2}], [{"id": 1}]]) if "--paginate" in command else encoded({"id": 99})

    monkeypatch.setattr(optional.subprocess, "check_output", gh)
    assert optional.release_assets(REPOSITORY) == [{"id": 2}, {"id": 1}]
    assert calls[1][0] == [
        "gh",
        "api",
        f"repos/{REPOSITORY}/releases/99/assets?per_page=100",
        "--paginate",
        "--slurp",
    ]
    assert all(kwargs["timeout"] == 60 for _, kwargs in calls)
    with pytest.raises(ValueError):
        optional.release_assets("owner/repo; shell command")


def test_cli_exports_only_explicitly_configured_paths(tmp_path, monkeypatch):
    env = tmp_path / "env"
    monkeypatch.setattr(
        optional, "prepare", lambda *a, **k: {"DISCOVERY_RATE_FILE": "/tmp/discovery_rate.json"}
    )
    optional.main(["--directory", str(tmp_path), "--env-file", str(env)])
    assert env.read_text() == "DISCOVERY_RATE_FILE=/tmp/discovery_rate.json\n"
    monkeypatch.setattr(optional, "prepare", lambda *a, **k: {})
    optional.main(["--directory", str(tmp_path), "--env-file", str(env)])
    assert env.read_text() == "DISCOVERY_RATE_FILE=/tmp/discovery_rate.json\n"


def test_stream_download_bounds_and_redirect_security(monkeypatch):
    class Response:
        url = "https://example.org/result"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            assert size == 65536
            yield b"123"
            yield b"456"

    response = Response()
    calls = []
    monkeypatch.setattr(optional.requests, "get", lambda *a, **k: calls.append(k) or response)
    assert optional.download("https://example.org/file") == b"123456"
    assert calls[0]["timeout"] == (10, 60)
    monkeypatch.setattr(optional, "MAX_BYTES", 5)
    with pytest.raises(ValueError, match="limit"):
        optional.download("https://example.org/file")
    response.url = "http://example.org/insecure"
    with pytest.raises(ValueError, match="HTTPS"):
        optional.download("https://example.org/file")


def test_grid_workflow_refreshes_optional_data_after_restore_outside_expensive_cache():
    workflow = (Path(__file__).parent / ".github/workflows/rebuild-grid.yml").read_text()
    aggregate = workflow.split("  aggregate:\n", 1)[1].split("  validate:\n", 1)[0]
    assert "prepare_optional_layers.py" not in aggregate
    manual = workflow.split("  deploy-site:\n", 1)[1].split("  deploy-site-fast:\n", 1)[0]
    fast = workflow.split("  deploy-site-fast:\n", 1)[1]
    for job, restore in (
        (manual, "Restore validated artifact data"),
        (fast, "Restore latest complete grid data"),
    ):
        assert (
            job.index(restore) < job.index("prepare_optional_layers.py") < job.index("python build_webapp.py")
        )
        assert 'prepare_optional_layers.py --env-file "$GITHUB_ENV"' in job
        assert "cp index.html _site/" in job
        assert "cp cluster_results/ca/*.json _site/cluster_results/ca/" in job
    assert "cp artifacts/index.html" not in manual


def evaluate_condition(expression, github):
    # These workflow guards use only boolean operators, equality and event fields.
    return eval(
        expression.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, {"github": github}
    )


@pytest.mark.parametrize(
    "event,conclusion,branch,repository,light,fast",
    [
        ("push", "", "main", REPOSITORY, True, True),
        ("pull_request", "", "topic", "contributor/fork", True, False),
        ("workflow_dispatch", "", "main", REPOSITORY, False, False),
        ("workflow_run", "success", "main", REPOSITORY, True, True),
        ("workflow_run", "failure", "main", REPOSITORY, False, False),
        ("workflow_run", "skipped", "main", REPOSITORY, False, False),
        ("workflow_run", "success", "topic", REPOSITORY, False, False),
        ("workflow_run", "success", "main", "contributor/fork", False, False),
    ],
)
def test_nightly_trigger_checks_trust_and_success_before_any_privileged_job(
    event, conclusion, branch, repository, light, fast
):
    workflow = (Path(__file__).parent / ".github/workflows/rebuild-grid.yml").read_text()
    github = SimpleNamespace(
        event_name=event,
        repository=REPOSITORY,
        event=SimpleNamespace(
            repository=SimpleNamespace(default_branch="main"),
            workflow_run=SimpleNamespace(
                conclusion=conclusion,
                head_branch=branch,
                head_repository=SimpleNamespace(full_name=repository),
            ),
        ),
    )
    for name, expected in (("validate-light", light), ("deploy-site-fast", fast)):
        expression = re.search(rf"^  {name}:\n(?:[^\n]*\n)*?    if: ([^\n]+)", workflow, re.M).group(1)
        assert evaluate_condition(expression, github) is expected


@pytest.mark.parametrize("layer", ["discovery_rate.json", "conservation_challenge.json"])
def test_release_bundle_validates_optional_artifacts_if_present(release_repo, candidate_data, layer):
    _, ca, _, _, _ = release_repo
    data, _, _, metadata = candidate_data
    payload = {p.relative_to(ca).as_posix(): p.read_bytes() for p in ca.rglob("*") if p.is_file()}
    payload[layer] = data[
        DISCOVERY_URL if layer == "discovery_rate.json" else metadata["browser_download_url"]
    ]
    provenance.validate_release_data(payload)
    payload[layer] = encoded({"schema_version": 1, "complete": False})
    with pytest.raises(ValueError):
        provenance.validate_release_data(payload)


@pytest.mark.parametrize("mutation", ["missing", "checksum"])
def test_release_bundle_rejects_missing_or_changed_getting_even_gate_masks(release_repo, mutation):
    _, ca, _, _, _ = release_repo
    payload = {p.relative_to(ca).as_posix(): p.read_bytes() for p in ca.rglob("*") if p.is_file()}
    content = b"independent gate decisions"
    payload["gettingeven_grid.json"] = encoded(
        {"tiers": {"5000": {"gate_mask": {"png": "gettingeven_gate_5000m.png", "sha256": checksum(content)}}}}
    )
    if mutation == "checksum":
        payload["gettingeven_gate_5000m.png"] = b"changed decisions"
    with pytest.raises(ValueError, match="gate-mask"):
        provenance.validate_release_data(payload)
    payload["gettingeven_gate_5000m.png"] = content
    # The fixture's original provenance hashes its fake sidecar, so adjust that record after the intentional edit.
    doc = json.loads(payload["provenance.json"])
    for record in doc["files"]:
        if record["path"] == "cluster_results/ca/gettingeven_grid.json":
            record["sha256"] = checksum(payload["gettingeven_grid.json"])
    payload["provenance.json"] = encoded(doc)
    provenance.validate_release_data(payload)


def test_gate_decisions_are_included_in_root_provenance_patterns():
    assert "cluster_results/ca/gettingeven_gate_*.png" in provenance.OUTPUT_PATTERNS


@pytest.mark.parametrize("mode", ["infraspecies", "unapproved-broader-target", "legacy-species"])
def test_preparation_preserves_narrow_targets_and_accepts_older_species_only_snapshots(tmp_path, mode):
    config = infraspecies_config() if mode != "legacy-species" else CONFIG
    source_row = observation(taxon={"id": 30, "ancestor_ids": [10]})
    feed = challenge.acquire(config, FixtureAPI([source_row]), AS_OF)
    if mode == "unapproved-broader-target":
        feed["observations"][0]["target_ids"] = [10]
    elif mode == "legacy-species":
        feed["observations"][0].pop("target_ids")
        feed["observations"][0].pop("identified_taxon_id")
    content, config_bytes = encoded(feed), encoded(config)
    metadata = asset(1, content)
    values = {TAXA_URL: config_bytes, metadata["browser_download_url"]: content}
    kwargs = {
        "taxa_url": TAXA_URL,
        "taxa_sha": checksum(config_bytes),
        "fetch": values.__getitem__,
        "assets": lambda _: [metadata],
        "now": NOW,
    }
    if mode == "unapproved-broader-target":
        with pytest.raises(ValueError, match="target/species mapping"):
            optional.prepare(tmp_path / "public", **kwargs)
    else:
        optional.prepare(tmp_path / "public", **kwargs)
        assert (tmp_path / "public" / "conservation_challenge.json").read_bytes() == content
