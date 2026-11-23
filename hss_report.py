# -*- coding: utf-8 -*-
"""HTML report (Indonesian) with SVG charts for the rainfall frequency analysis. No QGIS dependency."""
import math
from html import escape

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;line-height:1.45;max-width:1000px}
h1{font-size:22px} h2{font-size:18px;margin-top:34px;border-bottom:2px solid #2e6b2e;padding-bottom:4px}
h3{font-size:15px;margin-top:22px}
table{border-collapse:collapse;margin:8px 0 14px 0;font-size:13px}
th,td{border:1px solid #bbb;padding:4px 9px;text-align:right} th{background:#e9f2e9} td:first-child,th:first-child{text-align:left}
.ok{color:#1b7a1b;font-weight:600} .no{color:#b3261e;font-weight:600} .note{font-size:12px;color:#555}
.figs{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start} svg{max-width:100%;height:auto;background:#fff;border:1px solid #ddd}
svg text{font-family:Segoe UI,Arial,sans-serif}
"""

PALETTE = {"Normal": "#1f77b4", "Log Normal": "#2ca02c", "Gumbel": "#ff7f0e", "Log Pearson III": "#d62728"}
FALLBACK = "#7f7f7f"


def _f(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, int):
        return str(v)
    return ("%." + str(nd) + "f") % v


def _res(ok):
    if ok is None:
        return "<span class='note'>tidak dapat dihitung</span>"
    return "<span class='ok'>Diterima</span>" if ok else "<span class='no'>Ditolak</span>"


def recommend(dists):
    good = [d for d in dists if d["ks"]["ok"] and d["chi2"]["ok"] in (True, None)]
    if not good:
        return None
    return min(good, key=lambda d: d["ks"]["d"])


# ----------------------------------------------------------------------------
# SVG helpers
# ----------------------------------------------------------------------------
def _nice_axis(lo, hi, n=6):
    """Rounded axis limits and ticks."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if (hi - lo) / step <= n:
            break
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    ticks, v = [], a
    while v <= b + step * 1e-6:
        ticks.append(v)
        v += step
    return a, b, ticks


def _tick(v):
    return ("%g" % round(v, 6))


def svg_frequency_chart(res, return_periods):
    """Observed values (Weibull plotting positions) and fitted frequency curves, T on a log axis."""
    data = sorted(res["data"])
    n = len(data)
    dists = res["dists"]
    t_min = 1.01
    t_max = max(max(return_periods), 10.0) * 1.15
    W, H = 780, 450
    ml, mr, mt, mb = 66, 175, 34, 56
    pw, ph = W - ml - mr, H - mt - mb
    lt0, lt1 = math.log10(t_min), math.log10(t_max)

    def X(T):
        return ml + (math.log10(T) - lt0) / (lt1 - lt0) * pw

    ks = [t_min * (t_max / t_min) ** (k / 79.0) for k in range(80)]
    curves = []
    for d in dists:
        pts = []
        for T in ks:
            try:
                pts.append((T, d["ppf"](1.0 - 1.0 / T)))
            except Exception:
                pass
        curves.append(pts)
    lo_c = [v for pts in curves for t, v in pts if t >= 2.0]
    hi_c = [v for pts in curves for t, v in pts]
    lo = min(data + (lo_c or data))
    hi = max(data + [v for v in hi_c if v == v])
    y0, y1, yt = _nice_axis(max(0.0, lo * 0.9) if min(data) >= 0 else lo, hi * 1.03)

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<defs><clipPath id="clip"><rect x="%d" y="%d" width="%d" height="%d"/></clipPath></defs>' % (ml, mt, pw, ph),
         '<text x="%d" y="20" font-size="14" font-weight="600" fill="#222">Kurva frekuensi curah hujan</text>' % ml]
    for v in yt:  # horizontal grid + labels
        y = Y(v)
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e3e3"/>' % (ml, y, ml + pw, y))
        o.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#444">%s</text>' % (ml - 6, y + 4, _tick(v)))
    tick_T = sorted(set(t for t in return_periods if t >= t_min and t <= t_max))
    for T in tick_T:  # vertical grid at the chosen return periods
        x = X(T)
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e3e3e3"/>' % (x, mt, x, mt + ph))
        o.append('<text x="%.1f" y="%d" font-size="11" text-anchor="middle" fill="#444">%s</text>' % (x, mt + ph + 16, _tick(T)))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#888"/>' % (ml, mt, pw, ph))
    o.append('<text x="%.1f" y="%d" font-size="12" text-anchor="middle" fill="#222">Kala ulang T (tahun), skala logaritmik</text>' % (ml + pw / 2.0, H - 12))
    o.append('<text transform="rotate(-90 16 %.1f)" x="16" y="%.1f" font-size="12" text-anchor="middle" fill="#222">Curah hujan (satuan data)</text>' % (mt + ph / 2.0, mt + ph / 2.0))

    o.append('<g clip-path="url(#clip)">')
    for d, pts in zip(dists, curves):
        col = PALETTE.get(d["name"], FALLBACK)
        path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(t), Y(v)) for i, (t, v) in enumerate(pts))
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (path, col))
    for i, v in enumerate(data):  # ascending: i = 0 is the smallest value
        T = (n + 1.0) / (n - i)
        if T >= t_min:
            o.append('<circle cx="%.1f" cy="%.1f" r="3.6" fill="#222" stroke="#fff" stroke-width="1"/>' % (X(T), Y(v)))
    o.append("</g>")

    lx, ly = ml + pw + 16, mt + 10
    for d in dists:
        col = PALETTE.get(d["name"], FALLBACK)
        o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2.5"/>' % (lx, ly, lx + 22, ly, col))
        o.append('<text x="%d" y="%d" font-size="12" fill="#222">%s</text>' % (lx + 28, ly + 4, escape(d["name"])))
        ly += 20
    o.append('<circle cx="%d" cy="%d" r="3.6" fill="#222"/>' % (lx + 11, ly))
    o.append('<text x="%d" y="%d" font-size="12" fill="#222">Data (Weibull)</text>' % (lx + 28, ly + 4))
    o.append("</svg>")
    return "".join(o)


def svg_bars(title, labels, values, crits, ylabel):
    """Bar chart with a critical-value marker per bar. Green = accepted, red = rejected."""
    W, H = 380, 300
    ml, mr, mt, mb = 52, 12, 34, 72
    pw, ph = W - ml - mr, H - mt - mb
    vals = [v for v in values if v is not None]
    cs = [c for c in crits if c is not None]
    top = max(vals + cs + [1e-9]) * 1.12
    y0, y1, yt = _nice_axis(0.0, top, 5)

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">' % (W, H, W, H),
         '<text x="%d" y="20" font-size="14" font-weight="600" fill="#222">%s</text>' % (ml, escape(title))]
    for v in yt:
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e3e3"/>' % (ml, Y(v), ml + pw, Y(v)))
        o.append('<text x="%d" y="%.1f" font-size="11" text-anchor="end" fill="#444">%s</text>' % (ml - 5, Y(v) + 4, _tick(v)))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#888"/>' % (ml, mt, pw, ph))
    k = len(labels)
    slot = pw / float(k)
    bw = slot * 0.5
    for i, (lb, v, c) in enumerate(zip(labels, values, crits)):
        cx = ml + slot * (i + 0.5)
        if v is not None:
            ok = c is not None and v < c
            col = "#2e8b57" if ok else ("#c0392b" if c is not None else "#999")
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity="0.85"/>' % (cx - bw / 2, Y(v), bw, Y(0) - Y(v), col))
            o.append('<text x="%.1f" y="%.1f" font-size="11" text-anchor="middle" fill="#222">%s</text>' % (cx, Y(v) - 4, _f(v, 3)))
        if c is not None:
            o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#111" stroke-width="2.5" stroke-dasharray="4 2"/>' % (cx - bw / 2 - 6, Y(c), cx + bw / 2 + 6, Y(c)))
        o.append('<text transform="rotate(-35 %.1f %d)" x="%.1f" y="%d" font-size="11" text-anchor="end" fill="#222">%s</text>' % (cx, mt + ph + 14, cx + 4, mt + ph + 14, escape(lb)))
    o.append('<text transform="rotate(-90 14 %.1f)" x="14" y="%.1f" font-size="11" text-anchor="middle" fill="#222">%s</text>' % (mt + ph / 2.0, mt + ph / 2.0, escape(ylabel)))
    o.append('<text x="%d" y="%d" font-size="11" fill="#444">garis putus-putus = nilai kritis; hijau = diterima, merah = ditolak</text>' % (ml, H - 6))
    o.append("</svg>")
    return "".join(o)


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def _theory(name, cv):
    """Theoretical (Cs, Ck) used to screen the distribution type; None = not applicable."""
    if name == "Normal":
        return 0.0, 3.0
    if name == "Gumbel":
        return 1.14, 5.4
    if name == "Log Normal" and cv is not None:
        return cv ** 3 + 3 * cv, cv ** 8 + 6 * cv ** 6 + 15 * cv ** 4 + 16 * cv ** 2 + 3
    return None


def build_report(groups, params):
    """groups: list of (group_name, analysis_result). params: dict of the settings used."""
    h = ["<!DOCTYPE html><html lang='id'><head><meta charset='utf-8'>",
         "<title>Analisis Frekuensi Curah Hujan</title><style>%s</style></head><body>" % CSS,
         "<h1>Analisis Frekuensi Curah Hujan</h1>",
         "<p class='note'>Tingkat signifikansi &alpha; = %s &middot; Gumbel: %s &middot; Posisi plotting uji "
         "Smirnov-Kolmogorov: %s &middot; Kelas Chi-Square: %s</p>" % (
             escape(str(params["alpha"])), escape(params["gumbel"]), escape(params["ks_pos"]), escape(params["classes"]))]
    for name, res in groups:
        st = res["stats"]
        dists = res["dists"]
        periods = [q["T"] for q in dists[0]["quantiles"]]
        h.append("<h2>%s</h2>" % escape(str(name)))
        h.append("<h3>Statistik dasar</h3><table><tr><th>Parameter</th><th>Data asli</th><th>Data log<sub>10</sub></th></tr>")
        rows = [("Jumlah data (n)", st["n"], st["n"]), ("Rata-rata", st["mean"], st["log_mean"]),
                ("Standar deviasi (S)", st["std"], st["log_std"]), ("Koefisien variasi (Cv)", st["cv"],
                 (st["log_std"] / st["log_mean"]) if st["log_mean"] else None),
                ("Koefisien skewness (Cs)", st["cs"], st["log_cs"]), ("Koefisien kurtosis (Ck)", st["ck"], st["log_ck"]),
                ("Minimum", st["min"], None), ("Maksimum", st["max"], None)]
        for label, a, b in rows:
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (label, _f(a), _f(b)))
        h.append("</table>")

        h.append("<h3>Pemeriksaan parameter statistik (penyaringan awal jenis distribusi)</h3>"
                 "<table><tr><th>Distribusi</th><th>Syarat teoritis</th><th>Cs data</th><th>Cs teoritis</th>"
                 "<th>&Delta;Cs</th><th>Ck data</th><th>Ck teoritis</th><th>&Delta;Ck</th></tr>")
        for d in dists:
            th = _theory(d["name"], st["cv"])
            if d["name"] == "Log Pearson III":
                h.append("<tr><td>%s</td><td style='text-align:left'>tidak ada batasan (dipakai bila syarat lain tidak terpenuhi)</td>"
                         "<td colspan='6' style='text-align:left'>Cs log<sub>10</sub> = %s</td></tr>" % (escape(d["name"]), _f(st["log_cs"])))
                continue
            cond = {"Normal": "Cs &asymp; 0 dan Ck &asymp; 3", "Gumbel": "Cs &asymp; 1,14 dan Ck &asymp; 5,4",
                    "Log Normal": "Cs = Cv<sup>3</sup> + 3Cv; Ck = Cv<sup>8</sup> + 6Cv<sup>6</sup> + 15Cv<sup>4</sup> + 16Cv<sup>2</sup> + 3"}[d["name"]]
            if th is None:
                continue
            h.append("<tr><td>%s</td><td style='text-align:left'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(d["name"]), cond, _f(st["cs"]), _f(th[0]), _f(abs(st["cs"] - th[0])),
                _f(st["ck"]), _f(th[1]), _f(abs(st["ck"] - th[1]))))
        h.append("</table><p class='note'>Makin kecil selisih (&Delta;), makin sesuai sifat statistik data dengan distribusi tersebut. "
                 "Tidak ada batas selisih baku; gunakan sebagai pertimbangan, bukan penentu tunggal.</p>")

        h.append("<h3>Curah hujan rencana X<sub>T</sub></h3><table><tr><th>Kala ulang (tahun)</th>")
        for d in dists:
            h.append("<th>%s</th>" % escape(d["name"]))
        h.append("</tr>")
        for i, q in enumerate(dists[0]["quantiles"]):
            h.append("<tr><td>%g</td>" % q["T"])
            for d in dists:
                h.append("<td>%s</td>" % _f(d["quantiles"][i]["x"], 2))
            h.append("</tr>")
        h.append("</table>")

        h.append("<h3>Faktor frekuensi K<sub>T</sub></h3><table><tr><th>Kala ulang (tahun)</th>")
        for d in dists:
            h.append("<th>%s</th>" % escape(d["name"]))
        h.append("</tr>")
        for i, q in enumerate(dists[0]["quantiles"]):
            h.append("<tr><td>%g</td>" % q["T"])
            for d in dists:
                h.append("<td>%s</td>" % _f(d["quantiles"][i]["k"], 4))
            h.append("</tr>")
        h.append("</table><p class='note'>Distribusi Log Normal dan Log Pearson III memakai K<sub>T</sub> pada data log<sub>10</sub>.</p>")

        h.append("<h3>Grafik</h3><div class='figs'>")
        h.append(svg_frequency_chart(res, periods))
        names = [d["name"] for d in dists]
        h.append(svg_bars("Uji Smirnov-Kolmogorov (\u0394max)", names, [d["ks"]["d"] for d in dists],
                          [d["ks"]["crit"] for d in dists], "\u0394max"))
        h.append(svg_bars("Uji Chi-Square (\u03c7\u00b2)", names, [d["chi2"]["chi2"] for d in dists],
                          [d["chi2"]["crit"] for d in dists], "\u03c7\u00b2 hitung"))
        h.append("</div><p class='note'>Pada kurva frekuensi, distribusi yang baik adalah yang garisnya paling dekat dengan titik data, "
                 "terutama di sisi kala ulang besar (ekor atas) yang menentukan desain.</p>")

        h.append("<h3>Uji kecocokan distribusi</h3><table><tr><th>Distribusi</th>"
                 "<th>&Delta;max</th><th>&Delta;kritis</th><th>Smirnov-Kolmogorov</th>"
                 "<th>&chi;<sup>2</sup> hitung</th><th>&chi;<sup>2</sup> kritis</th><th>dk</th><th>Chi-Square</th><th>RMSE</th></tr>")
        for d in dists:
            ks, c2 = d["ks"], d["chi2"]
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(d["name"]), _f(ks["d"], 4), _f(ks["crit"], 4), _res(ks["ok"]),
                _f(c2["chi2"], 3), _f(c2["crit"], 3), c2["dof"], _res(c2["ok"]), _f(d["rmse"], 3)))
        h.append("</table>")
        k_used = dists[0]["chi2"]["k"]
        h.append("<p class='note'>Chi-Square: kelas berpeluang sama (jumlah kelas awal K = %d; dk = K &minus; 1 &minus; jumlah parameter). "
                 "Hasil Chi-Square kurang andal bila frekuensi harapan per kelas &lt; 5. "
                 "RMSE = akar rata-rata kuadrat selisih data terhadap nilai distribusi pada posisi Weibull (satuan data; makin kecil makin dekat).</p>" % k_used)

        best = recommend(dists)
        h.append("<h3>Ringkasan pemilihan distribusi</h3>")
        if best:
            ok_names = [d["name"] for d in dists if d["ks"]["ok"] and d["chi2"]["ok"] in (True, None)]
            h.append("<p><b>Distribusi yang lolos kedua uji:</b> %s. Selisih terkecil (&Delta;max) pada <b>%s</b>. "
                     "Pemilihan akhir tetap perlu mempertimbangkan sifat statistik data (Cs, Ck, Cv), kedekatan pada kurva frekuensi, "
                     "kewajaran hasil, dan ketentuan pedoman yang dipakai.</p>" % (escape(", ".join(ok_names)), escape(best["name"])))
            xs = {d["name"]: [q["x"] for q in d["quantiles"]] for d in dists if d["name"] in ok_names}
            if len(xs) > 1:
                spread = max((max(v[i] for v in xs.values()) - min(v[i] for v in xs.values())) / (sum(v[i] for v in xs.values()) / len(xs))
                             for i in range(len(periods)))
                h.append("<p class='note'>Selisih X<sub>T</sub> antar distribusi yang lolos paling besar %.1f%% dari rata-ratanya. "
                         "Bila selisih kecil, pilihan distribusi tidak terlalu berpengaruh pada hasil.</p>" % (spread * 100.0))
        else:
            h.append("<p><b>Tidak ada distribusi yang lolos kedua uji</b> pada &alpha; yang dipilih. Periksa data (pencilan, "
                     "panjang data, konsistensi), atau pertimbangkan hasil uji yang lolos salah satunya dengan hati-hati.</p>")

    h.append("<h2>Catatan metode</h2><ul class='note'>"
             "<li>Parameter diestimasi dengan metode momen. Standar deviasi memakai pembagi n&minus;1.</li>"
             "<li>Cs = n&Sigma;(x&minus;&xmacr;)<sup>3</sup>/((n&minus;1)(n&minus;2)S<sup>3</sup>); "
             "Ck = n<sup>2</sup>&Sigma;(x&minus;&xmacr;)<sup>4</sup>/((n&minus;1)(n&minus;2)(n&minus;3)S<sup>4</sup>) (normal = 3).</li>"
             "<li>X<sub>T</sub> = &xmacr; + K<sub>T</sub>&middot;S (Normal, Gumbel); log X<sub>T</sub> = log&xmacr; + K<sub>T</sub>&middot;S<sub>log</sub> (Log Normal, Log Pearson III).</li>"
             "<li>Log Pearson III: K<sub>T</sub> dihitung eksak dari distribusi Pearson III (bukan interpolasi tabel).</li>"
             "<li>Uji Smirnov-Kolmogorov: nilai kritis dihitung dengan koreksi Stephens (sesuai tabel standar); "
             "karena parameter diestimasi dari data yang sama, uji ini cenderung konservatif.</li>"
             "<li>Titik data pada grafik memakai posisi plotting Weibull T = (n+1)/m.</li>"
             "<li>Satuan hasil mengikuti satuan data input.</li></ul></body></html>")
    return "".join(h)
