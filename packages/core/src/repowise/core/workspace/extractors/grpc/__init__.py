"""gRPC contract extraction.

Scans ``.proto`` files for service/rpc declarations (providers) and
language-specific source files for gRPC server registrations (providers) and
client stubs (consumers). Each language is an independent *dialect* module
registered in :data:`DIALECTS`; :class:`GrpcExtractor` owns only the file walk
and dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import ScanContext, iter_source_files
from .csharp import CSharpGrpcDialect
from .dialect import GrpcDialect
from .go import GoGrpcDialect
from .java import JavaGrpcDialect
from .proto import ProtoDialect, _extract_service_blocks
from .python import PythonGrpcDialect
from .typescript import TypeScriptGrpcDialect

if TYPE_CHECKING:
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

# One dialect per language/IDL; extension sets are disjoint, so exactly one runs
# per file.
DIALECTS: tuple[GrpcDialect, ...] = (
    ProtoDialect(),
    GoGrpcDialect(),
    JavaGrpcDialect(),
    PythonGrpcDialect(),
    TypeScriptGrpcDialect(),
    CSharpGrpcDialect(),
)


def _union_extensions(dialects: tuple[GrpcDialect, ...]) -> frozenset[str]:
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


class GrpcExtractor:
    """Extract gRPC contracts from proto files and language-specific source."""

    dialects: tuple[GrpcDialect, ...] = DIALECTS

    def extract(self, repo_path: Path, repo_alias: str = "") -> list[Contract]:
        all_exts = _union_extensions(self.dialects)

        contracts: list[Contract] = []
        for rel_path, suffix, content in iter_source_files(repo_path, all_exts):
            ctx = ScanContext(repo_alias, rel_path, suffix, content)
            for dialect in self.dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
        return contracts


__all__ = ["DIALECTS", "GrpcExtractor", "_extract_service_blocks"]
