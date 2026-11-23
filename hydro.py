# -*- coding: utf-8 -*-
"""Pure Python/numpy hydrology core. No QGIS dependency (so it can be unit-tested)."""
import heapq
import math

import numpy as np

try:
    from math import nextafter as _next
except ImportError:  # Python < 3.9
    def _next(x, y):
        return x + abs(x) * 4.5e-16 + 1e-12

_NB = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


class Canceled(Exception):
    pass


def priority_flood(dem, nodata_mask, is_canceled=None, progress=None):
    """Priority-Flood with epsilon (Barnes et al. 2014).

    Fills depressions and, at the same time, records for every cell the
    neighbour it drains to (D8-like). Edge cells and cells next to NoData
    are the outlets of the whole DEM.

    Returns (order, receiver):
      order    - list of flat cell indices, downstream cells always before the
                 cells that drain into them
      receiver - list, receiver[i] = flat index of the cell i drains to (-1 = DEM outlet)
    """
    rows, cols = dem.shape
    n = rows * cols
    valid = ~nodata_mask
    pad = np.pad(nodata_mask, 1, mode="constant", constant_values=True)
    touches = np.zeros((rows, cols), dtype=bool)
    for dr, dc in _NB:
        touches |= pad[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
    seeds = np.flatnonzero((valid & touches).ravel()).tolist()

    z = np.where(valid, dem, 0.0).ravel().tolist()
    valid_l = bytearray(valid.ravel().astype(np.uint8).tobytes())
    closed = bytearray(n)
    receiver = [-1] * n
    heap = []
    for s in seeds:
        closed[s] = 1
        heap.append((z[s], s))
    heapq.heapify(heap)

    order = []
    pop, push = heapq.heappop, heapq.heappush
    inf = float("inf")
    total = int(valid.sum())
    while heap:
        zi, i = pop(heap)
        order.append(i)
        r, c = divmod(i, cols)
        for dr, dc in _NB:
            rr = r + dr
            cc = c + dc
            if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                continue
            j = rr * cols + cc
            if closed[j] or not valid_l[j]:
                continue
            closed[j] = 1
            receiver[j] = i
            zj = z[j]
            if zj <= zi:
                zj = _next(zi, inf)
            push(heap, (zj, j))
        if len(order) % 200000 == 0:
            if is_canceled is not None and is_canceled():
                raise Canceled()
            if progress is not None and total:
                progress(100.0 * len(order) / total)
    return order, receiver


def flow_accumulation(order, receiver, shape, nodata_mask):
    """Number of cells draining through each cell (the cell itself included)."""
    n = shape[0] * shape[1]
    acc = [1] * n
    for i in reversed(order):
        r = receiver[i]
        if r >= 0:
            acc[r] += acc[i]
    out = np.array(acc, dtype=np.int64).reshape(shape)
    out[nodata_mask] = 0
    return out


def snap_to_max(acc, row, col, radius):
    """Move (row, col) to the cell with highest accumulation within `radius` cells."""
    rows, cols = acc.shape
    if radius <= 0:
        return row, col
    r0, r1 = max(0, row - radius), min(rows, row + radius + 1)
    c0, c1 = max(0, col - radius), min(cols, col + radius + 1)
    win = acc[r0:r1, c0:c1]
    k = int(np.argmax(win))
    return r0 + k // win.shape[1], c0 + k % win.shape[1]


def label_catchments(order, receiver, outlets, shape):
    """Label every cell with the id of the nearest outlet downstream of it.

    outlets: dict {flat_index: outlet_id (>0)}
    Returns (labels 2D int32, parent dict {outlet_id: id of next downstream outlet or 0}).
    Nested outlets give sub-catchments (an upstream outlet's area is carved out
    of the downstream outlet's label).
    """
    n = shape[0] * shape[1]
    lab = [0] * n
    for i in order:
        oid = outlets.get(i)
        if oid:
            lab[i] = oid
        else:
            r = receiver[i]
            if r >= 0:
                lab[i] = lab[r]
    parent = {}
    for idx, oid in outlets.items():
        r = receiver[idx]
        parent[oid] = lab[r] if r >= 0 else 0
    return np.array(lab, dtype=np.int32).reshape(shape), parent


# ----------------------------------------------------------------------------
# Basins (no outlet points needed) and stream network
# ----------------------------------------------------------------------------
def label_basins(order, receiver, shape):
    """Label every cell with the basin (= DEM outlet cell) it drains to.

    Returns (labels 2D int32, roots) where roots[k] is the flat index of the
    outlet cell of basin id k + 1.
    """
    n = shape[0] * shape[1]
    lab = [0] * n
    roots = []
    nid = 0
    for i in order:
        r = receiver[i]
        if r < 0:
            nid += 1
            lab[i] = nid
            roots.append(i)
        else:
            lab[i] = lab[r]
    return np.array(lab, dtype=np.int32).reshape(shape), roots


def merge_small_basins(labels, min_cells):
    """Merge basins smaller than min_cells into the neighbour they share the
    longest border with. Returns (new_labels, mapping) where mapping[old_id] = new_id
    (ids are renumbered 1..m)."""
    nlab = int(labels.max())
    parent = list(range(nlab + 1))
    if min_cells > 1 and nlab > 1:
        sizes = np.bincount(labels.ravel(), minlength=nlab + 1).tolist()
        A = np.concatenate([labels[:, :-1].ravel(), labels[:-1, :].ravel()])
        B = np.concatenate([labels[:, 1:].ravel(), labels[1:, :].ravel()])
        m = (A != B) & (A > 0) & (B > 0)
        A, B = A[m], B[m]
        key = np.minimum(A, B).astype(np.int64) * (nlab + 1) + np.maximum(A, B)
        uk, cnt = np.unique(key, return_counts=True)
        adj = {}
        for k, c in zip(uk.tolist(), cnt.tolist()):
            x, y = divmod(k, nlab + 1)
            adj.setdefault(x, {})[y] = c
            adj.setdefault(y, {})[x] = c
        alive = [True] * (nlab + 1)
        heap = [(sizes[k], k) for k in range(1, nlab + 1) if sizes[k] < min_cells]
        heapq.heapify(heap)
        while heap:
            s, l = heapq.heappop(heap)
            if not alive[l] or s != sizes[l]:
                continue
            nb = adj.get(l)
            if not nb:
                continue  # isolated (surrounded by NoData): keep as is
            t = max(nb, key=nb.get)
            for c, v in nb.items():
                if c == t:
                    continue
                d = adj[c]
                del d[l]
                d[t] = d.get(t, 0) + v
                adj[t][c] = adj[t].get(c, 0) + v
            del adj[t][l]
            del adj[l]
            alive[l] = False
            parent[l] = t
            sizes[t] += sizes[l]
            if sizes[t] < min_cells:
                heapq.heappush(heap, (sizes[t], t))

    final = np.zeros(nlab + 1, dtype=np.int32)
    new_id = {}
    for k in range(1, nlab + 1):
        root = k
        while parent[root] != root:
            root = parent[root]
        if root not in new_id:
            new_id[root] = len(new_id) + 1
        final[k] = new_id[root]
    return final[labels], final


def stream_reaches(order, receiver, acc, threshold, restrict=None, cell_reach=None, info=None):
    """Extract the stream network as reaches (headwater/confluence -> next confluence/outlet).

    acc        2D accumulation (cells)
    threshold  minimum accumulation (cells) for a cell to be a stream cell
    restrict   optional flat boolean array; only cells where True can be stream cells
    cell_reach optional list (len = number of cells); filled with the 1-based reach id
               of every stream cell (a confluence cell belongs to the reach that starts there)
    info       optional dict; info['new_stream'] gets one bool per reach: True when the
               reach starts a new Strahler stream (headwater, or a confluence where the order rises)

    Returns list of (cells, strahler_order, acc_at_end). cells = flat indices from upstream to
    downstream; the last one may be the confluence cell that starts the next reach, so lines
    connect. A reach with a single cell has no length (no line).
    """
    accf = acc.ravel()
    is_s = accf >= threshold
    if restrict is not None:
        is_s &= restrict
    sl = is_s.tolist()
    n = len(sl)
    inflow = [0] * n
    maxo = [0] * n
    cnt = [0] * n
    sorder = [0] * n
    for i in reversed(order):
        if not sl[i]:
            continue
        m = maxo[i]
        o = 1 if m == 0 else (m + 1 if cnt[i] >= 2 else m)
        sorder[i] = o
        r = receiver[i]
        if r >= 0 and sl[r]:
            inflow[r] += 1
            if o > maxo[r]:
                maxo[r] = o
                cnt[r] = 1
            elif o == maxo[r]:
                cnt[r] += 1

    reaches = []
    flags = []
    for i in order:
        if not sl[i] or inflow[i] == 1:
            continue
        path = [i]
        own_last = i
        cur = i
        at_confluence = False
        while True:
            r = receiver[cur]
            if r < 0 or not sl[r]:
                break
            path.append(r)
            if inflow[r] != 1:
                at_confluence = True
                break
            cur = r
            own_last = r
        reaches.append((path, sorder[i], int(accf[own_last])))
        flags.append(inflow[i] == 0 or sorder[i] > maxo[i])
        if cell_reach is not None:
            rid = len(reaches)
            for c in (path[:-1] if at_confluence else path):
                cell_reach[c] = rid
    if info is not None:
        info["new_stream"] = flags
        info["cell_order"] = sorder
        info["inflow"] = inflow
    return reaches


def reach_downstream_ids(reaches):
    """Id (1-based) of the reach each reach flows into; 0 for reaches leaving the network."""
    start = {path[0]: k for k, (path, _, _) in enumerate(reaches, 1)}
    down = []
    for path, _, _ in reaches:
        down.append(start.get(path[-1], 0) if len(path) > 1 else 0)
    return down


def label_subcatchments(order, receiver, cell_reach, shape):
    """Label every cell with the reach it drains into (stream cells: their own reach).
    Cells that reach the DEM edge without meeting a stream keep label 0."""
    n = shape[0] * shape[1]
    lab = [0] * n
    for i in order:
        cr = cell_reach[i]
        if cr:
            lab[i] = cr
        else:
            r = receiver[i]
            if r >= 0:
                lab[i] = lab[r]
    return np.array(lab, dtype=np.int32).reshape(shape)


def flow_length(order, receiver, shape, dx_row, dy):
    """Distance (m) along the flow path from every cell to the DEM outlet, and the length of
    the step to its receiver. dx_row: cell width per row (m), dy: cell height (m)."""
    rows, cols = shape
    n = rows * cols
    dxr = [float(v) for v in np.broadcast_to(np.asarray(dx_row, dtype="float64"), (rows,))]
    dy = float(dy)
    FL = [0.0] * n
    step = [0.0] * n
    hyp = math.hypot
    for i in order:
        r = receiver[i]
        if r < 0:
            continue
        ri = i // cols
        rr = r // cols
        if rr == ri:
            sdist = dxr[ri]
        elif r - rr * cols == i - ri * cols:
            sdist = dy
        else:
            sdist = hyp(dxr[ri], dy)
        step[i] = sdist
        FL[i] = FL[r] + sdist
    return np.array(FL), np.array(step)


# ----------------------------------------------------------------------------
# Flow direction raster (ESRI D8 encoding)
# ----------------------------------------------------------------------------
_D8_CODES = {(0, 1): 1, (1, 1): 2, (1, 0): 4, (1, -1): 8, (0, -1): 16, (-1, -1): 32, (-1, 0): 64, (-1, 1): 128}
# E, S, W, N first so that outlet cells prefer a cardinal direction
_OUT_ORDER = ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, -1), (-1, 1))
FDIR_NODATA = 255


def flow_direction(receiver, shape, nodata_mask):
    """D8 flow direction raster (uint8) using ESRI codes:
    1=E 2=SE 4=S 8=SW 16=W 32=NW 64=N 128=NE.
    Outlet cells (DEM edge / next to NoData) point out of the DEM. NoData = 255.
    """
    rows, cols = shape
    n = rows * cols
    rec = np.asarray(receiver, dtype=np.int64)
    idx = np.arange(n)
    has = rec >= 0
    dr = np.zeros(n, dtype=np.int64)
    dc = np.zeros(n, dtype=np.int64)
    dr[has] = rec[has] // cols - idx[has] // cols
    dc[has] = rec[has] % cols - idx[has] % cols
    out = np.zeros(n, dtype=np.uint8)
    for (a, b), code in _D8_CODES.items():
        out[has & (dr == a) & (dc == b)] = code
    out = out.reshape(shape)

    roots = (rec.reshape(shape) < 0) & ~nodata_mask
    pad = np.pad(nodata_mask, 1, mode="constant", constant_values=True)
    for a, b in _OUT_ORDER:
        nb = pad[1 + a:1 + a + rows, 1 + b:1 + b + cols]
        sel = roots & (out == 0) & nb
        out[sel] = _D8_CODES[(a, b)]
    out[nodata_mask] = FDIR_NODATA
    return out
