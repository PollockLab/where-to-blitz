"""Checklist "light-up" view: which cells are lit under two rules, and how lit cells spread over climate.

Rules, per cell, group and tier (5, 25 and 100 km), on Canadian cells only:

    chao2   build_checklist's "complete" status: >= MIN_UNITS observer-days and Chao2
            completeness >= BANDS["complete"] (0.9)
    effort  >= EFFORT_LIT observer-days, the rule of thumb build_checklist already records

An effort-lit cell is dated by its EFFORT_LIT-th observer-day. Chao2 completeness is not monotone in
time, so a Chao2 lit date would need a rebuild per date; it is not derived.

Climate strata: terciles of degree-days below 0 C by terciles of climate moisture deficit
(research/fetch_chelsa_ddcmd.py), cut points taken over Canadian 5 km cells with CHELSA data and
applied unchanged at every tier. A cell takes the mean of the CHELSA samples whose centres fall in it.

    python research/checklist_lightup.py RECORDS.npz CHELSA.npz

RECORDS.npz holds build_checklist.read_records' arrays. Writes research/results/checklist_lightup.md
and .json, research/checklist_lightup.html and research/results/checklist_lightup.png, and rebuilds
the layer into cluster_results/lightup/checklist/ (not the shipped cluster_results/checklist/).
"""
import json
import os
import sys

import _rootpath  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape

import build_checklist as bc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GROUPS = ("Amphibia", "Mammalia")
TIERS = (5000, 25000, 100000)
EFFORT_LIT = bc.RULE_OF_THUMB_UNITS
TARGETS = (25, 50)
SHIPPED = os.path.join(ROOT, "cluster_results", "checklist", "checklist.json")
BOUNDARIES = os.path.join(ROOT, "cluster_results", "ca", "na_boundaries.geojson")
CMD_LABELS = ("moist", "medium", "dry")


def nth_unit_day(cell, observer, day, n):
    """Day of each cell's n-th distinct observer-day, for cells with at least n. Returns a Series."""
    u = pd.DataFrame({"cell": cell, "observer": observer, "day": day}).drop_duplicates()
    u = u.sort_values(["cell", "day"])
    rank = u.groupby("cell").cumcount() + 1
    return u[rank == n].set_index("cell")["day"]


def stratum(dd, cmd, dd_cuts, cmd_cuts):
    """(dd tercile, cmd tercile) per cell, -1 where climate is missing."""
    ok = np.isfinite(dd) & np.isfinite(cmd)
    i = np.where(ok, np.searchsorted(dd_cuts, np.nan_to_num(dd), side="right"), -1)
    j = np.where(ok, np.searchsorted(cmd_cuts, np.nan_to_num(cmd), side="right"), -1)
    return i, j


def cell_climate(clim, lat, to_lattice):
    """Mean DD<0 and CMD per lattice cell from the CHELSA samples inside it, NaN where none."""
    ok = np.isfinite(clim["dd0"]) & np.isfinite(clim["cmd"])
    x, y = to_lattice.transform(clim["lon"][ok], clim["lat"][ok])
    col = np.floor((x - lat.x0) / lat.res).astype(np.int64)
    row = np.floor((lat.y1 - y) / lat.res).astype(np.int64)
    inside = (row >= 0) & (row < lat.nrow) & (col >= 0) & (col < lat.ncol)
    idx = row[inside] * lat.ncol + col[inside]
    n = np.bincount(idx, minlength=lat.nrow * lat.ncol)
    out = []
    for v in ("dd0", "cmd"):
        s = np.bincount(idx, clim[v][ok][inside], minlength=lat.nrow * lat.ncol)
        with np.errstate(invalid="ignore"):
            out.append(np.where(n > 0, s / np.maximum(n, 1), np.nan))
    return out


def tier_cells(rec, res, crs, group):
    """Per Canadian cell of the group: index, observer-days, quantised completeness, both lit flags, lit day."""
    lat, hidden = bc.tier_grid(res, crs)
    k = res // 5000
    cell = (rec["row"] // k).astype(np.int64) * lat.ncol + rec["col"] // k
    sel = ~(rec["acc"] > res) & (rec["group"] == bc.GROUP_CODE[group])  # as in bc.build
    cells, st = bc.completeness_by_cell(cell[sel], rec["observer"][sel], rec["day"][sel], rec["species"][sel])
    m, s_obs, q1, q2 = st["m"], st["s_obs"], st["q1"], st["q2"]
    comp = np.round(s_obs / bc.chao2(s_obs, q1, q2, m) * 250) / 250
    canadian = ~hidden.reshape(-1)[cells]
    days = nth_unit_day(cell[sel], rec["observer"][sel], rec["day"][sel], EFFORT_LIT)
    df = pd.DataFrame({"cell": cells, "m": m, "comp": comp})[canadian]
    df["chao2"] = (df.m >= bc.MIN_UNITS) & (df.comp >= bc.BANDS["complete"])
    df["effort"] = df.m >= EFFORT_LIT
    df["lit_day"] = df.cell.map(days)
    return lat, hidden, df


def main(records_path, climate_path):
    with open(bc.GRID_SIDECAR) as fh:
        crs = CRS.from_wkt(json.load(fh)["tiers"]["5000"]["lattice"]["crs"])
    z = np.load(records_path)
    rec = {key: z[key] for key in z}
    clim = dict(np.load(climate_path))
    shipped = json.load(open(SHIPPED))
    doc = bc.build(rec, crs, shipped["source"], out_dir=os.path.join(ROOT, "cluster_results", "lightup", "checklist"))
    to_lattice = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_lonlat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    lat5, hidden5 = bc.tier_grid(5000, crs)
    dd5, cmd5 = cell_climate(clim, lat5, to_lattice)
    land = ~hidden5.reshape(-1) & np.isfinite(dd5) & np.isfinite(cmd5)
    dd_cuts = np.quantile(dd5[land], [1 / 3, 2 / 3])
    cmd_cuts = np.quantile(cmd5[land], [1 / 3, 2 / 3])

    gates, rows, agree, tally, markers = [], [], [], [], {}
    for res in TIERS:
        for g in GROUPS:
            lat, hidden, df = tier_cells(rec, res, crs, g)
            built = doc["tiers"][str(res)]["groups"][g]["cells"]["complete"]
            assert int(df.chao2.sum()) == built, (res, g, int(df.chao2.sum()), built)
            if res in (5000, 25000):
                gates.append((res, g, built, shipped["tiers"][str(res)]["groups"][g]["cells"]["complete"]))
            n = len(df)
            rows.append((g, res, n, int(df.chao2.sum()), int(df.effort.sum())))
            ct = pd.crosstab(df.chao2, df.effort).reindex(index=[True, False], columns=[True, False], fill_value=0)
            agree.append((g, res, *ct.to_numpy().ravel().tolist()))
            dd, cmd = cell_climate(clim, lat, to_lattice)
            i, j = stratum(dd[df.cell], cmd[df.cell], dd_cuts, cmd_cuts)
            for rule in ("chao2", "effort"):
                lit = df[rule].to_numpy()
                grid = np.zeros((3, 3), int)
                np.add.at(grid, (i[lit & (i >= 0)], j[lit & (i >= 0)]), 1)
                missing = int((lit & (i < 0)).sum())
                assert grid.sum() + missing == lit.sum()
                tally.append((g, res, rule, grid, missing))
            r, c = np.divmod(df.cell.to_numpy(), lat.ncol)
            x, y = lat.x0 + (c + 0.5) * lat.res, lat.y1 - (r + 0.5) * lat.res
            lon, latd = to_lonlat.transform(x, y)
            day = df.lit_day.to_numpy()
            for rule in ("chao2", "effort"):
                lit = df[rule].to_numpy()
                markers[f"{res}|{g}|{rule}"] = [
                    [round(float(a), 4), round(float(b), 4),
                     str(np.datetime64(int(d), "D")) if rule == "effort" and d == d else None]
                    for a, b, d in zip(lon[lit], latd[lit], day[lit])
                ]
    write_outputs(gates, rows, agree, tally, markers, dd_cuts, cmd_cuts, int(land.sum()))


def _md_table(head, body):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in body])


def write_outputs(gates, rows, agree, tally, markers, dd_cuts, cmd_cuts, n_land):
    km = {5000: "5 km", 25000: "25 km", 100000: "100 km"}
    out = ["# Checklist light-up: lit cells per rule, agreement, climate-band tally", "",
           f"Generated by `research/checklist_lightup.py`. Groups {', '.join(GROUPS)}; Canadian cells only. "
           f"Rules: chao2 = >= {bc.MIN_UNITS} observer-days and Chao2 completeness >= {bc.BANDS['complete']}; "
           f"effort = >= {EFFORT_LIT} observer-days. Assessed = Canadian cells with at least one record of the group.",
           "", "## Gate: 5 and 25 km complete counts against the shipped checklist.json", "",
           _md_table(["tier", "group", "this run", "shipped", "match"],
                     [(km[r], g, a, b, "yes" if a == b else "NO") for r, g, a, b in gates]),
           "", "## Lit cells and share of assessed cells", "",
           _md_table(["group", "tier", "assessed", "lit chao2", "share chao2", "lit effort", "share effort"],
                     [(g, km[r], n, a, f"{a / n:.1%}", b, f"{b / n:.1%}") for g, r, n, a, b in rows]),
           "", "## Agreement (2x2)", "",
           _md_table(["group", "tier", "both", "chao2 only", "effort only", "neither"],
                     [(g, km[r], tt, tf, ft, ff) for g, r, tt, tf, ft, ff in agree]),
           "", "## Climate-band tally", "",
           f"Strata: terciles over {n_land:,} Canadian 5 km cells with CHELSA data. Degree-days below 0 C cut "
           f"points {dd_cuts[0]:.0f} and {dd_cuts[1]:.0f} C-days (mild < cool < cold); climate moisture "
           f"deficit cut points {cmd_cuts[0]:.0f} and {cmd_cuts[1]:.0f} mm (moist < medium < dry). Nine strata, "
           f"so 25 checklists is about {25 / 9:.1f} per stratum and 50 about {50 / 9:.1f}. 'no climate' = lit "
           "cells with no CHELSA sample inside (coast, open water); strata plus 'no climate' sum to lit.", ""]
    dd_names = ("mild", "cool", "cold")  # tercile 0 has the fewest degree-days below 0
    strata = [f"{d}/{c}" for d in dd_names for c in CMD_LABELS]
    body = []
    for g, r, rule, grid, missing in tally:
        flat = grid.ravel()
        body.append((g, km[r], rule, *flat, missing, int(flat.sum()) + missing,
                     int((flat >= 3).sum()), int((flat >= 6).sum()),
                     int(np.minimum(flat, 3).sum()), int(np.minimum(flat, 6).sum())))
    out.append(_md_table(["group", "tier", "rule", *strata, "no climate", "lit",
                          "strata >= 3", "strata >= 6", "toward 25 (cap 3/stratum)", "toward 50 (cap 6/stratum)"],
                         body))
    out += ["", "'toward 25' counts lit cells usable for a 25-checklist design with at most 3 per stratum "
            "(27 slots); 'toward 50' with at most 6 per stratum (54 slots)."]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "checklist_lightup.md"), "w") as fh:
        fh.write("\n".join(out) + "\n")
    with open(os.path.join(HERE, "results", "checklist_lightup.json"), "w") as fh:
        json.dump({"markers": markers}, fh, separators=(",", ":"))
    outline = canada_outline()
    render_html(markers, outline)
    render_png(markers, outline)
    print("\n".join(out))


def canada_outline():
    with open(BOUNDARIES) as fh:
        ca = next(f for f in json.load(fh)["features"] if f["properties"]["country"] == "CA")
    geom = shape(ca["geometry"]).simplify(0.05)
    # Drop the many tiny Arctic islets; they add bytes, not outline.
    parts = [p for p in getattr(geom, "geoms", [geom]) if p.area > 0.5]
    return mapping(shapely.MultiPolygon(parts))


SYMBOL = {"Amphibia": ("Amphibians", "#1b6e3a"), "Mammalia": ("Mammals", "#7a3b12")}


def render_png(markers, outline, res=100000, rule="effort"):
    fig, ax = plt.subplots(figsize=(10, 6.2))
    for poly in outline["coordinates"]:
        xs, ys = zip(*poly[0])
        ax.plot(xs, ys, color="#555", lw=0.6)
    handles = []
    for g in GROUPS:
        pts = np.array([p[:2] for p in markers[f"{res}|{g}|{rule}"]] or np.empty((0, 2)))
        name, colour = SYMBOL[g]
        off = 0.25 if g == "Mammalia" else -0.25  # side by side where both groups light the same cell
        h = ax.scatter(pts[:, 0] + off, pts[:, 1], marker="s", s=14, facecolors="none", edgecolors=colour,
                       linewidths=0.8, label=f"{name} ({len(pts)})")
        handles.append(h)
    ax.legend(handles=handles, title="Complete checklists", loc="lower left", frameon=False)
    ax.set_xlim(-142, -50)
    ax.set_ylim(41, 84)
    ax.set_aspect(1 / np.cos(np.radians(58)))
    ax.axis("off")
    ax.set_title(f"Lit cells, {res // 1000} km tier, rule: >= {EFFORT_LIT} observer-days" if rule == "effort"
                 else f"Lit cells, {res // 1000} km tier, rule: Chao2 >= 0.9")
    fig.savefig(os.path.join(HERE, "results", "checklist_lightup.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


def render_html(markers, outline):
    with open(os.path.join(HERE, "checklist_lightup_template.html")) as fh:
        page = fh.read()
    data = json.dumps({"markers": markers, "outline": outline, "groups": {g: SYMBOL[g] for g in GROUPS},
                       "effort_lit": EFFORT_LIT}, separators=(",", ":"))
    with open(os.path.join(HERE, "checklist_lightup.html"), "w") as fh:
        fh.write(page.replace("__DATA__", data))


if __name__ == "__main__":
    main(*sys.argv[1:3])
