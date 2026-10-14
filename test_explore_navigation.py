"""Replay explicit place search through the shipped controller and selection fences."""

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from test_shared_context import _function

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
OTTAWA = {"name": "Ottawa", "latitude": 45.33339, "longitude": -75.58429, "province": {"code": "35"}}


def _run(action, responses=(), lang="en", *, plan=False):
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("let LANG=")]
    script += "\nlet LANG=" + json.dumps(lang) + ";\n"
    script += src[src.index("function t(") : src.index("// indexed-name helpers")]
    script += r"""
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8')),calls=[],steps=[];
class Element{
  constructor(id=''){this.id=id;this.value='';this.hidden=false;this.children=[];this.attrs={};this.dataset={};this.handlers={};this.style={};this.innerHTML='';this.textContent='';this.isConnected=true;
    const classes=new Set();this.classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)};}
  setAttribute(k,v){this.attrs[k]=String(v);}
  removeAttribute(k){delete this.attrs[k];}
  replaceChildren(...children){this.children=children;}
  append(...children){this.children.push(...children);}
  appendChild(child){this.children.push(child);}
  addEventListener(k,fn){this.handlers[k]=fn;}
  focus(){document.activeElement=this;}
  querySelectorAll(){return this.children;}
  querySelector(){return this.children.find(c=>c.dataset.lat)||null;}
  contains(el){return this.children.includes(el);}
  scrollIntoView(){}
  click(){if(this.onclick)this.onclick();}
}
const nodes={},document={activeElement:null,getElementById:id=>nodes[id]||(nodes[id]=new Element(id)),createElement:()=>new Element(),addEventListener(k,fn){this[k]=fn;}};
const state={view:'explore',taxon:'Aves',start:[49,-123],w:{gap:1}};
let location=new URL('https://example.org/map?at=49,-123&res=5000&g=Aves&goal=2&style=satellite&keep=yes#map');
const history={replaceState:(_a,_b,url)=>{location=new URL(url,location);}};
const window={__activePreset:2},critSel=document.getElementById('criteria'),bmSel=document.getElementById('basemap');
critSel.value='2';bmSel.value='Satellite';document.getElementById('placepanel').hidden=true;
let overlay='priority',_mapLoaded=true,_selectionSeq=0,_shareAction=0,_taxonSeq=0,_taxonPending=null;
let _sharedCell={lat:49,lon:-123,resM:5000},_shareContext=true,_lastProspect=null,_popScoreEl=null;
const geActive=()=>overlay==='gettingeven',climateOn=()=>overlay==='climate',covOn=()=>overlay==='coverage';
const TIER_SWITCH_Z=9,canvas=new Element('canvas'),map={jumpTo:o=>steps.push({jump:o}),getCanvas:()=>canvas};
const focusPopupTarget=el=>el.focus(),closePopup=()=>steps.push('close'),clearRoute=()=>steps.push('clear');
const renderCellTable=()=>steps.push('nearby cells');
const setStart=(lat,lon,name)=>{state.start=[lat,lon];steps.push({start:name});};
const FILES={Aves:'a.json'},PRESETS=[{}, {}, {}];let _overlayReady=Promise.resolve(),DRAWN5=null;
const whenMap=fn=>fn(),buildMarkers=()=>{},setView=v=>{state.view=v;},applyChallenge=()=>{},setBase=()=>{};
let loadGroup=async()=>{},loadUS=async()=>{};const setOverlay=async kind=>{overlay=kind;};
const refreshCells5=async()=>{},snapToLattice=(lat,lon)=>({lat,lon}),latKey=()=> 'cell';
const exploreCell=()=>{steps.push('old cell');return true;},locateMe=()=>steps.push('locate');
let fetchT=async(url,ms)=>{calls.push({url,ms});const r=input.responses.shift();if(!r)throw Error('no fixture');if(r.error)throw Error(r.error);return {ok:r.ok!==false,status:r.status||200,json:async()=>r.body};};
const sleep=async ms=>steps.push({sleep:ms});
"""
    for name in ("sharedCellUrl", "syncSharedCellUrl", "clearSelection", "restoreSharedCell"):
        script += _function(src, name)
    script += src[
        src.index("// Explore navigation searches official Canadian names") : src.index(
            "// The optional Plan address search"
        )
    ]
    if plan:
        script += src[src.index("// The optional Plan address search") : src.index("function locateMe(){")]
    script += (
        "\nconst event=key=>({key,preventDefault(){}});\n(async()=>{"
        + action
        + "})().catch(e=>{console.error(e);process.exit(1);});"
    )
    out = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"responses": list(responses)}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_opening_and_typing_do_not_issue_a_search():
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';placeQuery.handlers.input();"
        "console.log(JSON.stringify({calls,focus:document.activeElement.id,status:placeStatus.textContent}));"
    )
    assert result["calls"] == []
    assert result["focus"] == "placeQuery"
    assert "tap a coloured cell" in result["status"]


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_explicit_search_uses_official_names_and_current_language(lang):
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';await searchPlaces();"
        "console.log(JSON.stringify({calls,items:placeFinder.items,attrs:placeQuery.attrs}));",
        [{"body": {"items": [OTTAWA]}}],
        lang,
    )
    call = result["calls"][0]
    assert urlparse(call["url"]).netloc == "geogratis.gc.ca"
    assert urlparse(call["url"]).path == f"/services/geoname/{lang}/geonames.json"
    assert parse_qs(urlparse(call["url"]).query) == {"q": ["Ottawa"], "category": ["O"], "num": ["6"]}
    assert call["ms"] == 9000
    assert result["items"] == [OTTAWA]
    assert result["attrs"]["aria-expanded"] == "true"


def test_short_search_does_not_request_the_service():
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='ab';await searchPlaces();"
        "console.log(JSON.stringify({calls,status:placeStatus.textContent}));"
    )
    assert result["calls"] == []
    assert "at least 3" in result["status"]


@pytest.mark.parametrize(
    "body",
    [{"items": []}, {"items": None}, {}, {"items": [{"name": "Invalid", "latitude": None, "longitude": 12}]}],
)
def test_empty_and_invalid_response_are_distinguished(body):
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';await searchPlaces();"
        "console.log(JSON.stringify({status:placeStatus.textContent,items:placeFinder.items}));",
        [{"body": body}],
    )
    assert result["items"] == []
    assert ("No matching places" if body == {"items": []} else "unavailable") in result["status"]


@pytest.mark.parametrize(
    "failure", [{"error": "AbortError"}, {"ok": False, "status": 503}, {"body": {"items": "bad"}}]
)
def test_failed_search_is_retryable_and_not_cached(failure):
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';await searchPlaces();const error=placeStatus.textContent;"
        "await searchPlaces();console.log(JSON.stringify({error,calls,items:placeFinder.items,cache:placeFinder.cache.size}));",
        [failure, {"body": {"items": [OTTAWA]}}],
    )
    assert "unavailable" in result["error"]
    assert len(result["calls"]) == 2
    assert result["items"] == [OTTAWA]
    assert result["cache"] == 1


def test_success_cache_is_language_scoped_and_bounded():
    responses = [{"body": {"items": [OTTAWA]}}] * 32
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';await searchPlaces();await searchPlaces();"
        "LANG='fr';relabelPlaceFinder();await searchPlaces();"
        "for(let i=0;i<30;i++){placeQuery.value='place'+i;await searchPlaces();}"
        "console.log(JSON.stringify({calls,cache:placeFinder.cache.size}));",
        responses,
    )
    assert len(result["calls"]) == 32
    assert "/en/" in result["calls"][0]["url"] and "/fr/" in result["calls"][1]["url"]
    assert result["cache"] == 30


@pytest.mark.parametrize(
    "supersede",
    [
        "placeQuery.value='Victoria';placeQuery.handlers.input();",
        "LANG='fr';relabelPlaceFinder();",
        "setPlaceFinderOpen(false);",
    ],
)
def test_late_response_cannot_replace_edited_closed_or_retranslated_search(supersede):
    result = _run(
        "let finish;fetchT=()=>new Promise(resolve=>{finish=resolve;});setPlaceFinderOpen(true);placeQuery.value='Ottawa';"
        "const pending=searchPlaces();"
        + supersede
        + "finish({ok:true,json:async()=>({items:[{name:'Ottawa',latitude:45,longitude:-75}]})});"
        "await pending;console.log(JSON.stringify({items:placeFinder.items,status:placeStatus.textContent}));"
    )
    assert result["items"] == []
    assert "tap a coloured cell" in result["status"] or "cellule colorée" in result["status"]


def test_keyboard_selection_preserves_context_and_clears_the_old_cell():
    result = _run(
        "setPlaceFinderOpen(true);placeQuery.value='Ottawa';await searchPlaces();"
        "placeQuery.handlers.keydown(event('ArrowDown'));const active=placeQuery.attrs['aria-activedescendant'];"
        "placeQuery.handlers.keydown(event('Enter'));console.log(JSON.stringify({active,steps,state,url:location.href,seq:_selectionSeq,action:_shareAction,focus:document.activeElement.id,hidden:placePanel.hidden}));",
        [{"body": {"items": [OTTAWA]}}],
    )
    assert result["active"] == "geoname-result-0"
    assert result["steps"] == [
        "close",
        "clear",
        {"jump": {"center": [-75.58429, 45.33339], "zoom": 7}},
        "nearby cells",
    ]
    assert result["state"]["taxon"] == "Aves" and result["state"]["view"] == "explore"
    assert result["state"]["start"] == [49, -123]
    assert parse_qs(urlparse(result["url"]).query) == {
        "g": ["Aves"],
        "goal": ["2"],
        "style": ["satellite"],
        "keep": ["yes"],
    }
    assert result["seq"] == 1 and result["action"] == 1
    assert result["focus"] == "canvas" and result["hidden"] is True


def test_selecting_a_place_cancels_a_restore_waiting_for_the_canada_mask():
    result = _run(
        "let finish;loadUS=()=>new Promise(resolve=>{finish=resolve;});_shareAction=1;"
        "const pending=restoreSharedCell({cell:{lat:49,lon:-123,resM:5000},goal:'2',style:'Satellite'},0,'Aves',0);"
        "await new Promise(resolve=>setImmediate(resolve));selectPlaceResult({name:'Ottawa',latitude:45,longitude:-75});finish();await pending;"
        "console.log(JSON.stringify({steps,url:location.href}));"
    )
    assert result["steps"] == ["close", "clear", {"jump": {"center": [-75, 45], "zoom": 7}}, "nearby cells"]
    assert "at=" not in result["url"]


def test_escape_closes_search_and_restores_its_button_focus():
    result = _run(
        "setPlaceFinderOpen(true);document.keydown(event('Escape'));"
        "console.log(JSON.stringify({hidden:placePanel.hidden,focus:document.activeElement.id}));"
    )
    assert result == {"hidden": True, "focus": "findplacebtn"}


def test_plan_search_is_explicit_cached_and_serialized():
    result = _run(
        "psEl.value='Ottawa';psEl.handlers.input();const before=calls.length;await doSearch(psEl.value);await doSearch(psEl.value);"
        "psEl.value='Victoria';await doSearch(psEl.value);console.log(JSON.stringify({before,calls,steps}));",
        [
            {"body": [{"display_name": "Ottawa, Ontario", "lat": "45", "lon": "-75"}]},
            {"body": [{"display_name": "Victoria, BC", "lat": "48", "lon": "-123"}]},
        ],
        plan=True,
    )
    assert result["before"] == 0 and len(result["calls"]) == 2
    assert all(urlparse(c["url"]).netloc == "nominatim.openstreetmap.org" for c in result["calls"])
    assert any("sleep" in step for step in result["steps"] if isinstance(step, dict))


def test_arrow_up_starts_at_last_result_then_wraps_to_first():
    result = _run(
        "setPlaceFinderOpen(true);renderPlaceResults([{name:'First',latitude:45,longitude:-75},{name:'Last',latitude:46,longitude:-76}]);"
        "placeQuery.handlers.keydown(event('ArrowUp'));const last=placeFinder.index;"
        "placeQuery.handlers.keydown(event('ArrowDown'));console.log(JSON.stringify({last,first:placeFinder.index}));"
    )
    assert result == {"last": 1, "first": 0}


def test_invalid_coordinates_cannot_move_the_map_or_clear_selection():
    result = _run(
        "for(const latitude of [null,NaN,Infinity,91,-91,'45'])selectPlaceResult({name:'Invalid',latitude,longitude:-75});"
        "for(const longitude of [null,NaN,Infinity,181,-181,'-75'])selectPlaceResult({name:'Invalid',latitude:45,longitude});"
        "console.log(JSON.stringify({steps,seq:_selectionSeq,action:_shareAction}));"
    )
    assert result == {"steps": [], "seq": 0, "action": 0}


def test_cached_priority_legend_hint_follows_language_switches():
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("let LANG=")]
    script += """
let LANG='en',GE_PRIORITY_LEGEND=null;
function t(id){return I18N[LANG][id];}
const hint={innerHTML:''},tap={textContent:''},legend={innerHTML:'cached English markup',querySelector:()=>hint};
const document={getElementById:id=>id==='maplegend'?legend:tap};
const geActive=()=>false,covOn=()=>false,climateOn=()=>false,updateLegendLabels=()=>{},legendTapKey=()=> 'legendtap_explore';
"""
    script += _function(src, "updateLegend")
    script += "updateLegend();const en=hint.innerHTML;LANG='fr';updateLegend();console.log(JSON.stringify({en,fr:hint.innerHTML}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    assert "Brighter = higher priority" in result["en"]
    assert "Plus clair = priorité plus élevée" in result["fr"]
    assert "Brighter" not in result["fr"]


def test_late_geolocation_cannot_override_a_chosen_place():
    src = TEMPLATE.read_text()
    script = """
let _selectionSeq=0,success,failure;const steps=[];
const setStartLabel=label=>steps.push({label}),t=id=>id;
const map={jumpTo:o=>steps.push({jump:o})},setStart=(...args)=>steps.push({start:args});
const navigator={geolocation:{getCurrentPosition(ok,fail){success=ok;failure=fail;}}};
const setTimeout=()=>{},ipLoc=()=>{};
"""
    script += _function(src, "locateMe")
    script += "locateMe();_selectionSeq++;success({coords:{latitude:49,longitude:-123}});failure();console.log(JSON.stringify(steps));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [{"label": "locating"}]
