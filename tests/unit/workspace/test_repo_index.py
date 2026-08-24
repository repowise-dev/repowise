"""The read-only per-repo accessor the cross-repo hooks run on.

Every test here writes a real ``.repowise/wiki.db`` and reads it back, because
the failure this accessor exists to prevent — the hooks re-deriving from text
what the database already holds — is a plumbing failure, not a logic one.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.models import Symbol
from repowise.core.workspace.repo_index import open_repo_index, open_workspace_index

from ._repo_index import make_repo_index


def _symbol(name: str, start: int, end: int, *, kind="function", visibility="public"):
    return Symbol(
        id=f"m.py::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=f"def {name}()",
        start_line=start,
        end_line=end,
        docstring=None,
        visibility=visibility,
    )


class TestSymbolLookups:
    async def test_symbols_are_keyed_by_repo_relative_path(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"a/b.py": [_symbol("f", 1, 5)]})
        try:
            assert [s.name for s in index.symbols_for_file("a/b.py")] == ["f"]
            assert index.symbols_for_file("b.py") == []
        finally:
            await index.close()

    async def test_symbol_at_returns_the_innermost_span(self, tmp_path: Path) -> None:
        index = await make_repo_index(
            tmp_path,
            {
                "a/b.py": [
                    _symbol("Outer", 1, 20, kind="class"),
                    _symbol("inner", 5, 9, kind="method"),
                ]
            },
        )
        try:
            assert index.symbol_at("a/b.py", 7).name == "inner"
            assert index.symbol_at("a/b.py", 15).name == "Outer"
            assert index.symbol_at("a/b.py", 25) is None
        finally:
            await index.close()

    async def test_public_symbols_span_the_whole_repo(self, tmp_path: Path) -> None:
        index = await make_repo_index(
            tmp_path,
            {
                "a.py": [_symbol("pub", 1, 2)],
                "b.py": [_symbol("_priv", 1, 2, visibility="private")],
            },
        )
        try:
            assert {s.name for s in index.public_symbols()} == {"pub"}
        finally:
            await index.close()


class TestExternalImportEdges:
    async def test_the_prefix_is_stripped_and_names_are_carried(
        self, tmp_path: Path
    ) -> None:
        index = await make_repo_index(
            tmp_path,
            {},
            external_edges=[
                ("src/a.ts", "external:@repowise-dev/types", ["RepoSummary", "Health"]),
                ("src/b.ts", "src/c.ts", ["Local"]),
            ],
        )
        try:
            edges = index.external_import_edges()
            assert [e.external_name for e in edges] == ["@repowise-dev/types"]
            assert edges[0].imported_names == ("RepoSummary", "Health")
        finally:
            await index.close()

    async def test_a_malformed_names_payload_does_not_lose_the_repo(
        self, tmp_path: Path
    ) -> None:
        index = await make_repo_index(
            tmp_path, {}, external_edges=[("src/a.ts", "external:pkg", "not-a-list")]
        )
        try:
            assert index.external_import_edges()[0].imported_names == ()
        finally:
            await index.close()


class TestOpening:
    async def test_a_repo_with_no_index_opens_to_none(self, tmp_path: Path) -> None:
        assert await open_repo_index("alpha", tmp_path) is None

    async def test_the_workspace_skips_repos_without_one(self, tmp_path: Path) -> None:
        class _Entry:
            def __init__(self, alias: str, path: str) -> None:
                self.alias, self.path = alias, path

        class _Config:
            def __init__(self) -> None:
                self.repos = [_Entry("alpha", "alpha"), _Entry("beta", "beta")]

        (tmp_path / "beta").mkdir()
        index = await make_repo_index(tmp_path / "alpha", {"a.py": [_symbol("f", 1, 2)]})
        await index.close()

        workspace = await open_workspace_index(_Config(), tmp_path)
        try:
            assert workspace.get("alpha") is not None
            assert workspace.get("beta") is None
        finally:
            await workspace.close()
