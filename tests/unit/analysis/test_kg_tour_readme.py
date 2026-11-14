"""The guided tour opens on the primary README, not a translation."""

from __future__ import annotations

from repowise.core.analysis.kg_tour import _readme_overview_node
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult


def _kg(*paths: str) -> KnowledgeGraphResult:
    return KnowledgeGraphResult(nodes=[{"id": f"file:{p}", "filePath": p} for p in paths])


def _picked(*paths: str) -> str | None:
    node = _readme_overview_node(_kg(*paths))
    return node["filePath"] if node else None


def test_primary_readme_beats_translations_listed_first() -> None:
    # Regression: the first root README in graph order won, so a repo with
    # README.ar.md listed before README.md opened its tour in Arabic.
    assert _picked("README.ar.md", "README.de.md", "README.md", "README.zh-CN.md") == "README.md"


def test_order_does_not_matter() -> None:
    paths = ["README.md", "README.ar.md", "README.de.md"]
    assert _picked(*paths) == _picked(*reversed(paths)) == "README.md"


def test_extensionless_and_rst_are_primary() -> None:
    assert _picked("README.fr.rst", "README") == "README"
    assert _picked("README.ja.rst", "README.rst") == "README.rst"


def test_root_still_beats_a_shallow_subdirectory() -> None:
    assert _picked("docs/README.md", "README.de.md") == "README.de.md"
    assert _picked("docs/README.md", "README.md") == "README.md"


def test_only_translations_picks_shortest_then_alphabetical() -> None:
    assert _picked("README.zh-CN.md", "README.de.md", "README.ar.md") == "README.ar.md"


def test_none_without_a_root_readme() -> None:
    assert _picked("src/app.py", "docs/guide/README.md") is None
