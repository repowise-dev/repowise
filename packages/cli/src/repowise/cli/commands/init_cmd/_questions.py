"""The interactive questionnaire of a single-repo init."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from repowise.cli.helpers import console
from repowise.cli.ui import (
    OWL_SPINNER,
    WARN,
    agent_providers_set_up,
    interactive_advanced_config,
    interactive_customize_offer,
    interactive_fast_mode_offer,
    interactive_generate_docs_toggle,
    interactive_mode_select,
    interactive_provider_config_select,
    print_banner,
    print_scan_summary,
    prompt_wiki_style,
    quick_repo_scan,
    should_offer_fast_mode,
)

from ._options import InitRun


def _ask_mode(run: InitRun) -> None:
    """The mode menu, mapped onto the two axes (docs on/off, customize yes/no).

    ``sys.stdin.isatty()`` is not a reliable answer to "can I read from
    stdin". On Windows under Git Bash, ``repowise init < /dev/null`` reports a
    TTY and then reads EOF on the first question; the same goes for some pty
    wrappers and ``docker run -t`` without -i. Agents drive init through
    exactly those shapes, so the first prompt is the probe: if it cannot be
    answered, drop to the non-interactive path rather than dying on a question
    nobody can hear. Only EOFError is caught. A real Ctrl-C raises
    KeyboardInterrupt/Abort and must still stop the run.
    """
    try:
        mode = interactive_mode_select(console)
    except EOFError:
        run.is_interactive = False
        console.print(
            f"\n[{WARN}]No answer available on stdin[/] "
            "[dim]- continuing with defaults. Pass --yes to skip the "
            "questions, or --prose / --no-prose to choose directly.[/dim]"
        )
        return

    #   full       -> docs on,  optional customize
    #   index_only -> docs off, optional customize
    #   advanced   -> docs toggle, always customize
    if mode == "index_only":
        run.generate_docs = False
        run.customize = interactive_customize_offer(console, generate_docs=False)
    elif mode == "advanced":
        run.generate_docs = interactive_generate_docs_toggle(console)
        run.customize = True
    elif mode is not None:  # full
        run.generate_docs = True
        run.customize = interactive_customize_offer(console, generate_docs=True)
    if mode is not None:
        run.opts = replace(run.opts, index_only=not run.generate_docs)


def _ask_provider(run: InitRun) -> None:
    """Provider selection, only when docs will be generated."""
    opts = run.opts
    selection = interactive_provider_config_select(
        console,
        opts.model,
        opts.reasoning,
        repo_path=run.repo_path,
        save_key=opts.save_key,
        prefer=agent_providers_set_up(run.repo_path),
    )
    run.opts = replace(
        opts,
        provider_name=selection.provider_name,
        model=selection.model,
        reasoning=selection.reasoning,
    )


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


def _offer_fast_mode(run: InitRun, scan_info: Any) -> None:
    """No customization: still offer the fast first-index on large repos.

    Default yes for an index-only run (docs already opted out), no for a full
    run (the user explicitly asked for docs).
    """
    if (
        run.opts.run_mode != "fast"
        and should_offer_fast_mode(scan_info)
        and interactive_fast_mode_offer(console, scan_info, default_fast=not run.generate_docs)
    ):
        run.opts = replace(run.opts, run_mode="fast", index_only=True)
        run.generate_docs = False


def _ask_questions(run: InitRun) -> None:
    """The interactive questionnaire (TTY, no explicit flags)."""
    print_banner(console, repo_name=run.repo_path.name)
    with console.status("  Scanning repository…", spinner=OWL_SPINNER):
        scan_info = quick_repo_scan(run.repo_path)
    print_scan_summary(console, scan_info)

    _ask_mode(run)
    # ``is_interactive`` can have been cleared by an unanswerable menu, in
    # which case every remaining question here is unanswerable too.
    if run.generate_docs and run.is_interactive:
        _ask_provider(run)

    if run.customize:
        adv = interactive_advanced_config(
            console,
            scan=scan_info,
            allow_fast=True,
            prompt_reasoning=False,
            generate_docs=run.generate_docs,
            wiki_style=run.opts.wiki_style,
            language=run.opts.language_opt,
        )
        _apply_advanced_answers(run, adv)
    elif run.is_interactive:
        _offer_fast_mode(run, scan_info)

    # Wiki style: prompt for a docs run when not already chosen (via the
    # --wiki-style flag or the advanced generation section). Index-only runs
    # generate no pages, so the question is skipped. --yes uses the default.
    if (
        run.generate_docs
        and run.opts.wiki_style is None
        and not run.opts.yes
        and run.is_interactive
    ):
        run.opts = replace(run.opts, wiki_style=prompt_wiki_style(console))
