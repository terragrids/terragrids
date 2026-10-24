"""ERA5 input preparation: unit conversions, the direct-beam share and the sun's angle.

The sun's angle is checked against pvlib's implementation of the Solar Position
Algorithm of the US National Renewable Energy Laboratory (NREL; Reda and Andreas, 2004),
an independent reference accurate to a fraction of a thousandth of a degree. Our formula
is the faster approximation the European Centre for Medium-Range Weather Forecasts
(ECMWF) uses, so it is held to that approximation's accuracy, not to NREL's.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib
import pytest

from terragrids.era5 import Era5, Sun

# The approximation's measured error against NREL is about 0.007 in the cosine, the
# equivalent of the sun being about 0.4° out when it is high. The tolerances leave a
# small margin above that.
INSTANT_TOLERANCE = 0.008
HOURLY_TOLERANCE = 0.008
# Error of the hour average against a one-second average of the same formula.
QUADRATURE_TOLERANCE = 0.0005


def nrel_cos_zenith(times: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    """NREL's cosine of the geometric zenith angle, clipped at 0 like ours."""
    position = pvlib.solarposition.get_solarposition(times, lat, lon, method="nrel_numpy")
    return np.clip(np.cos(np.deg2rad(position["zenith"].to_numpy())), 0.0, None)


def random_cases(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Random UTC times over 2000–2030 and places between 80° S and 80° N."""
    rng = np.random.default_rng(seed)
    start = np.datetime64("2000-01-01T00:00:00")
    span = int((np.datetime64("2031-01-01T00:00:00") - start) / np.timedelta64(1, "s"))
    times = start + rng.integers(0, span, n).astype("timedelta64[s]")
    return times, rng.uniform(-80, 80, n), rng.uniform(-180, 180, n)


def test_wind_speed_combines_the_two_components() -> None:
    assert Era5.wind_speed(3.0, 4.0) == pytest.approx(5.0)
    np.testing.assert_allclose(Era5.wind_speed([0.0, -6.0], [0.0, 8.0]), [0.0, 10.0])


def test_an_hourly_accumulation_becomes_a_mean_rate() -> None:
    # 3.6 MJ/m² over an hour is 1,000 joules every second.
    assert Era5.accumulation_to_rate(3.6e6) == pytest.approx(1000.0)
    assert Era5.accumulation_to_rate(1800.0, seconds=1800.0) == pytest.approx(1.0)


def test_pressure_converts_from_pascals_to_hectopascals() -> None:
    assert Era5.pa_to_hpa(101325.0) == pytest.approx(1013.25)


def test_the_direct_share_is_bounded_and_zero_without_sun() -> None:
    np.testing.assert_allclose(
        Era5.direct_fraction([300.0, 0.0, 0.0, 1001.0, -1.0], [1000.0, 0.0, 500.0, 1000.0, 100.0]),
        [0.3, 0.0, 0.0, 1.0, 0.0],
    )


def test_the_direct_share_keeps_missing_values_missing() -> None:
    assert np.isnan(Era5.direct_fraction(np.nan, 100.0))
    assert np.isnan(Era5.direct_fraction(10.0, np.nan))


def test_the_sun_angle_matches_nrel_at_any_instant() -> None:
    times, lat, lon = random_cases(500, seed=0)
    ours = Sun.cos_zenith(times, lat, lon)
    reference = np.array(
        [
            nrel_cos_zenith(pd.DatetimeIndex([pd.Timestamp(t, tz="UTC")]), a, o)[0]
            for t, a, o in zip(times, lat, lon, strict=True)
        ]
    )
    np.testing.assert_allclose(ours, reference, atol=INSTANT_TOLERANCE)


@pytest.mark.parametrize(
    ("label", "lat", "lon", "valid_time"),
    [
        ("London, summer sunrise hour", 51.5, -0.1, "2025-06-21T04:00"),
        ("London, winter sunset hour", 51.5, -0.1, "2025-12-21T16:00"),
        ("Rome, midsummer noon", 41.9, 12.5, "2025-07-15T11:00"),
        ("Seville, autumn sunrise hour", 37.4, -6.0, "2025-10-01T07:00"),
        ("Tromsø, polar day at midnight", 69.6, 19.0, "2025-06-21T23:00"),
        ("Tromsø, polar night at noon", 69.6, 19.0, "2025-12-21T11:00"),
    ],
)
def test_the_hour_average_matches_nrel(label: str, lat: float, lon: float, valid_time: str) -> None:
    end = pd.Timestamp(valid_time, tz="UTC")
    # One sample every minute across the hour ending at the stamp, at mid-minute.
    minutes = pd.date_range(end - pd.Timedelta(seconds=3570), periods=60, freq="1min")
    reference = nrel_cos_zenith(minutes, lat, lon).mean()
    ours = Sun.cos_zenith_hourly(np.datetime64(valid_time), lat, lon)
    assert ours == pytest.approx(reference, abs=HOURLY_TOLERANCE), label


def test_the_hour_average_is_the_average_not_a_single_instant() -> None:
    # In London's sunrise hour on midsummer day the sun rises at about 03:43 UTC, so
    # the hour ending 04:00 is mostly dark. The value at the start of the hour is 0,
    # the value at its end is positive, and the average lies between them.
    valid_time = np.datetime64("2025-06-21T04:00:00")
    start = Sun.cos_zenith(valid_time - np.timedelta64(1, "h"), 51.5, -0.1)
    end = Sun.cos_zenith(valid_time, 51.5, -0.1)
    average = Sun.cos_zenith_hourly(valid_time, 51.5, -0.1)
    assert start == 0.0
    assert 0.0 < average < end


def test_the_hour_average_quadrature_is_accurate() -> None:
    times, lat, lon = random_cases(200, seed=1)
    hours = times.astype("datetime64[h]").astype("datetime64[s]")
    seconds = np.arange(-3599, 1).astype("timedelta64[s]")
    fine = np.array(
        [Sun.cos_zenith(t + seconds, a, o).mean() for t, a, o in zip(hours, lat, lon, strict=True)]
    )
    np.testing.assert_allclose(
        Sun.cos_zenith_hourly(hours, lat, lon), fine, atol=QUADRATURE_TOLERANCE
    )


def test_the_sun_angle_broadcasts_one_time_over_a_grid() -> None:
    lat, lon = np.meshgrid(np.linspace(34, 72, 5), np.linspace(-25, 45, 7), indexing="ij")
    result = Sun.cos_zenith_hourly(np.datetime64("2025-07-15T12:00"), lat, lon)
    assert result.shape == (5, 7)
    assert np.all((result >= 0.0) & (result <= 1.0))


def test_at_least_one_subinterval_is_required() -> None:
    with pytest.raises(ValueError):
        Sun.cos_zenith_hourly(np.datetime64("2025-07-15T12:00"), 0.0, 0.0, subintervals=0)
