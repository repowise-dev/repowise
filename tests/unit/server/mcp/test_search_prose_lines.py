"""Concept search rows carry the live source lines that best match a prose query."""

from __future__ import annotations

import pytest

from tests.unit.server.mcp.test_search import _mk_result, _seed_page

TS = """import { pendingRequests } from "./queue";
// pending requests are kept as exact identifiers
export function auditSummary(summary: string): boolean {
  const kept = pendingRequests.every((id) => summary.includes(id));
  return kept && checkIdentifiers(summary);
}
"""

PY = """import os


def backoff_schedule(polls):
    # schedule for repeated polls
    return POLL_DELAYS[min(polls, len(POLL_DELAYS) - 1)]


def unrelated():
    return os.getcwd()
"""

GO = """package recovery

// Resume interrupted sessions.
func ResumeOrphanedSession(s *Session) error {
	return s.Send("continue the interrupted session")
}
"""


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _seed_symbol(session, rid, path, name, start, end, language):
    from repowise.core.persistence.models import WikiSymbol

    session.add(
        WikiSymbol(
            id=f"prose-{path}-{name}",
            repository_id=rid,
            file_path=path,
            symbol_id=f"{path}::{name}",
            name=name,
            qualified_name=name,
            kind="function",
            signature=name,
            start_line=start,
            end_line=end,
            visibility="public",
            language=language,
        )
    )
    await session.commit()


def _terms(query):
    from repowise.server.mcp_server._hit_symbols import _stem
    from repowise.server.mcp_server._query_terms import content_terms

    return {_stem(t) for t in content_terms(query)}


class TestBestLines:
    def test_camel_case_lines_match_prose_words(self):
        from repowise.server.mcp_server._hit_symbols import best_lines

        lines = TS.splitlines()
        got = best_lines(lines, _terms("summary keeps pending requests"), [])
        # The import and the comment match too, but a code line outranks them.
        assert [r["line"] for r in got] == [4]
        assert got[0]["text"].startswith("const kept = pendingRequests")

    def test_snake_case_and_symbol_span(self):
        from repowise.server.mcp_server._hit_symbols import best_lines

        lines = PY.splitlines()
        got = best_lines(lines, _terms("schedule of repeated polls"), [(4, 6)])
        assert got == [{"line": 4, "text": "def backoff_schedule(polls):"}]

    def test_comment_lines_serve_when_nothing_else_matches(self):
        from repowise.server.mcp_server._hit_symbols import best_lines

        lines = GO.splitlines()
        terms = _terms("resume interrupted session")
        assert [r["line"] for r in best_lines(lines, terms, [])] == [4, 5]
        # A tie goes to the line inside a matching symbol.
        assert [r["line"] for r in best_lines(lines, terms, [(5, 6)])] == [5, 4]
        only_comment = best_lines(["// resume interrupted work", "x := 1"], _terms("resume interrupted"), [])
        assert only_comment == [{"line": 1, "text": "// resume interrupted work"}]

    def test_long_lines_are_cut_like_line_hits(self):
        from repowise.server.mcp_server._edit_sites import MAX_TEXT_CHARS
        from repowise.server.mcp_server._hit_symbols import best_lines

        got = best_lines(["pending requests " + "x" * 400], _terms("pending requests"), [])
        assert len(got[0]["text"]) == MAX_TEXT_CHARS


class TestAttachLines:
    @pytest.mark.asyncio
    async def test_top_rows_get_numbered_lines(self, session, populated_db, setup_mcp, tmp_path):
        from repowise.server.mcp_server._helpers import _resolve_repo_context
        from repowise.server.mcp_server._hit_symbols import attach_hit_symbols

        _write(tmp_path, "pkg/poll.py", PY)
        _write(tmp_path, "pkg/recovery.go", GO)
        await _seed_symbol(session, populated_db, "pkg/poll.py", "backoff_schedule", 4, 6, "python")
        rows = [
            {"page_type": "file_page", "target_path": "pkg/recovery.go"},
            {"page_type": "file_page", "target_path": "pkg/poll.py"},
            {"page_type": "file_page", "target_path": "pkg/missing.py"},
        ]
        ctx = await _resolve_repo_context(None)
        await attach_hit_symbols(ctx, "resume the interrupted session schedule polls", rows)
        assert rows[0]["matched_lines"] == [
            {"line": 4, "text": "func ResumeOrphanedSession(s *Session) error {"},
            {"line": 5, "text": 'return s.Send("continue the interrupted session")'},
        ]
        assert rows[1]["matched_lines"] == [{"line": 4, "text": "def backoff_schedule(polls):"}]
        # An unreadable file costs only its own field.
        assert "matched_lines" not in rows[2]

    @pytest.mark.asyncio
    async def test_stopword_query_adds_nothing(self, session, populated_db, setup_mcp, tmp_path):
        from repowise.server.mcp_server._helpers import _resolve_repo_context
        from repowise.server.mcp_server._hit_symbols import attach_hit_symbols

        _write(tmp_path, "pkg/poll.py", PY)
        rows = [{"page_type": "file_page", "target_path": "pkg/poll.py"}]
        ctx = await _resolve_repo_context(None)
        await attach_hit_symbols(ctx, "what is the", rows)
        assert rows == [{"page_type": "file_page", "target_path": "pkg/poll.py"}]

    @pytest.mark.asyncio
    async def test_concept_search_rows_carry_lines(
        self, session, populated_db, setup_mcp, tmp_path
    ):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        _write(tmp_path, "src/audit.ts", TS)
        await _seed_page("file_page:src/audit.ts", "src/audit.ts")

        async def fake_search(query, limit=10):
            return [_mk_result("file_page:src/audit.ts", "Audit", "file_page", "src/audit.ts", 0.9)]

        mcp_mod._vector_store.search = fake_search
        res = await search_codebase(
            "which file checks the summary keeps pending requests", mode="concept"
        )
        row = next(r for r in res["results"] if r.get("path") == "src/audit.ts")
        assert row["matched_lines"][0]["line"] == 4

    @pytest.mark.asyncio
    async def test_identifier_queries_are_unchanged(
        self, session, populated_db, setup_mcp, tmp_path, monkeypatch
    ):
        from repowise.server.mcp_server import _hit_symbols, search_codebase

        _write(tmp_path, "src/audit.ts", TS)
        before = await search_codebase("auditSummary")

        calls = []
        monkeypatch.setattr(_hit_symbols, "best_lines", lambda *a: calls.append(a) or [])
        after = await search_codebase("auditSummary")
        before.pop("_meta", None)
        after.pop("_meta", None)
        assert before == after
        assert calls == []
        assert not any("matched_lines" in r for r in after.get("results", []))


class TestLineBudget:
    @pytest.mark.asyncio
    async def test_unreadable_files_do_not_spend_the_budget(
        self, session, populated_db, setup_mcp, tmp_path
    ):
        from repowise.server.mcp_server._helpers import _resolve_repo_context
        from repowise.server.mcp_server._hit_symbols import attach_hit_symbols

        for name in ("a", "b", "c", "d"):
            _write(tmp_path, f"pkg/{name}.go", GO)
        paths = ["pkg/gone1.go", "pkg/gone2.go", "pkg/a.go", "pkg/b.go", "pkg/c.go", "pkg/d.go"]
        rows = [{"page_type": "file_page", "target_path": p} for p in paths]
        ctx = await _resolve_repo_context(None)
        await attach_hit_symbols(ctx, "resume interrupted session", rows)
        assert [r["target_path"] for r in rows if "matched_lines" in r] == paths[2:5]

    @pytest.mark.asyncio
    async def test_a_hybrid_search_reads_at_most_three_files(
        self, session, populated_db, setup_mcp, tmp_path, monkeypatch
    ):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import _hit_symbols, search_codebase

        paths = [f"pkg/r{i}.go" for i in range(5)]
        for path in paths:
            _write(tmp_path, path, GO)
            await _seed_page(f"file_page:{path}", path)

        async def fake_search(query, limit=10):
            return [
                _mk_result(f"file_page:{p}", p, "file_page", p, 0.9 - i / 10)
                for i, p in enumerate(paths)
            ]

        mcp_mod._vector_store.search = fake_search
        reads = []
        real = _hit_symbols._read_lines

        def counting(root, rel):
            reads.append(rel)
            return real(root, rel)

        monkeypatch.setattr(_hit_symbols, "_read_lines", counting)
        res = await search_codebase(
            "where is ResumeOrphanedSession resuming the interrupted session", mode="hybrid"
        )
        assert 0 < len(reads) <= 3
        assert sum("matched_lines" in r for r in res["results"]) <= 3


def test_form_feed_keeps_line_numbers(tmp_path):
    from repowise.server.mcp_server._edit_sites import _read_lines

    (tmp_path / "f.py").write_bytes(b"a = 1\n\x0c\nb = 2\r\nc = 3\n")
    assert _read_lines(tmp_path, "f.py") == ["a = 1", "\x0c", "b = 2", "c = 3"]
