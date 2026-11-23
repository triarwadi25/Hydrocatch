# -*- coding: utf-8 -*-
import math

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, hydro
from .common import NUM_INT, SINK_POLY, SRC_POINT, T_DBL, T_INT, T_STR, WKB_MULTIPOLY


class CatchmentFromDem(QgsProcessingAlgorithm):
    """Catchment upstream of each outlet point (like ArcGIS Watershed + Snap Pour Point)."""

    DEM = "DEM"
    OUTLETS = "OUTLETS"
    NAME_FIELD = "NAME_FIELD"
    SNAP = "SNAP"
    CUMULATIVE = "CUMULATIVE"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("CatchmentFromDem", s)

    def createInstance(self):
        return CatchmentFromDem()

    def name(self):
        return "catchmentfromdem"

    def displayName(self):
        return self.tr("Catchment from DEM and outlet points")

    def group(self):
        return self.tr("Catchment Analysis")

    def groupId(self):
        return "catchment"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Delineates the catchment (watershed / DAS) upstream of each outlet point.\n\n"
            "Method: depression filling and D8-style flow routing (Priority-Flood), "
            "then every cell is traced downstream to find which outlet it drains to.\n\n"
            "Tips:\n"
            "- Use a DEM in a projected CRS (metres) when possible. The DEM must be north-up.\n"
            "- Snapping moves each outlet to the highest flow-accumulation cell within the "
            "given radius, so a point clicked near a river lands exactly on the river.\n"
            "- Cumulative catchments: when outlets are nested, each downstream catchment "
            "includes the catchments of the outlets upstream of it. Turn off to get "
            "non-overlapping sub-catchments.\n"
            "- The optional stream network only covers the delineated catchments.\n"
            "- Cells on the DEM edge and next to NoData are treated as the DEM's own "
            "outlets. Clip the DEM generously around your area of interest.\n"
            "- Large DEMs are slow in pure Python (limit %d cells); clip or resample first."
            % common.MAX_CELLS
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(
            QgsProcessingParameterFeatureSource(self.OUTLETS, self.tr("Outlet points"), [SRC_POINT])
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.NAME_FIELD,
                self.tr("Outlet name field (optional)"),
                parentLayerParameterName=self.OUTLETS,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SNAP,
                self.tr("Snap outlets to highest flow accumulation within (cells, 0 = no snapping)"),
                type=NUM_INT,
                defaultValue=5,
                minValue=0,
                maxValue=200,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CUMULATIVE, self.tr("Cumulative catchments (include nested upstream outlets)"), defaultValue=True
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Catchments"), type=SINK_POLY))
        common.add_extra_outputs(self, self.tr)

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        outlets = self.parameterAsSource(parameters, self.OUTLETS, context)
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        snap_cells = self.parameterAsInt(parameters, self.SNAP, context)
        cumulative = self.parameterAsBoolean(parameters, self.CUMULATIVE, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))
        if outlets is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.OUTLETS))

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        dem_crs = dem_layer.crs()
        routed = common.route(feedback, arr, mask)
        if routed is None:
            return {}
        order, receiver, acc = routed

        # ---- outlets -> cells ---------------------------------------------------
        xform = QgsCoordinateTransform(outlets.sourceCrs(), dem_crs, context.transformContext())
        name_idx = outlets.fields().lookupField(name_field) if name_field else -1
        outlet_cells = {}
        info = {}
        next_id = 1
        for feat in outlets.getFeatures():
            if feedback.isCanceled():
                return {}
            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            pt = xform.transform(geom.centroid().asPoint())
            col = int(math.floor((pt.x() - gt[0]) / gt[1]))
            row = int(math.floor((pt.y() - gt[3]) / gt[5]))
            label = str(feat[name_idx]) if name_idx >= 0 else str(next_id)
            if not (0 <= row < rows and 0 <= col < cols):
                feedback.reportError(self.tr("Outlet '%s' is outside the DEM and was skipped.") % label, False)
                continue
            row, col = hydro.snap_to_max(acc, row, col, snap_cells)
            if mask[row, col]:
                feedback.reportError(self.tr("Outlet '%s' falls on a NoData cell and was skipped.") % label, False)
                continue
            flat = row * cols + col
            if flat in outlet_cells:
                feedback.reportError(
                    self.tr("Outlet '%s' snapped to the same cell as another outlet and was skipped.") % label, False
                )
                continue
            outlet_cells[flat] = next_id
            info[next_id] = {
                "name": label,
                "x": gt[0] + (col + 0.5) * gt[1],
                "y": gt[3] + (row + 0.5) * gt[5],
            }
            next_id += 1
        if not outlet_cells:
            raise QgsProcessingException(self.tr("No usable outlet points."))

        feedback.pushInfo(self.tr("Delineating %d catchment(s)...") % len(outlet_cells))
        labels, parent = hydro.label_catchments(order, receiver, outlet_cells, (rows, cols))
        counts = np.bincount(labels.ravel(), minlength=next_id)
        feedback.setProgress(75)

        sub_geom = common.polygonize_labels(labels, gt, ds.GetProjection())
        feedback.setProgress(88)

        geoms = {oid: [] for oid in info}
        cells = {oid: 0 for oid in info}
        for oid in info:
            own = sub_geom.get(oid)
            p = oid
            while p:
                if own is not None:
                    geoms[p].append(own)
                cells[p] += int(counts[oid])
                p = parent.get(p, 0) if cumulative else 0
                if p == oid:
                    break

        fields = QgsFields()
        fields.append(QgsField("outlet_id", T_INT))
        fields.append(QgsField("name", T_STR))
        fields.append(QgsField("cells", T_INT))
        fields.append(QgsField("area_km2", T_DBL))
        fields.append(QgsField("snap_x", T_DBL))
        fields.append(QgsField("snap_y", T_DBL))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_MULTIPOLY, dem_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        da = common.distance_area(context, dem_crs)
        for oid, meta in info.items():
            gl = geoms[oid]
            if not gl:
                continue
            g = QgsGeometry.unaryUnion(gl) if len(gl) > 1 else gl[0]
            g.convertToMultiType()
            area = da.convertAreaMeasurement(da.measureArea(g), common.KM2)
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes([oid, meta["name"], cells[oid], float(area), meta["x"], meta["y"]])
            sink.addFeature(f)
        results = {self.OUTPUT: dest_id}

        streams = common.write_streams(
            self, parameters, context, feedback, order, receiver, acc, labels.ravel() > 0, gt, cols, dem_crs, da
        )
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
