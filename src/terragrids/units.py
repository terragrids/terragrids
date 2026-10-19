"""Temperature conversions between kelvin, degrees Celsius and degrees Fahrenheit.

The library takes and returns kelvin, as ERA5 and thermofeel do. Kelvin is the
temperature measured from absolute zero, in steps the size of a degree Celsius, so
0 °C is 273.15 K. These functions convert values for reading or publishing.

Each is thermofeel's own conversion, so values match thermofeel's exactly. Inputs are
numpy arrays, or anything numpy converts to one: a number, a list or a grid.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray
from thermofeel import helpers


def kelvin_to_celsius(kelvin: ArrayLike) -> NDArray[np.float64]:
    """Degrees Celsius from kelvin: 273.15 K is 0 °C."""
    result: NDArray[np.float64] = helpers.kelvin_to_celsius(np.asarray(kelvin, dtype=float))
    return result


def celsius_to_kelvin(celsius: ArrayLike) -> NDArray[np.float64]:
    """Kelvin from degrees Celsius: 0 °C is 273.15 K."""
    result: NDArray[np.float64] = helpers.celsius_to_kelvin(np.asarray(celsius, dtype=float))
    return result


def kelvin_to_fahrenheit(kelvin: ArrayLike) -> NDArray[np.float64]:
    """Degrees Fahrenheit from kelvin: 273.15 K is 32 °F, the freezing point of water."""
    result: NDArray[np.float64] = helpers.kelvin_to_fahrenheit(
        np.asarray(kelvin, dtype=float)
    )
    return result


def fahrenheit_to_kelvin(fahrenheit: ArrayLike) -> NDArray[np.float64]:
    """Kelvin from degrees Fahrenheit: 32 °F is 273.15 K, the freezing point of water."""
    result: NDArray[np.float64] = helpers.fahrenheit_to_kelvin(
        np.asarray(fahrenheit, dtype=float)
    )
    return result
