"""Canonical identifiers and evidence references shared by MCP tools.

The identity helpers in this module are presentation-only. They normalize the
coordinates tools already expose; they do not merge persisted records or
manufacture precision that the underlying source cannot verify.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

_OMISSION_REF_RE = re.compile(r"(?:^repowise#|\[repowise#)([0-9a-f]{12})(?:$|:)")


def repository_identity(repository: str) -> str:
    """Return the canonical, case-insensitive public repository identity."""

    return repository.strip().replace("\\", "/").strip("/").casefold()


def path_identity(path: str) -> str:
    """Return one repository-relative POSIX path form."""

    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return normalized.removeprefix("./")


def symbol_identity(symbol_id: str) -> str:
    """Return the canonical ``path::Symbol`` public identifier form."""

    path, separator, name = symbol_id.strip().partition("::")
    if not separator:
        return path_identity(path)
    return f"{path_identity(path)}::{name.strip()}"


def content_id(value: object) -> str:
    """Return the stable compact digest used by public reference identities."""

    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def reference(kind: str, repository: str, **coordinates: object) -> dict[str, Any]:
    """Build a repository-scoped, deterministic evidence reference."""

    identity = {
        "repository": repository_identity(repository),
        "kind": kind,
        **coordinates,
    }
    return {
        "id": f"ev_{content_id(identity)}",
        **identity,
    }


def omission_reference(value: str) -> str | None:
    """Return the exact omission token accepted by ``get_symbol``."""

    candidate = value.strip()
    if re.fullmatch(r"[0-9a-f]{12}", candidate):
        return f"repowise#{candidate}"
    match = _OMISSION_REF_RE.search(candidate)
    return f"repowise#{match.group(1)}" if match else None


def source_reference(
    repository: str,
    path: str,
    *,
    lines: Sequence[int] | None = None,
    symbol_id: str | None = None,
    verification_basis: str,
    source_kind: str,
    commit: str | None = None,
) -> dict[str, Any]:
    """Build a source pointer whose ``id`` is accepted by its target tool."""

    normalized_path = path_identity(path)
    valid_range = (
        lines
        and len(lines) == 2
        and all(isinstance(line, int) and line > 0 for line in lines)
        and int(lines[1]) >= int(lines[0])
    )
    if symbol_id:
        identifier = symbol_identity(symbol_id)
        if "::" not in identifier:
            raise ValueError("symbol_id must use the canonical 'path::Symbol' form")
        symbol_path, _separator, _name = identifier.partition("::")
        if symbol_path != normalized_path:
            raise ValueError("symbol_id path must match the reference path")
        kind = "symbol"
    elif valid_range:
        identifier = f"{normalized_path}:{int(lines[0])}-{int(lines[1])}"
        kind = "file_range"
    else:
        identifier = normalized_path
        kind = "file"
    result: dict[str, Any] = {
        "id": identifier,
        "repository": repository_identity(repository),
        "kind": kind,
        "path": normalized_path,
        "verification_basis": verification_basis,
        "source_kind": source_kind,
    }
    if valid_range:
        result["range"] = [int(lines[0]), int(lines[1])]
    if commit:
        result["commit"] = commit.strip().lower()
    return result


def stable_entity_id(prefix: str, repository: str, coordinates: Mapping[str, object]) -> str:
    """Return a stable public ID for replace-on-analysis persisted rows."""

    identity = {"repository": repository_identity(repository), **coordinates}
    return f"{prefix}_{content_id(identity)}"


def refactoring_plan_id(suggestion: Any, repository: str) -> str:
    """Return the public ID for one refactoring plan, ORM row or dataclass.

    Delegates to the refactoring layer's identity kernel, which is the one owner
    of what makes two plans the same plan and is the id the row is stored under.
    Deriving a second one here let the emitted id churn whenever an incidental
    plan detail moved, so an agent that quoted it yesterday could not resolve it.

    *repository* is accepted for call-site compatibility and does not
    participate: storage scopes uniqueness by repository already, and hashing a
    local path or alias into the string would make the same plan carry different
    ids locally and hosted.
    """

    from repowise.core.analysis.health.refactoring.identity import refactoring_public_id

    return refactoring_public_id(suggestion)


# Compatibility aliases for the get_why-specific module that originally
# owned these primitives. Keeping the private spellings prevents a mechanical
# promotion from changing sealed evidence identities or downstream imports.
_repository_identity = repository_identity
_path_identity = path_identity
_content_id = content_id
_reference = reference


__all__ = [
    "content_id",
    "omission_reference",
    "path_identity",
    "refactoring_plan_id",
    "reference",
    "repository_identity",
    "source_reference",
    "stable_entity_id",
    "symbol_identity",
]
