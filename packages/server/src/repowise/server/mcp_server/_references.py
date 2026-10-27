"""Reference identity as the MCP tools consume it.

The identity primitives themselves are portable and live in
:mod:`repowise.core.references`; they are re-exported here so the tool modules
that already import them keep one spelling. What stays server-side is the one
helper that needs the health layer to answer.
"""

from __future__ import annotations

from typing import Any

from repowise.core.references import (
    content_id,
    omission_reference,
    path_identity,
    reference,
    repository_identity,
    source_reference,
    stable_entity_id,
    symbol_identity,
)


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
