"""Regression tests for workspace extractor file discovery.

The contract/topic/service-boundary extractors must scan only the repo's own
source — respecting ``.gitignore`` and nested-repo boundaries — exactly like the
main index. A naive ``os.walk`` from the repo root descends into nested git
repos (sibling/vendored repos, benchmark clones) physically located under a
workspace root, which once turned a sub-second scan into a multi-minute stall
over ~1M foreign files. These tests pin that behaviour.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.base import iter_source_files
from repowise.core.workspace.extractors.service_boundary import detect_service_boundaries
from repowise.core.workspace.extractors.topic_extractor import TopicExtractor

_PY = frozenset({".py"})


def _rel_paths(repo: Path) -> set[str]:
    return {rel for rel, _suffix, _content in iter_source_files(repo, _PY)}


def test_iter_source_files_skips_nested_git_repo(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('main')", encoding="utf-8")
    nested = tmp_path / "vendored"
    nested.mkdir()
    (nested / ".git").mkdir()  # makes ``vendored`` look like its own repo
    (nested / "foreign.py").write_text("x = 1", encoding="utf-8")

    found = _rel_paths(tmp_path)

    assert "app.py" in found
    assert "vendored/foreign.py" not in found


def test_iter_source_files_skips_nested_git_worktree_file(tmp_path: Path) -> None:
    # Worktrees and submodules use a ``.git`` *file* (not dir) — also a boundary.
    (tmp_path / "app.py").write_text("print('main')", encoding="utf-8")
    nested = tmp_path / "linked"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: /somewhere/else", encoding="utf-8")
    (nested / "foreign.py").write_text("x = 1", encoding="utf-8")

    found = _rel_paths(tmp_path)

    assert "app.py" in found
    assert "linked/foreign.py" not in found


def test_iter_source_files_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('main')", encoding="utf-8")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "skip.py").write_text("y = 2", encoding="utf-8")

    found = _rel_paths(tmp_path)

    assert "app.py" in found
    assert "ignored/skip.py" not in found


def test_topic_extractor_skips_nested_git_repo(tmp_path: Path) -> None:
    # A NATS producer in the repo is found; an identical one inside a nested
    # repo is not (the nested repo is a hard boundary).
    (tmp_path / "producer.py").write_text(
        'nc.publish("events.user")', encoding="utf-8"
    )
    nested = tmp_path / "bench"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "other.py").write_text('nc.publish("events.foreign")', encoding="utf-8")

    contracts = TopicExtractor().extract(tmp_path, "primary")
    topics = {c.meta["topic"] for c in contracts}

    assert "events.user" in topics
    assert "events.foreign" not in topics


def test_service_boundary_skips_nested_git_repo(tmp_path: Path) -> None:
    # A nested repo with service markers must not be reported as a sub-service
    # of the parent — it is an independent repo.
    nested = tmp_path / "sibling-service"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (nested / "main.py").write_text("print('svc')", encoding="utf-8")

    boundaries = detect_service_boundaries(tmp_path)
    names = {b.service_name for b in boundaries}

    assert "sibling-service" not in names
