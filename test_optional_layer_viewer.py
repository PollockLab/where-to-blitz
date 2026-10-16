"""Exercise the shipped optional-layer helpers and actual build without public data."""

import hashlib
import json
import os
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


def sighting_lifecycle(action, discovery_product=None, snapshot=None, start_recent=True):
    """Replay real popup wiring, list reconciliation and synchronous map move events."""
    from test_shared_context import _function

    source = (ROOT / "webapp/index.html").read_text()
    script = (
        SCRIPT
        + source[source.index("const I18N=") : source.index("\nlet LANG=")]
        + source[source.index("const esc=") : source.index("const safeImg=")]
        + r"""
const input=JSON.parse(process.argv[1]);
class Element{
 constructor(tag,connected=false){this.tag=tag;this.connected=connected;this.children=[];this.dataset={};this._text='';this.hidden=true;this.classList={contains:()=>false};}
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
 querySelector(selector){return this.children.find(el=>selector==='b'?el.tag==='b':selector==='p'?el.tag==='p':selector==='a'?el.tag==='a':el.tag==='close')||null;}
 querySelectorAll(){return [];}
 addEventListener(){}
 setAttribute(key,value){this[key]=value;}
 getClientRects(){return this.isConnected?[{}]:[];}
 focus(){if(this.isConnected&&this.tag!=='body')document.activeElement=this;}
}
const handlers={},nodes={},document={activeElement:null,body:new Element('body',true),
 createElement:tag=>new Element(tag),getElementById:id=>nodes[id]||(nodes[id]=new Element(id,true)),
 addEventListener:(event,fn)=>handlers[event]=fn};document.activeElement=document.body;
const getComputedStyle=()=>({visibility:'visible',display:'block'});
const canvas=new Element('canvas',true),language=new Element('language',true);
let curPopup=null,_reselect=false,destMarker=null,destCell=null,_mapLoaded=true,_shareAction=0,overlay='priority',LANG='en';
const state={view:'explore',taxon:'All biodiversity'},OPTIONAL_LAYERS={discovery:input.discovery?{taxon_group:'All biodiversity',scope:input.discovery.scope,metric:input.discovery.metric}:null,challenge:{}};
const syncSharedCellUrl=()=>{},localizeMapControls=()=>{};
const whenMap=fn=>fn(),clearSelection=()=>{},activeCellResM=()=>input.discovery?5000:25000;
const LATTICE_X0=-2150000,LATTICE_Y1=4300000,LATTICE_W=5500000,LATTICE_H=4525000;
const LAEA={fwd:()=>[-150000,2300000],inv:()=>[45,-75]},virColour=()=> 'rgb(1,2,3)',snapToLattice=()=>({row:400,col:400});let DRAWN5=null;
function exploreCell(){const origin=document.activeElement;map.jumpTo({});originAtExplore=origin;return true;}
let originAtExplore=null;
let viewportBounds=[-90,40,-60,50];
const map={getCanvas:()=>canvas,getBounds:()=>({getWest:()=>viewportBounds[0],getEast:()=>viewportBounds[2],getSouth:()=>viewportBounds[1],getNorth:()=>viewportBounds[3]}),
 getZoom:()=>7,getSource:id=>map.sources[id],getLayer:id=>map.sources[id],sources:{},
 addSource(id){this.sources[id]={setData(){}};},addLayer(){},setLayoutProperty(){},queryRenderedFeatures:()=>[],
 on(event,...args){handlers[event]=args.at(-1);},jumpTo(){handlers.moveend();}};
const maplibregl={Popup:class{
 constructor(options){this.options=options;this.handlers={};this.root=new Element('popup',true);this.close=new Element('close');this.root.append(this.close);}
 on(event,fn){(this.handlers[event]||(this.handlers[event]=[])).push(fn);return this;}
 emit(event){for(const fn of this.handlers[event]||[])fn();}
 setLngLat(){return this;}setDOMContent(el){this.root.append(el);return this;}
 getElement(){return this.root;}addTo(){this.emit('open');return this;}
 remove(){if(this.root.contains(document.activeElement))document.activeElement=document.body;this.root.connected=false;this.emit('close');}
}};
OptionalData.fetchPinned=async(_descriptor,kind)=>kind==='discovery'?input.discovery:input.snapshot;
"""
    )
    for name in (
        "t",
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
        ["node", "-e", script, json.dumps({"snapshot": snapshot, "discovery": discovery_product})],
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
        "verified targets" in result["counts"] if language == "en" else "taxons vérifiés" in result["counts"]
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
