"""Which forge a checkout is on: its remote, host overrides, then CI env."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.forges import (
    HOSTS_ENV_VAR,
    ForgeKind,
    detect_forge,
    forge_hosts,
    read_remote_url,
)


def _checkout(root: Path, config: str) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text(config, encoding="utf-8")
    return root


def _origin(url: str, name: str = "origin") -> str:
    # Duplicate keys and a `%` are both legal in a git config.
    return (
        f'[remote "{name}"]\n\turl = {url}\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
        "\tfetch = +refs/pull/*:refs/remotes/origin/pr/*\n"
    )


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("git@github.com:o/r.git", ForgeKind.GITHUB),
        ("https://gitlab.com/g/sub/p.git", ForgeKind.GITLAB),
        ("https://user%40corp.com@dev.azure.com/org/proj/_git/repo", ForgeKind.AZURE),
        ("git@bitbucket.org:ws/repo.git", ForgeKind.BITBUCKET),
        ("https://gitea.example.org/t/r.git", ForgeKind.GENERIC),
    ],
)
def test_the_remote_names_the_forge(tmp_path: Path, url: str, kind: ForgeKind) -> None:
    root = _checkout(tmp_path, _origin(url))
    assert detect_forge(root, env={}) is kind


def test_upstream_is_read_when_there_is_no_origin(tmp_path: Path) -> None:
    root = _checkout(tmp_path, _origin("git@gitlab.com:g/p.git", "upstream"))
    assert detect_forge(root, env={}) is ForgeKind.GITLAB


def test_a_linked_worktree_reads_the_main_checkout_config(tmp_path: Path) -> None:
    main = _checkout(tmp_path / "main", _origin("git@gitlab.com:g/p.git"))
    gitdir = main / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")

    assert read_remote_url(linked) == "git@gitlab.com:g/p.git"
    assert detect_forge(linked, env={}) is ForgeKind.GITLAB


def test_a_broken_config_reads_as_no_remote(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "this is not ini\n=== nope ===\n")
    assert read_remote_url(root) is None
    assert detect_forge(root, env={}) is ForgeKind.GENERIC


def test_no_git_dir_reads_as_no_remote(tmp_path: Path) -> None:
    assert read_remote_url(tmp_path) is None


@pytest.mark.parametrize(
    ("env", "kind"),
    [
        ({"GITHUB_ACTIONS": "true"}, ForgeKind.GITHUB),
        ({"GITLAB_CI": "true"}, ForgeKind.GITLAB),
        ({"TF_BUILD": "True"}, ForgeKind.AZURE),
        ({"SYSTEM_TEAMFOUNDATIONCOLLECTIONURI": "https://dev.azure.com/org/"}, ForgeKind.AZURE),
        ({"BITBUCKET_BUILD_NUMBER": "42"}, ForgeKind.BITBUCKET),
        ({"GITHUB_ACTIONS": "false"}, ForgeKind.GENERIC),
        ({}, ForgeKind.GENERIC),
    ],
)
def test_ci_env_decides_when_there_is_no_remote(env: dict[str, str], kind: ForgeKind) -> None:
    assert detect_forge(None, env=env) is kind


def test_an_unknown_remote_host_defers_to_ci(tmp_path: Path) -> None:
    root = _checkout(tmp_path, _origin("git@git.corp.com:g/p.git"))
    assert detect_forge(root, env={"GITLAB_CI": "true"}) is ForgeKind.GITLAB


def test_a_known_remote_beats_ci(tmp_path: Path) -> None:
    root = _checkout(tmp_path, _origin("git@github.com:o/r.git"))
    assert detect_forge(root, env={"GITLAB_CI": "true"}) is ForgeKind.GITHUB


def test_hosts_from_config_name_a_self_managed_forge(tmp_path: Path) -> None:
    root = _checkout(tmp_path, _origin("ssh://git@git.corp.com:2222/g/p.git"))
    (root / ".repowise").mkdir()
    (root / ".repowise" / "config.yaml").write_text(
        "forges:\n  hosts:\n    Git.Corp.com: gitlab\n    tfs.corp: nonsense\n",
        encoding="utf-8",
    )
    assert forge_hosts(root, env={}) == {"git.corp.com": ForgeKind.GITLAB}
    assert detect_forge(root, env={}) is ForgeKind.GITLAB


def test_the_env_override_wins_over_config(tmp_path: Path) -> None:
    root = _checkout(tmp_path, _origin("ssh://git@git.corp.com/g/p.git"))
    (root / ".repowise").mkdir()
    (root / ".repowise" / "config.yaml").write_text(
        "forges:\n  hosts:\n    git.corp.com: gitlab\n", encoding="utf-8"
    )
    env = {HOSTS_ENV_VAR: "git.corp.com=azure, https://bb.corp:7990/ = bitbucket ,junk"}
    assert forge_hosts(root, env=env) == {
        "git.corp.com": ForgeKind.AZURE,
        "bb.corp": ForgeKind.BITBUCKET,
    }


@pytest.mark.parametrize(
    ("key", "url"),
    [
        ("[::1]:80", "http://[::1]:80/g/p.git"),
        ("::1", "ssh://git@[::1]:2222/g/p.git"),
        ("https://User@Git.Corp.com:8443/x", "git@git.corp.com:g/p.git"),
    ],
)
def test_override_keys_match_parsed_hosts(key: str, url: str) -> None:
    from repowise.core.forges import parse_remote

    hosts = forge_hosts(None, env={HOSTS_ENV_VAR: f"{key}=gitlab"})
    ref = parse_remote(url, hosts=hosts)
    assert ref is not None and ref.forge is ForgeKind.GITLAB
    direct = parse_remote(url, hosts={key: ForgeKind.GITLAB})
    assert direct is not None and direct.forge is ForgeKind.GITLAB


def test_a_broken_repo_config_contributes_no_hosts(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("forges: [unclosed\n", encoding="utf-8")
    assert forge_hosts(tmp_path, env={}) == {}
