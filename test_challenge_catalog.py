"""Source-bound catalogue and real builder regressions, with no network acquisition."""

import copy
import json
from pathlib import Path

import pytest

from challenge_catalog import load_catalog
from conservation_challenge import acquire
from test_conservation_challenge import AS_OF, FixtureAPI
from test_optional_layer_viewer import build, descriptor, sighting_lifecycle

ROOT = Path(__file__).parent
CONFIG = ROOT / "webapp/challenge_taxa.json"


def empty_snapshot():
    return acquire(json.loads(CONFIG.read_text()), FixtureAPI([]), AS_OF)


def test_catalog_contains_every_exact_target_even_with_no_sightings():
    config = json.loads(CONFIG.read_text())
    result = load_catalog(CONFIG, empty_snapshot())
    assert result["taxa"] == [{key: row[key] for key in ("id", "name", "rank")} for row in config["taxa"]]
    assert len(result["taxa"]) == len({row["id"] for row in result["taxa"]}) == 335
    assert {row["rank"] for row in result["taxa"]} == {"species", "subspecies", "variety", "genus"}
    assert result["coverage"]["resolved_rows"] == 347
    assert len(result["coverage"]["omissions"]) == 22
    assert "resolved" not in result["coverage"] and "url" not in result
    assert all(set(row) == {"id", "name", "rank"} for row in result["taxa"])


@pytest.mark.parametrize("field", ["version", "config_sha256", "sha256", "url", "coverage"])
def test_catalog_abstains_for_any_different_source_vintage(field):
    snapshot = empty_snapshot()
    if field == "coverage":
        snapshot["coverage"]["omissions"][0]["common_name"] = "Different supplied label"
    elif field in ("sha256", "config_sha256"):
        snapshot["source"][field] = "a" * 64
    else:
        snapshot["source"][field] = "https://example.org/other" if field == "url" else "other"
    assert load_catalog(CONFIG, snapshot) is None


def test_duplicate_target_ids_are_rejected_instead_of_expanded_as_synonyms(tmp_path):
    config = json.loads(CONFIG.read_text())
    config["taxa"].append(copy.deepcopy(config["taxa"][0]))
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(path, empty_snapshot())


def test_actual_builder_binds_catalog_to_snapshot_and_abstains_without_one(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(empty_snapshot()))
    result = build(tmp_path, {"CONSERVATION_CHALLENGE_FILE": str(snapshot)})
    assert result.returncode == 0, result.stderr
    catalog = descriptor((tmp_path / "index.html").read_text())["challenge"]["catalog"]
    assert len(catalog["taxa"]) == 335 and len(catalog["coverage"]["omissions"]) == 22
    assert build(tmp_path).returncode == 0
    assert descriptor((tmp_path / "index.html").read_text())["challenge"] is None


def test_catalog_is_fully_reachable_with_sightings_off_without_observation_requests():
    catalog = load_catalog(CONFIG, empty_snapshot())
    result = sighting_lifecycle(
        "let requests=0;OptionalData.fetchPinned=()=>{requests++;throw Error('unexpected observation request');};"
        "const rows=[];for(let page=0;page<12;page++){rows.push(...nodes.challengeCatalogList.children.map(li=>({"
        "id:Number(li.dataset.catalogTarget),rank:li.dataset.rank,name:li.children[0].textContent,href:li.children[0].href})));"
        "nodes.challengeCatalogNext.onclick();}"
        "console.log(JSON.stringify({requests,rows,visible:!nodes.recentChallengeCoverage.hidden,"
        "omissions:nodes.recentChallengeOmissionList.children.length,status:nodes.challengeCatalogStatus.textContent}));",
        start_recent=False,
        challenge_catalog=catalog,
    )
    assert result["requests"] == 0 and result["visible"] and result["omissions"] == 22
    assert sorted(row["id"] for row in result["rows"]) == sorted(row["id"] for row in catalog["taxa"])
    expected = {row["id"]: row for row in catalog["taxa"]}
    for row in result["rows"]:
        assert row["rank"] == expected[row["id"]]["rank"] and row["name"] == expected[row["id"]]["name"]
        assert row["href"] == "https://www.inaturalist.org/taxa/" + str(row["id"])
    assert "335" in result["status"]


def test_catalog_search_paging_and_native_focus_survive_pan_and_language_changes():
    catalog = load_catalog(CONFIG, empty_snapshot())
    result = sighting_lifecycle(
        "const input=nodes.challengeCatalogSearch;input.value=' ';input.focus();input.oninput();"
        "nodes.challengeCatalogNext.onclick();const id=nodes.challengeCatalogList.children[0].dataset.catalogTarget;"
        "nodes.recentChallengeCoverage.open=true;handlers.moveend();LANG='fr';optionalLayers.refresh();"
        "const preserved={id:nodes.challengeCatalogList.children[0].dataset.catalogTarget,"
        "focused:document.activeElement===input,open:nodes.recentChallengeCoverage.open,status:nodes.challengeCatalogStatus.textContent};"
        "input.value='CALIDRIS';input.oninput();const matched=nodes.challengeCatalogList.children.map(li=>li.children[0].textContent);"
        "input.value='no matching supplied name';input.oninput();const empty={count:nodes.challengeCatalogList.children.length,"
        "text:nodes.challengeCatalogList.textContent,next:nodes.challengeCatalogNext.getAttribute('aria-disabled')};"
        "input.value='';input.oninput();console.log(JSON.stringify({id,preserved,matched,empty,all:nodes.challengeCatalogList.children.length}));",
        start_recent=False,
        challenge_catalog=catalog,
    )
    assert result["preserved"]["id"] == result["id"]
    assert result["preserved"]["focused"] and result["preserved"]["open"]
    assert "taxons inclus" in result["preserved"]["status"]
    assert result["matched"] and all("calidris" in name.lower() for name in result["matched"])
    assert result["empty"]["count"] == 0 and result["empty"]["next"] == "true"
    assert "Aucun nom inclus" in result["empty"]["text"] and result["all"] == 30


def test_catalog_remains_usable_during_sighting_loading_and_disabled_state():
    catalog = load_catalog(CONFIG, empty_snapshot())
    result = sighting_lifecycle(
        "let finish,requests=0;OptionalData.fetchPinned=()=>{requests++;return new Promise(resolve=>{finish=()=>resolve(input.snapshot);});};"
        "const pending=optionalLayers.toggleRecent(true);const loading={shown:!nodes.recentChallengeCoverage.hidden,"
        "count:nodes.challengeCatalogList.children.length};"
        "nodes.challengeCatalogSearch.value='Calidris';nodes.challengeCatalogSearch.oninput();"
        "await optionalLayers.toggleRecent(false);const off=nodes.challengeCatalogList.children.length;"
        "finish();await pending;console.log(JSON.stringify({loading,off,requests,after:nodes.challengeCatalogList.children.length,"
        "omissions:nodes.recentChallengeOmissionList.children.length}));",
        snapshot=empty_snapshot(),
        start_recent=False,
        challenge_catalog=catalog,
    )
    assert result["loading"] == {"shown": True, "count": 30}
    assert result["requests"] == 1 and result["off"] == result["after"] > 0
    assert result["omissions"] == 22


@pytest.mark.parametrize("matched", [False, True])
def test_list_rationale_is_only_attributed_to_the_matching_project_source(matched):
    catalog = load_catalog(CONFIG, empty_snapshot()) if matched else None
    result = sighting_lifecycle(
        "console.log(JSON.stringify({context:nodes.challengeCatalogContext.hidden,"
        "rationale:nodes.challengeCatalogRationale.hidden,"
        "unavailable:nodes.challengeCatalogUnavailable.hidden}));",
        start_recent=False,
        challenge_catalog=catalog,
    )
    assert result == {"context": not matched, "rationale": not matched, "unavailable": matched}
