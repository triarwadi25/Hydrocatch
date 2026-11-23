# -*- coding: utf-8 -*-
"""Helpers shared by the algorithms."""
import math

import numpy as np
from osgeo import gdal, ogr
from qgis.core import (
    Qgis,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsUnitTypes,
    QgsWkbTypes,
)

from . import hydro

# --- Qt5 / Qt6 (QGIS 3 / 4) compatibility ----------------------------------------
try:
    from qgis.PyQt.QtCore import QMetaType

    T_INT, T_DBL, T_STR = QMetaType.Type.Int, QMetaType.Type.Double, QMetaType.Type.QString
    QgsField("t", T_INT)
except Exception:
    from qgis.PyQt.QtCore import QVariant

    T_INT, T_DBL, T_STR = QVariant.Int, QVariant.Double, QVariant.String

try:
    SRC_POINT = QgsProcessing.SourceType.VectorPoint
    SINK_POLY = QgsProcessing.SourceType.VectorPolygon
    SINK_LINE = QgsProcessing.SourceType.VectorLine
except AttributeError:
    SRC_POINT = QgsProcessing.TypeVectorPoint
    SINK_POLY = QgsProcessing.TypeVectorPolygon
    SINK_LINE = QgsProcessing.TypeVectorLine

try:
    SRC_ANY = QgsProcessing.SourceType.VectorAnyGeometry
except AttributeError:
    SRC_ANY = QgsProcessing.TypeVectorAnyGeometry

try:
    GEOM_POLYGON = Qgis.GeometryType.Polygon
    GEOM_POINT = Qgis.GeometryType.Point
except AttributeError:
    GEOM_POLYGON = QgsWkbTypes.PolygonGeometry
    GEOM_POINT = QgsWkbTypes.PointGeometry

try:
    NUM_INT = QgsProcessingParameterNumber.Type.Integer
except AttributeError:
    NUM_INT = QgsProcessingParameterNumber.Integer

try:
    WKB_MULTIPOLY = Qgis.WkbType.MultiPolygon
    WKB_LINE = Qgis.WkbType.LineString
except AttributeError:
    WKB_MULTIPOLY = QgsWkbTypes.MultiPolygon
    WKB_LINE = QgsWkbTypes.LineString

try:
    KM2 = Qgis.AreaUnit.SquareKilometers
except AttributeError:
    KM2 = QgsUnitTypes.AreaSquareKilometers
try:
    KM = Qgis.DistanceUnit.Kilometers
    METERS = Qgis.DistanceUnit.Meters
except AttributeError:
    KM = QgsUnitTypes.DistanceKilometers
    METERS = QgsUnitTypes.DistanceMeters

MAX_CELLS = 16_000_000
STREAM_THRESHOLD = "STREAM_THRESHOLD"
STREAMS = "STREAMS"
ACC = "ACCUMULATION"
FDIR = "FLOW_DIRECTION"


def provided(parameters, name):
    v = parameters.get(name)
    return v is not None and v != ""


def add_extra_outputs(alg, tr):
    """Optional outputs shared by all algorithms: stream network + flow accumulation."""
    alg.addParameter(
        QgsProcessingParameterNumber(
            STREAM_THRESHOLD,
            tr("Stream threshold: minimum contributing cells (used for the stream network)"),
            type=NUM_INT,
            defaultValue=1000,
            minValue=1,
        )
    )
    alg.addParameter(
        QgsProcessingParameterFeatureSink(
            STREAMS, tr("Stream network (optional)"), type=SINK_LINE, optional=True, createByDefault=False
        )
    )
    alg.addParameter(
        QgsProcessingParameterRasterDestination(
            ACC, tr("Flow accumulation (optional)"), optional=True, createByDefault=False
        )
    )
    alg.addParameter(
        QgsProcessingParameterRasterDestination(
            FDIR,
            tr("Flow direction, ESRI D8 codes 1/2/4/8/16/32/64/128 (optional)"),
            optional=True,
            createByDefault=False,
        )
    )


def open_dem(alg, dem_layer, feedback):
    """Open the DEM and return (ds, gt, rows, cols, array, nodata_mask)."""
    if dem_layer is None:
        raise QgsProcessingException("Invalid DEM layer.")
    ds = gdal.Open(dem_layer.source())
    if ds is None:
        raise QgsProcessingException("Cannot open the DEM with GDAL.")
    gt = ds.GetGeoTransform()
    if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12 or gt[5] >= 0:
        raise QgsProcessingException("Rotated or south-up rasters are not supported. Reproject / warp the DEM first.")
    cols, rows = ds.RasterXSize, ds.RasterYSize
    if rows * cols > MAX_CELLS:
        raise QgsProcessingException(
            "DEM has %d cells (limit %d). Clip or resample the DEM first." % (rows * cols, MAX_CELLS)
        )
    feedback.pushInfo("Reading DEM (%d x %d)..." % (cols, rows))
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype("float64")
    nodata = band.GetNoDataValue()
    mask = ~np.isfinite(arr)
    if nodata is not None and np.isfinite(nodata):
        mask |= arr == nodata
    if mask.all():
        raise QgsProcessingException("The DEM contains no valid cells.")
    return ds, gt, rows, cols, arr, mask


def route(feedback, arr, mask):
    """Flood-fill + flow accumulation. Returns (order, receiver, acc) or None if canceled."""
    feedback.pushInfo("Filling depressions and routing flow...")
    try:
        order, receiver = hydro.priority_flood(
            arr, mask, is_canceled=feedback.isCanceled, progress=lambda p: feedback.setProgress(p * 0.6)
        )
    except hydro.Canceled:
        return None
    feedback.pushInfo("Computing flow accumulation...")
    acc = hydro.flow_accumulation(order, receiver, arr.shape, mask)
    feedback.setProgress(65)
    return order, receiver, acc


def distance_area(context, crs):
    da = QgsDistanceArea()
    da.setSourceCrs(crs, context.transformContext())
    ell = context.ellipsoid()
    if not ell or ell == "NONE":
        ell = crs.ellipsoidAcronym()
    da.setEllipsoid(ell)
    return da


def polygonize_labels(labels, gt, projection):
    """Label raster -> {label: dissolved QgsGeometry}."""
    rows, cols = labels.shape
    mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Int32)
    mem.SetGeoTransform(gt)
    mem.SetProjection(projection)
    mb = mem.GetRasterBand(1)
    mb.WriteArray(labels)
    mb.SetNoDataValue(0)
    drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
    vds = drv.CreateDataSource("catchments")
    lyr = vds.CreateLayer("c", None, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("lab", ogr.OFTInteger))
    gdal.Polygonize(mb, mb, lyr, 0, ["8CONNECTED=8"], callback=None)
    parts = {}
    for f in lyr:
        g = QgsGeometry.fromWkt(f.GetGeometryRef().ExportToWkt())
        if g and not g.isNull():
            parts.setdefault(f.GetField(0), []).append(g)
    return {k: QgsGeometry.unaryUnion(v) for k, v in parts.items()}


def write_streams(alg, parameters, context, feedback, order, receiver, acc, restrict, gt, cols, crs, da):
    """Write the optional stream network; returns the sink id or None."""
    if not provided(parameters, STREAMS):
        return None
    threshold = alg.parameterAsInt(parameters, STREAM_THRESHOLD, context)
    feedback.pushInfo("Extracting stream network (threshold %d cells)..." % threshold)
    reaches = hydro.stream_reaches(order, receiver, acc, threshold, restrict)
    fields = QgsFields()
    fields.append(QgsField("reach_id", T_INT))
    fields.append(QgsField("strahler", T_INT))
    fields.append(QgsField("acc_cells", T_INT))
    fields.append(QgsField("length_km", T_DBL))
    fields.append(QgsField("down_id", T_INT))
    sink, dest = alg.parameterAsSink(parameters, STREAMS, context, fields, WKB_LINE, crs)
    if sink is None:
        return None
    if not reaches:
        feedback.reportError("No cell reaches the stream threshold; the stream network is empty. Lower the threshold.", False)
    down = hydro.reach_downstream_ids(reaches)
    for rid, (path, so, a) in enumerate(reaches, 1):
        if feedback.isCanceled():
            break
        if len(path) < 2:
            continue
        pts = [
            QgsPointXY(gt[0] + ((p % cols) + 0.5) * gt[1], gt[3] + ((p // cols) + 0.5) * gt[5]) for p in path
        ]
        g = QgsGeometry.fromPolylineXY(pts)
        length = da.convertLengthMeasurement(da.measureLength(g), KM)
        f = QgsFeature(fields)
        f.setGeometry(g)
        f.setAttributes([rid, so, a, float(length), down[rid - 1]])
        sink.addFeature(f)
    return dest


def write_acc_raster(alg, parameters, context, acc, ds, gt):
    if not provided(parameters, ACC):
        return None
    path = alg.parameterAsOutputLayer(parameters, ACC, context)
    rows, cols = acc.shape
    out = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Float32)
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.WriteArray(acc.astype("float32"))
    ob.SetNoDataValue(0)
    ob.FlushCache()
    out = None
    return path


def write_dir_raster(alg, parameters, context, receiver, mask, ds, gt):
    """Optional D8 flow direction raster (Byte, ESRI codes, NoData 255)."""
    if not provided(parameters, FDIR):
        return None
    path = alg.parameterAsOutputLayer(parameters, FDIR, context)
    rows, cols = mask.shape
    fd = hydro.flow_direction(receiver, (rows, cols), mask)
    out = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Byte)
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.WriteArray(fd)
    ob.SetNoDataValue(hydro.FDIR_NODATA)
    ob.FlushCache()
    out = None
    return path


def cell_size_m(gt, rows, crs):
    """Cell width per row (array, metres) and cell height (metres). Handles geographic CRS."""
    if crs.isGeographic():
        lat = gt[3] + (np.arange(rows) + 0.5) * gt[5]
        dx = abs(gt[1]) * 111320.0 * np.cos(np.radians(lat))
        dy = abs(gt[5]) * 110574.0
        return dx, dy
    f = 1.0
    try:
        f = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), METERS)
    except Exception:
        pass
    return np.full(rows, abs(gt[1]) * f), abs(gt[5]) * f


def polygon_mask(geom, gt, rows, cols):
    """Rasterize a polygon (in DEM CRS) onto the DEM grid.
    Returns (row0, col0, bool window) for the cells whose centre is inside, or None."""
    bb = geom.boundingBox()
    c0 = max(0, int(math.floor((bb.xMinimum() - gt[0]) / gt[1])))
    c1 = min(cols, int(math.ceil((bb.xMaximum() - gt[0]) / gt[1])))
    r0 = max(0, int(math.floor((bb.yMaximum() - gt[3]) / gt[5])))
    r1 = min(rows, int(math.ceil((bb.yMinimum() - gt[3]) / gt[5])))
    if c1 <= c0 or r1 <= r0:
        return None
    w, h = c1 - c0, r1 - r0
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
    mem.SetGeoTransform((gt[0] + c0 * gt[1], gt[1], 0, gt[3] + r0 * gt[5], 0, gt[5]))
    drv = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
    vds = drv.CreateDataSource("poly")
    lyr = vds.CreateLayer("p", None, ogr.wkbUnknown)
    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(ogr.CreateGeometryFromWkb(bytes(geom.asWkb())))
    lyr.CreateFeature(feat)
    band = mem.GetRasterBand(1)
    for opts in (["ALL_TOUCHED=FALSE"], ["ALL_TOUCHED=TRUE"]):  # tiny polygons: fall back to touched cells
        band.Fill(0)
        gdal.RasterizeLayer(mem, [1], lyr, burn_values=[1], options=opts)
        m = band.ReadAsArray() > 0
        if m.any():
            return r0, c0, m
    return None


def write_raster(path, array, ds, gt, gdal_type, nodata):
    rows, cols = array.shape
    out = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal_type)
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.WriteArray(array)
    ob.SetNoDataValue(nodata)
    ob.FlushCache()
    out = None


def align_raster(src_ds, ref_ds, resample="bilinear", nodata=-9999.0):
    """Resample/reproject src_ds onto the grid of ref_ds (in-memory result)."""
    gt = ref_ds.GetGeoTransform()
    cols, rows = ref_ds.RasterXSize, ref_ds.RasterYSize
    minx, maxy = gt[0], gt[3]
    maxx, miny = minx + gt[1] * cols, maxy + gt[5] * rows
    return gdal.Warp(
        "", src_ds, format="MEM", width=cols, height=rows, outputBounds=(minx, miny, maxx, maxy),
        dstSRS=ref_ds.GetProjection(), resampleAlg=resample, dstNodata=nodata, outputType=gdal.GDT_Float32,
    )
