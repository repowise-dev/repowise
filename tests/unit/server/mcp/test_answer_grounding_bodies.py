"""Synthesis sees the code behind the pages it is shown, the agent's reply does not grow.

On a documented index the synthesis prompt was page prose: a flow page about
``ActiveStream::decodeHeaders`` with no body, a header whose method is a one-line
declaration, and ``namespace Envoy`` matched by the question word "Envoy's"
spending its 120-line body on an unrelated header. The answer then said "the
excerpts do not show ...". These tests pin the material, not the model.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._answer_context import build_context_block
from repowise.server.mcp_server.tool_answer.confidence import _retrieval_corpus
from repowise.server.mcp_server.tool_answer.config import _GROUNDING_MAX_BODIES
from repowise.server.mcp_server.tool_answer.grounding import attach_grounding_bodies
from repowise.server.mcp_server.tool_answer.hydration import _hydrate_symbols_for_hits
from repowise.server.mcp_server.tool_answer.retrieval import serialize_hits

_HEADER = """\
namespace Envoy {
class ActiveStream {
public:
  void decodeHeaders(HeaderMapPtr&& headers, bool end_stream);
};
} // namespace Envoy
"""

_IMPL = """\
namespace Envoy {
void ActiveStream::decodeHeaders(HeaderMapPtr&& headers, bool end_stream) {
  request_headers_ = std::move(headers);
  // MARKER_ROUTE_REFRESH: the route is picked once the headers are complete.
  refreshCachedRoute();
  filter_manager_.decodeHeaders(*request_headers_, end_stream);
}
} // namespace Envoy
"""


def _row(repo_id, rid, path, name, kind, start, end, parent=None, sig=None):
    return WikiSymbol(
        id=rid,
        repository_id=repo_id,
        file_path=path,
        symbol_id=f"{path}::{name}:{start}",
        name=name,
        qualified_name=f"{path}.{parent + '.' if parent else ''}{name}",
        kind=kind,
        signature=sig or name,
        start_line=start,
        end_line=end,
        docstring="",
        visibility="public",
        is_async=False,
        complexity_estimate=1,
        language="cpp",
        parent_name=parent,
    )


async def _seed(session, repo_id, tmp_path, rows, files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    session.add_all(rows)
    await session.commit()
    return SimpleNamespace(path=tmp_path, session_factory=None)


async def test_flow_page_gets_the_body_it_is_about(session, repo_id, tmp_path) -> None:
    ctx = await _seed(
        session,
        repo_id,
        tmp_path,
        [
            _row(repo_id, "d", "conn.h", "decodeHeaders", "method", 4, 4, "ActiveStream"),
            _row(repo_id, "i", "conn.cc", "decodeHeaders", "method", 2, 7, "ActiveStream"),
        ],
        {"conn.h": _HEADER, "conn.cc": _IMPL},
    )
    hits = [
        {
            "target_path": "conn.cc::ActiveStream::decodeHeaders",
            "page_type": "flow_page",
            "excerpt": "# How ActiveStream.decodeHeaders works",
        }
    ]
    await attach_grounding_bodies(session, repo_id, hits, ctx, "how are headers decoded")

    (body,) = hits[0]["_grounding"]
    assert body["path"] == "conn.cc" and body["start_line"] == 2
    assert "MARKER_ROUTE_REFRESH" in body["source"]
    prompt = build_context_block(hits)
    assert "implementation (read from source):" in prompt
    assert "ActiveStream::decodeHeaders in conn.cc (lines 2-7):" in prompt
    # Grounded claims stay grounded for the grade.
    assert "MARKER_ROUTE_REFRESH" in _retrieval_corpus(hits)
    # Prompt-only: the agent-facing row is unchanged.
    assert "MARKER_ROUTE_REFRESH" not in str(serialize_hits(hits))


async def test_header_declaration_reaches_its_definition(session, repo_id, tmp_path) -> None:
    ctx = await _seed(
        session,
        repo_id,
        tmp_path,
        [
            _row(repo_id, "d", "conn.h", "decodeHeaders", "method", 4, 4, "ActiveStream"),
            _row(repo_id, "i", "conn.cc", "decodeHeaders", "method", 2, 7, "ActiveStream"),
        ],
        {"conn.h": _HEADER, "conn.cc": _IMPL},
    )
    hits = [{"target_path": "conn.h", "page_type": "file_page"}]
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"decodeHeaders"}, question="decodeHeaders"
    )
    await attach_grounding_bodies(session, repo_id, hits, ctx, "decodeHeaders")

    (body,) = hits[0]["_grounding"]
    assert (body["path"], body["for"]) == ("conn.cc", "declaration")


async def test_unrelated_same_name_definitions_are_not_guessed(session, repo_id, tmp_path) -> None:
    """Two definitions in files unrelated to the header: ambiguous, so none."""
    ctx = await _seed(
        session,
        repo_id,
        tmp_path,
        [
            _row(repo_id, "d", "conn.h", "decodeHeaders", "method", 4, 4, "ActiveStream"),
            _row(repo_id, "a", "a.cc", "decodeHeaders", "method", 2, 7, "ActiveStream"),
            _row(repo_id, "b", "b.cc", "decodeHeaders", "method", 2, 7, "ActiveStream"),
        ],
        {"conn.h": _HEADER, "a.cc": _IMPL, "b.cc": _IMPL},
    )
    hits = [{"target_path": "conn.h", "page_type": "file_page"}]
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"decodeHeaders"}, question="decodeHeaders"
    )
    await attach_grounding_bodies(session, repo_id, hits, ctx, "decodeHeaders")
    assert "_grounding" not in hits[0]


async def test_namespace_matched_by_a_question_word_gets_no_body(
    session, repo_id, tmp_path
) -> None:
    ctx = await _seed(
        session,
        repo_id,
        tmp_path,
        [_row(repo_id, "n", "optref.h", "Envoy", "module", 1, 6)],
        {"optref.h": _HEADER},
    )
    hits = [{"target_path": "optref.h", "page_type": "file_page"}]
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"Envoy"}, question="In Envoy's codec"
    )
    (ns,) = hits[0]["symbols"]
    assert ns["_matched"] is True
    assert "source_excerpt" not in ns


async def test_unenriched_file_hit_gets_its_question_named_body_within_the_cap(
    session, repo_id, tmp_path
) -> None:
    files, rows, hits = {}, [], []
    for i in range(_GROUNDING_MAX_BODIES + 2):
        files[f"f{i}.cc"] = _IMPL
        rows.append(_row(repo_id, f"r{i}", f"f{i}.cc", "decodeHeaders", "method", 2, 7, "S"))
        rows.append(_row(repo_id, f"u{i}", f"f{i}.cc", "unrelated", "function", 1, 8))
        hits.append({"target_path": f"f{i}.cc", "page_type": "file_page"})
    ctx = await _seed(session, repo_id, tmp_path, rows, files)
    await attach_grounding_bodies(session, repo_id, hits, ctx, "how does decodeHeaders work")

    bodies = [g for h in hits for g in h.get("_grounding") or []]
    assert len(bodies) == _GROUNDING_MAX_BODIES
    assert {g["name"] for g in bodies} == {"decodeHeaders"}
    assert [g["path"] for g in bodies] == [f"f{i}.cc" for i in range(_GROUNDING_MAX_BODIES)]


async def test_a_mock_is_not_the_implementation(session, repo_id, tmp_path) -> None:
    """The only other definition lives in test support: not grounding for a non-test question."""
    (tmp_path / "test" / "mocks").mkdir(parents=True)
    ctx = await _seed(
        session,
        repo_id,
        tmp_path,
        [
            _row(repo_id, "d", "conn.h", "decodeHeaders", "method", 4, 4, "ActiveStream"),
            _row(
                repo_id, "m", "test/mocks/conn.cc", "decodeHeaders", "method", 2, 7, "ActiveStream"
            ),
        ],
        {"conn.h": _HEADER, "test/mocks/conn.cc": _IMPL},
    )
    hits = [{"target_path": "conn.h", "page_type": "file_page"}]
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, ctx, question_ids={"decodeHeaders"}, question="decodeHeaders"
    )
    await attach_grounding_bodies(session, repo_id, hits, ctx, "how does decodeHeaders work")
    assert "_grounding" not in hits[0]
