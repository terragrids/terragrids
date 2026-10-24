"""The README's examples run and print the values their comments state."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def example(word: str) -> str:
    """The README's Python example that uses ``word``."""
    blocks: list[str] = re.findall(r"```python\n(.*?)```", README.read_text(), re.S)
    found = [block for block in blocks if word in block]
    assert len(found) == 1
    return found[0]


def printed(code: str) -> list[str]:
    """The lines the code prints."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(code, "README.md", "exec"), {})
    return output.getvalue().splitlines()


def test_the_measures_example_prints_what_it_says() -> None:
    code = example("HeatStress")
    stated = [float(v) for v in re.findall(r"# (\d+\.\d) °C", code)]
    values = [float(line) for line in printed(code)]
    assert len(values) == len(stated) == 4
    for value, expected in zip(values, stated, strict=True):
        assert round(value, 1) == expected


def test_the_areas_example_prints_what_it_says() -> None:
    code = example("AreaWeights")
    # Each print's comment opens with what it prints, before a colon.
    stated = re.findall(r"^print\(.*\)  # (.*?):", code, re.M)
    assert printed(code) == stated
    assert len(stated) == 3
