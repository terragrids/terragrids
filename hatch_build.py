"""The package's description on PyPI: the README, with its links made full addresses.

In the repository, the README links to its figures and documents by relative paths, so it
reads correctly wherever the repository is opened, with nothing published first. PyPI
shows the same README but cannot follow a relative path. So the copy built into the
package points each one at the public repository, at the release's own tag: a figure at
its raw file, a document at its page. A release's page on PyPI then shows the files of
that release, even after later ones change.

hatchling runs this file when it builds the package, as set in ``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hatchling.metadata.plugin.interface import MetadataHookInterface


class PypiReadme:
    """The README as PyPI shows it, for one version of the package."""

    PUBLIC = "terragrids/terragrids"
    # A link or an image whose target is a path: not an address, and not an anchor.
    LINK = re.compile(r"(!?)\[([^\]]*)\]\((?![a-zA-Z][a-zA-Z0-9+.-]*:|#)([^)\s]+)\)")
    # A fenced code block, whose text is code and is left as it is.
    CODE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

    def __init__(self, version: str) -> None:
        self.tag = f"v{version}"

    def address(self, path: str, image: bool) -> str:
        """Where the public repository serves ``path`` at this release's tag."""
        if image:
            return f"https://raw.githubusercontent.com/{self.PUBLIC}/{self.tag}/{path}"
        return f"https://github.com/{self.PUBLIC}/blob/{self.tag}/{path}"

    def rewrite(self, text: str) -> str:
        """``text`` with every relative link made a full address, outside code blocks."""
        pieces, start = [], 0
        for block in self.CODE.finditer(text):
            pieces += [self.links(text[start : block.start()]), block.group(0)]
            start = block.end()
        pieces.append(self.links(text[start:]))
        return "".join(pieces)

    def links(self, text: str) -> str:
        """``text``, which holds no code block, with its relative links made full addresses."""

        def full(link: re.Match[str]) -> str:
            image, label, path = link.groups()
            return f"{image}[{label}]({self.address(path, bool(image))})"

        return self.LINK.sub(full, text)


class ReadmeHook(MetadataHookInterface):
    """Gives the package the README as PyPI shows it."""

    def update(self, metadata: dict[str, Any]) -> None:
        readme = (Path(self.root) / "README.md").read_text(encoding="utf-8")
        metadata["readme"] = {
            "content-type": "text/markdown",
            "text": PypiReadme(metadata["version"]).rewrite(readme),
        }
