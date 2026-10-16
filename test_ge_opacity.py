"""Replay the native opacity input against the viewer's actual layer lifecycle."""

import json
import re
import shutil
import subprocess

import pytest

from test_shared_context import TEMPLATE, _function

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_opacity(action, lang="en"):
    source = TEMPLATE.read_text()
    script = source[source.index("const I18N=") : source.index("\nlet LANG=")]
    script += f"\nlet LANG={json.dumps(lang)};"
    script += """
const assert=require('node:assert/strict');
function node(value){return {value,style:{},attrs:{},setAttribute(k,v){this.attrs[k]=v;},
  addEventListener(k,fn){this[k]=fn;}};}
const nodes={baseop:node(1),bople:node(),bopv:node(),opacityRow:node(),
  basemap:node('Standard'),criteria:node('ge')};
const document={getElementById:id=>nodes[id]||null};
const t=k=>I18N[LANG][k],window={__activePreset:0},state={taxon:'All biodiversity'};
const CELL_LAYER='cells-fill',TIER_SWITCH_Z=9,PM_BASE='',PM_TILE=256,COVPM=['All'];
const OVERLAY_TOGGLE={coverage:'tgCoverage',gettingeven:'tgGettingEven',climate:'tgClimateGap'};
let overlay='priority',_overlaySeq=0,_overlayReady,zoom=3,_mapLoaded=true;
let DRAWN5=null,_gridSeq=0,STRETCH=null,_lineFilterIds=-1;
const sources={cells:{setData(){}}},layers={'cells-fill':{paint:{}},basemap:{paint:{'raster-opacity':1}}};
const writes=[],cellStates={};
const map={getZoom:()=>zoom,getSource:id=>sources[id],getLayer:id=>layers[id],
  addSource(id){sources[id]={setData(){}};},removeSource(id){delete sources[id];},
  addLayer(layer){layers[layer.id]=layer;},removeLayer(id){delete layers[id];},
  setFeatureState({id},paint){cellStates[id]={...cellStates[id],...paint};},
  setPaintProperty(id,key,value){writes.push([id,key,value]);layers[id].paint[key]=value;},
  setFilter(){},once(){}};
const markers=[{i:0,r:[45,-75]},{i:1,r:[46,-76]}],US_CELLS=new Set(['46,-76']);
const gekey=(lat,lon)=>lat+','+lon,caOnlyActive=()=>true;
const geColour=()=> 'blue',eligibleMarkers=()=>[],impact=()=>0;
const renderCellTable=()=>{},paintPopScore=()=>{},renderGoalContext=()=>{},syncSharedCellUrl=()=>{};
const setGridLines=()=>{},updateLegend=()=>{},setBase=()=>{},ensurePM=()=>{};
const whenMap=fn=>fn(),loadGE=async()=>{},loadVals=async()=>({});
const sample25=()=>[],makeStretch=()=>null,paintGrid25=()=>{},warmOverlays=()=>{};
const refreshCells5=()=>{},updateLegendLabels=()=>{};
function snapshot(){return {visible:nodes.opacityRow.style.display!=='none',value:+nodes.baseop.value,
  step:nodes.baseop.step,label:nodes.bople.textContent,aria:nodes.baseop.attrs['aria-label'],
  text:nodes.baseop.attrs['aria-valuetext'],base:layers.basemap.paint['raster-opacity'],
  coarse:cellStates[0]?.op,masked:cellStates[1]?.op,fine:layers.grid5?.paint['fill-opacity']};}
function input(value){nodes.baseop.value=String(value);nodes.baseop.input({target:nodes.baseop});}
"""
    script += re.search(r"let baseOpacity=[^\n]+", source).group() + "\n"
    ge_state = re.search(r"let geOpacity=[^\n]+", source)
    if ge_state:
        script += ge_state.group() + "\n"
    for name in (
        "geActive",
        "covOn",
        "climateOn",
        "setCellState",
        "applyCanadaMask",
        "priorityPercentiles",
        "recolour",
        "setGridFill",
        "removeCov",
        "setCoverage",
        "setClimate",
        "setClimateOpacity",
        "dataOverlayActive",
        "setBaseOpacity",
        "setCovOpacity",
        "updateOpacityControl",
        "setOverlay",
    ):
        script += _function(source, name) + "\n"
    for name in ("paintGECells", "setGEOpacity"):
        if f"function {name}(" in source:
            script += _function(source, name) + "\n"
    script += source[
        source.index("document.getElementById('baseop').addEventListener('input'") : source.index(
            "\ndocument.getElementById('maxleg')"
        )
    ]
    result = subprocess.run(
        ["node"],
        input=script + "\n(async()=>{" + action + "})().catch(e=>{console.error(e);process.exitCode=1;});",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "lang,label", [("en", "Getting Even cell opacity"), ("fr", "Opacité des cellules Combler l'écart")]
)
def test_ge_native_slider_preserves_default_and_changes_only_its_cells(lang, label):
    result = run_opacity(
        """
await setOverlay('gettingeven');const initial=snapshot();writes.length=0;input(.31);
console.log(JSON.stringify({initial,changed:snapshot(),writes}));
""",
        lang,
    )
    assert result["initial"] == {
        "visible": True,
        "value": 0.62,
        "step": "0.01",
        "label": label,
        "aria": label,
        "text": "62%",
        "base": 1,
        "coarse": 0.62,
        "masked": 0,
        "fine": 0.62,
    }
    assert result["changed"]["coarse"] == result["changed"]["fine"] == 0.31
    assert result["changed"]["masked"] == 0 and result["changed"]["base"] == 1
    assert result["changed"]["text"] == "31%"
    assert all(write[0] == "grid5" for write in result["writes"])


def test_ge_opacity_survives_tier_and_goal_switches_without_changing_other_opacities():
    result = run_opacity("""
await setOverlay('coverage');input(.4);await setOverlay('climate');input(.55);
await setOverlay('gettingeven');input(.31);zoom=10;recolour();const fine=snapshot();
input(.74);const fineChanged=snapshot();await setOverlay('priority');const priority=snapshot();
const grid=layers.grid25.paint['fill-opacity'];await setOverlay('coverage');const density=snapshot();
await setOverlay('climate');const climate=snapshot();zoom=3;await setOverlay('gettingeven');
console.log(JSON.stringify({fine,fineChanged,priority,grid,density,climate,restored:snapshot()}));
""")
    assert result["fine"]["coarse"] == result["fineChanged"]["coarse"] == 0
    assert result["fine"]["fine"] == 0.31 and result["fineChanged"]["fine"] == 0.74
    assert not result["priority"]["visible"] and result["grid"] == 0.5
    assert result["density"]["value"] == 0.4 and result["density"]["step"] == "0.05"
    assert result["climate"]["value"] == 0.55 and result["climate"]["step"] == "0.05"
    assert result["restored"]["value"] == result["restored"]["coarse"] == result["restored"]["fine"] == 0.74
    assert all(snapshot["base"] == 1 for key, snapshot in result.items() if key != "grid")


def test_hidden_slider_input_cannot_change_the_basemap():
    result = run_opacity("""
await setOverlay('priority');writes.length=0;input(.2);
console.log(JSON.stringify({writes,state:snapshot()}));
""")
    assert result["writes"] == [] and result["state"]["base"] == 1


@pytest.mark.parametrize("value", [0.1, 1])
def test_ge_slider_boundaries_preserve_fine_geometry_and_mask(value):
    result = run_opacity(f"""
zoom=10;await setOverlay('gettingeven');const source=sources.cells5;DRAWN5=new Set(['scored']);
input({value});assert.equal(sources.cells5,source);assert.deepEqual([...DRAWN5],['scored']);
console.log(JSON.stringify(snapshot()));
""")
    assert result["value"] == result["fine"] == value
    assert result["coarse"] == result["masked"] == 0 and result["base"] == 1
