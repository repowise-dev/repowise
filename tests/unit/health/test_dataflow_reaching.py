"""Unit tests for the dataflow def/use + reaching-definitions pass.

Best-effort like the complexity-walker tests: each test skips when the Python
tree-sitter pack is missing rather than failing.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.ast_utils import _collect_function_nodes
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    analyze_function,
    compute_def_use,
    compute_reaching,
)
from repowise.core.analysis.health.dataflow.dialects.base import get_defuse_dialect


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _first_fn(src: str):
    _require_python()
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    lmap = get_language_map("python")
    parser = Parser(_get_language("python"))
    tree = parser.parse(textwrap.dedent(src).encode())
    nodes = _collect_function_nodes(tree.root_node, lmap)
    assert nodes, "no function node parsed"
    return nodes[0], lmap


def _analyze(src: str):
    fn_node, lmap = _first_fn(src)
    out = analyze_function(fn_node, "python", lmap)
    assert out is not None, "analysis returned None"
    return out  # (cfg, def_use, reaching)


def _def_use_names(src: str) -> tuple[set[str], set[str]]:
    """All def and use variable names across the function (params included)."""
    _cfg, def_use, _reaching = _analyze(src)
    defs = {d.var for d in def_use.definitions}
    uses = {u.name for bdu in def_use.blocks.values() for u in bdu.uses}
    return defs, uses


# -- def/use classification -----------------------------------------------------


def test_simple_assignment():
    defs, uses = _def_use_names(
        """
        def f(a):
            x = a + 1
            return x
        """
    )
    assert "x" in defs
    assert "a" in uses


def test_augmented_assignment_is_def_and_use():
    defs, uses = _def_use_names(
        """
        def f(a):
            x = 0
            x += a
            return x
        """
    )
    assert "x" in defs
    # x is read in ``x += a`` (read-modify-write) and a is read.
    assert "x" in uses
    assert "a" in uses


def test_tuple_unpacking_defines_all_targets():
    defs, _uses = _def_use_names(
        """
        def f(pair):
            a, b = pair
            return a + b
        """
    )
    assert {"a", "b"} <= defs


def test_for_target_is_def_iterable_is_use():
    _cfg, def_use, _r = _analyze(
        """
        def f(items):
            total = 0
            for x in items:
                total += x
            return total
        """
    )
    defs = {d.var for d in def_use.definitions}
    uses = {u.name for bdu in def_use.blocks.values() for u in bdu.uses}
    assert "x" in defs  # the for-target is a def
    assert "items" in uses  # the iterable is a use


def test_with_as_alias_is_def():
    defs, uses = _def_use_names(
        """
        def f(path):
            with open(path) as fh:
                data = fh.read()
            return data
        """
    )
    assert "fh" in defs
    assert "path" in uses  # the context expression is a use


def test_with_body_branch_defs_both_reach_return():
    _cfg_result, def_use, reaching = _analyze(
        """
        def f(ctx, cond):
            with ctx.push():
                if cond:
                    result = 1
                else:
                    result = 2
            return result
        """
    )

    return_block = next(
        block_id
        for block_id, block_def_use in def_use.blocks.items()
        if any(use.name == "result" and use.line == 8 for use in block_def_use.uses)
    )
    reaching_lines = {
        definition.line
        for definition in reaching.reaching_in(return_block)
        if definition.var == "result"
    }

    assert reaching_lines == {5, 7}


def test_walrus_is_def():
    defs, _uses = _def_use_names(
        """
        def f(a):
            if (n := compute(a)) > 0:
                return n
            return 0
        """
    )
    assert "n" in defs


def test_comprehension_target_is_def():
    defs, uses = _def_use_names(
        """
        def f(source):
            data = [v * 2 for v in source if v > 0]
            return data
        """
    )
    assert {"data", "v"} <= defs
    assert "source" in uses


def test_parameters_are_defs():
    _cfg, def_use, _r = _analyze(
        """
        def f(a, b=1, *args, **kw):
            return a
        """
    )
    param_names = {o.name for o in def_use.params}
    assert {"a", "b", "args", "kw"} <= param_names


def test_attribute_and_subscript_targets_are_not_local_defs():
    defs, uses = _def_use_names(
        """
        def f(obj, arr, i, v):
            obj.attr = v
            arr[i] = v
            return obj
        """
    )
    # No local named ``attr`` is defined; the bases / indices are reads.
    assert "attr" not in defs
    assert {"obj", "arr", "i", "v"} <= uses


def test_member_and_keyword_names_are_not_uses():
    _cfg, def_use, _r = _analyze(
        """
        def f(client, x):
            y = client.fetch(timeout=x)
            return y
        """
    )
    uses = {u.name for bdu in def_use.blocks.values() for u in bdu.uses}
    assert "client" in uses
    assert "x" in uses
    # ``fetch`` is a member name and ``timeout`` a keyword name -> not variables.
    assert "fetch" not in uses
    assert "timeout" not in uses


# -- reaching definitions -------------------------------------------------------


def _reaching_vars_at_exit(src: str) -> list[tuple[str, int]]:
    cfg, _def_use, reaching = _analyze(src)
    return [(d.var, d.line) for d in reaching.reaching_in(cfg.exit_id)]


def test_both_branch_defs_reach_join():
    cfg, _du, reaching = _analyze(
        """
        def f(c):
            if c:
                y = 1
            else:
                y = 2
            return y
        """
    )
    # At the return (which precedes exit), both definitions of y reach.
    reaching_y = [d for d in reaching.reaching_in(cfg.exit_id) if d.var == "y"]
    assert len(reaching_y) == 2


def test_handler_assignment_reaches_finally_on_reraise():
    _cfg_result, def_use, reaching = _analyze(
        """
        def f():
            failed = False
            try:
                run()
            except Exception:
                failed = True
                raise
            finally:
                if not failed:
                    cleanup()
        """
    )

    finally_block = next(
        block_id
        for block_id, block_def_use in def_use.blocks.items()
        if any(use.name == "failed" and use.line == 10 for use in block_def_use.uses)
    )
    reaching_lines = {
        definition.line
        for definition in reaching.reaching_in(finally_block)
        if definition.var == "failed"
    }

    assert 7 in reaching_lines


def test_redefinition_kills_earlier_def():
    cfg, _du, reaching = _analyze(
        """
        def f(a):
            x = a
            x = a + 1
            return x
        """
    )
    # Only the second definition of x reaches the end (straight-line kill).
    reaching_x = [d for d in reaching.reaching_in(cfg.exit_id) if d.var == "x"]
    assert len(reaching_x) == 1
    assert reaching_x[0].line == 4


def test_loop_def_and_preloop_def_both_reach():
    cfg, _du, reaching = _analyze(
        """
        def f(items):
            total = 0
            for x in items:
                total = total + x
            return total
        """
    )
    # The loop may run zero times, so both the pre-loop total and the in-loop
    # total reach the return -- a may-analysis keeps both.
    reaching_total = [d for d in reaching.reaching_in(cfg.exit_id) if d.var == "total"]
    assert len(reaching_total) == 2


def test_parameter_def_reaches_use():
    cfg, _du, reaching = _analyze(
        """
        def f(a):
            b = 1
            if a:
                b = 2
            return a + b
        """
    )
    # The parameter ``a`` (defined at entry) reaches the return.
    names = {d.var for d in reaching.reaching_in(cfg.exit_id)}
    assert "a" in names


# -- determinism ----------------------------------------------------------------


def test_reaching_is_deterministic():
    src = """
        def f(c, items):
            x = 0
            for it in items:
                if it > c:
                    x = x + it
                else:
                    x = x - it
            try:
                check(x)
            except ValueError:
                x = 0
            return x
        """

    def serialize():
        _cfg, _du, reaching = _analyze(src)
        return {
            bid: sorted((d.var, d.line) for d in reaching.reaching_in(bid))
            for bid in sorted(reaching.in_sets)
        }

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# -- budget / gating ------------------------------------------------------------


def test_analyze_file_flagged_only_gate():
    lines = ["def big(x):", "    y = 0"]
    for i in range(12):
        lines += [f"    if x == {i}:", f"        y = {i}"]
    lines += ["    return y", "", "def tiny():", "    return 1", ""]
    result = analyze_file("mem.py", "python", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_analyzed == 1
    assert [fa.name for fa in result.functions] == ["big"]
    # The analyzed function carries a converged reaching result.
    assert result.functions[0].reaching.converged


def test_convergence_guard_degrades_to_silence():
    # A tiny iteration cap forces non-convergence; compute_reaching must report
    # it rather than spin, and the result is then dropped by callers.
    cfg, def_use, _r = _analyze(
        """
        def f(c, items):
            x = 0
            for it in items:
                if c:
                    x = it
                else:
                    x = x + 1
            return x
        """
    )
    forced = compute_reaching(cfg, def_use, max_iterations=1)
    assert forced.converged is False


# -- graceful degradation -------------------------------------------------------


def test_unmapped_language_returns_none():
    # A function node parsed as Python but analyzed under a language with no
    # dialect yields no analysis.
    fn_node, lmap = _first_fn(
        """
        def f(a):
            return a
        """
    )
    assert get_defuse_dialect("cobol") is None
    assert analyze_function(fn_node, "cobol", lmap) is None


def test_analyze_file_unsupported_language_is_silent():
    result = analyze_file("x.unknown", "cobol", b"whatever")
    assert result.functions == []
    assert result.stats.functions_seen == 0


def test_def_use_orchestration_directly():
    # compute_def_use + compute_reaching can be driven without the file harness.
    fn_node, lmap = _first_fn(
        """
        def f(a):
            x = a
            return x
        """
    )
    from repowise.core.analysis.health.dataflow import build_cfg

    dialect = get_defuse_dialect("python")
    cfg = build_cfg(fn_node, lmap)
    def_use = compute_def_use(cfg, fn_node, lmap, dialect)
    reaching = compute_reaching(cfg, def_use)
    assert reaching.converged
    assert "x" in {d.var for d in def_use.definitions}
