"""Which names a function's text reads, and what each name is bound to in a file.

A call to an existing function ``F`` stands in for a copy of its body only when
every name the copy reads means the same thing at the copy's site as in ``F``'s
module: ``logger`` imported from the same module, ``CONFIG`` the same object.
This module reads the names off the text and each file's module-level bindings
off its top-level lines, so :mod:`.reuse` can compare them.

Deliberately narrow, Python and TypeScript / JavaScript only. A binding it does
not recognise (one made inside a top-level ``if`` or ``try``, a ``require``) is
no binding, and a name with no binding that is not a local or a builtin is
"unresolved", which the caller refuses on. Missing a binding costs a plan,
never a wrong one.
"""

from __future__ import annotations

import builtins
import keyword
import posixpath
import re

Binding = tuple[str, ...]

_PY_BUILTINS = frozenset(n for n in dir(builtins) if not n.startswith("__"))
_PY_WORDS = frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)
_JS_WORDS = frozenset(
    (
        "abstract", "any", "as", "async", "await", "bigint", "boolean", "break", "case",
        "catch", "class", "const", "continue", "debugger", "declare", "default",
        "delete", "do", "else", "enum", "export", "extends", "false", "finally", "for",
        "from", "function", "get", "if", "implements", "import", "in", "infer",
        "instanceof", "interface", "is", "keyof", "let", "new", "never", "null",
        "number", "object", "of", "package", "private", "protected", "public",
        "readonly", "return", "satisfies", "set", "static", "string", "super", "switch",
        "symbol", "this", "throw", "true", "try", "type", "typeof", "undefined",
        "unique", "unknown", "var", "void", "while", "with", "yield",
    )
)
_JS_GLOBALS = frozenset(
    (
        "Array", "ArrayBuffer", "Boolean", "console", "Date", "decodeURIComponent",
        "encodeURIComponent", "Error", "Infinity", "Intl", "isFinite", "isNaN", "JSON",
        "Map", "Math", "NaN", "Number", "Object", "parseFloat", "parseInt", "Partial",
        "Pick", "Omit", "Promise", "Proxy", "Readonly", "Record", "Reflect", "RegExp",
        "Required", "ReturnType", "Set", "String", "Symbol", "TypeError", "URL",
        "URLSearchParams", "WeakMap", "WeakSet",
    )
)
_MAX_CONTINUATION = 60
_JS_EXPORT_STATEMENT = re.compile(
    r"^(export\s*\{|export\s+default\s+[\w$]+\s*;?$|module\.exports|exports\.)"
)
_JS_EXTS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs")

# Names read from the text: an identifier not after a member dot (spread
# ``...x`` still reads ``x``) and not inside a number.
_IDENT = re.compile(r"(?<![\w$])(?<!(?<!\.\.)\.)[A-Za-z_$][\w$]*")
_PY_STRING = re.compile(r"""([rRbBuUfF]{0,2})("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
_JS_STRING = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)""")
_BRACED = re.compile(r"\{([^{}]*)\}")
_PY_KWARG = re.compile(r"[(,]\s*([A-Za-z_]\w*)\s*=(?!=)")
# An object key: after ``{`` or ``,``, or opening a line (a key on its own line).
_JS_KEY = re.compile(r"(?:^|[{,])\s*([A-Za-z_$][\w$]*)\s*:(?!:)")
# A JSX intrinsic element (``<div``, ``</span>``) names no binding.
_JSX_TAG = re.compile(r"</?([a-z][\w-]*)")
_JSX_ATTR = re.compile(r"\s([A-Za-z_$][\w$-]*)=[\"'{]")

_PY_FROM = re.compile(r"^from\s+(\.*)([\w.]*)\s+import\s+(.+)$")
_PY_IMPORT = re.compile(r"^import\s+(.+)$")
_PY_DEF = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)")
_PY_TOP_ASSIGN = re.compile(r"^([\w\s,]+?)\s*(?::[^=]+)?=(?!=)")
_JS_IMPORT = re.compile(r"""^import\s+(?:type\s+)?(.+?)\s+from\s+['"]([^'"]+)['"]""")
_JS_DECL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
    r"(?:function\*?|class|const|let|var|type|interface|enum)\s+([\[{][^=]*|[A-Za-z_$][\w$]*)"
)


def read_names(texts: list[str], language: str) -> set[str]:
    """Names *texts* read: string contents, comments, attribute names, keyword
    arguments, object keys and JSX attribute names left out; a formatted
    string's or template's expressions kept."""
    out: set[str] = set()
    for text in texts:
        bare = _strip_strings(text, language)
        bare = re.sub(r"#.*$" if language == "python" else r"//.*$", "", bare)
        skip = set(_PY_KWARG.findall(bare)) if language == "python" else set(
            _JS_KEY.findall(bare) + _JSX_ATTR.findall(" " + bare) + _JSX_TAG.findall(bare)
        )
        out |= {n for n in _IDENT.findall(bare) if n not in skip}
    return out - (_PY_WORDS if language == "python" else _JS_WORDS)


def is_builtin(name: str, language: str) -> bool:
    return name in (_PY_BUILTINS if language == "python" else _JS_GLOBALS)


def _strip_strings(text: str, language: str) -> str:
    if language == "python":

        def keep(m: re.Match[str]) -> str:
            if "f" not in m.group(1).lower():
                return '""'
            return " ".join(_expr(e) for e in _BRACED.findall(m.group(2)))

        return _PY_STRING.sub(keep, text)

    def keep_js(m: re.Match[str]) -> str:
        body = m.group(1)
        if not body.startswith("`"):
            return '""'
        return " ".join(_expr(e) for e in re.findall(r"\$\{([^{}]*)\}", body))

    return _JS_STRING.sub(keep_js, text)


def _expr(field: str) -> str:
    """A format field's expression: its conversion and format spec dropped."""
    return re.split(r"![rsa]|:(?![=])", field, maxsplit=1)[0]


def module_bindings(lines: list[str], path: str, language: str) -> dict[str, set[Binding]]:
    """Each name *path* binds at module level, mapped to what it is bound to:
    ``("def", path)`` for a definition here, ``("from", module, name)`` /
    ``("module", module)`` for an import (relative modules resolved against
    *path*). ``"*"`` maps to ``{("star",)}`` when the file star-imports."""
    out: dict[str, set[Binding]] = {}
    scan = _py_bindings if language == "python" else _js_bindings
    for statement in _top_level_statements(lines):
        for name, binding in scan(statement, path):
            out.setdefault(name, set()).add(binding)
    return out


def _top_level_statements(lines: list[str]) -> list[str]:
    """Unindented lines, a bracketed continuation joined onto its first line.
    A continuation that runs past ``_MAX_CONTINUATION`` lines (a bracket in a
    string threw the count off) is dropped: no binding is read from it."""
    out: list[str] = []
    parts: list[str] = []
    depth = 0
    for raw in lines:
        if depth > 0:
            parts.append(raw.strip())
        elif raw[:1].strip() and not raw.startswith(("#", "//", "/*", "*")):
            parts = [raw.strip()]
        else:
            continue
        depth = max(0, depth + sum(raw.count(o) - raw.count(c) for o, c in ("()", "{}", "[]")))
        if depth == 0:
            out.append(" ".join(parts))
        elif len(parts) > _MAX_CONTINUATION:
            depth = 0
    return out


def _py_bindings(statement: str, path: str) -> list[tuple[str, Binding]]:
    if m := _PY_FROM.match(statement):
        module = _py_module(m.group(1), m.group(2), path)
        names = m.group(3).strip("() ").split(",")
        out = []
        for item in (n.strip() for n in names if n.strip()):
            if item == "*":
                out.append(("*", ("star",)))
                continue
            original, _, alias = item.partition(" as ")
            out.append((alias.strip() or original.strip(), ("from", module, original.strip())))
        return out
    if m := _PY_IMPORT.match(statement):
        out = []
        for item in (n.strip() for n in m.group(1).split(",")):
            original, _, alias = item.partition(" as ")
            name = alias.strip() or original.split(".")[0].strip()
            out.append((name, ("module", original.strip() if alias else name)))
        return out
    if m := _PY_DEF.match(statement):
        return [(m.group(1), ("def", path))]
    if m := _PY_TOP_ASSIGN.match(statement):
        return [(n, ("def", path)) for n in re.findall(r"\w+", m.group(1))]
    return []


def _py_module(dots: str, module: str, path: str) -> str:
    if not dots:
        return module
    parts = posixpath.dirname(path).split("/")
    up = len(dots) - 1
    base = parts[: len(parts) - up] if up else parts
    return "/".join([*base, *module.split(".")]) if module else "/".join(base)


def _js_bindings(statement: str, path: str) -> list[tuple[str, Binding]]:
    if m := _JS_IMPORT.match(statement):
        module = _js_module(m.group(2), path)
        return _js_import_clause(m.group(1), module)
    if m := _JS_DECL.match(statement):
        target = m.group(1)
        names = re.findall(r"[A-Za-z_$][\w$]*", target) if target[0] in "[{" else [target]
        return [(n, ("def", path)) for n in names]
    return []


def _js_import_clause(clause: str, module: str) -> list[tuple[str, Binding]]:
    out: list[tuple[str, Binding]] = []
    braced = re.search(r"\{([^}]*)\}", clause)
    if braced:
        for item in (i.strip() for i in braced.group(1).split(",")):
            item = re.sub(r"^type\s+", "", item)
            if not item:
                continue
            original, _, alias = item.partition(" as ")
            out.append((alias.strip() or original.strip(), ("from", module, original.strip())))
        clause = clause[: braced.start()] + clause[braced.end() :]
    if m := re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause):
        out.append((m.group(1), ("module", module)))
        clause = clause[: m.start()] + clause[m.end() :]
    default = clause.strip(" ,")
    if re.fullmatch(r"[A-Za-z_$][\w$]*", default):
        out.append((default, ("from", module, "default")))
    return out


def _js_module(spec: str, path: str) -> str:
    if not spec.startswith("."):
        return spec
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    for ext in _JS_EXTS:
        if resolved.endswith(ext):
            resolved = resolved[: -len(ext)]
            break
    return resolved.removesuffix("/index")


def exported_later(lines: list[str], name: str, language: str) -> bool:
    """Whether a statement apart from *name*'s definition exports it: an
    ``export { name }`` / ``export default name`` / ``module.exports`` line, or
    Python's ``__all__``."""
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    quoted = re.compile(rf"['\"]{re.escape(name)}['\"]")
    for statement in _top_level_statements(lines):
        if language == "python":
            if statement.startswith("__all__") and quoted.search(statement):
                return True
        elif _JS_EXPORT_STATEMENT.match(statement) and word.search(statement):
            return True
    return False


def module_of(path: str, language: str) -> str:
    """*path* as the module string a relative import of it resolves to."""
    if language == "python":
        return path.removesuffix(".py").removesuffix("/__init__")
    return _js_module("./" + posixpath.basename(path), path)


__all__ = ["exported_later", "is_builtin", "module_bindings", "module_of", "read_names"]
