"""Unit tests for the ``repowise generate`` interactive chooser helpers."""

from __future__ import annotations

import click
import pytest

from repowise.cli.commands.generate_cmd.chooser import (
    choose_cascade,
    print_wiki_state,
    run_interactive_chooser,
)
from repowise.cli.commands.generate_cmd.command import _reject_structural_page_ids
from repowise.core.generation.cascade import build_page_dependencies
from repowise.core.generation.page_selection import PageRecord


def test_reject_structural_page_ids_errors_on_file_page():
    # A file page is rendered from structure; naming it for `generate` is a clear
    # error, not a silent no-op.
    with pytest.raises(click.ClickException) as exc:
        _reject_structural_page_ids(("module_page:src/api", "file_page:src/app.py"))
    assert "file_page:src/app.py" in str(exc.value)


def test_reject_structural_page_ids_allows_concept_pages():
    # Model-written page types pass through untouched.
    _reject_structural_page_ids(("module_page:src/api", "repo_overview:demo"))


class _RecordingConsole:
    """Captures print calls; asserts the prompt is never reached."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: object, **_: object) -> None:
        self.lines.append(" ".join(str(a) for a in args))


class _MG:
    def __init__(self, key: str, file_paths: tuple[str, ...]) -> None:
        self.key = key
        self.file_paths = file_paths


def test_choose_cascade_skips_prompt_when_outcome_is_identical() -> None:
    # No containers and no repo-wide pages: every cascade mode generates exactly
    # the seed, so the choice cannot change the outcome and must not prompt.
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    console = _RecordingConsole()
    mode = choose_cascade(console, {"file_page:a.py"}, deps, default="none")
    assert mode == "none"
    # Nothing printed because the modes did not diverge (no prompt shown).
    assert console.lines == []


def test_choose_cascade_prompts_when_modes_diverge(monkeypatch) -> None:
    # a.py rolls up into a module page and there is a repo-wide overview, so the
    # three modes generate different sets and the chooser must prompt.
    deps = build_page_dependencies(
        module_groups=[_MG("src", ("a.py",))],
        scc_groups=[],
        layer_page_of={},
        repo_wide_ids=("repo_overview:demo",),
    )
    asked: dict[str, object] = {}

    def fake_ask(prompt, choices, default, console):
        asked["choices"] = choices
        asked["default"] = default
        return "2"  # pick "dependents"

    monkeypatch.setattr("repowise.cli.commands.generate_cmd.chooser.Prompt.ask", fake_ask)
    mode = choose_cascade(_RecordingConsole(), {"file_page:a.py"}, deps, default="none")
    assert mode == "dependents"
    assert asked["choices"] == ["1", "2", "3"]
    assert asked["default"] == "1"  # default maps to "none"


def _run(records, deps):
    return run_interactive_chooser(_RecordingConsole(), records=records, deps=deps)


def test_run_interactive_chooser_returns_cascade_when_unwritten() -> None:
    records = [PageRecord("module_page:src", "module_page", "src", is_template=True)]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    choice = _run(records, deps)
    assert choice is not None
    # No containers/repo-wide, so cascade cannot change the outcome; the default
    # ("dependents") comes back without prompting.
    assert choice.cascade_mode == "dependents"


def test_run_interactive_chooser_bails_when_nothing_unwritten() -> None:
    records = [PageRecord("module_page:src", "module_page", "src", is_template=False)]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    # Guard fires before the cascade step: every page is already written.
    assert _run(records, deps) is None


def test_print_wiki_state_counts_by_provenance() -> None:
    records = [
        PageRecord("a", "file_page", "a.py", is_template=True),
        PageRecord("b", "file_page", "b.py", is_template=False),
        PageRecord("c", "file_page", "c.py", is_template=True, freshness_status="stale"),
    ]
    console = _RecordingConsole()
    print_wiki_state(console, records)
    line = console.lines[0]
    assert "3 pages" in line
    assert "written" in line and "unwritten" in line and "stale" in line
    # Numbers are wrapped in rich color markup, so match the wrapped forms.
    assert "1[/cyan] written" in line
    assert "2[/yellow] unwritten" in line
    assert "1[/red] stale" in line
