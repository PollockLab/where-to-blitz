"""Replay the gated plant pilot through the real popup, including bounded pagination."""

import copy
import json
import math
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from test_popup_evidence import _run_popup, _species

ROOT = Path(__file__).parent


def _pilot(ids=(1, 2, 3, 4, 5)):
    return {"schema_version": 1, "method": "bc-plants-habitat-season-v1",
            "training_before": "2025-07-01", "valid_until": "2099-12-31", "release_gate": {"passed": True},
            "feature_names": ["bio1", "bio4", "bio12"] + [f"cover_{v}" for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)],
            "scaler": {"mean": [0] * 14, "scale": [1] * 14}, "cells": {"1,2": [0] * 14},
            "species": [{"id": taxon, "mean": [0] * 14, "variance": [1] * 14,
                         "months": [1] * 12, "rarity": 0, "support_cells": 3} for taxon in ids]}


def _page(rows, total=None, page=1):
    return {"page": page, "total_results": len(rows) if total is None else total, "results": rows}


def _plant(taxon, count=3, **kwargs):
    return _species(taxon, count, group="Plantae", **kwargs)


def _run(responses, pilot=None, lang="en", before="", res=25000):
    return _run_popup(responses,
                      "fetchGapTree=async()=>{};state.taxon='Plantae';" + before +
                      f"await fetchProspects(49,-123,'right_here',{{toPopup:true,spEl,resM:{res}}});" +
                      "console.log(JSON.stringify({html:spEl.innerHTML,calls,callOptions}));",
                      lang, plant_pilot=pilot if pilot is not None else _pilot())


@pytest.mark.parametrize("lang,method,basis", [
    ("en", "BC plant pilot", "Habitat index"),
    ("fr", "Projet pilote de plantes", "Indice d'habitat"),
])
def test_active_pilot_shows_method_basis_and_four_ranked_missing_species(lang, method, basis):
    pilot = _pilot()
    pilot["species"][4]["rarity"] = 1
    result = _run([_page([]), _page([_plant(i) for i in range(1, 6)])], pilot, lang)
    assert method in result["html"] and basis in result["html"]
    assert result["html"].count('href="https://www.inaturalist.org/taxa/') == 4
    assert result["html"].index('taxa/5"') < result["html"].index('taxa/1"')
    assert 'taxa/4"' not in result["html"]
    assert len(result["calls"]) == 2
    for call in result["calls"]:
        params = parse_qs(urlparse(call).query)
        for key, value in {"geoprivacy": "open", "taxon_geoprivacy": "open",
                           "threatened": "false", "quality_grade": "research",
                           "iconic_taxa": "Plantae"}.items():
            assert params[key] == [value]


@pytest.mark.parametrize("change", ["gate", "expired", "future_training", "bad_scale", "bad_mean",
                                    "bad_variance", "bad_months", "bad_rarity", "bad_support", "duplicate",
                                    "features", "date", "cell_shape", "outside", "res", "group", "malformed"])
def test_inactive_or_invalid_artifact_falls_back_without_extra_requests(change):
    pilot, before, res = _pilot(), "", 25000
    if change == "gate":
        pilot["release_gate"]["passed"] = False
        pilot["species"], pilot["cells"] = [], {}
    elif change == "expired":
        pilot["valid_until"] = "2025-12-31"
    elif change == "future_training":
        pilot["training_before"] = "2099-01-01"
    elif change == "bad_scale":
        pilot["scaler"]["scale"][0] = 0
    elif change == "bad_mean":
        pilot["species"][0]["mean"] = [0]
    elif change == "bad_variance":
        pilot["species"][0]["variance"][0] = -1
    elif change == "bad_months":
        pilot["species"][0]["months"] = [1]
    elif change == "bad_rarity":
        pilot["species"][0]["rarity"] = 2
    elif change == "bad_support":
        pilot["species"][0]["support_cells"] = 2
    elif change == "duplicate":
        pilot["species"].append(copy.deepcopy(pilot["species"][0]))
    elif change == "features":
        pilot["feature_names"] = ["same"] * 14
    elif change == "date":
        pilot["training_before"] = "2025-02-30"
    elif change == "cell_shape":
        pilot["cells"]["1,2"] = [0]
    elif change == "outside":
        pilot["cells"] = {"3,4": [0] * 14}
    elif change == "res":
        res = 5000
    elif change == "group":
        before = "state.taxon='All biodiversity';"
    elif change == "malformed":
        pilot = {"release_gate": {"passed": True}}
    result = _run([_page([]), _page([_plant(1)])], pilot, before=before, res=res)
    assert "Nearby-count method" in result["html"]
    assert "Habitat index" not in result["html"]
    assert len(result["calls"]) == 2


def test_scorer_uses_standardization_tiers_season_and_deterministic_ties():
    pilot = _pilot((1, 2, 3, 4, 5))
    pilot["scaler"] = {"mean": [2] * 14, "scale": [2] * 14}
    pilot["species"][0]["mean"] = [0.5] * 14
    pilot["species"][0]["rarity"] = 1
    pilot["species"][1]["months"] = [0.49] * 12
    pilot["species"][2]["mean"] = [3] * 14
    result = _run_popup([], "console.log(JSON.stringify({scores:scorePlantPilot(BC_PLANT_PILOT,Array(14).fill(2),1)}));",
                        plant_pilot=pilot)
    # Habitat exp(-.125) is tier4. Perfect habitat tier5 wins even with no rarity bonus.
    assert [s["id"] for s in result["scores"]] == [4, 5, 1]
    assert result["scores"][2]["habitat"] == pytest.approx(math.exp(-0.125))
    assert result["scores"][2]["score"] == pytest.approx(math.exp(-0.125) * 1.25)


@pytest.mark.parametrize("lang,text", [("en", "No plant suggestions meet"), ("fr", "Aucune suggestion de plante")])
def test_pilot_abstains_without_already_recorded_or_weak_fillers(lang, text):
    pilot = _pilot((1, 2))
    pilot["species"][1]["months"] = [0.1] * 12
    result = _run([_page([_plant(1, 2)]), _page([_plant(1, 10), _plant(2, 3)])], pilot, lang)
    assert text in result["html"]
    assert 'href="https://www.inaturalist.org/taxa/' not in result["html"]
    assert "Nearby-count method" not in result["html"]


@pytest.mark.parametrize("local", [_plant(1, 1), _plant(11, 1, rank="subspecies", ancestors=[1])])
def test_local_singleton_or_subspecies_prevents_a_pilot_gap(local):
    result = _run([_page([local]), _page([_plant(1), _plant(2)])])
    assert 'taxa/1"' not in result["html"] and 'taxa/2"' in result["html"]


def test_live_display_and_protected_species_filters_still_apply():
    rows = [_plant(1), _plant(2, photo=False), _plant(3, 2), _plant(4), _plant(5), _plant(6)]
    rows[0]["taxon"]["observations_count"] = 40
    rows[3]["taxon"]["threatened"] = True
    rows[4]["taxon"]["geoprivacy"] = "obscured"
    rows.sort(key=lambda row: -row["count"])
    result = _run([_page([]), _page(rows)], _pilot(tuple(range(1, 7))))
    assert 'taxa/6"' in result["html"]
    assert all(f'taxa/{i}"' not in result["html"] for i in range(1, 6))


def test_full_regional_pool_can_select_a_species_beyond_first_500():
    rows = [_plant(i, 1000-i) for i in range(1, 502)]
    result = _run([_page([]), _page(rows[:500], 501), _page(rows[500:], 501, 2)], _pilot((501,)))
    assert 'taxa/501"' in result["html"]
    assert len(result["calls"]) == 3 and "page=2" in result["calls"][2]
    assert "Habitat index" in result["html"]


@pytest.mark.parametrize("bad", ["total", "page", "duplicate", "order", "error", "short"])
def test_bad_full_pool_pages_cannot_activate_pilot(bad):
    rows = [_plant(i, 1000-i) for i in range(1, 502)]
    last = _page(rows[500:], 501, 2)
    if bad == "total":
        last["total_results"] = 502
    elif bad == "page":
        last["page"] = 1
    elif bad == "duplicate":
        last["results"][0] = rows[0]
    elif bad == "order":
        last["results"][0]["count"] = 2000
    elif bad == "error":
        last = {"error": "offline"}
    else:
        last["results"] = []
    result = _run([_page([_plant(999, 2)]), _page(rows[:500], 501), last], _pilot((501,)))
    assert "Nearby-count method" in result["html"]
    assert "Habitat index" not in result["html"]


def test_full_pool_budget_rejects_more_than_twelve_pages_before_requests():
    first = _page([_plant(i, 10) for i in range(1, 501)], 6001)
    result = _run_popup([first], "const first=input.responses.shift();let error='';"
                        "try{await completePlantRegionalRows(first,'https://example.org',()=>true);}catch(e){error=e.message;}"
                        "console.log(JSON.stringify({error,calls}));", plant_pilot=_pilot())
    assert "Incomplete" in result["error"] and result["calls"] == []


def test_incomplete_local_history_checks_up_to_32_ranked_candidates_and_ancestor_presence():
    pilot = _pilot(tuple(range(1, 41)))
    local = _page([_plant(1000+i, 2) for i in range(500)], 501)
    check = _page([_plant(101, 1, rank="subspecies", ancestors=[1])])
    result = _run([local, _page([_plant(i) for i in range(1, 41)]), check], pilot)
    requested = parse_qs(urlparse(result["calls"][-1]).query)["taxon_id"][0].split(",")
    assert requested == list(map(str, range(1, 33)))
    assert 'taxa/1"' not in result["html"]
    assert all(f'taxa/{i}"' in result["html"] for i in range(2, 6))
    assert result["html"].count('href="https://www.inaturalist.org/taxa/') == 4


@pytest.mark.parametrize("change", ["prospectSeq++", "LANG='fr'", "state.taxon='Aves'"])
def test_stale_pilot_page_cannot_paint_or_launch_more_requests(change):
    rows = [_plant(i, 1000-i) for i in range(1, 502)]
    before = "const original=jget;jget=async(...args)=>{const result=await original(...args);if(calls.length===3){" + change + ";spEl.innerHTML='new selection';}return result;};"
    result = _run([_page([]), _page(rows[:500], 501), _page(rows[500:], 501, 2)], _pilot((501,)), before=before)
    assert result["html"] == "new selection" and len(result["calls"]) == 3


def _build(tmp_path, pilot="missing"):
    (tmp_path / "cluster_results/ca").mkdir(parents=True)
    (tmp_path / "cluster_results/ca/index.json").write_text('{"files":{}}')
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp/index.html").write_text((ROOT / "webapp/index.html").read_text())
    if pilot != "missing":
        (tmp_path / "webapp/bc_plant_pilot.json").write_text(json.dumps(pilot))
    return subprocess.run([sys.executable, str(ROOT / "build_webapp.py")], cwd=tmp_path,
                          capture_output=True, text=True, check=False)


def test_builder_missing_artifact_disables_pilot(tmp_path):
    result = _build(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "const BC_PLANT_PILOT=null;" in (tmp_path / "index.html").read_text()


def test_builder_inlines_valid_artifact_and_escapes_html_end_tags(tmp_path):
    pilot = _pilot()
    pilot["release_gate"]["interpretation"] = "</script>"
    result = _build(tmp_path, pilot)
    assert result.returncode == 0, result.stderr
    built = (tmp_path / "index.html").read_text()
    assert '__BC_PLANT_PILOT__' not in built
    assert '\\u003c/script>' in built


@pytest.mark.parametrize("bad", ["scale", "metadata", "disabled_payload", "month", "date", "boolean_version"])
def test_builder_rejects_malformed_artifact(tmp_path, bad):
    pilot = _pilot()
    if bad == "scale":
        pilot["scaler"]["scale"][0] = 0
    elif bad == "metadata":
        pilot = {"schema_version": 1}
    elif bad == "disabled_payload":
        pilot["release_gate"]["passed"] = False
    elif bad == "month":
        pilot["species"][0]["months"][0] = -1
    elif bad == "date":
        pilot["training_before"] = "2025-02-30"
    else:
        pilot["schema_version"] = True
    result = _build(tmp_path, pilot)
    assert result.returncode != 0 and "Malformed BC plant pilot artifact" in result.stderr


def test_builder_accepts_failed_gate_without_fitted_scaler_or_models(tmp_path):
    pilot = _pilot()
    pilot.update(release_gate={"passed": False}, scaler=None, species=[], cells={})
    result = _build(tmp_path, pilot)
    assert result.returncode == 0, result.stderr
    result = _run([_page([]), _page([_plant(1)])], pilot)
    assert "Nearby-count method" in result["html"] and len(result["calls"]) == 2


def test_current_month_is_used_without_enabling_other_months():
    pilot = _pilot((1, 2))
    pilot["species"][0]["months"] = [0] * 12
    pilot["species"][0]["months"][9] = 1
    pilot["species"][1]["months"] = [0] * 12
    pilot["species"][1]["months"][8] = 1
    result = _run_popup([], "console.log(JSON.stringify(plantPilotForCell(49,-123,25000,'Plantae',new Date('2026-10-15T12:00:00Z'))));",
                        plant_pilot=pilot)
    assert [score["id"] for score in result["scores"]] == [1]


@pytest.mark.parametrize("check", [{"error": "offline"}, {"page": 1, "total_results": 2, "results": []}])
def test_failed_local_verification_abstains_without_local_fillers(check):
    local = _page([_plant(999, 2)], 501)
    result = _run([local, _page([_plant(1)]), check], _pilot((1,)))
    assert "No plant suggestions meet" in result["html"]
    assert 'href="https://www.inaturalist.org/taxa/' not in result["html"]
    assert len(result["calls"]) == 3


@pytest.mark.parametrize("lang,heading", [("en", "Species suggestions from recorded observations"),
                                         ("fr", "Suggestions d’espèces selon les observations")])
def test_existing_method_heading_does_not_claim_top_species(lang, heading):
    pilot = _pilot()
    pilot.update(release_gate={"passed": False}, scaler=None, species=[], cells={})
    result = _run([_page([]), _page([_plant(1)])], pilot, lang)
    assert heading in result["html"]
    assert "Top species" not in result["html"] and "en priorité" not in result["html"]


@pytest.mark.parametrize("ancestors", [None, [], [0], [-1], ["1"], [1.5]])
@pytest.mark.parametrize("check,expected", [
    (_page([]), [1, 2]),
    (_page([_plant(101, 1, rank="subspecies", ancestors=[1])]), [2]),
    (_page([_plant(101, 1, rank="subspecies")]), []),
    (_page([_plant(101, 1, rank="subspecies", ancestors=["1"])]), []),
])
def test_unresolved_local_ancestry_forces_verified_pilot_candidate_check(ancestors, check, expected):
    local = _page([_plant(11, 1, rank="subspecies", ancestors=ancestors)])
    result = _run([local, _page([_plant(1), _plant(2)]), check], _pilot((1, 2)))
    assert len(result["calls"]) == 3
    assert parse_qs(urlparse(result["calls"][-1]).query)["taxon_id"] == ["1,2"]
    assert [taxon for taxon in (1, 2) if f'taxa/{taxon}"' in result["html"]] == expected
    assert "Nearby-count method" not in result["html"]


@pytest.mark.parametrize("bad", ["short", "duplicate", "order", "total", "page", "schema"])
def test_rejected_regional_page_is_evicted_and_retry_fetches_healthy_response(bad):
    rows = [_plant(i, 1000-i) for i in range(1, 502)]
    first, healthy = _page(rows[:500], 501), _page(rows[500:], 501, 2)
    rejected = copy.deepcopy(healthy)
    if bad == "short":
        rejected["results"] = []
    elif bad == "duplicate":
        rejected["results"] = [rows[0]]
    elif bad == "order":
        rejected["results"][0]["count"] = 2000
    elif bad == "total":
        rejected["total_results"] = 502
    elif bad == "page":
        rejected["page"] = 3
    else:
        rejected["results"][0]["taxon"]["name"] = None
    result = _run_popup([first, rejected, healthy],
                        "const url='https://example.org/species';let error='';"
                        "try{await completePlantRegionalRows(await jget(url),url,()=>true);}catch(e){error=e.message;}"
                        "const rows=await completePlantRegionalRows(await jget(url),url,()=>true);"
                        "console.log(JSON.stringify({error,calls,count:rows.length}));",
                        actual_cache=True, plant_pilot=_pilot())
    assert result["error"] and result["count"] == 501
    assert len(result["calls"]) == 3
    assert result["calls"][1:] == ["https://example.org/species&page=2"] * 2


@pytest.mark.parametrize("bad", ["short", "schema", "order"])
def test_rejected_first_regional_page_is_evicted(bad):
    healthy = _page([_plant(1, 4), _plant(2, 3)])
    rejected = copy.deepcopy(healthy)
    if bad == "short":
        rejected["results"] = []
    elif bad == "schema":
        rejected["results"][0]["taxon"]["name"] = None
    else:
        rejected["results"].reverse()
    result = _run_popup([rejected, healthy],
                        "const url='https://example.org/species';let error='';"
                        "try{await completePlantRegionalRows(await jget(url),url,()=>true);}catch(e){error=e.message;}"
                        "const rows=await completePlantRegionalRows(await jget(url),url,()=>true);"
                        "console.log(JSON.stringify({error,calls,count:rows.length}));",
                        actual_cache=True, plant_pilot=_pilot())
    assert result["error"] and result["count"] == 2
    assert result["calls"] == ["https://example.org/species"] * 2


def test_reordered_habitat_features_are_rejected_in_browser_and_builder(tmp_path):
    pilot = _pilot()
    pilot["feature_names"][:2] = pilot["feature_names"][1::-1]
    result = _run([_page([]), _page([_plant(1)])], pilot)
    assert "Nearby-count method" in result["html"]
    assert len(result["calls"]) == 2
    built = _build(tmp_path, pilot)
    assert built.returncode != 0 and "Malformed BC plant pilot artifact" in built.stderr
