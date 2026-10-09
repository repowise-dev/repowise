"""Making ``.repowise/decisions.yaml`` committable without un-ignoring the rest.

``.repowise/`` and ``.repowise/*`` look interchangeable and are not: the first
has no internal slash, so git matches it at any depth, while the second is
anchored to the ``.gitignore``'s own directory. Rewriting one into the other
would un-ignore every nested ``.repowise/`` in the tree, which in this
repository means committing a fixture's session database.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.repo_config import ensure_manifest_tracked


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    for rel in (".repowise", "pkg/.repowise"):
        (tmp_path / rel).mkdir(parents=True)
        (tmp_path / rel / "wiki.db").write_text("x", encoding="utf-8")
    (tmp_path / ".repowise" / "decisions.yaml").write_text("version: 1\n", encoding="utf-8")
    return tmp_path


def _ignored(repo, rel: str) -> bool:
    return _git(repo, "check-ignore", "-q", rel).returncode == 0


def test_the_manifest_becomes_committable(repo):
    (repo / ".gitignore").write_text(".repowise/\n", encoding="utf-8")

    assert ensure_manifest_tracked(repo) is True

    assert not _ignored(repo, ".repowise/decisions.yaml")
    assert _ignored(repo, ".repowise/wiki.db")


def test_a_nested_repowise_directory_stays_ignored(repo):
    (repo / ".gitignore").write_text(".repowise/\n", encoding="utf-8")

    ensure_manifest_tracked(repo)

    assert _ignored(repo, "pkg/.repowise/wiki.db")


def test_it_is_idempotent(repo):
    (repo / ".gitignore").write_text(".repowise/\n", encoding="utf-8")

    assert ensure_manifest_tracked(repo) is True
    after_first = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ensure_manifest_tracked(repo) is False
    assert (repo / ".gitignore").read_text(encoding="utf-8") == after_first


def test_unrelated_rules_survive(repo):
    (repo / ".gitignore").write_text(
        "# build output\nnode_modules/\n.repowise/\n*.log\n", encoding="utf-8"
    )

    ensure_manifest_tracked(repo)

    content = (repo / ".gitignore").read_text(encoding="utf-8")
    for line in ("# build output", "node_modules/", ".repowise/", "*.log"):
        assert line in content.splitlines()


def test_a_crlf_gitignore_is_not_rewritten_as_lf(repo):
    """One appended rule must not become a whole-file diff on Windows."""
    (repo / ".gitignore").write_bytes(b"node_modules/\r\n.repowise/\r\n")

    ensure_manifest_tracked(repo)

    raw = (repo / ".gitignore").read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_a_repo_without_a_gitignore_is_left_alone(tmp_path):
    """Creating one would take over ignoring decisions this tool does not own."""
    assert ensure_manifest_tracked(tmp_path) is False
    assert not (tmp_path / ".gitignore").exists()
