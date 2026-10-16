"""Replay shared links through the app's actual restoration and URL handlers."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(src, name):
    match = re.search(r"\n(?:async )?function " + name + r"\([^\n]*?\{", src)
    assert match, f"{name} not found in the template"
    depth = 0
    tokens = re.finditer(
        r"""/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[{}]""",
        src[match.end() - 1 :],
        re.S,
    )
    for token in tokens:
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return src[match.start() : match.end() - 1 + token.end()]
    raise AssertionError(f"{name} has no closing brace")


def _run(
    search, action, *, real_cells=False, real_overlay=False, controls=False, real_build=False, base_href=None
):
    src = TEMPLATE.read_text()
    script = r"""
const input=JSON.parse(process.argv[1]);
let location=new URL('https://example.org/map.html'+input.search);
const history={replaceState:(_state,_title,url)=>{const next=new URL(url,input.base_href||location);if(next.origin!==location.origin)throw Error('Cross-origin history URL');location=next;}};
const FILES={'All biodiversity':'all.json',Aves:'birds.json',Fungi:'fungi.json'};
const PRESETS=[{w:[1],proj:'p0'},{w:[2],proj:'p1'},{w:[3],proj:'p2'}];
const BB={minlon:-141,maxlon:-52,minlat:41,maxlat:84},TIER_SWITCH_Z=9;
const window={__activePreset:0};
const state={taxon:'All biodiversity',view:'explore',w:{}};
const nodes={taxon:{value:'All biodiversity'},criteria:{value:'0'},basemap:{value:'Standard'}};
const document={getElementById:id=>nodes[id]||(nodes[id]={style:{},dataset:{},innerHTML:'',classList:{remove(){}}})};
const taxonSel=nodes.taxon,critSel=nodes.criteria,bmSel=nodes.basemap;
const loads=[],opened=[],steps=[];
let zoom=3,overlay='priority',DRAWN5=null,_mapLoaded=true;
var _sharedCell=null,_shareContext=false,_shareAction=0,_selectionSeq=0,_taxonSeq=0,_taxonPending=null,_overlayReady=Promise.resolve();
var _committedTaxon='All biodiversity',_bootTaxon='All biodiversity';
let prospectSeq=0,_lastProspect=null;
const refreshes=[];
const DATA={'All biodiversity':[[49.3,-123.2,0]]};
let markers=[{r:[49.3,-123.2,0]}],M25=new Map();
const cellPoly=()=>[[0,0],[0,1],[1,1],[1,0],[0,0]];
const map={getZoom:()=>zoom,jumpTo:o=>{zoom=o.zoom;steps.push('jump');},getSource:()=>({setData(){}}),
  getContainer:()=>({clientHeight:0}),project:p=>({x:p[0],y:p[1]}),unproject:p=>p};
let whenMap=fn=>fn();
async function loadGroup(g){loads.push(g);}
function buildMarkers(){steps.push('markers');_committedTaxon=state.taxon;}
const setView=v=>{state.view=v;};
const showLoading=()=>{};
let located=0;
const locateMe=()=>{located++;};
const t=id=>id,presetName=i=>String(i),presetBlurb=i=>String(i),groupName=g=>g;
const applyWeights=()=>{};
const setBase=v=>{steps.push('base:'+v);};
const setCoverage=()=>{},setClimate=()=>{},setGridLines=()=>{},updateLegend=()=>{},recolour=()=>{};
const renderGoalContext=()=>{},renderCellTable=()=>{},paintPopScore=()=>{};
const geActive=()=>overlay==='gettingeven',covOn=()=>overlay==='coverage',climateOn=()=>overlay==='climate';
async function setOverlay(kind){overlay=kind;steps.push('overlay:'+kind);}
let loadUS=async()=>{steps.push('mask');};
async function refreshCells5(){await Promise.resolve();DRAWN5=new Set(['cell']);steps.push('draw5');}
const snapToLattice=(lat,lon)=>({lat,lon}),latKey=()=> 'cell';
function exploreCell(lat,lon){
  opened.push({lat,lon,resM:zoom>=TIER_SWITCH_Z?5000:25000,goal:critSel.value,overlay,style:bmSel.value,drawn:!!DRAWN5});
  return true;
}
"""
    for name in ("parseSharedContext", "sharedCellUrl", "syncSharedCellUrl", "restoreSharedCell"):
        if re.search(r"\n(?:async )?function " + name + r"\(", src):
            script += _function(src, name)
    script += _function(src, "applyChallenge") + _function(src, "bootSeq")
    if real_build:
        script += _function(src, "rows") + _function(src, "buildMarkers")
    if real_cells:
        script += r"""
let _reselect=false,_popScoreEl=null,destMarker=null;
const activeCellResM=()=>zoom>=TIER_SWITCH_Z?5000:25000;
let parentMarker=()=>markers[0];
const caOnlyActive=()=>false,US_CELLS=null;
const clearRoute=()=>{},popScoreHtml=()=>'',contribStr=()=>'',openHowSection=()=>{};
const cellBoundsForRes=()=>({swlat:49,nelat:49.1,swlng:-123,nelng:-122.9});
const ICONIC={Aves:'Aves',Fungi:'Fungi'},LANG='en',esc=s=>s;
function fetchProspects(lat,lon,whereKey,opts){
  prospectSeq++;_lastProspect={lat,lon,whereKey,...opts};
  refreshes.push({group:state.taxon,lat,lon,whereKey,resM:opts.resM,toPopup:opts.toPopup});
}
const setDestCell=(lat,lon,resM)=>opened.push({lat,lon,resM,group:state.taxon,goal:critSel.value,overlay,style:bmSel.value});
const wirePopup=p=>p;
const maplibregl={Popup:class{setDOMContent(){return this;}}};
const destMarkerAt=()=>({addTo(){return this;},setPopup(){return this;},togglePopup(){}});
document.createElement=()=>({dataset:{},appendChild(){},setAttribute(){}});
"""
        script += _function(src, "cellRecordsUrl") + _function(src, "popupActionsHtml")
        script += _function(src, "wireGroupComparison") + _function(src, "exploreCell")
        script += _function(src, "clearSelection")
    if real_overlay:
        script += "\nconst OVERLAY_TOGGLE={coverage:'tgCoverage',gettingeven:'tgGettingEven',climate:'tgClimateGap'};"
        script += "\nlet _overlaySeq=0;async function loadGE(){};"
        script += _function(src, "setOverlay")
    if controls:
        if "function refreshProspectGroup(" in src:
            script += _function(src, "refreshProspectGroup")
        script += src[src.index("\ntaxonSel.onchange=") : src.index("\n\nfunction applyWeights(")]
        script += src[
            src.index("\nif(critSel)critSel.onchange=") : src.index("\napplyChallenge(PRESETS[0]);")
        ]
        script += src[src.index("\nbmSel.onchange=") : src.index("\n// dec25 100 m density")]
    script += "\n(async()=>{" + action + "})().catch(e=>{console.error(e);process.exit(1);});"
    out = subprocess.run(
        ["node", "-e", script, "--", json.dumps({"search": search, "base_href": base_href})],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_shared_fine_cell_restores_group_goal_style_and_waits_for_the_grid():
    result = _run(
        "?at=49.300,-123.200&g=Aves&res=5000&goal=2&style=satellite&plan=1&compare=1",
        "await bootSeq();await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({loads,opened,steps}));",
    )
    assert result["loads"] == ["Aves"]
    assert result["opened"] == [
        {
            "lat": 49.3,
            "lon": -123.2,
            "resM": 5000,
            "goal": "2",
            "overlay": "priority",
            "style": "Satellite",
            "drawn": True,
        }
    ]
    assert result["steps"].index("jump") < result["steps"].index("draw5")


def test_remote_asset_base_cannot_prevent_shared_url_from_following_the_current_goal():
    result = _run(
        "?goal=0",
        "critSel.value='2';syncSharedCellUrl(true);console.log(JSON.stringify({goal:location.searchParams.get('goal'),origin:location.origin}));",
        base_href="https://assets.example.org/map/",
    )
    assert result == {"goal": "2", "origin": "https://example.org"}


def test_selecting_a_cell_preserves_flags_and_encodes_its_context():
    result = _run(
        "?plan=1&compare=1&campaign=autumn#map",
        "zoom=10;critSel.value='2';bmSel.value='Satellite';exploreCell(49.3,-123.2);"
        "console.log(JSON.stringify({url:location.href,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
    )
    assert result["params"] == {
        "plan": "1",
        "compare": "1",
        "campaign": "autumn",
        "at": "49.300,-123.200",
        "g": "All biodiversity",
        "res": "5000",
        "goal": "2",
        "style": "satellite",
    }
    assert result["url"].endswith("#map")


def test_open_cell_url_follows_group_goal_and_density_changes():
    result = _run(
        "?plan=1&compare=1#map",
        "zoom=10;exploreCell(49.3,-123.2);taxonSel.value='Aves';taxonSel.onchange();"
        "await new Promise(resolve=>setImmediate(resolve));critSel.value='2';critSel.onchange();"
        "bmSel.value='__inat__';bmSel.onchange();"
        "console.log(JSON.stringify({params:Object.fromEntries(location.searchParams),overlay}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["params"]["g"] == "Aves"
    assert result["params"]["goal"] == "2"
    assert result["params"]["style"] == "density"
    assert result["params"]["res"] == "5000"
    assert result["params"]["plan"] == result["params"]["compare"] == "1"
    assert result["overlay"] == "coverage"


@pytest.mark.parametrize("res", [5000, 25000])
@pytest.mark.parametrize(
    ("goal", "style", "overlay"),
    [
        ("2", "satellite", "priority"),
        ("ge", "standard", "gettingeven"),
        ("climate", "terrain", "climate"),
        ("2", "density", "coverage"),
    ],
)
def test_shared_cell_context_round_trips_through_real_selection_and_boot(res, goal, style, overlay):
    result = _run(
        "?plan=1&compare=1#map",
        "zoom=" + str(10 if res == 5000 else 7) + ";state.taxon='Aves';"
        f"critSel.value={json.dumps(goal)};bmSel.value={json.dumps({'standard': 'Standard', 'satellite': 'Satellite', 'terrain': 'Terrain', 'density': '__inat__'}[style])};"
        f"overlay={json.dumps(overlay)};exploreCell(49.3,-123.2);const first=location.href;"
        "zoom=3;overlay='priority';critSel.value='0';bmSel.value='Standard';state.taxon='All biodiversity';"
        "await bootSeq();console.log(JSON.stringify({first,second:location.href,opened,loads}));",
        real_cells=True,
        real_overlay=True,
    )
    assert result["first"] == result["second"]
    assert result["loads"] == ["Aves"]
    assert result["opened"][0] == result["opened"][1]
    assert result["opened"][1]["resM"] == res
    assert result["opened"][1]["overlay"] == overlay


@pytest.mark.parametrize(
    "search",
    [
        "?at=91,-123",
        "?at=49,-181",
        "?at=NaN,-123",
        "?at=Infinity,-123",
        "?at=49,",
        "?at=49,-123,0",
        "?at=40,-123",
        "?at=85,-123",
    ],
)
def test_invalid_coordinates_do_not_open_a_cell(search):
    result = _run(search, "await bootSeq();console.log(JSON.stringify({opened,located}));")
    assert result["opened"] == []
    assert result["located"] == 1


@pytest.mark.parametrize(
    "params",
    [
        "g=__proto__&res=0&goal=-1&style=__proto__",
        "g=unknown&res=5000.0&goal=NaN&style=unknown",
        "g=constructor&res=Infinity&goal=999999&style=Satellite",
        "g=unknown&res=25001&goal=2.5&style=javascript:alert(1)",
    ],
)
def test_invalid_context_values_fall_back_to_valid_defaults(params):
    result = _run("?at=49.3,-123.2&" + params, "await bootSeq();console.log(JSON.stringify({opened,loads}));")
    assert result["loads"] == ["All biodiversity"]
    assert result["opened"][0]["resM"] == 25000
    assert result["opened"][0]["goal"] == "0"
    assert result["opened"][0]["style"] == "Standard"


def test_legacy_cell_links_keep_their_25_km_default():
    result = _run("?at=49.3,-123.2&g=Aves", "await bootSeq();console.log(JSON.stringify({opened,loads}));")
    assert result["loads"] == ["Aves"]
    assert result["opened"][0]["resM"] == 25000
    assert result["opened"][0]["goal"] == "0"


def test_restoration_waits_for_getting_even_and_keeps_a_newer_goal():
    result = _run(
        "?at=49.3,-123.2&g=Aves&res=5000&goal=ge&style=standard",
        "let release;loadGE=()=>new Promise(resolve=>{release=resolve;});const pending=bootSeq();"
        "await new Promise(resolve=>setImmediate(resolve));const before=opened.length;"
        "critSel.value='2';critSel.onchange();bmSel.value='Terrain';bmSel.onchange();release();await pending;"
        "console.log(JSON.stringify({before,opened,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["before"] == 0
    assert result["opened"][0]["goal"] == "2"
    assert result["opened"][0]["overlay"] == "priority"
    assert result["params"]["goal"] == "2"
    assert result["params"]["style"] == "terrain"


@pytest.mark.parametrize("replacement", ["goal", "density"])
def test_old_getting_even_failure_cannot_reset_a_newer_shared_context(replacement):
    action = (
        "critSel.value='2';critSel.onchange();"
        if replacement == "goal"
        else "bmSel.value='__inat__';bmSel.onchange();"
    )
    result = _run(
        "?plan=1",
        "zoom=10;exploreCell(49.3,-123.2);let rejectGE;loadGE=()=>new Promise((_resolve,reject)=>{rejectGE=reject;});"
        "critSel.value='ge';const pending=setOverlay('gettingeven');"
        + action
        + "rejectGE(Error('offline'));await pending;console.log(JSON.stringify({overlay,goal:critSel.value,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["params"]["goal"] == ("2" if replacement == "goal" else "ge")
    assert result["overlay"] == ("priority" if replacement == "goal" else "coverage")
    assert result["params"]["style"] == ("standard" if replacement == "goal" else "density")


def test_shared_restoration_waits_for_map_readiness():
    result = _run(
        "?at=49.3,-123.2&res=5000",
        "_mapLoaded=false;let ready;whenMap=fn=>{ready=fn;};const pending=bootSeq();"
        "await new Promise(resolve=>setImmediate(resolve));const before=opened.length;"
        "_mapLoaded=true;ready();await pending;console.log(JSON.stringify({before,opened}));",
    )
    assert result["before"] == 0
    assert result["opened"][0]["resM"] == 5000


def test_shared_restoration_waits_for_the_fine_grid_and_does_not_select_its_parent():
    result = _run(
        "?at=49.3,-123.2&res=5000",
        "let draw;refreshCells5=()=>new Promise(resolve=>{draw=()=>{DRAWN5=new Set(['cell']);resolve();};});"
        "const pending=bootSeq();await new Promise(resolve=>setImmediate(resolve));const before=opened.length;"
        "draw();await pending;console.log(JSON.stringify({before,opened}));",
    )
    assert result["before"] == 0
    assert result["opened"][0]["resM"] == 5000


def test_an_unavailable_fine_grid_does_not_silently_open_the_parent_cell():
    result = _run(
        "?at=49.3,-123.2&res=5000",
        "refreshCells5=async()=>{};await bootSeq();"
        "console.log(JSON.stringify({opened,error:document.getElementById('loading').innerHTML}));",
    )
    assert result["opened"] == []
    assert "bootRetry" in result["error"]


def test_deselecting_only_removes_cell_parameters_and_keeps_current_context():
    result = _run(
        "?plan=1&compare=1&campaign=autumn#map",
        "zoom=10;state.taxon='Aves';critSel.value='2';bmSel.value='Satellite';exploreCell(49.3,-123.2);"
        "clearSelection();console.log(JSON.stringify({params:Object.fromEntries(location.searchParams),hash:location.hash}));",
        real_cells=True,
        controls=True,
    )
    assert result["params"] == {
        "plan": "1",
        "compare": "1",
        "campaign": "autumn",
        "g": "Aves",
        "goal": "2",
        "style": "satellite",
    }
    assert result["hash"] == "#map"


def test_initial_group_load_cannot_overwrite_a_newer_goal_and_style():
    result = _run(
        "?at=49.3,-123.2&g=Aves&res=5000&goal=2&style=satellite",
        "let ready;loadGroup=g=>new Promise(resolve=>{loads.push(g);ready=resolve;});const pending=bootSeq();"
        "critSel.value='climate';critSel.onchange();bmSel.value='Terrain';bmSel.onchange();ready();await pending;"
        "console.log(JSON.stringify({opened,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["opened"][0]["goal"] == "climate"
    assert result["opened"][0]["style"] == "Terrain"
    assert result["params"]["goal"] == "climate"
    assert result["params"]["style"] == "terrain"


def test_late_taxon_failure_does_not_revert_a_newer_group_or_its_link():
    result = _run(
        "?plan=1",
        "zoom=10;exploreCell(49.3,-123.2);const jobs={};loadGroup=g=>new Promise((resolve,reject)=>{jobs[g]={resolve,reject};});"
        "taxonSel.value='Aves';taxonSel.onchange();taxonSel.value='Fungi';taxonSel.onchange();"
        "jobs.Fungi.resolve();await new Promise(resolve=>setImmediate(resolve));jobs.Aves.reject(Error('offline'));"
        "await new Promise(resolve=>setImmediate(resolve));console.log(JSON.stringify({group:state.taxon,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["group"] == result["params"]["g"] == "Fungi"


@pytest.mark.parametrize("initial_fails", [False, True])
def test_initial_restoration_waits_for_the_latest_group_and_ignores_an_old_failure(initial_fails):
    finish = "jobs.Aves.reject(Error('offline'));" if initial_fails else "jobs.Aves.resolve();"
    result = _run(
        "?at=49.3,-123.2&g=Aves&res=5000&goal=2&style=satellite",
        "const jobs={};loadGroup=g=>new Promise((resolve,reject)=>{loads.push(g);jobs[g]={resolve,reject};});"
        "const pending=bootSeq();taxonSel.value='Fungi';taxonSel.onchange();critSel.value='climate';critSel.onchange();"
        + finish
        + "await new Promise(resolve=>setImmediate(resolve));const before=opened.length;jobs.Fungi.resolve();await pending;"
        "console.log(JSON.stringify({before,loads,opened,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["before"] == 0
    assert result["loads"] == ["Aves", "Fungi"]
    assert result["opened"][0]["group"] == "Fungi"
    assert result["opened"][0]["goal"] == "climate"
    assert result["params"]["g"] == "Fungi"


def test_an_unavailable_shared_cell_under_a_raster_reports_failure():
    result = _run(
        "?at=49.3,-123.2&res=5000&goal=climate",
        "parentMarker=()=>null;await bootSeq();"
        "console.log(JSON.stringify({opened,error:document.getElementById('loading').innerHTML}));",
        real_cells=True,
        real_overlay=True,
    )
    assert result["opened"] == []
    assert "bootRetry" in result["error"]


def test_leaving_density_keeps_the_goal_control_and_shared_layer_consistent():
    result = _run(
        "?plan=1",
        "zoom=10;exploreCell(49.3,-123.2);critSel.value='2';critSel.onchange();"
        "critSel.value='climate';critSel.onchange();bmSel.value='__inat__';bmSel.onchange();"
        "bmSel.value='Satellite';bmSel.onchange();"
        "console.log(JSON.stringify({goal:critSel.value,overlay,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
    )
    assert result["overlay"] == "priority"
    assert result["goal"] == result["params"]["goal"] == "2"
    assert result["params"]["style"] == "satellite"


@pytest.mark.parametrize("older_succeeds", [False, True])
def test_latest_group_failure_rolls_back_to_the_last_rendered_group(older_succeeds):
    finish = (
        "DATA.Aves=[[49.3,-123.2,1]];jobs.Aves.resolve();"
        if older_succeeds
        else "jobs.Aves.reject(Error('offline'));"
    )
    result = _run(
        "?plan=1",
        "zoom=10;buildMarkers();exploreCell(49.3,-123.2);const jobs={};"
        "loadGroup=g=>new Promise((resolve,reject)=>{jobs[g]={resolve,reject};});"
        "taxonSel.value='Aves';taxonSel.onchange();taxonSel.value='Fungi';taxonSel.onchange();"
        + finish
        + "await new Promise(resolve=>setImmediate(resolve));jobs.Fungi.reject(Error('offline'));"
        "await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({group:state.taxon,selected:taxonSel.value,rows:markers.map(m=>m.r[2]),params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
        real_overlay=True,
        controls=True,
        real_build=True,
    )
    assert result["group"] == result["selected"] == result["params"]["g"] == "All biodiversity"
    assert result["rows"] == [0]
    assert result["params"]["res"] == "5000"


def test_group_change_and_rollback_restart_the_open_cells_evidence():
    result = _run(
        "?plan=1",
        "zoom=10;buildMarkers();exploreCell(49.3,-123.2);const old=prospectSeq;"
        "let reject;loadGroup=()=>new Promise((_resolve,fail)=>{reject=fail;});"
        "taxonSel.value='Aves';taxonSel.onchange();const invalidated=prospectSeq>old;"
        "reject(Error('offline'));await new Promise(resolve=>setImmediate(resolve));"
        "console.log(JSON.stringify({refreshes,invalidated,group:state.taxon}));",
        real_cells=True,
        controls=True,
        real_build=True,
    )
    assert result["invalidated"]
    assert [r["group"] for r in result["refreshes"]] == ["All biodiversity", "Aves", "All biodiversity"]
    assert all(r["resM"] == 5000 and r["toPopup"] for r in result["refreshes"])
    assert result["group"] == "All biodiversity"


@pytest.mark.parametrize(
    "phase", ["initial_group", "map", "latest_group", "set_overlay", "overlay", "mask", "fine_grid"]
)
@pytest.mark.parametrize("manual", ["select", "clear"])
def test_manual_selection_or_deselection_cancels_each_deferred_restore_phase(phase, manual):
    barriers = {
        "initial_group": "loadGroup=g=>new Promise(resolve=>{loads.push(g);release=resolve;});",
        "map": "_mapLoaded=false;whenMap=fn=>{release=()=>{_mapLoaded=true;fn();};};",
        "latest_group": "loadGroup=async g=>{loads.push(g);if(g==='Fungi')await new Promise(resolve=>{release=resolve;});};",
        "set_overlay": "setOverlay=kind=>{overlay=kind;return new Promise(resolve=>{release=resolve;});};",
        "overlay": "_overlayReady=new Promise(resolve=>{release=resolve;});",
        "mask": "loadUS=()=>new Promise(resolve=>{release=resolve;});",
        "fine_grid": "refreshCells5=()=>new Promise(resolve=>{release=()=>{DRAWN5=new Set(['cell']);resolve();};});",
    }
    selected = "exploreCell(54.1,-120.2);" if manual == "select" else "clearSelection();"
    change_group = "state.taxon='Fungi';" if phase == "latest_group" else ""
    result = _run(
        "?at=49.3,-123.2&g=Aves&res=5000&goal=2&style=satellite&plan=1#map",
        "let release;"
        + barriers[phase]
        + "const pending=bootSeq();"
        + change_group
        + "await new Promise(resolve=>setImmediate(resolve));if(!release)throw Error('barrier not reached');"
        + selected
        + "const manualUrl=location.href,manualSteps=steps.slice();release();await pending;"
        "console.log(JSON.stringify({opened,params:Object.fromEntries(location.searchParams),url:location.href,manualUrl,steps,manualSteps,error:document.getElementById('loading').innerHTML}));",
        real_cells=True,
    )
    assert result["url"] == result["manualUrl"]
    assert result["steps"] == result["manualSteps"]
    assert "bootRetry" not in result["error"]
    assert result["params"]["plan"] == "1"
    if manual == "select":
        assert len(result["opened"]) == 1
        assert result["opened"][0]["lat"] == 54.1
        assert result["opened"][0]["lon"] == -120.2
        assert result["params"]["at"] == "54.100,-120.200"
    else:
        assert result["opened"] == []
        assert "at" not in result["params"] and "res" not in result["params"]


@pytest.mark.parametrize("phase", ["initial_group", "latest_group", "set_overlay", "mask", "fine_grid"])
@pytest.mark.parametrize("manual", ["select", "clear"])
def test_rejected_canceled_restore_does_not_show_an_error_or_retry(phase, manual):
    barriers = {
        "initial_group": "loadGroup=()=>new Promise((_resolve,reject)=>{fail=reject;});",
        "latest_group": "loadGroup=async g=>{if(g==='Fungi')await new Promise((_resolve,reject)=>{fail=reject;});};",
        "set_overlay": "setOverlay=kind=>{overlay=kind;return new Promise((_resolve,reject)=>{fail=reject;});};",
        "mask": "loadUS=()=>new Promise((_resolve,reject)=>{fail=reject;});",
        "fine_grid": "refreshCells5=()=>new Promise((_resolve,reject)=>{fail=reject;});",
    }
    selected = "exploreCell(54.1,-120.2);" if manual == "select" else "clearSelection();"
    result = _run(
        "?at=49.3,-123.2&g=Aves&res=5000",
        "let fail;"
        + barriers[phase]
        + "const pending=bootSeq();"
        + ("state.taxon='Fungi';" if phase == "latest_group" else "")
        + "await new Promise(resolve=>setImmediate(resolve));if(!fail)throw Error('barrier not reached');"
        + selected
        + "const manualUrl=location.href;fail(Error('offline'));await pending;"
        "console.log(JSON.stringify({opened,url:location.href,manualUrl,error:document.getElementById('loading').innerHTML}));",
        real_cells=True,
    )
    assert result["url"] == result["manualUrl"]
    assert len(result["opened"]) == (1 if manual == "select" else 0)
    assert "bootRetry" not in result["error"]


def test_an_invalid_manual_selection_does_not_cancel_a_valid_shared_restore():
    result = _run(
        "?at=49.3,-123.2&res=5000",
        "let release;loadUS=()=>new Promise(resolve=>{release=resolve;});const pending=bootSeq();"
        "await new Promise(resolve=>setImmediate(resolve));const original=parentMarker;parentMarker=()=>null;"
        "exploreCell(54.1,-120.2);parentMarker=original;release();await pending;"
        "console.log(JSON.stringify({opened,error:document.getElementById('loading').innerHTML}));",
        real_cells=True,
    )
    assert len(result["opened"]) == 1
    assert result["opened"][0]["lat"] == 49.3
    assert "bootRetry" not in result["error"]


@pytest.mark.parametrize("phase", ["initial_group", "mask", "fine_grid"])
def test_uncanceled_restoration_failures_keep_the_existing_retry(phase):
    barriers = {
        "initial_group": "loadGroup=async()=>{throw Error('offline');};",
        "mask": "loadUS=async()=>{throw Error('offline');};",
        "fine_grid": "refreshCells5=async()=>{throw Error('offline');};",
    }
    result = _run(
        "?at=49.3,-123.2&res=5000",
        barriers[phase]
        + "await bootSeq();console.log(JSON.stringify({opened,error:document.getElementById('loading').innerHTML}));",
        real_cells=True,
    )
    assert result["opened"] == []
    assert "bootRetry" in result["error"]
