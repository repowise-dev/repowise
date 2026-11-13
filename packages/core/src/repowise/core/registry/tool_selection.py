"""Pure selection of which MCP tools a server exposes.

The surface is resolved from four inputs:

1. each tool's metadata (``default`` / ``requires_workspace`` /
   ``available_when`` on :class:`~repowise.core.registry.ToolEntry`),
2. whether the server is running in workspace mode,
3. an optional user override (a CLI ``--tools`` flag or a ``mcp.tools`` block in
   ``.repowise/config.yaml``), and
4. optional :class:`AvailabilityFacts` about the repository, which an entry's
   ``available_when`` predicate reads.

With no override the surface depends on the client: a host that defers tool
schemas until the model asks for them (Claude Code) gets the ``standard``
profile, every ``default`` tool usable in this mode; any other client gets
``core``, because it pays every listed schema on every turn. The override can
name a profile (``core``, ``lean``, ``standard``, ``all``), adjust one with
``+name`` / ``-name`` deltas, or give an explicit list. Nothing here does I/O:
the server gathers the inputs and applies the result to its FastMCP instance.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

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

# A value of "core" enables the core profile: the four tools that answer a
# question about the code, plus get_why for the same history-why reason as
# lean (list_repos again joins in workspace mode). It is the default for
# clients that load every schema up front, where the full default surface
# costs about twice as many tokens per turn.
CORE = "core"
CORE_TOOLS = frozenset({"get_answer", "get_context", "get_symbol", "search_codebase", "get_why"})

# A value of "standard" enables every ``default`` tool usable in this mode: the
# surface a client that defers schemas gets when nothing is configured.
STANDARD = "standard"

PROFILES = (CORE, LEAN, STANDARD, ALL)

# Agent slugs (repowise.core.agents.identity) whose host defers MCP tool
# schemas until the model searches for them, so a larger surface costs only
# its tool names per turn. Every other client, including one that announced no
# name, gets the core profile by default.
DEFERRED_SCHEMA_CLIENTS = frozenset({"claude_code"})


@dataclass(frozen=True)
class AvailabilityFacts:
    """Plain repository-level facts an ``available_when`` predicate can read.

    ``counts`` maps a fact name to a number (e.g. how many of something the
    index holds). A missing key reads as absent, so predicates should use
    ``facts.counts.get(name, 0)``. Kept deliberately small: add a field only
    when a registered predicate needs it and the server can compute it cheaply.
    """

    counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Snapshot into a read-only view so the hash below cannot drift.
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))

    def __hash__(self) -> int:
        return hash(frozenset(self.counts.items()))


def normalize_override(override: str | Sequence[str] | None) -> list[str] | None:
    """Coerce a raw override (CLI/config) into a clean list of tokens.

    Accepts ``None`` (no override), a comma- or whitespace-separated string, or
    a sequence of strings. Returns ``None`` when nothing meaningful was given so
    callers fall through to the default surface.
    """
    if override is None:
        return None
    items = [override] if isinstance(override, str) else [str(t) for t in override]
    # A list item may itself hold several tokens ("core +get_health").
    tokens = [t for item in items for t in item.replace(",", " ").split()]
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


def _is_delta(tokens: list[str]) -> bool:
    return all(t[0] in "+-" for t in tokens)


def default_profile(client: str | None) -> str:
    """The profile a client gets when nothing is configured.

    ``client`` is an agent slug resolved from the MCP ``clientInfo`` name.
    ``None`` means there is no MCP client at all (in-product chat, the settings
    page), which keeps the standard surface.
    """
    if client is None or client in DEFERRED_SCHEMA_CLIENTS:
        return STANDARD
    return CORE


def _profile_names(
    profile: str, default_surface: set[str], usable: set[str], *, is_workspace: bool
) -> set[str]:
    extras = LEAN_WORKSPACE_EXTRAS if is_workspace else frozenset()
    if profile == ALL:
        return set(usable)
    if profile == STANDARD:
        return set(default_surface)
    members = CORE_TOOLS if profile == CORE else LEAN_TOOLS
    return usable & (members | extras)


def split_profile(tokens: Sequence[str] | None) -> tuple[str | None, list[str]]:
    """Split a normalized override into its leading profile and the rest."""
    if not tokens:
        return None, []
    head = tokens[0].lower()
    if head in PROFILES:
        return head, list(tokens[1:])
    return None, list(tokens)


def _apply_deltas(
    default_surface: set[str],
    tokens: list[str],
    catalog: Mapping[str, ToolEntry],
    usable: set[str],
    *,
    is_workspace: bool,
) -> set[str]:
    """Apply ``+name`` / ``-name`` tokens, in order, to *default_surface*.

    A token without a sign after a profile is read as ``+name``.
    """
    enabled = set(default_surface)
    for token in tokens:
        op, raw = (token[0], token[1:].strip()) if token[0] in "+-" else ("+", token)
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
    client: str | None = None,
) -> set[str]:
    """Return the set of tool names a server should expose.

    ``override`` semantics:

    - ``None`` / empty: the client's default profile (:func:`default_profile`).
    - a profile name (``core``, ``lean``, ``standard``, ``all``), optionally
      followed by ``+name`` / ``-name`` deltas applied to it.
    - all tokens prefixed ``+``/``-``: deltas applied to the client's default.
    - otherwise: an explicit allowlist (only the named tools).

    ``client`` is the agent slug of the MCP client asking; it matters only
    when the override names no profile and no explicit list.

    A tool is usable when its mode fits (workspace-only tools need a workspace)
    and its ``available_when`` predicate, if any, holds for *facts*. No
    override can enable an unusable tool, even by name: it could not do useful
    work here. ``facts=None`` skips predicates.
    """
    catalog = {e.name: e for e in entries}
    usable = _usable_names(catalog, is_workspace=is_workspace, facts=facts)
    default_surface = {name for name in usable if catalog[name].default}

    profile, rest = split_profile(normalize_override(override))
    if profile is None and rest and not _is_delta(rest):
        return _allowlist(rest, catalog, usable, is_workspace=is_workspace)
    base = _profile_names(
        profile or default_profile(client), default_surface, usable, is_workspace=is_workspace
    )
    return _apply_deltas(base, rest, catalog, usable, is_workspace=is_workspace)


def _never_called() -> None:
    """Stand-in callable for the name-only entries :func:`listed_default_tools` builds."""


def listed_default_tools(
    names: Iterable[str],
    *,
    client: str | None,
    override: str | Sequence[str] | None = None,
) -> frozenset[str]:
    """Which of *names*, all default tools, a single-repo server lists to *client*.

    For text written ahead of time that names tools (a generated agent file),
    where no tool catalog is loaded. Every name the override mentions joins the
    catalog too, so an explicit list or a delta naming an opt-in tool resolves
    without an "unknown tool" warning.
    """
    wanted = set(names)
    mentioned = {t.lstrip("+-") for t in normalize_override(override) or ()}
    entries = [ToolEntry(fn=_never_called, name=n) for n in (wanted | mentioned) - {""}]
    enabled = resolve_enabled_tools(entries, is_workspace=False, override=override, client=client)
    return frozenset(enabled & wanted)


__all__ = [
    "ALL",
    "CORE",
    "CORE_TOOLS",
    "DEFERRED_SCHEMA_CLIENTS",
    "LEAN",
    "LEAN_TOOLS",
    "LEAN_WORKSPACE_EXTRAS",
    "PROFILES",
    "STANDARD",
    "AvailabilityFacts",
    "default_profile",
    "is_available",
    "listed_default_tools",
    "normalize_override",
    "resolve_enabled_tools",
    "split_profile",
]
