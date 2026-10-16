"""Offline complete-enumeration, privacy and extent-first challenge regressions."""

import copy
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests

import conservation_challenge as challenge

AS_OF = datetime(2026, 10, 16, 2, tzinfo=timezone.utc)
CONFIG = {
    "schema_version": 1,
    "version": "combined-2026-v1",
    "source": {"url": "https://example.org/combined.csv", "sha256": "a" * 64},
    "approved_for_publication": True,
    "taxa": [
        {"id": 10, "name": "Species alpha", "rank": "species"},
        {"id": 20, "name": "Species beta", "rank": "species"},
    ],
}


def observation(obs_id=1, taxon_id=10, **changes):
    return {
        "id": obs_id,
        "taxon": {"id": taxon_id, "ancestor_ids": []},
        "observed_on": "2026-10-15",
        "obscured": False,
        "geoprivacy": None,
        "taxon_geoprivacy": None,
        "geojson": {"type": "Point", "coordinates": [-75, 45]},
        "place_ids": [6712],
        **changes,
    }


class FixtureAPI:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, url):
        query = parse_qs(urlparse(url).query)
        self.calls.append(query)
        remaining = [r for r in self.rows if r["id"] > int(query["id_above"][0])]
        return {"total_results": len(remaining), "page": 1, "per_page": 200, "results": remaining[:200]}


def test_complete_cursor_pagination_and_two_pass_provenance():
    api = FixtureAPI([observation(i) for i in range(1, 402)])
    snapshot = challenge.acquire(CONFIG, api, AS_OF)
    assert len(snapshot["observations"]) == 401
    assert len(api.calls) == 8
    assert [c["id_above"] for c in api.calls[:4]] == [["0"], ["200"], ["400"], ["401"]]
    assert snapshot["window"] == {"start": "2026-10-03", "end": "2026-10-16"}
    assert snapshot["source"]["config_sha256"] == challenge.digest(CONFIG)
    assert all(c["place_id"] == ["6712"] for c in api.calls)
    assert all(c["geoprivacy"] == ["open"] and c["taxon_geoprivacy"] == ["open"] for c in api.calls)
    assert len(snapshot["manifest"]["passes"]) == 2


def test_complete_empty_window_is_valid():
    snapshot = challenge.acquire(CONFIG, FixtureAPI([]), AS_OF)
    assert snapshot["complete"] is True
    assert snapshot["observations"] == []
    assert snapshot["manifest"]["source_count"] == 0


@pytest.mark.parametrize(
    "field,value",
    [("obscured", True), ("obscured", None), ("geoprivacy", "private"), ("taxon_geoprivacy", "obscured")],
)
def test_privacy_restricted_records_excluded(field, value):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation(**{field: value})]), AS_OF)
    assert snapshot["observations"] == []
    assert snapshot["manifest"]["source_count"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"observed_on": "2026-10-02"},
        {"observed_on": "2026-10-17"},
        {"observed_on": None},
        {"taxon": {"id": 30}},
        {"place_ids": [1]},
        {"geojson": None},
        {"geojson": {"type": "Point", "coordinates": [float("nan"), 40]}},
        {"geojson": {"type": "Point", "coordinates": [200, 40]}},
    ],
)
def test_malformed_or_out_of_filter_rows_fail(changes):
    with pytest.raises(ValueError):
        challenge.acquire(CONFIG, FixtureAPI([observation(**changes)]), AS_OF)


def test_subspecies_maps_to_approved_species():
    row = observation(taxon={"id": 30, "ancestor_ids": [10]})
    assert challenge.acquire(CONFIG, FixtureAPI([row]), AS_OF)["observations"][0]["taxon_id"] == 10


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(approved_for_publication=False),
        lambda c: c.update(taxa=[]),
        lambda c: c["taxa"].append(c["taxa"][0]),
        lambda c: c["source"].update(sha256="fake"),
        lambda c: c["source"].update(url="http://example.org/source"),
        lambda c: c.update(version=""),
        lambda c: c["taxa"][0].update(rank="genus"),
    ],
)
def test_unapproved_or_untraceable_lists_fail_before_fetch(mutate):
    config = copy.deepcopy(CONFIG)
    mutate(config)
    api = FixtureAPI([])
    with pytest.raises(ValueError):
        challenge.acquire(config, api, AS_OF)
    assert api.calls == []


@pytest.mark.parametrize(
    "results,total",
    [([observation(1), observation(1)], 2), ([observation(2), observation(1)], 2), ([observation(1)], 2)],
)
def test_duplicate_unordered_and_short_pages_fail(results, total):
    def fetch(_):
        return {"results": results, "total_results": total, "page": 1, "per_page": 200}

    with pytest.raises(ValueError):
        challenge.acquire(CONFIG, fetch, AS_OF)


def test_changed_identification_between_passes_fails():
    api = FixtureAPI([observation()])

    def fetch(url):
        if len(api.calls) == 2:
            api.rows[0]["taxon"]["id"] = 20
        return api(url)

    with pytest.raises(ValueError, match="changed between"):
        challenge.acquire(CONFIG, fetch, AS_OF)


def test_changed_total_during_enumeration_fails():
    api = FixtureAPI([observation(i) for i in range(1, 202)])

    def fetch(url):
        if len(api.calls) == 1:
            api.rows.append(observation(202))
        return api(url)

    with pytest.raises(ValueError, match="changing"):
        challenge.acquire(CONFIG, fetch, AS_OF)


def test_request_and_page_budgets_are_finite():
    fetch = challenge.PublicFetcher(sleep=lambda _: None, max_requests=0)
    with pytest.raises(ValueError, match="budget"):
        fetch(challenge.API)
    api = FixtureAPI([observation(i) for i in range(1, 202)])
    with pytest.raises(ValueError, match="page budget"):
        challenge.enumerate_observations(
            api, challenge.query_filters(CONFIG, AS_OF), {10: "alpha"}, max_pages=1
        )


def test_network_retry_is_bounded():
    class Session:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            assert kwargs["timeout"] == (10, 30)
            raise requests.ConnectionError("offline")

    session = Session()
    with pytest.raises(requests.ConnectionError):
        challenge.PublicFetcher(session, sleep=lambda _: None)(challenge.API)
    assert session.calls == 3


def test_cli_atomic_last_good_preservation_and_complete_empty_replacement(tmp_path):
    taxa, output = tmp_path / "taxa.json", tmp_path / "feed.json"
    taxa.write_text(json.dumps(CONFIG))
    output.write_text("last good")
    argv = ["--taxa", str(taxa), "--output", str(output), "--as-of", AS_OF.isoformat()]
    with pytest.raises(ValueError):
        challenge.main(argv, fetch=FixtureAPI([observation(taxon={"id": 999})]))
    assert output.read_text() == "last good"
    challenge.main(argv, fetch=FixtureAPI([]))
    assert json.loads(output.read_text())["observations"] == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["feed.json", "taxa.json"]


def test_extent_filter_precedes_diversity_rounds_and_global_cap():
    rows = [observation(i, observed_on="2026-10-16") for i in range(1, 602)]
    rows.append(observation(602, 20, geojson={"type": "Point", "coordinates": [-125, 50]}))
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    national = challenge.select_observations(snapshot, national=True)
    assert len(national) == 500
    assert [r["taxon_id"] for r in national[:2]] == [10, 20]
    assert challenge.select_observations(snapshot, [-130, 40, -120, 60]) == [snapshot["observations"][-1]]
    assert len(challenge.select_observations(snapshot)) == 150


def test_rounds_sort_recency_and_ties_independent_of_input_order():
    rows = [observation(1), observation(2), observation(3, 20, observed_on="2026-10-14"), observation(4, 20)]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    expected = [4, 2, 1, 3]
    assert [r["id"] for r in challenge.select_observations(snapshot)] == expected
    snapshot["observations"].reverse()
    assert [r["id"] for r in challenge.select_observations(snapshot)] == expected


def test_antimeridian_viewport_and_invalid_bounds():
    rows = [
        observation(1, geojson={"type": "Point", "coordinates": [-179, 50]}),
        observation(2, geojson={"type": "Point", "coordinates": [179, 50]}),
        observation(3),
    ]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    assert {r["id"] for r in challenge.select_observations(snapshot, [170, 40, -170, 60])} == {1, 2}
    with pytest.raises(ValueError, match="bounds"):
        challenge.select_observations(snapshot, [-180, 60, 180, 40])


def test_recency_uses_observed_timestamp_before_upload_id():
    rows = [
        observation(1, time_observed_at="2026-10-15T18:00:00-04:00"),
        observation(2, time_observed_at="2026-10-15T15:00:00Z"),
        observation(3),
    ]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    assert snapshot["observations"][0]["observed_at"] == "2026-10-15T22:00:00+00:00"
    assert [r["id"] for r in challenge.select_observations(snapshot)] == [1, 2, 3]


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-10-15T14:00:00", 42])
def test_malformed_observation_time_fails(timestamp):
    with pytest.raises(ValueError):
        challenge.acquire(CONFIG, FixtureAPI([observation(time_observed_at=timestamp)]), AS_OF)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(complete=False),
        lambda s: s["window"].update(start="2026-10-02"),
        lambda s: s["filters"].update(geoprivacy="obscured"),
        lambda s: s["manifest"].update(eligible_count=999),
        lambda s: s["manifest"]["passes"][0].pop(),
        lambda s: s["manifest"]["passes"][1][0].update(total_results=999),
        lambda s: s["source"].update(config_sha256="fake"),
        lambda s: s["observations"][0].update(latitude=91),
        lambda s: s["observations"].append(s["observations"][0]),
    ],
)
def test_downloaded_snapshot_revalidated_before_writing(tmp_path, mutate):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    mutate(snapshot)
    output = tmp_path / "snapshot.json"
    output.write_text("last good")
    with pytest.raises(ValueError):
        challenge.write_snapshot(output, snapshot)
    assert output.read_text() == "last good"


def test_future_observation_timestamp_fails():
    with pytest.raises(ValueError, match="after snapshot"):
        challenge.acquire(CONFIG, FixtureAPI([observation(time_observed_at="2026-10-16T19:00:00Z")]), AS_OF)


def test_naive_snapshot_cutoff_rejected_before_fetch():
    api = FixtureAPI([])
    with pytest.raises(ValueError, match="UTC"):
        challenge.acquire(CONFIG, api, datetime(2026, 10, 16))
    assert api.calls == []


def test_conflicting_duplicate_page_content_rejected():
    def fetch(_):
        return {
            "total_results": 2,
            "page": 1,
            "per_page": 200,
            "results": [observation(1), observation(1, 20)],
        }

    with pytest.raises(ValueError, match="duplicate"):
        challenge.acquire(CONFIG, fetch, AS_OF)


@pytest.mark.parametrize("field", ["obscured", "geoprivacy", "taxon_geoprivacy"])
def test_missing_source_privacy_evidence_never_publishes_coordinates(field):
    row = observation()
    del row[field]
    assert challenge.acquire(CONFIG, FixtureAPI([row]), AS_OF)["observations"] == []
