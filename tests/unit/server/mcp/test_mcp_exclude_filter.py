"""Tests for per-repo exclude_patterns filtering helpers used by MCP tools (#296, issue 5)."""

from __future__ import annotations

from types import SimpleNamespace

import pathspec
import pytest

SPEC = pathspec.PathSpec.from_lines("gitwildmatch", [".claude/", "tools/"])


# ---------------------------------------------------------------------------
# _get_exclude_spec / is_excluded
# ---------------------------------------------------------------------------


def test_get_exclude_spec_loads_from_config(tmp_path):
    from repowise.server.mcp_server._helpers import _get_exclude_spec

    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text(
        "exclude_patterns:\n  - .claude/\n  - tools/\n", encoding="utf-8"
    )

    spec = _get_exclude_spec(tmp_path)
    assert spec is not None
    assert spec.match_file(".claude/foo.py")
    assert spec.match_file("tools/build.sh")
    assert not spec.match_file("src/main.py")


def test_get_exclude_spec_returns_none_when_no_patterns(tmp_path):
    from repowise.server.mcp_server._helpers import _get_exclude_spec

    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text("embedder: minilm\n", encoding="utf-8")

    assert _get_exclude_spec(tmp_path) is None


def test_is_excluded_checks_path():
    from repowise.server.mcp_server._helpers import is_excluded

    assert is_excluded(".claude/foo.py", SPEC) is True
    assert is_excluded("src/main.py", SPEC) is False
    assert is_excluded("src/main.py", None) is False


# ---------------------------------------------------------------------------
# Shape A: ORM rows with a path attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr", ["file_path", "node_id"])
def test_filter_rows_by_attr(attr):
    from repowise.server.mcp_server._helpers import filter_rows_by_attr

    keep = SimpleNamespace(**{attr: "src/main.py"})
    drop = SimpleNamespace(**{attr: ".claude/x.py"})
    drop2 = SimpleNamespace(**{attr: "tools/build.sh"})

    assert filter_rows_by_attr([keep, drop, drop2], attr, SPEC) == [keep]
    assert filter_rows_by_attr([keep, drop], attr, None) == [keep, drop]


# ---------------------------------------------------------------------------
# Shape B: GraphNode file nodes (node_id) vs symbol nodes (file_path)
# ---------------------------------------------------------------------------


def test_filter_graph_nodes_uses_node_id_for_file_nodes():
    from repowise.server.mcp_server._helpers import filter_graph_nodes

    file_keep = SimpleNamespace(node_type="file", node_id="src/main.py", file_path=None)
    file_drop = SimpleNamespace(node_type="file", node_id=".claude/c.py", file_path=None)
    sym_keep = SimpleNamespace(node_type="symbol", node_id="src/main.py::foo", file_path="src/main.py")
    sym_drop = SimpleNamespace(node_type="symbol", node_id=".claude/c.py::bar", file_path=".claude/c.py")

    result = filter_graph_nodes([file_keep, file_drop, sym_keep, sym_drop], SPEC)
    assert result == [file_keep, sym_keep]
    assert filter_graph_nodes([file_drop], None) == [file_drop]


# ---------------------------------------------------------------------------
# Shape C: result dicts with a path key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["target_path", "file_path", "file"])
def test_filter_dicts_by_key(key):
    from repowise.server.mcp_server._helpers import filter_dicts_by_key

    keep = {key: "src/main.py", "v": 1}
    drop = {key: ".claude/c.py", "v": 2}

    assert filter_dicts_by_key([keep, drop], key, SPEC) == [keep]
    assert filter_dicts_by_key([keep, drop], key, None) == [keep, drop]


# ---------------------------------------------------------------------------
# Shape D: nested path-list of strings
# ---------------------------------------------------------------------------


def test_filter_path_list():
    from repowise.server.mcp_server._helpers import filter_path_list

    assert filter_path_list(["src/a.py", ".claude/c.py", "tools/b.sh"], SPEC) == ["src/a.py"]
    assert filter_path_list(["src/a.py", ".claude/c.py"], None) == ["src/a.py", ".claude/c.py"]
    assert filter_path_list(None, SPEC) == []


# ---------------------------------------------------------------------------
# Shape E: node IDs that embed a path ("path::Name")
# ---------------------------------------------------------------------------


def test_filter_embedded_path_ids():
    from repowise.server.mcp_server._helpers import filter_embedded_path_ids

    ids = ["src/main.py::foo", ".claude/c.py::bar", "tools/b.sh::baz"]
    assert filter_embedded_path_ids(ids, SPEC) == ["src/main.py::foo"]
    assert filter_embedded_path_ids(ids, None) == ids


# ---------------------------------------------------------------------------
# End-to-end: a real tool honors exclude_patterns from repo config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_health_excludes_configured_paths(setup_mcp, health_data, tmp_path, monkeypatch):
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_health

    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text("exclude_patterns:\n  - src/db/\n", encoding="utf-8")
    monkeypatch.setattr(mcp_mod._state, "_repo_path", str(tmp_path))

    result = await get_health()
    paths = {m["file_path"] for m in result.get("worst_files", [])}
    assert "src/db/models.py" not in paths
    assert "src/auth/service.py" in paths


# ---------------------------------------------------------------------------
# Targeted traversal-leak tests (edges / paths / co-change), not just helpers
# ---------------------------------------------------------------------------


def _set_excluded(monkeypatch, tmp_path, pattern: str) -> None:
    import repowise.server.mcp_server as mcp_mod

    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text(f"exclude_patterns:\n  - {pattern}\n", encoding="utf-8")
    monkeypatch.setattr(mcp_mod._state, "_repo_path", str(tmp_path))


@pytest.mark.asyncio
async def test_dependency_path_excludes_intermediate(setup_mcp, tmp_path, monkeypatch):
    """middleware.py -> models.py only routes through service.py; excluding
    service.py must not surface it as an intermediate."""
    import json

    from repowise.server.mcp_server.tool_dependency import get_dependency_path

    _set_excluded(monkeypatch, tmp_path, "src/auth/service.py")
    result = await get_dependency_path("src/auth/middleware.py", "src/db/models.py")
    assert "src/auth/service.py" not in json.dumps(result)


@pytest.mark.asyncio
async def test_risk_co_change_partners_exclude_filtered(setup_mcp, tmp_path, monkeypatch):
    """service.py's co-change partners include models.py; excluding src/db/
    must drop it from co_change_partners."""
    from repowise.server.mcp_server import get_risk

    _set_excluded(monkeypatch, tmp_path, "src/db/")
    result = await get_risk(["src/auth/service.py"])
    target = result["targets"]["src/auth/service.py"]
    partner_paths = {p["file_path"] for p in target.get("co_change_partners", [])}
    assert "src/db/models.py" not in partner_paths


@pytest.mark.asyncio
async def test_flows_trace_excludes_downstream(setup_mcp, tmp_path, monkeypatch):
    """A non-excluded entry can reach excluded files downstream; those must not
    appear in any trace."""
    import json

    from repowise.server.mcp_server.tool_flows import get_execution_flows

    _set_excluded(monkeypatch, tmp_path, "src/db/")
    result = await get_execution_flows()
    assert "src/db/models.py" not in json.dumps(result.get("flows", []))


@pytest.mark.asyncio
async def test_risk_raw_pr_blast_radius_excludes_filtered(setup_mcp, tmp_path, monkeypatch):
    """The raw pr_blast_radius payload (not just the directive lists) must not
    carry excluded paths."""
    import json

    from repowise.server.mcp_server import get_risk

    args = (["src/auth/service.py"],)
    kwargs = {"changed_files": ["src/auth/service.py"]}

    # Baseline (no exclude config) — confirm the path is present, so the test
    # below is not vacuous.
    baseline = await get_risk(*args, **kwargs)
    assert "src/db/models.py" in json.dumps(baseline.get("pr_blast_radius", {}))

    _set_excluded(monkeypatch, tmp_path, "src/db/")
    result = await get_risk(*args, **kwargs)
    assert "src/db/models.py" not in json.dumps(result.get("pr_blast_radius", {}))
