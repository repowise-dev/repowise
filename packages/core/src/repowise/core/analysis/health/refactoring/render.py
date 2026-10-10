"""The helper an Extract Method plan asks for, written out in the file's language.

A plan names the lines to lift, their inputs and output, whether the helper is
a method and whether it awaits. An agent applying it still had to turn that
into a header and a call. This module writes both, as text the agent copies:
Repowise never applies an edit, so they are spec, not a patch.

``signature_text`` is the helper's header (``async def _load(self, path: Path)
-> Config:``, ``private loadConfig(path: string): Config {``, ``func (s *S)
load(path string) Config {``); ``call_site.new_text`` is the one statement
that replaces the span (``config = await self._load(path)``,
``const config = this.loadConfig(path);``). Neither is indented: the
statement takes the replaced lines' indentation.

Per language: the private convention (Python's leading underscore, TS
``private``, Java ``private``, a C++ ``static`` free function; Go's lower-case
first letter and Rust's missing ``pub`` already hold), the async form
(``async`` + ``await``, Rust ``.await``, C++ ``co_await``), the receiver a
method keeps, and how the call declares an output first declared in the span
(``const`` / ``let``, Go ``:=``, Rust ``let`` / ``let mut``, Java / C++ the
declared type, ``var`` / ``auto`` without one). A type the parse does not
name is left out where the language allows it (Python, TS / JS) and written
``<type>`` where it does not (Go, Java, Rust, C++); a missing name is
``<name>``. Both are placeholders the agent must replace.

Ceiling: one output at most, as the slicer never offers more
(``dataflow.slice._MAX_RETURNS``); C7's staged plans bring tuple returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

ParamMode = Literal["in", "inout"]
#: ``inout``: the helper takes the value and hands its new value back.
PARAM_MODES: tuple[str, ...] = get_args(ParamMode)

NAME_PLACEHOLDER = "<name>"
TYPE_PLACEHOLDER = "<type>"

_FAMILY: dict[str, str] = {
    "python": "python",
    "typescript": "ts",
    "tsx": "ts",
    "javascript": "js",
    "jsx": "js",
    "svelte": "js",
    "vue": "js",
    "go": "go",
    "java": "java",
    "rust": "rust",
    "cpp": "cpp",
    "c": "cpp",
}
#: Languages whose signatures need every type written.
_TYPED = frozenset({"go", "java", "rust", "cpp"})


@dataclass(frozen=True)
class Slot:
    """One parameter or output: its name and declared type (None: unknown)."""

    name: str
    type: str | None = None


@dataclass(frozen=True)
class HelperShape:
    """What the renderer needs to know about one helper.

    ``receiver`` is the name a method reaches its instance by (``self``,
    ``cls``, ``this``, a Go receiver) and ``receiver_decl`` the text a Go or
    Rust method repeats (``(s *S)``, ``&mut self``). ``out_declared``: the
    output is first declared in the span, so the call declares it;
    ``out_rebound``: it is assigned again after the span.
    """

    language: str
    name: str | None
    kind: str | None
    is_async: bool
    params: tuple[Slot, ...] = ()
    returns: tuple[Slot, ...] = ()
    receiver: str | None = None
    receiver_decl: str | None = None
    out_declared: bool = False
    out_rebound: bool = False


def helper_name(language: str | None, name: str | None) -> str | None:
    """*name* in the language's private form: Python's leading underscore.
    Other languages mark privacy on the header, or already have it in the
    name's case (Go)."""
    if name and _FAMILY.get(language or "") == "python" and not name.startswith("_"):
        return "_" + name
    return name


def symbol_params(params: tuple[Slot, ...], returns: tuple[Slot, ...]) -> list[dict]:
    """``new_symbol.params``: name, type, and ``inout`` for a value the helper
    also returns."""
    outs = {r.name for r in returns}
    return [
        {"name": p.name, "type": p.type, "mode": "inout" if p.name in outs else "in"}
        for p in params
    ]


def render(shape: HelperShape) -> tuple[str, str] | None:
    """``(signature_text, call_text)``, or None when the helper's form is not
    known: a language without a renderer, or ``kind`` None, where the span may
    reach its object by a name a function would not have, so a function
    header would be a confident wrong spec."""
    family = _FAMILY.get(shape.language)
    if family is None or shape.kind not in ("method", "function"):
        return None
    sig, call = _RENDERERS[family]
    return sig(shape), call(shape)


# -- shared pieces -----------------------------------------------------------


def _name(shape: HelperShape) -> str:
    return shape.name or NAME_PLACEHOLDER


def _type(slot: Slot, family: str) -> str | None:
    return slot.type or (TYPE_PLACEHOLDER if family in _TYPED else None)


def _args(shape: HelperShape) -> str:
    return ", ".join(p.name for p in shape.params)


def _out(shape: HelperShape) -> Slot | None:
    return shape.returns[0] if shape.returns else None


def _method(shape: HelperShape) -> bool:
    return shape.kind == "method"


# -- Python ------------------------------------------------------------------


def _py_sig(s: HelperShape) -> str:
    params = [f"{p.name}: {p.type}" if p.type else p.name for p in s.params]
    if _method(s) and s.receiver:
        params.insert(0, s.receiver)
    out = _out(s)
    ret = f" -> {out.type}" if out and out.type else ""
    head = f"{'async ' if s.is_async else ''}def {_name(s)}({', '.join(params)}){ret}:"
    return ("@classmethod\n" + head) if _method(s) and s.receiver == "cls" else head


def _py_call(s: HelperShape) -> str:
    target = f"{s.receiver}." if _method(s) and s.receiver else ""
    expr = f"{'await ' if s.is_async else ''}{target}{_name(s)}({_args(s)})"
    out = _out(s)
    return f"{out.name} = {expr}" if out else expr


# -- TypeScript / JavaScript -------------------------------------------------


def _ts_sig(s: HelperShape, typed: bool) -> str:
    params = ", ".join(f"{p.name}: {p.type}" if p.type else p.name for p in s.params)
    out = _out(s)
    ret = ""
    if out and out.type:
        ret = f": Promise<{out.type}>" if s.is_async else f": {out.type}"
    prefix = "async " if s.is_async else ""
    if _method(s):
        return f"{'private ' if typed else ''}{prefix}{_name(s)}({params}){ret} {{"
    return f"{prefix}function {_name(s)}({params}){ret} {{"


def _ts_call(s: HelperShape) -> str:
    target = "this." if _method(s) else ""
    expr = f"{'await ' if s.is_async else ''}{target}{_name(s)}({_args(s)})"
    out = _out(s)
    if out is None:
        return expr + ";"
    keyword = ("let " if s.out_rebound else "const ") if s.out_declared else ""
    return f"{keyword}{out.name} = {expr};"


# -- Go ----------------------------------------------------------------------


def _go_sig(s: HelperShape) -> str:
    params = ", ".join(f"{p.name} {_type(p, 'go')}" for p in s.params)
    out = _out(s)
    ret = f" {_type(out, 'go')}" if out else ""
    recv = f"{s.receiver_decl} " if _method(s) and s.receiver_decl else ""
    return f"func {recv}{_name(s)}({params}){ret} {{"


def _go_call(s: HelperShape) -> str:
    target = f"{s.receiver}." if _method(s) and s.receiver else ""
    expr = f"{target}{_name(s)}({_args(s)})"
    out = _out(s)
    if out is None:
        return expr
    return f"{out.name} {':=' if s.out_declared else '='} {expr}"


# -- Java --------------------------------------------------------------------


def _java_sig(s: HelperShape) -> str:
    params = ", ".join(f"{_type(p, 'java')} {p.name}" for p in s.params)
    out = _out(s)
    ret = _type(out, "java") if out else "void"
    static = "" if _method(s) else "static "
    return f"private {static}{ret} {_name(s)}({params}) {{"


def _java_call(s: HelperShape) -> str:
    return _declared_call(s, f"{_name(s)}({_args(s)})", "var")


def _declared_call(s: HelperShape, expr: str, inferred: str) -> str:
    """``T x = expr;`` for an output the span declared (*inferred* when its
    type is unknown), ``x = expr;`` for one declared before it."""
    out = _out(s)
    if out is None:
        return expr + ";"
    if s.out_declared:
        return f"{out.type or inferred} {out.name} = {expr};"
    return f"{out.name} = {expr};"


# -- Rust --------------------------------------------------------------------


def _rust_self(s: HelperShape) -> str:
    """The receiver the helper borrows: the host's own when it borrows, a
    borrow when the host takes ``self`` by value (moving it into the helper
    would end the host's use of it)."""
    decl = (s.receiver_decl or "&self").strip()
    if decl.startswith("&"):
        return decl
    return "&mut self" if decl.startswith("mut") else "&self"


def _rust_sig(s: HelperShape) -> str:
    params = [f"{p.name}: {_type(p, 'rust')}" for p in s.params]
    if _method(s):
        params.insert(0, _rust_self(s))
    out = _out(s)
    ret = f" -> {_type(out, 'rust')}" if out else ""
    return f"{'async ' if s.is_async else ''}fn {_name(s)}({', '.join(params)}){ret} {{"


def _rust_call(s: HelperShape) -> str:
    target = "self." if _method(s) else ""
    expr = f"{target}{_name(s)}({_args(s)}){'.await' if s.is_async else ''}"
    out = _out(s)
    if out is None:
        return expr + ";"
    if s.out_declared:
        return f"let {'mut ' if s.out_rebound else ''}{out.name} = {expr};"
    return f"{out.name} = {expr};"


# -- C / C++ -----------------------------------------------------------------


def _cpp_sig(s: HelperShape) -> str:
    params = ", ".join(f"{_type(p, 'cpp')} {p.name}" for p in s.params)
    out = _out(s)
    ret = _type(out, "cpp") if out else "void"
    static = "" if _method(s) else "static "
    return f"{static}{ret} {_name(s)}({params}) {{"


def _cpp_call(s: HelperShape) -> str:
    expr = f"{'co_await ' if s.is_async else ''}{_name(s)}({_args(s)})"
    return _declared_call(s, expr, "auto")


_RENDERERS = {
    "python": (_py_sig, _py_call),
    "ts": (lambda s: _ts_sig(s, True), _ts_call),
    "js": (lambda s: _ts_sig(s, False), _ts_call),
    "go": (_go_sig, _go_call),
    "java": (_java_sig, _java_call),
    "rust": (_rust_sig, _rust_call),
    "cpp": (_cpp_sig, _cpp_call),
}


#: What the renderer adds to a stored plan, served on plan detail only.
_DETAIL_SYMBOL_KEYS = ("params", "returns", "signature_text")


def list_plan(plan: dict) -> dict:
    """*plan* without the rendered texts and typed slots, for a list row: a
    list serves every plan, and an agent reads the texts on one plan's
    detail."""
    symbol = plan.get("new_symbol")
    if "call_site" not in plan and not (
        isinstance(symbol, dict) and any(k in symbol for k in _DETAIL_SYMBOL_KEYS)
    ):
        return plan
    out = {k: v for k, v in plan.items() if k != "call_site"}
    if isinstance(symbol, dict):
        out["new_symbol"] = {k: v for k, v in symbol.items() if k not in _DETAIL_SYMBOL_KEYS}
    return out


def brief(name: str | None, params: list[str], returns: list[str], *, is_async: bool) -> str:
    """The helper in one language-neutral phrase for a step sentence:
    ``async _load(path) -> config, awaited at the call site``. A long
    parameter list is cut with its count; a missing name is ``<name>``."""
    shown = ", ".join(params[:4]) + (f", +{len(params) - 4} more" if len(params) > 4 else "")
    out = f"{'async ' if is_async else ''}{name or NAME_PLACEHOLDER}({shown})"
    out += f" -> {', '.join(returns[:3])}" if returns else ""
    return out + (", awaited at the call site" if is_async else "")


__all__ = [
    "NAME_PLACEHOLDER",
    "PARAM_MODES",
    "TYPE_PLACEHOLDER",
    "HelperShape",
    "ParamMode",
    "Slot",
    "brief",
    "helper_name",
    "list_plan",
    "render",
    "symbol_params",
]
