"""Native popup disclosures retain state while their evidence containers repaint."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_goal_context import _function

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(code):
    src = TEMPLATE.read_text()
    script = """
const created=[];
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.innerHTML='';this.dataset={};this.open=false;this.isConnected=true;created.push(this);}
  appendChild(child){this.children.push(child);child.parentElement=this;return child;}
  focus(){document.activeElement=this;}
  setAttribute(key,value){this[key]=value;}
}
const document={createElement:tag=>new Element(tag),activeElement:null};
const state={taxon:'All biodiversity'},markers=[{r:[],t:0.8}],overlay='priority';
const map={getZoom:()=>3,getContainer:()=>({clientHeight:400}),project:()=>({x:0,y:0}),unproject:p=>p,jumpTo(){}};
const activeCellResM=()=>25000,snapToLattice=(lat,lon)=>({lat,lon}),parentMarker=()=>markers[0];
const caOnlyActive=()=>true,US_CELLS=new Set(),gekey=()=>'',DRAWN5=null;
const clearRoute=()=>{},syncSharedCellUrl=()=>{},setDestCell=()=>{},latKey=()=>'';
const t=id=>id,popScoreHtml=()=>'cell facts',contribStr=()=>'components',openHowSection=()=>{};
const popupActionsHtml=()=>'record actions';
let _selectionSeq=0,_reselect=false,_sharedCell=null,_popScoreEl=null,_popComponentsEl=null,destMarker;
let content,popupOptions;
const maplibregl={Popup:class{constructor(options){popupOptions=options;}setDOMContent(el){content=el;return this;}}},wirePopup=p=>p;
const destMarkerAt=()=>({addTo(){return this;},setPopup(){return this;},togglePopup(){}});
let refs;
function fetchProspects(lat,lon,where,opts){refs=opts;}
"""
    script += _function(src, "exploreCell")
    script += "\nexploreCell(49,-123);\n" + code
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_species_precede_closed_group_and_component_disclosures():
    result = _run(
        "console.log(JSON.stringify({order:content.children.map(el=>el.id||el.className||el.tag),"
        "disclosures:created.filter(el=>el.tag==='details').map(el=>({open:el.open,ids:el.children.map(c=>c.id||c.tag)}))}));"
    )
    assert result["order"][:3] == ["popscore", "popsp", "popactions"]
    assert len(result["disclosures"]) == 2
    assert all(not item["open"] for item in result["disclosures"])
    assert result["disclosures"][0]["ids"] == ["summary", "popgaps"]


def test_explore_popup_disables_library_focus_before_the_open_event():
    assert _run("console.log(JSON.stringify(popupOptions.focusAfterOpen===false));") is True


def test_evidence_repaint_keeps_disclosure_and_summary_focus():
    result = _run(
        "const details=created.find(el=>el.tag==='details');if(details){"
        "details.open=true;const summary=details.children[0];summary.focus();"
        "refs.gapsEl.innerHTML='French evidence';refs.spEl.innerHTML='New species group';"
        "_popScoreEl.innerHTML='New goal';"
        "console.log(JSON.stringify({open:details.open,focused:document.activeElement===summary,"
        "same:refs.gapsEl.parentElement===details}));}else console.log('{}');"
    )
    assert result == {"open": True, "focused": True, "same": True}


def _run_help(code, responses=()):
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("\nlet LANG=")]
    script += src[src.index("function t(") : src.index("// indexed-name helpers")]
    script += src[src.index("const esc=") : src.index("const safeImg=")]
    script += """
let LANG='en',_howFocusReturn=null,_conservationMeta=null,_conservationLoad=null,_conservationError=false;
const DATA_DIR='cluster_results/ca/',nodes={},calls=[];
const responses=JSON.parse(process.argv[1]);
function element(name){return {name,isConnected:true,innerHTML:'',dataset:{},open:false,
  classes:new Set(),getClientRects:()=>[{}],focus(){document.activeElement=this;},
  setAttribute(){},scrollIntoView(){},querySelector(selector){
    if(selector==='summary')return this.summary||(this.summary=element(name+'-summary'));
    if(selector==='[data-metadata-retry]'&&this.innerHTML.includes('data-metadata-retry'))return this.retry||(this.retry={});
    return null;},
  classList:{toggle(k,on){on?nodes.howpanel.classes.add(k):nodes.howpanel.classes.delete(k);}}};}
for(const id of ['howpanel','howbtn','how-evidence','conservationinfo'])nodes[id]=element(id);
const document={getElementById:id=>nodes[id],activeElement:null};
const renderGoalContext=()=>{},setPlaceFinderOpen=()=>{};
const fetchT=async(url)=>{calls.push(url);const response=responses.shift();
  if(response.error)throw Error(response.error);
  return {ok:true,json:async()=>response};};
"""
    for name in (
        "conservationInfoHtml",
        "renderConservationInfo",
        "loadConservationInfo",
        "setHowOpen",
        "openHowSection",
    ):
        script += _function(src, name)
    script += "\n(async()=>{" + code + "})().catch(e=>{console.error(e);process.exit(1);});"
    result = subprocess.run(
        ["node", "-e", script, json.dumps(responses)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_help_opens_relevant_section_and_returns_focus_to_popup_link():
    result = _run_help(
        "const link=element('popup-help');openHowSection('how-evidence',link);"
        "const opened={section:nodes['how-evidence'].open,panel:nodes.howpanel.classes.has('open'),"
        "focus:document.activeElement.name};setHowOpen(false);"
        "await _conservationLoad;console.log(JSON.stringify({opened,closedFocus:document.activeElement.name}));",
        [{"taxonomy": {}, "shipping_policy": {}}],
    )
    assert result == {
        "opened": {"section": True, "panel": True, "focus": "how-evidence-summary"},
        "closedFocus": "popup-help",
    }


def test_conservation_help_uses_returned_metadata_and_repaints_in_french():
    metadata = {
        "doi": "10.12345/download-fixture",
        "taxonomy": {"title": "Fixture taxonomy", "shortName": "release-next", "doi": "10.12345/taxonomy"},
        "shipping_policy": {
            "applied": True,
            "method": "allowlist",
            "required_polygon_listing_ids": ["a", "b"],
            "dropped_listing_ids": ["c"],
        },
    }
    result = _run_help(
        "const pending=loadConservationInfo();const loading=nodes.conservationinfo.innerHTML;"
        "await pending;const english=nodes.conservationinfo.innerHTML;LANG='fr';renderConservationInfo();"
        "console.log(JSON.stringify({loading,english,french:nodes.conservationinfo.innerHTML,calls:calls.length}));",
        [metadata],
    )
    assert "Loading" in result["loading"]
    assert "release-next" in result["english"] and "10.12345/download-fixture" in result["english"]
    assert "Species-level allowlist" in result["english"]
    assert "2 listing safeguards" in result["english"] and "1 listings excluded" in result["english"]
    assert "Liste d'inclusion" in result["french"] and "2 garde-fous" in result["french"]
    assert result["calls"] == 1


def test_conservation_metadata_failure_offers_retry_without_blocking_help():
    result = _run_help(
        "openHowSection('how-evidence',element('link'));await _conservationLoad;"
        "const failed=nodes.conservationinfo.innerHTML;await nodes.conservationinfo.retry.onclick();"
        "console.log(JSON.stringify({failed,panel:nodes.howpanel.classes.has('open'),"
        "retried:nodes.conservationinfo.innerHTML,calls:calls.length}));",
        [{"error": "timeout"}, {"taxonomy": {}, "shipping_policy": {}}],
    )
    assert "data-metadata-retry" in result["failed"]
    assert result["panel"] is True
    assert "Not recorded in the shipped metadata" in result["retried"]
    assert result["calls"] == 2
