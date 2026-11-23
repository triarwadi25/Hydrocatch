# -*- coding: utf-8 -*-
"""Design storm calculations: Mononobe intensity, hourly rainfall distribution, effective rainfall.
Pure Python, no QGIS dependency."""
import math

AMC_NAMES = ["I (dry)", "II (average)", "III (wet)"]


def mononobe_intensity(r24, t_hours):
    """Mononobe: I = (R24 / 24) * (24 / t)^(2/3)   [mm/h], t in hours."""
    return r24 / 24.0 * (24.0 / t_hours) ** (2.0 / 3.0)


def hourly_ratios(duration_h, step_h=1.0):
    """Share of R24 that falls in every time step when all R24 falls in `duration_h` hours.

    Cumulative ratio at time t: (t / T)^(1/3)  (Mononobe, T = rain duration), so the first
    hour of a 6 h storm gets 55 %, the second 14.3 %, ... Returns a list that sums to 1.
    """
    n = int(round(duration_h / step_h))
    if n < 1 or abs(n * step_h - duration_h) > 1e-9:
        raise ValueError("The rain duration must be a whole number of time steps.")
    cum = [((k * step_h) / duration_h) ** (1.0 / 3.0) for k in range(n + 1)]
    return [cum[k + 1] - cum[k] for k in range(n)]


def alternating_block(blocks):
    """Alternating block arrangement: largest block in the middle (block ceil(n/2)),
    then the next ones alternately after and before it."""
    n = len(blocks)
    srt = sorted(blocks, reverse=True)
    out = [0.0] * n
    c = (n - 1) // 2
    placed = 0
    off = 0
    while placed < n:
        for pos in ((c + off,) if off == 0 else (c + off, c - off)):
            if 0 <= pos < n and placed < n:
                out[pos] = srt[placed]
                placed += 1
        off += 1
    return out


def adjust_cn(cn2, amc):
    """Antecedent moisture condition: 0 = I (dry), 1 = II (average), 2 = III (wet)."""
    if amc == 0:
        return 4.2 * cn2 / (10.0 - 0.058 * cn2)
    if amc == 2:
        return 23.0 * cn2 / (10.0 + 0.13 * cn2)
    return cn2


def effective_runoff_coeff(rain, c):
    return [c * r for r in rain]


def effective_scs(rain, cn, ia_ratio=0.2):
    """SCS-CN on the cumulative hyetograph; returns incremental effective rainfall (mm)."""
    s = 25400.0 / cn - 254.0
    ia = ia_ratio * s
    cum, prev_q, out = 0.0, 0.0, []
    for r in rain:
        cum += r
        q = 0.0 if cum <= ia else (cum - ia) ** 2 / (cum - ia + s)
        out.append(q - prev_q)
        prev_q = q
    return out


def effective_phi(rain, phi_mm_per_h, step_h):
    return [max(r - phi_mm_per_h * step_h, 0.0) for r in rain]


def design_storm(r24, duration_h, step_min, arrange="mononobe", method="none", c=0.5, cn=75.0, amc=1,
                 ia_ratio=0.2, phi=5.0):
    """Hourly (or sub-hourly) hyetograph for one design R24 and its effective rainfall.
    method: 'none', 'coeff', 'scs', 'phi'. Returns list of dict rows."""
    step_h = step_min / 60.0
    ratios = hourly_ratios(duration_h, step_h)
    if arrange == "abm":
        ratios = alternating_block(ratios)
    rain = [r24 * q for q in ratios]
    if method == "coeff":
        eff = effective_runoff_coeff(rain, c)
    elif method == "scs":
        eff = effective_scs(rain, adjust_cn(cn, amc), ia_ratio)
    elif method == "phi":
        eff = effective_phi(rain, phi, step_h)
    else:
        eff = None
    rows, cum, cum_e = [], 0.0, 0.0
    for k, (q, r) in enumerate(zip(ratios, rain), 1):
        cum += r
        row = {"step": k, "t0": (k - 1) * step_min, "t1": k * step_min, "ratio": q, "rain": r, "cum": cum,
               "intensity": r / step_h}
        if eff is not None:
            cum_e += eff[k - 1]
            row.update(eff=eff[k - 1], cum_eff=cum_e, loss=r - eff[k - 1])
        rows.append(row)
    return rows


def idf_table(r24, durations_hours):
    """Mononobe intensity (mm/h) and depth (mm) for each duration given in hours."""
    out = []
    for h in durations_hours:
        i = mononobe_intensity(r24, h)
        out.append({"hours": h, "duration": h * 60.0, "intensity": i, "depth": i * h})
    return out


def rational_discharge(c, intensity_mm_h, area_km2):
    """Q = 0.2778 C I A  (m3/s)."""
    return 0.2778 * c * intensity_mm_h * area_km2
