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

from ..strings import call_arguments, match_paren
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
# After a parameter list: an optional return type, an optional arrow, the body.
_BODY_RE = re.compile(r"\s*(?::[^{};=`]*?)?\s*(?:=>\s*)?(?P<open>[{(])")
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
        if close < 0 or re.match(r"\s*(?:[{:]|=>)", content[close + 1 : close + 40]):
            continue  # a definition or a method signature, not a call
        if ctx.in_comment(m.start()):
            continue
        for i, arg in enumerate(call_arguments(content, paren, close) or []):
            value = _base_value(arg)
            if value is not None:
                out[f"{_KEY}{m.group(1)}:{i}:{value}"] = ""
    return out


def _definitions(content: str, names: set[str]) -> list[tuple[int, int, str, list[str]]]:
    """``(body start, body end, function name, parameters)`` of each named
    function in *content* with one of *names* as a parameter, read once per file."""
    out = []
    for m in _DEF_RE.finditer(content):
        paren = m.end() - 1
        close = match_paren(content, paren)
        params = call_arguments(content, paren, close) if close > 0 else None
        if not params:
            continue
        params = [p.group(1) if (p := _PARAM_RE.match(a)) else "" for a in params]
        if names.isdisjoint(params):
            continue
        body = _BODY_RE.match(content, close + 1)
        if body is None:
            continue
        start = body.start("open")
        end = match_paren(content, start, closer="}" if body.group("open") == "{" else ")")
        out.append((start, end if end > 0 else len(content), m.group("f") or m.group("v"), params))
    return out


def _enclosing_factory(
    definitions: list[tuple[int, int, str, list[str]]], name: str, offset: int
) -> tuple[str, int] | None:
    """``(function name, position)`` of the innermost of *definitions* whose
    parameter *name* is and whose body holds *offset*."""
    found = None
    for start, end, factory, params in definitions:
        if start >= offset:
            break
        if offset < end and name in params:
            found = (factory, params.index(name))  # later starts are inner ones
    return found


def _bases(mounts: Mapping[str, str], factory: str, position: int, call_form: bool) -> list[str]:
    """The literal bodies of the bases calls to *factory* pass at *position*."""
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
        found = _enclosing_factory(definitions, lead.group("name"), m.offset) if lead else None
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
