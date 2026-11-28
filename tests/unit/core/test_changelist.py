"""Perforce changelist numbers read from a git-p4 or p4-fusion mirror's commit trailers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core import changelist
from repowise.core.changelist import (
    RevisionError,
    changelist_number,
    commit_for_changelist,
    resolve_revspec,
    without_changelist_number,
)

GIT_P4 = '[git-p4: depot-paths = "//depot/main/": change = {n}]'
GIT_P4_OPTIONS = (
    '[git-p4: depot-paths = "//depot/main/,//depot/lib/": change = {n}: options = ktext]'
)
P4_FUSION = '[p4-fusion: depot-paths = "//depot/main/": change = {n}]'


def test_each_mirror_trailer_names_its_changelist() -> None:
    assert changelist_number(f"Fix the loader\n\n{GIT_P4.format(n=1234567)}") == "1234567"
    assert changelist_number(f"Fix\n\n{GIT_P4_OPTIONS.format(n=88)}\n") == "88"
    assert changelist_number(f"88 - Fix\n{P4_FUSION.format(n=88)}") == "88"
    # git-p4 reads the line back with leading spaces allowed; so does this.
    assert changelist_number(f"Fix\n\n  {GIT_P4.format(n=5)}") == "5"
    # A one-line description leaves the trailer in git's subject, joined by a space.
    assert changelist_number(f"88 - Fix {P4_FUSION.format(n=88)}") == "88"


def test_no_trailer_or_one_quoted_mid_line_names_none() -> None:
    assert changelist_number("") is None
    assert changelist_number("fix: change = 12345") is None
    assert changelist_number(f"see {GIT_P4.format(n=12345)} for the import") is None
    assert changelist_number(f"see{GIT_P4.format(n=12345)}") is None
    assert changelist_number('[git-p4: depot-paths = "//depot/main/"]') is None
    # Both mirrors lead with the depot paths; a bracket without them is someone's note.
    assert changelist_number("[git-p4: change = 12345]") is None


def test_the_trailer_and_the_p4_fusion_lead_are_stripped_for_display() -> None:
    body = f"Longer reason.\n\n{GIT_P4.format(n=42)}\n"
    assert without_changelist_number(body) == "Longer reason."
    assert without_changelist_number("42 - Fix the loader", "42") == "Fix the loader"
    assert without_changelist_number(f"42 - Fix the loader {P4_FUSION.format(n=42)}", "42") == (
        "Fix the loader"
    )
    # A lead naming another number stays.
    assert without_changelist_number("43 - Fix the loader", "42") == "43 - Fix the loader"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str, message: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def mirror(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "mirror"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    shas = {
        "1234560": _commit(repo, "a", f"Add a\n\n{GIT_P4.format(n=1234560)}"),
        "123456": _commit(repo, "b", f"Add b\n\n{GIT_P4.format(n=123456)}"),
        "1234567": _commit(repo, "c", f"1234567 - Add c\n{P4_FUSION.format(n=1234567)}"),
    }
    return repo, shas


def test_a_changelist_resolves_to_the_commit_that_imported_it(mirror) -> None:
    repo, shas = mirror
    assert resolve_revspec(repo, "1234567") == shas["1234567"]
    # 12345 is a prefix of 123456's number, not that changelist.
    assert commit_for_changelist(repo, "123456") == shas["123456"]
    assert commit_for_changelist(repo, "12345") is None
    assert resolve_revspec(repo, "1234560..1234567") == f"{shas['1234560']}..{shas['1234567']}"
    assert resolve_revspec(repo, "1234560...HEAD") == f"{shas['1234560']}...HEAD"


def test_digits_naming_no_changelist_and_no_commit_are_refused(mirror) -> None:
    repo, _ = mirror
    with pytest.raises(RevisionError, match="changelist 7777777"):
        resolve_revspec(repo, "7777777")
    with pytest.raises(RevisionError):
        resolve_revspec(repo, "1234560..7777777")


def test_digits_git_reads_as_a_commit_and_other_revisions_pass_through(mirror) -> None:
    repo, shas = mirror
    _git(repo, "tag", "9999999", shas["123456"])
    assert resolve_revspec(repo, "9999999") == "9999999"
    for rev in ("HEAD", "HEAD~1", "main..HEAD", shas["123456"][:7], "nosuchref"):
        assert resolve_revspec(repo, rev) == rev


def test_more_than_ten_digits_is_no_changelist_and_is_left_to_git(mirror) -> None:
    repo, _ = mirror
    assert resolve_revspec(repo, "12345678901") == "12345678901"


def test_a_search_that_did_not_finish_is_not_reported_as_a_miss(
    mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = mirror

    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(changelist, "run_git", timeout)
    with pytest.raises(RevisionError, match="Could not search"):
        resolve_revspec(repo, "1234567")
