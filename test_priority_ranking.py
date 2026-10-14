"""Run the viewer's rank and eligibility code against boundary and shipped-data cases."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "webapp" / "index.html"
NODE = shutil.which("node")


def run_js(body):
    if not NODE:
        pytest.skip("Node.js is required to execute the viewer's ranking code")
    source = TEMPLATE.read_text()
    mask = source[source.index("function caOnlyActive()") : source.index("function geColour(")]
    ranking = source[
        source.index("function renderCellTable()") : source.index("// The viewport-relative repaint lives")
    ]
    planner = source[source.index("async function planTrip()") : source.index("function tripRow(")]
    overlays = source[source.index("function setOverlay(") : source.index("// The 25 km lattice outline")]
    harness = r"""const assert=require("node:assert/strict");
let markers=[],overlay="priority",failed=false,requests=0,maskOK=true,scoreOverlay="priority",_overlaySeq=0;
let maskData={us_cells:["77.190,-52.080"]};
let table={innerHTML:"",querySelectorAll:()=>[]};
let trips={innerHTML:"",textContent:""};
const document={getElementById:id=>id==="celltable"?table:id==="trips"?trips:null};
const gekey=(lat,lon)=>lat.toFixed(3)+","+lon.toFixed(3);
const t=key=>key,impact=r=>r[2];
const DATA_DIR="";
const geActive=()=>overlay==="gettingeven";
const geColour=()=>"grey",TIER_SWITCH_Z=9;
const _mapLoaded=false,map={getZoom:()=>3};
const setCellState=()=>{},setGridFill=()=>{},paintPopScore=()=>{scoreOverlay=overlay;};
const OVERLAY_TOGGLE={gettingeven:"tgGettingEven",climate:"tgClimateGap",coverage:"tgCoverage"};
const covOn=()=>overlay==="coverage",setGridLines=()=>{},setCoverage=()=>{},setClimate=()=>{},updateLegend=()=>{},renderGoalContext=()=>{};
let loadGE=()=>new Promise(()=>{});
const routeRequests=[];
const fetchT=async url=>{requests++;if(url.includes("route/v1"))routeRequests.push(url);
  if(failed)throw Error("offline");
  return {ok:maskOK,json:async()=>maskData};};
const cell=(lat,lon,score)=>({r:[lat,lon,score],i:0,t:0});
let planned=false,lastFit=null,lastScored=null,lastDest=null;
const state={start:[45,-75],budget:10,mode:"Drive",modeSet:true,maxLeg:0,minRatio:0};
const MODES={Drive:{host:"car",kmh:60,emit:0.18}},ROAD_FACTOR=1.35,MIN_FIELD_H=0.5,N_CANDIDATES=8;
const OSRM_BASE="https://routes.test/",modeName=m=>m;
const haversine=(a,b,c,d)=>Math.abs(a-c)+Math.abs(b-d);
const rankAndRender=()=>{},selectTrip=()=>{},tripRow=()=>{};
"""
    result = subprocess.run(
        [
            NODE,
            "-",
        ],
        input=harness + mask + ranking + planner + overlays + "\n(async()=>{\n" + body + "\n})().catch(e=>{"
        "console.error(e);process.exitCode=1;});",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_hidden_countries_are_excluded_from_rank_and_accessible_list():
    run_js("""
US_CELLS=new Set(["77.190,-52.080"]);
markers=[cell(45,-75,0.1),cell(46,-76,0.5),cell(77.19,-52.08,1)];
recolour();
assert.deepEqual(markers.map(m=>m.t),[0,1,null]);
assert(!table.innerHTML.includes('data-la="77.19"'));
assert(table.innerHTML.includes('data-la="46"'));
""")


def test_pending_overlay_load_removes_previous_priority_evidence_immediately():
    run_js("""
US_CELLS=new Set();markers=[cell(45,-75,0),cell(46,-76,1)];recolour();
assert(table.innerHTML.includes('data-la='));
assert.equal(scoreOverlay,'priority');
setOverlay('gettingeven');
assert(!table.innerHTML.includes('data-la='));
assert.equal(scoreOverlay,'gettingeven');
""")


def test_failed_previous_overlay_load_does_not_replace_the_new_selection():
    run_js("""
US_CELLS=new Set();markers=[cell(45,-75,0),cell(46,-76,1)];recolour();
let reject;loadGE=()=>new Promise((_,fail)=>{reject=fail;});
setOverlay('gettingeven');setOverlay('climate');
reject(Error('offline'));await Promise.resolve();
assert.equal(overlay,'climate');
assert.equal(scoreOverlay,'climate');
""")


def test_tied_priorities_share_average_rank_independent_of_input_order():
    run_js("""
US_CELLS=new Set();
markers=[cell(45,-75,0),cell(46,-76,0.5),cell(47,-77,0.5),cell(48,-78,1)];
recolour();
assert.deepEqual(markers.map(m=>m.t),[0,0.5,0.5,1]);
markers.reverse();recolour();
assert.deepEqual(markers.map(m=>m.t),[1,0.5,0.5,0]);
""")


def test_flat_singleton_and_empty_populations_do_not_invent_a_priority():
    run_js("""
US_CELLS=new Set();
for(const scores of [[],[0.5],[0.5,0.5,0.5]]){
  markers=scores.map((v,i)=>cell(45+i,-75,v));recolour();
  assert.deepEqual(markers.map(m=>m.t),scores.map(()=>0));
}
""")


def test_mask_loading_and_failure_never_publish_unverified_national_ranks():
    run_js("""
markers=[cell(45,-75,0),cell(77.19,-52.08,1)];failed=true;
recolour();
assert.deepEqual(markers.map(m=>m.t),[null,null]);
assert(!table.innerHTML.includes('data-la='));
await loadUS();recolour();
assert.equal(US_CELLS,null);
assert.deepEqual(markers.map(m=>m.t),[null,null]);
assert(table.innerHTML.includes('rank_mask_unavailable'));
failed=false;await loadUS();recolour();
assert.equal(markers[0].t,0);assert.equal(markers[1].t,null);
""")


def test_concurrent_mask_requests_share_one_fetch():
    run_js("""
await Promise.all([loadUS(),loadUS(),loadUS()]);
assert.equal(requests,1);
""")


@pytest.mark.parametrize("response", ["null", "{}", '{us_cells:"invalid"}', "{us_cells:[123]}"])
def test_invalid_mask_does_not_turn_every_cell_into_canadian_land(response):
    run_js(f"""
maskData={response};
assert.equal(await loadUS(),false);
assert.equal(US_CELLS,null);
assert.equal(_usFailed,true);
""")


def test_unsuccessful_http_response_does_not_validate_country_coverage():
    run_js("""
maskOK=false;
assert.equal(await loadUS(),false);
assert.equal(US_CELLS,null);
""")


def test_auto_trip_candidates_and_tapped_destinations_exclude_foreign_cells():
    run_js("""
US_CELLS=new Set(["77.190,-52.080"]);
markers=[cell(45,-75,0.1),cell(46,-76,0.5),cell(77.19,-52.08,1)];recolour();
await planTrip();
assert(lastFit.every(o=>!US_CELLS.has(gekey(o.m.r[0],o.m.r[1]))));
assert(routeRequests.length>0);
assert(routeRequests.every(url=>!url.includes("-52.08,77.19")));
routeRequests.length=0;await setDest(77.19,-52.08);
assert(routeRequests.every(url=>!url.includes("-52.08,77.19")));
""")


def test_trip_planning_stops_before_routing_when_country_mask_is_unavailable():
    run_js("""
failed=true;markers=[cell(45,-75,0.1),cell(77.19,-52.08,1)];
await planTrip();
assert.equal(routeRequests.length,0);
assert.equal(trips.textContent,"rank_mask_unavailable");
await setDest(77.19,-52.08);
assert.equal(routeRequests.length,0);
""")


@pytest.mark.parametrize("layer", ["gettingeven", "climate", "coverage"])
def test_categorical_and_raster_layers_do_not_show_an_old_priority_list(layer):
    run_js(f"""
US_CELLS=new Set();markers=[cell(45,-75,0),cell(46,-76,1)];recolour();
overlay={json.dumps(layer)};recolour();
assert(!table.innerHTML.includes('data-la='));
assert(table.innerHTML.includes('rank_layer_unavailable'));
""")


def test_top_cells_on_shipped_data_are_canadian_and_tie_consistent():
    rows = json.loads((ROOT / "cluster_results/ca/webapp_data_All_biodiversity.json").read_text())[
        "All biodiversity"
    ]
    hidden = json.loads((ROOT / "cluster_results/ca/us_cells.json").read_text())["us_cells"]
    run_js(f"""
US_CELLS=new Set({json.dumps(hidden)});
markers={json.dumps(rows)}.map((r,i)=>({{r,i,t:0}}));recolour();
const eligible=markers.filter(m=>!US_CELLS.has(gekey(m.r[0],m.r[1])));
assert.equal(eligible.length,markers.length-US_CELLS.size);
assert(markers.filter(m=>US_CELLS.has(gekey(m.r[0],m.r[1]))).every(m=>m.t===null));
const byScore=new Map();
for(const m of eligible){{if(byScore.has(m.r[2]))assert.equal(m.t,byScore.get(m.r[2]));
  else byScore.set(m.r[2],m.t);}}
const top=eligible.toSorted((a,b)=>b.t-a.t).slice(0,40);
assert(top.every(m=>!US_CELLS.has(gekey(m.r[0],m.r[1]))));
""")
