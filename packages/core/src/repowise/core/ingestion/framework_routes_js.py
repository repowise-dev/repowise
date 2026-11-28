"""Pieces the Node route frameworks outside the Express call shape share.

Fastify's ``route({method, url})`` and hapi's ``server.route({method, path})``
declare a route as an object literal, and both mount a plugin from another
module with a prefix (``register(plugin, {prefix})``). This module reads those
object literals, the relative module a plugin reference names, and the
module keys a file answers to; each framework's own rules stay in its module.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from .framework_routes import match_paren, scan_code

#: Extensions a relative import may omit, longest first so `.d.ts` style
#: suffixes never shadow a shorter one.
_JS_EXTS = (".tsx", ".jsx", ".mjs", ".cjs", ".ts", ".js")

# A key opening an object entry: `method:`, `'method':`, `"url":`.
_KEY_RE = re.compile(r"""\s*(?:(?P<q>['"])(?P<qkey>[\w$]+)(?P=q)|(?P<key>[\w$]+))\s*:(?!:)""")
_STR_RE = re.compile(r"""\s*(?P<q>['"`])(?P<text>[^'"`]*)(?P=q)""")
_STR_LIST_RE = re.compile(r"""\s*\[(?P<items>(?:\s*['"`]\w+['"`]\s*,?)+)\s*\]""")
_LIST_ITEM_RE = re.compile(r"""['"`](\w+)['"`]""")

# `require('./x')`, `require('./x').routes`, `import('./x')`, or a name.
_REF_RE = re.compile(
    r"""\s*(?:(?:await\s+)?(?:require|import)\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)"""
    r"""(?:\s*\.\s*(?P<member>[\w$]+))?|(?P<name>[\w$]+)(?![\w$]*\s*[(.]))"""
)

_CALL_REF_RE = re.compile(r"\s*(?P<name>[\w$]+)\s*\(")

_IMPORT_RE = re.compile(
    r"""\bimport\s+(?:type\s+)?(?P<clause>[\w$\s,{}]+?)\s+from\s+['"](?P<spec>[^'"]+)['"]"""
)
_REQUIRE_RE = re.compile(
    r"""\b(?:const|let|var)\s+(?P<lhs>[\w$]+|\{[^}]*\})\s*=\s*require\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)"""
    r"""(?:\s*\.\s*(?P<member>[\w$]+))?"""
)
_AS_RE = re.compile(r"^(?P<name>[\w$]+)(?:\s+as\s+(?P<alias>[\w$]+)|\s*:\s*(?P<rename>[\w$]+))?$")


@dataclass
class JsObject:
    """One object literal: where it opens and closes, and its top-level keys."""

    open: int
    close: int
    #: key -> ``(start, end)`` of its value
    keys: dict[str, tuple[int, int]] = field(default_factory=dict)

    def expr(self, content: str, key: str) -> str | None:
        """The source text of *key*'s value, None when absent."""
        span = self.keys.get(key)
        return content[span[0] : span[1]].strip() if span is not None else None

    def string(self, content: str, key: str) -> str | None:
        """The string literal *key* holds, None when absent or not a plain literal."""
        span = self.keys.get(key)
        m = _STR_RE.match(content, span[0]) if span is not None else None
        if m is None or "${" in m.group("text"):
            return None
        return m.group("text")

    def strings(self, content: str, key: str) -> tuple[str, ...] | None:
        """A string or a list of strings *key* holds, None when unreadable."""
        one = self.string(content, key)
        if one is not None:
            return (one,)
        span = self.keys.get(key)
        m = _STR_LIST_RE.match(content, span[0]) if span is not None else None
        return tuple(_LIST_ITEM_RE.findall(m.group("items"))) if m else None


@dataclass
class _Open:
    obj: JsObject | None  # None for `(` and `[`
    key: str | None = None  # the key whose value is being read
    value: int = 0


def js_objects(content: str, start: int = 0, end: int | None = None) -> Iterator[JsObject]:
    """Every object literal opened in ``content[start:end]``, innermost first.

    One pass over the code (strings and comments skipped), with a stack of the
    open brackets, so the cost is linear in the span however deep it nests.
    """
    end = len(content) if end is None else end
    stack: list[_Open] = []
    for i, c, _depth in scan_code(content, start):
        if i >= end:
            break
        top = stack[-1] if stack else None
        if c in ",}" and top is not None and top.obj is not None and top.key is not None:
            top.obj.keys[top.key] = (top.value, i)
            top.key = None
        if c in "([":
            stack.append(_Open(None))
        elif c == "{":
            stack.append(_Open(JsObject(open=i, close=-1)))
        elif c in ")]}":
            if stack:
                closed = stack.pop().obj
                if closed is not None:
                    closed.close = i
                    yield closed
            continue
        top = stack[-1] if stack else None
        if c in "{," and top is not None and top.obj is not None:
            m = _KEY_RE.match(content, i + 1)
            if m is not None:
                top.key, top.value = m.group("key") or m.group("qkey"), m.end()


def js_object_at(content: str, brace: int) -> JsObject | None:
    """The object literal opened by the ``{`` at *brace*, None for another character."""
    if content[brace : brace + 1] != "{":
        return None
    close = match_paren(content, brace)
    if close < 0:
        return None
    last = None
    for obj in js_objects(content, brace, close + 1):
        last = obj
    return last if last is not None and last.open == brace else None


def js_object_named(content: str, name: str) -> JsObject | None:
    """The object literal ``const <name> = {...}`` binds in *content*."""
    m = re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*(?::[^=;\n]+)?=\s*\{{", content)
    return js_object_at(content, m.end() - 1) if m else None


def call_arg_spans(content: str, paren: int) -> list[tuple[int, int]] | None:
    """``(start, end)`` of each top-level argument of the call opened at *paren*."""
    close = match_paren(content, paren)
    if close < 0:
        return None
    spans: list[tuple[int, int]] = []
    nest, at = 0, paren + 1
    for i, c, _depth in scan_code(content, paren + 1):
        if i >= close:
            break
        if c in "([{":
            nest += 1
        elif c in ")]}":
            nest -= 1
        elif c == "," and nest == 0:
            spans.append((at, i))
            at = i + 1
    if content[at:close].strip():
        spans.append((at, close))
    return spans


def chain_receiver(content: str, dot: int) -> str | None:
    """The name a call chain ending at the ``.`` at *dot* starts from.

    In ``app.register(a).register(b)`` it gives ``app`` for the second call.
    Parentheses are matched backwards without reading strings, which is enough
    for the argument lists of a registration chain.
    """
    i = _back_over_space(content, dot - 1)
    for _ in range(64):
        if i < 0:
            return None
        if content[i] == ")":
            i = _back_over_space(content, _open_paren_before(content, i) - 1)
            continue
        m = _NAME_AT_END_RE.search(content, max(0, i - 128), i + 1)
        if m is None:
            return None
        before = _back_over_space(content, m.start() - 1)
        if before < 0 or content[before] != ".":
            return m.group()
        i = _back_over_space(content, before - 1)
    return None


_NAME_AT_END_RE = re.compile(r"[\w$]+$")


def _back_over_space(content: str, i: int) -> int:
    while i >= 0 and content[i].isspace():
        i -= 1
    return i


def _open_paren_before(content: str, close: int) -> int:
    """Index of the ``(`` the ``)`` at *close* closes, or -1."""
    depth = 0
    for i in range(close, -1, -1):
        depth += {")": 1, "(": -1}.get(content[i], 0)
        if depth == 0:
            return i
    return -1


def module_key(rel_path: str) -> str:
    """The module name *rel_path* is imported by: its path without the
    extension, and an ``index`` file's directory (``./routes`` imports
    ``routes/index.ts``)."""
    return _module_name(_strip_ext(rel_path))


def resolve_module(rel_path: str, spec: str) -> str | None:
    """The repo-relative module a relative *spec* in *rel_path* names, else None.

    A bare or aliased spec (``fastify``, ``@/routes``) is not resolved: its
    target depends on the bundler or tsconfig, which this does not read.
    """
    if not spec.startswith("."):
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel_path), spec))
    if joined.startswith(".."):
        return None
    return _module_name(_strip_ext(joined))


def _module_name(stem: str) -> str:
    if posixpath.basename(stem) == "index" and "/" in stem:
        return posixpath.dirname(stem)
    return stem


def _strip_ext(path: str) -> str:
    for ext in _JS_EXTS:
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def js_imports(content: str) -> dict[str, tuple[str, str]]:
    """``local name -> (spec, exported name)`` for ES imports and ``require``.

    The exported name is ``default`` for a default import or a whole-module
    ``require``.
    """
    out: dict[str, tuple[str, str]] = {}
    for m in _IMPORT_RE.finditer(content):
        clause, spec = m.group("clause").strip(), m.group("spec")
        head, _, rest = clause.partition("{")
        head = head.strip().rstrip(",").strip()
        if head and _AS_RE.match(head) and "*" not in head:
            out[head] = (spec, "default")
        for part in rest.rstrip("}").split(","):
            named = _AS_RE.match(part.strip().removeprefix("type ").strip())
            if named:
                out[named.group("alias") or named.group("name")] = (spec, named.group("name"))
    for m in _REQUIRE_RE.finditer(content):
        lhs, spec = m.group("lhs"), m.group("spec")
        if not lhs.startswith("{"):
            out[lhs] = (spec, m.group("member") or "default")
            continue
        for part in lhs.strip("{}").split(","):
            named = _AS_RE.match(part.strip())
            if named:
                out[named.group("rename") or named.group("name")] = (spec, named.group("name"))
    return out


def plugin_ref(
    content: str, start: int, imports: dict[str, tuple[str, str]], *, calls: bool = False
) -> tuple[str | None, str] | None:
    """``(spec, exported name)`` of the plugin reference at *start*.

    *spec* is None for a name declared in the same file. None when the
    expression is not a module reference this can follow (a member, or a call
    unless *calls* admits a factory the module exports, ``Routes(server)``).
    """
    m = _REF_RE.match(content, start)
    if m is None and calls:
        m = _CALL_REF_RE.match(content, start)
    if m is None:
        return None
    if m.groupdict().get("spec") is not None:
        return m.group("spec"), m.group("member") or "default"
    name = m.group("name")
    if name in {"async", "function", "await", "new"}:
        return None
    return imports.get(name, (None, name))
