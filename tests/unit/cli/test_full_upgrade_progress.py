"""``repowise update --full`` must show progress and persist incrementally (#1709).

The full-upgrade path used to pass ``progress=None`` into generation (no-ops
every progress callback — a long run showed a dead screen) and buffered every
page in memory until the end (a Ctrl-C at hour eight cost the whole run). It
now routes through init's ``run_generation_with_persistence`` wrapper, which
gives progress, incremental per-page saves and resume.

The wrapper call lives inside an async closure that needs real DB/graph
objects, so like the strict-branch guards in test_coverage_cmd.py this is a
structural pin: it asserts the call site names the persistence wrapper with a
real progress callback, and can only fail if someone restores the old
buffered ``progress=None`` path.
"""

from __future__ import annotations

import inspect

from repowise.cli.commands import upgrade_flow


def _upgrade_source() -> str:
    return inspect.getsource(upgrade_flow._run_upgrade)


def test_full_upgrade_uses_the_persistence_wrapper() -> None:
    src = _upgrade_source()

    assert "run_generation_with_persistence" in src, (
        "the full upgrade must route generation through init's persistence "
        "wrapper so pages are saved as they complete"
    )


def test_full_upgrade_passes_a_real_progress_callback() -> None:
    src = _upgrade_source()

    assert "RichProgressCallback" in src, (
        "the full upgrade must attach a live progress bar, not progress=None"
    )
    # The callback is wired to the same rich Progress init uses.
    assert "Progress(" in src


def test_full_upgrade_estimate_names_structural_pages() -> None:
    src = inspect.getsource(upgrade_flow._gate_cost)

    # The estimate line must call the summary that says most pages never reach
    # a model — otherwise "3661 pages" reads as 3661 model calls.
    assert "structural_page_summary" in src
