"""gRPC contract extraction.

Scans ``.proto`` files for service/rpc declarations (providers) and
language-specific source files for gRPC server registrations (providers) and
client stubs (consumers). The generated-stub shapes are one table in
:mod:`.languages`; ``.proto`` keeps its own dialect because it is an IDL with a
grammar rather than a stub shape. :class:`GrpcExtractor` is the registry on the
shared :class:`..dialect.DialectExtractor`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor
from .languages import LANGUAGES, LanguageGrpcDialect
from .proto import ProtoDialect, _extract_service_blocks

# One dialect per language/IDL; extension sets are disjoint, so exactly one runs
# per file. Service identity is carried in the contract id
# (``grpc::<service>/<method-or-*>``).
DIALECTS: tuple[ContractDialect, ...] = (
    ProtoDialect(),
    *(LanguageGrpcDialect(lang) for lang in LANGUAGES),
)


class GrpcExtractor(DialectExtractor):
    """Extract gRPC contracts from proto files and language-specific source."""

    dialects = DIALECTS


__all__ = ["DIALECTS", "GrpcExtractor", "_extract_service_blocks"]
