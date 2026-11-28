"""Suspect ranking: signals, fusion and the seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from repowise.core.co_change import CoChangePartner
from repowise.core.fts_query import meaningful_terms
from repowise.core.suspects import (
    Candidate,
    Hit,
    SuspectContext,
    Symptom,
    rank_suspects,
    suspect_signals,
)
from repowise.core.suspects.git_text import hunk_symbols
from repowise.core.suspects.index import NOTE
from repowise.core.suspects.signals import path_mentions, path_terms

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


@pytest.fixture
async def suspects_index(tmp_path, monkeypatch):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import Change
    from repowise.core.repo_config import save_repo_config
    from repowise.core.suspects import index

    save_repo_config(tmp_path, {"commit_limit": 3})
    monkeypatch.setattr(index, "read_change_texts", lambda *args: {})
    monkeypatch.setattr(index, "_tracked_files", lambda path: frozenset())
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            session.add_all([
                Change(
                    repository_id=repo.id,
                    change_id=f"change-{i}",
                    identity_kind="commit",
                    identity_value=f"{i:040x}",
                    shape="commit",
                    title=f"Change {i}",
                    merged_at=NOW - timedelta(days=i + 1),
                    head_sha=f"{i:040x}",
                )
                for i in range(3)
            ])
            await session.flush()
            yield session, repo.id, tmp_path
    finally:
        await engine.dispose()


@pytest.mark.parametrize("commit_limit", [2, 3, "3"])
async def test_candidates_capped_when_the_window_holds_commit_limit_changes(
    suspects_index, commit_limit,
):
    from repowise.core.repo_config import save_repo_config
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path = suspects_index
    save_repo_config(path, {"commit_limit": commit_limit})
    out = await find_suspects(session, repo_id, path, Symptom(at=NOW), limit=1)
    assert out["candidates_capped"] is True
    assert out["candidates"] == 3  # Count candidates before the result limit.
    assert len(out["suspects"]) == 1
    assert out["suspects"][0]["id"] == "change-0"
    assert out["suspects"][0]["rank"] == 1
    assert out["note"].startswith(NOTE)
    assert f"at least `commit_limit` ({int(commit_limit)}) changes" in out["note"]
    assert "`repowise init --commit-limit`" in out["note"]


async def test_candidates_not_capped_below_the_limit(suspects_index):
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path = suspects_index
    out = await find_suspects(
        session, repo_id, path, Symptom(at=NOW), since=NOW - timedelta(days=2),
    )
    assert out["candidates"] == 2  # The stored third change is outside the window.
    assert out["candidates_capped"] is False
    assert [s["id"] for s in out["suspects"]] == ["change-0", "change-1"]
    # Recency alone is no evidence: the note says how to read further back.
    since = (NOW - timedelta(days=2)).strftime("%Y-%m-%d")
    assert f"none merged before {since} was read" in out["note"]
    # Chat and REST take `since` themselves; the CLI command works from every surface.
    assert "Read an earlier window with `since`" in out["note"]
    assert "`repowise suspects --since <an earlier date>`" in out["note"]


def test_a_capped_window_says_so_even_when_a_row_has_evidence():
    from repowise.core.suspects.index import _note

    rows = [{"signals": {"text": {}, "recency": {}}}]
    # The window itself is incomplete: evidence on a listed row does not read the unindexed changes.
    assert "`repowise init --commit-limit` above 50" in _note(NOW, rows, True, 50)
    assert _note(NOW, rows, False, 50) == NOTE


async def test_empty_suspects_window_is_not_capped(suspects_index):
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path = suspects_index
    out = await find_suspects(
        session, repo_id, path, Symptom(at=NOW), since=NOW - timedelta(hours=12),
    )
    assert out["candidates"] == 0
    assert out["candidates_capped"] is False
    assert out["suspects"] == []
    assert "`repowise suspects --since <an earlier date>`" in out["note"]


@pytest.mark.parametrize("config", [{}, {"commit_limit": 0}, {"commit_limit": "0"}, {"commit_limit": None}])
async def test_candidates_use_the_default_commit_limit(suspects_index, config):
    from repowise.core.repo_config import save_repo_config
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path = suspects_index
    save_repo_config(path, config)
    out = await find_suspects(session, repo_id, path, Symptom(at=NOW))
    assert out["candidates"] == 3
    assert out["candidates_capped"] is False


def test_path_target_text_drops_extension_and_layout_segments():
    text = path_terms(
        "packages/core/src/repowise/core/ingestion/call_resolver.py", frozenset({"repowise"})
    )
    assert set(meaningful_terms(text)) == {"call_resolver", "ingestion"}


def test_path_target_text_keeps_the_symbol_and_a_filler_stem_left_alone():
    assert path_terms("src/billing/invoice.ts::totalDue") == "billing invoice totalDue"
    assert path_terms("src/index.ts") == "index"


# -- trace frames resolved to symbols ------------------------------------------

from repowise.core.suspects.rank import prepare, resolve_trace  # noqa: E402
from repowise.core.workspace.repo_index import IndexedSymbol  # noqa: E402

AS_SQL = IndexedSymbol(
    f"{COMPILER}::SQLCompiler::as_sql", "as_sql", "django.db.models.sql.compiler.SQLCompiler.as_sql",
    "method", "", COMPILER, 5, 20, "public",
)


def _resolved(**kw):
    symptom = Symptom(text=TRACE, at=NOW)
    prepared = prepare(symptom, _ctx())
    kw.setdefault("symbols_by_file", {COMPILER: [AS_SQL]})
    return symptom, resolve_trace(prepared, root="/srv/app", **kw)


def test_a_resolved_frame_names_its_symbol_in_one_evidence_row():
    symptom, prepared = _resolved()
    assert [s.symbol_id for s in prepared.frame_symbols] == [AS_SQL.symbol_id]
    out = rank_suspects(symptom, _candidates(), _ctx(), signals=["trace"], prepared=prepared)
    assert out[0].change_id == "c"
    assert out[0].signals["trace"].evidence == (f"frame {AS_SQL.symbol_id}",)


def test_a_resolved_frame_does_not_match_the_same_name_in_another_file():
    symptom, prepared = _resolved()
    other = [Candidate("d", "Touch query", NOW - timedelta(days=1), (QUERY,), symbols=("as_sql",))]
    out = rank_suspects(symptom, other, _ctx(), signals=["trace"], prepared=prepared)
    assert "trace" not in out[0].signals


def test_a_frame_in_a_dirty_file_keeps_the_name_match():
    symptom, prepared = _resolved(dirty_paths={COMPILER})
    assert prepared.frame_symbols == () and prepared.resolved_frames == frozenset()
    out = rank_suspects(symptom, _candidates(), _ctx(), signals=["trace"], prepared=prepared)
    assert out[0].signals["trace"].evidence == (f"frame file {COMPILER}", "frame function as_sql")


def test_a_frame_under_another_root_is_not_resolved():
    symptom = Symptom(text=TRACE, at=NOW)
    prepared = resolve_trace(
        prepare(symptom, _ctx()), root="/srv/other", symbols_by_file={COMPILER: [AS_SQL]}
    )
    assert prepared.frame_symbols == ()


def test_go_and_javascript_frames_are_left_to_the_name_match():
    trace = "Error: boom\n    at handler (/srv/app/lib/app.js:10:5)\n"
    symptom = Symptom(text=trace, at=NOW)
    ctx = SuspectContext(repo_files=frozenset({"lib/app.js"}))
    prepared = prepare(symptom, ctx)
    assert resolve_trace(prepared, root="/srv/app", symbols_by_file={"lib/app.js": []}) is prepared


@pytest.fixture
async def traced_repo(tmp_path, monkeypatch):
    """A git checkout with one indexed method and one change that touched it."""
    import subprocess

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import Change, WikiSymbol
    from repowise.core.suspects import index

    src = tmp_path / "app" / "compiler.py"
    src.parent.mkdir()
    src.write_text("class SQLCompiler:\n    def as_sql(self):\n        return 1 / 0\n", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(
        index, "read_change_texts", lambda *args: {"h" * 40: ("", ("as_sql",)), "g" * 40: ("", ("as_sql",))}
    )
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            repo.head_commit = head
            session.add_all([
                WikiSymbol(
                    repository_id=repo.id, file_path="app/compiler.py",
                    symbol_id="app/compiler.py::SQLCompiler::as_sql", name="as_sql",
                    qualified_name="app.compiler.SQLCompiler.as_sql", kind="method",
                    start_line=2, end_line=3,
                ),
                Change(
                    repository_id=repo.id, change_id="touched", identity_kind="commit", identity_value="h" * 40,
                    shape="commit", title="Rework the compiler", merged_at=NOW - timedelta(days=1),
                    head_sha="h" * 40, files_json='[{"path": "app/compiler.py"}]', files_changed=1,
                ),
                Change(
                    repository_id=repo.id, change_id="elsewhere", identity_kind="commit", identity_value="g" * 40,
                    shape="commit", title="Rework another as_sql", merged_at=NOW - timedelta(days=2),
                    head_sha="g" * 40, files_json='[{"path": "app/other.py"}]', files_changed=1,
                ),
            ])
            await session.flush()
            trace = (
                "Traceback (most recent call last):\n"
                f'  File "{src}", line 3, in as_sql\n'
                "ZeroDivisionError: division by zero\n"
            )
            yield session, repo.id, tmp_path, trace
    finally:
        await engine.dispose()


async def test_find_suspects_names_the_function_a_trace_frame_ran_in(traced_repo):
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path, trace = traced_repo
    out = await find_suspects(session, repo_id, path, Symptom(trace=trace, at=NOW))
    rows = {r["id"]: r for r in out["suspects"]}
    assert rows["touched"]["signals"]["trace"]["evidence"] == [
        "frame app/compiler.py::SQLCompiler::as_sql"
    ]
    # The bare name no longer matches a change that never touched the frame's file.
    assert "trace" not in rows["elsewhere"]["signals"]


async def test_find_suspects_keeps_file_matching_when_the_file_changed_since_indexing(traced_repo):
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path, trace = traced_repo
    (path / "app" / "compiler.py").write_text("# edited\n", encoding="utf-8")
    out = await find_suspects(session, repo_id, path, Symptom(trace=trace, at=NOW))
    rows = {r["id"]: r for r in out["suspects"]}
    assert rows["touched"]["signals"]["trace"]["evidence"] == [
        "frame file app/compiler.py",
        "frame function as_sql",
    ]


async def test_find_suspects_skips_resolution_when_the_indexed_commit_is_unknown(traced_repo):
    from repowise.core.persistence.models import Repository
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path, trace = traced_repo
    repo = await session.get(Repository, repo_id)
    repo.head_commit = "0" * 40  # not in this checkout: git cannot diff against it
    out = await find_suspects(session, repo_id, path, Symptom(trace=trace, at=NOW))
    rows = {r["id"]: r for r in out["suspects"]}
    assert rows["touched"]["signals"]["trace"]["evidence"] == [
        "frame file app/compiler.py",
        "frame function as_sql",
    ]


async def test_find_suspects_does_no_resolution_work_for_a_trace_without_python_frames(
    traced_repo, monkeypatch
):
    from repowise.core.suspects import index
    from repowise.core.suspects.index import find_suspects

    session, repo_id, path, _trace = traced_repo

    async def boom(*_args):
        raise AssertionError("resolution ran")

    monkeypatch.setattr(index, "_resolve_trace", boom)
    trace = f"Error: x\n    at as_sql ({path}/app/compiler.js:3:5)\n"
    await find_suspects(session, repo_id, path, Symptom(trace=trace, at=NOW))


def test_two_frames_in_one_symbol_give_one_evidence_row():
    trace = TRACE + '  File "/srv/app/django/db/models/sql/compiler.py", line 12, in as_sql\n'
    symptom = Symptom(text=trace, at=NOW)
    prepared = resolve_trace(
        prepare(symptom, _ctx()), root="/srv/app", symbols_by_file={COMPILER: [AS_SQL]}
    )
    assert len(prepared.frame_symbols) == 1
