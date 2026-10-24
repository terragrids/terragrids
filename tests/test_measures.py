"""Heat-stress measures against published reference values.

The heat index is checked against the definitions of the US National Weather Service
(NWS), transcribed here independently of thermofeel: Rothfusz's regression and the NWS
adjustments, as published at https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml
and in NWS Technical Attachment SR 90-23 (1990), and points of the NWS heat index chart.

The Universal Thermal Climate Index (UTCI) is checked against its definition: in the
reference environment it equals the air temperature (Bröde et al., 2012).

Wet-bulb globe temperature (WBGT) by Liljegren's method is checked against Liljegren's
original code; the method from the mean radiant temperature (MRT) against Stull's worked
example and an identity of its formula.
"""

from __future__ import annotations

import numpy as np
import pytest
import thermofeel

from terragrids.measures import HeatStress

KELVIN = 273.15


def f_to_k(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0 + KELVIN


def k_to_f(k: float) -> float:
    return (k - KELVIN) * 9.0 / 5.0 + 32.0


def dewpoint_k(t_k: float, rh_percent: float) -> float:
    """The dewpoint (K) for a temperature and relative humidity, by the Magnus formula.

    This inverts the conversion the heat index uses, so a test can state its inputs as
    relative humidity, as the NWS sources do.
    """
    t_c = t_k - KELVIN
    log_e = np.log10(rh_percent / 100.0 * 6.11 * 10.0 ** (7.5 * t_c / (237.3 + t_c)) / 6.11)
    return float(237.3 * log_e / (7.5 - log_e) + KELVIN)


def rothfusz(t_f: float, rh: float) -> float:
    """Rothfusz's (1990) regression (°F), as published by the NWS."""
    return (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh
        - 0.22475541 * t_f * rh
        - 0.00683783 * t_f * t_f
        - 0.05481717 * rh * rh
        + 0.00122874 * t_f * t_f * rh
        + 0.00085282 * t_f * rh * rh
        - 0.00000199 * t_f * t_f * rh * rh
    )


def heat_index_f(t_f: float, rh: float) -> float:
    t_k = f_to_k(t_f)
    return k_to_f(float(HeatStress.heat_index(t_k, dewpoint_k(t_k, rh))))


@pytest.mark.parametrize(("t_f", "rh"), [(85, 40), (90, 50), (95, 60), (100, 45), (105, 70)])
def test_the_heat_index_is_rothfusz_regression_in_its_range(t_f: float, rh: float) -> None:
    assert heat_index_f(t_f, rh) == pytest.approx(rothfusz(t_f, rh), abs=0.01)


@pytest.mark.parametrize(
    ("t_f", "rh", "chart_f"),
    [(90, 70, 105), (96, 65, 121), (110, 40, 136)],
)
def test_the_heat_index_matches_the_nws_chart(t_f: float, rh: float, chart_f: float) -> None:
    # Chart values are rounded to whole degrees, and the regression is within ±1.3 °F
    # of the tables the chart summarises.
    assert heat_index_f(t_f, rh) == pytest.approx(chart_f, abs=2.0)


def test_very_dry_hot_air_gets_the_nws_low_humidity_adjustment() -> None:
    t_f, rh = 100.0, 10.0
    adjustment = (13 - rh) / 4 * np.sqrt((17 - abs(t_f - 95.0)) / 17)
    assert heat_index_f(t_f, rh) == pytest.approx(rothfusz(t_f, rh) - adjustment, abs=0.01)


def test_very_humid_warm_air_gets_the_nws_high_humidity_adjustment() -> None:
    t_f, rh = 84.0, 90.0
    adjustment = (rh - 85) / 10 * (87 - t_f) / 5
    assert heat_index_f(t_f, rh) == pytest.approx(rothfusz(t_f, rh) + adjustment, abs=0.01)


def test_mild_air_uses_the_nws_simple_formula() -> None:
    # At 70 °F and 50 % the simple formula averages with the temperature to below 80 °F,
    # so the regression is not used.
    t_f, rh = 70.0, 50.0
    simple = 0.5 * (t_f + 61.0 + (t_f - 68.0) * 1.2 + rh * 0.094)
    assert heat_index_f(t_f, rh) == pytest.approx(simple, abs=0.01)


def test_the_heat_index_accepts_scalars_and_grids() -> None:
    t = np.full((2, 3), f_to_k(95.0))
    d = np.full((2, 3), dewpoint_k(f_to_k(95.0), 60.0))
    grid = HeatStress.heat_index(t, d)
    assert grid.shape == (2, 3)
    np.testing.assert_allclose(grid, float(HeatStress.heat_index(t[0, 0], d[0, 0])))


@pytest.mark.parametrize("t_c", [20.0, 25.0, 30.0, 35.0, 40.0, 45.0])
def test_utci_equals_air_temperature_in_the_reference_environment(t_c: float) -> None:
    # The reference environment: wind 0.5 m/s at 10 m, mean radiant temperature equal
    # to air temperature, relative humidity 50 % with vapour pressure capped at 20 hPa.
    # The polynomial approximation reproduces UTCI = air temperature to within a few
    # tenths of a degree there.
    t_k = t_c + KELVIN
    saturation = 6.11 * 10.0 ** (7.5 * t_c / (237.3 + t_c))
    vapour = min(0.5 * saturation, 20.0)
    log_e = np.log10(vapour / 6.11)
    d_k = 237.3 * log_e / (7.5 - log_e) + KELVIN
    assert float(HeatStress.utci(t_k, d_k, 0.5, t_k)) - KELVIN == pytest.approx(t_c, abs=0.5)


def test_utci_rises_with_radiant_heat() -> None:
    t_k, d_k = 30.0 + KELVIN, 18.0 + KELVIN
    values = HeatStress.utci(t_k, d_k, 2.0, np.array([30.0, 40.0, 50.0, 60.0]) + KELVIN)
    assert np.all(np.diff(values) > 0)


def test_utci_accepts_scalars_and_grids() -> None:
    grid = HeatStress.utci(np.full(4, 303.15), 291.15, np.array([1.0, 2.0, 3.0, 4.0]), 318.15)
    assert grid.shape == (4,)
    assert float(HeatStress.utci(303.15, 291.15, 1.0, 318.15)) == pytest.approx(grid[0])


def dewpoint_from_rh_c(t_c: float, rh: float) -> float:
    return dewpoint_k(t_c + KELVIN, rh)


@pytest.mark.parametrize(
    ("t_c", "rh", "expected_c"),
    [
        (25, 50, 24.9),
        (30, 40, 29.7),
        (35, 50, 40.7),
        (32, 70, 40.4),
        (40, 25, 41.1),
        (38, 60, 55.0),
    ],
)
def test_the_heat_index_docstring_examples(t_c: float, rh: float, expected_c: float) -> None:
    value = float(HeatStress.heat_index(t_c + KELVIN, dewpoint_from_rh_c(t_c, rh))) - KELVIN
    assert value == pytest.approx(expected_c, abs=0.05)


@pytest.mark.parametrize(
    ("t_c", "rh", "wind", "mrt_c", "expected_c"),
    [
        (22, 50, 2.0, 22, 20.2),
        (30, 40, 2.0, 30, 28.9),
        (30, 40, 2.0, 60, 36.5),
        (30, 40, 0.5, 60, 37.4),
        (30, 40, 6.0, 60, 32.9),
        (35, 30, 1.0, 65, 42.1),
        (38, 30, 1.0, 70, 46.2),
    ],
)
def test_the_utci_docstring_examples(
    t_c: float, rh: float, wind: float, mrt_c: float, expected_c: float
) -> None:
    value = (
        float(HeatStress.utci(t_c + KELVIN, dewpoint_from_rh_c(t_c, rh), wind, mrt_c + KELVIN))
        - KELVIN
    )
    assert value == pytest.approx(expected_c, abs=0.05)


# Liljegren WBGT reference cases, computed with Liljegren's original C code (WBGT
# version 1.1, UChicago Argonne, 2008; the copy distributed in pywbgt,
# https://github.com/kwodzicki/pywbgt, src/pywbgt/src/liljegren_c.c). The C code's own
# functions were used for every step: stab_srdt and est_wind_speed (urban exponents,
# night column for a negative temperature gradient) to bring the 10 m wind to 2 m,
# then Tglobe and Twb, combined as 0.1 Ta + 0.2 Tg + 0.7 Tnwb. The direct-beam share
# was capped at 0.9, as KNMI does. All winds are above KNMI's 0.62 m/s floor.
LILJEGREN_CASES = [
    # label, air °C, RH %, pressure hPa, wind 10 m m/s, sunlight W/m², direct share,
    # cos zenith, WBGT °C from the C code
    ("low morning sun", 25, 60, 1013, 2.0, 150, 0.5, 0.25, 22.692),
    ("overcast", 28, 65, 1000, 4.0, 250, 0.1, 0.8, 25.836),
    ("windy and sunny", 30, 40, 1015, 8.0, 800, 0.85, 0.85, 25.581),
    ("humid, moderate sun", 32, 70, 1005, 3.0, 600, 0.6, 0.7, 32.106),
    ("hot, dry, still, high sun", 35, 30, 1010, 1.0, 900, 0.8, 0.9, 32.219),
    ("extreme dry heat", 42, 15, 1000, 1.5, 950, 0.85, 0.95, 33.002),
]


@pytest.mark.parametrize(
    ("label", "t_c", "rh", "p", "wind", "sun", "direct", "cza", "expected_c"), LILJEGREN_CASES
)
def test_wbgt_liljegren_matches_liljegrens_original_code(
    label: str,
    t_c: float,
    rh: float,
    p: float,
    wind: float,
    sun: float,
    direct: float,
    cza: float,
    expected_c: float,
) -> None:
    t_k = t_c + KELVIN
    value = HeatStress.wbgt_liljegren(t_k, dewpoint_k(t_k, rh), p, wind, sun, direct, cza)
    # The C code computes in single precision.
    assert float(value) - KELVIN == pytest.approx(expected_c, abs=0.01), label


def test_stull_wet_bulb_worked_example() -> None:
    # Stull (2011): at 20 °C and 50 % relative humidity the wet-bulb temperature is
    # 13.7 °C. The MRT method's wet-bulb term is this formula.
    wet_bulb = thermofeel.calculate_wbt(np.array([293.15]), np.array([50.0]))
    assert float(wet_bulb[0]) - KELVIN == pytest.approx(13.7, abs=0.05)


def test_wbgt_mrt_in_shade_is_the_wet_bulb_and_air_weighted() -> None:
    # When the mean radiant temperature equals the air temperature, the globe reads the
    # air temperature, so WBGT = 0.7 wet-bulb + 0.3 air.
    t_k, rh = 30.0 + KELVIN, 40.0
    d_k = dewpoint_k(t_k, rh)
    wet_bulb = thermofeel.calculate_wbt(
        np.array([t_k]), thermofeel.calculate_relative_humidity_percent(t_k, d_k)
    )[0]
    expected = 0.7 * wet_bulb + 0.3 * t_k
    assert float(HeatStress.wbgt_mrt(t_k, d_k, 2.0, t_k)) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(("t_c", "rh", "wind"), [(20, 70, 1.0), (25, 60, 2.0), (30, 40, 1.0)])
@pytest.mark.parametrize("mrt_below_air", [0.0, 3.0, 6.0])
def test_the_two_methods_nearly_agree_at_night(
    t_c: float, rh: float, wind: float, mrt_below_air: float
) -> None:
    # Without sun the methods differ only in their wet-bulb and globe physics. At night
    # the mean radiant temperature sits at or a few degrees below the air temperature.
    t_k = t_c + KELVIN
    d_k = dewpoint_k(t_k, rh)
    night = HeatStress.wbgt_liljegren(t_k, d_k, 1013.0, wind, 0.0, 0.0, 0.0)
    shade = HeatStress.wbgt_mrt(t_k, d_k, wind, t_k - mrt_below_air)
    assert float(night) == pytest.approx(float(shade), abs=0.6)


def test_wbgt_rises_with_sun_and_temperature_and_falls_with_wind_in_sun() -> None:
    d_k = 18.0 + KELVIN
    sun = HeatStress.wbgt_liljegren(
        32 + KELVIN, d_k, 1013.0, 2.0, np.array([0.0, 300, 600, 900]), 0.7, 0.9
    )
    warmer = HeatStress.wbgt_liljegren(
        np.array([28.0, 31.0, 34.0]) + KELVIN, d_k, 1013.0, 2.0, 700, 0.7, 0.9
    )
    windier = HeatStress.wbgt_liljegren(
        32 + KELVIN, d_k, 1013.0, np.array([1.0, 3.0, 6.0]), 800, 0.8, 0.9
    )
    assert np.all(np.diff(sun) > 0)
    assert np.all(np.diff(warmer) > 0)
    assert np.all(np.diff(windier) < 0)


def test_wbgt_never_falls_below_the_wet_bulb() -> None:
    rng = np.random.default_rng(3)
    t_k = rng.uniform(15, 45, 200) + KELVIN
    rh = rng.uniform(10, 90, 200)
    d_k = np.array([dewpoint_k(t, r) for t, r in zip(t_k, rh, strict=True)])
    cza = rng.uniform(0, 1, 200)
    values = HeatStress.wbgt_liljegren(
        t_k, d_k, 1013.0, rng.uniform(0.5, 8, 200), 900 * cza, 0.7, cza
    )
    wet_bulb = thermofeel.calculate_wbt(t_k, rh)
    assert np.all(values >= wet_bulb)


def test_fill_takes_the_mrt_value_only_where_liljegren_failed() -> None:
    liljegren = np.array([300.0, np.nan, np.nan, 301.0])
    mrt_based = np.array([299.0, 298.0, 297.0, 300.0])
    wind = np.array([2.0, 2.0, np.nan, 2.0])  # the third point had a missing input
    values, filled = HeatStress.fill_nonconverged(liljegren, mrt_based, [300.0, wind])
    np.testing.assert_array_equal(filled, [False, True, False, False])
    np.testing.assert_array_equal(values[[0, 1, 3]], [300.0, 298.0, 301.0])
    assert np.isnan(values[2])


def test_the_wbgt_functions_accept_scalars_and_grids() -> None:
    grid = HeatStress.wbgt_liljegren(np.full((2, 2), 305.0), 291.0, 1013.0, 2.0, 700.0, 0.7, 0.8)
    assert grid.shape == (2, 2)
    assert HeatStress.wbgt_mrt(305.0, 291.0, 2.0, np.full(3, 330.0)).shape == (3,)
