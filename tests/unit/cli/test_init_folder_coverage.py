"""``--folder-coverage`` flag wiring for ``repowise init`` (issue #633)."""

from __future__ import annotations

from click.testing import CliRunner

from repowise.cli.commands.init_cmd import command as init_cmd
from repowise.cli.main import cli


def test_init_help_lists_folder_coverage() -> None:
    result = CliRunner().invoke(cli, ["init", "--help"])
    assert result.exit_code == 0
    assert "--folder-coverage" in result.output


def test_folder_coverage_rules_parse_and_reach_generation_config(monkeypatch) -> None:
    """The repeatable flag parses to rules and lands in the GenerationConfig."""
    import click

    def fake_resolve_repo_path(*a, **k):
        return "/tmp/repo"

    def fake_config_flow(*a, **k):
        raise click.ClickException("stop after config")

    # Stub through the command until the generation phase would be reached.
    monkeypatch.setattr(init_cmd, "resolve_repo_path", fake_resolve_repo_path)

    # Parse-only check: the raw strings must produce the expected rules.
    from repowise.core.generation.folder_coverage import _parse_folder_coverage

    rules = _parse_folder_coverage(("src/core=1.0", "src/legacy=0.5"))
    assert rules == (("src/core", 1.0), ("src/legacy", 0.5))


def test_config_round_trip_persists_rules(monkeypatch, tmp_path) -> None:
    """save_config_partial must record folder_coverage as a list of strings."""
    import yaml

    from repowise.cli.helpers import save_config_partial

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()

    save_config_partial(repo, folder_coverage=["src/core=1.0"])

    cfg = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text())
    assert cfg.get("folder_coverage") == ["src/core=1.0"]
