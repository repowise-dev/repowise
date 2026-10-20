"""Regression tests for get_overview payload quality:

- git health names the riskiest files in the editor files' order,
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


# --- riskiest files --------------------------------------------------------


def test_git_health_names_the_riskiest_files_bug_magnets_first(tmp_path):
    rows = [
        _git_row(file_path="churny.py", is_hotspot=True, churn_percentile=0.99),
        _git_row(file_path="fixed.py", is_hotspot=True, fix_mass=4.0, churn_percentile=0.5),
        _git_row(file_path="magnet.py", bug_magnet=True, fix_mass=1.0),
        _git_row(file_path="gone.py", bug_magnet=True, fix_mass=9.0),
        _git_row(file_path="quiet.py"),
    ]
    for name in ("churny.py", "fixed.py", "magnet.py", "quiet.py"):
        (tmp_path / name).write_text("")

    out = _build_git_health(rows, tmp_path)

    # A row that outlived its file is not named.
    assert out["riskiest_files"] == ["magnet.py", "fixed.py", "churny.py"]
    assert out["hotspot_count"] == 2
    assert "files_git_attributed" not in out


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


def test_owner_display_name_rejects_email_shaped_name():
    # Git author name is commonly itself an email (bot/CI commits, misconfigured
    # user.name); it must NOT be surfaced — fall through to the local part.
    assert _owner_display_name("bob@corp.io", "bob@corp.io") == "bob"
    assert _owner_display_name("ci-bot@ci.internal", "bob@corp.io") == "bob"


def test_knowledge_map_rejects_email_shaped_name():
    rows = [_git_row(primary_owner_name="bob@corp.io", primary_owner_email="bob@corp.io")]
    out = _build_knowledge_map(rows)
    assert out["top_owners"][0]["name"] == "bob"
    assert "bob@corp.io" not in str(out["top_owners"])


def test_knowledge_map_falls_back_to_local_part():
    rows = [_git_row(primary_owner_name=None, primary_owner_email="bob@corp.io")]
    out = _build_knowledge_map(rows)
    assert out["top_owners"][0]["name"] == "bob"
    assert "bob@corp.io" not in str(out["top_owners"])
