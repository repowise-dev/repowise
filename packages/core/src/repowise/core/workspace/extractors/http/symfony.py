"""Symfony (PHP) HTTP provider dialect — ``#[Route]`` and ``/** @Route(...) */``.

Reads route attributes and docblock annotations placed directly above
controller classes and action methods. Reports contracts on the declaration line
so the central comment rule keeps docblock-annotated routes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..langs import PHP
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_ROUTE_ANNOT_RE = re.compile(r"(?:@|#\[\s*)Route\s*\((.*?)\)", re.DOTALL)
_PATH_ARG_RE = re.compile(r"(?:path\s*[:=]\s*)?[\"']([^\"']+)[\"']")
_METHODS_ARG_RE = re.compile(
    r"methods\s*[:=]\s*(?:\{([^}]*)\}|\[([^\]]*)\]|[\"']([^\"']+)[\"'])"
)
_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
_FUNC_RE = re.compile(
    r"\b(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+([A-Za-z_]\w*)\s*\("
)

_VALID_VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def _parse_methods(args: str) -> list[str]:
    mm = _METHODS_ARG_RE.search(args)
    if not mm:
        return ["GET"]
    raw_m = next((g for g in mm.groups() if g is not None), "")
    verbs = [w.upper() for w in re.findall(r"[A-Za-z]+", raw_m) if w.upper() in _VALID_VERBS]
    return verbs or ["GET"]


def _parse_route(annot_text: str) -> tuple[str, list[str]] | None:
    m = _ROUTE_ANNOT_RE.search(annot_text)
    if not m:
        return None
    args = m.group(1).strip()
    pm = _PATH_ARG_RE.search(args)
    if not pm:
        return None
    return pm.group(1), _parse_methods(args)


def _find_docblock_or_attribute(before: str, idx: int) -> str | None:
    if idx >= 1 and before[idx - 1 : idx + 1] == "*/":
        start_comment = before.rfind("/**", 0, idx)
        if start_comment != -1:
            return before[start_comment : idx + 1]
    if before[idx] == "]":
        start_attr = before.rfind("#[", 0, idx)
        if start_attr != -1:
            return before[start_attr : idx + 1]
    return None


def _preceding_annotation(content: str, start_pos: int) -> str | None:
    before = content[:start_pos]
    stripped = before.rstrip()
    if not stripped:
        return None
    return _find_docblock_or_attribute(before, len(stripped) - 1)


@dataclass(frozen=True, slots=True)
class _ClassScope:
    start: int
    end: int
    prefix: str
    methods: list[str] | None


def _class_scope(content: str, c_match: re.Match[str], next_start: int) -> _ClassScope:
    c_start = c_match.start()
    annot = _preceding_annotation(content, c_start)
    prefix = ""
    methods = None
    if annot:
        r = _parse_route(annot)
        if r:
            prefix, methods = r
    return _ClassScope(c_start, next_start, prefix, methods)


def _collect_classes(content: str) -> list[_ClassScope]:
    classes = list(_CLASS_RE.finditer(content))
    scopes: list[_ClassScope] = []
    for i, c_match in enumerate(classes):
        end = classes[i + 1].start() if i + 1 < len(classes) else len(content)
        scopes.append(_class_scope(content, c_match, end))
    return scopes


def _emit_contracts(
    ctx: ScanContext,
    methods: list[str],
    path: str,
    line_no: int,
) -> list[Contract]:
    out: list[Contract] = []
    for verb in methods:
        c = build_provider_contract(
            ctx,
            method=verb,
            path_raw=path,
            framework="symfony",
            line=line_no,
        )
        if c is not None:
            out.append(c)
    return out


def _annotated_contracts(
    ctx: ScanContext,
    f_match: re.Match[str],
    c_prefix: str,
) -> list[Contract]:
    f_start = f_match.start()
    annot = _preceding_annotation(ctx.content, f_start)
    if not annot:
        return []
    r = _parse_route(annot)
    if not r:
        return []
    m_path, methods = r
    full_path = compose_prefix(c_prefix, m_path)
    line_no = line_at(ctx.content, f_start)
    return _emit_contracts(ctx, methods, full_path, line_no)


def _invoke_contracts(
    ctx: ScanContext,
    f_match: re.Match[str],
    enclosing: _ClassScope | None,
) -> list[Contract]:
    if f_match.group(1) == "__invoke" and enclosing and enclosing.prefix:
        methods = enclosing.methods or ["GET"]
        line_no = line_at(ctx.content, f_match.start())
        return _emit_contracts(ctx, methods, enclosing.prefix, line_no)
    return []


def _contracts_for_function(
    ctx: ScanContext,
    f_match: re.Match[str],
    classes: list[_ClassScope],
) -> list[Contract]:
    f_start = f_match.start()
    enclosing = next((cp for cp in classes if cp.start < f_start < cp.end), None)
    c_prefix = enclosing.prefix if enclosing else ""

    annot_contracts = _annotated_contracts(ctx, f_match, c_prefix)
    if annot_contracts:
        return annot_contracts
    return _invoke_contracts(ctx, f_match, enclosing)


class SymfonyDialect:
    name = "symfony"
    extensions = PHP
    requires = ("Symfony\\Component\\Routing",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        classes = _collect_classes(ctx.content)
        out: list[Contract] = []
        for f_match in _FUNC_RE.finditer(ctx.content):
            out.extend(_contracts_for_function(ctx, f_match, classes))
        return out
