# -*- coding: utf-8 -*-
"""Per-catchment morphometry from routed DEM arrays, pure numpy (no QGIS dependency)."""
import math

import numpy as np


class Terrain:
    """Holder of the full-DEM arrays needed for per-polygon statistics."""

    def __init__(self, dem, accf, receiver, order, FL, step, is_stream, slope_pct, starts, orders, newflag,
                 cell_order=None, inflow=None, cols=0, dx=None, dy=None):
        self.dem = dem                    # flat float64, original (unfilled) elevations
        self.accf = accf                  # flat int64 flow accumulation
        self.receiver = receiver          # python list
        pos = np.empty(len(receiver), dtype=np.int64)
        pos[np.asarray(order, dtype=np.int64)] = np.arange(len(order))
        self.pos = pos                    # position in routing order (downstream first)
        self.FL = FL                      # flat flow length to DEM outlet (m)
        self.step = step                  # flat length of the step to the receiver (m)
        self.is_stream = is_stream        # flat bool
        self.slope = slope_pct            # flat float, percent
        self.starts = starts              # np array, first cell of every stream reach
        self.orders = orders              # np array, Strahler order of every reach
        self.newflag = newflag            # np bool array, reach starts a new Strahler stream
        self.cell_order = cell_order      # flat Strahler order of every stream cell (0 = not a stream)
        self.inflow = inflow              # flat number of stream cells flowing into each cell
        self.cols = cols                  # raster width (to turn flat indices into row / column)
        self.rows = (len(receiver) // cols) if cols else 0
        self.dx = dx                      # cell width per row (m)
        self.dy = dy                      # cell height (m)


def kirpich_tc_minutes(length_m, slope_mm):
    """Kirpich (1940): Tc [min] = 0.0195 * L^0.77 * S^-0.385 (L in m, S in m/m)."""
    if length_m <= 0:
        return None
    return 0.0195 * (length_m ** 0.77) * (max(slope_mm, 1e-5) ** -0.385)


def cell_stats(flat, t, area_km2, perim_km):
    """Statistics for the cells `flat` (flat indices, valid DEM cells only) of one polygon."""
    out = {}
    z = t.dem[flat]
    zmin, zmax, zmean = float(z.min()), float(z.max()), float(z.mean())
    relief = zmax - zmin
    out.update(elev_min=zmin, elev_max=zmax, elev_mean=zmean, relief_m=relief)
    sl = t.slope[flat]
    sl = sl[np.isfinite(sl)]
    out["slope_pct"] = float(sl.mean()) if sl.size else None
    out["hyps_int"] = (zmean - zmin) / relief if relief > 0 else None

    # --- longest flow path to the polygon outlet (cell with highest accumulation) ---
    o = int(flat[np.argmax(t.accf[flat])])
    srt = flat[np.argsort(t.pos[flat], kind="stable")]
    rec = t.receiver
    inset = set()
    members = []
    for c in srt.tolist():
        if c == o or rec[c] in inset:
            inset.add(c)
            members.append(c)
    m = np.array(members, dtype=np.int64)
    fl = t.FL[m] - t.FL[o]
    k = int(np.argmax(fl))
    L = float(fl[k])
    head = int(m[k])
    out["lfp_km"] = L / 1000.0
    if L > 0:
        s = (t.dem[head] - t.dem[o]) / L
        if s <= 0:
            s = relief / L
        out["lfp_slope"] = float(s)
        out["tc_min"] = kirpich_tc_minutes(L, float(s))
    else:
        out["lfp_slope"] = None
        out["tc_min"] = None

    # --- streams ---
    sflat = flat[t.is_stream[flat]]
    stream_m = float(t.step[sflat].sum()) if sflat.size else 0.0
    out["stream_km"] = stream_m / 1000.0
    out["drain_dens"] = (stream_m / 1000.0) / area_km2 if area_km2 > 0 else None
    inpoly = np.isin(t.starts, flat)
    ords = t.orders[inpoly]
    flags = t.newflag[inpoly]
    out["max_order"] = int(ords.max()) if ords.size else 0
    n_by = {}
    for u in range(1, out["max_order"] + 1):
        n_by[u] = int(((ords == u) & flags).sum())
    out["n_streams"] = int(sum(n_by.values()))
    ratios = [n_by[u] / n_by[u + 1] for u in range(1, out["max_order"]) if n_by.get(u + 1, 0) > 0]
    out["rb_mean"] = float(np.mean(ratios)) if ratios else None

    # --- descriptors used by HSS Gama I (approximated from the flow routing) ---
    out.update(lc_km=None, sf=None, sn=None, jn=None, wf=None, rua=None, sim=None)
    if t.cell_order is not None:
        so = t.cell_order[sflat] if sflat.size else np.array([], dtype=np.int64)
        tot = float(t.step[sflat].sum()) if sflat.size else 0.0
        if tot > 0:
            out["sf"] = float(t.step[sflat[so == 1]].sum()) / tot
        out["sn"] = (n_by.get(1, 0) / float(out["n_streams"])) if out["n_streams"] else None
        out["jn"] = int((t.inflow[sflat] >= 2).sum()) if sflat.size else 0
    out.update(lmain_km=None, smain=None)
    if L > 0 and t.cols and t.dx is not None:
        # main stream: from the outlet go upstream along the stream neighbour with the largest accumulation
        path, cur = [o], o
        while True:
            r_, c_ = divmod(cur, t.cols)
            best = None
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc2 = r_ + dr, c_ + dc
                    if (dr or dc) and 0 <= rr < t.rows and 0 <= cc2 < t.cols:
                        nb = rr * t.cols + cc2
                        if rec[nb] == cur and t.is_stream[nb] and nb in inset and (best is None or t.accf[nb] > t.accf[best]):
                            best = nb
            if best is None:
                break
            path.append(best)
            cur = best
        path.reverse()                                  # head first
        if len(path) < 2:                               # no stream: use the longest flow path
            path, c = [], head
            while True:
                path.append(c)
                if c == o or rec[c] < 0:
                    break
                c = rec[c]
        head_main = path[0]
        l_main = float(t.FL[head_main] - t.FL[o])
        if l_main > 0:
            out["lmain_km"] = l_main / 1000.0
            sm = (t.dem[head_main] - t.dem[o]) / l_main
            out["smain"] = float(sm if sm > 0 else relief / l_main)
        mainset = set(path)
        dxm, dy = float(np.mean(t.dx)), float(t.dy)
        rows_f, cols_f = flat // t.cols, flat % t.cols
        rc, cc = rows_f.mean(), cols_f.mean()
        pa = np.array(path, dtype=np.int64)
        d2 = (((pa // t.cols) - rc) * dy) ** 2 + (((pa % t.cols) - cc) * dxm) ** 2
        lc_cell = int(pa[int(np.argmin(d2))])
        lc_dist = float(t.FL[lc_cell] - t.FL[o])
        out["lc_km"] = lc_dist / 1000.0
        # position of every cell along the main stream = flow length of the nearest main-stream cell
        # (this mimics measuring perpendicular to the stream)
        pr_, pc_ = (pa // t.cols).astype(np.float64), (pa % t.cols).astype(np.float64)
        path_pos = t.FL[pa] - t.FL[o]
        d_a = np.empty(len(flat))
        for s0 in range(0, len(flat), 4000):
            rr = rows_f[s0:s0 + 4000, None].astype(np.float64)
            cc2 = cols_f[s0:s0 + 4000, None].astype(np.float64)
            d_a[s0:s0 + 4000] = path_pos[np.argmin(((rr - pr_[None, :]) * dy) ** 2 + ((cc2 - pc_[None, :]) * dxm) ** 2, axis=1)]
        out["rua"] = float((d_a > lc_dist).sum()) / len(flat)
        lm = l_main if l_main > 0 else L
        delta = max(0.05 * lm, 2.0 * dxm)
        area_cell = dxm * dy

        def width(x):
            return float((np.abs(d_a - x) <= delta).sum()) * area_cell / (2.0 * delta)

        wl, wu = width(0.25 * lm), width(0.75 * lm)
        if wl > 0 and wu > 0:
            out["wf"] = wu / wl
            out["sim"] = out["wf"] * out["rua"]

    # --- shape indices ---
    a_m2 = area_km2 * 1e6
    p_m = perim_km * 1000.0
    if L > 0 and a_m2 > 0:
        out["form_fac"] = a_m2 / (L * L)
        out["elong_rat"] = 1.128 * math.sqrt(a_m2) / L
        out["relief_rat"] = relief / L
    else:
        out["form_fac"] = out["elong_rat"] = out["relief_rat"] = None
    if a_m2 > 0 and p_m > 0:
        out["circ_rat"] = 4.0 * math.pi * a_m2 / (p_m * p_m)
        out["compact"] = 0.2821 * p_m / math.sqrt(a_m2)
    else:
        out["circ_rat"] = out["compact"] = None
    out["outlet_cell"] = o
    out["head_cell"] = head
    return out
