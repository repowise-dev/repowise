"""``assertion_free_test`` resolving a test's oracle across a file boundary.

Driven through a real parse, a real graph and a real walk rather than a stubbed
index: the thing under test is whether an edge exists between two files, and a
hand-built index would assert only that a dict lookup works.

The load-bearing cases are the negative ones. This rule *removes* findings, so
a suppression that should not have happened is the failure mode, and the tests
that matter are the ones pinning what still fires: a same-named helper on
another class that does not assert, a bare ``assert`` in production code, and a
call the graph could not resolve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.asserts.oracle_reach import (
    CLOSURE_FORWARD_DEPTH,
    DEFAULT_MAX_DEPTH,
    collect_cross_file_oracles,
)
from repowise.core.analysis.health.biomarkers.assertion_free_test import BIOMARKER
from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _require(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


class _FI:
    def __init__(self, path: str, language: str) -> None:
        self.path = path
        self.language = language


class _PF:
    def __init__(self, path: str, language: str) -> None:
        self.file_info = _FI(path, language)


def _build(tmp_path: Path, files: dict[str, str]):
    """Write *files*, then parse, graph and walk them. Returns (walked, graph)."""
    for rel, body in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")

    parser = ASTParser()
    gb = GraphBuilder()
    parsed = []
    for fi in FileTraverser(tmp_path).traverse():
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        gb.add_file(pf)
        parsed.append(pf)
    gb.build()

    walked = []
    for pf in parsed:
        rel = pf.file_info.path
        source = (tmp_path / rel).read_bytes()
        walked.append((_PF(rel, pf.file_info.language), walk_file(rel, pf.file_info.language, source)))
    return walked, gb.graph()


def _flagged(walked, graph, path: str, *, max_depth: int = DEFAULT_MAX_DEPTH) -> list[str]:
    """Names ``assertion_free_test`` reports for *path*, resolver applied."""
    resolved = (
        collect_cross_file_oracles(walked, graph, max_depth=max_depth) if graph is not None else {}
    )
    for pf, fcx in walked:
        if pf.file_info.path != path:
            continue
        ctx = FileContext(
            file_path=path,
            language=pf.file_info.language,
            nloc=fcx.file_nloc,
            has_test_file=True,
            module=None,
            all_functions=tuple(fcx.functions),
            cross_file_oracle_lines=frozenset(resolved.get(path, {})),
        )
        return sorted(r.function_name for r in BIOMARKER.detect(ctx))
    raise AssertionError(f"{path} was not walked")


# --------------------------------------------------------------------------
# The shape this exists for
# --------------------------------------------------------------------------


def test_python_test_delegating_to_a_helper_in_another_file_is_not_assertion_free(
    tmp_path: Path,
) -> None:
    _require("python")
    walked, graph = _build(
        tmp_path,
        {
            "tests/helpers.py": (
                "def check_ok(result):\n"
                "    assert result == 'ok'\n"
            ),
            "tests/test_thing.py": (
                "from tests.helpers import check_ok\n"
                "\n"
                "def test_it_works():\n"
                "    check_ok(run())\n"
                "\n"
                "def test_it_checks_nothing():\n"
                "    run()\n"
            ),
        },
    )
    assert _flagged(walked, graph, "tests/test_thing.py") == ["test_it_checks_nothing"]


def test_typescript_test_delegating_to_an_imported_helper_is_not_assertion_free(
    tmp_path: Path,
) -> None:
    _require("typescript")
    walked, graph = _build(
        tmp_path,
        {
            "tests/helpers.ts": (
                "export function checkOk(v: string) {\n"
                "  expect(v).toBe('ok');\n"
                "}\n"
            ),
            "tests/thing.test.ts": (
                "import { checkOk } from './helpers';\n"
                "\n"
                "it('delegates', () => {\n"
                "  checkOk(run());\n"
                "});\n"
                "\n"
                "it('checks nothing', () => {\n"
                "  run();\n"
                "});\n"
            ),
        },
    )
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    # The delegating callback starts on line 3, the silent one on line 7.
    assert 3 in lines, f"the delegating test resolved nothing: {lines}"
    assert 7 not in lines, f"the silent test must not resolve: {lines}"
    assert lines[3].basis == "file-edge"
    # And the marker must act on it.
    assert _flagged(walked, graph, "tests/thing.test.ts") == ["it callback"]


def test_a_receiver_qualified_call_does_not_borrow_a_sibling_test_s_edge(
    tmp_path: Path,
) -> None:
    """The file-edge lane's name conjunct is about THIS call, not the file's.

    One test delegating to an imported helper mints a file-level edge to it.
    A second test calling a same-named method on something else must not be
    suppressed by that edge: the call graph never bound it to anything, and
    letting it through would be one test licensing its siblings, which is the
    over-suppression the two conjuncts exist to prevent.
    """
    _require("typescript")
    walked, graph = _build(
        tmp_path,
        {
            "tests/helpers.ts": (
                "export function checkOk(v: string) {\n"
                "  expect(v).toBe('ok');\n"
                "}\n"
            ),
            "tests/thing.test.ts": (
                "import { checkOk } from './helpers';\n"
                "\n"
                "it('delegates', () => {\n"
                "  checkOk(run());\n"
                "});\n"
                "\n"
                "it('checks nothing', () => {\n"
                "  harness.checkOk();\n"
                "});\n"
            ),
        },
    )
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    assert 3 in lines, f"the unqualified delegation must still resolve: {lines}"
    assert 7 not in lines, f"a qualified call must not borrow the file's edge: {lines}"


_WRAPPED_SUITE = {
    "tests/helpers.ts": ("export function checkOk(v: string) {\n  expect(v).toBe('ok');\n}\n"),
    # ``const suite = describe(...)`` is a named declaration, so the graph holds
    # one symbol spanning every callback inside it. Both ``it`` bodies sit in
    # its line range and the import edge is attributed to it, not to either of
    # them.
    "tests/thing.test.ts": (
        "import { checkOk } from './helpers';\n"
        "export const suite = describe('outer', () => {\n"
        "  it('delegates', () => {\n"
        "    checkOk(run());\n"
        "  });\n"
        "  it('checks nothing', () => {\n"
        "    run();\n"
        "  });\n"
        "});\n"
    ),
}


def test_a_symbol_wrapped_around_the_suite_does_not_answer_for_a_test_inside_it(
    tmp_path: Path,
) -> None:
    """A containing symbol is not the test, and must not speak for it.

    ``resolve_function`` falls back to the innermost symbol whose range
    contains the start line, which exists to tolerate a decorator offset. A
    wrapper around the whole suite contains every callback in it, so reading
    that fallback as identity would let the wrapper's one edge to an asserting
    helper suppress every test beside the one that made it -- the same
    one-test-licenses-its-siblings failure the file lane's two conjuncts
    prevent, arriving through the other lane.
    """
    _require("typescript")
    walked, graph = _build(tmp_path, _WRAPPED_SUITE)
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    assert 6 not in lines, f"the silent callback borrowed the wrapper's edge: {lines}"
    assert _flagged(walked, graph, "tests/thing.test.ts") == ["it callback"]


def test_a_delegating_test_inside_such_a_wrapper_still_resolves(tmp_path: Path) -> None:
    """The mirror: rejecting the wrapper must not cost the real delegation.

    Declining the containing symbol has to fall through to the file-edge lane
    rather than answer ``None``. Without that, tightening the call-edge lane
    would trade a false suppression for a false finding on the very test the
    feature exists for.
    """
    _require("typescript")
    walked, graph = _build(tmp_path, _WRAPPED_SUITE)
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    assert 3 in lines, f"the delegating callback resolved nothing: {lines}"
    assert lines[3].basis == "file-edge"
    assert lines[3].oracle == "tests/helpers.ts::checkOk"


def test_a_wait_helper_that_gives_up_by_throwing_is_an_oracle(tmp_path: Path) -> None:
    """A helper that fails its caller by throwing checks as much as one that
    asserts. ``waitFor(events, p)`` that throws when the predicate never
    matches is the common shape, and no assertion vocabulary names ``throw``,
    so without counting it the helper reads as checking nothing and every test
    delegating to it is reported."""
    _require("typescript")
    walked, graph = _build(
        tmp_path,
        {
            "tests/wait.ts": (
                "export function waitForEvent(events: string[], want: string) {\n"
                "  if (!events.includes(want)) {\n"
                "    throw new Error(`never saw ${want}`);\n"
                "  }\n"
                "}\n"
            ),
            "tests/thing.test.ts": (
                "import { waitForEvent } from './wait';\n"
                "\n"
                "it('waits', () => {\n"
                "  waitForEvent(collect(), 'done');\n"
                "});\n"
                "\n"
                "it('checks nothing', () => {\n"
                "  run();\n"
                "});\n"
            ),
        },
    )
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    assert 3 in lines, f"the throwing helper was not read as an oracle: {lines}"
    assert lines[3].oracle == "tests/wait.ts::waitForEvent"
    assert 7 not in lines
    assert _flagged(walked, graph, "tests/thing.test.ts") == ["it callback"]


def test_a_wrapper_sharing_a_start_line_with_its_first_callback_is_still_declined(
    tmp_path: Path,
) -> None:
    """The bound has to cover the exact lookup, not only the fallback.

    ``resolve_function`` answers an exact start-line match before it reaches
    the containment scan. Put the wrapper and its first callback on one
    physical line and the wrapper wins that match, so without the same bound
    on it the fix would be defeated by formatting alone.
    """
    _require("typescript")
    walked, graph = _build(
        tmp_path,
        {
            "tests/helpers.ts": (
                "export function checkOk(v: string) {\n  expect(v).toBe('ok');\n}\n"
            ),
            "tests/thing.test.ts": (
                "import { checkOk } from './helpers';\n"
                "export const suite = describe('outer', () => "
                "{ it('delegates', () => { checkOk(run()); });\n"
                "  it('checks nothing', () => {\n"
                "    run();\n"
                "  });\n"
                "});\n"
            ),
        },
    )
    resolved = collect_cross_file_oracles(walked, graph)
    lines = resolved.get("tests/thing.test.ts", {})
    assert 3 not in lines, f"the silent callback borrowed the wrapper's edge: {lines}"
    assert 2 in lines, f"the delegating callback resolved nothing: {lines}"
    assert lines[2].basis == "file-edge"


# --------------------------------------------------------------------------
# The negative cases: what must still fire
# --------------------------------------------------------------------------


def test_a_same_named_helper_on_another_class_that_does_not_assert_suppresses_nothing(
    tmp_path: Path,
) -> None:
    """The receiver hazard, across files.

    Two classes in two files each expose ``check``. Only one asserts. The
    name-keyed same-file rule would let either suppress; resolving on an edge
    must suppress only the test that actually reaches the asserting one.
    """
    _require("python")
    walked, graph = _build(
        tmp_path,
        {
            "tests/asserting.py": (
                "class Asserting:\n"
                "    def check(self, v):\n"
                "        assert v\n"
            ),
            "tests/silent.py": (
                "class Silent:\n"
                "    def check(self, v):\n"
                "        return v\n"
            ),
            "tests/test_pair.py": (
                "from tests.asserting import Asserting\n"
                "from tests.silent import Silent\n"
                "\n"
                "def test_reaches_the_asserting_one():\n"
                "    Asserting().check(1)\n"
                "\n"
                "def test_reaches_the_silent_one():\n"
                "    Silent().check(1)\n"
            ),
        },
    )
    flagged = _flagged(walked, graph, "tests/test_pair.py")
    # Both halves. Asserting only that the silent one survives would pass just
    # as well if nothing resolved at all, which is the state this test exists
    # to tell apart from working discrimination.
    assert "test_reaches_the_silent_one" in flagged
    assert "test_reaches_the_asserting_one" not in flagged


def test_a_bare_assert_in_production_code_is_not_an_oracle(tmp_path: Path) -> None:
    """``assertion_count`` counts a production ``assert``; it must not suppress.

    Without the test-related gate on the sink set, every test calling a
    defensively-asserting production function would be silently suppressed.
    """
    _require("python")
    walked, graph = _build(
        tmp_path,
        {
            "app/service.py": (
                "def load(path):\n"
                "    assert path, 'path required'\n"
                "    return path\n"
            ),
            "tests/test_service.py": (
                "from app.service import load\n"
                "\n"
                "def test_load_runs():\n"
                "    load('x')\n"
            ),
        },
    )
    assert _flagged(walked, graph, "tests/test_service.py") == ["test_load_runs"]


def test_a_call_the_graph_cannot_resolve_suppresses_nothing(tmp_path: Path) -> None:
    """An unresolved call is indistinguishable from a call that checks nothing."""
    _require("python")
    walked, graph = _build(
        tmp_path,
        {
            "tests/test_external.py": (
                "import some_third_party\n"
                "\n"
                "def test_calls_out():\n"
                "    some_third_party.verify_everything()\n"
            ),
        },
    )
    assert _flagged(walked, graph, "tests/test_external.py") == ["test_calls_out"]


def test_without_a_graph_the_marker_is_unchanged(tmp_path: Path) -> None:
    _require("python")
    walked, _graph = _build(
        tmp_path,
        {
            "tests/helpers.py": "def check_ok(v):\n    assert v\n",
            "tests/test_thing.py": (
                "from tests.helpers import check_ok\n"
                "\n"
                "def test_it():\n"
                "    check_ok(1)\n"
            ),
        },
    )
    assert collect_cross_file_oracles(walked, None) == {}
    assert _flagged(walked, None, "tests/test_thing.py") == ["test_it"]


# --------------------------------------------------------------------------
# Depth
# --------------------------------------------------------------------------


def test_a_hop_beyond_max_depth_suppresses_nothing(tmp_path: Path) -> None:
    """Depth bounds the walk, and the bound is the thing being pinned."""
    _require("python")
    walked, graph = _build(
        tmp_path,
        {
            "tests/deep.py": "def deep(v):\n    assert v\n",
            "tests/mid.py": (
                "from tests.deep import deep\n"
                "\n"
                "def mid(v):\n"
                "    deep(v)\n"
            ),
            "tests/test_chain.py": (
                "from tests.mid import mid\n"
                "\n"
                "def test_two_hops():\n"
                "    mid(1)\n"
            ),
        },
    )
    assert _flagged(walked, graph, "tests/test_chain.py", max_depth=1) == ["test_two_hops"]
    assert _flagged(walked, graph, "tests/test_chain.py", max_depth=2) == []


def test_the_resolver_never_walks_past_the_incremental_closure(tmp_path: Path) -> None:
    """The one coupling that keeps ``update`` agreeing with ``init``.

    An incremental run walks the changed files plus
    ``ExecutionGraphIndex.affected_files``' forward closure. Resolving deeper
    than that closure walks would let an incremental run fail to see an oracle
    a full index sees, which is the verdict flip #1484 exists to prevent.
    """
    assert DEFAULT_MAX_DEPTH <= CLOSURE_FORWARD_DEPTH

    from repowise.core.analysis.execution_graph import ExecutionGraphIndex

    assert (
        ExecutionGraphIndex.affected_files.__defaults__ is None
    ), "affected_files takes its depths keyword-only; re-read them below"
    assert ExecutionGraphIndex.affected_files.__kwdefaults__["forward_depth"] == (
        CLOSURE_FORWARD_DEPTH
    )
