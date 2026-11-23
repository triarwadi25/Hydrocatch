# -*- coding: utf-8 -*-
"""HTML report (Indonesian) with SVG charts for the design storm tool. No QGIS dependency."""
import math
from html import escape

from .hss_report import CSS, _f, _nice_axis, _tick

T_COLORS = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd", "#8c564b", "#17becf", "#7f7f7f"]
PREFERRED_TICKS = (0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 24)


def _log_ticks(lo, hi):
    ticks = []
    for k in range(int(math.floor(math.log10(lo))), int(math.ceil(math.log10(hi))) + 1):
        for m in (1, 2, 5):
            v = m * 10.0 ** k
            if lo * 0.999 <= v <= hi * 1.001:
                ticks.append(v)
    return ticks


def _fmt_h(h):
    return ("%g" % round(h, 4)).replace(".", ",")


def svg_idf_chart(items):
    """items: list of (T, idf rows). Intensity vs duration (hours), both axes logarithmic."""
    W, H = 720, 430
    ml, mr, mt, mb = 66, 120, 34, 54
    pw, ph = W - ml - mr, H - mt - mb
    durs = sorted({r["hours"] for _, rows in items for r in rows})
    ints = [r["intensity"] for _, rows in items for r in rows]
    x0, x1 = math.log10(min(durs)), math.log10(max(durs))
    if x1 <= x0:
        x1 = x0 + 1
    ylo, yhi = min(ints) * 0.8, max(ints) * 1.25
    y0, y1 = math.log10(ylo), math.log10(yhi)
    xticks = [d for d in durs if any(abs(d - p) < 1e-9 for p in PREFERRED_TICKS)]
    if len(xticks) < 3:
        xticks = durs

    def X(d):
        return ml + (math.log10(d) - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (math.log10(v) - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<text x="%d" y="20" font-size="14" font-weight="600" fill="#222">Kurva intensitas-durasi (Mononobe)</text>' % ml]
    for v in _log_ticks(ylo, yhi):
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e3e3"/>' % (ml, Y(v), ml + pw, Y(v)))
        o.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#444">%s</text>' % (ml - 6, Y(v) + 4, _tick(v)))
    for d in xticks:
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e3e3e3"/>' % (X(d), mt, X(d), mt + ph))
        o.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#444">%s</text>' % (X(d), mt + ph + 15, _fmt_h(d)))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#888"/>' % (ml, mt, pw, ph))
    o.append('<text x="%.1f" y="%d" font-size="12" text-anchor="middle" fill="#222">Durasi (jam), skala logaritmik</text>' % (ml + pw / 2.0, H - 10))
    o.append('<text transform="rotate(-90 16 %.1f)" x="16" y="%.1f" font-size="12" text-anchor="middle" fill="#222">Intensitas (mm/jam), skala logaritmik</text>' % (mt + ph / 2.0, mt + ph / 2.0))
    ly = mt + 12
    for i, (T, rows) in enumerate(items):
        col = T_COLORS[i % len(T_COLORS)]
        pts = sorted((r["hours"], r["intensity"]) for r in rows)
        path = " ".join("%s%.1f,%.1f" % ("M" if k == 0 else "L", X(d), Y(v)) for k, (d, v) in enumerate(pts))
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (path, col))
        o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2.5"/>' % (ml + pw + 14, ly, ml + pw + 36, ly, col))
        o.append('<text x="%d" y="%d" font-size="12" fill="#222">T = %g th</text>' % (ml + pw + 42, ly + 4, T))
        ly += 20
    o.append("</svg>")
    return "".join(o)


def svg_hyetograph(title, rows):
    """Bars of rainfall per time step; x labels = end of the step in hours."""
    n = len(rows)
    W = max(380, min(620, 70 + 34 * n))
    H = 290
    ml, mr, mt, mb = 50, 12, 34, 50
    pw, ph = W - ml - mr, H - mt - mb
    top = max(r["rain"] for r in rows) * 1.12
    y0, y1, yt = _nice_axis(0.0, top, 5)

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<text x="%d" y="20" font-size="13" font-weight="600" fill="#222">%s</text>' % (ml, escape(title))]
    for v in yt:
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e3e3"/>' % (ml, Y(v), ml + pw, Y(v)))
        o.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#444">%s</text>' % (ml - 5, Y(v) + 4, _tick(v)))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#888"/>' % (ml, mt, pw, ph))
    slot = pw / float(n)
    bw = slot * 0.8
    for i, r in enumerate(rows):
        cx = ml + slot * (i + 0.5)
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#4a90c2"/>' % (cx - bw / 2, Y(r["rain"]), bw, Y(0) - Y(r["rain"])))
        o.append('<text x="%.1f" y="%d" font-size="10" text-anchor="middle" fill="#444">%s</text>' % (cx, mt + ph + 14, _fmt_h(r["t1"] / 60.0)))
    o.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#222">Waktu (jam, akhir langkah)</text>' % (ml + pw / 2.0, mt + ph + 32))
    o.append('<text transform="rotate(-90 13 %.1f)" x="13" y="%.1f" font-size="11" text-anchor="middle" fill="#222">mm per langkah</text>' % (mt + ph / 2.0, mt + ph / 2.0))
    o.append("</svg>")
    return "".join(o)


def build_report(stations, params):
    """stations: list of (name, [ {T, r24, idf, rows}, ... ]); params: dict of the settings used."""
    h = ["<!DOCTYPE html><html lang='id'><head><meta charset='utf-8'>",
         "<title>Hujan Rencana: Intensitas Mononobe dan Distribusi Hujan</title><style>%s</style></head><body>" % CSS,
         "<h1>Hujan Rencana: Intensitas Mononobe dan Distribusi Hujan</h1>",
         "<p class='note'>Durasi hujan: %g jam &middot; Interval distribusi: %d menit &middot; Penyusunan: %s &middot; Faktor R24: %g</p>" % (
             params["duration"], params["step"], escape(params["arrange"]), params["factor"])]
    for name, items in stations:
        h.append("<h2>%s</h2>" % escape(str(name)))
        h.append("<h3>Intensitas hujan (mm/jam) &ndash; rumus Mononobe</h3><table><tr><th rowspan='2'>Durasi (jam)</th><th rowspan='2'>Durasi (menit)</th>")
        h.append("".join("<th>T = %g th</th>" % it["T"] for it in items))
        h.append("</tr><tr>" + "".join("<th>R<sub>24</sub> = %s</th>" % _f(it["r24"], 1) for it in items) + "</tr>")
        for k, r0 in enumerate(items[0]["idf"]):
            h.append("<tr><td>%s</td><td>%g</td>" % (_fmt_h(r0["hours"]), r0["duration"]))
            for it in items:
                h.append("<td>%s</td>" % _f(it["idf"][k]["intensity"], 2))
            h.append("</tr>")
        h.append("</table><div class='figs'>%s</div>" % svg_idf_chart([(it["T"], it["idf"]) for it in items]))

        h.append("<h3>Distribusi hujan (per %d menit selama %g jam)</h3><table><tr><th rowspan='2'>Langkah</th><th rowspan='2'>Waktu (jam)</th>"
                 "<th rowspan='2'>Waktu (menit)</th><th rowspan='2'>Rasio</th><th rowspan='2'>Kumulatif</th>" % (params["step"], params["duration"]))
        h.append("".join("<th>T = %g th</th>" % it["T"] for it in items))
        h.append("</tr><tr>" + "".join("<th>mm</th>" for _ in items) + "</tr>")
        cum = 0.0
        for k, r0 in enumerate(items[0]["rows"]):
            cum += r0["ratio"]
            h.append("<tr><td>%d</td><td>%s &ndash; %s</td><td>%g &ndash; %g</td><td>%s</td><td>%s</td>" % (
                r0["step"], _fmt_h(r0["t0"] / 60.0), _fmt_h(r0["t1"] / 60.0), r0["t0"], r0["t1"], _f(r0["ratio"], 4), _f(cum, 4)))
            for it in items:
                h.append("<td>%s</td>" % _f(it["rows"][k]["rain"], 2))
            h.append("</tr>")
        h.append("<tr><th colspan='3'>Total</th><th>%s</th><th></th>" % _f(sum(r["ratio"] for r in items[0]["rows"]), 4))
        h.append("".join("<th>%s</th>" % _f(it["rows"][-1]["cum"], 2) for it in items) + "</tr></table>")
        h.append("<h3>Hyetograf</h3><div class='figs'>")
        for it in items:
            h.append(svg_hyetograph("T = %g tahun (R24 = %s mm)" % (it["T"], _f(it["r24"], 1)), it["rows"]))
        h.append("</div>")

    h.append("<h2>Catatan metode</h2><ul class='note'>"
             "<li>Intensitas Mononobe: I = (R<sub>24</sub>/24)&middot;(24/t)<sup>2/3</sup> (mm/jam, t dalam jam).</li>"
             "<li>Distribusi hujan: bila seluruh R<sub>24</sub> turun dalam T jam, kedalaman kumulatif hingga waktu t adalah "
             "R<sub>24</sub>&middot;(t/T)<sup>1/3</sup>; hujan tiap langkah adalah selisih kumulatifnya.</li>"
             "<li>Penyusunan 'Mononobe' menaruh hujan terbesar di awal; 'Alternating block' menaruh hujan terbesar di tengah, "
             "lalu bergantian sesudah dan sebelumnya.</li>"
             "<li>Hujan efektif belum dihitung (bergantung pada koefisien limpasan / tutupan lahan).</li></ul></body></html>")
    return "".join(h)
