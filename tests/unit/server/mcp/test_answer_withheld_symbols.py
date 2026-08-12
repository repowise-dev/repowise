"""Withheld-body handling for get_answer (follow-up to #1444 / PR #1445).

The gate PR #1445 added needs BOTH an exclusivity token in the prose AND a
truncated body. Measured across four independent runs of the reference defect,
the token appears in one of them; the other three are equally incomplete,
equally `confidence: high`, and the gate stays silent. It is also inert by
construction with no LLM, because there is no prose to hold a token.

These tests pin the follow-up behaviour:

  * a truncated body must say WHAT it withheld (`withheld_symbols`), so the
    consumer can continue inside the tool instead of falling back to Read;
  * confidence must drop when a withheld symbol is one the response depends on;
  * confidence must SURVIVE when the truncation withheld nothing relevant
    (22% of truncations measured, and `high` is worth keeping when earned);
  * the note must never tell the consumer to skip re-reading a payload that
    admits it withheld part of a cited body;
  * the dependency check must fire with NO prose at all, which is the no-LLM
    path and the one the previous gate cannot reach.

NOTE: the new helpers are imported INSIDE test bodies, never at module level.
A module-level import turns "this behaviour was wrong before" into a collection
error on the parent commit, so the file errors out and the end-to-end
assertions never run -- proving only that a function is new. With the imports
deferred the file collects on bd577942 and the e2e tests fail on real
assertions about confidence and the note.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

_SRC = '''\
class TodoStore:
    def write(self, todos):
        """Write todos."""
        return self._dedupe_by_id(todos)

    @staticmethod
    def _validate(item):
        """Validate and normalize."""
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"
        return {"id": item_id}

    @staticmethod
    def _dedupe_by_id(todos):
        """Drop duplicates."""
        return list({t["id"]: t for t in todos}.values())
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "todo_tool.py").write_text(_SRC, encoding="utf-8")
    return tmp_path


class TestWithheldDefinitions:
    def test_reports_symbol_whose_body_is_cut_by_the_boundary(self, repo) -> None:
        """The motivating case, and the one a naive implementation misses.

        `_validate` STARTS at line 6, before the cut, so its `def` line was
        served and it appears nowhere inside the withheld range. The line that
        causes the defect (`item_id = "?"`) is inside it. Reporting only defs
        that start after the cut would say nothing about the symbol the answer
        is actually about.
        """
        from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

        got = withheld_definitions(repo, "todo_tool.py:9-20")
        names = {d["name"] for d in got}
        assert "_validate" in names, f"expected _validate, got {names}"
        cut = next(d for d in got if d["name"] == "_validate")
        assert cut["body_continues"] is True
        assert cut["symbol_id"] == "todo_tool.py::_validate"

    def test_reports_definitions_starting_inside_the_range(self, repo) -> None:
        from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

        names = {d["name"] for d in withheld_definitions(repo, "todo_tool.py:9-20")}
        assert "_dedupe_by_id" in names

    @pytest.mark.parametrize(
        "cont", [None, "", "garbage", "todo_tool.py:x-y", "missing.py:1-9"]
    )
    def test_unreadable_input_yields_nothing(self, repo, cont) -> None:
        """A probe that cannot read must not manufacture doubt."""
        from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

        assert withheld_definitions(repo, cont) == []

    def test_no_repo_root_yields_nothing(self) -> None:
        from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

        assert withheld_definitions(None, "todo_tool.py:9-20") == []


class TestImplicatedWithheldSymbols:
    BODIES: ClassVar[list[dict]] = [
        {
            "truncated": True,
            "withheld_symbols": [
                {"name": "_validate", "symbol_id": "p::_validate", "body_continues": True}
            ],
        }
    ]

    def test_question_route_fires_with_no_prose_at_all(self) -> None:
        """The no-LLM path. PR #1445's gate cannot reach this case.

        With no synthesised prose there is no exclusivity token to find, so a
        prose-conditioned gate is silently inert exactly where a keyless
        deployment lives. The question is always present, so it is the route
        that has to carry both modes.
        """
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        assert implicated_withheld_symbols(
            "why does _validate stamp a placeholder id?", "", self.BODIES
        ) == ["_validate"]

    def test_answer_route_fires_on_code_reference(self) -> None:
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        assert implicated_withheld_symbols(
            "unrelated question", "the `_validate` helper stamps it", self.BODIES
        ) == ["_validate"]

    def test_prose_mention_as_english_does_not_fire(self) -> None:
        """Guards the false-positive that inflated the first measurement.

        Withheld symbols include ordinary English words (`on`, `line`, `input`,
        `width`). A bare word-boundary match scores "based on the excerpts" as a
        dependency on a symbol named `on`.
        """
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        bodies = [{"truncated": True, "withheld_symbols": [{"name": "on"}]}]
        assert implicated_withheld_symbols(
            "unrelated", "based on the excerpts", bodies
        ) == []

    def test_irrelevant_truncation_does_not_fire(self) -> None:
        """22% of truncations withhold nothing relevant; `high` is worth keeping."""
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        assert implicated_withheld_symbols(
            "how does write work?", "write delegates to a helper", self.BODIES
        ) == []

    def test_untruncated_body_never_fires(self) -> None:
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        bodies = [{"withheld_symbols": [{"name": "_validate"}]}]  # no truncated flag
        assert implicated_withheld_symbols("why does _validate", "x", bodies) == []

    def test_body_continues_sorts_first(self) -> None:
        """The sharper failure is a body cut mid-symbol, so it leads the note."""
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        bodies = [
            {
                "truncated": True,
                "withheld_symbols": [
                    {"name": "later_fn"},
                    {"name": "_validate", "body_continues": True},
                ],
            }
        ]
        got = implicated_withheld_symbols("_validate and later_fn", "", bodies)
        assert got[0] == "_validate", got


# ---------------------------------------------------------------------------
# End-to-end pipeline tests
# ---------------------------------------------------------------------------


def _build_e2e_src() -> str:
    """A class long enough to actually cross the inline-body line cap.

    The cap is 120 lines, so a short fixture is served whole and never
    truncates -- the first version of this test asserted on a truncation that
    never happened, and failed against the fix it was written for.

    Shaped to reproduce the real defect: `_validate`'s `def` line lands BEFORE
    the cut (so it is served and looks covered) while the line that matters,
    `item_id = "?"`, lands after it.
    """
    lines = ["class Store:"]
    for i in range(38):  # 3 lines each -> fills lines 2..115
        lines += [f"    def pad_{i}(self):", f"        return {i}", ""]
    assert len(lines) == 115, len(lines)
    lines += [
        "    @staticmethod",                                   # 116
        "    def _validate(item):",                            # 117 served
        '        """Validate."""',                             # 118
        '        item_id = str(item.get("id", "")).strip()',    # 119
        "        if not item_id:",                             # 120 boundary
        '            item_id = "?"',                           # 121 WITHHELD
        '        return {"id": item_id}',
        "",
    ]
    for i in range(6):
        lines += [f"    def tail_{i}(self):", f"        return {i}", ""]
    return "\n".join(lines) + "\n"


_E2E_SRC = _build_e2e_src()
_E2E_TOTAL_LINES = _E2E_SRC.count("\n")

# `Store` runs past the 120-line inline cap, so the served body stops at 120 and
# the entry truncates. `_validate`'s def (117) is inside the served part; the
# line that causes the defect (121) is not.
_TRUNCATED_CLASS = {
    "name": "Store",
    "kind": "class",
    "signature": "class Store",
    "docstring": "A store.",
    "start_line": 1,
    "end_line": _E2E_TOTAL_LINES,
    "_matched": True,
    "source_excerpt": "class Store:\n    def pad_0(self):\n        return 0",
}


def _patch_pipeline(monkeypatch, answer_mod, *, symbol: dict):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:store.py", "score": 5.0},
            {"page_id": "file_page:other.py", "score": 4.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Store summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if i == 0:
                h["symbols"] = [dict(symbol)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content: str):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


# The same file, but a symbol short enough to be served whole: the control for
# every "confidence must survive when nothing was withheld" assertion.
_WHOLE_SYMBOL = {
    "name": "Store",
    "kind": "class",
    "signature": "class Store",
    "docstring": "A store.",
    "start_line": 1,
    "end_line": 4,
    "_matched": True,
    "source_excerpt": "class Store:\n    def pad_0(self):\n        return 0",
}


def _patch_union(monkeypatch, answer_mod, *, path="store.py", end_line=None):
    """Force the homonym-union early return.

    Patching the anchor is the only practical way onto this path: the union
    fires on an exact-name index scan, and the payload returns before the
    provider is even resolved, so nothing downstream can steer it. The previous
    version of this file never drove `get_answer` down here, which is why the
    dead gate survived review of the unit tests.
    """

    async def _fake_anchor(session, repo_id, question_ids, hits, **_kw):
        return hits, {
            "union": {
                "Store": [
                    {
                        "name": "Store",
                        "file_path": path,
                        "start_line": 1,
                        "end_line": end_line or _E2E_TOTAL_LINES,
                    }
                ]
            },
            "qualified_miss": [],
        }

    monkeypatch.setattr(answer_mod, "_anchor_symbol_hits", _fake_anchor)


def _setup_repo(monkeypatch, tmp_path):
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "store.py").write_text(_E2E_SRC, encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_truncated_body_exposes_what_it_withheld(setup_mcp, monkeypatch, tmp_path):
    """FAILS on bd577942: the payload flags truncation but never says what was cut."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _setup_repo(monkeypatch, tmp_path)
    _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
    _patch_provider(monkeypatch, answer_mod, "Store.write delegates to a helper.")

    result = await get_answer("How does Store.write handle ids?")
    bodies = result.get("symbol_bodies") or []
    truncated = [b for b in bodies if b.get("truncated")]
    assert truncated, "expected a truncated body in this fixture"
    assert any(b.get("withheld_symbols") for b in truncated), (
        "a truncated body must name what it withheld so the consumer can "
        "continue inside the tool; got no withheld_symbols"
    )


@pytest.mark.asyncio
async def test_question_naming_withheld_symbol_drops_confidence(
    setup_mcp, monkeypatch, tmp_path
):
    """FAILS on bd577942. No exclusivity token anywhere, so gate 7 stays silent.

    This is the reproducible shape: the response depends on a symbol whose body
    was withheld, the prose contains no exclusivity language, and confidence
    reads `high` regardless.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _setup_repo(monkeypatch, tmp_path)
    _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
    # The answer must NAME the symbol: `_gather_body_candidates` only inlines a
    # body when `name in answer_text`, so a mock answer that never says "Store"
    # serves no symbol_bodies and the gate has nothing to inspect. The answer
    # deliberately contains NO exclusivity token.
    _patch_provider(monkeypatch, answer_mod, "Store normalises ids while writing todos.")

    # Names `Store` STANDALONE as well as `_validate`: a dotted
    # `Store._validate` resolves to `_validate`, which is not a hydrated symbol
    # here, so the question would serve no body at all.
    result = await get_answer("In Store, why does _validate stamp a placeholder id?")
    assert result["confidence"] != "high", (
        "confidence must not be high when the question names a symbol whose "
        f"body the payload withheld; got {result['confidence']!r}"
    )


@pytest.mark.asyncio
async def test_high_confidence_note_never_says_skip_rereading_when_truncated(
    setup_mcp, monkeypatch, tmp_path
):
    """FAILS on bd577942: the high note says 'do not re-read the source'."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _setup_repo(monkeypatch, tmp_path)
    _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
    _patch_provider(monkeypatch, answer_mod, "Store.write delegates to a helper.")

    result = await get_answer("How does Store.write work?")
    note = (result.get("note") or "").lower()
    assert "do not re-read" not in note, (
        "the payload withheld part of a cited body, so it must not tell the "
        f"consumer to skip re-reading; got: {note!r}"
    )


# ---------------------------------------------------------------------------
# The homonym-union path: the NO-LLM case.
#
# This payload is built and returned BEFORE the provider is resolved, so it is
# what a keyless deployment gets, and it reaches none of the confidence gates.
# It used to hardcode confidence="high" with "no verification Read" even when a
# body arrived truncated, and its note asserts "this is the complete set" --
# an exclusivity claim we generate ourselves rather than one a model wrote.
# ---------------------------------------------------------------------------


class TestUnionPathTruncation:
    def test_union_bodies_carry_withheld_symbols(self, tmp_path) -> None:
        """FAILS before: the union path flagged truncation without naming it."""
        from repowise.server.mcp_server.tool_answer.symbols import (
            build_homonym_union_bodies,
        )

        (tmp_path / "store.py").write_text(_E2E_SRC, encoding="utf-8")
        groups = {
            "Store": [
                {
                    "name": "Store",
                    "file_path": "store.py",
                    "start_line": 1,
                    "end_line": _E2E_TOTAL_LINES,
                }
            ]
        }
        bodies, _more = build_homonym_union_bodies(tmp_path, groups)
        truncated = [b for b in bodies if b.get("truncated")]
        assert truncated, "fixture must truncate for this test to mean anything"
        assert any(b.get("withheld_symbols") for b in truncated), (
            "the no-LLM union path must name what it withheld; it reaches no "
            "confidence gate, so the payload is the only place doubt can surface"
        )

    def test_confidence_helper_fires_on_union_bodies_without_prose(self) -> None:
        """The union path has no synthesised prose at all, by construction.

        A prose-conditioned check is inert here. The question route is the only
        one that can carry this path, which is the whole reason the gate is not
        keyed on the answer text alone.
        """
        from repowise.server.mcp_server.tool_answer.confidence import (
            implicated_withheld_symbols,
        )

        bodies = [
            {
                "truncated": True,
                "withheld_symbols": [
                    {"name": "_validate", "symbol_id": "store.py::_validate",
                     "body_continues": True}
                ],
            }
        ]
        assert implicated_withheld_symbols(
            "why does _validate stamp a placeholder id?", "", bodies
        ) == ["_validate"]

    @pytest.mark.asyncio
    async def test_union_answer_over_a_truncated_body_is_not_high(
        self, setup_mcp, monkeypatch, tmp_path
    ):
        """FAILS on 767391dd, and this is the blocker that review found.

        The union path is reached only for naming/lookup questions about the
        homonym, so the question names the SERVED symbol while the withheld
        symbols are its inner members, and there is no answer prose either. A
        dependency-keyed gate is therefore dead code here -- the same
        conjunction defect PR #1445 had, one layer down. The bodies ARE the
        answer on this path, so truncation alone has to cap it.

        The `grounding` assertion is deliberate: without it this test would keep
        passing if the pipeline stopped taking the union path at all, which is
        exactly how the previous version of this file missed the defect.
        """
        import repowise.server.mcp_server.tool_answer.answer as answer_mod
        from repowise.server.mcp_server import get_answer

        _setup_repo(monkeypatch, tmp_path)
        _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
        _patch_union(monkeypatch, answer_mod)

        result = await get_answer("where is Store defined?")
        assert result.get("grounding") == "exact_symbol", (
            f"this test only means something on the union path; got {result!r}"
        )
        assert any(b.get("truncated") for b in result["symbol_bodies"]), (
            "the fixture must truncate or there is nothing to gate on"
        )
        assert result["confidence"] != "high", (
            "a union answer whose cited body was cut must not read high; got "
            f"{result['confidence']!r}"
        )

    @pytest.mark.asyncio
    async def test_union_note_never_says_no_verification_read_when_truncated(
        self, setup_mcp, monkeypatch, tmp_path
    ):
        """FAILS on bd577942: the union note said 'no verification Read'."""
        import repowise.server.mcp_server.tool_answer.answer as answer_mod
        from repowise.server.mcp_server import get_answer

        _setup_repo(monkeypatch, tmp_path)
        _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
        _patch_union(monkeypatch, answer_mod)

        result = await get_answer("where is Store defined?")
        assert result.get("grounding") == "exact_symbol"
        assert "no verification read" not in (result.get("note") or "").lower()

    @pytest.mark.asyncio
    async def test_union_answer_over_whole_bodies_stays_high(
        self, setup_mcp, monkeypatch, tmp_path
    ):
        """The other direction: nothing withheld, so `high` survives.

        Without this the cap could be an unconditional demotion and every test
        above would still pass.
        """
        import repowise.server.mcp_server.tool_answer.answer as answer_mod
        from repowise.server.mcp_server import get_answer

        _setup_repo(monkeypatch, tmp_path)
        _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_CLASS)
        # A short definition, served whole.
        _patch_union(monkeypatch, answer_mod, end_line=4, path="small.py")
        (tmp_path / "small.py").write_text(
            "class Store:\n    def go(self):\n        return 1\n\n", encoding="utf-8"
        )

        result = await get_answer("where is Store defined?")
        assert result.get("grounding") == "exact_symbol"
        assert not any(b.get("truncated") for b in result["symbol_bodies"])
        assert result["confidence"] == "high"
        assert "no verification read" in (result.get("note") or "").lower()


# ---------------------------------------------------------------------------
# The question route must not collapse confidence on ordinary English.
#
# Symbols named `on`, `line`, `input`, `write`, `main`, `get` and `run` are
# everywhere, and a bare `\bname\b` match against the question demotes every
# question that happens to use the word. The route that carries the no-LLM mode
# is the one that must not over-fire.
# ---------------------------------------------------------------------------

_ENGLISH_COLLISIONS = [
    ("on", "which handler runs on startup?"),
    ("line", "how is each line of the log parsed?"),
    ("input", "where does user input get validated?"),
    ("write", "how do I write a new provider?"),
    ("main", "what is the main entry point?"),
    ("get", "how do I get the repo id?"),
    ("run", "how does a run get scheduled?"),
    ("width", "what sets the width of the output?"),
    ("join", "where do the two tables join?"),
]


@pytest.mark.parametrize("name,question", _ENGLISH_COLLISIONS)
def test_english_word_symbol_does_not_collapse_confidence(name, question) -> None:
    """FAILS on 767391dd for every row: the question route was a bare \\b match."""
    from repowise.server.mcp_server.tool_answer.confidence import (
        implicated_withheld_symbols,
    )

    bodies = [{"truncated": True, "withheld_symbols": [{"name": name}]}]
    assert implicated_withheld_symbols(question, "", bodies) == [], (
        f"a symbol named {name!r} must not be implicated by ordinary prose"
    )


@pytest.mark.parametrize(
    "name,question",
    [
        # Distinctive shapes: underscore, internal capital, digit.
        ("_validate", "why does _validate stamp a placeholder id?"),
        ("TodoStore", "what does TodoStore keep?"),
        ("dedupe_by_id", "what does dedupe_by_id drop?"),
        ("sha256", "where is sha256 computed?"),
        # Leading capital matches only with its own case.
        ("Store", "In Store, why is the id blank?"),
        # An English-word symbol still fires when named as code.
        ("write", "how does Store.write handle ids?"),
        ("run", "what does run() return?"),
        ("main", "what does `main` do first?"),
    ],
)
def test_question_route_still_fires_when_the_question_means_the_symbol(
    name, question
) -> None:
    """The other direction: the guard must not turn the route off entirely."""
    from repowise.server.mcp_server.tool_answer.confidence import (
        implicated_withheld_symbols,
    )

    bodies = [{"truncated": True, "withheld_symbols": [{"name": name}]}]
    assert implicated_withheld_symbols(question, "", bodies) == [name]


def test_leading_capital_name_is_not_matched_as_lowercase_english() -> None:
    from repowise.server.mcp_server.tool_answer.confidence import (
        implicated_withheld_symbols,
    )

    bodies = [{"truncated": True, "withheld_symbols": [{"name": "Store"}]}]
    assert implicated_withheld_symbols("where do we store the ids?", "", bodies) == []


def test_backticked_path_does_not_implicate_its_extension() -> None:
    """`store.py` is a path, not an attribute access on a symbol named `py`."""
    from repowise.server.mcp_server.tool_answer.confidence import _code_reference

    assert _code_reference("see `store.py` for this", "py") is False
    assert _code_reference("see `store.py` for this", "store") is False
    assert _code_reference("`Store._validate` stamps it", "_validate") is True


def test_prose_parenthetical_is_not_a_call() -> None:
    """FAILS on 767391dd: `\\bwidth\\s*\\(` matched 'the width (in pixels)'."""
    from repowise.server.mcp_server.tool_answer.confidence import _code_reference

    assert _code_reference("the width (in pixels) is fixed", "width") is False
    assert _code_reference("it calls width(px) first", "width") is True


# ---------------------------------------------------------------------------
# body_continues must be earned, not asserted.
# ---------------------------------------------------------------------------


def test_a_wholly_served_symbol_is_not_reported_as_continuing(tmp_path) -> None:
    """FAILS on 767391dd: the backward scan took the nearest preceding def.

    `alpha` ends at line 2 and the cut is at line 5, so `alpha` was served
    whole. It sorts FIRST in the note, so a false positive here becomes the
    headline name and the `get_symbol id=` the agent is told to call.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "a.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    got = withheld_definitions(tmp_path, "a.py:5-6")
    assert [d["name"] for d in got] == ["beta"], got
    assert not any(d.get("body_continues") for d in got)


def test_an_enclosing_symbol_that_does_reach_the_cut_is_reported(tmp_path) -> None:
    """The other direction: the class body genuinely spans the boundary."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "b.py").write_text(
        "class Store:\n"
        "    def head(self):\n"
        "        return 1\n"
        "    def tail(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "b.py:4-5")
    cut = [d for d in got if d.get("body_continues")]
    assert [d["name"] for d in cut] == ["Store"], got


# ---------------------------------------------------------------------------
# The scanner has to work on the languages the 120-line cap actually bites.
# ---------------------------------------------------------------------------


def test_typescript_definitions_are_found(tmp_path) -> None:
    """FAILS on 767391dd: the regex was `def|class`, so TS yielded nothing."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "c.ts").write_text(
        "export class Panel {\n"
        "  render(props: P): JSX.Element {\n"
        "    if (props.x) {\n"
        "      return null;\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        "}\n"
        "export function useThing(a: number) {\n"
        "  return a;\n"
        "}\n"
        "export const Widget = (props: P) => {\n"
        "  return null;\n"
        "};\n"
        "export interface Shape { x: number }\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "c.ts:3-15")
    names = [d["name"] for d in got]
    assert "useThing" in names and "Widget" in names and "Shape" in names, names
    # `render`'s body is cut by the boundary, so it leads.
    assert got[0]["name"] == "render" and got[0]["body_continues"] is True
    # Control flow is not a definition.
    assert "if" not in names


def test_go_definitions_are_found(tmp_path) -> None:
    """FAILS on 767391dd. A receiver method's name follows the receiver."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "d.go").write_text(
        "package main\n"
        "\n"
        "func (s *Store) Write(x int) error {\n"
        "\tfor i := 0; i < x; i++ {\n"
        "\t\ts.n++\n"
        "\t}\n"
        "\treturn nil\n"
        "}\n"
        "\n"
        "type Config struct {\n"
        "\tN int\n"
        "}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "d.go:5-12")
    names = [d["name"] for d in got]
    assert "Config" in names, names
    assert got[0]["name"] == "Write" and got[0]["body_continues"] is True
    assert "for" not in names


def test_a_go_anonymous_func_literal_is_not_a_symbol_named_func(tmp_path) -> None:
    """141 of 23,038 sweep entries on cli/cli, one of them the headline.

    ``func(`` has no space after the keyword, so the brace-member pattern's
    optional return-type group matches empty and the name group takes ``func``
    itself — an unresolvable ``d.go::func`` that the note then tells the agent
    to fetch. The named function around it must still be reported.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "rt.go").write_text(
        "package main\n"
        "\n"
        "func NewClient(t *testing.T) *http.Client {\n"
        "\treg := &httpmock.Registry{}\n"
        "\treg.Register(\n"
        "\t\thttpmock.REST(\"GET\", \"repos/o/r\"),\n"
        "\t\tfunc(req *http.Request) (*http.Response, error) {\n"
        "\t\t\treturn nil, nil\n"
        "\t\t},\n"
        "\t)\n"
        "\treturn reg.Client()\n"
        "}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "rt.go:5-12")
    names = [d["name"] for d in got]
    assert "func" not in names, names
    # Not vacuous: the enclosing named function is still found, so the guard
    # is rejecting the literal rather than the whole file.
    assert names and names[0] == "NewClient" and got[0]["body_continues"] is True


def test_a_python_function_actually_named_func_is_still_reported(tmp_path) -> None:
    """The reason ``func`` cannot simply join ``_RESERVED_NAMES``.

    That set is consulted for every pattern, and ``def func(...)`` is a real
    definition — 41 of them in django alone.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "fn.py").write_text(
        "import functools\n"
        "\n"
        "\n"
        "def func(a, b):\n"
        "    total = a + b\n"
        "    return total\n",
        encoding="utf-8",
    )
    names = [d["name"] for d in withheld_definitions(tmp_path, "fn.py:5-6")]
    assert "func" in names, names


def test_a_c_style_function_actually_named_func_is_still_reported(tmp_path) -> None:
    """The reason the guard requires ``func`` to OPEN the line.

    ``int func(int a) {`` is a real definition named ``func`` in C, C++, Java,
    C# and Kotlin, and it reaches the same brace-member pattern the Go literal
    does. A name-only test suppresses all five languages.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "u.c").write_text(
        "#include <stdio.h>\n"
        "\n"
        "int func(int a) {\n"
        "    int total = a + 1;\n"
        "    return total;\n"
        "}\n",
        encoding="utf-8",
    )
    names = [d["name"] for d in withheld_definitions(tmp_path, "u.c:4-6")]
    assert "func" in names, names


def test_java_members_are_found(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "e.java").write_text(
        "public class Runner {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(1);\n"
        "    }\n"
        "    private int helper(int a) {\n"
        "        return a;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    names = [d["name"] for d in withheld_definitions(tmp_path, "e.java:3-8")]
    assert "helper" in names, names


def test_a_callback_argument_is_not_a_definition(tmp_path) -> None:
    """`describe("x", () => {` matches the brace-member shape by accident."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "f.test.ts").write_text(
        "describe('outer', () => {\n"
        "  describe('inner', () => {\n"
        "    it('works', () => {\n"
        "      expect(appended).toMatchObject({ kind: 'trail' });\n"
        "      const rows = all.filter((p) => p.ok);\n"
        "    });\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )
    names = [d["name"] for d in withheld_definitions(tmp_path, "f.test.ts:2-8")]
    assert names == [], names


def test_a_multiline_signature_still_reaches_the_cut(tmp_path) -> None:
    """The live miss: the closing paren of a multi-line def sits at column 0.

    Found on repowise's own `get_answer`, not in a fixture. Its signature ends
    `) -> dict:` at column 0, so a running-minimum-indent walk hit zero on the
    signature's own tail and gave up two lines short of the `async def` -- the
    payload went out with `truncated: true` and no `withheld_symbols` at all.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "h.py").write_text(
        "async def wide(\n"
        "    a: str,\n"
        "    b: int = 1,\n"
        ") -> dict:\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return {}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "h.py:6-7")
    assert [d["name"] for d in got] == ["wide"], got
    assert got[0]["body_continues"] is True


def test_definitions_inside_a_docstring_are_not_reported(tmp_path) -> None:
    """FAILS on 767391dd: it matched raw text, so an example became a symbol_id.

    The id it produced (`f.py::fake_one`) resolves to nothing, and repowise's
    own docstrings are full of exactly this shape.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "f.py").write_text(
        "def real(x):\n"
        '    """Example.\n'
        "\n"
        "    def fake_one():\n"
        "        pass\n"
        "\n"
        "    class FakeTwo:\n"
        "        pass\n"
        '    """\n'
        "    return x\n"
        "\n"
        "def also_real():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    names = [d["name"] for d in withheld_definitions(tmp_path, "f.py:3-13")]
    assert "fake_one" not in names and "FakeTwo" not in names, names
    assert "also_real" in names, names


def test_a_one_line_body_does_not_bleed_into_the_signature(tmp_path) -> None:
    """FAILS on 767391dd: `def go(self): pass` never ends with ':'."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "g.py").write_text(
        "class K:\n    def go(self): pass\n    def after(self): pass\n",
        encoding="utf-8",
    )
    sigs = [d["signature"] for d in withheld_definitions(tmp_path, "g.py:2-3")]
    assert sigs == ["class K:", "def go(self): pass", "def after(self): pass"], sigs


# ---------------------------------------------------------------------------
# Cache and rationale
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scanner PRECISION. Every case below was found by an adversarial sweep over
# real source, not by writing a fixture: an entry that is not a definition hands
# the agent a `symbol_id` that resolves to nothing, and if its name happens to
# be code-shaped it also demotes a correct answer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "    function_name: Mapped[str] = mapped_column(String)",  # 507 of these
        "  functionCall(x)",
        "export functionality = 1",
        "    functions are called in sequence",  # docstring prose
    ],
)
def test_the_function_keyword_is_not_matched_as_an_identifier_prefix(line) -> None:
    """`function` needs a separator, or it splits any longer identifier."""
    from repowise.server.mcp_server.tool_answer.symbols import _match_definition

    m = _match_definition(line)
    assert m is None, f"{line!r} -> {m.group('name') if m else None}"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("export function useThing(a) {", "useThing"),
        ("export function* gen() {", "gen"),
        ("export default async function run() {", "run"),
    ],
)
def test_real_function_declarations_still_match(line, expected) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _match_definition

    m = _match_definition(line)
    assert m is not None and m.group("name") == expected


@pytest.mark.parametrize(
    "line",
    [
        '        raise ValueError(f"repo (no .git found): {resolved}")',
        "    return dict(a=(1), b={})",
        '  it("works", function () {',
        '  test("x", async function () {',
    ],
)
def test_a_statement_is_not_a_member_declaration(line) -> None:
    """The optional type group swallows the keyword, hiding it from a name guard.

    `raise ValueError(...)` reported a symbol `ValueError`, which is distinctive
    enough that a question saying "why does this raise a ValueError?" then
    demoted a correct answer.
    """
    from repowise.server.mcp_server.tool_answer.symbols import _match_definition

    m = _match_definition(line)
    assert m is None, f"{line!r} -> {m.group('name') if m else None}"


@pytest.mark.parametrize(
    "line,nxt",
    [
        # Rust's `fn <name>` shape, but this is Python comparing a variable.
        ("            fn is not None", "            and fn.text is not None"),
        # An argument on its own line whose successor happens to be a dict
        # literal, which looks exactly like an Allman brace.
        ("                    bool(matched_nums),", "                    {"),
        ("                    text(sql),", "                    {"),
    ],
)
def test_a_parse_accident_is_not_a_definition(line, nxt) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import _match_definition

    m = _match_definition(line, nxt)
    assert m is None, f"{line!r} -> {m.group('name') if m else None}"


def test_a_cut_landing_on_a_multiline_signature_still_finds_the_symbol(
    tmp_path,
) -> None:
    """The anchor has to obey the same exclusions as the walk.

    Found on the real `get_risk` MCP entry point. When the cut lands ON the
    `) -> dict:` line, an anchor that reads the first non-blank withheld line
    flatly sees column 0, the walk dies immediately, and the payload ships
    `truncated: true` with NO withheld_symbols -- gate 8 inert on exactly the
    long multi-line-signature entry points that truncate in the first place.
    """
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "r.py").write_text(
        "async def wide(\n"
        "    a: str,\n"
        ") -> dict:\n"
        "    x = 1\n"
        "    return {}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "r.py:3-5")  # cut ON the ') -> dict:' line
    assert [d["name"] for d in got] == ["wide"], got
    assert got[0]["body_continues"] is True


def test_a_cut_inside_a_flush_left_string_still_finds_the_symbol(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "s.py").write_text(
        'def f():\n    sql = """\nSELECT 1\n"""\n', encoding="utf-8"
    )
    got = withheld_definitions(tmp_path, "s.py:3-4")
    assert [d["name"] for d in got] == ["f"], got


def test_allman_braces_are_found(tmp_path) -> None:
    """The C# default, and it used to yield nothing in both directions."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "k.cs").write_text(
        "class K\n"
        "{\n"
        "    void Alpha()\n"
        "    {\n"
        "        X();\n"
        "    }\n"
        "    void Beta()\n"
        "    {\n"
        "        Y();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "k.cs:5-11")
    assert got[0]["name"] == "Alpha" and got[0]["body_continues"] is True
    assert "Beta" in [d["name"] for d in got], got


def test_a_top_level_c_function_is_found(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "m.c").write_text(
        "#include <stdio.h>\n"
        'int main(int argc, char **argv) {\n'
        '    puts("x");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    got = withheld_definitions(tmp_path, "m.c:3-5")
    assert [d["name"] for d in got] == ["main"], got


def test_repeated_names_do_not_eat_the_cap(tmp_path) -> None:
    """A minified line reported the same name once per line and burned the cap."""
    from repowise.server.mcp_server.tool_answer.symbols import withheld_definitions

    (tmp_path / "z.js").write_text(
        "function a(){}\nfunction a(){}\nfunction a(){}\n", encoding="utf-8"
    )
    assert [d["name"] for d in withheld_definitions(tmp_path, "z.js:1-3")] == ["a"]


@pytest.mark.parametrize(
    "name,question,expected",
    [
        # A sentence-initial capital before a determiner is an imperative verb.
        ("Run", "Run the indexer, then what?", False),
        ("Update", "Update the schema how?", False),
        ("Store", "Store the results where?", False),
        # ...but a sentence-initial capital is still the symbol otherwise.
        ("Store", "Store keeps what?", True),
        ("Store", "In Store, why is the id blank?", True),
    ],
)
def test_sentence_initial_capital_is_only_excluded_as_an_imperative(
    name, question, expected
) -> None:
    from repowise.server.mcp_server.tool_answer.confidence import _question_names

    assert _question_names(question, name) is expected


def test_the_string_mask_is_cached(tmp_path) -> None:
    """The union path masks the same file once per truncated body."""
    from repowise.server.mcp_server.tool_answer.symbols import _string_masked_lines

    lines = tuple(f"line {i}" for i in range(200))
    _string_masked_lines.cache_clear()
    _string_masked_lines(lines)
    _string_masked_lines(lines)
    assert _string_masked_lines.cache_info().hits >= 1


def test_answer_schema_version_was_bumped_for_this_change() -> None:
    """FAILS on 767391dd, and a stale row serves the old `high` for 14 days.

    Cache replay returns before symbol_bodies is built, so without a bump every
    previously-cached answer keeps both the old confidence and the old "do not
    re-read the source" note -- the two things this change exists to alter.
    """
    from repowise.server.mcp_server.tool_answer.config import _ANSWER_SCHEMA_VERSION

    assert _ANSWER_SCHEMA_VERSION >= 13


@pytest.mark.asyncio
async def test_high_note_no_longer_cites_the_answers_own_directness(
    setup_mcp, monkeypatch, tmp_path
):
    """FAILS on bd577942, and the clause it removes had no coverage at all.

    _SYSTEM_PROMPT instructs the model not to hedge, so citing "the answer is
    direct (no hedging)" as evidence FOR confidence reads the pipeline's own
    mandate back as a signal.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _setup_repo(monkeypatch, tmp_path)
    _patch_pipeline(monkeypatch, answer_mod, symbol=_WHOLE_SYMBOL)
    _patch_provider(monkeypatch, answer_mod, "Store keeps todos in a dict.")

    result = await get_answer("what does Store keep?")
    assert result["confidence"] == "high", (
        f"this test only covers the high note; got {result['confidence']!r}"
    )
    note = (result.get("note") or "").lower()
    assert "hedging" not in note and "is direct" not in note, note
