"""Client factories that take their URL base as a parameter.

A shared API package often exports a factory that builds its requests under a
base the caller supplies, so each host binds its own routes::

    export function createEvidenceApi(request, base: (id: string) => string) {
      return { status: (id) => request(`${base(id)}/status`) };
    }
    export const api = createEvidenceApi(apiRequest, (id) => `/api/repos/${id}/evidence`);

Read on its own, the request is ``/status``: the path the server serves is the
call site's base plus that tail. :func:`factory_mounts` records, repo-wide, the
literal bases each call to a factory passes, and :func:`factory_bases` puts
them back on the factory's requests: one request per distinct base, as each
binding is a request the code really makes.

A base that is a function of the call (``${base(id)}``) is a path builder, so
a request whose factory has no readable call site is dropped and counted as
unresolved rather than matched on its tail alone. A bare ``${base}`` with no
readable call site stays the host placeholder it always was (a base URL read
from config is the common case).

Ceiling: call sites are keyed by the factory's name, so two packages that each
export a factory of the same name and position pool their bases, and an
import alias (``import { createApi as make }``) is not followed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from ..strings import _expression_holds, _template_end, call_arguments, match_paren
from .wrappers import GENERIC_ARGS

if TYPE_CHECKING:
    from ..base import ScanContext
    from .client_calls import ClientCallMatch

_KEY = "js-factory-base:"
# A call-form base is stored behind this mark, a plain one as written.
_ARROW = "=>"

_NAME = r"[A-Za-z_$][\w$]*"
# `import a, { b, c as d } from "x"` and `const { e } = require("x")`.
_IMPORT_RE = re.compile(r"\bimport\s+(?:type\s+)?([^;'\"`]*?)\s+from\s*['\"]")
_REQUIRE_RE = re.compile(r"\b(?:const|let|var)\s+\{([^}]*)\}\s*=\s*require\s*\(")
# A function this file defines, named.
_DEF_RE = re.compile(
    rf"\bfunction\s*\*?\s*(?P<f>{_NAME})\s*{GENERIC_ARGS}\("
    rf"|\b(?:const|let|var)\s+(?P<v>{_NAME})\s*(?::[^=;\n]+)?=\s*(?:async\s+)?"
    rf"(?:function\b\s*\*?\s*(?:{_NAME})?\s*)?{GENERIC_ARGS}\("
)
_LITERAL = r"`[^`]*`|'[^'\n]*'|\"[^\"\n]*\""
_LITERAL_RE = re.compile(rf"(?:{_LITERAL})")
_ARROW_ARG_RE = re.compile(
    rf"(?:async\s+)?(?:\([^()]*\)|{_NAME})\s*(?::[^=]+?)?\s*=>\s*(?P<lit>{_LITERAL})"
)
# A base worth recording: a rooted path, an absolute URL, or a placeholder.
_BASE_START = ("/", "http://", "https://", "${")
_GATE = ("=>", "'/", '"/', "`/", "`${", "'http", '"http', "`http")
# The base a request leads with, and a path after it: `${base(id)}/x`, `${base}/x`.
_LEAD_RE = re.compile(rf"\$\{{\s*(?P<name>{_NAME})\s*(?P<call>\([^()]*\))?\s*\}}(?=/)")
# After a parameter list, up to its body: a return type (an object type literal
# included), then a block or an arrow.
_TYPE_START_RE = re.compile(r"\s*:\s*")
_TYPE_REST_RE = re.compile(r"[^{};=`]*?(?=\s*(?:=>|\{))")
_OPEN_RE = re.compile(r"\s*(?:(?P<arrow>=>)\s*)?(?P<block>\{)?")
# Before a parameter list: the name of the function it opens, when it has one.
_NAMED_HEAD_RE = re.compile(
    rf"(?:\bfunction\s*\*?\s*(?P<f>{_NAME})"
    rf"|(?<![\w$.])(?P<v>{_NAME})\s*(?::[^=;\n]+)?=\s*(?:async\s+)?"
    rf"(?:function\b\s*\*?\s*(?:{_NAME})?)?)\s*{GENERIC_ARGS}$"
)
_CONTROL_RE = re.compile(r"\b(?:if|for|while|switch|catch|with)\s*$")
_HEAD_WINDOW = 200
# What an arrow's expression body ends at, or steps over whole.
_EXPR_STOP_RE = re.compile(r"[()\[\]{};,'\"`]")
_CLOSER = {"(": ")", "[": "]", "{": "}"}
_QUOTED_RE = {q: re.compile(rf"{q}(?:[^{q}\\\n]|\\.)*{q}") for q in "'\""}
_PARAM_RE = re.compile(rf"\s*(?:\.\.\.)?(?:(?:private|public|protected|readonly)\s+)*({_NAME})")


def _bound_names(content: str) -> set[str]:
    """The function names *content* imports or defines."""
    names: set[str] = set()
    clauses = [m.group(1) for m in _IMPORT_RE.finditer(content)]
    clauses += [m.group(1) for m in _REQUIRE_RE.finditer(content)]
    for clause in clauses:
        for part in re.split(r"[,{}]", clause):
            words = re.findall(_NAME, part.replace(":", " as "))
            if words and words[-1] not in ("type", "as"):
                names.add(words[-1])
    for m in _DEF_RE.finditer(content):
        names.add(m.group("f") or m.group("v"))
    return names


def _base_value(arg: str) -> str | None:
    """What a call passes for a base: ``=>`` + literal for a function returning
    one, the literal for a plain one, None for anything else."""
    arrow = _ARROW_ARG_RE.fullmatch(arg)
    literal = arrow.group("lit") if arrow else arg if _LITERAL_RE.fullmatch(arg) else None
    if literal is None or not literal[1:-1].startswith(_BASE_START):
        return None
    return _ARROW + literal if arrow else literal


def factory_mounts(ctx: ScanContext) -> dict[str, str]:
    """``{key: ""}`` for each literal base a call in *ctx* passes a function it
    imports or defines; the key names the callee, the argument position and
    the base, so callers that disagree survive the mount merge side by side."""
    content = ctx.content
    if not any(t in content for t in _GATE):
        return {}
    names = _bound_names(content)
    if not names:
        return {}
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    call = re.compile(rf"(?<![\w$.])({alt})\s*{GENERIC_ARGS}\(")
    out: dict[str, str] = {}
    for m in call.finditer(content):
        if re.search(r"\bfunction\s*\*?\s*$", content[max(0, m.start() - 12) : m.start()]):
            continue
        paren = m.end() - 1
        close = match_paren(content, paren)
        if close < 0 or _body(content, close) is not None:
            continue  # a definition or a method signature, not a call
        if ctx.in_comment(m.start()):
            continue
        for i, arg in enumerate(call_arguments(content, paren, close) or []):
            value = _base_value(arg)
            if value is not None:
                out[f"{_KEY}{m.group(1)}:{i}:{value}"] = ""
    return out


# One function holding a base parameter: where its parameter list opens, where
# its body starts and ends, whether that body is an arrow's expression, its
# name (None when unnamed), and its parameters.
_Definition = tuple[int, int, int, bool, str | None, list[str]]


def _expression_end(content: str, start: int) -> int:
    """Where an arrow's expression body starting at *start* ends at the latest:
    the first top-level ``,`` or ``;``, or a bracket it did not open. Brackets
    and literals are stepped over whole, so each body costs its own length."""
    i = start
    while stop := _EXPR_STOP_RE.search(content, i):
        ch, at = stop.group(), stop.start()
        if ch in _CLOSER:
            close = match_paren(content, at, closer=_CLOSER[ch])
        elif ch == "`":
            close = _template_end(content, at)
        elif ch in _QUOTED_RE:
            quoted = _QUOTED_RE[ch].match(content, at)
            close = quoted.end() - 1 if quoted else at
        else:
            return at
        if close < 0:
            return len(content)
        i = close + 1
    return len(content)


def _body(content: str, close: int) -> tuple[int, int, bool] | None:
    """``(body start, body end, expression)`` after the parameter list closing
    at *close*; None when no function body follows (a call)."""
    pos = close + 1
    if annotation := _TYPE_START_RE.match(content, pos):
        pos = annotation.end()
        if content.startswith("{", pos):
            pos = match_paren(content, pos, closer="}") + 1
            if pos <= 0:
                return None
        rest = _TYPE_REST_RE.match(content, pos)
        if rest is None:
            return None
        pos = rest.end()
    opened = _OPEN_RE.match(content, pos)
    if opened.group("block"):
        end = match_paren(content, opened.start("block"), closer="}")
        return opened.start("block"), (end if end > 0 else len(content)), False
    if not opened.group("arrow"):
        return None
    return opened.end(), _expression_end(content, opened.end()), True


def _definitions(content: str, names: set[str]) -> list[_Definition]:
    """Every function in *content* with one of *names* as a parameter, named or
    not, read once per file in source order."""
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    heads = re.compile(
        rf"\((?=[^()]*?(?<![\w$.])(?:{alt})(?![\w$]))|(?<![\w$.])(?P<bare>{alt})(?=\s*=>)"
    )
    out: list[_Definition] = []
    for m in heads.finditer(content):
        if m.group("bare"):
            params, close = [m.group("bare")], m.end() - 1
        else:
            if _CONTROL_RE.search(content, max(0, m.start() - 10), m.start()):
                continue
            close = match_paren(content, m.start())
            args = call_arguments(content, m.start(), close) if close > 0 else None
            params = [p.group(1) if (p := _PARAM_RE.match(a)) else "" for a in args or []]
            if names.isdisjoint(params):
                continue
        body = _body(content, close)
        if body is None:
            continue
        head = _NAMED_HEAD_RE.search(content, max(0, m.start() - _HEAD_WINDOW), m.start())
        name = (head.group("f") or head.group("v")) if head else None
        out.append((m.start(), *body, name, params))
    return out


def _enclosing_factory(
    content: str, definitions: list[_Definition], name: str, offset: int
) -> tuple[str | None, int] | None:
    """``(function name, position)`` of the innermost of *definitions* whose
    parameter *name* is and whose body holds *offset*; the name is None for an
    unnamed function, whose callers cannot be read."""
    found = None
    for head, start, end, expression, factory, params in definitions:
        if head >= offset:
            break
        if name not in params or not start < offset < end:
            continue
        if not expression or _expression_holds(content, start, offset):
            found = (factory, params.index(name))  # later heads are inner ones
    return found


def _bases(
    mounts: Mapping[str, str], factory: str | None, position: int, call_form: bool
) -> list[str]:
    """The literal bodies of the bases calls to *factory* pass at *position*."""
    if factory is None:
        return []
    prefix = f"{_KEY}{factory}:{position}:"
    out = []
    for key in mounts:
        if key.startswith(prefix):
            value = key[len(prefix) :]
            if value.startswith(_ARROW) == call_form:
                out.append(value.removeprefix(_ARROW)[1:-1])
    return sorted(out)


def _prepend(base: str, inner: Callable[[str], str] | None, url: str) -> str:
    if inner is not None:
        url = inner(url)
    lead = _LEAD_RE.match(url)
    return base.rstrip("/") + url[lead.end() :] if lead else url


def factory_bases(ctx: ScanContext, matches: list[ClientCallMatch]) -> list[ClientCallMatch]:
    """*matches* with each factory request's base put back, one row per base."""
    if not any(m.url[1:3] == "${" for m in matches):
        return matches
    leads = [_LEAD_RE.match(m.url, 1) if m.url[1:3] == "${" else None for m in matches]
    names = {lead.group("name") for lead in leads if lead}
    if not names:
        return matches
    definitions = _definitions(ctx.content, names)
    out: list[ClientCallMatch] = []
    for m, lead in zip(matches, leads, strict=True):
        found = (
            _enclosing_factory(ctx.content, definitions, lead.group("name"), m.offset)
            if lead
            else None
        )
        if found is None:
            out.append(m)
            continue
        call_form = lead.group("call") is not None
        bases = _bases(ctx.mounts, *found, call_form)
        if not bases:
            if call_form:
                ctx.note_unresolved(m.offset)  # a path the caller builds, unread
            else:
                out.append(m)
            continue
        out.extend(_with_base(m, base) for base in bases)
    return out


def _with_base(m: ClientCallMatch, base: str) -> ClientCallMatch:
    inner = m.compose

    def compose(url: str) -> str:
        return _prepend(base, inner, url)

    return replace(m, compose=compose)


__all__ = ["factory_bases", "factory_mounts"]
