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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repowise.core.ingestion.models import ParsedFile, Symbol

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
}


_JS_LIKE = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs"})

# A ``/`` opens a regex literal (rather than dividing) when the previous
# significant character cannot end an expression. The standard heuristic, and
# enough to stop a ``/\\)/`` corrupting a parenthesis scan.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%~^<>")


def mask_source(text: str, suffix: str, *, strings: bool = False) -> str:
    """Blank out comments — and optionally string bodies — preserving offsets.

    Every masked character is replaced by a space and newlines are kept, so the
    result is the same length as *text* and indexes into it interchangeably.
    That is what lets the call scanner match against masked text while slicing
    arguments by the same offsets.

    Two callers, two needs. Sink detection masks strings as well, because a
    ``fetch(`` inside a comment or a string is not a call — without this, a
    symbol is confirmed an HTTP wrapper by a line like
    ``// used to fetch(path) directly``, which is the name-guessing failure
    reappearing through the back door. The call-site scanner masks comments
    only, since it still has to read the URL literal it is looking for.
    """
    if suffix.lower() not in _JS_LIKE:
        # Python: ``#`` to end of line. Triple-quoted strings are left alone;
        # no sink pattern here matches inside one without a call following it.
        if suffix.lower() != ".py":
            return text
        out = list(text)
        i, n, in_str = 0, len(text), ""
        while i < n:
            ch = text[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = ""
            elif ch in "'\"":
                in_str = ch
            elif ch == "#":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
                continue
            i += 1
        return "".join(out)

    out = list(text)
    i, n = 0, len(text)
    # Stack of open template literals, so a nested `` `x` `` inside ``${...}``
    # is opened rather than read as closing the outer one.
    tmpl_depth: list[int] = []
    quote = ""
    prev_sig = ""
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                if strings:
                    out[i] = " "
                    if i + 1 < n and text[i + 1] != "\n":
                        out[i + 1] = " "
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and text[i + 1] == "{":
                tmpl_depth.append(1)
                quote = ""
                i += 2
                continue
            if ch == quote:
                quote = ""
            elif strings:
                out[i] = " " if ch != "\n" else "\n"
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            for j in range(i, min(i + 2, n)):
                out[j] = " "
            i += 2
            continue
        if ch == "/" and prev_sig in _REGEX_PRECEDERS:
            i += 1
            while i < n and text[i] != "\n":
                if text[i] == "\\":
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if text[i] == "/":
                    out[i] = " "
                    i += 1
                    break
                out[i] = " "
                i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif tmpl_depth and ch == "}":
            tmpl_depth.pop()
            quote = "`"
        elif tmpl_depth and ch == "{":
            tmpl_depth[-1] += 1
        if not ch.isspace():
            prev_sig = ch
        i += 1
    return "".join(out)


def sink_patterns(suffix: str) -> tuple[re.Pattern[str], ...]:
    """The sink patterns for *suffix*, or empty when the language has none."""
    return _SINKS_BY_SUFFIX.get(suffix.lower(), ())


def sink_call_names(suffix: str) -> frozenset[str]:
    """Names whose *receiverless* call is itself an HTTP sink."""
    return _SINK_CALL_NAMES_BY_SUFFIX.get(suffix.lower(), frozenset())


def symbol_body(lines: list[str], symbol: Symbol) -> str:
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


def _callable_symbols(parsed: ParsedFile) -> dict[str, list[Symbol]]:
    """Callable symbols in this file, indexed by bare name.

    A name can be declared more than once (two classes with a ``get`` method);
    every candidate is kept and a wrapper chain is confirmed if *any* of them
    reaches a sink. That is deliberately the permissive side of the ambiguity:
    the alternative — dropping ambiguous names — would lose real calls in every
    file with two similarly-shaped classes.
    """
    out: dict[str, list[Symbol]] = {}
    for sym in parsed.symbols:
        if sym.kind in _CALLABLE_KINDS:
            out.setdefault(sym.name, []).append(sym)
    return out


def confirm_wrappers(
    parsed: ParsedFile,
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
    by_name = _callable_symbols(parsed)
    if not by_name:
        return set()

    # Memo over symbol id, so a fan-in method body is scanned once regardless of
    # how many callers reach it. Values are the best (largest) budget already
    # proven insufficient, so a later, deeper visit is still allowed to try.
    failed_at: dict[str, int] = {}
    confirmed: set[str] = set()

    def reaches_sink(sym: Symbol, hops: int, stack: frozenset[str]) -> bool:
        if sym.id in stack:  # a cycle cannot introduce a new sink
            return False
        if failed_at.get(sym.id, -1) >= hops:
            return False
        body = symbol_body(lines, sym)
        if not body:
            failed_at[sym.id] = max(failed_at.get(sym.id, -1), hops)
            return False
        if any(p.search(body) for p in patterns):
            return True
        if hops <= 0:
            failed_at[sym.id] = max(failed_at.get(sym.id, -1), hops)
            return False
        inner = stack | {sym.id}
        for m in _LOCAL_CALL_RE.finditer(body):
            for callee in by_name.get(m.group(1), ()):
                if callee.id != sym.id and reaches_sink(callee, hops - 1, inner):
                    return True
        failed_at[sym.id] = max(failed_at.get(sym.id, -1), hops)
        return False

    for name, syms in by_name.items():
        if any(reaches_sink(s, budget, frozenset()) for s in syms):
            confirmed.add(name)
    return confirmed
