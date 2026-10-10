"""Call an existing function instead of extracting a new helper (Extract Helper).

A clone group can contain a whole function: one site *is* ``F`` and the others
repeat its body. A new helper would be a second copy of ``F``, so the plan
becomes "call ``F``" at every other site, or "delete this copy and import
``F``" where a site is ``F`` again under the same header.

Precision first: a plan that is wrong is worse than none. Every site must hold,
or the group keeps its new-helper plan:

- one occurrence and ``F`` cover at least 90% of each other; ``F`` is an
  undecorated function or method outside tests;
- the site repeats ``F``'s body line for line, whitespace and whole-line
  comments aside. The clone index matches token shapes, so the text is checked
  here: this is the exact (type-1) clone only;
- every name the body reads that is not its own (a module global, an import)
  is bound to the same thing in the site's file, and the site's function does
  not shadow it;
- ``F`` is reachable: the same file, or a public top-level function the site's
  file already imports, or one in its directory whose file does not reach the
  site's (no import cycle). A method only from its own class;
- the call can stand in for the lines: every parameter of ``F`` is a name the
  lines use, they assign nothing their function reads elsewhere, a ``return``
  in them ends that function, they do not ``yield``, and an async ``F`` is
  called from an async function;
- a whole copy is deleted only when nothing else could import it, and replaced
  by a call only when its header is ``F``'s under another name.

Python and TypeScript / JavaScript only: the checks read their statements.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, NamedTuple, get_args

from ....callable_spans import body_span
from ...graph_view import ImportEdgeView
from ...signature_effect import parameter_names
from . import render
from .graph_signals import defined_symbols
from .reuse_names import exported_later, is_builtin, module_bindings, module_of, read_names

ReuseSiteAction = Literal["replace_with_call", "delete"]
#: What a reuse plan does at a site. Mirrored in ``packages/types``.
REUSE_SITE_ACTIONS: tuple[str, ...] = get_args(ReuseSiteAction)

#: Share of each other's lines an occurrence and ``F`` must cover for the site to be ``F``.
MIN_COVERAGE = 0.9
_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_CALLABLE = frozenset({"function", "method"})
_DEPENDENCY = ("imports", "dynamic_imports")
_DOC_OPENERS = re.compile(r"^[rRuUbB]?(\"\"\"|''')")
_RECEIVER = re.compile(r"\b(self|cls|this)\b")
_YIELD = re.compile(r"\byield\b")
_PY_SCOPE = re.compile(r"^(global|nonlocal|del)\b")
_PY_RETURN = re.compile(r"^return\b")
_JS_RETURN = re.compile(r"(?:^|[\s;{}])return\b")
_PY_ASSIGN = re.compile(
    r"^\(?([\w\s,]+?)\)?\s*(?::[^=]+)?(?:[-+*/%&|^@]|//|\*\*|>>|<<)?=(?!=)"
)
_PY_BIND = re.compile(r"\b(?:for|as)\s+\(?([\w\s,]+?)\)?\s*(?:\bin\b|:|$)")
_PY_LAMBDA = re.compile(r"\blambda\s+([\w\s,*=]*):")
_PY_LOCAL_IMPORT = re.compile(r"^(?:from\s+\S+\s+)?import\s+(.+)$")
_WALRUS = re.compile(r"([A-Za-z_]\w*)\s*:=")
_JS_DECL = re.compile(r"\b(?:const|let|var)\s+([^=;]+?)\s*(?:=|;|\bof\b|\bin\b|$)")
_JS_ASSIGN = re.compile(r"^([A-Za-z_$][\w$]*)\s*(?:[-+*/%&|^]|\*\*|>>>?|<<|\?\?|\|\||&&)?=(?!=)")
_JS_PARAMS = re.compile(r"\(([^()]*)\)\s*(?::[^=]*)?=>|([A-Za-z_$][\w$]*)\s*=>|catch\s*\(\s*([^)]*)\)")
_EXPORT = re.compile(r"^export\s+(default\s+)?")
_JS_METHOD_MODIFIER = re.compile(r"^(?:\w+\s+)*?(static|abstract|get|set)\s|^\*")
_CLOSERS = re.compile(r"^[})\];,\s]*$")
_IDENTS = re.compile(r"[A-Za-z_$][\w$]*")

Lines = list[str]
Occurrence = tuple[str, int, int]
Site = dict[str, Any]
Code = list[tuple[int, str]]


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
    qualified: str
    is_async: bool
    private: bool


class _Repo:
    """The graph and source one detector pass reads, each file looked up once."""

    def __init__(self, graph: Any, language: str, read: Callable[[str], Lines | None]) -> None:
        self.graph = graph
        self.edges = ImportEdgeView(graph)
        self.language = language
        self.read = read
        self._fns: dict[str, list[_Fn]] = {}
        self._bindings: dict[str, dict[str, set[tuple[str, ...]]]] = {}

    def functions(self, path: str) -> list[_Fn]:
        hit = self._fns.get(path)
        if hit is None:
            hit = self._fns[path] = _functions(self.graph, path, self.language)
        return hit

    def bindings(self, path: str) -> dict[str, set[tuple[str, ...]]]:
        hit = self._bindings.get(path)
        if hit is None:
            hit = self._bindings[path] = module_bindings(self.read(path) or [], path, self.language)
        return hit

    def imports(self, importer: str, imported: str) -> bool:
        return any(self.edges.has_edge(importer, imported, kind) for kind in _DEPENDENCY)

    def reaches(self, start: str, goal: str) -> bool:
        """Whether *start* depends on *goal* through file imports."""
        seen, queue = {start}, deque([start])
        while queue:
            node = queue.popleft()
            for _u, nxt, data in self.graph.out_edges(node, data=True):
                if data.get("edge_type") in _DEPENDENCY and nxt not in seen:
                    if nxt == goal:
                        return True
                    seen.add(nxt)
                    queue.append(nxt)
        return False


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
    """Functions and methods *path* defines."""
    out = []
    for sid, node in defined_symbols(graph, path):
        if node.get("kind") not in _CALLABLE or node["end_line"] <= node["start_line"]:
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
                start=node["start_line"],
                end=node["end_line"],
                parent=node.get("parent_name"),
                signature=signature,
                qualified=str(node.get("qualified_name") or ""),
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


@dataclass(frozen=True)
class _Source:
    """``F`` read once: its body, header, own names and the names it reads."""

    fn: _Fn
    body: Code
    header: list[str]
    params: tuple[str | None, list[str]] | None
    own: frozenset[str]
    reads: frozenset[str]


def _sites(repo: _Repo, fn: _Fn, others: list[Occurrence]) -> tuple[list[Site] | None, str | None]:
    lines = repo.read(fn.file)
    src = _source(repo, fn, lines) if lines else None
    if src is None:
        return None, "no_body"
    refused = _source_refusal(repo, src, lines or [])
    if refused:
        return None, refused
    sites = []
    for occ in others:
        site, reason = _site(repo, src, occ)
        if site is None:
            return None, reason
        sites.append(site)
    return sites, None


def _source(repo: _Repo, fn: _Fn, lines: Lines) -> _Source | None:
    lang = repo.language
    body = _body(lines, fn, lang)
    if not body:
        return None
    header = _header(lines, fn, lang)
    params = parameter_names(fn.signature, lang, fn.kind)
    texts = [t for _n, t in body]
    if params is not None:
        given = {p for p in (params[0], *params[1]) if p}
    else:
        # Destructured parameters: every name the header declares is the function's.
        given = set(_IDENTS.findall(" ".join(header)))
    own = given | _assigned(texts, lang) | {"self", "cls", "this", fn.name}
    # The header's names too: an annotation's type must mean the same at the site.
    reads = read_names([*texts, *header], lang)
    return _Source(fn, body, header, params, frozenset(own), frozenset(reads))


def _source_refusal(repo: _Repo, src: _Source, lines: Lines) -> str | None:
    """Why *src* can stand in for no copy at all."""
    if _decorated(lines, src.fn):
        return "decorated"
    if repo.language == "python" and any(_PY_SCOPE.match(t) for _n, t in src.body):
        return "scope_statement"
    head = src.header[0] if src.header else ""
    if repo.language != "python" and src.fn.kind == "method" and _JS_METHOD_MODIFIER.match(head):
        return "static_or_accessor"
    return None


def _site(repo: _Repo, src: _Source, occ: Occurrence) -> tuple[Site | None, str | None]:
    path = occ[0]
    lines = repo.read(path)
    if not lines:
        return None, "site_unread"
    fn = src.fn
    span = _match(lines, src.body, occ, repo.language, 3 + (fn.end - fn.start) // 10)
    if span is None:
        return None, "text_differs"
    refused = _unreachable(repo, fn, path) or _foreign_names(repo, src, path)
    if refused:
        return None, refused
    enclosing = [g for g in repo.functions(path) if g.start < span[0] and g.end >= span[1]]
    host = max(enclosing, key=lambda g: g.start, default=None)
    if host is None:
        return None, "not_in_function"
    host_body = _body(lines, host, repo.language)
    if host_body and (host_body[0][0], host_body[-1][0]) == span:
        return _twin_site(repo, src, host, span, lines)
    return _call_site(repo, src, host, span, lines)


def _unreachable(repo: _Repo, fn: _Fn, path: str) -> str | None:
    """Why *path* cannot call *fn*, or None when it can."""
    if fn.kind == "function" and fn.parent:
        return "not_importable"  # a nested function: only its parent sees it
    if path == fn.file:
        return None
    if fn.kind != "function" or fn.private:
        return "not_importable"
    if repo.imports(path, fn.file):
        return None
    same_dir = PurePosixPath(path).parent == PurePosixPath(fn.file).parent
    if not same_dir:
        return "not_importable"
    # A new import from a sibling is safe only when the sibling cannot reach back.
    return "import_cycle" if repo.reaches(fn.file, path) else None


def _foreign_names(repo: _Repo, src: _Source, path: str) -> str | None:
    """Why a name *src* reads would mean something else in *path*, or None."""
    fn = src.fn
    if path == fn.file:
        return None
    mine, theirs = repo.bindings(fn.file), repo.bindings(path)
    if "*" in mine or "*" in theirs:
        return "free_names"
    for name in src.reads - {fn.name}:  # its own name: ``_name_taken`` judges that
        here, there = mine.get(name), theirs.get(name)
        if here or there:
            if here != there:
                return "free_names"
        elif name not in src.own and not is_builtin(name, repo.language):
            return "free_names"
    return None


def _name_taken(repo: _Repo, fn: _Fn, path: str, *, deleting: bool) -> bool:
    """Whether *path* already binds ``fn.name`` to something other than an
    import of *fn* (or, when *deleting*, the copy the plan removes)."""
    if path == fn.file:
        return False
    for binding in repo.bindings(path).get(fn.name, ()):
        if not (deleting and binding == ("def", path)) and not _imports_fn(binding, fn):
            return True
    return False


def _imports_fn(binding: tuple[str, ...], fn: _Fn) -> bool:
    """Whether a site's *binding* of ``fn.name`` is an import of *fn* itself."""
    if binding[0] != "from" or binding[2] != fn.name:
        return False
    module = binding[1]
    return module == fn.qualified.rpartition(".")[0] or module == _module_path(fn)


def _module_path(fn: _Fn) -> str:
    return module_of(fn.file, "python" if fn.file.endswith(".py") else "typescript")


def _twin_site(
    repo: _Repo, src: _Source, twin: _Fn, span: tuple[int, int], lines: Lines
) -> tuple[Site | None, str | None]:
    """*twin* is a whole copy of ``F``. Under the same name it is deleted for an
    import (a body calling ``F`` by that name would call itself); under another
    name its header stays and its body becomes one call to ``F``."""
    fn = src.fn
    if (fn.kind == "method" or twin.kind == "method") and not _same_class(fn, twin):
        return None, "receiver"
    if _decorated(lines, twin):
        return None, "decorated"
    theirs = _header(lines, twin, repo.language)
    if [_renamed(t, twin.name, fn.name) for t in theirs] != src.header:
        return None, "header_differs"
    if _name_taken(repo, fn, twin.file, deleting=fn.name == twin.name):
        return None, "name_taken"
    if fn.name == twin.name:
        refused = _undeletable(repo, twin, lines)
        if refused:
            return None, refused
        return _site_dict(twin.file, (twin.start, twin.end), "delete", None, twin), None
    if src.params is None:
        return None, "param_mismatch"
    call = _call(repo.language, fn, src.params, async_host=twin.is_async)
    if call is None:
        return None, "host_not_async"
    return _site_dict(twin.file, span, "replace_with_call", f"return {call}", twin), None


def _undeletable(repo: _Repo, twin: _Fn, lines: Lines) -> str | None:
    """Why the copy *twin* cannot simply go: something else may import it."""
    if twin.kind == "method":
        return "twin_exported"  # a duplicate method under one name cannot exist
    head = lines[twin.start - 1].lstrip() if twin.start <= len(lines) else ""
    if head.startswith("export") or not twin.private:
        return "twin_exported"
    if exported_later(lines, twin.name, repo.language):
        return "twin_exported"
    for source, _v in repo.graph.in_edges(twin.id):
        node = repo.graph.nodes[source]
        if (node.get("file_path") or source) != twin.file:
            return "twin_imported"
    return None


def _call_site(
    repo: _Repo, src: _Source, host: _Fn, span: tuple[int, int], lines: Lines
) -> tuple[Site | None, str | None]:
    """The lines are a block inside *host*: one call to ``F`` replaces them."""
    lang = repo.language
    fn = src.fn
    block = [t for _n, t in _code(lines, span[0], span[1], lang)]
    refused = _block_refusal(src, host, block, lang)
    if refused or src.params is None:
        return None, refused or "param_mismatch"
    if _name_taken(repo, fn, host.file, deleting=False):
        return None, "name_taken"
    after = [t for _n, t in _code(lines, span[1] + 1, host.end, lang)]
    elsewhere = [t for _n, t in _code(lines, host.start, span[0] - 1, lang)] + after
    if _assigned(block, lang) & read_names(elsewhere, lang):
        return None, "outputs_used_after"
    if (src.reads - src.own) & _host_names(lines, host, elsewhere, lang):
        return None, "free_names"  # the host's own variable, not the module's
    text, refused = _call_text(src, host, block, after, lang)
    if text is None:
        return None, refused
    return _site_dict(host.file, span, "replace_with_call", text, None), None


def _call_text(
    src: _Source, host: _Fn, block: list[str], after: list[str], language: str
) -> tuple[str | None, str | None]:
    """The statement replacing *block*: the call, returned when the block
    returns (only where that ends *host*)."""
    returns = any((_PY_RETURN if language == "python" else _JS_RETURN).search(t) for t in block)
    if returns and not all(_CLOSERS.match(t) for t in after):
        return None, "returns_mid_host"
    call = _call(language, src.fn, src.params or (None, []), async_host=host.is_async)
    if call is None:
        return None, "host_not_async"
    return (f"return {call}" if returns else call), None


def _block_refusal(src: _Source, host: _Fn, block: list[str], language: str) -> str | None:
    """Why a call to ``F`` cannot stand in for *block* wherever it sits."""
    fn = src.fn
    if fn.kind == "method" and not _same_class(fn, host):
        return "receiver"
    if host.name == fn.name:
        return "name_shadowed"  # the call would reach the host itself
    if any(_YIELD.search(t) for t in block):
        return "generator"
    if fn.kind != "method" and any(_RECEIVER.search(t) for t in block):
        return "receiver"
    if src.params is None or not set(src.params[1]) <= read_names(block, language):
        return "param_mismatch"
    return None


def _host_names(lines: Lines, host: _Fn, elsewhere: list[str], language: str) -> set[str]:
    """Names *host* binds itself, outside the block: its parameters and locals."""
    params = parameter_names(host.signature, language, host.kind)
    if params is not None:
        given = set(params[1])
    else:
        given = set(_IDENTS.findall(" ".join(_header(lines, host, language))))
    return given | _assigned(elsewhere, language)


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
    path: str, span: tuple[int, int], action: ReuseSiteAction, text: str | None, twin: _Fn | None
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


def _code(lines: Lines, first: int, last: int, language: str) -> Code:
    """``(line, text)`` for the code lines of 1-indexed *first*..*last*:
    whitespace collapsed, blank and whole-line comment lines dropped."""
    comments = ("#",) if language == "python" else ("//", "/*", "*")
    out = []
    for n in range(max(first, 1), min(last, len(lines)) + 1):
        text = " ".join(lines[n - 1].split())
        if text and not text.startswith(comments):
            out.append((n, text))
    return out


def _body(lines: Lines, fn: _Fn, language: str) -> Code:
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


def _decorated(lines: Lines, fn: _Fn) -> bool:
    """A decorator (``@x``) on the line above *fn*'s span or opening it."""
    above = lines[fn.start - 2].strip() if fn.start >= 2 else ""
    first = lines[fn.start - 1].strip() if fn.start <= len(lines) else ""
    return above.startswith("@") or first.startswith("@")


def _renamed(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\w$]){re.escape(old)}(?![\w$])", new, text, count=1)


def _match(lines: Lines, body: Code, occ: Occurrence, language: str, slack: int) -> tuple[int, int] | None:
    """Where *body* appears line for line near the occurrence, else None."""
    window = _code(lines, occ[1] - slack, occ[2] + slack, language)
    want = [t for _n, t in body]
    texts = [t for _n, t in window]
    for i in range(len(texts) - len(want) + 1):
        if texts[i : i + len(want)] == want:
            return window[i][0], window[i + len(want) - 1][0]
    return None


def _assigned(texts: list[str], language: str) -> set[str]:
    """Names the lines bind: assignments, loop, ``as`` and walrus targets,
    lambda / arrow / catch parameters, local imports, JS declarations.
    Attribute and item writes bind no name."""
    found: list[str] = []
    for t in texts:
        if language == "python":
            m = _PY_ASSIGN.match(t)
            found += _PY_BIND.findall(t) + _PY_LAMBDA.findall(t) + _WALRUS.findall(t)
            if imp := _PY_LOCAL_IMPORT.match(t):
                found += [i.split(" as ")[-1].split(".")[0] for i in imp.group(1).split(",")]
        else:
            m = _JS_ASSIGN.match(t)
            found += _JS_DECL.findall(t)
            found += [" ".join(g) for g in _JS_PARAMS.findall(t)]
        if m:
            found.append(m.group(1))
    return {n for group in found for n in _IDENTS.findall(group)}


__all__ = ["MIN_COVERAGE", "REUSE_SITE_ACTIONS", "Reuse", "ReuseSiteAction", "find_reuse"]
