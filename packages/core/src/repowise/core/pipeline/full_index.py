"""Full index-only pipeline run + persistence for a single repo.

The "index this repo from scratch" step shared by ``repowise workspace add``
and the workspace updater's first-time / fallback indexing — both previously
hand-rolled the same run_pipeline → init_db → upsert_repository →
persist_pipeline_result sequence, and only one of them exported the
knowledge-graph artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def index_repo_full(
    repo_path: Path,
    *,
    commit_depth: int = 500,
    exclude_patterns: list[str] | None = None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    follow_renames: bool = False,
    require_git_success: bool = False,
    require_health_success: bool = False,
    progress: Any | None = None,
) -> Any:
    """Run the full pipeline (index-only, no LLM docs) and persist everything.

    Persists to the repo-local DB and exports ``.repowise/knowledge-graph.json``
    so downstream doc generation can load the curated module grouping. Returns
    the :class:`PipelineResult`.
    """
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.pipeline import run_pipeline
    from repowise.core.pipeline.persist import persist_pipeline_result

    result = await run_pipeline(
        repo_path,
        commit_depth=commit_depth,
        exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        follow_renames=follow_renames,
        generate_docs=False,
        progress=progress,
    )

    # The ordinary full-index fallback keeps Git and health best-effort for
    # backward compatibility. A configuration-driven rebuild is different:
    # advancing the dependency fingerprints after either required phase was
    # swallowed by the orchestrator would permanently strand stale rows.
    if require_git_success and getattr(result, "git_summary", None) is None:
        raise RuntimeError("Git indexing failed during a required configuration rebuild")
    if require_health_success and getattr(result, "health_report", None) is None:
        raise RuntimeError("Health analysis failed during a required configuration rebuild")

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    stale_page_ids: list[str] = []
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=result.repo_name,
                local_path=str(repo_path),
            )
            stale_page_ids = (
                await persist_pipeline_result(
                    result,
                    session,
                    repo.id,
                    replace_full_git_history=require_git_success,
                )
                or []
            )

        from repowise.core.pipeline.cleanup_debt import (
            clear_cleanup_debt,
            load_cleanup_debt,
            record_cleanup_debt,
        )

        debt = load_cleanup_debt(repo_path)
        fts_cleanup_ids = set(stale_page_ids) | debt["fts"]
        if fts_cleanup_ids:
            from repowise.core.persistence.search import FullTextSearch

            try:
                fts = FullTextSearch(engine)
                await fts.ensure_index()
                await fts.delete_many(sorted(fts_cleanup_ids))
                clear_cleanup_debt(repo_path, "fts", fts_cleanup_ids)
            except Exception:
                record_cleanup_debt(repo_path, "fts", fts_cleanup_ids)
                raise

        vector_cleanup_ids = set(stale_page_ids) | debt["vectors"]
        if vector_cleanup_ids:
            lance_dir = repo_path / ".repowise" / "lancedb"
            if lance_dir.exists():
                from repowise.core.persistence.vector_store import LanceDBVectorStore
                from repowise.core.providers.embedding.base import MockEmbedder

                # Deletion does not embed or validate vector dimensions, so a
                # mock instance can safely open a real-embedder table here.
                try:
                    vector_store = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())
                    await vector_store.delete_many(sorted(vector_cleanup_ids))
                    clear_cleanup_debt(repo_path, "vectors", vector_cleanup_ids)
                except Exception:
                    record_cleanup_debt(repo_path, "vectors", vector_cleanup_ids)
                    raise
            else:
                clear_cleanup_debt(repo_path, "vectors", vector_cleanup_ids)
    finally:
        await engine.dispose()

    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        from repowise.core.analysis.knowledge_graph import save_knowledge_graph_json

        save_knowledge_graph_json(repo_path, kg)

    return result
