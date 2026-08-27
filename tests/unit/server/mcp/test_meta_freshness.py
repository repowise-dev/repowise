"""Target-scoped staleness in the ``_meta`` envelope.

"Silence means current" only trains agent trust if ``stale_warning`` fires
when the served content is actually affected. These tests pin the contract:

  * HEAD match → prose silence, and ``index_behind: false``.
  * HEAD mismatch + a served target changed → ``stale_warning``.
  * HEAD mismatch + no served target changed → ``index_behind: true`` only.
  * HEAD mismatch + no targets passed (repo-level tools) → repo-level warning.
  * git can't diff the two SHAs → fall back to the repo-level warning
    (fail toward warning, never toward false silence).

``index_behind`` is two-valued wherever the live-vs-indexed comparison ran, and
absent only where it could not run. It used to be written on the true branches
only, which made the key unusable as a rate: every consumer that averaged it saw
100% stale because a false was indistinguishable from a tool that never checks.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from repowise.server.mcp_server import _meta

_INDEXED = "a" * 40
_LIVE = "b" * 40


@pytest.fixture(autouse=True)
def _clear_changed_files_cache():
    _meta._changed_files_cache.clear()
    yield
    _meta._changed_files_cache.clear()


def _repo(tmp_path, head_commit: str = _INDEXED):
    return types.SimpleNamespace(updated_at=None, local_path=str(tmp_path), head_commit=head_commit)


def _prime(tmp_path, changed: frozenset[str] | None):
    _meta._changed_files_cache[(str(tmp_path), _INDEXED, _LIVE)] = changed


def test_head_match_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _INDEXED)
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=["a.py"])
    assert "stale_warning" not in out
    assert out["index_behind"] is False
    assert out["live_head"] == _INDEXED[:12]


def test_no_git_signal_omits_index_behind_entirely(tmp_path, monkeypatch):
    """Absence means "not evaluated", which is not the same as false.

    Without a live HEAD the comparison never runs, so claiming ``false`` here
    would report a repo as verified-current on no evidence at all.
    """
    monkeypatch.setattr(_meta, "read_live_head", lambda p: None)
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=["a.py"])
    assert "index_behind" not in out


def test_served_target_changed_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, frozenset({"src/a.py"}))
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=["src/a.py"])
    assert "stale_warning" in out
    # The sharper signal does not suppress the measurable one.
    assert out["index_behind"] is True


def test_unaffected_targets_get_index_behind_not_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, frozenset({"docs/README.md"}))
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=["src/a.py"])
    assert "stale_warning" not in out
    assert out.get("index_behind") is True
    assert out.get("live_head") == _LIVE[:12]


def test_no_targets_keeps_repo_level_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=None)
    assert "stale_warning" in out


def test_empty_targets_means_nothing_served_and_never_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, frozenset({"src/a.py"}))
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=[])
    assert "stale_warning" not in out
    assert out.get("index_behind") is True


def test_no_targets_with_zero_changed_files_does_not_warn(tmp_path, monkeypatch):
    """HEAD moved but the trees are identical — nothing anywhere can be stale.

    Regression (N4): a repo-level response took the "no targets" branch before
    ever asking git *what* changed, so an empty commit (or a merge that changed
    nothing) produced ``stale_warning`` on a repo that was completely current.
    A warning that fires when zero files changed is the loudest possible way to
    be wrong, and it trains an agent to stop reading the field.
    """
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, frozenset())
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=None)
    assert "stale_warning" not in out
    assert out.get("index_behind") is True
    assert out.get("live_head") == _LIVE[:12]


def test_no_targets_with_real_changes_still_warns(tmp_path, monkeypatch):
    """The zero-change carve-out must not silence a genuinely behind index."""
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, frozenset({"src/a.py"}))
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=None)
    assert "stale_warning" in out
    assert out["index_behind"] is True


def test_empty_commit_over_real_git_is_not_stale(tmp_path):
    """End to end against git, since the carve-out turns on a real empty diff."""

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        )

    def sha():
        return subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "one")
    indexed = sha()
    git("commit", "-q", "--allow-empty", "-m", "empty")
    live = sha()

    assert live != indexed
    assert _meta._changed_files_between(str(tmp_path), indexed, live) == frozenset()
    repo = types.SimpleNamespace(updated_at=None, local_path=str(tmp_path), head_commit=indexed)
    out = _meta.freshness_from_repo(repo, targets=None)
    assert "stale_warning" not in out
    assert out.get("index_behind") is True


def test_git_diff_failure_falls_back_to_repo_level_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(_meta, "read_live_head", lambda p: _LIVE)
    _prime(tmp_path, None)  # git couldn't answer (rebased-away SHA, timeout)
    out = _meta.freshness_from_repo(_repo(tmp_path), targets=["src/a.py"])
    assert "stale_warning" in out


@pytest.mark.parametrize(
    ("target", "changed", "hit"),
    [
        ("src/a.py::MyClass", {"src/a.py"}, True),  # symbol id → file
        ("src/a.py:10-40", {"src/a.py"}, True),  # range read → file
        ("src\\a.py", {"src/a.py"}, True),  # backslashes normalize
        ("src", {"src/a.py"}, True),  # module dir → prefix
        ("./src/a.py", {"src/a.py"}, True),  # leading ./ stripped
        ("src/a.py", {"src/other.py"}, False),
        ("srcx", {"src/a.py"}, False),  # prefix must be a dir boundary
    ],
)
def test_target_intersection_shapes(target, changed, hit):
    assert _meta.targets_hit_by_changes([target], frozenset(changed)) is hit


def test_changed_files_between_real_git(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "one")
    sha1 = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "two")
    sha2 = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    changed = _meta._changed_files_between(str(tmp_path), sha1, sha2)
    assert changed == frozenset({"b.py"})
    # Unknown SHA → None (fail toward warning), and the miss is cached too.
    assert _meta._changed_files_between(str(tmp_path), "f" * 40, sha2) is None


def test_confident_answer_is_never_told_retrieval_found_nothing() -> None:
    """A high-confidence answer empties ``retrieval`` deliberately, so the hint
    must never read that block's length as a count of what retrieval found.
    Every cached reply re-derives its hint from the stored payload, which is
    where such a misreading would reach the most trustworthy answers we send."""
    from repowise.server.mcp_server._meta import answer_hint

    assert answer_hint("high") is None
    assert answer_hint("medium") is None
    assert "Read the listed fallback_targets" in (answer_hint("low") or "")


def test_no_answer_hint_names_an_external_tool() -> None:
    """A dead end redirects into our own surface; only the exhaustive-sweep
    wording may name an external tool, and it is not a get_answer hint."""
    from repowise.server.mcp_server._meta import NO_HITS_RECOVERY_HINT, answer_hint

    hints = [answer_hint(c) for c in ("high", "medium", "low")]
    hints += [
        answer_hint("low", degraded="no-llm-provider", retrieval_quality=q)
        for q in ("high", "partial", "weak")
    ]
    assert not [h for h in hints if h and "Grep" in h]
    assert "Grep" not in NO_HITS_RECOVERY_HINT
    assert "search_codebase" in NO_HITS_RECOVERY_HINT
