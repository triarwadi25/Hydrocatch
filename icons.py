# -*- coding: utf-8 -*-
"""Icons of the plugin, of every algorithm and of the menu groups."""
import os

from qgis.PyQt.QtGui import QIcon

_DIR = os.path.join(os.path.dirname(__file__), "icons")

ALGORITHM_ICONS = {
    "catchmentfromdem": "catchment_outlet",
    "basinsfromdem": "basins",
    "subcatchmentsfromdem": "subcatchments",
    "catchmentmorphometry": "morphometry",
    "slopefromdem": "slope",
    "contourfromdem": "contour",
    "rainfallfrequency": "rainfall_frequency",
    "designstorm": "design_storm",
    "effectiverainfall": "effective_rainfall",
    "landcoverclassification": "landcover",
    "runoffcoefficient": "runoff",
    "rationaldischarge": "rational",
    "floodhydrograph": "hydrograph",
}

GROUP_ICONS = {
    "Catchment Analysis": "catchment_outlet",
    "Terrain Analysis": "contour",
    "Rainfall Analysis": "design_storm",
    "Land Cover Analysis": "landcover",
    "Discharge Analysis": "hydrograph",
}


def icon(name):
    return QIcon(os.path.join(_DIR, name + ".svg"))


def for_algorithm(alg_name):
    return icon(ALGORITHM_ICONS.get(alg_name, "hydrocatch"))


def for_group(title):
    return icon(GROUP_ICONS.get(title, "hydrocatch"))
