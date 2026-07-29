"""Resolution of retired wiki page ids onto their successors."""

from __future__ import annotations

import pytest

from repowise.core.generation.page_redirects import (
    SUPERSEDED_IDS,
    SUPERSEDED_TYPES,
    SupersededCycleError,
    SupersededTargetError,
    resolve_superseded,
)

# ---------------------------------------------------------------------------
# Page-type retirement — the target path is carried across unchanged
# ---------------------------------------------------------------------------


class TestTypeRetirement:
    def test_retired_type_keeps_its_target_path(self):
        """A whole page type folded into another keeps the repo it described."""
        resolved = resolve_superseded(
            "architecture_diagram:repowise",
            superseded_types={"architecture_diagram": "repo_overview"},
        )
        assert resolved == "repo_overview:repowise"

    def test_target_path_containing_colons_survives(self):
        """Ids split on the first colon only — paths may contain more."""
        resolved = resolve_superseded(
            "architecture_diagram:weird:repo:name",
            superseded_types={"architecture_diagram": "repo_overview"},
        )
        assert resolved == "repo_overview:weird:repo:name"

    def test_live_type_resolves_to_nothing(self):
        assert (
            resolve_superseded(
                "repo_overview:repowise",
                superseded_types={"architecture_diagram": "repo_overview"},
            )
            is None
        )


# ---------------------------------------------------------------------------
# Exact-id retirement — used where the target path is fixed, not per-repo
# ---------------------------------------------------------------------------


class TestExactIdRetirement:
    def test_retired_id_resolves_to_its_successor(self):
        resolved = resolve_superseded(
            "onboarding:onboarding/how_it_works",
            superseded_ids={
                "onboarding:onboarding/how_it_works": "onboarding:onboarding/guided_tour"
            },
        )
        assert resolved == "onboarding:onboarding/guided_tour"

    def test_exact_id_wins_over_type_rule(self):
        """A page-level retirement is more specific than a type-level one.

        Both rules match the same source id and disagree; the exact one is the
        deliberate statement about this one page and takes precedence.
        """
        resolved = resolve_superseded(
            "architecture_diagram:repowise",
            superseded_ids={"architecture_diagram:repowise": "onboarding:onboarding/guided_tour"},
            superseded_types={"architecture_diagram": "repo_overview"},
        )
        assert resolved == "onboarding:onboarding/guided_tour"


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class TestTransitiveChains:
    def test_chain_resolves_to_its_final_destination(self):
        """A page retired twice lands on the page that is actually alive."""
        resolved = resolve_superseded(
            "onboarding:onboarding/a",
            superseded_ids={
                "onboarding:onboarding/a": "onboarding:onboarding/b",
                "onboarding:onboarding/b": "onboarding:onboarding/c",
            },
        )
        assert resolved == "onboarding:onboarding/c"

    def test_chain_across_both_rule_kinds(self):
        resolved = resolve_superseded(
            "architecture_diagram:repowise",
            superseded_types={"architecture_diagram": "repo_overview"},
            superseded_ids={"repo_overview:repowise": "onboarding:onboarding/overview"},
        )
        assert resolved == "onboarding:onboarding/overview"


# ---------------------------------------------------------------------------
# Loud failures.  A redirect table that quietly points nowhere strands every
# inbound link that depended on it, and reads as a pass.
# ---------------------------------------------------------------------------


class TestFailuresAreLoud:
    def test_direct_cycle_raises(self):
        with pytest.raises(SupersededCycleError) as exc:
            resolve_superseded(
                "onboarding:onboarding/a",
                superseded_ids={
                    "onboarding:onboarding/a": "onboarding:onboarding/b",
                    "onboarding:onboarding/b": "onboarding:onboarding/a",
                },
            )
        assert "onboarding:onboarding/a" in str(exc.value)

    def test_self_cycle_raises(self):
        with pytest.raises(SupersededCycleError):
            resolve_superseded(
                "onboarding:onboarding/a",
                superseded_ids={"onboarding:onboarding/a": "onboarding:onboarding/a"},
            )

    def test_type_level_cycle_raises(self):
        with pytest.raises(SupersededCycleError):
            resolve_superseded(
                "architecture_diagram:repowise",
                superseded_types={
                    "architecture_diagram": "repo_overview",
                    "repo_overview": "architecture_diagram",
                },
            )

    def test_successor_without_a_page_type_raises(self):
        """A successor id must itself be ``{page_type}:{target_path}``."""
        with pytest.raises(SupersededTargetError):
            resolve_superseded(
                "onboarding:onboarding/a",
                superseded_ids={"onboarding:onboarding/a": "no-colon-here"},
            )

    def test_malformed_page_id_raises(self):
        with pytest.raises(SupersededTargetError):
            resolve_superseded("no-colon-here", superseded_types={"a": "b"})

    def test_empty_page_id_raises(self):
        with pytest.raises(SupersededTargetError):
            resolve_superseded("", superseded_types={"a": "b"})


# ---------------------------------------------------------------------------
# The shipped tables
# ---------------------------------------------------------------------------


class TestShippedTables:
    def test_unknown_id_resolves_to_nothing_against_the_real_tables(self):
        """A live page must never be redirected away from itself."""
        assert resolve_superseded("file_page:packages/core/pyproject.toml") is None

    def test_shipped_tables_are_internally_consistent(self):
        """Every retired id in the shipped tables resolves without raising.

        This is the guard that keeps a future entry from shipping a cycle or a
        malformed successor.  It walks what is actually shipped, so it cannot
        drift from the tables the way a hand-written list would.
        """
        for retired_id in SUPERSEDED_IDS:
            assert resolve_superseded(retired_id) is not None
        for retired_type in SUPERSEDED_TYPES:
            assert resolve_superseded(f"{retired_type}:some-repo") is not None

    def test_no_retired_type_is_also_a_successor_of_itself(self):
        for retired_type, successor in SUPERSEDED_TYPES.items():
            assert retired_type != successor
