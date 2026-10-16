"""Verified-subset exports account for every source entry and bind published coverage."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import build_challenge_taxa as builder
import conservation_challenge as challenge
import prepare_optional_layers as optional
from audit_challenge_taxa import audit
from test_audit_challenge_taxa import response
from test_conservation_challenge import AS_OF, FixtureAPI
from test_optional_layers import NOW, REPOSITORY, TAXA_URL, asset

ROOT = Path(__file__).parent


def reviewed_audit(tmp_path):
    source = b"Common.name,Scientific.name\nAlpha,Species alpha\nSynonym,Oldgenus alpha\nVariety,Species beta var. gamma\nMissing,\n"

    def fetch(url):
        name = parse_qs(urlparse(url).query)["q"][0]
        if name == "Oldgenus alpha":
            return response(names=[{"name": name, "lexicon": "scientific-names", "is_valid": False}])
        if name == "Species beta gamma":
            return response(
                name, "variety", id=30, ancestors=[{"id": 20, "name": "Species beta", "rank": "species"}]
            )
        return response()

    return audit(source, fetch, tmp_path / "cache")


def generated(tmp_path):
    original = reviewed_audit(tmp_path)
    public, config = builder.generate(json.dumps(original).encode(), approved=True)
    return json.loads(public), json.loads(config)


def test_export_deduplicates_targets_but_preserves_every_source_row_and_omission(tmp_path):
    original = reviewed_audit(tmp_path)
    raw = json.dumps(original).encode()
    public_bytes, config_bytes = builder.generate(raw, approved=True)
    public, config = json.loads(public_bytes), json.loads(config_bytes)
    assert builder.generate(raw, approved=True) == (public_bytes, config_bytes)
    assert builder.generate(public_bytes, approved=True) == (public_bytes, config_bytes)
    assert len(public["rows"]) == 4
    assert public["rows"][3]["common_name"] == "Missing" and public["rows"][3]["raw_scientific_name"] == ""
    assert config["approved_for_publication"] is True and public["approved_for_publication"] is False
    assert config["taxa"] == [
        {"id": 10, "name": "Species alpha", "rank": "species"},
        {
            "id": 30,
            "name": "Species beta gamma",
            "rank": "variety",
            "species": {"id": 20, "name": "Species beta"},
        },
    ]
    coverage = config["coverage"]
    assert {
        key: coverage[key] for key in ("source_rows", "resolved_rows", "unique_targets", "omitted_rows")
    } == {"source_rows": 4, "resolved_rows": 3, "unique_targets": 2, "omitted_rows": 1}
    assert coverage["resolved"] == [
        {"csv_row": 2, "target_id": 10},
        {"csv_row": 3, "target_id": 10},
        {"csv_row": 4, "target_id": 30},
    ]
    assert coverage["omissions"][0]["reason_code"] == "blank_scientific_name"
    assert coverage["original_source_sha256"] == original["source_sha256"]
    assert config["source"] == {"url": builder.AUDIT_URL, "sha256": hashlib.sha256(public_bytes).hexdigest()}
    assert public["input_audit_sha256"] == hashlib.sha256(raw).hexdigest()
    assert public["rows"][0]["lookup"] == original["rows"][0]["lookup"]


def test_export_drops_nonallowlisted_private_fields_and_requires_explicit_approval(tmp_path):
    original = reviewed_audit(tmp_path)
    original["observer_records"] = "PRIVATE SENTINEL"
    original["rows"][0]["private_csv_path"] = "PRIVATE SENTINEL"
    original["rows"][0]["target"]["observer_data"] = "PRIVATE SENTINEL"
    with pytest.raises(ValueError, match="explicit approval"):
        builder.generate(json.dumps(original).encode())
    public, config = builder.generate(json.dumps(original).encode(), approved=True)
    assert b"PRIVATE SENTINEL" not in public + config


@pytest.mark.parametrize(
    "change",
    [
        "row_missing",
        "duplicate_row",
        "totals",
        "conflicting_id",
        "unrecognized_reason",
        "missing_lookup",
        "inactive_resolved",
        "unknown_status",
    ],
)
def test_inconsistent_reviewed_audits_fail_before_export(tmp_path, change):
    original = reviewed_audit(tmp_path)
    if change == "row_missing":
        original["rows"].pop(1)
    if change == "duplicate_row":
        original["rows"][1]["csv_row"] = 2
    if change == "totals":
        original["resolved_rows"] = 50
    if change == "conflicting_id":
        original["rows"][1]["target"]["name"] = "Different accepted identity"
    if change == "unrecognized_reason":
        original["rows"][3]["reason"] = "Unreviewed explanation"
    if change == "missing_lookup":
        original["rows"][0].pop("lookup")
    if change == "inactive_resolved":
        original["rows"][0]["raw_scientific_name"] += " (Inactive Taxon)"
    if change == "unknown_status":
        original["rows"][0]["status"] = "pending"
    with pytest.raises(ValueError):
        builder.generate(json.dumps(original).encode(), approved=True)


@pytest.mark.parametrize(
    "change",
    [
        "source_count",
        "resolved_count",
        "unique_count",
        "omitted_count",
        "missing_omission",
        "duplicate_row",
        "unknown_reason",
        "source_hash",
        "target_set",
    ],
)
def test_config_coverage_cannot_silently_drop_source_rows_or_targets(tmp_path, change):
    _, config = generated(tmp_path)
    coverage = config["coverage"]
    key = {
        "source_count": "source_rows",
        "resolved_count": "resolved_rows",
        "unique_count": "unique_targets",
        "omitted_count": "omitted_rows",
    }.get(change)
    if key:
        coverage[key] += 1
    if change == "missing_omission":
        coverage["omissions"].clear()
    if change == "duplicate_row":
        coverage["omissions"][0]["csv_row"] = 2
    if change == "unknown_reason":
        coverage["omissions"][0]["reason_code"] = "ignore"
    if change == "source_hash":
        coverage["original_source_sha256"] = "not-a-checksum"
    if change == "target_set":
        config["taxa"].pop()
    with pytest.raises(ValueError):
        challenge.validate_list(config)


def test_acquisition_carries_the_exact_complete_coverage_without_mutating_config(tmp_path):
    _, config = generated(tmp_path)
    before = copy.deepcopy(config)
    snapshot = challenge.acquire(config, FixtureAPI([]), AS_OF)
    assert snapshot["coverage"] == config["coverage"] and config == before
    assert snapshot["coverage"] is not config["coverage"]
    snapshot["coverage"]["omissions"][0]["csv_row"] = 2
    with pytest.raises(ValueError, match="duplicate challenge source row"):
        challenge.validate_snapshot(snapshot)
    assert config == before


@pytest.mark.parametrize("change", ["missing", "changed_detail"])
def test_staging_binds_omission_disclosure_to_the_exact_approved_config(tmp_path, change):
    _, config = generated(tmp_path)
    snapshot = challenge.acquire(config, FixtureAPI([]), AS_OF)
    if change == "missing":
        snapshot.pop("coverage")
    else:
        snapshot["coverage"]["omissions"][0]["reason"] = "Different otherwise valid detail"
    challenge.validate_snapshot(snapshot)
    payload, config_bytes = builder.encoded(snapshot), builder.encoded(config)
    metadata = asset(1, payload)
    values = {TAXA_URL: config_bytes, metadata["browser_download_url"]: payload}
    with pytest.raises(ValueError, match="coverage differs"):
        optional.prepare(
            tmp_path / "public",
            taxa_url=TAXA_URL,
            taxa_sha=hashlib.sha256(config_bytes).hexdigest(),
            fetch=values.__getitem__,
            assets=lambda _: [metadata],
            now=NOW,
            repository=REPOSITORY,
        )
    assert not (tmp_path / "public").exists()


def test_committed_approved_subset_is_exactly_reproducible_from_its_public_audit():
    public = (ROOT / "webapp/challenge_taxa_audit.json").read_bytes()
    config_bytes = (ROOT / "webapp/challenge_taxa.json").read_bytes()
    assert builder.generate(public, approved=True) == (public, config_bytes)
    config = json.loads(config_bytes)
    assert len(config["taxa"]) == 324
    assert config["coverage"]["source_rows"] == 369 and config["coverage"]["resolved_rows"] == 330
    assert len(config["coverage"]["omissions"]) == 39
    assert (
        config["coverage"]["original_source_sha256"]
        == "523ca0de128b897f18a511ff1d67448a4d7cd7640464a051f5f91dcd352f205f"
    )


def test_cli_requires_explicit_subset_approval_and_generates_both_reproducible_artifacts(tmp_path):
    source = tmp_path / "audit.json"
    source.write_text(json.dumps(reviewed_audit(tmp_path)))
    public, config = tmp_path / "public.json", tmp_path / "config.json"
    command = [
        sys.executable,
        str(ROOT / "build_challenge_taxa.py"),
        str(source),
        "--audit-out",
        str(public),
        "--config-out",
        str(config),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2 and not public.exists() and not config.exists()
    result = subprocess.run([*command, "--approve-verified-subset"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (public.read_bytes(), config.read_bytes()) == builder.generate(source.read_bytes(), approved=True)
