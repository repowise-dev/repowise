"""The scope line must name a directory the group is actually under.

``deterministic_scope`` phrased its boundary as "under ``target_path``", but
``target_path`` is one of the group's own directories, not their common
ancestor: ``_assign_targets`` falls through to the first sorted member when
the true ancestor is not itself a member. A group spanning ``src/lib/a`` …
``src/lib/d`` is targeted at ``src/lib/a``, so the page read "4 directories
under src/lib/a" directly above a membership line naming three directories
that are not under it.
"""

from __future__ import annotations

from repowise.core.generation.concept_tree.grouping import ConceptGroup
from repowise.core.generation.concept_tree.naming import deterministic_scope


def _group(dirs: list[str], target_path: str) -> ConceptGroup:
    members = [f"{d}/mod{i}.py" for i, d in enumerate(dirs)]
    return ConceptGroup(members=members, dirs=dirs, target_path=target_path)


def test_scope_names_the_common_ancestor_not_the_target() -> None:
    dirs = ["src/lib/a", "src/lib/b", "src/lib/c", "src/lib/d"]
    line = deterministic_scope(_group(dirs, target_path="src/lib/a"))

    assert "4 directories under src/lib." in line
    # The target is not a prefix of the other three, so it must not be the
    # boundary the sentence claims.
    assert "under src/lib/a" not in line


def test_scope_drops_the_clause_when_nothing_is_shared() -> None:
    """Siblings of the repository root share no ancestor worth naming, and
    "under the repository root" says nothing the next sentence does not."""
    line = deterministic_scope(_group(["src/app", "lib/util"], target_path="src/app"))

    assert "2 directories." in line
    assert "under" not in line.split(".")[0]


def test_single_directory_is_unchanged() -> None:
    line = deterministic_scope(_group(["src/lib/a"], target_path="src/lib/a"))
    assert "in src/lib/a." in line


def test_repository_root_single_directory_is_unchanged() -> None:
    line = deterministic_scope(_group([""], target_path=""))
    assert "in the repository root." in line
