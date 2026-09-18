"""Canonical, backward-compatible projection of an index's actual scope.

Writers persist this object in ``state.json:index_scope``.  Readers always go
through :func:`resolve_index_scope`, which also gives legacy state a stable,
conservative shape: missing evidence is ``unknown``/``None``, never inferred as
complete from a configured limit or from the absence of a finding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from repowise.core.store_location import resolve_store_dir

INDEX_SCOPE_VERSION = 1

# repo dir -> (identity of the files it was built from, scope). Every MCP
# response embeds this, so without a cache each one re-reads state.json and
# re-parses config.yaml. Keyed on mtime+size so an index rebuild invalidates it.
_SCOPE_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any] | None]] = {}
_SCOPE_CACHE_MAX = 16


def _file_identity(path: Path) -> tuple[Any, ...]:
    try:
        stat = path.stat()
    except OSError:
        return ()
    return (stat.st_mtime_ns, stat.st_size)


def load_index_scope(repo_path: str | Path) -> dict[str, Any] | None:
    """Load a repository's scope; return ``None`` when no readable state exists."""
    repowise_dir = resolve_store_dir(repo_path)
    state_file = repowise_dir / "state.json"
    config_file = repowise_dir / "config.yaml"

    key = str(repowise_dir)
    identity = (_file_identity(state_file), _file_identity(config_file))
    cached = _SCOPE_CACHE.get(key)
    if cached is not None and cached[0] == identity:
        # Callers embed this in a response that later passes get mutated.
        return deepcopy(cached[1])

    scope = _read_index_scope(state_file, config_file)
    if len(_SCOPE_CACHE) >= _SCOPE_CACHE_MAX:
        _SCOPE_CACHE.clear()
    _SCOPE_CACHE[key] = (identity, scope)
    return deepcopy(scope)


def _read_index_scope(state_file: Path, config_file: Path) -> dict[str, Any] | None:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None

    try:
        import yaml
    except ImportError:
        return resolve_index_scope(state)
    try:
        config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        config = {}
    return resolve_index_scope(state, config)


def _choice(value: Any, choices: set[str]) -> str:
    return value if isinstance(value, str) and value in choices else "unknown"


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def resolve_index_scope(
    state: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the one machine-readable scope contract for every output surface."""
    state = state if isinstance(state, Mapping) else {}
    config = config if isinstance(config, Mapping) else {}
    stored = state.get("index_scope")
    scope = deepcopy(stored) if isinstance(stored, Mapping) else {}

    explicit_docs_mode = state.get("docs_mode")
    provenance = {
        "none": "none",
        "deterministic": "template",
        "llm": "model",
    }.get(explicit_docs_mode, "unknown")

    pages = scope.get("file_pages") if isinstance(scope.get("file_pages"), Mapping) else {}
    configured_cap = pages.get("configured_cap")
    if configured_cap is None and "max_file_pages" in config:
        configured_cap = _number(config.get("max_file_pages"))

    provider = scope.get("provider") if isinstance(scope.get("provider"), Mapping) else {}
    search = scope.get("search") if isinstance(scope.get("search"), Mapping) else {}
    analysis = scope.get("analysis") if isinstance(scope.get("analysis"), Mapping) else {}
    upgrade = state.get("full_upgrade")
    if not isinstance(upgrade, Mapping):
        upgrade = scope.get("upgrade") if isinstance(scope.get("upgrade"), Mapping) else {}

    run_mode = _choice(state.get("run_mode", scope.get("run_mode")), {"fast", "standard"})
    git_tier = _choice(state.get("git_tier", scope.get("git_tier")), {"essential", "full"})
    if isinstance(stored, Mapping):
        provenance = _choice(
            scope.get("content_provenance", provenance), {"none", "template", "model"}
        )

    unavailable = analysis.get("unavailable", [])
    degraded = state.get("degraded", [])
    if isinstance(unavailable, list) and isinstance(degraded, list):
        unavailable = [*unavailable, *degraded]
    skipped = analysis.get("skipped", [])
    return {
        "version": INDEX_SCOPE_VERSION,
        "run_mode": run_mode,
        "content_provenance": provenance,
        "git_tier": git_tier,
        "git_commit_cap": _number(scope.get("git_commit_cap", config.get("commit_limit"))),
        "git_history_coverage": (
            deepcopy(state.get("git_history_coverage"))
            if isinstance(state.get("git_history_coverage"), Mapping)
            else deepcopy(scope.get("git_history_coverage"))
            if isinstance(scope.get("git_history_coverage"), Mapping)
            else None
        ),
        "file_pages": {
            "configured_cap": configured_cap,
            "effective_cap": _number(pages.get("effective_cap")),
            "eligible": _number(pages.get("eligible")),
            "generated": _number(pages.get("generated")),
            "omitted": _number(pages.get("omitted")),
        },
        "analysis": {
            "unavailable": sorted({str(v) for v in unavailable})
            if isinstance(unavailable, list)
            else [],
            "skipped": sorted({str(v) for v in skipped}) if isinstance(skipped, list) else [],
        },
        "upgrade": {
            "status": _choice(
                upgrade.get("status"),
                {"pending", "running", "resumable", "failed", "complete", "not_applicable"},
            ),
            "retryable": upgrade.get("retryable")
            if isinstance(upgrade.get("retryable"), bool)
            else None,
            "completed_stages": list(upgrade.get("completed_stages", []))
            if isinstance(upgrade.get("completed_stages"), list)
            else [],
            "next_stage": _string(upgrade.get("next_stage")),
        },
        "provider": {
            "name": _string(provider.get("name", state.get("provider"))),
            "model": _string(provider.get("model", state.get("model"))),
            "embedder": _string(provider.get("embedder", config.get("embedder"))),
            "reused": provider.get("reused") if isinstance(provider.get("reused"), bool) else None,
            "model_cost_possible": provider.get("model_cost_possible")
            if isinstance(provider.get("model_cost_possible"), bool)
            else None,
        },
        "search": {
            "full_text": _choice(search.get("full_text"), {"available", "unavailable", "pending"}),
            "semantic": _choice(search.get("semantic"), {"available", "unavailable", "pending"}),
            "next_command": _string(search.get("next_command")),
        },
    }


#: What a routine response says instead of the whole canonical scope. The full
#: object is roughly 900 characters, which on a small ``get_symbol`` response
#: was about a third of everything the agent received — paid for on every call
#: to answer a question almost none of them asked.
COMPACT_INDEX_SCOPE_PROJECTION = "compact"
CANONICAL_INDEX_SCOPE_PROJECTION = "full"

#: Set to ``full`` to serve the canonical scope everywhere, as builds before
#: the compact projection did. The compatibility window for a reader that
#: parses the whole object and cannot yet ask for it by name.
INDEX_SCOPE_ENV = "REPOWISE_MCP_INDEX_SCOPE"


#: Upgrade states that mean the index is still being built. Reported ahead of
#: everything else: they explain the rest and they resolve on their own.
_UPGRADE_IN_FLIGHT = frozenset({"pending", "running", "resumable"})

#: Search legs. A leg that is "pending" is not usable yet, which is a smaller
#: problem than one that failed but is not nothing.
_SEARCH_LEGS = ("full_text", "semantic")


def _section(scope: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """One sub-block of a scope, or an empty one when it is missing or junk."""
    section = scope.get(name)
    return section if isinstance(section, Mapping) else {}


def _is_degraded(upgrade: Mapping[str, Any], scope: Mapping[str, Any]) -> bool:
    """Whether some part of the index was meant to exist and does not."""
    search = _section(scope, "search")
    return (
        upgrade.get("status") == "failed"
        or bool(_section(scope, "analysis").get("unavailable"))
        or any(search.get(leg) == "unavailable" for leg in _SEARCH_LEGS)
    )


def _is_partial(scope: Mapping[str, Any]) -> bool:
    """Whether the index is sound but does not yet cover everything."""
    search = _section(scope, "search")
    return (
        bool(_number(_section(scope, "file_pages").get("omitted")))
        or bool(_section(scope, "analysis").get("skipped"))
        or any(search.get(leg) == "pending" for leg in _SEARCH_LEGS)
    )


def _is_unexamined(scope: Mapping[str, Any]) -> bool:
    """Whether the evidence that would tell complete from partial is missing.

    A legacy index, or any state with no ``index_scope`` key, projects every
    one of these as ``unknown``/``None``. Reading that as "nothing is wrong"
    would turn absence of evidence into a claim of completeness, which is the
    one thing this module promises never to do.
    """
    search = _section(scope, "search")
    return _number(_section(scope, "file_pages").get("omitted")) is None or any(
        search.get(leg) == "unknown" for leg in _SEARCH_LEGS
    )


def _scope_status(scope: Mapping[str, Any]) -> str:
    """One word for how much of the intended index actually exists.

    Ordered worst-first on purpose: an index that is both mid-upgrade and
    missing pages reports the upgrade, because that is the condition that
    explains the rest and the one that will change on its own. ``complete``
    is last and is reached only when the evidence exists and is clean, so it
    never stands in for evidence that was merely never recorded — that is
    ``unknown``, which is a different answer and says so.
    """
    upgrade = _section(scope, "upgrade")
    if upgrade.get("status") in _UPGRADE_IN_FLIGHT:
        return "upgrading"
    if _is_degraded(upgrade, scope):
        return "degraded"
    if _is_partial(scope):
        return "partial"
    if _is_unexamined(scope):
        return "unknown"
    return "complete"


def index_scope_fingerprint(scope: Mapping[str, Any]) -> str:
    """A short stable digest of one canonical scope.

    Two responses carrying the same fingerprint were built against the same
    scope, so a caller holding the full object from an earlier call knows its
    copy still describes this one — without either side resending it.

    sha256 over the canonical JSON, first 12 hex characters. No ``default``
    serializer: every field here is JSON-derived or passed through
    :func:`_number`/:func:`_string`/:func:`_choice`, and a fallback would let a
    future plain object stringify to its address and churn the digest every
    process while still looking valid. Raising is the better failure.
    """
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def compact_index_scope(
    scope: Mapping[str, Any] | None, *, full_hint: str = "get_overview()"
) -> dict[str, Any] | None:
    """The routine-response projection of a canonical scope.

    Keeps what changes how an answer should be read — the run mode, where the
    prose came from, how much git there is, and whether the index is whole —
    plus a fingerprint identifying the full object and the call that returns
    it. Everything else is a diagnostic, and a diagnostic that rides on every
    response is a tax, not a disclosure.

    The one exception is ``degraded_analyses``, carried only when non-empty:
    which analysis is missing changes what an answer means, and "health failed"
    and "the graph failed" are not the same warning.
    """
    if not isinstance(scope, Mapping):
        return None
    compact: dict[str, Any] = {
        "version": scope.get("version", INDEX_SCOPE_VERSION),
        "projection": COMPACT_INDEX_SCOPE_PROJECTION,
        "run_mode": scope.get("run_mode", "unknown"),
        "content_provenance": scope.get("content_provenance", "unknown"),
        "git_tier": scope.get("git_tier", "unknown"),
        "status": _scope_status(scope),
        "fingerprint": index_scope_fingerprint(scope),
        "full": full_hint,
    }
    unavailable = _section(scope, "analysis").get("unavailable")
    if unavailable:
        compact["degraded_analyses"] = list(unavailable)
    return compact


def stamp_index_scope(
    state: dict[str, Any],
    config: Mapping[str, Any] | None = None,
    **updates: Any,
) -> dict[str, Any]:
    """Merge *updates* into and persist the canonical projection in ``state``."""
    scope = resolve_index_scope(state, config)
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(scope.get(key), Mapping):
            scope[key] = {**scope[key], **value}
        else:
            scope[key] = value
    state["index_scope"] = scope
    return scope


def file_page_scope(
    *, configured_cap: int | None, eligible: int, generated_pages: list[Any] | None
) -> dict[str, int | None]:
    """Build configured/effective/achieved file-page figures for one run."""
    from repowise.core.generation.selection import auto_file_page_cap

    generated = sum(1 for page in (generated_pages or []) if page.page_type == "file_page")
    effective = configured_cap
    if configured_cap is None:
        effective = auto_file_page_cap(eligible)
    elif configured_cap == 0:
        effective = None
    return {
        "configured_cap": configured_cap,
        "effective_cap": effective,
        "eligible": eligible,
        "generated": generated,
        "omitted": max(0, eligible - generated),
    }
