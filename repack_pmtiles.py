#!/usr/bin/env python3
"""Drop the top zoom levels of a raster PMTiles so its whole directory fits in the first 16 KB.

climate_gap.pmtiles (rio-pmtiles 1.2.1, z0-9) holds 13,960 tiles. That overflows the root directory into a
leaf directory at the end of the 15 MB file, so turning the overlay on waits one extra round trip for it.
Cut to z0-8 (3,864 tiles) the root is 7 KB and there is no leaf directory: header and directory arrive in
the first 16 KB read. z9 adds no detail: the CHELSA source is ~1 km, and over 40 sampled tiles z9 differs
from z8 upsampled 2x by a mean of 0.44 RGB levels against a tile std of 32.
MapLibre overzooms z8 past the archive's maxzoom.

Tiles are copied byte for byte, nothing is re-encoded. Run with the project venv (pmtiles comes with
rio-pmtiles):
    python repack_pmtiles.py tiles/climate_gap.pmtiles climate_gap_z8.pmtiles --maxzoom 8
"""

import argparse
import os
import sys

from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import zxy_to_tileid
from pmtiles.writer import Writer

FIRST_READ = 16384  # pmtiles.js fetches this many bytes for the header and root directory


def read(path):
    with open(path, "rb") as f:
        rd = Reader(MmapSource(f))
        h, meta = rd.header(), rd.metadata()
        tiles = {zxy: bytes(d) for zxy, d in all_tiles(lambda o, n: (f.seek(o), f.read(n))[1])}
    return h, meta, tiles


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--maxzoom", type=int, required=True)
    a = ap.parse_args()
    h, meta, tiles = read(a.src)
    keep = {k: v for k, v in tiles.items() if k[0] <= a.maxzoom}
    if not keep:
        sys.exit(f"no tiles at or below z{a.maxzoom} in {a.src}")
    meta = dict(meta)
    if "maxzoom" in meta:
        meta["maxzoom"] = a.maxzoom
    hdr = {
        k: h[k]
        for k in (
            "tile_type",
            "tile_compression",
            "min_lon_e7",
            "min_lat_e7",
            "max_lon_e7",
            "max_lat_e7",
            "center_lon_e7",
            "center_lat_e7",
        )
    }
    hdr.update(min_zoom=h["min_zoom"], max_zoom=a.maxzoom, center_zoom=min(h["center_zoom"], a.maxzoom))
    with open(a.out, "wb") as f:
        w = Writer(f)
        for tid, k in sorted((zxy_to_tileid(*k), k) for k in keep):
            w.write_tile(tid, keep[k])
        w.finalize(hdr, meta)

    nh, _, ntiles = read(a.out)
    if ntiles != keep:
        sys.exit("repacked tiles differ from the source")
    if nh["leaf_directory_length"] or nh["root_offset"] + nh["root_length"] > FIRST_READ:
        sys.exit(f"directory does not fit in the first {FIRST_READ} bytes; lower --maxzoom")
    print(
        f"{a.out}: z{nh['min_zoom']}-{nh['max_zoom']}, {len(ntiles)} tiles, "
        f"{os.path.getsize(a.src):,} -> {os.path.getsize(a.out):,} bytes, root-only directory"
    )


if __name__ == "__main__":
    main()
