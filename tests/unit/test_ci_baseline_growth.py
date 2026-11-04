"""A baseline only shrinks: entries added over the same file at the change's base."""

from __future__ import annotations

import json
import subprocess

import pytest

from repowise.cli.ci import CannotEvaluateError
from repowise.cli.ci import baseline_growth as growth_since_base
from repowise.core.ci.baseline import BaselineError, baseline_growth


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _doc(*fingerprints: str) -> str:
    return json.dumps({"version": 1, "entries": [{"fingerprint": f} for f in fingerprints]})


def _bytes(*fingerprints: str) -> bytes:
    return _doc(*fingerprints).encode("utf-8")


def _entries(*fingerprints: str) -> list[dict]:
    return [{"fingerprint": f} for f in fingerprints]


def test_growth_names_only_what_the_base_lacks():
    assert baseline_growth(_bytes("a", "b"), _bytes("a", "c")) == _entries("c")


def test_removed_and_unchanged_entries_are_not_growth():
    assert baseline_growth(_bytes("a", "b"), _bytes("a")) == []
    assert baseline_growth(_bytes("b", "a"), _bytes("a", "b")) == []


def test_a_baseline_missing_at_the_base_or_the_head_is_not_growth():
    assert baseline_growth(None, _bytes("a")) == []
    assert baseline_growth(_bytes("a"), None) == []


@pytest.mark.parametrize(
    ("base", "head", "match"),
    [
        (b"{not json", _bytes("a"), "x.json at base is not valid JSON"),
        (_bytes("a"), b'{"version": 2, "entries": []}', "has version 2"),
        (_bytes("a"), b"\xff", "cannot read baseline x.json"),
        (_bytes("a"), b'{"version": 1, "entries": [{}]}', "has no fingerprint"),
    ],
)
def test_a_malformed_side_is_refused(base, head, match):
    with pytest.raises(BaselineError, match=match):
        baseline_growth(base, head, name="x.json")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@t.co"), ("user.name", "t"), ("core.autocrlf", "false")):
        _git(tmp_path, "config", key, value)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _commit_baseline(repo, text: str) -> None:
    (repo / "baseline.json").write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")


def test_the_checkout_is_compared_with_the_merge_base(repo):
    _commit_baseline(repo, _doc("a", "b"))
    _git(repo, "switch", "-qc", "feature")
    (repo / "baseline.json").write_text(_doc("b", "c"), encoding="utf-8")
    grown = growth_since_base(repo, repo / "baseline.json", "main...HEAD")
    assert grown == _entries("c")


def test_a_root_below_the_git_toplevel_reads_its_own_baseline(repo):
    package = repo / "pkg"
    package.mkdir()
    (package / "baseline.json").write_text(_doc("a"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "package baseline")
    _git(repo, "switch", "-qc", "feature")
    (package / "baseline.json").write_text(_doc("a", "n"), encoding="utf-8")
    grown = growth_since_base(package, package / "baseline.json", "main...HEAD")
    assert grown == _entries("n")


def test_a_baseline_new_in_the_change_is_not_growth(repo):
    _git(repo, "switch", "-qc", "feature")
    _commit_baseline(repo, _doc("a"))
    assert growth_since_base(repo, repo / "baseline.json", "main...HEAD") == []


def test_staged_changes_compare_with_head(repo):
    _commit_baseline(repo, _doc("a"))
    (repo / "baseline.json").write_text(_doc("a", "n"), encoding="utf-8")
    assert growth_since_base(repo, repo / "baseline.json", None) == _entries("n")


def test_a_malformed_base_file_cannot_evaluate(repo):
    _commit_baseline(repo, "{not json")
    _git(repo, "switch", "-qc", "feature")
    (repo / "baseline.json").write_text(_doc("a"), encoding="utf-8")
    with pytest.raises(CannotEvaluateError) as exc:
        growth_since_base(repo, repo / "baseline.json", "main...HEAD")
    assert exc.value.code == "baseline_unreadable"


def test_an_unknown_base_cannot_evaluate(repo):
    with pytest.raises(CannotEvaluateError) as exc:
        growth_since_base(repo, repo / "baseline.json", "nope...HEAD")
    assert exc.value.code == "base_not_found"


def test_a_baseline_outside_the_repository_cannot_evaluate(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "baseline.json"
    with pytest.raises(CannotEvaluateError) as exc:
        growth_since_base(repo, outside, "main...HEAD")
    assert exc.value.code == "baseline_outside_repository"
