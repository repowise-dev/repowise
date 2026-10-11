"""Core analysis must not depend on anything above it.

Core owns the semantics; surfaces own caps, rendering and transport. That only
holds as long as the dependency arrow points one way, and an accidental import
is the easy way to lose it silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_CORE = Path(__file__).parents[3] / "packages" / "core" / "src" / "repowise" / "core"

#: Module prefixes core may never import. Transport, presentation, and any
#: deployment-specific integration all sit above it.
_FORBIDDEN = (
    "repowise.server",
    "repowise.cli",
    "mcp",
    "fastapi",
    "starlette",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _violates(module: str) -> str | None:
    for prefix in _FORBIDDEN:
        if module == prefix or module.startswith(f"{prefix}."):
            return prefix
    return None


@pytest.mark.parametrize(
    "source", sorted(_CORE.rglob("*.py")), ids=lambda p: str(p.relative_to(_CORE))
)
def test_core_does_not_import_a_surface(source: Path):
    offenders = {
        module: prefix
        for module in _imported_modules(source)
        if (prefix := _violates(module)) is not None
    }
    assert not offenders, f"{source.relative_to(_CORE)} imports {offenders}"


def test_the_guard_is_actually_looking_at_core():
    """A glob that matched nothing would make every assertion above vacuous."""
    assert len(list(_CORE.rglob("*.py"))) > 100
