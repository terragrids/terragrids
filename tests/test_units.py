"""Temperature conversions at fixed points, round trips and input shapes."""

from __future__ import annotations

import numpy as np
import pytest

from terragrids.units import Temperature


@pytest.mark.parametrize(
    ("kelvin", "celsius", "fahrenheit"),
    [
        (0.0, -273.15, -459.67),  # absolute zero
        (233.15, -40.0, -40.0),  # where Celsius and Fahrenheit meet
        (273.15, 0.0, 32.0),  # water freezes
        (310.15, 37.0, 98.6),  # body temperature
        (373.15, 100.0, 212.0),  # water boils at sea level
    ],
)
def test_fixed_points(kelvin: float, celsius: float, fahrenheit: float) -> None:
    assert float(Temperature.kelvin_to_celsius(kelvin)) == pytest.approx(celsius)
    assert float(Temperature.celsius_to_kelvin(celsius)) == pytest.approx(kelvin)
    assert float(Temperature.kelvin_to_fahrenheit(kelvin)) == pytest.approx(fahrenheit)
    assert float(Temperature.fahrenheit_to_kelvin(fahrenheit)) == pytest.approx(kelvin)


def test_round_trips_return_the_input() -> None:
    kelvin = np.linspace(200.0, 330.0, 27)
    np.testing.assert_allclose(
        Temperature.celsius_to_kelvin(Temperature.kelvin_to_celsius(kelvin)), kelvin
    )
    np.testing.assert_allclose(
        Temperature.fahrenheit_to_kelvin(Temperature.kelvin_to_fahrenheit(kelvin)), kelvin
    )


def test_numbers_lists_and_grids_are_accepted() -> None:
    assert Temperature.kelvin_to_celsius([273.15, 283.15]).tolist() == pytest.approx([0.0, 10.0])
    assert Temperature.kelvin_to_fahrenheit(np.full((2, 3), 273.15)).shape == (2, 3)
