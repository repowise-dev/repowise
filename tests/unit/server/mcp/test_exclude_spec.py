"""_get_exclude_spec must union config excludes with the gitignore stack.

Indexes built before the traverser honoured ``.git/info/exclude`` still
contain rows for local-only scratch dirs; the query-time spec keeps those
paths out of MCP tool responses without forcing a reindex.
"""

from __future__ import annotations

from repowise.server.mcp_server._helpers import _get_exclude_spec, is_excluded


def test_unions_gitignore_and_info_exclude(tmp_path):
    (tmp_path / ".gitignore").write_text("dist/\n")
    info = tmp_path / ".git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("local-stash/\n")

    spec = _get_exclude_spec(tmp_path)
    assert spec is not None
    assert is_excluded("local-stash/bench/probe.py", spec)
    assert is_excluded("dist/bundle.js", spec)
    assert not is_excluded("src/app.py", spec)


def test_no_patterns_returns_none(tmp_path):
    assert _get_exclude_spec(tmp_path) is None
