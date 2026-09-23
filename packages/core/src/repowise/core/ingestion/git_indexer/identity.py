"""Re-export of :mod:`repowise.core.author_identity`, which lives outside the
ingestion package so pure folds can use it without importing the parser stack."""

from __future__ import annotations

from repowise.core.author_identity import (
    author_identity_key,
    build_identity_resolver,
    canonicalize_author_email,
)

__all__ = ["author_identity_key", "build_identity_resolver", "canonicalize_author_email"]
