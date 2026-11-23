# -*- coding: utf-8 -*-
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import catch_input as ci
from .common import T_DBL, T_INT, T_STR
from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, WKB_NONE, RainfallFrequency

try:
    SRC_POLY = QgsProcessing.SourceType.VectorPolygon
except AttributeError:
    SRC_POLY = QgsProcessing.TypeVectorPolygon


class EffectiveRainfall(QgsProcessingAlgorithm):
    """Effective rainfall = runoff coefficient x rainfall, per catchment, return period and time step."""

    HYETO = "HYETO"
    CATCHMENTS = "CATCHMENTS"
    NAME_FIELD = "NAME_FIELD"
    C_FIELD = "C_FIELD"
    C_CONST = "C_CONST"
    STATION = "STATION"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("EffectiveRainfall", s)

    def createInstance(self):
        return EffectiveRainfall()

    def name(self):
        return "effectiverainfall"

    def displayName(self):
        return self.tr("Effective rainfall (runoff coefficient x rainfall)")

    def group(self):
        return self.tr("Rainfall Analysis")

    def groupId(self):
        return "rainfall"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Effective rainfall Pe = C x P for every time step of the design rainfall distribution, for each catchment.\n\n"
            "Inputs: the 'Rainfall distribution' table from 'Design storm' (fields station, return_period, step, t_start_min, "
            "t_end_min, rain_mm are detected automatically) and catchment polygons that carry the runoff coefficient "
            "(field c_weighted from 'Runoff coefficient (C) from land cover and slope'). If a catchment has no C value, "
            "the constant C below is used.\n\n"
            "Output: one row per catchment, return period and time step (rain, effective rain, cumulative effective rain, "
            "loss)."
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        add(QgsProcessingParameterFeatureSource(self.HYETO, self.tr("Rainfall distribution table (output of Design storm)"), [SRC_TABLE]))
        add(QgsProcessingParameterFeatureSource(self.CATCHMENTS, self.tr("Catchment polygons (with runoff coefficient)"), [SRC_POLY]))
        add(QgsProcessingParameterField(self.NAME_FIELD, self.tr("Catchment name field (detected automatically)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterField(self.C_FIELD, self.tr("Runoff coefficient field (default c_weighted)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterNumber(self.C_CONST, self.tr("Constant C (used when a catchment has no C value)"), type=NUM_DOUBLE, defaultValue=0.5, minValue=0.0, maxValue=1.0))
        add(QgsProcessingParameterString(self.STATION, self.tr("Only this station (empty = all stations in the table)"), optional=True))
        add(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Effective rainfall"), type=SRC_TABLE))

    def processAlgorithm(self, parameters, context, feedback):
        hy = self.parameterAsSource(parameters, self.HYETO, context)
        catch = self.parameterAsSource(parameters, self.CATCHMENTS, context)
        if hy is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.HYETO))
        if catch is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.CATCHMENTS))
        hn = hy.fields().names()
        need = {"return_period": ci.pick(hn, ("return_period",)), "step": ci.pick(hn, ("step",)),
                "t0": ci.pick(hn, ("t_start_min",)), "t1": ci.pick(hn, ("t_end_min",)), "rain": ci.pick(hn, ("rain_mm",))}
        miss = [k for k, v in need.items() if not v]
        if miss:
            raise QgsProcessingException(self.tr("The rainfall distribution table lacks the fields for: %s. Use the output of 'Design storm'.") % ", ".join(miss))
        sf = ci.pick(hn, ("station",))
        only = (self.parameterAsString(parameters, self.STATION, context) or "").strip()
        to_f = RainfallFrequency._to_float

        series = {}
        for f in hy.getFeatures():
            st = str(f[sf]) if sf else "all data"
            if only and st != only:
                continue
            T, k = to_f(f[need["return_period"]]), to_f(f[need["step"]])
            t0, t1, r = to_f(f[need["t0"]]), to_f(f[need["t1"]]), to_f(f[need["rain"]])
            if None in (T, k, t0, t1, r):
                continue
            series.setdefault((st, T), []).append((int(k), t0, t1, r))
        if not series:
            raise QgsProcessingException(self.tr("No rainfall rows found%s.") % ((" for station '%s'" % only) if only else ""))

        cn = catch.fields().names()
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context) or ci.pick(cn, ci.NAME_CANDIDATES)
        c_field = self.parameterAsString(parameters, self.C_FIELD, context) or ci.pick(cn, ("c_weighted",))
        c_const = self.parameterAsDouble(parameters, self.C_CONST, context)
        if not c_field:
            feedback.pushWarning(self.tr("No runoff coefficient field found (c_weighted): the constant C = %g is used for all catchments.") % c_const)
        feedback.pushInfo(self.tr("Catchment name field: '%s'; runoff coefficient field: '%s'.") % (name_field or "-", c_field or "-"))

        fields = QgsFields()
        fields.append(QgsField("station", T_STR))
        fields.append(QgsField("catchment", T_STR))
        fields.append(QgsField("return_period", T_DBL))
        fields.append(QgsField("c", T_DBL))
        fields.append(QgsField("step", T_INT))
        for nm in ("t_start_min", "t_end_min", "t_end_h", "rain_mm", "eff_mm", "cum_eff_mm", "loss_mm"):
            fields.append(QgsField(nm, T_DBL))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_NONE, QgsCoordinateReferenceSystem())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        n = 0
        for feat in catch.getFeatures():
            cname = ci.label(feat, name_field)
            c = ci.number(feat, c_field)
            if c is None:
                feedback.pushWarning(self.tr("Catchment '%s' has no C value: constant C = %g used.") % (cname, c_const))
                c = c_const
            for (st, T), rows in sorted(series.items()):
                cum = 0.0
                for k, t0, t1, r in sorted(rows):
                    eff = c * r
                    cum += eff
                    out = QgsFeature(fields)
                    out.setAttributes([st, cname, float(T), float(c), k, t0, t1, t1 / 60.0, r, eff, cum, r - eff])
                    sink.addFeature(out)
            n += 1
        feedback.pushInfo(self.tr("%d catchments x %d (station, return period) series.") % (n, len(series)))
        return {self.OUTPUT: dest}
