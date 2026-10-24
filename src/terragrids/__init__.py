"""terragrids: population-weighted heat exposure for the areas where health data lives.

The library computes established heat-stress measures from reanalysis weather, and
turns gridded values into one population-weighted number per administrative area.

- ``terragrids.areas``: ``AreaWeights`` records how many of each area's people live in
  each grid cell. Applied to values on the grid, it gives each area the average over
  its people, the share of its people covered, its percentiles and the share of its
  people above a threshold.
- ``terragrids.era5`` turns ERA5's variables into the inputs the formulas need.
  ``Era5`` gives the wind speed, the sunlight rates, the direct-beam share and the
  pressure in hectopascals. ``Sun`` gives the sun's angle averaged over each hour.
- ``terragrids.measures``: ``HeatStress`` computes the heat index, the Universal
  Thermal Climate Index (UTCI) and the wet-bulb globe temperature (WBGT), by two
  methods, with thermofeel, the library of the European Centre for Medium-Range
  Weather Forecasts (ECMWF).
- ``terragrids.units``: ``Temperature`` converts temperatures between kelvin, which
  the library uses throughout, and degrees Celsius and Fahrenheit.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
