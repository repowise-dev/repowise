"""Unit tests for WorkspaceClaudeMdGenerator and WorkspaceEditorFileData."""

from __future__ import annotations

import pytest

from repowise.core.generation.editor_files.claude_md import WorkspaceClaudeMdGenerator
from repowise.core.generation.editor_files.data import (
    WorkspaceEditorFileData,
    WorkspaceRepoSummary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(
    alias: str = "backend",
    is_primary: bool = False,
    file_count: int = 42,
    symbol_count: int = 300,
    hotspot_count: int = 3,
    entry_points: list[str] | None = None,
) -> WorkspaceRepoSummary:
    return WorkspaceRepoSummary(
        alias=alias,
        is_primary=is_primary,
        file_count=file_count,
        symbol_count=symbol_count,
        hotspot_count=hotspot_count,
        entry_points=entry_points or [],
    )


def _make_data(
    workspace_name: str = "my-workspace",
    workspace_root: str = "/tmp/my-workspace",
    repos: list[WorkspaceRepoSummary] | None = None,
    default_repo: str = "backend",
    co_changes: list[dict] | None = None,
    package_deps: list[dict] | None = None,
    contract_links: list[dict] | None = None,
    contracts_by_type: dict[str, int] | None = None,
) -> WorkspaceEditorFileData:
    return WorkspaceEditorFileData(
        workspace_name=workspace_name,
        workspace_root=workspace_root,
        repos=repos if repos is not None else [_make_repo(is_primary=True)],
        default_repo=default_repo,
        co_changes=co_changes or [],
        package_deps=package_deps or [],
        contract_links=contract_links or [],
        contracts_by_type=contracts_by_type or {},
    )


@pytest.fixture
def gen() -> WorkspaceClaudeMdGenerator:
    return WorkspaceClaudeMdGenerator()


# ---------------------------------------------------------------------------
# Template rendering — basic structure
# ---------------------------------------------------------------------------


def test_render_returns_non_empty_string(gen):
    result = gen.render(_make_data())
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_contains_workspace_name(gen):
    result = gen.render(_make_data(workspace_name="acme-ws"))
    assert "acme-ws" in result


def test_render_contains_repo_alias(gen):
    repos = [_make_repo(alias="frontend")]
    result = gen.render(_make_data(repos=repos, default_repo="frontend"))
    assert "frontend" in result


def test_render_contains_repo_table_headers(gen):
    result = gen.render(_make_data())
    assert "Files" in result
    assert "Symbols" in result
    assert "Hotspots" in result


def test_render_contains_file_and_symbol_counts(gen):
    repos = [_make_repo(alias="svc", file_count=99, symbol_count=512)]
    result = gen.render(_make_data(repos=repos, default_repo="svc"))
    assert "99" in result
    assert "512" in result


def test_render_marks_default_repo(gen):
    repos = [_make_repo(alias="api"), _make_repo(alias="worker")]
    result = gen.render(_make_data(repos=repos, default_repo="api"))
    assert "default" in result


def test_render_contains_mcp_tools_section(gen):
    result = gen.render(_make_data())
    assert "get_overview" in result
    assert "get_context" in result
    assert "search_codebase" in result


def test_render_contains_workspace_query_note(gen):
    result = gen.render(_make_data())
    # The template mentions using repo="all" for workspace-wide queries
    assert 'repo="all"' in result


# ---------------------------------------------------------------------------
# Template rendering — optional sections
# ---------------------------------------------------------------------------


def test_render_skips_contract_section_when_empty(gen):
    result = gen.render(_make_data(contract_links=[]))
    assert "Cross-Repo API Contracts" not in result


def test_render_includes_contract_section_when_present(gen):
    links = [
        {
            "provider_repo": "api",
            "provider_file": "routes/users.py",
            "consumer_repo": "frontend",
            "consumer_file": "api/client.ts",
            "contract_type": "http",
            "contract_id": "GET::/api/users",
        }
    ]
    result = gen.render(_make_data(contract_links=links))
    assert "Cross-Repo API Contracts" in result
    assert "GET::/api/users" in result
    assert "api" in result
    assert "frontend" in result


def test_render_skips_co_changes_section_when_empty(gen):
    result = gen.render(_make_data(co_changes=[]))
    assert "Cross-Repo Co-Changes" not in result


def test_render_includes_co_changes_section_when_present(gen):
    cc = [
        {
            "source_repo": "api",
            "source_file": "models/user.py",
            "target_repo": "worker",
            "target_file": "tasks/sync.py",
            "frequency": 7,
        }
    ]
    result = gen.render(_make_data(co_changes=cc))
    assert "Cross-Repo Co-Changes" in result
    assert "models/user.py" in result
    assert "tasks/sync.py" in result
    assert "7" in result


def test_render_limits_co_changes_to_ten(gen):
    # Generate 15 co-change entries
    cc = [
        {
            "source_repo": "a",
            "source_file": f"file_{i}.py",
            "target_repo": "b",
            "target_file": f"other_{i}.py",
            "frequency": i,
        }
        for i in range(15)
    ]
    result = gen.render(_make_data(co_changes=cc))
    # Only the first 10 should appear (template uses co_changes[:10])
    assert result.count("source_repo") == 0  # field names not in output
    # Count occurrences of "file_" pattern — at most 10
    import re
    matches = re.findall(r"file_\d+\.py", result)
    assert len(matches) <= 10


def test_render_skips_package_deps_when_empty(gen):
    result = gen.render(_make_data(package_deps=[]))
    assert "Package Dependencies" not in result


def test_render_includes_package_deps_when_present(gen):
    deps = [{"source_repo": "frontend", "target_repo": "shared-lib", "kind": "npm_local_path"}]
    result = gen.render(_make_data(package_deps=deps))
    assert "Package Dependencies" in result
    assert "frontend" in result
    assert "shared-lib" in result
    assert "npm_local_path" in result


def test_render_includes_entry_points_per_repo(gen):
    repos = [
        _make_repo(
            alias="api",
            entry_points=["src/api/main.py", "src/api/server.py"],
        )
    ]
    result = gen.render(_make_data(repos=repos, default_repo="api"))
    assert "src/api/main.py" in result
    assert "src/api/server.py" in result


def test_render_shows_placeholder_when_no_entry_points(gen):
    repos = [_make_repo(alias="svc", entry_points=[])]
    result = gen.render(_make_data(repos=repos, default_repo="svc"))
    assert "No entry points indexed" in result


# ---------------------------------------------------------------------------
# File writing — path and directory
# ---------------------------------------------------------------------------


def test_write_creates_dot_claude_directory(gen, tmp_path):
    data = _make_data()
    gen.write(tmp_path, data)
    assert (tmp_path / ".claude").is_dir()


def test_write_creates_claude_md_at_workspace_root(gen, tmp_path):
    data = _make_data()
    written = gen.write(tmp_path, data)
    assert written == tmp_path / ".claude" / "CLAUDE.md"
    assert written.exists()


def test_write_returns_path(gen, tmp_path):
    result = gen.write(tmp_path, _make_data())
    assert result == tmp_path / ".claude" / "CLAUDE.md"


def test_write_content_contains_markers(gen, tmp_path):
    gen.write(tmp_path, _make_data())
    content = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<!-- REPOWISE:START" in content
    assert "<!-- REPOWISE:END -->" in content


def test_write_content_contains_user_placeholder_on_new_file(gen, tmp_path):
    gen.write(tmp_path, _make_data())
    content = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Workspace-level instructions" in content


def test_write_creates_parent_dirs_if_missing(gen, tmp_path):
    deep_root = tmp_path / "a" / "b" / "c"
    deep_root.mkdir(parents=True)
    written = gen.write(deep_root, _make_data())
    assert written.exists()


# ---------------------------------------------------------------------------
# Marker-based merge — preserves user content
# ---------------------------------------------------------------------------


def test_write_preserves_user_content_above_markers(gen, tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    old_managed_sentinel = "OLD_MANAGED_CONTENT_SENTINEL_ABC"
    existing = (
        "# My workspace notes\n\nDo not erase me!\n\n"
        f"{marker_start}\n{old_managed_sentinel}\n{marker_end}\n"
    )
    target.write_text(existing, encoding="utf-8")

    gen.write(tmp_path, _make_data())
    new_content = target.read_text(encoding="utf-8")

    assert "Do not erase me!" in new_content
    assert old_managed_sentinel not in new_content
    assert "my-workspace" in new_content


def test_write_appends_when_no_markers_exist(gen, tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Pre-existing file\n\nUser notes here.\n", encoding="utf-8")

    gen.write(tmp_path, _make_data())
    content = target.read_text(encoding="utf-8")

    assert "Pre-existing file" in content
    assert "User notes here." in content
    assert "<!-- REPOWISE:START" in content


def test_write_replaces_only_managed_section(gen, tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    marker_start = gen.MARKER_START_FMT.format(tag=gen.marker_tag)
    marker_end = gen.MARKER_END_FMT.format(tag=gen.marker_tag)
    old_managed_sentinel = "OLD_MANAGED_CONTENT_SENTINEL_XYZ"
    target.write_text(
        f"# Keep this\n\n{marker_start}\n{old_managed_sentinel}\n{marker_end}\n# Also keep this\n",
        encoding="utf-8",
    )

    gen.write(tmp_path, _make_data())
    content = target.read_text(encoding="utf-8")

    assert "Keep this" in content
    assert old_managed_sentinel not in content
    assert content.count("<!-- REPOWISE:START") == 1
    assert content.count("<!-- REPOWISE:END -->") == 1


def test_write_is_idempotent(gen, tmp_path):
    data = _make_data()
    gen.write(tmp_path, data)
    first = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")

    gen.write(tmp_path, data)
    second = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")

    assert first == second


# ---------------------------------------------------------------------------
# Edge cases — empty optional fields
# ---------------------------------------------------------------------------


def test_render_with_all_empty_optional_fields_does_not_crash(gen):
    data = WorkspaceEditorFileData(
        workspace_name="empty-ws",
        workspace_root="/tmp/empty-ws",
        repos=[],
        default_repo="",
        co_changes=[],
        package_deps=[],
        contract_links=[],
        contracts_by_type={},
    )
    result = gen.render(data)
    assert isinstance(result, str)
    assert "empty-ws" in result


def test_render_with_single_repo_no_cross_repo_data(gen):
    data = _make_data(
        repos=[_make_repo(alias="monolith", is_primary=True)],
        default_repo="monolith",
        co_changes=[],
        package_deps=[],
        contract_links=[],
    )
    result = gen.render(data)
    assert "monolith" in result
    assert "Cross-Repo API Contracts" not in result
    assert "Cross-Repo Co-Changes" not in result
    assert "Package Dependencies" not in result


def test_render_multiple_repos_all_appear_in_table(gen):
    repos = [
        _make_repo(alias="api", file_count=10, symbol_count=100),
        _make_repo(alias="worker", file_count=5, symbol_count=50),
        _make_repo(alias="frontend", file_count=80, symbol_count=400),
    ]
    result = gen.render(_make_data(repos=repos, default_repo="api"))
    assert "api" in result
    assert "worker" in result
    assert "frontend" in result


def test_contracts_by_type_shown_when_present(gen):
    links = [
        {
            "provider_repo": "api",
            "provider_file": "routes.py",
            "consumer_repo": "mobile",
            "consumer_file": "client.ts",
            "contract_type": "http",
            "contract_id": "GET::/v1/ping",
        }
    ]
    result = gen.render(
        _make_data(contract_links=links, contracts_by_type={"http": 3, "grpc": 1})
    )
    assert "http: 3" in result
    assert "grpc: 1" in result
