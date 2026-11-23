# -*- coding: utf-8 -*-
import numpy as np
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
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, hydro, morpho, terrain
from .common import NUM_INT, SINK_POLY, T_DBL, T_INT

try:
    SRC_POLY = QgsProcessing.SourceType.VectorPolygon
except AttributeError:
    SRC_POLY = QgsProcessing.TypeVectorPolygon

# (field name, type, key in morpho.cell_stats result)
STAT_FIELDS = [
    ("area_km2", T_DBL, None),
    ("perim_km", T_DBL, None),
    ("elev_min", T_DBL, "elev_min"),
    ("elev_max", T_DBL, "elev_max"),
    ("elev_mean", T_DBL, "elev_mean"),
    ("relief_m", T_DBL, "relief_m"),
    ("slope_pct", T_DBL, "slope_pct"),
    ("hyps_int", T_DBL, "hyps_int"),
    ("lfp_km", T_DBL, "lfp_km"),
    ("lfp_slope", T_DBL, "lfp_slope"),
    ("tc_min", T_DBL, "tc_min"),
    ("stream_km", T_DBL, "stream_km"),
    ("drain_dens", T_DBL, "drain_dens"),
    ("max_order", T_INT, "max_order"),
    ("n_streams", T_INT, "n_streams"),
    ("rb_mean", T_DBL, "rb_mean"),
    ("form_fac", T_DBL, "form_fac"),
    ("elong_rat", T_DBL, "elong_rat"),
    ("circ_rat", T_DBL, "circ_rat"),
    ("compact", T_DBL, "compact"),
    ("relief_rat", T_DBL, "relief_rat"),
    ("lmain_km", T_DBL, "lmain_km"),
    ("smain", T_DBL, "smain"),
    ("lc_km", T_DBL, "lc_km"),
    ("sf", T_DBL, "sf"),
    ("sn", T_DBL, "sn"),
    ("jn", T_INT, "jn"),
    ("wf", T_DBL, "wf"),
    ("rua", T_DBL, "rua"),
    ("sim", T_DBL, "sim"),
]


class CatchmentMorphometry(QgsProcessingAlgorithm):
    """Morphometric / hydrological statistics for catchment polygons."""

    DEM = "DEM"
    POLYGONS = "POLYGONS"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("CatchmentMorphometry", s)

    def createInstance(self):
        return CatchmentMorphometry()

    def name(self):
        return "catchmentmorphometry"

    def displayName(self):
        return self.tr("Catchment morphometry (statistics)")

    def group(self):
        return self.tr("Catchment Analysis")

    def groupId(self):
        return "catchment"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Adds morphometric and hydrological statistics to catchment polygons (for example the output of "
            "'Catchment from DEM and outlet points' or 'Basins from DEM').\n\n"
            "The outlet of each polygon is its cell with the highest flow accumulation; the longest flow path is "
            "traced inside the polygon from the farthest cell that drains to that outlet.\n\n"
            "Fields added:\n"
            "area_km2, perim_km - area and perimeter\n"
            "elev_min / elev_max / elev_mean, relief_m - elevation (m) and relief\n"
            "slope_pct - mean slope in percent (Horn)\n"
            "hyps_int - hypsometric integral (mean - min) / (max - min)\n"
            "lfp_km, lfp_slope - longest flow path length and its slope (m/m)\n"
            "tc_min - time of concentration, Kirpich: 0.0195 L^0.77 S^-0.385 (min; L in m, S in m/m)\n"
            "stream_km, drain_dens - stream length and drainage density (km/km2) for the stream threshold below\n"
            "max_order, n_streams, rb_mean - Strahler order, number of streams, mean bifurcation ratio\n"
            "form_fac - form factor A/L^2; elong_rat - elongation ratio 1.128 sqrt(A)/L\n"
            "circ_rat - circularity ratio 4 pi A/P^2; compact - Gravelius compactness 0.2821 P/sqrt(A)\n"
            "relief_rat - relief ratio H/L\n"
            "lmain_km, smain - main stream length (km) and slope (m/m): from the outlet upstream along the stream with the "
            "largest accumulation (the longest flow path is used when there is no stream)\n"
            "lc_km - distance along the main stream from the outlet to the point nearest the centroid (Snyder Lc)\n"
            "sf, sn, jn, wf, rua, sim - HSS Gama I descriptors: source factor, source frequency, number of junctions, "
            "width factor, relative upstream area, symmetry factor. They depend on the stream threshold, and wf / rua / "
            "lc_km are approximated from the flow routing (not measured with perpendicular lines on a map).\n\n"
            "Polygons may overlap (cumulative catchments are fine). Elevations are assumed to be in metres. "
            "Polygons that do not cover any DEM cell centre get empty values."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM (elevation raster)")))
        self.addParameter(
            QgsProcessingParameterFeatureSource(self.POLYGONS, self.tr("Catchment polygons"), [SRC_POLY])
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                common.STREAM_THRESHOLD,
                self.tr("Stream threshold: minimum contributing cells (for drainage density and orders)"),
                type=NUM_INT,
                defaultValue=1000,
                minValue=1,
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, self.tr("Catchments with statistics"), type=SINK_POLY))

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        source = self.parameterAsSource(parameters, self.POLYGONS, context)
        threshold = self.parameterAsInt(parameters, common.STREAM_THRESHOLD, context)
        if dem_layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.DEM))
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.POLYGONS))

        ds, gt, rows, cols, arr, mask = common.open_dem(self, dem_layer, feedback)
        dem_crs = dem_layer.crs()
        routed = common.route(feedback, arr, mask)
        if routed is None:
            return {}
        order, receiver, acc = routed

        feedback.pushInfo(self.tr("Preparing slope, flow lengths and streams..."))
        dx, dy = common.cell_size_m(gt, rows, dem_crs)
        slope = terrain.slope(arr, mask, dx, dy, "percent").ravel().astype("float64")
        FL, step = hydro.flow_length(order, receiver, (rows, cols), dx, dy)
        info = {}
        reaches = hydro.stream_reaches(order, receiver, acc, threshold, None, None, info)
        accf = acc.ravel()
        starts = np.array([p[0] for p, _, _ in reaches], dtype=np.int64)
        orders = np.array([o for _, o, _ in reaches], dtype=np.int64)
        newflag = np.array(info["new_stream"], dtype=bool)
        t = morpho.Terrain(
            arr.ravel(), accf, receiver, order, FL, step, accf >= threshold, slope, starts, orders, newflag,
            cell_order=np.array(info["cell_order"], dtype=np.int64), inflow=np.array(info["inflow"], dtype=np.int64),
            cols=cols, dx=dx, dy=dy,
        )
        mask_flat = mask.ravel()
        feedback.setProgress(60)

        # ---- output fields: keep input fields, add statistics (unique names) ---------
        fields = QgsFields()
        for f in source.fields():
            fields.append(f)
        taken = {f.name() for f in fields}
        out_names = {}
        for name, typ, key in STAT_FIELDS:
            nm = name
            k = 1
            while nm in taken:
                nm = "%s_%d" % (name, k)
                k += 1
            taken.add(nm)
            fields.append(QgsField(nm, typ))
            out_names[name] = nm
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields, source.wkbType(), source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        xform = QgsCoordinateTransform(source.sourceCrs(), dem_crs, context.transformContext())
        da = common.distance_area(context, source.sourceCrs())
        total = source.featureCount() or 1
        for n, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                return {}
            feedback.setProgress(60 + 40.0 * n / total)
            geom = feat.geometry()
            extra = [None] * len(STAT_FIELDS)
            if geom is not None and not geom.isNull() and not geom.isEmpty():
                area_km2 = da.convertAreaMeasurement(da.measureArea(geom), common.KM2)
                perim_km = da.convertLengthMeasurement(da.measurePerimeter(geom), common.KM)
                g2 = QgsGeometry(geom)
                g2.transform(xform)
                pm = common.polygon_mask(g2, gt, rows, cols)
                stats = None
                if pm is not None:
                    r0, c0, m = pm
                    rr, cc = np.nonzero(m)
                    flat = (rr + r0).astype(np.int64) * cols + (cc + c0)
                    flat = flat[~mask_flat[flat]]
                    if flat.size:
                        stats = morpho.cell_stats(flat, t, float(area_km2), float(perim_km))
                if stats is None:
                    feedback.reportError(
                        self.tr("Feature %s does not cover any valid DEM cell; statistics left empty.") % feat.id(), False
                    )
                    extra[0], extra[1] = float(area_km2), float(perim_km)
                else:
                    stats["area_km2"], stats["perim_km"] = float(area_km2), float(perim_km)
                    extra = [stats.get(key or name) for name, _, key in STAT_FIELDS]
            out = QgsFeature(fields)
            out.setGeometry(geom)
            out.setAttributes(list(feat.attributes()) + extra)
            sink.addFeature(out)
        feedback.setProgress(100)
        return {self.OUTPUT: dest_id}
