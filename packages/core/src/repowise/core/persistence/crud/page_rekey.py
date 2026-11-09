"""Re-key a repository's repo-level pages when its name changes.

A page id is ``<page_type>:<target_path>``, and the repo-level pages carry
the repository's folder name as their target (``repo_overview:<name>``).
When a checkout moves, is renamed, or is copied together with its
``.repowise`` into a folder with another name, generation looks the
overview up under the new folder name while the store still holds it
under the old one: the page is rebuilt from scratch instead of reused,
and notes or links on the old id are lost (#5366).

Both re-key sites share this module. The repo-local re-point
(``upsert_repository`` and ``adopt_moved_repository``) runs the
statements through :func:`rekey_repo_level_pages`; the worktree seed
(``repowise.cli.worktree._adopt_repository_identity``, which used to
re-key inline) runs the same :func:`repo_level_page_rekey_statements`
against its own connection. Either way the page row moves with its
versions, its children's parent edge and its FTS row, so nothing keeps
pointing at an id the store no longer holds.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The repo-level page types whose id carries the repository name. Both are
# one per repository, both are looked up as ``<type>:<repo folder name>``.
REPO_LEVEL_PAGE_TYPES: tuple[str, ...] = ("repo_overview", "architecture_diagram")


def repo_level_page_rekey_statements(
    repository_id: str,
    old_name: str,
    new_name: str,
    *,
    has_page_fts: bool = True,
) -> list[tuple[str, dict[str, Any]]]:
    """The statements that move repo-level pages from *old_name* to *new_name*.

    Empty when the name did not change: an id only moves when the name that
    builds it does. ``has_page_fts`` is False for a store that has never
    created its FTS index — a statement list cannot probe ``sqlite_master``
    itself, so the caller passes what it saw.
    """
    if not old_name or old_name == new_name:
        return []
    statements: list[tuple[str, dict[str, Any]]] = []
    for page_type in REPO_LEVEL_PAGE_TYPES:
        old_id, new_id = f"{page_type}:{old_name}", f"{page_type}:{new_name}"
        params: dict[str, Any] = {
            "repo_id": repository_id,
            "old_id": old_id,
            "new_id": new_id,
            "new_target": new_name,
        }
        # The page row first: it is the row the follow-ups point at, and a
        # page left under the old id is never regenerated nor found by
        # ``generate --page``.
        statements.append(
            (
                "UPDATE wiki_pages SET id = :new_id, target_path = :new_target "
                "WHERE repository_id = :repo_id AND id = :old_id",
                params,
            )
        )
        statements.append(
            (
                "UPDATE wiki_page_versions SET page_id = :new_id WHERE page_id = :old_id",
                params,
            )
        )
        # ``parent_page_id`` carries no foreign key, but it is how the tree
        # finds the overview again from its children.
        statements.append(
            (
                "UPDATE wiki_pages SET parent_page_id = :new_id "
                "WHERE repository_id = :repo_id AND parent_page_id = :old_id",
                params,
            )
        )
        if has_page_fts:
            statements.append(
                (
                    "UPDATE page_fts SET page_id = :new_id, target_path = :new_target "
                    "WHERE page_id = :old_id",
                    params,
                )
            )
    return statements


async def rekey_repo_level_pages(
    session: AsyncSession,
    *,
    repository_id: str,
    old_name: str,
    new_name: str,
) -> None:
    """Move *repository_id*'s repo-level pages to the ids *new_name* builds.

    No-op when the name is unchanged or empty. The store's connections run
    with ``foreign_keys=ON`` and ``wiki_page_versions.page_id`` references
    ``wiki_pages.id``, so neither side of the swap can move first: the
    constraints are deferred to commit, where every pair has landed.
    """
    if not old_name or old_name == new_name:
        return
    has_page_fts = (
        await session.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'page_fts'"))
    ).first() is not None
    statements = repo_level_page_rekey_statements(
        repository_id, old_name, new_name, has_page_fts=has_page_fts
    )
    if not statements:
        return
    # Outside a transaction this holds until the first write opens one; inside
    # one it lasts to commit. SQLite resets it at COMMIT/ROLLBACK either way.
    await session.execute(text("PRAGMA defer_foreign_keys = ON"))
    for sql, params in statements:
        await session.execute(text(sql), params)
