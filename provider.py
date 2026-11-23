# -*- coding: utf-8 -*-
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import CatchmentFromDem
from .basin_algorithm import BasinsFromDem
from .contour_algorithm import ContourFromDem
from .design_storm_algorithm import DesignStorm
from .effective_rain_algorithm import EffectiveRainfall
from .hss_algorithm import RainfallFrequency
from .hydrograph_algorithm import FloodHydrograph
from .landcover_algorithm import LandCoverClassification
from .morphometry_algorithm import CatchmentMorphometry
from .rational_algorithm import RationalDischarge
from .runoff_algorithm import RunoffCoefficient
from .slope_algorithm import SlopeFromDem
from .subcatchment_algorithm import SubcatchmentsFromDem


class HydroCatchProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(CatchmentFromDem())
        self.addAlgorithm(BasinsFromDem())
        self.addAlgorithm(SubcatchmentsFromDem())
        self.addAlgorithm(CatchmentMorphometry())
        self.addAlgorithm(SlopeFromDem())
        self.addAlgorithm(ContourFromDem())
        self.addAlgorithm(RainfallFrequency())
        self.addAlgorithm(DesignStorm())
        self.addAlgorithm(LandCoverClassification())
        self.addAlgorithm(RunoffCoefficient())
        self.addAlgorithm(EffectiveRainfall())
        self.addAlgorithm(RationalDischarge())
        self.addAlgorithm(FloodHydrograph())

    def id(self):
        return "hydrocatch"

    def name(self):
        return "HydroCatch"

    def longName(self):
        return self.name()

    def icon(self):
        from .icons import icon

        return icon("hydrocatch")
