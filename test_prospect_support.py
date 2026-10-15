"""The optional support disclosure describes complete records without changing rankings."""

import json
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import pytest

from test_popup_evidence import _run_popup, _species


def records():
    rows = []
    for taxon in range(1, 5):
        for day in range(1, 4):
            rows.append(
                {
                    "id": taxon * 10 + day,
                    "taxon": {"id": taxon},
                    "user": {"id": 1 if taxon == 1 else day},
                    "observed_on": f"2026-01-0{day}",
                    "geojson": {"coordinates": [-123.1, 49]},
                    "geoprivacy": None,
                    "taxon_geoprivacy": None,
                }
            )
    return {"total_results": len(rows), "results": rows}


def support(response=None, *, action="", lang="en", region=None, actual_cache=False):
    response = records() if response is None else response
    return _run_popup(
        [
            {"total_results": 0, "results": []},
            region or {"total_results": 4, "results": [_species(i, 3) for i in range(1, 5)]},
            response,
        ],
        "const body=new Element(),details={open:false,querySelector:()=>body};"
        "const retry={};body.querySelector=s=>s==='[data-support-retry]'?retry:null;"
        "spEl.querySelector=selector=>selector==='[data-prospect-support]'?details:null;"
        "fetchGapTree=async()=>{};surfaceFilter=async()=> '&without_taxon_id=marine';"
        "await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "const before=calls.length,cardHtml=spEl.innerHTML;"
        "details.open=true;await details.ontoggle();"
        + action
        + "console.log(JSON.stringify({before,calls,body:body.innerHTML,text:body.textContent,"
        "cardHtml,unchanged:spEl.innerHTML===cardHtml}));",
        lang,
        actual_cache=actual_cache,
    )


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_support_loads_only_on_open_and_distinguishes_identical_record_counts(lang):
    result = support(
        lang=lang,
        action="details.open=false;await details.ontoggle();details.open=true;await details.ontoggle();",
    )
    assert result["before"] == 2 and len(result["calls"]) == 3
    assert result["unchanged"]
    assert result["cardHtml"].count("3 within 40 km" if lang == "en" else "3 dans 40 km") == 4
    assert ("1 observer · 3 dates" if lang == "en" else "1 observateur · 3 dates") in result["body"]
    assert ("3 observers · 3 dates" if lang == "en" else "3 observateurs · 3 dates") in result["body"]
    assert ("Nearest record" if lang == "en" else "Observation la plus proche") in result["body"]
    params = parse_qs(urlparse(result["calls"][-1]).query)
    assert urlparse(result["calls"][-1]).path == "/v1/observations"
    assert params["taxon_id"] == ["1,2,3,4"] and params["per_page"] == ["200"]
    assert params["page"] == ["1"] and params["radius"] == ["40"]
    assert params["quality_grade"] == ["research"] and params["threatened"] == ["false"]
    assert params["geoprivacy"] == params["taxon_geoprivacy"] == ["open"]
    assert params["without_taxon_id"] == ["marine"]
    assert params["order_by"] == ["observed_on"]


@pytest.mark.parametrize(
    "bad",
    [
        "capped",
        "unknown_total",
        "duplicate",
        "missing_observer",
        "date",
        "coordinates",
        "protected",
        "protected_taxon",
        "unrelated",
        "changed",
        "page",
    ],
)
def test_uncertain_support_never_invents_observers_or_replaces_cards(bad):
    response = records()
    if bad == "capped":
        response["total_results"] += 1
    elif bad == "unknown_total":
        del response["total_results"]
    elif bad == "duplicate":
        response["results"][1] = deepcopy(response["results"][0])
    elif bad == "missing_observer":
        response["results"][0]["user"] = None
    elif bad == "date":
        response["results"][0]["observed_on"] = "2026-02-30"
    elif bad == "coordinates":
        response["results"][0]["geojson"]["coordinates"] = [200, 49]
    elif bad == "protected":
        response["results"][0]["geoprivacy"] = "obscured"
    elif bad == "protected_taxon":
        response["results"][0]["taxon_geoprivacy"] = "obscured"
    elif bad == "unrelated":
        response["results"][0]["taxon"]["id"] = 99
    elif bad == "changed":
        response["results"][0]["taxon"]["id"] = 2
    elif bad == "page":
        response["page"] = 2
    result = support(response)
    assert "Detailed support is unavailable" in result["body"]
    assert "observer ·" not in result["body"] and "observers ·" not in result["body"]
    assert result["unchanged"]


def test_support_failure_retries_without_reloading_species_or_changing_focus_target():
    action = "input.responses.push(" + json.dumps(records()) + ");await retry.onclick();"
    result = support({"error": "429"}, action=action)
    assert "1 observer · 3 dates" in result["body"]
    assert result["unchanged"] and len(result["calls"]) == 4


def test_retry_fetches_fresh_support_after_rejecting_http_success():
    bad = records()
    bad["total_results"] += 1
    result = support(bad, actual_cache=True,
                     action="input.responses.push(" + json.dumps(records()) + ");await retry.onclick();")
    assert len(result["calls"]) == 4
    assert "1 observer · 3 dates" in result["body"]
    assert result["unchanged"]


@pytest.mark.parametrize("change", ["prospectSeq++", "LANG='fr'", "state.taxon='Fungi'"])
def test_stale_support_response_cannot_paint_old_evidence(change):
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 1, "results": [_species(1, 3)]}, records()],
        "const body=new Element(),details={open:false,querySelector:()=>body};"
        "spEl.querySelector=()=>details;fetchGapTree=async()=>{};"
        "await fetchProspects(49,-123,'right_here',{toPopup:true,spEl});"
        "const get=jget;jget=async(...args)=>{const value=await get(...args);"
        + change
        + ";body.innerHTML='new evidence';return value;};"
        "details.open=true;await details.ontoggle();console.log(JSON.stringify({html:body.innerHTML}));",
    )
    assert result["html"] == "new evidence"


def test_large_record_sets_do_not_trigger_unbounded_support_requests():
    region = {"total_results": 4, "results": [_species(i, 51) for i in range(1, 5)]}
    result = support(region=region)
    assert len(result["calls"]) == 2
    assert "Detailed support is unavailable" in result["text"]


def test_subspecies_evidence_is_matched_to_exactly_one_requested_parent():
    response = records()
    response["results"][0]["taxon"] = {"id": 99, "ancestor_ids": [1]}
    assert "1 observer · 3 dates" in support(response)["body"]
    response["results"][0]["taxon"]["ancestor_ids"] = [1, 2]
    assert "Detailed support is unavailable" in support(response)["body"]
