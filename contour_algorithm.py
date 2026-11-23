# -*- coding: utf-8 -*-
import numpy as np
from osgeo import gdal, ogr
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, terrain
from .common import NUM_INT, T_DBL, T_INT, WKB_LINE
from .hss_algorithm import NUM_DOUBLE

SINK_LINE = common.SINK_LINE


class ContourFromDem(QgsProcessingAlgorithm):
    """Contour lines from a DEM, with index contours and optional smoothing."""

    DEM = "DEM"
    INTERVAL = "INTERVAL"
    BASE = "BASE"
    INDEX_EVERY = "INDEX_EVERY"
    SMOOTH = "SMOOTH"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("ContourFromDem", s)

    def createInstance(self):
        return ContourFromDem()

    def name(self):
        return "contourfromdem"

    def displayName(self):
        return self.tr("Contour lines from DEM")

    def group(self):
        return self.tr("Terrain Analysis")

    def groupId(self):
        return "terrain"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Creates contour lines from a DEM with the interval you choose.\n\n"
            "Fields: ELEV (elevation) and INDEX (1 for index contours, e.g. every 5th line, useful for thicker "
            "lines and labels).\n"
            "Base elevation: contours are drawn at base + n x interval (default base 0).\n"
            "Smoothing passes: repeats a 3x3 mean filter on the DEM before contouring to remove small steps "
            "(0 = none). Each pass lowers detail, so use 1 or 2 for noisy DEMs.\n\n"
            "Elevations are assumed to be in the same unit as the interval you give (usually metres)."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(
            QgsProcessingParameterNumber(self.INTERVAL, self.tr("Contour interval"), type=NUM_DOUBLE, defaultValue=10.0, minValue=0.001)
        )
        self.addParameter(
            QgsProcessingParameterNumber(self.BASE, self.tr("Base elevation"), type=NUM_DOUBLE, defaultValue=0.0)
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.INDEX_EVERY, self.tr("Index contour every N lines (0 = none)"), type=NUM_INT, defaultValue=5, minValue=0, maxValue=100
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTH, self.tr("Smoothing passes on the DEM (0 = none)"), type=NUM_INT, defaultValue=0, minValue=0, maxValue=10
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Contours"), type=SINK_LINE))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        base = self.parameterAsDouble(parameters, self.BASE, context)
        index_every = self.parameterAsInt(parameters, self.INDEX_EVERY, context)
        passes = self.parameterAsInt(parameters, self.SMOOTH, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        if passes:
            feedback.pushInfo(self.tr("Smoothing the DEM (%d pass(es))...") % passes)
            z = terrain.smooth(arr, mask, passes)
        else:
            z = np.where(mask, np.nan, arr)
        nodata = -9999.0
        z = np.where(np.isnan(z), nodata, z).astype("float32")
        feedback.setProgress(20)

        mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Float32)
        mem.SetGeoTransform(gt)
        mem.SetProjection(ds.GetProjection())
        band = mem.GetRasterBand(1)
        band.WriteArray(z)
        band.SetNoDataValue(nodata)

        drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
        vds = drv.CreateDataSource("contours")
        lyr = vds.CreateLayer("c", None, ogr.wkbLineString)
        lyr.CreateField(ogr.FieldDefn("ID", ogr.OFTInteger))
        lyr.CreateField(ogr.FieldDefn("ELEV", ogr.OFTReal))
        feedback.pushInfo(self.tr("Generating contours (interval %g, base %g)...") % (interval, base))
        try:
            err = gdal.ContourGenerate(band, interval, base, [], 1, nodata, lyr, 0, 1)
        except TypeError:
            err = gdal.ContourGenerate(band, interval, base, 0, [], 1, nodata, lyr, 0, 1)
        if err:
            raise QgsProcessingException(self.tr("GDAL could not generate the contours."))
        feedback.setProgress(70)

        fields = QgsFields()
        fields.append(QgsField("ELEV", T_DBL))
        fields.append(QgsField("INDEX", T_INT))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, WKB_LINE, dem_layer.crs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        count = 0
        for f in lyr:
            if feedback.isCanceled():
                return {}
            g = QgsGeometry.fromWkt(f.GetGeometryRef().ExportToWkt())
            if g is None or g.isNull():
                continue
            elev = f.GetField("ELEV")
            k = int(round((elev - base) / interval))
            idx = 1 if index_every > 0 and k % index_every == 0 else 0
            out = QgsFeature(fields)
            out.setGeometry(g)
            out.setAttributes([float(elev), idx])
            sink.addFeature(out)
            count += 1
        feedback.pushInfo(self.tr("%d contour lines.") % count)
        feedback.setProgress(100)
        return {self.OUTPUT: dest_id}
