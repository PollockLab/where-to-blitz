"""Density-weighted Gaussian KDE in climate space, at 5 km-tier scale (#87).

`env` (the "cover every habitat" axis) is the climate surprisal -log f(z), where

    f(z) = sum_r w_r exp(-|z - z_r|^2 / 2H^2)

over recorded cells r with weight w_r = their iNaturalist density. Evaluating that pairwise is
O(len(query) x len(reference)). At the 0.25-deg grid (38k x 38k) that was affordable. On the
5 km lattice it is ~1e11 exp() calls per taxon, per rebuild.

`kde_binned` bins the reference cloud onto a regular grid in standardized climate space,
convolves it with the same Gaussian (separable, so three 1-D passes), and reads the query
points back out with multilinear interpolation. Cost is O(len(query) + len(reference) + grid).

Binning at width b adds b^2/12 to the kernel's variance, so the default b = 0.08*H inflates the
effective bandwidth by 0.05%. Two approximations are cruder than that and are what the gate in
`test_climate_kde.py` actually bounds:

  - the Gaussian is truncated at 6 sigma, so the far tail of f is under-estimated. Those are
    cells with essentially no recorded climate analogue, which rank at the top of the surprisal
    axis either way;
  - cells beyond +/-6 standardized units on any climate axis (0.4% of the Canadian 25 km grid)
    are clipped to the boundary bin, compressing their ranks into near-ties -- again at the
    extreme-surprisal end.

Measured on the real 25 km climate field against the exact pairwise sum: Spearman > 0.99999 on
the ranked `env` axis, worst cell moving under 1 percentile point.
"""
import numpy as np


def _conv1d(a, k, axis):
    """Convolve `a` with 1-D kernel `k` along `axis`, zero-padded, shape preserved."""
    a = np.moveaxis(a, axis, -1)
    n = a.shape[-1]
    flat = a.reshape(-1, n)
    r = len(k) // 2
    pad = np.pad(flat, ((0, 0), (r, r)))
    out = np.zeros_like(flat)
    for i, kv in enumerate(k):
        out += kv * pad[:, i:i + n]
    return np.moveaxis(out.reshape(a.shape), -1, axis)


def kde_binned(Zref, wref, Zq, h=1.0, bin_w=0.08, span=6.0, trunc=6.0):
    """Binned-and-convolved approximation of `kde_exact`. See module docstring."""
    d = Zref.shape[1]
    nb = round(2 * span / bin_w)
    idx = np.clip(((Zref + span) / bin_w).astype(int), 0, nb - 1)
    flat = np.ravel_multi_index(tuple(idx.T), (nb,) * d)
    hist = np.bincount(flat, weights=wref, minlength=nb ** d).reshape((nb,) * d)

    sig = h / bin_w
    rad = int(np.ceil(trunc * sig))
    off = np.arange(-rad, rad + 1)
    kern = np.exp(-0.5 * (off / sig) ** 2)
    for ax in range(d):
        hist = _conv1d(hist, kern, ax)

    # bin centres sit at (i + 0.5) * bin_w - span, so the query's grid coordinate is p below
    p = np.clip((np.asarray(Zq) + span) / bin_w - 0.5, 0, nb - 1 - 1e-6)
    lo = p.astype(int)
    f = p - lo
    out = np.zeros(len(Zq))
    for corner in range(1 << d):
        bits = [(corner >> j) & 1 for j in range(d)]
        wgt = np.prod([f[:, j] if bits[j] else 1 - f[:, j] for j in range(d)], axis=0)
        out += wgt * hist[tuple(lo[:, j] + bits[j] for j in range(d))]
    return out


def kde_exact(Zref, wref, Zq, h=1.0, block=2048):
    """Exact pairwise sum_r w_r exp(-|z - z_r|^2 / 2h^2). Reference for `kde_binned`."""
    out = np.empty(len(Zq))
    r2 = np.sum(Zref ** 2, axis=1)
    for b in range(0, len(Zq), block):
        q = Zq[b:b + block]
        d2 = np.sum(q ** 2, axis=1)[:, None] - 2 * q @ Zref.T + r2[None, :]
        np.maximum(d2, 0, out=d2)
        out[b:b + len(q)] = np.exp(-d2 / (2 * h * h)) @ wref
    return out


def surprisal(Z, weights, kde=kde_binned, h=1.0):
    """Climate surprisal -log f(z) in nats; NaN where the cell has no climate or nothing is recorded.

    f is divided by the total reference weight, so it is a property of the climate field and of
    how recorded density is distributed over it, not of how many cells the lattice happens to
    have. Unnormalized, the 5 km tier's surprisal would sit a constant -log(25) away from the
    25 km tier's and the fixed ramps in national_breaks.py would not transfer between them.
    """
    ok = ~np.any(np.isnan(Z), axis=1)
    ref = (weights > 0) & ok
    s = np.full(len(Z), np.nan)
    if ref.sum() == 0:
        return s
    idx = np.where(ok)[0]
    s[idx] = -np.log(kde(Z[ref], weights[ref], Z[idx], h=h) / weights[ref].sum() + 1e-12)
    return s


def surprisal_rank(Z, weights, kde=kde_binned, h=1.0):
    """`surprisal`, percentile-ranked to 0..1 over the cells with finite climate.

    Cells whose climate is rare among recorded cells score high. Cells with no climate data,
    and every cell when nothing is recorded, score 0. This is a population rank, so it is only
    comparable within one grid -- the app's tiers go through national_breaks.py instead.
    """
    s = surprisal(Z, weights, kde=kde, h=h)
    out = np.zeros(len(Z))
    v = np.isfinite(s)
    if v.sum() > 1:
        out[np.where(v)[0]] = np.argsort(np.argsort(s[v])) / (v.sum() - 1)
    return out
