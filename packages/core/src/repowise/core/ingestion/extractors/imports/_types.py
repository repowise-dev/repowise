"""The per-language hooks that turn import query matches into ``Import`` records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter import Node

from ...models import Import

# (stmt_node, module_node, module_text, raw, language, src) -> imports
StatementFn = Callable[[Node, Node, str, str, str, str], list[Import]]


@dataclass(frozen=True)
class ImportExtractor:
    """One language's import rules; a ``None`` hook takes the shared default.

    ``statement`` maps one deduplicated statement to its imports.
    ``deduped`` replaces the shared raw-text dedup for languages that key a
    statement their own way, given a per-file ``seen`` set; those imports
    keep the default load kind. ``dedup_key`` refines the raw-text key, and
    ``finalize`` rewrites the file's whole import list once.
    """

    statement: StatementFn
    deduped: Callable[[Node, Node, str, set[str]], list[Import]] | None = None
    dedup_key: Callable[[Node, str, str], str] | None = None
    finalize: Callable[[list[Import]], list[Import]] | None = None
