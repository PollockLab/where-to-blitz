"""Published GE categories keep their identity when users filter source-count evidence."""

import json
import subprocess
from pathlib import Path

import pytest

from test_shared_context import _function

ROOT = Path(__file__).parent
MODULE = (ROOT / "webapp/gettingeven_evidence.js").read_text()
TEMPLATE = (ROOT / "webapp/index.html").read_text()


def run_js(action, *, viewer=False):
    setup = r"""
const crypto=require('node:crypto').webcrypto;
const hash=bytes=>require('node:crypto').createHash('sha256').update(bytes).digest('hex');
const bytes=image=>Buffer.from(JSON.stringify(image));
const counts=values=>({w:4,h:2,d:values.flatMap(n=>n===null?[0,0,0,0]:[Math.floor(n/65536),Math.floor(n/256)%256,n%256,255])});
const categories=image=>({w:4,h:2,d:image.d.flatMap((v,i)=>i%4===0?[i/4%6,0,0,image.d[i+3]]:[])});
const lattice=res=>({x0:0,y1:10000,res_m:res,ncol:4,nrow:2});
const images={},responses={},requests=[];
for(const res of [25000,5000]){
 images[res]=counts(res===5000?[0,1,5,20,100,458040,null,65536]:[0,20,1,5,100,458040,null,65536]);
 const category=categories(images[res]);
 responses['/counts_'+res+'.png']=bytes(images[res]);
 responses['/cluster_results/ca/gettingeven_grid_'+res+'m.png']=bytes(category);
}
const descriptor={schema_version:1,kind:'gettingeven-source-counts',encoding:'rgb24',source:'aggregate n_train, may be rounded density-derived proxies',tiers:{}};
for(const res of [25000,5000])descriptor.tiers[res]={path:'counts_'+res+'.png',sha256:hash(responses['/counts_'+res+'.png']),category_sha256:hash(responses['/cluster_results/ca/gettingeven_grid_'+res+'m.png']),lattice:lattice(res),observations_sha256:'a'.repeat(64)};
let active=true,fetchFailure=false,hold=null;
async function fetcher(url){requests.push(url);if(hold)await hold;if(fetchFailure)throw Error('offline');const body=responses[new URL(url).pathname];return{ok:!!body,status:body?200:404,arrayBuffer:async()=>body};}
const decode=async body=>{const image=JSON.parse(Buffer.from(body));image.d=Uint8Array.from(image.d);return image;};
const events=[];
let e=GettingEvenEvidence.create({descriptor,lattice,decode,fetcher,subtle:crypto.subtle,baseUrl:'https://example.org/map.html',active:()=>active,onChange:(res,phase)=>events.push([res,phase])});
"""
    script = MODULE + setup
    if viewer:
        script += TEMPLATE[TEMPLATE.index("const I18N=") : TEMPLATE.index("\nlet LANG=")]
        script += r"""
let LANG='en',overlay='gettingeven',_geLoading=false,_mapLoaded=true,geOpacity=.62,_shareAction=0,zoom=10;
const FILES={'All biodiversity':'all.json',Aves:'birds.json'},PRESETS=[{}],BB={minlat:-90,maxlat:90,minlon:-180,maxlon:180};
const state={taxon:'Aves'},GE={},GE_ALL='grey',GE_PAL=['a','b','c','d','e','f'],GE_UNSCORED=255;
const TIER_SWITCH_Z=9,LATTICE_X0=0,LATTICE_Y1=10000,LATTICE_W=20000,LATTICE_H=10000;
const geActive=()=>overlay==='gettingeven',covOn=()=>overlay==='coverage',climateOn=()=>overlay==='climate';
const activeCellResM=()=>zoom>=9?5000:25000;
const snapToLattice=(lat,lon,res)=>({col:lon,row:lat});
const esc=s=>s,gekey=(lat,lon)=>lat+','+lon;
const nodes={};
const document={baseURI:'https://example.org/map.html',getElementById:id=>nodes[id]||(nodes[id]={hidden:false,disabled:false,value:'1',textContent:'',setAttribute(){}}),createElement:()=>({getContext:()=>({drawImage(image){this.image=image;},getImageData(){return {data:this.image.d};}})})};
const createImageBitmap=async blob=>{const image=JSON.parse(await blob.text());return{width:image.w,height:image.h,d:Uint8Array.from(image.d),close(){}};};
const GE_EVIDENCE=descriptor,fetch=fetcher;
const markers=[{i:0,r:[0,1]},{i:1,r:[0,2]},{i:2,r:[0,3]}],cellStates={};
const setCellState=(id,fc,fo)=>cellStates[id]={fc,fo};
let popupPaints=0,sourcePaints=0,urlSyncs=0;
const applyCanadaMask=()=>{},paintPopScore=()=>popupPaints++,syncSharedCellUrl=()=>urlSyncs++;
const properties={},map={getZoom:()=>zoom,getLayer:id=>id==='grid5',getSource:()=>true,setPaintProperty:(id,key,value)=>properties[key]=value};
let refreshCells5=()=>sourcePaints++;
globalThis.optionalLayers={recentEnabled:()=>false};
"""
        for name in (
            "t",
            "geCat",
            "geColour",
            "paintGECells",
            "refreshGEEvidenceControls",
            "refreshGEEvidence",
            "repaintGEEvidence",
            "geEvidencePopup",
            "parseSharedContext",
            "sharedCellUrl",
        ):
            script += _function(TEMPLATE, name)
        script += "const actualCreate=GettingEvenEvidence.create;GettingEvenEvidence.create=options=>actualCreate({...options,lattice});\n"
        script += TEMPLATE[
            TEMPLATE.index("if(typeof GettingEvenEvidence!=='undefined')") : TEMPLATE.index(
                "// language toggle: seed"
            )
        ]
        script += "e=globalThis.geEvidence;\n"
    script += "\n(async()=>{" + action + "})().catch(error=>{console.error(error);process.exit(1);});"
    result = subprocess.run(["node"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_rgb24_preserves_zero_large_values_missing_cells_and_coordinates():
    result = run_js(
        "await e.load(5000);console.log(JSON.stringify({values:[e.countAt(5000,0,0),e.countAt(5000,1,0),e.countAt(5000,1,1),e.countAt(5000,2,1),e.countAt(5000,3,1),e.countAt(5000,4,0),e.countAt(5000,-1,0)],ready:e.ready(5000),requests:requests.length}));"
    )
    assert result == {"values": [0, 1, 458040, None, 65536, None, None], "ready": True, "requests": 2}


@pytest.mark.parametrize(
    "tamper",
    ["checksum", "category_checksum", "lattice", "dimensions", "alpha", "category_mask", "cross_origin"],
)
def test_unverified_or_misaligned_evidence_never_becomes_ready_and_retry_is_explicit(tamper):
    mutate = {
        "checksum": "descriptor.tiers[5000].sha256='b'.repeat(64);",
        "category_checksum": "descriptor.tiers[5000].category_sha256='b'.repeat(64);",
        "lattice": "descriptor.tiers[5000].lattice.x0=1;",
        "dimensions": "images[5000].w=3;responses['/counts_5000.png']=bytes(images[5000]);descriptor.tiers[5000].sha256=hash(responses['/counts_5000.png']);",
        "alpha": "images[5000].d[3]=128;responses['/counts_5000.png']=bytes(images[5000]);descriptor.tiers[5000].sha256=hash(responses['/counts_5000.png']);",
        "category_mask": "images[5000].d[3]=0;responses['/counts_5000.png']=bytes(images[5000]);descriptor.tiers[5000].sha256=hash(responses['/counts_5000.png']);",
        "cross_origin": "descriptor.tiers[5000].path='https://elsewhere.example/counts.png';",
    }[tamper]
    result = run_js(
        mutate
        + "await e.load(5000);const before=requests.length;await e.load(5000);console.log(JSON.stringify({ready:e.ready(5000),state:e.state(5000),count:e.countAt(5000,0,0),noLoop:requests.length===before}));"
    )
    assert result == {"ready": False, "state": "error", "count": None, "noLoop": True}


def test_loading_default_parity_nondefault_abstention_retry_and_mode_guards():
    result = run_js(
        "let release;hold=new Promise(resolve=>release=resolve);const pending=e.load(5000);const initial={state:e.state(5000),shown:e.visible(5000,0,0),rejected:!e.choose(20,5000)};e.restore(20);const withheld=!e.visible(5000,1,0);release();await pending;hold=null;active=false;const inactive=!e.choose(5,5000);active=true;const accepted=e.choose(5,5000);fetchFailure=true;await e.load(25000);const failed=e.state(25000);fetchFailure=false;await e.load(25000,true);console.log(JSON.stringify({initial,withheld,inactive,accepted,failed,recovered:e.ready(25000),cutoff:e.cutoff()}));"
    )
    assert result == {
        "initial": {"state": "loading", "shown": True, "rejected": True},
        "withheld": True,
        "inactive": True,
        "accepted": True,
        "failed": "error",
        "recovered": True,
        "cutoff": 5,
    }


@pytest.mark.parametrize("language", ["en", "fr"])
def test_actual_controls_popup_and_paints_use_same_tier_all_taxa_counts(language):
    result = run_js(
        "LANG="
        + json.dumps(language)
        + ";refreshGEEvidenceControls();const pending=nodes.geCountCutoff.disabled;await e.load(5000);await e.load(25000);const fine=geEvidencePopup(0,1,5000),coarse=geEvidencePopup(0,1,25000);const before=sourcePaints;e.choose(20,5000);repaintGEEvidence();const expression=properties['fill-opacity'];zoom=3;repaintGEEvidence();console.log(JSON.stringify({pending,enabled:!nodes.geCountCutoff.disabled,fine,coarse,expression,noRebuild:sourcePaints===before,states:cellStates,reference:nodes.geReference.textContent,status:nodes.geEvidenceStatus.textContent}));",
        viewer=True,
    )
    assert result["pending"] and result["enabled"] and result["noRebuild"]
    assert ": 1 (" in result["fine"] and ": 20 (" in result["coarse"]
    assert "aggregate" in result["fine"] if language == "en" else "agrégée" in result["fine"]
    assert result["expression"] == ["case", [">=", ["coalesce", ["get", "geCount"], -1], 20], 0.62, 0]
    assert (
        result["states"]["0"]["fo"] == 0.62
        and result["states"]["1"]["fo"] == result["states"]["2"]["fo"] == 0
    )
    assert "25 km" in result["reference"] and "20+" in result["status"]


def test_shared_filter_validates_and_stays_out_of_other_goals():
    result = run_js(
        "e.restore(20);const valid=parseSharedContext('?goal=ge&ge_count=20'),invalid=parseSharedContext('?goal=ge&ge_count=2'),other=parseSharedContext('?goal=0&ge_count=20');console.log(JSON.stringify({valid:valid.geCount,invalid:invalid.geCount||1,other:other.geCount||1,ge:sharedCellUrl(null,'Aves','ge','standard','https://example.org/map.html'),priority:sharedCellUrl(null,'Aves','0','standard','https://example.org/map.html?ge_count=20')}));",
        viewer=True,
    )
    assert result["valid"] == 20 and result["invalid"] == result["other"] == 1
    assert "ge_count=20" in result["ge"] and "ge_count" not in result["priority"]


def test_failed_inactive_completion_cannot_paint_a_newer_goal():
    result = run_js(
        "let release;hold=new Promise(resolve=>release=resolve);const pending=e.load(5000);overlay='climate';const before={popupPaints,sourcePaints};release();await pending;console.log(JSON.stringify({before,after:{popupPaints,sourcePaints},hidden:nodes.geEvidenceControls.hidden}));",
        viewer=True,
    )
    assert result["before"] == result["after"] and result["hidden"]


def test_native_control_rejects_pending_input_and_keeps_focus_across_language_refresh():
    result = run_js(
        "nodes.geCountCutoff=document.getElementById('geCountCutoff');document.activeElement=nodes.geCountCutoff;nodes.geCountCutoff.value='20';nodes.geCountCutoff.onchange();const pending=e.cutoff();await e.load(5000);nodes.geCountCutoff.value='20';nodes.geCountCutoff.onchange();LANG='fr';refreshGEEvidenceControls();console.log(JSON.stringify({pending,chosen:e.cutoff(),focused:document.activeElement===nodes.geCountCutoff,value:nodes.geCountCutoff.value,status:nodes.geEvidenceStatus.textContent}));",
        viewer=True,
    )
    assert result["pending"] == 1 and result["chosen"] == 20
    assert result["focused"] and result["value"] == "20" and "Seuil" in result["status"]


def test_null_descriptor_keeps_default_map_and_disables_the_optional_filter():
    result = run_js(
        "descriptor.kind='unavailable';e.restore(20);console.log(JSON.stringify({cutoff:e.cutoff(),state:e.state(5000),shown:e.visible(5000,1,0),choose:e.choose(20,5000)}));"
    )
    assert result == {"cutoff": 1, "state": "unavailable", "shown": True, "choose": False}


def test_actual_fine_cell_geometry_and_click_keys_survive_cutoff_changes():
    action = (
        r"""
await e.load(5000);
let _c5seq=0,DRAWN5=null,STRETCH=null,data=null;
const LAEA={fwd:(lat,lon)=>[lon,lat],inv:(x,y)=>[y,x]};
const bounds={getWest:()=>0,getEast:()=>19999,getSouth:()=>0,getNorth:()=>10000};
map.getBounds=()=>bounds;map.getSource=()=>({setData:value=>{data=value;}});
const loadGEVals=async()=>e.categoryImage(5000),loadVals=()=>{throw Error('priority values loaded');};
const updateLegendLabels=()=>{},makeStretch=()=>null,valG=()=>-1;
"""
        + "refreshCells5=("
        + _function(TEMPLATE, "refreshCells5")
        + ");"
        + r"""
await refreshCells5();const before={keys:[...DRAWN5],geometry:data.features.map(f=>f.geometry),counts:data.features.map(f=>f.properties.geCount)};
e.choose(20,5000);repaintGEEvidence();const after={keys:[...DRAWN5],geometry:data.features.map(f=>f.geometry),counts:data.features.map(f=>f.properties.geCount)};
console.log(JSON.stringify({before,after,opacity:properties['fill-opacity']}));
"""
    )
    result = run_js(action, viewer=True)
    assert result["before"] == result["after"]
    assert len(result["after"]["keys"]) == 7 and "1,0" in result["after"]["keys"]
    assert result["after"]["counts"] == [0, 1, 5, 20, 100, 458040, 65536]
    assert result["opacity"][-1] == 0


def test_old_tier_completion_updates_popup_without_repainting_current_tier():
    result = run_js(
        "await e.load(25000);let release;hold=new Promise(resolve=>release=resolve);const pending=e.load(5000);zoom=3;const before={popupPaints,sourcePaints};release();await pending;console.log(JSON.stringify({before,after:{popupPaints,sourcePaints}}));",
        viewer=True,
    )
    assert result["after"]["sourcePaints"] == result["before"]["sourcePaints"]
    assert result["after"]["popupPaints"] == result["before"]["popupPaints"] + 1


def test_failed_nondefault_filter_withholds_fill_and_never_claims_all_recommendations_are_shown():
    result = run_js(
        "e.restore(20);fetchFailure=true;await e.load(5000);console.log(JSON.stringify({cutoff:e.cutoff(),ready:e.ready(5000),shown:e.visible(5000,1,0),popup:geEvidencePopup(0,1,5000),status:nodes.geEvidenceStatus.textContent,retry:!nodes.geEvidenceRetry.hidden}));",
        viewer=True,
    )
    assert result["cutoff"] == 20 and not result["ready"] and not result["shown"]
    assert "could not be verified" in result["popup"] and "could not be verified" in result["status"]
    assert "All published recommendations are shown" not in result["popup"] and result["retry"]


def test_unavailable_filtered_view_can_return_to_the_published_map_without_network():
    result = run_js(
        "e.restore(20);fetchFailure=true;await e.load(5000);const before={cutoff:e.cutoff(),reset:!nodes.geEvidenceReset.hidden,shown:e.visible(5000,1,0)};nodes.geEvidenceReset.onclick();console.log(JSON.stringify({before,after:{cutoff:e.cutoff(),reset:!nodes.geEvidenceReset.hidden,shown:e.visible(5000,1,0),urlSyncs}}));",
        viewer=True,
    )
    assert result["before"] == {"cutoff": 20, "reset": True, "shown": False}
    assert result["after"]["cutoff"] == 1 and result["after"]["shown"]
    assert not result["after"]["reset"] and result["after"]["urlSyncs"] > 0


def test_default_category_loading_does_not_wait_for_optional_count_evidence():
    function = _function(TEMPLATE, "loadGEVals")
    result = run_js(
        "globalThis.geEvidence=e;const GE_VALS={};const DATA_DIR='cluster_results/ca/';const cachedPng=async()=>({published:true});"
        + function
        + "hold=new Promise(()=>{});const value=await Promise.race([loadGEVals(5000),new Promise(resolve=>setTimeout(()=>resolve({blocked:true}),20))]);console.log(JSON.stringify(value));"
    )
    assert result == {"published": True}


def test_language_switch_refreshes_accessible_layer_context_without_changing_filter_or_fetching():
    result = run_js(
        "const window={},legendTapKey=()=>'',relabelPlaceFinder=()=>{},renderGoalContext=()=>{};"
        "globalThis.optionalLayers.refresh=()=>{};"
        + _function(TEMPLATE, "renderCellTable")
        + _function(TEMPLATE, "relabelDynamic")
        + "await e.load(5000);e.restore(20);const before=requests.length,text=[];"
        "for(const language of ['en','fr','en']){LANG=language;relabelDynamic();text.push(nodes.celltable.innerHTML);}"
        "console.log(JSON.stringify({text,overlay,cutoff:e.cutoff(),requests:requests.length-before}));",
        viewer=True,
    )
    assert "no national priority ranking" in result["text"][0]
    assert "classement national" in result["text"][1]
    assert result["text"][2] == result["text"][0]
    assert result["overlay"] == "gettingeven" and result["cutoff"] == 20
    assert result["requests"] == 0
