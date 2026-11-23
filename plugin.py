# -*- coding: utf-8 -*-
import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:  # Qt6
    from qgis.PyQt.QtGui import QAction

from . import icons
from .provider import HydroCatchProvider

# (submenu, [(text, algorithm id), ...])
MENU = [
    ("Catchment Analysis", [
        ("Catchment from DEM and outlet points...", "hydrocatch:catchmentfromdem"),
        ("Basins from DEM (no outlet points)...", "hydrocatch:basinsfromdem"),
        ("Sub-catchments per stream reach...", "hydrocatch:subcatchmentsfromdem"),
        ("Catchment morphometry (statistics)...", "hydrocatch:catchmentmorphometry"),
    ]),
    ("Terrain Analysis", [
        ("Slope from DEM (percent / degrees, with classes)...", "hydrocatch:slopefromdem"),
        ("Contour lines from DEM...", "hydrocatch:contourfromdem"),
    ]),
    ("Rainfall Analysis", [
        ("Rainfall frequency analysis...", "hydrocatch:rainfallfrequency"),
        ("Design storm: Mononobe intensity and distribution...", "hydrocatch:designstorm"),
        ("Effective rainfall...", "hydrocatch:effectiverainfall"),
    ]),
    ("Land Cover Analysis", [
        ("Land cover classification (supervised)...", "hydrocatch:landcoverclassification"),
        ("Runoff coefficient (C) from land cover and slope...", "hydrocatch:runoffcoefficient"),
    ]),
    ("Discharge Analysis", [
        ("Design discharge (rational method)...", "hydrocatch:rationaldischarge"),
        ("Flood hydrograph (Nakayasu, Snyder-Alexeyev)...", "hydrocatch:floodhydrograph"),
    ]),
]


class HydroCatchPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.menu = None
        self.toolbar_action = None

    def initProcessing(self):
        self.provider = HydroCatchProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = icons.icon("hydrocatch")
        self.menu = QMenu("&HydroCatch", self.iface.mainWindow())
        self.menu.setIcon(icon)
        for title, entries in MENU:
            sub = self.menu.addMenu(icons.for_group(title), title)
            for text, alg_id in entries:
                action = QAction(icons.for_algorithm(alg_id.split(":")[1]), text, sub)
                action.triggered.connect(lambda _checked=False, a=alg_id: self.run(a))
                sub.addAction(action)
        self.iface.pluginMenu().addMenu(self.menu)
        self.toolbar_action = QAction(icon, "HydroCatch: catchment from DEM", self.iface.mainWindow())
        self.toolbar_action.triggered.connect(lambda _checked=False: self.run("hydrocatch:catchmentfromdem"))
        self.iface.addToolBarIcon(self.toolbar_action)

    def run(self, alg_id):
        from qgis import processing

        processing.execAlgorithmDialog(alg_id)

    def unload(self):
        if self.menu is not None:
            self.iface.pluginMenu().removeAction(self.menu.menuAction())
            self.menu = None
        if self.toolbar_action is not None:
            self.iface.removeToolBarIcon(self.toolbar_action)
            self.toolbar_action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
