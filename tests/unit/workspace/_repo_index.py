"""Build a real per-repo index for the contract-extraction tests.

Writes an actual ``.repowise/wiki.db`` with the rows ingestion would have
persisted and opens it through :func:`open_repo_index`, so the tests exercise
the query path — repository resolution, ``file_path`` keys, external edges —
rather than a stand-in for it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from repowise.core.persistence.models import GraphEdge, Repository, WikiSymbol
from repowise.core.workspace.registry import open_repo_db
from repowise.core.workspace.repo_index import RepoIndex, open_repo_index

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

_REPO_ID = "testrepo"


async def make_repo_index(
    repo: Path,
    symbols_by_file: Mapping[str, Sequence[Any]],
    *,
    alias: str = "backend",
    external_edges: Sequence[tuple[str, str, list[str]]] = (),
) -> RepoIndex:
    """Persist *symbols_by_file* to ``repo``'s wiki.db and open it.

    Values are ingestion ``Symbol`` objects; they are mapped onto ``WikiSymbol``
    exactly as :func:`repowise.core.persistence.crud.external_systems` does.
    *external_edges* are ``(source_file, target_node_id, imported_names)``;
    ``imported_names`` is written verbatim so a malformed payload can be tested.
    """
    (repo / ".repowise").mkdir(parents=True, exist_ok=True)
    engine, session_factory = await open_repo_db(repo)
    async with session_factory() as session:
        session.add(
            Repository(id=_REPO_ID, name=alias, url="", local_path=str(repo))
        )
        await session.flush()  # graph_edges carries an FK onto the row above
        for rel_path, symbols in symbols_by_file.items():
            for sym in symbols:
                session.add(
                    WikiSymbol(
                        repository_id=_REPO_ID,
                        file_path=rel_path,
                        symbol_id=sym.id,
                        name=sym.name,
                        qualified_name=sym.qualified_name,
                        kind=sym.kind,
                        signature=sym.signature,
                        start_line=sym.start_line,
                        end_line=sym.end_line,
                        docstring=sym.docstring,
                        visibility=sym.visibility,
                        language=sym.language,
                        parent_name=sym.parent_name,
                    )
                )
        for source, target, names in external_edges:
            session.add(
                GraphEdge(
                    repository_id=_REPO_ID,
                    source_node_id=source,
                    target_node_id=target,
                    edge_type="imports",
                    imported_names_json=json.dumps(names),
                )
            )
        await session.commit()
    await engine.dispose()

    index = await open_repo_index(alias, repo)
    assert index is not None
    return index
