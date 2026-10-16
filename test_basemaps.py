"""Provider selection must not send unkeyed previews to watermark-only tiles."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
SCRIPT = (ROOT / "webapp/basemaps.js").read_text()


def catalog(key):
    result = subprocess.run(
        [
            "node",
            "-e",
            SCRIPT + "\nconsole.log(JSON.stringify(BasemapCatalog.create(JSON.parse(process.argv[1]))));",
            json.dumps(key),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_unkeyed_preview_uses_osm_for_standard_and_density_background(key):
    sources = catalog(key)
    assert set(sources) == {"Standard", "Satellite", "Terrain", "Light"}
    for name in ("Standard", "Light"):
        source = sources[name]
        assert source["tiles"] == ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]
        assert source["maxzoom"] == 19
        assert "www.openstreetmap.org/copyright" in source["attribution"]
        assert "CARTO" not in source["attribution"]
    assert all("cartocdn" not in url for source in sources.values() for url in source["tiles"])


def test_configured_carto_preserves_styles_and_encodes_key_as_one_query_parameter():
    sources = catalog(" fixture&another=value ")
    for name, style in (("Standard", "rastertiles/voyager"), ("Light", "light_all")):
        assert sources[name]["tiles"] == [
            "https://basemaps.cartocdn.com/" + style + "/{z}/{x}/{y}.png?key=fixture%26another%3Dvalue"
        ]
        assert sources[name]["maxzoom"] == 20
        assert "CARTO" in sources[name]["attribution"]
        assert "OpenStreetMap" in sources[name]["attribution"]
    assert all("key=" not in url for name in ("Terrain", "Satellite") for url in sources[name]["tiles"])


def test_independent_providers_carry_their_own_credits():
    sources = catalog("")
    assert "Esri" in sources["Satellite"]["attribution"]
    assert "Earthstar" in sources["Satellite"]["attribution"]
    assert "OpenStreetMap" not in sources["Satellite"]["attribution"]
    assert "SRTM" in sources["Terrain"]["attribution"]
    assert "OpenTopoMap" in sources["Terrain"]["attribution"]
    assert "creativecommons.org/licenses/by-sa/3.0/" in sources["Terrain"]["attribution"]
    assert all("CARTO" not in sources[name]["attribution"] for name in ("Satellite", "Terrain"))


def test_build_injects_module_and_json_key_from_outside_repository(tmp_path):
    webapp = tmp_path / "webapp"
    webapp.mkdir()
    # A minimal template exercises the builder independently of the browser layout.
    (webapp / "index.html").write_text(
        "__BASEMAP_SCRIPT__\nconst CARTO_KEY=__CARTO_KEY_JSON__;\nBasemapCatalog.create(CARTO_KEY);"
    )
    target = tmp_path / "cluster_results/ca"
    target.mkdir(parents=True)
    (target / "index.json").write_text(json.dumps({"files": {"All biodiversity": "all.json"}}))
    key = "fixture'\"</script>&value"
    env = {
        k: v for k, v in os.environ.items() if k not in ("DISCOVERY_RATE_FILE", "CONSERVATION_CHALLENGE_FILE")
    }
    env.update(PYTHONPATH=str(ROOT), CARTO_API_KEY=key)
    result = subprocess.run(
        [sys.executable, str(ROOT / "build_webapp.py")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "index.html").read_text()
    assert "__BASEMAP_SCRIPT__" not in html and "__CARTO_KEY_JSON__" not in html
    assert SCRIPT in html
    assert "</script>" not in html
    literal = html.split("const CARTO_KEY=", 1)[1].split(";", 1)[0]
    assert json.loads(literal) == key
    assert key not in result.stdout + result.stderr
