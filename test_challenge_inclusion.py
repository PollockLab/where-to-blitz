"""Show source-bound target matches without assigning conservation categories."""

from test_conservation_challenge import AS_OF, CONFIG, FixtureAPI, observation
from test_optional_layer_viewer import challenge, sighting_lifecycle


def inclusion_fixture(targets, target_ids):
    snapshot = challenge.acquire(CONFIG, FixtureAPI([observation(1)]), AS_OF)
    snapshot["observations"][0]["target_ids"] = target_ids
    catalog = {
        "source_version": snapshot["source"]["version"],
        "source_config_sha256": snapshot["source"]["config_sha256"],
        "taxa": targets,
    }
    return snapshot, catalog


READ_ROUTE = """
const flatten=el=>el.textContent+el.children.map(flatten).join('');
const button=nodes.recentChallengeList.children[0];button.focus();button.onclick();
const body=curPopup.root.children[1];
const descendants=el=>[el,...el.children.flatMap(descendants)];
const links=descendants(body).filter(el=>el.dataset.inclusionTarget).map(el=>({name:el.textContent,href:el.href,tag:el.tag}));
console.log(JSON.stringify({list:button.textContent,lines:body.children.filter(el=>el.className==='sighting-inclusion').map(flatten),links}));
"""


def test_genus_route_names_the_listed_genus_in_popup_and_visible_list():
    snapshot, catalog = inclusion_fixture([{"id": 100, "name": "Helix", "rank": "genus"}], [100])
    result = sighting_lifecycle(READ_ROUTE, snapshot=snapshot, challenge_catalog=catalog)
    assert "Included through listed genus Helix" in result["list"]
    assert result["lines"] == ["Included through listed genus Helix"]
    assert result["links"] == [{"name": "Helix", "href": "https://www.inaturalist.org/taxa/100", "tag": "a"}]


def test_species_and_genus_overlap_retains_both_routes_without_conservation_categories():
    snapshot, catalog = inclusion_fixture(
        [{"id": 10, "name": "Helix pomatia", "rank": "species"}, {"id": 100, "name": "Helix", "rank": "genus"}],
        [10, 100],
    )
    result = sighting_lifecycle(READ_ROUTE, snapshot=snapshot, challenge_catalog=catalog)
    expected = "Included through listed species Helix pomatia · Included through listed genus Helix"
    assert result["lines"] == [expected] and expected in result["list"]
    assert [link["name"] for link in result["links"]] == ["Helix pomatia", "Helix"]
    assert "invasive" not in expected and "climate" not in expected and "KBA" not in expected


def test_exact_infraspecies_target_is_named_without_substitution_by_its_species():
    for rank, name in [("subspecies", "Helix pomatia example"), ("variety", "Plant alpha example"), ("form", "Plant alpha pale")]:
        snapshot, catalog = inclusion_fixture([{"id": 101, "name": name, "rank": rank}], [101])
        result = sighting_lifecycle(READ_ROUTE, snapshot=snapshot, challenge_catalog=catalog)
        assert result["lines"] == [f"Included through listed {rank} {name}"]
        assert result["links"][0]["name"] == name
        assert result["links"][0]["href"] == "https://www.inaturalist.org/taxa/101"


def test_unknown_catalog_or_missing_target_abstains_without_hiding_identification():
    for change in ["version", "config_hash", "missing_id", "missing_ids", "duplicate_ids", "catalog_absent"]:
        snapshot, catalog = inclusion_fixture([{"id": 10, "name": "Listed alpha", "rank": "species"}], [10])
        if change == "version":
            catalog["source_version"] = "different-vintage"
        elif change == "config_hash":
            catalog["source_config_sha256"] = "a" * 64
        elif change == "missing_id":
            snapshot["observations"][0]["target_ids"] = [10, 999]
        elif change == "missing_ids":
            del snapshot["observations"][0]["target_ids"]
        elif change == "duplicate_ids":
            snapshot["observations"][0]["target_ids"] = [10, 10]
        else:
            catalog = None
        result = sighting_lifecycle(READ_ROUTE, snapshot=snapshot, challenge_catalog=catalog)
        assert result["lines"] == [] and result["links"] == [], change
        assert result["list"] == "Species alpha · 2026-10-15", change


def test_supplied_target_name_is_literal_text_and_cannot_create_markup_or_links():
    name = '<img src=x onerror="alert(1)">'
    snapshot, catalog = inclusion_fixture([{"id": 100, "name": name, "rank": "genus"}], [100])
    result = sighting_lifecycle(READ_ROUTE, snapshot=snapshot, challenge_catalog=catalog)
    assert result["lines"] == ["Included through listed genus " + name]
    assert result["links"] == [{"name": name, "href": "https://www.inaturalist.org/taxa/100", "tag": "a"}]
    result = sighting_lifecycle(
        "const button=nodes.recentChallengeList.children[0];button.onclick();"
        "const all=el=>[el,...el.children.flatMap(all)];"
        "console.log(JSON.stringify({images:all(curPopup.root).filter(el=>el.tag==='img').length,"
        "nestedLinks:all(button).filter(el=>el.tag==='a').length}));",
        snapshot=snapshot,
        challenge_catalog=catalog,
    )
    assert result == {"images": 0, "nestedLinks": 0}


def test_shared_position_chooser_keeps_each_reports_route_and_restores_link_focus_in_french():
    snapshot, catalog = inclusion_fixture(
        [{"id": 10, "name": "Listed alpha", "rank": "species"}, {"id": 100, "name": "Helix", "rank": "genus"}],
        [100],
    )
    other = dict(snapshot["observations"][0], id=2, target_ids=[10], name="Other identification")
    snapshot["observations"].append(other)
    result = sighting_lifecycle(
        "const feature=map.sources['recent-challenge'].data.features[0];"
        "layerHandlers['recent-challenge:click']({features:[feature]});const body=curPopup.root.children[1];"
        "const all=el=>[el,...el.children.flatMap(all)],flat=el=>el.textContent+el.children.map(flat).join('');"
        "const route=()=>body.children.filter(el=>el.className==='sighting-inclusion').map(flat);"
        "const first=route();all(body).find(el=>el.dataset.sightingAction==='next').onclick();const second=route();"
        "const link=all(body).find(el=>el.dataset.inclusionTarget);link.focus();LANG='fr';optionalLayers.refresh();"
        "const translated=route(),focus=document.activeElement.dataset.sightingAction;"
        "console.log(JSON.stringify({count:feature.properties.count,first,second,translated,focus,"
        "list:nodes.recentChallengeList.children.map(el=>el.textContent)}));",
        snapshot=snapshot,
        challenge_catalog=catalog,
    )
    assert result["count"] == 2
    assert result["first"] == ["Included through listed species Listed alpha"]
    assert result["second"] == ["Included through listed genus Helix"]
    assert result["translated"] == ["Inclus via le genre répertorié Helix"]
    assert result["focus"] == "target-100"
    assert any("Inclus via l’espèce répertoriée Listed alpha" in line for line in result["list"])
    assert any("Inclus via le genre répertorié Helix" in line for line in result["list"])
