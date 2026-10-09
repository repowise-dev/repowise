"""C# socket endpoints and clients: SignalR hubs, ClientWebSocket, Unity websocket libraries.

``new WebSocket(...)`` is spelled alike by two Unity libraries, so each is read
only in a file naming its own namespace.
"""

from __future__ import annotations

import re

from ..langs import CSHARP
from ..strings import Arg
from .dialect import SocketCall, SocketDialect

_URL = Arg(pos=0)

DOTNET = SocketDialect(
    name="dotnet-sockets",
    calls=(
        SocketCall(
            head=re.compile(r"\bConnectAsync\s*\(\s*new\s+Uri\s*\(", re.IGNORECASE),
            role="consumer",
            transport="clientwebsocket",
            label="ClientWebSocket.ConnectAsync",
            extensions=CSHARP,
            name=_URL,
            confidence=0.75,
        ),
        SocketCall(
            head=re.compile(r"\bWithUrl\s*\(", re.IGNORECASE),
            role="consumer",
            transport="signalr",
            label="HubConnectionBuilder.WithUrl",
            extensions=CSHARP,
            name=_URL,
            requires=("HubConnectionBuilder", "SignalR.Client"),
        ),
        SocketCall(
            head=re.compile(r"\bnew\s+WebSocket\s*\(", re.IGNORECASE),
            role="consumer",
            transport="nativewebsocket",
            label="NativeWebSocket.WebSocket",
            extensions=CSHARP,
            name=_URL,
            confidence=0.75,
            requires=("NativeWebSocket",),
        ),
        SocketCall(
            head=re.compile(r"\bnew\s+(?:\w+\.)?WebSocket\s*\(", re.IGNORECASE),
            role="consumer",
            transport="websocketsharp",
            label="WebSocketSharp.WebSocket",
            extensions=CSHARP,
            name=_URL,
            confidence=0.75,
            requires=("WebSocketSharp",),
        ),
        SocketCall(
            head=re.compile(r"\.\s*MapHub\s*<\s*\w+\s*>\s*\(", re.IGNORECASE),
            role="provider",
            transport="signalr",
            label="MapHub",
            extensions=CSHARP,
            name=_URL,
            confidence=0.85,
        ),
    ),
)

__all__ = ["DOTNET"]
