"""HTTP route contract extraction.

Scans source files for route handler declarations (providers) and HTTP client
calls (consumers). Each framework / client library is an independent *dialect*
module registered in :data:`PROVIDER_DIALECTS` / :data:`CONSUMER_DIALECTS`; the
:class:`HttpExtractor` orchestrator owns only the file walk and dispatch. Adding
a framework means dropping one dialect module and appending it to a registry —
no orchestrator edits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import ScanContext, select_files
from ..langs import PYTHON
from .aspnet import AspNetDialect
from .csharp_http import CSharpHttpDialect
from .dialect import HttpDialect
from .django import DjangoDialect
from .express import ExpressDialect
from .fastapi import FastApiDialect
from .go import GoDialect
from .jaxrs import JaxRsDialect
from .js_clients import JsClientsDialect
from .laravel import LaravelDialect
from .mounts import merge_mount_maps
from .next_app import NextAppDialect
from .paths import normalize_http_path
from .python_clients import PythonClientsDialect
from .rust_axum import RustAxumDialect
from .rust_clients import RustClientsDialect
from .spring import SpringDialect

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.repo_index import RepoIndex

    from ..base import SourceFile

# Route-declaration recognisers (one framework each).
PROVIDER_DIALECTS: tuple[HttpDialect, ...] = (
    ExpressDialect(),
    FastApiDialect(),
    SpringDialect(),
    LaravelDialect(),
    GoDialect(),
    AspNetDialect(),
    RustAxumDialect(),
    DjangoDialect(),
    JaxRsDialect(),
    NextAppDialect(),
)

# HTTP-client call recognisers (one client/language each).
CONSUMER_DIALECTS: tuple[HttpDialect, ...] = (
    JsClientsDialect(),
    PythonClientsDialect(),
    CSharpHttpDialect(),
    RustClientsDialect(),
)


# Provider dialects the index path fully replaces for a file it has parsed.
# Only the Python route regex is superseded today; every other framework still
# runs its dialect, because nothing reads their decorators yet.
_INDEX_BACKED_DIALECTS = frozenset({"fastapi"})

# Consumer dialects whose duplicates the index pass removes. Unlike the
# provider side this supersedes per *contract id*, not per file: the index pass
# reads wrapper calls, while the dialect also reads shapes the index pass does
# not model (``axios.get``), so dropping the dialect wholesale for a file would
# subtract recall from a change made to add it.
_INDEX_BACKED_CONSUMER_DIALECTS = frozenset({"js-clients"})


def _union_extensions(dialects: tuple[HttpDialect, ...]) -> frozenset[str]:
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


class HttpExtractor:
    """Extract HTTP route contracts from source files via registered dialects."""

    provider_dialects: tuple[HttpDialect, ...] = PROVIDER_DIALECTS
    consumer_dialects: tuple[HttpDialect, ...] = CONSUMER_DIALECTS

    @classmethod
    def source_extensions(cls) -> frozenset[str]:
        """Every extension this extractor's dialects claim."""
        return _union_extensions(cls.provider_dialects) | _union_extensions(
            cls.consumer_dialects
        )

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
        files: Sequence[SourceFile] | None = None,
        repo_index: RepoIndex | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[Contract]:
        """Scan all source files in *repo_path* and return Contract instances.

        Files are read once into memory so a first pass can collect repo-wide
        router mounts (``include_router(prefix=...)`` / ``app.use('/x', router)``)
        before the extraction pass stitches them onto each provider route.

        *files* is an already-walked ``(rel_path, suffix, content)`` list from
        the orchestrator's single traversal; None walks the repo directly.
        *repo_index* is the repo's read-only symbol table. A file it has
        symbols for gets its Python routes from the declarations above those
        symbols rather than from the FastAPI text regex, which is what stops a
        route in a comment becoming a contract and an empty path being lost.

        *stats* is an optional out-dict of counters. ``http_consumer_unresolved``
        counts calls to a *confirmed* HTTP wrapper, or through a variable bound
        to an HTTP client instance, whose path argument could not be resolved
        statically — real endpoint calls that were located but cannot be named.
        They are counted rather than dropped, so a recall figure built from
        these contracts states its own denominator.
        """
        scanned = select_files(repo_path, self.source_extensions(), exclude, files)
        mounts = self._collect_mounts(scanned)

        from ..from_index import (
            CONSUMER_INDEX_SUFFIXES,
            extract_http_providers,
            symbols_for_content,
        )
        from .index_clients import extract_consumers

        contracts: list[Contract] = []
        for rel_path, suffix, content in scanned:
            ctx = ScanContext(repo_alias, rel_path, suffix, content, mounts, repo_index)
            # Python sits in both sets now, so one test covers the provider
            # pass (which reads the declarations above a span) and the consumer
            # pass (which reads the span itself).
            indexed = suffix in CONSUMER_INDEX_SUFFIXES
            # Only spans that can still describe this file's text supersede the
            # regex; a missing or overrun entry leaves the dialect in charge.
            symbols = (
                symbols_for_content(ctx, content.count("\n") + 1) if indexed else []
            )
            lines = content.split("\n") if symbols else []
            # Run the index pass first: it only supersedes the text dialect for
            # a file it actually produced routes for. A file that yielded no
            # decorated symbols (a grammar the parser stumbled on, a route
            # shape the queries do not capture) must not silently delete the
            # routes the regex can still see in the file's text.
            from_parse = (
                extract_http_providers(ctx, symbols, lines)
                if symbols and suffix in PYTHON
                else []
            )
            contracts.extend(from_parse)

            # Consumers, under the same per-file supersede rule: calls at the
            # sites of wrappers confirmed to reach an HTTP sink.
            consumers_from_parse: list[Contract] = []
            if symbols and suffix in CONSUMER_INDEX_SUFFIXES:
                consumers_from_parse, unresolved = extract_consumers(ctx, symbols)
                contracts.extend(consumers_from_parse)
                if stats is not None and unresolved:
                    stats["http_consumer_unresolved"] = (
                        stats.get("http_consumer_unresolved", 0) + unresolved
                    )

            for dialect in self.provider_dialects:
                if suffix not in dialect.extensions:
                    continue
                if from_parse and dialect.name in _INDEX_BACKED_DIALECTS:
                    continue  # superseded by the index pass above
                contracts.extend(dialect.extract(ctx))
            index_ids = {c.contract_id for c in consumers_from_parse}
            for dialect in self.consumer_dialects:
                if suffix not in dialect.extensions:
                    continue
                found = dialect.extract(ctx)
                if index_ids and dialect.name in _INDEX_BACKED_CONSUMER_DIALECTS:
                    # Supersede per *contract*, not per file. Dropping the whole
                    # dialect for a file the index read would delete anything
                    # the index cannot see but the regex can — an ``axios.get``
                    # beside a confirmed wrapper, say — which is a silent loss
                    # of recall in a change made to increase it. Only the
                    # duplicates the index already produced are removed.
                    found = [c for c in found if c.contract_id not in index_ids]
                contracts.extend(found)
        return contracts

    def _collect_mounts(self, files: list[tuple[str, str, str]]) -> dict[str, str]:
        """Build the unambiguous repo-wide ``router-var -> mount-prefix`` map.

        Each provider dialect may expose ``collect_mounts(content)``; results are
        merged across every file, dropping any router name mounted at conflicting
        prefixes (see :func:`merge_mount_maps`).
        """
        per_file: list[dict[str, str]] = []
        for _rel, suffix, content in files:
            for dialect in self.provider_dialects:
                collect = getattr(dialect, "collect_mounts", None)
                if collect is not None and suffix in dialect.extensions:
                    found = collect(content)
                    if found:
                        per_file.append(found)
        return merge_mount_maps(per_file)


__all__ = ["CONSUMER_DIALECTS", "PROVIDER_DIALECTS", "HttpExtractor", "normalize_http_path"]
