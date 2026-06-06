"""Tests for curated-grouping wiring in ``repowise workspace add``.

``module_grouping="curated"`` is the default. Doc generation can only engage
curated module grouping if (a) the KG artifact (``.repowise/knowledge-graph.json``)
was saved during indexing and (b) the generator is told the ``repo_path`` so it
can load that artifact. These tests pin both seams so a regression to community
grouping is caught.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from repowise.cli.commands.workspace_cmd import _generate_docs_for_added_repo


def test_do_index_save_idiom_writes_artifact(tmp_path):
    """The ``_do_index`` save idiom must persist the curated KG artifact.

    ``_do_index`` is a nested closure, so we exercise the exact save call it
    performs: when the pipeline result carries a ``knowledge_graph_result``,
    ``save_knowledge_graph_json`` writes ``.repowise/knowledge-graph.json``.
    """
    from repowise.cli.state_persistence import save_knowledge_graph_json

    kg = SimpleNamespace(to_dict=lambda: {"nodes": [], "layers": []})
    result = SimpleNamespace(knowledge_graph_result=kg)

    saved = getattr(result, "knowledge_graph_result", None)
    assert saved is not None
    save_knowledge_graph_json(tmp_path, saved)

    assert (tmp_path / ".repowise" / "knowledge-graph.json").is_file()


def test_generate_docs_for_added_repo_passes_repo_path(tmp_path):
    """``generate_all`` must receive ``repo_path`` so the curated KG loads."""
    repo_path = tmp_path

    generator = MagicMock()
    generator.generate_all = AsyncMock(return_value=[])

    traverser = MagicMock()
    traverser.traverse.return_value = []
    traverser.get_repo_structure.return_value = object()

    fts = MagicMock()
    fts.ensure_index = AsyncMock()
    fts.index = AsyncMock()

    engine = MagicMock()
    engine.dispose = AsyncMock()

    class _SessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *a):
            return False

    def _fake_get_session(_sf):
        return _SessionCtx()

    with (
        patch("repowise.cli.helpers.get_db_url_for_repo", return_value="sqlite://"),
        patch("repowise.core.generation.PageGenerator", return_value=generator),
        patch("repowise.core.generation.ContextAssembler"),
        patch("repowise.core.generation.GenerationConfig"),
        patch("repowise.core.ingestion.FileTraverser", return_value=traverser),
        patch("repowise.core.ingestion.ASTParser"),
        patch("repowise.core.ingestion.GraphBuilder", return_value=MagicMock()),
        patch("repowise.core.persistence.create_engine", return_value=engine),
        patch("repowise.core.persistence.create_session_factory", return_value=MagicMock()),
        patch("repowise.core.persistence.init_db", AsyncMock()),
        patch("repowise.core.persistence.get_session", _fake_get_session),
        patch("repowise.core.persistence.upsert_repository", AsyncMock()),
        patch("repowise.core.persistence.upsert_page_from_generated", AsyncMock()),
        patch("repowise.core.persistence.FullTextSearch", return_value=fts),
    ):
        _generate_docs_for_added_repo(
            repo_path=repo_path,
            provider=object(),
            embedder_name="fake",
            concurrency=1,
            reasoning="low",
            exclude_patterns=[],
        )

    generator.generate_all.assert_awaited_once()
    assert generator.generate_all.await_args.kwargs["repo_path"] == repo_path
