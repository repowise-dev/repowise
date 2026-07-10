"""Unit tests for the dataflow CFG core.

Best-effort like the complexity-walker tests: tree-sitter language packs may
not be installed in every CI lane, so each test skips when the Python pack is
missing rather than failing.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.ast_utils import _collect_function_nodes
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    BasicBlock,
    build_cfg,
    build_cfgs_for_file,
    is_flagged,
)
from repowise.core.analysis.health.dataflow.cfg import CFGGuardTrippedError


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _first_fn(src: str):
    """Parse *src* and return ``(function_node, lmap)`` for the first function."""
    _require_python()
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    lmap = get_language_map("python")
    parser = Parser(_get_language("python"))
    tree = parser.parse(textwrap.dedent(src).encode())
    nodes = _collect_function_nodes(tree.root_node, lmap)
    assert nodes, "no function node parsed"
    return nodes[0], lmap


def _cfg(src: str):
    fn_node, lmap = _first_fn(src)
    return build_cfg(fn_node, lmap)


def _by_kind(cfg, kind: str) -> list[BasicBlock]:
    return [b for b in cfg.blocks if b.kind == kind]


# -- straight-line --------------------------------------------------------------


def test_straight_line_single_path():
    cfg = _cfg(
        """
        def f():
            a = 1
            b = 2
            c = a + b
            print(c)
        """
    )
    # entry -> body -> exit, no branches.
    assert _by_kind(cfg, "branch") == []
    assert _by_kind(cfg, "loop_header") == []
    # Every block is reachable from entry, and exit is among them.
    reachable = cfg.reachable_ids()
    assert cfg.exit_id in reachable
    # The single body block carries all four statements.
    body = [b for b in cfg.blocks if b.statements]
    assert len(body) == 1
    assert len(body[0].statements) == 4
    # entry has exactly one successor; exit has no successors.
    assert len(cfg.entry.successors) == 1
    assert cfg.exit.successors == []


# -- if / else join -------------------------------------------------------------


def test_if_else_join():
    cfg = _cfg(
        """
        def f(x):
            if x > 0:
                y = 1
            else:
                y = 2
            return y
        """
    )
    branches = _by_kind(cfg, "branch")
    assert len(branches) == 1
    branch = branches[0]
    # The branch test has two successors (then / else).
    assert len(branch.successors) == 2
    # Both arms converge on a single join block.
    joins = _by_kind(cfg, "join")
    assert len(joins) == 1
    join = joins[0]
    assert len(join.predecessors) == 2
    assert set(join.predecessors) == {s for s in cfg.block(join.id).predecessors}
    # The join flows to the return -> exit.
    assert cfg.exit_id in cfg.reachable_ids()


def test_if_without_else_falls_through():
    cfg = _cfg(
        """
        def f(x):
            if x:
                y = 1
            return y
        """
    )
    branch = _by_kind(cfg, "branch")[0]
    join = _by_kind(cfg, "join")[0]
    # No else: the branch's false edge goes straight to the join, and the then
    # block also reaches it -> two predecessors.
    assert join.id in branch.successors
    assert len(join.predecessors) == 2


def test_elif_chain_nested_branches():
    cfg = _cfg(
        """
        def f(x):
            if x == 1:
                y = 1
            elif x == 2:
                y = 2
            elif x == 3:
                y = 3
            else:
                y = 4
            return y
        """
    )
    # One head test + two elif tests = three branch blocks.
    assert len(_by_kind(cfg, "branch")) == 3
    # A single join collects all four arms.
    joins = _by_kind(cfg, "join")
    assert len(joins) == 1
    assert len(joins[0].predecessors) == 4


# -- while back-edge ------------------------------------------------------------


def test_while_back_edge():
    cfg = _cfg(
        """
        def f(n):
            i = 0
            while i < n:
                i += 1
            return i
        """
    )
    headers = _by_kind(cfg, "loop_header")
    assert len(headers) == 1
    header = headers[0]
    # Header has two successors: into the body and past the loop.
    assert len(header.successors) == 2
    # A back-edge: some block inside the body is a predecessor of the header
    # with a higher id than the header (the loop tail re-enters).
    assert any(p > header.id for p in header.predecessors)
    assert cfg.back_edges()  # non-empty
    # The loop_exit block exists and is reachable.
    assert _by_kind(cfg, "loop_exit")


def test_for_loop_shape():
    cfg = _cfg(
        """
        def f(items):
            total = 0
            for it in items:
                total += it
            return total
        """
    )
    assert len(_by_kind(cfg, "loop_header")) == 1
    assert cfg.back_edges()


def test_break_and_continue_targets():
    cfg = _cfg(
        """
        def f(items):
            for it in items:
                if it < 0:
                    continue
                if it > 100:
                    break
                process(it)
            return 0
        """
    )
    header = _by_kind(cfg, "loop_header")[0]
    after = _by_kind(cfg, "loop_exit")[0]
    # ``continue`` adds a back-edge to the header; ``break`` adds an edge to the
    # loop-exit block from inside the body.
    assert any(p > header.id for p in header.predecessors)
    assert any(p > header.id for p in after.predecessors)


# -- try / except ---------------------------------------------------------------


def test_try_except_handler_reachable():
    cfg = _cfg(
        """
        def f():
            try:
                risky()
            except ValueError:
                recover()
            return 1
        """
    )
    handlers = _by_kind(cfg, "handler")
    assert len(handlers) == 1
    handler = handlers[0]
    # The handler is reachable from entry (an exception may escape the body).
    assert handler.id in cfg.reachable_ids()
    # Both the normal body and the handler converge before exit.
    assert cfg.exit_id in cfg.reachable_ids()


def test_try_except_finally_convergence():
    cfg = _cfg(
        """
        def f():
            try:
                risky()
            except KeyError as e:
                handle(e)
            except ValueError:
                handle2()
            else:
                ok()
            finally:
                cleanup()
            return 1
        """
    )
    # Two handlers, both reachable.
    assert len(_by_kind(cfg, "handler")) == 2
    # The finally body runs on every path: its block has the normal body's
    # else-out and both handlers as predecessors converging into it.
    assert cfg.exit_id in cfg.reachable_ids()


# -- early return ---------------------------------------------------------------


def test_early_return_edges_to_exit():
    cfg = _cfg(
        """
        def f(x):
            if x:
                return 1
            y = compute()
            return y
        """
    )
    # The then-branch returns: it has an edge to exit and the join is reached
    # only from the branch's false edge.
    exit_preds = cfg.exit.predecessors
    # At least two paths reach exit: the early return and the final return.
    assert len(exit_preds) >= 2
    # The code after the if is still reachable (false edge).
    assert cfg.exit_id in cfg.reachable_ids()


def test_unreachable_after_return():
    cfg = _cfg(
        """
        def f():
            return 1
            dead = 2
        """
    )
    unreachable = _by_kind(cfg, "unreachable")
    assert len(unreachable) == 1
    # The dead block has no predecessors and is not reachable from entry.
    assert unreachable[0].predecessors == []
    assert unreachable[0].id not in cfg.reachable_ids()


# -- comments are not statements --------------------------------------------------
# Tree-sitter emits a trailing comment as a named sibling *after* its statement,
# so a comment following a terminator used to spawn a bogus ``unreachable`` block
# at the terminator's own line. These mirror the shapes found in the wild.


def _assert_all_reachable(cfg) -> None:
    reachable = cfg.reachable_ids()
    assert [b.id for b in cfg.blocks if b.id not in reachable] == []
    assert _by_kind(cfg, "unreachable") == []


def test_guarded_bare_return_with_trailing_comment():
    cfg = _cfg(
        """
        def f(token):
            if not token:
                return  # no token configured, skip verification
            check(token)
        """
    )
    _assert_all_reachable(cfg)


def test_trailing_return_after_if_chain_with_comment():
    cfg = _cfg(
        """
        def f(kind, is_test):
            if not kind:
                return True
            if kind == "test":
                return is_test
            return False  # config / doc: symbols do not qualify
        """
    )
    _assert_all_reachable(cfg)


def test_post_guard_body_stays_reachable():
    cfg = _cfg(
        """
        def f(ids, titles):
            if not ids:
                return None  # no siblings to compare
            shared = titles & ids
            return len(shared) / len(ids)
        """
    )
    _assert_all_reachable(cfg)


def test_if_guarded_return_with_comment_mid_function():
    cfg = _cfg(
        """
        def f(include, targets):
            if not targets:
                return None
            if include and "source" in include:
                return None  # source mode provides its own truncation info
            return build(targets)
        """
    )
    _assert_all_reachable(cfg)


def test_standalone_comment_after_return_is_not_a_statement():
    cfg = _cfg(
        """
        def f(x):
            if x:
                return 1
                # explains the early exit
            return 2
        """
    )
    _assert_all_reachable(cfg)


def test_ts_mid_return_chain_with_trailing_comment():
    src = textwrap.dedent(
        """
        function f(scope, dead, hot) {
            if (dead && hot) return "unified";
            if (dead) return "dead";
            if (hot) return "hotfiles";
            return scope; // "architecture" | "full"
        }
        """
    )
    result = build_cfgs_for_file("m.ts", "typescript", src.encode(), flagged_only=False)
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for typescript")
    _assert_all_reachable(result.functions[0].cfg)


def test_finally_reachable_when_body_and_handlers_return():
    # The finally body runs on every path out of the protected region, even
    # when the body and all handlers terminate; it must never be flagged.
    cfg = _cfg(
        """
        def f(store):
            try:
                write(store)
                return True
            except Exception:
                return False
            finally:
                store.close()
        """
    )
    _assert_all_reachable(cfg)


def test_finally_reachable_after_always_returning_try_without_except():
    cfg = _cfg(
        """
        def f(store):
            try:
                return read(store)
            finally:
                store.close()
        """
    )
    _assert_all_reachable(cfg)


def test_true_unreachable_after_comment_still_flagged():
    # A comment between the terminator and real dead code must not hide the
    # dead code: the statement itself stays flagged.
    cfg = _cfg(
        """
        def f():
            return 1
            # a note about the exit
            dead = 2
        """
    )
    unreachable = _by_kind(cfg, "unreachable")
    assert len(unreachable) == 1
    assert [s.kind for s in unreachable[0].statements] == ["expression_statement"]
    assert unreachable[0].statements[0].start_line == 5


# -- determinism ----------------------------------------------------------------


def test_determinism_identical_structure():
    src = """
        def f(x, items):
            total = 0
            for it in items:
                if it > x:
                    total += it
                else:
                    total -= it
            try:
                check(total)
            except ValueError:
                total = 0
            return total
        """

    def serialize(cfg):
        return [
            (b.id, b.kind, [(s.kind, s.start_line) for s in b.statements], list(b.successors))
            for b in cfg.blocks
        ]

    first = serialize(_cfg(src))
    for _ in range(3):
        assert serialize(_cfg(src)) == first


# -- gate (the budget contract) -------------------------------------------------


def test_is_flagged_thresholds():
    # complex_method: ccn >= 9 alone flags.
    assert is_flagged(ccn=9, nloc=10)
    assert not is_flagged(ccn=8, nloc=10)
    # large_method: nloc >= 60 with real branching (ccn >= 3). CCN 2 no longer
    # qualifies — that's the score a flat match/case dispatch table gets from its
    # lone keyword point, a layout artefact rather than a complexity smell.
    assert is_flagged(ccn=3, nloc=60)
    assert not is_flagged(ccn=2, nloc=60)  # flat-match dispatch, not substance
    assert not is_flagged(ccn=1, nloc=200)  # flat body, no branching
    assert not is_flagged(ccn=8, nloc=59)


def test_flagged_only_gate_skips_small_functions():
    # One large/complex function and one trivial function in the same file.
    lines = ["def big(x):", "    y = 0"]
    for i in range(12):
        lines += [f"    if x == {i}:", f"        y = {i}"]
    lines += ["    return y", "", "def tiny():", "    return 1", ""]
    src = "\n".join(lines)
    result = build_cfgs_for_file("mem.py", "python", src.encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    # Both functions were discovered, but only the flagged one got a CFG.
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]
    assert result.functions[0].cfg.blocks


def test_flagged_only_false_builds_all():
    src = textwrap.dedent(
        """
        def tiny():
            return 1
        """
    )
    result = build_cfgs_for_file("mem.py", "python", src.encode(), flagged_only=False)
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    assert result.stats.functions_built == 1


# -- graceful degradation -------------------------------------------------------


def test_unsupported_language_is_silent():
    result = build_cfgs_for_file("x.unknown", "cobol", b"whatever")
    assert result.functions == []
    assert result.stats.functions_seen == 0


def test_garbage_source_does_not_raise():
    # Not valid Python; tree-sitter still produces a (broken) tree, so this must
    # not raise -- at worst it yields no flagged functions.
    result = build_cfgs_for_file("x.py", "python", b">>>!!! not python @@@")
    assert isinstance(result.functions, list)


def test_size_guard_trips_on_low_cap():
    fn_node, lmap = _first_fn(
        """
        def f(x):
            if x:
                a = 1
            else:
                a = 2
            return a
        """
    )
    with pytest.raises(CFGGuardTrippedError):
        build_cfg(fn_node, lmap, max_blocks=2)
