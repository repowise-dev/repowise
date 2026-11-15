"""Confirm that a function really is an HTTP wrapper, instead of guessing.

``js_clients.py`` decides whether ``IDENT("/path")`` is a service call by
pattern-matching *IDENT's name* against ``fetch|request|http|api|ajax|rest|rpc``.
That cannot distinguish ``apiGet("/users")`` (a real call) from ``apiGet`` reading
a local cache, and it cannot recognise a wrapper whose name carries no HTTP word.

This module answers the question by construction instead: a symbol is an HTTP
wrapper when its **own body** issues a sink call (``fetch(``, ``axios.get(``,
``new XMLHttpRequest``…), or when it calls another symbol that does, within a
small hop budget. The name is never consulted.

**Why this reads symbol bodies rather than the resolved call graph.** The plan
for this work assumed ``ParsedFile.calls`` / the persisted ``graph_edges`` rows
already encode "does this wrapper reach ``fetch``". Measured on the live
frontend index, they do not, for two independent reasons:

1. ``fetch`` is in TypeScript's ``builtin_calls`` set
   (``ingestion/languages/specs/typescript.py:37``), and ``parser.py:1041``
   *drops* a call site whose target is a builtin. The sink call is erased
   before it ever reaches a ``CallSite``, so no ``calls`` edge can exist for it.
2. The TypeScript call queries match ``object: (identifier)``
   (``queries/typescript.scm:222``). ``this`` is a distinct node type, not an
   identifier, so ``this.foo(...)`` matches no call pattern at all. Measured on
   ``frontend/src/lib/api/client.ts``: **0** call sites with ``receiver == "this"``
   out of 297, while the file makes 172 of them.

So the call graph cannot see either end of the chain this module needs. What the
index *does* carry, verified and load-bearing here, is the **symbol table**:
every function/method with its exact ``start_line``/``end_line`` and its class.
That is what bounds "this symbol's own body" to real, parsed extents rather than
a brace-counting guess, and it is why a body scan here is not the same kind of
claim as a regex over a whole file.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

from repowise.core.ingestion.source_mask import mask_source

from ..strings import call_arguments

if TYPE_CHECKING:
    from collections.abc import Sequence

    from repowise.core.workspace.repo_index import IndexedSymbol

# Hop budget for wrapper confirmation. 2 is not arbitrary: the real chain in
# ``frontend/src/lib/api/client.ts`` is
# ``getSnapshot -> this.fetch -> this.request -> fetch()``, so a caller's target
# (``fetch``) sits one hop from the sink and the budget has to cover it with
# room for one more indirection. It stays small on purpose — each hop widens the
# search, and an unbounded walk would turn confirmation into a whole-repo graph
# traversal, which the performance budget for this phase forbids.
DEFAULT_HOP_BUDGET = 2

# A call to a real HTTP sink. The negative lookbehind is the whole point: it
# requires the sink to be called *globally* (``fetch(...)``), not as a member
# (``this.fetch(...)`` / ``client.fetch(...)``), because a member call is the
# wrapper we are trying to confirm rather than the sink that confirms it.
# ``(?:<[^<>()]*>\s*)?`` admits TypeScript's generic call syntax — ``fetch<T>(``
# — which is exactly what the existing dialect's ``fetch\s*\(`` cannot match.
# TypeScript type arguments, admitting one level of nesting so
# ``publicFetch<Paginated<Commit>>(...)`` is still recognised as a call.
GENERIC_ARGS = r"(?:<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>\s*)?"

_JS_SINK_RE = re.compile(
    r"(?<![.\w$])(?:fetch|axios|XMLHttpRequest|EventSource)\s*" + GENERIC_ARGS + r"\(",
)
# ``axios.get(...)`` / ``new XMLHttpRequest()`` reach the sink through a member
# or constructor rather than a bare call, so they get their own patterns.
_JS_SINK_MEMBER_RE = re.compile(r"(?<![.\w$])(?:axios)\s*\.\s*\w+\s*\(")
_JS_SINK_NEW_RE = re.compile(r"\bnew\s+(?:XMLHttpRequest|EventSource)\s*\(")

_PY_SINK_RE = re.compile(
    r"(?<![.\w])(?:requests|httpx|aiohttp)\s*\.\s*\w+\s*\(|"
    r"(?<![.\w])(?:urlopen|ClientSession)\s*\(",
)

_CS_SINK_RE = re.compile(r"\b(?:HttpClient|RestClient)\b")

_SINKS_BY_SUFFIX: dict[str, tuple[re.Pattern[str], ...]] = {
    ".ts": (_JS_SINK_RE, _JS_SINK_MEMBER_RE, _JS_SINK_NEW_RE),
    ".tsx": (_JS_SINK_RE, _JS_SINK_MEMBER_RE, _JS_SINK_NEW_RE),
    ".js": (_JS_SINK_RE, _JS_SINK_MEMBER_RE, _JS_SINK_NEW_RE),
    ".jsx": (_JS_SINK_RE, _JS_SINK_MEMBER_RE, _JS_SINK_NEW_RE),
    ".mjs": (_JS_SINK_RE, _JS_SINK_MEMBER_RE, _JS_SINK_NEW_RE),
    ".py": (_PY_SINK_RE,),
    ".cs": (_CS_SINK_RE,),
}

# Symbol kinds that can hold a body worth scanning.
_CALLABLE_KINDS = frozenset({"function", "method", "constructor"})

# A call to a locally-declared name, used to walk one hop. Deliberately matches
# both ``name(`` and ``this.name(`` / ``self.name(`` — the receiver is not what
# decides anything here; whether *name* resolves to a callable symbol in this
# file, and whether that symbol reaches a sink, is.
_LOCAL_CALL_RE = re.compile(
    r"(?:(?:this|self)\s*\.\s*)?([A-Za-z_$][\w$]*)\s*" + GENERIC_ARGS + r"\("
)


# Sinks that are themselves callable by name, so a *global* call to one is an
# HTTP call outright and needs no wrapper behind it. Only consulted for a call
# with no receiver: ``this.fetch(...)`` is a method on the enclosing class that
# happens to share the sink's name, and must still be confirmed on its merits.
_SINK_CALL_NAMES_BY_SUFFIX: dict[str, frozenset[str]] = {
    ".ts": frozenset({"fetch"}),
    ".tsx": frozenset({"fetch"}),
    ".js": frozenset({"fetch"}),
    ".jsx": frozenset({"fetch"}),
    ".mjs": frozenset({"fetch"}),
    ".py": frozenset({"urlopen"}),  # stdlib, and its first argument is the URL
}


def sink_patterns(suffix: str) -> tuple[re.Pattern[str], ...]:
    """The sink patterns for *suffix*, or empty when the language has none."""
    return _SINKS_BY_SUFFIX.get(suffix.lower(), ())


def sink_call_names(suffix: str) -> frozenset[str]:
    """Names whose *receiverless* call is itself an HTTP sink."""
    return _SINK_CALL_NAMES_BY_SUFFIX.get(suffix.lower(), frozenset())


def symbol_body(lines: list[str], symbol: IndexedSymbol) -> str:
    """The source text of *symbol*'s body, excluding its own declaration.

    The declaration must be excluded because a symbol *named* like a sink
    declares itself with the sink's own syntax: ``private async fetch<T>(path)``
    matches the sink pattern on the strength of its signature alone. Keeping it
    would confirm any symbol called ``fetch`` by name, which is the failure this
    module exists to remove.

    Excluding the whole first *line* is too blunt: a concise wrapper puts its
    body on the declaration line (``getX() { return fetch("/x"); }``) and would
    lose it entirely. So for a brace language the body starts just after the
    first ``{`` on the declaration line — the symbol's own name always precedes
    its parameter list, and therefore precedes any brace on that line, so the
    self-match is excluded either way. With no brace there (a multi-line
    signature, or Python) the body starts on the next line as before.
    """
    first = symbol.start_line - 1  # 0-based index of the declaration line
    end = symbol.end_line
    if first < 0 or end <= first:
        return ""
    decl = lines[first] if first < len(lines) else ""
    brace = decl.find("{")
    head = decl[brace + 1 :] if brace >= 0 else ""
    rest = lines[first + 1 : end]
    return "\n".join([head, *rest]) if head else "\n".join(rest)


# The module qualifier is required and is what makes this safe: a bare
# ``Session(`` is SQLAlchemy far more often than requests, and binding it would
# read every ``session.get(Model, pk)`` as an endpoint call. A name imported
# from the library (``from httpx import AsyncClient``) is as certain as the
# qualified spelling, so it binds too, but only when nothing else imports it.
_PY_CLIENT_CLASSES = frozenset({"AsyncClient", "Client", "ClientSession", "Session"})
_PY_CLIENT_LIBS = ("httpx", "requests", "aiohttp")

_PY_FROM_IMPORT_RE = re.compile(
    r"^[ \t]*from[ \t]+(?P<module>[\w.]+)[ \t]+import[ \t]+(?P<names>\([^)]*\)|[^\n]*)",
    re.MULTILINE,
)
_PY_IMPORTED_NAME_RE = re.compile(r"(?P<name>\w+)(?:\s+as\s+(?P<alias>\w+))?")


_PY_CLIENT_IMPORT_RE = re.compile(
    r"^[ \t]*from[ \t]+(?:" + "|".join(_PY_CLIENT_LIBS) + r")[ \t]+import\b", re.MULTILINE
)


def imports_client_library(content: str, suffix: str) -> bool:
    """Does *content* import a name from an HTTP client library, so a bare ``AsyncClient()`` may bind?"""
    return suffix.lower() == ".py" and _PY_CLIENT_IMPORT_RE.search(content) is not None


def _imported_client_classes(code: str) -> dict[str, str]:
    """``{local name: library}`` for each client class imported from its library alone."""
    modules: dict[str, set[str]] = {}
    written: dict[str, str] = {}
    for m in _PY_FROM_IMPORT_RE.finditer(code):
        for item in _PY_IMPORTED_NAME_RE.finditer(m.group("names").strip("()")):
            local = item.group("alias") or item.group("name")
            modules.setdefault(local, set()).add(m.group("module"))
            written[local] = item.group("name")
    return {
        local: library
        for local, found in modules.items()
        for library in found
        if len(found) == 1 and library in _PY_CLIENT_LIBS and written[local] in _PY_CLIENT_CLASSES
    }


class BoundClient(NamedTuple):
    """An HTTP client instance bound to *var*, and the scope that binding holds in."""

    var: str
    library: str
    start: int
    end: int
    #: The ``base_url`` the constructor passed, as source text; empty when it passed none.
    base: str = ""


@lru_cache(maxsize=64)
def _client_patterns(bare: tuple[str, ...]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The assignment and ``with`` readers for a file that imports *bare* client names."""
    ctor = (
        r"(?:(?P<lib>httpx|requests|aiohttp)\s*\.\s*"
        r"(?:AsyncClient|Client|ClientSession|Session)"
    )
    if bare:
        ctor += r"|(?<![\w.])(?P<bare>" + "|".join(re.escape(n) for n in bare) + ")"
    ctor += r")\s*(?P<open>)\("
    assign = re.compile(
        r"^[ \t]*(?P<attr>self\s*\.\s*)?(?P<var>[A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*(?!=)"
        r"(?:[^\n=]*?\bor\s+)?(?:await\s+)?" + ctor,
        re.MULTILINE,
    )
    with_ = re.compile(
        r"\bwith\s+(?:await\s+)?" + ctor + r"[^\n]*?\bas\s+(?P<var>[A-Za-z_]\w*)"
    )
    return assign, with_


def _base_argument(source: str, paren: int) -> str:
    """The ``base_url=`` argument of the constructor call opening at *paren*, or ``""``."""
    args = call_arguments(source, paren) or []
    for arg in args:
        m = re.match(r"base_url\s*=\s*(.+)", arg, re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def bound_clients(
    content: str, suffix: str, symbols: Sequence[IndexedSymbol], source: str | None = None
) -> list[BoundClient]:
    """Each HTTP client instance bound in *content*, with the scope it holds in.

    Python's dominant client shape binds the client to a variable
    (``async with httpx.AsyncClient() as http``) and calls the endpoint through
    it (``http.post(url)``). That receiver is neither absent nor ``self``, so
    :func:`confirm_wrappers` never sees the call — the sink *is* the call.

    The binding is scoped to the enclosing symbol, not to the file. Measured on
    ``backend/modal_app/content_engine_chain.py``: ``client`` is an
    ``httpx.AsyncClient`` in one function and a Supabase client in another, so a
    file-wide binding would read a database call as an endpoint call. An
    *attribute* binding (``self._client = httpx.Client()``) is file-scoped
    instead, since it is assigned in one method to be read from others.

    *content* is the text with string bodies blanked, so a client named in prose
    binds nothing; *source* is the same text with them kept, where the
    constructor's ``base_url=`` is read. Empty for a language with no entry,
    like the sink tables.
    """
    if suffix.lower() != ".py":
        return []
    imported = _imported_client_classes(content)
    assign_re, with_re = _client_patterns(tuple(sorted(imported)))
    # Narrowest span first, so the first hit is the tightest enclosing scope.
    spans = sorted(
        ((s.start_line, s.end_line) for s in symbols if s.kind in _CALLABLE_KINDS),
        key=lambda se: se[1] - se[0],
    )
    whole_file = (1, content.count("\n") + 1)

    def scope(line: int, is_attr: bool) -> tuple[int, int]:
        if is_attr:
            return whole_file
        for start, end in spans:
            if start <= line <= end:
                return start, end
        return whole_file  # module level: the client really is file-wide

    out: list[BoundClient] = []
    for regex in (assign_re, with_re):
        for m in regex.finditer(content):
            line = content.count("\n", 0, m.start()) + 1
            start, end = scope(line, m.groupdict().get("attr") is not None)
            library = m.group("lib") or imported[m.group("bare")]
            base = _base_argument(source, m.start("open")) if source is not None else ""
            out.append(BoundClient(m.group("var"), library, start, end, base))
    return out


def _callable_symbols(symbols: Sequence[IndexedSymbol]) -> dict[str, list[IndexedSymbol]]:
    """Callable symbols in this file, indexed by bare name.

    A name can be declared more than once (two classes with a ``get`` method);
    every candidate is kept and a wrapper chain is confirmed if *any* of them
    reaches a sink. That is deliberately the permissive side of the ambiguity:
    the alternative — dropping ambiguous names — would lose real calls in every
    file with two similarly-shaped classes.
    """
    out: dict[str, list[IndexedSymbol]] = {}
    for sym in symbols:
        if sym.kind in _CALLABLE_KINDS:
            out.setdefault(sym.name, []).append(sym)
    return out


def confirm_wrappers(
    symbols: Sequence[IndexedSymbol],
    content: str,
    suffix: str,
    budget: int = DEFAULT_HOP_BUDGET,
) -> set[str]:
    """Names of symbols in this file confirmed to reach an HTTP sink.

    Returns bare symbol names, because that is what a call site spells: the call
    ``this.fetch(path)`` names ``fetch``, not ``client.ts::HostedApiClient::fetch``.
    A name is present only if some symbol carrying it reaches a sink within
    *budget* hops.
    """
    patterns = sink_patterns(suffix)
    if not patterns:
        return set()

    # Comments and string bodies are blanked before any sink or hop matching:
    # a `fetch(` inside `// used to fetch(path) directly` is prose, and
    # confirming a wrapper on it would reintroduce name-guessing by another
    # route. Masking preserves offsets, so symbol line ranges still apply.
    lines = mask_source(content, suffix, strings=True).split("\n")
    by_name = _callable_symbols(symbols)
    if not by_name:
        return set()

    # Memo over symbol id, so a fan-in method body is scanned once regardless of
    # how many callers reach it. Values are the best (largest) budget already
    # proven insufficient, so a later, deeper visit is still allowed to try.
    failed_at: dict[str, int] = {}
    confirmed: set[str] = set()

    def reaches_sink(sym: IndexedSymbol, hops: int, stack: frozenset[str]) -> bool:
        if sym.symbol_id in stack:  # a cycle cannot introduce a new sink
            return False
        if failed_at.get(sym.symbol_id, -1) >= hops:
            return False
        body = symbol_body(lines, sym)
        if not body:
            failed_at[sym.symbol_id] = max(failed_at.get(sym.symbol_id, -1), hops)
            return False
        if any(p.search(body) for p in patterns):
            return True
        if hops <= 0:
            failed_at[sym.symbol_id] = max(failed_at.get(sym.symbol_id, -1), hops)
            return False
        inner = stack | {sym.symbol_id}
        for m in _LOCAL_CALL_RE.finditer(body):
            for callee in by_name.get(m.group(1), ()):
                if callee.symbol_id != sym.symbol_id and reaches_sink(callee, hops - 1, inner):
                    return True
        failed_at[sym.symbol_id] = max(failed_at.get(sym.symbol_id, -1), hops)
        return False

    for name, syms in by_name.items():
        if any(reaches_sink(s, budget, frozenset()) for s in syms):
            confirmed.add(name)
    return confirmed
