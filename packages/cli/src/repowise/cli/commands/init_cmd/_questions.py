"""The interactive part of a single-repo init: the plan screen and its answers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from repowise.cli.helpers import console
from repowise.cli.ui import print_banner, quick_repo_scan
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mascot import owl_status

from ._options import InitRun


def _apply_advanced_answers(run: InitRun, adv: dict[str, Any]) -> None:
    """Fold the advanced-config answers into the run."""
    opts = run.opts
    # Indexing knobs (always present).
    changes: dict[str, Any] = {
        "commit_limit": adv["commit_limit"],
        "follow_renames": adv["follow_renames"],
        "skip_tests": adv["skip_tests"],
        "skip_infra": adv["skip_infra"],
        # Merge with the command-line -x patterns rather than replacing them,
        # keeping order and dropping duplicates (same as the workspace path).
        "exclude": tuple(dict.fromkeys((*opts.exclude, *adv["exclude"]))),
        "include_submodules": adv.get("include_submodules", opts.include_submodules),
        "run_mode": adv.get("run_mode", opts.run_mode),
        # Asked in both branches: an index-only run renders a wiki too, and
        # those pages embed like any other, so the answer applies either way.
        "embedder_name": adv.get("embedder") or opts.embedder_name,
    }
    # Asked in both branches for the same reason: an index-only run renders
    # file pages too.
    run.max_file_pages_choice = adv.get("max_file_pages")
    # Generation knobs (only gathered when docs are on).
    if run.generate_docs:
        changes["concurrency"] = adv["concurrency"]
        changes["reasoning"] = adv.get("reasoning") or opts.reasoning
        changes["test_run"] = adv["test_run"]
        changes["onboarding"] = adv.get("onboarding", opts.onboarding)
        if adv.get("wiki_style"):
            changes["wiki_style"] = adv["wiki_style"]
        if adv.get("language"):
            run.language_choice = adv["language"]
    # Fast mode (picked in the indexing section) is a no-LLM index, so it
    # forces index-only even if the user had asked for docs.
    if changes["run_mode"] == "fast":
        changes["index_only"] = True
        run.generate_docs = False
    run.opts = replace(opts, **changes)


def _ask_questions(run: InitRun) -> None:
    """The plan screen (TTY, no explicit flags), then the follow-ups its keys open."""
    from ._plan import run_plan_screen

    print_banner(console)
    with owl_status(console, f"  Scanning repository{glyph('ellipsis')}"):
        scan_info = quick_repo_scan(run.repo_path)
    run_plan_screen(run, scan_info)
