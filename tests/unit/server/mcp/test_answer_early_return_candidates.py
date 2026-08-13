"""Every ``get_answer`` return that happens after retrieval carries ``candidates``.

``get_answer`` has several early returns that fire once retrieval has already
run: the qualified-miss guard, answer-by-union, the no-hits reply, the legacy
abstain, the value-extraction fast path, and both degraded paths. Each is a
complete reply in its own terms and each used to set ``retrieval`` to ``[]`` and
return, discarding ``resolved_pool`` -- the full ranked file list, already built,
already paid for.

Measured on the 70 ContextBench dev instances: the gates that fire after
retrieval account for 20 firings and 15 replies that named no gold file, every
one of them with a ranked pool in hand (finding A25's census).

The structural test below is the one that matters. A behavioural test can only
cover the branches it can reach, and the defect here is a branch nobody thought
about, so the invariant is asserted over the source: **no ``return`` inside
``get_answer`` positioned after ``resolved_pool`` is assigned may bypass
``_with_candidates``.** A future reordering of a 1,800-line function cannot
quietly re-open the hole.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from repowise.server.mcp_server.tool_answer import answer as answer_mod
from repowise.server.mcp_server.tool_answer.answer import _with_candidates

# Returns that route through this are covered: the helper attaches the block
# itself. ``_degraded_payload`` is the shared shape for both synthesis-less
# paths and calls ``_with_candidates`` internally. ``_degrade`` is the local
# binding of ``_degraded_payload`` inside ``get_answer``, which exists so the
# two synthesis-less returns name only what differs between them; it forwards
# ``resolved_pool`` like any other call to it.
_COVERING_CALLS = {"_with_candidates", "_degraded_payload", "_degrade"}

# The mainline return. It attaches ``candidates`` a few lines above itself
# rather than through the helper, because it also has to build the block after
# synthesis has run. Named explicitly so the exemption is a decision on the
# record rather than a hole the test cannot see.
_MAINLINE_RETURN = "payload"


def _get_answer_ast() -> tuple[ast.AsyncFunctionDef, str]:
    src = Path(inspect.getfile(answer_mod)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_answer"
    )
    return fn, src


def _resolved_pool_line(fn: ast.AsyncFunctionDef) -> int:
    return next(
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "resolved_pool" for t in n.targets)
    )


def test_no_post_retrieval_return_bypasses_the_candidates_helper():
    fn, src = _get_answer_ast()
    pool_line = _resolved_pool_line(fn)

    offenders: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if node.lineno <= pool_line:
            continue  # fires before retrieval; there is no pool to hand over
        value = node.value
        # ``_degraded_payload`` builds evidence off disk, so it is awaited; the
        # covering call is the awaited expression, not the ``await`` node.
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Name) and value.id == _MAINLINE_RETURN:
            continue
        covered = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _COVERING_CALLS
        )
        if not covered:
            snippet = (ast.get_source_segment(src, value) or "").splitlines()[:1]
            offenders.append(f"line {node.lineno}: {snippet}")

    assert not offenders, (
        "these returns fire after retrieval and hand back none of the ranked "
        f"pool they already hold: {offenders}"
    )


def test_the_mainline_return_is_still_the_only_exemption():
    """Guards the exemption above from widening by accident.

    If someone renames the mainline payload variable or adds a second bare-name
    return, the test above would silently stop covering it. This pins the count.
    """
    fn, _ = _get_answer_ast()
    pool_line = _resolved_pool_line(fn)
    bare = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Return)
        and n.lineno > pool_line
        and isinstance(n.value, ast.Name)
    ]
    assert len(bare) == 1, f"expected exactly one bare-name return, found {len(bare)}"
    assert bare[0].value.id == _MAINLINE_RETURN


def test_helper_attaches_the_shortlist():
    payload = {"answer": "", "retrieval": []}
    out = _with_candidates(payload, [{"target_path": "a.py"}, {"target_path": "b.py"}])
    assert out["candidates"] == [{"path": "a.py"}, {"path": "b.py"}]


def test_helper_adds_nothing_when_the_pool_is_empty():
    """An empty block is worse than none: it reads as "retrieval found nothing"."""
    payload = {"answer": "", "retrieval": []}
    assert "candidates" not in _with_candidates(payload, [])


def test_helper_takes_nothing_away():
    """The point of the fix is that each gate's own reply is returned unchanged."""
    payload = {
        "answer": "x",
        "citations": ["a.py"],
        "confidence": "high",
        "grounding": "exact_symbol",
        "symbol_bodies": [{"path": "a.py"}],
        "retrieval": [],
    }
    before = dict(payload)
    out = _with_candidates(payload, [{"target_path": "z.py"}])
    for key, value in before.items():
        assert out[key] == value


def test_a_page_that_names_no_file_never_becomes_a_candidate_here_either():
    """Finding A15 holds on the early-return path too.

    The helper delegates to ``serialize_candidates``, so this is a wiring
    assertion rather than a re-test of the resolver: the fix must not have
    introduced a second, laxer path to the same field.
    """
    out = _with_candidates(
        {},
        [
            {"page_type": "onboarding", "target_path": "onboarding/guided_tour"},
            {"page_type": "file_page", "target_path": "pkg/list.go"},
        ],
    )
    assert out["candidates"] == [{"path": "pkg/list.go"}]
