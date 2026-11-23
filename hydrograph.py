# -*- coding: utf-8 -*-
"""Synthetic unit hydrographs (Nakayasu, Snyder-Alexeyev) and flood hydrographs by convolution.
Pure Python, no QGIS dependency.

Units: area km2, lengths km, times hours, discharge m3/s, rainfall mm.
The unit hydrograph is the response to 1 mm of effective rainfall over the whole catchment.
"""
import math

NAKAYASU = "nakayasu"
SNYDER = "snyder"
NAK_SEGMENTS = ("QA", "Qd1", "Qd2", "Qd3")
MM_KM2_M3S_H = 1000.0 / 3600.0      # 1 mm over 1 km2 = 1000 m3 = 0.2778 m3/s x hour


# ----------------------------------------------------------------------------
# Parameters and curves
# ----------------------------------------------------------------------------
def nakayasu_params(length_km, area_km2, tr_factor=0.75, alpha=2.0):
    """tg = 0.21 L^0.7 (L < 15 km) or 0.4 + 0.058 L; tr = tr_factor * tg; Tp = tg + 0.8 tr;
    T0.3 = alpha * tg; Qp = A * 1 mm / (3.6 (0.3 Tp + T0.3))."""
    if length_km <= 0 or area_km2 <= 0:
        raise ValueError("Length and area must be greater than zero.")
    tg = 0.21 * length_km ** 0.7 if length_km < 15 else 0.4 + 0.058 * length_km
    tr = tr_factor * tg
    tpeak = tg + 0.8 * tr
    t03 = alpha * tg
    qp = area_km2 / (3.6 * (0.3 * tpeak + t03))
    return {"method": NAKAYASU, "lag": tg, "tr": tr, "tpeak": tpeak, "t03": t03, "qp": qp,
            "area": area_km2, "length": length_km, "alpha": alpha, "tr_factor": tr_factor}


def snyder_params(length_km, lc_km, area_km2, ct, cp):
    """Snyder with the Alexeyev curve: tp = Ct (L Lc)^0.3; tr = tp / 5.5; Tp = tp + 0.5 tr;
    Qp = 0.278 Cp A / tp (m3/s per mm); lambda = 3.6 Qp Tp / A; a = 1.32 lambda^2 + 0.15 lambda + 0.045."""
    if length_km <= 0 or lc_km <= 0 or area_km2 <= 0:
        raise ValueError("Length, Lc and area must be greater than zero.")
    tp = ct * (length_km * lc_km) ** 0.3
    tr = tp / 5.5
    tpeak = tp + 0.5 * tr
    qp = 0.278 * cp * area_km2 / tp
    lam = 3.6 * qp * tpeak / area_km2
    a = 1.32 * lam ** 2 + 0.15 * lam + 0.045
    return {"method": SNYDER, "lag": tp, "tr": tr, "tpeak": tpeak, "qp": qp, "a": a, "lambda": lam,
            "area": area_km2, "length": length_km, "lc": lc_km, "ct": ct, "cp": cp}


def raw_curve(t, p):
    """(segment name, discharge) of the unit hydrograph at time t (before any scaling)."""
    qp, tpk = p["qp"], p["tpeak"]
    if t <= 0:
        return ("QA" if p["method"] == NAKAYASU else "Q"), 0.0
    if p["method"] == NAKAYASU:
        t03 = p["t03"]
        if t < tpk:
            return "QA", qp * (t / tpk) ** 2.4
        if t < tpk + t03:
            return "Qd1", qp * 0.3 ** ((t - tpk) / t03)
        if t < tpk + 2.5 * t03:
            return "Qd2", qp * 0.3 ** ((t - tpk + 0.5 * t03) / (1.5 * t03))
        return "Qd3", qp * 0.3 ** ((t - tpk + 1.5 * t03) / (2.0 * t03))
    x = t / tpk
    return "Q", qp * 10 ** (-p["a"] * (1.0 - x) ** 2 / x)


def horizon(p, cutoff=0.001, max_steps=4000):
    """Time (h) after which the unit hydrograph is below `cutoff` x Qp."""
    tpk = p["tpeak"]
    step = tpk / 20.0
    t = tpk
    for _ in range(max_steps):
        t += step
        if raw_curve(t, p)[1] < cutoff * p["qp"]:
            return t
    return t


def curve_volume(p, n=4000):
    """Integral of the raw curve in m3/s x h (Simpson)."""
    th = horizon(p, 1e-6)
    n += n % 2
    h = th / n
    s = raw_curve(0.0, p)[1] + raw_curve(th, p)[1]
    for i in range(1, n):
        s += (4 if i % 2 else 2) * raw_curve(i * h, p)[1]
    return s * h / 3.0


def norm_factor(p):
    """Factor that makes the unit hydrograph volume exactly 1 mm x A."""
    return MM_KM2_M3S_H * p["area"] / curve_volume(p)


# ----------------------------------------------------------------------------
# Ordinates
# ----------------------------------------------------------------------------
def _s_curve(t, p, scale):
    """S(t) = sum of the curve lagged by multiples of tr (response to a continuous rain of 1 mm per tr)."""
    tr = p["tr"]
    s, j = 0.0, 0
    while t - j * tr > 0:
        s += scale * raw_curve(t - j * tr, p)[1]
        j += 1
    return s


def ordinates(p, dt, normalize=True, scurve=False):
    """Unit hydrograph ordinates every dt hours (index 0 = time 0).
    Returns (rows, scale), rows = list of dict(t, seg, curve, q):
      curve - the (scaled) base curve of the method at time t,
      q     - ordinate used in the convolution: the curve itself, or the S-curve converted
              hydrograph for a rain of duration dt when scurve=True."""
    scale = norm_factor(p) if normalize else 1.0
    th = horizon(p)
    k_max = int(math.ceil(th / dt)) + 1
    rows = []
    for k in range(k_max + 1):
        t = k * dt
        seg, q = raw_curve(t, p)
        q *= scale
        if scurve and k > 0:
            u = p["tr"] / dt * (_s_curve(t, p, scale) - _s_curve(t - dt, p, scale))
        else:
            u = q
        rows.append({"t": t, "seg": seg, "curve": q, "q": u})
    return rows, scale


def fine_curve(p, scale=1.0, n=240):
    """Dense curve for plotting: list of (t, seg, q)."""
    th = horizon(p)
    out = []
    for i in range(n + 1):
        t = th * i / n
        seg, q = raw_curve(t, p)
        out.append((t, seg, q * scale))
    return out


# ----------------------------------------------------------------------------
# Flood hydrograph
# ----------------------------------------------------------------------------
def convolve(pe, u):
    """Q_m = sum_{j=1..min(m,n)} Pe_j * U_{m-j+1}  (rain of interval j applied at its start).
    pe: effective rainfall per step (mm) for steps 1..n; u: unit ordinates U_0..U_K (U_0 = 0).
    Returns Q_0..Q_M with M = n + K - 1."""
    n, k_max = len(pe), len(u) - 1
    m_max = n + k_max - 1
    q = [0.0] * (m_max + 1)
    for m in range(1, m_max + 1):
        s = 0.0
        for j in range(1, min(m, n) + 1):
            k = m - j + 1
            if k <= k_max:
                s += pe[j - 1] * u[k]
        q[m] = s
    return q


def flood(pe, dt, urows, area_km2, baseflow=0.0):
    """Flood hydrograph and summary for the effective rainfall series `pe` (mm per step of dt hours)."""
    q = convolve(pe, [r["q"] for r in urows])
    total = [v + baseflow for v in q]
    peak = max(total)
    k = total.index(peak)
    vol_m3 = sum(q) * dt * 3600.0
    return {"t": [i * dt for i in range(len(total))], "q": total, "peak": peak, "t_peak": k * dt,
            "pe_total": sum(pe), "volume_mm": vol_m3 / (area_km2 * 1000.0)}
