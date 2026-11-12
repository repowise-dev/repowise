"""The ``editor_files.sections`` key and what each section renders."""

from __future__ import annotations

import re

import pytest

from repowise.core.generation.editor_files import AgentsMdGenerator, ClaudeMdGenerator
from repowise.core.generation.editor_files.data import (
    CodeHealthBlock,
    DecisionSummary,
    EditorFileData,
    HotspotFile,
    KeyModule,
)
from repowise.core.generation.editor_files.sections import (
    ALL_SECTIONS,
    DEFAULT_SECTIONS,
    OPT_IN_SECTIONS,
    load_sections,
    resolve_sections,
)


def test_no_key_means_the_default():
    assert resolve_sections(None) == DEFAULT_SECTIONS


def test_prefixed_names_adjust_the_default():
    assert resolve_sections(["+commands", "-decisions"]) == (DEFAULT_SECTIONS | {"commands"}) - {
        "decisions"
    }


def test_the_list_replaces_the_default():
    assert resolve_sections(["hotspots", "Tools"]) == {"hotspots", "tools"}


def test_only_one_prefix_is_read():
    assert resolve_sections(["--architecture"]) == DEFAULT_SECTIONS
    assert resolve_sections({"commands": True}) == DEFAULT_SECTIONS


def test_unknown_names_are_dropped():
    assert resolve_sections(["hotspots", "nonsense"]) == {"hotspots"}


def test_a_bare_string_is_one_name():
    assert resolve_sections("commands") == {"commands"}


def test_a_non_list_value_falls_back_to_the_default():
    assert resolve_sections(7) == DEFAULT_SECTIONS


def test_opt_in_names_are_not_in_the_default():
    assert not OPT_IN_SECTIONS & DEFAULT_SECTIONS
    assert {"code_health", "fix_first", "commands", "how_to_work_full"} <= ALL_SECTIONS


def test_load_reads_the_repo_config(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "editor_files:\n  claude_md: true\n  sections: [+fix_first]\n", encoding="utf-8"
    )
    assert load_sections(tmp_path) == DEFAULT_SECTIONS | {"fix_first"}


def test_load_without_config_is_the_default(tmp_path):
    assert load_sections(tmp_path) == DEFAULT_SECTIONS


def test_a_broken_config_never_fails_the_write(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("editor_files: [\n", encoding="utf-8")
    assert load_sections(tmp_path) == DEFAULT_SECTIONS


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _full_data(**kw) -> EditorFileData:
    base = {
        "repo_name": "demo",
        "indexed_at": "2026-10-19",
        "indexed_commit": "a1b2c3d",
        "architecture_summary": "A demo service.",
        "key_modules": [KeyModule(name="src/api", purpose="HTTP layer", owner=None)],
        "entry_points": ["src/main.py"],
        "hotspots": [
            HotspotFile(
                path="src/api/routes.py",
                churn_percentile=99.0,
                commit_count_90d=12,
                owner=None,
                fix_count=4,
                bug_magnet=True,
                last_fix_date="2026-10-12",
            )
        ],
        "decisions": [
            DecisionSummary(title=f"Rule {i}", status="active", rationale="because") for i in range(4)
        ],
        "decisions_in_force": True,
        "build_commands": {"test": "pytest"},
        "code_health": CodeHealthBlock(
            hotspot_health=6.0,
            average_health=7.9,
            band="Good",
            worst_score=2.1,
            worst_path="src/api/routes.py",
            maintainability_average=8.0,
            fix_first=[{"title": "Split route", "where": "src/api/routes.py:10", "why": "long"}],
        ),
    }
    return EditorFileData(**{**base, **kw})


_HEADINGS = {
    "architecture": "### Architecture",
    "key_modules": "### Key modules",
    "entry_points": "### Entry points",
    "hotspots": "### Files that need care",
    "health": "### Code health",
    "tools": "### Tools",
    "commands": "### Commands",
}

_KEYLESS_SCOPE = {"content_provenance": "template", "search": {"semantic": "unavailable"}}

_STEER = (
    "For any question about this codebase, call a Repowise tool first "
    "(get_answer for how/where/why questions, search_codebase for an identifier, "
    "get_context for callers or a file)."
)
_POINTER = (
    'Recorded decisions govern some files: `get_why(targets=["<file>"])` '
    "before changing how one works."
)


@pytest.fixture(params=[ClaudeMdGenerator, AgentsMdGenerator], ids=["claude", "agents"])
def gen(request):
    return request.param()


@pytest.mark.parametrize("section", sorted(_HEADINGS))
def test_each_section_renders_only_when_listed(gen, section):
    alone = gen.render(_full_data(sections=frozenset({section})))
    assert _HEADINGS[section] in alone
    others = [h for name, h in _HEADINGS.items() if name != section]
    assert not [h for h in others if h in alone]


@pytest.mark.parametrize(
    "sections", [DEFAULT_SECTIONS, ALL_SECTIONS, frozenset()], ids=["default", "all", "none"]
)
def test_every_block_leads_with_the_call_first_line(gen, sections):
    rendered = gen.render(_full_data(sections=sections))
    assert rendered.count(_STEER) == 1
    body = rendered.split("Last indexed:", 1)[1].split("\n", 1)[1]
    assert body.lstrip("\n").startswith(_STEER)


def test_the_call_first_line_is_the_one_every_host_gets():
    from repowise.core.agent_steer import AGENT_STEER

    assert AGENT_STEER == _STEER


def test_the_default_is_lean(gen):
    lean = gen.render(_full_data())
    full = gen.render(_full_data(sections=ALL_SECTIONS))
    assert len(lean) < len(full)
    assert "### " not in lean
    # Pinned decisions by title only.
    assert "- Pinned decision: Rule 3\n" in lean
    assert "because" not in lean


def test_orientation_sections_are_one_setting_away(gen):
    for name in ("architecture", "key_modules", "entry_points", "hotspots", "health"):
        assert name in OPT_IN_SECTIONS
        rendered = gen.render(_full_data(sections=resolve_sections([f"+{name}"])))
        assert _HEADINGS[name] in rendered


def test_the_opt_in_sections_bring_the_detail_back(gen):
    full = gen.render(_full_data(sections=ALL_SECTIONS))
    assert "Three co-equal signals" in full
    assert "maintainability 8.0/10" in full
    assert "- Split route (`src/api/routes.py:10`): long" in full
    assert "- Test: `pytest`" in full
    assert "never re-read them" in full


def test_full_how_to_work_names_the_pre_edit_and_finish_calls(gen):
    rendered = gen.render(
        _full_data(sections=frozenset({"how_to_work_full"}), mcp_tools="standard")
    )
    assert rendered.count("**Before an edit:**") == 1
    assert 'get_risk(targets=["<file>"])' in rendered
    assert 'get_change_risk(revspec="<base>..HEAD")' in rendered
    assert 'get_health(targets=["<each file you touched>"])' in rendered


def test_how_to_work_is_one_line_per_moment(gen):
    rendered = gen.render(_full_data(sections=frozenset({"how_to_work"}), mcp_tools="standard"))
    assert (
        '- Before a rename: `get_context(targets=["<symbol>"], include=["references"])`.'
        in rendered
    )
    assert 'Before editing a file: `get_risk(targets=["<file>"])`' in rendered
    assert 'before changing how it works: `get_why(targets=["<file>"])`' in rendered
    assert '- Before finishing: `get_change_risk(revspec="<base>..HEAD")`.' in rendered
    assert "`repowise distill <cmd>`" in rendered


def test_nothing_sends_the_agent_to_grep_first(gen):
    for sections in (DEFAULT_SECTIONS, ALL_SECTIONS):
        assert "Grep, Glob and Read are faster" not in gen.render(_full_data(sections=sections))


def test_the_tool_table_is_opt_in(gen):
    assert "| Tool | When and why |" not in gen.render(_full_data())
    assert "tools" in OPT_IN_SECTIONS


def test_hotspot_rows_carry_a_date_not_an_age(gen):
    rendered = gen.render(_full_data(sections=frozenset({"hotspots"})))
    assert "4 bug fixes in 6 months, last fix 2026-10-12 (bug magnet); 12 commits/90d" in rendered
    assert " ago" not in rendered


def test_zero_accepted_decisions_and_no_health_drop_their_sections(gen):
    rendered = gen.render(
        _full_data(
            decisions=[],
            decisions_in_force=False,
            code_health=None,
            sections=DEFAULT_SECTIONS | {"health"},
        )
    )
    assert "decisions govern" not in rendered
    assert "### Code health" not in rendered
    assert "\n\n\n" not in rendered


def test_no_git_renders_without_commit_or_hotspots(gen):
    rendered = gen.render(_full_data(indexed_commit="", hotspots=[], sections=ALL_SECTIONS))
    assert "Last indexed: 2026-10-19.\n" in rendered
    assert "### Files that need care" not in rendered


def test_the_doctor_stamp_survives(gen):
    import re

    rendered = gen.render(_full_data())
    # `repowise doctor` reads the commit back out of this line.
    assert re.search(r"Last indexed:.*?\(commit\s+([0-9a-fA-F]{7,})\)", rendered)


def test_no_trailing_whitespace_in_any_section_mix(gen):
    keyless = {"content_provenance": "template", "search": {"semantic": "unavailable"}}
    for sections in (DEFAULT_SECTIONS, ALL_SECTIONS, frozenset({"fix_first"}), frozenset()):
        for scope in ({}, keyless):
            rendered = gen.render(_full_data(sections=sections, index_scope=scope))
            assert not [line for line in rendered.splitlines() if line != line.rstrip()]
            assert "\n\n\n" not in rendered


def test_fix_first_alone_keeps_its_heading(gen):
    rendered = gen.render(_full_data(sections=frozenset({"fix_first"})))
    assert "### Code health\n\nFix first:" in rendered


@pytest.mark.parametrize("sections", [None, ALL_SECTIONS], ids=["default", "all"])
def test_the_shared_body_differs_only_in_the_read_note(sections):
    # With every default tool listed to both readers, only the read note differs.
    data = _full_data(mcp_tools="standard", **({"sections": sections} if sections else {}))
    claude = ClaudeMdGenerator().render(data)
    agents = AgentsMdGenerator().render(data)
    if sections is None:
        assert claude == agents
    assert (
        claude.replace(
            "Read a file before you Edit it; Claude Code requires that Read.",
            "Read a file before you edit it.",
        )
        == agents
    )


# ---------------------------------------------------------------------------
# AGENTS.md names only the tools its reader's client is listed
# ---------------------------------------------------------------------------

_TOOL_NAME = re.compile(r"\b(?:get_[a-z_]+|search_codebase|list_repos)\b")


@pytest.mark.parametrize("scope", [{}, _KEYLESS_SCOPE], ids=["model", "keyless"])
@pytest.mark.parametrize("sections", [DEFAULT_SECTIONS, ALL_SECTIONS], ids=["default", "all"])
def test_agents_md_names_only_the_core_tools_by_default(sections, scope):
    from repowise.core.registry.tool_selection import CORE_TOOLS

    rendered = AgentsMdGenerator().render(_full_data(sections=sections, index_scope=scope))
    assert set(_TOOL_NAME.findall(rendered)) <= CORE_TOOLS, rendered
    assert 'get_why(targets=["<file>"])' in rendered or "get_why(query=" in rendered


def test_claude_md_keeps_every_moment_line():
    rendered = ClaudeMdGenerator().render(_full_data(mcp_tools="core"))
    assert 'get_risk(targets=["<file>"])' in rendered
    assert 'get_change_risk(revspec="<base>..HEAD")' in rendered


def test_agents_md_follows_the_configured_tool_list():
    rendered = AgentsMdGenerator().render(_full_data(mcp_tools="core +get_risk"))
    assert (
        '- Before editing a file: `get_risk(targets=["<file>"])`; before changing how it works: '
        '`get_why(targets=["<file>"])`'
    ) in rendered
    assert "get_change_risk" not in rendered


def test_agents_md_drops_a_line_whose_tool_is_configured_off():
    rendered = AgentsMdGenerator().render(_full_data(mcp_tools="get_answer search_codebase"))
    assert "get_context(" not in rendered
    assert "get_why(" not in rendered
    assert "Recorded decisions govern" not in rendered


def test_agents_md_tool_table_lists_only_the_listed_tools():
    from repowise.core.registry.tool_selection import CORE_TOOLS

    rendered = AgentsMdGenerator().render(_full_data(sections=frozenset({"tools"})))
    rows = [line for line in rendered.splitlines() if line.startswith("| `")]
    assert len(rows) == len(CORE_TOOLS)
    assert set(_TOOL_NAME.findall("\n".join(rows))) <= CORE_TOOLS


# ---------------------------------------------------------------------------
# Budget: the block is read into the agent's context on every turn
# ---------------------------------------------------------------------------

#: About 250 tokens at the four-characters-per-token estimate the repo uses
#: (``distill.budget.estimate_tokens``); the longest default block below is
#: 249 tokens with the cl100k tokenizer.
_BLOCK_BUDGET_CHARS = 1_000
#: Each pinned decision is one line a person chose to pay for.
_PIN_LINE_BUDGET_CHARS = 160

_KEYLESS = {
    "content_provenance": "template",
    "search": {"semantic": "unavailable"},
    "git_tier": "essential",
}
_UPGRADING = {"upgrade": {"status": "resumable"}, "git_tier": "essential"}


@pytest.mark.parametrize("scope", [{}, _KEYLESS, _UPGRADING], ids=["model", "keyless", "upgrade"])
@pytest.mark.parametrize("in_force", [False, True], ids=["no-decisions", "decisions"])
def test_the_default_block_fits_its_budget(gen, scope, in_force):
    rendered = gen.render(
        _full_data(
            repo_name="a-fairly-long-repository-name-for-budget",
            decisions=[],
            decisions_in_force=in_force,
            index_scope=scope,
        )
    )
    assert len(rendered) <= _BLOCK_BUDGET_CHARS, rendered


def test_pinned_decisions_add_one_capped_line_each(gen):
    pins = [
        DecisionSummary(
            title="t" * 79 + "…",
            status="active",
            rationale="",
            signed_by=" [accepted by claude_code, not a person]",
        )
        for _ in range(5)
    ]
    bare = gen.render(_full_data(decisions=[], decisions_in_force=True, index_scope=_KEYLESS))
    pinned = gen.render(_full_data(decisions=pins, decisions_in_force=True, index_scope=_KEYLESS))
    added = [line for line in pinned.splitlines() if line.startswith("- Pinned decision:")]
    assert len(added) == 5
    assert all(len(line) <= _PIN_LINE_BUDGET_CHARS for line in added)
    assert len(pinned) - len(bare) <= 5 * (_PIN_LINE_BUDGET_CHARS + 1)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_the_full_block_keeps_the_promote_command(gen):
    # Putting a background record in force stays documented where there is room.
    full = gen.render(_full_data(sections=frozenset({"how_to_work_full"})))
    assert "`repowise decision confirm <id> --agent <your slug>`" in full


def test_the_short_block_points_at_the_record_command(gen):
    # The command's help says an agent's record comes into force when the
    # commit it cites merges; the line only has to name the command.
    lean = gen.render(_full_data())
    assert "`repowise decision add --help`" in lean
    assert "decision confirm" not in lean


def test_with_decisions_in_force_the_edit_line_says_so(gen):
    rendered = gen.render(_full_data(decisions=[]))
    assert "as recorded decisions govern some files." in rendered
    assert _POINTER not in rendered


def test_without_the_moment_lines_the_pointer_stands_alone(gen):
    rendered = gen.render(_full_data(decisions=[], sections=frozenset({"decisions"})))
    assert f"- {_POINTER}\n" in rendered


def test_read_only_drops_the_record_a_decision_line(gen):
    for sections in (None, ALL_SECTIONS):
        kw = {"sections": sections} if sections else {}
        full = gen.render(_full_data(**kw))
        read_only = gen.render(_full_data(record_decisions=False, **kw))
        assert "repowise decision add" in full
        assert "repowise decision add" not in read_only
        assert "Pinned decision: Rule 0" in read_only
        assert len(read_only) < len(full)


@pytest.mark.parametrize("sections", [None, frozenset({"how_to_work_full"})], ids=["short", "full"])
def test_how_to_work_points_at_settings_in_one_line(gen, sections):
    rendered = gen.render(_full_data(sections=sections) if sections else _full_data())
    lines = [line for line in rendered.splitlines() if "repowise config" in line]
    assert len(lines) == 1
    assert "`repowise config list`" in lines[0]
    assert "explain <key>`" in lines[0]
