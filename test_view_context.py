"""Visible context follows the active data layer without changing its filters."""

import json
import subprocess

import pytest

from test_shared_context import TEMPLATE, _function


def run_context(action):
    source = TEMPLATE.read_text()
    script = source[source.index("const I18N=") : source.index("\nlet LANG=")]
    script += """
let LANG='en',overlay='priority',tier=25000,recentReady=false;
const state={taxon:'Aves'},window={__activePreset:0},COVPM=['All biodiversity','Aves','Plantae'];
const OBJ=[{key:'discover'},{key:'conservation'},{key:'env'},{key:'staleness'},{key:'urgency'}];
const PRESETS=[{w:[1,0,0,0,0]},{w:[0,1,0,0,1]}];
const geActive=()=>overlay==='gettingeven',covOn=()=>overlay==='coverage',climateOn=()=>overlay==='climate',clActive=()=>overlay==='checklist';
const activeCellResM=()=>tier,presetName=()=>LANG==='en'?'Spatial Gap':'Lacune spatiale';
const groupName=key=>I18N[LANG].group[key]||key;
globalThis.optionalLayers={isDiscovery:()=>overlay==='discovery',recentReady:()=>recentReady,group:()=>"Plantae"};
class Element{
 constructor(){this.children=[];this._text='';}
 set textContent(value){this._text=value;this.children=[];}
 get textContent(){return this._text;}
 append(child){this.children.push(child);}
 prepend(child){this.children.unshift(child);}
 querySelector(selector){return this.children.find(child=>'#'+child.id===selector)||null;}
}
const legend=new Element(),details=new Element();details.open=true;legend.append(details);
const document={getElementById:id=>id==='maplegend'?legend:null,createElement:()=>new Element()};
"""
    for name in ("t", "mapContextText", "paintMapContext"):
        script += _function(source, name)
    result = subprocess.run(["node"], input=script + action, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_context_names_only_relevant_groups_and_tracks_the_current_grid(language):
    result = run_context(
        "LANG="
        + json.dumps(language)
        + ";const priority=mapContextText();tier=5000;const fine=mapContextText();"
        "window.__activePreset=1;const conservation=mapContextText();overlay='gettingeven';const ge=mapContextText();overlay='climate';const climate=mapContextText();"
        "overlay='discovery';const historical=mapContextText();overlay='coverage';const density=mapContextText();"
        "state.taxon='Mollusca';const fallback=mapContextText();"
        "console.log(JSON.stringify({priority,fine,conservation,ge,climate,historical,density,fallback}));"
    )
    group = "Birds" if language == "en" else "Oiseaux"
    assert group in result["priority"] and group in result["fine"] and group in result["density"]
    assert "25 km" in result["priority"] and "5 km" in result["fine"]
    for mode in ("conservation", "ge", "climate", "historical", "fallback"):
        assert group not in result[mode] and "5 km" in result[mode]
    assert ("All biodiversity" if language == "en" else "Toute la biodiversité") in result["conservation"]
    assert ("Plants" if language == "en" else "Plantes") in result["historical"]
    assert "Mollusca" not in result["fallback"]
    assert ("Getting Even" if language == "en" else "Combler l'écart") in result["ge"]
    assert ("Historical" if language == "en" else "historiques") in result["historical"]


def test_ready_recent_key_updates_without_replacing_open_legend_details():
    result = run_context(
        "paintMapContext();const context=legend.querySelector('#mapcontext'),pending=context.children.length;"
        "recentReady=true;paintMapContext();const ready=context.children[0].textContent;"
        "LANG='fr';tier=5000;paintMapContext();const french={context:context.textContent,key:context.children[0].textContent};"
        "recentReady=false;paintMapContext();console.log(JSON.stringify({pending,ready,french,"
        "hidden:context.children.length===0,stable:legend.children[1]===details&&details.open,"
        "single:legend.children.filter(child=>child.id==='mapcontext').length===1}));"
    )
    assert result["pending"] == 0 and "all groups" in result["ready"]
    assert "Oiseaux" in result["french"]["context"] and "5 km" in result["french"]["context"]
    assert "tous groupes" in result["french"]["key"]
    assert "14" not in result["ready"] and "14" not in result["french"]["key"]
    assert result["hidden"] and result["stable"] and result["single"]


@pytest.mark.parametrize("overlay", ["priority", "coverage"])
@pytest.mark.parametrize("failed", [False, True])
def test_actual_group_change_refreshes_context_while_pending_and_after_failure(overlay, failed):
    from test_shared_context import _run

    result = _run(
        "",
        "const seen=[];globalThis.refreshMapContext=()=>seen.push(state.taxon);"
        + "overlay="
        + json.dumps(overlay)
        + ";let release,reject;loadGroup=()=>new Promise((yes,no)=>{release=yes;reject=no;});"
        "taxonSel.value='Aves';const pending=taxonSel.onchange();const immediate=seen.slice();"
        + ("reject(Error('offline'));" if failed else "release();")
        + "await pending;console.log(JSON.stringify({immediate,seen,taxon:state.taxon}));",
        controls=True,
    )
    assert result["immediate"] == ["Aves"]
    assert result["seen"] == (["Aves", "All biodiversity"] if failed else ["Aves"])
    assert result["taxon"] == ("All biodiversity" if failed else "Aves")


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("popup", [True, False])
def test_species_cards_keep_names_evidence_order_and_accessible_external_destination(language, popup):
    import re
    from html import unescape

    from test_popup_evidence import _run_popup, _species

    local = _species(1, 2)
    common, identical, absent = [_species(i, i + 1) for i in range(2, 5)]
    common["taxon"]["preferred_common_name"] = "Common & name"
    identical["taxon"]["preferred_common_name"] = identical["taxon"]["name"]
    invocation = (
        "fetchGapTree=async()=>{};await fetchProspects(49,-123,'right_here',{toPopup:true,spEl,gapsEl:gapEl});"
        if popup
        else "await fetchProspects(49,-123,'around_start');"
    )
    result = _run_popup(
        [
            {"total_results": 1, "results": [local]},
            {"total_results": 4, "results": [_species(1, 10), common, identical, absent]},
        ],
        invocation + "console.log(JSON.stringify({html:spEl.innerHTML,calls}));",
        language,
    )
    cards = re.findall(r'<a[^>]+href="https://www.inaturalist.org/taxa/[^>]+>.*?</a>', result["html"])
    assert [int(re.search(r"taxa/(\d+)", card)[1]) for card in cards] == [2, 3, 4, 1]
    assert 'Common &amp; name' in cards[0]
    assert '<div class="species-scientific">Species 2</div>' in cards[0]
    assert all('species-scientific' not in card for card in cards[1:])
    assert re.sub(r"<[^>]*>", "", cards[1]).count("Species 3") == 1
    description = 'popup-species-destination' if popup else 'start-species-destination'
    assert result["html"].count('id="' + description + '"') == 1
    for card in cards:
        assert 'aria-describedby="' + description + '"' in card and 'aria-label=' not in card
        assert 'target="_blank" rel="noopener"' in card
        assert 'iNaturalist' in unescape(card)
    assert ('3 within 40 km' if language == 'en' else '3 dans 40 km') in cards[0]
    assert ('2 here' if language == 'en' else '2 ici') in cards[-1]
    assert ('new tab' if language == 'en' else 'nouvel onglet') in result["html"]
    assert len(result["calls"]) == 2


def test_recent_key_waits_for_verified_snapshot_and_visible_map():
    from test_optional_layer_viewer import sighting_lifecycle

    result = sighting_lifecycle(
        "const seen=[];globalThis.refreshMapContext=()=>seen.push(optionalLayers.recentReady());"
        "let release;const original=OptionalData.fetchPinned;OptionalData.fetchPinned=async(...args)=>{"
        "await new Promise(resolve=>release=resolve);return original(...args);};"
        "const pending=optionalLayers.toggleRecent(true),loading=optionalLayers.recentReady();"
        "release();await pending;const ready=optionalLayers.recentReady();"
        "_mapLoaded=false;optionalLayers.refresh();const unloaded=optionalLayers.recentReady();"
        "_mapLoaded=true;await optionalLayers.toggleRecent(false);"
        "console.log(JSON.stringify({loading,ready,unloaded,hidden:optionalLayers.recentReady(),seen}));",
        start_recent=False,
    )
    assert result == {
        "loading": False,
        "ready": True,
        "unloaded": False,
        "hidden": False,
        "seen": [False, True, False, False, False],
    }
