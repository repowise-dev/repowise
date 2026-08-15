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

Unresolved paths are counted, never guessed and never silently dropped — see
:func:`extract_consumers`' ``unresolved`` return value.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .dialect import build_consumer_contract
from .wrappers import DEFAULT_HOP_BUDGET, GENERIC_ARGS, confirm_wrappers, sink_call_names

if TYPE_CHECKING:
    from repowise.core.ingestion.models import ParsedFile

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# A call to a named function, optionally through ``this``/``self`` and optionally
# carrying TypeScript type arguments. The name is captured to be looked up
# against the confirmed-wrapper set; it is never pattern-matched for meaning.
_CALL_RE = re.compile(
    r"(?<![.\w$])(?:(?P<recv>this|self)\s*\.\s*)?(?P<name>[A-Za-z_$][\w$]*)\s*"
    + GENERIC_ARGS
    + r"\(",
)

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
    quote: str | None = None
    while i < n:
        ch = content[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            elif quote == "`" and ch == "$" and i + 1 < n and content[i + 1] == "{":
                depth += 1
                i += 2
                continue
            elif quote == "`" and ch == "}" and depth > 0:
                depth -= 1
        elif ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0 and ch == ")":
                return i
        i += 1
    return -1


def _split_first_arg(args: str) -> tuple[str, str]:
    """Split an argument list into ``(first_arg, rest)`` at the top-level comma."""
    depth = 0
    quote: str | None = None
    i = 0
    n = len(args)
    while i < n:
        ch = args[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            elif quote == "`" and ch == "$" and i + 1 < n and args[i + 1] == "{":
                depth += 1
                i += 2
                continue
            elif quote == "`" and ch == "}" and depth > 0:
                depth -= 1
        elif ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
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


def _declaration_sites(parsed: ParsedFile) -> set[tuple[int, str]]:
    """``(line, name)`` for each callable's own declaration.

    A method declaration is syntactically a call — ``fetch<T>(path: string)`` —
    so without this the wrapper's own signature would be read as a call site
    with an unresolvable first argument, inflating the unresolved count with
    one phantom per wrapper.
    """
    return {
        (s.start_line, s.name)
        for s in parsed.symbols
        if s.kind in {"function", "method", "constructor"}
    }


def extract_consumers(
    ctx: ScanContext,
    parsed: ParsedFile,
    budget: int = DEFAULT_HOP_BUDGET,
) -> tuple[list[Contract], int]:
    """Consumer contracts at confirmed wrapper call sites, plus an unresolved count.

    The second element counts call sites of a wrapper *known to take a URL path*
    — proven by another call site in the same file passing it a concrete literal
    — whose own path argument could not be resolved statically. Those are real
    endpoint calls this layer cannot name; reporting the number is what keeps
    the recall figure honest rather than flattering.
    """
    confirmed = confirm_wrappers(parsed, ctx.content, ctx.suffix, budget)
    sinks = sink_call_names(ctx.suffix)
    if not confirmed and not sinks:
        return [], 0

    content = ctx.content
    declarations = _declaration_sites(parsed)

    # Pass 1: every call site of a confirmed wrapper, split into resolved
    # (concrete literal) and unresolved. The set of wrappers proven to take a
    # path is only complete once the whole file has been scanned, so the
    # unresolved tally is settled afterwards.
    resolved: list[tuple[str, str, str]] = []  # (callee, url, method)
    unresolved_by_callee: dict[str, int] = {}
    path_taking: set[str] = set()

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
        line = content.count("\n", 0, m.start()) + 1
        if (line, name) in declarations:
            continue  # the wrapper's own signature, not a call to it
        open_idx = m.end() - 1
        close_idx = _match_paren(content, open_idx)
        if close_idx < 0:
            continue
        first, rest = _split_first_arg(content[open_idx + 1 : close_idx])
        url = _literal_url(first)
        if url is None:
            unresolved_by_callee[name] = unresolved_by_callee.get(name, 0) + 1
            continue
        path_taking.add(name)
        opt = _METHOD_OPT_RE.search(rest)
        resolved.append((name, url, opt.group(1).upper() if opt else "GET"))

    from ..from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX

    out: list[Contract] = []
    for callee, url, method in resolved:
        # Higher than the regex dialect's 0.65/0.75: the callee is a confirmed
        # sink-reaching wrapper and the URL is a whole literal argument, not a
        # string that happened to sit next to a parenthesis.
        contract = build_consumer_contract(
            ctx, method=method, url=url, client=callee, confidence=0.85
        )
        if contract is not None:
            contract.meta[EXTRACTION_LAYER_KEY] = LAYER_INDEX
            out.append(contract)

    # Only a wrapper some call site proved takes a URL contributes unresolved
    # counts. Without that gate every ``client.getSnapshot(id)`` — a call to an
    # API method, whose first argument is an id and never was a path — would be
    # reported as an unresolved endpoint, and the number would mean nothing.
    unresolved = sum(n for name, n in unresolved_by_callee.items() if name in path_taking)
    return out, unresolved
