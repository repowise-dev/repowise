"""Pure normalization rules shared by future capture adapters."""

from __future__ import annotations

from collections.abc import Mapping

from repowise.core.agents.identity import (
    CLIENT_IDENTITY_MAPPING_VERSION,
    normalize_client_name,
    resolve_client_identity,
)

#: Re-exported so an event keeps recording which rules produced its attribution.
#: The rules themselves belong to the identity registry; this module only shapes
#: their result into event metadata.
IDENTITY_MAPPING_VERSION = CLIENT_IDENTITY_MAPPING_VERSION
METADATA_ALLOWLIST = frozenset({"client_info_normalized", "identity_mapping_version"})


def normalize_mcp_identity(client_name: object | None) -> tuple[str, dict[str, str]]:
    """Attribute a self-declared MCP clientInfo name, and record how.

    Resolution lives in :mod:`repowise.core.agents.identity` — savings does not
    enumerate agents. What *is* savings' concern is the audit trail: the
    normalized form that was looked up and the version of the rules that looked
    it up, so an attribution can be re-derived from the stored row later.
    """
    normalized = normalize_client_name(client_name)
    metadata = {"identity_mapping_version": IDENTITY_MAPPING_VERSION}
    if normalized:
        metadata["client_info_normalized"] = normalized
    return resolve_client_identity(normalized), metadata


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
