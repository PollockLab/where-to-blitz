"""Keyboard cell choices follow Explore's viewport without changing national scores."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_shared_context import _function

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _render(action="", lang="en"):
    source = TEMPLATE.read_text()
    script = source[source.index("const I18N=") : source.index("let LANG=")]
    script += f"\nlet LANG={json.dumps(lang)};\n"
    script += _function(source, "t")
    script += """
let _mapLoaded=true,overlay='priority',_usFailed=false,US_CELLS=new Set();
const state={view:'explore'},gekey=(lat,lon)=>lat.toFixed(3)+','+lon.toFixed(3),caOnlyActive=()=>true;
const markers=[{r:[45.25,-75.5],t:0.24},{r:[45.75,-75],t:0.56},{r:[82,-80],t:1}];
const table={innerHTML:'',querySelectorAll:()=>[]},document={getElementById:()=>table};
let contains=([lon,lat])=>lat>=45&&lat<=46&&lon>=-76&&lon<=-74,parent=markers[0];
const map={getBounds:()=>({contains:p=>contains(p)}),getCenter:()=>({lat:45.4,lng:-75.4})};
const parentMarker=()=>parent;
const getComputedStyle=()=>({visibility:'visible',display:'block'});
"""
    script += _function(source, "eligibleMarkers")
    script += _function(source, "focusPopupTarget") + _function(source, "restorePopupFocus")
    script += _function(source, "renderCellTable")
    script += (
        action
        + "\nrenderCellTable();console.log(JSON.stringify({html:table.innerHTML,scores:markers.map(m=>m.t),focus:document.activeElement?.name}));"
    )
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_explore_lists_visible_cells_and_keeps_national_percentiles(lang):
    result = _render(lang=lang)
    assert 'data-la="82"' not in result["html"]
    assert result["html"].index('data-la="45.75"') < result["html"].index('data-la="45.25"')
    assert "56/100" in result["html"] and "24/100" in result["html"]
    assert result["scores"] == [0.24, 0.56, 1]
    assert ("national percentiles" if lang == "en" else "centiles nationaux") in result["html"]


def test_panning_replaces_the_choices_without_recomputing_scores():
    result = _render("contains=([lon,lat])=>lat>80;")
    assert 'data-la="82"' in result["html"]
    assert 'data-la="45.25"' not in result["html"]
    assert result["scores"] == [0.24, 0.56, 1]


def test_fine_viewport_offers_its_enclosing_cell_when_no_centres_are_visible():
    result = _render("contains=()=>false;")
    assert 'data-la="45.25"' in result["html"]
    assert 'data-la="82"' not in result["html"]


@pytest.mark.parametrize("change", ["parent=null;", "US_CELLS.add(gekey(...parent.r));", "parent.t=null;"])
def test_empty_view_does_not_offer_a_remote_masked_or_unranked_fallback(change):
    result = _render("contains=()=>false;" + change)
    assert "data-la=" not in result["html"]
    assert "zoom out" in result["html"]


def test_country_eligibility_still_applies_to_visible_cells():
    result = _render("US_CELLS.add(gekey(...markers[1].r));")
    assert 'data-la="45.75"' not in result["html"]
    assert 'data-la="45.25"' in result["html"]


@pytest.mark.parametrize("overlay", ["climate", "coverage", "gettingeven"])
def test_layers_without_a_priority_rank_do_not_publish_nearby_scalar_scores(overlay):
    result = _render(f"overlay={json.dumps(overlay)};")
    assert "data-la=" not in result["html"]
    assert "no national priority ranking" in result["html"]


def test_optional_plan_keeps_its_existing_national_list():
    result = _render("state.view='plan';")
    assert 'data-la="82"' in result["html"]
    assert result["html"].index('data-la="82"') < result["html"].index('data-la="45.75"')


def test_pending_boundary_evidence_takes_precedence_over_nearby_choices():
    result = _render("US_CELLS=null;")
    assert "data-la=" not in result["html"]
    assert "boundary data before ranking" in result["html"]


@pytest.mark.parametrize("pan_away", [False, True])
def test_list_refresh_restores_its_focused_cell_or_the_map_when_it_leaves_view(pan_away):
    action = """
function actor(name,lat,lon){return {name,isConnected:true,dataset:{la:lat,lo:lon},
  closest(){return this;},getClientRects(){return this.isConnected?[{}]:[];},focus(){document.activeElement=this;}};}
const old=actor('old row','45.25','-75.5'),canvas=actor('map'),body=actor('body');let rows=[];
document.activeElement=old;document.body=body;map.getCanvas=()=>canvas;
table.querySelectorAll=()=>rows;
Object.defineProperty(table,'innerHTML',{get(){return this.html;},set(html){
  this.html=html;old.isConnected=false;document.activeElement=body;
  rows=[...html.matchAll(/data-la="([^"]+)" data-lo="([^"]+)"/g)].map(m=>actor('new row',m[1],m[2]));
}});
"""
    if pan_away:
        action += "contains=([lon,lat])=>lat>80;"
    result = _render(action)
    assert result["focus"] == ("map" if pan_away else "new row")
