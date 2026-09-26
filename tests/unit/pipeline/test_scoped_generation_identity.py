"""Repository identity stays stable across renamed checkout directories."""

from repowise.core.generation.page_selection import PageRecord
from repowise.core.pipeline.scoped_generation import _canonical_repo_name


def _record(page_id: str, page_type: str, target_path: str) -> PageRecord:
    return PageRecord(page_id, page_type, target_path, is_template=False)


def test_overview_target_is_the_canonical_scoped_generation_name() -> None:
    records = [
        _record("repo_overview:repowise", "repo_overview", "rw-upper"),
        _record("onboarding:onboarding/overview", "onboarding", "onboarding/overview"),
    ]

    assert _canonical_repo_name(records, "rw-upper") == "repowise"


def test_checkout_name_is_used_without_one_unambiguous_overview() -> None:
    no_overview = [_record("file_page:a.py", "file_page", "a.py")]
    conflicting = [
        _record("repo_overview:one", "repo_overview", "one"),
        _record("repo_overview:two", "repo_overview", "two"),
    ]

    assert _canonical_repo_name(no_overview, "checkout") == "checkout"
    assert _canonical_repo_name(conflicting, "checkout") == "checkout"
