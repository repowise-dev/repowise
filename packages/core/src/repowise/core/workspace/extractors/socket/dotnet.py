"""C# socket endpoints and clients: SignalR hubs, ClientWebSocket, Unity websocket libraries."""

from __future__ import annotations

import re

from ..langs import CSHARP
from .dialect import CSHARP_STRING, SocketDialect, SocketPattern

DOTNET = SocketDialect(
    name="dotnet-sockets",
    extensions=CSHARP,
    patterns=(
        SocketPattern(
            regex=re.compile(
                rf"""\bConnectAsync\s*\(\s*new\s+Uri\s*\(\s*{CSHARP_STRING}\s*\)""",
                re.IGNORECASE,
            ),
            role="consumer",
            transport="clientwebsocket",
            confidence=0.75,
            label="ClientWebSocket.ConnectAsync",
            prefix_group=1,
            identity_group=2,
        ),
        SocketPattern(
            regex=re.compile(rf"""\bWithUrl\s*\(\s*{CSHARP_STRING}""", re.IGNORECASE),
            role="consumer",
            transport="signalr",
            confidence=0.8,
            label="HubConnectionBuilder.WithUrl",
            prefix_group=1,
            identity_group=2,
            context=re.compile(
                r"\bHubConnectionBuilder\b|SignalR\.Client|Microsoft\.AspNetCore\.SignalR\.Client"
            ),
        ),
        SocketPattern(
            regex=re.compile(rf"""\bnew\s+WebSocket\s*\(\s*{CSHARP_STRING}""", re.IGNORECASE),
            role="consumer",
            transport="nativewebsocket",
            confidence=0.75,
            label="NativeWebSocket.WebSocket",
            prefix_group=1,
            identity_group=2,
            context=re.compile(r"\bNativeWebSocket\b"),
        ),
        SocketPattern(
            regex=re.compile(
                rf"""\bnew\s+(?:\w+\.)?WebSocket\s*\(\s*{CSHARP_STRING}""", re.IGNORECASE
            ),
            role="consumer",
            transport="websocketsharp",
            confidence=0.75,
            label="WebSocketSharp.WebSocket",
            prefix_group=1,
            identity_group=2,
            context=re.compile(r"\bWebSocketSharp\b"),
        ),
        SocketPattern(
            regex=re.compile(
                rf"""\.\s*MapHub\s*<\s*\w+\s*>\s*\(\s*{CSHARP_STRING}""", re.IGNORECASE
            ),
            role="provider",
            transport="signalr",
            confidence=0.85,
            label="MapHub",
            prefix_group=1,
            identity_group=2,
        ),
    ),
)

__all__ = ["DOTNET"]
