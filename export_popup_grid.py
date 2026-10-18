"""Export popup axes and the shared cell inventory from the final 25 km colour stacks.

Run after all 5 km stacks have been aggregated, before country-mask and Getting Even
builds. Native 25 km builds fit the ramps; their intermediate JSON is replaced here.
No remote inputs are read and no ramps or raster values are changed.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rasterio

from fullgrid_outputs import write_webapp_json
from grid_lattice import Lattice
from grid_schema import BANDS


def export_popup_grid(directory):
    directory = Path(directory)
    index = json.loads((directory / "index.json").read_text())
    if index["res_m"] != 25000 or index["row_format"] != ["lat", "lon"] + BANDS:
        raise ValueError("popup export requires the declared 25 km band schema")
    groups = index["groups"]
    if not groups or set(groups) != set(index["files"]) or "All biodiversity" not in groups:
        raise ValueError("popup export requires all-taxa and matching group files")
    canonical = directory / "grid_25000m/All_biodiversity.tif"
    with rasterio.open(canonical) as source:
        lat = Lattice(25000, source.crs)
        mask = np.isfinite(source.read(1))
    rows, cols = np.where(mask)
    if not len(rows):
        raise ValueError("final all-taxa support is empty")
    lon, latitude = lat.centres_lonlat(rows, cols)
    fields = SimpleNamespace(n=len(rows), clat=latitude, clon=lon)
    selected = {}
    # Validate the entire set before replacing any published file.
    for group in groups:
        expected_name = f"webapp_data_{group.replace(' ', '_')}.json"
        if index["files"][group] != expected_name:
            raise ValueError(f"{group}: unexpected popup filename")
        with rasterio.open(directory / "grid_25000m" / f"{group.replace(' ', '_')}.tif") as source:
            if (source.crs, source.transform, source.shape) != (lat.crs, lat.transform, lat.shape):
                raise ValueError(f"{group}: final stack differs from the canonical lattice")
            if source.descriptions != tuple(BANDS):
                raise ValueError(f"{group}: final stack differs from the band schema")
            planes = source.read()
        if not np.array_equal(np.isfinite(planes[0]), mask):
            raise ValueError(f"{group}: final stack differs from all-taxa support")
        bands = {band: planes[i, rows, cols] for i, band in enumerate(BANDS)}
        counts = bands["n_train"]
        if not np.all(np.isfinite(counts) & (counts >= 0) & (counts == np.floor(counts))):
            raise ValueError(f"{group}: n_train must contain finite nonnegative integer counts")
        selected[group] = bands
    if rasterio.CRS.from_string(index["crs"]) != lat.crs:
        raise ValueError("index CRS differs from the final stacks")
    declared = index["lattice"]
    if any(
        declared[k] != value
        for k, value in {"x0": lat.x0, "y1": lat.y1, "nrow": lat.nrow, "ncol": lat.ncol}.items()
    ):
        raise ValueError("index geometry differs from the final stacks")
    index["n_cells"] = len(rows)
    index["popup_source"] = "final grid_25000m stacks, aggregated from 5 km; float32 axes retained"
    index["discover_method"] = "NaN-aware mean of the 5 km under-sampling scores, including their climate ordering"
    index["resample_note"] = "25 km axes and travel are means of supported 5 km child values; n_train sums child counts"
    index["ramps"] = "National ramps fitted on native 25 km fields and applied at 5 km; displayed 25 km axes aggregate the scored children"
    index.setdefault("axes_status", {})["n_train"] = "REAL-proxy (sum of rounded 5 km density-derived record counts)"
    index["land_mask"] = "Union of supported 5 km child cells in the final aggregated all-taxa stack"
    index["density_vintage"] = (
        "iNaturalist (Biodiversite Quebec) inat_canada_heatmaps; n_train sums the rounded 5 km child counts"
    )
    with tempfile.TemporaryDirectory(dir=directory, prefix=".popup-export-") as temporary:
        staged = Path(temporary)
        for group in groups:
            write_webapp_json(fields, group, selected[group], output_dir=staged)
        payload = json.dumps(index, indent=2) + "\n"
        for name in index["files"].values():
            os.replace(staged / name, directory / name)
        (directory / "grid_25000m/index.json").write_text(payload)
        (directory / "index.json").write_text(payload)
    print(f"exported {len(groups)} popup groups on {len(rows):,} final 25 km cells")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("cluster_results/ca"))
    export_popup_grid(parser.parse_args().directory)
