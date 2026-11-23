# -*- coding: utf-8 -*-
"""Shared handling of the design rainfall input (table from 'Rainfall frequency analysis' or typed pairs)."""
import re

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from .hss_algorithm import FIELD_ANY, NUM_DOUBLE, SRC_TABLE, RainfallFrequency

INPUT = "INPUT"
PERIOD_FIELD = "PERIOD_FIELD"
RAIN_FIELD = "RAIN_FIELD"
GROUP = "GROUP"
DIST_FIELD = "DIST_FIELD"
DIST = "DIST"
VALUES = "VALUES"
FACTOR = "FACTOR"
STATION_FIELD = "STATION_FIELD"
STATION_VALUE = "STATION_VALUE"

DIST_NAMES = ["Normal", "Log Normal", "Gumbel", "Log Pearson III"]


def pick(names, wanted):
    low = {n.lower(): n for n in names}
    for w in wanted:
        if w in low:
            return low[w]
    return ""


def add_rain_input_params(alg, tr):
    add = alg.addParameter
    add(QgsProcessingParameterFeatureSource(INPUT, tr("Design rainfall table (output of Rainfall frequency analysis)"), [SRC_TABLE], optional=True))
    add(QgsProcessingParameterEnum(DIST, tr("Distribution to use"), options=DIST_NAMES, defaultValue=2))
    add(QgsProcessingParameterField(PERIOD_FIELD, tr("Return period field (detected automatically)"), parentLayerParameterName=INPUT, type=FIELD_ANY, optional=True))
    add(QgsProcessingParameterField(RAIN_FIELD, tr("Design rainfall field (detected automatically)"), parentLayerParameterName=INPUT, type=FIELD_ANY, optional=True))
    add(QgsProcessingParameterField(GROUP, tr("Station field in the rainfall table (detected automatically)"), parentLayerParameterName=INPUT, type=FIELD_ANY, optional=True))
    add(QgsProcessingParameterField(DIST_FIELD, tr("Distribution field in the rainfall table (detected automatically)"), parentLayerParameterName=INPUT, type=FIELD_ANY, optional=True))
    add(QgsProcessingParameterString(VALUES, tr("Or type return period = R24 pairs (used when no table is chosen)"), multiLine=True, optional=True))
    add(QgsProcessingParameterNumber(FACTOR, tr("Multiply R24 by (1.0 = no conversion)"), type=NUM_DOUBLE, defaultValue=1.0, minValue=0.1, maxValue=3.0))


def add_station_params(alg, tr, catchments_param):
    alg.addParameter(QgsProcessingParameterField(
        STATION_FIELD, tr("Station field in the catchments (optional)"), parentLayerParameterName=catchments_param, type=FIELD_ANY, optional=True))
    alg.addParameter(QgsProcessingParameterString(
        STATION_VALUE, tr("Or one station name for all catchments (optional)"), optional=True))


def read_design_rain(alg, parameters, context, feedback):
    """Returns {station: {T: R24}} (R24 not yet multiplied by the factor)."""
    source = alg.parameterAsSource(parameters, INPUT, context)
    out = {}
    if source is None:
        text = alg.parameterAsString(parameters, VALUES, context) or ""
        pairs = re.findall(r"([\d.]+)\s*[=:]\s*([\d.]+)", text)
        if not pairs:
            raise QgsProcessingException("Choose the design rainfall table or type pairs like '2=96.5, 5=127.8'.")
        out["typed values"] = {float(a): float(b) for a, b in pairs}
        return out
    names = source.fields().names()
    pf = alg.parameterAsString(parameters, PERIOD_FIELD, context) or pick(names, ("return_period", "kala_ulang", "periode", "t"))
    rf = alg.parameterAsString(parameters, RAIN_FIELD, context) or pick(names, ("rainfall", "r24", "hujan", "rain", "xt", "x_t"))
    gf = alg.parameterAsString(parameters, GROUP, context) or pick(names, ("station", "stasiun"))
    df = alg.parameterAsString(parameters, DIST_FIELD, context) or pick(names, ("distribution", "distribusi"))
    if not pf or not rf:
        raise QgsProcessingException("Could not find the return period / rainfall fields. Choose them explicitly.")
    dist_value = DIST_NAMES[alg.parameterAsEnum(parameters, DIST, context)]
    feedback.pushInfo("Design rainfall: distribution '%s'; fields return period '%s', rainfall '%s', station '%s'." % (
        dist_value, pf, rf, gf or "-"))
    feats = list(source.getFeatures())
    if df:
        found = sorted({str(f[df]) for f in feats})
        feats = [f for f in feats if str(f[df]).strip().lower() == dist_value.lower()]
        if not feats:
            raise QgsProcessingException("The table has no rows for the distribution '%s'. Available: %s" % (dist_value, ", ".join(found)))
    to_f = RainfallFrequency._to_float
    dup = 0
    for f in feats:
        t, r = to_f(f[pf]), to_f(f[rf])
        if t is None or r is None:
            continue
        key = str(f[gf]) if gf else "all data"
        d = out.setdefault(key, {})
        dup += t in d
        d[t] = r
    if dup:
        feedback.pushWarning("%d duplicate return periods found (last value used). Check the table." % dup)
    if not out:
        raise QgsProcessingException("No valid return period / rainfall pairs found.")
    return out


def station_for(feat, alg, parameters, context, stations):
    """Station name that applies to a catchment feature."""
    keys = list(stations)
    field = alg.parameterAsString(parameters, STATION_FIELD, context)
    value = (alg.parameterAsString(parameters, STATION_VALUE, context) or "").strip()
    if field:
        key = str(feat[field])
    elif value:
        key = value
    elif len(keys) == 1:
        return keys[0]
    else:
        raise QgsProcessingException(
            "The rainfall table has several stations (%s). Choose the station field of the catchments, or type one station name." % ", ".join(keys))
    if key not in stations:
        low = {k.lower(): k for k in keys}
        if key.strip().lower() in low:
            return low[key.strip().lower()]
        raise QgsProcessingException("Station '%s' is not in the rainfall table. Available: %s" % (key, ", ".join(keys)))
    return key


# --- names used by Design storm and Design discharge -----------------------------
def add_params(alg, tr):
    """Parameters of the design rainfall input (table, distribution list, fields, typed pairs, R24 factor)."""
    add_rain_input_params(alg, tr)


def read(alg, parameters, context, feedback):
    """{station: {T: R24}} with the R24 factor already applied."""
    data = read_design_rain(alg, parameters, context, feedback)
    factor = alg.parameterAsDouble(parameters, FACTOR, context)
    return {st: {t: r * factor for t, r in table.items()} for st, table in data.items()}
