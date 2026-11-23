# HydroCatch (QGIS plugin)

Processing Toolbox > HydroCatch (also in the Plugins menu):

**Catchment Analysis**
- Catchment from DEM and outlet points - watershed upstream of each outlet (ArcGIS Watershed + Snap Pour Point).
- Basins from DEM (no outlet points) - all drainage basins of the DEM (ArcGIS Basin).
- Sub-catchments per stream reach - one polygon per stream reach (Arc Hydro Catchment Polygon).
- Catchment morphometry - area, relief, slope, longest flow path, Tc (Kirpich), drainage density, shape indices.

**Terrain Analysis**
- Slope from DEM - percent or degrees, manual or automatic classes (default: SK Mentan 837/1980 classes).
- Contour lines from DEM - interval, base, index contours, optional smoothing.

**Rainfall Analysis**
- Rainfall frequency analysis - mean, SD, Cv, Cs, Ck; Normal, Log Normal, Gumbel, Log Pearson III; Chi-Square and Smirnov-Kolmogorov tests; design rainfall for chosen return periods; HTML report with charts.
- Design storm - Mononobe intensity for durations 0.25-12 h and rainfall distribution per 30 minutes over 6 h; the distribution is chosen from a list.
- Effective rainfall - effective rainfall per catchment from the runoff coefficient.

**Land Cover Analysis**
- Land cover classification (supervised) - maximum likelihood / minimum distance on a satellite image with training samples; accuracy assessment.
- Runoff coefficient (C) from land cover and slope - per-pixel C and area-weighted C per catchment.

**Discharge Analysis**
- Design discharge (rational method) - Q = 0.2778 C I A per catchment and return period.
- Flood hydrograph - Nakayasu and Snyder-Alexeyev synthetic unit hydrographs (QA, Qd1, Qd2, Qd3, ordinates) and flood hydrographs per return period; river length from a catchment field (e.g. lfp_km) or typed in the tool.

Optional outputs of the catchment tools: stream network (Strahler), flow accumulation raster,
flow direction raster (ESRI D8: 1=E 2=SE 4=S 8=SW 16=W 32=NW 64=N 128=NE, NoData 255).
