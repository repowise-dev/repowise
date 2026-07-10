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
        generate_docs=False,
        progress=progress,
    )

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=result.repo_name,
                local_path=str(repo_path),
            )
            await persist_pipeline_result(result, session, repo.id)
    finally:
        await engine.dispose()

    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        from repowise.core.analysis.knowledge_graph import save_knowledge_graph_json

        save_knowledge_graph_json(repo_path, kg)

    return result
