# -*- coding: utf-8 -*-
"""Slope and classification, pure numpy (no QGIS dependency)."""
import math
import re

import numpy as np


def slope(arr, nodata_mask, dx, dy, unit="percent"):
    """Slope with Horn's 3x3 method (same as ArcGIS Slope / gdaldem slope).

    dx: cell width in metres, scalar or array of shape (rows,) (varies with latitude)
    dy: cell height in metres
    unit: 'percent' or 'degrees'
    Returns float32 array, NaN where the DEM is NoData.
    """
    rows, cols = arr.shape
    c = np.where(nodata_mask, np.nan, arr)
    p = np.pad(c, 1, mode="edge")

    def nb(a, b):
        v = p[1 + a:1 + a + rows, 1 + b:1 + b + cols]
        return np.where(np.isnan(v), c, v)  # NoData neighbours take the centre value

    dxa = np.asarray(dx, dtype="float64")
    if dxa.ndim == 1:
        dxa = dxa[:, None]
    zc, zf = nb(-1, 1), nb(0, 1)
    zi = nb(1, 1)
    za, zd, zg = nb(-1, -1), nb(0, -1), nb(1, -1)
    dzdx = ((zc + 2 * zf + zi) - (za + 2 * zd + zg)) / (8.0 * dxa)
    del zc, zf, zi, za, zd, zg
    zb, zh = nb(-1, 0), nb(1, 0)
    za, zc, zg, zi = nb(-1, -1), nb(-1, 1), nb(1, -1), nb(1, 1)
    dzdy = ((zg + 2 * zh + zi) - (za + 2 * zb + zc)) / (8.0 * dy)
    rise = np.hypot(dzdx, dzdy)
    out = rise * 100.0 if unit == "percent" else np.degrees(np.arctan(rise))
    out[nodata_mask] = np.nan
    return out.astype("float32")


def parse_breaks(text):
    """'8, 15; 25 45' -> [8.0, 15.0, 25.0, 45.0]  (must be strictly ascending)."""
    parts = [t for t in re.split(r"[,;\s]+", (text or "").strip()) if t]
    try:
        vals = [float(t.replace(",", ".")) for t in parts]
    except ValueError:
        raise ValueError("Class breaks must be numbers separated by commas, e.g. 8, 15, 25, 45")
    if not vals:
        raise ValueError("No class breaks given.")
    if any(b <= a for a, b in zip(vals[:-1], vals[1:])):
        raise ValueError("Class breaks must be in strictly ascending order.")
    if vals[0] < 0:
        raise ValueError("Class breaks cannot be negative.")
    return vals


def default_breaks(unit):
    """Standard Indonesian slope classes (SK Mentan 837/1980): 0-8, 8-15, 15-25, 25-45, >45 %."""
    pct = [8.0, 15.0, 25.0, 45.0]
    if unit == "percent":
        return pct
    return [round(math.degrees(math.atan(p / 100.0)), 1) for p in pct]


def auto_breaks(values, n_classes, method="equal"):
    """Automatic breaks. method: 'equal' (equal interval) or 'quantile'."""
    v = values[np.isfinite(values)]
    if v.size == 0 or n_classes < 2:
        return []
    if method == "quantile":
        b = np.quantile(v, np.linspace(0, 1, n_classes + 1)[1:-1])
    else:
        b = np.linspace(float(v.min()), float(v.max()), n_classes + 1)[1:-1]
    b = np.unique(np.round(b, 2))
    return [float(x) for x in b if x > 0]


def classify(values, breaks):
    """Class ids 1..len(breaks)+1 (lower bound inclusive); 0 where values are NaN."""
    cls = np.digitize(values, breaks).astype("int16") + 1
    cls[~np.isfinite(values)] = 0
    return cls


def class_labels(breaks, names=None):
    def f(x):
        return ("%g" % x)

    labels = []
    lo = None
    for b in breaks:
        labels.append("0 - %s" % f(b) if lo is None else "%s - %s" % (f(lo), f(b)))
        lo = b
    labels.append("> %s" % f(lo))
    if names and len(names) == len(labels):
        return ["%s (%s)" % (nm, lb) for nm, lb in zip(names, labels)]
    return labels


def smooth(arr, nodata_mask, passes=1):
    """Light smoothing (3x3 mean, NoData ignored), repeated `passes` times. Returns float64 with NaN for NoData."""
    z = np.where(nodata_mask, np.nan, arr.astype("float64"))
    rows, cols = z.shape
    for _ in range(max(0, int(passes))):
        p = np.pad(z, 1, mode="edge")
        total = np.zeros_like(z)
        cnt = np.zeros_like(z)
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                v = p[1 + a:1 + a + rows, 1 + b:1 + b + cols]
                ok = ~np.isnan(v)
                total += np.where(ok, v, 0.0)
                cnt += ok
        new = np.where(cnt > 0, total / np.maximum(cnt, 1), np.nan)
        new[np.isnan(z)] = np.nan
        z = new
    return z
