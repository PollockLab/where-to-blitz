"""Literal genus targets retain species-level diversity and source-bound ancestry."""

import copy
import json
from urllib.parse import urlparse

import pytest

import conservation_challenge as challenge
from test_conservation_challenge import AS_OF, CONFIG, FixtureAPI, observation


def genus_config():
    config = copy.deepcopy(CONFIG)
    config["taxa"] = [{"id": 100, "name": "Genus", "rank": "genus"}]
    return config


def identity(taxon_id, name, rank):
    return {"id": taxon_id, "name": name, "rank": rank, "is_active": True}


def taxonomy(taxon_id=10, name="Genus alpha", rank="species", ancestors=None):
    return {
        "results": [
            {
                **identity(taxon_id, name, rank),
                "ancestors": ancestors if ancestors is not None else [identity(100, "Genus", "genus")],
            }
        ]
    }


class GenusAPI(FixtureAPI):
    def __init__(self, rows, facts):
        super().__init__(rows)
        self.facts, self.taxonomy_calls = facts, []

    def __call__(self, url):
        if url.startswith("https://api.inaturalist.org/v1/taxa/"):
            self.taxonomy_calls.append(url)
            return copy.deepcopy(self.facts[int(urlparse(url).path.rsplit("/", 1)[1])])
        return super().__call__(url)


def test_genus_descendants_keep_species_diversity_and_one_cached_taxonomy_lookup_per_identity():
    rows = [
        observation(1, taxon={"id": 30, "ancestor_ids": [100, 10]}),
        observation(2, taxon={"id": 30, "ancestor_ids": [100, 10]}),
        observation(3, taxon={"id": 20, "ancestor_ids": [100]}, observed_on="2026-10-14"),
        observation(4, taxon={"id": 100, "ancestor_ids": []}),
    ]
    facts = {
        30: taxonomy(
            30,
            "Genus alpha minor",
            "subspecies",
            [identity(100, "Genus", "genus"), identity(10, "Genus alpha", "species")],
        ),
        20: taxonomy(20, "Genus beta"),
        100: taxonomy(100, "Genus", "genus", []),
    }
    api = GenusAPI(rows, facts)
    snapshot = challenge.acquire(genus_config(), api, AS_OF)
    assert [row["id"] for row in snapshot["observations"]] == [1, 2, 3]
    assert [row["taxon_id"] for row in snapshot["observations"]] == [10, 10, 20]
    assert [row["id"] for row in challenge.select_observations(snapshot)] == [2, 3, 1]
    assert snapshot["manifest"]["source_count"] == 4
    assert snapshot["manifest"]["eligible_count"] == 3
    assert len(api.taxonomy_calls) == 3
    assert [entry["id"] for entry in snapshot["taxonomy"]] == [20, 30]
    assert all(row["target_ids"] == [100] for row in snapshot["observations"])
    assert challenge.validate_snapshot(snapshot) == snapshot


def simple_feed(config=None, **changes):
    row = observation(taxon={"id": 10, "ancestor_ids": [100]}, **changes)
    return challenge.acquire(config or genus_config(), GenusAPI([row], {10: taxonomy()}), AS_OF)


def test_taxon_api_omits_only_life_root_from_ranked_ancestors():
    row = observation(taxon={"id": 10, "ancestor_ids": [48460, 100]})
    feed = challenge.acquire(genus_config(), GenusAPI([row], {10: taxonomy()}), AS_OF)
    assert feed["observations"][0]["taxon_id"] == 10
    row["taxon"]["ancestor_ids"].append(999)
    with pytest.raises(ValueError, match="differs from verified"):
        challenge.acquire(genus_config(), GenusAPI([row], {10: taxonomy()}), AS_OF)


def test_overlapping_genus_species_and_variety_keep_all_exact_matches_without_broadening_variety():
    config = genus_config()
    config["taxa"] += [
        {"id": 10, "name": "Genus alpha", "rank": "species"},
        {
            "id": 30,
            "name": "Genus alpha minor",
            "rank": "variety",
            "species": {"id": 10, "name": "Genus alpha"},
        },
    ]
    rows = [
        observation(i, taxon={"id": taxon, "ancestor_ids": [100] + ([10] if taxon != 10 else [])})
        for i, taxon in enumerate((10, 30, 31), 1)
    ]
    facts = {
        10: taxonomy(),
        30: taxonomy(
            30,
            "Genus alpha minor",
            "variety",
            [identity(100, "Genus", "genus"), identity(10, "Genus alpha", "species")],
        ),
        31: taxonomy(
            31,
            "Genus alpha other",
            "variety",
            [identity(100, "Genus", "genus"), identity(10, "Genus alpha", "species")],
        ),
    }
    feed = challenge.acquire(config, GenusAPI(rows, facts), AS_OF)
    assert [row["target_ids"] for row in feed["observations"]] == [[10, 100], [10, 30, 100], [10, 100]]
    assert {row["taxon_id"] for row in feed["observations"]} == {10}


@pytest.mark.parametrize(
    "change", ["missing-species", "two-species", "inactive", "wrong-id", "wrong-genus", "missing-ancestors"]
)
def test_unverified_genus_descendant_ancestry_aborts_acquisition(change):
    fact = taxonomy(
        30,
        "Genus alpha minor",
        "subspecies",
        [identity(100, "Genus", "genus"), identity(10, "Genus alpha", "species")],
    )
    detail = fact["results"][0]
    if change == "missing-species":
        detail["ancestors"].pop()
    elif change == "two-species":
        detail["ancestors"].append(identity(20, "Genus beta", "species"))
    elif change == "inactive":
        detail["is_active"] = False
    elif change == "wrong-id":
        detail["id"] = 31
    elif change == "wrong-genus":
        detail["ancestors"][0]["id"] = 200
    else:
        detail.pop("ancestors")
    with pytest.raises(ValueError):
        challenge.acquire(
            genus_config(),
            GenusAPI([observation(taxon={"id": 30, "ancestor_ids": [100, 10]})], {30: fact}),
            AS_OF,
        )


@pytest.mark.parametrize(
    "privacy", [{"obscured": True}, {"geoprivacy": "obscured"}, {"taxon_geoprivacy": "private"}]
)
def test_genus_support_does_not_publish_restricted_records_or_unused_taxonomy(privacy):
    feed = simple_feed(place_guess="PRIVATE SENTINEL", user={"login": "PRIVATE SENTINEL"}, **privacy)
    assert feed["observations"] == [] and feed["taxonomy"] == []
    assert feed["manifest"]["source_count"] == 1
    assert "PRIVATE SENTINEL" not in json.dumps(feed)


def test_taxonomy_allowlist_drops_extra_fields_and_raw_passes_still_must_agree():
    fact = taxonomy()
    fact["results"][0]["private_extra"] = "PRIVATE SENTINEL"
    api = GenusAPI([observation(taxon={"id": 10, "ancestor_ids": [100]})], {10: fact})
    feed = challenge.acquire(genus_config(), api, AS_OF)
    assert "PRIVATE SENTINEL" not in json.dumps(feed)

    class ChangingAPI(GenusAPI):
        def __call__(self, url):
            if url.startswith(challenge.API) and len(self.calls) >= 2:
                self.rows[0]["ignored_raw_context"] = "changed between passes"
            return super().__call__(url)

    with pytest.raises(ValueError, match="changed between"):
        challenge.acquire(genus_config(), ChangingAPI(api.rows, {10: fact}), AS_OF)


@pytest.mark.parametrize(
    "change",
    ["missing", "missing-entry", "group-id", "group-name", "target", "lookup", "private-field", "rank"],
)
def test_downloaded_genus_evidence_cannot_be_removed_or_reassigned(change):
    feed = simple_feed()
    if change == "missing":
        feed.pop("taxonomy")
    elif change == "missing-entry":
        feed["taxonomy"] = []
    elif change == "group-id":
        feed["observations"][0]["taxon_id"] = 100
    elif change == "group-name":
        feed["observations"][0]["name"] = "Another species"
    elif change == "target":
        feed["observations"][0]["target_ids"] = [10]
    elif change == "lookup":
        feed["taxonomy"][0]["lookup"]["url"] += "?private=1"
    elif change == "private-field":
        feed["taxonomy"][0]["ancestors"][0]["user"] = "private"
    else:
        feed["taxonomy"][0]["rank"] = "genus"
    with pytest.raises(ValueError):
        challenge.validate_snapshot(feed)
        challenge.validate_target_mapping(feed, genus_config())


def test_genus_snapshot_preparation_is_source_bound_and_atomic(tmp_path):
    import prepare_optional_layers as optional
    from test_optional_layers import NOW, TAXA_URL, asset, checksum, encoded

    config, feed = genus_config(), simple_feed()
    public = tmp_path / "public"
    public.mkdir()
    last_good = public / "conservation_challenge.json"
    last_good.write_bytes(b"last good")

    def prepare(candidate):
        content, config_bytes = encoded(candidate), encoded(config)
        metadata = asset(1, content)
        values = {TAXA_URL: config_bytes, metadata["browser_download_url"]: content}
        return optional.prepare(
            public,
            taxa_url=TAXA_URL,
            taxa_sha=checksum(config_bytes),
            fetch=values.__getitem__,
            assets=lambda _: [metadata],
            now=NOW,
        )

    invalid = copy.deepcopy(feed)
    invalid.pop("taxonomy")
    with pytest.raises(ValueError, match="taxonomy"):
        prepare(invalid)
    assert last_good.read_bytes() == b"last good"
    prepare(feed)
    assert json.loads(last_good.read_bytes())["observations"][0]["taxon_id"] == 10


@pytest.mark.parametrize("national", [False, True])
def test_genus_species_selection_has_python_javascript_parity_and_unchanged_caps(national):
    from test_optional_layer_viewer import run_js

    rows = [observation(i, taxon={"id": 10 + i % 3, "ancestor_ids": [100]}) for i in range(1, 601)]
    facts = {
        taxon: taxonomy(taxon, name)
        for taxon, name in ((10, "Genus alpha"), (11, "Genus beta"), (12, "Genus gamma"))
    }
    feed = challenge.acquire(genus_config(), GenusAPI(rows, facts), AS_OF)
    selected = challenge.select_observations(feed, national=national)
    actual = run_js(
        "const p=JSON.parse(process.argv[1]);console.log(JSON.stringify(OptionalData.selectChallenge(p.feed,[-180,-90,180,90],p.national).map(r=>r.id)));",
        {"feed": feed, "national": national},
    )
    assert actual == [row["id"] for row in selected]
    assert len(actual) == (500 if national else 150)
    assert len({row["taxon_id"] for row in selected[:3]}) == 3


def test_literal_genus_compiles_as_one_target_with_no_species_expansion(tmp_path):
    from audit_challenge_taxa import audit
    from build_challenge_taxa import generate

    raw = b"Common.name,Scientific.name\nGroup,Genus spp.\nDuplicate,Genus spp.\n"
    report = audit(
        raw, lambda _: {"total_results": 1, "results": [identity(100, "Genus", "genus")]}, tmp_path / "cache"
    )
    public, config = generate(json.dumps(report).encode(), approved=True)
    result = json.loads(config)
    assert result["taxa"] == [{"id": 100, "name": "Genus", "rank": "genus"}]
    assert result["coverage"]["resolved"] == [
        {"csv_row": 2, "target_id": 100},
        {"csv_row": 3, "target_id": 100},
    ]
    assert result["coverage"]["omitted_rows"] == 0
    assert generate(public, approved=True) == (public, config)
    report["rows"][0]["raw_scientific_name"] = "Genus"
    with pytest.raises(ValueError, match="explicit spp"):
        generate(json.dumps(report).encode(), approved=True)


def test_genus_synonym_cannot_silently_expand_into_a_broader_accepted_genus():
    from audit_challenge_taxa import resolve

    response = {
        "total_results": 1,
        "results": [
            {
                **identity(100, "Broader", "genus"),
                "names": [{"name": "Narrow", "lexicon": "scientific-names"}],
            }
        ],
    }
    assert resolve("Narrow spp.", response)["status"] == "needs_review"


def test_genus_catalog_keeps_rank_with_sightings_off_and_requires_source_bound_evidence(tmp_path):
    from challenge_catalog import load_catalog
    from test_optional_layer_viewer import sighting_lifecycle

    config, feed = genus_config(), simple_feed()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    catalog = load_catalog(path, feed)
    assert catalog["taxa"] == [{"id": 100, "name": "Genus", "rank": "genus"}]
    invalid = copy.deepcopy(feed)
    invalid.pop("taxonomy")
    with pytest.raises(ValueError, match="taxonomy"):
        load_catalog(path, invalid)
    result = sighting_lifecycle(
        "OptionalData.fetchPinned=()=>{throw Error('catalog fetched observations');};"
        "const en={rank:nodes.challengeCatalogList.children[0].children[1].textContent,context:nodes.challengeCatalogContext.textContent};"
        "LANG='fr';optionalLayers.refresh();console.log(JSON.stringify({en,fr:{rank:nodes.challengeCatalogList.children[0].children[1].textContent,context:nodes.challengeCatalogContext.textContent},open:nodes.recentChallengeCoverage.open||false}));",
        start_recent=False,
        challenge_catalog=catalog,
    )
    assert result["en"]["rank"] == " (genus)" and "species-level" in result["en"]["context"]
    assert result["fr"]["rank"] == " (genre)" and "niveau de l’espèce" in result["fr"]["context"]
    assert result["open"] is False


def test_taxonomy_cannot_assign_conflicting_names_to_one_shared_species():
    rows = [
        observation(i, taxon={"id": taxon, "ancestor_ids": [100, 10]}) for i, taxon in enumerate((30, 31), 1)
    ]
    facts = {
        taxon: taxonomy(
            taxon,
            name,
            "subspecies",
            [identity(100, "Genus", "genus"), identity(10, species_name, "species")],
        )
        for taxon, name, species_name in (
            (30, "Genus alpha minor", "Genus alpha"),
            (31, "Genus alpha other", "Conflicting species"),
        )
    }
    with pytest.raises(ValueError, match="shared identity"):
        challenge.acquire(genus_config(), GenusAPI(rows, facts), AS_OF)
