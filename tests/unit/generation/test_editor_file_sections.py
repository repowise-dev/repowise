"""The ``editor_files.sections`` key and what each section renders."""

from __future__ import annotations

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
    "decisions": "### Standing decisions",
    "how_to_work": "### How to work in this repo",
    "tools": "### Tools",
    "commands": "### Commands",
}


@pytest.fixture(params=[ClaudeMdGenerator, AgentsMdGenerator], ids=["claude", "agents"])
def gen(request):
    return request.param()


@pytest.mark.parametrize("section", sorted(_HEADINGS))
def test_each_section_renders_only_when_listed(gen, section):
    alone = gen.render(_full_data(sections=frozenset({section})))
    assert _HEADINGS[section] in alone
    others = [h for name, h in _HEADINGS.items() if name != section]
    assert not [h for h in others if h in alone]


def test_the_default_is_lean(gen):
    lean = gen.render(_full_data())
    full = gen.render(_full_data(sections=ALL_SECTIONS))
    assert len(lean) < len(full)
    assert "### Commands" not in lean
    assert "Fix first" not in lean
    assert "hotspot health" not in lean
    # Decision titles only, at most three.
    assert "- Rule 2\n" in lean and "Rule 3" not in lean
    assert "because" not in lean


def test_the_opt_in_sections_bring_the_detail_back(gen):
    full = gen.render(_full_data(sections=ALL_SECTIONS))
    assert "Three co-equal signals" in full
    assert "maintainability 8.0/10" in full
    assert "- Split route (`src/api/routes.py:10`): long" in full
    assert "- Test: `pytest`" in full
    assert "never re-read them" in full


def test_full_how_to_work_names_the_pre_edit_and_finish_calls(gen):
    rendered = gen.render(_full_data(sections=frozenset({"how_to_work_full"})))
    assert rendered.count("**Before an edit:**") == 1
    assert 'get_risk(targets=["<file>"])' in rendered
    assert 'get_change_risk(revspec="<base>..HEAD")' in rendered
    assert 'get_health(targets=["<each file you touched>"])' in rendered


def test_how_to_work_routes_each_task_kind(gen):
    rendered = gen.render(_full_data(sections=frozenset({"how_to_work"})))
    # Exact names go to the text tools; the rest name one call with arguments.
    assert "Exact identifier, string or file name: Grep, Glob and Read are faster." in rendered
    assert 'get_answer(question="how does ...")' in rendered
    assert 'get_context(targets=["<symbol>"], include=["references"])' in rendered
    assert 'get_risk(targets=["<file>"])' in rendered
    assert 'get_why(query="why ...", targets=["<file>"])' in rendered
    assert 'get_change_risk(revspec="<base>..HEAD")' in rendered
    assert 'get_health(targets=["<files you touched>"])' in rendered


def test_the_tool_table_is_opt_in(gen):
    assert "| Tool | When and why |" not in gen.render(_full_data())
    assert "tools" in OPT_IN_SECTIONS


def test_repo_facts_lead_the_block(gen):
    rendered = gen.render(_full_data(sections=DEFAULT_SECTIONS | {"tools"}))
    order = [rendered.index(_HEADINGS[s]) for s in ("hotspots", "health", "how_to_work", "tools")]
    assert order == sorted(order)


def test_hotspot_rows_carry_a_date_not_an_age(gen):
    rendered = gen.render(_full_data())
    assert "4 bug fixes in 6 months, last fix 2026-10-12 (bug magnet); 12 commits/90d" in rendered
    assert " ago" not in rendered


def test_zero_accepted_decisions_and_no_health_drop_their_sections(gen):
    rendered = gen.render(_full_data(decisions=[], code_health=None))
    assert "### Standing decisions" not in rendered
    assert "### Code health" not in rendered
    assert "\n\n\n" not in rendered


def test_no_git_renders_without_commit_or_hotspots(gen):
    rendered = gen.render(_full_data(indexed_commit="", hotspots=[]))
    assert "Last indexed: 2026-10-19.\n" in rendered
    assert "### Files that need care" not in rendered


def test_the_doctor_stamp_survives(gen):
    import re

    rendered = gen.render(_full_data())
    # `repowise doctor` reads the commit back out of this line.
    assert re.search(r"Last indexed:.*?\(commit\s+([0-9a-fA-F]{7,})\)", rendered)


def test_no_trailing_whitespace_in_any_section_mix(gen):
    for sections in (DEFAULT_SECTIONS, ALL_SECTIONS, frozenset({"fix_first"}), frozenset()):
        rendered = gen.render(_full_data(sections=sections))
        assert not [line for line in rendered.splitlines() if line != line.rstrip()]
        assert "\n\n\n" not in rendered


def test_fix_first_alone_keeps_its_heading(gen):
    rendered = gen.render(_full_data(sections=frozenset({"fix_first"})))
    assert "### Code health\n\nFix first:" in rendered


_CLAUDE_NAMES = (
    "Repowise tools are MCP tools: call them as `mcp__repowise__<tool>` "
    "(`mcp__plugin_repowise_repowise__<tool>` when Repowise is installed as a Claude Code plugin)."
)
_AGENTS_NAMES = "Repowise tools come from the `repowise` MCP server."


@pytest.mark.parametrize("sections", [None, ALL_SECTIONS], ids=["default", "all"])
def test_the_shared_body_differs_only_in_tool_names_and_the_read_note(sections):
    data = _full_data(sections=sections) if sections else _full_data()
    claude = ClaudeMdGenerator().render(data)
    agents = AgentsMdGenerator().render(data)
    if sections is None:
        assert _CLAUDE_NAMES in claude and _AGENTS_NAMES in agents
    claude = claude.replace(_CLAUDE_NAMES, _AGENTS_NAMES)
    assert claude.replace(
        "Read a file before you Edit it; Claude Code requires that Read.",
        "Read a file before you edit it.",
    ).replace(
        "Claude Code requires a raw Read of any file you will Edit, and that Read is correct and expected.",
        "Reading a file before you edit it is correct and expected.",
    ) == agents


def test_the_short_confirm_command_supplies_evidence(gen):
    # `decision add` records no evidence, so a bare confirm of that candidate is
    # refused with "no evidence reference"; the copied command must carry it.
    lean = gen.render(_full_data())
    assert "`repowise decision confirm <id> --agent <your slug> --evidence <commit|file>`" in lean


def test_the_record_command_carries_its_commit(gen):
    # An agent's own record is accepted only with an evidence reference, so the
    # copied add command names the commit and the same agent can then accept it.
    lean = gen.render(_full_data())
    assert "--affects <path> --evidence-commit <sha>`" in lean
