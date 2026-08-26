"""Canonical identifiers and evidence references shared by MCP tools.

The identity helpers in this module are presentation-only. They normalize the
coordinates tools already expose; they do not merge persisted records or
manufacture precision that the underlying source cannot verify.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from typing import Any


def repository_identity(repository: str) -> str:
    """Return the canonical, case-insensitive public repository identity."""

    return repository.strip().replace("\\", "/").strip("/").casefold()


def path_identity(path: str) -> str:
    """Return one repository-relative POSIX path form."""

    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return normalized.removeprefix("./")


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


# Compatibility aliases for the get_why-specific module that originally
# owned these primitives. Keeping the private spellings prevents a mechanical
# promotion from changing sealed evidence identities or downstream imports.
_repository_identity = repository_identity
_path_identity = path_identity
_content_id = content_id
_reference = reference


__all__ = [
    "content_id",
    "path_identity",
    "reference",
    "repository_identity",
]
