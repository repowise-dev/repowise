"""Doctor check ids, levels and fixes.

A server and a web Setup page key on ``id``, so the set of ids is pinned here:
renaming a display name must not move an id, and a changed id is a breaking
change someone has to make on purpose.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import doctor_cmd
from repowise.cli.commands.doctor_cmd import collect_repo_checks, doctor_command, repo_checks
from repowise.cli.commands.doctor_cmd._checks import integrations, providers
from repowise.cli.commands.doctor_cmd._types import _check

# "*" stands for a per-target or per-language suffix.
PINNED_IDS = {
    "agent-*",
    "agent-hook-scripts",
    "agent-hooks-firing",
    "agent-integrations",
    "attribution-notes",
    "claude-code-mcp-entry",
    "claude-desktop-mcp-entry",
    "coordinator-drift",
    "database",
    "distill-config",
    "distill-rewrite-hook",
    "events-config",
    "fts-index-sync",
    "git-repository",
    "grammar-*",
    "hosted-account",
    "llm-provider",
    "mcp-responds",
    "omission-store",
    "post-commit-hook",
    "provider-config",
    "providers",
    "repowise-dir",
    "stale-pages",
    "state-json",
    "store-consistency",
    "store-format",
    "vector-store",
    "vector-store-sync",
}

_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_ID_NAMES = {"check_id", "CHECK_ID"}
_PACKAGE = Path(doctor_cmd.__file__).parent


def _id_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        head = node.values[0]
        assert isinstance(head, ast.Constant), "an f-string id must start with a literal"
        return f"{head.value}*"
    return None


def _bound_pairs(target: ast.expr, value: ast.expr) -> list[tuple[ast.expr, ast.expr]]:
    if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
        return list(zip(target.elts, value.elts, strict=True))
    return [(target, value)]


def _node_ids(node: ast.AST) -> list[str | None]:
    if isinstance(node, ast.keyword) and node.arg == "check_id":
        return [_id_value(node.value)]
    if not isinstance(node, ast.Assign):
        return []
    pairs = [pair for target in node.targets for pair in _bound_pairs(target, node.value)]
    return [
        _id_value(value)
        for name, value in pairs
        if isinstance(name, ast.Name) and name.id in _ID_NAMES
    ]


def _ids_in(tree: ast.AST) -> set[str]:
    """Every literal id: ``check_id=`` arguments and ``check_id``/``CHECK_ID`` assignments.

    A keyword passing a variable through yields None here; the assignment
    that bound it is what carries the literal.
    """
    return {i for node in ast.walk(tree) for i in _node_ids(node) if i}


def _source_trees() -> list[ast.AST]:
    return [ast.parse(p.read_text(encoding="utf-8")) for p in sorted(_PACKAGE.rglob("*.py"))]


def test_check_ids_are_pinned() -> None:
    ids: set[str] = set()
    for tree in _source_trees():
        ids |= _ids_in(tree)
    assert ids == PINNED_IDS


def test_check_ids_are_kebab_case() -> None:
    for check_id in PINNED_IDS:
        assert _KEBAB.match(check_id.removesuffix("-*")), check_id


def test_every_check_call_names_an_id() -> None:
    for tree in _source_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_check"
            ):
                assert any(k.arg == "check_id" for k in node.keywords), ast.unparse(node)


def test_agent_row_ids_are_kebab_case() -> None:
    from repowise.cli.agent_targets.registry import all_targets

    for target in all_targets():
        assert _KEBAB.match(f"agent-{target.id}"), target.id


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


@pytest.fixture
def no_mcp_launch(monkeypatch):
    """Keep the run off the machine's real MCP registration and hosted account."""
    integrations._mcp_responds_check.cache_clear()
    monkeypatch.setattr(integrations, "_registered_mcp_entry", lambda: None)
    monkeypatch.setattr(
        providers,
        "_hosted_account_check",
        lambda: _check("Hosted account", True, "", check_id="hosted-account"),
    )
    yield
    integrations._mcp_responds_check.cache_clear()


def test_collected_rows_have_unique_ids_and_consistent_levels(
    tmp_path: Path, no_mcp_launch
) -> None:
    checks = collect_repo_checks(_git_repo(tmp_path))
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids))
    for c in checks:
        assert _KEBAB.match(c.id), c
        assert c.level in ("ok", "warn", "fail")
        assert c.ok is (c.level != "fail")


def test_missing_post_commit_hook_is_a_warn_with_the_install_command(tmp_path: Path) -> None:
    row = repo_checks._autosync_hook_check(_git_repo(tmp_path))
    assert (row.id, row.level, row.ok) == ("post-commit-hook", "warn", True)
    assert row.fix == "repowise hook install"


def test_installed_post_commit_hook_is_ok(tmp_path: Path) -> None:
    from repowise.cli import hooks

    repo = _git_repo(tmp_path)
    hooks.install(repo)
    row = repo_checks._autosync_hook_check(repo)
    assert (row.level, row.fix) == ("ok", None)


def test_missing_repowise_dir_is_a_fail_with_the_init_command(tmp_path: Path) -> None:
    row = repo_checks._repowise_dir_check(tmp_path / ".repowise")
    assert (row.id, row.level, row.ok) == ("repowise-dir", "fail", False)
    assert row.fix == "repowise init"


def test_registration_serving_another_repo_is_a_warn(tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    entry = {"command": "repowise", "args": ["mcp", str(other), "--transport", "stdio"]}
    monkeypatch.setattr(integrations, "_registered_mcp_entry", lambda: entry)
    monkeypatch.setattr(integrations, "_registers_repo", lambda _t, _r: False)
    row, wedged = integrations._claude_registration_check(tmp_path)
    assert (row.id, row.level, wedged) == ("claude-code-mcp-entry", "warn", False)
    assert row.fix == "repowise agents add --target=claude-code --scope=project"


@pytest.mark.parametrize(
    ("settings", "level"), [("{not json", "warn"), (None, "ok")], ids=["unparseable", "absent"]
)
def test_unregistered_entry_warns_only_when_settings_cannot_be_parsed(
    tmp_path: Path, monkeypatch, settings: str | None, level: str
) -> None:
    from repowise.cli.editor_integrations import claude_config

    settings_path = tmp_path / ".claude.json"
    if settings is not None:
        settings_path.write_text(settings, encoding="utf-8")
    monkeypatch.setattr(claude_config, "_claude_code_user_config_path", lambda: settings_path)
    monkeypatch.setattr(integrations, "_registered_mcp_entry", lambda: None)
    row, wedged = integrations._claude_registration_check(tmp_path)
    assert (row.id, row.level, row.ok, wedged) == ("claude-code-mcp-entry", level, True, False)


def test_json_rows_carry_id_level_and_fix(tmp_path: Path, no_mcp_launch) -> None:
    result = CliRunner().invoke(
        doctor_command, [str(_git_repo(tmp_path)), "--no-workspace", "--format", "json"]
    )
    payload = json.loads(result.output[result.output.index("{") :])
    assert set(payload) == {"ok", "checks"}
    for c in payload["checks"]:
        assert set(c) == {"id", "name", "ok", "level", "detail", "fix"}
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["repowise-dir"]["level"] == "fail"
    assert by_id["repowise-dir"]["fix"] == "repowise init"
    assert by_id["post-commit-hook"]["level"] == "warn"
    assert by_id["git-repository"] == {
        "id": "git-repository",
        "name": "Git repository",
        "ok": True,
        "level": "ok",
        "detail": str(tmp_path),
        "fix": None,
    }
