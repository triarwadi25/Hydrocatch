# -*- coding: utf-8 -*-
import re

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import design_storm as ds
from . import rain_input
from . import design_storm_report as report
from .common import NUM_INT, T_DBL, T_INT, T_STR
from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, WKB_NONE, RainfallFrequency

ARRANGE = ["mononobe", "abm"]
ARRANGE_TEXT = ["Mononobe (hujan terbesar di awal)", "Alternating block (puncak di tengah)"]
DEFAULT_DURATIONS = "0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10, 11, 12"


def _pick(names, wanted):
    low = {n.lower(): n for n in names}
    for w in wanted:
        if w in low:
            return low[w]
    return ""


class DesignStorm(QgsProcessingAlgorithm):
    """Mononobe intensities and rainfall distribution (hyetograph) for design rainfall."""

    INPUT = "INPUT"
    PERIOD_FIELD = "PERIOD_FIELD"
    RAIN_FIELD = "RAIN_FIELD"
    GROUP = "GROUP"
    DIST_FIELD = "DIST_FIELD"
    DIST_VALUE = "DIST_VALUE"
    VALUES = "VALUES"
    FACTOR = "FACTOR"
    DURATION = "DURATION"
    STEP = "STEP"
    ARRANGE = "ARRANGE"
    IDF_DURATIONS = "IDF_DURATIONS"
    OUT_IDF = "OUT_IDF"
    OUT_HYETO = "OUT_HYETO"
    REPORT = "REPORT"

    def tr(self, s):
        return QCoreApplication.translate("DesignStorm", s)

    def createInstance(self):
        return DesignStorm()

    def name(self):
        return "designstorm"

    def displayName(self):
        return self.tr("Design storm: Mononobe intensity and rainfall distribution")

    def group(self):
        return self.tr("Rainfall Analysis")

    def groupId(self):
        return "rainfall"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Turns the design rainfall R24 of each return period into:\n"
            "1. Rainfall intensity with Mononobe: I = (R24/24)(24/t)^(2/3), for the durations you list "
            "(in hours; default 0.25 to 12 h). The output gives both hours and minutes.\n"
            "2. A rainfall distribution (hyetograph) for a rain duration T (default 6 h) in time steps "
            "(default 30 minutes). The cumulative share at time t is (t/T)^(1/3); the rain of each step is the "
            "difference. The blocks can be arranged with the largest first (Mononobe) or in the middle "
            "(alternating block).\n\n"
            "Input: the 'Design rainfall' table from 'Rainfall frequency analysis' (fields return_period, rainfall, "
            "station, distribution are detected automatically); choose the distribution from the list (Normal, Log Normal, Gumbel, Log Pearson III). "
            "Or type pairs like '2=96.5, 5=127.8, 10=148' in the Values box.\n\n"
            "The R24 factor lets you convert a 1-day maximum into a 24-hour value if your guideline requires it "
            "(1.0 = no conversion).\n\n"
            "Effective rainfall is not calculated here."
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        rain_input.add_params(self, self.tr)
        add(QgsProcessingParameterString(self.IDF_DURATIONS, self.tr("Durations for the intensity table (hours)"), defaultValue=DEFAULT_DURATIONS))
        add(QgsProcessingParameterNumber(self.DURATION, self.tr("Rain duration T for the distribution (hours)"), type=NUM_DOUBLE, defaultValue=6.0, minValue=0.25, maxValue=24.0))
        add(QgsProcessingParameterNumber(self.STEP, self.tr("Time step of the distribution (minutes)"), type=NUM_INT, defaultValue=30, minValue=5, maxValue=360))
        add(QgsProcessingParameterEnum(self.ARRANGE, self.tr("Arrangement of the rainfall blocks"), options=["Mononobe order (largest first)", "Alternating block (peak in the middle)"], defaultValue=0))
        add(QgsProcessingParameterFeatureSink(self.OUT_IDF, self.tr("Intensity (Mononobe)"), type=SRC_TABLE))
        add(QgsProcessingParameterFeatureSink(self.OUT_HYETO, self.tr("Rainfall distribution"), type=SRC_TABLE))
        add(QgsProcessingParameterFileDestination(self.REPORT, self.tr("HTML report with charts (optional)"), fileFilter="HTML files (*.html)", optional=True, createByDefault=True))

    # ------------------------------------------------------------------
    @staticmethod
    def _numbers(text):
        """Numbers separated by spaces, semicolons or commas (a lone comma inside a token is a decimal comma)."""
        out = []
        for t in re.split(r"[;\s]+", (text or "").strip()):
            t = t.strip(",")
            if not t:
                continue
            if "," in t and ("." in t or t.count(",") > 1):
                parts = [p for p in t.split(",") if p]
            else:
                parts = [t.replace(",", ".")]
            for part in parts:
                try:
                    out.append(float(part))
                except ValueError:
                    raise QgsProcessingException("Not a number: '%s'" % part)
        return out

    def processAlgorithm(self, parameters, context, feedback):
        factor = self.parameterAsDouble(parameters, rain_input.FACTOR, context)
        duration = self.parameterAsDouble(parameters, self.DURATION, context)
        step = self.parameterAsInt(parameters, self.STEP, context)
        arrange = ARRANGE[self.parameterAsEnum(parameters, self.ARRANGE, context)]
        durs = [d for d in self._numbers(self.parameterAsString(parameters, self.IDF_DURATIONS, context)) if d > 0]
        if not durs:
            raise QgsProcessingException(self.tr("Give at least one duration (in hours) for the intensity table."))
        durs = sorted(set(durs))
        try:
            ds.hourly_ratios(duration, step / 60.0)
        except ValueError:
            raise QgsProcessingException(self.tr("Rain duration (%g h) must be a whole number of time steps (%d min).") % (duration, step))

        stations = rain_input.read(self, parameters, context, feedback)

        f_idf = QgsFields()
        f_idf.append(QgsField("station", T_STR))
        for nm in ("return_period", "r24", "duration_h", "duration_min", "intensity", "depth_mm"):
            f_idf.append(QgsField(nm, T_DBL))
        f_h = QgsFields()
        f_h.append(QgsField("station", T_STR))
        f_h.append(QgsField("return_period", T_DBL))
        f_h.append(QgsField("r24", T_DBL))
        f_h.append(QgsField("step", T_INT))
        for nm in ("t_start_min", "t_end_min", "t_start_h", "t_end_h", "ratio", "cum_ratio", "rain_mm", "cum_rain_mm", "intensity"):
            f_h.append(QgsField(nm, T_DBL))
        crs = QgsCoordinateReferenceSystem()
        s_idf, id_idf = self.parameterAsSink(parameters, self.OUT_IDF, context, f_idf, WKB_NONE, crs)
        s_h, id_h = self.parameterAsSink(parameters, self.OUT_HYETO, context, f_h, WKB_NONE, crs)
        if s_idf is None or s_h is None:
            raise QgsProcessingException(self.tr("Could not create an output table."))

        done = []
        for name, table in stations.items():
            items = []
            for T in sorted(table):
                if feedback.isCanceled():
                    return {}
                r24 = table[T]
                idf = ds.idf_table(r24, durs)
                rows = ds.design_storm(r24, duration, step, arrange, "none")
                items.append({"T": T, "r24": r24, "idf": idf, "rows": rows})
                for r in idf:
                    f = QgsFeature(f_idf)
                    f.setAttributes([name, float(T), r24, float(r["hours"]), float(r["duration"]), r["intensity"], r["depth"]])
                    s_idf.addFeature(f)
                cum_ratio = 0.0
                for r in rows:
                    cum_ratio += r["ratio"]
                    f = QgsFeature(f_h)
                    f.setAttributes([name, float(T), r24, r["step"], float(r["t0"]), float(r["t1"]), r["t0"] / 60.0, r["t1"] / 60.0,
                                     r["ratio"], cum_ratio, r["rain"], r["cum"], r["intensity"]])
                    s_h.addFeature(f)
            done.append((name, items))
            feedback.pushInfo("%s: %d return periods, %d duration values, %d time steps." % (name, len(items), len(durs), len(items[0]["rows"])))

        results = {self.OUT_IDF: id_idf, self.OUT_HYETO: id_h}
        path = self.parameterAsFileOutput(parameters, self.REPORT, context)
        if path:
            html = report.build_report(done, {
                "duration": duration, "step": step, "arrange": ARRANGE_TEXT[ARRANGE.index(arrange)], "factor": factor})
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            results[self.REPORT] = path
        return results
