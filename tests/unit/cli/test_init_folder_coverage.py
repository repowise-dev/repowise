"""``--folder-coverage`` flag wiring for ``repowise init`` (issue #633)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from click.testing import CliRunner

from repowise.cli.commands.init_cmd import _generate
from repowise.cli.main import cli


def test_init_help_lists_folder_coverage() -> None:
    result = CliRunner().invoke(cli, ["init", "--help"])
    assert result.exit_code == 0
    assert "--folder-coverage" in result.output


def test_malformed_rule_is_a_usage_error(tmp_path: Any) -> None:
    result = CliRunner().invoke(cli, ["init", str(tmp_path), "--folder-coverage", "src/core"])
    assert result.exit_code == 2
    assert "glob=pct" in result.output
    assert not (tmp_path / ".repowise").exists()


def test_flag_rules_override_config_in_generation_config(tmp_path: Any, monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(_generate, "load_config", lambda _p: {"folder_coverage": ["lib=0.5"]})
    monkeypatch.setattr(_generate, "run_repo_generation", lambda **kw: captured.update(kw))

    def run(rules: tuple[str, ...]) -> Any:
        _generate._run_deterministic_generation_phase(
            repo_path=tmp_path,
            result=SimpleNamespace(repo_name="demo"),
            total_phases=3,
            concurrency=1,
            language="en",
            onboarding=True,
            wiki_style="comprehensive",
            max_file_pages=None,
            embedder_name_resolved="mock",
            embedder_was_requested=False,
            resume=False,
            folder_coverage_rules=rules,
        )
        return captured["gen_config"].folder_coverage

    assert run(("src/core=1.0",)) == (("src/core", 1.0),)
    assert run(()) == (("lib", 0.5),)


def test_config_round_trip_persists_rules(tmp_path: Any) -> None:
    """save_config_partial must record folder_coverage as a list of strings."""
    import yaml

    from repowise.cli.helpers import save_config_partial

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()

    save_config_partial(repo, folder_coverage=["src/core=1.0"])

    cfg = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text())
    assert cfg.get("folder_coverage") == ["src/core=1.0"]
