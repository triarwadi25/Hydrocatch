# -*- coding: utf-8 -*-
def classFactory(iface):
    from .plugin import HydroCatchPlugin
    return HydroCatchPlugin(iface)
