# -*- coding: utf-8 -*-
import numpy as np
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication
from osgeo import gdal

from . import common, terrain
from .common import NUM_INT, SINK_POLY, T_DBL, T_INT, T_STR, WKB_MULTIPOLY


class SlopeFromDem(QgsProcessingAlgorithm):
    """Slope raster (percent or degrees) with optional automatic / manual classes."""

    DEM = "DEM"
    UNIT = "UNIT"
    MODE = "MODE"
    BREAKS = "BREAKS"
    NAMES = "NAMES"
    N_CLASSES = "N_CLASSES"
    SLOPE = "SLOPE"
    CLASSES = "CLASSES"
    POLYGONS = "POLYGONS"

    UNITS = ["percent", "degrees"]
    MODES = ["Manual breaks", "Automatic: equal interval", "Automatic: quantile", "No classification (slope only)"]

    def tr(self, s):
        return QCoreApplication.translate("SlopeFromDem", s)

    def createInstance(self):
        return SlopeFromDem()

    def name(self):
        return "slopefromdem"

    def displayName(self):
        return self.tr("Slope from DEM (percent / degrees, with classes)")

    def group(self):
        return self.tr("Terrain Analysis")

    def groupId(self):
        return "terrain"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Computes slope from a DEM with Horn's method (same as ArcGIS Slope) in percent or degrees "
            "and optionally classifies it.\n\n"
            "Classification:\n"
            "- Manual breaks: upper limits of the classes, in the unit you chose, e.g. '8, 15, 25, 45' "
            "gives 0-8, 8-15, 15-25, 25-45 and >45 (lower limit included). Leave empty to use the "
            "standard Indonesian classes (SK Mentan 837/1980: 0-8, 8-15, 15-25, 25-45, >45 %; "
            "converted to degrees when the unit is degrees). Optional class names, comma separated "
            "(one per class), are used in manual mode only.\n"
            "- Automatic: equal interval or quantile with the number of classes you give.\n\n"
            "Outputs: slope raster, classified raster (class ids, NoData 0) and class polygons with "
            "area per class. A summary table is also written to the log.\n\n"
            "Notes: elevations are assumed to be in metres. For a geographic DEM (degrees) the cell size is "
            "converted to metres per row."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(
            QgsProcessingParameterEnum(self.UNIT, self.tr("Slope unit"), options=["Percent (%)", "Degrees (°)"], defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterEnum(self.MODE, self.tr("Classification"), options=self.MODES, defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.BREAKS,
                self.tr("Manual class breaks (upper limits, empty = standard Indonesian classes)"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.NAMES,
                self.tr("Class names for manual mode (optional, comma separated)"),
                defaultValue="Datar, Landai, Agak curam, Curam, Sangat curam",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.N_CLASSES,
                self.tr("Number of classes (automatic modes)"),
                type=NUM_INT,
                defaultValue=5,
                minValue=2,
                maxValue=30,
            )
        )
        self.addParameter(QgsProcessingParameterRasterDestination(self.SLOPE, self.tr("Slope")))
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.CLASSES, self.tr("Classified slope (optional)"), optional=True, createByDefault=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.POLYGONS, self.tr("Slope class polygons (optional)"), type=SINK_POLY, optional=True, createByDefault=False
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))
        unit = self.UNITS[self.parameterAsEnum(parameters, self.UNIT, context)]
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        n_classes = self.parameterAsInt(parameters, self.N_CLASSES, context)
        symbol = "%" if unit == "percent" else "°"

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        dem_crs = dem_layer.crs()
        dx, dy = common.cell_size_m(gt, rows, dem_crs)
        feedback.pushInfo(self.tr("Computing slope (%s)...") % unit)
        slope = terrain.slope(arr, mask, dx, dy, unit)
        feedback.setProgress(40)

        results = {}
        slope_path = self.parameterAsOutputLayer(parameters, self.SLOPE, context)
        out = np.where(np.isnan(slope), -9999.0, slope).astype("float32")
        common.write_raster(slope_path, out, ds, gt, gdal.GDT_Float32, -9999.0)
        results[self.SLOPE] = slope_path
        valid = slope[np.isfinite(slope)]
        feedback.pushInfo(
            self.tr("Slope range: %.2f - %.2f %s, mean %.2f %s")
            % (float(valid.min()), float(valid.max()), symbol, float(valid.mean()), symbol)
        )

        if mode == 3:
            return results

        # ---- breaks -------------------------------------------------------------
        names = None
        try:
            if mode == 0:
                text = self.parameterAsString(parameters, self.BREAKS, context)
                breaks = terrain.parse_breaks(text) if text and text.strip() else terrain.default_breaks(unit)
                nm = self.parameterAsString(parameters, self.NAMES, context)
                names = [t.strip() for t in nm.split(",")] if nm and nm.strip() else None
                if names and len(names) != len(breaks) + 1:
                    feedback.pushWarning(
                        self.tr("%d class names given for %d classes: names ignored.") % (len(names), len(breaks) + 1)
                    )
                    names = None
            else:
                breaks = terrain.auto_breaks(valid, n_classes, "equal" if mode == 1 else "quantile")
                if not breaks:
                    raise QgsProcessingException(self.tr("Could not compute automatic breaks (flat DEM?)."))
        except ValueError as e:
            raise QgsProcessingException(str(e))
        if len(breaks) > 250:
            raise QgsProcessingException(self.tr("Too many classes (maximum 250)."))
        labels = terrain.class_labels(breaks, names)
        feedback.pushInfo(self.tr("Class breaks (%s): %s") % (symbol, ", ".join("%g" % b for b in breaks)))

        classes = terrain.classify(slope, breaks)
        counts = np.bincount(classes.ravel(), minlength=len(labels) + 1)
        total = int(counts[1:].sum())
        feedback.pushInfo(self.tr("Class summary:"))
        for k, lb in enumerate(labels, 1):
            feedback.pushInfo("  %d  %-28s %10d cells  %6.2f %%" % (k, lb, counts[k], 100.0 * counts[k] / max(total, 1)))
        feedback.setProgress(60)

        if common.provided(parameters, self.CLASSES):
            cpath = self.parameterAsOutputLayer(parameters, self.CLASSES, context)
            common.write_raster(cpath, classes.astype("uint8") if len(labels) < 255 else classes, ds, gt, gdal.GDT_Byte, 0)
            results[self.CLASSES] = cpath

        if common.provided(parameters, self.POLYGONS):
            fields = QgsFields()
            fields.append(QgsField("class_id", T_INT))
            fields.append(QgsField("label", T_STR))
            fields.append(QgsField("unit", T_STR))
            fields.append(QgsField("min_val", T_DBL))
            fields.append(QgsField("max_val", T_DBL))
            fields.append(QgsField("cells", T_INT))
            fields.append(QgsField("area_km2", T_DBL))
            fields.append(QgsField("area_ha", T_DBL))
            fields.append(QgsField("pct_area", T_DBL))
            sink, dest = self.parameterAsSink(parameters, self.POLYGONS, context, fields, WKB_MULTIPOLY, dem_crs)
            geoms = common.polygonize_labels(classes.astype("int32"), gt, ds.GetProjection())
            da = common.distance_area(context, dem_crs)
            areas = {}
            for k, g in geoms.items():
                g.convertToMultiType()
                areas[k] = da.convertAreaMeasurement(da.measureArea(g), common.KM2)
            tot_area = sum(areas.values()) or 1.0
            lows = [0.0] + list(breaks)
            for k in range(1, len(labels) + 1):
                g = geoms.get(k)
                if g is None:
                    continue
                f = QgsFeature(fields)
                f.setGeometry(g)
                up = float(breaks[k - 1]) if k <= len(breaks) else None
                f.setAttributes(
                    [k, labels[k - 1], symbol, float(lows[k - 1]), up, int(counts[k]), float(areas[k]),
                     float(areas[k] * 100.0), float(100.0 * areas[k] / tot_area)]
                )
                sink.addFeature(f)
            results[self.POLYGONS] = dest
            for k in range(1, len(labels) + 1):
                if k in areas:
                    feedback.pushInfo("  %-28s %12.3f km2  %8.1f ha" % (labels[k - 1], areas[k], areas[k] * 100))
        feedback.setProgress(100)
        return results
