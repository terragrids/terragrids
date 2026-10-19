"""terragrids: population-weighted heat exposure for the areas where health data lives.

The library computes established heat-stress measures from reanalysis weather, and
will turn gridded values into one population-weighted number per administrative area.

- ``terragrids.era5`` turns ERA5's variables into the inputs the formulas need: wind
  speed, sunlight rates, the direct-beam share, and the sun's angle averaged over each
  hour.
- ``terragrids.measures`` computes the heat index, the Universal Thermal Climate Index
  (UTCI) and the wet-bulb globe temperature (WBGT), by two methods, with thermofeel,
  the library of the European Centre for Medium-Range Weather Forecasts (ECMWF).
- ``terragrids.units`` converts temperatures between kelvin, which the library uses
  throughout, and degrees Celsius and Fahrenheit.

The aggregation to areas is not part of the library yet.
"""

__version__ = "0.0.2"

__all__ = ["__version__"]
