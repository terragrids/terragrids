"""Turn ERA5 variables into the inputs the heat-stress formulas expect.

ERA5 is the reanalysis of the European Centre for Medium-Range Weather Forecasts
(ECMWF): a reconstruction of past weather on a regular grid, hour by hour. It does not
publish every quantity the heat-stress formulas need in the form they need it:

- wind comes as two components, eastward (``u10``) and northward (``v10``), not a speed;
- sunlight comes as energy accumulated over the hour (J/m²), not as a rate (W/m²);
- the direct-beam share of sunlight must be derived from two accumulated fields;
- surface pressure comes in pascals, where the formulas take hectopascals;
- the sun's position is not published at all.

:class:`Era5` converts the first four, and :class:`Sun` computes the sun's position.
Every method takes and returns numpy arrays and broadcasts its inputs.

**Accumulation convention.** An ERA5 hourly accumulation stamped with time ``T`` covers
the hour ending at ``T``, from ``T - 1 h`` to ``T``. Sunlight measured over that hour
must be paired with the sun's position averaged over the same hour, which is what
:meth:`Sun.cos_zenith_hourly` returns.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

SECONDS_PER_HOUR = 3600.0


class Era5:
    """Conversions of ERA5's variables into the forms the heat-stress formulas take."""

    @staticmethod
    def wind_speed(u10: ArrayLike, v10: ArrayLike) -> NDArray[np.float64]:
        """Wind speed at 10 m (m/s) from its eastward and northward components (m/s)."""
        eastward = np.asarray(u10, dtype=float)
        northward = np.asarray(v10, dtype=float)
        speed: NDArray[np.float64] = np.hypot(eastward, northward)
        return speed

    @staticmethod
    def accumulation_to_rate(
        accumulated: ArrayLike, seconds: float = SECONDS_PER_HOUR
    ) -> NDArray[np.float64]:
        """Mean rate (W/m²) over a period, from the energy accumulated in it (J/m²).

        A watt is one joule per second, so the mean rate is the accumulated energy divided
        by the period's length in seconds. ERA5's hourly fields accumulate over one hour.
        """
        return np.asarray(accumulated, dtype=float) / seconds

    @staticmethod
    def direct_fraction(fdir: ArrayLike, ssrd: ArrayLike) -> NDArray[np.float64]:
        """The share of surface sunlight that arrives as a direct beam, from 0 to 1.

        ``fdir`` is ERA5's total-sky direct solar radiation at the surface and ``ssrd`` its
        surface solar radiation downwards, both accumulated over the same hour and in the
        same units. Where there is no sunlight (``ssrd`` is 0 or below) the share is 0. The
        result is clipped to [0, 1], because the two fields are computed separately and
        rounding can push their ratio slightly outside it. NaN inputs give NaN.
        """
        direct = np.asarray(fdir, dtype=float)
        total = np.asarray(ssrd, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.clip(direct / total, 0.0, 1.0)
        result: NDArray[np.float64] = np.where(total > 0.0, ratio, 0.0)
        return np.where(np.isnan(direct) | np.isnan(total), np.nan, result)

    @staticmethod
    def pa_to_hpa(pressure: ArrayLike) -> NDArray[np.float64]:
        """Pressure in hectopascals from pressure in pascals."""
        return np.asarray(pressure, dtype=float) / 100.0


class Sun:
    """The sun's angle from overhead, at an instant and averaged over an hour."""

    # Gauss–Legendre nodes and weights for three points on [-1, 1].
    _GAUSS_NODES = np.array([-np.sqrt(3.0 / 5.0), 0.0, np.sqrt(3.0 / 5.0)])
    _GAUSS_WEIGHTS = np.array([5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0])

    @staticmethod
    def cos_zenith(time: ArrayLike, lat: ArrayLike, lon: ArrayLike) -> NDArray[np.float64]:
        """Cosine of the solar zenith angle at an instant, clipped at 0.

        The solar zenith angle is the angle between the sun and the point directly
        overhead. Its cosine is 1 with the sun overhead and 0 at sunrise and sunset. Values
        below 0 (the sun below the horizon) are set to 0, since no sunlight arrives then.

        ``time`` is in Coordinated Universal Time (UTC), as ``numpy.datetime64`` values or
        anything numpy converts to them. ``lat`` and ``lon`` are in degrees, longitude
        positive eastward. The three inputs broadcast against each other.

        The formula is the one earthkit-meteo, the meteorological library of the European
        Centre for Medium-Range Weather Forecasts (ECMWF), uses (Hogan and Hirahara, 2016,
        https://doi.org/10.1002/2015GL066868): a Fourier approximation of the sun's
        declination and the equation of time, from the fraction of the year elapsed. It
        uses the time of day to the second. Releases of earthkit-meteo before 1.2.1 used the
        whole hour only, which made their hourly average equal to the value at the start of
        the hour. From 1.2.1 the two agree, and a test checks it.
        """
        t = np.asarray(time, dtype="datetime64[s]")
        year_start = t.astype("datetime64[Y]").astype("datetime64[s]")
        day_start = t.astype("datetime64[D]").astype("datetime64[s]")
        day_of_year = (t - year_start).astype(float) / 86400.0
        hour_of_day = (t - day_start).astype(float) / 3600.0

        angle = 2.0 * np.pi * day_of_year / 365.25
        declination_deg = (
            0.396372
            - 22.91327 * np.cos(angle)
            + 4.025430 * np.sin(angle)
            - 0.387205 * np.cos(2 * angle)
            + 0.051967 * np.sin(2 * angle)
            - 0.154527 * np.cos(3 * angle)
            + 0.084798 * np.sin(3 * angle)
        )
        # The equation of time, as an hour-angle correction in degrees.
        time_correction_deg = (
            0.004297
            + 0.107029 * np.cos(angle)
            - 1.837877 * np.sin(angle)
            - 0.837378 * np.cos(2 * angle)
            - 2.340475 * np.sin(2 * angle)
        )

        declination = np.deg2rad(declination_deg)
        latitude = np.deg2rad(np.asarray(lat, dtype=float))
        hour_angle = np.deg2rad(
            (hour_of_day - 12.0) * 15.0 + np.asarray(lon, dtype=float) + time_correction_deg
        )
        cosine = np.sin(declination) * np.sin(latitude) + np.cos(declination) * np.cos(
            latitude
        ) * np.cos(hour_angle)
        clipped: NDArray[np.float64] = np.clip(cosine, 0.0, None)
        return clipped

    @classmethod
    def cos_zenith_hourly(
        cls,
        valid_time: ArrayLike,
        lat: ArrayLike,
        lon: ArrayLike,
        subintervals: int = 4,
    ) -> NDArray[np.float64]:
        """Cosine of the solar zenith angle averaged over the hour ending at ``valid_time``.

        This is the sun's position that matches an ERA5 hourly accumulation stamped
        ``valid_time``. The average is not the value at mid-hour: around sunrise and sunset
        the sun is up for only part of the hour, and the clipped cosine bends at the
        horizon.

        The hour is split into ``subintervals`` equal parts, and each part is averaged with
        three-point Gauss–Legendre quadrature: a standard rule that samples three points and
        is exact for any polynomial up to degree five. The clipped cosine is not smooth at
        the horizon, so splitting the hour keeps the error small in the part that contains
        sunrise or sunset. Inputs are as for :meth:`cos_zenith`.
        """
        if subintervals < 1:
            raise ValueError("subintervals must be at least 1")
        end = np.asarray(valid_time, dtype="datetime64[s]")
        part = SECONDS_PER_HOUR / subintervals
        total: NDArray[np.float64] = np.zeros(
            np.broadcast_shapes(end.shape, np.shape(lat), np.shape(lon))
        )
        for k in range(subintervals):
            midpoint = -SECONDS_PER_HOUR + (k + 0.5) * part
            for node, weight in zip(cls._GAUSS_NODES, cls._GAUSS_WEIGHTS, strict=True):
                offset = np.timedelta64(round((midpoint + node * part / 2.0) * 1e3), "ms")
                total += weight / 2.0 * cls.cos_zenith(end + offset, lat, lon)
        return total / subintervals
