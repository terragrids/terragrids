"""The method registry describes the methods that exist, and names a valid default."""

from __future__ import annotations

import inspect

import thermofeel

from terragrids.measures import METHODS, WBGT_DEFAULT, HeatStress


def test_every_registered_method_is_a_library_method_with_those_inputs() -> None:
    for key, method in METHODS.items():
        assert key == method.function
        computes = getattr(HeatStress, method.function)
        assert tuple(inspect.signature(computes).parameters) == method.inputs


def test_every_registered_method_names_a_thermofeel_function() -> None:
    for method in METHODS.values():
        assert callable(getattr(thermofeel, method.thermofeel_function))


def test_the_wbgt_default_is_a_registered_wbgt_method() -> None:
    assert WBGT_DEFAULT in METHODS
    assert METHODS[WBGT_DEFAULT].measure == "wet-bulb globe temperature"
