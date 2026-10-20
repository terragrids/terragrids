"""Three rules for everything in this repository, not only the package.

1. Nothing carries a task ID: task IDs point to a plan that readers cannot see.
2. Every document is reachable from the README by following links.
3. Every link in the README is a full address. The README is also the package's page on
   PyPI, where a link relative to the repository leads nowhere. So it links to this
   repository's documents through their public copies.
"""

from __future__ import annotations

import re
from pathlib import Path

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

MARKDOWN_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
# Where the public repository shows this repository's files.
PUBLIC_FILES = "https://github.com/terragrids/terragrids/blob/master/"


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
    if target.startswith(PUBLIC_FILES):
        return Path(target.removeprefix(PUBLIC_FILES))
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


def test_every_link_in_the_readme_works_on_pypi() -> None:
    links = MARKDOWN_LINK.findall((ROOT / "README.md").read_text())
    assert links and [link for link in links if "://" not in link] == []
    public = [
        Path(link.removeprefix(PUBLIC_FILES)) for link in links if link.startswith(PUBLIC_FILES)
    ]
    assert public and all((ROOT / path).is_file() for path in public)


def test_the_pattern_catches_what_it_should() -> None:
    assert TASK_ID.search("(" + "M" + "1.5.1)")
    assert not TASK_ID.search("a 1.5 m wind")
