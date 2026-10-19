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
        "state": {"knowledge_graph": {"fingerprint": "fp"}},
        "repo_path": tmp_path,
        "graph_builder": object(),
    }
    return SimpleNamespace(**{**fields, **overrides})


@pytest.fixture
def kg_current(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "knowledge-graph.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "repowise.core.analysis.knowledge_graph.compute_kg_fingerprint", lambda _b: "fp"
    )


def test_prose_only_change_keeps_stored_findings(tmp_path, kg_current) -> None:
    assert command._dead_code_unchanged(_run(tmp_path, "README.md", "docs/guide.adoc"))


def test_no_change_keeps_stored_findings(tmp_path, kg_current) -> None:
    assert command._dead_code_unchanged(_run(tmp_path))


@pytest.mark.parametrize(
    "overrides",
    [
        {"analyzer_changed": True},
        {"history_stale": True},
        {"config": SimpleNamespace(changed=True)},
        {"affected": SimpleNamespace(regenerate=["src/a.py"])},
        {"stale_deterministic_ids": {"scc_page:x"}},
        {"state": {"knowledge_graph": {"fingerprint": "older"}}},
    ],
)
def test_anything_that_reads_or_moves_findings_reruns(tmp_path, kg_current, overrides) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, "README.md", **overrides))


def test_code_or_manifest_change_reruns(tmp_path, kg_current) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, "README.md", "src/a.py"))
    assert not command._dead_code_unchanged(_run(tmp_path, "requirements.txt"))
    assert not command._dead_code_unchanged(_run(tmp_path, "package.json"))


@pytest.mark.parametrize("path", ["docs/page.mdx", "docs/api.rst"])
def test_prose_that_names_code_reruns(tmp_path, kg_current, path) -> None:
    assert not command._dead_code_unchanged(_run(tmp_path, path))


def test_rename_from_code_reruns(tmp_path, kg_current) -> None:
    run = _run(tmp_path)
    run.file_diffs = [SimpleNamespace(path="notes.md", old_path="src/notes.py")]
    assert not command._dead_code_unchanged(run)


def test_missing_knowledge_graph_reruns(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "repowise.core.analysis.knowledge_graph.compute_kg_fingerprint", lambda _b: "fp"
    )
    assert not command._dead_code_unchanged(_run(tmp_path, "README.md"))
