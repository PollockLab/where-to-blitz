"""The CSS legend swatches must be the colormaps the tiles are actually rendered with.

The legend once carried a YlGnBu gradient with dark on the "biggest gaps" end while the
tiles were rendered through viridis, whose high end is bright yellow — so a well-sampled
city read as the biggest gap. Nothing caught it, because the swatch lives in CSS and the
colormap lives in Python and the two never meet. This is that meeting.
"""
import re
from pathlib import Path

import matplotlib
import numpy as np
import pytest
from matplotlib.colors import to_hex

TEMPLATE = Path(__file__).resolve().parent / "webapp" / "index.html"

# selector -> the colormap the corresponding raster is baked with
RAMPS = {
    ".bar": "viridis",                 # "skip -> go here" bar, How-scored panel
    "#maplegend .ramp": "viridis",     # priority legend, build_grid_values.py
    "#maplegend .ramp.magma": "magma",  # density overlay, build_density_pmtiles.py
}


def _stops(selector):
    """The hex stops of the linear-gradient declared for exactly this selector."""
    css = TEMPLATE.read_text()
    # match the selector at the start of a rule, not as a prefix of a longer one
    rule = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert rule, f"no CSS rule for {selector}"
    grad = re.search(r"linear-gradient\(90deg,([^)]*)\)", rule.group(1))
    assert grad, f"no linear-gradient in the rule for {selector}"
    return [s.strip().lower() for s in grad.group(1).split(",")]


@pytest.mark.parametrize(("selector", "cmap_name"), RAMPS.items())
def test_swatch_samples_its_colormap(selector, cmap_name):
    stops = _stops(selector)
    cmap = matplotlib.colormaps[cmap_name]
    expected = [to_hex(cmap(x)) for x in np.linspace(0, 1, len(stops))]
    assert stops == expected, (
        f"{selector} is not {cmap_name}: the legend would contradict the pixels it labels"
    )


def test_priority_copy_says_brighter_not_darker():
    """Viridis puts high priority at the bright end, so the copy cannot say 'darker'."""
    css = TEMPLATE.read_text().lower()
    for stale in ("darker = higher priority", "darker = go there",
                  "plus foncé = priorité", "plus foncé = y aller"):
        assert stale not in css, f"stale legend copy: {stale!r}"


def test_no_placeholder_survives_the_build():
    """Every __PLACEHOLDER__ in the template must be substituted by build_webapp.py."""
    built = TEMPLATE.parent.parent / "index.html"
    if not built.exists():
        pytest.skip("run build_webapp.py first")
    leftover = sorted(set(re.findall(r"__[A-Z][A-Z0-9_]*__", built.read_text())))
    assert not leftover, f"unsubstituted placeholders shipped: {leftover}"


def test_lattice_is_injected_not_hardcoded():
    """The clickable lattice must come from grid_lattice, not a second copy in the template.

    A drifted origin would put the clickable cells off the raster grid they label — the
    misalignment this app was already reported for.
    """
    import grid_lattice

    src = TEMPLATE.read_text()
    for placeholder in ("__LATTICE_X0__", "__LATTICE_Y1__", "__LATTICE_W__", "__LATTICE_H__"):
        assert placeholder in src, f"{placeholder} is hardcoded again in the template"

    built = TEMPLATE.parent.parent / "index.html"
    if not built.exists():
        pytest.skip("run build_webapp.py first")
    line = re.search(r"const LATTICE_X0=(-?\d+), LATTICE_Y1=(-?\d+), LATTICE_W=(\d+), LATTICE_H=(\d+)",
                     built.read_text())
    assert line, "the built lattice constants are missing or malformed"
    assert [int(g) for g in line.groups()] == [
        grid_lattice.X0, grid_lattice.Y1, grid_lattice.WIDTH_M, grid_lattice.HEIGHT_M]
