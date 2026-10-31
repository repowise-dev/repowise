"""A name the question writes that production code defines in more than one
file does not earn high unless the answer tells the definitions apart."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer.confidence import _grade_answer
from repowise.server.mcp_server.tool_answer.payload import _split_names_note
from repowise.server.mcp_server.tool_answer.symbols import _split_definitions

_POOL = "src/utils/concurrent.ts"
_SSG = "src/helper/ssg/ssg.ts"
_SPLIT = {"DEFAULT_CONCURRENCY": [_SSG, _POOL]}
_HITS = [
    {
        "target_path": _POOL,
        "score": 4.6,
        "summary": "A bounded pool for running async tasks; DEFAULT_CONCURRENCY caps it.",
        "symbols": [
            {
                "name": "DEFAULT_CONCURRENCY",
                "kind": "constant",
                "signature": "const DEFAULT_CONCURRENCY = 1024",
                "_verified": True,
                "_matched": True,
            }
        ],
    },
    {
        "target_path": _SSG,
        "score": 3.2,
        "summary": "Static site generation.",
        "symbols": [{"name": "DEFAULT_CONCURRENCY", "kind": "constant", "_verified": True}],
    },
]
_ONE = f"In `{_POOL}`, `DEFAULT_CONCURRENCY` is `1024`."
_BOTH = f"`DEFAULT_CONCURRENCY` is `1024` in `{_POOL}` and `2` in `{_SSG}`."


def _grade(question, answer, *, split=_SPLIT, citations=(_POOL,), hits=_HITS):
    return _grade_answer(
        question=question,
        question_ids={"DEFAULT_CONCURRENCY"},
        answer_text=answer,
        hits=hits,
        citations=list(citations),
        symbol_bodies=[],
        served_named_body=False,
        dominance="gap",
        split_names=split,
    )


def test_an_answer_naming_one_of_two_definitions_is_held_at_medium() -> None:
    grade = _grade("DEFAULT_CONCURRENCY", _ONE)
    assert grade.confidence == "medium"
    assert grade.split_names == _SPLIT


def test_an_answer_naming_every_definition_keeps_high() -> None:
    grade = _grade("DEFAULT_CONCURRENCY", _BOTH, citations=(_POOL, _SSG))
    assert grade.confidence == "high"
    assert grade.split_names == {}


def test_a_file_named_for_another_reason_does_not_settle_it() -> None:
    answer = f"{_ONE} The pool is imported by `{_SSG}`."
    grade = _grade("DEFAULT_CONCURRENCY", answer, citations=(_POOL, _SSG))
    assert grade.confidence == "medium"


def test_a_question_word_picking_one_definition_settles_it() -> None:
    answer = f"In `{_SSG}`, `DEFAULT_CONCURRENCY` is `2`."
    ssg_first = [{**_HITS[1], "score": 4.6, "summary": "SSG helper defaults."}, _HITS[0]]
    grade = _grade(
        "What is DEFAULT_CONCURRENCY in the ssg helper?", answer, citations=(_SSG,), hits=ssg_first
    )
    assert grade.confidence == "high"


def test_a_question_word_picking_one_definition_does_not_settle_the_other() -> None:
    grade = _grade("What is DEFAULT_CONCURRENCY in the ssg helper?", _ONE)
    assert grade.confidence == "medium"


def test_a_name_defined_once_is_untouched() -> None:
    assert _grade("DEFAULT_CONCURRENCY", _ONE, split={}).confidence == "high"


def test_a_lower_grade_carries_no_split_finding() -> None:
    weak = [{**h, "score": 0.5} for h in _HITS]
    grade = _grade_answer(
        question="DEFAULT_CONCURRENCY",
        question_ids={"DEFAULT_CONCURRENCY"},
        answer_text=_ONE,
        hits=weak,
        citations=[_POOL],
        symbol_bodies=[],
        served_named_body=False,
        dominance=None,
        split_names=_SPLIT,
    )
    assert grade.confidence == "medium"
    assert grade.split_names == {}


def test_the_note_names_every_definition() -> None:
    note = _split_names_note(_SPLIT)
    assert _SSG in note and _POOL in note and note.startswith("Held at medium")


def _row(name, path, language="typescript"):
    return SimpleNamespace(name=name, file_path=path, language=language)


def test_split_definitions_counts_production_files_only() -> None:
    rows = [
        _row("DEFAULT_CONCURRENCY", _SSG),
        _row("DEFAULT_CONCURRENCY", _POOL),
        _row("run", "src/a.ts"),
        _row("run", "src/a.ts"),
        _row("run", "src/a.test.ts"),
        _row("orphan", None),
    ]
    assert _split_definitions(rows) == {"DEFAULT_CONCURRENCY": [_SSG, _POOL]}


def test_split_lines_keep_each_files_first_indexed_line() -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _split_lines

    rows = [
        SimpleNamespace(name="DEFAULT_CONCURRENCY", file_path=_SSG, start_line=40),
        SimpleNamespace(name="DEFAULT_CONCURRENCY", file_path=_SSG, start_line=17),
        SimpleNamespace(name="DEFAULT_CONCURRENCY", file_path=_POOL, start_line=6),
        SimpleNamespace(name="DEFAULT_CONCURRENCY", file_path="src/a.test.ts", start_line=2),
        SimpleNamespace(name="other", file_path=_POOL, start_line=1),
    ]
    assert _split_lines(rows, _SPLIT) == {"DEFAULT_CONCURRENCY": {_SSG: 17, _POOL: 6}}


def test_a_generic_name_defined_in_many_files_is_not_split_checked() -> None:
    rows = [_row("get", f"src/m{i}.ts") for i in range(6)]
    assert _split_definitions(rows) == {}


class _UntrustingGuard:
    async def trusts(self, token, row):
        return False


def test_a_name_written_as_an_owner_anchors_its_definition() -> None:
    import asyncio

    from repowise.server.mcp_server.tool_answer.symbols import _trusted_over_topic

    client = SimpleNamespace(
        name="Client", file_path="httpx/_client.py", parent_name=None, qualified_name="httpx.Client"
    )
    by_name = {"Client": [client]}
    run = asyncio.run
    assert run(_trusted_over_topic(by_name, {"Client", "Client.get"}, _UntrustingGuard())) == by_name
    assert run(_trusted_over_topic(by_name, {"Client"}, _UntrustingGuard())) == {}
    other = SimpleNamespace(
        name="Client", file_path="pkg/rpc.py", parent_name=None, qualified_name="pkg.rpc.Client"
    )
    two = {"Client": [client, other]}
    assert run(_trusted_over_topic(two, {"Client", "Client.get"}, _UntrustingGuard())) == {}


# --- narrowing a shared name to the definitions the question names -----------

_PAGES = "src/adapter/cloudflare-pages/handler.ts"
_WORKERS = "src/adapter/cloudflare-workers/serve-static.ts"
_DENO = "src/adapter/deno/serve-static.ts"
_DENO_PKG = "adapters/deno/src/serve-static.ts"


def _defs(*paths):
    return [
        SimpleNamespace(
            name="serveStatic", file_path=p, kind="function", parent_name=None,
            qualified_name="serveStatic",
        )
        for p in paths
    ]


async def _choose(question, cands, question_ids=frozenset({"serveStatic"})):
    from repowise.server.mcp_server.tool_answer.symbols import _choose_anchor_symbols

    async def _verified(c):
        return {"path": c.file_path}

    homonyms = {"union": {}, "qualified_miss": []}
    chosen = await _choose_anchor_symbols(
        {"serveStatic": cands}, set(question_ids), _verified, homonyms, question=question
    )
    return [c.file_path for c in chosen], {
        name: [d["path"] for d in defs] for name, defs in homonyms["union"].items()
    }


async def test_a_question_naming_one_file_anchors_its_definition() -> None:
    chosen, union = await _choose(
        "serveStatic in the cloudflare pages handler", _defs(_PAGES, _WORKERS, _DENO)
    )
    assert chosen == [_PAGES]
    assert union == {}


async def test_a_question_naming_several_files_unions_only_those() -> None:
    chosen, union = await _choose(
        "how does serveStatic work on deno", _defs(_PAGES, _WORKERS, _DENO, _DENO_PKG)
    )
    assert chosen == []
    assert union == {"serveStatic": [_DENO, _DENO_PKG]}


async def test_a_question_naming_no_file_unions_every_definition() -> None:
    chosen, union = await _choose("how does serveStatic work", _defs(_PAGES, _WORKERS, _DENO))
    assert chosen == []
    assert union == {"serveStatic": [_PAGES, _WORKERS, _DENO]}


async def test_a_qualifier_that_matches_nothing_is_still_a_miss() -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _choose_anchor_symbols

    async def _verified(c):
        return {"path": c.file_path}

    homonyms = {"union": {}, "qualified_miss": []}
    chosen = await _choose_anchor_symbols(
        {"serveStatic": _defs(_PAGES, _DENO)},
        {"Router.serveStatic", "serveStatic"},
        _verified,
        homonyms,
        question="Router.serveStatic on deno",
    )
    assert chosen == []
    assert homonyms["qualified_miss"] == ["serveStatic"]


def test_the_path_sharing_the_most_question_words_is_the_one_named() -> None:
    from repowise.server.mcp_server._query_terms import paths_the_question_names

    paths = [_PAGES, _WORKERS]
    question = "the cloudflare pages handler"
    assert paths_the_question_names("serveStatic", paths, question, best_only=True) == [_PAGES]
    assert paths_the_question_names("serveStatic", paths, question) == paths
    assert paths_the_question_names("serveStatic", paths, "how does it work") == []


def test_a_question_partly_naming_a_second_definition_does_not_settle_the_split() -> None:
    """Grading stays strict: a question word that also names the other file
    leaves the answer to tell both definitions apart."""
    split = {"serveStatic": [_PAGES, _WORKERS]}
    answer = f"In `{_PAGES}`, `serveStatic` serves assets for advanced mode."
    grade = _grade(
        "serveStatic in the cloudflare pages handler", answer, split=split, citations=(_PAGES,)
    )
    assert grade.split_names == split


# --- The value fast path ------------------------------------------------------


def _value_reply(root, split, **extraction):
    from repowise.server.mcp_server.tool_answer.payload import build_value_payload

    entry = {
        "name": "DEFAULT_CONCURRENCY",
        "signature": "const DEFAULT_CONCURRENCY = 1024",
        "file": _POOL,
        "line": 6,
        "answer": f"const DEFAULT_CONCURRENCY = 1024  ({_POOL}:6)",
        "exact_name": True,
        **extraction,
    }
    return build_value_payload(
        extraction=entry,
        hits=[],
        fallback_targets=[],
        repository=None,
        t0=0.0,
        split_at=split,
        repo_root=root,
    )


def _write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_a_value_defined_in_two_files_lists_both_and_keeps_high(tmp_path) -> None:
    _write(tmp_path, _SSG, "import x from 'y'\n\nconst DEFAULT_CONCURRENCY = 2 // ssg\n")
    reply = _value_reply(tmp_path, {_SSG: 3, _POOL: 6})
    assert reply["confidence"] == "high"
    assert f"const DEFAULT_CONCURRENCY = 2 // ssg  ({_SSG}:3)" in reply["answer"]
    assert f"({_POOL}:6)" in reply["answer"]
    assert reply["citations"] == [_POOL, _SSG]
    assert "no verification Read needed" in reply["note"]


def test_a_value_whose_other_definition_cannot_be_read_is_held_at_medium(tmp_path) -> None:
    _write(tmp_path, _SSG, "export function DEFAULT_CONCURRENCY() {}\n")
    reply = _value_reply(tmp_path, {_SSG: 1, _POOL: 6})
    assert reply["confidence"] == "medium"
    assert _SSG in reply["note"] and _POOL in reply["note"]
    assert "no verification Read needed" not in reply["note"]
    assert reply["citations"] == [_POOL]


def test_a_multi_line_value_in_another_file_is_served_whole(tmp_path) -> None:
    _write(tmp_path, _SSG, "const DEFAULT_CONCURRENCY = {\n  pages: 2,\n}\nconst other = 1\n")
    reply = _value_reply(tmp_path, {_SSG: 1, _POOL: 6}, value_source="const DEFAULT_CONCURRENCY = 1024")
    assert reply["confidence"] == "high"
    assert "const DEFAULT_CONCURRENCY = {\n  pages: 2,\n}" in reply["answer"]
    assert "const other" not in reply["answer"]


def test_a_value_defined_once_is_answered_as_before(tmp_path) -> None:
    reply = _value_reply(tmp_path, None, value_source="const DEFAULT_CONCURRENCY = 1024")
    assert reply["confidence"] == "high"
    assert reply["answer"] == (
        f"const DEFAULT_CONCURRENCY = 1024  ({_POOL}:6)\n\nconst DEFAULT_CONCURRENCY = 1024"
    )


def test_a_drifted_index_line_is_held_at_medium(tmp_path) -> None:
    _write(tmp_path, _SSG, "// moved\nconst DEFAULT_CONCURRENCY = 2\n")
    assert _value_reply(tmp_path, {_SSG: 1, _POOL: 6})["confidence"] == "medium"


def test_only_a_statement_declaring_the_name_counts() -> None:
    from repowise.server.mcp_server.tool_answer.payload import _assignment

    for line in (
        "// LIMIT = 9 in a comment",
        "obj.LIMIT = 8",
        "run(LIMIT=7)",
        "def f(a, LIMIT=3):",
        "const { a, LIMIT = 5 } = opts",
        "    LIMIT=3,",
        "if LIMIT == 6:",
    ):
        assert _assignment(line, "LIMIT", 1) is None, line
    for line in (
        "\tLIMIT ShellCompDirective = 5",
        "LIMIT: int = 4",
        "pub const LIMIT: u32 = 3;",
        "export const LIMIT = 2",
    ):
        assert _assignment(line, "LIMIT", 1) == (1, line), line


def test_a_value_not_shown_whole_is_not_a_definition() -> None:
    from repowise.server.mcp_server.tool_answer.payload import _assignment

    assert _assignment("const LIMIT = [\n" + "  1,\n" * 60 + "]", "LIMIT", 1) is None
    assert _assignment('LIMIT = """\nabc\n"""', "LIMIT", 1) is None
    # A continued value runs to the line that ends it.
    assert _assignment("const LIMIT =\n  2 * 1024\nfoo()", "LIMIT", 1) == (
        1,
        "const LIMIT =\n  2 * 1024",
    )
    # Brackets in strings and comments do not hold the statement open.
    text = 'LIMIT = "(" # (\nother = (1)'
    assert _assignment(text, "LIMIT", 1) == (1, 'LIMIT = "(" # (')


@pytest.mark.asyncio
async def test_get_answer_lists_every_definition_of_a_value(setup_mcp, tmp_path, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    one, two = "pkg/alpha/one.py", "pkg/beta/two.py"
    _write(tmp_path, two, "MIN_COUNT = 5\n")
    symbol = {
        "name": "MIN_COUNT",
        "kind": "constant",
        "signature": "MIN_COUNT = 2",
        "start_line": 10,
        "end_line": 10,
        "_matched": True,
    }

    async def _retrieve(question, ctx):
        return [{"page_id": f"file_page:{one}", "score": 5.0}]

    async def _hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h.update(target_path=one, title=one, summary="", snippet="", page_type="file_page")
            h["symbols"] = [dict(symbol)]
        return hits

    async def _anchor(session, repo_id, question_ids, hits, **_kw):
        return hits, {
            "union": {},
            "qualified_miss": [],
            "split": {"MIN_COUNT": [one, two]},
            "split_at": {"MIN_COUNT": {one: 10, two: 1}},
        }

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _hydrate)
    monkeypatch.setattr(answer_mod, "_anchor_symbol_hits", _anchor)

    result = await get_answer("What is the default value of MIN_COUNT?")
    assert result["grounding"] == "extracted"
    assert result["confidence"] == "high"
    assert f"MIN_COUNT = 5  ({two}:1)" in result["answer"]
    assert result["citations"] == [one, two]
