"""Unit tests for the ``repowise generate`` interactive chooser helpers."""

from __future__ import annotations

from repowise.cli.commands.generate_cmd import chooser as chooser_mod
from repowise.cli.commands.generate_cmd.chooser import (
    choose_cascade,
    print_wiki_state,
    run_interactive_chooser,
)
from repowise.core.generation.cascade import build_page_dependencies
from repowise.core.generation.page_selection import PageRecord


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

    monkeypatch.setattr(
        "repowise.cli.commands.generate_cmd.chooser.Prompt.ask", fake_ask
    )
    mode = choose_cascade(_RecordingConsole(), {"file_page:a.py"}, deps, default="none")
    assert mode == "dependents"
    assert asked["choices"] == ["1", "2", "3"]
    assert asked["default"] == "1"  # default maps to "none"


class _Provider:
    provider_name = "openai"
    model_name = "gpt-x"


class _Option:
    def __init__(self, pct: float) -> None:
        self.pct = pct


def _patch_chooser(monkeypatch, *, chosen_pct: float, seed: set[str]) -> None:
    monkeypatch.setattr(chooser_mod, "compute_coverage_options", lambda **_: [_Option(0.2)])
    monkeypatch.setattr(
        chooser_mod, "interactive_coverage_select", lambda *a, **k: _Option(chosen_pct)
    )
    monkeypatch.setattr(chooser_mod, "build_ranked_seed", lambda **_: set(seed))


def _run(records, deps):
    return run_interactive_chooser(
        _RecordingConsole(),
        records=records,
        parsed_files=[],
        graph_builder=object(),
        config=object(),
        kg_ctx=object(),
        provider=_Provider(),
        repo_path=object(),
        repo_name="demo",
        deps=deps,
    )


def test_run_interactive_chooser_returns_scope(monkeypatch) -> None:
    records = [PageRecord("file_page:a.py", "file_page", "a.py", is_template=True)]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    _patch_chooser(monkeypatch, chosen_pct=0.2, seed={"file_page:a.py"})
    choice = _run(records, deps)
    assert choice is not None
    assert choice.ranked_seed == {"file_page:a.py"}
    # No containers/repo-wide, so cascade cannot change the outcome -> none.
    assert choice.cascade_mode == "none"


def test_run_interactive_chooser_bails_when_nothing_unwritten(monkeypatch) -> None:
    records = [PageRecord("file_page:a.py", "file_page", "a.py", is_template=False)]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    _patch_chooser(monkeypatch, chosen_pct=0.2, seed=set())
    # Guard fires before the menu: every page is already written.
    assert _run(records, deps) is None


def test_run_interactive_chooser_bails_when_pick_is_all_written(monkeypatch) -> None:
    records = [PageRecord("file_page:a.py", "file_page", "a.py", is_template=True)]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    # There are unwritten pages, but the chosen coverage resolves to an empty
    # seed (e.g. a tiny pct on a repo whose important pages are all written).
    _patch_chooser(monkeypatch, chosen_pct=0.1, seed=set())
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
