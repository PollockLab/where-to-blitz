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

RECORDS.npz holds build_checklist.read_records' arrays. Writes research/results/checklist_lightup.md and
.png, and cluster_results/checklist/lightup/ (per-tier lit cells and meta.json, gitignored: it needs the full
iNat export, so neither the repo nor CI carries it; the viewer's checklist goal reads the shipped PNGs). It
also rebuilds the layer into cluster_results/lightup/checklist/ (not the shipped cluster_results/checklist/).
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
GROUPS = tuple(bc.GROUP_TO_COG)
DEFAULT_ON = ("Amphibia", "Mammalia")
TIERS = (5000, 25000, 100000)
EFFORT_LIT = bc.RULE_OF_THUMB_UNITS
TARGETS = (25, 50)
SHIPPED = os.path.join(ROOT, "cluster_results", "checklist", "checklist.json")
DATA = os.path.join(os.path.dirname(HERE), "cluster_results", "checklist", "lightup")
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


def record_centroids(cell, row5, col5, x0, y1):
    """Mean projected position of each cell's records, from their 5 km cell centres (the finest position the
    record arrays keep). Returns a DataFrame indexed by cell with columns x, y."""
    return pd.DataFrame({"cell": cell, "x": x0 + (np.asarray(col5) + 0.5) * 5000,
                         "y": y1 - (np.asarray(row5) + 0.5) * 5000}).groupby("cell").mean()


def tier_cells(rec, res, crs, group):
    """Per Canadian cell of the group: index, observer-days, species, quantised completeness with its 95%
    interval, both lit flags and the effort lit day."""
    lat, hidden = bc.tier_grid(res, crs)
    k = res // 5000
    cell = (rec["row"] // k).astype(np.int64) * lat.ncol + rec["col"] // k
    sel = ~(rec["acc"] > res)  # as in bc.build
    if group in bc.ICONIC:
        sel &= rec["group"] == bc.GROUP_CODE[group]
    cells, st = bc.completeness_by_cell(cell[sel], rec["observer"][sel], rec["day"][sel], rec["species"][sel])
    m, s_obs, q1, q2 = st["m"], st["s_obs"], st["q1"], st["q2"]
    comp = np.round(s_obs / bc.chao2(s_obs, q1, q2, m) * 250) / 250
    lo, hi = bc.chao2_interval(s_obs, q1, q2, m, st["e1"], st["e2"], st["qe1"])
    canadian = ~hidden.reshape(-1)[cells]
    days = nth_unit_day(cell[sel], rec["observer"][sel], rec["day"][sel], EFFORT_LIT)
    df = pd.DataFrame({"cell": cells, "m": m, "s_obs": s_obs, "comp": comp,
                       "comp_lo": s_obs / hi, "comp_hi": s_obs / lo})[canadian]
    df["chao2"] = (df.m >= bc.MIN_UNITS) & (df.comp >= bc.BANDS["complete"])
    df["effort"] = df.m >= EFFORT_LIT
    df["lit_day"] = df.cell.map(days)
    cen = record_centroids(cell[sel], rec["row"][sel], rec["col"][sel], lat.x0, lat.y1)
    df["x"], df["y"] = df.cell.map(cen.x), df.cell.map(cen.y)
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

    gates, rows, agree, tally = [], [], [], []
    for res in TIERS:
        markers, unlit = {}, {}
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
            left, top = lat.x0 + c * lat.res, lat.y1 - r * lat.res
            assert ((df.x > left) & (df.x < left + lat.res) & (df.y < top) & (df.y > top - lat.res)).all()
            lon, latd = to_lonlat.transform(df.x.to_numpy(), df.y.to_numpy())
            # Cell footprint corners NW, NE, SE, SW for the popup outline.
            corners = [to_lonlat.transform(left + dx, top - dy) for dx, dy in
                       ((0, 0), (lat.res, 0), (lat.res, lat.res), (0, lat.res))]
            lit = (df.chao2 | df.effort).to_numpy()
            flags = (df.chao2.astype(int) + 2 * df.effort.astype(int)).to_numpy()
            # [lon, lat (records' mean position), observer-days, species, completeness, lower, upper,
            #  effort lit date or 0, 1 chao2 + 2 effort, [cell corners NW NE SE SW as lon, lat pairs]]
            ring = np.stack([np.stack(cn, -1) for cn in corners], 1).round(3)
            markers[g] = [
                [round(float(a), 4), round(float(b), 4), int(mm), int(sp), round(float(cp), 3),
                 round(float(cl), 3), round(float(ch), 3), str(np.datetime64(int(d), "D")) if d == d else 0, int(f),
                 rg.ravel().tolist()]
                for a, b, mm, sp, cp, cl, ch, d, f, rg in zip(
                    lon[lit], latd[lit], *(df[k].to_numpy()[lit] for k in ("m", "s_obs", "comp", "comp_lo", "comp_hi",
                                                                            "lit_day")), flags[lit], ring[lit])
            ]
            # Assessed but unlit cells, faint on the page so gaps read as "not yet": [observer-days,
            # completeness or null, NW lon, NW lat, SE lon, SE lat]; the page rebuilds NE and SW.
            comp = df.comp.to_numpy()
            unlit[g] = [[int(mm), None if cp != cp else round(float(cp), 2), *rg[[0, 1, 4, 5]].tolist()]
                        for mm, cp, rg in zip(df.m.to_numpy()[~lit], comp[~lit], ring[~lit].reshape(-1, 8))]
            assert len(unlit[g]) + len(markers[g]) == n
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, f"{res}.json"), "w") as fh:
            json.dump(markers, fh, separators=(",", ":"))
        # One unlit file per group, so the page fetches only the ticked groups.
        for g, cells in unlit.items():
            with open(os.path.join(DATA, f"{res}_unlit_{SYMBOL[g][0].replace(' ', '_')}.json"), "w") as fh:
                json.dump(cells, fh, separators=(",", ":"))
    write_outputs(gates, rows, agree, tally, dd_cuts, cmd_cuts, int(land.sum()))


def _md_table(head, body):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in body])


def write_outputs(gates, rows, agree, tally, dd_cuts, cmd_cuts, n_land):
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
    write_meta()
    render_png()
    print("\n".join(out))


def canada_outline():
    with open(BOUNDARIES) as fh:
        ca = next(f for f in json.load(fh)["features"] if f["properties"]["country"] == "CA")
    geom = shape(ca["geometry"]).simplify(0.05)
    # Drop the many tiny Arctic islets; they add bytes, not outline.
    parts = [p for p in getattr(geom, "geoms", [geom]) if p.area > 0.5]
    return mapping(shapely.MultiPolygon(parts))


# Label and colour per group; colours are what-to-id's MapFilter.BY, "All biodiversity" its Animalia grey.
SYMBOL = {
    "All biodiversity": ("All biodiversity", "#787878"), "Plantae": ("Plants", "#28a03c"),
    "Insecta": ("Insects", "#f08200"), "Aves": ("Birds", "#325adc"), "Fungi": ("Fungi", "#965a32"),
    "Mammalia": ("Mammals", "#963caa"), "Actinopterygii": ("Ray-finned fishes", "#0096c8"),
    "Reptilia": ("Reptiles", "#5a6e00"), "Amphibia": ("Amphibians", "#00be96"),
    "Arachnida": ("Arachnids", "#d22828"), "Mollusca": ("Molluscs", "#e66ea0"),
}


def write_meta():
    meta = {"groups": {g: SYMBOL[g] for g in GROUPS}, "default_on": DEFAULT_ON, "effort_lit": EFFORT_LIT,
            "min_units": bc.MIN_UNITS, "complete": bc.BANDS["complete"], "outline": canada_outline()}
    with open(os.path.join(DATA, "meta.json"), "w") as fh:
        json.dump(meta, fh, separators=(",", ":"))


def render_png(res=100000, rule="effort"):
    """The static figure for a document: DEFAULT_ON groups at one tier and rule, sketch style."""
    with open(os.path.join(DATA, f"{res}.json")) as fh:
        markers = json.load(fh)
    fig, ax = plt.subplots(figsize=(10, 6.2))
    for poly in canada_outline()["coordinates"]:
        xs, ys = zip(*poly[0])
        ax.plot(xs, ys, color="#555", lw=0.6)
    bit = 2 if rule == "effort" else 1
    for k, g in enumerate(DEFAULT_ON):
        pts = np.array([p[:2] for p in markers[g] if p[8] & bit] or np.empty((0, 2)))
        name, colour = SYMBOL[g]
        off = 0.25 * (2 * k - 1)  # side by side where both groups light the same cell
        ax.scatter(pts[:, 0] + off, pts[:, 1], marker="s", s=14, facecolors="none", edgecolors=colour,
                   linewidths=0.9, label=f"{name} ({len(pts)})")
    ax.legend(title="Complete checklists", loc="lower left", frameon=False)
    ax.set_xlim(-142, -50)
    ax.set_ylim(41, 84)
    ax.set_aspect(1 / np.cos(np.radians(58)))
    ax.axis("off")
    ax.set_title(f"Lit cells, {res // 1000} km cells, lit at >= {EFFORT_LIT} observer-days" if rule == "effort"
                 else f"Lit cells, {res // 1000} km cells, lit at Chao2 completeness >= {bc.BANDS['complete']}")
    fig.savefig(os.path.join(HERE, "results", "checklist_lightup.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main(*sys.argv[1:3])
