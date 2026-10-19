"""The README's example runs and prints the values its comments state."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_the_readme_example_prints_what_it_says() -> None:
    text = README.read_text()
    code = re.search(r"```python\n(.*?)```", text, re.S)
    assert code is not None
    stated = [float(v) for v in re.findall(r"# (\d+\.\d) °C", code.group(1))]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(code.group(1), "README.md", "exec"), {})
    printed = [float(line) for line in output.getvalue().split()]
    assert len(printed) == len(stated) == 4
    for value, expected in zip(printed, stated, strict=True):
        assert round(value, 1) == expected
