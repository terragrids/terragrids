"""The package imports, reports its version, and imports only what users can install."""

import ast
import re
import sys
from pathlib import Path

import terragrids

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "terragrids"


def declared_dependencies() -> set[str]:
    """The packages installed with terragrids: the ``dependencies`` in pyproject.toml.

    Read with a pattern rather than a TOML parser, which Python 3.10 lacks.
    """
    text = (ROOT / "pyproject.toml").read_text()
    listed = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    assert listed, "pyproject.toml lists no dependencies"
    requirements = re.findall(r'"([A-Za-z0-9_.-]+)', listed.group(1))
    return {name.lower().replace("-", "_") for name in requirements}


# Anything else would be a package that users of the library don't have.
ALLOWED = set(sys.stdlib_module_names) | {"terragrids"} | declared_dependencies()


def imported_top_level_names(path: Path) -> set[str]:
    """Top-level names of every absolute import in one module."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_version_is_exposed() -> None:
    assert terragrids.__version__ == "0.0.3"


def test_the_library_imports_only_what_users_can_install() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(imported_top_level_names(path) - ALLOWED)
        for path in SRC.rglob("*.py")
        if imported_top_level_names(path) - ALLOWED
    }
    assert offenders == {}


def test_the_dependencies_are_read_from_the_metadata() -> None:
    assert {"numpy", "thermofeel"} <= declared_dependencies()
    assert "pytest" not in declared_dependencies()


def test_the_boundary_check_catches_any_other_import(tmp_path: Path) -> None:
    module = tmp_path / "bad.py"
    module.write_text("from unlisted.storage import x\nimport another_package\nimport os\n")
    assert imported_top_level_names(module) - ALLOWED == {"unlisted", "another_package"}
