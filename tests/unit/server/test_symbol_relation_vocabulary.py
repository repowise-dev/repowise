"""A relation kind must be named on the page, not filed under "Called by".

Three copies of one vocabulary: the engine's ``SYMBOL_USE_EDGE_TYPES``, the
server's ``SYMBOL_RELATION_GROUPS`` partition of it, and the label table in
``packages/ui/src/graph/symbol-relations.ts``. The failure this pins is not
hypothetical — the whole set was rendered under one heading, so ``extends``
was presented as a call for as long as the endpoint existed.

An edge type added to the engine and to neither of the others degrades
silently: it is fetched, grouped by the fallthrough, and captioned with its
raw verb. These tests make it fail loudly at the two places a human would
otherwise have to remember.

Ceiling: a regex read of the TypeScript table's keys. It proves the *key set*
matches, not that the file parses.
"""

from __future__ import annotations

import pathlib
import re

from repowise.core.ingestion.models import SYMBOL_USE_EDGE_TYPES
from repowise.server.schemas.intelligence import SYMBOL_RELATION_GROUPS

_LABELS = (
    pathlib.Path(__file__).resolve().parents[3] / "packages/ui/src/graph/symbol-relations.ts"
)


def _label_table_keys() -> set[str]:
    """The edge types keyed in `RELATION_LABELS`."""
    source = _LABELS.read_text(encoding="utf-8")
    match = re.search(
        r"const RELATION_LABELS: [^=]+= \{(.*?)\n\};", source, re.DOTALL
    )
    assert match, f"RELATION_LABELS is not declared in {_LABELS.name}"
    return set(re.findall(r"^  (\w+): \{", match.group(1), re.MULTILINE))


def test_groups_partition_the_engine_set() -> None:
    """Every use edge type is in exactly one group, and no group invents one."""
    members = [t for types in SYMBOL_RELATION_GROUPS.values() for t in types]
    assert set(members) == set(SYMBOL_USE_EDGE_TYPES)
    assert len(members) == len(set(members)), "an edge type is in two groups"


def test_every_non_call_relation_has_a_label() -> None:
    """A relation the page can show is a relation the page can name."""
    non_call = set(SYMBOL_USE_EDGE_TYPES) - SYMBOL_RELATION_GROUPS["call"]
    assert _label_table_keys() == non_call


def test_calls_are_not_a_labelled_relation() -> None:
    """`calls` renders as the call graph itself, not as a relation section."""
    assert "calls" not in _label_table_keys()
