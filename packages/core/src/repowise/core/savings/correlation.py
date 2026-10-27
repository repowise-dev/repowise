"""Privacy-conscious identity and correlation helpers."""

from __future__ import annotations

import hashlib
from uuid import uuid4


def new_event_id() -> str:
    """Return a server-owned UUID for one logical interaction."""
    return str(uuid4())


def _digest(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        value = part.encode("utf-8", errors="strict")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return f"sha256:{digest.hexdigest()}"


def hash_correlation_evidence(namespace: str, value: object) -> str:
    """Hash connection-scoped evidence without persisting its raw value."""
    if not namespace or value is None or str(value) == "":
        raise ValueError("correlation namespace and value must be non-empty")
    return _digest(namespace, str(value))


def scoped_idempotency_key(repository_id: str, surface: str, *parts: object) -> str:
    """Build a bounded repository/surface-scoped retry key."""
    if (
        not repository_id
        or not surface
        or not parts
        or any(part is None or str(part) == "" for part in parts)
    ):
        raise ValueError("repository, surface, and evidence are required")
    return _digest(repository_id, surface, *(str(part) for part in parts))
