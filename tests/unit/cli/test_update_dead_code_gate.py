"""An update keeps the stored dead-code findings when nothing it changed can move them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import command


def _run(tmp_path, *paths: str, **overrides) -> SimpleNamespace:
    fields = {
        "analyzer_changed": False,
        "extraction_changed": False,
        "renderer_changed": False,
        "history_stale": False,
        "config": SimpleNamespace(changed=False),
        "file_diffs": [SimpleNamespace(path=p, old_path=None) for p in paths],
        "affected": SimpleNamespace(regenerate=[]),
        "stale_deterministic_ids": set(),
        "index_only": False,
        "state": {"docs_mode": "deterministic"},
    }
    return SimpleNamespace(**{**fields, **overrides})


def test_prose_only_change_keeps_stored_findings(tmp_path) -> None:
    assert command._dead_code_unchanged(_run(tmp_path, "README.md", "docs/guide.adoc"))


def test_no_change_keeps_stored_findings(tmp_path) -> None:
    assert command._dead_code_unchanged(_run(tmp_path))


@pytest.mark.parametrize(
    "overrides",
    [
        {"analyzer_changed": True},
        {"history_stale": True},
        {"config": SimpleNamespace(changed=True)},
        {"affected": SimpleNamespace(regenerate=["src/a.py"])},
        {"stale_deterministic_ids": {"scc_page:x"}},
    ],
)
def test_anything_that_reads_or_moves_findings_reruns(tmp_path, overrides) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, "README.md", **overrides))


def test_code_or_manifest_change_reruns(tmp_path) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, "README.md", "src/a.py"))
    assert not command._dead_code_unchanged(_run(tmp_path, "requirements.txt"))
    assert not command._dead_code_unchanged(_run(tmp_path, "package.json"))


@pytest.mark.parametrize("path", ["docs/page.mdx", "docs/api.rst"])
def test_prose_that_names_code_reruns(tmp_path, path) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, path))


def test_rename_from_code_reruns(tmp_path) -> None:
    run = _run(tmp_path)
    run.file_diffs = [SimpleNamespace(path="notes.md", old_path="src/notes.py")]
    assert not command._dead_code_unchanged(run)


def test_index_only_model_wiki_renders_nothing(tmp_path) -> None:
    run = _run(
        tmp_path,
        "README.md",
        index_only=True,
        state={"docs_mode": "llm"},
        affected=SimpleNamespace(regenerate=["src/a.py"]),
    )
    assert command._dead_code_unchanged(run)
    run.file_diffs = [SimpleNamespace(path="src/a.py", old_path=None)]
    assert not command._dead_code_unchanged(run)
