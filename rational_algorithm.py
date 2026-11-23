# -*- coding: utf-8 -*-
from qgis.core import (
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
    QgsProcessingParameterNumber,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import catch_input as ci
from . import common, design_storm as ds, rain_input
from .common import T_DBL, T_STR
from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, WKB_NONE

try:
    SRC_POLY = QgsProcessing.SourceType.VectorPolygon
except AttributeError:
    SRC_POLY = QgsProcessing.TypeVectorPolygon


class RationalDischarge(QgsProcessingAlgorithm):
    """Design discharge with the rational method: Q = 0.2778 C I A, I from Mononobe at the time of concentration."""

    CATCHMENTS = "CATCHMENTS"
    NAME_FIELD = "NAME_FIELD"
    C_FIELD = "C_FIELD"
    C_CONST = "C_CONST"
    TC_FIELD = "TC_FIELD"
    TC_UNIT = "TC_UNIT"
    TC_CONST = "TC_CONST"
    AREA_FIELD = "AREA_FIELD"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("RationalDischarge", s)

    def createInstance(self):
        return RationalDischarge()

    def name(self):
        return "rationaldischarge"

    def displayName(self):
        return self.tr("Design discharge (rational method)")

    def group(self):
        return self.tr("Discharge Analysis")

    def groupId(self):
        return "discharge"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Peak design discharge Q = 0.2778 C I A (m3/s) for every catchment and return period.\n\n"
            "I (mm/h) is the Mononobe intensity at the time of concentration: I = (R24/24)(24/tc)^(2/3).\n"
            "Area A comes from the catchment polygon (km2, ellipsoidal). C is read from the field c_weighted "
            "(output of 'Runoff coefficient (C) from land cover and slope') and tc from the field tc_min "
            "(Kirpich time of concentration from 'Catchment morphometry', in minutes); both can be changed. "
            "R24 comes from the 'Design rainfall' table of the rainfall frequency analysis for the distribution you choose.\n\n"
            "The rational method is meant for small catchments; a warning is given above 25 km2. Check the area limit "
            "of the guideline you follow."
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        add(QgsProcessingParameterFeatureSource(self.CATCHMENTS, self.tr("Catchment polygons (output of Catchment morphometry / Runoff coefficient)"), [SRC_POLY]))
        add(QgsProcessingParameterField(self.NAME_FIELD, self.tr("Catchment name field (detected automatically)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterField(self.C_FIELD, self.tr("Runoff coefficient field (default c_weighted)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterNumber(self.C_CONST, self.tr("Constant C (used when a catchment has no C value)"), type=NUM_DOUBLE, defaultValue=0.5, minValue=0.0, maxValue=1.0))
        add(QgsProcessingParameterField(self.TC_FIELD, self.tr("Time of concentration field (default tc_min)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterEnum(self.TC_UNIT, self.tr("Unit of the time of concentration field"), options=["minutes", "hours"], defaultValue=0))
        add(QgsProcessingParameterNumber(self.TC_CONST, self.tr("Constant tc in hours (used when a catchment has no tc value)"), type=NUM_DOUBLE, defaultValue=1.0, minValue=0.05, maxValue=24.0))
        add(QgsProcessingParameterField(self.AREA_FIELD, self.tr("Area field in km2 (empty = area of the polygon)"), parentLayerParameterName=self.CATCHMENTS, type=FIELD_ANY, optional=True))
        rain_input.add_params(self, self.tr)
        add(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Design discharge"), type=SRC_TABLE))

    def processAlgorithm(self, parameters, context, feedback):
        catch = self.parameterAsSource(parameters, self.CATCHMENTS, context)
        if catch is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.CATCHMENTS))
        cn = catch.fields().names()
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context) or ci.pick(cn, ci.NAME_CANDIDATES)
        c_field = self.parameterAsString(parameters, self.C_FIELD, context) or ci.pick(cn, ("c_weighted",))
        tc_field = self.parameterAsString(parameters, self.TC_FIELD, context) or ci.pick(cn, ("tc_min",))
        area_field = self.parameterAsString(parameters, self.AREA_FIELD, context)
        tc_hours = self.parameterAsEnum(parameters, self.TC_UNIT, context) == 1
        c_const = self.parameterAsDouble(parameters, self.C_CONST, context)
        tc_const = self.parameterAsDouble(parameters, self.TC_CONST, context)
        feedback.pushInfo(self.tr("Fields: name '%s', C '%s', tc '%s', area '%s'.") % (name_field or "-", c_field or "-", tc_field or "-", area_field or "polygon"))
        if not c_field:
            feedback.pushWarning(self.tr("No runoff coefficient field found: constant C = %g for all catchments.") % c_const)
        if not tc_field:
            feedback.pushWarning(self.tr("No time of concentration field found: constant tc = %g h for all catchments.") % tc_const)

        rain = rain_input.read(self, parameters, context, feedback)
        da = common.distance_area(context, catch.sourceCrs())

        fields = QgsFields()
        fields.append(QgsField("station", T_STR))
        fields.append(QgsField("catchment", T_STR))
        for nm in ("return_period", "r24", "area_km2", "c", "tc_h", "intensity", "q_m3s"):
            fields.append(QgsField(nm, T_DBL))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_NONE, QgsCoordinateReferenceSystem())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        for feat in catch.getFeatures():
            cname = ci.label(feat, name_field)
            geom = feat.geometry()
            area = ci.number(feat, area_field)
            if area is None:
                if geom is None or geom.isNull() or geom.isEmpty():
                    feedback.reportError(self.tr("Catchment '%s' has no geometry and no area value: skipped.") % cname, False)
                    continue
                area = da.convertAreaMeasurement(da.measureArea(geom), common.KM2)
            c = ci.number(feat, c_field)
            if c is None:
                feedback.pushWarning(self.tr("Catchment '%s' has no C value: constant C = %g used.") % (cname, c_const))
                c = c_const
            tc = ci.number(feat, tc_field)
            tc = (tc if tc_hours else tc / 60.0) if tc is not None else tc_const
            if tc <= 0:
                tc = tc_const
            if tc < 5.0 / 60.0:
                feedback.pushWarning(self.tr("Catchment '%s': tc = %.1f min is very short; Mononobe intensities are uncertain.") % (cname, tc * 60))
            if area > 25.0:
                feedback.pushWarning(self.tr("Catchment '%s' is %.1f km2: the rational method is meant for small catchments.") % (cname, area))
            for st, table in rain.items():
                for T in sorted(table):
                    r24 = table[T]
                    i = ds.mononobe_intensity(r24, tc)
                    f = QgsFeature(fields)
                    f.setAttributes([st, cname, float(T), r24, float(area), float(c), tc, i, ds.rational_discharge(c, i, area)])
                    sink.addFeature(f)
        return {self.OUTPUT: dest}
