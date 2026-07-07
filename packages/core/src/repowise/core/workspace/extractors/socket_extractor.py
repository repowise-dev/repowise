"""Socket / websocket contract extraction.

Scans source files for common websocket-style consumer and provider patterns,
with an initial focus on Unity / C# clients and path-based server endpoints.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .base import iter_source_files
from .http.paths import (
    extract_path_from_url,
    is_unusable_consumer_path,
    normalize_http_path,
    strip_leading_base_expr,
)
from .langs import CSHARP, PYTHON

if TYPE_CHECKING:
    from collections.abc import Callable

    from repowise.core.workspace.contracts import Contract

_log = logging.getLogger("repowise.workspace.extractors.socket")

_EXTENSIONS = CSHARP | PYTHON

_STR = r"""(\$?@?)"([^"]*)\""""


@dataclass(frozen=True)
class _PatternDef:
    regex: re.Pattern[str]
    role: str
    transport: str
    confidence: float
    label: str
    identity_group: int
    prefix_group: int | None = None
    context_regex: re.Pattern[str] | None = None


_SIGNALR_CLIENT_CONTEXT_RE = re.compile(
    r"\bHubConnectionBuilder\b|SignalR\.Client|Microsoft\.AspNetCore\.SignalR\.Client"
)

_NATIVE_WS_CONTEXT_RE = re.compile(r"\bNativeWebSocket\b")
_WEBSOCKETSHARP_CONTEXT_RE = re.compile(r"\bWebSocketSharp\b")

_CLIENT_PATTERNS: list[_PatternDef] = [
    _PatternDef(
        regex=re.compile(
            rf"""\bConnectAsync\s*\(\s*new\s+Uri\s*\(\s*{_STR}\s*\)""",
            re.IGNORECASE,
        ),
        role="consumer",
        transport="clientwebsocket",
        confidence=0.75,
        label="ClientWebSocket.ConnectAsync",
        prefix_group=1,
        identity_group=2,
    ),
    _PatternDef(
        regex=re.compile(rf"""\bWithUrl\s*\(\s*{_STR}""", re.IGNORECASE),
        role="consumer",
        transport="signalr",
        confidence=0.8,
        label="HubConnectionBuilder.WithUrl",
        prefix_group=1,
        identity_group=2,
        context_regex=_SIGNALR_CLIENT_CONTEXT_RE,
    ),
    _PatternDef(
        regex=re.compile(rf"""\bnew\s+WebSocket\s*\(\s*{_STR}""", re.IGNORECASE),
        role="consumer",
        transport="nativewebsocket",
        confidence=0.75,
        label="NativeWebSocket.WebSocket",
        prefix_group=1,
        identity_group=2,
        context_regex=_NATIVE_WS_CONTEXT_RE,
    ),
    _PatternDef(
        regex=re.compile(rf"""\bnew\s+(?:\w+\.)?WebSocket\s*\(\s*{_STR}""", re.IGNORECASE),
        role="consumer",
        transport="websocketsharp",
        confidence=0.75,
        label="WebSocketSharp.WebSocket",
        prefix_group=1,
        identity_group=2,
        context_regex=_WEBSOCKETSHARP_CONTEXT_RE,
    ),
]

_PROVIDER_PATTERNS: list[_PatternDef] = [
    _PatternDef(
        regex=re.compile(rf"""\.\s*MapHub\s*<\s*\w+\s*>\s*\(\s*{_STR}""", re.IGNORECASE),
        role="provider",
        transport="signalr",
        confidence=0.85,
        label="MapHub",
        prefix_group=1,
        identity_group=2,
    ),
    _PatternDef(
        regex=re.compile(r"""@(?:app|router)\.websocket\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE),
        role="provider",
        transport="fastapi-websocket",
        confidence=0.8,
        label="@app.websocket",
        identity_group=1,
    ),
]


def _to_template(prefix: str, text: str) -> str:
    """Rewrite a C# interpolated string body into ``${expr}`` template form."""
    if "$" in prefix:
        return text.replace("{", "${")
    return text


def _normalize_socket_identity(raw: str, prefix: str = "") -> str | None:
    """Extract the stable path/channel identity from a socket URL-like string."""
    value = _to_template(prefix, raw).strip()
    if not value or "/" not in value:
        return None
    path = extract_path_from_url(value)
    path, _base_token = strip_leading_base_expr(path)
    norm = normalize_http_path(path)
    if norm in ("", "/") or is_unusable_consumer_path(norm):
        return None
    return norm


class SocketExtractor:
    """Extract socket/websocket contracts from source files."""

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
    ) -> list[Contract]:
        from repowise.core.workspace.contracts import Contract

        contracts: list[Contract] = []
        seen: set[tuple[str, str, str]] = set()

        for rel_path, _suffix, content in iter_source_files(repo_path, _EXTENSIONS, exclude):
            for pdef in _CLIENT_PATTERNS + _PROVIDER_PATTERNS:
                if pdef.context_regex is not None and not pdef.context_regex.search(content):
                    continue
                for match in pdef.regex.finditer(content):
                    prefix = match.group(pdef.prefix_group) if pdef.prefix_group else ""
                    identity = _normalize_socket_identity(
                        match.group(pdef.identity_group),
                        prefix,
                    )
                    if identity is None:
                        continue
                    contract_id = f"socket::{identity}"
                    dedup_key = (rel_path, contract_id, pdef.role)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    contracts.append(
                        Contract(
                            repo=repo_alias,
                            contract_id=contract_id,
                            contract_type="socket",
                            role=pdef.role,
                            file_path=rel_path,
                            symbol_name=f"{pdef.label}('{identity}')",
                            confidence=pdef.confidence,
                            service=None,
                            meta={
                                "path": identity,
                                "transport": pdef.transport,
                            },
                        )
                    )
        return contracts
