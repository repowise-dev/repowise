"""Public reference and evidence identity.

Every surface that hands an agent a pointer back into the code — an MCP tool
response, a persisted finding, a stored evidence row — has to spell it the same
way, because the pointer is the contract: an id quoted in one response is
passed back verbatim to another tool later. This module knows the spelling.

Two id shapes. :func:`source_reference` composes a readable pointer (``path``,
``path:start-end``, ``path::Symbol``) accepted verbatim by the tool that serves
it. :func:`content_id`, :func:`reference` and :func:`stable_entity_id` mint a
digest: sha256 of canonical JSON truncated to 20 hex characters, behind a
caller-chosen prefix, so an id quoted yesterday still resolves today.

Normalisation is what keeps either shape stable: ``src\\main.py`` from a
Windows caller and ``./src/main.py`` from a POSIX one must produce one id.

Pure and dependency-free — stdlib only, no database or filesystem — so
ingestion, the server and the CLI can all use it.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: Length of the truncated sha256 digest behind every ``<prefix>_<digest>`` id.
_DIGEST_LENGTH = 20

#: A ``repowise#<ref>`` omission token, bare or embedded in a distill marker.
#: The 12-hex ref itself is minted by ``core.distill``; this only recognises it.
_OMISSION_REF_RE = re.compile(r"(?:^repowise#|\[repowise#)([0-9a-f]{12})(?:$|:)")
_BARE_OMISSION_REF_RE = re.compile(r"[0-9a-f]{12}")


def repository_identity(repository: str) -> str:
    """Return the canonical, case-insensitive public repository identity."""

    return repository.strip().replace("\\", "/").strip("/").casefold()


def path_identity(path: str) -> str:
    """Return one repository-relative POSIX path form."""

    normalized = posixpath.normpath(str(path).strip().replace("\\", "/"))
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
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


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
    if _BARE_OMISSION_REF_RE.fullmatch(candidate):
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


__all__ = [
    "content_id",
    "omission_reference",
    "path_identity",
    "reference",
    "repository_identity",
    "source_reference",
    "stable_entity_id",
    "symbol_identity",
]
