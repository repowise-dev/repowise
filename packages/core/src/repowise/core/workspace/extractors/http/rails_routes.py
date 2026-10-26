"""Rails (Ruby) HTTP provider dialect — ``config/routes.rb``.

Reads routes declared via ``resources``/``resource`` (with ``only`` and
``except`` options), ``namespace``, ``scope``, ``member``, ``collection``,
direct verb helpers (``get``, ``post``, etc.), and ``match ... via:``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..langs import RUBY
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_PLURAL_ACTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("index", ("GET",), ""),
    ("new", ("GET",), "/new"),
    ("create", ("POST",), ""),
    ("show", ("GET",), "/:id"),
    ("edit", ("GET",), "/:id/edit"),
    ("update", ("PUT", "PATCH"), "/:id"),
    ("destroy", ("DELETE",), "/:id"),
)

_SINGULAR_ACTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("new", ("GET",), "/new"),
    ("create", ("POST",), ""),
    ("show", ("GET",), ""),
    ("edit", ("GET",), "/edit"),
    ("update", ("PUT", "PATCH"), ""),
    ("destroy", ("DELETE",), ""),
)

_NAMESPACE_RE = re.compile(r"\bnamespace\s+(?::(\w+)|\"([^\"]+)\"|'([^']+)')")
_SCOPE_RE = re.compile(
    r"\bscope\s+(?:path:\s*[\"']([^\"']+)[\"']|(?::(\w+)|\"([^\"]+)\"|'([^']+)'))"
)
_RESOURCES_RE = re.compile(r"\b(resources|resource)\s+([^\n]+)")
_DIRECT_RE = re.compile(r"\b(get|post|put|patch|delete)\s+(?:[\"']([^\"']+)[\"']|:(\w+))")
_ROOT_RE = re.compile(r"\broot\b")
_MATCH_RE = re.compile(
    r"\bmatch\s+[\"']([^\"']+)[\"'][^\n]*?(?:via:\s*|:via\s*=>\s*)(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+))"
)
_DO_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*$")


@dataclass
class _Scope:
    kind: str
    prefix: str = ""
    resource: str = ""
    singular: bool = False


def _parse_options(text: str) -> tuple[set[str] | None, set[str]]:
    only_m = re.search(
        r"only:\s*(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+)|\"(\w+)\"|'(\w+)')", text
    )
    except_m = re.search(
        r"except:\s*(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+)|\"(\w+)\"|'(\w+)')", text
    )
    only: set[str] | None = None
    if only_m:
        raw = next(g for g in only_m.groups() if g is not None)
        only = set(re.findall(r"\w+", raw))
    excluded: set[str] = set()
    if except_m:
        raw = next(g for g in except_m.groups() if g is not None)
        excluded = set(re.findall(r"\w+", raw))
    return only, excluded


def _current_base(stack: list[_Scope]) -> str:
    parts: list[str] = []
    for s in stack:
        if s.prefix:
            parts.append(s.prefix.strip("/"))
        elif s.resource:
            parts.append(s.resource.strip("/"))
            if not s.singular:
                parts.append(":id")
    return "/" + "/".join(parts) if parts else ""


def _read_statements(lines: list[str], ctx: ScanContext) -> list[tuple[int, str]]:
    """Join continued lines and filter empty/comment-only lines."""
    statements: list[tuple[int, str]] = []
    buf = ""
    start_line = 1
    for line_no, mask_line in enumerate(lines, 1):
        line = mask_line.strip()
        if not line or ctx.comment_only_line(line_no):
            continue
        if not buf:
            start_line = line_no
            buf = line
        else:
            buf += " " + line
        if not buf.endswith(","):
            statements.append((start_line, buf))
            buf = ""
    if buf:
        statements.append((start_line, buf))
    return statements


def _strip_inline_end(raw: str) -> tuple[str, bool]:
    clean = raw
    has_inline_end = False
    if "; end" in clean:
        clean = clean.split("; end")[0].strip()
        has_inline_end = True
    elif re.search(r"\s+end$", clean):
        clean = re.sub(r"\s+end$", "", clean).strip()
        has_inline_end = True
    return clean, has_inline_end


def _pop_if_enclosing(
    has_inline_end: bool, had_do: bool, stack: list[_Scope]
) -> None:
    if has_inline_end and not had_do and stack:
        stack.pop()


def _handle_scope(line: str, push_block: bool, stack: list[_Scope]) -> bool:
    if "routes.draw" in line:
        if push_block:
            stack.append(_Scope(kind="drawer"))
        return True

    m = _NAMESPACE_RE.search(line)
    if m:
        ns = next(g for g in m.groups() if g is not None)
        if push_block:
            stack.append(_Scope(kind="namespace", prefix="/" + ns))
        return True

    m = _SCOPE_RE.search(line)
    if m:
        sc = next(g for g in m.groups() if g is not None)
        prefix = ("/" + sc.strip("/")) if sc else ""
        if push_block:
            stack.append(_Scope(kind="scope", prefix=prefix))
        return True

    if re.search(r"\bmember\b", line):
        if push_block:
            stack.append(_Scope(kind="member"))
        return True

    if re.search(r"\bcollection\b", line):
        if push_block:
            stack.append(_Scope(kind="collection"))
        return True

    return False


def _expand_actions(
    actions: tuple[tuple[str, tuple[str, ...], str], ...],
    res_names: list[str],
    base: str,
    only: set[str] | None,
    excluded: set[str],
    line_no: int,
    ctx: ScanContext,
) -> list[Contract]:
    out: list[Contract] = []
    for name in res_names:
        for act_name, verbs, suffix in actions:
            if (only is not None and act_name not in only) or act_name in excluded:
                continue
            res_path = f"{base}/{name}{suffix}"
            for v in verbs:
                c = build_provider_contract(
                    ctx,
                    method=v,
                    path_raw=res_path,
                    framework="rails",
                    line=line_no,
                )
                if c is not None:
                    out.append(c)
    return out


def _handle_resources(
    line: str,
    line_no: int,
    push_block: bool,
    stack: list[_Scope],
    ctx: ScanContext,
) -> list[Contract] | None:
    m = _RESOURCES_RE.search(line)
    if not m:
        return None

    kind = m.group(1)
    rest = m.group(2)
    first_arg = rest.split(",")[0]
    names = re.findall(r":(\w+)|[\"']([^\"']+)[\"']", first_arg)
    res_names = [g[0] or g[1] for g in names]
    only, excluded = _parse_options(rest)
    is_singular = kind == "resource"
    actions = _SINGULAR_ACTIONS if is_singular else _PLURAL_ACTIONS

    base = _current_base(stack)
    out = _expand_actions(actions, res_names, base, only, excluded, line_no, ctx)
    if push_block:
        for name in res_names:
            stack.append(_Scope(kind="resource", resource=name, singular=is_singular))
    return out


def _handle_match(
    line: str,
    line_no: int,
    ctx: ScanContext,
) -> list[Contract] | None:
    m = _MATCH_RE.search(line)
    if not m:
        return None

    path = m.group(1)
    v_list = next(g for g in m.groups()[1:] if g is not None)
    if v_list == "all":
        verbs = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    else:
        verbs = [v.upper() for v in re.findall(r"\w+", v_list)]

    out: list[Contract] = []
    for v in verbs:
        c = build_provider_contract(
            ctx,
            method=v,
            path_raw=path,
            framework="rails",
            line=line_no,
        )
        if c is not None:
            out.append(c)
    return out


def _direct_verb_path(line: str, p: str, stack: list[_Scope]) -> str:
    if p.startswith("/"):
        base = "".join(s.prefix for s in stack if s.prefix)
        return f"{base}{p}"

    in_coll = any(s.kind == "collection" for s in stack) or "on: :collection" in line
    res = next((s for s in reversed(stack) if s.resource), None)
    if not res:
        base = _current_base(stack)
        return f"{base}/{p}"

    base = "".join(s.prefix for s in stack if s.prefix)
    suffix = (
        f"{res.resource}/{p}" if in_coll or res.singular else f"{res.resource}/:id/{p}"
    )
    return f"{base}/{suffix}"


def _handle_direct_verb(
    line: str,
    line_no: int,
    push_block: bool,
    stack: list[_Scope],
    ctx: ScanContext,
) -> list[Contract] | None:
    m = _DIRECT_RE.search(line)
    if not m:
        return None

    verb = m.group(1).upper()
    p = m.group(2) or m.group(3)
    full = _direct_verb_path(line, p, stack)

    out: list[Contract] = []
    c = build_provider_contract(
        ctx,
        method=verb,
        path_raw=full,
        framework="rails",
        line=line_no,
    )
    if c is not None:
        out.append(c)
    if push_block:
        stack.append(_Scope(kind="other"))
    return out


def _handle_root(
    line_no: int,
    stack: list[_Scope],
    ctx: ScanContext,
) -> list[Contract]:
    base = _current_base(stack) or "/"
    c = build_provider_contract(
        ctx,
        method="GET",
        path_raw=base,
        framework="rails",
        line=line_no,
    )
    return [c] if c is not None else []


def _process_statement(
    line: str,
    line_no: int,
    push_block: bool,
    stack: list[_Scope],
    ctx: ScanContext,
) -> list[Contract] | None:
    if _handle_scope(line, push_block, stack):
        return []

    res_contracts = _handle_resources(line, line_no, push_block, stack, ctx)
    if res_contracts is not None:
        return res_contracts

    if _ROOT_RE.search(line):
        return _handle_root(line_no, stack, ctx)

    match_contracts = _handle_match(line, line_no, ctx)
    if match_contracts is not None:
        return match_contracts

    return _handle_direct_verb(line, line_no, push_block, stack, ctx)


class RailsRoutesDialect:
    name = "rails"
    extensions = RUBY
    requires = ("routes.draw",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        mask = ctx._mask()
        code = mask if mask is not None else ctx.content
        lines = code.splitlines()

        stack: list[_Scope] = []
        out: list[Contract] = []
        statements = _read_statements(lines, ctx)

        for line_no, raw in statements:
            if raw == "end" or raw.startswith("end "):
                if stack:
                    stack.pop()
                continue

            line, has_inline_end = _strip_inline_end(raw)
            if not line:
                _pop_if_enclosing(has_inline_end, False, stack)
                continue

            had_do = bool(_DO_BLOCK_RE.search(line))
            push_block = had_do and not has_inline_end

            contracts = _process_statement(line, line_no, push_block, stack, ctx)
            if contracts is not None:
                out.extend(contracts)
            elif push_block:
                stack.append(_Scope(kind="other"))

            _pop_if_enclosing(has_inline_end, had_do, stack)

        return out
