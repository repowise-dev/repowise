"""Pure normalization rules shared by future capture adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping

IDENTITY_MAPPING_VERSION = "mcp_client_info_v1"
_IDENTITIES = {
    "claude": "claude_code",
    "claudecode": "claude_code",
    "codex": "codex",
    "opencode": "opencode",
    "hermes": "hermes",
    "cursor": "cursor",
    "vscode": "vscode",
}
METADATA_ALLOWLIST = frozenset({"client_info_normalized", "identity_mapping_version"})


def normalize_mcp_identity(client_name: object | None) -> tuple[str, dict[str, str]]:
    """Map self-declared MCP clientInfo through the versioned allow-list."""
    normalized = re.sub(r"[^a-z0-9]", "", str(client_name or "").lower())[:64]
    identity = _IDENTITIES.get(normalized, "unknown")
    metadata = {"identity_mapping_version": IDENTITY_MAPPING_VERSION}
    if normalized:
        metadata["client_info_normalized"] = normalized
    return identity, metadata


def normalize_metadata(metadata: Mapping[str, object] | None) -> dict[str, str]:
    """Reject arbitrary telemetry and bound the two permitted string fields."""
    if metadata is None:
        return {}
    if not set(metadata) <= METADATA_ALLOWLIST:
        raise ValueError(f"metadata keys must be drawn from {sorted(METADATA_ALLOWLIST)}")
    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError(f"metadata.{key} must be a non-empty string of at most 64 characters")
        normalized[key] = value
    return normalized
