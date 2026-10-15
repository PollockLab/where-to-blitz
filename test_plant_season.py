"""Contract tests for complete, bounded and non-overwriting seasonal acquisition."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

import fetch_plant_season as season


def row(taxon_id, count=3, rank="species"):
    return {"count": count, "taxon": {"id": taxon_id, "rank": rank}}


def page(rows, total=None, number=1):
    return {
        "results": rows,
        "total_results": len(rows) if total is None else total,
        "page": number,
        "per_page": 500,
    }


def place():
    return {
        "total_results": 1,
        "results": [
            {
                "id": 7085,
                "name": "British Columbia",
                "display_name": "British Columbia, CA",
                "geometry_geojson": {
                    "type": "MultiPolygon",
                    "coordinates": [[[[-130, 49], [-120, 49], [-120, 59], [-130, 49]]]],
                },
            }
        ],
    }


def fetch_pages(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[len(calls) - 1], {"url": url, "sha256": "a" * 64, "retrieved_utc": "test"}

    return fetch, calls


def test_full_enumeration_precedes_species_rank_filter():
    fetch, calls = fetch_pages(
        [page([row(i, rank="genus") for i in range(1, 501)], 502), page([row(501, 2), row(502, 1)], 502, 2)]
    )
    counts, manifest = season.month_pages(fetch, 7)
    assert counts == {501: 2, 502: 1}
    assert len(calls) == 2 and [p["result_count"] for p in manifest] == [500, 2]
    assert parse_qs(urlsplit(calls[0]).query)["month"] == ["7"]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"results": None},
        page([row(1)], 2),
        page([row(1)], number=2),
        page([row(1)], number=True),
        page([row(1)], total=True),
        page([row(1, 0)]),
        page([row(1, True)]),
        page([row(True)]),
        page([row(1, rank="")]),
        page([row(1), row(1)]),
        page([row(1, 1), row(2, 3)]),
    ],
)
def test_malformed_or_incomplete_pages_fail_closed(bad):
    fetch, _ = fetch_pages([bad])
    with pytest.raises(ValueError):
        season.month_pages(fetch, 1)


@pytest.mark.parametrize(
    "second", [page([row(501)], 502, 2), page([row(1)], 501, 2), page([row(501, 4)], 501, 2)]
)
def test_cross_page_total_duplicate_and_order_changes_are_rejected(second):
    fetch, _ = fetch_pages([page([row(i) for i in range(1, 501)], 501), second])
    with pytest.raises(ValueError):
        season.month_pages(fetch, 1)


def test_page_cap_rejects_before_fetching_the_remaining_pages():
    fetch, calls = fetch_pages([page([row(i) for i in range(1, 501)], 6001)])
    with pytest.raises(ValueError, match="page limit"):
        season.month_pages(fetch, 1)
    assert len(calls) == 1


def test_annual_aggregation_has_all_months_and_sampling_denominators():
    def fetch(url):
        if "/places/" in url:
            data = place()
        else:
            query = parse_qs(urlsplit(url).query)
            assert query["d2"] == ["2024-12-31"]
            assert query["created_d2"] == ["2025-01-01"]
            assert query["place_id"] == ["7085"]
            assert query["geoprivacy"] == query["taxon_geoprivacy"] == ["open"]
            assert query["quality_grade"] == ["research"] and query["threatened"] == ["false"]
            month = int(query["month"][0])
            data = page([row(1, month + 12), row(2, month), row(99, 1, "subspecies")])
        return data, {"url": url, "sha256": "a" * 64}

    aggregate, boundary = season.acquire(fetch)
    assert aggregate["months"] == list(range(1, 13))
    assert aggregate["species"] == [[1, list(range(13, 25))], [2, list(range(1, 13))]]
    assert aggregate["monthly_species_record_totals"] == list(range(14, 38, 2))
    assert len(aggregate["manifest"]["pages"]) == 12
    assert boundary["geometry"]["type"] == "MultiPolygon"


def test_missing_month_cannot_be_published_as_zero_seasonal_activity():
    fetch, _ = fetch_pages([place(), page([])])
    with pytest.raises(ValueError, match="annual coverage"):
        season.acquire(fetch)


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"total_results": 1, "results": [None]},
        {"total_results": 1, "results": []},
        {"total_results": 1, "results": [{"id": 1}]},
    ],
)
def test_boundary_requires_actual_requested_province(data):
    with pytest.raises(ValueError):
        season.boundary_feature(data)


def test_open_polygon_or_bounding_box_is_not_a_boundary():
    data = place()
    data["results"][0]["geometry_geojson"]["coordinates"][0][0].pop()
    with pytest.raises(ValueError, match="ring"):
        season.boundary_feature(data)


def test_existing_output_is_never_replaced(tmp_path):
    output = tmp_path / "counts.json"
    season.write_exclusive(output, {"first": True})
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        season.write_exclusive(output, {"second": True})
    assert output.read_bytes() == original
    assert list(tmp_path.iterdir()) == [output]


class Response:
    def __init__(self, status=200):
        self.status_code = status
        self.content = b'{"results":[]}'
        self.headers = {"Retry-After": "9999"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_network_budget_retries_and_rate_wait_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(season, "MAX_REQUESTS", 2)
    responses = iter([Response(429), Response(429)])

    class Session:
        def get(self, url, **kwargs):
            assert kwargs["timeout"] == (10, 30)
            return next(responses)

    sleeps = []
    # pytest's temporary root is under /private/var on macOS, so bypass only path validation here.
    cache = season.PublicFetcher.__new__(season.PublicFetcher)
    cache.cache_dir, cache.session = tmp_path, Session()
    cache.clock, cache.sleep = lambda: 0.0, sleeps.append
    cache.attempts, cache.last_started = 0, None
    with pytest.raises(ValueError, match="budget"):
        cache("https://example.test")
    assert cache.attempts == 2 and max(sleeps) == 10 and 1 in sleeps
    assert len(list(tmp_path.iterdir())) == 2
    assert json.loads((tmp_path / "response-001.json").read_bytes()) == {"results": []}


def test_raw_responses_cannot_be_saved_outside_tmp(tmp_path):
    if tmp_path.resolve().is_relative_to(season.Path("/tmp").resolve()):
        pytest.skip("pytest temporary root itself is under /tmp")
    with pytest.raises(ValueError, match="under /tmp"):
        season.PublicFetcher(tmp_path)
