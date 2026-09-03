"""gRPC contract extraction.

Scans ``.proto`` files for service/rpc declarations (providers) and
language-specific source files for gRPC server registrations (providers) and
client stubs (consumers). The generated-stub shapes are one table in
:mod:`.languages`; ``.proto`` keeps its own dialect because it is an IDL with a
grammar rather than a stub shape. :class:`GrpcExtractor` owns only the file walk
and dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import ScanContext, select_files
from .dialect import GrpcDialect
from .languages import LANGUAGES, LanguageGrpcDialect
from .proto import ProtoDialect, _extract_service_blocks

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

    from ..base import SourceFile

# One dialect per language/IDL; extension sets are disjoint, so exactly one runs
# per file.
DIALECTS: tuple[GrpcDialect, ...] = (
    ProtoDialect(),
    *(LanguageGrpcDialect(lang) for lang in LANGUAGES),
)


def _union_extensions(dialects: tuple[GrpcDialect, ...]) -> frozenset[str]:
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


class GrpcExtractor:
    """Extract gRPC contracts from proto files and language-specific source."""

    dialects: tuple[GrpcDialect, ...] = DIALECTS

    @classmethod
    def source_extensions(cls) -> frozenset[str]:
        """Every extension this extractor's dialects claim."""
        return _union_extensions(cls.dialects)

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
        files: Sequence[SourceFile] | None = None,
    ) -> list[Contract]:
        contracts: list[Contract] = []
        for rel_path, suffix, content in select_files(
            repo_path, self.source_extensions(), exclude, files
        ):
            ctx = ScanContext(repo_alias, rel_path, suffix, content)
            for dialect in self.dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
        return contracts


__all__ = ["DIALECTS", "GrpcExtractor", "_extract_service_blocks"]
