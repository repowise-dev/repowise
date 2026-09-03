"""The Python half of the decision-vocabulary contract.

``tests/fixtures/decision_vocabulary.json`` is the one list every surface
derives from. This file checks it against the registry it was generated from;
``packages/types/__tests__/decisions-vocabulary.test.ts`` checks the same file
against the TypeScript consts. A source, status, currency or review state added
to one side without the other fails here or there rather than drifting.

The drift is not hypothetical. Before this test the TypeScript union named a
retired ``readme_mining`` and omitted five live sources, the retired set was
hand-mirrored in ``packages/ui`` with a comment saying so, and two of the four
status ladders disagreed about where ``superseded`` sat.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repowise.core.analysis.decisions import lifecycle, policy, provenance
from repowise.core.persistence.crud import decisions as crud_decisions

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = json.loads(
    (_ROOT / "tests/fixtures/decision_vocabulary.json").read_text(encoding="utf-8")
)


def test_sources_match_the_listable_registry() -> None:
    assert _FIXTURE["sources"] == list(provenance.LISTABLE_SOURCES)


def test_retired_sources_match_provenance() -> None:
    assert _FIXTURE["retired_sources"] == list(provenance.RETIRED_SOURCES)


def test_no_source_is_both_live_and_retired() -> None:
    assert not set(_FIXTURE["sources"]) & set(_FIXTURE["retired_sources"])


def test_every_source_has_a_confidence_rank() -> None:
    """A source the ranker has never heard of floors to 1 and sorts last."""
    for source in _FIXTURE["sources"] + _FIXTURE["retired_sources"]:
        assert source in provenance.SOURCE_RANK, source


def test_statuses_match_the_one_ladder() -> None:
    assert _FIXTURE["statuses"] == list(lifecycle.DECISION_STATUS_ORDER)


def test_status_ladder_covers_every_storable_status() -> None:
    assert set(_FIXTURE["statuses"]) == set(crud_decisions._VALID_DECISION_STATUSES)


def test_list_ordering_derives_from_the_ladder() -> None:
    """The ORDER BY rank is the ladder minus the status the query excludes."""
    expected = {
        status: rank
        for rank, status in enumerate(_FIXTURE["statuses"])
        if status != "dismissed"
    }
    assert expected == crud_decisions._STATUS_RANK


def test_currencies_match_lifecycle() -> None:
    assert _FIXTURE["currencies"] == list(lifecycle.DECISION_CURRENCIES)
    assert _FIXTURE["stored_currencies"] == list(lifecycle.STORED_CURRENCIES)


def test_stored_currencies_are_a_subset_of_the_vocabulary() -> None:
    assert set(_FIXTURE["stored_currencies"]) <= set(_FIXTURE["currencies"])


def test_review_states_and_actions_match_lifecycle() -> None:
    assert _FIXTURE["candidate_review_states"] == list(lifecycle.CANDIDATE_REVIEW_STATES)
    assert _FIXTURE["acceptance_actions"] == list(lifecycle.ACCEPTANCE_ACTIONS)


def test_review_lanes_match_lifecycle() -> None:
    assert _FIXTURE["review_lanes"] == list(lifecycle.REVIEW_LANES)


def test_review_lanes_are_the_currencies_plus_candidates() -> None:
    """The five partition a repository, so they must cover the vocabulary."""
    lanes = set(_FIXTURE["review_lanes"])
    assert lanes == (set(_FIXTURE["currencies"]) | {"candidates"}) - {
        "superseded",
        "dismissed",
    } | {"history"}


def test_the_acceptance_blocker_sentences_are_stable() -> None:
    """The TypeScript mirror copies these strings; a reword must break it.

    A review surface predicts a refusal with the mirror and reports a real one
    from the API, and the two have to read identically or the same problem gets
    two names.
    """
    blockers = lifecycle.acceptance_blockers(
        lifecycle.AcceptanceRequirement(reason="", scope=[], evidence=[])
    )
    assert blockers == [
        "no rationale or explicit constraint reason",
        "no scope: name the files or modules it governs",
        "no evidence reference",
        "no accepter or tracked-artifact identity",
    ]


def test_capture_sources_and_presets_match_policy() -> None:
    assert _FIXTURE["capture_sources"] == list(policy.CAPTURE_SOURCE_KEYS)
    assert _FIXTURE["presets"] == list(policy.PRESET_NAMES)


def test_every_capture_source_is_declared_in_the_registry() -> None:
    declared = {spec.key for spec in policy.SOURCE_SPECS}
    assert set(_FIXTURE["capture_sources"]) <= declared


def test_typescript_declares_the_same_words() -> None:
    """Guards the TypeScript half for a checkout with no node toolchain.

    The vitest suite is the real assertion; this one keeps a Python-only CI
    lane from passing a fixture the TypeScript side has not adopted.
    """
    ts = (_ROOT / "packages/types/src/decisions.ts").read_text(encoding="utf-8")
    for const, key in (
        ("DECISION_SOURCES", "sources"),
        ("RETIRED_DECISION_SOURCES", "retired_sources"),
        ("DECISION_STATUSES", "statuses"),
        ("DECISION_CURRENCIES", "currencies"),
        ("CANDIDATE_REVIEW_STATES", "candidate_review_states"),
        ("DECISION_LANES", "review_lanes"),
        ("DECISION_PRESETS", "presets"),
    ):
        match = re.search(rf"export const {const} = \[(.*?)\] as const;", ts, re.S)
        assert match is not None, f"{const} is not declared in decisions.ts"
        members = re.findall(r'"([^"]+)"', match.group(1))
        assert members == _FIXTURE[key], const
