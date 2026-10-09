"""The record conventions both external-dependency folds share.

Both folds read the same three record kinds — a manifest declaration, an
external graph node resolved to a declared package, and an import edge that
reaches one — and both need the same two conventions: which declaration paths
count as the repository proper, and how a package identity round-trips through
a single string. They live here so the two folds cannot answer them
differently.
"""

from __future__ import annotations

from typing import Literal

# ``field`` reads a dataclass, an ORM row or a dict alike. It is the shared
# adapter for exactly this problem; it sits under ``health`` only because that
# is where the first fold needing it was written.
from repowise.core.analysis.health.rows import field

Scope = Literal["primary", "all"]

#: Declaration paths that describe a checkout of some other tree living inside
#: this one. Counted, reported as ``excluded_declarations``, never folded into
#: the primary scope's totals.
AUXILIARY_PREFIXES = (".claude/worktrees/", "local-stash/")


#: The only edge type that counts as declared-package usage. The relationship
#: folds additionally expect file sources; both rules are applied by whoever
#: fetches the edges, not by the folds.
IMPORT_EDGE_TYPE = "imports"


def is_primary_path(path: str | None) -> bool:
    """Whether a declaration or importing file belongs to the repository proper."""
    return not (path or "").startswith(AUXILIARY_PREFIXES)


def in_scope(path: str | None, scope: Scope) -> bool:
    """Whether a path is visible under ``scope``."""
    return True if scope == "all" else is_primary_path(path)


def package_key(ecosystem: str, name: str) -> str:
    """The single string that addresses one package across the HTTP surface."""
    return f"{ecosystem}:{name}"


def split_package_key(key: str) -> tuple[str, str]:
    """Invert :func:`package_key`, rejecting anything that is not one."""
    ecosystem, separator, name = key.partition(":")
    if not separator or not ecosystem or not name:
        raise ValueError("package_key must contain an ecosystem and package name")
    return ecosystem, name


def target_basis(node_id: str, package_name: str) -> Literal["exact", "subpath", "mapped"]:
    """How confidently one external graph node stands for the declared package."""
    value = node_id.casefold()
    expected = f"external:{package_name}".casefold()
    if value == expected:
        return "exact"
    if value.startswith(f"{expected}/") or value.startswith(f"{expected}:"):
        return "subpath"
    return "mapped"


__all__ = [
    "AUXILIARY_PREFIXES",
    "IMPORT_EDGE_TYPE",
    "Scope",
    "field",
    "in_scope",
    "is_primary_path",
    "package_key",
    "split_package_key",
    "target_basis",
]
