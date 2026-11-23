# -*- coding: utf-8 -*-
import math
import re

from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import hss, hss_report
from .common import NUM_INT, T_DBL, T_INT, T_STR

try:
    SRC_TABLE = QgsProcessing.SourceType.Vector
except AttributeError:
    SRC_TABLE = QgsProcessing.TypeVector
try:
    FIELD_NUMERIC = QgsProcessingParameterField.DataType.Numeric
    FIELD_ANY = QgsProcessingParameterField.DataType.Any
except AttributeError:
    FIELD_NUMERIC = QgsProcessingParameterField.Numeric
    FIELD_ANY = QgsProcessingParameterField.Any
try:
    NUM_DOUBLE = QgsProcessingParameterNumber.Type.Double
except AttributeError:
    NUM_DOUBLE = QgsProcessingParameterNumber.Double
try:
    WKB_NONE = Qgis.WkbType.NoGeometry
except AttributeError:
    WKB_NONE = QgsWkbTypes.NoGeometry


def _verdict(ok):
    return "n/a" if ok is None else ("Diterima" if ok else "Ditolak")


class RainfallFrequency(QgsProcessingAlgorithm):
    """Rainfall statistics, frequency analysis and goodness-of-fit tests."""

    INPUT = "INPUT"
    FIELD = "FIELD"
    GROUP = "GROUP"
    VALUES = "VALUES"
    PERIODS = "PERIODS"
    GUMBEL = "GUMBEL"
    KS_POS = "KS_POS"
    ALPHA = "ALPHA"
    K_CLASSES = "K_CLASSES"
    OUT_STATS = "OUT_STATS"
    OUT_QUANT = "OUT_QUANT"
    OUT_TESTS = "OUT_TESTS"
    REPORT = "REPORT"

    GUMBEL_OPTS = ["Gumbel table (Yn, Sn) - common in Indonesian practice", "Method of moments (asymptotic)"]
    KS_OPTS = ["Weibull m/(n+1) - common in Indonesian practice", "Empirical CDF (standard statistic)"]

    def tr(self, s):
        return QCoreApplication.translate("RainfallFrequency", s)

    def createInstance(self):
        return RainfallFrequency()

    def name(self):
        return "rainfallfrequency"

    def displayName(self):
        return self.tr("Rainfall frequency analysis (statistics, distributions, return periods)")

    def group(self):
        return self.tr("Rainfall Analysis")

    def groupId(self):
        return "rainfall"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Frequency analysis of annual maximum rainfall (hujan harian maksimum tahunan).\n\n"
            "Input: a table / attribute table (for example a CSV loaded as a layer) with a field of "
            "annual maxima, or values typed in the 'Values' box (separated by spaces, semicolons or new lines; "
            "decimal point). With a 'Group by' field (e.g. station name) every group is analysed separately.\n\n"
            "Statistics: mean, standard deviation, coefficient of variation (Cv), skewness (Cs) and kurtosis (Ck), "
            "for the data and for log10 of the data.\n\n"
            "Distributions (method of moments): Normal, Log Normal, Gumbel, Log Pearson III. "
            "Design rainfall X_T for the return periods you give (default 2, 5, 10, 25, 50, 100 years).\n\n"
            "Goodness of fit: Chi-Square (equal-probability classes, Sturges unless you set the number of classes) "
            "and Smirnov-Kolmogorov, at the significance level you choose. Log distributions are skipped when the "
            "data contain values <= 0.\n\n"
            "Outputs: statistics table, design rainfall table (long format), goodness-of-fit table and an optional "
            "HTML report (in Indonesian) with charts (frequency curves, goodness-of-fit bars) and a distribution "
            "selection summary, ready to copy into a document.\n\n"
            "At least 5 values are required; 10 or more (preferably 20+) are recommended. "
            "Test results are not reliable for very short records."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(self.INPUT, self.tr("Input table (optional if values are typed below)"), [SRC_TABLE], optional=True)
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.FIELD, self.tr("Rainfall field (annual maximum; detected automatically if left empty)"), parentLayerParameterName=self.INPUT, type=FIELD_ANY, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.GROUP, self.tr("Group by field, e.g. station (optional)"), parentLayerParameterName=self.INPUT, type=FIELD_ANY, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.VALUES, self.tr("Or type the values here (used when no input table is chosen)"), multiLine=True, optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(self.PERIODS, self.tr("Return periods (years)"), defaultValue="2, 5, 10, 25, 50, 100")
        )
        self.addParameter(QgsProcessingParameterEnum(self.GUMBEL, self.tr("Gumbel method"), options=self.GUMBEL_OPTS, defaultValue=0))
        self.addParameter(
            QgsProcessingParameterEnum(self.KS_POS, self.tr("Smirnov-Kolmogorov statistic"), options=self.KS_OPTS, defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ALPHA, self.tr("Significance level (alpha)"), type=NUM_DOUBLE, defaultValue=0.05, minValue=0.001, maxValue=0.5
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.K_CLASSES, self.tr("Number of classes for Chi-Square (0 = automatic, Sturges)"), type=NUM_INT, defaultValue=0, minValue=0, maxValue=50
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_STATS, self.tr("Statistics"), type=SRC_TABLE))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_QUANT, self.tr("Design rainfall (return periods)"), type=SRC_TABLE))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_TESTS, self.tr("Goodness-of-fit tests"), type=SRC_TABLE))
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.REPORT, self.tr("HTML report with charts (optional)"), fileFilter="HTML files (*.html)", optional=True, createByDefault=True
            )
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_numbers(text):
        parts = [t for t in re.split(r"[;\s]+", (text or "").strip()) if t]
        if len(parts) == 1 and "," in parts[0]:
            parts = [t for t in parts[0].split(",") if t]
        out = []
        for t in parts:
            t = t.strip().strip(",")
            if not t:
                continue
            try:
                out.append(float(t))
            except ValueError:
                raise QgsProcessingException("Not a number: '%s'" % t)
        return out

    @staticmethod
    def _to_float(v):
        """Number from a numeric or text value ('12.5', '12,5'); None if empty / not a number."""
        if v is None or v == NULL:
            return None
        if isinstance(v, (int, float)):
            x = float(v)
        else:
            t = str(v).strip()
            if not t:
                return None
            if "," in t and "." not in t:
                t = t.replace(",", ".")
            try:
                x = float(t)
            except ValueError:
                return None
        return None if math.isnan(x) else x

    _NOT_RAIN = re.compile(r"^(year|tahun|thn|th|no|nomor|id|fid|bulan|month|date|tanggal)$", re.I)

    def _guess_field(self, feats, names, group_field, feedback):
        cands = []
        for nm in names:
            if nm == group_field or self._NOT_RAIN.match(nm):
                continue
            vals = [feats_v for feats_v in (f[nm] for f in feats) if feats_v is not None and feats_v != NULL and str(feats_v).strip() != ""]
            if not vals:
                continue
            ok = sum(1 for v in vals if self._to_float(v) is not None)
            if ok / len(vals) >= 0.9:
                cands.append(nm)
        if not cands:
            raise QgsProcessingException(self.tr("No numeric field found. Choose the rainfall field."))
        if len(cands) > 1:
            feedback.pushWarning(
                self.tr("Several numeric fields found (%s). Using '%s'; choose the rainfall field explicitly if this is wrong.")
                % (", ".join(cands), cands[0])
            )
        else:
            feedback.pushInfo(self.tr("Rainfall field not chosen: using '%s'.") % cands[0])
        return cands[0]

    def _read_groups(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        groups = {}
        if source is not None:
            names = source.fields().names()
            field = self.parameterAsString(parameters, self.FIELD, context)
            gfield = self.parameterAsString(parameters, self.GROUP, context)
            feats = list(source.getFeatures())
            if not feats:
                raise QgsProcessingException(self.tr("The input table is empty."))
            if not field:
                field = self._guess_field(feats, names, gfield, feedback)
            elif field not in names:
                raise QgsProcessingException(self.tr("Field '%s' not found in the input table.") % field)
            skipped = 0
            for feat in feats:
                v = self._to_float(feat[field])
                if v is None:
                    skipped += 1
                    continue
                key = "all data"
                if gfield:
                    g = feat[gfield]
                    key = "(empty)" if g == NULL or g is None else str(g)
                groups.setdefault(key, []).append(v)
            if skipped:
                feedback.pushWarning(self.tr("%d rows without a valid number in '%s' were skipped.") % (skipped, field))
            if not groups:
                raise QgsProcessingException(self.tr("Field '%s' has no numeric values.") % field)
        else:
            values = self._parse_numbers(self.parameterAsString(parameters, self.VALUES, context))
            if not values:
                raise QgsProcessingException(self.tr("Choose an input table or type the values."))
            groups["typed values"] = values
        return groups

    def processAlgorithm(self, parameters, context, feedback):
        periods = []
        for t in self._parse_numbers(self.parameterAsString(parameters, self.PERIODS, context).replace(",", " ")):
            if t <= 1:
                feedback.pushWarning(
                    self.tr("Return period %g skipped: it must be greater than 1 year (use e.g. 1.01 for a near-annual event).") % t
                )
                continue
            periods.append(t)
        if not periods:
            raise QgsProcessingException(self.tr("Give at least one return period greater than 1 year."))
        alpha = self.parameterAsDouble(parameters, self.ALPHA, context)
        gum_idx = self.parameterAsEnum(parameters, self.GUMBEL, context)
        ks_idx = self.parameterAsEnum(parameters, self.KS_POS, context)
        k_classes = self.parameterAsInt(parameters, self.K_CLASSES, context)
        gumbel = "table" if gum_idx == 0 else "moments"
        ks_pos = "weibull" if ks_idx == 0 else "ecdf"

        groups = self._read_groups(parameters, context, feedback)

        f_stats = QgsFields()
        f_stats.append(QgsField("station", T_STR))
        for nm, tp in (("n", T_INT), ("mean", T_DBL), ("std", T_DBL), ("cv", T_DBL), ("cs", T_DBL), ("ck", T_DBL),
                       ("min", T_DBL), ("max", T_DBL), ("log_mean", T_DBL), ("log_std", T_DBL), ("log_cs", T_DBL), ("log_ck", T_DBL)):
            f_stats.append(QgsField(nm, tp))
        f_q = QgsFields()
        f_q.append(QgsField("station", T_STR))
        f_q.append(QgsField("distribution", T_STR))
        for nm in ("return_period", "prob_exceed", "k_factor", "rainfall"):
            f_q.append(QgsField(nm, T_DBL))
        f_t = QgsFields()
        f_t.append(QgsField("station", T_STR))
        f_t.append(QgsField("distribution", T_STR))
        f_t.append(QgsField("n_params", T_INT))
        for nm, tp in (("ks_d", T_DBL), ("ks_crit", T_DBL), ("ks_p", T_DBL), ("ks_result", T_STR),
                       ("chi2_class", T_INT), ("chi2_dof", T_INT), ("chi2", T_DBL), ("chi2_crit", T_DBL), ("chi2_p", T_DBL),
                       ("chi2_result", T_STR), ("rmse", T_DBL), ("result", T_STR)):
            f_t.append(QgsField(nm, tp))

        crs = QgsCoordinateReferenceSystem()
        s_stats, id_stats = self.parameterAsSink(parameters, self.OUT_STATS, context, f_stats, WKB_NONE, crs)
        s_q, id_q = self.parameterAsSink(parameters, self.OUT_QUANT, context, f_q, WKB_NONE, crs)
        s_t, id_t = self.parameterAsSink(parameters, self.OUT_TESTS, context, f_t, WKB_NONE, crs)
        if s_stats is None or s_q is None or s_t is None:
            raise QgsProcessingException(self.tr("Could not create an output table."))

        done = []
        total = len(groups)
        for i, (name, values) in enumerate(groups.items()):
            if feedback.isCanceled():
                return {}
            feedback.setProgress(100.0 * i / total)
            try:
                res = hss.analyse(values, periods, alpha, k_classes, ks_pos, gumbel)
            except ValueError as e:
                feedback.reportError(self.tr("'%s' skipped: %s") % (name, e), False)
                continue
            if len(values) < 10:
                feedback.pushWarning(self.tr("'%s' has only %d values: test results are unreliable.") % (name, len(values)))
            if res["stats"]["log_mean"] is None:
                feedback.pushWarning(self.tr("'%s' contains values <= 0: Log Normal and Log Pearson III were skipped.") % name)
            done.append((name, res))

            st = res["stats"]
            f = QgsFeature(f_stats)
            f.setAttributes([name, st["n"], st["mean"], st["std"], st["cv"], st["cs"], st["ck"], st["min"], st["max"],
                             st["log_mean"], st["log_std"], st["log_cs"], st["log_ck"]])
            s_stats.addFeature(f)
            for d in res["dists"]:
                for q in d["quantiles"]:
                    f = QgsFeature(f_q)
                    f.setAttributes([name, d["name"], float(q["T"]), q["p_exceed"], q["k"], q["x"]])
                    s_q.addFeature(f)
                ks, c2 = d["ks"], d["chi2"]
                both = ks["ok"] and c2["ok"] is not False
                f = QgsFeature(f_t)
                f.setAttributes([name, d["name"], d["nparams"], ks["d"], ks["crit"], ks["p"], _verdict(ks["ok"]),
                                 c2["k"], c2["dof"], c2["chi2"], c2["crit"], c2["p"], _verdict(c2["ok"]),
                                 d["rmse"], "Diterima" if both else "Ditolak"])
                s_t.addFeature(f)
            best = hss_report.recommend(res["dists"])
            feedback.pushInfo("%s: n=%d, mean=%.2f, Cs=%.3f, Ck=%.3f, Cv=%.3f -> %s" % (
                name, st["n"], st["mean"], st["cs"], st["ck"], st["cv"] or float("nan"),
                ("best fit: %s" % best["name"]) if best else "no distribution passes both tests"))
        if not done:
            raise QgsProcessingException(self.tr("No group could be analysed."))

        results = {self.OUT_STATS: id_stats, self.OUT_QUANT: id_q, self.OUT_TESTS: id_t}
        report = self.parameterAsFileOutput(parameters, self.REPORT, context)
        if report:
            html = hss_report.build_report(done, {
                "alpha": alpha,
                "gumbel": "tabel Yn, Sn" if gumbel == "table" else "metode momen (asimtotik)",
                "ks_pos": "Weibull m/(n+1)" if ks_pos == "weibull" else "ECDF standar",
                "classes": "otomatis (Sturges)" if k_classes == 0 else str(k_classes),
            })
            with open(report, "w", encoding="utf-8") as fh:
                fh.write(html)
            results[self.REPORT] = report
        feedback.setProgress(100)
        return results
