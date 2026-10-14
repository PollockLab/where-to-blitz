"""Offline shipping-policy selection and production fetch integration tests."""

import csv
import io
import json
import zipfile

import pytest
from shapely.geometry import Polygon, box

import atrisk_ranges as ar
import atrisk_shipping as shipping
import build_atrisk_layer as bal


def policy(**changes):
    return {
        "download_key": "download-1",
        "method": "allowlist",
        "dropped_listing_ids": ["drop"],
        "gate_commit": "7da9d9b",
        "scorer_commit": "d765666",
        **changes,
    }


def listing(lid, name="Test species", source="province"):
    return {
        "listing_id": lid,
        "common_name": name,
        "names": [name],
        "status_weight": 2,
        "range_source": source,
        "range_ids": [],
    }


def entry(lid, keys=("A",), weight=2, geom=None, infra=("sub",)):
    return {"id": lid, "keys": set(keys), "weight": weight, "geom": geom, "infra": set(infra)}


def columns():
    return ar.read_columns(
        io.StringIO(
            "specieskey\tacceptedtaxonkey\tyi\txi\tn\n"
            "A\tsub\t10\t10\t3\n"
            "alias\tsub\t10\t10\t7\n"
            "A\tsub\t30\t30\t11\n"
            "B\tB\t10\t10\t5\n"
        )
    )


def write_policy(tmp_path, value):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return path


def test_policy_selection_drops_listing_without_dropping_shared_species_key():
    polygon = box(100, 100, 101, 101)
    rows = [listing("drop"), listing("keep")]
    entries = [entry("drop", ("A", "alias"), 3, polygon), entry("keep", ("A", "alias"), 1, polygon)]
    selected, dropped, applied = shipping.apply_policy(policy(), rows, entries)
    score, nsp, kept = ar.score_cells(columns(), selected, lambda x, y: (x, y))
    assert score == {(0, 0): 1, (1, 1): 1}
    assert nsp == {(0, 0): 1, (1, 1): 1}
    assert selected[0]["geom"] is None and selected[0]["infra"] == set()
    assert entries[1]["geom"] is polygon and entries[1]["infra"] == {"sub"}
    assert shipping.with_dropped_totals(columns(), kept, dropped) == {"keep": (21, 21), "drop": (0, 21)}
    assert applied["retained_listing_ids"] == ["keep"]
    assert applied["dropped_matched_listing_ids"] == ["drop"]
    assert applied["dropped_listing_reasons"]["drop"] == "excluded by selected allowlist policy"


def test_range_clip_retains_geometry_and_normal_infraspecific_rule():
    polygon = box(0, 0, 0.2, 0.2)
    rows = [listing("drop"), listing("keep")]
    entries = [entry("drop"), entry("keep", geom=polygon)]
    selected, dropped, _ = shipping.apply_policy(policy(method="range_clip"), rows, entries)
    assert selected == [entries[1]] and dropped == [entries[0]]
    _, _, kept = ar.score_cells(columns(), selected, lambda x, y: (x, y))
    assert kept == {"keep": (14, 14)}  # the normal infraspecific rule accepts the outside row


@pytest.mark.parametrize("method", shipping.METHODS)
@pytest.mark.parametrize("name", sorted(shipping.POLYGON_REQUIRED_NAMES))
def test_ambiguous_taxa_keep_polygon_without_infraspecific_bypass(method, name):
    polygon = box(0, 0, 0.2, 0.2)
    rows = [listing("special", name)]
    entries = [entry("special", geom=polygon)]
    selected, dropped, applied = shipping.apply_policy(
        policy(method=method, dropped_listing_ids=[]), rows, entries
    )
    assert not dropped and selected[0]["geom"] is polygon and selected[0]["infra"] == set()
    assert applied["required_polygon_listing_ids"] == ["special"]
    _, _, kept = ar.score_cells(columns(), selected, lambda x, y: (x, y))
    assert kept == {"special": (3, 14)}


@pytest.mark.parametrize("geom", [None, Polygon()])
def test_ambiguous_taxa_without_usable_polygon_drop_and_log(geom, capsys):
    rows = [listing("special", "Lampetra richardsoni")]
    entries = [entry("special", geom=geom)]
    selected, dropped, applied = shipping.apply_policy(policy(dropped_listing_ids=[]), rows, entries)
    assert selected == [] and dropped == entries
    assert shipping.with_dropped_totals(columns(), {}, dropped) == {"special": (0, 14)}
    assert applied["requested_dropped_listing_ids"] == []
    assert applied["dropped_listing_ids"] == ["special"]
    assert "no usable polygon" in applied["dropped_listing_reasons"]["special"]
    assert "dropped listing special" in capsys.readouterr().out


def test_drop_reasons_and_unmatched_exclusions_remain_in_provenance():
    rows = [listing("drop"), listing("unmatched")]
    p = policy(
        dropped_listing_ids=["drop", "unmatched"], dropped_listing_reasons={"drop": "SPILLS test name"}
    )
    selected, dropped, applied = shipping.apply_policy(p, rows, [entry("drop")])
    assert selected == [] and len(dropped) == 1
    assert applied["dropped_listing_ids"] == ["drop", "unmatched"]
    assert applied["dropped_matched_listing_ids"] == ["drop"]
    assert applied["dropped_listing_reasons"]["drop"] == "SPILLS test name"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"method": "clip"}, "method"),
        ({"dropped_listing_ids": ["unknown"]}, "unknown listing IDs"),
        ({"dropped_listing_ids": ["drop", "drop"]}, "unique"),
        ({"dropped_listing_ids": [1]}, "nonempty strings"),
        ({"dropped_listing_ids": "drop"}, "list of nonempty strings"),
        ({"dropped_listing_ids": None}, "list of nonempty strings"),
        ({"dropped_listing_ids": [""]}, "nonempty strings"),
        ({"download_key": "other"}, "does not match"),
        ({"gate_commit": ""}, "gate_commit"),
        ({"scorer_commit": None}, "scorer_commit"),
        ({"dropped_listing_reasons": []}, "dropped_listing_reasons"),
        ({"dropped_listing_reasons": {"keep": "wrong ID"}}, "dropped_listing_reasons"),
        ({"dropped_listing_reasons": {"drop": ""}}, "dropped_listing_reasons"),
    ],
)
def test_invalid_policy_fails_clearly(tmp_path, changes, message):
    with pytest.raises(SystemExit, match=message):
        shipping.load_policy(write_policy(tmp_path, policy(**changes)), "download-1", [listing("drop")])


def test_policy_missing_or_invalid_json_fails_clearly(tmp_path):
    path = tmp_path / "missing.json"
    for content in (None, "{bad json"):
        if content:
            path.write_text(content)
        with pytest.raises(SystemExit, match="unavailable or malformed"):
            shipping.load_policy(path, "download-1", [])
    with pytest.raises(SystemExit, match="JSON object"):
        shipping.load_policy(write_policy(tmp_path, []), "download-1", [])


def test_valid_policy_preserves_gate_results(tmp_path):
    p = policy(metrics={"true": 3830, "false": 7224, "ci95": [0.086, 0.970]})
    assert shipping.load_policy(write_policy(tmp_path, p), "download-1", [listing("drop")]) == p


def test_range_provenance_identifies_only_effective_polygons():
    rows = [listing("keep"), listing("special", "Peltigera gowardii", "dfo"), listing("drop")]
    entries = [entry("keep"), entry("special", geom=box(0, 0, 1, 1))]
    result = shipping.range_provenance(ar.provenance(rows, entries), rows, entries, "allowlist")
    assert result["method"] == "allowlist"
    assert result["effective_clipped_listing_ids"] == ["special"]
    assert result["listings_clipped_by_source"] == {"dfo": 1}
    assert result["species_level_listing_ids"] == ["keep"]
    assert result["selected_listings_by_available_range_source"] == {"province": 1, "dfo": 1}
    assert "listings_in_layer_by_source" not in result


def fetch_fixture(tmp_path, monkeypatch):
    rows = [listing("drop"), listing("keep", "Other species")]
    polygon = box(0, 0, 0.2, 0.2)
    entries = [entry("drop", ("A", "alias"), 3, polygon), entry("keep", ("B",), 1, polygon)]
    monkeypatch.setattr(bal, "SHIPPING_POLICY", str(tmp_path / "policy.json"))
    monkeypatch.setattr(bal, "PROVENANCE", str(tmp_path / "provenance.json"))
    monkeypatch.setattr(bal, "OUT_CSV", str(tmp_path / "richness.csv"))
    monkeypatch.setattr(
        bal,
        "read_crosswalk",
        lambda: {"Test species": (("A", "alias"), "sub"), "Other species": (("B",), "")},
    )
    monkeypatch.setattr(ar, "read_listings", lambda: rows)
    monkeypatch.setattr(ar, "load_range_geoms", lambda: {})
    monkeypatch.setattr(ar, "listing_entries", lambda *args: entries)
    monkeypatch.setattr(ar, "transformer", lambda: lambda x, y: (x, y))
    monkeypatch.setattr(bal, "check_counts", lambda *args: None)
    monkeypatch.setattr(bal, "col_release", lambda: {"version": "COL26.6 XR"})
    write_clip_table = ar.write_clip_table
    monkeypatch.setattr(
        ar,
        "write_clip_table",
        lambda listings, entries, kept: write_clip_table(listings, entries, kept, tmp_path / "clip.csv"),
    )
    monkeypatch.setattr(
        bal,
        "http_json",
        lambda *args: (
            200,
            {
                "status": "SUCCEEDED",
                "doi": "10.15468/dl.test",
                "downloadLink": "https://example.test/download",
            },
        ),
    )
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as z:
        z.writestr(
            "records.tsv",
            "specieskey\tacceptedtaxonkey\tyi\txi\tn\nA\tsub\t10\t10\t3\nalias\tsub\t10\t10\t7\nB\tB\t10\t10\t5\n",
        )
    monkeypatch.setattr(bal.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(blob.getvalue()))
    return tmp_path / "policy.json"


def test_fetch_builds_selected_method_and_audits_dropped_alias_totals(tmp_path, monkeypatch):
    path = fetch_fixture(tmp_path, monkeypatch)
    path.write_text(json.dumps(policy()))
    bal.do_fetch("download-1")
    with open(tmp_path / "richness.csv") as f:
        result = list(csv.DictReader(f))
    assert len(result) == 1 and result[0]["n_species"] == "1" and result[0]["score_raw"] == "1.00"
    with open(tmp_path / "clip.csv") as f:
        audit = {r["listing_id"]: r for r in csv.DictReader(f)}
    assert audit["drop"]["records_kept"] == "0" and audit["drop"]["records_species"] == "10"
    assert audit["keep"]["records_kept"] == "5"
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    assert provenance["n_species_matched"] == 2  # input counter, including the dropped listing
    assert provenance["shipping_policy"]["applied"] is True
    assert provenance["shipping_policy"]["dropped_listing_ids"] == ["drop"]
    assert provenance["ranges"]["effective_clipped_listing_ids"] == []


def test_candidate_fetch_bypasses_missing_policy_and_keeps_raw_entries(tmp_path, monkeypatch):
    fetch_fixture(tmp_path, monkeypatch)
    bal.do_fetch("download-1", candidate=True)
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    assert provenance["shipping_policy"]["applied"] is False
    assert provenance["shipping_policy"]["mode"] == "candidate"
    assert provenance["ranges"]["effective_clipped_listing_ids"] == ["drop", "keep"]
    with open(tmp_path / "richness.csv") as f:
        result = list(csv.DictReader(f))
    assert result[0]["n_species"] == "2" and result[0]["score_raw"] == "4.00"


@pytest.mark.parametrize("p", [None, policy(download_key="stale")])
def test_fetch_rejects_missing_or_stale_policy_before_network(tmp_path, monkeypatch, p):
    path = fetch_fixture(tmp_path, monkeypatch)
    if p is not None:
        path.write_text(json.dumps(p))

    def unexpected(*args):
        pytest.fail("production fetch must reject policy before network")

    monkeypatch.setattr(bal, "http_json", unexpected)
    with pytest.raises(SystemExit, match="shipping policy"):
        bal.do_fetch("download-1")
    assert not (tmp_path / "richness.csv").exists()


@pytest.mark.parametrize("candidate", [False, True])
def test_cli_passes_candidate_mode(monkeypatch, candidate):
    calls = []
    monkeypatch.setattr(
        "sys.argv",
        ["build_atrisk_layer.py", "--fetch", "download-1"] + (["--candidate"] if candidate else []),
    )
    monkeypatch.setattr(bal, "do_fetch", lambda key, candidate=False: calls.append((key, candidate)))
    bal.main()
    assert calls == [("download-1", candidate)]


def test_candidate_flag_requires_fetch(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["build_atrisk_layer.py", "--validate", "--candidate"])
    with pytest.raises(SystemExit) as exc:
        bal.main()
    assert exc.value.code == 2 and "--candidate requires --fetch" in capsys.readouterr().err
