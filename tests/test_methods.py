"""The method registry describes the functions that exist, and names a valid default."""

from __future__ import annotations

import inspect

import thermofeel

from terragrids import measures
from terragrids.measures import METHODS, WBGT_DEFAULT


def test_every_registered_method_is_a_library_function_with_those_inputs() -> None:
    for key, method in METHODS.items():
        assert key == method.function
        function = getattr(measures, method.function)
        assert tuple(inspect.signature(function).parameters) == method.inputs


def test_every_registered_method_names_a_thermofeel_function() -> None:
    for method in METHODS.values():
        assert callable(getattr(thermofeel, method.thermofeel_function))


def test_the_wbgt_default_is_a_registered_wbgt_method() -> None:
    assert WBGT_DEFAULT in METHODS
    assert METHODS[WBGT_DEFAULT].measure == "wet-bulb globe temperature"
