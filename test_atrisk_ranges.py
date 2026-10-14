"""Offline tests of the listing range clip: point in range, buffer, max weight, infraspecific records, no range."""

import io
import json

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point, Polygon, box, mapping
from shapely.ops import transform

from atrisk_ranges import (
    assign_ranges,
    buffered_union,
    dfo_join_key,
    is_population,
    listing_entries,
    load_range_geoms,
    match_dfo,
    read_columns,
    score_cells,
    transformer,
)

HDR = "specieskey\tacceptedtaxonkey\tyi\txi\tn\n"
# Unit grid: fine cell (yi, xi) has its centre at (xi + 0.5, yi + 0.5); ten fine cells per layer cell.
FINE, RES = 1, 10
GEOMS = {"west": box(0, 0, 10, 10), "east": box(20, 0, 30, 10)}


def ident(x, y):
    return x, y


def listing(lid, weight, names, ranges=(), common="Thing"):
    return {
        "listing_id": lid,
        "common_name": common,
        "names": list(names),
        "status_weight": weight,
        "range_source": "dfo" if ranges else "none",
        "range_ids": list(ranges),
    }


def run(tsv, listings, crosswalk, buffer_m=0):
    entries = listing_entries(listings, crosswalk, GEOMS, buffer_m=buffer_m)
    return score_cells(read_columns(io.StringIO(HDR + tsv)), entries, ident, FINE, RES)


CW = {"Sp": (("K",), ""), "Sp sub": (("K",), "SUB")}


@pytest.mark.parametrize("count", [2, 1024, 1025])
def test_buffered_union_matches_original_geometry_and_point_masks(count):
    parts = [box(4 * (i % 40), 4 * (i // 40), 4 * (i % 40) + 1, 4 * (i // 40) + 1) for i in range(count)]
    expected = shapely.union_all(parts).buffer(1)
    got = buffered_union(parts, 1)
    assert got.is_valid and not got.is_empty
    assert got.bounds == expected.bounds
    assert expected.symmetric_difference(got).area < expected.area * 1e-12
    rng = np.random.default_rng(30114)
    xmin, ymin, xmax, ymax = expected.bounds
    points = shapely.points(rng.uniform([xmin, ymin], [xmax, ymax], size=(10_000, 2)))
    shapely.prepare(expected)
    shapely.prepare(got)
    np.testing.assert_array_equal(shapely.contains(expected, points), shapely.contains(got, points))


def test_fragmented_buffer_preserves_nested_collection_lines_and_points():
    polygon = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)], [[(2, 2), (6, 2), (6, 6), (2, 6)]])
    fillers = MultiPoint([(100 + i * 4, 0) for i in range(1025)])
    nested = GeometryCollection(
        [
            fillers,
            GeometryCollection([polygon, LineString([(20, 0), (20, 8)])]),
            GeometryCollection(),
        ]
    )
    parts = [nested, Point(30, 4)]
    expected = shapely.union_all(parts).buffer(0.5)
    got = buffered_union(parts, 0.5)
    assert expected.symmetric_difference(got).area < expected.area * 1e-12
    assert got.contains(Point(20.25, 4)) and got.contains(Point(30.25, 4))
    assert not got.contains(Point(4, 4))
    x = np.array([0.25, 4, 20.25, 30.25, 32, 100.25, 4196.25])
    y = np.array([4, 4, 4, 4, 4, 0, 0])
    np.testing.assert_array_equal(shapely.contains_xy(expected, x, y), shapely.contains_xy(got, x, y))


@pytest.mark.parametrize("distance", [0, -0.25])
def test_nonpositive_fragmented_buffer_unions_before_buffering(distance):
    # Eroding each touching square separately would introduce gaps in the continuous strip.
    parts = [box(i, 0, i + 1, 1) for i in range(1025)]
    got = buffered_union(parts, distance)
    assert got.equals(shapely.union_all(parts).buffer(distance))
    assert got.contains(Point(512, 0.5))


def test_empty_range_buffer_remains_empty():
    assert buffered_union([GeometryCollection()], 1).is_empty


def test_loaded_ranges_are_valid_after_projection(tmp_path):
    # Reduced from DFO dfo:363: a thin hole crosses the shell after projection.
    geographic = Polygon(
        [(-76.444, 44.088), (-76.797, 43.631), (-79.657, 43.223)],
        [[(-76.992, 43.835), (-76.641, 43.834), (-76.641, 43.835)]],
    )
    assert geographic.is_valid
    projected = transform(transformer(), geographic)
    assert not projected.is_valid
    path = tmp_path / "ranges.geojson"
    path.write_text(json.dumps({"features": [{"properties": {"range_id": "dfo:363"},
                                           "geometry": mapping(geographic)}]}))  # fmt: skip

    loaded = load_range_geoms(path)["dfo:363"]
    assert loaded.is_valid and not loaded.is_empty
    assert loaded.bounds == projected.bounds


def test_record_counts_only_inside_range():
    score, nsp, kept = run("K\tK\t5\t5\t3\nK\tK\t5\t15\t4\n", [listing("1", 2, ["Sp"], ["west"])], CW)
    assert score == {(0, 0): 2} and nsp == {(0, 0): 1}
    assert kept == {"1": (3, 7)}


def test_buffer_admits_records_near_the_edge():
    tsv = "K\tK\t5\t11\t1\n"  # centre x = 11.5, 1.5 outside the west range
    assert not run(tsv, [listing("1", 2, ["Sp"], ["west"])], CW, buffer_m=1)[0]
    assert run(tsv, [listing("1", 2, ["Sp"], ["west"])], CW, buffer_m=2)[0] == {(0, 1): 2}


def test_max_weight_across_listings_containing_the_record():
    lst = [
        listing("1", 1, ["Sp"], ["west"]),
        listing("2", 3, ["Sp"], ["west"]),
        listing("3", 2, ["Sp"], ["east"]),
    ]
    score, nsp, _ = run("K\tK\t5\t5\t1\nK\tK\t6\t6\t1\nK\tK\t5\t25\t1\n", lst, CW)
    assert score == {(0, 0): 3, (0, 2): 2}  # one species once per cell, best weight; east only takes 3
    assert nsp == {(0, 0): 1, (0, 2): 1}


def test_infraspecific_record_counts_outside_range():
    lst = [listing("1", 3, ["Sp sub"], ["west"])]
    score, _, kept = run("K\tSUB\t5\t25\t2\nK\tK\t5\t25\t9\n", lst, CW)
    assert score == {(0, 2): 3} and kept == {"1": (2, 11)}


def test_population_listing_does_not_take_subspecies_records_outside_range():
    lst = [listing("1", 3, ["Sp sub"], ["west"], common="Thing (Northern population)")]
    assert not run("K\tSUB\t5\t25\t2\n", lst, CW)[0]


def test_listing_without_range_counts_species_everywhere():
    score, _, kept = run("K\tK\t5\t5\t1\nK\tK\t50\t-50\t1\n", [listing("1", 1, ["Sp"])], CW)
    assert score == {(0, 0): 1, (5, -5): 1} and kept == {"1": (2, 2)}


def test_alias_keys_count_once_per_cell():
    cw = {"Sp": (("K", "K2"), "")}
    score, nsp, _ = run("K\tK\t5\t5\t1\nK2\tK2\t6\t6\t1\n", [listing("1", 3, ["Sp"], ["west"])], cw)
    assert score == {(0, 0): 3} and nsp == {(0, 0): 1}


def test_unmatched_listing_and_empty_download():
    assert listing_entries([listing("1", 3, ["Nope"])], CW, GEOMS) == []
    score, nsp, kept = run("", [listing("1", 2, ["Sp"], ["west"])], CW)
    assert not score and not nsp and kept == {"1": (0, 0)}


def test_range_assignment_order_and_dfo_join():
    lst = {
        "1": {"common": "Western Brook Lamprey (Morrison Creek population)", "provinces": {"BC"}},
        "2": {"common": "Great Blue Heron fannini subspecies", "provinces": {"BC"}},
        "3": {"common": "Painted Turtle", "provinces": {"BC", "AB"}},
        "4": {"common": "Fin Whale (Atlantic population)", "provinces": set()},
    }
    dfo = match_dfo(lst, [("Western Brook Lamprey", "Morrison Creek"), ("Fin Whale", "Pacific")])
    assert dfo == {"1": ("Western Brook Lamprey", "Morrison Creek")}
    got = assign_ranges(lst, dfo, {"2": ["13"]})
    assert got == {
        "1": ("dfo", ["dfo:1"]),
        "2": ("ecozone", ["eco:13:BC"]),
        "3": ("province", ["prov:AB", "prov:BC"]),
        "4": ("none", []),
    }
    assert dfo_join_key("Rockfish", "Pacific populations") == ["rockfish", "pacific"]
    assert is_population("Caribou (Boreal population)") and not is_population("Painted Turtle")
