"""Budget guarantees for structural episode derivation.

Two layers, because a wall clock alone is a weak gate on a loaded box:

  1. An import-graph guard. The episode store is read on hook paths whose
     budget was fought down from 965 ms to 155 ms by deleting three
     module-level imports, so the store must stay stdlib-only. This is the
     half that catches a regression before it is a stopwatch problem.
  2. A wall-clock ceiling on the free facts, which run on every update. Loose
     on purpose: it exists to catch an added walk or subprocess, not to
     measure the machine.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.precedent.structural import derive_structural_episodes

#: Nothing in this list may be reachable from the episode store's imports.
_HEAVY_PREFIXES = (
    "click",
    "sqlalchemy",
    "structlog",
    "networkx",
    "yaml",
    "rich",
    "pathspec",
    "repowise.core.ingestion",
    "repowise.core.persistence",
    "repowise.core.generation",
)

#: Ceiling for deriving the three subprocess-free facts on a small tree.
#: The update-path allowance is 200 ms for everything Precedent adds; this is
#: an order of magnitude under it so a real regression is unambiguous.
_FREE_FACTS_BUDGET_MS = 250.0


def test_store_import_stays_stdlib_only() -> None:
    code = (
        "import sys; "
        "import repowise.core.precedent.store; "
        f"heavy = [m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})]; "
        "print('\\n'.join(heavy)); "
        "sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"heavy imports leaked:\n{result.stdout}{result.stderr}"


def test_free_facts_add_no_subprocess(tmp_path: Path, monkeypatch) -> None:
    """The update path derives only what the walk already paid for."""
    (tmp_path / ".repowise").mkdir()
    (tmp_path / "backend" / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess allowed here")),
    )
    traverser = FileTraverser(tmp_path)
    list(traverser._walk())
    assert derive_structural_episodes(tmp_path, traverser, allow_formatter_check=False)


def test_free_facts_stay_within_budget(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / "backend" / ".git").mkdir(parents=True)
    for i in range(200):
        (tmp_path / f"mod_{i}.py").write_text("x = 1\n", encoding="utf-8")
    traverser = FileTraverser(tmp_path)
    list(traverser._walk())

    derive_structural_episodes(tmp_path, traverser, allow_formatter_check=False)  # warm
    start = time.perf_counter()
    derive_structural_episodes(tmp_path, traverser, allow_formatter_check=False)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < _FREE_FACTS_BUDGET_MS, f"free facts took {elapsed_ms:.1f} ms"
