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
statement takes the replaced lines' indentation. ``notes`` say what the texts
cannot (a Rust value that may move, a coroutine's return type).

Per language: the private convention (Python's leading underscore, TS
``private``, Java ``private``, a C++ ``static`` free function; Go's lower-case
first letter and Rust's missing ``pub`` already hold), the async form
(``async`` + ``await``, Rust ``.await``, C++ ``co_await``), the receiver a
method keeps (a C++ ``const`` member stays ``const``), and how the call
declares an output first declared in the span (``const`` / ``let``, Go
``:=`` unless the name was written before, Rust ``let`` / ``let mut``, Java /
C++ the declared type, ``var`` / ``auto`` without one). A Rust value the host
still reads after the call is borrowed, not moved. A type the parse does not
name is left out where the language allows it (Python, TS / JS) and written
``<type>`` where it does not (Go, Java, Rust, C / C++); a missing name is
``<name>``. Both are placeholders the agent must replace.

Ceilings: one output at most, except Python, where a staged plan's helper
may hand back a tuple (``a, b = _stage(...)``). A staged plan's parameter
object is written by :func:`render_context`. A C++ method defined out of line
(``A::f``) has an unknown receiver, so it gets no texts and the header is
never qualified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple, get_args

from .naming import split_words

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
#: Rust types that copy rather than move when passed by value.
#: Outputs one call of a staged plan's helper can bind, per language with a
#: staged renderer: a Python tuple, one TS / JS value.
_STAGED_OUTPUTS: dict[str, int] = {"python": 3, "ts": 1, "js": 1}
_RUST_COPY = frozenset(
    {
        *(f"{s}{n}" for s in "iu" for n in ("8", "16", "32", "64", "128", "size")),
        "f32",
        "f64",
        "bool",
        "char",
    }
)


@dataclass(frozen=True)
class Slot:
    """One parameter or output: its name, declared type (None: unknown), and
    whether the host reads it after the span (a Rust move would end that)."""

    name: str
    type: str | None = None
    read_after: bool = False


@dataclass(frozen=True)
class HelperShape:
    """What the renderer needs to know about one helper.

    ``receiver`` is the name a method reaches its instance by (``self``,
    ``cls``, ``this``, a Go receiver) and ``receiver_decl`` the text a method
    repeats (Go ``(s *S)``, Rust ``&mut self``, C++ trailing ``const``).
    ``async_host`` False: the host is not declared async, so nothing there can
    await the helper. ``out_declared``: the output is first declared in the
    span, so the call declares it; ``out_written_before``: it was written
    before the span (Go's ``:=`` would then declare nothing new);
    ``out_rebound``: it is assigned again after the span. ``typed_host``: the
    host declares its return type, so a helper with no output says so too.
    """

    language: str
    name: str | None
    kind: str | None
    is_async: bool
    params: tuple[Slot, ...] = ()
    returns: tuple[Slot, ...] = ()
    receiver: str | None = None
    receiver_decl: str | None = None
    async_host: bool = True
    out_declared: bool = False
    out_written_before: bool = False
    out_rebound: bool = False
    typed_host: bool = False


class Rendered(NamedTuple):
    signature: str
    call: str | None
    notes: tuple[str, ...]
    #: The helper's last line handing its outputs back, None without outputs.
    returns: str | None = None


def private_name(language: str | None, name: str | None) -> str | None:
    """*name* in the language's private form: Python's leading underscore.
    Other languages mark privacy on the header, or already have it in the
    name's case (Go)."""
    if name and _FAMILY.get(language or "") == "python" and not name.startswith("_"):
        return "_" + name
    return name


def staged_outputs(language: str | None) -> int | None:
    """How many outputs a staged plan's helper may return in *language*, None
    where no staged plan is rendered."""
    return _STAGED_OUTPUTS.get(_FAMILY.get(language or "", ""))


def symbol_params(params: tuple[Slot, ...], returns: tuple[Slot, ...]) -> list[dict]:
    """``new_symbol.params``: name, type, and ``inout`` for a value the helper
    also returns."""
    outs = {r.name for r in returns}
    return [
        {"name": p.name, "type": p.type, "mode": "inout" if p.name in outs else "in"}
        for p in params
    ]


def render(shape: HelperShape) -> Rendered | None:
    """The header, the call (None when the host cannot await the helper) and
    notes, or None when the helper's form is not known: a language without a
    renderer, or ``kind`` None, where the span may reach its object by a name
    a function would not have, so a function header would be a confident
    wrong spec."""
    family = _FAMILY.get(shape.language)
    if family is None or shape.kind not in ("method", "function"):
        return None
    sig, call = _RENDERERS[family]
    notes = list(_notes(shape, family))
    if shape.is_async and not shape.async_host:
        notes.append(
            "The function holding these lines is not async, so the call cannot await "
            "the helper where it stands."
        )
        return Rendered(sig(shape), None, tuple(notes), _return_line(shape, family))
    return Rendered(sig(shape), call(shape), tuple(notes), _return_line(shape, family))


def _return_line(s: HelperShape, family: str) -> str | None:
    """``return a, b`` (Python), ``return a;`` (C family), a Rust tail ``a``."""
    if not s.returns:
        return None
    names = ", ".join(r.name for r in s.returns)
    if family == "rust":
        return names
    return f"return {names}" if family in ("python", "go") else f"return {names};"


def _undefined_arm_notes(s: HelperShape) -> list[str]:
    """A TS / JS output the author declared ``T | undefined`` that the helper
    always writes (it is not passed in): the declared type is kept, and the
    note says the narrower return type is safe."""
    taken = {p.name for p in s.params}
    out = []
    for r in s.returns:
        arms = [a.strip() for a in (r.type or "").split("|")]
        kept = [a for a in arms if a != "undefined"]
        if r.name not in taken and kept and len(kept) < len(arms):
            out.append(
                f"{r.name} is declared `{r.type}`, but every path through these lines "
                f"writes it, so the helper may return `{' | '.join(kept)}`."
            )
    return out


def _notes(s: HelperShape, family: str) -> list[str]:
    out = []
    if family in ("ts", "js"):
        out.extend(_undefined_arm_notes(s))
    if family == "ts":
        untyped = [p.name for p in s.params if not p.type]
        if untyped:
            out.append(
                f"Write the types of {', '.join(untyped)}: the code declares none for them."
            )
    if family == "rust":
        unknown = [p.name for p in _rust_unsure(s)]
        if unknown:
            out.append(
                f"Pass {', '.join(unknown)} by reference (&) unless the type is Copy: "
                "the caller still reads it after the call."
            )
    if family == "cpp" and s.is_async:
        out.append("Give the helper the coroutine return type the caller co_awaits.")
    return out


# -- shared pieces -----------------------------------------------------------


def _name(shape: HelperShape) -> str:
    return shape.name or NAME_PLACEHOLDER


def _type(slot: Slot, family: str) -> str | None:
    return slot.type or (TYPE_PLACEHOLDER if family in _TYPED else None)


def _args(shape: HelperShape) -> str:
    return ", ".join(p.name for p in shape.params)


def _single_out(shape: HelperShape) -> Slot | None:
    """The one output every renderer but Python's binds (see the ceilings)."""
    return shape.returns[0] if shape.returns else None


def _method(shape: HelperShape) -> bool:
    return shape.kind == "method"


# -- Python ------------------------------------------------------------------


def _py_sig(s: HelperShape) -> str:
    params = [f"{p.name}: {p.type}" if p.type else p.name for p in s.params]
    if _method(s) and s.receiver:
        params.insert(0, s.receiver)
    types = [r.type for r in s.returns]
    ret = " -> None" if not types and s.typed_host else ""
    if types and all(types):
        ret = f" -> {types[0]}" if len(types) == 1 else f" -> tuple[{', '.join(map(str, types))}]"
    head = f"{'async ' if s.is_async else ''}def {_name(s)}({', '.join(params)}){ret}:"
    return ("@classmethod\n" + head) if _method(s) and s.receiver == "cls" else head


def _py_call(s: HelperShape) -> str:
    target = f"{s.receiver}." if _method(s) and s.receiver else ""
    expr = f"{'await ' if s.is_async else ''}{target}{_name(s)}({_args(s)})"
    return f"{', '.join(r.name for r in s.returns)} = {expr}" if s.returns else expr


# -- TypeScript / JavaScript -------------------------------------------------


def _ts_sig(s: HelperShape, typed: bool) -> str:
    params = ", ".join(f"{p.name}: {p.type}" if p.type else p.name for p in s.params)
    out = _single_out(s)
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
    out = _single_out(s)
    if out is None:
        return expr + ";"
    keyword = ("let " if s.out_rebound else "const ") if s.out_declared else ""
    return f"{keyword}{out.name} = {expr};"


# -- Go ----------------------------------------------------------------------


def _go_sig(s: HelperShape) -> str:
    params = ", ".join(f"{p.name} {_type(p, 'go')}" for p in s.params)
    out = _single_out(s)
    ret = f" {_type(out, 'go')}" if out else ""
    recv = f"{s.receiver_decl} " if _method(s) and s.receiver_decl else ""
    return f"func {recv}{_name(s)}({params}){ret} {{"


def _go_call(s: HelperShape) -> str:
    target = f"{s.receiver}." if _method(s) and s.receiver else ""
    expr = f"{target}{_name(s)}({_args(s)})"
    out = _single_out(s)
    if out is None:
        return expr
    # ``x, err :=`` in the span may only redeclare ``x``; alone it must assign.
    return f"{out.name} {'=' if s.out_written_before else ':='} {expr}"


# -- Java / C / C++ ------------------------------------------------------------


def _type_first(s: HelperShape, family: str) -> str:
    """``T a, U b``: Java and C / C++ write the type before the name."""
    return ", ".join(f"{_type(p, family)} {p.name}" for p in s.params)


def _java_sig(s: HelperShape) -> str:
    out = _single_out(s)
    ret = _type(out, "java") if out else "void"
    static = "" if _method(s) else "static "
    return f"private {static}{ret} {_name(s)}({_type_first(s, 'java')}) {{"


def _java_call(s: HelperShape) -> str:
    return _declared_call(s, f"{_name(s)}({_args(s)})", "var")


def _declared_call(s: HelperShape, expr: str, inferred: str) -> str:
    """``T x = expr;`` for an output the span declared (*inferred* when its
    type is unknown), ``x = expr;`` for one declared before it."""
    out = _single_out(s)
    if out is None:
        return expr + ";"
    if s.out_declared:
        return f"{out.type or inferred} {out.name} = {expr};"
    return f"{out.name} = {expr};"


def _cpp_sig(s: HelperShape) -> str:
    out = _single_out(s)
    if s.is_async:
        ret = TYPE_PLACEHOLDER  # the coroutine's own type (a note says so)
    elif out:
        # A helper returns its value, never a reference to its own local.
        ret = (out.type or TYPE_PLACEHOLDER).rstrip("&").rstrip()
    else:
        ret = "void"
    static = "" if _method(s) else "static "
    const = " const" if _method(s) and s.receiver_decl == "const" else ""
    return f"{static}{ret} {_name(s)}({_type_first(s, 'cpp')}){const} {{"


def _cpp_call(s: HelperShape) -> str:
    expr = f"{'co_await ' if s.is_async else ''}{_name(s)}({_args(s)})"
    # ``auto`` declares nothing before C23.
    return _declared_call(s, expr, TYPE_PLACEHOLDER if s.language == "c" else "auto")


# -- Rust --------------------------------------------------------------------


def _rust_borrowed(s: HelperShape, p: Slot) -> bool:
    """A known non-Copy value the host still reads after the call: passing it
    by value would move it into the helper."""
    outs = {r.name for r in s.returns}
    return bool(
        p.read_after
        and p.type
        and p.name not in outs
        and not p.type.startswith("&")
        and p.type not in _RUST_COPY
    )


def _rust_unsure(s: HelperShape) -> list[Slot]:
    """Values read after the call whose type is unknown: they may move."""
    outs = {r.name for r in s.returns}
    return [p for p in s.params if p.read_after and not p.type and p.name not in outs]


def _rust_self(s: HelperShape) -> str:
    """The receiver the helper borrows: the host's own when it borrows, a
    borrow when the host takes ``self`` by value (moving it into the helper
    would end the host's use of it)."""
    decl = (s.receiver_decl or "&self").strip()
    if decl.startswith("&"):
        return decl
    return "&mut self" if decl.startswith("mut") else "&self"


def _rust_sig(s: HelperShape) -> str:
    params = [
        f"{p.name}: &{p.type}" if _rust_borrowed(s, p) else f"{p.name}: {_type(p, 'rust')}"
        for p in s.params
    ]
    if _method(s):
        params.insert(0, _rust_self(s))
    out = _single_out(s)
    ret = f" -> {_type(out, 'rust')}" if out else ""
    return f"{'async ' if s.is_async else ''}fn {_name(s)}({', '.join(params)}){ret} {{"


def _rust_call(s: HelperShape) -> str:
    target = "self." if _method(s) else ""
    args = ", ".join(f"&{p.name}" if _rust_borrowed(s, p) else p.name for p in s.params)
    expr = f"{target}{_name(s)}({args}){'.await' if s.is_async else ''}"
    out = _single_out(s)
    if out is None:
        return expr + ";"
    if s.out_declared:
        return f"let {'mut ' if s.out_rebound else ''}{out.name} = {expr};"
    return f"{out.name} = {expr};"


_RENDERERS = {
    "python": (_py_sig, _py_call),
    "ts": (lambda s: _ts_sig(s, True), _ts_call),
    "js": (lambda s: _ts_sig(s, False), _ts_call),
    "go": (_go_sig, _go_call),
    "java": (_java_sig, _java_call),
    "rust": (_rust_sig, _rust_call),
    "cpp": (_cpp_sig, _cpp_call),
}


# -- a staged plan's parameter object ------------------------------------------


class ContextText(NamedTuple):
    """The parameter object's declaration (None where the language needs
    none), the statement building it before the first stage, and notes."""

    declaration: str | None
    construct: str
    notes: tuple[str, ...]


def context_name(language: str | None, host: str) -> str:
    """``_PersistContext`` for host ``persist`` in Python, ``PersistContext``
    in TS / JS: a type name, private where the language spells it."""
    words = [*split_words(host.rsplit(".", 1)[-1]), "context"]
    return private_name(language, "".join(w.capitalize() for w in words)) or "Context"


def render_context(
    language: str, name: str, var: str, fields: tuple[Slot, ...]
) -> ContextText | None:
    """The object carrying *fields* to every stage, or None for a language
    without a staged renderer."""
    family = _FAMILY.get(language)
    if family == "python":
        lines = [f"    {f.name}: {f.type or 'Any'}" for f in fields]
        notes = ["Import dataclass from dataclasses."]
        if any(f.type is None for f in fields):
            notes.append("Import Any from typing, or write the fields' real types.")
        construct = f"{var} = {name}({', '.join(f'{f.name}={f.name}' for f in fields)})"
        head = ["@dataclass(frozen=True)", f"class {name}:"]
        return ContextText("\n".join([*head, *lines]), construct, tuple(notes))
    if family not in ("ts", "js"):
        return None
    names = ", ".join(f.name for f in fields)
    if family == "js":
        return ContextText(None, f"const {var} = {{ {names} }};", ())
    lines = [f"  {f.name}: {f.type or TYPE_PLACEHOLDER};" for f in fields]
    declaration = "\n".join([f"interface {name} {{", *lines, "}"])
    return ContextText(declaration, f"const {var}: {name} = {{ {names} }};", ())


#: What the renderer adds to a stored plan, served on plan detail only.
_DETAIL_SYMBOL_KEYS = ("params", "returns", "signature_text", "return_text", "notes")
#: A staged plan's stages, parameter object and residual sit on detail too.
_DETAIL_PLAN_KEYS = ("call_site", "stages", "parameter_object", "orchestrator")


def list_plan(plan: dict) -> dict:
    """*plan* without the rendered texts and typed slots, for a list row: a
    list serves every plan, and an agent reads the texts on one plan's
    detail."""
    symbol = plan.get("new_symbol")
    if not any(k in plan for k in _DETAIL_PLAN_KEYS) and not (
        isinstance(symbol, dict) and any(k in symbol for k in _DETAIL_SYMBOL_KEYS)
    ):
        return plan
    out = {k: v for k, v in plan.items() if k not in _DETAIL_PLAN_KEYS}
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
    "ContextText",
    "HelperShape",
    "ParamMode",
    "Rendered",
    "Slot",
    "brief",
    "context_name",
    "list_plan",
    "private_name",
    "render",
    "render_context",
    "staged_outputs",
    "symbol_params",
]
