# -*- coding: utf-8 -*-
"""Helpers to read catchment polygons (with the fields of 'Catchment morphometry') for the flood tools."""
from qgis.core import QgsProcessingParameterDefinition

from .hss_algorithm import RainfallFrequency

NAME_CANDIDATES = ("name", "nama", "catchment", "das", "outlet_id", "basin_id", "reach_id", "id", "fid")


def pick(names, wanted):
    low = {n.lower(): n for n in names}
    for w in wanted:
        if w in low:
            return low[w]
    return ""


def advanced(param):
    try:
        flag = QgsProcessingParameterDefinition.Flag.Advanced
    except AttributeError:
        flag = QgsProcessingParameterDefinition.FlagAdvanced
    param.setFlags(param.flags() | flag)
    return param


def number(feat, field):
    """Float value of a feature field (None if the field is empty / not a number)."""
    if not field:
        return None
    return RainfallFrequency._to_float(feat[field])


def label(feat, field):
    if field:
        v = feat[field]
        if v is not None and str(v) != "NULL" and str(v).strip() != "":
            return str(v)
    return "C%d" % feat.id()
