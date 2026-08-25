"""FastMCP server instance, lifespan, and entry points."""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.persistence.database import (
    create_engine,
    get_configured_db_url,
    get_repo_db_path,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import KeylessEmbedder
from repowise.server.mcp_server import _state

_log = __import__("logging").getLogger("repowise.mcp")


# Per-embedder remediation hints, appended to the ERROR log and the `_meta`
# warning so a misconfiguration is actionable without grepping SDK tracebacks.
# Keyed by built-in embedder name; unknown/custom embedders fall back to the
# generic exception message alone.
_EMBEDDER_REMEDIATION: dict[str, str] = {
    "openai": "set OPENAI_API_KEY in the MCP server's environment (and `pip install openai`)",
    "gemini": (
        "set GEMINI_API_KEY (or GOOGLE_API_KEY) in the MCP server's environment "
        "(and `pip install google-genai`)"
    ),
    "ollama": "start Ollama, pull an embedding model, and set OLLAMA_BASE_URL if not local",
    "openrouter": "set OPENROUTER_API_KEY in the MCP server's environment (and `pip install openai`)",
}


def _configured_embedder_name() -> str:
    """Read the configured embedder name from env or ``.repowise/config.yaml``.

    Returns a lowercased name, or ``""`` when nothing is explicitly configured
    (in which case MockEmbedder is the intended default, not a degradation).
    """
    name = os.environ.get("REPOWISE_EMBEDDER", "").strip().lower()
    if name:
        return name
    if _state._repo_path:
        try:
            from pathlib import Path

            cfg_path = Path(_state._repo_path) / ".repowise" / "config.yaml"
            if cfg_path.exists():
                import yaml  # type: ignore[import-untyped]

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                return (cfg.get("embedder") or "").strip().lower()
        except Exception:
            _log.debug("Failed to read embedder from config.yaml", exc_info=True)
    return ""


# Keyed embedders and the env vars each reads its credential from. The first
# entry is the canonical name the CLI persists under; the rest are accepted
# aliases. Embedders outside this map (ollama, mock, custom) need no API key.
_EMBEDDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "edenai": ("EDENAI_API_KEY",),
}


def _persisted_embedder_key(name: str) -> str | None:
    """Recover the configured embedder's API key from where the CLI persists it.

    ``repowise mcp`` loads ``<repo>/.repowise/.env`` before starting, but that
    covers only the repo it was pointed at: a workspace serves sibling repos
    whose ``.env`` is never read, and embedded callers reach :func:`run_mcp`
    without going through the CLI at all. ``repowise serve`` already falls back
    to the ``embedder_api_key`` saved in ``~/.repowise/config.yaml``. Without
    the same fallback here the server builds a ``MockEmbedder`` and queries a
    real index with vectors that cannot match it — semantic search silently
    degrades to full-text-only.

    Order: the served repo's ``.repowise/.env`` first, then the global config.
    Returns ``None`` when the embedder needs no key or none is persisted.
    """
    from pathlib import Path

    env_vars = _EMBEDDER_KEY_ENV.get(name)
    if not env_vars:
        return None
    canonical = env_vars[0]

    # The process environment is the highest-precedence source, matching the
    # CLI's resolver (cli/providers/keys.py): an exported key is an explicit
    # override. The server used to skip this tier entirely, so on a machine
    # with an exported credential it and the CLI disagreed about the same
    # repo in the same shell (issue #1711).
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return value

    if _state._repo_path:
        try:
            # Reuse the reader get_answer already uses for the LLM credential,
            # rather than a third copy of .env parsing.
            from repowise.server.mcp_server.tool_answer.synthesis import (
                _load_repo_provider_config,
            )

            _, _, overlay = _load_repo_provider_config(Path(_state._repo_path))
            for var in env_vars:
                if overlay.get(var):
                    return overlay[var]
        except Exception:
            _log.debug("Failed to read repo .env for embedder key", exc_info=True)

    try:
        cfg_path = Path.home() / ".repowise" / "config.yaml"
        if cfg_path.is_file():
            import yaml  # type: ignore[import-untyped]

            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            # Only honour the saved key when it belongs to the embedder being
            # resolved — handing an openai key to gemini fails confusingly.
            if (cfg.get("embedder") or "").strip().lower() == name:
                key = str(cfg.get("embedder_api_key") or "").strip()
                if key:
                    _log.info(
                        "Embedder key for '%s' recovered from ~/.repowise/config.yaml "
                        "(%s not set in the MCP server's environment).",
                        name,
                        canonical,
                    )
                    return key
    except Exception:
        _log.debug("Failed to read global config for embedder key", exc_info=True)

    return None


def _embedder_kwargs(name: str) -> dict[str, Any]:
    """Map repowise embedding env vars onto an embedder's constructor kwargs.

    Kept backend-agnostic: ``REPOWISE_EMBEDDING_MODEL`` applies to any embedder
    that accepts a ``model`` arg. ``REPOWISE_EMBEDDING_DIMS`` is read directly by
    the openai and ollama embedders in their own constructors; only gemini needs
    it mapped here, because its constructor spells the width
    ``output_dimensionality``. Anything not set here falls through to the
    embedder's own defaults.

    When the embedder needs an API key and the environment has none, the key is
    recovered from the repo/global config so the server matches the index it
    was pointed at instead of falling back to mock vectors.
    """
    kwargs: dict[str, Any] = {}
    model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
    if model:
        kwargs["model"] = model
    if name == "gemini":
        dims = os.environ.get("REPOWISE_EMBEDDING_DIMS")
        kwargs["output_dimensionality"] = int(dims) if dims else 768

    env_vars = _EMBEDDER_KEY_ENV.get(name, ())
    if env_vars and not any(os.environ.get(var) for var in env_vars):
        key = _persisted_embedder_key(name)
        if key:
            kwargs["api_key"] = key
    return kwargs


def _resolve_embedder():
    """Resolve the embedder from ``REPOWISE_EMBEDDER`` / ``.repowise/config.yaml``.

    Goes through the shared embedder registry (``get_embedder``) so *every*
    backend is honoured — openai, gemini, openrouter, and any custom embedder
    registered via ``register_embedder`` — not just a hardcoded subset.

    When an embedder is **explicitly configured** but fails to initialise (most
    often a missing API key, but also a missing SDK or an unknown name), we
    still fall back to ``MockEmbedder`` so the server keeps serving non-RAG
    tools — but we record the degradation in ``_state._embedder_status`` and log
    at ``ERROR`` with the missing key and remediation. ``build_meta`` then
    surfaces ``embedder_degraded`` in every tool's ``_meta`` envelope so callers
    can detect that semantic search is running on mock vectors instead of the
    real index, rather than the broken server masquerading as healthy (#306).

    When nothing is configured (or ``mock`` is requested explicitly),
    MockEmbedder is the intended default and is **not** flagged as degraded.
    """
    from repowise.core.providers.embedding import get_embedder

    name = _configured_embedder_name()

    if not name or name == "mock":
        _state._embedder_status = {
            "active": "mock",
            "requested": name or None,
            "degraded": False,
        }
        return KeylessEmbedder()

    try:
        embedder = get_embedder(name, **_embedder_kwargs(name))
        _state._embedder_status = {"active": name, "requested": name, "degraded": False}
        return embedder
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        reason = (
            f"Configured embedder '{name}' failed to initialise ({detail}). "
            "Semantic search (search_codebase, get_answer) is running on mock "
            "vectors and CANNOT match the real index — results will be empty or "
            "irrelevant."
        )
        remediation = _EMBEDDER_REMEDIATION.get(name)
        if remediation:
            reason += f" To fix: {remediation}, then restart the MCP server."
        _log.error(reason, exc_info=True)
        _state._embedder_status = {
            "active": "mock",
            "requested": name,
            "degraded": True,
            "reason": reason,
        }
        return KeylessEmbedder()


async def _cancel_task(task: asyncio.Task) -> None:
    """Cancel a lifespan background task and swallow its unwind."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _warm_lancedb() -> None:
    """Import lancedb once, off the event loop, and signal when it is done.

    The import pulls in numpy/pyarrow and their native extensions, which costs
    seconds on a cold filesystem. Running it on a worker thread keeps the loop
    answering, but a worker-thread import holds Python's import locks, and tool
    bodies lazily import as they run. When the two overlap they block each
    other and the event loop stops making progress entirely — at which point
    the timeouts that would cap the call cannot fire either, because firing
    them needs the loop.

    Tool dispatch waits on this event before running any handler body (see
    ``_failure_shield``), so the two never overlap.
    """
    try:
        await asyncio.to_thread(__import__, "lancedb")
    except ImportError:
        pass  # optional dependency — the InMemory fallback covers it
    except Exception:
        _log.warning("lancedb import failed — vector search falls back to InMemory")
    finally:
        if _state._lancedb_ready is not None:
            _state._lancedb_ready.set()


async def _load_vector_stores(repo_path: str | None) -> None:
    """Load embedder + vector stores in the background.

    Runs as an asyncio.Task started from _lifespan so the MCP server
    starts accepting connections immediately.  tool_search awaits
    _state._vector_store_ready before performing a search.

    We pre-warm the LanceDB connection here so the first search() call
    never hits a cold import or connection.  Specifically:

    1. `import lancedb` is deferred to asyncio.to_thread — the first-time
       import loads Rust/Arrow DLLs which can block the event loop for
       tens of seconds on Windows (AV scanning).  Running it in a thread
       keeps the event loop responsive.
    2. `_ensure_connected()` is called here so LanceDB opens the table
       before the first search.  Subsequent search() calls see
       self._db is not None and skip the blocking import entirely.
    """
    import asyncio as _asyncio

    try:
        embedder = _resolve_embedder()
        vector_store: Any = InMemoryVectorStore(embedder=embedder)

        try:
            # Step 1 — import lancedb in a thread to keep event loop free.
            await _asyncio.to_thread(__import__, "lancedb")

            from repowise.core.persistence.vector_store import LanceDBVectorStore

            if repo_path:
                from pathlib import Path

                lance_dir = Path(repo_path) / ".repowise" / "lancedb"
                if lance_dir.exists():
                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                    # Step 2 — pre-connect so first search() is instant.
                    await vs._ensure_connected()
                    vector_store = vs
        except ImportError:
            pass
        except Exception:
            _log.warning("LanceDB pre-connect failed — using InMemory fallback")

        # decision_store is repointed to the shared page store — decisions are
        # now embedded under the "decision:" namespace within the same table.
        _state._vector_store = vector_store
        _state._decision_store = vector_store
    except Exception:
        _log.exception("Failed to load vector stores — falling back to MockEmbedder")
        _fallback = InMemoryVectorStore(embedder=KeylessEmbedder())
        _state._vector_store = _fallback
        _state._decision_store = _fallback
    finally:
        if _state._vector_store_ready is not None:
            _state._vector_store_ready.set()


def _detect_workspace(repo_path: str | None):
    """Check if ``repo_path`` is inside a workspace.

    Returns ``(workspace_root, ws_config, repo_alias)`` or ``(None, None, None)``.
    """
    if not repo_path:
        return None, None, None
    try:
        from pathlib import Path as _Path

        from repowise.core.workspace import WorkspaceConfig, find_workspace_root

        ws_root = find_workspace_root(_Path(repo_path))
        if ws_root is None:
            return None, None, None

        ws_config = WorkspaceConfig.load(ws_root)
        if not ws_config.repos:
            return None, None, None

        # Determine which repo the given path belongs to
        resolved = _Path(repo_path).resolve()
        repo_alias = None
        for entry in ws_config.repos:
            entry_abs = (ws_root / entry.path).resolve()
            try:
                resolved.relative_to(entry_abs)
                repo_alias = entry.alias
                break
            except ValueError:
                continue

        if repo_alias is None:
            # Path is inside workspace but doesn't match a repo — use default
            primary = ws_config.get_primary()
            repo_alias = primary.alias if primary else ws_config.repos[0].alias

        return ws_root, ws_config, repo_alias
    except Exception:
        _log.debug("Workspace detection failed", exc_info=True)
        return None, None, None


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize DB engine, session factory, and FTS synchronously on startup.

    Vector store / LanceDB loading is deferred to a background asyncio task so
    the server starts accepting tool calls immediately.  search_codebase awaits
    _state._vector_store_ready before querying the vector store.
    """

    # Start the lancedb import immediately and gate tool dispatch on it. Both
    # modes load vector stores in the background, and in both the first tool
    # call can otherwise land mid-import and wedge the loop.
    _state._lancedb_ready = asyncio.Event()
    _warm_task = asyncio.create_task(_warm_lancedb(), name="lancedb-warmup")

    # --- Workspace detection ------------------------------------------------
    ws_root, ws_config, ws_repo_alias = _detect_workspace(_state._repo_path)

    if ws_root is not None and ws_config is not None:
        # Workspace mode — use RepoRegistry for multi-repo serving

        from repowise.core.workspace.registry import RepoRegistry

        # Override default repo to the one the path points at
        if ws_repo_alias and ws_config.get_repo(ws_repo_alias):
            ws_config.default_repo = ws_repo_alias

        registry = RepoRegistry(
            workspace_root=ws_root,
            ws_config=ws_config,
            embedder_factory=lambda: _resolve_embedder(),
        )

        # Eagerly load the default repo so tools work immediately
        default_ctx = await registry.get_default()

        _state._registry = registry
        _state._workspace_root = str(ws_root)

        # Alias default repo's resources into _state for backward compat
        _state._session_factory = default_ctx.session_factory
        _state._fts = default_ctx.fts
        _state._vector_store = default_ctx.vector_store
        _state._decision_store = default_ctx.decision_store
        _state._vector_store_ready = default_ctx.vector_store_ready

        # Load cross-repo enricher (Phase 3 + 4)
        try:
            from repowise.core.workspace.breaking_change import BREAKING_CHANGES_FILENAME
            from repowise.core.workspace.config import WORKSPACE_DATA_DIR
            from repowise.core.workspace.conformance import CONFORMANCE_FILENAME
            from repowise.core.workspace.contracts import CONTRACTS_FILENAME
            from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME
            from repowise.server.mcp_server._enrichment import CrossRepoEnricher

            cross_repo_path = ws_root / WORKSPACE_DATA_DIR / "cross_repo_edges.json"
            contracts_path = ws_root / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
            system_graph_path = ws_root / WORKSPACE_DATA_DIR / SYSTEM_GRAPH_FILENAME
            breaking_changes_path = ws_root / WORKSPACE_DATA_DIR / BREAKING_CHANGES_FILENAME
            conformance_path = ws_root / WORKSPACE_DATA_DIR / CONFORMANCE_FILENAME
            enricher = CrossRepoEnricher(
                cross_repo_path,
                contracts_path=contracts_path,
                system_graph_path=system_graph_path,
                breaking_changes_path=breaking_changes_path,
                conformance_path=conformance_path,
            )
            if enricher.has_data or enricher.has_system_graph:
                _state._cross_repo_enricher = enricher
                _log.info(
                    "Cross-repo enricher loaded: %d co-change edges, %d package deps, %d contract links",
                    len(enricher._co_changes),
                    len(enricher._package_deps),
                    len(enricher._contract_links),
                )
        except Exception:
            _log.debug("Cross-repo enricher not available", exc_info=True)

        _log.info(
            "repowise MCP: workspace mode — %d repos, default='%s'",
            len(ws_config.repos),
            registry.get_default_alias(),
        )

        yield

        await _cancel_task(_warm_task)
        _state._lancedb_ready = None
        _state._cross_repo_enricher = None
        await registry.close()
        _state._registry = None
        _state._workspace_root = None
        return

    # --- Single-repo mode (existing behavior) --------------------------------
    configured_db_url = get_configured_db_url()

    # When repo path is set and no env override, prefer repo-local DB.
    if _state._repo_path and configured_db_url is None:
        db_path = get_repo_db_path(_state._repo_path)
        repowise_dir = db_path.parent
        if not repowise_dir.exists():
            _log.warning(
                "No .repowise directory at %s — run 'repowise init' first",
                _state._repo_path,
            )
            repowise_dir.mkdir(parents=True, exist_ok=True)
        elif not db_path.exists():
            _log.warning(
                "No wiki.db in %s — run 'repowise init' to generate the wiki",
                repowise_dir,
            )

    db_url = resolve_db_url(_state._repo_path)

    _log.info("repowise MCP: initialising database…")
    engine = create_engine(db_url)
    await init_db(engine)

    _state._session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    _state._fts = FullTextSearch(engine)
    try:
        await _state._fts.ensure_index()
    except Exception:
        # Every tool this server exposes was unreachable when this raised
        # (issue #1309), including the ones that never touch the index. The
        # instance is kept: its search still runs against whatever shape the
        # index is in, and the vector arm answers alongside it.
        _log.warning("repowise MCP: full-text index unavailable", exc_info=True)

    # Seed InMemory placeholder so tools that don't need vector search
    # can start immediately, before the background load completes.
    # decision_store is repointed to the same store — no separate table.
    _placeholder = InMemoryVectorStore(embedder=KeylessEmbedder())
    _state._vector_store = _placeholder
    _state._decision_store = _placeholder

    # Defer embedder resolution + LanceDB open to a background task so
    # the server starts accepting connections without blocking on disk I/O.
    _state._vector_store_ready = asyncio.Event()
    _bg_task = asyncio.create_task(_load_vector_stores(_state._repo_path))
    _log.info("repowise MCP: ready (vector stores loading in background)")

    yield

    await _cancel_task(_bg_task)
    await _cancel_task(_warm_task)
    _state._lancedb_ready = None

    await engine.dispose()
    # _decision_store is an alias for _vector_store — close only once.
    await _state._vector_store.close()


# ---------------------------------------------------------------------------
# Create the MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "repowise",
    instructions=(
        "repowise is a codebase documentation engine. Use these tools to query "
        "the wiki for architecture overviews, contextual docs on files/modules/"
        "symbols, modification and change-risk assessment, architectural decision "
        "rationale, semantic search, dead code, and code health. In workspace mode, "
        "get_architecture and get_blast_radius are also available. If the tools "
        "report that the repo has no index, tell the user to run "
        "'repowise init --yes' in the repo root; it needs no API key. Suggest it, "
        "do not run it yourself."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Server entry points
# ---------------------------------------------------------------------------


def create_mcp_server(
    repo_path: str | None = None,
    tools: str | list[str] | None = None,
) -> FastMCP:
    """Create and return the MCP server instance, optionally scoped to a repo.

    ``tools`` is an optional surface override (an explicit allowlist, ``+``/``-``
    deltas, or ``"all"``); when omitted the ``mcp.tools`` config block is used.
    """
    _state._repo_path = repo_path
    from repowise.server.mcp_server import ensure_full_surface
    from repowise.server.mcp_server._tool_selection import apply_tool_selection

    # Tool modules import lazily now, so a server has to ask for the full
    # surface before it can advertise (or trim) it.
    ensure_full_surface()

    apply_tool_selection(mcp, repo_path=repo_path, override=tools)
    return mcp


#: Depth bound when unwrapping nested task-group failures. Groups nest one or
#: two deep in practice; the cap only stops a pathological cycle from hanging.
_MAX_GROUP_DEPTH = 10

#: Host values FastMCP's own default construction already treats as local.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: ``allowed_hosts``/``allowed_origins`` patterns for the loopback callers a
#: non-loopback bind should still accept (e.g. an SSH tunnel or a client
#: running on the same box as the server).
_LOOPBACK_ALLOWLIST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


def _bracket_if_ipv6(host: str) -> str:
    """Bracket a bare IPv6 literal to match the ``Host``/``Origin`` header shape a client sends.

    ``TransportSecurityMiddleware`` matches by ``host.startswith(base + ":")``
    (see ``mcp/server/transport_security.py``), so an allowlist entry has to
    be shaped exactly like the wire value. A client connecting to an IPv6
    literal sends a bracketed Host header (``[2001:db8::1]:7338``), which
    never starts with a bare ``2001:db8::1:`` — hostnames and IPv4 addresses
    never contain a colon, so any colon in ``host`` here means IPv6.
    """
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _configure_transport_security(host: str) -> None:
    """Widen the DNS-rebinding ``Host`` allowlist to match ``--host``.

    ``FastMCP`` builds ``mcp.settings.transport_security`` at import time,
    before any CLI ``--host`` is known, so it bakes in an allowlist scoped to
    loopback only. ``run_mcp`` rebinds the socket via ``mcp.settings.host``
    later, but nothing updated the allowlist to match — so every request to a
    non-loopback ``--host`` failed ``Host`` header validation with
    ``421 Misdirected Request`` no matter what host was actually given.

    A loopback host needs no change (FastMCP's default already covers it).
    Anything else — including a concrete IPv6 literal, bracketed to match the
    header shape — gets an allowlist scoped to that host plus loopback.

    A wildcard bind (``0.0.0.0``/``::``) can't be matched by any single
    ``Host`` value, so the check is disabled rather than left permanently
    failing. That trades DNS-rebinding protection for reachability on a
    wildcard bind; the startup security warning logged elsewhere in
    ``run_mcp`` for an unauthenticated wide bind is, until #1400 lands, the
    only remaining gate on that surface — this fix is what makes it live
    traffic instead of traffic that already 421'd.
    """
    if host in _LOOPBACK_HOSTS:
        return
    if host in ("0.0.0.0", "::"):
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        return
    base = _bracket_if_ipv6(host)
    # allowed_origins inherits the SDK's same startswith(base + ":") prefix
    # match as allowed_hosts (see _validate_origin), so e.g.
    # "http://172.21.12.48:*" also technically accepts an Origin like
    # "http://172.21.12.48:8080.evil.com". Pre-existing SDK behavior — the
    # loopback defaults FastMCP bakes in have the identical looseness — not
    # something introduced or worsened here.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{base}:*", *_LOOPBACK_ALLOWLIST_PATTERNS],
        allowed_origins=[
            f"http://{base}:*",
            *(f"http://{p}" for p in _LOOPBACK_ALLOWLIST_PATTERNS),
        ],
    )


def _run_transport(transport: str) -> None:
    """Run the server, raising the cause of a task-group failure rather than the group.

    ``mcp.run`` drives an anyio event loop, and anyio reports a child task's
    failure as an ``ExceptionGroup`` wrapping the real exception. Anything that
    reads the outermost class then learns only that *a* task failed: the CLI
    records the wrapper's name as the error type, so a missing dependency, a
    permission problem and a closed pipe are indistinguishable after the fact.
    Unwrap to the first leaf and raise that, so the layers above name the real
    error. A group holding several distinct failures loses the siblings, which
    is worth it to stop losing the cause entirely.
    """
    try:
        mcp.run(transport=transport)
    except BaseExceptionGroup as group:
        leaf: BaseException = group
        for _ in range(_MAX_GROUP_DEPTH):
            if not isinstance(leaf, BaseExceptionGroup) or not leaf.exceptions:
                break
            leaf = leaf.exceptions[0]
        # A cancelled run is how a client-initiated shutdown looks, not a fault.
        if isinstance(leaf, Exception):
            _log.error("MCP server (%s) stopped: %r", transport, leaf, exc_info=leaf)
        raise leaf from group


def run_mcp(
    transport: str = "stdio",
    repo_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 7338,
    tools: str | list[str] | None = None,
) -> None:
    """Run the MCP server with the specified transport.

    ``tools`` overrides which tools are advertised (see
    :func:`repowise.server.mcp_server._tool_selection.apply_tool_selection`);
    when omitted, the ``mcp.tools`` config block is honoured.
    """
    _state._repo_path = repo_path
    from repowise.server.mcp_server import ensure_full_surface
    from repowise.server.mcp_server._tool_selection import apply_tool_selection

    ensure_full_surface()
    apply_tool_selection(mcp, repo_path=repo_path, override=tools)

    if transport == "sse":
        mcp.settings.host = host
        mcp.settings.port = port
        _configure_transport_security(host)
        if host in ("0.0.0.0", "::") and not os.environ.get("REPOWISE_API_KEY"):
            _log.warning(
                "SECURITY WARNING: MCP server (sse) is binding to %s without "
                "REPOWISE_API_KEY. All tools are unauthenticated and "
                "network-accessible. Set REPOWISE_API_KEY or bind to 127.0.0.1.",
                host,
            )
        _run_transport("sse")
    elif transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        _configure_transport_security(host)
        if host in ("0.0.0.0", "::") and not os.environ.get("REPOWISE_API_KEY"):
            _log.warning(
                "SECURITY WARNING: MCP server (streamable-http) is binding to %s without "
                "REPOWISE_API_KEY. All tools are unauthenticated and "
                "network-accessible. Set REPOWISE_API_KEY or bind to 127.0.0.1.",
                host,
            )
        _run_transport("streamable-http")
    else:
        # stdout is the JSON-RPC channel on stdio, so every log line written
        # there arrives at the client as a malformed protocol frame. Move the
        # log sinks to stderr before anything can log.
        from repowise.server.mcp_server._stdio_logging import route_logging_to_stderr

        route_logging_to_stderr()
        # stdio servers are spawned per-session by the MCP client; when the
        # client dies abnormally the stdio loop doesn't exit (and Windows
        # never kills children), leaking servers that hold wiki.db handles.
        # The watchdog exits this process once the client is gone.
        from repowise.server.mcp_server._watchdog import start_parent_watchdog

        start_parent_watchdog()
        _run_transport("stdio")
