"""Picking a workspace's repos without prompts: ``init --repos`` and the all-repos line."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from repowise.cli.commands.init_cmd import _workspace_repos
from repowise.cli.commands.init_cmd._workspace_repos import (
    all_repos_line,
    repos_primary_line,
    select_workspace_repos,
    split_repo_names,
)
from repowise.cli.ui.workspace_selection import match_repo_names

ROOT = Path("/ws")


def _repo(rel: str, alias: str | None = None) -> SimpleNamespace:
    path = ROOT / rel
    return SimpleNamespace(path=path, name=path.name, alias=alias or path.name)


@pytest.fixture
def repos() -> list[SimpleNamespace]:
    return [_repo("alpha"), _repo("beta"), _repo("gamma")]


def test_names_match_alias_in_the_order_given(repos):
    picked = match_repo_names(repos, ["gamma", "Alpha"], ROOT)
    assert [r.alias for r in picked] == ["gamma", "alpha"]


def test_a_repeated_name_is_taken_once(repos):
    assert len(match_repo_names(repos, ["beta", "beta"], ROOT)) == 1


def test_unknown_name_lists_every_available_repo(repos):
    with pytest.raises(ValueError) as exc:
        match_repo_names(repos, ["alpha", "nope", "zap"], ROOT)
    msg = str(exc.value)
    assert "Unknown repo: nope, zap." in msg
    assert "Available: alpha, beta, gamma." in msg


def test_a_shared_name_is_ambiguous_and_the_path_settles_it():
    repos = [_repo("svc/api"), _repo("web/api", alias="api-2")]
    with pytest.raises(ValueError, match=r"'api' matches 2 repos \(svc/api, web/api\)"):
        match_repo_names(repos, ["api"], ROOT)
    assert match_repo_names(repos, ["web/api"], ROOT) == [repos[1]]
    assert match_repo_names(repos, ["svc\\api\\"], ROOT) == [repos[0]]
    assert match_repo_names(repos, ["api-2"], ROOT) == [repos[1]]


def test_split_repo_names_drops_blanks():
    assert split_repo_names(" a, ,c ,") == ["a", "c"]
    assert split_repo_names(None) == []


def test_repos_flag_skips_the_picker_and_makes_the_first_primary(repos, monkeypatch):
    def no_prompt(*_a, **_k):
        raise AssertionError("--repos must not prompt")

    monkeypatch.setattr(_workspace_repos, "interactive_repo_select", no_prompt)
    monkeypatch.setattr(_workspace_repos, "interactive_primary_select", no_prompt)
    scan = SimpleNamespace(root=ROOT, repos=repos)

    pick = select_workspace_repos(scan, repo_names=["gamma", "alpha"], select_all=True)

    assert [r.alias for r in pick.selected] == ["gamma", "alpha"]
    assert pick.primary_alias == "gamma"
    assert pick.primary_repo is repos[2]
    # An existing workspace keeps its primary; a new one takes the first listed.
    assert pick.primary_picked is False


def test_repos_primary_line_says_where_the_primary_came_from(repos):
    pick = select_workspace_repos(
        SimpleNamespace(root=ROOT, repos=repos), repo_names=["gamma", "beta"], select_all=False
    )
    kept = repos_primary_line("beta", pick)
    assert "Primary: [bold]beta[/bold] (kept from the workspace)" in kept
    first = repos_primary_line("gamma", pick)
    assert "Primary: [bold]gamma[/bold] (first in --repos)" in first
    assert "(first in --repos)" in repos_primary_line(None, pick)


def test_repos_flag_unknown_name_is_a_usage_error(repos):
    scan = SimpleNamespace(root=ROOT, repos=repos)
    with pytest.raises(click.UsageError, match="--repos: Unknown repo: delta"):
        select_workspace_repos(scan, repo_names=["delta"], select_all=False)


def test_yes_takes_every_repo_and_says_which(repos, capsys):
    scan = SimpleNamespace(root=ROOT, repos=repos)
    pick = select_workspace_repos(scan, repo_names=[], select_all=True)

    assert pick.selected == repos
    assert pick.primary_alias == "alpha"
    out = capsys.readouterr().out
    assert "Indexing all 3 repos: alpha, beta, gamma." in out
    assert "Pick some with --repos alpha,beta" in out


def test_all_repos_line_truncates_a_long_list():
    repos = [_repo(f"r{i:02d}") for i in range(13)]
    line = all_repos_line(repos)
    assert "Indexing all 13 repos: r00, r01" in line
    assert "r09 and 3 more." in line
    assert "r10" not in line


def test_repos_on_a_single_repo_is_a_usage_error(tmp_path):
    from click.testing import CliRunner

    from repowise.cli.commands.init_cmd import init_command

    (tmp_path / ".git").mkdir()
    result = CliRunner().invoke(init_command, [str(tmp_path), "--repos", "x", "--no-editor-setup"])
    assert result.exit_code == 2
    assert "--repos picks repos in a folder that holds several" in result.output


def test_repos_with_all_is_a_usage_error(tmp_path):
    from click.testing import CliRunner

    from repowise.cli.commands.init_cmd import init_command

    result = CliRunner().invoke(init_command, [str(tmp_path), "--repos", "x", "--all"])
    assert result.exit_code == 2
    assert "--repos and --all contradict each other" in result.output


@pytest.mark.parametrize("value", ["", ",", " , "])
def test_repos_with_no_names_is_a_usage_error(tmp_path, value):
    from click.testing import CliRunner

    from repowise.cli.commands.init_cmd import init_command

    for name in ("repo-a", "repo-b"):
        (tmp_path / name / ".git").mkdir(parents=True)
    result = CliRunner().invoke(
        init_command, [str(tmp_path), "--yes", "--no-editor-setup", "--repos", value]
    )
    assert result.exit_code == 2
    assert "--repos needs at least one repo name" in result.output


def test_a_path_to_a_repo_folder_matches_it(tmp_path, monkeypatch):
    repos = [SimpleNamespace(path=tmp_path / n, name=n, alias=n) for n in ("repo-a", "repo-b")]
    monkeypatch.chdir(tmp_path)
    assert match_repo_names(repos, ["./repo-b"], tmp_path) == [repos[1]]
    assert match_repo_names(repos, [str(tmp_path / "repo-a")], tmp_path) == [repos[0]]
    with pytest.raises(ValueError, match=r"Unknown repo: \./nope"):
        match_repo_names(repos, ["./nope"], tmp_path)


def test_a_relative_path_is_read_from_the_workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    repos = [SimpleNamespace(path=root / n, name=n, alias=n) for n in ("repo-a", "repo-b")]
    for repo in repos:
        repo.path.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert match_repo_names(repos, ["./repo-b"], root) == [repos[1]]


def test_a_path_that_cannot_be_resolved_matches_nothing(tmp_path, monkeypatch):
    from repowise.cli.ui import workspace_selection

    def _fail(self, *a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(workspace_selection.Path, "resolve", _fail)
    assert workspace_selection._resolved(tmp_path) is None
