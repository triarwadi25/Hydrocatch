# -*- coding: utf-8 -*-
import re

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import catch_input as ci
from . import common, hydrograph as hg, hydrograph_report as rep
from .common import T_DBL, T_INT, T_STR
from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, WKB_NONE, RainfallFrequency

try:
    SRC_POLY = QgsProcessing.SourceType.VectorPolygon
except AttributeError:
    SRC_POLY = QgsProcessing.TypeVectorPolygon

METHOD_NAMES = {"nakayasu": "Nakayasu", "snyder": "Snyder-Alexeyev"}


class FloodHydrograph(QgsProcessingAlgorithm):
    """Flood hydrographs from effective rainfall and a synthetic unit hydrograph (Nakayasu, Snyder-Alexeyev)."""

    EFFECTIVE = "EFFECTIVE"
    CATCHMENTS = "CATCHMENTS"
    NAME_FIELD = "NAME_FIELD"
    METHOD = "METHOD"
    LENGTH_SOURCE = "LENGTH_SOURCE"
    L_FIELD = "L_FIELD"
    L_VALUE = "L_VALUE"
    L_PAIRS = "L_PAIRS"
    LC_FIELD = "LC_FIELD"
    LC_VALUE = "LC_VALUE"
    LC_RATIO = "LC_RATIO"
    TR_FACTOR = "TR_FACTOR"
    ALPHA = "ALPHA"
    CT = "CT"
    CP = "CP"
    BASEFLOW = "BASEFLOW"
    NORMALIZE = "NORMALIZE"
    SCURVE = "SCURVE"
    STATION = "STATION"
    OUT_UH = "OUT_UH"
    OUT_FLOOD = "OUT_FLOOD"
    OUT_PEAK = "OUT_PEAK"
    REPORT = "REPORT"

    METHOD_OPTS = ["Nakayasu", "Snyder-Alexeyev", "Both"]
    LENGTH_OPTS = ["Field of the catchment layer (e.g. lfp_km from Catchment morphometry)",
                   "Value typed in this tool (not added to the catchment layer)"]

    def tr(self, s):
        return QCoreApplication.translate("FloodHydrograph", s)

    def createInstance(self):
        return FloodHydrograph()

    def name(self):
        return "floodhydrograph"

    def displayName(self):
        return self.tr("Flood hydrograph (Nakayasu, Snyder-Alexeyev)")

    def group(self):
        return self.tr("Discharge Analysis")

    def groupId(self):
        return "discharge"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Synthetic unit hydrograph and flood hydrograph for each catchment and return period.\n\n"
            "Inputs: the 'Effective rainfall' table (output of that tool) and the catchment polygons (the same layer, "
            "so the catchment names match). The time step of the unit hydrograph is the time step of the effective rainfall.\n\n"
            "River length L: either a field of the catchment layer (default lfp_km from 'Catchment morphometry') or a value "
            "you type in this tool, which is NOT added to the layer (one value for all catchments, or 'name=value' pairs "
            "in the advanced box). Lc (Snyder only): a field, a typed value, or Lc = ratio x L (default 0.5).\n\n"
            "Nakayasu: tg = 0.21 L^0.7 (L < 15 km) or 0.4 + 0.058 L; tr = factor x tg; Tp = tg + 0.8 tr; T0.3 = alpha x tg; "
            "curves QA, Qd1, Qd2, Qd3. Snyder-Alexeyev: tp = Ct (L Lc)^0.3; tr = tp/5.5; Tp = tp + 0.5 tr; "
            "Qp = 0.278 Cp A/tp; Alexeyev curve.\n\n"
            "Outputs: unit hydrograph ordinates (with QA, Qd1, Qd2, Qd3 for Nakayasu), flood hydrograph ordinates per "
            "return period, a peak discharge summary and an HTML report with charts.\n\n"
            "'Scale to exactly 1 mm' (default on) corrects the small volume error of the empirical formulas. "
            "Ct, Cp, alpha and tr are calibration coefficients: choose them for your region and guideline."
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        add(QgsProcessingParameterFeatureSource(self.EFFECTIVE, self.tr("Effective rainfall table (output of Effective rainfall)"), [SRC_TABLE]))
        add(QgsProcessingParameterFeatureSource(self.CATCHMENTS, self.tr("Catchment polygons"), [SRC_POLY]))
        add(QgsProcessingParameterField(self.NAME_FIELD, self.tr("Catchment name field (detected automatically)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterEnum(self.METHOD, self.tr("Synthetic unit hydrograph method"), options=self.METHOD_OPTS, defaultValue=2))
        add(QgsProcessingParameterEnum(self.LENGTH_SOURCE, self.tr("River length L from"), options=self.LENGTH_OPTS, defaultValue=0))
        add(QgsProcessingParameterField(self.L_FIELD, self.tr("River length field in km (default lfp_km)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterNumber(self.L_VALUE, self.tr("River length L typed here (km, used when 'Value typed in this tool')"), type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0))
        add(QgsProcessingParameterNumber(self.LC_VALUE, self.tr("Lc typed here (km, 0 = Lc ratio x L)"), type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0))
        add(QgsProcessingParameterNumber(self.TR_FACTOR, self.tr("Nakayasu: tr / tg (0.5 - 1.0)"), type=NUM_DOUBLE, defaultValue=0.75, minValue=0.3, maxValue=1.5))
        add(QgsProcessingParameterNumber(self.ALPHA, self.tr("Nakayasu: alpha (T0.3 = alpha x tg)"), type=NUM_DOUBLE, defaultValue=2.0, minValue=0.5, maxValue=6.0))
        add(QgsProcessingParameterNumber(self.CT, self.tr("Snyder: Ct"), type=NUM_DOUBLE, defaultValue=1.5, minValue=0.1, maxValue=10.0))
        add(QgsProcessingParameterNumber(self.CP, self.tr("Snyder: Cp"), type=NUM_DOUBLE, defaultValue=0.6, minValue=0.1, maxValue=1.5))
        add(QgsProcessingParameterNumber(self.BASEFLOW, self.tr("Base flow (m3/s, added to every flood hydrograph)"), type=NUM_DOUBLE, defaultValue=0.0, minValue=0.0))
        add(QgsProcessingParameterBoolean(self.NORMALIZE, self.tr("Scale the unit hydrograph to exactly 1 mm of runoff"), defaultValue=True))
        add(ci.advanced(QgsProcessingParameterString(self.L_PAIRS, self.tr("River length per catchment as 'name=km' pairs (typed mode, overrides the single value)"), multiLine=True, optional=True)))
        add(ci.advanced(QgsProcessingParameterField(self.LC_FIELD, self.tr("Lc field in km (default lc_km)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True)))
        add(ci.advanced(QgsProcessingParameterNumber(self.LC_RATIO, self.tr("Lc / L ratio when Lc is not given"), type=NUM_DOUBLE, defaultValue=0.5, minValue=0.1, maxValue=1.0)))
        add(ci.advanced(QgsProcessingParameterBoolean(self.SCURVE, self.tr("Convert the unit hydrograph to the rain time step with the S-curve method"), defaultValue=False)))
        add(ci.advanced(QgsProcessingParameterString(self.STATION, self.tr("Only this station (empty = all stations in the table)"), optional=True)))
        add(QgsProcessingParameterFeatureSink(self.OUT_UH, self.tr("Unit hydrograph ordinates"), type=SRC_TABLE))
        add(QgsProcessingParameterFeatureSink(self.OUT_FLOOD, self.tr("Flood hydrographs"), type=SRC_TABLE))
        add(QgsProcessingParameterFeatureSink(self.OUT_PEAK, self.tr("Peak discharge summary"), type=SRC_TABLE))
        add(QgsProcessingParameterFileDestination(self.REPORT, self.tr("HTML report with charts (optional)"), fileFilter="HTML files (*.html)", optional=True, createByDefault=True))

    # ------------------------------------------------------------------
    def _read_effective(self, source, only, feedback):
        hn = source.fields().names()
        f = {"catchment": ci.pick(hn, ("catchment",)), "T": ci.pick(hn, ("return_period",)), "step": ci.pick(hn, ("step",)),
             "t0": ci.pick(hn, ("t_start_min",)), "t1": ci.pick(hn, ("t_end_min",)), "eff": ci.pick(hn, ("eff_mm",)),
             "station": ci.pick(hn, ("station",))}
        miss = [k for k in ("catchment", "T", "step", "t0", "t1", "eff") if not f[k]]
        if miss:
            raise QgsProcessingException(self.tr("The effective rainfall table lacks the fields for: %s. Use the output of 'Effective rainfall'.") % ", ".join(miss))
        to_f = RainfallFrequency._to_float
        series = {}
        for feat in source.getFeatures():
            st = str(feat[f["station"]]) if f["station"] else "all data"
            if only and st != only:
                continue
            vals = [to_f(feat[f[k]]) for k in ("T", "step", "t0", "t1", "eff")]
            if None in vals:
                continue
            T, k, t0, t1, eff = vals
            series.setdefault((st, str(feat[f["catchment"]]), T), []).append((int(k), t0, t1, eff))
        if not series:
            raise QgsProcessingException(self.tr("No effective rainfall rows found."))
        out = {}
        for key, rows in series.items():
            rows.sort()
            dts = {round(r[2] - r[1], 6) for r in rows}
            if len(dts) != 1 or min(dts) <= 0:
                raise QgsProcessingException(self.tr("The time step of %s / T=%g is not constant.") % (key[1], key[2]))
            out[key] = (dts.pop() / 60.0, [r[3] for r in rows])
        return out

    @staticmethod
    def _pairs(text):
        out = {}
        for name, val in re.findall(r"([^=,;\n]+?)\s*=\s*([\d.]+)", text or ""):
            out[name.strip().lower()] = float(val)
        return out

    def processAlgorithm(self, parameters, context, feedback):
        eff = self.parameterAsSource(parameters, self.EFFECTIVE, context)
        catch = self.parameterAsSource(parameters, self.CATCHMENTS, context)
        if eff is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.EFFECTIVE))
        if catch is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.CATCHMENTS))
        m_idx = self.parameterAsEnum(parameters, self.METHOD, context)
        methods = [hg.NAKAYASU] if m_idx == 0 else ([hg.SNYDER] if m_idx == 1 else [hg.NAKAYASU, hg.SNYDER])
        typed = self.parameterAsEnum(parameters, self.LENGTH_SOURCE, context) == 1
        l_value = self.parameterAsDouble(parameters, self.L_VALUE, context)
        lc_value = self.parameterAsDouble(parameters, self.LC_VALUE, context)
        lc_ratio = self.parameterAsDouble(parameters, self.LC_RATIO, context)
        pairs = self._pairs(self.parameterAsString(parameters, self.L_PAIRS, context))
        tr_f = self.parameterAsDouble(parameters, self.TR_FACTOR, context)
        alpha = self.parameterAsDouble(parameters, self.ALPHA, context)
        ct = self.parameterAsDouble(parameters, self.CT, context)
        cp = self.parameterAsDouble(parameters, self.CP, context)
        base = self.parameterAsDouble(parameters, self.BASEFLOW, context)
        normalize = self.parameterAsBoolean(parameters, self.NORMALIZE, context)
        scurve = self.parameterAsBoolean(parameters, self.SCURVE, context)
        only = (self.parameterAsString(parameters, self.STATION, context) or "").strip()

        series = self._read_effective(eff, only, feedback)
        cn = catch.fields().names()
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context) or ci.pick(cn, ci.NAME_CANDIDATES)
        l_field = self.parameterAsString(parameters, self.L_FIELD, context) or ci.pick(cn, ("lfp_km",))
        lc_field = self.parameterAsString(parameters, self.LC_FIELD, context) or ci.pick(cn, ("lc_km",))
        area_field = ci.pick(cn, ("area_km2", "area", "luas_km2"))
        if typed:
            if l_value <= 0 and not pairs:
                raise QgsProcessingException(self.tr("Type the river length L (km) or choose the field option."))
            feedback.pushInfo(self.tr("River length typed in the tool (nothing is added to the catchment layer)."))
        else:
            if not l_field:
                raise QgsProcessingException(self.tr("No river length field found (lfp_km). Run 'Catchment morphometry' first, choose the field, or type the length in this tool."))
            feedback.pushInfo(self.tr("River length field: '%s'.") % l_field)
        feedback.pushInfo(self.tr("Catchment name field: '%s'; area field: '%s'.") % (name_field or "-", area_field or "geometry"))

        da = common.distance_area(context, catch.sourceCrs())
        names_in_rain = {k[1] for k in series}
        results_uh, results_flood, results_peak, report = [], [], [], []
        matched = 0
        total = catch.featureCount() or 1
        for n, feat in enumerate(catch.getFeatures()):
            if feedback.isCanceled():
                return {}
            feedback.setProgress(80.0 * n / total)
            cname = ci.label(feat, name_field)
            if cname not in names_in_rain:
                feedback.pushWarning(self.tr("Catchment '%s' has no rows in the effective rainfall table: skipped.") % cname)
                continue
            area = ci.number(feat, area_field)
            if not area or area <= 0:
                geom = feat.geometry()
                area = da.convertAreaMeasurement(da.measureArea(geom), common.KM2) if geom is not None and not geom.isNull() else None
            if not area or area <= 0:
                feedback.pushWarning(self.tr("Catchment '%s' has no valid area: skipped.") % cname)
                continue
            if typed:
                length = pairs.get(cname.strip().lower(), l_value)
            else:
                length = ci.number(feat, l_field)
            if not length or length <= 0:
                feedback.pushWarning(self.tr("Catchment '%s' has no river length (km): skipped.") % cname)
                continue
            lc = None
            if typed and lc_value > 0:
                lc = lc_value
            elif lc_field:
                lc = ci.number(feat, lc_field)
            if lc is not None and lc <= 0:
                lc = None
            lc_note = self.tr("L = %.3f km (%s)") % (length, self.tr("typed in the tool") if typed else self.tr("field %s") % l_field)
            if lc is None:
                lc = lc_ratio * length
                lc_note += self.tr("; Lc = %g x L = %.3f km (assumed)") % (lc_ratio, lc)
            else:
                lc_note += self.tr("; Lc = %.3f km") % lc
            matched += 1
            entry = {"name": cname, "area": area, "length": length, "lc_note": lc_note, "methods": []}
            for key in methods:
                try:
                    p = (hg.nakayasu_params(length, area, tr_f, alpha) if key == hg.NAKAYASU
                         else hg.snyder_params(length, lc, area, ct, cp))
                except ValueError as e:
                    feedback.pushWarning("%s / %s: %s" % (cname, METHOD_NAMES[key], e))
                    continue
                feedback.pushInfo("%s / %s: lag %.3f h, tr %.3f h, Tp %.3f h, Qp %.3f m3/s per mm (before scaling)." % (
                    cname, METHOD_NAMES[key], p["lag"], p["tr"], p["tpeak"], p["qp"]))
                scale = hg.norm_factor(p) if normalize else 1.0
                mdata = {"key": key, "params": p, "scale": scale, "fine": hg.fine_curve(p, scale), "rows": None, "floods": {}}
                cache = {}
                for (st, cn_, T), (dt, pe) in sorted(series.items()):
                    if cn_ != cname:
                        continue
                    if dt not in cache:
                        rows, _ = hg.ordinates(p, dt, normalize, scurve)
                        cache[dt] = rows
                        for r in rows:
                            seg = r["seg"]
                            results_uh.append([cname, METHOD_NAMES[key], dt, r["t"]] + [
                                (r["curve"] if (key == hg.NAKAYASU and seg == nm and r["t"] > 0) else None) for nm in hg.NAK_SEGMENTS] + [r["q"]])
                        if mdata["rows"] is None:
                            mdata["rows"] = rows
                    rows = cache[dt]
                    fl = hg.flood(pe, dt, rows, area, base)
                    for i, (t, q) in enumerate(zip(fl["t"], fl["q"])):
                        results_flood.append([st, cname, METHOD_NAMES[key], float(T), dt, t, pe[i - 1] if 1 <= i <= len(pe) else 0.0, q])
                    results_peak.append([st, cname, METHOD_NAMES[key], float(T), area, length, lc, p["lag"], p["tr"], p["tpeak"],
                                         p["qp"] * scale, fl["peak"], fl["t_peak"], fl["pe_total"], fl["volume_mm"], base])
                    mdata["floods"].setdefault(st, []).append({"T": T, "t": fl["t"], "q": fl["q"], "peak": fl["peak"], "t_peak": fl["t_peak"],
                                                               "pe_total": fl["pe_total"], "volume_mm": fl["volume_mm"]})
                if mdata["rows"] is not None:
                    entry["methods"].append(mdata)
            if entry["methods"]:
                report.append(entry)
        if not report:
            raise QgsProcessingException(self.tr("No catchment could be calculated. Catchment names in the rainfall table: %s") % ", ".join(sorted(names_in_rain)))
        feedback.pushInfo(self.tr("%d catchment(s) calculated.") % matched)
        feedback.setProgress(90)

        crs = QgsCoordinateReferenceSystem()

        def table(key, spec, rows):
            fields = QgsFields()
            for nm, tp in spec:
                fields.append(QgsField(nm, tp))
            sink, dest = self.parameterAsSink(parameters, key, context, fields, WKB_NONE, crs)
            if sink is None:
                raise QgsProcessingException(self.invalidSinkError(parameters, key))
            for r in rows:
                f = QgsFeature(fields)
                f.setAttributes(r)
                sink.addFeature(f)
            return dest

        d_uh = table(self.OUT_UH, [("catchment", T_STR), ("method", T_STR), ("dt_h", T_DBL), ("t_h", T_DBL), ("qa", T_DBL), ("qd1", T_DBL),
                                    ("qd2", T_DBL), ("qd3", T_DBL), ("q", T_DBL)], results_uh)
        d_fl = table(self.OUT_FLOOD, [("station", T_STR), ("catchment", T_STR), ("method", T_STR), ("return_period", T_DBL), ("dt_h", T_DBL),
                                       ("t_h", T_DBL), ("pe_mm", T_DBL), ("q_m3s", T_DBL)], results_flood)
        d_pk = table(self.OUT_PEAK, [("station", T_STR), ("catchment", T_STR), ("method", T_STR), ("return_period", T_DBL), ("area_km2", T_DBL),
                                      ("length_km", T_DBL), ("lc_km", T_DBL), ("lag_h", T_DBL), ("tr_h", T_DBL), ("tpeak_uh_h", T_DBL),
                                      ("qp_uh", T_DBL), ("peak_m3s", T_DBL), ("t_peak_h", T_DBL), ("pe_total_mm", T_DBL), ("volume_mm", T_DBL),
                                      ("base_m3s", T_DBL)], results_peak)
        results = {self.OUT_UH: d_uh, self.OUT_FLOOD: d_fl, self.OUT_PEAK: d_pk}
        path = self.parameterAsFileOutput(parameters, self.REPORT, context)
        if path:
            dts = sorted({dt for dt, _ in series.values()})
            html = rep.build_report(report, {"dt_text": ", ".join("%g" % d for d in dts), "normalize": normalize, "scurve": scurve, "baseflow": base})
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            results[self.REPORT] = path
        feedback.setProgress(100)
        return results
