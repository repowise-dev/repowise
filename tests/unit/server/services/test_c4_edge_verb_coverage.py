"""The C4 verb map must stay total over the edge-type vocabulary.

`relation_label` falls through to "depends on" for an unknown type, and that
fall-through is silent: `framework` and `dynamic_uses` rendered that way for
their whole life, covering 10% of file-level edges on a live index, and
`type_use` (6,596 rows across 42 local indexes) still did until this test
existed.

Lives here rather than beside the other vocabulary tests in
`tests/unit/ingestion/` because it imports `repowise.server` — an ingestion
test that hard-fails when only the core package is installed is a worse trade
than a slightly split test suite.
"""

from __future__ import annotations

from repowise.core.ingestion.models import EDGE_TYPE_VALUES
from repowise.server.services.c4_builder.labels import _EDGE_VERB, _VERB_PRIORITY


def test_every_edge_type_has_a_verb() -> None:
    missing = sorted(EDGE_TYPE_VALUES - _EDGE_VERB.keys())
    assert not missing, (
        f"edge type(s) with no C4 verb — they render as the vague 'depends on': {missing}"
    )


def test_no_verb_is_keyed_on_a_type_nothing_emits() -> None:
    dead = sorted(_EDGE_VERB.keys() - EDGE_TYPE_VALUES)
    assert not dead, f"C4 verb(s) keyed on a type nothing emits: {dead}"


def test_every_verb_can_be_ranked() -> None:
    """An aggregated edge picks the highest-priority verb, so an unranked one never wins."""
    unranked = sorted(set(_EDGE_VERB.values()) - set(_VERB_PRIORITY))
    assert not unranked, f"verb(s) missing from _VERB_PRIORITY: {unranked}"
