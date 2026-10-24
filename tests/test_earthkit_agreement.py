"""The hourly sun angle agrees with earthkit-meteo's.

``terragrids.era5.Sun.cos_zenith_hourly`` uses the formula of earthkit-meteo, the
meteorological library of the European Centre for Medium-Range Weather Forecasts
(ECMWF). Releases of earthkit-meteo before 1.2.1 dropped the minutes from the time of
day, so their hourly average was the value at the start of the hour. Release 1.2.1 uses
the full time of day, and from it on the two averages agree.

They do not agree exactly. Both split the hour into four parts and average each part
from three samples, but the samples sit at different points. The library uses the
standard three-point Gauss–Legendre rule, whose outer samples lie at ±√(3/5) of each
part's half-width. earthkit-meteo places them at ±√(5/9). Together with small
differences in how the two round times, this gives gaps of up to about 0.00004 in the
cosine. The tolerance below allows 0.0001, far below the formula's own error of about
0.007 against the solar-position algorithm of the US National Renewable Energy
Laboratory (NREL, ``tests/test_era5.py``).
"""

from __future__ import annotations

import datetime

import numpy as np
import pytest

from terragrids.era5 import Sun

earthkit_solar = pytest.importorskip("earthkit.meteo.solar")

AGREEMENT_TOLERANCE = 1e-4


@pytest.mark.parametrize(
    ("label", "lat", "lon", "valid_time"),
    [
        ("London, summer sunrise hour", 51.5, -0.1, "2025-06-21T04:00"),
        ("London, winter sunset hour", 51.5, -0.1, "2025-12-21T16:00"),
        ("Rome, midsummer noon", 41.9, 12.5, "2025-07-15T11:00"),
        ("Tromsø, polar day at midnight", 69.6, 19.0, "2025-06-21T23:00"),
    ],
)
def test_the_hour_average_matches_earthkit_meteo(
    label: str, lat: float, lon: float, valid_time: str
) -> None:
    # In London's sunrise hour on 21 June 2025 the sun rises at about 03:43 UTC.
    # Releases before 1.2.1 returned the value at 03:00, which is 0.
    end = datetime.datetime.fromisoformat(valid_time)
    theirs = earthkit_solar.cos_solar_zenith_angle_integrated(
        end - datetime.timedelta(hours=1),
        end,
        np.asarray(lat),
        np.asarray(lon),
        intervals_per_hour=4,
    )
    ours = Sun.cos_zenith_hourly(np.datetime64(valid_time), lat, lon)
    assert float(theirs) == pytest.approx(float(ours), abs=AGREEMENT_TOLERANCE), label
