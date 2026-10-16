"""Exact-name decisions preserve the source's taxonomic scope and review items."""

import json

import pytest

from audit_challenge_taxa import TaxonFetcher, audit, lookup_url, query_name, resolve


def response(name="Species alpha", rank="species", **changes):
    return {
        "total_results": 1,
        "results": [{"id": 10, "name": name, "rank": rank, "is_active": True, **changes}],
    }


def test_no_fuzzy_match_or_silent_infraspecies_expansion():
    assert resolve("Species alpha", response())["target"] == {
        "id": 10,
        "name": "Species alpha",
        "rank": "species",
    }
    assert resolve("Species alfa", response())["status"] == "needs_review"
    assert resolve("Species alpha var. beta", response())["status"] == "needs_review"
    assert resolve("Species alpha (Inactive Taxon)", response())["status"] == "needs_review"
    duplicate = response()
    duplicate["results"] *= 2
    duplicate["total_results"] = 2
    assert resolve("Species alpha", duplicate)["status"] == "needs_review"


def test_exact_variety_retains_its_identifier_and_verified_species_ancestor():
    data = response(
        "Species alpha beta", "variety", ancestors=[{"id": 3, "name": "Species alpha", "rank": "species"}]
    )
    answer = resolve("Species alpha var. beta", data)
    assert answer["status"] == "resolved"
    assert answer["target"] == {
        "id": 10,
        "name": "Species alpha beta",
        "rank": "variety",
        "species": {"id": 3, "name": "Species alpha"},
    }
    data["results"][0]["ancestors"] = []
    assert resolve("Species alpha var. beta", data)["status"] == "needs_review"
    data["results"][0]["rank"] = "subspecies"
    assert "different rank" in resolve("Species alpha var. beta", data)["reason"]


def test_higher_rank_and_inactive_candidates_are_reviewed():
    assert resolve("Genus", response("Genus", "genus"))["candidate"]["rank"] == "genus"
    assert resolve("Species alpha", response(is_active=False))["status"] == "needs_review"
    partial = response()
    partial["total_results"] = 201
    assert "incomplete" in resolve("Species alpha", partial)["reason"]


def test_csv_audit_preserves_blank_rows_encoding_raw_names_and_cache(tmp_path):
    raw = "Common.name,Scientific.name\nFirst,Species\xa0alpha\nUnknown,\n".encode("cp1252")
    calls = []

    def fetch(url):
        calls.append(url)
        return response()

    report = audit(raw, fetch, tmp_path)
    assert len(calls) == 1 and calls[0] == lookup_url("Species alpha")
    assert report["resolved_rows"] == 1 and report["review_rows"] == 1
    assert report["source_encoding"] == "cp1252"
    assert report["rows"][0]["raw_scientific_name"] == "Species\xa0alpha"
    assert report["rows"][1]["csv_row"] == 3 and report["rows"][1]["query"] == ""
    assert report["approved_for_publication"] is False
    assert audit(raw, lambda _: pytest.fail("cached replay must not fetch"), tmp_path) == report
    cache = next(tmp_path.glob("*.json"))
    cache.write_text(json.dumps({"results": None}))
    with pytest.raises(ValueError, match="invalid taxon response"):
        audit(raw, fetch, tmp_path)


def test_normalization_preserves_concepts_and_rejects_wrong_columns(tmp_path):
    assert query_name("  Boloria eunomia ssp. nichollae ") == "Boloria eunomia nichollae"
    assert query_name("XiphinemaÂ\xa0spp.") == "Xiphinema"
    with pytest.raises(ValueError, match="columns"):
        audit(b"Common.name,SomethingElse\nAlpha,beta", lambda _: {}, tmp_path)


def test_ancestry_is_fetched_by_exact_id_and_bound_to_the_same_concept(tmp_path):
    data = b"Common.name,Scientific.name\nVariety,Species alpha var. beta\n"
    shallow = response("Species alpha beta", "variety")
    deep = response(
        "Species alpha beta", "variety", ancestors=[{"id": 3, "name": "Species alpha", "rank": "species"}]
    )
    urls = []

    def fetch(url):
        urls.append(url)
        return deep if url.endswith("/10") else shallow

    result = audit(data, fetch, tmp_path / "good")
    assert result["resolved_rows"] == 1
    assert len(urls) == 2 and urls[1].endswith("/taxa/10")
    assert result["rows"][0]["target"]["species"]["id"] == 3
    assert result["rows"][0]["ancestry_lookup"]["url"] == urls[1]
    deep["results"][0]["id"] = 99
    with pytest.raises(ValueError, match="different identifier"):
        audit(data, fetch, tmp_path / "bad")


def test_recorded_scientific_synonym_is_distinct_from_fuzzy_or_common_names():
    data = response(
        "Accepted genus alpha",
        names=[{"name": "Oldgenus alpha", "lexicon": "scientific-names", "is_valid": False}],
    )
    answer = resolve("Oldgenus alpha", data)
    assert answer["status"] == "resolved"
    assert answer["match_type"] == "recorded_scientific_synonym"
    assert answer["target"]["name"] == "Accepted genus alpha"
    data["results"][0]["names"][0]["lexicon"] = "english"
    assert resolve("Oldgenus alpha", data)["status"] == "needs_review"
    data = response("Species alpha")
    data["results"].append({"id": 11, "name": "Species alpha", "rank": "complex", "is_active": True})
    data["total_results"] = 2
    assert resolve("Species alpha", data)["target"]["id"] == 10


def test_transient_connection_failure_retries_without_losing_the_result(monkeypatch):
    import requests

    monkeypatch.setattr("audit_challenge_taxa.time.sleep", lambda _: None)
    fetch = TaxonFetcher()
    calls = []

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return response()

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("remote closed")
        return Reply()

    monkeypatch.setattr(fetch.session, "get", get)
    assert fetch("https://example.test/taxa") == response()
    assert len(calls) == 2


def test_timeouts_are_bounded_and_preserve_the_original_failure(monkeypatch):
    import requests

    monkeypatch.setattr("audit_challenge_taxa.time.sleep", lambda _: None)
    fetch = TaxonFetcher()
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        raise requests.Timeout("source unavailable")

    monkeypatch.setattr(fetch.session, "get", get)
    with pytest.raises(requests.Timeout, match="source unavailable"):
        fetch("https://example.test/taxa")
    assert len(calls) == 3
