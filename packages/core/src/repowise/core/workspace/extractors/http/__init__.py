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

from ..base import ScanContext, iter_source_files
from .aspnet import AspNetDialect
from .csharp_http import CSharpHttpDialect
from .dialect import HttpDialect
from .express import ExpressDialect
from .fastapi import FastApiDialect
from .go import GoDialect
from .js_clients import JsClientsDialect
from .laravel import LaravelDialect
from .mounts import merge_mount_maps
from .paths import normalize_http_path
from .python_clients import PythonClientsDialect
from .rust_axum import RustAxumDialect
from .rust_clients import RustClientsDialect
from .spring import SpringDialect

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

# Route-declaration recognisers (one framework each).
PROVIDER_DIALECTS: tuple[HttpDialect, ...] = (
    ExpressDialect(),
    FastApiDialect(),
    SpringDialect(),
    LaravelDialect(),
    GoDialect(),
    AspNetDialect(),
    RustAxumDialect(),
)

# HTTP-client call recognisers (one client/language each).
CONSUMER_DIALECTS: tuple[HttpDialect, ...] = (
    JsClientsDialect(),
    PythonClientsDialect(),
    CSharpHttpDialect(),
    RustClientsDialect(),
)


def _union_extensions(dialects: tuple[HttpDialect, ...]) -> frozenset[str]:
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


class HttpExtractor:
    """Extract HTTP route contracts from source files via registered dialects."""

    provider_dialects: tuple[HttpDialect, ...] = PROVIDER_DIALECTS
    consumer_dialects: tuple[HttpDialect, ...] = CONSUMER_DIALECTS

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
    ) -> list[Contract]:
        """Scan all source files in *repo_path* and return Contract instances.

        Files are read once into memory so a first pass can collect repo-wide
        router mounts (``include_router(prefix=...)`` / ``app.use('/x', router)``)
        before the extraction pass stitches them onto each provider route.
        """
        all_exts = _union_extensions(self.provider_dialects) | _union_extensions(
            self.consumer_dialects
        )

        files = list(iter_source_files(repo_path, all_exts, exclude))
        mounts = self._collect_mounts(files)

        contracts: list[Contract] = []
        for rel_path, suffix, content in files:
            ctx = ScanContext(repo_alias, rel_path, suffix, content, mounts)
            for dialect in self.provider_dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
            for dialect in self.consumer_dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
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
