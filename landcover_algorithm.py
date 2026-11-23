# -*- coding: utf-8 -*-
import math

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import common, landcover
from .common import GEOM_POINT, GEOM_POLYGON, NUM_INT, SINK_POLY, SRC_ANY, T_DBL, T_INT, T_STR, WKB_MULTIPOLY
from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, WKB_NONE

MAX_PIXEL_FEATURES = 80_000_000   # cells x bands
MAX_SAMPLES_PER_CLASS = 50000
MIN_PIXELS = 5


class LandCoverClassification(QgsProcessingAlgorithm):
    """Supervised land cover classification of a satellite image (RGB or multi-band)."""

    IMAGE = "IMAGE"
    TRAIN = "TRAIN"
    CLASS_FIELD = "CLASS_FIELD"
    CLASSES = "CLASSES"
    METHOD = "METHOD"
    VARI = "VARI"
    TEXTURE = "TEXTURE"
    RED_BAND = "RED_BAND"
    NIR_BAND = "NIR_BAND"
    MAJORITY = "MAJORITY"
    MIN_SIZE = "MIN_SIZE"
    VALIDATION = "VALIDATION"
    VAL_FIELD = "VAL_FIELD"
    HOLDOUT = "HOLDOUT"
    OUT_RASTER = "OUT_RASTER"
    OUT_POLY = "OUT_POLY"
    OUT_ACC = "OUT_ACC"

    METHODS = ["Maximum likelihood", "Minimum distance"]
    TEXTURES = ["None", "3 x 3", "5 x 5", "7 x 7"]

    def tr(self, s):
        return QCoreApplication.translate("LandCoverClassification", s)

    def createInstance(self):
        return LandCoverClassification()

    def name(self):
        return "landcoverclassification"

    def displayName(self):
        return self.tr("Land cover classification (supervised)")

    def group(self):
        return self.tr("Land Cover Analysis")

    def groupId(self):
        return "landcover"

    def icon(self):
        from .icons import for_algorithm

        return for_algorithm(self.name())

    def shortHelpString(self):
        return self.tr(
            "Classifies a satellite image (e.g. a Sentinel-2 RGB composite) into land cover classes using training "
            "samples you digitise.\n\n"
            "Training samples: a polygon (best) or point layer with a field holding the class id (1, 2, ...) or the "
            "class name from the class table. Digitise 10+ polygons per class, spread over the area, on clearly "
            "identifiable pixels.\n\n"
            "Class table: one line per class, 'id, name, C(<3%), C(3-15%), C(>15%)'. Only id and name are used here; "
            "the C values are used by 'Runoff coefficient from land cover and slope'. The default table is the "
            "runoff coefficient table by land cover and slope.\n\n"
            "RGB-only images separate vegetation classes poorly. To help, the tool can add a visible-band greenness "
            "index (VARI, bands 1-3 = R, G, B), local texture (forest is rougher than paddy fields) and, if the image has "
            "a near-infrared band, NDVI (give the red and NIR band numbers). An image with NIR (B8) gives clearly better "
            "results than RGB alone.\n\n"
            "Methods: Maximum likelihood (default) or Minimum distance. Post-processing: 3x3 majority filter passes and "
            "an optional minimum mapping unit (cells).\n\n"
            "Accuracy: give a validation layer (independent points or polygons) for the best estimate. Without one, a "
            "share of the training features is held out (by feature, not by pixel). Output: overall accuracy, Kappa and "
            "per-class producer's/user's accuracy.\n\n"
            "Black pixels (0, 0, 0) or the NoData / alpha band are treated as outside the image. Clip the image to your "
            "study area first (limit: %d cells x bands)." % MAX_PIXEL_FEATURES
        )

    def initAlgorithm(self, config=None):
        add = self.addParameter
        add(QgsProcessingParameterRasterLayer(self.IMAGE, self.tr("Satellite image (RGB or multi-band)")))
        add(QgsProcessingParameterFeatureSource(self.TRAIN, self.tr("Training samples (polygons or points)"), [SRC_ANY]))
        add(QgsProcessingParameterField(self.CLASS_FIELD, self.tr("Class field (class id or class name)"), parentLayerParameterName=self.TRAIN, type=FIELD_ANY))
        add(QgsProcessingParameterString(self.CLASSES, self.tr("Class table: id, name, C(<3%), C(3-15%), C(>15%)"), multiLine=True, defaultValue=landcover.DEFAULT_CLASS_TABLE))
        add(QgsProcessingParameterEnum(self.METHOD, self.tr("Classification method"), options=self.METHODS, defaultValue=0))
        add(QgsProcessingParameterBoolean(self.VARI, self.tr("Add VARI greenness index (bands 1-3 = R, G, B)"), defaultValue=True))
        add(QgsProcessingParameterEnum(self.TEXTURE, self.tr("Add local texture (window)"), options=self.TEXTURES, defaultValue=1))
        add(QgsProcessingParameterNumber(self.RED_BAND, self.tr("Red band number (for NDVI)"), type=NUM_INT, defaultValue=1, minValue=1, maxValue=50))
        add(QgsProcessingParameterNumber(self.NIR_BAND, self.tr("NIR band number for NDVI (0 = not used)"), type=NUM_INT, defaultValue=0, minValue=0, maxValue=50))
        add(QgsProcessingParameterNumber(self.MAJORITY, self.tr("Majority filter passes (0 = none)"), type=NUM_INT, defaultValue=1, minValue=0, maxValue=5))
        add(QgsProcessingParameterNumber(self.MIN_SIZE, self.tr("Minimum mapping unit (cells, 0 = none)"), type=NUM_INT, defaultValue=0, minValue=0, maxValue=100000))
        add(QgsProcessingParameterFeatureSource(self.VALIDATION, self.tr("Validation samples (optional)"), [SRC_ANY], optional=True))
        add(QgsProcessingParameterField(self.VAL_FIELD, self.tr("Validation class field (default: same name as the class field)"), parentLayerParameterName=self.VALIDATION, type=FIELD_ANY, optional=True))
        add(QgsProcessingParameterNumber(self.HOLDOUT, self.tr("Share of training features held out for accuracy when no validation layer is given"), type=NUM_DOUBLE, defaultValue=0.3, minValue=0.0, maxValue=0.5))
        add(QgsProcessingParameterRasterDestination(self.OUT_RASTER, self.tr("Land cover (class ids)")))
        add(QgsProcessingParameterFeatureSink(self.OUT_POLY, self.tr("Land cover polygons (optional)"), type=SINK_POLY, optional=True, createByDefault=False))
        add(QgsProcessingParameterFeatureSink(self.OUT_ACC, self.tr("Accuracy table (optional)"), type=SRC_TABLE, optional=True, createByDefault=False))

    # ------------------------------------------------------------------
    def _class_of(self, value, ids, names):
        try:
            v = int(float(value))
            if v in ids:
                return v
        except (TypeError, ValueError):
            pass
        return names.get(str(value).strip().lower())

    def _collect(self, source, field, ids, names, xform, gt, rows, cols, valid_flat, feedback, what):
        """List of (class_id, flat pixel indices) for every sample feature."""
        out, skipped = [], 0
        for feat in source.getFeatures():
            if feedback.isCanceled():
                break
            cid = self._class_of(feat[field], ids, names)
            geom = feat.geometry()
            if cid is None or geom is None or geom.isNull() or geom.isEmpty():
                skipped += 1
                continue
            g2 = QgsGeometry(geom)
            g2.transform(xform)
            if g2.type() == GEOM_POLYGON:
                pm = common.polygon_mask(g2, gt, rows, cols)
                if pm is None:
                    skipped += 1
                    continue
                r0, c0, m = pm
                rr, cc = np.nonzero(m)
                flat = (rr + r0).astype(np.int64) * cols + (cc + c0)
            elif g2.type() == GEOM_POINT:
                pt = g2.centroid().asPoint()
                c = int(math.floor((pt.x() - gt[0]) / gt[1]))
                r = int(math.floor((pt.y() - gt[3]) / gt[5]))
                if not (0 <= r < rows and 0 <= c < cols):
                    skipped += 1
                    continue
                flat = np.array([r * cols + c], dtype=np.int64)
            else:
                skipped += 1
                continue
            flat = flat[valid_flat[flat]]
            if flat.size:
                out.append((cid, flat))
            else:
                skipped += 1
        if skipped:
            feedback.pushWarning(self.tr("%d %s features were skipped (unknown class, outside the image or no valid pixels).") % (skipped, what))
        return out

    @staticmethod
    def _cap(idx, rng):
        return idx if len(idx) <= MAX_SAMPLES_PER_CLASS else rng.choice(idx, MAX_SAMPLES_PER_CLASS, replace=False)

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.IMAGE, context)
        train_src = self.parameterAsSource(parameters, self.TRAIN, context)
        field = self.parameterAsString(parameters, self.CLASS_FIELD, context)
        val_src = self.parameterAsSource(parameters, self.VALIDATION, context)
        val_field = self.parameterAsString(parameters, self.VAL_FIELD, context) or field
        method = "ml" if self.parameterAsEnum(parameters, self.METHOD, context) == 0 else "md"
        use_vari = self.parameterAsBoolean(parameters, self.VARI, context)
        tex = [0, 3, 5, 7][self.parameterAsEnum(parameters, self.TEXTURE, context)]
        red = self.parameterAsInt(parameters, self.RED_BAND, context)
        nir = self.parameterAsInt(parameters, self.NIR_BAND, context)
        majority = self.parameterAsInt(parameters, self.MAJORITY, context)
        min_size = self.parameterAsInt(parameters, self.MIN_SIZE, context)
        holdout = self.parameterAsDouble(parameters, self.HOLDOUT, context)
        if layer is None:
            raise QgsProcessingException(self.invalidRasterError(parameters, self.IMAGE))
        if train_src is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.TRAIN))
        try:
            classes = landcover.parse_class_table(self.parameterAsString(parameters, self.CLASSES, context))
        except ValueError as e:
            raise QgsProcessingException(str(e))
        ids = {c["id"] for c in classes}
        cname = {c["id"]: c["name"] for c in classes}
        names = {c["name"].strip().lower(): c["id"] for c in classes}

        # ---- image ------------------------------------------------------------
        ds = gdal.Open(layer.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Cannot open the image with GDAL."))
        gt = ds.GetGeoTransform()
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12 or gt[5] >= 0:
            raise QgsProcessingException(self.tr("Rotated or south-up rasters are not supported. Reproject / warp the image first."))
        rows, cols = ds.RasterYSize, ds.RasterXSize
        alpha = None
        data_bands = []
        for i in range(1, ds.RasterCount + 1):
            b = ds.GetRasterBand(i)
            if b.GetColorInterpretation() == gdal.GCI_AlphaBand:
                alpha = i
            else:
                data_bands.append(i)
        if not data_bands:
            raise QgsProcessingException(self.tr("The image has no data bands."))
        if rows * cols * (len(data_bands) + 3) > MAX_PIXEL_FEATURES:
            raise QgsProcessingException(self.tr("The image is too large (%d x %d, %d bands). Clip it to the study area first.") % (cols, rows, len(data_bands)))
        feedback.pushInfo(self.tr("Reading the image (%d x %d, %d bands)...") % (cols, rows, len(data_bands)))
        bands = np.stack([ds.GetRasterBand(i).ReadAsArray().astype("float32") for i in data_bands])
        nodata = ds.GetRasterBand(data_bands[0]).GetNoDataValue()
        valid = np.isfinite(bands).all(axis=0)
        if nodata is not None:
            valid &= ~(bands == nodata).any(axis=0)
        else:
            valid &= (bands != 0).any(axis=0)
        if alpha:
            valid &= ds.GetRasterBand(alpha).ReadAsArray() > 0
        if not valid.any():
            raise QgsProcessingException(self.tr("The image contains no valid pixels."))
        if use_vari and len(data_bands) < 3:
            use_vari = False
        if nir and nir > len(data_bands):
            raise QgsProcessingException(self.tr("NIR band %d does not exist (the image has %d bands).") % (nir, len(data_bands)))
        feats = landcover.build_features(bands, vari=use_vari, texture_window=tex, red=red, nir=nir)
        del bands
        nf = feats.shape[0]
        feedback.pushInfo(self.tr("%d features per pixel (bands%s%s%s).") % (
            nf, " + VARI" if use_vari else "", " + NDVI" if nir else "", " + texture" if tex else ""))
        X = feats.reshape(nf, -1).T
        valid_flat = valid.ravel()
        vidx = np.flatnonzero(valid_flat)
        sub = vidx[:: max(1, len(vidx) // 200000)]
        mean, std = landcover.standardize_params(X[sub].astype("float64"))
        feedback.setProgress(15)

        # ---- samples ------------------------------------------------------------
        img_crs = layer.crs()
        xf_train = QgsCoordinateTransform(train_src.sourceCrs(), img_crs, context.transformContext())
        train_feats = self._collect(train_src, field, ids, names, xf_train, gt, rows, cols, valid_flat, feedback, self.tr("training"))
        if not train_feats:
            raise QgsProcessingException(self.tr("No usable training samples. Check the class field and that the samples lie inside the image."))
        rng = np.random.default_rng(1)
        val_feats = []
        if val_src is not None:
            xf_val = QgsCoordinateTransform(val_src.sourceCrs(), img_crs, context.transformContext())
            if val_src.fields().lookupField(val_field) < 0:
                raise QgsProcessingException(self.tr("The validation layer has no field '%s'.") % val_field)
            val_feats = self._collect(val_src, val_field, ids, names, xf_val, gt, rows, cols, valid_flat, feedback, self.tr("validation"))
        elif holdout > 0:
            step = max(2, int(round(1.0 / holdout)))
            by_class = {}
            for cid, flat in train_feats:
                by_class.setdefault(cid, []).append(flat)
            train_feats, no_holdout = [], []
            for cid, lst in by_class.items():
                n_pix = len(np.unique(np.concatenate(lst)))
                if len(lst) >= 3 and n_pix >= 50:
                    for n, flat in enumerate(lst):
                        (val_feats if n % step == step - 1 else train_feats).append((cid, flat))
                else:
                    train_feats.extend((cid, f) for f in lst)
                    no_holdout.append(cname[cid])
            if no_holdout:
                feedback.pushWarning(self.tr(
                    "Too few samples to hold out any for accuracy: %s (need 3+ features and 50+ pixels). "
                    "Their accuracy is not assessed unless you give a validation layer.") % ", ".join(no_holdout))
        per = {}
        for cid, flat in train_feats:
            per.setdefault(cid, []).append(flat)
        tr_idx = {cid: self._cap(np.unique(np.concatenate(lst)), rng) for cid, lst in per.items()}
        feedback.pushInfo(self.tr("Training samples used per class (features / pixels):"))
        for c in classes:
            nfeat = len(per.get(c["id"], []))
            npix = len(tr_idx.get(c["id"], []))
            feedback.pushInfo("  %d  %-40s %5d features  %7d pixels" % (c["id"], c["name"], nfeat, npix))
        too_few = [(cname[c], len(per[c]), len(i)) for c, i in tr_idx.items() if len(i) < MIN_PIXELS]
        if too_few:
            raise QgsProcessingException(self.tr(
                "Too few training pixels (minimum %d per class): %s. Typical causes: point samples (1 pixel each; use "
                "polygons or 30+ points per class), very small polygons, samples partly outside the image or on black / "
                "NoData pixels, or a hold-out that took part of a small sample (set the hold-out share to 0 and use a "
                "validation layer instead).") % (
                MIN_PIXELS, "; ".join("%s (%d features, %d pixels)" % t for t in too_few)))
        weak = [cname[c] for c, i in tr_idx.items() if MIN_PIXELS <= len(i) < max(30, 5 * nf)]
        if weak:
            feedback.pushWarning(self.tr(
                "Few training pixels for: %s. The result for these classes will be unreliable; add more or larger samples.") % ", ".join(weak))
        missing = [c["name"] for c in classes if c["id"] not in tr_idx]
        if missing:
            feedback.pushWarning(self.tr("No training samples for: %s. These classes will not appear in the result.") % ", ".join(missing))
        if len(tr_idx) < 2:
            raise QgsProcessingException(self.tr("At least two classes with training samples are needed."))
        Xtr = np.concatenate([X[i] for i in tr_idx.values()]).astype("float64")
        ytr = np.concatenate([np.full(len(i), c) for c, i in tr_idx.items()])
        feedback.pushInfo(self.tr("Training %s on %d pixels, %d classes...") % (self.METHODS[0 if method == "ml" else 1], len(ytr), len(tr_idx)))
        model = landcover.train((Xtr - mean) / std, ytr, method)

        # ---- classify -----------------------------------------------------------
        feedback.pushInfo(self.tr("Classifying %d pixels...") % len(vidx))
        labels = np.zeros(rows * cols, dtype="uint8")
        chunk = 400000
        for s in range(0, len(vidx), chunk):
            if feedback.isCanceled():
                return {}
            sel = vidx[s:s + chunk]
            labels[sel] = model.predict((X[sel].astype("float64") - mean) / std)
            feedback.setProgress(15 + 55.0 * (s + chunk) / len(vidx))
        del X, feats
        labels = labels.reshape(rows, cols)
        if majority:
            labels = landcover.majority_filter(labels, majority)
        if min_size > 1:
            try:
                mem_in = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
                mem_in.GetRasterBand(1).WriteArray(labels)
                mem_out = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
                gdal.SieveFilter(mem_in.GetRasterBand(1), None, mem_out.GetRasterBand(1), min_size, 8)
                labels = mem_out.GetRasterBand(1).ReadAsArray()
            except Exception as e:  # noqa: BLE001
                feedback.pushWarning(self.tr("Minimum mapping unit filter failed (%s); skipped.") % e)
        labels[~valid] = 0
        labels = labels.astype("uint8")
        feedback.setProgress(75)

        # ---- summary --------------------------------------------------------------
        dx, dy = common.cell_size_m(gt, rows, img_crs)
        feedback.pushInfo(self.tr("Class summary:"))
        total_cells = int((labels > 0).sum())
        counts = {}
        for c in classes:
            m = labels == c["id"]
            n = int(m.sum())
            counts[c["id"]] = n
            if n:
                ha = float((m.sum(axis=1) * dx).sum() * dy / 1e4)
                feedback.pushInfo("  %d  %-40s %10d cells  %10.1f ha  %6.2f %%" % (c["id"], c["name"], n, ha, 100.0 * n / max(total_cells, 1)))

        results = {}
        path = self.parameterAsOutputLayer(parameters, self.OUT_RASTER, context)
        common.write_raster(path, labels, ds, gt, gdal.GDT_Byte, 0)
        results[self.OUT_RASTER] = path

        # ---- accuracy ---------------------------------------------------------------
        if val_feats:
            lab_flat = labels.ravel()
            refs, preds = [], []
            vper = {}
            for cid, flat in val_feats:
                vper.setdefault(cid, []).append(flat)
            for cid, lst in vper.items():
                idx = self._cap(np.unique(np.concatenate(lst)), rng)
                refs.append(np.full(len(idx), cid))
                preds.append(lab_flat[idx])
            ref, pred = np.concatenate(refs), np.concatenate(preds)
            cids = sorted(set(tr_idx) | set(vper))
            cm = landcover.confusion(ref, pred, cids)
            if cm["oa"] is None:
                raise QgsProcessingException(self.tr("No validation pixel fell on a classified pixel. Check the validation samples."))
            src_txt = self.tr("validation layer") if val_src is not None else self.tr("held-out training features")
            feedback.pushInfo(self.tr("Accuracy (%s, %d pixels): overall %.1f %%, Kappa %.3f") % (src_txt, cm["n"], 100 * cm["oa"], cm["kappa"]))
            for i, cid in enumerate(cids):
                pa, ua = cm["producers"][i], cm["users"][i]
                feedback.pushInfo("  %-40s producer %s  user %s" % (cname[cid], "-" if pa is None else "%.1f %%" % (100 * pa), "-" if ua is None else "%.1f %%" % (100 * ua)))
            if val_src is None:
                feedback.pushWarning(self.tr("This estimate comes from held-out training polygons. Use an independent validation layer for a reliable accuracy."))
            if common.provided(parameters, self.OUT_ACC):
                f_acc = QgsFields()
                f_acc.append(QgsField("class_id", T_INT))
                f_acc.append(QgsField("name", T_STR))
                for nm in ("n_ref", "n_pred", "n_correct"):
                    f_acc.append(QgsField(nm, T_INT))
                for nm in ("producers_acc", "users_acc", "overall_acc", "kappa"):
                    f_acc.append(QgsField(nm, T_DBL))
                sink, dest = self.parameterAsSink(parameters, self.OUT_ACC, context, f_acc, WKB_NONE, QgsCoordinateReferenceSystem())
                mx = cm["matrix"]
                for i, cid in enumerate(cids):
                    f = QgsFeature(f_acc)
                    f.setAttributes([cid, cname.get(cid, str(cid)), int(mx[i].sum()), int(mx[:, i].sum()), int(mx[i, i]),
                                     cm["producers"][i], cm["users"][i], cm["oa"], cm["kappa"]])
                    sink.addFeature(f)
                results[self.OUT_ACC] = dest
        else:
            feedback.pushWarning(self.tr("No validation samples: accuracy was not assessed."))

        # ---- polygons ---------------------------------------------------------------
        if common.provided(parameters, self.OUT_POLY):
            geoms = common.polygonize_labels(labels.astype("int32"), gt, ds.GetProjection())
            fields = QgsFields()
            fields.append(QgsField("class_id", T_INT))
            fields.append(QgsField("name", T_STR))
            fields.append(QgsField("cells", T_INT))
            fields.append(QgsField("area_km2", T_DBL))
            fields.append(QgsField("area_ha", T_DBL))
            fields.append(QgsField("pct_area", T_DBL))
            sink, dest = self.parameterAsSink(parameters, self.OUT_POLY, context, fields, WKB_MULTIPOLY, img_crs)
            da = common.distance_area(context, img_crs)
            areas = {}
            for k, g in geoms.items():
                g.convertToMultiType()
                areas[k] = da.convertAreaMeasurement(da.measureArea(g), common.KM2)
            tot = sum(areas.values()) or 1.0
            for k, g in geoms.items():
                f = QgsFeature(fields)
                f.setGeometry(g)
                f.setAttributes([int(k), cname.get(int(k), str(k)), counts.get(int(k), 0), float(areas[k]), float(areas[k] * 100.0), float(100.0 * areas[k] / tot)])
                sink.addFeature(f)
            results[self.OUT_POLY] = dest
        feedback.setProgress(100)
        return results
