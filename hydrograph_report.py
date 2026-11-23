# -*- coding: utf-8 -*-
"""HTML report (Indonesian) with SVG charts for the flood hydrograph tool. No QGIS dependency."""
import math
from html import escape

from .design_storm_report import T_COLORS, _fmt_h
from .hss_report import CSS, _f, _nice_axis, _tick

SEG_COLORS = {"QA": "#1f77b4", "Qd1": "#2ca02c", "Qd2": "#ff7f0e", "Qd3": "#d62728", "Q": "#1f77b4"}
METHOD_TITLE = {"nakayasu": "HSS Nakayasu", "snyder": "HSS Snyder-Alexeyev"}


def _axes(o, ml, mt, pw, ph, xt, X, yt, Y, xlabel, ylabel):
    for v in yt:
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e3e3"/>' % (ml, Y(v), ml + pw, Y(v)))
        o.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#444">%s</text>' % (ml - 6, Y(v) + 4, _tick(v)))
    for v in xt:
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e3e3e3"/>' % (X(v), mt, X(v), mt + ph))
        o.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#444">%s</text>' % (X(v), mt + ph + 15, _tick(v)))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#888"/>' % (ml, mt, pw, ph))
    o.append('<text x="%.1f" y="%d" font-size="12" text-anchor="middle" fill="#222">%s</text>' % (ml + pw / 2.0, mt + ph + 36, escape(xlabel)))
    o.append('<text transform="rotate(-90 15 %.1f)" x="15" y="%.1f" font-size="12" text-anchor="middle" fill="#222">%s</text>' % (mt + ph / 2.0, mt + ph / 2.0, escape(ylabel)))


def svg_uh_chart(title, method, fine, rows):
    """Unit hydrograph curve; Nakayasu is coloured by segment (QA, Qd1, Qd2, Qd3). Circles = ordinates."""
    W, H = 740, 400
    ml, mr, mt, mb = 66, 110, 34, 56
    pw, ph = W - ml - mr, H - mt - mb
    tmax = max(t for t, _, _ in fine)
    qmax = max(max(q for _, _, q in fine), max(r["q"] for r in rows))
    x0, x1, xt = _nice_axis(0.0, tmax, 8)
    y0, y1, yt = _nice_axis(0.0, qmax * 1.05, 6)

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<text x="%d" y="20" font-size="14" font-weight="600" fill="#222">%s</text>' % (ml, escape(title))]
    _axes(o, ml, mt, pw, ph, xt, X, yt, Y, "Waktu (jam)", "Q (m\u00b3/s per mm hujan efektif)")
    runs, cur = [], []
    for t, seg, q in fine:
        if cur and cur[-1][1] != seg:
            runs.append((cur[-1][1], cur + [(t, seg, q)]))   # overlap one point so the colours join
            cur = []
        cur.append((t, seg, q))
    if cur:
        runs.append((cur[-1][1], cur))
    for seg, pts in runs:
        path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(t), Y(q)) for i, (t, _, q) in enumerate(pts))
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.4"/>' % (path, SEG_COLORS.get(seg, "#1f77b4")))
    for r in rows:
        o.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#fff" stroke="#222" stroke-width="1"/>' % (X(r["t"]), Y(r["q"])))
    ly = mt + 12
    names = ["QA", "Qd1", "Qd2", "Qd3"] if method == "nakayasu" else ["Q"]
    for nm in names:
        o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="3"/>' % (ml + pw + 14, ly, ml + pw + 36, ly, SEG_COLORS[nm]))
        o.append('<text x="%d" y="%d" font-size="12" fill="#222">%s</text>' % (ml + pw + 42, ly + 4, nm))
        ly += 20
    o.append('<circle cx="%d" cy="%d" r="3" fill="#fff" stroke="#222"/>' % (ml + pw + 25, ly))
    o.append('<text x="%d" y="%d" font-size="12" fill="#222">ordinat</text>' % (ml + pw + 42, ly + 4))
    o.append("</svg>")
    return "".join(o)


def svg_flood_chart(title, floods):
    """Flood hydrographs, one line per return period."""
    W, H = 740, 400
    ml, mr, mt, mb = 66, 110, 34, 56
    pw, ph = W - ml - mr, H - mt - mb
    tmax = max(max(f["t"]) for f in floods)
    qmax = max(max(f["q"]) for f in floods)
    x0, x1, xt = _nice_axis(0.0, tmax, 8)
    y0, y1, yt = _nice_axis(0.0, qmax * 1.05, 6)

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<text x="%d" y="20" font-size="14" font-weight="600" fill="#222">%s</text>' % (ml, escape(title))]
    _axes(o, ml, mt, pw, ph, xt, X, yt, Y, "Waktu (jam)", "Debit (m\u00b3/s)")
    ly = mt + 12
    for i, f in enumerate(floods):
        col = T_COLORS[i % len(T_COLORS)]
        path = " ".join("%s%.1f,%.1f" % ("M" if k == 0 else "L", X(t), Y(q)) for k, (t, q) in enumerate(zip(f["t"], f["q"])))
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.2"/>' % (path, col))
        o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="3"/>' % (ml + pw + 14, ly, ml + pw + 36, ly, col))
        o.append('<text x="%d" y="%d" font-size="12" fill="#222">T = %g th</text>' % (ml + pw + 42, ly + 4, f["T"]))
        ly += 20
    o.append("</svg>")
    return "".join(o)


def _param_rows(p):
    if p["method"] == "nakayasu":
        return [("Luas DAS, A (km<sup>2</sup>)", p["area"]), ("Panjang sungai, L (km)", p["length"]),
                ("Waktu kelambatan, tg (jam)", p["lag"]), ("Durasi hujan satuan, tr = %g tg (jam)" % p["tr_factor"], p["tr"]),
                ("Waktu puncak, Tp = tg + 0,8 tr (jam)", p["tpeak"]), ("T<sub>0,3</sub> = &alpha; tg, &alpha; = %g (jam)" % p["alpha"], p["t03"]),
                ("Debit puncak hidrograf satuan, Qp (m<sup>3</sup>/s per mm)", p["qp"])]
    return [("Luas DAS, A (km<sup>2</sup>)", p["area"]), ("Panjang sungai, L (km)", p["length"]), ("Lc (km)", p["lc"]),
            ("Waktu kelambatan, tp = Ct (L&middot;Lc)<sup>0,3</sup>, Ct = %g (jam)" % p["ct"], p["lag"]),
            ("Durasi hujan satuan, tr = tp/5,5 (jam)", p["tr"]), ("Waktu puncak, Tp = tp + 0,5 tr (jam)", p["tpeak"]),
            ("Qp = 0,278 Cp A/tp, Cp = %g (m<sup>3</sup>/s per mm)" % p["cp"], p["qp"]), ("&lambda; = 3,6 Qp Tp/A", p["lambda"]), ("a (Alexeyev)", p["a"])]


def build_report(catchments, settings):
    """catchments: list of dict(name, area, length, lc_note, methods=[dict(key, params, scale, rows, fine, floods)])."""
    h = ["<!DOCTYPE html><html lang='id'><head><meta charset='utf-8'>",
         "<title>Hidrograf Banjir Rencana</title><style>%s</style></head><body>" % CSS,
         "<h1>Hidrograf Banjir Rencana (Hidrograf Satuan Sintetik)</h1>",
         "<p class='note'>Interval waktu: %s jam &middot; Hidrograf satuan diskalakan tepat 1 mm: %s &middot; Konversi S-curve: %s &middot; Aliran dasar: %g m<sup>3</sup>/s</p>" % (
             escape(settings["dt_text"]), "ya" if settings["normalize"] else "tidak", "ya" if settings["scurve"] else "tidak", settings["baseflow"])]
    for c in catchments:
        h.append("<h2>DAS %s</h2><p class='note'>%s</p>" % (escape(c["name"]), escape(c["lc_note"])))
        for m in c["methods"]:
            p = m["params"]
            title = METHOD_TITLE[m["key"]]
            h.append("<h3>%s</h3><table><tr><th>Parameter</th><th>Nilai</th></tr>" % title)
            for lab, v in _param_rows(p):
                h.append("<tr><td>%s</td><td>%s</td></tr>" % (lab, _f(v, 4)))
            h.append("<tr><td>Faktor skala ke 1 mm</td><td>%s</td></tr></table>" % _f(m["scale"], 4))
            h.append("<div class='figs'>%s</div>" % svg_uh_chart("Hidrograf satuan sintetik - %s" % title, m["key"], m["fine"], m["rows"]))
            nak = m["key"] == "nakayasu"
            h.append("<h3>Ordinat hidrograf satuan - %s</h3><table><tr><th>t (jam)</th>%s<th>Q (m<sup>3</sup>/s)</th></tr>" % (
                title, "<th>QA</th><th>Qd1</th><th>Qd2</th><th>Qd3</th>" if nak else ""))
            for r in m["rows"]:
                cells = ""
                if nak:
                    for nm in ("QA", "Qd1", "Qd2", "Qd3"):
                        cells += "<td>%s</td>" % (_f(r["curve"], 4) if r["seg"] == nm and r["t"] > 0 else "")
                h.append("<tr><td>%s</td>%s<td>%s</td></tr>" % (_fmt_h(r["t"]), cells, _f(r["q"], 4)))
            h.append("</table>")
            for station, floods in m["floods"].items():
                h.append("<h3>Hidrograf banjir - %s (stasiun %s)</h3><table><tr><th>Kala ulang (tahun)</th><th>Hujan efektif (mm)</th>"
                         "<th>Q puncak (m<sup>3</sup>/s)</th><th>Waktu puncak (jam)</th><th>Volume hidrograf (mm)</th></tr>" % (title, escape(str(station))))
                for f in floods:
                    h.append("<tr><td>%g</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        f["T"], _f(f["pe_total"], 2), _f(f["peak"], 3), _fmt_h(f["t_peak"]), _f(f["volume_mm"], 2)))
                h.append("</table><div class='figs'>%s</div>" % svg_flood_chart(
                    "Hidrograf banjir - %s (DAS %s, stasiun %s)" % (title, c["name"], station), floods))
    h.append("<h2>Catatan metode</h2><ul class='note'>"
             "<li>Hidrograf banjir dihitung dengan konvolusi: Q<sub>m</sub> = &Sigma; Pe<sub>j</sub> &middot; U<sub>m&minus;j+1</sub> (hujan efektif tiap interval dianggap bekerja di awal intervalnya), "
             "ditambah aliran dasar bila ada.</li>"
             "<li>Nakayasu: Qp = A &middot; 1 mm/(3,6 (0,3 Tp + T<sub>0,3</sub>)); kurva naik QA = Qp (t/Tp)<sup>2,4</sup>; kurva turun Qd1, Qd2, Qd3 berturut-turut "
             "0,3<sup>(t&minus;Tp)/T0,3</sup>, 0,3<sup>(t&minus;Tp+0,5T0,3)/(1,5T0,3)</sup>, 0,3<sup>(t&minus;Tp+1,5T0,3)/(2T0,3)</sup> dikali Qp.</li>"
             "<li>Snyder-Alexeyev: Q = Qp &middot; 10<sup>&minus;a(1&minus;X)<sup>2</sup>/X</sup>, X = t/Tp, a = 1,32&lambda;<sup>2</sup> + 0,15&lambda; + 0,045.</li>"
             "<li>Ordinat memakai langkah waktu yang sama dengan hujan efektif. Tanpa konversi S-curve, ordinat kurva diambil langsung pada langkah itu "
             "(praktik umum di spreadsheet); dengan S-curve, hidrograf satuan dikonversi ke durasi hujan sebesar langkah waktu.</li>"
             "<li>Nilai tr, &alpha; (Nakayasu), Ct dan Cp (Snyder) adalah parameter yang perlu disesuaikan dengan DAS dan pedoman yang dipakai.</li></ul></body></html>")
    return "".join(h)
