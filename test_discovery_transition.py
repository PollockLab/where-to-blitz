"""Replay the actual overlay transition before optional data settle."""

import json
import subprocess

import pytest

from test_shared_context import TEMPLATE, _function


def replay(previous, outcome, language="en"):
    source = TEMPLATE.read_text()
    script = source[source.index("const I18N=") : source.index("\nlet LANG=")]
    script += r"""
const input=JSON.parse(process.argv[1]);let LANG=input.language;
const t=(key,...args)=>{const value=I18N[LANG][key];return typeof value==='function'?value(...args):value;};
let overlay=input.previous,_overlaySeq=0,_overlayReady,GE_PRIORITY_LEGEND='PRIORITY LEGEND',GE_LOADED=false,_geLoading=false;
const state={taxon:'All biodiversity'},window={__activePreset:0};
const OVERLAY_TOGGLE={coverage:'tgCoverage',gettingeven:'tgGettingEven',climate:'tgClimateGap'};
const nodes={criteria:{value:'discovery'},basemap:{value:'Standard'},challengeBlurb:{},maplegend:{innerHTML:'OLD LEGEND',querySelector:()=>null}};
const document={getElementById:id=>nodes[id]||null};
const geActive=()=>overlay==='gettingeven',covOn=()=>overlay==='coverage',climateOn=()=>overlay==='climate';
const renderCellTable=()=>{},paintPopScore=()=>{},setGridLines=()=>{},renderGoalContext=()=>{},syncSharedCellUrl=()=>{},updateLegendLabels=()=>{};
const GE_PAL=['a','b','c'],GE_ALL='all';
let painted=input.previous,verified=false,resolve,reject;
const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
const setCoverage=()=>{if(painted==='coverage')painted=null;};
const setClimate=()=>{if(painted==='climate')painted=null;};
const recolour=()=>{painted=overlay==='discovery'?null:overlay;};
globalThis.optionalLayers={supports:()=>true,isDiscovery:()=>overlay==='discovery',load:()=>promise,
  discoveryLegend:()=> 'VERIFIED DISCOVERY',refresh:()=>{if(overlay==='discovery'&&verified)painted='discovery';}};
function snapshot(){return {overlay,painted,legend:nodes.maplegend.innerHTML};}
"""
    script += _function(source, "updateLegend") + _function(source, "setOverlay")
    script += r"""
(async()=>{
const pending=setOverlay('discovery');const before=snapshot();
if(input.outcome.startsWith('superseded'))await setOverlay('climate');
if(input.outcome.endsWith('failure'))reject(Error('HTTP 503'));
else{verified=true;resolve();}
await pending;console.log(JSON.stringify({before,after:snapshot()}));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = subprocess.run(
        ["node", "-e", script, json.dumps({"previous": previous, "outcome": outcome, "language": language})],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("previous", ["priority", "coverage", "climate", "gettingeven"])
@pytest.mark.parametrize("language", ["en", "fr"])
def test_pending_discovery_clears_previous_colours_and_explains_loading(previous, language):
    result = replay(previous, "success", language)
    assert result["before"]["overlay"] == "discovery"
    assert result["before"]["painted"] is None
    assert 'role="status"' in result["before"]["legend"]
    assert ("Loading" if language == "en" else "Chargement") in result["before"]["legend"]
    assert "VERIFIED DISCOVERY" not in result["before"]["legend"]
    assert result["after"] == {"overlay": "discovery", "painted": "discovery", "legend": "VERIFIED DISCOVERY"}


def test_rejected_discovery_restores_the_priority_map_and_legend():
    result = replay("climate", "failure")
    assert result["after"] == {"overlay": "priority", "painted": "priority", "legend": "PRIORITY LEGEND"}


@pytest.mark.parametrize("outcome", ["superseded", "superseded_failure"])
def test_late_discovery_cannot_repaint_a_newer_selection(outcome):
    result = replay("priority", outcome)
    assert result["after"]["overlay"] == result["after"]["painted"] == "climate"
    assert "VERIFIED DISCOVERY" not in result["after"]["legend"]
