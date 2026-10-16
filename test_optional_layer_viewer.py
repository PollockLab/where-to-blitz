"""Exercise the shipped optional-layer helpers and actual build without public data."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import build_discovery_layer as discovery
import conservation_challenge as challenge
from test_conservation_challenge import AS_OF, CONFIG, FixtureAPI, observation
from test_discovery_layer import edit_metadata, event, sources
from test_shared_context import _run as run_shared

ROOT = Path(__file__).parent
SCRIPT = (ROOT / "webapp/optional_layers.js").read_text()


def run_js(code, data=None):
    result = subprocess.run(
        ["node", "-e", SCRIPT + "\n" + code, json.dumps(data)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rate_product(tmp_path):
    inputs = sources(tmp_path, [event("optional-unique-fixture-event")], {(400, 400): 10})
    for sidecar in inputs[2:]:
        edit_metadata(sidecar, lambda m: m["scope"]["filters"].update(taxon_group="All biodiversity"))
    return discovery.build(*inputs)


def build(tmp_path, environ=None):
    (tmp_path / "webapp").mkdir(exist_ok=True)
    for filename in ("index.html", "optional_layers.js"):
        shutil.copyfile(ROOT / "webapp" / filename, tmp_path / "webapp" / filename)
    target = tmp_path / "cluster_results/ca"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.json").write_text(
        json.dumps({"files": {"All biodiversity": "all.json", "Aves": "aves.json"}})
    )
    env = {
        k: v for k, v in os.environ.items() if k not in ("DISCOVERY_RATE_FILE", "CONSERVATION_CHALLENGE_FILE")
    }
    env.update(PYTHONPATH=str(ROOT), **(environ or {}))
    return subprocess.run(
        [sys.executable, str(ROOT / "build_webapp.py")], cwd=tmp_path, env=env, capture_output=True, text=True
    )


def descriptor(html):
    return json.loads(html.split("const OPTIONAL_LAYERS=", 1)[1].split(";", 1)[0])


def test_unconfigured_build_preserves_existing_viewer_and_advertises_no_optional_data(tmp_path):
    result = build(tmp_path)
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "index.html").read_text()
    assert descriptor(html) == {"discovery": None, "challenge": None}
    assert "__OPTIONAL_LAYER_SCRIPT__" not in html and "__OPTIONAL_LAYERS__" not in html
    assert "installOptionalLayers();" in html
    assert 'id="recentChallengePanel" hidden' in html


def test_build_pins_validated_files_without_inlining_observations_or_cells(tmp_path):
    product = rate_product(tmp_path)
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    rate, recent = tmp_path / "rate.json", tmp_path / "recent.json"
    rate.write_text(json.dumps(product))
    recent.write_text(json.dumps(snapshot))
    result = build(tmp_path, {"DISCOVERY_RATE_FILE": str(rate), "CONSERVATION_CHALLENGE_FILE": str(recent)})
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "index.html").read_text()
    desc = descriptor(html)
    assert desc["discovery"]["sha256"] == hashlib.sha256(rate.read_bytes()).hexdigest()
    assert desc["discovery"]["taxon_group"] == "All biodiversity"
    assert desc["challenge"]["sha256"] == hashlib.sha256(recent.read_bytes()).hexdigest()
    assert desc["discovery"]["path"] == "cluster_results/ca/discovery_rate.json"
    assert desc["challenge"]["path"] == "cluster_results/ca/conservation_challenge.json"
    assert "eligible_count" not in html and "optional-unique-fixture-event" not in html
    assert "observations" not in desc["challenge"] and "tiers" not in desc["discovery"]


@pytest.mark.parametrize("change", ["missing", "malformed", "implicit_group", "unknown_group"])
def test_configured_bad_or_semantically_ambiguous_discovery_input_fails_build(tmp_path, change):
    product = rate_product(tmp_path)
    if change == "malformed":
        product["events"]["unique"] = 9
    if change in ("implicit_group", "unknown_group"):
        for scope in (product["scope"], *(s["scope"] for s in product["sources"].values())):
            if change == "implicit_group":
                scope["filters"].pop("taxon_group", None)
            else:
                scope["filters"]["taxon_group"] = "unknown"
    path = tmp_path / "rate.json"
    if change != "missing":
        path.write_text(json.dumps(product))
    result = build(tmp_path, {"DISCOVERY_RATE_FILE": str(path)})
    assert result.returncode != 0
    assert not (tmp_path / "index.html").exists()


def test_incomplete_challenge_snapshot_cannot_advertise_a_public_overlay(tmp_path):
    path = tmp_path / "challenge.json"
    path.write_text(json.dumps({"schema_version": 1, "complete": False, "observations": []}))
    result = build(tmp_path, {"CONSERVATION_CHALLENGE_FILE": str(path)})
    assert result.returncode != 0 and not (tmp_path / "index.html").exists()


def test_discovery_js_never_exposes_zero_or_incomplete_support(tmp_path):
    product = rate_product(tmp_path)
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify([OptionalData.availableCells(p,5000),OptionalData.availableCells(p,25000)]));",
        product,
    )
    assert result[0] == [[400, 400, 1, 10, 1000, 1, 1]] and result[1] == []


@pytest.mark.parametrize("national", [False, True])
def test_shipped_challenge_selector_matches_python_including_caps_dates_ties_and_extent(national):
    rows = []
    for identity in range(1, 620):
        item = {
            "id": identity,
            "taxon_id": (identity % 11) + 1,
            "longitude": -75 if identity % 3 else -125,
            "latitude": 45,
            "observed_on": "2026-10-15",
            "observed_at": None,
        }
        if identity % 7:
            item["observed_at"] = "2026-10-15T12:00:00+00:00"
        rows.append(item)
    snapshot = {"schema_version": 1, "complete": True, "observations": rows}
    bounds = [-90, 40, -60, 50]
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify(OptionalData.selectChallenge(p.snapshot,p.bounds,p.national).map(r=>r.id)));",
        {"snapshot": snapshot, "bounds": bounds, "national": national},
    )
    assert result == [r["id"] for r in challenge.select_observations(snapshot, bounds, national)]
    assert len(result) == (len([r for r in rows if r["longitude"] == -75]) if national else 150)


def test_viewport_selection_happens_before_national_thinning():
    rows = [
        {
            "id": i,
            "taxon_id": 1,
            "longitude": -75,
            "latitude": 45,
            "observed_on": "2026-10-15",
            "observed_at": None,
        }
        for i in range(1, 601)
    ]
    rows.append({**rows[0], "id": 999, "taxon_id": 2, "longitude": -125, "observed_on": "2026-10-03"})
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify(OptionalData.selectChallenge(p,[-130,40,-120,50],false).map(r=>r.id)));",
        {"observations": rows},
    )
    assert result == [999]


def test_dateline_wrapping_and_whole_world_bounds():
    result = run_js(
        "console.log(JSON.stringify([OptionalData.viewport(170,40,210,60),OptionalData.viewport(-220,40,220,60)]));"
    )
    assert result == [[170, 40, -150, 60], [-180, 40, 180, 60]]


@pytest.mark.parametrize("corrupt", [False, True])
def test_lazy_loader_rejects_wrong_bytes_before_data_can_be_used(tmp_path, corrupt):
    product = rate_product(tmp_path)
    body = json.dumps(product)
    desc = {
        "schema_version": 1,
        "path": "cluster_results/ca/discovery_rate.json",
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "taxon_group": "All biodiversity",
        "scope": product["scope"],
        "metric": product["metric"],
    }
    result = run_js(
        """(async()=>{
      const p=JSON.parse(process.argv[1]),subtle=require('node:crypto').webcrypto.subtle;let calls=0;
      try{await OptionalData.fetchPinned(p.desc,'discovery',async()=>{calls++;return{ok:true,arrayBuffer:async()=>new TextEncoder().encode(p.body).buffer};},subtle,'https://example.org/map/');console.log(JSON.stringify({valid:true,calls}));}
      catch(e){console.log(JSON.stringify({valid:false,calls,message:e.message}));}
    })();""",
        {"body": body + (" " if corrupt else ""), "desc": desc},
    )
    assert result["valid"] is not corrupt and result["calls"] == 1
    if corrupt:
        assert "hash mismatch" in result["message"]


def test_pinned_loader_rejects_cross_origin_before_fetch():
    result = run_js(
        """(async()=>{let calls=0;try{await OptionalData.fetchPinned({path:'https://other.example/data'},'challenge',async()=>{calls++;}, {},'https://example.org/map/');}catch(e){console.log(JSON.stringify({calls,message:e.message}));}})();"""
    )
    assert result == {"calls": 0, "message": "Cross-origin optional data"}


def test_stale_snapshot_threshold_is_explicit():
    result = run_js(
        "console.log(JSON.stringify([OptionalData.stale('2026-10-15T00:00:00+00:00',Date.parse('2026-10-16T12:00:00Z')),OptionalData.stale('2026-10-15T00:00:00+00:00',Date.parse('2026-10-16T12:00:01Z'))]));"
    )
    assert result == [False, True]


def test_measured_rate_and_recent_overlay_round_trip_through_real_shared_restoration():
    result = run_shared(
        "?at=49.300,-123.200&g=Aves&res=5000&goal=discovery&recent=1&style=satellite",
        "let recent=false;globalThis.optionalLayers={supports:g=>g==='Aves',isDiscovery:()=>overlay==='discovery',"
        "recentEnabled:()=>recent,load:async()=>{},refresh:()=>{},discoveryCell:()=>[1],toggleRecent:async enabled=>{recent=enabled;}};"
        "await bootSeq();console.log(JSON.stringify({opened,recent,params:Object.fromEntries(location.searchParams)}));",
        real_overlay=True,
        real_cells=True,
    )
    assert result["opened"][0]["overlay"] == "discovery"
    assert result["opened"][0]["goal"] == "discovery"
    assert result["recent"] is True
    assert result["params"]["goal"] == "discovery" and result["params"]["recent"] == "1"


def test_measured_rate_shared_link_cannot_label_another_taxon_group():
    result = run_shared(
        "?at=49.300,-123.200&g=Fungi&res=25000&goal=discovery",
        "globalThis.optionalLayers={supports:g=>g==='Aves',isDiscovery:()=>false,recentEnabled:()=>false};"
        "await bootSeq();console.log(JSON.stringify({opened,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
    )
    assert result["opened"][0]["goal"] == "0"
    assert result["params"]["g"] == "Fungi" and result["params"]["goal"] == "0"


def sighting_lifecycle(action, discovery_product=None, snapshot=None, start_recent=True, cell_res=None, historical_points=None, challenge_catalog=None):
    """Replay real popup wiring, list reconciliation and synchronous map move events."""
    from test_shared_context import _function

    source = (ROOT / "webapp/index.html").read_text()
    script = (
        SCRIPT
        + source[source.index("const I18N=") : source.index("\nlet LANG=")]
        + source[source.index("const esc=") : source.index("const safeImg=")]
        + r"""
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const HISTORICAL_DISCOVERY_POINTS=input.historical||null;
class Element{
 constructor(tag,connected=false){this.tag=tag;this.connected=connected;this.children=[];this.dataset={};this._text='';this.hidden=true;this.classList={contains:()=>false};}
 getContext(){return {beginPath(){},arc(){},moveTo(){},lineTo(){},closePath(){},fill(){},stroke(){},fillText(){},getImageData(){return {width:64,height:64,data:new Uint8Array(64*64*4)};}};}
 get isConnected(){return this.connected||!!this.parentElement?.isConnected;}
 set textContent(value){this.replaceChildren();this._text=value;}
 get textContent(){return this._text;}
 append(...children){for(const child of children)this.insertBefore(child,null);}
 prepend(child){this.insertBefore(child,this.children[0]||null);}
 insertBefore(child,next){if(child.parentElement)child.remove();const index=next?this.children.indexOf(next):this.children.length;this.children.splice(index,0,child);child.parentElement=this;}
 remove(){if(!this.parentElement)return;if(this.contains(document.activeElement))document.activeElement=document.body;const items=this.parentElement.children;items.splice(items.indexOf(this),1);this.parentElement=null;}
 replaceChildren(){for(const child of [...this.children])child.remove();}
 contains(el){return el===this||this.children.some(c=>c.contains(el));}
 closest(){return null;}
 querySelector(selector){return this.children.find(el=>selector==='summary'?el.tag==='summary':selector==='b'?el.tag==='b':selector==='p'?el.tag==='p':selector==='a'?el.tag==='a':el.tag==='close')||null;}
 querySelectorAll(){return [];}
 addEventListener(){}
 setAttribute(key,value){this[key]=value;}
 getAttribute(key){return this[key]??null;}
 getClientRects(){return this.isConnected?[{}]:[];}
 focus(){if(this.isConnected&&this.tag!=='body')document.activeElement=this;}
}
const layerHandlers={},handlers={},nodes={},document={activeElement:null,body:new Element('body',true),
 createElement:tag=>new Element(tag),getElementById:id=>nodes[id]||(nodes[id]=new Element(id,true)),
 addEventListener:(event,fn)=>handlers[event]=fn};document.activeElement=document.body;
const getComputedStyle=()=>({visibility:'visible',display:'block'});
const canvas=new Element('canvas',true),language=new Element('language',true);
let curPopup=null,_reselect=false,destMarker=null,destCell=null,_mapLoaded=true,_shareAction=0,overlay='priority',LANG='en',GE_PRIORITY_LEGEND=null,_discoveryLoading=false,_geLoading=false;
const state={view:'explore',taxon:'All biodiversity'},OPTIONAL_LAYERS={discovery:input.discovery?{taxon_group:'All biodiversity',scope:input.discovery.scope,metric:input.discovery.metric}:null,challenge:{catalog:input.catalog}};
const syncSharedCellUrl=()=>{},localizeMapControls=()=>{};
const whenMap=fn=>fn(),clearSelection=()=>{},activeCellResM=()=>input.cellRes||(input.discovery?5000:25000);
const LATTICE_X0=-2150000,LATTICE_Y1=4300000,LATTICE_W=5500000,LATTICE_H=4525000;
const LAEA={fwd:()=>[-150000,2300000],inv:()=>[45,-75]},virColour=value=> 'colour('+value+')',snapToLattice=()=>({row:400,col:400});let DRAWN5=null;
function exploreCell(){const origin=document.activeElement;map.jumpTo({});originAtExplore=origin;return true;}
let originAtExplore=null;
let viewportBounds=[-90,40,-60,50];
const map={getCanvas:()=>canvas,getBounds:()=>({getWest:()=>viewportBounds[0],getEast:()=>viewportBounds[2],getSouth:()=>viewportBounds[1],getNorth:()=>viewportBounds[3]}),
 getZoom:()=>7,getSource:id=>map.sources[id],getLayer:id=>map.sources[id],sources:{},
 addImage(){},addSource(id,options){this.sources[id]={data:options.data,setData(data){this.data=data;}};},addLayer(){},setLayoutProperty(){},queryRenderedFeatures:()=>[],
 on(event,...args){handlers[event]=args.at(-1);if(args.length>1)layerHandlers[args[0]+':'+event]=args.at(-1);},jumpTo(){handlers.moveend();}};
const maplibregl={Popup:class{
 constructor(options){this.options=options;this.handlers={};this.root=new Element('popup',true);this.close=new Element('close');this.root.append(this.close);}
 on(event,fn){(this.handlers[event]||(this.handlers[event]=[])).push(fn);return this;}
 emit(event){for(const fn of this.handlers[event]||[])fn();}
 setLngLat(value){this.lngLat=value;return this;}setDOMContent(el){this.root.append(el);return this;}
 getElement(){return this.root;}addTo(){this.emit('open');return this;}
 remove(){if(this.root.contains(document.activeElement))document.activeElement=document.body;this.root.connected=false;this.emit('close');}
}};
OptionalData.fetchPinned=async(_descriptor,kind)=>kind==='discovery'?input.discovery:input.snapshot;
"""
    )
    for name in (
        "t",
        "updateLegend",
        "popupFocusOrigin",
        "focusPopupTarget",
        "restorePopupFocus",
        "onPopupOpen",
        "onPopupClose",
        "wirePopup",
        "closePopup",
    ):
        script += _function(source, name)
    script += source[
        source.index("document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;") : source.index(
            "// Raster basemaps."
        )
    ]
    script += (
        "\n(async()=>{installOptionalLayers();"
        + ("await optionalLayers.toggleRecent(true);" if start_recent else "")
        + action
        + "})().catch(e=>{console.error(e);process.exit(1);});"
    )
    if snapshot is None:
        snapshot = challenge.acquire(CONFIG, FixtureAPI([observation(1), observation(2, 20)]), AS_OF)
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"snapshot": snapshot, "discovery": discovery_product, "cellRes": cell_res,"historical":historical_points,"catalog":challenge_catalog}),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sighting_native_activation_and_escape_restore_the_connected_invoker_after_jump():
    result = sighting_lifecycle(
        "const button=nodes.recentChallengeList.children[0];button.focus();button.onclick();"
        "const opened={focused:document.activeElement===curPopup.close,sameInvoker:nodes.recentChallengeList.children[0]===button,connected:button.isConnected};"
        "handlers.keydown({key:'Escape'});console.log(JSON.stringify({opened,closed:curPopup===null,returned:document.activeElement===button}));"
    )
    assert result == {
        "opened": {"focused": True, "sameInvoker": True, "connected": True},
        "closed": True,
        "returned": True,
    }


@pytest.mark.parametrize("destination", ["summary", "toggle", "elsewhere", "hidden"])
def test_sighting_escape_restores_browser_context_after_local_cap_removes_invoker(destination):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation(i, 10) for i in range(1, 621)]), AS_OF)
    before_close = {
        "summary": "",
        "toggle": "summary.remove();",
        "elsewhere": "language.focus();",
        "hidden": "summary.getClientRects=()=>[];nodes.recentChallenge.getClientRects=()=>[];",
    }[destination]
    result = sighting_lifecycle(
        "const summary=document.createElement('summary');nodes.recentChallengeSightings.append(summary);"
        "const initial=nodes.recentChallengeList.children.length,button=nodes.recentChallengeList.children[200];"
        "button.focus();button.onclick();viewportBounds=[-76,44,-74,46];handlers.moveend();"
        "const local=nodes.recentChallengeList.children.length,removed=!button.isConnected;"
        + before_close
        + "handlers.keydown({key:'Escape'});console.log(JSON.stringify({initial,local,removed,"
        "summary:document.activeElement===summary,toggle:document.activeElement===nodes.recentChallenge,"
        "elsewhere:document.activeElement===language,map:document.activeElement===canvas}));",
        snapshot=snapshot,
    )
    assert result == {
        "initial": 500,
        "local": 150,
        "removed": True,
        "summary": destination == "summary",
        "toggle": destination == "toggle",
        "elsewhere": destination == "elsewhere",
        "map": destination == "hidden",
    }


def test_sighting_escape_resolves_a_rebuilt_invoker_by_observation_id():
    result = sighting_lifecycle(
        "const button=nodes.recentChallengeList.children[0],id=button.dataset.sightingId;button.focus();button.onclick();"
        "button.remove();handlers.moveend();const replacement=nodes.recentChallengeList.children.find(b=>b.dataset.sightingId===id);"
        "handlers.keydown({key:'Escape'});console.log(JSON.stringify({rebuilt:replacement!==button,"
        "returned:document.activeElement===replacement}));"
    )
    assert result == {"rebuilt": True, "returned": True}


def test_challenge_refresh_preserves_keyboard_focus_and_never_steals_focus_from_map_or_controls():
    result = sighting_lifecycle(
        "const button=nodes.recentChallengeList.children[0];button.focus();handlers.moveend();const preserved=document.activeElement===button;"
        "canvas.focus();handlers.moveend();const pointerStayed=document.activeElement===canvas;"
        "language.focus();LANG='fr';optionalLayers.refresh();"
        "console.log(JSON.stringify({preserved,pointerStayed,controlStayed:document.activeElement===language}));"
    )
    assert result == {"preserved": True, "pointerStayed": True, "controlStayed": True}


def test_sighting_close_does_not_steal_focus_after_a_pointer_map_action():
    result = sighting_lifecycle(
        "const button=nodes.recentChallengeList.children[0];button.focus();button.onclick();"
        "canvas.focus();curPopup.remove();console.log(JSON.stringify({closed:curPopup===null,focusStayed:document.activeElement===canvas}));"
    )
    assert result == {"closed": True, "focusStayed": True}


@pytest.mark.parametrize("newer_goal", [None, "climate"])
def test_failed_discovery_group_load_reconciles_context_and_ignores_late_overlay_paint(newer_goal):
    later = "critSel.value='climate';critSel.onchange();" if newer_goal else ""
    result = run_shared(
        "?at=49.3,-123.2&g=Aves&res=25000&goal=0",
        "await bootSeq();state.taxon='Aves';taxonSel.value='Aves';_committedTaxon='Aves';"
        "let fail,finishData;loadGroup=()=>new Promise((_resolve,reject)=>{fail=reject;});"
        "globalThis.optionalLayers={supports:g=>g==='All biodiversity',group:()=> 'All biodiversity',"
        "isDiscovery:()=>overlay==='discovery',load:()=>new Promise(resolve=>{finishData=resolve;}),refresh:()=>{},recentEnabled:()=>false};"
        "critSel.value='discovery';critSel.onchange();const groupPending=_taxonPending.promise,paintPending=_overlayReady;"
        + later
        + "fail(Error('HTTP503'));await groupPending;finishData();await paintPending;await _overlayReady;"
        "console.log(JSON.stringify({taxon:state.taxon,selected:taxonSel.value,overlay,goal:critSel.value,"
        "supported:overlay!=='discovery'||optionalLayers.supports(state.taxon),params:Object.fromEntries(location.searchParams)}));",
        real_overlay=True,
        real_cells=True,
        controls=True,
    )
    assert result["taxon"] == result["selected"] == result["params"]["g"] == "Aves"
    assert result["supported"] is True
    assert result["overlay"] == ("climate" if newer_goal else "priority")
    assert result["goal"] == result["params"]["goal"] == (newer_goal or "0")


def test_late_failed_discovery_group_request_cannot_revert_a_newer_taxon_and_goal():
    result = run_shared(
        "?at=49.3,-123.2&g=Aves&res=25000&goal=0",
        "await bootSeq();const jobs={};loadGroup=g=>new Promise((resolve,reject)=>{jobs[g]={resolve,reject};});"
        "globalThis.optionalLayers={supports:g=>g==='All biodiversity',group:()=> 'All biodiversity',"
        "isDiscovery:()=>overlay==='discovery',load:async()=>{},refresh:()=>{},recentEnabled:()=>false};"
        "critSel.value='discovery';critSel.onchange();const old=_taxonPending.promise;"
        "taxonSel.value='Fungi';taxonSel.onchange();const current=_taxonPending.promise;"
        "critSel.value='climate';critSel.onchange();jobs.Fungi.resolve();await current;"
        "jobs['All biodiversity'].reject(Error('late HTTP503'));await old;await _overlayReady;"
        "console.log(JSON.stringify({taxon:state.taxon,selected:taxonSel.value,overlay,goal:critSel.value,params:Object.fromEntries(location.searchParams)}));",
        real_overlay=True,
        real_cells=True,
        controls=True,
    )
    assert result["taxon"] == result["selected"] == result["params"]["g"] == "Fungi"
    assert result["overlay"] == result["goal"] == result["params"]["goal"] == "climate"


def test_measured_cell_buttons_keep_identity_and_capture_keyboard_invoker_before_map_jump(tmp_path):
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "const button=nodes.discoveryCells.children[0];button.focus();button.onclick();"
        "const afterActivation={same:nodes.discoveryCells.children[0]===button,origin:originAtExplore===button,focused:document.activeElement===button};"
        "canvas.focus();handlers.moveend();const mapStayed=document.activeElement===canvas;"
        "language.focus();optionalLayers.refresh();console.log(JSON.stringify({afterActivation,mapStayed,controlStayed:document.activeElement===language}));",
        rate_product(tmp_path),
    )
    assert result == {
        "afterActivation": {"same": True, "origin": True, "focused": True},
        "mapStayed": True,
        "controlStayed": True,
    }


@pytest.mark.parametrize(
    "bounds,minimum,maximum",
    [
        ([-76, 44, -74, 46], 157, 158),
        ([-80, 44, -70, 46], 785, 787),
        ([178, 40, -178, 50], 314, 315),
        ([-180, 40, 180, 60], 10000, 40000),
        ([-75, 44, -75, 46], 0, 0),
    ],
)
def test_viewport_width_uses_ground_distance_and_handles_wrapping(bounds, minimum, maximum):
    result = run_js(
        "console.log(JSON.stringify(OptionalData.viewportWidthKm(JSON.parse(process.argv[1]))));", bounds
    )
    assert minimum <= result <= maximum


def test_challenge_cap_tracks_viewport_ground_width_instead_of_zoom():
    rows = [observation(i, 10 if i % 2 else 20) for i in range(1, 621)]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    result = sighting_lifecycle(
        "viewportBounds=[-80,44,-70,46];handlers.moveend();const broad=nodes.recentChallengeList.children.length;"
        "viewportBounds=[-76,44,-74,46];handlers.moveend();const local=nodes.recentChallengeList.children.length;"
        "console.log(JSON.stringify({zoom:map.getZoom(),broad,local}));",
        snapshot=snapshot,
    )
    assert result == {"zoom": 7, "broad": 500, "local": 150}


@pytest.mark.parametrize("language", ["en", "fr"])
def test_supplied_discovery_point_scope_is_visible_in_legend_and_popup_in_both_languages(tmp_path, language):
    product = rate_product(tmp_path)
    for scope in (product["scope"], *(source["scope"] for source in product["sources"].values())):
        scope["filters"]["discovery_points"] = "lab-supplied point set; original selection unknown"
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();LANG=" + json.dumps(language) + ";"
        "console.log(JSON.stringify({legend:optionalLayers.discoveryLegend(),popup:optionalLayers.discoveryPopup(45,-75,5000)}));",
        product,
    )
    note = (
        "original selection method is unknown"
        if language == "en"
        else "méthode de sélection initiale est inconnue"
    )
    unit = "supplied discovery points" if language == "en" else "points de découverte fournis"
    for value in result.values():
        assert note in value and unit in value
        assert "2024-01-01" in value and "2024-12-31" in value


def test_unknown_discovery_scope_is_escaped_and_missing_scope_adds_no_disclosure(tmp_path):
    product = rate_product(tmp_path)
    no_note = sighting_lifecycle(
        "console.log(JSON.stringify(optionalLayers.discoveryLegend()));",
        product,
    )
    assert "Discovery point-set scope" not in no_note and "selection method" not in no_note
    for scope in (product["scope"], *(source["scope"] for source in product["sources"].values())):
        scope["filters"]["discovery_points"] = '<img src=x onerror="alert(1)">'
    result = sighting_lifecycle("console.log(JSON.stringify(optionalLayers.discoveryLegend()));", product)
    assert "&lt;img" in result and "<img" not in result


def coverage_snapshot(omissions=39):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    codes = [
        "blank_scientific_name",
        "inactive_source_taxon",
        "no_unique_exact_match",
        "incomplete_search",
        "broader_than_species",
        "rank_mismatch",
        "missing_species_ancestry",
        "future_reason_code",
    ]
    snapshot["coverage"] = {
        "source_rows": 369,
        "resolved_rows": 330,
        "unique_targets": 324,
        "omitted_rows": omissions,
        "omissions": [
            {
                "csv_row": index + 2,
                "raw_scientific_name": f'<img src=x onerror="fail()"> taxon {index}' if index else "",
                "common_name": f"Common <name> {index}",
                "reason_code": codes[index % len(codes)],
                "reason": f"Original detail <unsafe> {index}",
            }
            for index in range(omissions)
        ],
    }
    return snapshot


@pytest.mark.parametrize("language", ["en", "fr"])
def test_approved_subset_discloses_every_omitted_source_entry_as_text_in_both_languages(language):
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";optionalLayers.refresh();"
        "const list=nodes.recentChallengeOmissionList;console.log(JSON.stringify({"
        "hidden:nodes.recentChallengeCoverage.hidden,counts:nodes.recentChallengeCoverageCounts.textContent,"
        "summary:nodes.recentChallengeOmissionsSummary.textContent,open:!!nodes.recentChallengeOmissions.open,"
        "rows:list.children.map(li=>li.children.map(child=>({tag:child.tag,text:child.textContent,html:child.innerHTML??null})))"
        "}));",
        snapshot=coverage_snapshot(),
    )
    assert not result["hidden"] and not result["open"]
    assert all(str(count) in result["counts"] for count in (324, 330, 369))
    assert "39" in result["summary"] and len(result["rows"]) == 39
    assert (
        "included names" in result["counts"] if language == "en" else "noms inclus" in result["counts"]
    )
    assert result["rows"][0][0]["text"] == (
        "Scientific name missing" if language == "en" else "Nom scientifique manquant"
    )
    for index, row in enumerate(result["rows"]):
        assert len(row) == 3
        assert f"Common <name> {index}" in row[1]["text"]
        if index:
            assert row[0]["text"] == f'<img src=x onerror="fail()"> taxon {index}'
        assert all(child["html"] is None for child in row)
        if index % 8 == 7:
            assert row[2]["text"] == f"Original detail <unsafe> {index}"
        else:
            assert row[2]["text"] != f"Original detail <unsafe> {index}"
    assert (
        "no scientific name" in result["rows"][0][2]["text"]
        if language == "en"
        else "aucun nom scientifique" in result["rows"][0][2]["text"]
    )


def test_omissions_disclosure_keeps_native_open_state_and_focus_during_pan_and_translation():
    result = sighting_lifecycle(
        "const details=nodes.recentChallengeOmissions,summary=nodes.recentChallengeOmissionsSummary,"
        "first=nodes.recentChallengeOmissionList.children[0];details.open=true;summary.focus();"
        "handlers.moveend();const stable=nodes.recentChallengeOmissionList.children[0]===first;"
        "LANG='fr';optionalLayers.refresh();console.log(JSON.stringify({stable,open:details.open,"
        "focused:document.activeElement===summary,summary:summary.textContent,"
        "reason:nodes.recentChallengeOmissionList.children[0].children[2].textContent}));",
        snapshot=coverage_snapshot(),
    )
    assert result["stable"] and result["open"] and result["focused"]
    assert "entrées omises" in result["summary"] and "aucun nom scientifique" in result["reason"]


def test_older_snapshot_adds_no_coverage_warning_and_complete_subset_hides_omissions():
    older = sighting_lifecycle(
        "console.log(JSON.stringify({hidden:nodes.recentChallengeCoverage.hidden,"
        "rows:nodes.recentChallengeOmissionList?.children.length||0}));"
    )
    assert older == {"hidden": True, "rows": 0}
    complete = sighting_lifecycle(
        "console.log(JSON.stringify({coverageHidden:nodes.recentChallengeCoverage.hidden,"
        "omissionsHidden:nodes.recentChallengeOmissions.hidden}));",
        snapshot=coverage_snapshot(0),
    )
    assert complete == {"coverageHidden": False, "omissionsHidden": True}


def test_subset_coverage_is_only_exposed_after_the_pinned_snapshot_is_loaded():
    result = sighting_lifecycle(
        "const before=nodes.recentChallengeCoverage.hidden;await optionalLayers.toggleRecent(true);"
        "console.log(JSON.stringify({before,after:nodes.recentChallengeCoverage.hidden}));",
        snapshot=coverage_snapshot(),
        start_recent=False,
    )
    assert result == {"before": True, "after": False}


def test_national_discovery_renders_every_supported_coarse_cell_under_wrapped_canada_bounds(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [
        [index // 220, index % 220, 1, 10, 1000, 25, 25] for index in range(5997)
    ]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();viewportBounds=[164.362842,41,2.637158,84];"
        "optionalLayers.refresh();const canada={features:map.sources['measured-discovery'].data.features.length,"
        "buttons:nodes.discoveryCells.children.length};"
        "viewportBounds=[10,40,20,50];handlers.moveend();"
        "console.log(JSON.stringify({canada,outsideFeatures:map.sources['measured-discovery'].data.features.length,"
        "outsideButtons:nodes.discoveryCells.children.length,outsideText:nodes.discoveryCells.textContent}));",
        product,
        cell_res=25000,
    )
    assert result["canada"] == {"features": 5997, "buttons": 30}
    assert result["outsideFeatures"] == 5997 and result["outsideButtons"] == 0
    assert "No measured rate with complete observation support" in result["outsideText"]


def test_fine_discovery_keeps_bounded_rendering_and_viewport_scoped_accessible_cells(tmp_path):
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "const inside=nodes.discoveryCells.children.length;viewportBounds=[10,40,20,50];handlers.moveend();"
        "console.log(JSON.stringify({inside,features:map.sources['measured-discovery'].data.features.length,"
        "outside:nodes.discoveryCells.children.length}));",
        rate_product(tmp_path),
    )
    assert result == {"inside": 1, "features": 1, "outside": 0}


def test_discovery_log_colours_distinguish_positive_rates_and_preserve_raw_counts(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [
        [400, 400, 0, 10000, 0, 25, 25],
        [400, 401, 1, 10000, 1, 25, 25],
        [400, 402, 10, 10000, 10, 25, 25],
        [400, 403, 1, 1, 10000, 25, 25],
    ]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "console.log(JSON.stringify({features:map.sources['measured-discovery'].data.features.map(f=>f.properties),"
        "buttons:nodes.discoveryCells.children.map(b=>b.textContent),legend:optionalLayers.discoveryLegend()}));",
        product,
        cell_res=25000,
    )
    features = result["features"]
    assert features[0]["colour"] == "#94a39d" and features[0]["opacity"] < features[1]["opacity"]
    assert len({row["colour"] for row in features}) == 4
    assert [[row["discovery_count"], row["observation_count"], row["rate"]] for row in features] == [
        row[2:5] for row in product["tiers"]["25000"]["cells"]
    ]
    assert "Research-grade observations" not in result["buttons"][0]
    assert "Observations: 1." in result["buttons"][0]
    assert '<details class="optional-source-details">' in result["legend"]


def test_zero_rate_is_supported_but_zero_observation_exposure_is_not():
    product = {
        "tiers": {
            "5000": {"cells": [[1, 1, 0, 20, 0, 1, 1], [1, 2, 0, 0, None, 1, 1], [1, 3, 1, 10, 1000, 1, 25]]}
        }
    }
    assert run_js(
        "console.log(JSON.stringify(OptionalData.availableCells(JSON.parse(process.argv[1]),5000)));", product
    ) == [[1, 1, 0, 20, 0, 1, 1]]


def test_relative_freshness_uses_calendar_days_and_preserves_stale_threshold():
    assert run_js(
        "console.log(JSON.stringify(['2026-10-16T01:00:00Z','2026-10-15T23:00:00Z','2026-10-13T23:00:00Z'].map(value=>OptionalData.relativeRefresh(value,Date.parse('2026-10-16T02:00:00Z'))))); "
    ) == [["recent_updated_today"], ["recent_updated_yesterday"], ["recent_updated_days", 3]]


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://static.inaturalist.org/photos/123/medium.jpg", True),
        ("https://static.inaturalist.org/photos/123/medium.JPG", True),
        ("https://inaturalist-open-data.s3.amazonaws.com/photos/1/small.png", True),
        ("http://static.inaturalist.org/photos/123/medium.jpg", False),
        ("https://static.inaturalist.org.evil.example/photos/123/medium.jpg", False),
        ("https://static.inaturalist.org/photos/123/medium.jpg?redirect=1", False),
        ("https://user@static.inaturalist.org/photos/123/medium.jpg", False),
        ("https://static.inaturalist.org:8443/photos/123/medium.jpg", False),
        ("https://static.inaturalist.org/private/image.jpg", False),
    ],
)
def test_optional_reference_photo_urls_match_the_public_snapshot_contract(url, allowed):
    assert (
        bool(run_js("console.log(JSON.stringify(OptionalData.photoUrl(JSON.parse(process.argv[1]))));", url))
        is allowed
    )


def test_sighting_metadata_is_text_and_reference_photo_requires_public_license():
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    snapshot["observations"][0].update(
        common_name="Common <name>",
        identified_name="Exact variety",
        place_name="Public <place>",
        photo={
            "url": "https://static.inaturalist.org/photos/123/medium.jpg",
            "attribution": "Photographer <name>",
            "license_code": "cc-by",
            "alt": "Reference photo of Exact variety",
        },
    )
    result = sighting_lifecycle(
        "nodes.recentChallengeList.children[0].onclick();const body=curPopup.root.children[1];"
        "const before=body.children.slice();handlers.moveend();console.log(JSON.stringify({"
        "same:before.every((node,index)=>body.children[index]===node),"
        "children:body.children.map(child=>({tag:child.tag,text:child.textContent,src:child.src,alt:child.alt,html:child.innerHTML??null})),"
        'credit:body.children.find(child=>child.tag==="small").children.map(link=>({href:link.href,text:link.textContent})),'
        "actions:body.children.at(-1).children.map(link=>({href:link.href,text:link.textContent}))}));",
        snapshot=snapshot,
    )
    assert result["same"]
    assert result["children"][0]["text"] == "Common <name>"
    assert result["children"][1]["text"] == "Exact variety"
    image = next(child for child in result["children"] if child["tag"] == "img")
    assert image["alt"] == "Reference photo of Exact variety"
    assert all(child["html"] is None for child in result["children"])
    assert result["credit"][0] == {
        "href": "https://www.inaturalist.org/photos/123",
        "text": "Photographer <name>",
    }
    assert result["credit"][2] == {"href": "https://creativecommons.org/licenses/by/4.0/", "text": "CC-BY"}
    assert result["actions"][0]["href"].startswith("https://www.inaturalist.org/observations/")
    assert result["actions"][1]["href"] == "https://www.inaturalist.org/observations/new"


def test_recording_action_keeps_focus_when_sighting_translates():
    result = sighting_lifecycle(
        "nodes.recentChallengeList.children[0].onclick();const body=curPopup.root.children[1];"
        "body.children.at(-1).children[1].focus();LANG='fr';optionalLayers.refresh();"
        "console.log(JSON.stringify({focused:document.activeElement===body.children.at(-1).children[1],text:document.activeElement.textContent}));"
    )
    assert result["focused"] and result["text"] == "Noter une observation"


def test_recent_status_discloses_all_matching_rows_and_cap_before_thinning():
    rows = [observation(i, 10 if i % 2 else 20) for i in range(1, 621)]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    result = sighting_lifecycle(
        "const calls=[],translate=t;t=(key,...args)=>{calls.push([key,...args]);return translate(key,...args)};"
        "viewportBounds=[-76,44,-74,46];optionalLayers.refresh();console.log(JSON.stringify({"
        "counts:calls.find(row=>row[0]==='recent_challenge_shown'),allGroups:calls.some(row=>row[0]==='recent_challenge_all_groups'),"
        "exact:nodes.recentChallengeStatus.title}));",
        snapshot=snapshot,
    )
    assert result["counts"] == ["recent_challenge_shown", "150", "620", 150]
    assert result["allGroups"]
    assert snapshot["as_of"] in result["exact"] and snapshot["window"]["start"] in result["exact"]


def test_user_known_place_context_is_labeled_nearby_and_never_replaces_distant_coordinates(tmp_path):
    result = sighting_lifecycle(
        "const calls=[],translate=t;t=(key,...args)=>{calls.push([key,...args]);return translate(key,...args)};"
        "overlay='discovery';await optionalLayers.load();optionalLayers.setPlaceContext({lat:45,lon:-75,name:'Known place'});"
        "const near=calls.filter(row=>row[0]==='discovery_near_place');calls.length=0;"
        "optionalLayers.setPlaceContext({lat:50,lon:-90,name:'Distant place'});console.log(JSON.stringify({near,"
        "distant:calls.some(row=>row[0]==='discovery_near_place'),text:nodes.discoveryCells.children[0].textContent}));",
        rate_product(tmp_path),
    )
    assert result["near"] == [["discovery_near_place", "Known place"]]
    assert not result["distant"] and result["text"].startswith("45.00, -75.00")


@pytest.mark.parametrize("action", ["photo", "license"])
def test_reference_credit_links_keep_focus_during_translation(action):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    snapshot["observations"][0]["photo"] = {
        "url": "https://static.inaturalist.org/photos/123/medium.jpg",
        "attribution": "Photographer",
        "license_code": "cc-by",
        "alt": "Reference photo",
    }
    result = sighting_lifecycle(
        "nodes.recentChallengeList.children[0].onclick();const body=curPopup.root.children[1];"
        "const credit=body.children.find(child=>child.tag==='small');"
        + "credit.children.find(link=>link.dataset.sightingAction==="
        + json.dumps(action)
        + ").focus();"
        "LANG='fr';optionalLayers.refresh();console.log(JSON.stringify({focused:document.activeElement.dataset.sightingAction,"
        "connected:document.activeElement.isConnected}));",
        snapshot=snapshot,
    )
    assert result == {"focused": action, "connected": True}


@pytest.mark.parametrize(
    "license_code,expected",
    [
        ("cc0", "https://creativecommons.org/publicdomain/zero/1.0/"),
        ("cc-by-nc-sa", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
        ("all-rights-reserved", ""),
        ("unknown", ""),
    ],
)
def test_photo_license_links_follow_inaturalist_canonical_license_mapping(license_code, expected):
    assert (
        run_js(
            "console.log(JSON.stringify(OptionalData.photoLicense(JSON.parse(process.argv[1]))));",
            license_code,
        )
        == expected
    )


@pytest.mark.parametrize("cell_res", [5000, 25000])
@pytest.mark.parametrize("language", ["en", "fr"])
def test_discovery_zero_is_visible_and_never_confused_with_unavailable(tmp_path, cell_res, language):
    product = rate_product(tmp_path)
    support = 1 if cell_res == 5000 else 25
    rows = [
        [400, 400, 0, 240, 0, support, support],
        [400, 401, 3, 270, 10000 / 90, support, support],
        [400, 402, 0, 0, None, support, support],
        [400, 403, 1, 10, None, 1, 25],
    ]
    product["tiers"][str(cell_res)]["cells"] = rows
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();LANG=" + json.dumps(language) + ";"
        "optionalLayers.refresh();console.log(JSON.stringify({"
        "features:map.sources['measured-discovery'].data.features.map(f=>f.properties),"
        "legend:optionalLayers.discoveryLegend(),popup:optionalLayers.discoveryPopup(45,-75,"
        + str(cell_res) + ")}));",
        product,
        cell_res=cell_res,
    )
    zero, positive = result["features"]
    assert zero["opacity"] >= 0.6
    assert zero["outline"] != positive["outline"]
    assert zero["colour"] != positive["colour"]
    assert [[r["discovery_count"], r["observation_count"], r["rate"]] for r in result["features"]] == [
        row[2:5] for row in rows[:2]
    ]
    assert "optional-zero" in result["legend"] and "optional-missing" in result["legend"]
    visible_legend = result["legend"].split('<details class="optional-source-details">')[0]
    if language == "en":
        assert "blank" in visible_legend and "Sparse historical" in visible_legend
        assert "separate" in visible_legend and "Orange dots" in visible_legend
        assert "not evidence that no undiscovered species remain" in result["popup"]
        assert "Observations: 240." in result["popup"]
    else:
        assert "vides" in visible_legend and "historiques clairsemés" in visible_legend
        assert "distincts" in visible_legend and "points orange" in visible_legend
        assert "ne signifie pas qu’il ne reste aucune espèce à découvrir" in result["popup"]
        assert "240 observations" in result["popup"]


@pytest.mark.parametrize("language", ["en", "fr"])
def test_recent_toggle_immediately_updates_discovery_legend(tmp_path, language):
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();LANG=" + json.dumps(language) + ";"
        "updateLegend();const before=nodes.maplegend.innerHTML;"
        "await optionalLayers.toggleRecent(true);const enabled=nodes.maplegend.innerHTML;"
        "await optionalLayers.toggleRecent(false);const disabled=nodes.maplegend.innerHTML;"
        "console.log(JSON.stringify({before,enabled,disabled}));",
        rate_product(tmp_path),
        start_recent=False,
    )
    note = "Orange dots" if language == "en" else "points orange"
    assert note not in result["before"]
    assert note in result["enabled"]
    assert note not in result["disabled"]


def test_discovery_tier_change_refreshes_legend_without_repainting_on_pan(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [[80, 80, 1, 100, 100, 25, 25]]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();"
        "const legend=document.getElementById('maplegend');let paints=0,html='';"
        "Object.defineProperty(legend,'innerHTML',{get:()=>html,set:value=>{html=value;paints++;}});"
        "optionalLayers.refresh();const fine=html;const initialPaints=paints;"
        "handlers.moveend();const panPaints=paints;input.cellRes=25000;handlers.moveend();"
        "const coarse=html;const switchedPaints=paints;handlers.moveend();"
        "console.log(JSON.stringify({fine,coarse,initialPaints,panPaints,switchedPaints,finalPaints:paints}));",
        product,
        start_recent=False,
        cell_res=5000,
    )
    assert '<span>1,000</span>' in result["fine"]
    assert '<span>100</span>' in result["coarse"]
    assert result["initialPaints"] == result["panPaints"] == 1
    assert result["switchedPaints"] == result["finalPaints"] == 2


@pytest.mark.parametrize("language", ["en", "fr"])
def test_discovery_browser_discloses_scope_and_reaches_zero_cells_on_bounded_pages(tmp_path, language):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [
        [400, 400 + i, i + 1 if i < 35 else 0, 100, 100 * (i + 1) if i < 35 else 0, 25, 25]
        for i in range(70)
    ]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();LANG=" + json.dumps(language) + ";"
        "optionalLayers.refresh();const status=()=>nodes.discoveryCellStatus.textContent;"
        "const first={count:nodes.discoveryCells.children.length,status:status(),previous:nodes.discoveryPrevious.getAttribute('aria-disabled')==='true',keys:nodes.discoveryCells.children.map(b=>b.dataset.discoveryCell)};"
        "nodes.discoveryNext.focus();nodes.discoveryNext.onclick();"
        "const second={count:nodes.discoveryCells.children.length,status:status(),focused:document.activeElement===nodes.discoveryNext};"
        "nodes.discoveryCellFilter.value='positive';nodes.discoveryCellFilter.onchange();"
        "const positive={count:nodes.discoveryCells.children.length,status:status()};"
        "nodes.discoveryCellFilter.value='zero';nodes.discoveryCellFilter.focus();nodes.discoveryCellFilter.onchange();"
        "const zero={count:nodes.discoveryCells.children.length,status:status(),focused:document.activeElement===nodes.discoveryCellFilter,"
        "keys:nodes.discoveryCells.children.map(b=>b.dataset.discoveryCell),previous:nodes.discoveryPrevious.getAttribute('aria-disabled')==='true'};"
        "nodes.discoveryNext.onclick();const last={count:nodes.discoveryCells.children.length,status:status(),next:nodes.discoveryNext.getAttribute('aria-disabled')==='true'};"
        "canvas.focus();handlers.moveend();const pan={count:nodes.discoveryCells.children.length,focused:document.activeElement===canvas};"
        "const button=nodes.discoveryCells.children[0];button.focus();button.onclick();"
        "console.log(JSON.stringify({first,second,positive,zero,last,pan,activation:{same:nodes.discoveryCells.children[0]===button,origin:originAtExplore===button}}));",
        product,
        cell_res=25000,
    )
    assert result["first"]["count"] == result["second"]["count"] == result["zero"]["count"] == 30
    assert result["first"]["previous"] and result["zero"]["previous"]
    assert result["second"]["focused"] and result["zero"]["focused"]
    assert result["last"]["count"] == 5 and result["last"]["next"]
    assert result["positive"]["count"] == 30 and "35" in result["positive"]["status"]
    assert result["first"]["keys"][0] == "25000:400,434"
    assert result["pan"] == {"count": 5, "focused": True}
    assert result["activation"] == {"same": True, "origin": True}
    assert all(int(key.split(",")[1]) >= 435 for key in result["zero"]["keys"])
    for page in ("first", "second", "zero", "last"):
        assert "70" in result[page]["status"]
    assert "35" in result["zero"]["status"] and "35" in result["last"]["status"]
    assert ("this view" if language == "en" else "cette vue") in result["first"]["status"]


def test_discovery_browser_resets_pages_for_changed_view_tier_and_empty_filter(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [
        [400, 400 + i, 1, 100, 100, 25, 25] for i in range(35)
    ]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();nodes.discoveryNext.onclick();"
        "viewportBounds=[-89,40,-60,50];handlers.moveend();const moved=nodes.discoveryCells.children.length;"
        "nodes.discoveryNext.onclick();input.cellRes=5000;handlers.moveend();const tier=nodes.discoveryCells.children.length;"
        "nodes.discoveryCellFilter.value='zero';nodes.discoveryCellFilter.onchange();"
        "console.log(JSON.stringify({moved,tier,empty:nodes.discoveryCells.textContent,count:nodes.discoveryCells.children.length,"
        "previous:nodes.discoveryPrevious.getAttribute('aria-disabled')==='true',next:nodes.discoveryNext.getAttribute('aria-disabled')==='true',status:nodes.discoveryCellStatus.textContent}));",
        product,
        cell_res=25000,
    )
    assert result["moved"] == 30 and result["tier"] == 1
    assert result["count"] == 0 and result["previous"] and result["next"]
    assert "No cells match this filter" in result["empty"]
    assert "1 measured" in result["status"]


def test_discovery_browser_page_boundaries_keep_focus_and_removed_cell_has_a_focus_fallback(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [[400, 400 + i, 1, 100, 100, 25, 25] for i in range(31)]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "nodes.discoveryPrevious.focus();nodes.discoveryPrevious.onclick();const first=nodes.discoveryCells.children.length;"
        "nodes.discoveryNext.focus();nodes.discoveryNext.onclick();nodes.discoveryNext.onclick();"
        "const last={count:nodes.discoveryCells.children.length,focused:document.activeElement===nodes.discoveryNext};"
        "nodes.discoveryCells.children[0].focus();viewportBounds=[10,40,20,50];handlers.moveend();"
        "console.log(JSON.stringify({first,last,fallback:document.activeElement===nodes.discoveryCellFilter}));",
        product,
        cell_res=25000,
    )
    assert result == {"first": 30, "last": {"count": 1, "focused": True}, "fallback": True}


def test_discovery_page_two_popup_escape_returns_to_browser_after_map_jump_removes_invoker(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [[400, 400 + i, 1, 100, 100, 25, 25] for i in range(35)]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();nodes.discoveryNext.onclick();"
        "exploreCell=()=>{const p=wirePopup(new maplibregl.Popup({focusAfterOpen:false}).setDOMContent(document.createElement('div')));"
        "viewportBounds=[-89,40,-60,50];map.jumpTo({});p.addTo(map);return true;};"
        "const invoker=nodes.discoveryCells.children[0];invoker.focus();invoker.onclick();"
        "const opened={removed:!invoker.isConnected,focused:document.activeElement===curPopup.close};"
        "handlers.keydown({key:'Escape'});console.log(JSON.stringify({opened,closed:curPopup===null,"
        "returnedToFilter:document.activeElement===nodes.discoveryCellFilter}));",
        product,
        cell_res=25000,
    )
    assert result == {
        "opened": {"removed": True, "focused": True},
        "closed": True,
        "returnedToFilter": True,
    }


def test_discovery_browser_explicit_navigation_resets_scroll_but_pan_preserves_it(tmp_path):
    product = rate_product(tmp_path)
    product["tiers"]["25000"]["cells"] = [[400, 400 + i, 1, 100, 100, 25, 25] for i in range(35)]
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();nodes.discoveryCells.scrollTop=200;"
        "nodes.discoveryNext.onclick();const next=nodes.discoveryCells.scrollTop;nodes.discoveryCells.scrollTop=100;"
        "handlers.moveend();const pan=nodes.discoveryCells.scrollTop;nodes.discoveryPrevious.onclick();"
        "const previous=nodes.discoveryCells.scrollTop;nodes.discoveryCells.scrollTop=200;"
        "nodes.discoveryCellFilter.value='positive';nodes.discoveryCellFilter.onchange();"
        "console.log(JSON.stringify({next,pan,previous,filter:nodes.discoveryCells.scrollTop}));",
        product,
        cell_res=25000,
    )
    assert result == {"next": 0, "pan": 100, "previous": 0, "filter": 0}


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("cell_res", [5000, 25000])
def test_discovery_browser_waits_for_verified_data_before_reporting_counts(tmp_path, language, cell_res):
    product = rate_product(tmp_path)
    support = 1 if cell_res == 5000 else 25
    product["tiers"][str(cell_res)]["cells"] = [
        [400, 400 + i, 0, 100, 0, support, support] for i in range(35)
    ] + [[400, 435, 1, 100, 100, support, support]]
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";let finish;"
        "OptionalData.fetchPinned=()=>new Promise(resolve=>{finish=()=>resolve(input.discovery);});"
        "['discoveryCellStatus','discoveryCells','discoveryPrevious','discoveryNext'].forEach(id=>document.getElementById(id));"
        "LAEA.fwd=(lat,lon)=>[LATTICE_X0+(lon<-75?399:437)*input.cellRes,LATTICE_Y1-400*input.cellRes];"
        "overlay='discovery';_discoveryLoading=true;const pending=optionalLayers.load();optionalLayers.refresh();"
        "const loading=()=>({disabled:nodes.discoveryCellFilter.disabled,status:nodes.discoveryCellStatus.textContent,"
        "list:nodes.discoveryCells.textContent,previous:nodes.discoveryPrevious.getAttribute('aria-disabled'),"
        "next:nodes.discoveryNext.getAttribute('aria-disabled')});const initial=loading();"
        "nodes.discoveryCellFilter.value='zero';nodes.discoveryCellFilter.onchange();nodes.discoveryNext.onclick();"
        "handlers.moveend();const changed=loading();finish();await pending;_discoveryLoading=false;optionalLayers.refresh();"
        "console.log(JSON.stringify({initial,changed,ready:{disabled:nodes.discoveryCellFilter.disabled,"
        "filter:nodes.discoveryCellFilter.value,status:nodes.discoveryCellStatus.textContent,"
        "count:nodes.discoveryCells.children.length,previous:nodes.discoveryPrevious.getAttribute('aria-disabled'),"
        "next:nodes.discoveryNext.getAttribute('aria-disabled'),keys:nodes.discoveryCells.children.map(b=>b.dataset.discoveryCell)}}));",
        product,
        start_recent=False,
        cell_res=cell_res,
    )
    message = (
        "Loading historical discovery evidence…"
        if language == "en"
        else "Chargement des données historiques de découverte…"
    )
    for phase in ("initial", "changed"):
        assert result[phase] == {
            "disabled": True,
            "status": message,
            "list": "",
            "previous": "true",
            "next": "true",
        }
    ready = result["ready"]
    assert not ready["disabled"] and ready["filter"] == "zero"
    assert ready["count"] == 30 and ready["previous"] == "true" and ready["next"] == "false"
    assert "35" in ready["status"] and "36" in ready["status"]
    assert ready["keys"][0] == f"{cell_res}:400,400"


def historical_fixture():
    return {"schema_version": 1, "discovery_sha256": "fixture", "points": [
        {"event_id": str(i), "scientific_name": "<supplied name>" if i == 0 else "Name " + str(i),
         "observed_year": 2025, "longitude": -75 if i < 27 else -75 + i / 100, "latitude": 45}
        for i in range(62)
    ]}


def test_historical_shared_coordinates_preserve_all_names():
    fixture = historical_fixture()
    groups = run_js("console.log(JSON.stringify(OptionalData.historicalGroups(JSON.parse(process.argv[1]).points)));", fixture)
    assert len(groups) == 36 and len(groups[0]["points"]) == 27
    assert sum(len(group["points"]) for group in groups) == 62
    assert groups[0]["points"][0]["scientific_name"] == "<supplied name>"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_historical_points_are_local_grouped_inspectable_and_keyboard_accessible(tmp_path, language):
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "const initial={features:map.sources['historical-discovery'].data.features,count:nodes.historicalPointsList.children.length,status:nodes.historicalPointsStatus.textContent};"
        "const button=nodes.historicalPointsList.children[0];button.focus();button.onclick();"
        "const body=curPopup.root.children[1];const opened={names:body.children[2].children.map(el=>el.textContent),detail:body.children[3].innerHTML,focused:document.activeElement===curPopup.close};"
        "handlers.keydown({key:'Escape'});const returned=document.activeElement===button;"
        "nodes.historicalPointsNext.onclick();const last=nodes.historicalPointsList.children.length;"
        "input.cellRes=25000;handlers.moveend();const coarse=nodes.historicalPointsList.children.length;"
        "input.cellRes=5000;handlers.moveend();nodes.historicalPointsToggle.onchange({target:{checked:false}});"
        "console.log(JSON.stringify({initial,opened,returned,last,coarse,hidden:nodes.historicalPointsList.children.length}));",
        rate_product(tmp_path), cell_res=5000, historical_points=historical_fixture(),
    )
    assert len(result["initial"]["features"]) == 36 and result["initial"]["features"][0]["properties"]["count"] == 27
    assert result["initial"]["count"] == 30 and "36" in result["initial"]["status"]
    assert len(result["opened"]["names"]) == 27 and result["opened"]["names"][0] == "<supplied name> · 2025"
    assert result["opened"]["focused"] and result["returned"]
    assert ("Observations: 10." if language == "en" else "10 observations") in result["opened"]["detail"]
    assert result["last"] == 6 and result["coarse"] == result["hidden"] == 0


def test_historical_markers_prevent_cell_clickthrough_and_defer_to_recent(tmp_path):
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();let explored=0;exploreCell=()=>{explored++;};"
        "map.queryRenderedFeatures=(_point,options)=>options.layers[0]==='historical-discovery'?[{}]:[];"
        "layerHandlers['measured-discovery:click']({point:{},lngLat:{lat:45,lng:-75}});"
        "layerHandlers['historical-discovery:click']({point:{},features:[{properties:{key:'-75,45'}}]});"
        "const opened=!!curPopup;handlers.keydown({key:'Escape'});"
        "map.queryRenderedFeatures=()=>[{}];layerHandlers['historical-discovery:click']({point:{},features:[{properties:{key:'-75,45'}}]});"
        "console.log(JSON.stringify({explored,opened,recentPriority:curPopup===null}));",
        rate_product(tmp_path), cell_res=5000, historical_points=historical_fixture(),
    )
    assert result == {"explored": 0, "opened": True, "recentPriority": True}


def test_historical_popup_relabels_and_closes_on_tier_exit(tmp_path):
    result = sighting_lifecycle(
        "overlay='discovery';await optionalLayers.load();optionalLayers.refresh();"
        "nodes.historicalPointsList.children[0].focus();nodes.historicalPointsList.children[0].onclick();"
        "LANG='fr';optionalLayers.refresh();const title=curPopup.root.children[1].children[0].textContent;"
        "input.cellRes=25000;handlers.moveend();console.log(JSON.stringify({title,closed:curPopup===null,"
        "focused:document.activeElement===nodes.historicalPointsToggle}));",
        rate_product(tmp_path), cell_res=5000, historical_points=historical_fixture(),
    )
    assert result == {"title": "27 points fournis à cet emplacement", "closed": True, "focused": True}


def test_unconfigured_historical_points_add_no_map_source_or_visible_panel():
    result = sighting_lifecycle(
        "optionalLayers.refresh();console.log(JSON.stringify({hidden:nodes.historicalPointsPanel.hidden,"
        "source:!!map.sources['historical-discovery']}));", start_recent=False,
    )
    assert result == {"hidden": True, "source": False}


@pytest.mark.parametrize("language", ["en", "fr"])
def test_recent_disclosures_only_open_after_verified_active_data_is_ready(language):
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";"
        "const details=document.getElementById('recentChallengeDetails'),sightings=document.getElementById('recentChallengeSightings');"
        "const read=()=>({metadataHidden:details.hidden,listHidden:sightings.hidden,metadata:nodes.recentChallengeMeta?.textContent||'',status:nodes.recentChallengeStatus.textContent});"
        "const initial=read();let finish;OptionalData.fetchPinned=()=>new Promise(resolve=>finish=resolve);"
        "const request=optionalLayers.toggleRecent(true);const pending=read();finish(input.snapshot);await request;const ready=read();"
        "await optionalLayers.toggleRecent(false);const off=read();await optionalLayers.toggleRecent(true);const cached=read();"
        "viewportBounds=[0,0,1,1];handlers.moveend();const empty={...read(),message:nodes.recentChallengeList.textContent};"
        "_mapLoaded=false;optionalLayers.refresh();const noMap=read();_mapLoaded=true;optionalLayers.refresh();const restored=read();"
        "console.log(JSON.stringify({initial,pending,ready,off,cached,empty,noMap,restored}));",
        start_recent=False,
    )
    for name in ("initial", "pending", "off", "noMap"):
        assert result[name]["metadataHidden"] and result[name]["listHidden"], name
    for name in ("ready", "cached", "empty", "restored"):
        assert not result[name]["metadataHidden"] and not result[name]["listHidden"], name
        assert AS_OF.isoformat() in result[name]["metadata"]
    assert result["empty"]["message"]
    assert "No eligible" in result["empty"]["message"] if language == "en" else "Aucune" in result["empty"]["message"]


def test_failed_recent_download_keeps_empty_disclosures_hidden():
    result = sighting_lifecycle(
        "const details=document.getElementById('recentChallengeDetails'),sightings=document.getElementById('recentChallengeSightings');"
        "OptionalData.fetchPinned=async()=>{throw Error('failed download');};await optionalLayers.toggleRecent(true);"
        "console.log(JSON.stringify({metadataHidden:details.hidden,listHidden:sightings.hidden,status:nodes.recentChallengeStatus.textContent}));",
        start_recent=False,
    )
    assert result["metadataHidden"] and result["listHidden"]
    assert "could not be verified" in result["status"]


def test_recent_markers_count_only_selected_reports_at_exact_reported_positions():
    rows = [observation(i, 10 if i % 2 else 20) for i in range(1, 621)]
    rows[-1]["geojson"]["coordinates"] = [-75.000001, 45]
    snapshot = challenge.acquire(CONFIG, FixtureAPI(rows), AS_OF)
    result = sighting_lifecycle(
        "const national=map.sources['recent-challenge'].data.features;"
        "viewportBounds=[-76,44,-74,46];handlers.moveend();const local=map.sources['recent-challenge'].data.features;"
        "viewportBounds=[0,0,1,1];handlers.moveend();"
        "const empty={features:map.sources['recent-challenge'].data.features,list:nodes.recentChallengeList.children.length};"
        "viewportBounds=[-75.0000011,44.99999,-75.0000009,45.00001];handlers.moveend();"
        "const feature=map.sources['recent-challenge'].data.features[0];layerHandlers['recent-challenge:click']({features:[feature]});"
        "const body=curPopup.root.children[1];console.log(JSON.stringify({national,local,empty,singleton:{"
        "count:feature.properties.count,link:body.children.at(-1).children[0].href,"
        "chooser:body.children.some(el=>el.className==='optional-pagination')}}));",
        snapshot=snapshot,
    )
    for name, count in (("national", 500), ("local", 150)):
        features = result[name]
        assert len(features) == 2
        assert sum(feature["properties"]["count"] for feature in features) == count
        assert sorted(feature["geometry"]["coordinates"] for feature in features) == [
            [-75.000001, 45],
            [-75, 45],
        ]
        assert sorted(feature["properties"]["count"] for feature in features) == [1, count - 1]
    assert result["empty"] == {"features": [], "list": 0}
    assert result["singleton"] == {
        "count": 1,
        "link": "https://www.inaturalist.org/observations/620",
        "chooser": False,
    }


@pytest.mark.parametrize("language", ["en", "fr"])
def test_counted_recent_popup_reaches_every_selected_report_with_bounded_native_navigation(language):
    snapshot = challenge.acquire(
        CONFIG, FixtureAPI([observation(i, 10 if i % 2 else 20) for i in range(1, 501)]), AS_OF
    )
    result = sighting_lifecycle(
        "LANG=" + json.dumps(language) + ";optionalLayers.refresh();"
        "const feature=map.sources['recent-challenge'].data.features[0];"
        "layerHandlers['recent-challenge:click']({features:[feature]});"
        "const body=curPopup.root.children[1],all=el=>[el,...el.children.flatMap(all)],"
        "action=key=>all(body).find(el=>el.dataset.sightingAction===key);"
        "const ids=[],sizes=[];for(let i=0;i<500;i++){ids.push(Number(action('0').href.split('/').at(-1)));"
        "sizes.push(all(body).length);action('next').focus();action('next').onclick();}"
        "const boundary=action('next').getAttribute('aria-disabled');"
        "LANG=LANG==='en'?'fr':'en';optionalLayers.refresh();"
        "const translated={focused:document.activeElement===action('next'),label:action('next').textContent};"
        "await optionalLayers.toggleRecent(false);console.log(JSON.stringify({count:feature.properties.count,ids,"
        "maxNodes:Math.max(...sizes),boundary,translated,closed:curPopup===null,list:nodes.recentChallengeList.children.length}));",
        snapshot=snapshot,
    )
    assert result["count"] == 500 and set(result["ids"]) == set(range(1, 501))
    assert result["maxNodes"] < 20 and result["boundary"] == "true"
    assert result["translated"] == {
        "focused": True,
        "label": "Observation suivante" if language == "en" else "Next sighting",
    }
    assert result["closed"] and result["list"] == 0


def test_counted_recent_popup_keeps_each_reports_date_photo_and_link():
    snapshot = challenge.acquire(
        CONFIG, FixtureAPI([observation(1, 10, observed_on="2026-10-14"), observation(2, 20)]), AS_OF
    )
    for row in snapshot["observations"]:
        row.update(
            common_name=f"Common {row['id']}",
            identified_name=f"Scientific {row['id']}",
            photo={
                "url": f"https://static.inaturalist.org/photos/{row['id']}/medium.jpg",
                "attribution": f"Photographer {row['id']}",
                "license_code": "cc-by",
                "alt": f"Reference {row['id']}",
            },
        )
    result = sighting_lifecycle(
        "const feature=map.sources['recent-challenge'].data.features[0];"
        "layerHandlers['recent-challenge:click']({features:[feature]});const coordinates=curPopup.lngLat,body=curPopup.root.children[1];"
        "const all=el=>[el,...el.children.flatMap(all)],action=key=>all(body).find(el=>el.dataset.sightingAction===key);"
        "const read=()=>({name:body.children.find(el=>el.tag==='b').textContent,"
        "date:body.children.find(el=>el.textContent.startsWith('Observed:')).textContent,"
        "photo:body.children.find(el=>el.tag==='img').src,link:action('0').href});const first=read();"
        "action('next').focus();action('next').onclick();const second=read();"
        "action('previous').focus();action('previous').onclick();const returned=read();"
        "console.log(JSON.stringify({coordinates,first,second,returned,focused:document.activeElement===action('previous')}));",
        snapshot=snapshot,
    )
    assert result["coordinates"] == [-75, 45] and result["focused"]
    assert result["returned"] == result["first"]
    for name, identity, date in (("first", 2, "2026-10-15"), ("second", 1, "2026-10-14")):
        assert result[name] == {
            "name": f"Common {identity}",
            "date": f"Observed: {date}",
            "photo": f"https://static.inaturalist.org/photos/{identity}/medium.jpg",
            "link": f"https://www.inaturalist.org/observations/{identity}",
        }


def test_counted_recent_popup_tracks_selected_rows_on_pan_and_hides_without_more_requests():
    snapshot = challenge.acquire(
        CONFIG, FixtureAPI([observation(i, 10 if i % 2 else 20) for i in range(1, 621)]), AS_OF
    )
    result = sighting_lifecycle(
        "OptionalData.fetchPinned=()=>{throw Error('unexpected request');};"
        "const feature=map.sources['recent-challenge'].data.features[0];layerHandlers['recent-challenge:click']({features:[feature]});"
        "const body=curPopup.root.children[1],all=el=>[el,...el.children.flatMap(all)],"
        "action=key=>all(body).find(el=>el.dataset.sightingAction===key);action('next').focus();"
        "viewportBounds=[-76,44,-74,46];handlers.moveend();input.cellRes=5000;handlers.moveend();"
        "const local={count:map.sources['recent-challenge'].data.features[0].properties.count,"
        "progress:body.children[0].textContent,list:nodes.recentChallengeList.children.length,"
        "focused:document.activeElement===action('next'),status:nodes.recentChallengeStatus.textContent};"
        "viewportBounds=[0,0,1,1];handlers.moveend();const emptyClosed=curPopup===null;"
        "viewportBounds=[-76,44,-74,46];handlers.moveend();nodes.recentChallengeList.children[0].onclick();"
        "const singleton=curPopup.root.children[1].children.every(el=>el.className!=='optional-pagination');"
        "const visibility=[];map.setLayoutProperty=(...args)=>visibility.push(args);await optionalLayers.toggleRecent(false);"
        "console.log(JSON.stringify({local,emptyClosed,singleton,closed:curPopup===null,visibility,hit:optionalLayers.hitRecent({})}));",
        snapshot=snapshot,
        cell_res=25000,
    )
    assert result["local"]["count"] == result["local"]["list"] == 150
    assert "1 of 150" in result["local"]["progress"] and result["local"]["focused"]
    assert "Reported locations: 1" in result["local"]["status"]
    assert result["emptyClosed"] and result["singleton"] and result["closed"] and not result["hit"]
    assert ["recent-challenge", "visibility", "none"] in result["visibility"]


@pytest.mark.parametrize("focus", ["chooser", "elsewhere"])
def test_counted_popup_shrinking_to_one_selected_report_keeps_focus_in_context(focus):
    rows = [observation(1), observation(151)]
    rows.extend(
        observation(i, geojson={"type": "Point", "coordinates": [-75.01 + i * 0.00001, 45]})
        for i in range(2, 151)
    )
    snapshot = challenge.acquire(CONFIG, FixtureAPI(sorted(rows, key=lambda row: row["id"])), AS_OF)
    result = sighting_lifecycle(
        "const feature=map.sources['recent-challenge'].data.features.find(f=>f.properties.count===2);"
        "layerHandlers['recent-challenge:click']({features:[feature]});const body=curPopup.root.children[1],"
        "all=el=>[el,...el.children.flatMap(all)],action=key=>all(body).find(el=>el.dataset.sightingAction===key);"
        "action('next').focus();action('next').onclick();const oldLink=action('0').href;"
        + ("language.focus();" if focus == "elsewhere" else "")
        + "viewportBounds=[-76,44,-74,46];handlers.moveend();console.log(JSON.stringify({oldLink,link:action('0').href,"
        "viewFocused:document.activeElement===action('0'),elsewhere:document.activeElement===language,"
        "count:map.sources['recent-challenge'].data.features.find(f=>f.properties.key==='-75,45').properties.count,"
        "chooser:!!action('next')}));",
        snapshot=snapshot,
    )
    assert result == {
        "oldLink": "https://www.inaturalist.org/observations/1",
        "link": "https://www.inaturalist.org/observations/151",
        "viewFocused": focus == "chooser",
        "elsewhere": focus == "elsewhere",
        "count": 1,
        "chooser": False,
    }


def test_historical_disclosure_only_opens_for_ready_visible_local_points(tmp_path):
    result = sighting_lifecycle(
        "const details=document.getElementById('historicalPointsDetails');overlay='discovery';optionalLayers.refresh();"
        "const pending=details.hidden;await optionalLayers.load();optionalLayers.refresh();const ready=details.hidden;"
        "input.cellRes=25000;handlers.moveend();const coarse=details.hidden;input.cellRes=5000;handlers.moveend();"
        "nodes.historicalPointsToggle.onchange({target:{checked:false}});const off=details.hidden;"
        "nodes.historicalPointsToggle.onchange({target:{checked:true}});const enabled=details.hidden;"
        "viewportBounds=[0,0,1,1];handlers.moveend();const empty={hidden:details.hidden,message:nodes.historicalPointsList.textContent};"
        "overlay='priority';optionalLayers.refresh();const otherMode=details.hidden;"
        "console.log(JSON.stringify({pending,ready,coarse,off,enabled,empty,otherMode}));",
        rate_product(tmp_path), start_recent=False, cell_res=5000, historical_points=historical_fixture(),
    )
    assert result["pending"] and result["coarse"] and result["off"] and result["otherMode"]
    assert not result["ready"] and not result["enabled"] and not result["empty"]["hidden"]
    assert "No supplied historical points" in result["empty"]["message"]


def test_data_dependent_disclosures_start_hidden_in_actual_template():
    source = (ROOT / "webapp/index.html").read_text()
    for identity in ("recentChallengeDetails", "recentChallengeSightings", "historicalPointsDetails"):
        assert re.search(r'<details\b[^>]*\bid="' + identity + r'"[^>]*\bhidden(?:\s|>)', source), identity
