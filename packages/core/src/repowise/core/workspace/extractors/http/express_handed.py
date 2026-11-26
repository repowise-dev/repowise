"""Express routers handed to a module's exported function.

A route module often exports a function that registers routes on whatever
router it is given, and the mounting file decides where that router serves::

    // routes/users.js
    module.exports = function (router) { router.get('/users', list) }
    // app.js
    const api = express.Router()
    require('./routes/users')(api)
    app.use('/api', api)                 // GET /users is served at /api/users

The call site is recorded under the callee module, its export and the argument
position (``express:routes/users:default(0)``), with the prefix the handed
router serves under and the router it hangs off as parent. A route on that
parameter inside the exported function takes the prefix. The parent may itself
be a parameter handed in from another module, so chains compose.

Never a guessed path: a function handed routers at two different prefixes, a
chain whose top is a parameter no readable call site hands a router, a chain
deeper than 16 or cycling, a parameter the function rebinds or a nested
function shadows, and a call in test code all leave the route's in-function
path. Ceilings, each keeping the in-function path: an arrow function with an
expression body, a destructured parameter, a function the mounting file
defines itself, and a bare or aliased module spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..strings import call_arguments
from .mounts import compose_prefix, declare_mount, declared_mount

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from ..base import ScanContext

_NAMESPACE = "express"
# Handed chains nest a few modules deep; a longer chain is a cycle.
_MAX_DEPTH = 16

# A function head whose body is a block: `function [name](params) {` or
# `(params) => {` / `param => {`, with an optional TypeScript return type.
# Read on the file with comments and strings blanked, so params hold no quotes.
_FN_HEAD_RE = re.compile(
    r"""\bfunction\b\s*\*?\s*(?P<fname>[\w$]*)\s*\((?P<fparams>[^()]*)\)\s*(?::[^{;=()]*)?\{"""
    r"""|(?:\((?P<aparams>[^()]*)\)|(?<![\w$.])(?P<aparam>[\w$]+))\s*(?::[^{;=()]*)?=>\s*\{"""
)
_PARAM_RE = re.compile(r"\s*(?:\.\.\.)?([A-Za-z_$][\w$]*)\s*(?:[?:=]|$)")
# What a head's leading text says it is exported as.
_DEFAULT_LEAD_RE = re.compile(r"(?:\bmodule\.exports\s*=|\bexport\s+default)\s*(?:async\s+)?$")
_NAMED_LEAD_RE = re.compile(r"\b(?:module\.)?exports\.([\w$]+)\s*=\s*(?:async\s+)?$")
_EXPORT_FN_LEAD_RE = re.compile(r"\bexport\s+(?:async\s+)?$")
_VAR_LEAD_RE = re.compile(r"\b(?:const|let|var)\s+([\w$]+)\s*(?::[^=;]*)?=\s*(?:async\s+)?$")
_ASYNC_TAIL_RE = re.compile(r"\basync$")
# A call of an imported function: `require('./x')(`, `require('./x').reg(`,
# `name(` or `mod.reg(`.
_CALL_RE = re.compile(
    r"""(?<![\w$.])(?:require\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)(?:\s*\.\s*(?P<member>[\w$]+))?"""
    r"""|(?P<name>[\w$]+)(?:\s*\.\s*(?P<attr>[\w$]+))?)\s*\("""
)
_BARE_NAME_RE = re.compile(r"[\w$]+")
# How far back a head's export lead is read.
_LEAD_SPAN = 120


def handed_key(module: str, exported: str, index: int) -> str:
    """The mount key of argument *index* of *module*'s export *exported*."""
    return f"{module}:{exported}({index})"


def _is_handed_key(key: str) -> bool:
    return key.endswith(")")


def _marker(module: str) -> str:
    return f"{_NAMESPACE}-handed:{module}"


def declare_handed(module: str) -> dict[str, str]:
    """The entry saying some call hands a router to a function of *module*."""
    return declare_mount(_marker(module), "")


def is_handed(mounts: Mapping[str, str], module: str) -> bool:
    """Whether any call site hands a router to a function of *module*."""
    return declared_mount(mounts, _marker(module))[0]


@dataclass(frozen=True)
class _Fn:
    exported: str
    params: tuple[str | None, ...]
    start: int
    end: int
    shadowed: frozenset[str]


class HandedFunctions:
    """The exported functions of one module, read once, for the routers their
    parameters are handed."""

    def __init__(
        self, ctx: ScanContext, module: str, exported_as: Callable[[str], str | None]
    ) -> None:
        self._ctx = ctx
        self._module = module
        self._exported_as = exported_as
        self._fns: list[_Fn] | None = None

    def key(self, var: str, offset: int) -> str | None:
        """The handed key of parameter *var* of the innermost exported function
        around *offset*, None when *var* is no such parameter there."""
        best: _Fn | None = None
        for fn in self._functions():
            if fn.start < offset < fn.end and (best is None or fn.start > best.start):
                best = fn
        if best is None or var not in best.params or var in best.shadowed:
            return None
        return handed_key(self._module, best.exported, best.params.index(var))

    def _functions(self) -> list[_Fn]:
        if self._fns is None:
            self._fns = list(self._read())
        return self._fns

    def _read(self) -> Iterator[_Fn]:
        code = self._ctx._mask(strings=True)
        if code is None:
            return
        heads = [
            (m, _params(m), m.end() - 1, _block_end(code, m.end() - 1))
            for m in _FN_HEAD_RE.finditer(code)
        ]
        for m, params, start, end in heads:
            if end < 0:
                continue
            exported = self._export_of(code, m)
            if exported is None:
                continue
            body = code[start:end]
            inner = {p for _m, ps, s, _e in heads if start < s < end for p in ps if p}
            shadowed = frozenset(p for p in params if p and (p in inner or _rebinds(body, p)))
            yield _Fn(exported, params, start, end, shadowed)

    def _export_of(self, code: str, m: re.Match[str]) -> str | None:
        """The name the function at *m* is exported under, None when it is not."""
        lead = code[max(0, m.start() - _LEAD_SPAN) : m.start()]
        if _DEFAULT_LEAD_RE.search(lead):
            return "default"
        named = _NAMED_LEAD_RE.search(lead)
        if named:
            return named.group(1)
        fname = m.group("fname")
        if fname and _EXPORT_FN_LEAD_RE.search(lead):
            return fname
        var = _VAR_LEAD_RE.search(lead)
        if var:
            return self._exported_as(var.group(1))
        # A declaration `function name(` starts a statement; anything else
        # (a callback argument, an expression) is not exported by its name.
        before = _ASYNC_TAIL_RE.sub("", lead.rstrip()).rstrip()
        if not fname or (before and before[-1] not in ";{}"):
            return None
        return self._exported_as(fname)


def _params(m: re.Match[str]) -> tuple[str | None, ...]:
    """A head's parameter names; None for one this cannot name (destructured)."""
    if m.group("aparam"):
        return (m.group("aparam"),)
    text = m.group("fparams") if m.group("fparams") is not None else m.group("aparams")
    if not text.strip():
        return ()
    out: list[str | None] = []
    for part in text.split(","):
        p = _PARAM_RE.match(part)
        out.append(p.group(1) if p else None)
    return tuple(out)


def _block_end(code: str, open_idx: int) -> int:
    """The ``}`` closing the block at *open_idx* in comment- and string-blanked code."""
    depth = 0
    for i in range(open_idx, len(code)):
        ch = code[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _rebinds(body: str, name: str) -> bool:
    """Whether *body* declares or assigns *name* itself (not a member of it)."""
    n = re.escape(name)
    return bool(re.search(rf"\b(?:const|let|var)\s+{n}(?![\w$])|(?<![\w$.]){n}\s*=(?![=>])", body))


def handed_calls(
    ctx: ScanContext,
    imports: Mapping[str, tuple[str, str]],
    resolve: Callable[[str], str | None],
) -> Iterator[tuple[str, str, int, str, int]]:
    """``(module, export, argument index, argument, offset)`` for each argument
    that is a bare name in a call of a function another module exports.

    *resolve* maps a spec to a repo module (None when it cannot). Calls in
    non-production code (tests, examples, tooling) hand nothing: a test
    registering routes on its own router says nothing about where they serve.
    """
    from repowise.core.code_origin import path_origin
    from repowise.core.workspace.contracts import _NOT_CONTRACT_ORIGINS

    # The collect pass holds no stored origin, so the path decides (the
    # code_origin fallback).
    if path_origin(ctx.rel_path) in _NOT_CONTRACT_ORIGINS:
        return
    for m in _CALL_RE.finditer(ctx.content):
        target = _callee(m, imports)
        if target is None or ctx.in_comment(m.start()):
            continue
        module = resolve(target[0])
        if module is None:
            continue
        args = call_arguments(ctx.content, m.end() - 1)
        for i, arg in enumerate(args or ()):
            if _BARE_NAME_RE.fullmatch(arg):
                yield module, target[1], i, arg, m.start()


def _callee(m: re.Match[str], imports: Mapping[str, tuple[str, str]]) -> tuple[str, str] | None:
    """``(spec, export)`` of the function a call names, None for a local one."""
    if m.group("spec"):
        return m.group("spec"), m.group("member") or "default"
    name, attr = m.group("name"), m.group("attr")
    if name not in imports:
        return None
    spec, exported = imports[name]
    if not attr:
        return spec, exported
    # A member of a whole module; a member of a named import is not followed.
    return (spec, attr) if exported == "default" else None


def served_prefix(mounts: Mapping[str, str], key: str, depth: int = 0) -> tuple[bool, str | None]:
    """``(declared, full prefix)`` of the Express mount *key*; the prefix is
    None when unreadable.

    :func:`.mounts.module_prefix` with one rule more: a parent that is a
    handed parameter no readable call site hands a router makes the chain
    unreadable, where an unmounted router module reads as served at the root.
    """
    declared, value = declared_mount(mounts, f"{_NAMESPACE}:{key}")
    if not declared:
        return False, ""
    has_parent, parent = declared_mount(mounts, f"{_NAMESPACE}-in:{key}")
    if value is None or (has_parent and parent is None) or depth > _MAX_DEPTH:
        return True, None
    if not parent:
        return True, value
    mounted, outer = served_prefix(mounts, parent, depth + 1)
    if outer is None or (not mounted and _is_handed_key(parent)):
        return True, None
    return True, compose_prefix(outer, value)
