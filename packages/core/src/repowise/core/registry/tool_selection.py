"""Pure selection of which MCP tools a server exposes.

The surface is resolved from four inputs:

1. each tool's metadata (``default`` / ``requires_workspace`` /
   ``available_when`` on :class:`~repowise.core.registry.ToolEntry`),
2. whether the server is running in workspace mode,
3. an optional user override (a CLI ``--tools`` flag or a ``mcp.tools`` block in
   ``.repowise/config.yaml``), and
4. optional :class:`AvailabilityFacts` about the repository, which an entry's
   ``available_when`` predicate reads.

The default surface is every ``default`` tool that is usable in this mode. The
override can replace that set (an explicit allowlist, ``all``, ``lean``) or
adjust it (``+name`` / ``-name`` deltas). Nothing here does I/O: the server
gathers the inputs and applies the result to its FastMCP instance.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .mcp_tool_registry import ToolEntry

_log = logging.getLogger("repowise.mcp")

# A value (CLI flag or config) of "all" enables every registered tool that is
# usable in the current mode, including opt-in and workspace-only tools.
ALL = "all"

# A value of "lean" enables the agent-lean profile: the pre-edit tools a
# coding agent actually reaches for, small enough that every schema can stay
# always-loaded instead of deferred behind a tool-search round trip. list_repos
# joins it in workspace mode only, where repo aliases must be discoverable.
# get_why belongs in the lean set: why/history questions are the category no
# graph- or search-shaped tool can answer, and a comparative benchmark run
# with a lean surface that omitted it scored repowise BELOW a bare agent on
# history-why questions — the differentiator was configured out, not absent.
LEAN = "lean"
LEAN_TOOLS = frozenset(
    {"get_answer", "get_context", "get_symbol", "search_codebase", "get_risk", "get_why"}
)
LEAN_WORKSPACE_EXTRAS = frozenset({"list_repos"})


@dataclass(frozen=True)
class AvailabilityFacts:
    """Plain repository-level facts an ``available_when`` predicate can read.

    ``counts`` maps a fact name to a number (e.g. how many of something the
    index holds). A missing key reads as absent, so predicates should use
    ``facts.counts.get(name, 0)``. Kept deliberately small: add a field only
    when a registered predicate needs it and the server can compute it cheaply.
    """

    counts: Mapping[str, int] = field(default_factory=dict)


def normalize_override(override: str | Sequence[str] | None) -> list[str] | None:
    """Coerce a raw override (CLI/config) into a clean list of tokens.

    Accepts ``None`` (no override), a comma- or whitespace-separated string, or
    a sequence of strings. Returns ``None`` when nothing meaningful was given so
    callers fall through to the default surface.
    """
    if override is None:
        return None
    if isinstance(override, str):
        tokens = [t.strip() for t in override.replace(",", " ").split()]
    else:
        tokens = [str(t).strip() for t in override]
    tokens = [t for t in tokens if t]
    return tokens or None


def is_available(entry: ToolEntry, facts: AvailabilityFacts | None) -> bool:
    """Whether *entry*'s ``available_when`` predicate admits these facts.

    ``facts=None`` means the caller has no facts, so predicates are skipped and
    every entry counts as available. A predicate that raises hides its tool
    rather than taking the whole surface down with it.
    """
    if facts is None or entry.available_when is None:
        return True
    try:
        return bool(entry.available_when(facts))
    except Exception:
        _log.warning("MCP tool %r availability check failed; hiding it", entry.name, exc_info=True)
        return False


def _usable_names(
    catalog: Mapping[str, ToolEntry], *, is_workspace: bool, facts: AvailabilityFacts | None
) -> set[str]:
    return {
        name
        for name, entry in catalog.items()
        if (is_workspace or not entry.requires_workspace) and is_available(entry, facts)
    }


def _resolve_name(
    raw: str, catalog: Mapping[str, ToolEntry], usable: set[str], *, is_workspace: bool
) -> str | None:
    """Return *raw* when an override may enable it, logging why it may not."""
    entry = catalog.get(raw)
    if entry is None:
        _log.warning("Ignoring unknown MCP tool in selection: %r", raw)
        return None
    if raw in usable:
        return raw
    if entry.requires_workspace and not is_workspace:
        _log.warning("Ignoring workspace-only MCP tool %r outside workspace mode", raw)
    else:
        _log.warning("Ignoring MCP tool %r: not available for this repository", raw)
    return None


def _is_keyword(tokens: list[str], keyword: str) -> bool:
    return len(tokens) == 1 and tokens[0].lower() == keyword


def _is_delta(tokens: list[str]) -> bool:
    return all(t[0] in "+-" for t in tokens)


def _lean_names(is_workspace: bool) -> frozenset[str]:
    return (LEAN_TOOLS | LEAN_WORKSPACE_EXTRAS) if is_workspace else LEAN_TOOLS


def _apply_deltas(
    default_surface: set[str],
    tokens: list[str],
    catalog: Mapping[str, ToolEntry],
    usable: set[str],
    *,
    is_workspace: bool,
) -> set[str]:
    """Apply ``+name`` / ``-name`` tokens, in order, to the default surface."""
    enabled = set(default_surface)
    for token in tokens:
        op, raw = token[0], token[1:].strip()
        if op == "-":
            enabled.discard(raw)
        elif _resolve_name(raw, catalog, usable, is_workspace=is_workspace):
            enabled.add(raw)
    return enabled


def _allowlist(
    tokens: list[str],
    catalog: Mapping[str, ToolEntry],
    usable: set[str],
    *,
    is_workspace: bool,
) -> set[str]:
    """Only the named tools, minus any an override may not enable."""
    return {t for t in tokens if _resolve_name(t, catalog, usable, is_workspace=is_workspace)}


def resolve_enabled_tools(
    entries: Iterable[ToolEntry],
    *,
    is_workspace: bool,
    override: str | Sequence[str] | None = None,
    facts: AvailabilityFacts | None = None,
) -> set[str]:
    """Return the set of tool names a server should expose.

    ``override`` semantics:

    - ``None`` / empty: the curated default surface.
    - ``"all"`` (or ``["all"]``): every tool usable in the current mode.
    - ``"lean"``: the agent-lean profile (see :data:`LEAN_TOOLS`).
    - all tokens prefixed ``+``/``-``: deltas applied to the default surface.
    - otherwise: an explicit allowlist (only the named tools).

    A tool is usable when its mode fits (workspace-only tools need a workspace)
    and its ``available_when`` predicate, if any, holds for *facts*. No
    override can enable an unusable tool, even by name: it could not do useful
    work here. ``facts=None`` skips predicates.
    """
    catalog = {e.name: e for e in entries}
    usable = _usable_names(catalog, is_workspace=is_workspace, facts=facts)
    default_surface = {name for name in usable if catalog[name].default}

    tokens = normalize_override(override)
    if tokens is None:
        return default_surface
    if _is_keyword(tokens, ALL):
        return usable
    if _is_keyword(tokens, LEAN):
        return usable & _lean_names(is_workspace)
    if _is_delta(tokens):
        return _apply_deltas(default_surface, tokens, catalog, usable, is_workspace=is_workspace)
    return _allowlist(tokens, catalog, usable, is_workspace=is_workspace)


__all__ = [
    "ALL",
    "LEAN",
    "LEAN_TOOLS",
    "LEAN_WORKSPACE_EXTRAS",
    "AvailabilityFacts",
    "is_available",
    "normalize_override",
    "resolve_enabled_tools",
]
