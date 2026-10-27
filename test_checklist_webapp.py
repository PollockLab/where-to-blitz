"""The "Complete a checklist" goal: strings, palette, and the app's pixel decode match the builder."""

import json
import shutil
import subprocess

import numpy as np
import pytest

import build_checklist as bc
from test_shared_context import TEMPLATE, _function
from test_template_wiring import _i18n_keys, _i18n_segments

CL_KEYS = (
    "crit_cl", "cl_loading", "cl_opacity", "aria_cl_opacity", "cl_status", "cl_legend", "cl_blurb",
    "method_cl", "cl_how", "pop_layer_cl", "cl_pop_pct", "cl_pop_days", "cl_pop_note", "cl_pop_ci",
    "cl_pop_effort",
)  # fmt: skip
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _node(code, lang="en"):
    src = TEMPLATE.read_text()
    script = src[src.index("const I18N=") : src.index("\nlet LANG=")]
    script += f"\nlet LANG={json.dumps(lang)};const GE_ALL='#BBBBBB';"
    script += "\nfunction t(id,...a){const v=I18N[LANG][id];return typeof v==='function'?v(...a):v;}"
    script += "\n" + next(line for line in src.splitlines() if line.startswith("const CL_DIR="))
    script += "\nlet CL_META=null;const state={taxon:'Aves'},esc=s=>String(s);"
    script += "\nconst paintPopScore=()=>{},loadCLVals=async()=>{},loadCLPart=async()=>{};"
    for name in ("clImage", "clStatus", "clPopup"):
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


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_legend_has_four_distinct_classes(lang):
    out = _node(
        "console.log(JSON.stringify({pal:CL_PAL,legend:t('cl_legend',90,70,10),status:t('cl_status')}));",
        lang,
    )
    assert len(out["pal"]) == len(set(out["pal"])) == 4
    assert len(out["legend"]) == len(out["status"]) == 4
    assert "90" in out["legend"][0] and "70" in out["legend"][1] and "10" in out["legend"][3]


@needs_node
def test_decoded_status_matches_the_builders_bands():
    # Pixels built the way bc.encode does, including both band edges and the effort floor.
    comp = np.array([1.0, 0.9, 0.896, 0.7, 0.696, 0.2, 0.95, 0.95, 0.0])
    days = np.array([40, bc.MIN_UNITS, 12, 12, 12, 300, bc.MIN_UNITS - 1, 0, 0])
    rgba = np.zeros((1, len(comp), 4), np.uint8)
    rgba[0, :, 0] = np.round(comp * 250)
    rgba[0, :, 1], rgba[0, :, 2], rgba[0, :, 3] = days // 256, days % 256, 255
    rgba[0, -2, 3] = bc.NO_RECORDS_ALPHA  # Canadian, no records of the group
    rgba[0, -1, 3] = 0  # outside Canada
    meta = {"min_units": bc.MIN_UNITS, "bands": bc.BANDS}
    out = _node(
        f"CL_META={json.dumps(meta)};const v={{w:{len(comp)},h:1,d:{json.dumps(rgba.ravel().tolist())}}};"
        f"console.log(JSON.stringify([...Array({len(comp)}).keys(),-1,{len(comp)}].map(c=>clStatus(v,c,0))));"
    )
    decoded, units, _present, _canadian = bc.decode(rgba[0, :-1])
    names = ["complete", "close", "far", "too_few"]
    expected = [
        names.index(next(k for k, n in bc.status_counts(np.array([c]), np.array([u])).items() if n))
        for c, u in zip(decoded, units)
    ]
    assert out[:-3] == expected == [0, 0, 1, 1, 2, 2, 3, 3]
    assert out[-3] == -1  # outside Canada: drawn as nothing at both tiers
    assert out[-2:] == [-1, -1]  # off the raster


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_reports_completeness_effort_and_source(lang):
    meta = {"min_units": 10, "bands": {"complete": 0.9, "close": 0.7}, "rule_of_thumb_units": 30,
            "source": {"export_date": "2026-10-01"}}  # fmt: skip
    out = _node(
        f"CL_META={json.dumps(meta)};CL_VALS['25000|Aves']={{w:1,h:1,d:[225,0,14,255]}};"
        "const snapToLattice=()=>({col:0,row:0});console.log(JSON.stringify(clPopup(45,-75,25000)));",
        lang,
    )
    assert ("Complete" if lang == "en" else "Complète") in out
    assert "90" in out and "14" in out and "2026-10-01" in out and "Chao2" in out
    assert "30" not in out  # a complete cell needs no rule of thumb


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_reports_interval(lang):
    meta = {"min_units": 10, "bands": {"complete": 0.9, "close": 0.7}, "rule_of_thumb_units": 30,
            "source": {"export_date": "2026-10-01"}}  # fmt: skip
    out = _node(
        f"CL_META={json.dumps(meta)};CL_VALS['25000|Aves']={{w:1,h:1,d:[200,0,40,255]}};"
        "CL_VALS['25000|Aves|ci']={w:1,h:1,d:[150,240,245,255]};"
        "const snapToLattice=()=>({col:0,row:0});console.log(JSON.stringify(clPopup(45,-75,25000)));",
        lang,
    )
    assert "80" in out and "60–96" in out
    assert ("may already be complete" if lang == "en" else "peut-être déjà complète") in out  # 96% upper bound


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_gives_effort_to_complete_for_unfinished_cells(lang):
    meta = {"min_units": 10, "bands": {"complete": 0.9, "close": 0.7}, "rule_of_thumb_units": 30,
            "source": {"export_date": "2026-10-01"}}  # fmt: skip
    # 80% complete on 40 observer-days; 1*256+44 = 300 more, past double the effort so far.
    out = _node(
        f"CL_META={json.dumps(meta)};CL_VALS['25000|Aves']={{w:1,h:1,d:[200,0,40,255]}};"
        "CL_VALS['25000|Aves|effort']={w:1,h:1,d:[1,44,0,255]};const snapToLattice=()=>({col:0,row:0});"
        "const rough=clPopup(45,-75,25000);CL_VALS['25000|Aves|effort'].d=[0,12,0,255];"
        "console.log(JSON.stringify([rough,clPopup(45,-75,25000)]));",
        lang,
    )
    rough, near = out
    # Past double the effort so far iNEXT does not extrapolate reliably, so no point figure is shown.
    assert "300" not in rough and "40" in rough and ("Over 40" if lang == "en" else "Plus de 40") in rough
    assert "12" in near and "40" in near and ("Over" not in near and "Plus de" not in near)
    assert (
        "about 30" not in near and "environ 30" not in near
    )  # the per-cell figure replaces the rule of thumb


@needs_node
@pytest.mark.parametrize("lang", ["en", "fr"])
def test_popup_does_not_print_the_encoding_cap(lang):
    meta = {"min_units": 10, "bands": {"complete": 0.9, "close": 0.7}, "rule_of_thumb_units": 30,
            "source": {"export_date": "2026-10-01"}}  # fmt: skip
    out = _node(
        f"CL_META={json.dumps(meta)};CL_VALS['25000|Aves']={{w:1,h:1,d:[200,255,255,255]}};"
        "CL_VALS['25000|Aves|effort']={w:1,h:1,d:[255,255,0,255]};const snapToLattice=()=>({col:0,row:0});"
        "console.log(JSON.stringify(clPopup(45,-75,25000)));",
        lang,
    )
    assert "65535" not in out.replace(",", "").replace(" ", "")
    assert ("Over 65,000" if lang == "en" else "Plus de 65 000") in out
