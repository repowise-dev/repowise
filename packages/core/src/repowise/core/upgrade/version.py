"""Authoritative version constants for the on-disk store format.

These two integers are the *single source of truth* for "what shape is the
``.repowise`` store, and does upgrading repowise need to touch it." They are
deliberately decoupled from the package ``__version__`` so that ordinary
releases (which ship code changes but no store-format change) never invalidate
a user's cached work.

When to bump
------------
``STORE_FORMAT_VERSION``
    Bump by one whenever the *meaning or layout* of the persisted store changes
    in a way the running code must react to (a new required column the additive
    reconcile cannot back-fill, a vector-store layout change, a re-derivation
    that older stores lack). Every bump MUST add a matching entry to
    :data:`repowise.core.upgrade.registry.MIGRATIONS` declaring its upgrade
    impact tier. The overwhelming default for a release is to leave this alone.

``PARSER_SCHEMA_VERSION``
    Bump by one only when parser / extractor logic changes such that a cached
    ``ParsedFile`` from an older build is no longer correct (tree-sitter query
    edits are already covered by hashing the ``.scm`` sources; bump this for
    Python-side extraction changes). Bumping it invalidates the parse cache and
    forces a cheap, automatic re-parse on the next ingest. Leaving package
    ``__version__`` out of the fingerprint is the whole point: unrelated
    releases keep the cache warm.
"""

from __future__ import annotations

#: Current on-disk store format. Stamped into ``state.json`` as
#: ``store_format_version`` on every persist. Legacy stores predating this
#: field are treated as version 0.
STORE_FORMAT_VERSION: int = 1

#: Current parser/extractor schema. Folded into the parse-cache fingerprint in
#: place of the package version. See :mod:`repowise.core.ingestion.parse_cache`.
PARSER_SCHEMA_VERSION: int = 1

#: state.json key holding the store format version that wrote the store.
STORE_FORMAT_VERSION_KEY = "store_format_version"

#: state.json key holding the package ``__version__`` that last wrote the store.
WRITTEN_BY_VERSION_KEY = "written_by_version"

#: state.json key holding the embedding model id the vectors were built with.
EMBEDDING_MODEL_KEY = "embedding_model"

__all__ = [
    "EMBEDDING_MODEL_KEY",
    "PARSER_SCHEMA_VERSION",
    "STORE_FORMAT_VERSION",
    "STORE_FORMAT_VERSION_KEY",
    "WRITTEN_BY_VERSION_KEY",
]
