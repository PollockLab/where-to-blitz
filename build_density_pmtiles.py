#!/usr/bin/env python3
"""Build high-resolution iNaturalist density raster PMTiles for where-to-blitz.

Guillaume's 100 m density COGs (object-arbutus) are striped (block 54810x1, no
overviews), so a remote tiler times out at low zoom. We can't write to that
bucket, so instead we bake the magma colormap (rescale 0..10, matching the
app's TiTiler call) into transparent RGBA web tiles and ship them as raster
PMTiles served statically from GitHub Pages. The density overlay then gets
finer as you zoom (native to ~z9 / ~300 m), with the ~25 km priority cells
unchanged.

Pipeline per taxon: download 100 m COG -> render Float32 -> magma RGBA
(alpha=0 where count<=0) -> rio pmtiles (reproject to WebMercator, z0..9, WEBP).

Run with the project venv (rasterio, rio-cogeo, rio-pmtiles, matplotlib):
    python build_density_pmtiles.py [TAXON ...]
Defaults to all dec25 taxa. Outputs <taxon>.pmtiles into ./pmtiles/.
"""
import io
import subprocess
import sys
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from rasterio.enums import ColorInterp

# dec25 taxa served at 100 m on object-arbutus (Fungi has no dec25 layer).
TAXA = ["All", "Amphibia", "Aves", "Insecta", "Mammalia", "Plantae",
        "Reptilia", "Actinopterygii", "Arachnida", "Mollusca"]
SRC_URL = ("https://object-arbutus.alliancecan.ca/86e1f3d5df8442d39450533329f621ae"
           ":stac/inat_canada_heatmaps/{taxon}_density_inat_dec25_100m.tif")

RMIN, RMAX = 0.0, 10.0     # matches the app's rescale=0,10
ZOOM = "0..9"              # native to ~z9 (~300 m); Leaflet over-zooms past that
HERE = Path(__file__).resolve().parent
OUT = HERE / "density"          # served relative to index.html on GitHub Pages
OUT.mkdir(exist_ok=True)

_MAGMA = matplotlib.colormaps["magma"]
_LUT = (np.asarray([_MAGMA(i / 255.0) for i in range(256)])[:, :3] * 255
        ).round().astype(np.uint8)


def render_rgba(src_path: Path, dst_path: Path) -> int:
    """Float32 counts -> magma RGBA GeoTIFF; alpha=0 where count<=0. Returns visible px."""
    with rasterio.open(src_path) as src:
        data = src.read(1).astype(np.float32)
        nodata = src.nodata
        prof = src.profile

    valid = np.isfinite(data)
    if nodata is not None:
        valid &= data != nodata
    visible = valid & (data > 0)

    norm = np.clip((data - RMIN) / (RMAX - RMIN), 0, 1)
    rgb = _LUT[(norm * 255).round().astype(np.uint8)]
    alpha = np.where(visible, 255, 0).astype(np.uint8)

    prof.update(count=4, dtype="uint8", nodata=None, compress="deflate",
                tiled=True, blockxsize=512, blockysize=512)
    prof.pop("photometric", None)
    with rasterio.open(dst_path, "w", **prof) as dst:
        dst.write(rgb[..., 0], 1)
        dst.write(rgb[..., 1], 2)
        dst.write(rgb[..., 2], 3)
        dst.write(alpha, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green,
                           ColorInterp.blue, ColorInterp.alpha]
    return int(visible.sum())


def build(taxon: str) -> None:
    src = OUT / f"_{taxon}_src.tif"
    rgba = OUT / f"_{taxon}_rgba.tif"
    out = OUT / f"{taxon}.pmtiles"
    url = SRC_URL.format(taxon=taxon)

    subprocess.run(["curl", "-sf", "-o", str(src), url], check=True)
    visible = render_rgba(src, rgba)
    subprocess.run(["rio", "pmtiles", str(rgba), str(out), "--zoom-levels",
                    ZOOM, "--format", "WEBP", "--resampling", "cubic"], check=True)
    src.unlink(missing_ok=True)
    rgba.unlink(missing_ok=True)
    print(f"{taxon}: {visible:,} visible px -> {out.name} "
          f"{out.stat().st_size / 1e6:.1f} MB")


def main() -> None:
    taxa = sys.argv[1:] or TAXA
    total = 0
    for taxon in taxa:
        build(taxon)
        total += (OUT / f"{taxon}.pmtiles").stat().st_size
    print(f"\ntotal: {total / 1e6:.1f} MB across {len(taxa)} taxa")


if __name__ == "__main__":
    main()
