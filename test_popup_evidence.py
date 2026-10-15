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


def _run_popup(responses, action, lang="en", *, actual_cache=False, plant_pilot=None):
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
    script += "\nconst BC_PLANT_PILOT=" + json.dumps(plant_pilot) + ";\n"
    script += r"""
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')), calls=[],callOptions=[];
class Element{
  constructor(){this.innerHTML='';this.dataset={};}
  querySelector(selector){
    if((selector==='[data-prospect-retry]'||selector==='[data-group-retry]')&&this.innerHTML.includes(selector.slice(1,-1)))
      return this.retry||(this.retry=new Element());
    return null;
  }
  querySelectorAll(){return [];}
}
const spEl=new Element(),gapEl=new Element(),actionsEl=new Element();
const document={getElementById:()=>spEl};
const state={taxon:'All biodiversity',project:'test'};
const ICONIC={Aves:'Aves',Fungi:'Fungi',Plantae:'Plantae'},FILES={Aves:'birds',Fungi:'fungi'},taxonSel={};
const DOMESTIC=new Set(['Felis catus']);
const WHERE_LBL={right_here:'right_here',around_start:'prospects_where_start'};
let prospectSeq=0,_lastProspect=null,gapSeq=0;
const GT_CACHE={},GT_DENSE={};
const gtkey=(lat,lon)=>lat.toFixed(3)+','+lon.toFixed(3);
const gtHead=()=>'<div class="popsec">'+t('pop_groups_hd')+'</div>';
const groupName=k=>t('group')[k]||k;
let cellBoundsForRes=()=>({swlat:49,nelat:49.1,swlng:-123,nelng:-122.9});
let surfaceFilter=async()=>'';
let snapToLattice=()=>({row:1,col:2});
const inatLocale=()=>LANG==='fr'?'&locale=fr':'';
const HH=0.1;
let jget=async (url,ms,tries)=>{
  calls.push(url);callOptions.push({ms,tries});
  const response=input.responses.shift();
  if(!response)throw Error('unexpected request');
  if(response.error)throw Error(response.error);
  return response;
};
const _jc=new Map();
"""
    if actual_cache:
        script += """
const JC_MAX=150,sleep=async()=>{};
const fetchT=async(url)=>{
  calls.push(url);const response=input.responses.shift();
  if(!response)throw Error('unexpected request');
  if(response.error)throw Error(response.error);
  return {ok:true,status:200,json:async()=>response};
};
"""
        script += src[src.index("function _jcSet(") : src.index("// Robust JSON GET:")]
        script += _function(src, "jget").replace("async function jget", "jget=async function") + ";"
    script += src[src.index("const esc=") : src.index("// whereKey 'right_here'")]
    script += _function(src, "completeSpeciesCounts") + _function(src, "checkedCandidateAbsences")
    if "function validSpeciesCounts(" in src:
        script += _function(src, "validSpeciesCounts")
    script += _function(src, "cellRecordsUrl") + _function(src, "popupActionsHtml")
    script += _function(src, "refreshPopupActions")
    if "function rareRegionalRows(" in src:
        script += _function(src, "rareRegionalRows")
    if "function wireProspectSupport(" in src:
        script += _function(src, "prospectSupportRows") + _function(src, "wireProspectSupport")
        start = src.index("function haversine(")
        script += src[start : src.index("\n", start)] + "\n"
    for helper in ("validPlantPilotArtifact", "scorePlantPilot", "plantPilotForCell", "completePlantRegionalRows"):
        script += _function(src, helper)
    script += _function(src, "fetchProspects")
    script += _function(src, "paintGapTree") + _function(src, "fetchGapTree")
    if "function paintGapStatus(" in src:
        script += _function(src, "paintGapStatus")
    script += "\n(async()=>{" + action + "})().catch(e=>{console.error(e);process.exit(1);});"
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"responses": responses}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
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
    assert "Local list incomplete" in result["html"]
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
    assert ("Local list incomplete" in result["html"]) == (total > 500)


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
    if incomplete.startswith("unknown"):
        assert "data-group-retry" in result["first"]
    else:
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
        ("en", "no matching record here", "Public research-grade iNaturalist records"),
        (
            "fr",
            "aucune observation retenue ici",
            "Observations publiques de qualité recherche sur iNaturalist",
        ),
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


@pytest.mark.parametrize(
    ("lang", "status", "qualified", "local_count", "nearby_count"),
    [
        ("en", "no matching record", "no matching record here", "2 here", "10 within 40 km"),
        ("fr", "retenue", "aucune observation retenue ici", "2 ici", "10 dans 40 km"),
    ],
)
def test_popup_gap_status_appears_once_and_keeps_existing_species_counts(
    lang, status, qualified, local_count, nearby_count
):
    result = _run_popup(
        [
            {"total_results": 1, "results": [_species(1, 2)]},
            {
                "total_results": 4,
                "results": [_species(1, 10), _species(2, 3), _species(3, 4), _species(4, 5)],
            },
        ],
        "fetchGapTree=async()=>{};await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML}));",
        lang,
    )
    html = result["html"]
    cards = "".join(re.findall(r'<a href="https://www.inaturalist.org/taxa/.*?</a>', html))
    assert cards.count(status) == 3
    assert cards.count(qualified) == 3
    assert local_count in html and nearby_count in html
    assert html.index('taxa/2"') < html.index('taxa/3"') < html.index('taxa/4"') < html.index('taxa/1"')


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
        "if(calls.length===2){" + change + ";gapEl.innerHTML='new selection';}return response;};"
        "await fetchGapTree(49,-123,gapEl,25000);console.log(JSON.stringify({html:gapEl.innerHTML,cache:GT_CACHE}));",
    )
    assert result["html"] == "new selection"
    assert result["cache"] == {}


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_group_retry_evicts_a_malformed_200_body_from_the_actual_request_cache(lang):
    result = _run_popup(
        [
            {"results": []},
            {"total_results": 3, "results": [_species(i, 3) for i in range(1, 4)]},
            {"total_results": 0, "results": []},
        ],
        "await fetchGapTree(49,-123,gapEl,25000);const first=gapEl.innerHTML;"
        "const retry=gapEl.querySelector('[data-group-retry]');"
        "if(retry)await retry.onclick({preventDefault(){}});"
        "console.log(JSON.stringify({first,second:gapEl.innerHTML,calls:calls.length}));",
        lang,
        actual_cache=True,
    )
    assert "data-group-retry" in result["first"]
    assert result["calls"] == 3  # the valid nearby response stays cached
    assert "gtrow" in result["second"]


def test_a_valid_capped_group_list_has_no_retry_and_remains_cached():
    result = _run_popup(
        [
            {"total_results": 501, "results": [_species(i, 3) for i in range(1, 501)]},
            {"total_results": 3, "results": [_species(i, 3) for i in range(1, 4)]},
        ],
        "await fetchGapTree(49,-123,gapEl,25000);const first=gapEl.innerHTML;"
        "await fetchGapTree(49,-123,gapEl,25000);"
        "console.log(JSON.stringify({first,second:gapEl.innerHTML,calls:calls.length}));",
        actual_cache=True,
    )
    assert "Partial species lists" in result["first"]
    assert "data-group-retry" not in result["first"]
    assert result["calls"] == 2


@pytest.mark.parametrize(
    "response",
    [
        {"total_results": 0, "results": []},
        {"total_results": 1, "results": [_species(1, 2)]},
        {"total_results": 501, "results": [_species(1, 2)]},
        {"error": "offline"},
    ],
)
def test_recording_actions_survive_empty_capped_and_failed_evidence(response):
    result = _run_popup(
        [response, {"total_results": 0, "results": []}],
        "fetchGapTree=()=>{};await fetchProspects(49,-123,'destination',{toPopup:true,spEl,actionsEl,resM:5000});"
        "console.log(JSON.stringify({html:actionsEl.innerHTML}));",
    )
    assert "View matching records" in result["html"]
    assert 'href="https://www.inaturalist.org/observations/new"' in result["html"]
    href = re.search(r'href="([^\"]+)"', result["html"]).group(1).replace("&amp;", "&")
    assert parse_qs(urlparse(href).query) == {
        "subview": ["map"],
        "swlat": ["49"],
        "nelat": ["49.1"],
        "swlng": ["-123"],
        "nelng": ["-122.9"],
        "quality_grade": ["research"],
        "geoprivacy": ["open"],
        "taxon_geoprivacy": ["open"],
        "threatened": ["false"],
    }


def test_recording_actions_follow_current_group_language_and_cell_scale():
    result = _run_popup(
        [],
        "fetchGapTree=()=>{};jget=async()=>({total_results:0,results:[]});"
        "cellBoundsForRes=(_lat,_lon,res)=>({swlat:res/1000,nelat:res/1000+1,swlng:-123,nelng:-122});"
        "state.taxon='Aves';await fetchProspects(49,-123,'destination',{toPopup:true,spEl,actionsEl,resM:5000});"
        "const first=actionsEl.innerHTML;LANG='fr';state.taxon='Fungi';"
        "await fetchProspects(50,-124,'destination',{toPopup:true,spEl,actionsEl,resM:25000});"
        "console.log(JSON.stringify({first,second:actionsEl.innerHTML}));",
    )
    first = parse_qs(
        urlparse(re.search(r'href="([^\"]+)"', result["first"]).group(1).replace("&amp;", "&")).query
    )
    second = parse_qs(
        urlparse(re.search(r'href="([^\"]+)"', result["second"]).group(1).replace("&amp;", "&")).query
    )
    assert first["iconic_taxa"] == ["Aves"] and first["swlat"] == ["5"]
    assert second["iconic_taxa"] == ["Fungi"] and second["swlat"] == ["25"]
    assert second["locale"] == ["fr"]
    assert "Voir les observations retenues" in result["second"]
    assert "Noter une observation" in result["second"]


def test_evidence_and_locale_refresh_preserve_the_focused_record_action():
    result = _run_popup(
        [],
        "fetchGapTree=()=>{};jget=async()=>({total_results:0,results:[]});"
        "const first={href:'',textContent:''},second={href:'https://www.inaturalist.org/observations/new',textContent:''};"
        "actionsEl.querySelectorAll=()=>[first,second];document.activeElement=first;let replacements=0;"
        "Object.defineProperty(actionsEl,'innerHTML',{set(){replacements++;document.activeElement=null;}});"
        "state.taxon='Aves';await fetchProspects(49,-123,'destination',{toPopup:true,spEl,actionsEl,resM:5000});"
        "const en=first.textContent;LANG='fr';state.taxon='Fungi';"
        "await fetchProspects(49,-123,'destination',{toPopup:true,spEl,actionsEl,resM:5000});"
        "console.log(JSON.stringify({replacements,focused:document.activeElement===first,en,fr:first.textContent,href:first.href,log:second.href}));",
    )
    assert result["replacements"] == 0 and result["focused"] is True
    assert result["en"] == "View matching records"
    assert result["fr"] == "Voir les observations retenues"
    assert parse_qs(urlparse(result["href"]).query)["iconic_taxa"] == ["Fungi"]
    assert result["log"] == "https://www.inaturalist.org/observations/new"


@pytest.mark.parametrize(
    ("medium_url", "expected"),
    [
        ("https://example.org/medium.jpg", "https://example.org/medium.jpg"),
        (None, "https://example.org/photo.jpg"),
        ("", "https://example.org/photo.jpg"),
        ("javascript:alert(1)", "https://example.org/photo.jpg"),
        ("data:image/svg+xml,unsafe", "https://example.org/photo.jpg"),
    ],
)
def test_popup_uses_larger_photos_with_a_safe_thumbnail_fallback(medium_url, expected):
    species = _species(1, 2)
    species["taxon"]["default_photo"]["medium_url"] = medium_url
    result = _run_popup(
        [{"total_results": 1, "results": [species]}, {"total_results": 0, "results": []}],
        "fetchGapTree=()=>{};await fetchProspects(49,-123,'destination',{toPopup:true,spEl,resM:25000});"
        "console.log(JSON.stringify({html:spEl.innerHTML}));",
    )
    assert f'<img src="{expected}"' in result["html"]
    assert 'data-fallback="https://example.org/photo.jpg"' in result["html"]
    assert "this.onerror=null" in result["html"]
    assert "javascript:" not in result["html"] and "data:image" not in result["html"]


def _paginated_prospects(pages, *, local=None, check=None, lang="en", failed_page=None, stale_page=None):
    return _run_popup(
        [local or {"total_results": 0, "results": []}, *pages],
        "const local=input.responses.shift(),pages=input.responses;fetchGapTree=async()=>{};"
        "jget=async url=>{calls.push(url);const p=new URL(url).searchParams;"
        + "if(p.has('taxon_id'))return " + json.dumps(check or {"total_results": 0, "results": []}) + ";"
        + "if(!p.has('radius'))return local;const page=Number(p.get('page')||1);"
        + (f"if(page==={failed_page})throw Error('429');" if failed_page else "")
        + (f"if(page==={stale_page}){{prospectSeq++;spEl.innerHTML='new selection';}}" if stale_page else "")
        + "return pages[page-1];};"
        "surfaceFilter=async()=> '&without_taxon_id=marine';"
        "await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
        lang,
    )


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_regional_rarity_is_not_the_bottom_of_the_most_common_500(lang):
    rows = [_species(i, 2000 - i) for i in range(1, 501)]
    rows += [_species(i, 4) for i in range(501, 1000)] + [_species(1000, 3)]
    rows += [_species(i, 1) for i in range(1001, 2501)]
    pages = [{"total_results": len(rows), "results": rows[i : i + 500]} for i in range(0, len(rows), 500)]
    result = _paginated_prospects(pages, lang=lang)
    html = result["html"]
    assert 'taxa/1000"' in html
    assert ("3 within 40 km" if lang == "en" else "3 dans 40 km") in html
    assert 'taxa/500"' not in html
    assert len(re.findall(r'href="https://www.inaturalist.org/taxa/', html)) == 4
    assert len(result["calls"]) <= 6  # local + first page + logarithmic probes + adjacent page
    for call in result["calls"][1:]:
        params = parse_qs(urlparse(call).query)
        assert params['radius'] == ['40'] and params['order_by'] == ['count']
        assert params['without_taxon_id'] == ['marine']
        assert params['quality_grade'] == ['research'] and params['threatened'] == ['false']
        assert params['geoprivacy'] == ['open'] and params['taxon_geoprivacy'] == ['open']
        if lang == "fr":
            assert params['locale'] == ["fr"]


@pytest.mark.parametrize("boundary", [1, 2, 3])
def test_rarest_qualifying_page_can_be_first_middle_or_last(boundary):
    rows = [_species(i, 100) for i in range(1, (boundary - 1) * 500 + 1)]
    rare_id = len(rows) + 1
    rows += [_species(rare_id + i, 3 if i < 8 else 1) for i in range(500)]
    rows += [_species(i, 1) for i in range(len(rows) + 1, 1501)]
    pages = [{"total_results": 1500, "results": rows[i : i + 500]} for i in range(0, 1500, 500)]
    result = _paginated_prospects(pages)
    for taxon_id in range(rare_id, rare_id + 4):
        assert f'taxa/{taxon_id}"' in result["html"]
    assert result["html"].count("3 within 40 km") == 4
    if boundary == 1:
        assert len(result["calls"]) == 2


def _three_species_pages():
    rows = [_species(i, 100) for i in range(1, 501)]
    rows += [_species(i, 3) for i in range(501, 1001)]
    rows += [_species(i, 1) for i in range(1001, 1501)]
    return [{"total_results": len(rows), "results": rows[i : i + 500]} for i in range(0, len(rows), 500)]


@pytest.mark.parametrize("failure", ["request", "total", "page", "short", "order", "invalid"])
def test_failed_or_inconsistent_page_search_keeps_local_evidence_without_cutoff_suggestions(failure):
    pages = _three_species_pages()
    if failure == "total":
        pages[1]["total_results"] += 1
    elif failure == "page":
        pages[1]["page"] = 1
    elif failure == "short":
        pages[1]["results"].pop()
    elif failure == "order":
        pages[1]["results"][1]["count"] = 4
    elif failure == "invalid":
        pages[1]["results"][1] = None
    result = _paginated_prospects(
        pages, local={"total_results": 1, "results": [_species(1, 2)]},
        failed_page=2 if failure == "request" else None,
    )
    assert 'taxa/1"' in result["html"] and "2 here" in result["html"]
    assert "no matching record here" not in result["html"]
    assert 'taxa/500"' not in result["html"]


def test_stale_page_search_stops_before_requesting_or_painting_more_species():
    result = _paginated_prospects(_three_species_pages(), stale_page=2)
    assert result["html"] == "new selection"
    assert len(result["calls"]) == 3


def test_rare_page_candidates_still_get_the_capped_local_presence_check():
    result = _paginated_prospects(
        _three_species_pages(),
        local={"total_results": 501, "results": [_species(501, 1)]},
        check={"total_results": 1, "results": [_species(502, 1)]},
    )
    params = parse_qs(urlparse(result["calls"][-1]).query)
    assert params["taxon_id"] == ["502,503,504,505,506,507,508,509"]
    assert "radius" not in params and "swlat" in params
    assert 'taxa/501"' not in result["html"]
    assert 'taxa/502"' not in result["html"]
    assert 'taxa/503"' in result["html"]
    assert "checked separately" in result["html"]


def test_ineligible_rare_pages_do_not_hide_eligible_species_on_an_earlier_page():
    pages = _three_species_pages()
    for row in pages[1]["results"]:
        row["taxon"]["observations_count"] = 20
    for row in pages[0]["results"]:
        row["count"] = 101
    pages[0]["results"][-1]["count"] = 100
    result = _paginated_prospects(pages)
    assert 'taxa/500"' in result["html"]
    assert "100 within 40 km" in result["html"]
    assert 'taxa/501"' not in result["html"]


def test_partial_final_page_keeps_its_rare_candidate_and_fills_from_preceding_page():
    rows = [_species(i, 10) for i in range(1, 501)] + [_species(501, 3)]
    result = _paginated_prospects(
        [{"total_results": 501, "results": rows[:500]}, {"total_results": 501, "results": rows[500:]}]
    )
    assert 'taxa/501"' in result["html"] and "3 within 40 km" in result["html"]
    assert len(re.findall(r'href="https://www.inaturalist.org/taxa/', result["html"])) == 4
    assert len(result["calls"]) == 3


def test_regional_page_search_has_a_finite_request_budget():
    first = {"total_results": 500 * 8192, "results": [_species(i, 3) for i in range(1, 501)]}
    result = _run_popup(
        [first],
        "const first=input.responses[0];fetchGapTree=async()=>{};"
        "jget=async url=>{calls.push(url);return new URL(url).searchParams.has('radius')?first:"
        "{total_results:0,results:[]};};"
        "await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        "console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
    )
    assert len(result["calls"]) == 14  # local + initial region + at most twelve probes
    assert "no matching record here" not in result["html"]
