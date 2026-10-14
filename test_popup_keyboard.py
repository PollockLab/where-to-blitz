"""Popup focus belongs to the invoker, including when ranking rows are replaced."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(src, name):
    match = re.search(r"function " + name + r"\([^)]*\)\{", src)
    assert match, name
    depth = 1
    for end in range(match.end(), len(src)):
        depth += (src[end] == "{") - (src[end] == "}")
        if depth == 0:
            return src[match.start() : end + 1]
    raise AssertionError(f"unterminated function {name}")


def _run(code):
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("\nlet LANG=")]
    script += """
let LANG='en',curPopup=null,_reselect=false,destMarker={},destCell={},cleared=0;
const state={view:'explore'},rows=[],controls={};
const document={activeElement:null,body:null,getElementById:()=>({querySelectorAll:()=>rows}),
  querySelectorAll:selector=>controls[selector]||[]};
function actor(name,{connected=true,visible=true,focusable=true,lat=null,lon=null}={}){
  return {name,isConnected:connected,visible,focusable,dataset:{la:lat,lo:lon},labels:{},
    closest(){return lat===null?null:this;},getClientRects(){return this.visible&&this.isConnected?[{}]:[];},
    focus(){if(this.focusable&&this.isConnected&&this.visible)document.activeElement=this;},
    setAttribute(key,value){this.labels[key]=value;}};
}
document.body=actor('body',{focusable:false});document.activeElement=document.body;
const canvas=actor('canvas'),map={getCanvas:()=>canvas};
function getComputedStyle(el){return {visibility:el.visible?'visible':'hidden',display:el.visible?'block':'none'};}
function clearSelection(){cleared++;destMarker=null;destCell=null;}
function popup(){const handlers={};return {handlers,on(name,fn){handlers[name]=fn;return this;},
  getElement(){return {contains:el=>el===close};}};}
const close=actor('close');
const row=actor('row',{lat:'45.5',lon:'-73.6'});rows.push(row);
"""
    script += "\n" + "\n".join(
        _function(src, name)
        for name in (
            "t",
            "localizeMapControls",
            "popupFocusOrigin",
            "focusPopupTarget",
            "restorePopupFocus",
            "onPopupOpen",
            "onPopupClose",
            "wirePopup",
        )
    )
    script += """
function openFromRow(){row.focus();const p=wirePopup(popup());close.focus();p.handlers.open();return p;}
function closeFocusedPopup(p){document.activeElement=document.body;p.handlers.close();}
"""
    result = subprocess.run(["node", "-e", script + "\n" + code], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_close_returns_to_invoker_captured_before_library_focus():
    result = _run(
        "const p=openFromRow();closeFocusedPopup(p);"
        "console.log(JSON.stringify({focus:document.activeElement.name,cleared}));"
    )
    assert result == {"focus": "row", "cleared": 1}


def test_replaced_rank_row_is_resolved_by_cell_coordinates():
    result = _run(
        """
const p=openFromRow();row.isConnected=false;
rows.splice(0,1,actor('replacement',{lat:'45.5',lon:'-73.6'}));
closeFocusedPopup(p);console.log(JSON.stringify(document.activeElement.name));
"""
    )
    assert result == "replacement"


@pytest.mark.parametrize("change", ["row.isConnected=false;rows.length=0;", "row.visible=false;"])
def test_unavailable_invoker_falls_back_to_map_canvas(change):
    result = _run(
        "const p=openFromRow();"
        + change
        + "closeFocusedPopup(p);console.log(JSON.stringify(document.activeElement.name));"
    )
    assert result == "canvas"


def test_popup_reselection_does_not_restore_old_focus_or_clear_new_selection():
    result = _run(
        "const p=openFromRow();_reselect=true;closeFocusedPopup(p);"
        "console.log(JSON.stringify({focus:document.activeElement.name,cleared}));"
    )
    assert result == {"focus": "body", "cleared": 0}


def test_late_close_from_old_popup_does_not_steal_new_popup_focus():
    result = _run(
        "const old=openFromRow(),current=wirePopup(popup());current.handlers.open();old.handlers.close();"
        "console.log(JSON.stringify({focus:document.activeElement.name,cleared,current:curPopup===current}));"
    )
    assert result == {"focus": "close", "cleared": 0, "current": True}


def test_close_preserves_focus_on_another_control():
    result = _run(
        "const p=openFromRow();const control=actor('control');control.focus();p.handlers.close();"
        "console.log(JSON.stringify({focus:document.activeElement.name,cleared}));"
    )
    assert result == {"focus": "control", "cleared": 1}


def test_map_control_and_popup_labels_follow_language_changes():
    result = _run(
        """
for(const selector of ['.maplibregl-ctrl-zoom-in','.maplibregl-ctrl-zoom-out',
  '.maplibregl-ctrl-attrib-button','.maplibregl-popup-close-button'])controls[selector]=[actor(selector)];
LANG='fr';localizeMapControls();const french=Object.values(controls).map(a=>a[0].labels['aria-label']);
LANG='en';localizeMapControls();const english=Object.values(controls).map(a=>a[0].labels['aria-label']);
console.log(JSON.stringify({french,english}));
"""
    )
    assert result["french"] == [
        "Agrandir la carte",
        "Réduire la carte",
        "Afficher l'attribution",
        "Fermer la fenêtre de la cellule",
    ]
    assert result["english"] == ["Zoom in", "Zoom out", "Toggle attribution", "Close popup"]
