"""The cell popup's GPX export is a pure, offline string builder with escaped text and a closed boundary."""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from test_goal_context import _function

TEMPLATE = Path(__file__).parent / "webapp" / "index.html"
NS = {"g": "http://www.topografix.com/GPX/1/1"}
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _gpx(resM=25000, taxon="Birds", opts=None):
    src = TEMPLATE.read_text()
    # A flat 1 degree box stands in for the LAEA port, which cellGpx only reaches through cellPoly.
    script = (
        "const esc=" + src.split("const esc=")[1].split("\n")[0]
        + "\nconst cellPoly=(lat,lon,r)=>[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]].map(d=>[lon+d[0],lat+d[1]]);\n"
        + _function(src, "cellGpx")
        + f"\nprocess.stdout.write(cellGpx(49.5,-123.5,{resM},{json.dumps(taxon)},{json.dumps(opts)}));"
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return ET.fromstring(result.stdout)


def test_waypoint_and_closed_boundary_track():
    root = _gpx(opts={"facts": "Score 80", "names": ["Robin", "Wren"], "url": "https://x.test/?at=49.5,-123.5"})
    assert root.get("creator") == "where-to-blitz"
    assert root.find("g:metadata/g:link", NS).get("href") == "https://x.test/?at=49.5,-123.5"
    (wpt,) = root.findall("g:wpt", NS)
    assert (float(wpt.get("lat")), float(wpt.get("lon"))) == (49.5, -123.5)
    assert wpt.find("g:name", NS).text.endswith("25 km cell 49.500, -123.500")
    desc = wpt.find("g:desc", NS).text
    assert desc.splitlines() == ["Score 80", "Birds", "https://x.test/?at=49.5,-123.5", "Robin", "Wren"]
    (seg,) = root.findall("g:trk/g:trkseg", NS)
    pts = [(float(p.get("lat")), float(p.get("lon"))) for p in seg.findall("g:trkpt", NS)]
    assert len(pts) == 5 and pts[0] == pts[-1]
    assert all(48.5 <= la <= 50.5 and -124.5 <= lo <= -122.5 for la, lo in pts)


def test_special_characters_are_escaped():
    root = _gpx(taxon="Fish & <Frogs>", opts={"names": ["A&B <i>"], "url": "https://x.test/?a=1&b=2"})
    assert "A&B <i>" in root.find("g:wpt/g:desc", NS).text
    assert "Fish & <Frogs>" in root.find("g:wpt/g:desc", NS).text
    assert root.find("g:metadata/g:link", NS).get("href") == "https://x.test/?a=1&b=2"


def test_missing_opts_still_valid():
    root = _gpx(resM=5000, opts=None)
    assert "5 km cell" in root.find("g:wpt/g:name", NS).text
    assert root.find("g:wpt/g:desc", NS).text == "Birds"
