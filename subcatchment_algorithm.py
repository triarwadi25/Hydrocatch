# -*- coding: utf-8 -*-
import numpy as np
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, hydro
from .common import SINK_POLY, T_DBL, T_INT, WKB_MULTIPOLY


class SubcatchmentsFromDem(QgsProcessingAlgorithm):
    """One catchment polygon per stream reach (like Arc Hydro Catchment Polygon)."""

    DEM = "DEM"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("SubcatchmentsFromDem", s)

    def createInstance(self):
        return SubcatchmentsFromDem()

    def name(self):
        return "subcatchmentsfromdem"

    def displayName(self):
        return self.tr("Sub-catchments per stream reach")

    def group(self):
        return self.tr("Catchment Analysis")

    def groupId(self):
        return "catchment"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Splits the DEM into one catchment polygon for every stream reach (the stretch of stream between "
            "two confluences, or from a headwater to the first confluence), like Arc Hydro's Catchment Polygon.\n\n"
            "A stream cell is a cell receiving at least 'Stream threshold' cells. Every other cell belongs to the "
            "reach it drains into. The reach_id matches the optional stream network output, and down_id gives the "
            "reach it flows into (0 = leaves the DEM), so the network topology is kept.\n\n"
            "Cells that leave the DEM without meeting a stream are not part of any polygon.\n"
            "Smaller threshold = more and smaller sub-catchments."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Sub-catchments"), type=SINK_POLY))
        common.add_extra_outputs(self, self.tr)

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        threshold = self.parameterAsInt(parameters, common.STREAM_THRESHOLD, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        dem_crs = dem_layer.crs()
        routed = common.route(feedback, arr, mask)
        if routed is None:
            return {}
        order, receiver, acc = routed

        feedback.pushInfo(self.tr("Extracting stream reaches (threshold %d cells)...") % threshold)
        cell_reach = [0] * (rows * cols)
        reaches = hydro.stream_reaches(order, receiver, acc, threshold, None, cell_reach)
        if not reaches:
            raise QgsProcessingException(self.tr("No cell reaches the stream threshold. Lower the threshold."))
        down = hydro.reach_downstream_ids(reaches)
        labels = hydro.label_subcatchments(order, receiver, cell_reach, (rows, cols))
        counts = np.bincount(labels.ravel(), minlength=len(reaches) + 1)
        feedback.pushInfo(self.tr("%d sub-catchments.") % len(reaches))
        feedback.setProgress(75)

        geoms = common.polygonize_labels(labels, gt, ds.GetProjection())
        feedback.setProgress(88)

        fields = QgsFields()
        fields.append(QgsField("reach_id", T_INT))
        fields.append(QgsField("strahler", T_INT))
        fields.append(QgsField("down_id", T_INT))
        fields.append(QgsField("acc_cells", T_INT))
        fields.append(QgsField("cells", T_INT))
        fields.append(QgsField("area_km2", T_DBL))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_MULTIPOLY, dem_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        da = common.distance_area(context, dem_crs)
        for rid, (path, so, a) in enumerate(reaches, 1):
            g = geoms.get(rid)
            if g is None:
                continue
            g.convertToMultiType()
            area = da.convertAreaMeasurement(da.measureArea(g), common.KM2)
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes([rid, so, down[rid - 1], a, int(counts[rid]), float(area)])
            sink.addFeature(f)
        results = {self.OUTPUT: dest_id}

        streams = common.write_streams(self, parameters, context, feedback, order, receiver, acc, None, gt, cols, dem_crs, da)
        if streams:
            results[common.STREAMS] = streams
        acc_path = common.write_acc_raster(self, parameters, context, acc, ds, gt)
        if acc_path:
            results[common.ACC] = acc_path
        fdir_path = common.write_dir_raster(self, parameters, context, receiver, mask, ds, gt)
        if fdir_path:
            results[common.FDIR] = fdir_path
        feedback.setProgress(100)
        return results
