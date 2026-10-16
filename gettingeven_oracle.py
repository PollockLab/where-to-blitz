"""Independent Getting Even arithmetic for build and shipped-output verification.

No builder functions are used. Missing richness is zero, quartiles use linear
interpolation, population standard deviations are computed from centred sums.
"""

import math

import numpy as np


def gate_bits(richness, scored):
    bits = np.zeros(len(scored), dtype=np.uint8)
    for category, values in richness.items():
        values = np.where(np.isfinite(values), values, 0.0)
        ordered = sorted(values[scored])
        if not ordered:
            raise ValueError("no scored cells for richness quartile")
        position = (len(ordered) - 1) / 4
        lower, upper = math.floor(position), math.ceil(position)
        quartile = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
        bits[scored & (values < quartile)] |= 1 << category
    return bits


def metric(total, counts, floor, excluded):
    """Return winner and z, using only observations and independently recorded gate bits."""
    total, counts = np.asarray(total), np.asarray(counts)
    scored = np.isfinite(total) & (total >= floor)
    z = np.full(counts.shape, np.inf)
    for category, values in enumerate(counts):
        shares = values[scored] / total[scored]
        mean = math.fsum(shares) / len(shares)
        sd = math.sqrt(math.fsum((float(x) - mean) ** 2 for x in shares) / len(shares))
        if not sd:
            raise ValueError("no variation in category shares")
        z[category, scored] = (shares - mean) / sd
        z[category, (excluded & (1 << category)) != 0] = np.inf
    winners = np.argmin(z, axis=0)
    best = z[winners, np.arange(len(total))]
    return np.where(np.isfinite(best), winners, -1), np.where(np.isfinite(best), best, np.nan)
