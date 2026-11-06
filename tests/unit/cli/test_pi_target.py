"""Pi's on-disk protocol and the CLI paths that install it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.agent_targets import registry
from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
from repowise.cli.agent_targets.targets import pi
from repowise.cli.agent_targets.types import DoctorStatus, FileAction, Scope
from repowise.cli.main import cli
from repowise.core.agents.identity import resolve_client_identity


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.chdir(path)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    return path


def _run(*args: str) -> dict:
    result = CliRunner().invoke(cli, ["agents", *args, "--format=json"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.parametrize("scope", ["project", "user", "both"])
def test_pi_cli_installs_in_the_hosts_paths_and_removes_both_scopes(repo: Path, scope: str) -> None:
    result = _run("add", "--target=pi", f"--scope={scope}", "--yes")
    assert result["changed"]
    scopes = {Scope.PROJECT, Scope.USER} if scope == "both" else {Scope(scope)}
    expected_paths = {
        Scope.PROJECT: (repo / ".pi" / "mcp.json", repo / "AGENTS.md"),
        Scope.USER: (
            Path.home() / ".pi" / "agent" / "mcp.json",
            Path.home() / ".pi" / "agent" / "AGENTS.md",
        ),
    }
    for installed in scopes:
        config, instructions = expected_paths[installed]
        entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["repowise"]
        if installed is Scope.PROJECT:
            assert entry == {
                "command": "repowise",
                "args": [
                    "mcp",
                    repo.resolve().as_posix(),
                    "--transport",
                    "stdio",
                ],
            }
        else:
            assert Path(entry["command"]).is_absolute()
            assert entry["args"] == ["mcp", "--transport", "stdio"]
        assert DISTILL_MARKER_START in instructions.read_text(encoding="utf-8")
    assert {r.scope for r in pi.TARGET.detect(repo)} == scopes
    assert not _run("add", "--target=pi", f"--scope={scope}", "--yes")["changed"]
    assert [t.id for t in registry.resolve_target_flag("auto", repo)] == ["pi"]
    assert resolve_client_identity("pi") == "pi"
    assert pi.TARGET.doctor(repo).status is DoctorStatus.OK
    _run("remove", "--target=pi", f"--scope={scope}")
    assert pi.TARGET.detect(repo) == []
    for installed in scopes:
        config, instructions = expected_paths[installed]
        assert not config.exists()
        assert not instructions.exists()


@pytest.mark.parametrize("configured", ["~/custom-pi", "", "relative-pi"])
def test_pi_user_directory_follows_the_host_override(
    repo: Path, monkeypatch, configured: str
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", configured)
    expected = Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"
    pi.TARGET.install(Scope.USER)
    assert (expected / "mcp.json").exists()
    assert (expected / "AGENTS.md").exists()
    assert pi.TARGET.describe_paths(Scope.USER) == [
        str(expected / "mcp.json"),
        str(expected / "AGENTS.md"),
    ]
    assert pi.mcp_config_path(Scope.PROJECT, repo) == repo / ".pi" / "mcp.json"
    assert pi.TARGET.detect()[0].config_path == expected / "mcp.json"
    pi.TARGET.uninstall(Scope.USER)
    assert not (expected / "mcp.json").exists()


@pytest.mark.parametrize("scope", list(Scope))
def test_pi_refresh_keeps_other_servers_and_user_options(repo: Path, scope: Scope) -> None:
    path = pi.mcp_config_path(scope, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    options = {
        "env": {"LOCAL_KEY": "kept"},
        "enabled": False,
        "exposure": "deferred",
        "toolExposure": {"get_context": "direct"},
        "timeout": 90,
    }
    other = {"command": "other-server", "args": []}
    path.write_text(
        json.dumps(
            {
                "custom": True,
                "mcpServers": {
                    "other": other,
                    "repowise": {"command": "old", **options},
                },
            }
        ),
        encoding="utf-8",
    )
    pi.TARGET.install(scope, repo_path=repo)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["custom"] is True
    assert data["mcpServers"]["other"] == other
    entry = data["mcpServers"]["repowise"]
    assert entry["command"] != "old"
    assert {key: entry[key] for key in options} == options
    pi.TARGET.uninstall(scope, repo_path=repo)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "custom": True,
        "mcpServers": {"other": other},
    }


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b'{"mcpServers":[]}', b"\xff"])
def test_pi_does_not_rewrite_unreadable_configs(repo: Path, raw: bytes) -> None:
    path = repo / ".pi" / "mcp.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    result = pi.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert result.files[0].action is FileAction.KEPT
    assert result.files[0].reason
    assert path.read_bytes() == raw
    assert pi.TARGET.detect(repo) == []
    assert pi.TARGET.doctor(repo).status is DoctorStatus.BROKEN
    pi.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("extra", [{}, {"type": "http"}, {"type": "stdio", "command": "old"}])
def test_pi_keeps_a_remote_registration(repo: Path, extra: dict) -> None:
    path = repo / ".pi" / "mcp.json"
    path.parent.mkdir()
    raw = json.dumps({"mcpServers": {"repowise": {"url": "https://example.com/mcp", **extra}}})
    path.write_text(raw, encoding="utf-8")
    result = pi.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert result.files[0].action is FileAction.KEPT
    assert "remote" in result.files[0].reason
    assert path.read_text(encoding="utf-8") == raw
    assert pi.TARGET.detect(repo)[0].scope is Scope.PROJECT


@pytest.mark.parametrize("removed,remaining", [("pi", "opencode"), ("opencode", "pi")])
def test_pi_and_other_agents_keep_shared_instructions_until_both_are_removed(
    repo: Path,
    removed: str,
    remaining: str,
) -> None:
    path = repo / "AGENTS.md"
    original = "# Project rules\n\nKeep my instructions.\n"
    path.write_text(original, encoding="utf-8")
    for target in (removed, remaining):
        registry.get_target(target).install(Scope.PROJECT, repo_path=repo)
    result = registry.get_target(removed).uninstall(Scope.PROJECT, repo_path=repo)
    assert any(f.action is FileAction.KEPT and f.path == path for f in result.files)
    assert DISTILL_MARKER_START in path.read_text(encoding="utf-8")
    registry.get_target(remaining).uninstall(Scope.PROJECT, repo_path=repo)
    assert path.read_text(encoding="utf-8") == original


def test_pi_refuses_unpaired_instruction_markers(repo: Path) -> None:
    path = repo / "AGENTS.md"
    raw = f"# My rules\n{DISTILL_MARKER_START}\nKeep this.\n"
    path.write_text(raw, encoding="utf-8")
    result = pi.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert result.files[1].action is FileAction.KEPT
    assert result.files[1].reason
    pi.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert path.read_text(encoding="utf-8") == raw


def test_pi_reports_failed_config_removal(repo: Path, monkeypatch) -> None:
    pi.TARGET.install(Scope.PROJECT, repo_path=repo)

    def fail(_path):
        raise OSError("read only")

    monkeypatch.setattr("repowise.cli.agent_targets.targets.cursor._remove_server_entry", fail)
    result = pi.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert result.files[0].action is FileAction.FAILED
    assert (repo / ".pi" / "mcp.json").exists()


def test_auto_finds_a_fresh_pi_install(repo: Path, monkeypatch) -> None:
    for target in registry.all_targets():
        if target.id != "pi":
            monkeypatch.setattr(target, "is_present", lambda _repo=None: False)
    monkeypatch.setattr(
        "repowise.core.agents.identity.shutil.which",
        lambda name: "/bin/pi" if name == "pi" else None,
    )
    assert pi.TARGET.detect(repo) == []
    assert pi.TARGET.is_present(repo)
    result = _run("add", "--target=auto", "--scope=project", "--yes")
    assert [row["id"] for row in result["agents"]] == ["pi"]
    assert (repo / ".pi" / "mcp.json").exists()


def test_auto_keeps_existing_registrations_ahead_of_unwired_installs(
    repo: Path, monkeypatch
) -> None:
    registry.get_target("opencode").install(Scope.PROJECT, repo_path=repo)
    monkeypatch.setattr(pi.TARGET, "is_present", lambda _repo=None: True)
    assert [target.id for target in registry.resolve_target_flag("auto", repo)] == ["opencode"]


def test_auto_skips_an_unreadable_install_probe(repo: Path, monkeypatch) -> None:
    def fail(_repo=None):
        raise OSError("unreadable config directory")

    for target in registry.all_targets():
        monkeypatch.setattr(target, "is_present", fail)
    monkeypatch.setattr(pi.TARGET, "is_present", lambda _repo=None: True)
    assert [target.id for target in registry.resolve_target_flag("auto", repo)] == ["pi"]


def test_pi_presence_probe_does_not_keep_its_own_install_residue(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr("repowise.core.agents.identity.shutil.which", lambda _name: None)
    assert not pi.TARGET.is_present(repo)
    pi.TARGET.install(Scope.USER)
    assert pi.TARGET.is_present(repo)
    pi.TARGET.uninstall(Scope.USER)
    assert not pi.TARGET.is_present(repo)
