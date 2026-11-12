"""The "Complete a checklist" goal: strings, palette, light-up decode and popup."""

import json
import shutil
import subprocess

import pytest

import build_checklist as bc
from test_shared_context import TEMPLATE, _function
from test_template_wiring import _i18n_keys, _i18n_segments

CL_KEYS = (
    "crit_cl", "cl_loading", "cl_opacity", "aria_cl_opacity", "cl_status", "cl_legend", "cl_blurb",
    "method_cl", "cl_how", "pop_layer_cl", "cl_pop_pct", "cl_pop_days", "cl_pop_note", "cl_pop_far",
)  # fmt: skip
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _node(code, lang="en"):
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("\nlet LANG=")]
    script += f"\nlet LANG={json.dumps(lang)};const GE_ALL='#BBBBBB';"
    script += "\nfunction t(id,...a){const v=I18N[LANG][id];return typeof v==='function'?v(...a):v;}"
    script += "\n" + "\n".join(
        line for line in src.splitlines() if line.startswith(("const CL_DIR=", "const CL_FAR="))
    )
    script += "\nlet CL_META=null;const state={taxon:'Aves'},esc=s=>String(s);"
    script += "\nconst paintPopScore=()=>{},loadCLVals=async()=>{},loadCLPart=async()=>{};"
    for name in ("clImage", "clDays", "clStatus", "clPopup"):
        script += "\n" + _function(src, name)
    out = subprocess.run(["node", "-e", script + "\n" + code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_every_checklist_string_exists_in_both_languages():
    en, fr = _i18n_segments(TEMPLATE.read_text())
    for seg in (en, fr):
        assert set(CL_KEYS) <= _i18n_keys(seg)


def test_goal_menu_offers_checklist():
    src = TEMPLATE.read_text()
    assert "cl.value='cl'" in src and "setOverlay('checklist')" in src
    assert '<p data-i18n-html="method_cl"></p>' in src
    assert 'href="checklist/"' not in src  # one view: the old preview page only redirects here


META = {"min_units": 10, "rule_of_thumb_units": 30, "bands": {"complete": 0.9, "close": 0.7},
        "source": {"export_date": "2026-10-01"}}  # fmt: skip


def _pixel(days, comp=0.0, alpha=255):
    return [round(comp * 250), days // 256, days % 256, alpha]


def _popup(days, lang="en", res=25000, taxon="Aves", comp=0.0, alpha=255, ci=None):
    vals = {f"{res}|{taxon}": {"w": 1, "h": 1, "d": _pixel(days, comp, alpha)}}
    if ci:
        vals[f"{res}|{taxon}|ci"] = {"w": 1, "h": 1, "d": [*ci, 0, 255]}
    return _node(
        f"CL_META={json.dumps(META)};state.taxon={json.dumps(taxon)};Object.assign(CL_VALS,{json.dumps(vals)});"
        f"const snapToLattice=()=>({{col:0,row:0}});console.log(JSON.stringify(clPopup(45,-75,{res})));",
        lang,
    )


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_legend_has_three_effort_states(lang):
    out = _node(
        "console.log(JSON.stringify({pal:CL_PAL,legend:t('cl_legend',30,15),status:t('cl_status')}));",
        lang,
    )
    assert len(out["pal"]) == len(set(out["pal"])) == 3
    assert len(out["legend"]) == len(out["status"]) == 3
    assert "30" in out["legend"][0] and "15–29" in out["legend"][1] and "15" in out["legend"][2]


@needs_node
def test_status_lights_by_observer_days_not_chao2():
    days = [bc.RULE_OF_THUMB_UNITS, 1000, 29, 15, 14, 0, 5, 40]
    comp = [0.0, 0.1, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    alpha = [255] * 5 + [bc.NO_RECORDS_ALPHA, 0, 255]  # Canadian without records; outside Canada
    rgba = [v for d, c, a in zip(days, comp, alpha) for v in _pixel(d, c, a)]
    out = _node(
        f"CL_META={json.dumps(META)};const v={{w:{len(days)},h:1,d:{json.dumps(rgba)}}};"
        f"console.log(JSON.stringify([...Array({len(days)}).keys(),-1,{len(days)}].map(c=>clStatus(v,c,0))));"
    )
    assert out == [0, 0, 1, 1, 2, 2, -1, 0, -1, -1]


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_unrecorded_canadian_cell_needs_the_full_rule(lang):
    out = _popup(0, lang, alpha=bc.NO_RECORDS_ALPHA)
    assert ("Not yet" if lang == "en" else "Pas encore") in out
    assert "30" in out and "Chao2" not in out and "2026-10-01" in out


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_gives_observer_days_left_and_chao2_interval(lang):
    out = _popup(22, lang, comp=0.8, ci=(150, 240))
    assert ("Close" if lang == "en" else "Presque") in out
    assert "22" in out and "<b>" + ("8 more" if lang == "en" else "encore 8") in out
    assert "Chao2" in out and "80" in out and "60–96" in out and "95" in out
    assert "complete" not in out.split("Chao2")[0].lower()  # completeness is a number, never the status


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_chao2_is_omitted_below_the_estimation_floor(lang):
    out = _popup(META["min_units"] - 1, lang, comp=0.5)
    assert "Chao2" not in out and "21" in out


@needs_node
@pytest.mark.parametrize(
    "taxon,res,days,shown",
    [("Amphibia", 5000, 30, ("3.6", "9.1")), ("Mammalia", 5000, 45, ("2.1", "4.9")),
     ("Amphibia", 25000, 30, None), ("Aves", 5000, 30, None), ("Amphibia", 5000, 29, None)],
)  # fmt: skip
def test_error_rate_only_where_measured(taxon, res, days, shown):
    en, fr = (_popup(days, lang, res, taxon, comp=0.9) for lang in ("en", "fr"))
    if shown is None:
        assert "turned up later" not in en and "plus tard" not in fr
    else:
        assert "turned up later" in en and all(x in en for x in shown)
        assert all(x.replace(".", ",") in fr for x in shown)


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_does_not_print_the_encoding_cap(lang):
    out = _popup(65535, lang, comp=0.8)
    assert "65535" not in out.replace(",", "").replace(" ", "")
    assert ("Over 65,000" if lang == "en" else "Plus de 65 000") in out


def test_error_rate_comment_cites_its_source():
    src = TEMPLATE.read_text()
    block = src[src.index("const CL_DIR=") : src.index("const CL_FAR=")]
    assert "research/results/checklist_calibrated.md" in block and "H-S2" in block
