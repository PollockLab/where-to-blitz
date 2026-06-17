#!/usr/bin/env python3
"""Render `where-to-blitz-walkthrough.ipynb` to the public methodology HTML page
(`where-to-blitz-walkthrough.html`) that the in-app "Full methodology" link points at.

Plain `nbconvert --to html` leaves every matplotlib figure without alt text (it only
sources alt for markdown `![alt](src)` images, then back-fills outputs with a useless
"No description has been provided for this image"). This wrapper renders the notebook,
then gives each figure meaningful alt derived from the section heading it sits under —
so the page is accessible to screen-reader users. Run after re-executing the notebook:

    .venv/bin/python -m nbconvert --to notebook --execute --inplace where-to-blitz-walkthrough.ipynb
    .venv/bin/python render_walkthrough_html.py
"""
import re
import subprocess
import sys

import nbformat
from bs4 import BeautifulSoup

NB = "where-to-blitz-walkthrough.ipynb"
OUT = "where-to-blitz-walkthrough.html"


def figure_alts(nb_path):
    """Ordered alt text, one per image output, from the heading each figure sits under."""
    nb = nbformat.read(nb_path, as_version=4)
    last_heading, alts = None, []
    for cell in nb.cells:
        if cell.cell_type == "markdown":
            for line in cell.source.splitlines():
                m = re.match(r"#{1,4}\s+(.*)", line.strip())
                if m:
                    # strip markdown emphasis and the "3.1 · " section prefix
                    h = re.sub(r"[*_`]", "", m.group(1)).strip()
                    h = re.sub(r"^[\d.]+\s*·\s*", "", h)
                    last_heading = h
        elif cell.cell_type == "code":
            for out in cell.get("outputs", []):
                if out.get("data", {}).get("image/png"):
                    alts.append(f"Figure: {last_heading}" if last_heading else "Figure")
    return alts


def main():
    r = subprocess.run(
        [sys.executable, "-m", "nbconvert", "--to", "html", "--output", OUT, NB]
    )
    if r.returncode:
        sys.exit(r.returncode)

    alts = figure_alts(NB)
    soup = BeautifulSoup(open(OUT, encoding="utf-8").read(), "html.parser")
    imgs = soup.select(".jp-OutputArea-output img")
    if len(imgs) != len(alts):
        sys.exit(
            f"alt count mismatch: {len(imgs)} output images vs {len(alts)} figure headings "
            "— re-execute the notebook, then re-run."
        )
    for img, alt in zip(imgs, alts):
        img["alt"] = alt
    open(OUT, "w", encoding="utf-8").write(str(soup))
    print(f"wrote {OUT} with alt text on {len(imgs)} figures")


if __name__ == "__main__":
    main()
