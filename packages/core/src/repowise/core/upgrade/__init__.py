"""Store-format versioning and the upgrade decision layer.

This package is the single source of truth for "what shape is the ``.repowise``
store, and does upgrading repowise need to touch it." :func:`assess` returns a
pure :class:`UpgradeVerdict`; the CLI and server present it and (for no-LLM auto
actions) execute it via :func:`apply_auto`. Reindex is only ever *recommended*,
never forced.
"""

from __future__ import annotations

from .manager import UpgradeContext, apply_auto, assess, stamp
from .registry import MIGRATIONS, Migration, migrations_between
from .verdict import (
    UpgradeAction,
    UpgradeActionKind,
    UpgradeTier,
    UpgradeVerdict,
)
from .version import (
    EMBEDDING_MODEL_KEY,
    PARSER_SCHEMA_VERSION,
    STORE_FORMAT_VERSION,
    STORE_FORMAT_VERSION_KEY,
    WRITTEN_BY_VERSION_KEY,
)

__all__ = [
    "EMBEDDING_MODEL_KEY",
    "MIGRATIONS",
    "PARSER_SCHEMA_VERSION",
    "STORE_FORMAT_VERSION",
    "STORE_FORMAT_VERSION_KEY",
    "WRITTEN_BY_VERSION_KEY",
    "Migration",
    "UpgradeAction",
    "UpgradeActionKind",
    "UpgradeContext",
    "UpgradeTier",
    "UpgradeVerdict",
    "apply_auto",
    "assess",
    "migrations_between",
    "stamp",
]
