"""Socket / websocket contract extraction.

Each library's endpoints, connections and events are a dialect (usually a
:class:`.dialect.SocketDialect` table) registered in :data:`DIALECTS`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor, file_identity
from .broadcasting import ECHO, LARAVEL_BROADCAST
from .dotnet import DOTNET
from .python import PYTHON_SOCKETS
from .socketio import SOCKET_IO, WEBSOCKETS

DIALECTS: tuple[ContractDialect, ...] = (
    DOTNET,
    PYTHON_SOCKETS,
    WEBSOCKETS,
    SOCKET_IO,
    LARAVEL_BROADCAST,
    ECHO,
)


class SocketExtractor(DialectExtractor):
    """Extract socket/websocket contracts from source files."""

    dialects = DIALECTS
    identity = staticmethod(file_identity)


__all__ = ["DIALECTS", "SocketExtractor"]
