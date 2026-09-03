"""HTTP consumer contracts at the call sites of *confirmed* wrapper functions.

The regex dialect (:mod:`.js_clients`) recognises a client call by the callee's
name. This module recognises one by asking :mod:`.wrappers` whether the callee
actually reaches an HTTP sink, so a function named ``fetchUser`` that only reads
a cache yields nothing, and a wrapper whose name carries no HTTP word is still
found.

It also parses the call's argument list properly rather than matching a literal
that happens to sit next to a parenthesis. That difference is what recovers the
bulk of the missing recall: the frontend spells every call
``this.fetch<SnapshotResponse>(`/snapshots/${id}`)``, and the existing dialect's
``fetch\\s*\\(`` cannot match across the ``<SnapshotResponse>`` type argument.

Python reaches its endpoints a second way no wrapper confirmation can see: the
call goes through a *variable* bound to a client instance
(``async with httpx.AsyncClient() as http`` … ``http.post(url)``), so its
receiver is neither absent nor ``self``. Those bindings come from
:func:`.wrappers.bound_clients` and count as sink calls in their own right.
Measured on a FastAPI service that calls its frontend: 42 such calls against 3
with a literal ``httpx.``/``requests.`` receiver, the only shape the text
dialect matches.

Unresolved paths are counted, never guessed and never silently dropped — see
:func:`extract_consumers`' ``unresolved`` return value.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import line_at
from ..langs import PYTHON
from .dialect import build_consumer_contract
from .python_urls import resolve_url_argument, string_constants
from .wrappers import (
    DEFAULT_HOP_BUDGET,
    GENERIC_ARGS,
    bound_clients,
    confirm_wrappers,
    mask_source,
    sink_call_names,
    sink_patterns,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.repo_index import IndexedSymbol

    from ..base import ScanContext

# A call to a named function, optionally through ``this``/``self`` and optionally
# carrying TypeScript type arguments. The name is captured to be looked up
# against the confirmed-wrapper set; it is never pattern-matched for meaning.
_CALL_RE = re.compile(
    r"(?<![.\w$])(?:(?P<recv>this|self)\s*\.\s*)?(?P<name>[A-Za-z_$][\w$]*)\s*"
    + GENERIC_ARGS
    + r"\(",
)

# A method call through an instance receiver. Kept apart from ``_CALL_RE``
# rather than folded into it: widening that shared pattern would change what
# the JS/TS pass sees in every file.
_CLIENT_CALL_RE = re.compile(
    r"(?<![.\w])(?:self\s*\.\s*)?(?P<recv>[A-Za-z_]\w*)\s*\.\s*(?P<verb>\w+)\s*\(",
)

# ``request`` is absent on purpose: it takes the method as its first argument
# and the URL as its second, so this rule would record the verb as the path.
_HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# A first argument concrete enough to be a URL: a path, a base placeholder, or
# an absolute URL. Mirrors the concreteness test the regex dialect already
# applies, so both paths agree on what counts as a usable literal.
_CONCRETE_URL_RE = re.compile(r"^(?:/|\$\{|https?:|//)")

# ``method: "POST"`` anywhere in the remaining arguments of the same call.
_METHOD_OPT_RE = re.compile(r"""method\s*:\s*['"](\w+)['"]""")

_QUOTES = "'\"`"


def _match_paren(content: str, open_idx: int) -> int:
    """Index of the ``)`` closing the ``(`` at *open_idx*, or -1.

    Quote- and template-aware so a parenthesis inside a string literal does not
    unbalance the scan. Nested ``${...}`` inside a template literal is tracked
    as ordinary depth, which is what makes ``` `/a/${f(x)}/b` ``` parse.
    """
    depth = 0
    i = open_idx
    n = len(content)
    # Open template literals, innermost last. A backtick inside a ``${...}``
    # opens a *nested* literal rather than closing the outer one, so the state
    # has to be a stack: ``fetch(`/a/${c ? `x` : `y`}/b`)`` otherwise reads the
    # inner backtick as the end of the string and desynchronises everything
    # after it.
    tmpl: list[int] = []
    quote: str | None = None
    while i < n:
        ch = content[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and content[i + 1] == "{":
                tmpl.append(depth)
                depth += 1
                quote = None
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if tmpl and ch == "}" and depth == tmpl[-1]:
                tmpl.pop()
                quote = "`"  # back inside the template literal that opened it
            elif depth == 0 and ch == ")":
                return i
        i += 1
    return -1


def _split_first_arg(args: str) -> tuple[str, str]:
    """Split an argument list into ``(first_arg, rest)`` at the top-level comma."""
    depth = 0
    tmpl: list[int] = []  # see :func:`_match_paren` — nested templates need a stack
    quote: str | None = None
    i = 0
    n = len(args)
    while i < n:
        ch = args[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and args[i + 1] == "{":
                tmpl.append(depth)
                depth += 1
                quote = None
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if tmpl and ch == "}" and depth == tmpl[-1]:
                tmpl.pop()
                quote = "`"
        elif ch == "," and depth == 0:
            return args[:i].strip(), args[i + 1 :]
        i += 1
    return args.strip(), ""


def _literal_url(arg: str) -> str | None:
    """The URL text of *arg* when it is a single string/template literal, else None.

    A literal spanning the whole argument is required: ``"/a" + x`` and a bare
    identifier both return ``None``, because neither is a path this layer may
    claim to know.
    """
    arg = arg.strip()
    if len(arg) < 2 or arg[0] not in _QUOTES or arg[-1] != arg[0]:
        return None
    inner = arg[1:-1]
    # A closing quote in the middle means the literal ended and something else
    # followed (concatenation), so the argument is not a single literal.
    if arg[0] in inner:
        return None
    return inner if _CONCRETE_URL_RE.match(inner) else None


def _first_arg_url(arg: str, is_python: bool, constants: dict[str, str]) -> str | None:
    """The URL a call's first argument names, or ``None`` if it is not one.

    Python additionally reads an f-string and folds a name bound to a string
    literal, then faces the same concreteness test as every other language.
    """
    if not is_python:
        return _literal_url(arg)
    url = resolve_url_argument(arg, constants)
    return url if url is not None and _CONCRETE_URL_RE.match(url) else None


def _client_library(
    clients: list[tuple[str, str, int, int]], receiver: str, line: int
) -> str | None:
    """The client library *receiver* is bound to at *line*, or ``None``."""
    for var, lib, start, end in clients:
        if var == receiver and start <= line <= end:
            return lib
    return None


def _declaration_sites(symbols: Sequence[IndexedSymbol]) -> set[tuple[int, str]]:
    """``(line, name)`` for each callable's own declaration.

    A method declaration is syntactically a call — ``fetch<T>(path: string)`` —
    so without this the wrapper's own signature would be read as a call site
    with an unresolvable first argument, inflating the unresolved count with
    one phantom per wrapper.
    """
    return {
        (s.start_line, s.name)
        for s in symbols
        if s.kind in {"function", "method", "constructor"}
    }


def _confirmed_ranges(
    symbols: Sequence[IndexedSymbol], confirmed: set[str]
) -> list[tuple[int, int]]:
    """Line extents of the symbols confirmed to reach a sink."""
    return [
        (s.start_line, s.end_line)
        for s in symbols
        if s.name in confirmed and s.kind in {"function", "method", "constructor"}
    ]


def _within(ranges: list[tuple[int, int]], line: int) -> bool:
    return any(start <= line <= end for start, end in ranges)


def extract_consumers(
    ctx: ScanContext,
    symbols: Sequence[IndexedSymbol],
    budget: int = DEFAULT_HOP_BUDGET,
) -> tuple[list[Contract], int]:
    """Consumer contracts at confirmed wrapper call sites, plus an unresolved count.

    The second element counts call sites of a wrapper *known to take a URL path*
    — proven by another call site in the same file passing it a concrete literal
    — whose own path argument could not be resolved statically, plus every
    unresolved call through a bound client instance (whose binding already
    proves the first argument is a URL, so no such gate applies). Those are real
    endpoint calls this layer cannot name; reporting the number is what keeps
    the recall figure honest rather than flattering.
    """
    # No sink anywhere in the raw text means no wrapper can be confirmed and no
    # receiverless sink call can exist, so the two O(n) masking passes below
    # would run to find nothing. Masking only removes matches, so a miss here is
    # a miss there. Most files in a repo take this exit.
    if not any(p.search(ctx.content) for p in sink_patterns(ctx.suffix)):
        return [], 0

    confirmed = confirm_wrappers(symbols, ctx.content, ctx.suffix, budget)
    sinks = sink_call_names(ctx.suffix)

    # Comments are blanked (string bodies are not — the URL literal is what we
    # are here to read). This both stops a commented-out call becoming a
    # contract and keeps an apostrophe in prose from desynchronising the
    # argument scanner. Masking preserves offsets and length, so slices taken
    # against this text are the real argument text.
    content = mask_source(ctx.content, ctx.suffix)
    # A second mask for Python, with string bodies blanked too. Offsets are
    # preserved, so this locates code positions while the text above supplies
    # their bytes. Everything Python-side is found through it, because a log
    # line reading ``"call foo.get('/api/x') to see"`` is prose, not a call, and
    # reading it off ``content`` would mint a contract for an unmade request.
    is_python = ctx.suffix in PYTHON
    code = mask_source(ctx.content, ctx.suffix, strings=True) if is_python else ""
    clients = bound_clients(code, ctx.suffix, symbols)
    if not confirmed and not sinks and not clients:
        return [], 0

    constants = string_constants(content, code) if is_python else {}
    declarations = _declaration_sites(symbols)

    # Pass 1: every call site of a confirmed wrapper, split into resolved
    # (concrete literal) and unresolved. The set of wrappers proven to take a
    # path is only complete once the whole file has been scanned, so the
    # unresolved tally is settled afterwards.
    resolved: list[tuple[str, str, str, int]] = []  # (callee, url, method, line)
    unresolved_by_callee: dict[str, int] = {}
    path_taking: set[str] = set()
    # Calls whose argument list would not parse. Kept apart from the
    # non-literal tally because they must be reported unconditionally: the
    # `path_taking` gate below is a judgement about what a *resolved* call
    # proved, and a call that never parsed proved nothing either way. Gating
    # these would let a scanner failure zero itself out.
    parse_failures = 0
    confirmed_ranges = _confirmed_ranges(symbols, confirmed)

    for m in _CALL_RE.finditer(content):
        name = m.group("name")
        # Either the callee is a wrapper proven to reach a sink, or the call is
        # a receiverless call to a sink itself (a streaming endpoint calls the
        # global ``fetch`` directly, bypassing the client's own wrapper). A
        # *member* call that merely shares a sink's name — ``this.fetch(...)`` —
        # gets no such shortcut and must be confirmed on its own merits.
        is_sink_call = m.group("recv") is None and name in sinks
        if not is_sink_call and name not in confirmed:
            continue
        line = line_at(content, m.start())
        if (line, name) in declarations:
            continue  # the wrapper's own signature, not a call to it
        open_idx = m.end() - 1
        close_idx = _match_paren(content, open_idx)
        if close_idx < 0:
            # A call whose argument list would not parse is still a call to a
            # wrapper. Counting it rather than dropping it keeps the guarantee
            # that nothing located is silently discarded — a scanner failure
            # must show up in the number, not hide inside it.
            parse_failures += 1
            continue
        first, rest = _split_first_arg(content[open_idx + 1 : close_idx])
        url = _first_arg_url(first, is_python, constants)
        if url is None:
            # The wrapper's own plumbing — ``fetch(path)`` inside the very
            # function that wraps it — is not a lost endpoint. Whatever flows
            # through it is already counted at that wrapper's call sites, so
            # counting it here would report each indirection as a miss.
            if is_sink_call and _within(confirmed_ranges, line):
                continue
            unresolved_by_callee[name] = unresolved_by_callee.get(name, 0) + 1
            continue
        path_taking.add(name)
        opt = _METHOD_OPT_RE.search(rest)
        resolved.append((name, url, opt.group(1).upper() if opt else "GET", line))

    # Pass 1b: calls through a variable bound to an HTTP client instance. No
    # wrapper confirmation applies — the binding already proves the receiver.
    client_unresolved = 0
    for m in _CLIENT_CALL_RE.finditer(code) if clients else ():
        verb = m.group("verb")
        if verb not in _HTTP_VERBS:
            continue
        line = line_at(content, m.start())
        lib = _client_library(clients, m.group("recv"), line)
        if lib is None:
            continue
        open_idx = m.end() - 1
        close_idx = _match_paren(content, open_idx)
        if close_idx < 0:
            parse_failures += 1
            continue
        first, _ = _split_first_arg(content[open_idx + 1 : close_idx])
        url = _first_arg_url(first, is_python, constants)
        if url is None:
            # Unconditional, unlike the wrapper tally above: a client's
            # ``.post`` takes a URL by definition, so ``path_taking`` has no
            # ambiguity to resolve and every miss here is a real endpoint call.
            client_unresolved += 1
            continue
        resolved.append((lib, url, verb.upper(), line))

    from ..from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX

    out: list[Contract] = []
    for callee, url, method, line in resolved:
        # Higher than the regex dialect's 0.65/0.75: the callee is a confirmed
        # sink-reaching wrapper and the URL is a whole literal argument, not a
        # string that happened to sit next to a parenthesis.
        contract = build_consumer_contract(
            ctx, method=method, url=url, client=callee, line=line, confidence=0.85
        )
        if contract is not None:
            contract.meta[EXTRACTION_LAYER_KEY] = LAYER_INDEX
            out.append(contract)

    # Only a wrapper some call site proved takes a URL contributes unresolved
    # counts. Without that gate every ``client.getSnapshot(id)`` — a call to an
    # API method, whose first argument is an id and never was a path — would be
    # reported as an unresolved endpoint, and the number would mean nothing.
    unresolved = sum(n for name, n in unresolved_by_callee.items() if name in path_taking)
    return out, unresolved + parse_failures + client_unresolved
