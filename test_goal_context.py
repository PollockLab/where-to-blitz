"""The displayed goal and cell evidence must describe the calculation being shown."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from goal_presets import PRESETS

TEMPLATE = Path(__file__).resolve().parent / "webapp" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(src, name):
    match = re.search(r"function " + name + r"\([^)]*\)\{.*?\n\}", src, re.S)
    assert match, name
    return match.group(0)


def _run(code, lang="en", presets=None):
    src = TEMPLATE.read_text()
    i18n = src[src.index("const I18N=") : src.index("\nlet LANG=")]
    script = i18n + f"\nlet LANG={json.dumps(lang)};"
    script += "\nconst PRESETS=" + json.dumps(PRESETS if presets is None else presets) + ";"
    script += """
function t(id,...args){const value=I18N[LANG][id];return typeof value==='function'?value(...args):value;}
function presetName(i){return t('preset_name')[i];}
function presetBlurb(i){return t('preset_blurb')[i];}
let overlay='priority';
function geActive(){return overlay==='gettingeven';}
function climateOn(){return overlay==='climate';}
function clActive(){return overlay==='checklist';}
function contribStr(){return 'source contribution';}
function clPopup(){return 'checklist status';}
const elements={goalmethods:{},challengeBlurb:{},plansource:{style:{}}};
const document={getElementById:id=>elements[id]};
const window={__activePreset:1};
const state={view:'explore'};
"""
    script += "\n" + "\n".join(
        _function(src, name) for name in ("goalMethodsHtml", "renderGoalContext", "popScoreHtml")
    )
    result = subprocess.run(["node", "-e", script + "\n" + code], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_method_menu_uses_current_preset_weights(lang):
    """Changing a preset's weight must change its explanation without editing translated prose."""
    presets = [dict(p, w=list(p["w"])) for p in PRESETS]
    presets[1]["w"][3] = 0.73
    help_text = _run("console.log(JSON.stringify(goalMethodsHtml()));", lang, presets)
    assert "0.73 ×" in help_text
    assert "0.6 ×" not in help_text
    assert "0.4 ×" in help_text
    assert "CHELSA" in help_text and "CAN-SAR" in help_text and "2001–2023" in help_text
    assert "Getting Even" in help_text if lang == "en" else "Combler l'écart" in help_text
    assert "Climate Gap" in help_text if lang == "en" else "Lacune climatique" in help_text


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_context_switches_with_overlay_and_preserves_planner_help(lang):
    context = _run(
        """
const output={};
for(const kind of ['gettingeven','climate','checklist','coverage']){
  overlay=kind;renderGoalContext();output[kind]=elements.challengeBlurb.textContent;
}
output.explore=elements.plansource.style.display;
state.view='plan';renderGoalContext();output.plan=elements.plansource.style.display;
console.log(JSON.stringify(output));
""",
        lang,
    )
    assert len({context[k] for k in ("gettingeven", "climate", "checklist", "coverage")}) == 4
    assert "CHELSA" in context["climate"]
    assert "Biodiversité Québec" in context["coverage"]
    assert context["explore"] == "none" and context["plan"] == ""


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_distinguishes_fine_evidence_from_parent_percentile(lang):
    text = _run("console.log(JSON.stringify(popScoreHtml({t:0.8,r:[]},5000)));", lang)
    assert "5 km" in text and "25 km" in text and "80/100" in text
    assert "enclosing" in text if lang == "en" else "englobante" in text
    assert "Species discovery" in text if lang == "en" else "Découverte d'espèces" in text
    assert "source contribution" not in text


@pytest.mark.parametrize("score", [None, "0.8"])
def test_popup_does_not_coerce_missing_or_invalid_percentiles(score):
    text = _run(f"console.log(JSON.stringify(popScoreHtml({{t:{json.dumps(score)},r:[]}},25000)));")
    assert "/100" not in text
    assert "No national score" in text


@pytest.mark.parametrize("kind", ["gettingeven", "climate", "checklist", "coverage"])
def test_popup_does_not_show_previous_goals_score_on_other_layers(kind):
    text = _run(f"overlay={json.dumps(kind)};console.log(JSON.stringify(popScoreHtml({{t:0.8,r:[]}},5000)));")
    assert "/100" not in text
    assert "no national percentile" in text


@pytest.mark.parametrize(
    ("lang", "shared_inputs", "precision"),
    [
        ("en", "same aggregated scoring inputs", "full stored score"),
        ("fr", "mêmes indicateurs agrégés", "score stocké sans arrondi"),
    ],
)
def test_colour_help_explains_shared_inputs_and_display_quantization(
    lang, shared_inputs, precision
):
    text = _run("console.log(JSON.stringify(t('colour_expl')));", lang)
    assert shared_inputs in text and precision in text
