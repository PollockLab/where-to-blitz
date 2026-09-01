"""Does the Getting Even floor put noise on the map?

`GE_MIN_RECORDS` is 1, so a cell holding one record gets a priority group even though
five of the six groups sit at a share of zero there. This measures whether those thin
cells are the ragged part of the 25 km map, and what pooling would cost if they were.

Five statistics, all at 25 km:

* how the records are distributed over the lattice;
* pooled priorities over 3x3, 5x5 and 7x7 windows, and how often the pooled answer
  still matches the unpooled one on cells that were never short of data;
* the share of cells whose priority differs from the majority of their eight
  neighbours, split by the cell's record count, against two permutation nulls: one that
  keeps the national mix of groups, and one that keeps each record band's own mix,
  because a one-record cell can only answer the group its single record belongs to;
* the same disagreement measured against the neighbours holding >=100 records, so a thin
  cell is judged against a vote worth trusting rather than against other thin cells;
* a holdout: cells holding >=300 records thinned to 1, 5 and 20 records, and how often
  the thinned cell still lands on the group its full record set chose.

Run from the repo root, after a 25 km grid build:

    python probe_gettingeven.py
"""

import json

import numpy as np
import rasterio
import rasterio.warp
from numpy.lib.stride_tricks import sliding_window_view

D = "cluster_results/ca"
idx = json.load(open(D + "/index.json"))
ntr = idx["row_format"].index("n_train")
GE = {
    "Fishes": ["Actinopterygii"],
    "Fungi": ["Fungi"],
    "Reptiles & Amphibians": ["Amphibia", "Reptilia"],
    "Invertebrates": ["Arachnida", "Insecta", "Mollusca"],
    "Mammals": ["Mammalia"],
    "Plants": ["Plantae"],
}
CATS = list(GE)


def col(g):
    r = json.load(open(D + "/" + idx["files"][g]))[g]
    return np.array([x[ntr] for x in r], float)


tot = col("All biodiversity")
cnt = np.array([sum(col(g) for g in GE[c]) for c in CATS])
print("cells", len(tot))
bins = [0, 1, 3, 10, 30, 100, 300, 1000, 10**9]
for a, b in zip(bins, bins[1:]):
    m = (tot >= a) & (tot < b)
    print(
        f"  {a:>5}-{b if b < 10**8 else 'inf':>6}: {m.sum():6d} cells {100 * m.mean():5.1f}%  {tot[m].sum():12,.0f} records"
    )
print("share of all records in cells with <30: %.3f%%" % (100 * tot[tot < 30].sum() / tot.sum()))
ds = rasterio.open(D + "/grid_25000m/All_biodiversity.tif")
cells = json.load(open(D + "/" + idx["files"]["All biodiversity"]))["All biodiversity"]
x, y = rasterio.warp.transform("EPSG:4326", ds.crs, [c[1] for c in cells], [c[0] for c in cells])
r, c = rasterio.transform.rowcol(ds.transform, x, y)
r = np.asarray(r)
c = np.asarray(c)
H, W = ds.shape
T = np.zeros((H, W))
T[r, c] = tot
C = np.zeros((len(CATS), H, W))
for i in range(len(CATS)):
    C[i][r, c] = cnt[i]
onmask = np.zeros((H, W), bool)
onmask[r, c] = True


def pool(a, k):
    if k == 1:
        return a.copy()
    h = k // 2
    return sliding_window_view(np.pad(a, h), (k, k)).sum((-1, -2))


base = None
for k in (1, 3, 5, 7):
    Tp = pool(T, k)
    Cp = np.array([pool(C[i], k) for i in range(len(CATS))])
    scored = onmask & (Tp >= 1)
    prop = np.where(scored, Cp / np.where(Tp > 0, Tp, 1), np.nan)
    z = np.array([(prop[i] - np.nanmean(prop[i])) / np.nanstd(prop[i]) for i in range(len(CATS))])
    pri = np.where(scored, np.argmin(np.where(np.isfinite(z), z, np.inf), 0), -1)
    u, cn = np.unique(pri[scored], return_counts=True)
    print(f"k={k} ({k * 25} km window): scored {scored.sum():6d} ", {CATS[a]: int(b) for a, b in zip(u, cn)})
    if k == 1:
        base = pri.copy()
        scored1 = scored.copy()
        rich = onmask & (T >= 100)
    else:
        m = rich & scored
        print(
            f"       agrees with unpooled on the {m.sum()} cells with >=100 records: {100 * (pri[m] == base[m]).mean():.1f}%"
        )
# --- speckle: does a cell's priority group disagree with its eight neighbours, and
# --- does that disagreement depend on how few records the cell holds?
NEIGH = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)]
MIN_VOTERS = 3  # below this the "majority" is itself a tie-break


def vote(p, voters):
    """Eight-neighbour majority label and voter count, counting only cells in `voters`."""
    v = np.zeros((len(CATS), H, W))
    n = np.zeros((H, W))
    for dr, dc in NEIGH:
        sp = np.roll(np.roll(p, dr, 0), dc, 1)
        so = np.roll(np.roll(voters, dr, 0), dc, 1).copy()
        # np.roll wraps; blank the wrapped edge so a cell never votes across the raster
        if dr:
            (so[:1] if dr > 0 else so[-1:])[:] = False
        if dc:
            (so[:, :1] if dc > 0 else so[:, -1:])[:] = False
        n += so
        for i in range(len(CATS)):
            v[i] += so & (sp == i)
    return np.argmax(v, 0), n


majority, nvote = vote(base, scored1)
comparable = scored1 & (nvote >= MIN_VOTERS)
disagree = comparable & (base != majority)
print(
    f"\nspeckle: {comparable.sum()} scored cells have >={MIN_VOTERS} scored neighbours; "
    f"{disagree[comparable].sum() if comparable.any() else 0} differ from the neighbour majority "
    f"({100 * disagree[comparable].mean():.1f}%)"
)
print("  records in cell   cells   differ from neighbour majority")
rb = [1, 2, 3, 6, 11, 31, 101, 301, 10**9]
for a, b in zip(rb, rb[1:]):
    m = comparable & (T >= a) & (T < b)
    if not m.any():
        continue
    lab = f"{a}" if b == a + 1 else (f"{a}+" if b > 10**8 else f"{a}-{b - 1}")
    print(f"  {lab:>15}  {m.sum():6d}   {100 * disagree[m].mean():5.1f}%")

# A 6-category map disagrees with its own neighbours a lot by chance. Permute the
# priority labels over the scored cells, keeping the national mix, to get the floor.


def permuted_null(comp, voters, groups=None, draws=50, seed=0):
    """Per-cell and per-draw disagreement rates when the labels are shuffled.

    `groups` is the list of masks the shuffle stays inside. One mask holding every scored
    cell gives the national-mix null. A mask per record band keeps each band's own label
    mix and only destroys the arrangement of the labels within the band.
    """
    gen = np.random.default_rng(seed)
    groups = [scored1] if groups is None else groups
    per_draw = []
    rate = np.zeros((H, W))
    for _ in range(draws):
        perm = base.copy()
        for g in groups:
            lab = base[g].copy()
            gen.shuffle(lab)
            perm[g] = lab
        d = comp & (perm != vote(perm, voters)[0])
        per_draw.append(d[comp].mean())
        rate += d
    return rate / draws, np.array(per_draw)


nullcell, null = permuted_null(comparable, scored1)
print(
    f"  permuted null (national mix, no spatial structure): {100 * null.mean():.1f}% "
    f"+/- {100 * null.std():.1f}%  ->  observed {100 * disagree[comparable].mean():.1f}%"
)
# Per bin, because the low bins answer Plants or Invertebrates almost always, and two
# neighbours holding the same common answer agree for no ecological reason.
print("  records in cell   cells   observed   null   structure (null - observed)")
for a, b in zip(rb, rb[1:]):
    m = comparable & (T >= a) & (T < b)
    if not m.any():
        continue
    lab = f"{a}" if b == a + 1 else (f"{a}+" if b > 10**8 else f"{a}-{b - 1}")
    o = 100 * disagree[m].mean()
    n = 100 * nullcell[m].mean()
    print(f"  {lab:>15}  {m.sum():6d}    {o:5.1f}%  {n:5.1f}%   {n - o:+5.1f} pts")

# --- Two harder versions of the same test, because the pair above flatters the thin cells.
#
# The national-mix null hands a one-record cell any of the six labels, but a one-record
# cell can only answer the group its single record belongs to, and nearly all single
# records are Plants or Invertebrates. So part of the margin above is arithmetic, not
# ecology. Shuffle inside each record band instead: the null then carries the band's own
# label mix and only the arrangement is destroyed.
bandmask = [comparable & (T >= a) & (T < b) for a, b in zip(rb, rb[1:])]
shufmask = [scored1 & (T >= a) & (T < b) for a, b in zip(rb, rb[1:])]
stratcell, strat = permuted_null(comparable, scored1, groups=shufmask)
print(
    f"\n  band-restricted null (each band keeps its own label mix): {100 * strat.mean():.1f}% "
    f"+/- {100 * strat.std():.1f}%  ->  observed {100 * disagree[comparable].mean():.1f}%"
)
print("  records in cell   cells   observed   null   structure (null - observed)")
for m, (a, b) in zip(bandmask, zip(rb, rb[1:])):
    if not m.any():
        continue
    lab = f"{a}" if b == a + 1 else (f"{a}+" if b > 10**8 else f"{a}-{b - 1}")
    o = 100 * disagree[m].mean()
    n = 100 * stratcell[m].mean()
    print(f"  {lab:>15}  {m.sum():6d}    {o:5.1f}%  {n:5.1f}%   {n - o:+5.1f} pts")

# The majority a thin cell is judged against is itself built from thin cells, so two
# neighbours can agree because both are guessing the same common answer. Judge each cell
# against the majority of its neighbours holding >=100 records instead, which is the only
# vote worth trusting. Fewer cells qualify, and the null has to be recomputed on them.
WELL = 100
well = scored1 & (T >= WELL)
majw, nvotew = vote(base, well)
compw = scored1 & (nvotew >= MIN_VOTERS)
disw = compw & (base != majw)
nullw, drawsw = permuted_null(compw, well, groups=shufmask)
print(
    f"\n  against well-sampled neighbours only (>={WELL} records): {compw.sum()} cells qualify, "
    f"{100 * disw[compw].mean():.1f}% differ, band-restricted null {100 * drawsw.mean():.1f}% "
    f"+/- {100 * drawsw.std():.1f}%"
)
print("  records in cell   cells   observed   null   structure (null - observed)")
for a, b in zip(rb, rb[1:]):
    m = compw & (T >= a) & (T < b)
    if not m.any():
        continue
    lab = f"{a}" if b == a + 1 else (f"{a}+" if b > 10**8 else f"{a}-{b - 1}")
    o = 100 * disw[m].mean()
    n = 100 * nullw[m].mean()
    print(f"  {lab:>15}  {m.sum():6d}    {o:5.1f}%  {n:5.1f}%   {n - o:+5.1f} pts")


# --- Does a thin cell recover the answer its own records would have given with more of
# them? Neighbour agreement cannot say: it asks whether a cell matches its surroundings,
# not whether it is right. Take the cells that hold enough records to have a trustworthy
# answer, throw records away until they are as thin as the cells in question, and see how
# often the thinned cell still lands on the full-data group. The z-standardisation is
# recomputed inside this set both times, so the comparison holds the reference fixed.
HOLD = 300
sel = scored1 & (T >= HOLD)
cs = np.array([C[i][sel] for i in range(len(CATS))])
ts = T[sel]


def argmin_z(counts, totals):
    """The Getting Even answer for a set of cells: share per group, z per group, lowest z."""
    prop = counts / totals
    z = (prop - prop.mean(1, keepdims=True)) / prop.std(1, keepdims=True)
    return np.argmin(z, 0)


truth = argmin_z(cs, ts)
common = np.bincount(truth, minlength=len(CATS)).argmax()
print(
    f"\nsubsample holdout: {sel.sum():,} cells hold >={HOLD} records, full-data answer "
    f"{CATS[common]} on {100 * (truth == common).mean():.1f}% of them (the guess to beat)"
)
gen = np.random.default_rng(0)
for n in (1, 5, 20):
    hit = []
    for _ in range(20):
        # Multivariate hypergeometric: draw n of the cell's own records without replacement.
        sub = np.array(
            [gen.multivariate_hypergeometric(cs[:, j].astype(int), n) for j in range(cs.shape[1])]
        ).T
        hit.append((argmin_z(sub.astype(float), float(n)) == truth).mean())
    hit = np.array(hit)
    print(
        f"  thinned to {n:>2} records: recovers the full-data group {100 * hit.mean():4.1f}% "
        f"+/- {100 * hit.std():.1f}% of the time"
    )


empty = onmask & (T < 1)
print("empty cells:", int(empty.sum()))
for k in (3, 5, 7):
    print(
        f"   with a >=30-record cell inside {k}x{k}:",
        int((empty & (pool((T >= 30).astype(float), k) > 0)).sum()),
    )

# The app shows 25 km zoomed out and 5 km zoomed in, so the floor matters most at the
# tier a user looks at closest. n_train is band 7 of the per-group grid rasters.
print("\nhow thin the scored cells are, per tier")
for res in (25000, 5000):
    try:
        with rasterio.open(f"{D}/grid_{res}m/All_biodiversity.tif") as s:
            n = s.read(7).astype(float)
        with rasterio.open(f"{D}/grid_{res}m/gettingeven.tif") as s:
            p = s.read(1)
    except rasterio.RasterioIOError as e:
        print(f"  {res // 1000:>2} km: not built ({e.__class__.__name__})")
        continue
    n = n[p >= 0]
    print(
        f"  {res // 1000:>2} km: scored {len(n):>7,}   one record {100 * (n == 1).mean():4.1f}%"
        f"   under ten {100 * (n < 10).mean():4.1f}%   median {np.median(n):.0f} records"
    )


def tier(res):
    """Priority, record count and scored mask for a built tier, straight off the rasters.

    Unscored cells are nodata in gettingeven.tif, so a plain astype(int) turns them into
    garbage category indices that then vote. Mask on isfinite before casting.
    """
    with rasterio.open(f"{D}/grid_{res}m/gettingeven.tif") as s:
        p = s.read(1).astype(float)
    with rasterio.open(f"{D}/grid_{res}m/All_biodiversity.tif") as s:
        n = s.read(7).astype(float)
    ok = np.isfinite(p) & (p >= 0) & np.isfinite(n)
    return np.where(ok, p, -1).astype(int), np.nan_to_num(n), ok


def neighbour_disagreement(pri, ok, draws=20, seed=0):
    """Share differing from the eight-neighbour majority, and the label-permutation null."""

    def majority(p):
        v = np.zeros((len(CATS), *p.shape))
        n = np.zeros(p.shape)
        for dr, dc in NEIGH:
            sp = np.roll(np.roll(p, dr, 0), dc, 1)
            so = np.roll(np.roll(ok, dr, 0), dc, 1).copy()
            if dr:
                (so[:1] if dr > 0 else so[-1:])[:] = False
            if dc:
                (so[:, :1] if dc > 0 else so[:, -1:])[:] = False
            n += so
            for i in range(len(CATS)):
                v[i] += so & (sp == i)
        return np.argmax(v, 0), n

    m, n = majority(pri)
    comp = ok & (n >= MIN_VOTERS)
    dis = comp & (pri != m)
    gen = np.random.default_rng(seed)
    nul = []
    for _ in range(draws):
        q = pri.copy()
        shuffled = pri[ok].copy()
        gen.shuffle(shuffled)
        q[ok] = shuffled
        mq, _ = majority(q)
        nul.append((comp & (q != mq))[comp].mean())
    return comp, dis, np.array(nul)


# The 25 km run above is built from the per-group JSONs. Rebuilding it from the rasters
# reproduces it and reaches the 5 km tier, where the thin cells are a quarter of the map.
print("\nneighbour disagreement per tier, from the rasters")
for res in (25000, 5000):
    try:
        pri, n, ok = tier(res)
    except rasterio.RasterioIOError:
        print(f"  {res // 1000:>2} km: not built")
        continue
    comp, dis, nul = neighbour_disagreement(pri, ok)
    print(
        f"  {res // 1000:>2} km: {comp.sum():,} comparable cells, {100 * dis[comp].mean():.1f}% "
        f"differ, null {100 * nul.mean():.1f}% +/- {100 * nul.std():.1f}%"
    )
    for a, b in zip(rb, rb[1:]):
        m = comp & (n >= a) & (n < b)
        if not m.any():
            continue
        lab = f"{a}" if b == a + 1 else (f"{a}+" if b > 10**8 else f"{a}-{b - 1}")
        print(f"    {lab:>13}  {m.sum():7,}  {100 * dis[m].mean():5.1f}%")
