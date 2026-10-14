"""Replay iNaturalist responses through the popup's actual JavaScript renderer."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(src, name):
    match = re.search(r"\n(?:async )?function " + name + r"\(.*?\n\}", src, re.S)
    assert match, f"{name} not found in the template"
    return match.group(0)


def _run_popup(responses, action, lang="en"):
    src = TEMPLATE.read_text()
    i18n = src[src.index("const I18N=") : src.index("let LANG=")]
    # t() is one line; include its declaration independently of the multiline renderer functions.
    script = (
        i18n
        + "\nlet LANG="
        + json.dumps(lang)
        + ";\n"
        + src[src.index("function t(") : src.index("// indexed-name helpers")]
    )
    script += r"""
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')), calls=[],callOptions=[];
class Element{
  constructor(){this.innerHTML='';this.dataset={};}
  querySelector(selector){
    if(selector==='[data-prospect-retry]'&&this.innerHTML.includes('data-prospect-retry'))
      return this.retry||(this.retry=new Element());
    return null;
  }
  querySelectorAll(){return [];}
}
const spEl=new Element(),gapEl=new Element();
const document={getElementById:()=>spEl};
const state={taxon:'All biodiversity',project:'test'};
const ICONIC={Aves:'Aves',Fungi:'Fungi'},FILES={Aves:'birds',Fungi:'fungi'},taxonSel={};
const DOMESTIC=new Set(['Felis catus']);
const WHERE_LBL={right_here:'right_here',around_start:'prospects_where_start'};
let prospectSeq=0,_lastProspect=null,gapSeq=0;
const GT_CACHE={},GT_DENSE={};
const gtkey=(lat,lon)=>lat.toFixed(3)+','+lon.toFixed(3);
const gtHead=()=>'<div class="popsec">'+t('pop_groups_hd')+'</div>';
const groupName=k=>t('group')[k]||k;
const cellBoundsForRes=()=>({swlat:49,nelat:49.1,swlng:-123,nelng:-122.9});
let surfaceFilter=async()=>'';
const inatLocale=()=>LANG==='fr'?'&locale=fr':'';
const HH=0.1;
let jget=async (url,ms,tries)=>{
  calls.push(url);callOptions.push({ms,tries});
  const response=input.responses.shift();
  if(!response)throw Error('unexpected request');
  if(response.error)throw Error(response.error);
  return response;
};
"""
    script += src[src.index("const esc=") : src.index("// whereKey 'right_here'")]
    script += _function(src, "completeSpeciesCounts") + _function(src, "checkedCandidateAbsences")
    script += _function(src, "fetchProspects")
    script += _function(src, "paintGapTree") + _function(src, "fetchGapTree")
    script += "\n(async()=>{" + action + "})().catch(e=>{console.error(e);process.exit(1);});"
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"responses": responses}),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _species(taxon_id, count, *, photo=True, group="Aves", rank="species", ancestors=None):
    taxon = {
        "id": taxon_id,
        "rank": rank,
        "name": f"Species {taxon_id}",
        "observations_count": 100,
        "iconic_taxon_name": group,
    }
    if ancestors is not None:
        taxon["ancestor_ids"] = ancestors
    if photo:
        taxon["default_photo"] = {"square_url": "https://example.org/photo.jpg"}
    return {"count": count, "taxon": taxon}


@pytest.mark.parametrize(
    "renderer", ["fetchProspects(49,-123,'around_start')", "fetchGapTree(49,-123,gapEl,25000)"]
)
def test_cell_evidence_excludes_obscured_coordinates_and_threatened_taxa(renderer):
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 0, "results": []}],
        f"await {renderer};console.log(JSON.stringify({{calls}}));",
    )
    assert len(result["calls"]) == 2
    for call in result["calls"]:
        params = parse_qs(urlparse(call).query)
        assert params["geoprivacy"] == ["open"]
        assert params["taxon_geoprivacy"] == ["open"]
        assert params["quality_grade"] == ["research"]
        assert params["threatened"] == ["false"]


@pytest.mark.parametrize("local", [_species(1, 1), _species(1, 5, photo=False)])
def test_display_filters_do_not_make_a_recorded_species_new_here(local):
    result = _run_popup(
        [
            {"total_results": 2, "results": [local, _species(2, 2)]},
            {"total_results": 2, "results": [_species(1, 3), _species(2, 4)]},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert 'taxa/1"' not in result["html"]
    assert 'taxa/2"' in result["html"]
    assert "no matching record here" not in result["html"]


@pytest.mark.parametrize("total", [501, None])
def test_incomplete_local_results_do_not_claim_an_omitted_species_is_absent(total):
    local = {"results": [_species(i, 2) for i in range(1, 501)]}
    if total is not None:
        local["total_results"] = total
    result = _run_popup(
        [local, {"total_results": 1, "results": [_species(501, 3)]}],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert 'taxa/501"' not in result["html"]
    assert "no matching record here" not in result["html"]
    assert "Only part of the local species list is available" in result["html"]
    if total is None:
        assert "≥500 species recorded" in result["html"]


def test_complete_results_keep_new_species_and_the_local_rarity_order():
    result = _run_popup(
        [
            {"total_results": 2, "results": [_species(1, 2), _species(2, 3)]},
            {"total_results": 3, "results": [_species(1, 10), _species(2, 3), _species(3, 4)]},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    html = result["html"]
    assert html.index('taxa/3"') < html.index('taxa/1"') < html.index('taxa/2"')
    assert "no matching record" in html
    assert "2 here · 10 within 40 km" in html


@pytest.mark.parametrize(
    ("lang", "expected", "absent"),
    [
        (
            "en",
            "No species suggestions are available from these records",
            "No matching public research-grade iNaturalist records found here",
        ),
        (
            "fr",
            "Aucune suggestion d'espèce n'est disponible à partir de ces observations",
            "Aucune observation publique de qualité recherche sur iNaturalist ne correspond aux critères ici",
        ),
    ],
)
def test_filtered_empty_suggestions_do_not_claim_no_local_records(lang, expected, absent):
    result = _run_popup(
        [
            {"total_results": 1, "results": [_species(1, 1)]},
            {"total_results": 1, "results": [_species(1, 4)]},
        ],
        "fetchGapTree=async()=>{};await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML}));",
        lang,
    )
    assert expected in result["html"]
    assert absent not in result["html"]


@pytest.mark.parametrize("total", [500, 501])
def test_popup_distinguishes_a_full_page_from_a_truncated_list(total):
    result = _run_popup(
        [
            {"total_results": total, "results": [_species(i, 2) for i in range(1, 501)]},
            {"total_results": 1, "results": [_species(501, 3)]},
        ],
        "fetchGapTree=async()=>{};await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert ('taxa/501"' in result["html"]) == (total == 500)
    assert ("Only part of the local species list is available" in result["html"]) == (total > 500)


@pytest.mark.parametrize("failed", ["cell", "neighbourhood", "both"])
def test_any_failed_coverage_input_is_unavailable_and_a_retap_retries(failed):
    cell = {"total_results": 3, "results": [_species(1, 2), _species(2, 3), _species(3, 4, group="Fungi")]}
    neighbourhood = {
        "total_results": 6,
        "results": [_species(i, 3) for i in range(1, 4)]
        + [_species(i, 3, group="Fungi") for i in range(4, 7)],
    }
    result = _run_popup(
        [
            {"error": "429"} if failed in {"cell", "both"} else cell,
            {"error": "429"} if failed in {"neighbourhood", "both"} else neighbourhood,
            cell,
            neighbourhood,
        ],
        "await fetchGapTree(49,-123,gapEl,25000);const first=gapEl.innerHTML;"
        "await fetchGapTree(49,-123,gapEl,25000);"
        "console.log(JSON.stringify({first,second:gapEl.innerHTML,calls:calls.length}));",
    )
    assert "Couldn’t read what's recorded just now" in result["first"]
    assert "gtrow" not in result["first"]
    assert result["calls"] == 4
    assert "2 here · ~3 nearby" in result["second"]
    assert "1 here · ~3 nearby" in result["second"]
    assert 'style="width:67%"' in result["second"]
    assert 'style="width:100%"' in result["second"]


def test_confirmed_empty_local_records_can_show_zero_coverage_and_be_cached():
    result = _run_popup(
        [
            {"total_results": 0, "results": []},
            {"total_results": 3, "results": [_species(i, 3) for i in range(1, 4)]},
        ],
        "await fetchGapTree(49,-123,gapEl,25000);const first=gapEl.innerHTML;"
        "await fetchGapTree(49,-123,gapEl,25000);"
        "console.log(JSON.stringify({first,second:gapEl.innerHTML,calls:calls.length}));",
    )
    assert "0 here · ~3 nearby" in result["first"]
    assert "gtrow gt-gap" in result["first"]
    assert result["second"] == result["first"]
    assert result["calls"] == 2


def test_failed_species_lookup_offers_a_working_retry_instead_of_claiming_absence():
    result = _run_popup(
        [
            {"error": "429"},
            {"total_results": 1, "results": [_species(1, 2)]},
            {"total_results": 1, "results": [_species(1, 3)]},
        ],
        "await fetchProspects(49,-123,'around_start');const first=spEl.innerHTML;"
        "spEl.querySelector('[data-prospect-retry]').onclick({preventDefault(){}});"
        "await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({first,second:spEl.innerHTML,calls:calls.length}));",
    )
    assert "Couldn’t load species just now" in result["first"]
    assert "No matching public research-grade iNaturalist records found here" not in result["first"]
    assert "2 here · 3 within 40 km" in result["second"]
    assert result["calls"] == 3


def test_confirmed_empty_species_response_keeps_the_empty_record_message():
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 0, "results": []}],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert "No matching public research-grade iNaturalist records found here" in result["html"]
    assert "Threatened species and species with protected locations are excluded" in result["html"]
    assert "No species suggestions" not in result["html"]


@pytest.mark.parametrize("incomplete", ["cell", "neighbourhood", "unknown_cell", "unknown_neighbourhood"])
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_incomplete_group_lists_do_not_classify_omitted_groups_and_are_not_cached(incomplete, lang):
    cell = {"total_results": 500, "results": [_species(i, 3) for i in range(1, 501)]}
    neighbourhood = {
        "total_results": 500,
        "results": [_species(i, 3) for i in range(1, 498)]
        + [_species(i, 3, group="Fungi") for i in range(498, 501)],
    }
    target = cell if incomplete.endswith("cell") and "neighbourhood" not in incomplete else neighbourhood
    if incomplete.startswith("unknown"):
        del target["total_results"]
    else:
        target["total_results"] = 501
    result = _run_popup(
        [
            cell,
            neighbourhood,
            {"total_results": 0, "results": []},
            {"total_results": 3, "results": [_species(i, 3) for i in range(1, 4)]},
        ],
        "await fetchGapTree(49,-123,gapEl,25000);const first=gapEl.innerHTML;"
        "await fetchGapTree(49,-123,gapEl,25000);"
        "console.log(JSON.stringify({first,second:gapEl.innerHTML,calls:calls.length}));",
        lang,
    )
    expected = (
        "Partial species lists cannot reliably compare groups here"
        if lang == "en"
        else "Des listes d'espèces partielles ne permettent pas de comparer les groupes ici de façon fiable"
    )
    assert expected in result["first"]
    assert "gtrow" not in result["first"]
    assert "0 here" not in result["first"]
    assert result["calls"] == 4
    assert "gtrow" in result["second"]


@pytest.mark.parametrize(
    "neighbourhood",
    [{"error": "429"}, {"total_results": 501, "results": [_species(2, 3)]}],
)
def test_missing_regional_evidence_does_not_invent_nearby_counts_or_rarity(neighbourhood):
    result = _run_popup(
        [{"total_results": 1, "results": [_species(1, 2)]}, neighbourhood],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    species = re.search(r'href="https://www.inaturalist.org/taxa/1".*?</a>', result["html"]).group(0)
    assert "2 here" in species
    assert "within 40 km" not in species
    assert "rare nearby" not in species


@pytest.mark.parametrize(
    ("lang", "badge", "scope"),
    [
        ("en", "no matching record", "Public research-grade iNaturalist records"),
        ("fr", "aucune obs. retenue", "Observations publiques de qualité recherche sur iNaturalist"),
    ],
)
def test_popup_absence_is_qualified_by_the_matching_record_scope(lang, badge, scope):
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 1, "results": [_species(1, 3)]}],
        "fetchGapTree=async()=>{};await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML}));",
        lang,
    )
    assert badge in result["html"]
    assert scope in result["html"]
    assert "new here" not in result["html"]
    assert "nouveau ici" not in result["html"]


def test_a_complete_500_species_page_can_compare_groups_without_a_cap_warning():
    result = _run_popup(
        [
            {"total_results": 500, "results": [_species(i, 3) for i in range(1, 501)]},
            {
                "total_results": 500,
                "results": [_species(i, 3) for i in range(1, 498)]
                + [_species(i, 3, group="Fungi") for i in range(498, 501)],
            },
        ],
        "await fetchGapTree(49,-123,gapEl,25000);console.log(JSON.stringify({html:gapEl.innerHTML}));",
    )
    assert "gtrow" in result["html"]
    assert "0 here · ~3 nearby" in result["html"]
    assert "Dense area" not in result["html"]
    assert "Partial species lists" not in result["html"]


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_capped_local_list_gets_one_bounded_candidate_check(lang):
    result = _run_popup(
        [
            {"total_results": 501, "results": [_species(1, 2)]},
            {"total_results": 3, "results": [_species(3, 7), _species(2, 3), _species(4, 5)]},
            {
                "total_results": 2,
                "results": [_species(2, 1), _species(44, 1, rank="subspecies", ancestors=[4])],
            },
        ],
        "surfaceFilter=async()=> '&without_taxon_id=marine';state.taxon='Aves';"
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls,callOptions}));",
        lang,
    )
    assert len(result["calls"]) == 3
    params = parse_qs(urlparse(result["calls"][2]).query)
    assert params["taxon_id"] == ["2,4,3"]
    assert params["page"] == ["1"] and params["per_page"] == ["500"]
    assert params["swlat"] == ["49"] and params["nelat"] == ["49.1"]
    assert params["iconic_taxa"] == ["Aves"]
    assert params["quality_grade"] == ["research"]
    assert params["geoprivacy"] == ["open"] and params["taxon_geoprivacy"] == ["open"]
    assert params["threatened"] == ["false"]
    assert "without_taxon_id" not in params and "radius" not in params
    assert result["callOptions"][2] == {"ms": 9000, "tries": 1}
    if lang == "fr":
        assert params["locale"] == ["fr"]
        assert "vérifiées séparément" in result["html"]
        assert "uniquement les espèces déjà enregistrées ici" not in result["html"]
    else:
        assert "checked separately" in result["html"]
        assert "only species already recorded here" not in result["html"]
    assert 'taxa/3"' in result["html"]
    assert 'taxa/2"' not in result["html"] and 'taxa/4"' not in result["html"]


@pytest.mark.parametrize(
    "batch",
    [
        {"error": "429"},
        {"results": []},
        {"total_results": 0, "results": [], "page": 2},
        {"total_results": 1, "results": []},
        {"total_results": 0, "results": {}},
        {"total_results": 1, "results": [None]},
        {"total_results": 1, "results": [_species(-1, 2)]},
        {"total_results": 1, "results": [_species(2, 0)]},
        {"total_results": 1, "results": [_species(2, 1.5)]},
        {"total_results": 2, "results": [_species(2, 1), _species(2, 1)]},
        {"total_results": 1, "results": [_species(99, 1)]},
        {"total_results": 1, "results": [_species(99, 1, ancestors=[98])]},
        {"total_results": 1, "results": [_species(99, 1, ancestors=[2, 3])]},
        {"total_results": 1, "results": [_species(99, 1, ancestors=[2, "bad"])]},
    ],
)
def test_uncertain_candidate_check_retains_local_cards_without_absence(batch):
    result = _run_popup(
        [
            {"total_results": 501, "results": [_species(1, 2)]},
            {"total_results": 2, "results": [_species(2, 3), _species(3, 4)]},
            batch,
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert len(result["calls"]) == 3
    assert 'taxa/1"' in result["html"]
    assert 'taxa/2"' not in result["html"] and 'taxa/3"' not in result["html"]
    assert "no matching record here" not in result["html"]
    assert "only species already recorded here" in result["html"]


def test_candidate_batch_is_species_only_deduplicated_and_rarest_first_with_eight_ids():
    region = [_species(i, i + 3) for i in range(2, 14)]
    region += [_species(3, 8), _species(99, 3, rank="genus"), _species(100, 3, rank="subspecies")]
    result = _run_popup(
        [
            {"total_results": 501, "results": [_species(1, 2)]},
            {"total_results": len(region), "results": list(reversed(region))},
            {"total_results": 0, "results": []},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    params = parse_qs(urlparse(result["calls"][2]).query)
    assert params["taxon_id"] == ["2,3,4,5,6,7,8,9"]
    html = result["html"]
    assert html.index('taxa/2"') < html.index('taxa/3"') < html.index('taxa/4"')
    assert 'taxa/99"' not in html and 'taxa/100"' not in html


@pytest.mark.parametrize("lat", [49, 77])
@pytest.mark.parametrize("res", [5000, 25000])
@pytest.mark.parametrize("renderer", ["prospects", "groups"])
def test_neighbourhood_uses_a_true_40_km_radius_without_intersecting_bbox(lat, res, renderer):
    action = (
        f"await fetchProspects({lat},-123,'right_here',{{resM:{res}}});"
        if renderer == "prospects"
        else f"await fetchGapTree({lat},-123,gapEl,{res});"
    )
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 0, "results": []}],
        "surfaceFilter=async()=> '&without_taxon_id=marine';"
        + action
        + "console.log(JSON.stringify({calls}));",
        "fr",
    )
    local, nearby = [parse_qs(urlparse(call).query) for call in result["calls"]]
    assert "radius" not in local and local["swlat"] == ["49"]
    assert nearby["lat"] == [str(lat)] and nearby["lng"] == ["-123"]
    assert nearby["radius"] == ["40"]
    assert not {"swlat", "nelat", "swlng", "nelng"}.intersection(nearby)
    assert nearby["without_taxon_id"] == ["marine"]
    assert local["locale"] == ["fr"] and nearby["locale"] == ["fr"]


@pytest.mark.parametrize("changed", ["taxon", "language", "sequence"])
@pytest.mark.parametrize("at_call", [2, 3])
def test_stale_candidate_requests_cannot_continue_or_paint(changed, at_call):
    change = {"taxon": "state.taxon='Fungi'", "language": "LANG='fr'", "sequence": "prospectSeq++"}[changed]
    result = _run_popup(
        [
            {"total_results": 501, "results": [_species(1, 2)]},
            {"total_results": 1, "results": [_species(2, 3)]},
            {"total_results": 0, "results": []},
        ],
        "const originalGet=jget;jget=async(...args)=>{const response=await originalGet(...args);"
        f"if(calls.length==={at_call}){{{change};spEl.innerHTML='new selection';}}return response;}};"
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert len(result["calls"]) == at_call
    assert result["html"] == "new selection"


def test_local_subspecies_prevents_false_parent_species_absence():
    result = _run_popup(
        [
            {"total_results": 1, "results": [_species(20, 2, rank="subspecies", ancestors=[2])]},
            {"total_results": 1, "results": [_species(2, 3)]},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert 'taxa/2"' not in result["html"]
    assert "no matching record here" not in result["html"]
    assert len(result["calls"]) == 2


@pytest.mark.parametrize("rows", [{}, [None], [_species(1, 0)], [_species(1, 2), _species(1, 2)]])
def test_malformed_local_results_need_verified_candidate_absence(rows):
    result = _run_popup(
        [
            {"total_results": len(rows), "results": rows},
            {"total_results": 1, "results": [_species(2, 3)]},
            {"error": "timeout"},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert len(result["calls"]) == 3
    assert 'taxa/2"' not in result["html"]
    assert "no matching record here" not in result["html"]


def test_malformed_result_container_does_not_turn_total_into_an_exact_species_count():
    result = _run_popup(
        [
            {"total_results": 0, "results": {}},
            {"total_results": 1, "results": [_species(2, 3)]},
            {"total_results": 0, "results": []},
        ],
        "await fetchProspects(49,-123,'around_start');console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert "≥0 species recorded" in result["html"]
    assert "· 0 species recorded" not in result["html"]


@pytest.mark.parametrize("changed", ["taxon", "language", "sequence"])
def test_stale_surface_resolution_cannot_launch_a_neighbourhood_or_candidate_request(changed):
    change = {"taxon": "state.taxon='Fungi'", "language": "LANG='fr'", "sequence": "prospectSeq++"}[changed]
    result = _run_popup(
        [{"total_results": 501, "results": [_species(1, 2)]}],
        "let resolveSurface;surfaceFilter=()=>new Promise(resolve=>{resolveSurface=resolve;});"
        "const pending=fetchProspects(49,-123,'around_start');await new Promise(resolve=>setImmediate(resolve));"
        + change
        + ";spEl.innerHTML='new selection';resolveSurface('');await pending;console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert len(result["calls"]) == 1
    assert result["html"] == "new selection"


@pytest.mark.parametrize("changed", ["taxon", "language", "sequence"])
def test_stale_group_counts_are_not_painted_or_cached(changed):
    change = {"taxon": "state.taxon='Fungi'", "language": "LANG='fr'", "sequence": "gapSeq++"}[changed]
    result = _run_popup(
        [{"total_results": 0, "results": []}, {"total_results": 0, "results": []}],
        "const originalGet=jget;jget=async(...args)=>{const response=await originalGet(...args);"
        "if(calls.length===2){"
        + change
        + ";gapEl.innerHTML='new selection';}return response;};"
        "await fetchGapTree(49,-123,gapEl,25000);console.log(JSON.stringify({html:gapEl.innerHTML,cache:GT_CACHE}));",
    )
    assert result["html"] == "new selection"
    assert result["cache"] == {}
