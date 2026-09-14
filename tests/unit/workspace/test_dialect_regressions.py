"""Per-dialect regression locks for contracts that were fabricated or lost.

The dialects under ``workspace/extractors/`` had no direct tests: coverage was
indirect, through ``test_contracts.py`` and ``test_extractor_traversal.py``
exercising the top-level extractors. Four defects reached the live workspace
artifact through that gap, and each one gets a test here that fails against the
pre-fix code:

1. ``@router.post("")`` on a prefixed router was invisible — the decorator
   regex required at least one path character. It cost 2 of the 3 real SSE
   endpoints in the hosted backend.
2. The extractors' own sources were scanned, so the example syntax in their
   docstrings became 14 live contracts (``topic::orders``, ``http::GET::/path``,
   ``grpc::AuthService/*``).
3. ``WITH ranked AS (...)`` reported a table named ``ranked`` — sqlglot models a
   CTE reference as a table.
4. A docstring reading "...update path..." / "...update would..." satisfied the
   SQL verb gate and became the tables ``path`` and ``would``.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.base import (
    ScanContext,
    make_exclude_predicate,
)
from repowise.core.workspace.extractors.data import DataExtractor
from repowise.core.workspace.extractors.data.sql_strings import SqlStringsDialect
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.jaxrs import JaxRsDialect


def _ctx(content: str, rel_path: str = "app/routers/chat.py") -> ScanContext:
    return ScanContext(
        repo_alias="backend", rel_path=rel_path, suffix=".py", content=content
    )


def _ids(contracts) -> set[str]:
    return {c.contract_id for c in contracts}


class TestFastApiEmptyPath:
    """Regression 1: ``@router.post("")`` mounted at a router prefix."""

    # Mirrors backend/app/routers/workspace_chat.py:34,162 and chat.py:49,352.
    SOURCE = '''
router = APIRouter(prefix="/snapshots/{snapshot_id}/chat", tags=["chat"])


@router.post("")
async def chat_message(request: Request) -> None:
    ...


@router.get("/conversations")
async def list_conversations() -> None:
    ...
'''

    def test_empty_path_route_is_extracted_at_the_router_prefix(self) -> None:
        ids = _ids(FastApiDialect().extract(_ctx(self.SOURCE)))
        assert "http::POST::/snapshots/{param}/chat" in ids

    def test_sibling_routes_still_extracted(self) -> None:
        ids = _ids(FastApiDialect().extract(_ctx(self.SOURCE)))
        assert "http::GET::/snapshots/{param}/chat/conversations" in ids

    def test_empty_path_without_a_prefix_contributes_nothing(self) -> None:
        # No prefix to stitch onto, so there is no path to record. Dropped by
        # build_provider_contract rather than silently becoming bare "/".
        src = '''
app = FastAPI()


@app.post("")
async def nothing() -> None:
    ...
'''
        assert FastApiDialect().extract(_ctx(src)) == []

    def test_non_router_decorator_is_not_a_route(self) -> None:
        # The empty-path relaxation must not turn every @x.get() into a route.
        src = '''
router = APIRouter(prefix="/api")


@cache.get("/not-a-route")
def cached() -> None:
    ...
'''
        assert FastApiDialect().extract(_ctx(src)) == []


class TestExtractorSelfExclusion:
    """Regression 2: the extractors' own example syntax became live contracts."""

    SELF_PATHS = (
        "packages/core/src/repowise/core/workspace/extractors/http/fastapi.py",
        "packages/core/src/repowise/core/workspace/extractors/http/mounts.py",
        "packages/core/src/repowise/core/workspace/extractors/topic_extractor.py",
        "packages/core/src/repowise/core/workspace/extractors/grpc/python.py",
        "packages/core/src/repowise/core/workspace/extractors/data/sql_strings.py",
    )

    def test_extractor_sources_are_skipped(self) -> None:
        skip = make_exclude_predicate()
        for path in self.SELF_PATHS:
            assert skip(path), path

    def test_the_rest_of_the_workspace_package_is_still_scanned(self) -> None:
        # The exclusion is the extractors tree only, not workspace/ at large.
        skip = make_exclude_predicate()
        assert not skip("packages/core/src/repowise/core/workspace/contracts.py")

    def test_ordinary_source_is_unaffected(self) -> None:
        skip = make_exclude_predicate()
        assert not skip("app/routers/chat.py")
        assert not skip("src/lib/api/client.ts")

    def test_user_supplied_globs_still_apply(self) -> None:
        skip = make_exclude_predicate(("vendor/*",))
        assert skip("vendor/thing.py")
        assert not skip("app/thing.py")


class TestSqlCteAlias:
    """Regression 3: ``WITH ranked AS (...)`` emitted a table named ``ranked``."""

    # Reduced from packages/core/src/repowise/core/persistence/crud/git.py:258.
    LITERAL = """
WITH ranked AS (
  SELECT id, PERCENT_RANK() OVER (ORDER BY commit_count_90d) AS prank
  FROM git_metadata
  WHERE repository_id = :repo_id
)
UPDATE git_metadata
SET churn_percentile = (SELECT prank FROM ranked WHERE ranked.id = git_metadata.id)
"""

    def test_cte_alias_is_not_a_table(self) -> None:
        tables = {t for t, _verb in SqlStringsDialect()._tables_in(self.LITERAL)}
        assert "ranked" not in tables

    def test_the_real_table_is_still_found(self) -> None:
        tables = {t for t, _verb in SqlStringsDialect()._tables_in(self.LITERAL)}
        assert "git_metadata" in tables

    def test_a_real_table_sharing_a_cte_name_elsewhere_is_unaffected(self) -> None:
        # Shadowing is per-statement: no CTE here, so ``ranked`` is a table.
        tables = {
            t for t, _verb in SqlStringsDialect()._tables_in("SELECT id FROM ranked")
        }
        assert "ranked" in tables


class TestSqlProseRejection:
    """Regression 4: a docstring became the tables ``path`` and ``would``."""

    # Reduced from the batch_upsert_symbols docstring in
    # packages/core/src/repowise/core/persistence/crud/external_systems.py.
    DOCSTRING_SOURCE = '''
def batch_upsert_symbols(file_paths, symbols):
    """Make ``wiki_symbols`` for *file_paths* match a fresh parse (*symbols*).

    The incremental update path re-parses changed files but historically never
    persisted their symbols. Calling the repo-wide helper per update would
    SELECT the whole table. This prunes rows for symbols that vanished from a
    still-existing file.
    """
'''

    def test_docstring_prose_yields_no_tables(self) -> None:
        ctx = _ctx(self.DOCSTRING_SOURCE, rel_path="crud/external_systems.py")
        assert SqlStringsDialect().extract(ctx) == []

    def test_the_leaked_words_are_gone(self) -> None:
        ctx = _ctx(self.DOCSTRING_SOURCE, rel_path="crud/external_systems.py")
        ids = _ids(SqlStringsDialect().extract(ctx))
        assert "data::path" not in ids
        assert "data::would" not in ids

    def test_real_sql_in_the_same_file_is_still_extracted(self) -> None:
        src = self.DOCSTRING_SOURCE + '''
    rows = conn.execute("SELECT id, name FROM wiki_symbols WHERE repo_id = :r")
'''
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "crud/external_systems.py")))
        assert "data::wiki_symbols" in ids

    def test_named_query_comment_still_opens_the_gate(self) -> None:
        # aiosql / yesql / sqlc / HugSQL all prefix a query with a name comment.
        src = '''
q = """
-- name: get_user
SELECT id FROM accounts WHERE id = :id
"""
'''
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "q.py")))
        assert "data::accounts" in ids

    def test_block_comment_prefix_still_opens_the_gate(self) -> None:
        src = 'q = "/* cached */ SELECT id FROM accounts WHERE id = 1"\n'
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "q.py")))
        assert "data::accounts" in ids

    def test_create_table_as_select_records_the_table_it_reads(self) -> None:
        src = 'q = "CREATE TABLE snapshot AS SELECT id FROM accounts WHERE x = 1"\n'
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "q.py")))
        assert "data::accounts" in ids

    def test_explain_still_opens_the_gate(self) -> None:
        src = 'q = "EXPLAIN ANALYZE SELECT id FROM accounts WHERE id = 1"\n'
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "q.py")))
        assert "data::accounts" in ids

    def test_prose_beginning_with_a_dash_is_still_rejected(self) -> None:
        # The comment strip must not become a way back in for prose.
        src = '''
def f():
    """- The incremental update path would SELECT rows from a file."""
'''
        assert SqlStringsDialect().extract(_ctx(src, "f.py")) == []

    def test_indented_sql_literal_still_opens_the_gate(self) -> None:
        # Multi-line SQL literals usually start with a newline and indentation.
        src = '''
q = """
    SELECT id FROM projects WHERE owner = :owner
"""
'''
        ids = _ids(SqlStringsDialect().extract(_ctx(src, "q.py")))
        assert "data::projects" in ids


class TestDataExtractorEndToEnd:
    """The two SQL fixes hold through the DataExtractor, not just the dialect."""

    def test_prose_and_cte_do_not_reach_contracts(self, tmp_path: Path) -> None:
        (tmp_path / "git.py").write_text(
            f'SQL = """{TestSqlCteAlias.LITERAL}"""\n', encoding="utf-8"
        )
        (tmp_path / "external_systems.py").write_text(
            TestSqlProseRejection.DOCSTRING_SOURCE, encoding="utf-8"
        )
        ids = _ids(DataExtractor().extract(tmp_path, "repowise", make_exclude_predicate()))
        assert "data::git_metadata" in ids
        for fabricated in ("data::ranked", "data::path", "data::would"):
            assert fabricated not in ids


class TestQuarkusIsJaxRs:
    """A Quarkus REST resource is a JAX-RS resource and needs no dialect of its own.

    The JAX-RS dialect gates on ``@Path`` in the file, not on the stack, so the
    ``jakarta.ws.rs`` annotations Quarkus uses yield contracts as they are.
    """

    RESOURCE = """package org.acme;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import io.quarkus.runtime.annotations.RegisterForReflection;

@Path("/fruits")
public class FruitResource {

    @GET
    public List<Fruit> list() { return Fruit.listAll(); }

    @GET
    @Path("/{id}")
    public Fruit get(@PathParam("id") Long id) { return Fruit.findById(id); }

    @POST
    public Response add(Fruit fruit) { fruit.persist(); return Response.ok(fruit).build(); }
}
"""

    def test_quarkus_resource_yields_jaxrs_contracts(self) -> None:
        ctx = ScanContext(
            repo_alias="api",
            rel_path="src/main/java/org/acme/FruitResource.java",
            suffix=".java",
            content=self.RESOURCE,
        )
        contracts = JaxRsDialect().extract(ctx)
        assert _ids(contracts) == {
            "http::GET::/fruits",
            "http::GET::/fruits/{param}",
            "http::POST::/fruits",
        }
        assert {c.meta["framework"] for c in contracts} == {"jaxrs"}

