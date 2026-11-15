"""`repowise update` mines the PR bodies and commits it brought in, gated like init."""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from repowise.cli.commands.update_cmd import decision_passes
from repowise.core.analysis.decisions import evolution
from repowise.core.analysis.decisions import extractor as extractor_mod
from repowise.core.analysis.decisions.commit_mining import restrict_to_commits
from repowise.core.analysis.decisions.policy import preset_policy
from repowise.core.analysis.decisions.records import DecisionExtractionReport, ExtractedDecision


def _meta(*shas: str) -> dict:
    return {"significant_commits_json": json.dumps([{"sha": sha} for sha in shas])}


def test_restrict_keeps_only_the_named_commits_and_drops_empty_files():
    meta_map = {"a.py": _meta("aaaa1111", "bbbb2222"), "b.py": _meta("cccc3333")}

    narrowed = restrict_to_commits(meta_map, {"bbbb2222"})

    assert list(narrowed) == ["a.py"]
    assert narrowed["a.py"]["significant_commits_json"] == [{"sha": "bbbb2222"}]
    # The caller's map is not edited.
    assert json.loads(meta_map["a.py"]["significant_commits_json"])[0]["sha"] == "aaaa1111"


def test_restrict_matches_a_full_sha_by_its_prefix():
    narrowed = restrict_to_commits({"a.py": _meta("bbbb2222" + "f" * 32)}, {"bbbb2222"})

    assert list(narrowed) == ["a.py"]


class _StubExtractor:
    calls: ClassVar[list[dict]] = []

    def __init__(self, **kwargs) -> None:
        self.git_meta_map = kwargs["git_meta_map"]

    async def extract_all(self, *, enabled_sources):
        _StubExtractor.calls.append(
            {"sources": set(enabled_sources), "files": sorted(self.git_meta_map)}
        )
        found = ExtractedDecision(title="Mined", decision="d", source="pr")
        return DecisionExtractionReport(total_found=1, decisions=[found], by_source={"pr": 1})


@pytest.fixture
def stubbed(monkeypatch):
    _StubExtractor.calls = []
    monkeypatch.setattr(extractor_mod, "DecisionExtractor", _StubExtractor)
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: {"bbbb2222"})
    from repowise.core import git_refs

    # The fake shas below name no commit; treat every stored pointer as reachable.
    monkeypatch.setattr(git_refs, "is_ancestor", lambda *_a: True)
    return _StubExtractor.calls


def _run_and_docs(*, git_changed: bool = False, provider: object | None = object()):
    run = SimpleNamespace(
        repo_path=Path("."),
        base_ref="base",
        head="head",
        config=SimpleNamespace(git_changed=git_changed),
        git_meta_map={"old.py": _meta("aaaa1111"), "new.py": _meta("bbbb2222")},
        graph_builder=SimpleNamespace(graph=lambda: None),
        degraded=[],
        state={},
    )
    docs = SimpleNamespace(
        provider=provider,
        cost_tracker=SimpleNamespace(record_as=lambda _op: nullcontext()),
        new_decision_markers=[],
    )
    return run, docs


def _mine(run, docs, policy=None) -> None:
    opts = SimpleNamespace(verbose=False)
    policy = policy or preset_policy("default").with_update_mining(True)
    provider = docs.provider if policy.update_llm else None
    decision_passes._mine_new_history(opts, run, docs, policy, provider)


def test_update_mines_pr_and_commits_in_its_own_range(stubbed):
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert stubbed == [{"sources": {"pr", "git_archaeology"}, "files": ["new.py"]}]
    assert [d.title for d in docs.new_decision_markers] == ["Mined"]


def test_a_history_window_change_leaves_git_archaeology_to_the_full_rerun(stubbed):
    run, docs = _run_and_docs(git_changed=True)

    _mine(run, docs)

    assert stubbed[0]["sources"] == {"pr"}


def test_no_provider_mines_nothing(stubbed):
    run, docs = _run_and_docs(provider=None)

    _mine(run, docs)

    assert stubbed == []
    assert docs.new_decision_markers == []


def test_a_policy_with_the_model_off_mines_nothing(stubbed):
    run, docs = _run_and_docs()

    _mine(run, docs, preset_policy("default").with_update_mining(True).with_llm(False))

    assert stubbed == []


def test_no_new_commit_mines_nothing(stubbed, monkeypatch):
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: set())
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert stubbed == []


def test_a_source_failure_is_reported_on_the_run(stubbed, monkeypatch):
    async def _failing(self, *, enabled_sources):
        return DecisionExtractionReport(
            total_found=0, decisions=[], by_source={}, failures={"pr": "RuntimeError: outage"}
        )

    monkeypatch.setattr(_StubExtractor, "extract_all", _failing)
    run, docs = _run_and_docs()

    _mine(run, docs)

    assert run.degraded == ["Decision mining (pr): RuntimeError: outage"]


def test_a_failed_source_keeps_its_range_and_a_succeeding_one_advances(stubbed, monkeypatch):
    bases: list[str] = []

    def _commits(_repo_path, base, _head):
        bases.append(base)
        return {"bbbb2222"}

    monkeypatch.setattr(evolution, "commits_in_range", _commits)

    async def _partial(self, *, enabled_sources):
        if "pr" in enabled_sources:
            return DecisionExtractionReport(
                total_found=0,
                decisions=[],
                by_source={},
                failures={"pr": "RuntimeError: outage"},
            )
        return DecisionExtractionReport(total_found=0, decisions=[], by_source={})

    monkeypatch.setattr(_StubExtractor, "extract_all", _partial)
    run, docs = _run_and_docs()
    run.state = {
        "decision_mining": {
            "pr": {"last_success_commit": "base"},
            "git_archaeology": {"last_success_commit": "base"},
        }
    }

    _mine(run, docs)

    assert bases == ["base"]
    ledger = run.state["decision_mining"]
    assert ledger["pr"]["last_success_commit"] == "base"
    assert ledger["pr"]["last_status"] == "failed"
    assert ledger["pr"]["last_failure"] == "RuntimeError: outage"
    assert ledger["git_archaeology"]["last_success_commit"] == "head"
    assert ledger["git_archaeology"]["last_status"] == "ok"

    # The next run starts pr at the commit it last succeeded on, and the
    # source that already advanced at the new base.
    bases.clear()
    run.base_ref = "head"
    run.head = "next"
    _mine(run, docs)

    # Ranges in order; a pathless retry range asks git twice for the same base.
    assert list(dict.fromkeys(bases)) == ["base", "head"]


def test_an_empty_range_does_not_advance_a_source(stubbed, monkeypatch):
    """An empty answer can be a git failure; advancing would lose the retry."""
    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: set())
    run, docs = _run_and_docs()
    run.state = {
        "decision_mining": {
            "pr": {"last_success_commit": "base"},
            "git_archaeology": {"last_success_commit": "base"},
        }
    }

    _mine(run, docs)

    assert stubbed == []
    assert run.state["decision_mining"]["pr"]["last_success_commit"] == "base"
    assert run.state["decision_mining"]["git_archaeology"]["last_success_commit"] == "base"


def test_a_retry_without_stored_git_metadata_is_degraded(stubbed, monkeypatch):
    """Unreadable rows for a retried range are named, not silently re-queued."""
    from repowise.cli.commands.update_cmd import deterministic
    from repowise.core import git_refs

    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: {"bbbb2222"})
    monkeypatch.setattr(git_refs, "commit_file_sets", lambda *_a: [frozenset({"old.py"})])
    monkeypatch.setattr(deterministic, "with_stored_git_meta", lambda *_a: None)
    run, docs = _run_and_docs()
    run.state = {"decision_mining": {"pr": {"last_success_commit": "older"}}}

    _mine(run, docs)

    assert run.degraded == ["Decision mining (pr): no indexed git metadata for older..head"]
    ledger = run.state["decision_mining"]["pr"]
    assert ledger["last_success_commit"] == "older"
    assert ledger["last_status"] == "failed"
    assert ledger["last_failure"] == "no indexed git metadata for older..head"


def test_a_retry_with_nothing_to_mine_advances_the_source(stubbed, monkeypatch):
    """Readable rows with no commit in range are a completed run, not a failure."""
    from repowise.cli.commands.update_cmd import deterministic
    from repowise.core import git_refs

    monkeypatch.setattr(evolution, "commits_in_range", lambda *_a: {"bbbb2222"})
    monkeypatch.setattr(git_refs, "commit_file_sets", lambda *_a: [frozenset({"old.py"})])
    monkeypatch.setattr(
        deterministic,
        "with_stored_git_meta",
        lambda *_a: {"old.py": _meta("aaaa1111")},
    )
    run, docs = _run_and_docs()
    run.state = {"decision_mining": {"pr": {"last_success_commit": "older"}}}

    _mine(run, docs)

    ledger = run.state["decision_mining"]["pr"]
    assert ledger["last_success_commit"] == "head"
    assert ledger["last_status"] == "ok"
    assert "Decision mining (pr)" not in " ".join(run.degraded)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def _rewritten_run(repo: Path, pointer: str, base: str, head: str):
    run, docs = _run_and_docs()
    run.repo_path = repo
    run.base_ref = base
    run.head = head
    run.state = {"decision_mining": {"pr": {"last_success_commit": pointer}}}
    return run, docs


def test_a_pointer_rewritten_out_of_history_falls_back_to_the_run_base(repo):
    """A force-push leaves the stored commit off HEAD's history: restart at base."""
    base = _commit(repo, "a.txt")
    orphaned = _commit(repo, "b.txt")
    _git(repo, "reset", "-q", "--hard", base)
    head = _commit(repo, "c.txt")
    run, _docs = _rewritten_run(repo, orphaned, base, head)

    groups = decision_passes._source_groups(run, {"pr"})

    assert groups == [(base, {"pr"})]
    assert run.degraded == [
        f"Decision mining (pr): stored commit {orphaned[:8]} is no longer in history; "
        f"mining from {base[:8]}"
    ]


def test_a_pointer_git_no_longer_has_falls_back_to_the_run_base(repo):
    """A garbage-collected or shallow-cut sha names no commit at all."""
    base = _commit(repo, "a.txt")
    head = _commit(repo, "b.txt")
    run, _docs = _rewritten_run(repo, "0123456789abcdef0123456789abcdef01234567", base, head)

    assert decision_passes._source_groups(run, {"pr"}) == [(base, {"pr"})]


def test_a_reachable_pointer_keeps_its_own_range(repo):
    pointer = _commit(repo, "a.txt")
    base = _commit(repo, "b.txt")
    head = _commit(repo, "c.txt")
    run, _docs = _rewritten_run(repo, pointer, base, head)

    assert decision_passes._source_groups(run, {"pr"}) == [(pointer, {"pr"})]
    assert run.degraded == []


def test_a_rewritten_pointer_mines_again_and_advances(stubbed, repo, monkeypatch):
    """End to end: the stuck source mines the new commits and its pointer moves."""
    from repowise.core import git_refs

    monkeypatch.setattr(git_refs, "is_ancestor", lambda *_a: False)
    bases: list[str] = []

    def _commits(_repo_path, base, _head):
        bases.append(base)
        return {"bbbb2222"}

    monkeypatch.setattr(evolution, "commits_in_range", _commits)
    run, docs = _run_and_docs()
    run.state = {"decision_mining": {"pr": {"last_success_commit": "rewritten"}}}

    _mine(run, docs)

    assert bases == ["base"]
    assert run.state["decision_mining"]["pr"]["last_success_commit"] == "head"


def test_a_retry_over_only_empty_commits_advances_the_source(stubbed, repo, monkeypatch):
    """Empty and merge commits name no file; that is a finished range, not a failure."""
    monkeypatch.undo()  # real git throughout: the stubs above would hide the trigger
    monkeypatch.setattr(extractor_mod, "DecisionExtractor", _StubExtractor)
    pointer = _commit(repo, "a.txt")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    head = _git(repo, "rev-parse", "HEAD")
    run, docs = _rewritten_run(repo, pointer, "other-base", head)

    _mine(run, docs)

    ledger = run.state["decision_mining"]["pr"]
    assert ledger["last_success_commit"] == head
    assert ledger["last_status"] == "ok"
    assert "Decision mining (pr)" not in " ".join(run.degraded)
