"""Regression tests for get_overview payload quality:

- the git-attributed file count is labelled distinctly (not "total_files"),
- the knowledge map emits a contributor display name, never the raw email.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server.tool_overview import (
    _build_git_health,
    _build_knowledge_map,
    _owner_display_name,
)


def _git_row(**kw) -> SimpleNamespace:
    base = {
        "file_path": "src/app.py",
        "is_hotspot": False,
        "bus_factor": 2,
        "commit_count_30d": 1,
        "commit_count_90d": 3,
        "primary_owner_email": "jane.doe@example.com",
        "primary_owner_name": "Jane Doe",
        "primary_owner_commit_pct": 0.5,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# --- file-count labelling --------------------------------------------------


def test_git_health_labels_git_attributed_count():
    rows = [_git_row(file_path="a.py"), _git_row(file_path="b.py")]
    out = _build_git_health(rows)
    assert out["files_git_attributed"] == 2
    # The ambiguous name is gone so it can't read as a parsed-file total.
    assert "total_files_indexed" not in out


# --- contributor-email privacy ---------------------------------------------


def test_knowledge_map_emits_name_not_email():
    rows = [_git_row(file_path=f"f{i}.py") for i in range(3)]
    out = _build_knowledge_map(rows)
    owners = out["top_owners"]
    assert owners
    top = owners[0]
    assert top["name"] == "Jane Doe"
    assert "email" not in top
    # The raw address never appears anywhere in the owner payload.
    assert "jane.doe@example.com" not in str(owners)


def test_owner_display_name_derives_from_email_when_name_missing():
    # No recorded name → conservative local-part label, never the raw email.
    assert _owner_display_name(None, "jane.doe@example.com") == "jane.doe"
    assert _owner_display_name("", "jane.doe@example.com") == "jane.doe"
    assert _owner_display_name("Jane Doe", "jane.doe@example.com") == "Jane Doe"


def test_knowledge_map_falls_back_to_local_part():
    rows = [_git_row(primary_owner_name=None, primary_owner_email="bob@corp.io")]
    out = _build_knowledge_map(rows)
    assert out["top_owners"][0]["name"] == "bob"
    assert "bob@corp.io" not in str(out["top_owners"])
