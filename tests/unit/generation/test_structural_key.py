"""Properties of the structural key, and the one list it shares with the sweep."""

from __future__ import annotations

from repowise.core.generation.models import (
    STRUCTURALLY_KEYED_PAGE_TYPES,
    member_structural_key,
    scc_page_slug,
)


class TestMemberStructuralKey:
    def test_member_order_does_not_matter(self):
        assert member_structural_key(["b.py", "a.py"], prefix="grp") == member_structural_key(
            ["a.py", "b.py"], prefix="grp"
        )

    def test_different_members_give_different_keys(self):
        assert member_structural_key(["a.py", "b.py"], prefix="grp") != member_structural_key(
            ["a.py", "c.py"], prefix="grp"
        )

    def test_membership_change_moves_the_key(self):
        """Intended: the page now covers a different thing, so it is a new page."""
        assert member_structural_key(["a.py"], prefix="grp") != member_structural_key(
            ["a.py", "b.py"], prefix="grp"
        )

    def test_prefix_separates_namespaces(self):
        """The same members under two groupings are not the same page."""
        assert member_structural_key(["a.py"], prefix="scc") != member_structural_key(
            ["a.py"], prefix="grp"
        )

    def test_key_carries_no_path_syntax(self):
        """Ids flow into target_path, which the answer layer treats as a file
        target unless it is obviously not one."""
        key = member_structural_key(["pkg/a.py", "pkg/b.py"], prefix="grp")
        assert key.startswith("grp-")
        assert "/" not in key and "." not in key

    def test_accepts_any_iterable(self):
        assert member_structural_key(iter(["b.py", "a.py"]), prefix="grp") == (
            member_structural_key({"a.py", "b.py"}, prefix="grp")
        )


class TestSccSlugUnchanged:
    """The slug was the original of this pattern; generalising it must not
    move any existing SCC page id."""

    def test_slug_is_the_member_key_under_its_own_prefix(self):
        members = ["pkg/a.py", "pkg/b.py"]
        assert scc_page_slug(members) == member_structural_key(members, prefix="scc")

    def test_slug_value_is_pinned(self):
        """Pinned literal, so a refactor that changes the hash is loud.

        Every SCC page in every existing store is keyed on this exact value.
        """
        assert scc_page_slug(["a.py", "b.py"]) == "scc-c643247e56f2"


def test_the_sweep_uses_the_same_list_generation_stamps():
    """A type in one list and not the other is the doubling bug itself."""
    from repowise.core.pipeline.persist import _SWEPT_GENERATED_PAGE_TYPES

    assert _SWEPT_GENERATED_PAGE_TYPES is STRUCTURALLY_KEYED_PAGE_TYPES
