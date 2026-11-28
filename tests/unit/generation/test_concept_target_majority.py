"""A page is targeted at the directory holding most of its files."""

from __future__ import annotations

from repowise.core.generation.concept_tree.grouping import group_files


def test_a_group_is_targeted_at_the_directory_holding_most_of_it() -> None:
    """The title names the majority directory, so the page id must as well.

    Taken in path order, a page about ``dead_code`` (most of its files) was
    targeted at ``change_risk``, which sorted first, and read as a title that
    did not match its path.
    """
    files = [f"pkg/analysis/change_risk/r{i}.py" for i in range(2)] + [
        f"pkg/analysis/dead_code/d{i}.py" for i in range(6)
    ]
    groups = group_files(files)
    assert [g.target_path for g in groups] == ["pkg/analysis/dead_code"]
