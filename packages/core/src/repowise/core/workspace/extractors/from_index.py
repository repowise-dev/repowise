"""Index-backed contract extraction: read the symbols ingestion already parsed.

Workspace mode re-derived route tables from raw text with regexes, running
*after* the ingestion pipeline had parsed the very files it then re-read from
disk (``update.py`` indexes every stale repo, and only then calls
``run_cross_repo_hooks``). This module reads that parse instead.

The source is the repo's ``wiki.db`` symbol table, reached through
:class:`~repowise.core.workspace.repo_index.RepoIndex`. It replaces the per-repo
parse cache this module used to read: that cache gates every entry on
``parser_fingerprint()``, so a repo indexed by a different repowise version
loads empty — measured at three of three repos on a live workspace, which left
the whole index path inert. The database carries no such gate.

**What the index carries is identity, not syntax.** A symbol row is a name, a
kind and a line span; it holds no decorator text. A route's *identity* is the
handler symbol that span names, and its declaration is read from the unbroken
run of decorators immediately above the span (:func:`_decorators_above`) rather
than from anywhere in the file. That is what stops the route-shaped prose in a
comment — the defect class that motivated this module — from becoming a
contract. It is a narrowing, not a proof: a docstring line beginning with ``@``
directly above a definition would still be read as one.

A router's mount prefix comes from a call expression
(``APIRouter(prefix="/x")``, ``include_router(r, prefix="/y")``), never from a
declaration above a handler, so prefix resolution still reads the file text via
:mod:`.http.mounts`. The route's identity moves onto the index; its prefix is
stitched on exactly as before.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .http.dialect import build_provider_contract
from .http.mounts import compose_prefix, router_prefixes
from .langs import JS_TS, PYTHON

if TYPE_CHECKING:
    from collections.abc import Sequence

    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.repo_index import IndexedSymbol

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

# A decorator's head and its first string argument, from the verbatim text
# above a symbol. ``@router.post("")`` -> ("router.post", ""). The argument is
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

# How far above a definition the decorator walk may reach. Bounds the scan on a
# file whose indexed spans no longer line up with its text.
_DECORATOR_LOOKBACK = 40

# Symbol kinds a route decorator can sit above. Classes are included because
# Flask-style pluggable views decorate one, which the retired path also caught.
_HANDLER_KINDS = frozenset({"function", "method", "class"})

# The languages anything here reads decorators for. Widening this means
# widening :func:`extract_http_providers` to match.
INDEX_SUFFIXES = frozenset({".py"})

# Consumer extraction needs the *symbol table* — line ranges and kinds — for the
# languages HTTP clients are written in, so the wrapper-confirmation pass can
# bound "this symbol's own body" to a parsed extent instead of guessing it by
# counting braces. Kept separate from INDEX_SUFFIXES because the two passes read
# different things off the same rows: providers read the declaration above a
# span, consumers read the span itself.
CONSUMER_INDEX_SUFFIXES = frozenset(JS_TS) | frozenset(PYTHON)


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


def _decorators_above(lines: list[str], start_line: int) -> list[str]:
    """Verbatim decorator text stacked directly above a definition.

    ``start_line`` is 1-indexed and names the ``def`` line: the parser takes a
    symbol's span from the definition node, not from Python's enclosing
    ``decorated_definition``, so any decorators sit above it.

    The run above the definition must be decorators and nothing else. Stopping
    at the first ordinary line — not merely at a blank one — is what keeps a
    decorated *class* from lending its route to the first method under it, and
    a neighbouring handler from lending its route to the next.
    """
    out: list[str] = []
    pending: list[str] = []
    depth = 0  # unclosed parens below, so a walk inside a multi-line decorator
    i = start_line - 2  # 0-indexed line directly above the definition
    floor = max(0, i - _DECORATOR_LOOKBACK)
    while i >= floor:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            break
        # A line belongs to the stack only as a decorator head or as the
        # argument list of one still open below it. Anything else is ordinary
        # code and ends the run.
        opened = depth + line.count(")") - line.count("(")
        if opened <= 0 and not stripped.startswith("@"):
            break
        depth = opened
        pending.append(line)
        if depth == 0:
            out.append("\n".join(reversed(pending)))
            pending = []
        i -= 1
    return out


# ---------------------------------------------------------------------------
# Reading the index
# ---------------------------------------------------------------------------


def symbols_for_content(ctx: ScanContext, line_count: int) -> list[IndexedSymbol]:
    """This file's indexed symbols, but only if they still describe *ctx.content*.

    The index carries no per-file content hash, so currency is proven against
    the text itself. Two checks, because they catch opposite edits: a span
    reaching past the end of the file was parsed from a longer version of it,
    and a definition the index does not place is code added since it was
    written. The second matters most — the index pass supersedes the text
    dialect for a file it produced routes for, so an unseen definition would be
    a route silently dropped rather than one merely not upgraded.

    Neither catches an edit that shifts lines without changing the definition
    set. A shifted span reads no decorator and yields no route, so the cost is
    a miss, not a fabrication.
    """
    if ctx.index is None:
        return []
    symbols = ctx.index.symbols_for_file(ctx.rel_path)
    if any(s.end_line > line_count for s in symbols):
        _log.debug("Indexed spans for %s overrun the file; using the regex path", ctx.rel_path)
        return []
    if ctx.suffix in INDEX_SUFFIXES and not _definitions_are_indexed(ctx.content, symbols):
        _log.debug("Indexed symbols for %s miss a definition; using the regex path", ctx.rel_path)
        return []
    return symbols


# A Python definition line, wherever it is indented.
_DEF_LINE_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]", re.MULTILINE)


def _definitions_are_indexed(content: str, symbols: Sequence[IndexedSymbol]) -> bool:
    """True when every ``def`` in *content* is one the index knows about.

    A definition is known if a symbol starts on that line, or if it is nested
    inside a symbol's span — the parser records a closure as part of its
    enclosing function rather than on its own row.
    """
    starts = {s.start_line for s in symbols}
    spans = [(s.start_line, s.end_line) for s in symbols]
    for m in _DEF_LINE_RE.finditer(content):
        line = content.count("\n", 0, m.start()) + 1
        if line in starts:
            continue
        if not any(start < line <= end for start, end in spans):
            return False
    return True


# ---------------------------------------------------------------------------
# Provider extraction
# ---------------------------------------------------------------------------


def extract_http_providers(
    ctx: ScanContext,
    symbols: Sequence[IndexedSymbol],
    lines: list[str],
) -> list[Contract]:
    """HTTP provider contracts from the decorators above *symbols*.

    *lines* is ``ctx.content`` already split; the mount prefix still comes from
    the whole file text (see the module docstring), so ``ctx.content`` is read
    as well.
    """
    # Routes before prefixes: ``router_prefixes`` scans the whole file, so it
    # is worth paying for only once a route exists to stitch a prefix onto.
    routes = [
        (symbol, decorator, route)
        for symbol in symbols
        if symbol.kind in _HANDLER_KINDS
        for decorator in _decorators_above(lines, symbol.start_line)
        for route in _routes_in_decorator(decorator)
    ]
    if not routes:
        return []

    prefixes = router_prefixes(ctx.content, "APIRouter|FastAPI|Flask|Blueprint")
    known = set(prefixes) | {"app", "router", "bp", "blueprint"}

    out: list[Contract] = []
    seen: set[tuple[str, str]] = set()
    for symbol, decorator, (var, method, raw_path) in routes:
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
            ctx, method=method, path_raw=path, framework=framework, line=symbol.start_line
        )
        if contract is not None:
            contract.meta[EXTRACTION_LAYER_KEY] = LAYER_INDEX
            contract.meta["handler"] = symbol.name
            # Bound here rather than by line: this path found the route *by*
            # walking up from the handler's own span, so the symbol is known
            # exactly and no lookahead rule needs to re-derive it.
            contract.symbol_id = symbol.symbol_id
            out.append(contract)
    return out


def _is_route_head(decorator: str) -> bool:
    head, _ = _decorator_head_and_arg(decorator)
    return head.rpartition(".")[2].lower() in _ROUTE_HEADS
