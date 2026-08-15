"""Index-backed contract extraction: read the symbols ingestion already parsed.

Workspace mode re-derived route tables from raw text with regexes, running
*after* the ingestion pipeline had parsed the very files it then re-read from
disk (``update.py`` indexes every stale repo, and only then calls
``run_cross_repo_hooks``). This module reads that parse instead.

The source is the per-repo parse cache (``<repo>/.repowise/parse_cache.pkl``),
which stores a :class:`~repowise.core.ingestion.models.ParsedFile` per file —
symbols, their kinds, their line ranges, and ``Symbol.decorators`` holding the
verbatim decorator text (``@router.post("")``). Reading a decorator node cannot
pick up a route-shaped string from a comment or a docstring, and cannot lose an
empty path, so two of the defect classes that motivated this module are gone by
construction rather than by a better regex.

**What the index does not carry.** A router's mount prefix comes from a call
expression (``APIRouter(prefix="/x")``, ``include_router(r, prefix="/y")``), not
from a decorator, and the cache stores extraction results rather than the tree.
Prefix resolution therefore still reads the file text via :mod:`.http.mounts`.
The route's *identity* is what moves onto the parse; its prefix is stitched on
exactly as before.

**Availability is not guaranteed.** :class:`ParseCache` gates every entry on
``parser_fingerprint()``, so a repo indexed by a different repowise version
loads as empty — measured on this workspace, two of three repos. Callers must
treat :func:`load_repo_index` returning ``None`` as "use the regex path", which
is also the honest answer for languages with no AST tier at all.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .http.dialect import build_provider_contract
from .http.mounts import compose_prefix, router_prefixes
from .langs import JS_TS

if TYPE_CHECKING:
    from pathlib import Path

    from repowise.core.ingestion.models import ParsedFile
    from repowise.core.workspace.contracts import Contract

    from .base import ScanContext

_log = logging.getLogger(__name__)

# Recorded on every contract as ``meta[EXTRACTION_LAYER_KEY]`` so the coverage
# metric can report which layer produced it. Deliberately not ``meta["source"]``:
# eleven gRPC dialects already use that key for *dialect* provenance
# ("py_servicer", "go_client", "proto", ...), and reusing it would have left
# every gRPC contract classified as neither index- nor regex-sourced.
EXTRACTION_LAYER_KEY = "extraction_layer"
LAYER_INDEX = "index"
LAYER_REGEX = "regex"

# ---------------------------------------------------------------------------
# Decorator parsing
# ---------------------------------------------------------------------------

# A decorator's head and its first string argument, from the verbatim text the
# parser stores. ``@router.post("")`` -> ("router.post", ""). The argument is
# ``*`` not ``+``: an empty path is the idiomatic collection root on a prefixed
# router and must survive to be stitched onto that prefix.
_DECORATOR_RE = re.compile(r"""^@([\w.]+)\s*\(\s*(?:(?P<q>['"])(?P<arg>[^'"]*)(?P=q))?""")

# HTTP verbs usable as the trailing segment of a decorator head
# (``@router.get`` / ``@app.post``).
_VERB_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# Flask/Django-style ``@app.route("/x", methods=["POST"])``. Ingestion resolves
# Flask (framework edges + dynamic hints) while the workspace regex layer was
# FastAPI-only, so this is the framework the index path inherits for free.
_METHODS_KW_RE = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""")
_METHOD_LITERAL_RE = re.compile(r"""['"]([A-Za-z]+)['"]""")

_ROUTE_HEADS = frozenset({"route"})

# The languages anything here reads decorators for. Widening this means
# widening :func:`extract_http_providers` to match.
INDEX_SUFFIXES = frozenset({".py"})

# Consumer extraction needs the *symbol table* — line ranges and kinds — for the
# languages HTTP clients are written in, so the wrapper-confirmation pass can
# bound "this symbol's own body" to a parsed extent instead of guessing it by
# counting braces. Kept separate from INDEX_SUFFIXES because the two passes read
# different things off the same parse: providers read ``Symbol.decorators``,
# consumers read line ranges.
CONSUMER_INDEX_SUFFIXES = frozenset(JS_TS)

# What :func:`load_repo_index` deserializes for both passes together. One load
# per repo feeds both; asking for them separately would read the cache twice.
ALL_INDEX_SUFFIXES = INDEX_SUFFIXES | CONSUMER_INDEX_SUFFIXES


def _decorator_head_and_arg(decorator: str) -> tuple[str, str | None]:
    """Split verbatim decorator text into its dotted head and first string arg.

    ``@router.post("/x")`` -> ``("router.post", "/x")``;
    ``@router.post("")`` -> ``("router.post", "")``;
    ``@dataclass`` -> ``("dataclass", None)``.
    """
    m = _DECORATOR_RE.match(decorator.strip())
    if m is None:
        return decorator.strip().lstrip("@"), None
    return m.group(1), m.group("arg")


def _routes_in_decorator(decorator: str) -> list[tuple[str, str, str]]:
    """Return ``(router_var, METHOD, raw_path)`` for a route decorator.

    A ``@app.route(...)`` carrying ``methods=[...]`` yields one entry per verb
    (Flask's way of declaring several on one handler); a verb-named decorator
    yields exactly one. Anything else yields nothing.
    """
    head, arg = _decorator_head_and_arg(decorator)
    if arg is None or "." not in head:
        return []
    var, _, tail = head.rpartition(".")
    tail = tail.lower()

    if tail in _VERB_METHODS:
        return [(var, tail.upper(), arg)]

    if tail in _ROUTE_HEADS:
        mk = _METHODS_KW_RE.search(decorator)
        verbs = [v.upper() for v in _METHOD_LITERAL_RE.findall(mk.group(1))] if mk else []
        # Flask defaults to GET when ``methods`` is absent.
        return [(var, verb, arg) for verb in (verbs or ["GET"])]

    return []


# ---------------------------------------------------------------------------
# Reading the parse cache
# ---------------------------------------------------------------------------


def load_repo_index(
    repo_root: Path,
    suffixes: frozenset[str] = INDEX_SUFFIXES,
) -> dict[str, ParsedFile] | None:
    """Return ``rel_path -> ParsedFile`` from the repo's parse cache.

    Only entries whose path ends in one of *suffixes* are deserialized. The
    cache holds every file the repo indexed (27 MB of pickled ``ParsedFile``
    graphs on this repo) and only the languages read here are ever consulted,
    so unpickling the rest would be pure memory cost — multiplied by the repos
    running concurrently.

    ``None`` when the cache is absent or unusable — a repo indexed by another
    repowise version fails the ``parser_fingerprint`` gate and loads empty,
    which is indistinguishable from "never indexed" and gets the same answer:
    fall back to the regex path.
    """
    import pickle

    from repowise.core.ingestion.parse_cache import ParseCache

    cache_dir = repo_root / ".repowise"
    if not (cache_dir / "parse_cache.pkl").is_file():
        return None

    cache = ParseCache(cache_dir)
    try:
        cache.load()
    except Exception:
        _log.debug("Parse cache unreadable for %s", repo_root, exc_info=True)
        return None

    # ParseCache exposes lookup by (FileInfo, hash) but not enumeration, and
    # this needs the whole map. Reaching for the private dict is the coupling
    # that buys that; log loudly rather than go dark if it ever moves.
    entries = getattr(cache, "_entries", None)
    if entries is None:
        _log.warning(
            "ParseCache no longer exposes _entries; index-backed contract "
            "extraction is disabled and everything falls back to regex"
        )
        return None
    if not entries:
        _log.info(
            "Parse cache for %s loaded no entries (version mismatch or empty); "
            "contract extraction falls back to the regex path",
            repo_root,
        )
        return None

    out: dict[str, ParsedFile] = {}
    for (rel_path, content_hash), blob in entries.items():
        if not rel_path.endswith(tuple(suffixes)):
            continue
        try:
            parsed = pickle.loads(blob)
        except Exception:  # a single corrupt entry must not lose the repo
            _log.debug("Skipping unpicklable parse-cache entry %s", rel_path)
            continue
        # The cache key carries the hash the entry was parsed from; keep it on
        # the object so :func:`parsed_for` can reject a stale entry.
        parsed.content_hash = content_hash
        out[rel_path] = parsed
    return out or None


def parsed_for(
    index: dict[str, ParsedFile] | None,
    rel_path: str,
    content_hash: str | None,
) -> ParsedFile | None:
    """The parse for *rel_path*, but only if it describes the bytes on disk.

    Parse-cache entries are keyed by content hash. An entry whose hash does not
    match the file just walked describes an older version of it, and trusting
    that would report routes which no longer exist (or miss ones that now do).
    Without a hash to check against there is nothing to trust, so the caller
    gets ``None`` and stays on the regex path.
    """
    if index is None or content_hash is None:
        return None
    parsed = index.get(rel_path)
    if parsed is None:
        return None
    if parsed.content_hash != content_hash:
        _log.debug("Parse-cache entry for %s is stale; using the regex path", rel_path)
        return None
    return parsed


# ---------------------------------------------------------------------------
# Provider extraction
# ---------------------------------------------------------------------------

def extract_http_providers(
    ctx: ScanContext,
    parsed: ParsedFile,
) -> list[Contract]:
    """HTTP provider contracts from *parsed*'s decorated symbols.

    Routes come from ``Symbol.decorators``; the mount prefix still comes from
    the file text (see the module docstring), so ``ctx.content`` is required.
    """
    prefixes = router_prefixes(ctx.content, "APIRouter|FastAPI|Flask|Blueprint")
    known = set(prefixes) | {"app", "router", "bp", "blueprint"}

    out: list[Contract] = []
    seen: set[tuple[str, str]] = set()
    for symbol in parsed.symbols:
        # ``Symbol.decorators`` can repeat an entry (the parser appends per
        # matching capture), so dedupe per symbol before building contracts.
        for decorator in dict.fromkeys(symbol.decorators):
            for var, method, raw_path in _routes_in_decorator(decorator):
                if var not in known:
                    continue
                path = compose_prefix(prefixes.get(var, ""), raw_path)
                path = compose_prefix(ctx.mounts.get(var, ""), path)
                key = (method, path)
                if key in seen:
                    continue
                seen.add(key)
                framework = "flask" if _is_route_head(decorator) else "fastapi"
                contract = build_provider_contract(
                    ctx, method=method, path_raw=path, framework=framework
                )
                if contract is not None:
                    contract.meta[EXTRACTION_LAYER_KEY] = LAYER_INDEX
                    contract.meta["handler"] = symbol.name
                    out.append(contract)
    return out


def _is_route_head(decorator: str) -> bool:
    head, _ = _decorator_head_and_arg(decorator)
    return head.rpartition(".")[2].lower() in _ROUTE_HEADS
