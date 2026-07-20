from __future__ import annotations

from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
from repowise.core.generation.editor_files.data import EditorFileData
from repowise.core.generation.editor_files.tool_table import (
    TOOL_TABLE_ROWS,
    render_tool_table,
)


def _data() -> EditorFileData:
    return EditorFileData(
        repo_name="demo-repo",
        indexed_at="2026-05-03",
        indexed_commit="abc1234",
        architecture_summary="A small service.",
        build_commands={"test": "pytest"},
    )


def test_agents_md_renders_repowise_workflows() -> None:
    rendered = AgentsMdGenerator().render(_data())

    assert "get_overview()" in rendered
    assert "search_codebase" in rendered
    assert "get_context" in rendered
    assert "get_risk" in rendered
    assert "get_why" in rendered
    assert "get_dead_code" in rendered


def test_agents_md_renders_the_full_shared_tool_table() -> None:
    # AGENTS.md must reach the same tool surface as CLAUDE.md: both render the
    # single shared table from tool_table.py. Asserting the whole rendered table
    # is embedded means a future tool added to the registry (and therefore to
    # TOOL_TABLE_ROWS, which test_tool_table_drift pins to the registry) fails CI
    # here if AGENTS.md ever stops rendering it. This is the AGENTS.md twin of
    # the CLAUDE.md guard so the two can never drift apart again.
    rendered = AgentsMdGenerator().render(_data())

    assert render_tool_table() in rendered
    for signature, _row in TOOL_TABLE_ROWS.values():
        assert signature in rendered, f"AGENTS.md omits tool row: {signature}"
    # get_change_risk is the row that regressed before this guard existed.
    assert "get_change_risk" in rendered


def test_agents_md_writes_repo_root_file(tmp_path) -> None:
    written = AgentsMdGenerator().write(tmp_path, _data())

    assert written == tmp_path / "AGENTS.md"
    content = written.read_text(encoding="utf-8")
    assert "<!-- REPOWISE_AGENTS:START" in content
    assert "<!-- REPOWISE_AGENTS:END -->" in content
    assert "demo-repo" in content


def test_agents_md_preserves_user_content(tmp_path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("# Team Rules\n\nKeep this instruction.\n", encoding="utf-8")

    AgentsMdGenerator().write(tmp_path, _data())

    content = target.read_text(encoding="utf-8")
    assert "Keep this instruction." in content
    assert content.count("<!-- REPOWISE_AGENTS:START") == 1


def test_agents_md_replaces_only_managed_section(tmp_path) -> None:
    gen = AgentsMdGenerator()
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"# Team Rules\n\nKeep this.\n\n{marker_start}\nold managed text\n{marker_end}\n",
        encoding="utf-8",
    )

    gen.write(tmp_path, _data())

    content = target.read_text(encoding="utf-8")
    assert "Keep this." in content
    assert "old managed text" not in content
    assert "demo-repo" in content
