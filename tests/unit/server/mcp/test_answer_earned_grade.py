"""Grades for correct answers the cascade under-rated, and the guards that keep
wrong ones from gaining: a closing caveat is not a hedge, a verified constant
line the answer states earns high, and a name no code carries grades low."""

from __future__ import annotations

import asyncio

from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _grade_answer,
    stated_value_line,
)
from repowise.server.mcp_server.tool_answer.payload import (
    _held_at_medium_note,
    _high_confidence_note,
)
from repowise.server.mcp_server.tool_answer.symbols import _names_nothing_in_code, _TopicGuard

_TS = "core/ingestion/extractors/imports/ts_js.py"
_TS_HITS = [
    {
        "target_path": _TS,
        "score": 4.4,
        "summary": "TypeScript and JavaScript import extraction for ingestion, "
        "including dynamic import() call expressions.",
        "symbols": [
            {
                "name": "_is_dynamic_esm_import",
                "kind": "function",
                "signature": "def _is_dynamic_esm_import(stmt_node)",
                "_verified": True,
            }
        ],
    },
    {
        "target_path": "core/ingestion/extractors/bindings/ts_js.py",
        "score": 3.2,
        "symbols": [{"name": "dynamic_import_bindings", "kind": "function", "_verified": True}],
    },
]
_TS_QUESTION = "How are TypeScript dynamic import() expressions handled in ingestion?"
_TS_CLAIM = (
    "Dynamic `import()` calls are detected by `_is_dynamic_esm_import`, which checks for a "
    "call_expression whose function node is `import`; `dynamic_import_bindings` binds them."
)
_TS_CAVEAT = "The excerpt does not include the body of `_ts_js_statement`, which wires it in."


def _grade(question, answer, hits, *, dominance="gap", ids=(), **kw):
    return _grade_answer(
        question=question,
        question_ids=set(ids),
        answer_text=answer,
        hits=hits,
        citations=[hits[0]["target_path"]],
        symbol_bodies=[],
        served_named_body=False,
        dominance=dominance,
        **kw,
    )


# --- a closing caveat after a grounded claim is not a hedge ------------------


def test_a_closing_caveat_holds_a_grounded_answer_at_medium_not_low() -> None:
    grade = _grade(
        _TS_QUESTION, f"{_TS_CLAIM} {_TS_CAVEAT}", _TS_HITS,
        ids={"TypeScript"}, unindexed_names={"TypeScript"},
    )
    assert not grade.hedged
    assert grade.confidence == "medium"
    assert grade.admitted_gap == _TS_CAVEAT


def test_the_same_admission_before_any_claim_is_still_a_hedge() -> None:
    grade = _grade(_TS_QUESTION, f"{_TS_CAVEAT} {_TS_CLAIM}", _TS_HITS, ids={"TypeScript"})
    assert grade.hedged
    assert grade.confidence == "low"


def test_naming_only_what_the_question_named_is_no_claim() -> None:
    assert _answer_is_hedged(
        "`RetryPolicyManager` is in the excerpts' list. The excerpts do not contain its body.",
        "How does RetryPolicyManager back off?",
    )


def test_an_admission_is_the_answer_when_retrieval_lacks_what_was_asked() -> None:
    hits = [{"target_path": "completions.go", "score": 3.0, "summary": "Shell completion.",
             "symbols": [{"name": "GenBashCompletionV2", "kind": "function"}]}]
    answer = (
        "Cobra generates completions through `GenBashCompletionV2`. "
        "The excerpts do not contain any Kubernetes reconciliation code."
    )
    grade = _grade("Where does cobra implement Kubernetes reconciliation?", answer, hits)
    assert grade.hedged and grade.confidence == "low"


def test_an_unanswered_value_is_a_hedge() -> None:
    assert _answer_is_hedged(
        "The default value of the floor in `get_answer` is not present.",
        "What is the default value of the floor in get_answer?",
    )


# --- a verified constant line the answer states earns high ------------------

_CFG = "packages/server/src/repowise/server/mcp_server/tool_answer/config.py"
_FLOOR = {
    "name": "_HIGH_CONFIDENCE_SCORE_FLOOR",
    "kind": "constant",
    "signature": "_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5",
    "start_line": 299,
    "_verified": True,
}
_FLOOR_QUESTION = "What is the default value of the high confidence score floor in get_answer?"
_FLOOR_ANSWER = "The floor is 1.5: `_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5` in config.py."


def _floor_hits(floor: dict | None = None, extra: list[dict] | None = None) -> list[dict]:
    return [
        {"target_path": _CFG, "score": 2.0, "summary": "Tuning constants for get_answer.",
         "symbols": [floor or _FLOOR]},
        {"target_path": "tool_answer/answer.py", "score": 2.1,
         "symbols": [{"name": "get_answer", "kind": "function", "_matched": True}]},
        *(extra or []),
    ]


def test_a_stated_verified_constant_earns_high_on_an_ambiguous_retrieval() -> None:
    hits = _floor_hits()
    before = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, hits, dominance=None, ids={"get_answer"})
    assert before.confidence == "high"
    assert before.high_reason == "value_line"
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, _FLOOR_ANSWER, hits) == {
        "path": _CFG, "name": "_HIGH_CONFIDENCE_SCORE_FLOOR", "line": 299,
    }


def test_an_exact_name_lookup_of_a_variable_earns_high() -> None:
    alias = {
        "name": "_projected_get_answer", "kind": "variable", "_verified": True, "_matched": True,
        "signature": "_projected_get_answer = project_answer(_get_answer_raw)",
    }
    hits = [
        {"target_path": "tool_answer/answer.py", "score": 2.0, "symbols": [alias]},
        {"target_path": "tool_answer/projection.py", "score": 2.1},
    ]
    answer = "`_projected_get_answer` is `project_answer(_get_answer_raw)`, the projected tool."
    grade = _grade("_projected_get_answer", answer, hits, dominance=None,
                   ids={"_projected_get_answer"})
    assert (grade.confidence, grade.high_reason) == ("high", "value_line")


def test_a_lookup_answer_misreading_the_assignment_earns_nothing() -> None:
    alias = {
        "name": "_projected_get_answer", "kind": "variable", "_verified": True, "_matched": True,
        "signature": "_projected_get_answer = project_answer(_get_answer_raw)",
    }
    hits = [
        {"target_path": "tool_answer/answer.py", "score": 2.0, "symbols": [alias]},
        {"target_path": "tool_answer/projection.py", "score": 2.1},
    ]
    answer = "`_projected_get_answer` is an alias of `get_answer`."
    grade = _grade("_projected_get_answer", answer, hits, dominance=None,
                   ids={"_projected_get_answer"})
    assert grade.confidence == "medium"


def test_a_wrong_value_beside_the_right_one_earns_nothing() -> None:
    hits = _floor_hits()
    answer = (
        "It is set by `_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5`. "
        "At runtime `_HIGH_CONFIDENCE_SCORE_FLOOR` is raised to 2.0."
    )
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_a_longer_name_holding_the_constant_names_nothing() -> None:
    hits = _floor_hits()
    answer = "`_HIGH_CONFIDENCE_SCORE_FLOOR_MAX` is 1.5."
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_a_value_too_short_to_match_on_earns_nothing() -> None:
    hits = _floor_hits({**_FLOOR, "signature": '_HIGH_CONFIDENCE_SCORE_FLOOR = "a"'})
    answer = "`_HIGH_CONFIDENCE_SCORE_FLOOR` is a constant in config.py."
    assert stated_value_line(_FLOOR_QUESTION, {"get_answer"}, answer, hits) is None


def test_an_unverified_line_earns_nothing() -> None:
    hits = _floor_hits({**_FLOOR, "_verified": False})
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, hits, dominance=None, ids={"get_answer"})
    assert grade.confidence == "medium"


def test_a_value_the_line_does_not_assign_earns_nothing() -> None:
    answer = "The floor is 2.0, from `_HIGH_CONFIDENCE_SCORE_FLOOR`."
    grade = _grade(_FLOOR_QUESTION, answer, _floor_hits(), dominance=None, ids={"get_answer"})
    assert grade.confidence != "high"


def test_a_name_defined_twice_in_the_hits_earns_nothing() -> None:
    twin = {"target_path": "other/config.py", "score": 1.0, "symbols": [{**_FLOOR}]}
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, _floor_hits(extra=[twin]), dominance=None,
                   ids={"get_answer"})
    assert grade.confidence == "medium"


def test_a_constant_whose_page_misses_a_question_word_earns_nothing() -> None:
    # Two constants share the name; the one served is the pool's, not the SSG one asked about.
    pool = {"name": "DEFAULT_CONCURRENCY", "kind": "constant", "_verified": True,
            "signature": "const DEFAULT_CONCURRENCY = 1024"}
    hits = [
        {"target_path": "src/utils/concurrent.ts", "score": 2.0, "summary": "A task pool.",
         "symbols": [pool]},
        {"target_path": "src/helper/css/index.ts", "score": 2.1},
    ]
    answer = "The default concurrency is 1024 (`DEFAULT_CONCURRENCY`)."
    assert stated_value_line("What is the default concurrency for SSG in hono?",
                             {"SSG"}, answer, hits) is None


def test_the_value_line_note_points_at_the_quote() -> None:
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, _floor_hits(), dominance=None,
                   ids={"get_answer"})
    assert "verified line" in _high_confidence_note(grade, "")


# --- names no symbol carries ------------------------------------------------

_REDIRECT_HITS = [
    {"target_path": "httpx/_client.py", "score": 4.0, "summary": "Client redirect handling.",
     "symbols": [{"name": "_redirect_method", "kind": "method", "_verified": True,
                  "source_excerpt": 'if status == SEE_OTHER and method != "HEAD": method = "GET"'}]},
    {"target_path": "httpx/_types.py", "score": 2.8},
]
_REDIRECT_Q = "How does httpx decide the HTTP method when following a redirect?"
_REDIRECT_A = "`_redirect_method` turns a 303 into GET unless the method is HEAD."


def test_a_word_no_symbol_carries_does_not_demote_as_an_unhydrated_identifier() -> None:
    kept = _grade(_REDIRECT_Q, _REDIRECT_A, _REDIRECT_HITS, ids={"HTTP"},
                  unindexed_names={"HTTP"})
    demoted = _grade(_REDIRECT_Q, _REDIRECT_A, _REDIRECT_HITS, ids={"HTTP"})
    assert kept.confidence == "high"
    assert demoted.confidence == "medium"


def test_a_name_nothing_defines_writes_or_serves_grades_low() -> None:
    hits = [{"target_path": "packages/ui/src/zoom/types.ts", "score": 2.5,
             "summary": "Containment tree types for the zoom map."}]
    q = "How does the FileTree component render nested folders?"
    answer = "Folders nest through containment nodes in `ZoomNode`."
    grade = _grade(q, answer, hits, dominance="ratio", ids={"FileTree"},
                   unindexed_names={"FileTree"}, absent_names=("FileTree",))
    assert grade.confidence == "low"
    assert grade.absent_names == ("FileTree",)


def test_a_name_the_retrieval_serves_is_not_absent() -> None:
    hits = [{"target_path": "packages/ui/src/FileTree.tsx", "score": 2.5}]
    grade = _grade("How does FileTree render?", "`FileTree` renders rows.", hits,
                   dominance="ratio", absent_names=("FileTree",))
    assert grade.absent_names == ()


class _Guard:
    def __init__(self, written: set[str], unscanned: set[str] = frozenset()):
        self.names, self.unscanned = written, unscanned

    async def written(self, token: str, scope: str | None = None) -> bool | None:
        return None if token in self.unscanned else token in self.names


def test_only_code_shaped_names_no_line_writes_are_absent() -> None:
    guard = _Guard({"RealThing"}, unscanned={"BigRepoThing"})

    def absent(token: str) -> bool:
        return asyncio.run(_names_nothing_in_code(token, guard))

    assert absent("FileTree")
    assert absent("build_answer_index_v2")
    assert not absent("RealThing")
    assert not absent("SSG")  # an acronym is prose often enough
    assert not absent("Client.get")
    assert not absent("BigRepoThing")  # the scan stopped short: unknown, not absent


# --- markdown markup does not hide a hedge -----------------------------------

_TREE_Q = "How does the FileTree component in packages/ui render nested folders?"
_TREE_HITS = [
    {"target_path": "packages/ui/src/graph/code-graph-tree.tsx", "score": 2.3,
     "summary": "Outline tree of directories and files.",
     "symbols": [{"name": "CodeGraphTree", "kind": "function"}]},
    {"target_path": "packages/ui/src/zoom/types.ts", "score": 1.6},
]


def test_an_emphasised_admission_is_still_a_hedge() -> None:
    answer = (
        "## What the excerpts show\n\nThe retrieved material does **not include the "
        "`FileTree` component** or its source body. The closest symbol is `CodeGraphTree`."
    )
    grade = _grade(_TREE_Q, answer, _TREE_HITS, dominance="ratio", ids={"FileTree"},
                   unindexed_names={"FileTree"})
    assert grade.hedged
    assert grade.confidence == "low"


def test_markup_inside_an_identifier_is_not_stripped() -> None:
    # Only edge underscores go, so a snake_case name cannot turn into a phrase.
    assert not _answer_is_hedged("`does_not_include_tests` filters the walk.", "What filters it?")


# --- a scoped question's name nothing under the scope writes ------------------


def _scanned_guard(rows: list[str], *, complete: bool = True) -> _TopicGuard:
    guard = _TopicGuard(None, [], "", set())
    guard._scans["FileTree"] = ([{"path": p, "line": 1, "text": "FileTree"} for p in rows], complete)
    return guard


def test_a_name_written_only_outside_the_scope_is_absent_in_it() -> None:
    guard = _scanned_guard(["packages/server/symbols.py"])
    assert asyncio.run(_names_nothing_in_code("FileTree", guard, "packages/ui/src"))
    assert not asyncio.run(_names_nothing_in_code("FileTree", guard))


def test_a_name_written_under_the_scope_is_not_absent() -> None:
    inside = _scanned_guard(["packages/ui/src/tree/FileTree.tsx"])
    assert not asyncio.run(_names_nothing_in_code("FileTree", inside, "packages/ui/src"))
    # A sibling directory sharing the prefix is outside the scope.
    sibling = _scanned_guard(["packages/ui/src-old/FileTree.tsx"])
    assert asyncio.run(_names_nothing_in_code("FileTree", sibling, "packages/ui/src"))
    # A scan that stopped short proves nothing.
    partial = _scanned_guard(["packages/server/symbols.py"], complete=False)
    assert not asyncio.run(_names_nothing_in_code("FileTree", partial, "packages/ui/src"))


# --- agreement alone needs a strong top score ---------------------------------

_CORS_Q = "How does the cors middleware handle a preflight OPTIONS request?"
_CORS_A = "The `cors` middleware sets `Access-Control-Allow-Methods` and returns 204."


def _cors_hits(top: float) -> list[dict]:
    return [
        {"target_path": "src/middleware/cors/index.ts", "score": top,
         "summary": "cors middleware: preflight OPTIONS request handling.",
         "symbols": [{"name": "cors", "kind": "function", "_matched": True}]},
        {"target_path": "src/client/types.ts", "score": top - 0.03},
    ]


def test_agreement_on_a_weak_top_score_holds_at_medium_with_a_note() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(3.27), dominance="agreement")
    assert grade.confidence == "medium"
    assert grade.weak_agreement
    assert "3.27" in _held_at_medium_note(grade)


def test_agreement_on_a_strong_top_score_stays_high() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(4.2), dominance="agreement")
    assert grade.confidence == "high"
    assert grade.high_reason == "agreement"
    assert not grade.weak_agreement


def test_the_agreement_floor_leaves_other_dominance_alone() -> None:
    grade = _grade(_CORS_Q, _CORS_A, _cors_hits(3.27), dominance="gap")
    assert grade.confidence == "high"
    assert not grade.weak_agreement


def test_a_specific_gate_keeps_its_finding_under_weak_agreement() -> None:
    hits = _cors_hits(3.27)
    grade = _grade(_CORS_Q, _CORS_A, hits, dominance="agreement", ids={"cors"},
                   split_names={"cors": ["src/middleware/cors/index.ts", "src/adapter/cors.ts"]})
    assert grade.confidence == "medium"
    assert grade.split_names
    assert not grade.weak_agreement


def test_a_file_cut_at_the_line_cap_leaves_the_word_scan_complete(tmp_path) -> None:
    from repowise.server.mcp_server._line_hits import MAX_LINES_PER_FILE, word_scan

    (tmp_path / "many.py").write_text("RowTree = 1\n" * (MAX_LINES_PER_FILE + 3), encoding="utf-8")
    rows, complete = word_scan(tmp_path, "RowTree")
    assert complete
    assert {r["path"] for r in rows} == {"many.py"}


def test_the_anchor_marks_a_name_absent_under_the_question_scope(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    class _NoRows:
        async def execute(self, *a, **k):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    (tmp_path / "server").mkdir()
    (tmp_path / "ui").mkdir()
    (tmp_path / "server" / "notes.py").write_text("register(RowTree)\n", encoding="utf-8")
    (tmp_path / "ui" / "tree.ts").write_text("export const outline = 1\n", encoding="utf-8")
    q = "How does the RowTree component render nested folders?"
    hits = [{"target_path": "ui/tree.ts", "score": 2.0}]

    def absent(scope):
        _, homonyms = asyncio.run(_anchor_symbol_hits(
            _NoRows(), "r", {"RowTree"}, list(hits), repo_root=tmp_path, question=q, scope=scope
        ))
        return homonyms["absent"]

    assert absent("ui") == ["RowTree"]
    assert absent(None) == []  # written elsewhere in the repository


def test_the_absent_name_note_names_the_missing_code() -> None:
    from repowise.server.mcp_server.tool_answer.payload import _noted_unfounded_claim

    grade = _grade(_TREE_Q, "Folders nest through `CodeGraphTree`.", _TREE_HITS,
                   dominance="ratio", ids={"FileTree"}, unindexed_names={"FileTree"},
                   absent_names=("FileTree",))
    payload: dict = {}
    assert _noted_unfounded_claim(payload, grade, [])
    assert payload["note"].startswith("Nothing in the code searched defines or writes FileTree")


# --- a verified value line earns high under the score floor -------------------

_RATIO = {"name": "_DOMINANCE_RATIO", "kind": "constant", "signature": "_DOMINANCE_RATIO = 1.2",
          "start_line": 119, "_verified": True, "_matched": True}
_RATIO_A = "`_DOMINANCE_RATIO` is set to 1.2 in config.py: `_DOMINANCE_RATIO = 1.2`."


def _ratio_hits(top: float, sym: dict | None = None) -> list[dict]:
    return [
        {"target_path": _CFG, "score": top, "symbols": [sym or _RATIO]},
        {"target_path": "tool_answer/_meta.py", "score": top / 1.5},
    ]


def test_a_stated_value_line_earns_high_under_the_score_floor() -> None:
    grade = _grade("What is _DOMINANCE_RATIO?", _RATIO_A, _ratio_hits(1.3),
                   dominance="ratio", ids={"_DOMINANCE_RATIO"})
    assert (grade.confidence, grade.high_reason) == ("high", "value_line")
    assert "verified line" in _high_confidence_note(grade, "")


def test_a_dominant_answer_under_the_floor_without_a_value_line_is_noted() -> None:
    from repowise.server.mcp_server.tool_answer.payload import _closing_note

    answer = "`_DOMINANCE_RATIO` sets how far the top hit must lead."
    grade = _grade("What is _DOMINANCE_RATIO?", answer, _ratio_hits(1.3),
                   dominance="ratio", ids={"_DOMINANCE_RATIO"})
    assert grade.confidence == "medium"
    assert grade.under_floor
    assert "1.30" in _closing_note(grade, [])


def _pad_hits(excerpt: str) -> list[dict]:
    # A Go const block stores the bare name as the signature.
    pad = {"name": "minUsagePadding", "kind": "variable", "signature": "minUsagePadding",
           "source_excerpt": excerpt, "_verified": True, "_matched": True}
    return [{"target_path": "command.go", "score": 1.3, "symbols": [pad]},
            {"target_path": "completions.go", "score": 0.9}]


def test_a_bare_name_signature_reads_the_value_from_its_live_line() -> None:
    answer = "In command.go, `minUsagePadding` is a constant set to 25."
    grade = _grade("What is minUsagePadding?", answer, _pad_hits("const minUsagePadding = 25"),
                   dominance="ratio", ids={"minUsagePadding"})
    assert (grade.confidence, grade.high_reason) == ("high", "value_line")


def test_a_live_line_assigning_another_name_reads_no_value() -> None:
    answer = "In command.go, `minUsagePadding` is a constant set to 25."
    grade = _grade("What is minUsagePadding?", answer, _pad_hits("// see padding = 25 below"),
                   dominance="ratio", ids={"minUsagePadding"})
    assert grade.confidence == "medium"


def _ranked_low_hits(matched: bool) -> list[dict]:
    const = {"name": "_GATED_RETURN_HITS", "kind": "constant", "start_line": 222,
             "signature": "_GATED_RETURN_HITS = 3", "_verified": True, "_matched": matched}
    return [
        {"target_path": "core/health/perf/gated.py", "score": 1.81,
         "symbols": [{"name": "LOCK_IO_KIND", "kind": "constant", "_verified": True}]},
        {"target_path": "core/crud/shown.py", "score": 1.8},
        {"target_path": "core/health/sink_reach.py", "score": 1.79},
        {"target_path": _CFG, "score": 1.77, "symbols": [const]},
    ]


_GATED_A = f"`_GATED_RETURN_HITS` is defined in {_CFG} as `_GATED_RETURN_HITS = 3`."


def test_an_exactly_named_constant_earns_high_wherever_it_ranked() -> None:
    grade = _grade("What is _GATED_RETURN_HITS?", _GATED_A, _ranked_low_hits(True),
                   dominance=None, ids={"_GATED_RETURN_HITS"}, lead_off_top=_CFG)
    # Leading with the value line's file, and that file hydrating the name,
    # are the evidence: neither the named-file nor the identifier gate demotes.
    assert (grade.confidence, grade.high_reason) == ("high", "value_line")
    assert grade.lead_off_top is None
    assert not grade.unhydrated_names


def test_a_constant_ranked_low_without_its_exact_name_earns_nothing() -> None:
    grade = _grade("What is _GATED_RETURN_HITS?", _GATED_A, _ranked_low_hits(False),
                   dominance=None, ids={"_GATED_RETURN_HITS"}, lead_off_top=_CFG)
    assert grade.confidence == "medium"


def test_an_unhydrated_question_name_is_held_with_a_note() -> None:
    hits = [{"target_path": "cache.py", "score": 10.5, "summary": "The answer cache.",
             "symbols": [{"name": "_cache_entry_expired", "kind": "function"}]},
            {"target_path": "synthesis.py", "score": 9.4}]
    grade = _grade("How does get_answer cache answers?", "Rows expire after a TTL.", hits,
                   ids={"get_answer"})
    assert grade.confidence == "medium"
    assert grade.unhydrated_names == ("get_answer",)
    assert "get_answer" in _held_at_medium_note(grade)


# --- the absent-name scan reads only code ------------------------------------


def _writes(tmp_path, rel: str, text: str) -> bool:
    from repowise.server.mcp_server.tool_answer.symbols import _writes_as_code

    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _writes_as_code(tmp_path, [{"path": rel, "line": 1}], "RowTree")


def test_a_docstring_or_comment_does_not_write_a_name(tmp_path) -> None:
    assert not _writes(tmp_path, "a.py", 'def f():\n    """Shapes such as\n    RowTree.\n    """\n')
    assert not _writes(tmp_path, "b.py", '"""RowTree is an example."""\n')
    assert not _writes(tmp_path, "c.py", "# RowTree is an example\n")
    assert not _writes(tmp_path, "d.ts", "/** RowTree */\nconst x = 1\n")
    assert _writes(tmp_path, "e.py", "tree = RowTree()\n")
    assert _writes(tmp_path, "f.py", 'KEYS = ("RowTree",)\n')  # a production key is real


def test_a_hash_or_star_line_is_code_outside_hash_comment_languages(tmp_path) -> None:
    assert _writes(tmp_path, "a.h", "#define RowTree 3\n")
    assert _writes(tmp_path, "b.rs", "#[derive(RowTree)]\n")
    assert _writes(tmp_path, "c.ts", "#RowTree = 0;\n")
    assert _writes(tmp_path, "d.c", "*ptr = RowTree;\n")
    assert not _writes(tmp_path, "e.rb", "# RowTree is an example\n")


def test_a_test_writes_a_name_only_outside_its_strings(tmp_path) -> None:
    assert not _writes(tmp_path, "tests/test_a.py", 'q = "How does RowTree render?"\n')
    assert not _writes(tmp_path, "tests/test_c.py", "q = 'How does RowTree render?'\n")
    assert _writes(tmp_path, "tests/test_b.py", "from ui import RowTree\n")
    # A Rust lifetime is not a quote.
    assert _writes(tmp_path, "tests/tree_test.rs", "fn f<'a>(t: &'a RowTree<'a>) {}\n")


def test_a_negated_literal_is_not_a_stated_value() -> None:
    debug = {"name": "DEBUG_MODE", "kind": "constant", "signature": "DEBUG_MODE = False",
             "_verified": True, "_matched": True}
    hits = [{"target_path": "settings.py", "score": 1.2, "symbols": [debug]},
            {"target_path": "app.py", "score": 0.8}]
    said = _grade("What is DEBUG_MODE?", "`DEBUG_MODE` is False.", hits, dominance="ratio",
                  ids={"DEBUG_MODE"})
    denied = _grade("What is DEBUG_MODE?", "`DEBUG_MODE` is not False; it is on.", hits,
                    dominance="ratio", ids={"DEBUG_MODE"})
    assert (said.confidence, denied.confidence) == ("high", "medium")


def test_an_arrow_or_a_commented_line_assigns_nothing() -> None:
    answer = "In command.go, `minUsagePadding` is 25."
    for excerpt in ("minUsagePadding: () => 25", "// minUsagePadding = 25"):
        grade = _grade("What is minUsagePadding?", answer, _pad_hits(excerpt),
                       dominance="ratio", ids={"minUsagePadding"})
        assert grade.confidence == "medium", excerpt


def test_a_value_line_does_not_spare_a_dominant_high_the_gates_that_measure_dominance() -> None:
    # Dominance vouched for this high, so the gates checking the top hits still apply.
    grade = _grade("What is _GATED_RETURN_HITS?", _GATED_A, _ranked_low_hits(True),
                   dominance="gap", ids={"_GATED_RETURN_HITS"})
    assert grade.confidence == "medium"
    assert grade.unhydrated_names == ("_GATED_RETURN_HITS",)


def test_an_unreadable_file_counts_and_a_write_past_the_scan_cap_is_found(tmp_path) -> None:
    from repowise.server.mcp_server._line_hits import MAX_LINES_PER_FILE
    from repowise.server.mcp_server.tool_answer.symbols import _writes_as_code

    assert _writes_as_code(tmp_path, [{"path": "gone.py", "line": 1}], "RowTree")
    comments = "# RowTree\n" * MAX_LINES_PER_FILE
    (tmp_path / "many.py").write_text(comments + "tree = RowTree()\n", encoding="utf-8")
    (tmp_path / "notes.py").write_text(comments * 2, encoding="utf-8")
    rows = [{"path": p, "line": n} for p in ("many.py", "notes.py")
            for n in range(1, MAX_LINES_PER_FILE + 1)]
    assert _writes_as_code(tmp_path, rows, "RowTree")
    assert not _writes_as_code(tmp_path, [r for r in rows if r["path"] == "notes.py"], "RowTree")


def test_a_name_only_a_comment_writes_is_absent(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    class _NoRows:
        async def execute(self, *a, **k):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    (tmp_path / "notes.py").write_text("# RowTree is an example\n", encoding="utf-8")
    _, homonyms = asyncio.run(_anchor_symbol_hits(
        _NoRows(), "r", {"RowTree"}, [{"target_path": "notes.py", "score": 2.0}],
        repo_root=tmp_path, question="How does the RowTree component render?",
    ))
    assert homonyms["absent"] == ["RowTree"]


# --- caveats about what was not asked ----------------------------------------


_LINE_ONLY = (
    "The excerpt does not include line numbers.",
    "Its body isn't line-numbered in the excerpts, so I can't cite exact lines.",
    "`preExecHookFn` is a variable (the excerpt does not include line numbers):",
    "The excerpts don't include line numbers, so none are cited.",
)
_STILL_ADMITS = (
    "The excerpts do not include line numbers nor the validator.",
    "The excerpts do not include line numbers, nor the validator.",
    "The excerpts do not include the line numbers, plus the function that enforces it.",
    "The excerpts do not include the line numbers of the retry check.",
    "The excerpts do not show the line numbers or the code that reads it.",
    "The excerpts do not include the check, so I can't cite its line numbers.",
    "(The excerpts don't include the validator's name or implementation.)",
    # Punctuation is kept: the clause end after "is not included" still reads.
    "`_retry` is not included (in any hit).",
)
_CODE_BEHAVIOUR = (
    "`Client.send` retries 3 times and doesn't include backoff.",
    "It doesn't contain a lock.",
)


def test_a_missing_line_number_is_not_an_admission() -> None:
    from repowise.server.mcp_server.tool_answer.confidence import _is_admission

    assert not any(_is_admission(s) for s in _LINE_ONLY)
    assert all(_is_admission(s) for s in _STILL_ADMITS)
    assert not any(_is_admission(s) for s in _CODE_BEHAVIOUR)


def test_a_line_number_aside_before_the_claim_is_no_hedge() -> None:
    aside = "`_is_dynamic_esm_import` is in ts_js.py (the excerpt does not include line numbers):"
    grade = _grade(_TS_QUESTION, f"{aside} {_TS_CLAIM}", _TS_HITS)
    assert not grade.hedged
    assert grade.admitted_gap is None


_READ_CAVEAT = "The excerpts do not show the code that reads it, so I can't confirm how it is applied."


def _dominant_floor_hits(floor: dict | None = None) -> list[dict]:
    return [
        {"target_path": _CFG, "score": 5.0, "summary": "Default values of the get_answer tuning constants.",
         "symbols": [floor or _FLOOR]},
        {"target_path": "tool_answer/payload.py", "score": 2.0},
    ]


def test_a_caveat_beside_a_stated_value_line_keeps_high() -> None:
    grade = _grade(_FLOOR_QUESTION, f"{_FLOOR_ANSWER} {_READ_CAVEAT}", _dominant_floor_hits())
    assert grade.confidence == "high"
    assert grade.admitted_gap is None


def test_the_caveat_still_holds_an_answer_with_no_stated_value_line() -> None:
    unverified = {**_FLOOR, "_verified": False}
    grade = _grade(_FLOOR_QUESTION, f"{_FLOOR_ANSWER} {_READ_CAVEAT}", _dominant_floor_hits(unverified))
    assert grade.confidence == "medium"
    assert grade.admitted_gap == _READ_CAVEAT
    wrong = _grade(_FLOOR_QUESTION, f"The floor is 2.0. {_READ_CAVEAT}", _dominant_floor_hits())
    assert wrong.confidence != "high"


def test_a_stated_value_line_exempts_the_identifier_gate() -> None:
    hits = _dominant_floor_hits()
    grade = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, hits, ids={"get_answer"})
    assert grade.confidence == "high"
    assert grade.unhydrated_names == ()
    unverified = _dominant_floor_hits({**_FLOOR, "_verified": False})
    held = _grade(_FLOOR_QUESTION, _FLOOR_ANSWER, unverified, ids={"get_answer"})
    assert held.confidence == "medium"
    assert held.unhydrated_names == ("get_answer",)


def test_a_contracted_admission_before_any_claim_is_a_hedge() -> None:
    answer = (
        "The check is part of flag-group validation in command.go. "
        "(The excerpts don't include the validator's name or implementation.) "
        "It runs after `Execute` calls `ExecuteC`."
    )
    assert _answer_is_hedged(answer, "Where is the mutually exclusive flags check enforced?")
    # A contraction about the code is a claim, not an admission.
    grade = _grade(_TS_QUESTION, f"{_TS_CLAIM} It doesn't include the CJS path.", _TS_HITS)
    assert not grade.hedged
    assert grade.admitted_gap is None


def test_a_value_line_does_not_clear_a_gap_in_a_second_question() -> None:
    question = f"{_FLOOR_QUESTION[:-1]} and where is it read?"
    answer = f"{_FLOOR_ANSWER} The excerpts do not show where it is read."
    grade = _grade(question, answer, _dominant_floor_hits())
    assert grade.confidence != "high"


def test_the_synthesis_prompt_asks_only_for_gaps_that_limit_the_answer() -> None:
    from repowise.server.mcp_server.tool_answer.config import _SYSTEM_PROMPT, _USER_TEMPLATE

    system, user = (" ".join(p.split()) for p in (_SYSTEM_PROMPT, _USER_TEMPLATE))
    for prompt in (system, user):
        assert "limits the answer to this question" in prompt
        assert "line numbers" in prompt
    assert "say nothing about line numbers" in user
