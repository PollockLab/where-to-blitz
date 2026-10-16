"""Historical evidence preserves recording choices and explains its available scope."""

from test_shared_context import TEMPLATE, _function, _run


def run_controls(action):
    source = TEMPLATE.read_text()
    setup = """
const OPTIONAL_LAYERS={discovery:{scope:{period:{start:'2025-01-01',end:'2025-12-31'}}}};
globalThis.optionalLayers={group:()=> 'All biodiversity',supports:g=>g==='All biodiversity',
  isDiscovery:()=>overlay==='discovery',load:()=>Promise.resolve(),refresh(){}};
var _evidenceReturn=null,_evidenceSeq=0;
for(const id of ['evidenceControls','evidenceLayer','priorityControls','historicalScope','sheetSummary'])nodes[id]={};
function snapshot(){return {overlay,taxon:state.taxon,goal:critSel.value,
  display:nodes.evidenceLayer.value,hidden:nodes.priorityControls.hidden,
  groupDisabled:taxonSel.disabled,goalDisabled:critSel.disabled,scopeHidden:nodes.historicalScope.hidden};}
async function birdGoal(){taxonSel.value='Aves';await taxonSel.onchange();critSel.value='2';critSel.onchange();}
"""
    setup += _function(source, "syncEvidenceControls") + _function(source, "selectEvidenceLayer")
    return _run("", setup + action, controls=True, real_overlay=True)


def test_historical_display_explains_scope_and_restores_the_previous_group_and_goal():
    result = run_controls(
        """
await birdGoal();await selectEvidenceLayer('discovery');syncEvidenceControls();const historical=snapshot();
await selectEvidenceLayer('goal');syncEvidenceControls();console.log(JSON.stringify({historical,restored:snapshot()}));
"""
    )
    assert result["historical"] == {
        "overlay": "discovery",
        "taxon": "All biodiversity",
        "goal": "discovery",
        "display": "discovery",
        "hidden": True,
        "groupDisabled": True,
        "goalDisabled": True,
        "scopeHidden": False,
    }
    assert result["restored"] == {
        "overlay": "priority",
        "taxon": "Aves",
        "goal": "2",
        "display": "goal",
        "hidden": False,
        "groupDisabled": False,
        "goalDisabled": False,
        "scopeHidden": True,
    }


def test_newer_goal_selection_survives_delayed_historical_group_loading():
    result = run_controls(
        """
await birdGoal();const normal=loadGroup;let release;
loadGroup=g=>g==='All biodiversity'?new Promise(resolve=>{release=resolve;}):normal(g);
const pending=selectEvidenceLayer('discovery');critSel.value='1';critSel.onchange();
release();await pending;syncEvidenceControls();console.log(JSON.stringify(snapshot()));
"""
    )
    assert result["overlay"] == "priority"
    assert result["goal"] == "1"
    assert result["display"] == "goal" and not result["groupDisabled"]


def test_failed_historical_group_request_preserves_the_committed_recording_context():
    result = run_controls(
        """
await birdGoal();loadGroup=async()=>{throw Error('offline');};
await selectEvidenceLayer('discovery');syncEvidenceControls();console.log(JSON.stringify(snapshot()));
"""
    )
    assert result["taxon"] == "Aves" and result["goal"] == "2"
    assert result["overlay"] == "priority" and not result["hidden"]


def test_newer_goal_survives_delayed_return_to_the_previous_species_group():
    result = run_controls(
        """
await birdGoal();await selectEvidenceLayer('discovery');const normal=loadGroup;let release;
loadGroup=g=>g==='Aves'?new Promise(resolve=>{release=resolve;}):normal(g);
const pending=selectEvidenceLayer('goal');critSel.value='climate';critSel.onchange();
release();await pending;syncEvidenceControls();console.log(JSON.stringify(snapshot()));
"""
    )
    assert result["overlay"] == "climate" and result["goal"] == "climate"


def test_legacy_historical_link_can_return_to_the_default_recording_goal():
    result = run_controls(
        """
overlay='discovery';critSel.value='discovery';await selectEvidenceLayer('goal');syncEvidenceControls();
console.log(JSON.stringify(snapshot()));
"""
    )
    assert result["overlay"] == "priority" and result["goal"] == "0"
    assert not result["hidden"] and result["scopeHidden"] is True


def test_unconfigured_viewer_hides_the_historical_display_control():
    result = run_controls(
        """
delete globalThis.optionalLayers;syncEvidenceControls();
console.log(JSON.stringify({hidden:nodes.evidenceControls.hidden,groupDisabled:taxonSel.disabled}));
"""
    )
    assert result == {"hidden": True, "groupDisabled": False}


def test_other_recording_overlay_restores_without_becoming_a_numeric_goal():
    result = run_controls(
        """
critSel.value='climate';critSel.onchange();await selectEvidenceLayer('discovery');
await selectEvidenceLayer('goal');console.log(JSON.stringify({overlay,goal:critSel.value}));
"""
    )
    assert result == {"overlay": "climate", "goal": "climate"}
