"""Heat-stress measures, computed with thermofeel.

A heat-stress measure turns several weather variables into one number that tracks how
hard heat is on the human body. Air temperature alone misses most of it: humid air
slows the evaporation of sweat, and sunshine adds heat that a thermometer in the shade
never sees.

Every formula here comes from thermofeel (https://github.com/ecmwf/thermofeel). It is
the library of the European Centre for Medium-Range Weather Forecasts (ECMWF), the
intergovernmental organisation that produces ERA5, a reconstruction of past weather
hour by hour. ECMWF uses thermofeel to produce ERA5-HEAT, its dataset of hourly
heat-stress measures computed from ERA5. Each method of :class:`HeatStress` fixes which
thermofeel function its measure uses, so that values computed by anyone with this
library match the published Terragrids data.

Inputs and outputs are in kelvin, as in ERA5 and thermofeel. Inputs are numpy arrays,
or anything numpy converts to one, and broadcast against each other. The methods
do not clamp inputs to each formula's validity range; each method's docstring states
that range.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import thermofeel
from numpy.typing import ArrayLike, NDArray


class HeatStress:
    """The heat-stress measures, each computed with thermofeel."""

    @staticmethod
    def _broadcast(*arrays: ArrayLike) -> tuple[tuple[int, ...], list[NDArray[np.float64]]]:
        """The common shape of the inputs, and each input broadcast to it as a 1-D array.

        Some thermofeel functions index their inputs with boolean masks, which fails on
        0-dimensional arrays. Flattening to at least one dimension, then restoring the
        shape, lets every measure accept scalars as well as grids.
        """
        as_arrays = [np.asarray(a, dtype=float) for a in arrays]
        shape = np.broadcast_shapes(*(a.shape for a in as_arrays))
        flat: list[NDArray[np.float64]] = [np.broadcast_to(a, shape).reshape(-1) for a in as_arrays]
        return shape, flat

    @classmethod
    def heat_index(cls, t2m: ArrayLike, d2m: ArrayLike) -> NDArray[np.float64]:
        """The heat index (K): how hot shaded air feels, given its humidity.

        ``t2m`` is the air temperature at 2 m (K) and ``d2m`` the dewpoint at 2 m (K): the
        temperature at which the air's moisture would start to condense, which ERA5 gives
        in place of relative humidity.

        This is the method of the US National Weather Service (NWS), through thermofeel's
        ``calculate_heat_index_adjusted``:

        - a simple formula first; if its average with the air temperature reaches 80 °F
          (26.7 °C), Rothfusz's (1990) regression on Steadman's (1979) tables replaces it;
        - the NWS adjustments for very dry air (relative humidity below 13 %, 80–112 °F)
          and very humid air (above 85 %, 80–87 °F);
        - at or below 40 °F (4.4 °C), the air temperature itself.

        Rothfusz gives the regression's error as ±1.3 °F (±0.7 °C) against Steadman's
        tables. The NWS states it is not valid beyond the range of conditions Steadman
        considered. Relative humidity is derived from the dewpoint by the Magnus formula.
        The measure ignores sunshine and wind.

        **How to read it.** The heat index is a temperature: the shaded air temperature
        that would feel as hot at a moderate humidity. Dry air can make it lower than the
        air temperature, and humid air higher. The NWS reads it in four bands
        (https://www.weather.gov/safety/heat-index):

        =================  ===============  ============================================
        Heat index         NWS band         What it means for prolonged activity
        =================  ===============  ============================================
        27–32 °C           Caution          fatigue possible; heat cramps with activity
        32–41 °C           Extreme caution  heat cramps and heat exhaustion possible
        41–54 °C           Danger           heat exhaustion likely; heat stroke probable
        above 54 °C        Extreme danger   heat stroke imminent
        =================  ===============  ============================================

        Examples, computed with this method (values rounded; ``tests/test_measures.py``
        checks them):

        =========  =================  ==========  ===============================
        Air        Relative humidity  Heat index  Reading
        =========  =================  ==========  ===============================
        25 °C      50 %               24.9 °C     feels like the air temperature
        30 °C      40 %               29.7 °C     a dry hot day; caution
        35 °C      50 %               40.7 °C     extreme caution
        32 °C      70 %               40.4 °C     cooler air, but as hot to the body
        40 °C      25 %               41.1 °C     a dry heatwave; danger
        38 °C      60 %               55.0 °C     humid heatwave; extreme danger
        =========  =================  ==========  ===============================

        Full sunshine can add up to about 8 °C to the felt heat, which this measure leaves
        out (NWS).

        References: https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml;
        Rothfusz, NWS Technical Attachment SR 90-23 (1990).
        """
        shape, (t, d) = cls._broadcast(t2m, d2m)
        result: NDArray[np.float64] = thermofeel.calculate_heat_index_adjusted(t, d)
        return result.reshape(shape)

    @classmethod
    def utci(
        cls, t2m: ArrayLike, d2m: ArrayLike, wind10m: ArrayLike, mrt: ArrayLike
    ) -> NDArray[np.float64]:
        """The Universal Thermal Climate Index (UTCI, in K): the body's response to heat.

        It covers cold as well.

        UTCI is the air temperature of a reference environment that would put the same
        strain on the body as the actual one. The reference environment has a wind of
        0.5 m/s at 10 m, a mean radiant temperature equal to the air temperature, and
        relative humidity of 50 % (vapour pressure capped at 20 hPa). Its model is a
        walking person, whose clothing adapts to the temperature.

        - ``t2m``: air temperature at 2 m (K);
        - ``d2m``: dewpoint at 2 m (K);
        - ``wind10m``: wind speed at 10 m (m/s);
        - ``mrt``: mean radiant temperature (K), the uniform surface temperature that would
          give the body the same radiant heat as the actual sun, sky and ground. ERA5-HEAT,
          the heat-stress dataset of the European Centre for Medium-Range Weather Forecasts
          (ECMWF), publishes it.

        This is thermofeel's ``calculate_utci``: the polynomial approximation of Bröde et al.
        (2012), which is also how ERA5-HEAT computes UTCI. The published data takes UTCI
        from ERA5-HEAT itself. This method serves users with their own inputs, such as
        forecasts or other datasets that come without UTCI.

        Given ERA5's own inputs and ERA5-HEAT's mean radiant temperature, it does not
        exactly reproduce ERA5-HEAT's published UTCI. Over European land in July 2025 the
        difference had a median of 0.8 °C and a 99th percentile of 3.7 °C, with no overall
        bias. It is larger where the weather changes sharply between neighbouring cells,
        which fits ERA5-HEAT computing UTCI on its own grid before regridding; that does
        not explain all of it.

        The polynomial was fitted for air temperature from −50 °C to 50 °C, wind from 0.5
        to 17 m/s, mean radiant temperature from 30 °C below to 70 °C above the air
        temperature, and vapour pressure up to 50 hPa. Outside that range it extrapolates.

        **How to read it.** UTCI is a temperature, read on the UTCI assessment scale of
        thermal stress (Bröde et al., 2012). The heat side of the scale:

        ==============  ==========================
        UTCI            Thermal stress
        ==============  ==========================
        9–26 °C         no thermal stress
        26–32 °C        moderate heat stress
        32–38 °C        strong heat stress
        38–46 °C        very strong heat stress
        above 46 °C     extreme heat stress
        ==============  ==========================

        Below 9 °C the scale continues into cold stress.

        Mean radiant temperature is what separates shade from sun. In shade, or at night,
        it is close to the air temperature. In full summer sun it can be tens of degrees
        above it. Examples, computed with this method (relative humidity, RH, 40 %
        unless stated; values rounded; ``tests/test_measures.py`` checks them):

        =====  ====  ======================  ===========  ==============================
        Air    Wind  Mean radiant            UTCI         Reading
        =====  ====  ======================  ===========  ==============================
        22 °C  2     22 °C (shade), 50 % RH  20.2 °C      no thermal stress
        30 °C  2     30 °C (shade)           28.9 °C      moderate heat stress
        30 °C  2     60 °C (full sun)        36.5 °C      strong: sun adds 7.6 °C
        30 °C  0.5   60 °C (full sun)        37.4 °C      still air: hotter still
        30 °C  6     60 °C (full sun)        32.9 °C      a breeze: 3.6 °C cooler
        35 °C  1     65 °C, 30 % RH          42.1 °C      very strong heat stress
        38 °C  1     70 °C, 30 % RH          46.2 °C      extreme heat stress
        =====  ====  ======================  ===========  ==============================

        Wind is in m/s at 10 m.

        Reference: Bröde et al. (2012), https://doi.org/10.1007/s00484-011-0454-1.
        """
        shape, (t, d, v, m) = cls._broadcast(t2m, d2m, wind10m, mrt)
        result: NDArray[np.float64] = thermofeel.calculate_utci(t, v, m, td_k=d)
        return result.reshape(shape)

    @classmethod
    def wbgt_liljegren(
        cls,
        t2m: ArrayLike,
        d2m: ArrayLike,
        sp_hpa: ArrayLike,
        wind10m: ArrayLike,
        ssrd_wm2: ArrayLike,
        fdir_frac: ArrayLike,
        cossza: ArrayLike,
    ) -> NDArray[np.float64]:
        """Wet-bulb globe temperature (WBGT, in K) by Liljegren's physical model.

        WBGT is the reading of an instrument: a weighted sum of a natural wet-bulb
        thermometer (a wet wick in the open, cooled by evaporation, 70 %), a black globe
        thermometer in the sun (20 %) and the shaded air temperature (10 %). Liljegren et
        al. (2008) estimate what that instrument would read from weather data, by solving
        the heat balance of the globe and of the wick.

        - ``t2m``, ``d2m``: air temperature and dewpoint at 2 m (K);
        - ``sp_hpa``: surface pressure (hPa);
        - ``wind10m``: wind speed at 10 m (m/s);
        - ``ssrd_wm2``: sunlight reaching the ground (W/m²), the mean over the hour;
        - ``fdir_frac``: the share of that sunlight arriving as a direct beam, 0 to 1;
        - ``cossza``: cosine of the solar zenith angle, averaged over the same hour
          (:meth:`terragrids.era5.Sun.cos_zenith_hourly`).

        This is thermofeel's ``calculate_wbgt_liljegren``, configured as KNMI runs it
        operationally. KNMI is the Koninklijk Nederlands Meteorologisch Instituut (Royal
        Netherlands Meteorological Institute), the Dutch national weather service. Its
        configuration:

        - the 10 m wind is first raised to at least 0.62 m/s;
        - it is then converted to the instrument's 2 m height by Liljegren's
          stability-dependent profile (urban exponents);
        - the direct-beam share is capped at 0.9, and set to 0 when the sun is at or below
          89.5° from the zenith.

        The solvers repeat until the answer settles. Where they do not settle, the result
        is NaN: no value is substituted. :meth:`fill_nonconverged` fills those points
        when a caller asks.

        **How to read it.** WBGT runs well below the air temperature, because the
        wet-bulb term dominates. KNMI's public heat scale, the "heat force" from 0 to 10,
        runs in 2 °C steps of WBGT from 14 °C to 32 °C (KNMI Technical Report TR-26-04,
        2026). In the examples below, RH is relative humidity in %, wind is in m/s at 10 m,
        and sun is surface sunlight in W/m². They are computed with this method (values
        rounded; ``tests/test_measures.py`` checks them against Liljegren's original
        code):

        ==========================  =====  ==  ====  =====  ==========  ======
        Conditions                  Air    RH  Wind  Sun    Sun height  WBGT
        ==========================  =====  ==  ====  =====  ==========  ======
        low morning sun             25 °C  60  2.0   150    cos 0.25    22.7 °C
        overcast                    28 °C  65  4.0   250    cos 0.80    25.8 °C
        windy and sunny             30 °C  40  8.0   800    cos 0.85    25.6 °C
        humid, moderate sun         32 °C  70  3.0   600    cos 0.70    32.1 °C
        hot, dry, still, high sun   35 °C  30  1.0   900    cos 0.90    32.2 °C
        extreme dry heat            42 °C  15  1.5   950    cos 0.95    33.0 °C
        ==========================  =====  ==  ====  =====  ==========  ======

        The humid 32 °C day reaches the same WBGT as the dry 35 °C day.

        References: Liljegren et al. (2008), https://doi.org/10.1080/15459620802310770;
        Kong and Huber (2022), https://doi.org/10.1029/2021EF002334.
        """
        shape, (t, d, p, v, s, f, c) = cls._broadcast(
            t2m, d2m, sp_hpa, wind10m, ssrd_wm2, fdir_frac, cossza
        )
        rh = thermofeel.calculate_relative_humidity_percent(t, d)
        result: NDArray[np.float64] = thermofeel.calculate_wbgt_liljegren(t, rh, p, v, s, f, c)
        return result.reshape(shape)

    @classmethod
    def wbgt_mrt(
        cls, t2m: ArrayLike, d2m: ArrayLike, wind10m: ArrayLike, mrt: ArrayLike
    ) -> NDArray[np.float64]:
        """Wet-bulb globe temperature (WBGT, in K) from the mean radiant temperature (MRT).

        The same weighted sum as :meth:`wbgt_liljegren`, with two terms estimated more
        simply:

        - the globe temperature is solved from the mean radiant temperature, the air
          temperature and the wind (Guo et al., 2018), in closed form;
        - the wet-bulb term is Stull's (2011) fitted wet-bulb temperature. That is the
          reading of a shaded, ventilated thermometer, not of a wick in the open. The two
          differ most in strong sun and still air.

        - ``t2m``, ``d2m``: air temperature and dewpoint at 2 m (K);
        - ``wind10m``: wind speed at 10 m (m/s);
        - ``mrt``: mean radiant temperature (K), the uniform surface temperature that would
          give the body the same radiant heat as the actual sun, sky and ground. ERA5-HEAT,
          the heat-stress dataset of the European Centre for Medium-Range Weather Forecasts
          (ECMWF), publishes it.

        This is thermofeel's ``calculate_wbgt``, the method of Brimicombe et al. (2023). It
        shares its radiant input with the Universal Thermal Climate Index (:meth:`utci`),
        needs no sun position, and always returns a
        value for valid inputs. It is read on the same scale as :meth:`wbgt_liljegren`.

        References: Stull (2011), https://doi.org/10.1175/JAMC-D-11-0143.1;
        Brimicombe et al. (2023), https://doi.org/10.1029/2022GH000701.
        """
        shape, (t, d, v, m) = cls._broadcast(t2m, d2m, wind10m, mrt)
        result: NDArray[np.float64] = thermofeel.calculate_wbgt(t, m, v, d)
        return result.reshape(shape)

    @staticmethod
    def fill_nonconverged(
        liljegren: ArrayLike, mrt_based: ArrayLike, inputs: Sequence[ArrayLike]
    ) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
        """Fill the points where Liljegren's solver did not settle, and say which they are.

        ``liljegren`` is the output of :meth:`wbgt_liljegren`, ``mrt_based`` the output of
        :meth:`wbgt_mrt` for the same points, and ``inputs`` the arrays that were passed
        to :meth:`wbgt_liljegren`.

        A point is filled only where every Liljegren input is a number and the result is
        still NaN: the solver failed. A NaN caused by a missing input stays NaN, so the two
        causes are never confused.

        Returns the filled values and a boolean mask of the points that were filled. Every
        filled value is an estimate by the mean-radiant-temperature method, not a Liljegren
        one, and should be reported as such.
        """
        lil = np.asarray(liljegren, dtype=float)
        alt = np.asarray(mrt_based, dtype=float)
        valid = np.ones(lil.shape, dtype=bool)
        for array in inputs:
            valid &= np.broadcast_to(np.isfinite(np.asarray(array, dtype=float)), lil.shape)
        filled: NDArray[np.bool_] = np.isnan(lil) & valid & np.isfinite(alt)
        values: NDArray[np.float64] = np.where(filled, alt, lil)
        return values, filled


@dataclass(frozen=True)
class Method:
    """How one measure is computed, for documentation and published metadata."""

    measure: str
    function: str
    thermofeel_function: str
    reference: str
    inputs: tuple[str, ...]
    unit: str = "K"


METHODS: dict[str, Method] = {
    "heat_index": Method(
        measure="heat index",
        function="heat_index",
        thermofeel_function="calculate_heat_index_adjusted",
        reference="Rothfusz (1990), NWS Technical Attachment SR 90-23; NWS adjustments",
        inputs=("t2m", "d2m"),
    ),
    "utci": Method(
        measure="Universal Thermal Climate Index",
        function="utci",
        thermofeel_function="calculate_utci",
        reference="Bröde et al. (2012), https://doi.org/10.1007/s00484-011-0454-1",
        inputs=("t2m", "d2m", "wind10m", "mrt"),
    ),
    "wbgt_liljegren": Method(
        measure="wet-bulb globe temperature",
        function="wbgt_liljegren",
        thermofeel_function="calculate_wbgt_liljegren",
        reference="Liljegren et al. (2008), https://doi.org/10.1080/15459620802310770",
        inputs=("t2m", "d2m", "sp_hpa", "wind10m", "ssrd_wm2", "fdir_frac", "cossza"),
    ),
    "wbgt_mrt": Method(
        measure="wet-bulb globe temperature",
        function="wbgt_mrt",
        thermofeel_function="calculate_wbgt",
        reference="Brimicombe et al. (2023), https://doi.org/10.1029/2022GH000701",
        inputs=("t2m", "d2m", "wind10m", "mrt"),
    ),
}
"""Every measure the library computes, keyed by the method of :class:`HeatStress` that
computes it.

The published data's metadata is generated from this table, so it cannot drift from
the code.
"""

WBGT_DEFAULT = "wbgt_liljegren"
"""The WBGT method published as ``wbgt``; the other is published under its own name.

Liljegren's model was chosen on a month of ERA5 for Europe (July 2025). It produced a
value at every land cell and hour, its sun-position input matched ECMWF's own, and its
daily maximum ran 2.8 °C above the MRT method's on average on hot days, and 4.8 °C at
the 95th percentile. The choice therefore matters, and Liljegren is the method that
models the instrument WBGT is defined by. The study, the rule that decided it and how
that rule was amended are in ``docs/methodology/heat_stress.md``.
"""
