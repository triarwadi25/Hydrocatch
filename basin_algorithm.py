# -*- coding: utf-8 -*-
import numpy as np
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, hydro
from .common import NUM_INT, SINK_POLY, T_DBL, T_INT, WKB_MULTIPOLY


class BasinsFromDem(QgsProcessingAlgorithm):
    """All drainage basins of a DEM, no outlet points needed (like ArcGIS Basin)."""

    DEM = "DEM"
    MIN_CELLS = "MIN_CELLS"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("BasinsFromDem", s)

    def createInstance(self):
        return BasinsFromDem()

    def name(self):
        return "basinsfromdem"

    def displayName(self):
        return self.tr("Basins from DEM (no outlet points)")

    def group(self):
        return self.tr("Catchment Analysis")

    def groupId(self):
        return "catchment"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Divides the whole DEM into drainage basins, like the ArcGIS Basin tool. "
            "No outlet points are needed: each basin is the area that drains to the same "
            "outlet cell on the edge of the DEM (or next to NoData, e.g. the sea).\n\n"
            "Method: depression filling and D8-style flow routing (Priority-Flood).\n\n"
            "Tips:\n"
            "- Because every edge cell is an outlet, many tiny basins appear along the DEM "
            "border. Basins smaller than the minimum size are merged into the neighbour "
            "they share the longest border with. Set 0 or 1 to keep every basin.\n"
            "- The attributes give the outlet cell (highest flow accumulation) of each basin.\n"
            "- The optional stream network covers the whole DEM; adjust the threshold to "
            "control its density.\n"
            "- Use a projected DEM (north-up). Limit %d cells: clip or resample larger DEMs."
            % common.MAX_CELLS
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_CELLS,
                self.tr("Minimum basin size (cells, smaller basins are merged into a neighbour)"),
                type=NUM_INT,
                defaultValue=1000,
                minValue=0,
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Basins"), type=SINK_POLY))
        common.add_extra_outputs(self, self.tr)

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        min_cells = self.parameterAsInt(parameters, self.MIN_CELLS, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        dem_crs = dem_layer.crs()
        routed = common.route(feedback, arr, mask)
        if routed is None:
            return {}
        order, receiver, acc = routed

        feedback.pushInfo(self.tr("Delineating basins..."))
        labels, roots = hydro.label_basins(order, receiver, (rows, cols))
        n_raw = len(roots)
        labels, mapping = hydro.merge_small_basins(labels, min_cells)
        n = int(labels.max())
        feedback.pushInfo(self.tr("%d basins (%d before merging small ones).") % (n, n_raw))
        counts = np.bincount(labels.ravel(), minlength=n + 1)
        feedback.setProgress(75)

        # outlet of each basin = its root cell with the highest accumulation
        accf = acc.ravel()
        best = {}
        for k, root in enumerate(roots):
            new = int(mapping[k + 1])
            a = int(accf[root])
            if new not in best or a > best[new][0]:
                best[new] = (a, root)

        geoms = common.polygonize_labels(labels, gt, ds.GetProjection())
        feedback.setProgress(88)

        fields = QgsFields()
        fields.append(QgsField("basin_id", T_INT))
        fields.append(QgsField("cells", T_INT))
        fields.append(QgsField("area_km2", T_DBL))
        fields.append(QgsField("outlet_x", T_DBL))
        fields.append(QgsField("outlet_y", T_DBL))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_MULTIPOLY, dem_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        da = common.distance_area(context, dem_crs)
        for bid in range(1, n + 1):
            g = geoms.get(bid)
            if g is None:
                continue
            g.convertToMultiType()
            area = da.convertAreaMeasurement(da.measureArea(g), common.KM2)
            root = best[bid][1]
            r, c = divmod(root, cols)
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes(
                [bid, int(counts[bid]), float(area), gt[0] + (c + 0.5) * gt[1], gt[3] + (r + 0.5) * gt[5]]
            )
            sink.addFeature(f)
        results = {self.OUTPUT: dest_id}

        streams = common.write_streams(
            self, parameters, context, feedback, order, receiver, acc, None, gt, cols, dem_crs, da
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
