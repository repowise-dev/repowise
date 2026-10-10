"""Call an existing function instead of extracting a new helper (Extract Helper).

A clone group can contain a whole function: one site *is* ``F`` and the others
repeat its body. A new helper would be a second copy of ``F``, so the plan
becomes "call ``F``" at every other site, or "delete this copy and import
``F``" where a site is ``F`` again under the same header.

Precision first. Every site must hold, or the group keeps its new-helper plan:

- one occurrence and ``F`` cover at least 90% of each other; ``F`` is a
  function or method outside tests (the detector already dropped test sites);
- the site repeats ``F``'s body line for line, whitespace and whole-line
  comments aside. The clone index matches token shapes, so the text is checked
  here: this is the exact (type-1) clone only;
- ``F`` is reachable from the site: the same file, or a public top-level
  function in the same directory or in a file the site imports. A method only
  from its own class;
- the call can stand in for the lines: every parameter of ``F`` is a name the
  lines use, they assign nothing their function reads elsewhere, a ``return``
  in them ends that function, they do not ``yield``, and an async ``F`` is
  called from an async function.

Python and TypeScript / JavaScript only: the checks read their statements.
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from ....distill.skeleton import body_span
from ...signature_effect import parameter_names
from . import render

#: Share of each other's lines an occurrence and ``F`` must cover for the site to be ``F``.
MIN_COVERAGE = 0.9
_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_CALLABLE = frozenset({"function", "method"})
_DOC_OPENERS = re.compile(r"^[rRuUbB]?(\"\"\"|''')")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_RECEIVER = re.compile(r"\b(self|cls|this)\b")
_YIELD = re.compile(r"\byield\b")
_PY_RETURN = re.compile(r"^return\b")
_JS_RETURN = re.compile(r"(?:^|[\s;{}])return\b")
_PY_ASSIGN = re.compile(
    r"^\(?([\w\s,]+?)\)?\s*(?::[^=]+)?(?:[-+*/%&|^@]|//|\*\*|>>|<<)?=(?!=)"
)
_PY_BIND = re.compile(r"\b(?:for|as)\s+\(?([\w\s,]+?)\)?\s*(?:\bin\b|:|$)")
_JS_DECL = re.compile(r"\b(?:const|let|var)\s+([^=;]+?)\s*(?:=|;|\bof\b|\bin\b|$)")
_JS_ASSIGN = re.compile(r"^([A-Za-z_$][\w$]*)\s*(?:[-+*/%&|^]|\*\*|>>>?|<<|\?\?|\|\||&&)?=(?!=)")
_EXPORT = re.compile(r"^export\s+(default\s+)?")
_CLOSERS = re.compile(r"^[})\];,\s]*$")

Lines = list[str]
Occurrence = tuple[str, int, int]
Site = dict[str, Any]


class Reuse(NamedTuple):
    """The ``plan.reuse`` payload, or why a site that is a function was refused."""

    plan: dict[str, Any] | None
    refused: str | None = None


@dataclass(frozen=True)
class _Fn:
    id: str
    name: str
    kind: str
    file: str
    start: int
    end: int
    parent: str | None
    signature: str
    is_async: bool
    private: bool


class _Repo:
    """The graph and source one detector pass reads, each file looked up once."""

    def __init__(self, graph: Any, language: str, read: Callable[[str], Lines | None]) -> None:
        self.graph = graph
        self.language = language
        self.read = read
        self._fns: dict[str, list[_Fn]] = {}

    def functions(self, path: str) -> list[_Fn]:
        hit = self._fns.get(path)
        if hit is None:
            hit = self._fns[path] = _functions(self.graph, path, self.language)
        return hit

    def imports(self, importer: str, imported: str) -> bool:
        if not self.graph.has_edge(importer, imported):
            return False
        return self.graph.edges[importer, imported].get("edge_type") in ("imports", "dynamic_imports")


def find_reuse(
    graph: Any,
    language: str,
    occurrences: list[Occurrence],
    read: Callable[[str], Lines | None],
) -> Reuse:
    """The reuse payload when one occurrence is a function every other site can
    call, else ``Reuse(None, reason)`` (``reason`` None: no occurrence is one)."""
    if graph is None or language not in _LANGUAGES:
        return Reuse(None)
    repo = _Repo(graph, language, read)
    candidates = sorted(
        ((fn, occ) for occ in occurrences for fn in _covered(repo, occ)),
        key=lambda c: (c[0].private, c[0].file, c[0].start),
    )
    refused = None
    for fn, own in candidates:
        sites, reason = _sites(repo, fn, [o for o in occurrences if o != own])
        if sites is not None:
            return Reuse(_payload(fn, sites))
        refused = refused or reason
    return Reuse(None, refused)


def _functions(graph: Any, path: str, language: str) -> list[_Fn]:
    """Functions and methods *path* defines, via ``defines`` edges."""
    if path not in graph:
        return []
    out = []
    for _u, sid, data in graph.out_edges(path, data=True):
        node = graph.nodes[sid]
        start, end = node.get("start_line"), node.get("end_line")
        if data.get("edge_type") != "defines" or node.get("kind") not in _CALLABLE:
            continue
        if not (isinstance(start, int) and isinstance(end, int) and end > start):
            continue
        name = str(node.get("name") or "")
        signature = str(node.get("signature") or "")
        # ``visibility`` is the parser's export reading (a TS function without
        # ``export`` is private); Python's underscore is the convention on top.
        private = node.get("visibility") != "public" or (
            language == "python" and name.startswith("_")
        )
        out.append(
            _Fn(
                id=sid,
                name=name,
                kind=node["kind"],
                file=path,
                start=start,
                end=end,
                parent=node.get("parent_name"),
                signature=signature,
                # A graph rebuilt from storage has no ``is_async``; the header says it.
                is_async=bool(node.get("is_async")) or "async" in signature.split("(")[0].split(),
                private=private,
            )
        )
    return out


def _covered(repo: _Repo, occ: Occurrence) -> list[_Fn]:
    """Functions the occurrence is: each covers the other at ``MIN_COVERAGE``
    (a two-line function inside a long block is not the block)."""
    path, start, end = occ
    out = []
    for fn in repo.functions(path):
        shared = min(end, fn.end) - max(start, fn.start) + 1
        if shared >= MIN_COVERAGE * max(fn.end - fn.start + 1, end - start + 1):
            out.append(fn)
    return out


def _sites(repo: _Repo, fn: _Fn, others: list[Occurrence]) -> tuple[list[Site] | None, str | None]:
    lines = repo.read(fn.file)
    body = _body(lines, fn, repo.language) if lines else None
    if not body:
        return None, "no_body"
    sites = []
    for occ in others:
        site, reason = _site(repo, fn, body, occ)
        if site is None:
            return None, reason
        sites.append(site)
    return sites, None


def _site(
    repo: _Repo, fn: _Fn, body: list[tuple[int, str]], occ: Occurrence
) -> tuple[Site | None, str | None]:
    path = occ[0]
    lines = repo.read(path)
    if not lines:
        return None, "site_unread"
    span = _match(lines, body, occ, repo.language, 3 + (fn.end - fn.start) // 10)
    if span is None:
        return None, "text_differs"
    unreachable = _unreachable(repo, fn, path)
    if unreachable:
        return None, unreachable
    enclosing = [g for g in repo.functions(path) if g.start < span[0] and g.end >= span[1]]
    host = max(enclosing, key=lambda g: g.start, default=None)
    if host is None:
        return None, "not_in_function"
    host_body = _body(lines, host, repo.language)
    if host_body and (host_body[0][0], host_body[-1][0]) == span:
        return _twin_site(repo, fn, host, span, lines)
    return _call_site(repo, fn, host, span, lines)


def _unreachable(repo: _Repo, fn: _Fn, path: str) -> str | None:
    """Why *path* cannot call *fn*, or None when it can."""
    if fn.kind == "function" and fn.parent:
        return "not_importable"  # a nested function: only its parent sees it
    if path == fn.file:
        return None
    if fn.kind != "function" or fn.private:
        return "not_importable"
    same_dir = PurePosixPath(path).parent == PurePosixPath(fn.file).parent
    return None if same_dir or repo.imports(path, fn.file) else "not_importable"


def _twin_site(
    repo: _Repo, fn: _Fn, twin: _Fn, span: tuple[int, int], lines: Lines
) -> tuple[Site | None, str | None]:
    """*twin* is a whole copy of *fn*. Under the same name it is deleted for an
    import (a body calling *fn* by that name would call itself); under another
    name its header stays and its body becomes one call to *fn*."""
    if (fn.kind == "method" or twin.kind == "method") and not _same_class(fn, twin):
        return None, "receiver"
    mine = parameter_names(fn.signature, repo.language, fn.kind)
    theirs = parameter_names(twin.signature, repo.language, twin.kind)
    same_params = mine is not None and theirs is not None and mine[1] == theirs[1]
    if fn.name == twin.name:
        # An identical header needs no parameter reading (destructured props);
        # TypeScript checks parameter types, so there only the header will do.
        own = repo.read(fn.file) or []
        same_header = _header(own, fn, repo.language) == _header(lines, twin, repo.language)
        loose = same_params and repo.language != "typescript"
        if fn.is_async != twin.is_async or not (same_header or loose):
            return None, "param_mismatch"
        return _site_dict(twin.file, (twin.start, twin.end), "delete", None, twin), None
    if mine is None or not same_params:
        return None, "param_mismatch"
    call = _call(repo.language, fn, mine, async_host=twin.is_async)
    if call is None:
        return None, "host_not_async"
    return _site_dict(twin.file, span, "replace_with_call", f"return {call}", twin), None


def _call_site(
    repo: _Repo, fn: _Fn, host: _Fn, span: tuple[int, int], lines: Lines
) -> tuple[Site | None, str | None]:
    """The lines are a block inside *host*: one call to *fn* replaces them."""
    lang = repo.language
    block = [t for _n, t in _code(lines, span[0], span[1], lang)]
    sig = parameter_names(fn.signature, lang, fn.kind)
    refused = _block_refusal(fn, host, block, sig)
    if refused or sig is None:
        return None, refused or "param_mismatch"
    after = [t for _n, t in _code(lines, span[1] + 1, host.end, lang)]
    elsewhere = [t for _n, t in _code(lines, host.start, span[0] - 1, lang)] + after
    if _assigned(block, lang) & _names(elsewhere):
        return None, "outputs_used_after"
    returns = any((_PY_RETURN if lang == "python" else _JS_RETURN).search(t) for t in block)
    if returns and not all(_CLOSERS.match(t) for t in after):
        return None, "returns_mid_host"
    call = _call(lang, fn, sig, async_host=host.is_async)
    if call is None:
        return None, "host_not_async"
    text = f"return {call}" if returns else call
    return _site_dict(host.file, span, "replace_with_call", text, None), None


def _block_refusal(
    fn: _Fn, host: _Fn, block: list[str], sig: tuple[str | None, list[str]] | None
) -> str | None:
    """Why a call to *fn* cannot stand in for *block* wherever it sits."""
    if fn.kind == "method" and not _same_class(fn, host):
        return "receiver"
    if host.name == fn.name:
        return "name_shadowed"  # the call would reach the host itself
    if any(_YIELD.search(t) for t in block):
        return "generator"
    if fn.kind != "method" and any(_RECEIVER.search(t) for t in block):
        return "receiver"
    if sig is None or not set(sig[1]) <= _names(block):
        return "param_mismatch"
    return None


def _call(
    language: str, fn: _Fn, sig: tuple[str | None, list[str]], *, async_host: bool
) -> str | None:
    """The call to *fn*, from the shared renderer; None when the host cannot
    await it or a Python method is not reached through ``self``."""
    receiver, params = sig
    if fn.kind == "method" and language == "python" and receiver != "self":
        return None
    shape = render.HelperShape(
        language=language,
        name=fn.name,
        kind=fn.kind,
        is_async=fn.is_async,
        params=tuple(render.Slot(p) for p in params),
        receiver=receiver if fn.kind == "method" else None,
        async_host=async_host,
    )
    out = render.render(shape)
    return out.call if out else None


def _same_class(a: _Fn, b: _Fn) -> bool:
    return a.file == b.file and a.parent is not None and a.parent == b.parent


def _site_dict(
    path: str, span: tuple[int, int], action: str, text: str | None, twin: _Fn | None
) -> Site:
    return {
        "file": path,
        "span": {"start": span[0], "end": span[1]},
        "action": action,
        "new_text": text,
        "replaces": twin.id if twin else None,
    }


def _payload(fn: _Fn, sites: list[Site]) -> dict[str, Any]:
    others = len(sites)
    return {
        "existing_symbol": fn.id,
        "file": fn.file,
        "span": {"start": fn.start, "end": fn.end},
        "reason": (
            f"`{fn.name}` already holds this block; "
            + ("the other site repeats" if others == 1 else f"the other {others} sites repeat")
            + " its body line for line."
        ),
        "sites": sites,
    }


# -- text ----------------------------------------------------------------------


def _code(lines: Lines, first: int, last: int, language: str) -> list[tuple[int, str]]:
    """``(line, text)`` for the code lines of 1-indexed *first*..*last*:
    whitespace collapsed, blank and whole-line comment lines dropped."""
    comments = ("#",) if language == "python" else ("//", "/*", "*")
    out = []
    for n in range(max(first, 1), min(last, len(lines)) + 1):
        text = " ".join(lines[n - 1].split())
        if text and not text.startswith(comments):
            out.append((n, text))
    return out


def _body(lines: Lines, fn: _Fn, language: str) -> list[tuple[int, str]]:
    """*fn*'s body code lines, a leading Python docstring left out."""
    first, last = body_span(lines, fn.start - 1, min(fn.end, len(lines)) - 1)
    code = _code(lines, first + 1, last + 1, language)
    if language == "python" and code and (m := _DOC_OPENERS.match(code[0][1])):
        delim = m.group(1)
        if delim in code[0][1][m.end() :]:
            return code[1:]
        close = next((i for i, (_n, t) in enumerate(code[1:], 1) if delim in t), len(code))
        return code[close + 1 :]
    return code


def _header(lines: Lines, fn: _Fn, language: str) -> list[str]:
    """*fn*'s header lines, an ``export`` prefix aside (the import replaces it)."""
    first, _last = body_span(lines, fn.start - 1, min(fn.end, len(lines)) - 1)
    head = [t for _n, t in _code(lines, fn.start, first, language)]
    return [_EXPORT.sub("", head[0]), *head[1:]] if head else head


def _match(
    lines: Lines, body: list[tuple[int, str]], occ: Occurrence, language: str, slack: int
) -> tuple[int, int] | None:
    """Where *body* appears line for line near the occurrence, else None."""
    window = _code(lines, occ[1] - slack, occ[2] + slack, language)
    want = [t for _n, t in body]
    texts = [t for _n, t in window]
    for i in range(len(texts) - len(want) + 1):
        if texts[i : i + len(want)] == want:
            return window[i][0], window[i + len(want) - 1][0]
    return None


def _names(texts: list[str]) -> set[str]:
    return {name for t in texts for name in _IDENT.findall(t)}


def _assigned(texts: list[str], language: str) -> set[str]:
    """Names the lines bind (assignments, loop and ``as`` targets, JS
    declarations). Attribute and item writes bind no name."""
    found: list[str] = []
    for t in texts:
        if language == "python":
            m = _PY_ASSIGN.match(t)
            found += _PY_BIND.findall(t)
        else:
            m = _JS_ASSIGN.match(t)
            found += _JS_DECL.findall(t)
        if m:
            found.append(m.group(1))
    return {n for group in found for n in _IDENT.findall(group) if not keyword.iskeyword(n)}


__all__ = ["MIN_COVERAGE", "Reuse", "find_reuse"]
