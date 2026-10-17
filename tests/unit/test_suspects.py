"""Suspect ranking: signals, fusion and the seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.co_change import CoChangePartner
from repowise.core.suspects import (
    Candidate,
    Hit,
    SuspectContext,
    Symptom,
    rank_suspects,
    suspect_signals,
)
from repowise.core.suspects.git_text import hunk_symbols
from repowise.core.suspects.signals import path_mentions

NOW = datetime(2026, 1, 10, tzinfo=UTC)
QUERY = "django/db/models/query.py"
COMPILER = "django/db/models/sql/compiler.py"


def _ctx() -> SuspectContext:
    return SuspectContext(
        repo_files=frozenset({QUERY, COMPILER, "docs/index.txt"}),
        imports={QUERY: [COMPILER], COMPILER: [QUERY]},
        co_change={QUERY: [CoChangePartner(COMPILER, 1.0, support=3, self_commits=10)]},
    )


def _candidates() -> list[Candidate]:
    return [
        Candidate("a", "Fix queryset filter", NOW - timedelta(days=1), (QUERY,), symbols=("filter",)),
        Candidate("b", "Docs typo", NOW - timedelta(hours=2), ("docs/index.txt",)),
        Candidate("c", "Refactor sql compiler", NOW - timedelta(days=5), (COMPILER,), symbols=("as_sql",)),
    ]


TRACE = 'Traceback (most recent call last):\n  File "/srv/app/django/db/models/sql/compiler.py", line 10, in as_sql\n'


def test_every_candidate_is_ranked_with_the_signals_that_fired():
    out = rank_suspects(Symptom(text="queryset filter crash in query.py\n" + TRACE, at=NOW), _candidates(), _ctx())
    assert [s.change_id for s in out] == ["c", "a", "b"]
    assert [s.rank for s in out] == [1, 2, 3]
    top = out[0].signals
    assert set(top) == {"text", "graph", "co-change", "recency", "trace"}
    assert top["trace"].evidence == (f"frame file {COMPILER}", "frame function as_sql")
    assert top["co-change"].evidence == (f"{COMPILER} changed in 3 of 10 commits to {QUERY}",)
    assert set(out[2].signals) == {"recency"}


def test_no_symptom_evidence_leaves_recency_order():
    out = rank_suspects(Symptom(text="", at=NOW), _candidates(), SuspectContext())
    assert [s.change_id for s in out] == ["b", "a", "c"]


def test_signals_argument_restricts_the_fusion():
    out = rank_suspects(Symptom(text="sql compiler", at=NOW), _candidates(), _ctx(), signals=["text"])
    assert out[0].change_id == "c"
    assert set(out[0].signals) == {"text"}


def test_ties_share_a_rank_within_a_signal():
    out = rank_suspects(Symptom(files=(QUERY, COMPILER), at=NOW), _candidates(), _ctx(), signals=["graph"])
    graph = {s.change_id: s.signals["graph"].rank for s in out if "graph" in s.signals}
    assert graph == {"a": 1, "c": 1}
    assert out[0].change_id == "a"  # fused tie: newest first


def test_a_registered_plugin_signal_fuses_with_the_defaults():
    class Pin:
        def rank(self, symptom, candidates, ctx):
            return {"c": Hit(1.0, ("pinned",))}

    suspect_signals.register("test-pin", Pin())
    try:
        out = rank_suspects(Symptom(text="", at=NOW), _candidates(), SuspectContext())
        assert out[0].change_id == "c"
        assert out[0].signals["test-pin"].evidence == ("pinned",)
    finally:
        suspect_signals._impls.pop("test-pin")


def test_path_mentions_reads_urls_and_bare_names():
    text = "See https://github.com/o/r/blob/main/django/db/models/query.py#L12 and compiler.py, version 3.2"
    assert path_mentions(text) == ["github.com/o/r/blob/main/django/db/models/query.py", "compiler.py"]


def test_hunk_symbols_reads_function_context():
    diff = "@@ -1,2 +1,3 @@ def as_sql(self, compiler):\n+x\n@@ -9 +9 @@ class Query(BaseExpression):\n@@ -20 +20 @@\n"
    assert hunk_symbols(diff) == ("as_sql", "Query")


def test_a_raising_signal_did_not_fire():
    class Broken:
        def rank(self, symptom, candidates, ctx):
            raise RuntimeError("boom")

    suspect_signals.register("test-broken", Broken())
    try:
        out = rank_suspects(Symptom(text="", at=NOW), _candidates(), SuspectContext())
        assert [s.change_id for s in out] == ["b", "a", "c"]
        assert all("test-broken" not in s.signals for s in out)
    finally:
        suspect_signals._impls.pop("test-broken")


def test_text_evidence_follows_the_fts_prefix_rule():
    cands = [Candidate("a", "Invalidates the cache", NOW, ("x.py",))]
    out = rank_suspects(Symptom(text="invalidat", at=NOW), cands, SuspectContext(), signals=["text"])
    assert out[0].signals["text"].evidence == ("term invalidat",)


def test_person_trailers_never_reach_the_text(tmp_path):
    import subprocess

    from repowise.core.suspects.git_text import read_change_texts, strip_person_trailers

    body = "Why.\n\nPR-URL: https://h/pull/1\nReviewed-By: Ann Lee <ann@x.org>\nSigned-off-by: Bob\nCo-authored-by: C <c@d.e>"
    assert strip_person_trailers(body) == "Why.\n\nPR-URL: https://h/pull/1"

    env = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a",
           "GIT_COMMITTER_EMAIL": "a@x", "PATH": __import__("os").environ["PATH"]}
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True,
                              check=True, env=env).stdout.strip()
    git("init", "-q")
    (tmp_path / "f.py").write_text("def f():\n    return 1\n")
    git("add", "f.py")
    git("commit", "-q", "-m", "Add f", "-m", body)
    head = git("rev-parse", "HEAD")
    # An unknown and an empty sha in the batch cost only themselves.
    got = read_change_texts(tmp_path, [(head, 1), ("f" * 40, 1), ("", 1)])
    assert list(got) == [head]
    assert "Ann" not in got[head][0] and "Bob" not in got[head][0]


def test_a_change_text_is_read_from_git_once(monkeypatch, tmp_path):
    """A commit's text never changes, so a long-lived server reads each one once."""
    from repowise.core.suspects import git_text

    sha_a, sha_b = "a" * 40, "b" * 40
    asked: list[list[str]] = []

    def fake_log(_repo, args, shas):
        asked.append(list(shas))
        if args[0].startswith("--format"):
            return "".join(f"\x00{s}\nbody {s[0]}\n" for s in shas)
        return "".join(f"\x00{s}\n@@ -1 +1 @@ def f_{s[0]}(\n" for s in shas)

    monkeypatch.setattr(git_text, "_log", fake_log)
    monkeypatch.setattr(git_text, "_TEXTS", type(git_text._TEXTS)())
    first = git_text.read_change_texts(tmp_path, [(sha_a, 1)])
    asked.clear()
    both = git_text.read_change_texts(tmp_path, [(sha_a, 1), (sha_b, 1)])
    assert both[sha_a] == first[sha_a] == ("body a", ("f_a",))
    assert both[sha_b] == ("body b", ("f_b",))
    assert asked == [[sha_b], [sha_b]]


def _fresh_text_cache(monkeypatch, git_text):
    monkeypatch.setattr(git_text, "_TEXTS", type(git_text._TEXTS)())
    monkeypatch.setattr(git_text, "_INFLIGHT", {})


def test_concurrent_cold_reads_run_git_once(monkeypatch, tmp_path):
    """Two calls that miss the same changes together read each change once."""
    import threading
    import time

    from repowise.core.suspects import git_text

    shas = [c * 40 for c in "abc"]
    heads = [(s, 1) for s in shas]
    calls: list[int] = []

    def slow(repo, batch):
        calls.append(len(batch))
        time.sleep(0.3)
        return {s: (f"body {s[0]}", ()) for s, _ in batch}

    monkeypatch.setattr(git_text, "_read_change_texts", slow)
    _fresh_text_cache(monkeypatch, git_text)
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def worker():
        barrier.wait()
        results.append(git_text.read_change_texts(tmp_path, heads))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=10) for t in threads]
    assert not any(t.is_alive() for t in threads)
    assert sum(calls) == len(heads)
    assert len(results) == 2 and results[0] == results[1]
    assert set(results[0]) == set(shas)
    assert git_text._INFLIGHT == {}


def test_failed_read_releases_waiters(monkeypatch, tmp_path):
    """A claimed read that raises still wakes its waiter; the next call retries."""
    import threading
    import time

    from repowise.core.suspects import git_text

    sha = "a" * 40
    heads = [(sha, 1)]
    state = {"calls": 0, "fail": True}
    started = threading.Event()

    def flaky(repo, batch):
        state["calls"] += 1
        if state["fail"]:
            started.set()
            time.sleep(0.3)
            raise RuntimeError("git exploded")
        return {s: ("body", ()) for s, _ in batch}

    monkeypatch.setattr(git_text, "_read_change_texts", flaky)
    _fresh_text_cache(monkeypatch, git_text)
    errors: list[BaseException] = []
    waiter_result: list[dict] = []

    def leader():
        try:
            git_text.read_change_texts(tmp_path, heads)
        except RuntimeError as exc:
            errors.append(exc)

    def waiter():
        started.wait(timeout=5)
        waiter_result.append(git_text.read_change_texts(tmp_path, heads))

    threads = [threading.Thread(target=leader), threading.Thread(target=waiter)]
    [t.start() for t in threads]
    [t.join(timeout=10) for t in threads]
    assert not any(t.is_alive() for t in threads)
    assert len(errors) == 1
    assert waiter_result == [{}]
    assert state["calls"] == 1
    state["fail"] = False
    assert git_text.read_change_texts(tmp_path, heads) == {sha: ("body", ())}
    assert state["calls"] == 2


def test_git_never_inherits_stdin_and_cannot_hang(monkeypatch, tmp_path):
    # Under the stdio MCP server on Windows an inherited stdin pipe blocks git.
    import subprocess

    from repowise.core.suspects import git_text

    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(git_text.subprocess, "run", fake_run)
    assert git_text._git(tmp_path, "ls-files") is None
    assert seen["stdin"] is subprocess.DEVNULL and seen["timeout"] > 0


def test_text_evidence_skips_filler_and_the_repo_name_but_is_never_empty():
    cands = [
        Candidate("a", "repowise shows the invoice total", NOW, ("billing/invoice.py",)),
        Candidate("b", "repowise shows the readme", NOW, ("docs/readme.md",)),
    ]
    ctx = SuspectContext(repo_terms=frozenset({"repowise"}))
    out = {
        s.change_id: s.signals["text"].evidence
        for s in rank_suspects(Symptom(text="repowise shows invoice", at=NOW), cands, ctx, signals=["text"])
    }
    assert out["a"] == ("term invoice",)
    assert out["b"] == ("term repowise",)  # nothing else matched: the top term stays


def test_one_import_hop_reads_singular():
    ctx = SuspectContext(imports={QUERY: [COMPILER], COMPILER: [QUERY]})
    out = rank_suspects(Symptom(files=(QUERY,), at=NOW), _candidates(), ctx, signals=["graph"])
    hop = next(s for s in out if s.change_id == "c").signals["graph"].evidence
    assert hop == (f"1 import hop from {QUERY} to {COMPILER}",)
