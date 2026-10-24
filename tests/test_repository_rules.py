"""Five rules for everything in this repository, not only the package.

1. Nothing carries a task ID: task IDs point to a plan that readers cannot see.
2. Every document is reachable from the README by following links.
3. Every link in the README to a file of this repository is a relative path, so the README
   reads correctly wherever the repository is opened. Each one must point at a file that
   exists here and ships in the sdist. PyPI cannot follow a relative path, so the package's
   copy of the README is rewritten by ``hatch_build.py``: every such link points at the
   public repository at the release's tag, a figure at its raw file, a document at its page.
4. The publishing workflow runs only code we have reviewed. Each Action is named by the
   exact commit of a release, with the release in a comment, since a tag can be moved.
   Each build tool comes from ``.github/requirements/publish.txt``, which gives every
   package an exact version and the SHA-256 of its files. The sdist carries the tests
   but not the workflow, so these tests skip there.
5. Every module of the package holds its logic in classes. It defines at least one class,
   and no function outside one. ``__init__.py`` is exempt, since it only names the
   package and its version.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".cfg", ".ini"}
TEXT_NAMES = {"LICENSE", "NOTICE"}

# Built from parts, so that this file does not match its own pattern.
TASK_ID = re.compile(r"\b" + "M" + r"\d+\.\d+(\.\d+)*\b")

# Internal guidance for development tools, never published.
EXEMPT = {Path("CLAUDE.md")}

PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
PUBLISH_LOCK = ROOT / ".github" / "requirements" / "publish.txt"
# An Action named by a commit, with the release it is in a comment after it.
PINNED_ACTION = re.compile(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+")
PINNED_INSTALL = (
    "pip install --quiet --require-hashes --only-binary :all: -r .github/requirements/publish.txt"
)
needs_the_workflow = pytest.mark.skipif(
    not PUBLISH_WORKFLOW.is_file(), reason="the sdist does not carry the workflows"
)

MARKDOWN_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
# The public repository, where the package's copy of the README points.
PUBLIC = "terragrids/terragrids"
README_HOOK = ROOT / "hatch_build.py"

PACKAGE = ROOT / "src" / "terragrids"


def repository_files() -> list[Path]:
    """Every text file in the repository, relative to its root."""
    files = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        skipped = (p in SKIPPED_DIRECTORIES or p.endswith(".egg-info") for p in relative.parts)
        if any(skipped):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES):
            files.append(relative)
    return sorted(files)


def test_nothing_carries_a_task_id() -> None:
    offenders = {}
    for relative in repository_files():
        if relative in EXEMPT:
            continue
        text = (ROOT / relative).read_text(errors="ignore")
        found = sorted({m.group(0) for m in TASK_ID.finditer(text)})
        if found:
            offenders[str(relative)] = found
    assert offenders == {}


def linked_file(page: Path, target: str) -> Path | None:
    """The repository file a link on ``page`` points to, if it points to one."""
    if "://" in target:
        return None
    return ((ROOT / page.parent / target).resolve()).relative_to(ROOT)


def test_every_document_is_reachable_from_the_readme() -> None:
    documents = {p for p in repository_files() if p.suffix == ".md" and p not in EXEMPT}
    reached = {Path("README.md")}
    to_visit = [Path("README.md")]
    while to_visit:
        page = to_visit.pop()
        for target in MARKDOWN_LINK.findall((ROOT / page).read_text()):
            linked = linked_file(page, target)
            if linked is not None and linked in documents and linked not in reached:
                reached.add(linked)
                to_visit.append(linked)
    assert sorted(map(str, documents - reached)) == []


def shipped(path: Path) -> bool:
    """Whether ``path`` is among the files the sdist carries, as ``pyproject.toml`` lists them."""
    text = (ROOT / "pyproject.toml").read_text()
    listed = re.search(r"\[tool\.hatch\.build\.targets\.sdist\]\s*include = \[(.*?)\]", text, re.S)
    assert listed, "pyproject.toml lists no sdist files"
    entries = [Path(entry) for entry in re.findall(r'"([^"]+)"', listed.group(1))]
    return any(path == entry or entry in path.parents for entry in entries)


def test_every_link_in_the_readme_to_a_file_here_is_relative() -> None:
    links = MARKDOWN_LINK.findall((ROOT / "README.md").read_text())
    assert [link for link in links if PUBLIC + "/" in link] == []
    paths = [Path(link) for link in links if "://" not in link]
    assert paths, "the README links to no file of this repository"
    assert [str(p) for p in paths if not (ROOT / p).is_file() or not shipped(p)] == []


def readme_hook() -> ModuleType:
    """``hatch_build.py``, the hook that writes the package's copy of the README."""
    pytest.importorskip("hatchling", reason="the README hook runs inside hatchling")
    spec = importlib.util.spec_from_file_location("hatch_build", README_HOOK)
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def test_the_package_readme_points_every_link_at_the_release() -> None:
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert version
    tag = f"v{version.group(1)}"
    readme = (ROOT / "README.md").read_text()
    pypi = readme_hook().PypiReadme(version.group(1)).rewrite(readme)
    assert [link for link in MARKDOWN_LINK.findall(pypi) if "://" not in link] == []
    images = set(re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", readme))
    for path in [link for link in MARKDOWN_LINK.findall(readme) if "://" not in link]:
        if path in images:
            assert f"(https://raw.githubusercontent.com/{PUBLIC}/{tag}/{path})" in pypi
        else:
            assert f"(https://github.com/{PUBLIC}/blob/{tag}/{path})" in pypi


def test_the_readme_hook_leaves_code_addresses_and_anchors_alone() -> None:
    text = "[a](https://x.org) [b](#c)\n```python\nprint([1](2))\n```\n[d](e.md)\n"
    out = readme_hook().PypiReadme("1.0").rewrite(text)
    assert out == text.replace("(e.md)", f"(https://github.com/{PUBLIC}/blob/v1.0/e.md)")


def test_the_pattern_catches_what_it_should() -> None:
    assert TASK_ID.search("(" + "M" + "1.5.1)")
    assert not TASK_ID.search("a 1.5 m wind")


def workflow_lines(word: str) -> list[str]:
    """The publishing workflow's lines that hold a word, without indentation or list dash."""
    lines = PUBLISH_WORKFLOW.read_text().splitlines()
    return [line.strip().removeprefix("- ") for line in lines if word in line]


@needs_the_workflow
def test_every_action_that_publishes_is_named_by_a_commit() -> None:
    uses = [line for line in workflow_lines("uses:") if not line.startswith("#")]
    assert uses
    assert [line for line in uses if not PINNED_ACTION.fullmatch(line)] == []


@needs_the_workflow
def test_the_build_tools_come_only_from_the_lock_file() -> None:
    installs = [line for line in workflow_lines("pip install") if not line.startswith("#")]
    assert installs == [PINNED_INSTALL]
    assert "python -m build --no-isolation" in workflow_lines("python -m build")


@needs_the_workflow
def test_every_locked_tool_has_an_exact_version_and_a_fingerprint() -> None:
    blocks = re.split(r"\n(?=\S)", PUBLISH_LOCK.read_text())
    tools = [block for block in blocks if not block.startswith("#")]
    assert tools
    for block in tools:
        assert re.match(r"[\w.-]+==[\w.]+ ", block), block.splitlines()[0]
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), block.splitlines()[0]


def test_the_pin_pattern_tells_a_tag_from_a_commit() -> None:
    commit = "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    assert PINNED_ACTION.fullmatch(f"{commit}  # v4.4.0")
    assert not PINNED_ACTION.fullmatch("uses: pypa/gh-action-pypi-publish@release/v1")
    # Without its release, a reviewer cannot tell which version the commit is.
    assert not PINNED_ACTION.fullmatch(commit)


def outside_classes(source: str) -> list[str]:
    """What breaks the class rule in a module: each function outside a class, or no class."""
    body = ast.parse(source).body
    found = [
        f"function {node.name}"
        for node in body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not any(isinstance(node, ast.ClassDef) for node in body):
        found.append("no class")
    return found


def test_every_module_holds_its_logic_in_classes() -> None:
    modules = sorted(path for path in PACKAGE.rglob("*.py") if path.name != "__init__.py")
    assert modules
    found = {str(path.relative_to(ROOT)): outside_classes(path.read_text()) for path in modules}
    assert {module: parts for module, parts in found.items() if parts} == {}


def test_the_class_rule_catches_a_function_outside_a_class() -> None:
    assert outside_classes("def convert(x):\n    return x\n") == ["function convert", "no class"]
    in_both = "class Grid:\n    pass\n\n\ndef helper():\n    pass\n"
    assert outside_classes(in_both) == ["function helper"]
    assert outside_classes("class Grid:\n    def cells(self):\n        pass\n") == []
