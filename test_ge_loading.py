"""Cold GE requests never reuse another goal's colours or invent grey categories."""

import pytest

from test_ge_opacity import run_opacity
from test_shared_context import TEMPLATE, _function


def run_loading(action, language="en"):
    source = TEMPLATE.read_text()
    setup = """
const GE={},GE_PAL=['verified-category'],GE_ALL='grey',DATA_DIR='';
let finishGE,failGE,requests=0,GE_PRIORITY_LEGEND='PRIORITY LEGEND';
const fetchT=()=>{requests++;return new Promise((yes,no)=>{finishGE=yes;failGE=no;});};
GE_LOADED=false;nodes.maplegend={innerHTML:'PRIORITY LEGEND',querySelector:()=>null};
function frame(){return {...snapshot(),disabled:!!nodes.baseop.disabled,opacity:geOpacity,
  climate:!!layers['climate-gap'],density:!!layers.cov,priority:!!layers.grid25,
  finePresent:!!layers.grid5,category:cellStates[0]?.fc,legend:nodes.maplegend.innerHTML};}
function finish(){finishGE({json:async()=>({gettingeven:[[45,-75,0,1]]})});}
"""
    for name in ("loadGE", "geColour", "updateLegend"):
        setup += name + "=(" + _function(source, name) + ");\n"
    return run_opacity(setup + action, language)


@pytest.mark.parametrize("previous", ["priority", "coverage", "climate"])
@pytest.mark.parametrize("zoom", [3, 10])
@pytest.mark.parametrize("language", ["en", "fr"])
def test_cold_ge_clears_old_layers_and_disables_its_correctly_labelled_opacity(previous, zoom, language):
    result = run_loading(
        f"zoom={zoom};await setOverlay('{previous}');const pending=setOverlay('gettingeven');"
        "const loading=frame();input(.31);setGEOpacity(.45);recolour();const during=frame();"
        "finish();await pending;console.log(JSON.stringify({loading,during,ready:frame()}));",
        language,
    )
    for frame in (result["loading"], result["during"]):
        assert not any(frame[key] for key in ("climate", "density", "priority", "finePresent"))
        assert frame["coarse"] == 0 and frame["category"] is None
        assert frame["disabled"] and frame["opacity"] == 0.62 and frame["text"] == "62%"
        assert ("Getting Even" if language == "en" else "Combler l'écart") in frame["label"]
        assert 'role="status"' in frame["legend"]
        assert ("Loading" if language == "en" else "Chargement") in frame["legend"]
        assert "grey" not in frame["legend"]
    ready = result["ready"]
    assert result["loading"]["value"] == 0.62
    assert not ready["disabled"] and ready["fine"] == ready["value"] == 0.62
    assert ready["coarse"] == (0.62 if zoom == 3 else 0)
    assert ready["category"] == "verified-category" and 'role="status"' not in ready["legend"]


def test_cached_ge_restores_immediately_with_remembered_opacity_and_no_new_request():
    result = run_loading("""
const pending=setOverlay('gettingeven');finish();await pending;input(.31);
await setOverlay('climate');input(.55);const cached=setOverlay('gettingeven');
const immediate=frame();await cached;console.log(JSON.stringify({immediate,requests,climateOpacity}));
""")
    assert result["requests"] == 1 and result["climateOpacity"] == 0.55
    assert result["immediate"]["value"] == result["immediate"]["coarse"] == 0.31
    assert not result["immediate"]["disabled"] and 'role="status"' not in result["immediate"]["legend"]


@pytest.mark.parametrize("superseded", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_ge_completion_preserves_failure_fallback_and_newer_selection(superseded, failure):
    result = run_loading(
        "const pending=setOverlay('gettingeven');"
        + ("await setOverlay('climate');" if superseded else "")
        + ("failGE(Error('HTTP 503'));" if failure else "finish();")
        + "await pending;console.log(JSON.stringify({overlay,state:frame()}));"
    )
    assert result["overlay"] == ("climate" if superseded else "priority" if failure else "gettingeven")
    assert not result["state"]["disabled"]
    assert 'role="status"' not in result["state"]["legend"]
    assert result["state"]["climate"] is superseded
