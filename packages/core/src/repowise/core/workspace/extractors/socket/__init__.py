"""Socket / websocket contract extraction.

Each language's endpoint and connect shapes are a :class:`.dialect.SocketDialect`
table registered in :data:`DIALECTS`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor, file_identity
from .dotnet import DOTNET
from .python import PYTHON_SOCKETS

DIALECTS: tuple[ContractDialect, ...] = (DOTNET, PYTHON_SOCKETS)


class SocketExtractor(DialectExtractor):
    """Extract socket/websocket contracts from source files."""

    dialects = DIALECTS
    identity = staticmethod(file_identity)


__all__ = ["DIALECTS", "SocketExtractor"]
