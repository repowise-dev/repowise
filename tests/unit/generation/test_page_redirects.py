"""Resolution of retired wiki page ids onto their successors."""

from __future__ import annotations

import pytest

from repowise.core.generation.page_redirects import (
    RETIRED_IDS,
    SUPERSEDED_IDS,
    SUPERSEDED_IDS_TO_REPO_WIDE,
    SUPERSEDED_TYPES,
    SupersededCycleError,
    SupersededTargetError,
    repo_wide_successor_type,
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

    def test_every_retired_id_resolves_somewhere(self):
        """Whichever table names it, a retired id must not dead-end.

        Walks ``RETIRED_IDS`` rather than either table on its own, so an id
        added to one of them cannot be left with no destination.
        """
        for retired_id in sorted(RETIRED_IDS):
            landed = resolve_superseded(retired_id) or repo_wide_successor_type(retired_id)
            assert landed, f"{retired_id} is retired but resolves to nothing"

    def test_the_two_id_tables_do_not_both_claim_an_id(self):
        """One id, one destination — two rules for it is a table bug."""
        assert not set(SUPERSEDED_IDS) & set(SUPERSEDED_IDS_TO_REPO_WIDE)


# ---------------------------------------------------------------------------
# The architecture map moved onto the overview
# ---------------------------------------------------------------------------


class TestArchitectureDiagramRetirement:
    def test_the_retired_page_resolves_to_the_overview(self):
        assert resolve_superseded("architecture_diagram:repowise") == "repo_overview:repowise"

    def test_it_resolves_for_any_repository(self):
        """The rule is a type rewrite, so it cannot be repo-specific."""
        for repo in ("repowise", "some/other-repo", "a"):
            assert resolve_superseded(f"architecture_diagram:{repo}") == f"repo_overview:{repo}"

    def test_the_overview_itself_is_not_redirected(self):
        assert resolve_superseded("repo_overview:repowise") is None


# ---------------------------------------------------------------------------
# Three orientation pages retired onto the overview
# ---------------------------------------------------------------------------


RETIRED_ORIENTATION_IDS = [
    "onboarding:onboarding/guided_tour",
    "onboarding:onboarding/codebase_map",
    "onboarding:onboarding/development_guide",
]

SURVIVING_ORIENTATION_IDS = [
    "onboarding:onboarding/getting_started",
    "onboarding:onboarding/key_concepts",
    "onboarding:onboarding/how_it_works",
    "onboarding:onboarding/active_landscape",
    "onboarding:onboarding/glossary",
]


class TestOrientationRetirement:
    """The id-keyed repo-wide shape, which these three are the first users of.

    The successor is the overview, whose id carries the repository name, so it
    cannot be written as a literal successor id.  And the retired pages share
    ``page_type='onboarding'`` with the five that survive, so the rule cannot
    be keyed on type either.  Both halves are asserted here because getting
    either wrong silently takes out the whole orientation collection.
    """

    @pytest.mark.parametrize("page_id", RETIRED_ORIENTATION_IDS)
    def test_a_retired_slot_hands_off_to_the_overview(self, page_id):
        assert repo_wide_successor_type(page_id) == "repo_overview"

    @pytest.mark.parametrize("page_id", RETIRED_ORIENTATION_IDS)
    def test_a_retired_slot_has_no_literal_successor(self, page_id):
        """It resolves repo-wide instead, which the serving layer handles."""
        assert resolve_superseded(page_id) is None

    @pytest.mark.parametrize("page_id", SURVIVING_ORIENTATION_IDS)
    def test_a_surviving_slot_is_not_redirected(self, page_id):
        """The regression that a type-keyed rule would cause."""
        assert repo_wide_successor_type(page_id) is None
        assert resolve_superseded(page_id) is None

    def test_the_retired_set_is_exactly_these_three(self):
        assert sorted(RETIRED_IDS) == sorted(RETIRED_ORIENTATION_IDS)
