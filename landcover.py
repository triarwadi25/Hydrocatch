# -*- coding: utf-8 -*-
"""Land cover classification and runoff coefficient helpers, pure numpy (no QGIS / GDAL dependency)."""
import re

import numpy as np

# Class id, name, runoff coefficient C for slope classes (<3 %, 3-15 %, >15 %). '-' = not defined in the table.
DEFAULT_CLASS_TABLE = """1, Sawah / rawa, 0.2, -, -
2, Hutan / perkebunan, 0.3, 0.4, 0.6
3, Perumahan (dengan kebun), 0.4, 0.5, 0.7
4, Tumbuhan jarang, -, 0.6, 0.8
5, Tanpa tumbuhan / lahan terbuka / tambang, -, 0.7, 0.9"""
DEFAULT_BREAKS = "3, 15"


# ----------------------------------------------------------------------------
# Class table
# ----------------------------------------------------------------------------
def parse_class_table(text):
    """'id, name, C(<3), C(3-15), C(>15)' per line ('-' or empty = not defined)."""
    classes, seen = [], set()
    for ln, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[;\t,]", line)]
        if len(parts) < 2:
            raise ValueError("Class table line %d: need at least 'id, name'." % ln)
        try:
            cid = int(parts[0])
        except ValueError:
            raise ValueError("Class table line %d: the class id must be a whole number." % ln)
        if cid < 1 or cid > 250 or cid in seen:
            raise ValueError("Class table line %d: class ids must be unique numbers from 1 to 250." % ln)
        seen.add(cid)
        c = []
        for p in parts[2:5]:
            if p in ("", "-", "--", "na", "NA"):
                c.append(None)
            else:
                try:
                    v = float(p)
                except ValueError:
                    raise ValueError("Class table line %d: '%s' is not a number." % (ln, p))
                if not 0.0 <= v <= 1.5:
                    raise ValueError("Class table line %d: C = %g is outside 0 - 1.5." % (ln, v))
                c.append(v)
        c += [None] * (3 - len(c))
        classes.append({"id": cid, "name": parts[1], "c": c})
    if not classes:
        raise ValueError("The class table is empty.")
    return classes


def parse_breaks(text):
    try:
        b = [float(t) for t in re.split(r"[;,\s]+", (text or "").strip()) if t]
    except ValueError:
        raise ValueError("Slope breaks must be numbers, e.g. '3, 15'.")
    if len(b) != 2 or b[0] >= b[1] or b[0] < 0:
        raise ValueError("Give two slope breaks in ascending order, e.g. '3, 15' (percent).")
    return b


def resolve_c(c3):
    """Fill undefined slope classes with the nearest defined one (mean of equally near ones)."""
    if all(v is None for v in c3):
        return None
    out = []
    for i in range(3):
        if c3[i] is not None:
            out.append(c3[i])
            continue
        for dist in (1, 2):
            near = [c3[j] for j in (i - dist, i + dist) if 0 <= j < 3 and c3[j] is not None]
            if near:
                out.append(sum(near) / len(near))
                break
    return out


def slope_class_index(slope, breaks):
    """0 = below breaks[0], 1 = between, 2 = at or above breaks[1]; -1 where slope is NaN."""
    idx = np.digitize(slope, breaks).astype("int8")
    idx[~np.isfinite(slope)] = -1
    return idx


def c_raster(lc, slope, classes, breaks):
    """Runoff coefficient per pixel from land cover ids and slope (percent). NaN where undefined."""
    out = np.full(lc.shape, np.nan, dtype="float32")
    idx = slope_class_index(slope, breaks)
    for cl in classes:
        c = resolve_c(cl["c"])
        if c is None:
            continue
        sel = (lc == cl["id"]) & (idx >= 0)
        if sel.any():
            out[sel] = np.asarray(c, dtype="float32")[idx[sel]]
    return out


# ----------------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------------
def local_std(a, window):
    """Local standard deviation in a window x window neighbourhood."""
    r = window // 2
    p = np.pad(a.astype("float64"), r, mode="reflect")
    h, w = a.shape
    s = np.zeros((h, w))
    s2 = np.zeros((h, w))
    for i in range(window):
        for j in range(window):
            v = p[i:i + h, j:j + w]
            s += v
            s2 += v * v
    n = float(window * window)
    return np.sqrt(np.maximum(s2 / n - (s / n) ** 2, 0.0)).astype("float32")


def build_features(bands, vari=False, texture_window=0, red=1, nir=0):
    """bands: (F, H, W). Optional extra layers: VARI (RGB greenness), NDVI, local texture.
    Band 1-3 are assumed to be R, G, B for VARI. Returns (F2, H, W) float32."""
    b = np.asarray(bands, dtype="float32")
    layers = [b[i] for i in range(b.shape[0])]
    if vari and b.shape[0] >= 3:
        r, g, bl = b[0], b[1], b[2]
        den = g + r - bl
        den = np.where(np.abs(den) < 1e-6, 1e-6, den)
        layers.append(np.clip((g - r) / den, -1.0, 1.0))
    if nir and 1 <= nir <= b.shape[0] and 1 <= red <= b.shape[0] and nir != red:
        rd, ni = b[red - 1], b[nir - 1]
        den = ni + rd
        den = np.where(np.abs(den) < 1e-6, 1e-6, den)
        layers.append(np.clip((ni - rd) / den, -1.0, 1.0))
    if texture_window and texture_window >= 3:
        gray = b[:3].mean(axis=0) if b.shape[0] >= 3 else b[0]
        layers.append(local_std(gray, int(texture_window)))
    return np.stack(layers).astype("float32")


def standardize_params(X):
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    return mean, std


# ----------------------------------------------------------------------------
# Classifiers
# ----------------------------------------------------------------------------
class Model:
    def __init__(self, kind, ids, means, covs=None):
        self.kind, self.ids, self.means = kind, list(ids), means
        self.inv, self.logdet = None, None
        if kind == "ml":
            self.inv = np.array([np.linalg.inv(c) for c in covs])
            self.logdet = np.array([np.linalg.slogdet(c)[1] for c in covs])

    def predict(self, X, chunk=200000):
        out = np.empty(len(X), dtype="int16")
        ids = np.asarray(self.ids)
        for s in range(0, len(X), chunk):
            x = X[s:s + chunk].astype("float64")
            if self.kind == "md":
                d = ((x[:, None, :] - self.means[None, :, :]) ** 2).sum(axis=2)
                out[s:s + chunk] = ids[np.argmin(d, axis=1)]
            else:
                score = np.empty((len(x), len(ids)))
                for k in range(len(ids)):
                    dv = x - self.means[k]
                    score[:, k] = -0.5 * (self.logdet[k] + ((dv @ self.inv[k]) * dv).sum(axis=1))
                out[s:s + chunk] = ids[np.argmax(score, axis=1)]
        return out


def train(X, y, kind="ml", shrink=0.05, ridge=1e-3):
    """X standardised (N, F), y class ids. kind: 'ml' (maximum likelihood) or 'md' (minimum distance)."""
    ids = sorted(set(int(v) for v in y))
    means = np.array([X[y == k].mean(axis=0) for k in ids])
    if kind == "md":
        return Model("md", ids, means)
    f = X.shape[1]
    pooled = np.cov(X.T) if len(X) > 1 else np.eye(f)
    pooled = np.atleast_2d(pooled)
    covs = []
    for k in ids:
        xk = X[y == k]
        c = np.atleast_2d(np.cov(xk.T)) if len(xk) > 1 else pooled
        if not np.all(np.isfinite(c)):
            c = pooled
        # few samples -> lean on the pooled covariance (a class covariance from a handful of pixels is unreliable)
        a = max(shrink, min(0.9, 1.0 - len(xk) / (5.0 * f)))
        c = (1.0 - a) * c + a * pooled + ridge * np.eye(f)
        covs.append(c)
    return Model("ml", ids, means, covs)


# ----------------------------------------------------------------------------
# Post-processing and accuracy
# ----------------------------------------------------------------------------
def majority_filter(labels, passes=1):
    """3x3 majority filter; 0 (NoData) stays 0 and does not vote."""
    lab = labels.copy()
    h, w = lab.shape
    for _ in range(max(0, int(passes))):
        p = np.pad(lab, 1, mode="edge")
        best = np.zeros((h, w), dtype="uint8")
        out = lab.copy()
        for k in sorted(int(v) for v in np.unique(lab) if v != 0):
            cnt = np.zeros((h, w), dtype="uint8")
            for i in range(3):
                for j in range(3):
                    cnt += (p[i:i + h, j:j + w] == k)
            better = cnt > best
            out[better] = k
            best[better] = cnt[better]
        out[lab == 0] = 0
        lab = out
    return lab


def confusion(ref, pred, ids):
    """Confusion matrix (rows = reference, columns = predicted) and accuracy measures."""
    ids = list(ids)
    pos = {k: i for i, k in enumerate(ids)}
    m = np.zeros((len(ids), len(ids)), dtype=np.int64)
    for r, p in zip(np.asarray(ref).tolist(), np.asarray(pred).tolist()):
        if r in pos and p in pos:
            m[pos[r], pos[p]] += 1
    total = int(m.sum())
    if total == 0:
        return {"matrix": m, "oa": None, "kappa": None, "producers": [None] * len(ids), "users": [None] * len(ids), "n": 0}
    diag = np.diag(m).astype(float)
    row, col = m.sum(axis=1).astype(float), m.sum(axis=0).astype(float)
    oa = diag.sum() / total
    pe = float((row * col).sum()) / (total * total)
    kappa = (oa - pe) / (1 - pe) if pe < 1 else 1.0
    prod = [float(diag[i] / row[i]) if row[i] else None for i in range(len(ids))]
    user = [float(diag[i] / col[i]) if col[i] else None for i in range(len(ids))]
    return {"matrix": m, "oa": float(oa), "kappa": float(kappa), "producers": prod, "users": user, "n": total}
