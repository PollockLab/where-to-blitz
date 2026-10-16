"""Exercise the shipped optional-layer helpers and actual build without public data."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import build_discovery_layer as discovery
import conservation_challenge as challenge
from test_conservation_challenge import AS_OF, CONFIG, FixtureAPI, observation
from test_discovery_layer import edit_metadata, event, sources
from test_shared_context import _run as run_shared

ROOT = Path(__file__).parent
SCRIPT = (ROOT / "webapp/optional_layers.js").read_text()


def run_js(code, data=None):
    result = subprocess.run(
        ["node", "-e", SCRIPT + "\n" + code, json.dumps(data)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rate_product(tmp_path):
    inputs = sources(tmp_path, [event("optional-unique-fixture-event")], {(400, 400): 10})
    for sidecar in inputs[2:]:
        edit_metadata(sidecar, lambda m: m["scope"]["filters"].update(taxon_group="All biodiversity"))
    return discovery.build(*inputs)


def build(tmp_path, environ=None):
    (tmp_path / "webapp").mkdir(exist_ok=True)
    for filename in ("index.html", "optional_layers.js"):
        shutil.copyfile(ROOT / "webapp" / filename, tmp_path / "webapp" / filename)
    target = tmp_path / "cluster_results/ca"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.json").write_text(
        json.dumps({"files": {"All biodiversity": "all.json", "Aves": "aves.json"}})
    )
    env = {
        k: v for k, v in os.environ.items() if k not in ("DISCOVERY_RATE_FILE", "CONSERVATION_CHALLENGE_FILE")
    }
    env.update(PYTHONPATH=str(ROOT), **(environ or {}))
    return subprocess.run(
        [sys.executable, str(ROOT / "build_webapp.py")], cwd=tmp_path, env=env, capture_output=True, text=True
    )


def descriptor(html):
    return json.loads(html.split("const OPTIONAL_LAYERS=", 1)[1].split(";", 1)[0])


def test_unconfigured_build_preserves_existing_viewer_and_advertises_no_optional_data(tmp_path):
    result = build(tmp_path)
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "index.html").read_text()
    assert descriptor(html) == {"discovery": None, "challenge": None}
    assert "__OPTIONAL_LAYER_SCRIPT__" not in html and "__OPTIONAL_LAYERS__" not in html
    assert "installOptionalLayers();" in html
    assert 'id="recentChallengePanel" hidden' in html


def test_build_pins_validated_files_without_inlining_observations_or_cells(tmp_path):
    product = rate_product(tmp_path)
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation()]), AS_OF)
    rate, recent = tmp_path / "rate.json", tmp_path / "recent.json"
    rate.write_text(json.dumps(product))
    recent.write_text(json.dumps(snapshot))
    result = build(tmp_path, {"DISCOVERY_RATE_FILE": str(rate), "CONSERVATION_CHALLENGE_FILE": str(recent)})
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "index.html").read_text()
    desc = descriptor(html)
    assert desc["discovery"]["sha256"] == hashlib.sha256(rate.read_bytes()).hexdigest()
    assert desc["discovery"]["taxon_group"] == "All biodiversity"
    assert desc["challenge"]["sha256"] == hashlib.sha256(recent.read_bytes()).hexdigest()
    assert desc["discovery"]["path"] == "cluster_results/ca/discovery_rate.json"
    assert desc["challenge"]["path"] == "cluster_results/ca/conservation_challenge.json"
    assert "eligible_count" not in html and "optional-unique-fixture-event" not in html
    assert "observations" not in desc["challenge"] and "tiers" not in desc["discovery"]


@pytest.mark.parametrize("change", ["missing", "malformed", "implicit_group", "unknown_group"])
def test_configured_bad_or_semantically_ambiguous_discovery_input_fails_build(tmp_path, change):
    product = rate_product(tmp_path)
    if change == "malformed":
        product["events"]["unique"] = 9
    if change in ("implicit_group", "unknown_group"):
        for scope in (product["scope"], *(s["scope"] for s in product["sources"].values())):
            if change == "implicit_group":
                scope["filters"].pop("taxon_group", None)
            else:
                scope["filters"]["taxon_group"] = "unknown"
    path = tmp_path / "rate.json"
    if change != "missing":
        path.write_text(json.dumps(product))
    result = build(tmp_path, {"DISCOVERY_RATE_FILE": str(path)})
    assert result.returncode != 0
    assert not (tmp_path / "index.html").exists()


def test_incomplete_challenge_snapshot_cannot_advertise_a_public_overlay(tmp_path):
    path = tmp_path / "challenge.json"
    path.write_text(json.dumps({"schema_version": 1, "complete": False, "observations": []}))
    result = build(tmp_path, {"CONSERVATION_CHALLENGE_FILE": str(path)})
    assert result.returncode != 0 and not (tmp_path / "index.html").exists()


def test_discovery_js_never_exposes_zero_or_incomplete_support(tmp_path):
    product = rate_product(tmp_path)
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify([OptionalData.availableCells(p,5000),OptionalData.availableCells(p,25000)]));",
        product,
    )
    assert result[0] == [[400, 400, 1, 10, 1000, 1, 1]] and result[1] == []


@pytest.mark.parametrize("national", [False, True])
def test_shipped_challenge_selector_matches_python_including_caps_dates_ties_and_extent(national):
    rows = []
    for identity in range(1, 620):
        item = {
            "id": identity,
            "taxon_id": (identity % 11) + 1,
            "longitude": -75 if identity % 3 else -125,
            "latitude": 45,
            "observed_on": "2026-10-15",
            "observed_at": None,
        }
        if identity % 7:
            item["observed_at"] = "2026-10-15T12:00:00+00:00"
        rows.append(item)
    snapshot = {"schema_version": 1, "complete": True, "observations": rows}
    bounds = [-90, 40, -60, 50]
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify(OptionalData.selectChallenge(p.snapshot,p.bounds,p.national).map(r=>r.id)));",
        {"snapshot": snapshot, "bounds": bounds, "national": national},
    )
    assert result == [r["id"] for r in challenge.select_observations(snapshot, bounds, national)]
    assert len(result) == (len([r for r in rows if r["longitude"] == -75]) if national else 150)


def test_viewport_selection_happens_before_national_thinning():
    rows = [
        {
            "id": i,
            "taxon_id": 1,
            "longitude": -75,
            "latitude": 45,
            "observed_on": "2026-10-15",
            "observed_at": None,
        }
        for i in range(1, 601)
    ]
    rows.append({**rows[0], "id": 999, "taxon_id": 2, "longitude": -125, "observed_on": "2026-10-03"})
    result = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify(OptionalData.selectChallenge(p,[-130,40,-120,50],false).map(r=>r.id)));",
        {"observations": rows},
    )
    assert result == [999]


def test_dateline_wrapping_and_whole_world_bounds():
    result = run_js(
        "console.log(JSON.stringify([OptionalData.viewport(170,40,210,60),OptionalData.viewport(-220,40,220,60)]));"
    )
    assert result == [[170, 40, -150, 60], [-180, 40, 180, 60]]


@pytest.mark.parametrize("corrupt", [False, True])
def test_lazy_loader_rejects_wrong_bytes_before_data_can_be_used(tmp_path, corrupt):
    product = rate_product(tmp_path)
    body = json.dumps(product)
    desc = {
        "schema_version": 1,
        "path": "cluster_results/ca/discovery_rate.json",
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "taxon_group": "All biodiversity",
        "scope": product["scope"],
        "metric": product["metric"],
    }
    result = run_js(
        """(async()=>{
      const p=JSON.parse(process.argv[1]),subtle=require('node:crypto').webcrypto.subtle;let calls=0;
      try{await OptionalData.fetchPinned(p.desc,'discovery',async()=>{calls++;return{ok:true,arrayBuffer:async()=>new TextEncoder().encode(p.body).buffer};},subtle,'https://example.org/map/');console.log(JSON.stringify({valid:true,calls}));}
      catch(e){console.log(JSON.stringify({valid:false,calls,message:e.message}));}
    })();""",
        {"body": body + (" " if corrupt else ""), "desc": desc},
    )
    assert result["valid"] is not corrupt and result["calls"] == 1
    if corrupt:
        assert "hash mismatch" in result["message"]


def test_pinned_loader_rejects_cross_origin_before_fetch():
    result = run_js(
        """(async()=>{let calls=0;try{await OptionalData.fetchPinned({path:'https://other.example/data'},'challenge',async()=>{calls++;}, {},'https://example.org/map/');}catch(e){console.log(JSON.stringify({calls,message:e.message}));}})();"""
    )
    assert result == {"calls": 0, "message": "Cross-origin optional data"}


def test_stale_snapshot_threshold_is_explicit():
    result = run_js(
        "console.log(JSON.stringify([OptionalData.stale('2026-10-15T00:00:00+00:00',Date.parse('2026-10-16T12:00:00Z')),OptionalData.stale('2026-10-15T00:00:00+00:00',Date.parse('2026-10-16T12:00:01Z'))]));"
    )
    assert result == [False, True]


def test_measured_rate_and_recent_overlay_round_trip_through_real_shared_restoration():
    result = run_shared(
        "?at=49.300,-123.200&g=Aves&res=5000&goal=discovery&recent=1&style=satellite",
        "let recent=false;globalThis.optionalLayers={supports:g=>g==='Aves',isDiscovery:()=>overlay==='discovery',"
        "recentEnabled:()=>recent,load:async()=>{},refresh:()=>{},discoveryCell:()=>[1],toggleRecent:async enabled=>{recent=enabled;}};"
        "await bootSeq();console.log(JSON.stringify({opened,recent,params:Object.fromEntries(location.searchParams)}));",
        real_overlay=True,
        real_cells=True,
    )
    assert result["opened"][0]["overlay"] == "discovery"
    assert result["opened"][0]["goal"] == "discovery"
    assert result["recent"] is True
    assert result["params"]["goal"] == "discovery" and result["params"]["recent"] == "1"


def test_measured_rate_shared_link_cannot_label_another_taxon_group():
    result = run_shared(
        "?at=49.300,-123.200&g=Fungi&res=25000&goal=discovery",
        "globalThis.optionalLayers={supports:g=>g==='Aves',isDiscovery:()=>false,recentEnabled:()=>false};"
        "await bootSeq();console.log(JSON.stringify({opened,params:Object.fromEntries(location.searchParams)}));",
        real_cells=True,
    )
    assert result["opened"][0]["goal"] == "0"
    assert result["params"]["g"] == "Fungi" and result["params"]["goal"] == "0"
