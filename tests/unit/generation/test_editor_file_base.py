"""Unit tests for BaseEditorFileGenerator marker-merge logic."""

from __future__ import annotations

import pytest

from repowise.core.generation.editor_files.base import BaseEditorFileGenerator
from repowise.core.generation.editor_files.data import (
    CodeHealthBlock,
    EditorFileData,
    HotspotFile,
)

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class _TestGenerator(BaseEditorFileGenerator):
    filename = "TEST.md"
    marker_tag = "REPOWISE"
    template_name = "claude_md.j2"
    user_placeholder = "# TEST.md\n\n<!-- user content here -->\n"


def _minimal_data() -> EditorFileData:
    return EditorFileData(
        repo_name="test-repo",
        indexed_at="2026-03-28",
        indexed_commit="a1b2c3d",
        architecture_summary="A test repo.",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def gen():
    return _TestGenerator()


def test_render_returns_non_empty_string(gen):
    data = _minimal_data()
    result = gen.render(data)
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_never_references_repowise_repo_paths(gen):
    # The generated file lands in USERS' repos — a `docs/CODE_HEALTH.md`-style
    # reference points at a file that only exists in the repowise repo itself.
    import dataclasses

    data = dataclasses.replace(_minimal_data(), code_health=_health_block(6.4))
    result = gen.render(data)
    for repo_relative in ("docs/CODE_HEALTH.md", "docs/MCP_TOOLS.md", "docs/DISTILL.md"):
        assert repo_relative not in result


def test_render_contains_repo_name(gen):
    data = _minimal_data()
    result = gen.render(data)
    assert "test-repo" in result


def _health_block(
    maintainability_average: float | None,
    performance_average: float | None = None,
    performance_findings: int = 0,
    performance_coverage_pct: float | None = None,
) -> CodeHealthBlock:
    return CodeHealthBlock(
        hotspot_health=5.0,
        average_health=7.5,
        worst_score=2.0,
        worst_path="src/bad.py",
        maintainability_average=maintainability_average,
        performance_average=performance_average,
        performance_findings=performance_findings,
        performance_coverage_pct=performance_coverage_pct,
    )


def test_render_surfaces_maintainability_when_present(gen):
    import dataclasses

    data = dataclasses.replace(_minimal_data(), code_health=_health_block(6.4))
    result = gen.render(data)
    assert "maintainability 6.4/10" in result


def test_render_omits_maintainability_when_unmeasured(gen):
    import dataclasses

    data = dataclasses.replace(_minimal_data(), code_health=_health_block(None))
    result = gen.render(data)
    # Defect-risk headline still renders; the maintainability clause is
    # suppressed (the word still appears in the static tool table, so match
    # the clause shape, not the bare word).
    assert "hotspot health" in result
    assert "· maintainability" not in result


def test_render_surfaces_performance_when_present(gen):
    import dataclasses

    data = dataclasses.replace(
        _minimal_data(),
        code_health=_health_block(
            6.4, performance_average=9.2, performance_findings=7, performance_coverage_pct=88.0
        ),
    )
    result = gen.render(data)
    # Leads with the finding COUNT; the bounded [9,10] average and coverage %
    # are deliberately not rendered (nothing an agent can act on there).
    assert "performance risk 7 open static I/O-in-loop / N+1 findings" in result


def test_render_omits_performance_when_unmeasured(gen):
    import dataclasses

    data = dataclasses.replace(_minimal_data(), code_health=_health_block(6.4))
    result = gen.render(data)
    # Maintainability still renders; the performance clause is suppressed.
    assert "maintainability 6.4/10" in result
    assert "performance risk" not in result


def test_write_creates_new_file(gen, tmp_path):
    data = _minimal_data()
    written = gen.write(tmp_path, data)
    assert written.exists()
    content = written.read_text(encoding="utf-8")
    assert "<!-- REPOWISE:START" in content
    assert "<!-- REPOWISE:END -->" in content
    assert "<!-- user content here -->" in content  # placeholder present


def test_write_new_file_has_correct_structure(gen, tmp_path):
    data = _minimal_data()
    gen.write(tmp_path, data)
    content = (tmp_path / "TEST.md").read_text(encoding="utf-8")
    start_idx = content.index("<!-- REPOWISE:START")
    end_idx = content.index("<!-- REPOWISE:END -->")
    assert start_idx < end_idx
    # User placeholder is BEFORE the markers
    placeholder_idx = content.index("<!-- user content here -->")
    assert placeholder_idx < start_idx


def test_write_preserves_user_content_when_no_markers(gen, tmp_path):
    target = tmp_path / "TEST.md"
    user_content = "# My Project\n\nDo not touch this content!\n"
    target.write_text(user_content, encoding="utf-8")

    data = _minimal_data()
    gen.write(tmp_path, data)

    content = target.read_text(encoding="utf-8")
    assert "Do not touch this content!" in content
    assert "<!-- REPOWISE:START" in content


def test_write_appends_when_no_markers(gen, tmp_path):
    target = tmp_path / "TEST.md"
    target.write_text("# Existing\n\nsome content", encoding="utf-8")

    gen.write(tmp_path, _minimal_data())
    content = target.read_text(encoding="utf-8")

    # Existing content is BEFORE the markers
    existing_idx = content.index("# Existing")
    marker_idx = content.index("<!-- REPOWISE:START")
    assert existing_idx < marker_idx


def test_write_replaces_between_markers(gen, tmp_path):
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    target = tmp_path / "TEST.md"
    old_content = (
        "# My notes\n\nUser section.\n\n"
        f"{marker_start}\n## Old Content\nOld managed content.\n{marker_end}\n"
    )
    target.write_text(old_content, encoding="utf-8")

    gen.write(tmp_path, _minimal_data())
    new_content = target.read_text(encoding="utf-8")

    assert "User section." in new_content
    assert "Old managed content." not in new_content
    assert "test-repo" in new_content  # new managed content present


def test_write_replaces_only_managed_section(gen, tmp_path):
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    target = tmp_path / "TEST.md"
    target.write_text(
        f"# User notes\n\nKeep this.\n\n{marker_start}\nold\n{marker_end}\n",
        encoding="utf-8",
    )

    gen.write(tmp_path, _minimal_data())
    content = target.read_text(encoding="utf-8")

    assert "Keep this." in content
    assert content.count("<!-- REPOWISE:START") == 1
    assert content.count("<!-- REPOWISE:END -->") == 1


def test_write_is_idempotent(gen, tmp_path):
    data = _minimal_data()
    gen.write(tmp_path, data)
    first = (tmp_path / "TEST.md").read_text(encoding="utf-8")

    gen.write(tmp_path, data)
    second = (tmp_path / "TEST.md").read_text(encoding="utf-8")

    assert first == second


def test_write_returns_path(gen, tmp_path):
    result = gen.write(tmp_path, _minimal_data())
    assert result == tmp_path / "TEST.md"


def test_render_full_matches_write_output(gen, tmp_path):
    data = _minimal_data()
    preview = gen.render_full(tmp_path, data)
    gen.write(tmp_path, data)
    written = (tmp_path / "TEST.md").read_text(encoding="utf-8")
    assert preview == written


# ---------------------------------------------------------------------------
# Attention-list section: fix history leads, churn is the fallback
# ---------------------------------------------------------------------------


def _hotspot(path: str, **kw) -> HotspotFile:
    base = {"churn_percentile": 99.0, "commit_count_90d": 28, "owner": None}
    return HotspotFile(path=path, **{**base, **kw})


def test_render_hotspot_section_no_longer_claims_to_rank_by_churn(gen):
    # The header used to read "high churn" while telling the agent to call
    # get_risk, a defect tool. The list and its own instruction disagreed.
    import dataclasses

    data = dataclasses.replace(_minimal_data(), hotspots=[_hotspot("src/a.py")])
    result = gen.render(data)
    assert "high churn" not in result
    assert "get_risk" in result


def test_render_hotspot_row_leads_with_fix_history(gen):
    import dataclasses

    data = dataclasses.replace(
        _minimal_data(),
        hotspots=[_hotspot("src/a.py", fix_count=5, bug_magnet=True, last_fix_age="2 weeks ago")],
    )
    result = gen.render(data)
    assert "5 bug fixes, last fix 2 weeks ago (bug magnet); 28 commits/90d" in result


def test_render_hotspot_row_singularizes_one_fix(gen):
    import dataclasses

    data = dataclasses.replace(
        _minimal_data(),
        hotspots=[_hotspot("src/a.py", fix_count=1, last_fix_age="yesterday")],
    )
    result = gen.render(data)
    assert "1 bug fix, last fix yesterday" in result
    assert "1 bug fixes" not in result


def test_render_hotspot_row_falls_back_to_churn_without_fix_history(gen):
    # A repo whose commit messages carry no fix convention has zero fix mass
    # everywhere. The section must still render, on churn alone.
    import dataclasses

    data = dataclasses.replace(_minimal_data(), hotspots=[_hotspot("src/a.py")])
    result = gen.render(data)
    assert "- `src/a.py` — 28 commits/90d" in result
    assert "bug fix" not in result


def test_render_never_shows_a_fix_count_without_its_age(gen):
    # The recency contract: a count with no timestamp reads as an accusation
    # about 2019, so an unanchored count degrades to the churn row.
    import dataclasses

    data = dataclasses.replace(
        _minimal_data(),
        hotspots=[_hotspot("src/a.py", fix_count=9, bug_magnet=True, last_fix_age=None)],
    )
    result = gen.render(data)
    assert "9 bug fixes" not in result
    assert "bug magnet" not in result
    assert "28 commits/90d" in result
