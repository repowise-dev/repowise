"""Tests for the BLAS thread pin (issue #1394).

The thing under test is a memory saving that nothing else in the suite can
see, so these pin the two properties that make it work rather than asserting
the saving itself: every backend variable is set, and an explicit choice from
the environment is never overwritten.

The third property — that the pin runs *before* numpy is imported — is the one
that actually decides whether any memory is saved, and it is pinned by
inspecting the CLI entry module's source rather than its behaviour: by the time
a test can observe an imported numpy, the workspace is already committed and
the damage cannot be detected from inside the same process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repowise.core.blas_threads import _BLAS_THREAD_VARS, limit_blas_threads


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in _BLAS_THREAD_VARS:
        monkeypatch.delenv(name, raising=False)


def test_pins_every_backend_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    limit_blas_threads()

    import os

    # Asserting the literal "1" rather than comparing against the default
    # argument: a change of default is a change of behaviour and should fail
    # here rather than pass silently against itself.
    for name in _BLAS_THREAD_VARS:
        assert os.environ[name] == "1", name


def test_covers_the_backend_that_actually_costs_the_memory() -> None:
    # OpenBLAS is the one measured at ~32 MB per thread. A refactor that
    # dropped it while keeping the others would keep every other test passing
    # and give back none of the memory.
    assert "OPENBLAS_NUM_THREADS" in _BLAS_THREAD_VARS


def test_does_not_pin_openmp() -> None:
    # Deliberately absent: igraph's community detection is OpenMP-parallel and
    # really uses those threads. Pinning it cost 18% wall clock for no extra
    # memory saving, so its absence is a decision, not an oversight.
    assert "OMP_NUM_THREADS" not in _BLAS_THREAD_VARS


def test_respects_an_explicit_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")

    limit_blas_threads()

    import os

    assert os.environ["OPENBLAS_NUM_THREADS"] == "8"
    # The others were unset, so they still get pinned.
    assert os.environ["MKL_NUM_THREADS"] == "1"


def test_accepts_a_thread_count(monkeypatch: pytest.MonkeyPatch) -> None:
    limit_blas_threads(4)

    import os

    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"


def _first_statements(path: Path) -> list[ast.stmt]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = tree.body
    # Skip the module docstring and `from __future__ import annotations`.
    return [
        node
        for node in body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ]


@pytest.mark.parametrize(
    "entry",
    [
        Path("packages/cli/src/repowise/cli/main.py"),
        Path("packages/server/src/repowise/server/app.py"),
    ],
)
def test_entry_point_pins_before_any_other_import(entry: Path) -> None:
    """The pin must be the first thing the entry module does.

    Ordering is the whole mechanism. If any import runs first and reaches
    numpy, the BLAS workspace is committed at the host's core count and no
    later call can shrink it — the process just silently keeps the memory.
    """
    assert entry.exists(), f"entry point moved: {entry}"
    statements = _first_statements(entry)

    assert statements, f"no statements found in {entry}"
    first, second = statements[0], statements[1]

    assert isinstance(first, ast.ImportFrom), (
        f"{entry}: expected the BLAS import first, got {ast.dump(first)[:80]}"
    )
    assert first.module == "repowise.core.blas_threads", (
        f"{entry}: first import is {first.module}, not the BLAS pin"
    )
    assert isinstance(second, ast.Expr) and isinstance(second.value, ast.Call), (
        f"{entry}: expected limit_blas_threads() to be called immediately"
    )
    assert second.value.func.id == "limit_blas_threads"  # type: ignore[attr-defined]
