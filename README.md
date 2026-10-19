# terragrids

**Pre-release.** This package is in development, and its functions may still change
from one version to the next.

## What it does

`terragrids` measures heat exposure where health data is recorded. When complete it
will:

- compute established heat-stress measures from reanalysis weather;
- turn gridded values into one number per administrative area, weighting each grid
  cell by how many people live in it.

The first part exists now. The second is in development, and described below.

## Heat-stress measures

Three measures, each computed with thermofeel, the library of the European Centre for
Medium-Range Weather Forecasts (ECMWF):

| Measure | What it captures | Function |
|---|---|---|
| Heat index | how hot shaded air feels, given its humidity | `heat_index` |
| Universal Thermal Climate Index (UTCI) | the body's response to air, humidity, wind and radiant heat | `utci` |
| Wet-bulb globe temperature (WBGT) | the reading of the instrument used to limit work and sport in heat | `wbgt_liljegren` (the default), `wbgt_mrt` |

`terragrids.era5` turns ERA5's variables into the inputs these functions need. ERA5 is
ECMWF's reconstruction of past weather, hour by hour on a 0.25° grid. Every function
takes and returns kelvin, as ERA5 does; `terragrids.units` converts to and from degrees
Celsius and Fahrenheit.

One hour in Rome on 15 July 2025, with illustrative values for each input:

```python
import numpy as np

from terragrids.era5 import (
    accumulation_to_rate,
    cos_zenith_hourly,
    direct_fraction,
    pa_to_hpa,
    wind_speed,
)
from terragrids.measures import heat_index, utci, wbgt_liljegren, wbgt_mrt
from terragrids.units import kelvin_to_celsius

t2m, d2m = 307.15, 291.15  # air 34 °C, dewpoint 18 °C
wind = wind_speed(1.5, 1.0)  # from the eastward and northward components, m/s
sp = pa_to_hpa(101000.0)  # surface pressure
ssrd, fdir = 3.0e6, 2.4e6  # sunlight over the hour, total and direct beam, J/m²
mrt = 333.15  # mean radiant temperature, as ERA5-HEAT publishes it

# The sun's angle averaged over the hour ending 13:00 UTC, ERA5's convention.
cossza = cos_zenith_hourly(np.datetime64("2025-07-15T13:00"), 41.9, 12.5)

sun = accumulation_to_rate(ssrd)  # W/m², the mean over the hour
direct = direct_fraction(fdir, ssrd)  # share of sunlight arriving as a direct beam

print(kelvin_to_celsius(heat_index(t2m, d2m)))  # 35.1 °C
print(kelvin_to_celsius(utci(t2m, d2m, wind, mrt)))  # 40.1 °C: very strong heat stress
print(kelvin_to_celsius(wbgt_liljegren(t2m, d2m, sp, wind, sun, direct, cossza)))  # 31.0 °C
print(kelvin_to_celsius(wbgt_mrt(t2m, d2m, wind, mrt)))  # 28.3 °C
```

Each function's documentation explains how to read its values, with typical ranges
and examples. [How the measures are computed](docs/methodology/heat_stress.md) explains
the methods, how they were checked, and why the published WBGT uses Liljegren's.

## Values per area

**In development.** ERA5 comes as a grid of cells about 25 km across. Health data, such
as weekly deaths or emergency visits, is recorded per administrative area instead: a
region, a county, a city. To compare the two, the heat in each cell has to become one
value per area.

A plain average over an area's cells would count an empty mountainside as much as a
city. So the library will weight each cell by how many people live in it, taken from
the population grid of the Global Human Settlement Layer (GHS-POP), which the European
Commission's Joint Research Centre publishes. A cell where nobody lives counts for
nothing.

For example, take an area that covers two cells: a city at 36 °C, where 900,000
people live, and countryside at 30 °C, where 100,000 people live. The plain average is
33 °C. The population-weighted value is 35.4 °C, the heat that nine in ten of the
area's residents were in.

It will work with any boundaries a user supplies. Terragrids will also publish the
results for Europe as an open dataset: the three measures for every day and every
NUTS-3 region. NUTS-3 regions are the statistical areas Eurostat uses, each with
roughly 150,000 to 800,000 people.

## Licence

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
