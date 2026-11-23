# -*- coding: utf-8 -*-
import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, landcover
from .common import SINK_POLY, T_DBL

try:
    SRC_POLY = QgsProcessing.SourceType.VectorPolygon
except AttributeError:
    SRC_POLY = QgsProcessing.TypeVectorPolygon


class RunoffCoefficient(QgsProcessingAlgorithm):
    """Runoff coefficient C from land cover and slope (class table), and area-weighted C per catchment."""

    LANDCOVER = "LANDCOVER"
    SLOPE = "SLOPE"
    CLASSES = "CLASSES"
    BREAKS = "BREAKS"
    CATCHMENTS = "CATCHMENTS"
    OUT_RASTER = "OUT_RASTER"
    OUT_POLY = "OUT_POLY"

    def tr(self, s):
        return QCoreApplication.translate("RunoffCoefficient", s)

    def createInstance(self):
        return RunoffCoefficient()

    def name(self):
        return "runoffcoefficient"

    def displayName(self):
        return self.tr("Runoff coefficient (C) from land cover and slope")

    def group(self):
        return self.tr("Land Cover Analysis")

    def groupId(self):
        return "landcover"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Gives every pixel a runoff coefficient C from its land cover class and slope class, and the "
            "area-weighted C of each catchment.\n\n"
            "Inputs: land cover raster (class ids, e.g. from 'Land cover classification'), slope raster in PERCENT "
            "(from 'Slope from DEM', unit percent; it is resampled to the land cover grid) and the class table "
            "'id, name, C(<3%), C(3-15%), C(>15%)'. A '-' means the table has no value for that slope class: the "
            "nearest slope class of the same land cover is used (e.g. sawah/rawa on steeper land keeps 0.2).\n\n"
            "Slope breaks default to 3 and 15 percent: below 3 -> first column, 3 up to 15 -> second, 15 and "
            "above -> third.\n\n"
            "Catchment polygons (optional): adds c_weighted (mean C over the catchment pixels), area_km2 and the "
            "percentage of every land cover class (pct_<id>). Pixels of classes without a C value are left out of "
            "the average and reported as pct_noC.\n\n"
            "C is a design coefficient; check that the table matches the guideline you are following."
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        add(QgsProcessingParameterRasterLayer(self.LANDCOVER, self.tr("Land cover raster (class ids)")))
        add(QgsProcessingParameterRasterLayer(self.SLOPE, self.tr("Slope raster (percent)")))
        add(QgsProcessingParameterString(self.CLASSES, self.tr("Class table: id, name, C(<3%), C(3-15%), C(>15%)"), multiLine=True, defaultValue=landcover.DEFAULT_CLASS_TABLE))
        add(QgsProcessingParameterString(self.BREAKS, self.tr("Slope breaks (percent)"), defaultValue=landcover.DEFAULT_BREAKS))
        add(QgsProcessingParameterFeatureSource(self.CATCHMENTS, self.tr("Catchment polygons (optional)"), [SRC_POLY], optional=True))
        add(QgsProcessingParameterRasterDestination(self.OUT_RASTER, self.tr("Runoff coefficient C")))
        add(QgsProcessingParameterFeatureSink(self.OUT_POLY, self.tr("Catchments with weighted C (optional)"), type=SINK_POLY, optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        lc_layer = self.parameterAsRasterLayer(parameters, self.LANDCOVER, context)
        sl_layer = self.parameterAsRasterLayer(parameters, self.SLOPE, context)
        catch = self.parameterAsSource(parameters, self.CATCHMENTS, context)
        if lc_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.LANDCOVER))
        if sl_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.SLOPE))
        try:
            classes = landcover.parse_class_table(self.parameterAsString(parameters, self.CLASSES, context))
            breaks = landcover.parse_breaks(self.parameterAsString(parameters, self.BREAKS, context))
        except ValueError as e:
            raise QgsProcessingException(str(e))

        ds = gdal.Open(lc_layer.source())
        sds = gdal.Open(sl_layer.source())
        if ds is None or sds is None:
            raise QgsProcessingException(self.tr("Cannot open the rasters with GDAL."))
        gt = ds.GetGeoTransform()
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12 or gt[5] >= 0:
            raise QgsProcessingException(self.tr("Rotated or south-up rasters are not supported."))
        rows, cols = ds.RasterYSize, ds.RasterXSize
        lc = ds.GetRasterBand(1).ReadAsArray()
        nd = ds.GetRasterBand(1).GetNoDataValue()
        if nd is not None:
            lc = np.where(lc == nd, 0, lc)
        feedback.pushInfo(self.tr("Resampling the slope raster to the land cover grid..."))
        aligned = common.align_raster(sds, ds, "bilinear", -9999.0)
        if aligned is None:
            raise QgsProcessingException(self.tr("Could not align the slope raster to the land cover raster."))
        slope = aligned.GetRasterBand(1).ReadAsArray().astype("float32")
        slope[slope <= -9998] = np.nan
        feedback.setProgress(30)
        covered = np.isfinite(slope) & (lc > 0)
        if not covered.any():
            raise QgsProcessingException(self.tr("Land cover and slope do not overlap. Check the coordinate systems and extents."))
        unknown = sorted({int(v) for v in np.unique(lc[lc > 0])} - {c["id"] for c in classes})
        if unknown:
            feedback.pushWarning(self.tr("Land cover ids not in the class table (no C): %s") % ", ".join(str(u) for u in unknown))
        if np.nanmax(slope) <= 1.6 and np.nanmax(slope) > 0:
            feedback.pushWarning(self.tr("The slope values look very small (max %.2f). The slope raster must be in percent, not degrees or a ratio.") % float(np.nanmax(slope)))

        c = landcover.c_raster(lc, slope, classes, breaks)
        out = np.where(np.isnan(c), -9999.0, c).astype("float32")
        path = self.parameterAsOutputLayer(parameters, self.OUT_RASTER, context)
        common.write_raster(path, out, ds, gt, gdal.GDT_Float32, -9999.0)
        results = {self.OUT_RASTER: path}
        valid_c = c[np.isfinite(c)]
        feedback.pushInfo(self.tr("C range %.2f - %.2f, mean %.3f over %d pixels.") % (float(valid_c.min()), float(valid_c.max()), float(valid_c.mean()), valid_c.size))
        feedback.setProgress(60)

        if catch is not None and common.provided(parameters, self.OUT_POLY):
            img_crs = lc_layer.crs()
            xform = QgsCoordinateTransform(catch.sourceCrs(), img_crs, context.transformContext())
            fields = QgsFields()
            for f in catch.fields():
                fields.append(f)
            taken = {f.name() for f in fields}
            new_names = ["c_weighted", "area_km2"] + ["pct_%d" % cl["id"] for cl in classes] + ["pct_noC"]
            for nm in new_names:
                u, k = nm, 1
                while u in taken:
                    u = "%s_%d" % (nm, k)
                    k += 1
                taken.add(u)
                fields.append(QgsField(u, T_DBL))
            sink, dest = self.parameterAsSink(parameters, self.OUT_POLY, context, fields, catch.wkbType(), catch.sourceCrs())
            da = common.distance_area(context, catch.sourceCrs())
            total = catch.featureCount() or 1
            for n, feat in enumerate(catch.getFeatures()):
                if feedback.isCanceled():
                    return {}
                feedback.setProgress(60 + 40.0 * n / total)
                geom = feat.geometry()
                extra = [None] * len(new_names)
                if geom is not None and not geom.isNull() and not geom.isEmpty():
                    extra[1] = float(da.convertAreaMeasurement(da.measureArea(geom), common.KM2))
                    g2 = QgsGeometry(geom)
                    g2.transform(xform)
                    pm = common.polygon_mask(g2, gt, rows, cols)
                    if pm is not None:
                        r0, c0, m = pm
                        h, w = m.shape
                        lcw = lc[r0:r0 + h, c0:c0 + w][m]
                        cw = c[r0:r0 + h, c0:c0 + w][m]
                        inside = lcw > 0
                        if inside.any():
                            ok = np.isfinite(cw)
                            extra[0] = float(cw[ok].mean()) if ok.any() else None
                            n_in = float(inside.sum())
                            for i, cl in enumerate(classes):
                                extra[2 + i] = 100.0 * float((lcw == cl["id"]).sum()) / n_in
                            extra[-1] = 100.0 * float((inside & ~ok).sum()) / n_in
                out_f = QgsFeature(fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(list(feat.attributes()) + extra)
                sink.addFeature(out_f)
            results[self.OUT_POLY] = dest
        feedback.setProgress(100)
        return results
