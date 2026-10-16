"""Reviewed synonyms preserve source identity and never admit conflicting lineages."""

import json
from pathlib import Path

import pytest

import build_challenge_taxa as builder
from test_challenge_subset import reviewed_audit
from test_optional_layer_viewer import coverage_snapshot, sighting_lifecycle


def external_audit(tmp_path, *, conflict=False):
    original = reviewed_audit(tmp_path)
    row = original["rows"][1]
    row["external_review"] = {
        "decision": "concept_conflict" if conflict else "synonym",
        "source_name": row["query"],
        "authority_name": "Species alpha",
        "kingdom": "Bacteria" if conflict else "Animalia",
        "provider_kingdom": "Animalia",
        "authority": {"url": "https://gd.eppo.int/taxon/ABCDEF", "sha256": "a" * 64},
        "taxonomy_lookup": {"url": "https://api.inaturalist.org/v1/taxa/10", "sha256": "b" * 64},
    }
    if not conflict:
        row["external_review"]["name_lookup"] = {
            "url": "https://api.inaturalist.org/v1/taxa?q=Species+alpha&per_page=200&all_names=true&is_active=true",
            "sha256": "c" * 64,
        }
    row["match_type"] = "reviewed_scientific_synonym"
    if conflict:
        row["status"] = "needs_review"
        row["reason"] = "taxonomic concept conflicts with authoritative lineage"
        row["candidate"] = row.pop("target")
        row.pop("match_type")
        original["resolved_rows"] -= 1
        original["review_rows"] += 1
    return original


@pytest.mark.parametrize("conflict", [False, True])
def test_review_preserves_bound_primary_references_and_regenerates_exactly(tmp_path, conflict):
    original = external_audit(tmp_path, conflict=conflict)
    original["rows"][1]["external_review"]["private_notes"] = "PRIVATE SENTINEL"
    public, config = builder.generate(json.dumps(original).encode(), approved=True)
    row = json.loads(public)["rows"][1]
    assert b"PRIVATE SENTINEL" not in public + config
    assert row["lookup"] == original["rows"][1]["lookup"]
    assert row["external_review"]["authority"]["sha256"] == "a" * 64
    assert row["external_review"]["taxonomy_lookup"]["url"].endswith("/10")
    assert builder.generate(public, approved=True) == (public, config)
    coverage = json.loads(config)["coverage"]
    assert coverage["resolved_rows"] == (2 if conflict else 3)
    assert coverage["omitted_rows"] == (2 if conflict else 1)
    if conflict:
        assert coverage["omissions"][0]["reason_code"] == "taxonomic_concept_conflict"
        assert {item["csv_row"] for item in coverage["resolved"]} == {2, 4}


@pytest.mark.parametrize(
    "change",
    [
        "missing_review",
        "wrong_decision",
        "source_name",
        "authority_name",
        "kingdom",
        "authority_url",
        "authority_hash",
        "taxonomy_id",
        "taxonomy_search",
        "taxonomy_hash",
        "ordinary_match",
        "unsupported_rank",
        "missing_name_lookup",
        "unrelated_name_lookup",
    ],
)
def test_review_cannot_resolve_without_matching_source_target_and_primary_references(tmp_path, change):
    original = external_audit(tmp_path)
    row = original["rows"][1]
    review = row["external_review"]
    if change == "missing_review":
        row.pop("external_review")
    elif change == "wrong_decision":
        review["decision"] = "concept_conflict"
    elif change == "source_name":
        review["source_name"] = "Unrelated alpha"
    elif change == "authority_name":
        review["authority_name"] = ""
    elif change == "kingdom":
        review["provider_kingdom"] = "Fungi"
    elif change == "authority_url":
        review["authority"]["url"] = "https://gd.eppo.int.evil.example/taxon/ABCDEF"
    elif change == "authority_hash":
        review["authority"]["sha256"] = "not a hash"
    elif change == "taxonomy_id":
        review["taxonomy_lookup"]["url"] = "https://api.inaturalist.org/v1/taxa/11"
    elif change == "taxonomy_search":
        review["taxonomy_lookup"]["url"] = "https://api.inaturalist.org/v1/taxa?q=Species+alpha"
    elif change == "taxonomy_hash":
        review["taxonomy_lookup"]["sha256"] = "B" * 64
    elif change == "ordinary_match":
        row["match_type"] = "recorded_scientific_synonym"
    elif change == "unsupported_rank":
        row["target"]["rank"] = "family"
    elif change == "missing_name_lookup":
        review.pop("name_lookup")
    elif change == "unrelated_name_lookup":
        review["name_lookup"]["url"] = review["name_lookup"]["url"].replace("Species+alpha", "Other+name")
    with pytest.raises(ValueError):
        builder.generate(json.dumps(original).encode(), approved=True)


@pytest.mark.parametrize("change", ["same_kingdom", "no_candidate", "no_review"])
def test_lineage_conflict_requires_independent_identity_evidence(tmp_path, change):
    original = external_audit(tmp_path, conflict=True)
    row = original["rows"][1]
    if change == "same_kingdom":
        row["external_review"]["provider_kingdom"] = "Bacteria"
    elif change == "no_candidate":
        row.pop("candidate")
    else:
        row.pop("external_review")
    with pytest.raises(ValueError):
        builder.generate(json.dumps(original).encode(), approved=True)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_conflicting_taxonomic_concepts_have_a_localized_omission_reason(language):
    snapshot = coverage_snapshot(1)
    snapshot["coverage"]["omissions"][0].update(
        reason_code="taxonomic_concept_conflict", reason="UNTRANSLATED FALLBACK"
    )
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";optionalLayers.refresh();"
        "console.log(JSON.stringify(nodes.recentChallengeOmissionList.children[0].children[2].textContent));",
        snapshot=snapshot,
    )
    assert "UNTRANSLATED FALLBACK" not in result
    assert (
        "conflicting taxonomic groups" if language == "en" else "groupes taxonomiques différents"
    ) in result


def test_committed_reviews_keep_supported_synonyms_and_hold_conflicting_or_changed_concepts():
    root = Path(__file__).parent
    audit = json.loads((root / "webapp/challenge_taxa_audit.json").read_bytes())
    config = json.loads((root / "webapp/challenge_taxa.json").read_bytes())
    rows = {row["csv_row"]: row for row in audit["rows"]}
    expected = {
        177: 1120249,
        185: 1120252,
        222: 1507288,
        252: 447062,
        275: 978988,
        283: 352525,
        286: 1120249,
        333: 775527,
    }
    for index, identity in expected.items():
        row = rows[index]
        assert row["status"] == "resolved" and row["target"]["id"] == identity
        assert row["match_type"] == "reviewed_scientific_synonym"
        assert row["external_review"]["source_name"] == row["query"]
    assert {row["csv_row"] for row in audit["rows"] if row.get("target", {}).get("rank") == "genus"} == {
        202,
        251,
        261,
        280,
        294,
        316,
        331,
        343,
        357,
        367,
    }
    for index in (55, 64, 269, 270, 369):
        assert rows[index]["status"] == "needs_review"
    assert rows[369]["reason_code"] == "taxonomic_concept_conflict"
    assert 1503461 not in {row["id"] for row in config["taxa"]}
